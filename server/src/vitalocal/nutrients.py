"""Nutrient catalog shared by the food catalog, diary entries, daily totals and Open Food Facts.

Keys are Waistline's (and Open Food Facts') nutrient names, so Waistline exports, the food
catalog and diary entries all use the same `nutrients` JSON. Units follow Waistline: energy in
kcal, macros in g, minerals mostly mg, some vitamins µg. A food that does not declare a nutrient
keeps it empty in the catalog (so the editor shows it is not given).

Daily totals: a nutrient with a metric becomes `nutrition.*` for every day with entries, the sum
of the entries' values; an entry without the value counts as 0 (a food without a salt value has
almost always no salt).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Nutrient:
    key: str
    unit: str
    label: str
    label_de: str
    off_field: str | None = None  # Open Food Facts nutriment name (`<name>_100g`)
    metric: str | None = None  # daily total metric


NUTRIENTS: tuple[Nutrient, ...] = (
    # the order of a nutrition label; FORM_KEYS (the first eight) are the editors' default fields
    Nutrient("calories", "kcal", "Energy", "Energie", "energy-kcal", "nutrition.energy"),
    Nutrient("fat", "g", "Fat", "Fett", "fat", "nutrition.fat"),
    Nutrient(
        "saturated-fat",
        "g",
        "Saturated fat",
        "Gesättigte Fettsäuren",
        "saturated-fat",
        "nutrition.saturated_fat",
    ),
    Nutrient(
        "carbohydrates", "g", "Carbohydrates", "Kohlenhydrate", "carbohydrates", "nutrition.carbohydrates"
    ),
    Nutrient("sugars", "g", "Sugars", "Zucker", "sugars", "nutrition.sugars"),
    Nutrient("fiber", "g", "Fiber", "Ballaststoffe", "fiber", "nutrition.fiber"),
    Nutrient("proteins", "g", "Protein", "Eiweiß", "proteins", "nutrition.protein"),
    Nutrient("salt", "g", "Salt", "Salz", "salt", "nutrition.salt"),
    Nutrient("kilojoules", "kJ", "Energy (kJ)", "Energie (kJ)", "energy-kj"),
    Nutrient(
        "monounsaturated-fat",
        "g",
        "Monounsaturated fat",
        "Einfach ungesättigte Fettsäuren",
        "monounsaturated-fat",
    ),
    Nutrient(
        "polyunsaturated-fat",
        "g",
        "Polyunsaturated fat",
        "Mehrfach ungesättigte Fettsäuren",
        "polyunsaturated-fat",
    ),
    Nutrient("omega-3-fat", "g", "Omega-3 fat", "Omega-3-Fettsäuren", "omega-3-fat"),
    Nutrient("trans-fat", "g", "Trans fat", "Transfette", "trans-fat"),
    Nutrient("cholesterol", "mg", "Cholesterol", "Cholesterin", "cholesterol"),
    Nutrient("sodium", "mg", "Sodium", "Natrium", "sodium"),
    Nutrient("caffeine", "mg", "Caffeine", "Koffein", "caffeine", "nutrition.caffeine"),
    Nutrient("alcohol", "g", "Alcohol", "Alkohol", "alcohol", "nutrition.alcohol"),
    Nutrient("potassium", "mg", "Potassium", "Kalium", "potassium"),
    Nutrient("calcium", "mg", "Calcium", "Calcium", "calcium"),
    Nutrient("magnesium", "mg", "Magnesium", "Magnesium", "magnesium"),
    Nutrient("iron", "mg", "Iron", "Eisen", "iron"),
    Nutrient("zinc", "mg", "Zinc", "Zink", "zinc"),
    Nutrient("phosphorus", "mg", "Phosphorus", "Phosphor", "phosphorus"),
    Nutrient("copper", "mg", "Copper", "Kupfer", "copper"),
    Nutrient("manganese", "mg", "Manganese", "Mangan", "manganese"),
    Nutrient("selenium", "µg", "Selenium", "Selen", "selenium"),
    Nutrient("vitamin-a", "µg", "Vitamin A", "Vitamin A", "vitamin-a"),
    Nutrient("vitamin-b1", "mg", "Vitamin B1", "Vitamin B1", "vitamin-b1"),
    Nutrient("vitamin-b2", "mg", "Vitamin B2", "Vitamin B2", "vitamin-b2"),
    Nutrient("vitamin-pp", "mg", "Niacin (B3)", "Niacin (B3)", "vitamin-pp"),
    Nutrient("pantothenic-acid", "mg", "Pantothenic acid (B5)", "Pantothensäure (B5)", "pantothenic-acid"),
    Nutrient("vitamin-b6", "mg", "Vitamin B6", "Vitamin B6", "vitamin-b6"),
    Nutrient("biotin", "µg", "Biotin (B7)", "Biotin (B7)", "biotin"),
    Nutrient("vitamin-b9", "µg", "Folate (B9)", "Folat (B9)", "vitamin-b9"),
    Nutrient("vitamin-b12", "µg", "Vitamin B12", "Vitamin B12", "vitamin-b12"),
    Nutrient("vitamin-c", "mg", "Vitamin C", "Vitamin C", "vitamin-c"),
    Nutrient("vitamin-d", "µg", "Vitamin D", "Vitamin D", "vitamin-d"),
    Nutrient("vitamin-e", "mg", "Vitamin E", "Vitamin E", "vitamin-e"),
    Nutrient("vitamin-k", "µg", "Vitamin K", "Vitamin K", "vitamin-k"),
)
BY_KEY: dict[str, Nutrient] = {n.key: n for n in NUTRIENTS}

# Daily totals: nutrient key -> (metric, unit), in display order.
DAILY_NUTRIENTS: dict[str, tuple[str, str]] = {n.key: (n.metric, n.unit) for n in NUTRIENTS if n.metric}
METRIC_KEY: dict[str, str] = {m: k for k, (m, _) in DAILY_NUTRIENTS.items()}
# Shown as the day's headline: energy and macros.
MAIN_KEYS = ("calories", "proteins", "carbohydrates", "fat")
# Shown by the food editors; every other nutrient sits under "more nutrients".
FORM_KEYS = ("calories", "fat", "saturated-fat", "carbohydrates", "sugars", "fiber", "proteins", "salt")


def scaled(nutrients: dict, factor: float) -> dict[str, float]:
    """Nutrients of `factor` base amounts; non-numeric values are dropped, never turned into 0."""
    out = {}
    for k, v in (nutrients or {}).items():
        if isinstance(v, int | float) and not isinstance(v, bool):
            out[k] = round(v * factor, 4)
    return out


def day_totals(entries: list[dict]) -> dict[str, dict]:
    """{key: {"value", "missing"}} over entries' `nutrients` for the daily keys: the sum, an entry
    without the value counting as 0; `missing` = how many entries do not declare it (information)."""
    out = {}
    for key in DAILY_NUTRIENTS:
        have = [e[key] for e in entries if isinstance(e.get(key), int | float)]
        out[key] = {"value": round(sum(have), 3), "missing": len(entries) - len(have)}
    return out


def catalog() -> list[dict]:
    return [
        {
            "key": n.key,
            "unit": n.unit,
            "label": n.label,
            "label_de": n.label_de,
            "metric": n.metric,
            "form": n.key in FORM_KEYS,
        }
        for n in NUTRIENTS
    ]
