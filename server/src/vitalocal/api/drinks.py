"""Drinks (water, tea, coffee, other): part of nutrition, logged in the WebUI and the Android app.

`caffeine` marks an entry as caffeinated (tea may be herbal); when a client leaves it out, coffee
and tea count as caffeinated. Entries can be corrected later (kind, amount, time, caffeine).
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, text

from vitalocal.config import get_settings
from vitalocal.db import CONN

router = APIRouter(prefix="/api/v1", tags=["nutrition"])

DrinkKind = Literal["water", "tea", "coffee", "other"]
CAFFEINATED_BY_DEFAULT = frozenset({"coffee", "tea"})
_COLS = "id, ts, drink, ml, caffeine, source, created_at"


class DrinkIn(BaseModel):
    id: uuid.UUID = Field(description="Client-generated; resubmitting the same id is a no-op")
    ts: datetime | None = Field(None, description="Defaults to now")
    drink: DrinkKind = "water"
    ml: float = Field(gt=0, le=5000)
    caffeine: bool | None = Field(None, description="Defaults to true for coffee and tea")
    source: str = Field("webui", max_length=40)

    @model_validator(mode="after")
    def _check(self):
        if self.ts is not None and self.ts.tzinfo is None:
            raise ValueError("ts must include a timezone")
        if self.caffeine is None:
            self.caffeine = self.drink in CAFFEINATED_BY_DEFAULT
        return self


class DrinkUpdate(BaseModel):
    """The full corrected entry (a replayed PUT gives the same result)."""

    ts: datetime
    drink: DrinkKind
    ml: float = Field(gt=0, le=5000)
    caffeine: bool | None = Field(None, description="Defaults to true for coffee and tea")

    @model_validator(mode="after")
    def _check(self):
        if self.ts.tzinfo is None:
            raise ValueError("ts must include a timezone")
        if self.caffeine is None:
            self.caffeine = self.drink in CAFFEINATED_BY_DEFAULT
        return self


def store_drink(conn: Connection, d: DrinkIn) -> tuple[dict, bool]:
    """Insert idempotently. Returns (row, created)."""
    row = conn.execute(
        text(f"""INSERT INTO drink (id, ts, drink, ml, caffeine, source)
                 VALUES (:id, :ts, :drink, :ml, :caffeine, :source)
                 ON CONFLICT (id) DO NOTHING RETURNING {_COLS}"""),
        {**d.model_dump(), "ts": d.ts or datetime.now(UTC)},
    ).first()
    if row is not None:
        return dict(row._mapping), True
    row = conn.execute(text(f"SELECT {_COLS} FROM drink WHERE id = :id"), {"id": d.id}).one()
    return dict(row._mapping), False


def drinks_between(conn: Connection, start: datetime, end: datetime) -> list[dict]:
    rows = conn.execute(
        text(f"SELECT {_COLS} FROM drink WHERE ts >= :s AND ts < :e ORDER BY ts"), {"s": start, "e": end}
    )
    return [dict(r._mapping) for r in rows]


def drinks_of_day(conn: Connection, day: date) -> dict:
    """{total_ml, by_drink, entries} for one local day."""
    tz = ZoneInfo(get_settings().timezone)
    start = datetime.combine(day, time.min, tzinfo=tz)
    entries = drinks_between(conn, start, datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz))
    by_drink: dict[str, float] = {}
    for e in entries:
        by_drink[e["drink"]] = by_drink.get(e["drink"], 0) + e["ml"]
    return {"total_ml": sum(e["ml"] for e in entries), "by_drink": by_drink, "entries": entries}


@router.post("/drinks", status_code=status.HTTP_201_CREATED)
def create_drink(d: DrinkIn, response: Response, conn: Connection = CONN) -> dict:
    row, created = store_drink(conn, d)
    if not created:
        response.status_code = status.HTTP_200_OK
    return row


@router.get("/drinks")
def list_drinks(
    day: date | None = Query(None, description="Local day; alternative to from/to"),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    conn: Connection = CONN,
) -> dict | list[dict]:
    if day is not None:
        return drinks_of_day(conn, day)
    if from_ is None or to is None:
        raise HTTPException(422, "give either day or from and to")
    return drinks_between(conn, from_, to)


@router.put("/drinks/{drink_id}")
def update_drink(drink_id: uuid.UUID, d: DrinkUpdate, conn: Connection = CONN) -> dict:
    row = conn.execute(
        text(f"""UPDATE drink SET ts = :ts, drink = :drink, ml = :ml, caffeine = :caffeine
                 WHERE id = :id RETURNING {_COLS}"""),
        {**d.model_dump(), "id": drink_id},
    ).first()
    if row is None:
        raise HTTPException(404, "drink not found")
    return dict(row._mapping)


@router.delete("/drinks/{drink_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_drink(drink_id: uuid.UUID, conn: Connection = CONN) -> Response:
    conn.execute(text("DELETE FROM drink WHERE id = :id"), {"id": drink_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
