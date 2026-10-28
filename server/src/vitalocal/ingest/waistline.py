"""Parser `waistline`: Waistline JSON export -> nutrition log entries, daily totals, body stats.

Semantics (Waistline schema 9, export version 34):
- `foodList` / `recipes` hold nutrients per base portion (`portion` + `unit`, e.g. per 100 g;
  a recipe's nutrients cover the whole recipe, e.g. 4 portions).
- A diary item references a food or recipe by id; consumed = nutrients x (item.portion /
  base portion) x quantity. `category` is the meal index into `settings.diary.meal-names`.
- The diary date is a calendar label stored as UTC midnight; `stats` holds body measurements.
- Nutrient units: energy in kcal (kilojoules separately), macros in g, minerals in mg as in
  Waistline. Daily totals: an entry whose food does not declare a nutrient counts as 0.

Goals (`settings.goals`): per nutrient a `goal-list` whose first entry holds 7 weekday values;
with `shared-goal` the first applies every day. `minimum-goal` makes it a lower bound, otherwise
it is an upper bound (Waistline's "stay below"). Per-weekday and percent goals are not taken over
(a warning says so) rather than guessed.

The export also contains app settings including credentials; this parser never reads them
beyond the meal names, units and goals.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from vitalocal.nutrients import DAILY_NUTRIENTS

PARSER_NAME = "waistline"
PARSER_VERSION = "1.1.0"
KNOWN_SCHEMA_VERSIONS = frozenset({9})

DEFAULT_MEAL_NAMES = ["Breakfast", "Lunch", "Dinner", "Snacks", "Meal 5", "Meal 6", "Meal 7"]

# Daily totals exported as samples: nutrient key -> (metric, unit), shared with the food catalog.
# Waistline goal key -> metric (energy goal in kilojoules duplicates calories; weight is a target
# body weight, not a daily bound).
GOAL_METRICS = {k: m for k, (m, _) in DAILY_NUTRIENTS.items()}

BODY_STATS = {  # Waistline body stat -> metric, unit
    "weight": ("weight", "kg"),
    "body fat": ("body_fat", "%"),
    "waist": ("body.waist", "cm"),
    "hips": ("body.hips", "cm"),
    "neck": ("body.neck", "cm"),
}


@dataclass
class Entry:
    day: date
    meal_index: int | None
    meal: str | None
    logged_at: datetime | None
    item_type: str
    source_item_id: int
    name: str
    brand: str | None
    amount: float | None
    unit: str | None
    quantity: float
    nutrients: dict[str, float]


@dataclass
class DaySample:
    ts: datetime
    metric: str
    value: float
    unit: str


@dataclass
class WaistlineParse:
    schema_version: int | None
    entries: list[Entry] = field(default_factory=list)
    samples: list[DaySample] = field(default_factory=list)
    days: list[date] = field(default_factory=list)
    missing_nutrients: dict[str, dict[str, int]] = field(default_factory=dict)  # day -> key -> n
    goals: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)  # metric -> (min, max)
    warnings: list[str] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)

    def stats(self) -> dict:
        return {
            "parser": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "schema_version": self.schema_version,
            "schema_known": self.schema_version in KNOWN_SCHEMA_VERSIONS,
            "diary_days": len(self.days),
            "entries": len(self.entries),
            "samples": len(self.samples),
            "goals": {m: {"min": lo, "max": hi} for m, (lo, hi) in self.goals.items()},
            "skipped": dict(self.skipped),
            "warnings": self.warnings[:50],
        }


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "."))
        except ValueError:
            return None
    return None


def _ts(v) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_waistline_export(data: dict) -> bool:
    return isinstance(data, dict) and "diary" in data and "foodList" in data


def parse(raw: bytes, timezone: str = "UTC") -> WaistlineParse:
    data = json.loads(raw)
    if not is_waistline_export(data):
        raise ValueError("not a Waistline export (diary/foodList missing)")
    settings = data.get("settings") or {}
    out = WaistlineParse(schema_version=settings.get("schemaVersion"))
    if out.schema_version not in KNOWN_SCHEMA_VERSIONS:
        out.warnings.append(f"Waistline schema {out.schema_version} not verified; parsed by field name")

    meal_names = list((settings.get("diary") or {}).get("meal-names") or [])
    units = settings.get("units") or {}
    if units.get("energy", "kcal") != "kcal":
        out.warnings.append(f"energy unit {units.get('energy')!r}: 'calories' is still read as kcal")

    out.goals = _goals(settings.get("goals") or {}, out)
    foods = {f["id"]: f for f in data.get("foodList", []) if isinstance(f, dict) and "id" in f}
    recipes = {r["id"]: r for r in data.get("recipes", []) if isinstance(r, dict) and "id" in r}
    tz = ZoneInfo(timezone)

    for day_rec in data.get("diary", []):
        day_ts = _ts(day_rec.get("dateTime"))
        if day_ts is None:
            out.skipped["diary_day_without_date"] += 1
            continue
        day = day_ts.astimezone(UTC).date()  # calendar label stored as UTC midnight
        out.days.append(day)
        totals: dict[str, float] = defaultdict(float)
        missing: Counter = Counter()
        n_entries = 0
        for item in day_rec.get("items") or []:
            entry = _entry(item, day, foods, recipes, meal_names, out)
            if entry is None:
                continue
            out.entries.append(entry)
            n_entries += 1
            for key in DAILY_NUTRIENTS:
                if key in entry.nutrients:
                    totals[key] += entry.nutrients[key]
                else:
                    missing[key] += 1
        noon = datetime.combine(day, time(12, 0), tzinfo=tz)  # day-level values: local noon
        if n_entries:
            for key, (metric, unit) in DAILY_NUTRIENTS.items():
                out.samples.append(DaySample(noon, metric, round(totals.get(key, 0.0), 3), unit))
            if missing:
                out.missing_nutrients[day.isoformat()] = {k: v for k, v in missing.items() if v}
        for stat, value in (day_rec.get("stats") or {}).items():
            v = _num(value)
            if stat in BODY_STATS and v is not None:
                metric, unit = BODY_STATS[stat]
                out.samples.append(DaySample(noon, metric, v, unit))
            elif v is not None:
                out.skipped[f"body_stat_{stat}"] += 1
    return out


def _goals(raw: dict, out: WaistlineParse) -> dict[str, tuple[float | None, float | None]]:
    goals = {}
    for key, metric in GOAL_METRICS.items():
        spec = raw.get(key)
        entries = (spec or {}).get("goal-list") if isinstance(spec, dict) else None
        if not entries or not isinstance(entries[0], dict):
            continue
        g = entries[0]
        values = [_num(v) for v in g.get("goal") or []]
        if not any(v is not None for v in values):
            continue
        if len(entries) > 1:
            out.warnings.append(f"goal {key}: {len(entries)} goal entries, only the first is used")
        if g.get("percent-goal"):
            out.warnings.append(f"goal {key}: percent goals are not taken over")
            continue
        if not g.get("shared-goal") and (len(values) != 7 or len(set(values)) != 1):
            out.warnings.append(f"goal {key}: goals per weekday are not taken over")
            continue
        value = next(v for v in values if v is not None)
        goals[metric] = (value, None) if g.get("minimum-goal") else (None, value)
    return goals


def _entry(item, day, foods, recipes, meal_names, out: WaistlineParse) -> Entry | None:
    kind = item.get("type")
    source = foods if kind == "food" else recipes if kind == "recipe" else None
    ref = source.get(item.get("id")) if source is not None else None
    if ref is None:
        out.skipped[f"unresolved_{kind}"] += 1
        return None
    amount = _num(item.get("portion"))
    base = _num(ref.get("portion"))
    qty = _num(item.get("quantity"))
    qty = 1.0 if qty is None else qty
    if amount is None or not base:
        out.skipped["entry_without_portion"] += 1
        out.warnings.append(f"{day}: '{ref.get('name')}' has no usable portion")
        return None
    factor = amount / base * qty
    nutrients = {}
    for key, value in (ref.get("nutrition") or {}).items():
        v = _num(value)
        if v is not None:
            nutrients[key] = round(v * factor, 4)
    meal_index = _num(item.get("category"))
    meal_index = int(meal_index) if meal_index is not None else None
    meal = None
    if meal_index is not None:
        custom = meal_names[meal_index] if meal_index < len(meal_names) else ""
        meal = custom or (DEFAULT_MEAL_NAMES[meal_index] if meal_index < len(DEFAULT_MEAL_NAMES) else None)
    return Entry(
        day=day,
        meal_index=meal_index,
        meal=meal,
        logged_at=_ts(item.get("dateTime")),
        item_type=kind,
        source_item_id=int(item["id"]),
        name=str(ref.get("name") or "?"),
        brand=ref.get("brand") or None,
        amount=amount,
        unit=ref.get("unit"),
        quantity=qty,
        nutrients=nutrients,
    )
