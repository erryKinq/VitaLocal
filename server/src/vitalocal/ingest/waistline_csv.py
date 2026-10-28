"""Parser `waistline-csv`: Waistline diary export (CSV with one row per day).

Semantics, verified against the JSON export of the same data (2026-09-28):
- One row per diary day: Waistline's own daily totals plus the body weight of that day.
- Totals equal the sum of the nutrient values the logged foods declare; a food that does not
  declare a nutrient counts as 0 there. For rarely declared nutrients (fiber, salt, sugars,
  saturated fat) the totals are therefore lower bounds. Per-food detail is not in this export.
- A day with 0 kcal and all other nutrient cells empty has no food logged: no nutrition values
  are written for it (never an intake of 0). Its weight is still valid.
- An empty weight cell means no measurement.
- German exports use `;`, `dd.mm.yyyy` and decimal commas; header names follow the app language,
  so columns are matched by keywords in German and English.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

PARSER_NAME = "waistline-csv"
PARSER_VERSION = "1.0.0"

# metric, unit, keywords matched against the normalized header (first match wins)
COLUMNS = [
    ("nutrition.saturated_fat", "g", ("gesattigte", "saturated")),
    ("nutrition.energy", "kcal", ("kalorien", "calories", "energie", "energy")),
    ("nutrition.fat", "g", ("fett", "fat")),
    ("nutrition.carbohydrates", "g", ("kohlenhydrate", "carbohydrate")),
    ("nutrition.sugars", "g", ("zucker", "sugar")),
    ("nutrition.fiber", "g", ("ballaststoffe", "fiber", "fibre")),
    ("nutrition.protein", "g", ("eiweiss", "protein")),
    ("nutrition.salt", "g", ("salz", "salt")),
    ("weight", "kg", ("gewicht", "weight")),
]
_DATE_KEYS = ("datum", "date")
_NUTRITION = [m for m, _, _ in COLUMNS if m.startswith("nutrition.")]


@dataclass
class DaySample:
    ts: datetime
    metric: str
    value: float
    unit: str


@dataclass
class CsvParse:
    days: list[date] = field(default_factory=list)
    samples: list[DaySample] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)  # header -> metric
    ignored_columns: list[str] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def stats(self) -> dict:
        return {
            "parser": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "days": len(self.days),
            "samples": len(self.samples),
            "columns": self.columns,
            "ignored_columns": self.ignored_columns,
            "counts": dict(self.counts),
            "warnings": self.warnings[:50],
        }


def _norm(header: str) -> str:
    h = header.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
        h = h.replace(a, b)
    return re.sub(r"[^a-z]", " ", h)


def looks_like_diary_csv(head: bytes) -> bool:
    """Waistline's layout: date, calories second, weight last (other apps' CSVs differ)."""
    first = head.decode("utf-8-sig", errors="ignore").splitlines()[0] if head else ""
    cells = [_norm(c).strip() for c in re.split(r"[;,\t]", first)]
    return (
        len(cells) >= 3
        and cells[0] in _DATE_KEYS
        and any(k in cells[1] for k in ("kalorien", "calories"))
        and any(k in cells[-1] for k in ("gewicht", "weight"))
    )


def _number(cell: str) -> float | None:
    cell = cell.strip()
    if not cell:
        return None
    if "," in cell and "." in cell:  # 1.234,5
        cell = cell.replace(".", "")
    try:
        return float(cell.replace(",", "."))
    except ValueError:
        return None


def _date(cell: str) -> date | None:
    cell = cell.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(cell, fmt).date()
        except ValueError:
            continue
    return None


def parse(raw: bytes, timezone: str = "UTC") -> CsvParse:
    text = raw.decode("utf-8-sig")
    first = text.splitlines()[0] if text else ""
    delimiter = max((";", ",", "\t"), key=first.count)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows or _norm(rows[0][0]).strip() not in _DATE_KEYS:
        raise ValueError("not a Waistline diary CSV (first column must be the date)")
    out = CsvParse()
    header = rows[0]
    mapping: dict[int, tuple[str, str]] = {}
    for i, name in enumerate(header[1:], start=1):
        n = _norm(name)
        match = next(((m, u) for m, u, keys in COLUMNS if any(k in n for k in keys)), None)
        if match and match[0] not in {m for m, _ in mapping.values()}:
            mapping[i] = match
            out.columns[name] = match[0]
        else:
            out.ignored_columns.append(name)
    tz = ZoneInfo(timezone)

    for line_no, row in enumerate(rows[1:], start=2):
        if not row or not any(c.strip() for c in row):
            continue
        day = _date(row[0])
        if day is None:
            out.warnings.append(f"line {line_no}: unreadable date {row[0]!r}")
            out.counts["rows_skipped"] += 1
            continue
        out.days.append(day)
        values = {mapping[i][0]: _number(row[i]) for i in mapping if i < len(row)}
        # "0 kcal and nothing else" is Waistline's way of writing a day without food entries
        others = [values.get(m) for m in _NUTRITION if m != "nutrition.energy"]
        nothing_logged = (values.get("nutrition.energy") or 0) == 0 and all(v is None for v in others)
        if nothing_logged:
            out.counts["days_without_food"] += 1
        noon = datetime.combine(day, time(12, 0), tzinfo=tz)
        for metric, unit in mapping.values():
            v = values.get(metric)
            if v is None or (nothing_logged and metric.startswith("nutrition.")):
                continue
            out.samples.append(DaySample(noon, metric, v, unit))
            out.counts[metric] += 1
    return out
