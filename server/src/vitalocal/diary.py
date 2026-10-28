"""Food diary helpers shared by the day view, goals and the nutrition API.

Entries (`nutrition_entry`) come from VitaLocal (WebUI, Android, an agent) or from Waistline
exports. Waistline's are replaced wholesale by every export, so they become editable only after
the takeover (`nutrition.waistline_retired_at`), when Waistline imports are refused.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Connection, text

from vitalocal.nutrients import MAIN_KEYS, day_totals

MEALS_KEY = "nutrition.meals"
RETIRED_KEY = "nutrition.waistline_retired_at"
DEFAULT_MEALS = ["Frühstück", "Mittagessen", "Abendessen", "Snacks"]
DEFAULT_MEALS_EN = ["Breakfast", "Lunch", "Dinner", "Snacks"]

ENTRY_COLS = """uid AS id, day, logged_at, meal_index, meal, item_type, food_id, name, brand,
    amount, unit, quantity, nutrients, source, entered_via, source_item_id, template_id,
    created_at, updated_at"""


def setting(conn: Connection, key: str):
    return conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": key}).scalar()


def put_setting(conn: Connection, key: str, value_json: str) -> None:
    conn.execute(
        text("""INSERT INTO app_setting (key, value) VALUES (:k, CAST(:v AS jsonb))
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"""),
        {"k": key, "v": value_json},
    )


def waistline_retired_at(conn: Connection) -> str | None:
    v = setting(conn, RETIRED_KEY)
    return v if isinstance(v, str) else None


def meal_names(conn: Connection) -> list[str]:
    """Configured meal names; before that, Waistline's names as seen on its entries."""
    v = setting(conn, MEALS_KEY)
    if isinstance(v, list) and v:
        return [str(x) for x in v]
    from vitalocal.profile import lang  # the profile builds on this module

    english = lang(conn) == "en"
    names = list(DEFAULT_MEALS_EN if english else DEFAULT_MEALS)
    rows = conn.execute(
        text("""SELECT DISTINCT ON (meal_index) meal_index, meal FROM nutrition_entry
                WHERE source = 'waistline' AND meal_index BETWEEN 0 AND 9 AND meal IS NOT NULL
                ORDER BY meal_index, day DESC""")
    )
    for r in rows:
        while len(names) <= r.meal_index:
            names.append(f"{'Meal' if english else 'Mahlzeit'} {len(names) + 1}")
        if r.meal not in ("Breakfast", "Lunch", "Dinner", "Snacks"):  # Waistline's English defaults
            names[r.meal_index] = r.meal
    return names


def editable(source: str, retired: bool) -> bool:
    return source != "waistline" or retired


def entry_out(row, retired: bool) -> dict:
    e = dict(row._mapping)
    e["editable"] = editable(e["source"], retired)
    return e


def entries_of_day(conn: Connection, day: date) -> list:
    """In the order they were added within each meal (Waistline's in their diary order)."""
    return conn.execute(
        text(f"""SELECT {ENTRY_COLS} FROM nutrition_entry WHERE day = :d
                 ORDER BY meal_index NULLS LAST, created_at, id"""),
        {"d": day},
    ).all()


def day_summary(conn: Connection, day: date) -> dict | None:
    """The day card: totals (value + entries lacking the nutrient) and the entries per meal."""
    rows = entries_of_day(conn, day)
    if not rows:
        return None
    retired = waistline_retired_at(conn) is not None
    names = meal_names(conn)
    totals = day_totals([r.nutrients for r in rows])
    meals: dict = {}
    for r in rows:
        mi = r.meal_index
        name = names[mi] if mi is not None and 0 <= mi < len(names) else r.meal
        meal = meals.setdefault(mi, {"meal_index": mi, "meal": name, "calories": 0.0, "items": []})
        meal["calories"] += r.nutrients.get("calories", 0.0) or 0.0
        meal["items"].append(
            {
                "id": r.id,
                "food_id": r.food_id,
                "name": r.name,
                "brand": r.brand,
                "amount": r.amount,
                "unit": r.unit,
                "quantity": r.quantity,
                "type": r.item_type,
                "logged_at": r.logged_at,
                "source": r.source,
                "editable": editable(r.source, retired),
                **{k: r.nutrients.get(k) for k in MAIN_KEYS},
            }
        )
    sources = sorted({r.source for r in rows})
    return {
        "entries": len(rows),
        "totals": {k: {"value": v["value"] or 0.0, "missing": v["missing"]} for k, v in totals.items()},
        "meals": list(meals.values()),
        "sources": sources,
        # older clients show this as "from …"
        "source": "Waistline" if sources == ["waistline"] else "VitaLocal",
    }
