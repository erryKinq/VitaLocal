"""Meal templates ("Vorlagen", Waistline's meals): a saved combination of foods with amounts.

Logging a template writes one diary entry per food (each amount can be changed afterwards),
marked with the template. A dish ("Gericht", a food with components) is the other way: one entry.
Ids are client-generated as everywhere in the diary, so the app can log a template offline by
expanding it into ordinary entries itself.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, text

from vitalocal.api.nutrition import MAX_MEALS, EnteredVia, _from_food, _insert_entry, _waistline_id
from vitalocal.db import CONN
from vitalocal.deps import get_store
from vitalocal.diary import ENTRY_COLS, entry_out, waistline_retired_at
from vitalocal.nutrients import scaled
from vitalocal.rawstore import RawStore

router = APIRouter(prefix="/api/v1", tags=["nutrition"])


class TemplateItem(BaseModel):
    food_id: uuid.UUID
    amount: float = Field(gt=0, le=100_000, description="In the food's base unit")
    quantity: float = Field(1, gt=0, le=100)


class TemplateIn(BaseModel):
    id: uuid.UUID | None = Field(None, description="Client-generated; resubmitting the same id is a no-op")
    name: str = Field(min_length=1, max_length=200)
    items: list[TemplateItem] = Field(min_length=1, max_length=60)
    favorite: bool = False
    archived: bool = False


class LogIn(BaseModel):
    logged_at: datetime = Field(description="With time zone; the local day follows from it")
    meal_index: int = Field(ge=0, lt=MAX_MEALS)
    ids: list[uuid.UUID] = Field(min_length=1, max_length=60, description="New entry id per item, in order")
    entered_via: EnteredVia = "webui"

    @model_validator(mode="after")
    def _check(self):
        if self.logged_at.tzinfo is None:
            raise ValueError("logged_at must include a timezone")
        return self


def templates(conn: Connection, ids: list | None = None, include_archived: bool = True) -> list[dict]:
    rows = conn.execute(
        text("""SELECT t.id, t.name, t.source, t.favorite, t.archived, t.updated_at,
                       (SELECT count(DISTINCT (e.day, e.meal_index)) FROM nutrition_entry e
                        WHERE e.template_id = t.id) AS uses,
                       (SELECT max(e.day) FROM nutrition_entry e WHERE e.template_id = t.id) AS last_used
                FROM meal_template t
                WHERE (CAST(:ids AS uuid[]) IS NULL OR t.id = ANY(:ids)) AND (:arch OR NOT t.archived)
                ORDER BY t.favorite DESC, lower(t.name)"""),
        {"ids": ids, "arch": include_archived},
    ).all()
    out = [dict(r._mapping) | {"items": [], "nutrients": {}} for r in rows]
    by_id = {t["id"]: t for t in out}
    if not by_id:
        return out
    items = conn.execute(
        text("""SELECT i.template_id, i.food_id, i.amount, i.quantity, f.name, f.brand, f.base_amount,
                       f.base_unit, f.nutrients, f.archived
                FROM meal_template_item i JOIN food f ON f.id = i.food_id
                WHERE i.template_id = ANY(:ids) ORDER BY i.template_id, i.position"""),
        {"ids": list(by_id)},
    )
    for i in items:
        t = by_id[i.template_id]
        n = scaled(i.nutrients, i.amount / i.base_amount * i.quantity)
        t["items"].append(
            {
                "food_id": i.food_id,
                "name": i.name,
                "brand": i.brand,
                "amount": i.amount,
                "quantity": i.quantity,
                "unit": i.base_unit,
                "nutrients": n,
            }
        )
        for k, v in n.items():
            t["nutrients"][k] = round(t["nutrients"].get(k, 0.0) + v, 4)
    return out


def _get(conn: Connection, template_id) -> dict:
    found = templates(conn, [template_id])
    if not found:
        raise HTTPException(404, "template not found")
    return found[0]


def store_template(
    conn: Connection,
    t: TemplateIn,
    *,
    update: bool = False,
    source: str = "vitalocal",
    source_ref: str | None = None,
) -> tuple[dict | None, bool]:
    """Insert (or with `update`, replace) a template. Returns (template, created)."""
    tid = t.id or uuid.uuid4()
    known = {
        r[0]
        for r in conn.execute(
            text("SELECT id FROM food WHERE id = ANY(:ids)"), {"ids": [i.food_id for i in t.items]}
        )
    }
    if missing := [str(i.food_id) for i in t.items if i.food_id not in known]:
        raise HTTPException(422, f"unknown food: {', '.join(missing)}")
    params = {"id": tid, "name": t.name.strip() or "?", "fav": t.favorite, "arch": t.archived}
    if update:
        if (
            conn.execute(
                text("""UPDATE meal_template SET name = :name, favorite = :fav, archived = :arch,
                            updated_at = now()
                        WHERE id = :id RETURNING id"""),
                params,
            ).first()
            is None
        ):
            return None, False
        created = False
    else:
        row = conn.execute(
            text("""INSERT INTO meal_template (id, name, favorite, archived, source, source_ref)
                    VALUES (:id, :name, :fav, :arch, :src, :ref) ON CONFLICT DO NOTHING RETURNING id"""),
            params | {"src": source, "ref": source_ref},
        ).first()
        if row is None:
            existing = conn.execute(
                text(
                    "SELECT id FROM meal_template"
                    " WHERE id = :id OR (source = :src AND source_ref = :ref) LIMIT 1"
                ),
                {"id": tid, "src": source, "ref": source_ref},
            ).scalar()
            return _get(conn, existing), False
        created = True
    conn.execute(text("DELETE FROM meal_template_item WHERE template_id = :id"), {"id": tid})
    conn.execute(
        text("""INSERT INTO meal_template_item (template_id, position, food_id, amount, quantity)
                VALUES (:t, :p, :f, :a, :q)"""),
        [{"t": tid, "p": n, "f": i.food_id, "a": i.amount, "q": i.quantity} for n, i in enumerate(t.items)],
    )
    return _get(conn, tid), created


@router.get("/templates")
def list_templates(include_archived: bool = False, conn: Connection = CONN) -> list[dict]:
    """Meal templates with their items and summed nutrients; favorites first."""
    return templates(conn, include_archived=include_archived)


@router.post("/templates", status_code=status.HTTP_201_CREATED)
def create_template(t: TemplateIn, response: Response, conn: Connection = CONN) -> dict:
    out, created = store_template(conn, t)
    if not created:
        response.status_code = status.HTTP_200_OK
    return out


@router.get("/templates/{template_id}")
def read_template(template_id: uuid.UUID, conn: Connection = CONN) -> dict:
    return _get(conn, template_id)


@router.put("/templates/{template_id}")
def update_template(template_id: uuid.UUID, t: TemplateIn, conn: Connection = CONN) -> dict:
    t.id = template_id
    out, _ = store_template(conn, t, update=True)
    if out is None:
        raise HTTPException(404, "template not found")
    return out


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: uuid.UUID, conn: Connection = CONN) -> Response:
    """Deleted; entries logged from it stay (they are ordinary entries)."""
    conn.execute(text("DELETE FROM meal_template WHERE id = :id"), {"id": template_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/templates/{template_id}/log")
def log_template(template_id: uuid.UUID, body: LogIn, conn: Connection = CONN) -> dict:
    """One diary entry per item; `ids` (one per item, in order) make a replay a no-op."""
    t = _get(conn, template_id)
    if len(body.ids) != len(t["items"]):
        raise HTTPException(422, f"give {len(t['items'])} ids, one per item")
    created = 0
    for uid, item in zip(body.ids, t["items"], strict=True):
        entry = _from_food(conn, item["food_id"], item["amount"], item["quantity"])
        created += _insert_entry(
            conn, uid, body.logged_at, body.meal_index, entry, body.entered_via, template_id
        )
    retired = waistline_retired_at(conn) is not None
    rows = conn.execute(
        text(f"SELECT {ENTRY_COLS} FROM nutrition_entry WHERE uid = ANY(:ids) ORDER BY id"), {"ids": body.ids}
    )
    return {"created": created, "entries": [entry_out(r, retired) for r in rows]}


def import_waistline_meals(conn: Connection, data: dict) -> int:
    """Waistline's meals from an export as templates (foods must be taken over already)."""
    from vitalocal.ingest import waistline

    n = 0
    for m in data.get("meals") or []:
        if not isinstance(m, dict) or "id" not in m:
            continue
        items = []
        for it in m.get("items") or []:
            kind = "recipe" if it.get("type") == "recipe" else "food"
            amount = waistline._num(it.get("portion"))
            qty = waistline._num(it.get("quantity")) or 1.0
            if amount and amount > 0 and qty > 0:
                items.append(
                    TemplateItem(food_id=_waistline_id(kind, it.get("id")), amount=amount, quantity=qty)
                )
        known = {
            r[0]
            for r in conn.execute(
                text("SELECT id FROM food WHERE id = ANY(:ids)"), {"ids": [i.food_id for i in items]}
            )
        }
        items = [i for i in items if i.food_id in known]
        if not items:
            continue
        _, created = store_template(
            conn,
            TemplateIn(id=_waistline_id("meal", m["id"]), name=str(m.get("name") or "?")[:200], items=items),
            source="waistline",
            source_ref=f"meal:{m['id']}",
        )
        n += created
    return n


@router.post("/templates/waistline-import")
def waistline_meals(conn: Connection = CONN, store: RawStore = Depends(get_store)) -> dict:
    """Takes over Waistline's meals from the newest Waistline export (after the takeover, which
    brought the foods); already taken over ones stay as they are."""
    from vitalocal.api.nutrition import _latest_waistline_export

    export = _latest_waistline_export(conn)
    if export is None or not export["available"] or not store.exists(export["sha256"]):
        raise HTTPException(409, "no stored Waistline export")
    data = json.loads(store.path_for(export["sha256"]).read_bytes())
    return {"templates": import_waistline_meals(conn, data), "export": export["received_at"]}
