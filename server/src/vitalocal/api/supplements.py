"""Supplements: taken X of Y units a day, with their active ingredients summed per day.

A supplement is done on a day when X ≥ Y. Ingredients come from a small catalog (unit and EFSA
tolerable upper intake level for adults) or are free text without a limit. The day's total per
ingredient is compared with the upper level; the level applies to the total intake including
food, so a supplement total near it is already worth a look.
"""

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, text

from vitalocal.api.days import _tz
from vitalocal.api.habits import _streaks
from vitalocal.db import CONN

router = APIRouter(prefix="/api/v1", tags=["supplements"])

# key -> (label, unit, EFSA upper level per day for adults or None, note)
INGREDIENTS: dict[str, tuple[str, str, float | None, str | None]] = {
    "vitamin_d": ("Vitamin D", "IE", 4000, "EFSA UL 100 µg = 4000 IE"),
    "vitamin_a": ("Vitamin A (Retinol)", "µg", 3000, None),
    "vitamin_e": ("Vitamin E", "mg", 300, None),
    "vitamin_k": ("Vitamin K", "µg", None, None),
    "vitamin_c": ("Vitamin C", "mg", None, None),
    "vitamin_b6": ("Vitamin B6", "mg", 12, None),
    "vitamin_b12": ("Vitamin B12", "µg", None, None),
    "folic_acid": ("Folsäure", "µg", 1000, None),
    "magnesium": ("Magnesium", "mg", 250, "EFSA UL for supplements (not food)"),
    "zinc": ("Zink", "mg", 25, None),
    "iron": ("Eisen", "mg", 40, "EFSA safe level, no UL"),
    "calcium": ("Calcium", "mg", 2500, None),
    "selenium": ("Selen", "µg", 255, None),
    "iodine": ("Jod", "µg", 600, None),
    "copper": ("Kupfer", "mg", 5, None),
    "omega3": ("Omega-3 (EPA+DHA)", "mg", 5000, "EFSA: up to 5 g a day raise no concern"),
    "creatine": ("Kreatin", "g", None, None),
}
_COLS = (
    "id, name, brand, form, per_day, per_week, ingredients, timing, note, started_on, position, archived, "
    "created_at"
)


class Ingredient(BaseModel):
    key: str | None = Field(None, description="Catalog key (unit from the catalog)")
    name: str | None = Field(None, max_length=60, description="Free ingredient without a catalog entry")
    unit: str | None = Field(None, max_length=10, description="Only for free ingredients")
    amount: float = Field(gt=0, le=1_000_000, description="Per unit (capsule, tablet, drop, …)")

    @model_validator(mode="after")
    def _check(self):
        if (self.key is None) == (self.name is None):
            raise ValueError("give either a catalog key or a name")
        if self.key is not None and self.key not in INGREDIENTS:
            raise ValueError(f"unknown ingredient {self.key!r}")
        if self.name is not None and not self.unit:
            raise ValueError("free ingredients need a unit")
        return self


class SupplementIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    brand: str | None = Field(None, max_length=80)
    form: Literal["capsule", "tablet", "drops", "powder", "other"] = "capsule"
    per_day: int = Field(1, ge=1, le=50)
    per_week: int = Field(7, ge=1, le=7, description="Days a week it is taken (7 = daily)")
    ingredients: list[Ingredient] = Field(default_factory=list, max_length=40)
    timing: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=500)
    started_on: date | None = Field(None, description="Taken since (also before it was logged here)")
    position: int = 0
    archived: bool = False


def _row(conn: Connection, sid) -> dict:
    row = conn.execute(text(f"SELECT {_COLS} FROM supplement WHERE id = :id"), {"id": sid}).first()
    if row is None:
        raise HTTPException(404, "supplement not found")
    return dict(row._mapping)


def list_supplements(conn: Connection, include_archived: bool = False) -> list[dict]:
    rows = conn.execute(
        text(f"""SELECT {_COLS} FROM supplement WHERE archived = false OR :all
                 ORDER BY archived, position, created_at"""),
        {"all": include_archived},
    )
    return [dict(r._mapping) for r in rows]


def _params(s: SupplementIn) -> dict:
    return {
        **s.model_dump(exclude={"ingredients"}),
        "name": s.name.strip(),
        "ingredients": json.dumps([i.model_dump(exclude_none=True) for i in s.ingredients]),
    }


def ingredient_totals(supplements: list[dict], counts: dict) -> list[dict]:
    """Per ingredient the day's amount (units taken × amount per unit) and its upper level."""
    totals: dict[tuple, dict] = {}
    for s in supplements:
        n = counts.get(s["id"], 0)
        if not n:
            continue
        for ing in s["ingredients"]:
            key = ing.get("key")
            if key in INGREDIENTS:
                label, unit, ul, note = INGREDIENTS[key]
            else:
                label, unit, ul, note = ing["name"], ing["unit"], None, None
            empty = {"key": key, "label": label, "unit": unit, "amount": 0.0, "upper_level": ul, "note": note}
            t = totals.setdefault((key or label.lower(), unit), {**empty, "from": []})
            t["amount"] += n * ing["amount"]
            t["from"].append(s["name"])
    out = []
    for t in totals.values():
        t["amount"] = round(t["amount"], 3)
        ul = t["upper_level"]
        t["share_of_upper_level"] = round(t["amount"] / ul, 2) if ul else None
        t["above_upper_level"] = bool(ul and t["amount"] > ul)
        out.append(t)
    return sorted(out, key=lambda t: t["label"])


def day_status(conn: Connection, day: date, history_days: int = 365) -> dict:
    """Active supplements with the day's count, done, streak and the day's ingredient totals."""
    sups = list_supplements(conn)
    start = day - timedelta(days=history_days)
    logs: dict = {}
    if sups:
        for r in conn.execute(
            text("""SELECT supplement_id, day, count FROM supplement_log
                    WHERE supplement_id = ANY(:ids) AND day BETWEEN :s AND :e"""),
            {"ids": [s["id"] for s in sups], "s": start, "e": day},
        ):
            logs.setdefault(r.supplement_id, {})[r.day] = r.count
    items = []
    for s in sups:
        per = logs.get(s["id"], {})
        days = {d.isoformat(): {"value": c, "done": c >= s["per_day"]} for d, c in per.items()}
        count = per.get(day, 0)
        monday = day - timedelta(days=day.weekday())
        week_count = sum(1 for d, c in per.items() if monday <= d <= day and c >= s["per_day"])
        items.append(
            {
                **s,
                "count": count,
                "done": count >= s["per_day"],
                # some days a week: this week's goal is reached (nothing more to take this week)
                "week_count": week_count,
                "week_done": s["per_week"] < 7 and week_count >= s["per_week"],
                "days": days,
                **_streaks({"per_week": s["per_week"]}, days, day, start, jokers=False),
            }
        )
    counts = {s["id"]: s["count"] for s in items}
    return {"day": day, "supplements": items, "ingredients": ingredient_totals(sups, counts)}


# --- API --------------------------------------------------------------------------------------------------


@router.get("/supplements/ingredients")
def ingredient_catalog() -> list[dict]:
    return [
        {"key": k, "label": label, "unit": unit, "upper_level": ul, "note": note}
        for k, (label, unit, ul, note) in INGREDIENTS.items()
    ]


@router.get("/supplements")
def get_supplements(include_archived: bool = False, conn: Connection = CONN) -> list[dict]:
    return list_supplements(conn, include_archived)


@router.post("/supplements", status_code=status.HTTP_201_CREATED)
def create_supplement(s: SupplementIn, conn: Connection = CONN) -> dict:
    sid = s.id or uuid.uuid4()
    conn.execute(
        text("""INSERT INTO supplement (id, name, brand, form, per_day, per_week, ingredients, timing, note,
                                        started_on, position, archived)
                VALUES (:id, :name, :brand, :form, :per_day, :per_week, CAST(:ingredients AS jsonb), :timing,
                        :note, :started_on, :position, :archived)
                ON CONFLICT (id) DO NOTHING"""),
        {**_params(s), "id": sid},
    )
    return _row(conn, sid)


@router.put("/supplements/{sid}")
def update_supplement(sid: uuid.UUID, s: SupplementIn, conn: Connection = CONN) -> dict:
    _row(conn, sid)
    conn.execute(
        text("""UPDATE supplement SET name = :name, brand = :brand, form = :form, per_day = :per_day,
                    per_week = :per_week,
                    ingredients = CAST(:ingredients AS jsonb), timing = :timing, note = :note,
                    started_on = :started_on, position = :position, archived = :archived
                WHERE id = :id"""),
        {**_params(s), "id": sid},
    )
    return _row(conn, sid)


@router.delete("/supplements/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supplement(sid: uuid.UUID, conn: Connection = CONN) -> Response:
    conn.execute(text("DELETE FROM supplement WHERE id = :id"), {"id": sid})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class LogIn(BaseModel):
    count: int = Field(ge=0, le=100, description="Units taken that day (X); 0 removes the entry")


@router.put("/supplements/{sid}/log/{day}")
def log_supplement(sid: uuid.UUID, day: date, body: LogIn, conn: Connection = CONN) -> dict:
    s = _row(conn, sid)
    if body.count == 0:
        conn.execute(
            text("DELETE FROM supplement_log WHERE supplement_id = :s AND day = :d"), {"s": sid, "d": day}
        )
    else:
        conn.execute(
            text("""INSERT INTO supplement_log (supplement_id, day, count) VALUES (:s, :d, :c)
                    ON CONFLICT (supplement_id, day)
                    DO UPDATE SET count = EXCLUDED.count, updated_at = now()"""),
            {"s": sid, "d": day, "c": body.count},
        )
    return {"supplement_id": sid, "day": day, "count": body.count, "done": body.count >= s["per_day"]}


@router.get("/supplements/status")
def supplement_status(
    day: date | None = Query(None, description="Local day (defaults to today)"),
    conn: Connection = CONN,
) -> dict:
    """Active supplements with the day's X of Y, streak, 365 days of history and ingredient totals."""
    return day_status(conn, day or datetime.now(_tz()).date())
