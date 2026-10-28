"""Daily energy burn: from the watch where it measured, otherwise estimated from the profile.

Every place that shows or reasons about "burned" uses `daily_burn`. Per day it returns the base
(resting metabolic rate from the watch, or BMR × PAL / the person's own estimate) and the active
part (the watch's active energy, or an estimate of that day's workouts), plus where it came from.

Estimates: BMR after Mifflin-St Jeor; workouts as net MET × body weight × hours (MET − 1, as the
base already contains the resting share), with METs from the Compendium of Physical Activities and
the intensity read from the session's RPE and exercise ratings.
"""

from __future__ import annotations

import bisect
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, text

from vitalocal import profile as profile_mod
from vitalocal.config import get_settings

GOAL_FACTOR = {"lose": 0.85, "keep": 1.0, "gain": 1.1}
SET_SECONDS = 150  # a working set incl. rest, for strength sessions without a duration
# (sport, sub_sport) → MET at moderate effort; sub_sport None = the sport's default
METS: dict[tuple[str, str | None], float] = {
    ("training", "strength_training"): 5.0,
    ("training", "cardio_training"): 7.0,
    ("training", "hiit"): 8.0,
    ("training", "yoga"): 2.5,
    ("training", "pilates"): 3.0,
    ("training", "flexibility_training"): 2.5,
    ("training", None): 5.0,
    ("fitness_equipment", "elliptical"): 5.0,
    ("fitness_equipment", "stair_climbing"): 8.0,
    ("fitness_equipment", "indoor_rowing"): 7.0,
    ("fitness_equipment", "indoor_cycling"): 7.0,
    ("fitness_equipment", None): 5.0,
    ("running", None): 9.8,
    ("cycling", None): 7.5,
    ("walking", None): 3.5,
    ("hiking", None): 6.0,
    ("swimming", None): 7.0,
    ("rowing", None): 7.0,
    ("cross_country_skiing", None): 9.0,
    ("climbing", None): 7.5,
    ("tennis", None): 7.3,
    ("soccer", None): 7.0,
    ("basketball", None): 6.5,
}
DEFAULT_MET = 4.5
# strength has its own Compendium values (light / moderate / vigorous); other sports are scaled
STRENGTH_METS = {"light": 3.5, "moderate": 5.0, "vigorous": 6.0}
INTENSITY_SCALE = {"light": 0.8, "moderate": 1.0, "vigorous": 1.2}


def age_on(birth_year: int, day: date) -> int:
    return day.year - birth_year


def bmr(sex: str, age: int, height_cm: float, weight_kg: float) -> float:
    """Mifflin-St Jeor resting energy (kcal/day)."""
    return 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "male" else -161)


def met_for(sport: str | None, sub_sport: str | None, intensity: str) -> float:
    sport, sub_sport = (sport or "").lower(), (sub_sport or "").lower() or None
    if sub_sport == "strength_training" or (sport == "training" and sub_sport in (None, "generic")):
        return STRENGTH_METS[intensity]
    base = METS.get((sport, sub_sport)) or METS.get((sport, None)) or DEFAULT_MET
    return base * INTENSITY_SCALE[intensity]


def intensity(rpe: float | None, hard_share: float | None, easy_share: float | None) -> str:
    if rpe is not None:
        return "vigorous" if rpe >= 8 else "light" if rpe <= 5 else "moderate"
    if hard_share is not None and hard_share >= 0.5:
        return "vigorous"
    if easy_share is not None and easy_share >= 0.5:
        return "light"
    return "moderate"


def workout_kcal(w: dict, weight_kg: float, bmr_kcal: float | None) -> float:
    """Active energy of one workout on top of the base: the watch's value minus its resting share,
    otherwise net MET × kg × hours."""
    hours = w["seconds"] / 3600
    if w["energy_kcal"]:
        resting = (bmr_kcal or 0) / 24 * hours
        return max(0.0, w["energy_kcal"] - resting)
    met = met_for(w["sport"], w["sub_sport"], w["intensity"])
    return max(0.0, (met - 1) * weight_kg * hours)


# --- data access --------------------------------------------------------------------------------


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, datetime.min.time(), tz)


def _weights(conn: Connection, before: datetime) -> tuple[list[datetime], list[float]]:
    rows = conn.execute(
        text("""SELECT ts, value FROM sample WHERE metric = 'weight' AND ts < :e
                UNION ALL
                SELECT ts, value FROM body_entry WHERE metric = 'weight' AND ts < :e
                ORDER BY ts"""),
        {"e": before},
    ).all()
    return [r.ts for r in rows], [r.value for r in rows]


def _weight_at(series: tuple[list[datetime], list[float]], at: datetime) -> float | None:
    i = bisect.bisect_left(series[0], at)
    return series[1][i - 1] if i else (series[1][0] if series[1] else None)


def workouts(conn: Connection, tz: ZoneInfo, first: date, last: date) -> dict[date, list[dict]]:
    """Workouts per local day with what the estimate needs (duration, sport, intensity)."""
    rows = conn.execute(
        text("""SELECT (a.start_ts AT TIME ZONE :tz)::date AS day, a.id, a.sport, a.sub_sport,
                       coalesce(a.timer_s, a.elapsed_s) AS seconds, a.energy_kcal,
                       (SELECT count(*) FROM activity_set s
                        LEFT JOIN activity_set_edit e
                          ON e.activity_id = s.activity_id AND e.set_index = s.set_index
                        WHERE s.activity_id = a.id AND s.set_type = 'active'
                          AND NOT coalesce(e.hidden, false) AND NOT coalesce(e.warmup, false))
                     + (SELECT count(*) FROM training_set t
                        WHERE t.activity_id = a.id AND NOT t.warmup) AS sets,
                       (SELECT value FROM checkin c WHERE c.kind = 'rpe' AND c.activity_id = a.id
                        ORDER BY ts DESC LIMIT 1) AS rpe,
                       (SELECT avg((rating = 'hard')::int) FROM exercise_rating r
                        WHERE r.activity_id = a.id) AS hard,
                       (SELECT avg((rating = 'easy')::int) FROM exercise_rating r
                        WHERE r.activity_id = a.id) AS easy
                FROM activity a WHERE a.start_ts >= :s AND a.start_ts < :e ORDER BY a.start_ts"""),
        {"tz": tz.key, "s": _midnight(first, tz), "e": _midnight(last + timedelta(days=1), tz)},
    )
    out: dict[date, list[dict]] = {}
    for r in rows:
        seconds = r.seconds or (r.sets or 0) * SET_SECONDS
        if not seconds:
            continue
        out.setdefault(r.day, []).append(
            {
                "id": r.id,
                "sport": r.sport,
                "sub_sport": r.sub_sport,
                "seconds": seconds,
                "energy_kcal": r.energy_kcal,
                "intensity": intensity(
                    r.rpe,
                    float(r.hard) if r.hard is not None else None,
                    float(r.easy) if r.easy is not None else None,
                ),
            }
        )
    return out


def daily_burn(
    conn: Connection, first: date, last: date, tz: ZoneInfo | None = None, watch: dict | None = None
) -> dict[date, dict]:
    """{day: {rest, active, total, source}} for first..last; days without any basis are left out.

    `watch` takes already loaded daily values ({metric: {day: value}}) to save a query."""
    from vitalocal.insights import daily  # insights builds on this module

    tz = tz or _tz()
    p = profile_mod.get_profile(conn)
    src = p["energy_source"]
    if watch is None:
        watch = daily(conn, tz, first, last, keys=("resting_metabolic_rate", "active_energy"))
    rmr, act = watch.get("resting_metabolic_rate", {}), watch.get("active_energy", {})
    weights = _weights(conn, _midnight(last + timedelta(days=1), tz))
    ws = workouts(conn, tz, first, last) if src != "watch" else {}
    complete = profile_mod.is_complete(p)

    out: dict[date, dict] = {}
    d = first
    while d <= last:
        if src in ("auto", "watch") and d in rmr and d in act:
            out[d] = _row(rmr[d], act[d], "watch")
        elif src != "watch":
            kg = _weight_at(weights, _midnight(d + timedelta(days=1), tz))
            b = bmr(p["sex"], age_on(p["birth_year"], d), p["height_cm"], kg) if complete and kg else None
            if src in ("manual", "auto") and p["manual_tdee_kcal"]:
                base, source = float(p["manual_tdee_kcal"]), "manual"
            elif b is not None:
                base, source = b * p["pal"], "estimate"
            else:
                base = None
            if base is not None:
                active = sum(workout_kcal(w, kg, b) for w in ws.get(d, [])) if kg else 0.0
                out[d] = _row(base, active, source)
        d += timedelta(days=1)
    return out


def _row(rest: float, active: float, source: str) -> dict:
    return {"rest": round(rest), "active": round(active), "total": round(rest + active), "source": source}


def profile_estimate(conn: Connection, p: dict | None = None) -> dict | None:
    """BMR, everyday burn and a suggested kcal target for the profile's goal."""
    from vitalocal.api.days import latest_weight

    p = p or profile_mod.get_profile(conn)
    kg = latest_weight(conn, datetime.now(UTC))
    today = datetime.now(_tz()).date()
    b = (
        bmr(p["sex"], age_on(p["birth_year"], today), p["height_cm"], kg)
        if profile_mod.is_complete(p) and kg
        else None
    )
    everyday = p["manual_tdee_kcal"] or (b * p["pal"] if b else None)
    burns = daily_burn(conn, today - timedelta(days=14), today - timedelta(days=1))
    avg = round(sum(x["total"] for x in burns.values()) / len(burns)) if len(burns) >= 7 else None
    basis = avg or everyday
    return {
        "weight_kg": kg,
        "bmr": round(b) if b else None,
        "everyday_kcal": round(everyday) if everyday else None,
        "avg_burn_14d": avg,
        "suggested_kcal": round(basis * GOAL_FACTOR[p["goal"]] / 10) * 10 if basis else None,
    }
