"""Derived insights and the WebUI day-view layout (stored on the server so every browser shares it)."""

import json
from datetime import date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from vitalocal.api.days import _tz
from vitalocal.db import CONN
from vitalocal.insights import insights, readiness_with_garmin

router = APIRouter(prefix="/api/v1", tags=["days"])

LAYOUT_KEY = "webui.day_layout"


@router.get("/insights")
def get_insights(
    day: date | None = Query(None, description="Reference day (defaults to today)"),
    conn: Connection = CONN,
) -> dict:
    """Readiness, sleep rhythm, recovery after training, energy averages and weekly trends."""
    tz = _tz()
    now = datetime.now(tz)
    day = day or now.date()
    # a past day is judged as of its morning, like the watch would have
    ref = now if day >= now.date() else datetime.combine(day, time(9, 0), tzinfo=tz)
    return insights(conn, tz, day, ref)


@router.get("/readiness")
def get_readiness(
    day: date | None = Query(None, description="Reference day (defaults to today)"),
    conn: Connection = CONN,
) -> dict:
    """Only the `readiness` section of /insights (own estimate, components and Garmin's value)."""
    tz = _tz()
    now = datetime.now(tz)
    day = day or now.date()
    ref = now if day >= now.date() else datetime.combine(day, time(9, 0), tzinfo=tz)
    return readiness_with_garmin(conn, tz, day, ref)


FITNESS_METRICS = (
    "vo2max",
    "vo2max_daily",
    "race_time_5k",
    "race_time_10k",
    "race_time_half_marathon",
    "race_time_marathon",
)


@router.get("/fitness")
def get_fitness(
    day: date | None = Query(None, description="Reference day (defaults to today)"),
    conn: Connection = CONN,
) -> dict:
    """Latest VO2max and race predictions up to the end of `day`, each with its value 4 weeks earlier."""
    tz = _tz()
    day = day or datetime.now(tz).date()
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    out = {}
    for m in FITNESS_METRICS:
        rows = conn.execute(
            text("""(SELECT ts, value FROM sample WHERE metric = :m AND ts < :e
                     ORDER BY ts DESC LIMIT 1)
                    UNION ALL
                    (SELECT ts, value FROM sample WHERE metric = :m AND ts < :e4
                     ORDER BY ts DESC LIMIT 1)"""),
            {"m": m, "e": end, "e4": end - timedelta(weeks=4)},
        ).all()
        if rows:
            out[m] = {
                "value": rows[0].value,
                "ts": rows[0].ts,
                "previous": rows[1].value if len(rows) > 1 else None,
            }
    return {"day": day, "metrics": out}


class Widget(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]{1,40}$")
    size: Literal["half", "full"] = "full"  # before 0.9: width only; w/h take precedence
    w: int | None = Field(None, ge=1, le=4, description="Width in columns of a 4-column grid")
    h: int | None = Field(None, ge=1, le=6, description="Height in fixed rows")
    text: str | None = Field(None, max_length=60, description="Title of a section heading (id heading_*)")


class DayLayout(BaseModel):
    today: list[Widget] = Field(max_length=60)
    past: list[Widget] = Field(max_length=60)


@router.get("/settings/day-layout")
def get_day_layout(conn: Connection = CONN) -> dict | None:
    """The saved layout, or null when the WebUI default applies."""
    return conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": LAYOUT_KEY}).scalar()


@router.put("/settings/day-layout")
def put_day_layout(body: DayLayout, conn: Connection = CONN) -> dict:
    conn.execute(
        text("""INSERT INTO app_setting (key, value) VALUES (:k, CAST(:v AS jsonb))
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"""),
        {"k": LAYOUT_KEY, "v": json.dumps(body.model_dump(exclude_none=True))},
    )
    return body.model_dump(exclude_none=True)


@router.delete("/settings/day-layout")
def reset_day_layout(conn: Connection = CONN) -> None:
    conn.execute(text("DELETE FROM app_setting WHERE key = :k"), {"k": LAYOUT_KEY})
