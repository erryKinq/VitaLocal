"""Deterministic fact sheets for AI summaries.

The dashboard already shows every number, so the model gets pre-digested signal instead of a
table: what is outside the personal usual range (percentiles of the last 28 days), the previous
day, the day's structure, training load, 7-day trends and descriptive 60-day patterns (with n).
Everything here is computed by VitaLocal; the model only interprets it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import mean, median

from sqlalchemy import Connection, text

FACTS_VERSION = 7
BASELINE_DAYS = 28
PATTERN_DAYS = 60
MIN_HISTORY = 10  # days needed before "usual range" statements are made
MIN_GROUP = 3  # per group in a pattern comparison

# metric -> (label, unit, decimals)
KEY_METRICS = {
    "daily.sleep_score": ("sleep score", "", 0),
    "daily.hrv_last_night_avg": ("HRV last night", "ms", 0),
    "resting_heart_rate": ("resting heart rate", "bpm", 0),
    "stress": ("average stress", "", 0),
    "body_energy": ("body energy peak", "", 0),
    "steps": ("steps", "", 0),
    "active_energy": ("active energy", "kcal", 0),
    "intensity_minutes_moderate": ("moderate intensity", "min", 0),
    "intensity_minutes_vigorous": ("vigorous intensity", "min", 0),
    "respiratory_rate": ("respiration", "brpm", 1),
    "daily.skin_temp_deviation": ("skin temperature deviation", "°C", 2),
    "daily.sleep_avg_stress": ("stress during sleep", "", 0),
    "nutrition.energy": ("energy intake", "kcal", 0),
    "nutrition.protein": ("protein", "g", 0),
    "weight": ("weight", "kg", 1),
    "checkin.mood": ("self-rated mood (1–5)", "", 1),
    "checkin.energy": ("self-rated energy (1–5)", "", 1),
}
# Smallest difference from the personal median that is worth mentioning (below that: noise).
MIN_DIFF = {
    "daily.sleep_score": 5,
    "daily.hrv_last_night_avg": 3,
    "resting_heart_rate": 2,
    "stress": 3,
    "body_energy": 5,
    "steps": 1000,
    "active_energy": 75,
    "intensity_minutes_moderate": 10,
    "intensity_minutes_vigorous": 5,
    "respiratory_rate": 0.5,
    "daily.skin_temp_deviation": 0.2,
    "daily.sleep_avg_stress": 3,
    "nutrition.energy": 150,
    "nutrition.protein": 15,
    "weight": 0.4,
    "checkin.mood": 0.5,
    "checkin.energy": 0.5,
}
YESTERDAY_KEYS = ("steps", "active_energy", "stress", "nutrition.energy", "intensity_minutes_vigorous")
TREND_KEYS = (
    "daily.sleep_score",
    "daily.hrv_last_night_avg",
    "resting_heart_rate",
    "stress",
    "steps",
    "nutrition.energy",
    "weight",
    "checkin.mood",
)
DAYPARTS = (("night", 0, 6), ("morning", 6, 12), ("afternoon", 12, 18), ("evening", 18, 24))
TRAINING_MIN_MINUTES = 20


NOT_SHARED = "not shared with this AI model"


def _shared(shared: dict | None, what: str) -> bool:
    """`shared` lists what an external model may see; None (local model, tests) means everything."""
    return shared is None or bool(shared.get(what))


def _r(v, nd=1):
    return None if v is None else round(v, nd)


def _pct(sorted_vals: list[float], p: float) -> float:
    k = (len(sorted_vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _daily(conn, keys, start: date, end_excl: date) -> dict:
    from vitalocal.api import days as days_api

    tz = days_api._tz()
    raw = days_api._daily_values(
        conn, list(keys), days_api._local_midnight(start, tz), days_api._local_midnight(end_excl, tz)
    )
    return {
        k: {date.fromisoformat(d): v["value"] for d, v in vals.items() if v["value"] is not None}
        for k, vals in raw.items()
    }


def deviation(
    value: float | None, history: list[float], decimals: int = 0, min_diff: float = 0
) -> dict | None:
    """Place a value within the personal distribution of the history (None if not enough).

    Outside p10–p90 is "clearly", outside p25–p75 "slightly" above/below usual – but only if the
    distance to the median reaches `min_diff`, so narrow ranges do not turn noise into findings.
    """
    if value is None or len(history) < MIN_HISTORY:
        return None
    s = sorted(history)
    p10, p25, p75, p90 = (_pct(s, p) for p in (0.10, 0.25, 0.75, 0.90))
    if abs(value - median(s)) < min_diff:
        strength = None
    elif value > p90 or value < p10:
        strength = "clearly"
    elif value > p75 or value < p25:
        strength = "slightly"
    else:
        strength = None
    return {
        "usual_range_p25_p75": [_r(p25, decimals), _r(p75, decimals)],
        "median": _r(median(s), decimals),
        "position": None if strength is None else f"{strength} {'above' if value > p75 else 'below'} usual",
    }


def training_days(conn, start: date, end: date, tz) -> dict[date, list[dict]]:
    rows = conn.execute(
        text("""SELECT (start_ts AT TIME ZONE :tz)::date AS day, id,
                       coalesce((SELECT t.title FROM training_session t WHERE t.activity_id = a.id),
                                sport_profile_name, name, sub_sport, sport) AS name, sport, sub_sport,
                       elapsed_s, avg_hr, energy_kcal,
                       (SELECT count(*) FROM activity_set s
                        WHERE s.activity_id = a.id AND s.set_type = 'active') AS sets,
                       (SELECT value FROM checkin c WHERE c.kind = 'rpe' AND c.activity_id = a.id
                        ORDER BY ts DESC LIMIT 1) AS rpe,
                       fit_attrs
                FROM activity a
                WHERE start_ts >= :s AND start_ts < :e ORDER BY start_ts"""),
        {
            "tz": tz.key,
            "s": datetime.combine(start, datetime.min.time(), tz),
            "e": datetime.combine(end + timedelta(days=1), datetime.min.time(), tz),
        },
    )
    out: dict[date, list[dict]] = {}
    for r in rows:
        out.setdefault(r.day, []).append(
            {
                "name": r.name,
                "minutes": _r((r.elapsed_s or 0) / 60, 0),
                "avg_hr": r.avg_hr,
                "kcal": _r(r.energy_kcal, 0),
                "active_sets": r.sets or None,
                "rpe_1_10": r.rpe,
                "aerobic_te": (r.fit_attrs or {}).get("total_training_effect"),
                "anaerobic_te": (r.fit_attrs or {}).get("total_anaerobic_training_effect"),
            }
        )
    return out


def _is_training_day(acts: list[dict] | None) -> bool:
    return bool(acts) and sum(a["minutes"] or 0 for a in acts) >= TRAINING_MIN_MINUTES


def patterns(conn, end: date, tz) -> list[dict]:
    """Descriptive comparisons over the last PATTERN_DAYS (no causality, small n stated)."""
    start = end - timedelta(days=PATTERN_DAYS)
    d = _daily(
        conn,
        [
            "daily.sleep_score",
            "daily.hrv_last_night_avg",
            "stress",
            "checkin.mood",
            "nutrition.energy",
            "daily.sleep_avg_stress",
        ],
        start,
        end + timedelta(days=1),
    )
    train = training_days(conn, start, end, tz)
    days = [start + timedelta(days=i) for i in range(PATTERN_DAYS + 1)]
    out = []

    def compare(desc, metric, split, shift=0, decimals=0):
        a, b = [], []
        for day in days:
            v = d.get(metric, {}).get(day + timedelta(days=shift))
            flag = split(day)
            if v is None or flag is None:
                continue
            (a if flag else b).append(v)
        if len(a) >= MIN_GROUP and len(b) >= MIN_GROUP:
            out.append(
                {
                    "pattern": desc,
                    "with": _r(mean(a), decimals),
                    "without": _r(mean(b), decimals),
                    "n_with": len(a),
                    "n_without": len(b),
                }
            )

    trained = lambda day: _is_training_day(train.get(day))  # noqa: E731
    compare(
        "sleep score in the night after a training day vs. after a rest day",
        "daily.sleep_score",
        trained,
        shift=1,
    )
    compare(
        "HRV in the night after a training day vs. after a rest day",
        "daily.hrv_last_night_avg",
        trained,
        shift=1,
    )
    stress = d.get("stress", {})
    if len(stress) >= 2 * MIN_GROUP:
        cut = median(stress.values())
        compare(
            f"sleep score after days with average stress above {cut:.0f} vs. below",
            "daily.sleep_score",
            lambda day: None if day not in stress else stress[day] > cut,
            shift=1,
        )
    sleep = d.get("daily.sleep_score", {})
    if len(sleep) >= 2 * MIN_GROUP:
        cut = median(sleep.values())
        compare(
            f"self-rated mood on days after a sleep score above {cut:.0f} vs. below",
            "checkin.mood",
            lambda day: None if day not in sleep else sleep[day] > cut,
            decimals=1,
        )
    compare("energy intake on training days vs. rest days", "nutrition.energy", trained)
    return out


def _goal_facts(goals: list[dict]) -> list[dict] | str:
    out = []
    for g in goals:
        row = {
            "goal": g["label"],
            "value": _r(g["value"], g["decimals"]),
            "unit": g["unit"],
            "min": g["min_value"],
            "max": g["max_value"],
            "status": g["status"] or "no value yet",
        }
        if g.get("formula"):
            f = g["formula"]
            row["note"] = f"goal = body weight × ml/kg ({f['base_ml']} ml) + training ({f['training_ml']} ml)"
        out.append(row)
    return out or "none set"


def _supplement_facts(st: dict) -> dict | str:
    if not st["supplements"]:
        return "none taken regularly"
    return {
        "taken": [
            {
                "name": s["name"] + (f" ({s['brand']})" if s["brand"] else ""),
                "units": f"{s['count']} of {s['per_day']} {s['form']}",
                "days_complete_last_30": s["done_last_30"],
            }
            for s in st["supplements"]
        ],
        "ingredients_today": [
            {
                "ingredient": i["label"],
                "amount": f"{i['amount']:g} {i['unit']}",
                **({"efsa_upper_level": f"{i['upper_level']:g} {i['unit']}"} if i["upper_level"] else {}),
                **({"above_upper_level": True} if i["above_upper_level"] else {}),
            }
            for i in st["ingredients"]
        ]
        or "none recorded",
    }


def _habit_facts(h: dict, day: date) -> dict:
    e = h["days"].get(day.isoformat()) or {}
    row = {
        "name": h["name"],
        "today": e.get("done"),
        "last_30_days": h["done_last_30"],
        "streak": h["streak"],
        "streak_unit": h["streak_unit"],
    }
    if e.get("display"):  # clock habits: the time and how far outside the window
        row["time"] = e["display"]
        if e.get("delta"):
            row["minutes_outside_window"] = e["delta"]
    return row


def day_facts(conn: Connection, day: date, shared: dict | None = None) -> dict:
    from vitalocal.api import days as days_api
    from vitalocal.api import habits as habits_api
    from vitalocal.api import labs as labs_api
    from vitalocal.api import supplements as supplements_api
    from vitalocal.api import training as training_api

    tz = days_api._tz()
    d = days_api.get_day(day, conn)
    hist = _daily(conn, KEY_METRICS, day - timedelta(days=BASELINE_DAYS), day)
    y = _daily(conn, YESTERDAY_KEYS, day - timedelta(days=1), day)
    recent = _daily(conn, TREND_KEYS, day - timedelta(days=BASELINE_DAYS), day + timedelta(days=1))

    notable, usual, missing = [], [], []
    for key, (label, unit, nd) in KEY_METRICS.items():
        cur = d["summary"].get(key, {}).get("value")
        if cur is None:
            if key in (
                "daily.sleep_score",
                "daily.hrv_last_night_avg",
                "resting_heart_rate",
                "steps",
                "stress",
            ):
                missing.append(label)
            continue
        dev = deviation(cur, list(hist.get(key, {}).values()), nd, MIN_DIFF.get(key, 0))
        if dev and dev["position"]:
            notable.append({"metric": label, "value": _r(cur, nd), "unit": unit, **dev})
        elif dev:
            usual.append(label)
    notable.sort(key=lambda n: 0 if n["position"].startswith("clearly") else 1)

    # day structure: stress by part of day, body energy course
    stress_parts = {}
    for name, h0, h1 in DAYPARTS:
        vals = [v for t, v in d["series"]["stress"] if h0 <= datetime.fromtimestamp(t / 1000, tz).hour < h1]
        if len(vals) >= 30:
            stress_parts[name] = _r(mean(vals), 0)
    be = [v for _, v in d["series"]["body_energy"]]
    body_energy = {"start": be[0], "min": min(be), "max": max(be), "end": be[-1]} if be else None

    sl = d["sleep"]
    sleep = None
    if sl["segments"]:
        closed = [s for s in sl["segments"] if s["end"]]
        sleep = {
            "asleep_h": _r((sl["asleep_minutes"] or 0) / 60, 1),
            "bed": closed[0]["start"].astimezone(tz).strftime("%H:%M") if closed else None,
            "wake": closed[-1]["end"].astimezone(tz).strftime("%H:%M") if closed else None,
            "stages_min": sl["minutes"],
            "garmin_sub_scores": {
                k.removeprefix("daily.sleep_").removesuffix("_score"): _r(v["value"], 0)
                for k, v in d["summary"].items()
                if k.startswith("daily.sleep_") and k.endswith("_score")
            },
            "awakenings": _r(d["summary"].get("daily.sleep_awakenings", {}).get("value"), 0),
        }

    train = training_days(conn, day - timedelta(days=14), day, tz)
    past = sorted(k for k in train if k < day and _is_training_day(train[k]))
    load = _daily(
        conn,
        ["training_load_acute", "training_load_chronic"],
        day - timedelta(days=3),
        day + timedelta(days=1),
    )
    acute = max(load.get("training_load_acute", {}).items(), default=(None, None))[1]
    chronic = max(load.get("training_load_chronic", {}).items(), default=(None, None))[1]
    training = {
        "today": train.get(day) or "none",
        "yesterday": train.get(day - timedelta(days=1)) or "none",
        "days_since_last_training": (day - past[-1]).days if past else None,
        "training_days_last_7": sum(
            1 for k in train if day - timedelta(days=7) < k <= day and _is_training_day(train[k])
        ),
        "acute_chronic_load_ratio": _r(acute / chronic, 2) if acute and chronic else None,
        "load_ratio_reading": "Garmin acute vs. chronic training load; commonly read as <0.8 low/detraining, "
        "0.8–1.3 balanced, >1.5 sharp increase",
    }

    s = d["summary"]
    intake = s.get("nutrition.energy", {}).get("value")
    burned = (d["energy"] or {}).get("total")  # watch, or estimated from profile + training
    weight = (
        s.get("weight", {}).get("value")
        or next(iter(sorted(recent.get("weight", {}).items(), reverse=True)), (None, None))[1]
    )
    protein = s.get("nutrition.protein", {}).get("value")
    nutrition = None
    if intake is not None:
        nutrition = {
            "intake_kcal": _r(intake, 0),
            "burned_estimate_kcal": _r(burned, 0),
            "burned_source": (d["energy"] or {}).get("source"),
            "balance_kcal": _r(intake - burned, 0) if burned else None,
            "protein_g_per_kg": _r(protein / weight, 2) if protein and weight else None,
        }

    trends = {}
    for key in TREND_KEYS:
        vals = recent.get(key, {})
        last7 = [v for k, v in vals.items() if k > day - timedelta(days=7)]
        prior = [v for k, v in vals.items() if k <= day - timedelta(days=7)]
        if len(last7) >= 4 and len(prior) >= 7:
            m7, mp = mean(last7), mean(prior)
            spread = (max(prior) - min(prior)) or 1
            change = (m7 - mp) / spread
            trends[KEY_METRICS[key][0]] = {
                "last_7d_mean": _r(m7, KEY_METRICS[key][2]),
                "previous_mean": _r(mp, KEY_METRICS[key][2]),
                "direction": "rising" if change > 0.15 else "falling" if change < -0.15 else "stable",
            }

    j = d["journal"]
    habits = habits_api.evaluate(
        conn, habits_api.list_habits(False, conn), day - timedelta(days=30), day, day
    )
    return {
        "facts_version": FACTS_VERSION,
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "data_quality": {
            "heart_rate_coverage_pct": round(
                d["coverage"]["heart_rate_minutes"] / d["coverage"]["minutes_in_day"] * 100
            ),
            "not_measured": missing or "nothing important missing",
        },
        "notable_vs_usual": notable or "nothing outside the usual range",
        "within_usual_range": usual,
        "last_night": sleep or "no sleep recorded",
        "day_structure": {"average_stress_by_part_of_day": stress_parts or None, "body_energy": body_energy},
        "training": training,
        "yesterday": {
            KEY_METRICS[k][0]: _r(v.get(day - timedelta(days=1)), KEY_METRICS[k][2])
            for k, v in y.items()
            if v.get(day - timedelta(days=1)) is not None
        }
        or "no data",
        "nutrition": nutrition or "not logged",
        "goals": _goal_facts(d["goals"]),
        "supplements": _supplement_facts(supplements_api.day_status(conn, day, history_days=30)),
        "lab_results": labs_api.lab_facts(conn, day, new_days=14) if _shared(shared, "labs") else NOT_SHARED,
        "strength_training": training_api.training_facts(conn, day, days=7),
        "trends_7d_vs_previous_3w": trends,
        "patterns_last_60d": patterns(conn, day, tz) or "not enough data",
        "self_report": {
            "check_ins": [
                {
                    "kind": c["kind"],
                    "value": c["value"],
                    "label": c["label"],
                    "note": c["note"] if _shared(shared, "journal") else None,
                }
                for c in j["checkins"]
                if c["kind"] in ("mood", "energy", "symptom")
                or (c["kind"] == "note" and _shared(shared, "journal"))
            ]
            or "none",
            "journal": ((j["text"] or "")[:2000] or "no entry") if _shared(shared, "journal") else NOT_SHARED,
        },
        "habits": [_habit_facts(h, day) for h in habits] or "none defined",
    }


def week_facts(conn: Connection, end: date, shared: dict | None = None) -> dict:
    from vitalocal.api import days as days_api
    from vitalocal.api import habits as habits_api

    tz = days_api._tz()
    start = end - timedelta(days=6)
    week = _daily(conn, KEY_METRICS, start, end + timedelta(days=1))
    before = _daily(conn, KEY_METRICS, start - timedelta(days=BASELINE_DAYS), start)
    train = training_days(conn, start, end, tz)

    per_day = []
    for i in range(7):
        day = start + timedelta(days=i)
        row = {"date": day.isoformat(), "weekday": day.strftime("%a")}
        for key in (
            "daily.sleep_score",
            "daily.hrv_last_night_avg",
            "resting_heart_rate",
            "stress",
            "steps",
            "nutrition.energy",
            "checkin.mood",
        ):
            v = week.get(key, {}).get(day)
            if v is not None:
                row[KEY_METRICS[key][0]] = _r(v, KEY_METRICS[key][2])
        if train.get(day):
            row["training"] = [
                f"{a['name']} {a['minutes']:.0f} min" + (f" RPE {a['rpe_1_10']:.0f}" if a["rpe_1_10"] else "")
                for a in train[day]
            ]
        per_day.append(row)

    notable, usual = [], []
    for key, (label, unit, nd) in KEY_METRICS.items():
        vals = list(week.get(key, {}).values())
        if len(vals) < 3:
            continue
        dev = deviation(mean(vals), list(before.get(key, {}).values()), nd, MIN_DIFF.get(key, 0) / 2)
        if dev and dev["position"]:
            notable.append(
                {
                    "metric": label,
                    "week_mean": _r(mean(vals), nd),
                    "unit": unit,
                    "days_measured": len(vals),
                    **dev,
                }
            )
        elif dev:
            usual.append(label)

    journal = conn.execute(
        text("SELECT day, left(text, 500) AS text FROM journal_day WHERE day BETWEEN :s AND :e ORDER BY day"),
        {"s": start, "e": end},
    )
    symptoms = conn.execute(
        text("""SELECT label, count(*) AS n, max(value) AS max_severity FROM checkin
                WHERE kind = 'symptom' AND ts >= :s AND ts < :e GROUP BY label ORDER BY n DESC"""),
        {
            "s": datetime.combine(start, datetime.min.time(), tz),
            "e": datetime.combine(end + timedelta(days=1), datetime.min.time(), tz),
        },
    )
    habits = habits_api.evaluate(conn, habits_api.list_habits(False, conn), start, end, end)
    from vitalocal.api.goals import goals_of_day
    from vitalocal.api.labs import lab_facts
    from vitalocal.api.supplements import day_status
    from vitalocal.api.training import training_facts

    goal_week: dict[str, dict] = {}
    for i in range(7):
        for g in goals_of_day(conn, start + timedelta(days=i)):
            empty = {"within": 0, "below": 0, "above": 0, "days_with_value": 0}
            row = goal_week.setdefault(g["label"], empty)
            if g["status"]:
                row[g["status"]] += 1
                row["days_with_value"] += 1
    return {
        "facts_version": FACTS_VERSION,
        "period": f"{start.isoformat()} – {end.isoformat()}",
        "notable_week_vs_previous_4_weeks": notable or "nothing outside the usual range",
        "within_usual_range": usual,
        "days": per_day,
        "training_days": sum(1 for v in train.values() if _is_training_day(v)),
        "patterns_last_60d": patterns(conn, end, tz) or "not enough data",
        "symptoms": [dict(r._mapping) for r in symptoms] or "none",
        "journal": ([{"date": r.day.isoformat(), "text": r.text} for r in journal] or "no entries")
        if _shared(shared, "journal")
        else NOT_SHARED,
        "goals_days_below_within_above": goal_week or "none set",
        "supplements_days_complete": {
            s["name"]: sum(1 for d, v in s["days"].items() if v["done"] and d >= start.isoformat())
            for s in day_status(conn, end, history_days=7)["supplements"]
        }
        or "none taken regularly",
        "lab_results": lab_facts(conn, end, new_days=7) if _shared(shared, "labs") else NOT_SHARED,
        "strength_training": training_facts(conn, end, days=7),
        "habits": [
            {
                "name": h["name"],
                "days_done": sum(1 for v in h["days"].values() if v["done"]),
                "goal_per_week": h["per_week"],
            }
            for h in habits
        ]
        or "none defined",
    }
