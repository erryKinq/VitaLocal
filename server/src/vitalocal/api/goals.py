"""Daily goals under nutrition: nutrient goals and the drink goal.

Nutrient goals are set in the WebUI or by an agent (`PUT /goals/{metric}`); until the
Waistline takeover, a goal changed in Waistline is taken over with its next export. Every
change is a new version valid from its day, so a past day is judged by the goal that applied
then. The drink goal is computed per day from a formula:
body weight × ml/kg plus a supplement per hour of recorded activity.
"""

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, text

from vitalocal.api.days import _daily_values, _local_midnight, _nutrition, _tz, latest_weight
from vitalocal.db import CONN
from vitalocal.metrics import METRICS
from vitalocal.nutrients import METRIC_KEY

router = APIRouter(prefix="/api/v1", tags=["nutrition"])

# Metrics a goal can be set for, in display order.
GOAL_METRICS = (
    "nutrition.energy",
    "nutrition.protein",
    "nutrition.carbohydrates",
    "nutrition.fat",
    "nutrition.saturated_fat",
    "nutrition.sugars",
    "nutrition.fiber",
    "nutrition.salt",
    "nutrition.caffeine",
    "nutrition.alcohol",
)
DRINKS = "nutrition.drinks"
DRINK_GOAL_KEY = "goals.drinks"
_NO_DATE = "DATE '0001-01-01'"


# --- drink goal ------------------------------------------------------------------------------------


class DrinkGoalSettings(BaseModel):
    enabled: bool = True
    ml_per_kg: float = Field(35, ge=0, le=100)
    ml_per_training_hour: float = Field(500, ge=0, le=3000)
    fallback_ml: float = Field(2000, ge=0, le=10000, description="Base when no weight is known")


def drink_settings(conn: Connection) -> DrinkGoalSettings:
    raw = conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": DRINK_GOAL_KEY}).scalar()
    return DrinkGoalSettings(**(raw or {}))


def drink_goal(conn: Connection, day: date, s: DrinkGoalSettings | None = None) -> dict | None:
    """The day's drink goal with its parts (rounded to 100 ml), or None when switched off.

    Weight: the latest value up to the end of the day. Training: the duration of all activities
    that started that day, so the goal rises once a workout is synced.
    """
    s = s or drink_settings(conn)
    if not s.enabled:
        return None
    tz = _tz()
    start, end = _local_midnight(day, tz), _local_midnight(day + timedelta(days=1), tz)
    weight = latest_weight(conn, end)
    seconds = conn.execute(
        text("SELECT coalesce(sum(elapsed_s), 0) FROM activity WHERE start_ts >= :s AND start_ts < :e"),
        {"s": start, "e": end},
    ).scalar_one()
    base = weight * s.ml_per_kg if weight else s.fallback_ml
    training = seconds / 3600 * s.ml_per_training_hour
    return {
        "goal_ml": round((base + training) / 100) * 100,
        "base_ml": round(base),
        "training_ml": round(training),
        "weight_kg": weight,
        "training_min": round(seconds / 60),
        "settings": s.model_dump(),
    }


# --- versions ------------------------------------------------------------------------------------------


def effective_goals(conn: Connection, day: date, source: str | None = None) -> dict[str, dict]:
    """The goal version valid on `day` per metric (removed goals included, both bounds None)."""
    rows = conn.execute(
        text("""SELECT DISTINCT ON (metric) metric, min_value, max_value, valid_from, source FROM goal
                WHERE (valid_from IS NULL OR valid_from <= :d)
                  AND (CAST(:src AS text) IS NULL OR source = :src)
                ORDER BY metric, valid_from DESC NULLS LAST, id DESC"""),
        {"d": day, "src": source},
    )
    return {r.metric: dict(r._mapping) for r in rows}


def _store_version(conn: Connection, metric: str, lo, hi, valid_from: date | None, source: str) -> None:
    conn.execute(
        text(f"""INSERT INTO goal (metric, min_value, max_value, valid_from, source)
                 VALUES (:m, :lo, :hi, :vf, :src)
                 ON CONFLICT (metric, (coalesce(valid_from, {_NO_DATE})))
                 DO UPDATE SET min_value = EXCLUDED.min_value, max_value = EXCLUDED.max_value,
                               source = EXCLUDED.source, created_at = now()"""),
        {"m": metric, "lo": lo, "hi": hi, "vf": valid_from, "src": source},
    )


def sync_goals(conn: Connection, source: str, goals: dict[str, tuple], day: date) -> list[str]:
    """Take over a snapshot of goals (a Waistline export) as new versions where it changed.

    Compared with the source's own latest version, so a goal set elsewhere in the meantime stays
    until the source really changes. The very first version of a metric applies retroactively.
    """
    current = effective_goals(conn, day, source)
    changed = []
    for metric in sorted(set(goals) | set(current)):
        new = tuple(goals.get(metric, (None, None)))
        cur = current.get(metric)
        if (cur and (cur["min_value"], cur["max_value"]) == new) or (cur is None and new == (None, None)):
            continue
        known = conn.execute(text("SELECT 1 FROM goal WHERE metric = :m LIMIT 1"), {"m": metric}).first()
        _store_version(conn, metric, *new, day if known else None, source)
        changed.append(metric)
    return changed


# --- status of a day -----------------------------------------------------------------------------------


def _status(value, lo, hi) -> str | None:
    if value is None:
        return None
    if lo is not None and value < lo:
        return "below"
    return "above" if hi is not None and value > hi else "within"


def goals_of_day(conn: Connection, day: date) -> list[dict]:
    """Every goal valid on `day` with the day's value and whether it is below, within or above.

    Nutrient values are the diary totals (an entry without the nutrient counts as 0); `missing`
    says how many entries do not declare it. `drinks` comes from the formula (`formula`).
    """
    tz = _tz()
    goals = {
        m: g
        for m, g in effective_goals(conn, day).items()
        if g["min_value"] is not None or g["max_value"] is not None
    }
    dg = drink_goal(conn, day)
    metrics = [m for m in GOAL_METRICS if m in goals] + [m for m in goals if m not in GOAL_METRICS]
    if dg:
        metrics.append(DRINKS)
    if not metrics:
        return []
    start, end = _local_midnight(day, tz), _local_midnight(day + timedelta(days=1), tz)
    daily = _daily_values(conn, metrics, start, end)
    nutrition = _nutrition(conn, day) or {}
    totals = nutrition.get("totals", {})
    out = []
    for m in metrics:
        d = METRICS.get(m)
        value = daily.get(m, {}).get(day.isoformat(), {}).get("value")
        entry_total = totals.get(METRIC_KEY.get(m), {})
        g = goals.get(m) or {
            "min_value": dg["goal_ml"], "max_value": None, "valid_from": None, "source": "formula"
        }  # fmt: skip
        entry = {
            "metric": m,
            "label": d.label if d else m,
            "unit": d.unit if d else None,
            "decimals": d.decimals if d else 0,
            **{k: g[k] for k in ("min_value", "max_value", "valid_from", "source")},
            "value": value,
            "status": _status(value, g["min_value"], g["max_value"]),
            "incomplete": False,  # kept for older clients: missing values count as 0
            "missing": entry_total.get("missing", 0),
        }
        if m == DRINKS:
            entry["formula"] = {k: v for k, v in dg.items() if k != "settings"}
        out.append(entry)
    return out


# --- API --------------------------------------------------------------------------------------------------


@router.get("/goals")
def get_goals(
    day: date | None = Query(None, description="Local day (defaults to today)"),
    conn: Connection = CONN,
) -> dict:
    day = day or datetime.now(_tz()).date()
    return {"day": day, "goals": goals_of_day(conn, day)}


@router.get("/goals/history")
def goal_history(metric: str | None = None, conn: Connection = CONN) -> list[dict]:
    rows = conn.execute(
        text("""SELECT metric, min_value, max_value, valid_from, source, created_at FROM goal
                WHERE CAST(:m AS text) IS NULL OR metric = :m
                ORDER BY metric, valid_from NULLS FIRST, id"""),
        {"m": metric},
    )
    return [dict(r._mapping) for r in rows]


class GoalIn(BaseModel):
    min_value: float | None = Field(None, ge=0)
    max_value: float | None = Field(None, ge=0)
    valid_from: date | None = Field(None, description="Defaults to today")

    @model_validator(mode="after")
    def _check(self):
        if self.min_value is None and self.max_value is None:
            raise ValueError("give min_value and/or max_value")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("min_value must not be above max_value")
        return self


def _goal_metric(metric: str) -> str:
    if metric not in GOAL_METRICS:
        raise HTTPException(422, f"no goals for {metric!r}; possible: {', '.join(GOAL_METRICS)}")
    return metric


@router.put("/goals/{metric}")
def put_goal(metric: str, g: GoalIn, conn: Connection = CONN) -> dict:
    """Set a goal from `valid_from` (default today) on; the Waistline import overrides it only
    when the goal in Waistline changes."""
    _goal_metric(metric)
    valid_from = g.valid_from or datetime.now(_tz()).date()
    _store_version(conn, metric, g.min_value, g.max_value, valid_from, "vitalocal")
    return {"metric": metric, **g.model_dump(), "valid_from": valid_from, "source": "vitalocal"}


@router.delete("/goals/{metric}", status_code=status.HTTP_204_NO_CONTENT)
def delete_goal(metric: str, valid_from: date | None = Query(None), conn: Connection = CONN) -> Response:
    """No goal from `valid_from` (default today) on; earlier days keep theirs."""
    _goal_metric(metric)
    _store_version(conn, metric, None, None, valid_from or datetime.now(_tz()).date(), "vitalocal")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/settings/drink-goal")
def get_drink_goal_settings(conn: Connection = CONN) -> dict:
    return drink_settings(conn).model_dump()


@router.put("/settings/drink-goal")
def put_drink_goal_settings(body: DrinkGoalSettings, conn: Connection = CONN) -> dict:
    conn.execute(
        text("""INSERT INTO app_setting (key, value) VALUES (:k, CAST(:v AS jsonb))
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"""),
        {"k": DRINK_GOAL_KEY, "v": json.dumps(body.model_dump())},
    )
    return body.model_dump()
