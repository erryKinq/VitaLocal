"""Compact day overview for mobile clients: KPIs with baseline, drinks, habits and sync state.

`/days/{day}` carries minute series for the charts; this is the same day in a few kilobytes.
"""

from datetime import UTC, date, datetime, timedelta
from statistics import median

from fastapi import APIRouter, Query
from sqlalchemy import Connection, text

from vitalocal import energy
from vitalocal.api.days import (
    SLEEP_WINDOW,
    _activities,
    _daily_values,
    _local_midnight,
    _nutrition,
    _sleep,
    _tz,
    sync_state,
)
from vitalocal.api.drinks import drinks_of_day
from vitalocal.api.goals import goals_of_day
from vitalocal.api.habit_rewards import compact_rewards
from vitalocal.api.habits import evaluate, list_habits
from vitalocal.api.supplements import day_status
from vitalocal.api.training import next_workout
from vitalocal.db import CONN
from vitalocal.metrics import METRICS

router = APIRouter(prefix="/api/v1", tags=["days"])

# Daily values offered to the app, in display order (mirrors the WebUI day KPIs).
TODAY_METRICS = (
    "daily.sleep_score",
    "daily.hrv_last_night_avg",
    "resting_heart_rate",
    "steps",
    "active_energy",
    "resting_metabolic_rate",
    "intensity_minutes_moderate",
    "intensity_minutes_vigorous",
    "stress",
    "body_energy",
    "spo2",
    "respiratory_rate",
    "daily.skin_temp_deviation",
    "nutrition.energy",
    "nutrition.drinks",
    "checkin.mood",
    "checkin.energy",
    "weight",
)
BASELINE_DAYS = 28
HABIT_HISTORY_DAYS = 365


def compact_training(nxt: dict | None) -> dict | None:
    """Next workout of the active plan for the phone: exercise, current target and suggestion."""
    if nxt is None:
        return None
    return {
        "plan": nxt["plan"],
        "plan_day": nxt["plan_day"],
        "unreviewed_sessions": nxt["unreviewed_sessions"],
        "exercises": [
            {
                "exercise": e["exercise"],
                "target": e["current"],
                "next": e["next"]["text"] if e["differs"] else None,
            }
            for e in nxt["exercises"]
        ],
    }


def compact_supplements(st: dict) -> dict:
    """Supplements without the per-day history (mobile payload)."""
    keep = (
        "id",
        "name",
        "brand",
        "form",
        "per_day",
        "per_week",
        "timing",
        "count",
        "done",
        "week_count",
        "week_done",
        "streak",
        "streak_unit",
        "done_last_30",
    )
    return {
        "items": [{k: s[k] for k in keep} for s in st["supplements"]],
        "ingredients": [{k: v for k, v in i.items() if k != "from"} for i in st["ingredients"]],
    }


@router.get("/today")
def today(
    day: date | None = Query(None, description="Local day (defaults to today in the server time zone)"),
    conn: Connection = CONN,
) -> dict:
    tz = _tz()
    day = day or datetime.now(tz).date()
    start, end = _local_midnight(day, tz), _local_midnight(day + timedelta(days=1), tz)

    keys = list(TODAY_METRICS)
    current = _daily_values(conn, keys, start, end)
    history = _daily_values(conn, keys, _local_midnight(day - timedelta(days=BASELINE_DAYS), tz), start)
    metrics = {}
    for k in keys:
        m = METRICS[k]
        past = [v["value"] for v in history.get(k, {}).values() if v["value"] is not None]
        metrics[k] = {
            "value": current.get(k, {}).get(day.isoformat(), {}).get("value"),
            "label": m.label,
            "unit": m.unit,
            "decimals": m.decimals,
            "better": m.better,
            "baseline": median(past) if past else None,
            "baseline_days": len(past),
        }

    sleep = _sleep(
        conn,
        datetime.combine(day - timedelta(days=1), SLEEP_WINDOW[0], tzinfo=tz),
        datetime.combine(day, SLEEP_WINDOW[1], tzinfo=tz),
    )
    checkins = [
        dict(r._mapping)
        for r in conn.execute(
            text("""SELECT id, ts, kind, value, label, note, activity_id, source FROM checkin
                    WHERE ts >= :s AND ts < :e ORDER BY ts"""),
            {"s": start, "e": end},
        )
    ]
    drinks = drinks_of_day(conn, day)
    goals = goals_of_day(conn, day)
    drink_goal = next((g for g in goals if g["metric"] == "nutrition.drinks"), None)

    nutrition = _nutrition(conn, day)
    habits = evaluate(conn, list_habits(False, conn), day - timedelta(days=HABIT_HISTORY_DAYS), day, day)

    return {
        "day": day,
        "timezone": tz.key,
        "generated_at": datetime.now(UTC),
        "metrics": metrics,
        # burned energy: watch, or estimated from the profile and the day's workouts (null: no basis)
        "energy": energy.daily_burn(conn, day, day, tz).get(day),
        "sleep": {k: sleep[k] for k in ("asleep_minutes", "bed_start", "bed_end", "minutes")},
        "drinks": {
            "total_ml": drinks["total_ml"],
            # the day's goal from the formula (null when switched off), with its parts
            "goal_ml": drink_goal["min_value"] if drink_goal else None,
            "goal": drink_goal["formula"] if drink_goal else None,
            "by_drink": drinks["by_drink"],
            "by_label": drinks["by_drink"],  # deprecated alias (app ≤ 0.2)
            "entries": [
                # label/value: deprecated aliases of drink/ml (app ≤ 0.2)
                {
                    **{k: e[k] for k in ("id", "ts", "drink", "ml", "caffeine", "source")},
                    "label": e["drink"],
                    "value": e["ml"],
                }
                for e in drinks["entries"]
            ],
        },
        "checkins": checkins,
        "goals": goals,
        "supplements": compact_supplements(day_status(conn, day, history_days=60)),
        "training": compact_training(next_workout(conn)),
        "nutrition": (
            {
                "entries": nutrition["entries"],
                "totals": {k: round(v["value"], 3) for k, v in nutrition["totals"].items()},
            }
            if nutrition
            else None
        ),
        "activities": _activities(conn, start, end),
        "habits": [
            {
                **{
                    k: h[k]
                    for k in (
                        "id",
                        "name",
                        "icon",
                        "kind",
                        "unit",
                        "target",
                        "per_week",
                        "metric",
                        "min_value",
                        "max_value",
                        "streak",
                        "streak_unit",
                    )
                },
                "this_week": h.get("this_week"),
                "jokers": h.get("jokers", 0),
                "hard": h["hard"],
                "today": h["days"].get(day.isoformat()),
            }
            for h in habits
        ],
        "habit_rewards": compact_rewards(conn),
        "sync": sync_state(conn),
    }
