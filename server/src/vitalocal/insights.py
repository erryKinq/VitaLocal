"""Derived views over the stored data: sleep rhythm, readiness, recovery, energy and weekly trends.

Everything here is computed from values VitaLocal already holds. The readiness score is VitaLocal's
own transparent estimate from Garmin's factors (every component is returned); Garmin's own score,
which Gadgetbridge exports as a generic metric, is returned alongside for comparison.
"""

from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from vitalocal import energy as energy_mod
from vitalocal.api.days import _daily_values, _local_midnight
from vitalocal.api.habit_rewards import period_rewards
from vitalocal.api.habits import evaluate, list_habits
from vitalocal.sleep import clock, nights, noon_minutes

DAILY_KEYS = (
    "daily.sleep_score",
    "daily.hrv_last_night_avg",
    "resting_heart_rate",
    "body_energy",
    "steps",
    "active_energy",
    "resting_metabolic_rate",
    "stress",
    "training_load_acute",
    "training_load_chronic",
    "daily.hrv_weekly_avg",
    "daily.hrv_baseline_balanced_lower",
    "nutrition.energy",
    "nutrition.drinks",
    "checkin.mood",
    "checkin.energy",
    "weight",
)
BASELINE_DAYS = 28
MIN_BASELINE = 7
RECOVERY_LOOKBACK_DAYS = 60
WEEKS = 8
# A workout counts for recovery analysis from this duration on.
MIN_WORKOUT_MIN = 20


# --- data access --------------------------------------------------------------------------------


def daily(conn: Connection, tz: ZoneInfo, first: date, last: date, keys=DAILY_KEYS) -> dict[str, dict]:
    """{metric: {day: value}} for complete local days first..last."""
    start, end = _local_midnight(first, tz), _local_midnight(last + timedelta(days=1), tz)
    raw = _daily_values(conn, list(keys), start, end)
    return {
        k: {date.fromisoformat(d): v["value"] for d, v in days.items() if v["value"] is not None}
        for k, days in raw.items()
    }


def workouts(conn: Connection, tz: ZoneInfo, first: date, last: date) -> list[dict]:
    rows = conn.execute(
        text("""SELECT id, start_ts, end_ts, (start_ts AT TIME ZONE :tz)::date AS day,
                       coalesce(sport_profile_name, name, sub_sport, sport) AS name, sport,
                       elapsed_s, energy_kcal, avg_hr,
                       (fit_attrs->>'total_training_effect')::float AS aerobic_te,
                       (fit_attrs->>'total_anaerobic_training_effect')::float AS anaerobic_te
                FROM activity WHERE start_ts >= :s AND start_ts < :e ORDER BY start_ts"""),
        {"tz": tz.key, "s": _local_midnight(first, tz), "e": _local_midnight(last + timedelta(days=1), tz)},
    )
    out = []
    for r in rows:
        w = dict(r._mapping)
        w["minutes"] = round((w["elapsed_s"] or 0) / 60)
        w["end"] = w["end_ts"] or (w["start_ts"] + timedelta(seconds=w["elapsed_s"] or 0))
        out.append(w)
    return out


# --- helpers ----------------------------------------------------------------------------------------


def _r(v, decimals=0):
    if v is None:
        return None
    return round(v) if decimals == 0 else round(v, decimals)


def _avg(values):
    v = [x for x in values if x is not None]
    return sum(v) / len(v) if v else None


def _days(first: date, last: date):
    d = first
    while d <= last:
        yield d
        d += timedelta(days=1)


def _spread(values: list[float]) -> float | None:
    """Mean absolute deviation in minutes (how regular a bed/wake time is)."""
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return sum(abs(v - m) for v in values) / len(values)


def sleep_times(night_list: list[dict]) -> dict:
    beds = [noon_minutes(n["bed_start"]) for n in night_list]
    wakes = [noon_minutes(n["bed_end"]) for n in night_list]
    return {
        "nights": len(night_list),
        "bedtime": clock(_avg(beds)),
        "wake_time": clock(_avg(wakes)),
        "bedtime_spread_min": _r(_spread(beds)),
        "wake_time_spread_min": _r(_spread(wakes)),
        "asleep_minutes": _r(_avg([n["asleep_minutes"] for n in night_list])),
    }


def _burned(values: dict, d: date) -> float | None:
    return values.get("energy.total", {}).get(d)


def with_burn(conn: Connection, tz: ZoneInfo, first: date, last: date, values: dict) -> dict:
    """Adds energy.total / .rest / .active per day (watch or estimate, see energy.daily_burn)."""
    burn = energy_mod.daily_burn(conn, first, last, tz, values)
    for k in ("total", "rest", "active"):
        values[f"energy.{k}"] = {d: b[k] for d, b in burn.items()}
    return values


# --- sections ------------------------------------------------------------------------------------------


def sleep_rhythm(ns: dict[date, dict], day: date, sleep_score: dict[date, float]) -> dict:
    """Average bed/wake times over the last 7 and 28 nights plus one row per week."""
    last = lambda n: [ns[d] for d in _days(day - timedelta(days=n - 1), day) if d in ns]  # noqa: E731
    week_start = day - timedelta(days=day.weekday())
    weeks = []
    for i in range(WEEKS - 1, -1, -1):
        ws = week_start - timedelta(weeks=i)
        rows = [ns[d] for d in _days(ws, min(ws + timedelta(days=6), day)) if d in ns]
        weeks.append(
            {
                "week_start": ws,
                "partial": ws + timedelta(days=6) > day,
                **sleep_times(rows),
                "sleep_score": _r(
                    _avg([sleep_score.get(d) for d in _days(ws, min(ws + timedelta(days=6), day))])
                ),
            }
        )
    return {"last_7": sleep_times(last(7)), "last_28": sleep_times(last(28)), "weeks": weeks}


def _baseline(series: dict[date, float], day: date) -> float | None:
    past = [v for d, v in series.items() if day - timedelta(days=BASELINE_DAYS) <= d < day]
    return median(past) if len(past) >= MIN_BASELINE else None


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


# Garmin's factors as deductions from 100, fitted against the watch's own score (58 readings,
# 2026-08-26 – 2026-10-01, mean deviation 6 points): each factor costs points in proportion to how
# far it falls short; a long remaining recovery time caps the score instead (see _recovery_cap).
SLEEP_FULL = 94  # sleep score from which sleep costs nothing
SLEEP_PER_POINT = 0.95  # last night
SLEEP_HISTORY_PER_POINT = 0.15  # average of the last 3 nights
HRV_PER_PERCENT = 0.5  # per % the 7-day average lies below the balanced range
STRESS_FREE = 34  # average stress of the 3 days before up to which it costs nothing
STRESS_PER_POINT = 2.5
LOAD_FREE = 1.3  # acute : chronic load up to which it costs nothing
LOAD_PER_UNIT = 40
# Garmin's levels: 1 poor, 2 low, 3 moderate, 4 high, 5 prime
LEVELS = ((95, "prime"), (75, "high"), (50, "moderate"), (25, "low"), (0, "poor"))


def _recovery_cap(hours: float) -> float:
    """Highest score with this much recovery left: up to 10 h nothing, then quickly down to 75 at
    15 h and slowly on (Garmin: 18 h → 73, 20 h → 71, 30 h → 68)."""
    if hours <= 10:
        return 100.0
    if hours <= 15:
        return 100 - (hours - 10) * 5
    return max(50.0, 75 - (hours - 15) * 0.5)


def readiness_level(score: float) -> str:
    return next(name for low, name in LEVELS if score >= low)


def _latest(conn: Connection, metric: str, now: datetime):
    return conn.execute(
        text("SELECT ts, value FROM sample WHERE metric = :m AND ts <= :t ORDER BY ts DESC LIMIT 1"),
        {"m": metric, "t": now},
    ).first()


_FACTOR_PREFIX = "garmin.readiness_factor."


def garmin_readiness(conn: Connection, day: date, now: datetime, tz: ZoneInfo) -> dict | None:
    """The watch's own training readiness: its latest value of `day` up to `now`."""
    rows = conn.execute(
        text("""SELECT DISTINCT ON (metric) metric, ts, value FROM sample
                WHERE (metric IN ('garmin.training_readiness', 'garmin.training_readiness_level')
                       OR metric LIKE 'garmin.readiness\\_factor.%')
                  AND ts >= :s AND ts < :e AND ts <= :now ORDER BY metric, ts DESC"""),
        {"s": _local_midnight(day, tz), "e": _local_midnight(day + timedelta(days=1), tz), "now": now},
    ).all()
    by = {r.metric: r for r in rows}
    if "garmin.training_readiness" not in by:
        return None
    level = by.get("garmin.training_readiness_level")
    return {
        "score": round(by["garmin.training_readiness"].value),
        # Garmin's levels: 1 poor, 2 low, 3 moderate, 4 high, 5 prime
        "level": int(level.value) if level else None,
        "ts": by["garmin.training_readiness"].ts,
        # the watch's rating per factor in % (100 = no limit), keyed like our components
        "factors": {
            m.removeprefix(_FACTOR_PREFIX): round(r.value)
            for m, r in by.items()
            if m.startswith(_FACTOR_PREFIX)
        },
    }


def readiness(values: dict, day: date, now: datetime, recovery_min=None) -> dict:
    """Garmin's six factors as deductions from 100; needs last night's sleep score and two more.

    recovery_min: (ts, minutes) of the watch's latest recovery time up to `now`.
    Every component has `value`, `baseline`, `unit` and `impact` (points it costs, ≤ 0).
    """
    comps = []

    def add(key, value, cost, baseline=None, unit=None):
        comps.append(
            {"key": key, "value": value, "baseline": baseline, "unit": unit, "impact": -_r(max(0.0, cost))}
        )

    series = lambda k: values.get(k, {})  # noqa: E731
    sleep = series("daily.sleep_score")
    if day in sleep:
        add("sleep_score", sleep[day], (SLEEP_FULL - sleep[day]) * SLEEP_PER_POINT)
    weekly = series("daily.hrv_weekly_avg").get(day)
    # Garmin's HRV status: the 7-day average against the lower edge of the balanced range; until
    # the watch has a range, the 28-day median stands in for it
    lower = series("daily.hrv_baseline_balanced_lower").get(day) or _baseline(
        series("daily.hrv_weekly_avg"), day
    )
    if weekly is not None and lower:
        add("hrv_status", weekly, (lower - weekly) / lower * 100 * HRV_PER_PERCENT, _r(lower), "ms")
    yesterday = day - timedelta(days=1)
    acute = series("training_load_acute").get(day) or series("training_load_acute").get(yesterday)
    chronic = series("training_load_chronic").get(day) or series("training_load_chronic").get(yesterday)
    if acute is not None and chronic:
        ratio = acute / chronic
        add("acute_load", round(ratio, 2), (ratio - LOAD_FREE) * LOAD_PER_UNIT, unit="ratio")
    nights3 = [sleep[d] for d in _days(day - timedelta(days=2), day) if d in sleep]
    if len(nights3) >= 2:
        add("sleep_history", _r(_avg(nights3)), (SLEEP_FULL - _avg(nights3)) * SLEEP_HISTORY_PER_POINT)
    stress3 = [v for d, v in series("stress").items() if day - timedelta(days=3) <= d < day]
    if len(stress3) >= 2:
        add("stress_history", _r(_avg(stress3)), (_avg(stress3) - STRESS_FREE) * STRESS_PER_POINT)
    base = max(0.0, 100 + sum(c["impact"] for c in comps))
    if recovery_min is not None:
        ts, minutes = recovery_min
        hours = max(0.0, minutes - (now - ts).total_seconds() / 60) / 60
        add("recovery_time", round(hours, 1), base - _recovery_cap(hours), unit="h")

    if day not in sleep or len(comps) < 3:
        return {
            "score": None,
            "level": None,
            "components": comps,
            "reason": "needs the sleep score and two more",
        }
    score = min(base, _recovery_cap(hours)) if recovery_min is not None else base
    return {"score": round(score), "level": readiness_level(round(score)), "components": comps}


READINESS_KEYS = (
    "daily.sleep_score",
    "daily.hrv_weekly_avg",
    "daily.hrv_baseline_balanced_lower",
    "training_load_acute",
    "training_load_chronic",
    "stress",
)


def readiness_with_garmin(conn: Connection, tz: ZoneInfo, day: date, now: datetime, values=None) -> dict:
    """Own readiness plus the watch's (`garmin`); loads its own values unless given."""
    if values is None:
        values = daily(conn, tz, day - timedelta(days=BASELINE_DAYS), day, keys=READINESS_KEYS)
    return {
        **readiness(values, day, now, _latest(conn, "recovery_time", now)),
        "garmin": garmin_readiness(conn, day, now, tz),
    }


def recovery(values: dict, ws: list[dict], day: date) -> dict:
    """How nights after a workout compare with nights after rest days, and per workout the days
    until resting HR and HRV were back at the personal baseline."""
    first = day - timedelta(days=RECOVERY_LOOKBACK_DAYS)
    workout_days = {w["day"] for w in ws if w["minutes"] >= MIN_WORKOUT_MIN}
    any_activity_days = {w["day"] for w in ws}
    hrv, rhr, sleep = (
        values.get(k, {}) for k in ("daily.hrv_last_night_avg", "resting_heart_rate", "daily.sleep_score")
    )

    def nights_after(days: list[date]) -> dict:
        nxt = [d + timedelta(days=1) for d in days if d + timedelta(days=1) <= day]
        return {
            "nights": len(nxt),
            "hrv": _r(_avg([hrv.get(d) for d in nxt]), 1),
            "resting_heart_rate": _r(_avg([rhr.get(d) for d in nxt]), 1),
            "sleep_score": _r(_avg([sleep.get(d) for d in nxt]), 1),
        }

    period = list(_days(first, day - timedelta(days=1)))
    after_training = nights_after([d for d in period if d in workout_days])
    after_rest = nights_after([d for d in period if d not in any_activity_days])

    per_workout = []
    for w in ws:
        if w["minutes"] < MIN_WORKOUT_MIN or w["day"] < first:
            continue
        hb, rb = _baseline(hrv, w["day"]), _baseline(rhr, w["day"])
        nxt = w["day"] + timedelta(days=1)
        recovered = None
        if hb and rb:
            for k in range(1, 5):
                d = w["day"] + timedelta(days=k)
                if d > day:
                    break
                if d in hrv and d in rhr and hrv[d] >= hb * 0.97 and rhr[d] <= rb + 1:
                    recovered = k
                    break
        per_workout.append(
            {
                "activity_id": w["id"],
                "day": w["day"],
                "name": w["name"],
                "minutes": w["minutes"],
                "kcal": _r(w["energy_kcal"]),
                "aerobic_te": w["aerobic_te"],
                "next_night": {
                    "hrv_delta": _r(hrv[nxt] - hb, 1) if hb and nxt in hrv else None,
                    "resting_heart_rate_delta": _r(rhr[nxt] - rb, 1) if rb and nxt in rhr else None,
                    "sleep_score": sleep.get(nxt),
                },
                # days until HRV ≥ 97 % and resting HR ≤ +1 bpm of baseline; None: not within 4 days
                # or not measured yet
                "recovered_after_days": recovered,
            }
        )
    known = [w["recovered_after_days"] for w in per_workout if w["recovered_after_days"] is not None]
    return {
        "lookback_days": RECOVERY_LOOKBACK_DAYS,
        "after_training": after_training,
        "after_rest": after_rest,
        "typical_recovery_days": _r(median(known), 1) if len(known) >= 3 else None,
        "workouts": per_workout[-6:][::-1],
    }


def energy(values: dict, day: date) -> dict:
    """Averages over complete days (the running day is excluded)."""

    def window(n: int) -> dict:
        ds = list(_days(day - timedelta(days=n), day - timedelta(days=1)))
        burned = [_burned(values, d) for d in ds]
        intake = [values.get("nutrition.energy", {}).get(d) for d in ds]
        balance = [i - b for i, b in zip(intake, burned, strict=True) if i is not None and b is not None]
        return {
            "days": n,
            "burned": _r(_avg(burned)),
            "active": _r(_avg([values.get("energy.active", {}).get(d) for d in ds])),
            "resting": _r(_avg([values.get("energy.rest", {}).get(d) for d in ds])),
            "intake": _r(_avg(intake)),
            "intake_days": sum(1 for i in intake if i is not None),
            "balance": _r(_avg(balance)),
            "balance_days": len(balance),
            "steps": _r(_avg([values.get("steps", {}).get(d) for d in ds])),
            "drinks_ml": _r(_avg([values.get("nutrition.drinks", {}).get(d) for d in ds])),
        }

    return {"last_7": window(7), "last_28": window(28)}


def weeks(values: dict, ns: dict, ws: list[dict], day: date) -> list[dict]:
    week_start = day - timedelta(days=day.weekday())
    out = []
    for i in range(WEEKS - 1, -1, -1):
        s = week_start - timedelta(weeks=i)
        ds = [d for d in _days(s, s + timedelta(days=6)) if d <= day]

        def avg(k, n=0, ds=ds):
            return _r(_avg([values.get(k, {}).get(d) for d in ds]), n)

        wk = [w for w in ws if s <= w["day"] <= s + timedelta(days=6)]
        out.append(
            {
                "week_start": s,
                "partial": s + timedelta(days=6) > day,
                "sleep_score": avg("daily.sleep_score"),
                "asleep_minutes": _r(_avg([ns[d]["asleep_minutes"] for d in ds if d in ns])),
                "hrv": avg("daily.hrv_last_night_avg", 1),
                "resting_heart_rate": avg("resting_heart_rate", 1),
                "steps": avg("steps"),
                "burned": _r(_avg([_burned(values, d) for d in ds])),
                "intake": avg("nutrition.energy"),
                "stress": avg("stress"),
                "mood": avg("checkin.mood", 1),
                "workouts": len(wk),
                "workout_minutes": sum(w["minutes"] for w in wk),
            }
        )
    return out


def sleep_habits(conn: Connection, day: date) -> list[dict]:
    """Bedtime / wake time habits over the last 7 days: how often the window was hit."""
    hs = [h for h in list_habits(False, conn) if h["metric"] in ("sleep.bedtime", "sleep.wake_time")]
    return [
        {
            **{k: h[k] for k in ("id", "name", "metric", "min_value", "max_value")},
            "done": sum(1 for v in h["days"].values() if v["done"]),
            "measured": len(h["days"]),
        }
        for h in (evaluate(conn, hs, day - timedelta(days=6), day, day) if hs else [])
    ]


def insights(conn: Connection, tz: ZoneInfo, day: date, now: datetime) -> dict:
    """`now` bounds "hours since the last hard workout"; for a past day pass that morning."""
    first = day - timedelta(days=max(RECOVERY_LOOKBACK_DAYS + BASELINE_DAYS, WEEKS * 7 + 7))
    values = with_burn(conn, tz, first, day, daily(conn, tz, first, day))
    ns = nights(conn, tz, first, day)
    ws = workouts(conn, tz, first, day)
    return {
        "day": day,
        "readiness": readiness_with_garmin(conn, tz, day, now, values),
        "sleep_rhythm": {
            **sleep_rhythm(ns, day, values.get("daily.sleep_score", {})),
            "habits": sleep_habits(conn, day),
        },
        "recovery": recovery(values, ws, day),
        "energy": energy(values, day),
        "weeks": weeks(values, ns, ws, day),
    }


# --- review facts -------------------------------------------------------------------------------

FACT_METRICS = {
    "daily.sleep_score": 0,
    "daily.hrv_last_night_avg": 1,
    "resting_heart_rate": 1,
    "steps": 0,
    "active_energy": 0,
    "stress": 0,
    "nutrition.energy": 0,
    "nutrition.drinks": 0,
    "checkin.mood": 1,
    "checkin.energy": 1,
}


def period_facts(conn: Connection, tz: ZoneInfo, start: date, end: date) -> dict:
    """Aggregates for a weekly/monthly review, with the previous period of equal length alongside.

    Metric averages are over days that have a value (`days` says how many).
    """
    length = (end - start).days + 1
    prev_start = start - timedelta(days=length)
    values = daily(conn, tz, prev_start, end, keys=tuple(FACT_METRICS) + ("weight",))
    cur = list(_days(start, end))
    prev = list(_days(prev_start, start - timedelta(days=1)))

    metrics = {}
    for k, dec in FACT_METRICS.items():
        series = values.get(k, {})
        c = [series[d] for d in cur if d in series]
        p = [series[d] for d in prev if d in series]
        metrics[k] = {
            "avg": _r(_avg(c), dec),
            "min": _r(min(c), dec) if c else None,
            "max": _r(max(c), dec) if c else None,
            "days": len(c),
            "previous_avg": _r(_avg(p), dec),
        }
    weight = values.get("weight", {})
    wv = [weight[d] for d in cur if d in weight]

    ns = nights(conn, tz, start, end)
    ws = workouts(conn, tz, start, end)
    by_name: dict[str, int] = {}
    for w in ws:
        by_name[w["name"] or w["sport"] or "?"] = by_name.get(w["name"] or w["sport"] or "?", 0) + 1

    symptoms = conn.execute(
        text("""SELECT label, count(DISTINCT (ts AT TIME ZONE :tz)::date) AS days, max(value) AS max_severity
                FROM checkin WHERE kind = 'symptom' AND ts >= :s AND ts < :e
                GROUP BY label ORDER BY days DESC, label"""),
        {"tz": tz.key, "s": _local_midnight(start, tz), "e": _local_midnight(end + timedelta(days=1), tz)},
    )
    journal_days = conn.execute(
        text("SELECT count(*) FROM journal_day WHERE day BETWEEN :s AND :e AND text <> ''"),
        {"s": start, "e": end},
    ).scalar_one()

    habits = [
        {
            "name": h["name"],
            "done_days": sum(1 for v in h["days"].values() if v["done"]),
            "goal_days": round(h["per_week"] * length / 7),
        }
        for h in evaluate(conn, list_habits(False, conn), start, end, end)
    ]
    return {
        "period": {"start": start, "end": end, "days": length},
        "metrics": metrics,
        "weight": {"first": wv[0] if wv else None, "last": wv[-1] if wv else None, "days": len(wv)},
        "sleep": sleep_times([ns[d] for d in cur if d in ns]),
        "activities": {
            "count": len(ws),
            "minutes": sum(w["minutes"] for w in ws),
            "kcal": _r(sum(w["energy_kcal"] or 0 for w in ws)),
            "by_name": by_name,
        },
        "symptoms": [
            {"label": r.label, "days": r.days, "max_severity": _r(r.max_severity)} for r in symptoms
        ],
        "journal_days": journal_days,
        "habits": habits,
        "rewards": period_rewards(conn, start, end),
    }
