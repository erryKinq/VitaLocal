"""Parser `daily-csv`: daily totals from other apps' CSV exports (nutrition and body values).

Covers exports with one or more rows per day and a date column, e.g. MyFitnessPal's nutrition
summary (one row per meal: summed per day), Cronometer's daily summary, or a hand-made weight list
(`date,weight[,body_fat]`). Columns are matched by words in English and German; the column with
the fewest extra words wins (`Fat (g)` before `Trans Fat`). Sodium becomes salt (× 2.5).

Like the Waistline diary CSV these are totals: per-food detail stays in the other app. A day whose
nutrition cells are all empty or 0 has nothing logged and gets no nutrition values. Values are
stamped at local noon of their day.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

PARSER_NAME = "daily-csv"
PARSER_VERSION = "1.0.0"

LB = 0.45359237
# metric, unit, word sets (any set fully in the header's words matches)
COLUMNS = [
    ("nutrition.energy", "kcal", [{"calories"}, {"kalorien"}, {"energy"}, {"energie"}, {"kcal"}]),
    ("nutrition.protein", "g", [{"protein"}, {"eiweiss"}, {"eiweis"}]),
    ("nutrition.carbohydrates", "g", [{"carbohydrates"}, {"carbs"}, {"carbohydrate"}, {"kohlenhydrate"}]),
    ("nutrition.fat", "g", [{"fat"}, {"fett"}]),
    ("nutrition.saturated_fat", "g", [{"saturated"}, {"saturated", "fat"}, {"gesattigte"}]),
    ("nutrition.sugars", "g", [{"sugar"}, {"sugars"}, {"zucker"}]),
    ("nutrition.fiber", "g", [{"fiber"}, {"fibre"}, {"ballaststoffe"}]),
    ("nutrition.salt", "g", [{"salt"}, {"salz"}]),
    ("sodium", "mg", [{"sodium"}, {"natrium"}]),
    ("weight", "kg", [{"weight"}, {"gewicht"}, {"body", "weight"}]),
    ("body_fat", "%", [{"body", "fat"}, {"fat", "percent"}, {"korperfett"}, {"bodyfat"}]),
]
# header words that are units or filler, not part of the name
FILLER = {"g", "mg", "kg", "lb", "lbs", "kcal", "kj", "percent", "pct", "in", "total", "gesamt"}
DATE_WORDS = ({"date"}, {"datum"}, {"day"}, {"tag"})
NUTRITION = [m for m, _, _ in COLUMNS if m.startswith("nutrition.")]


@dataclass
class DaySample:
    ts: datetime
    metric: str
    value: float
    unit: str


@dataclass
class DailyParse:
    app: str
    days: list[date] = field(default_factory=list)
    samples: list[DaySample] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)
    ignored_columns: list[str] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def stats(self) -> dict:
        return {
            "parser": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "app": self.app,
            "days": len(self.days),
            "samples": len(self.samples),
            "columns": self.columns,
            "ignored_columns": self.ignored_columns,
            "counts": dict(self.counts),
            "warnings": self.warnings[:50],
        }


def words(header: str) -> set[str]:
    h = header.lower()
    for a, b in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss"), ("%", " percent ")):
        h = h.replace(a, b)
    return set(re.findall(r"[a-z]+", h))


def _rows(raw: bytes) -> list[list[str]]:
    text = raw.decode("utf-8-sig", errors="replace")
    first = text.splitlines()[0] if text else ""
    delimiter = max((";", ",", "\t"), key=first.count)
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def _first_line(head: bytes) -> list[str]:
    line = head.decode("utf-8-sig", errors="ignore").splitlines()[0] if head else ""
    delimiter = max((";", ",", "\t"), key=line.count)
    return next(csv.reader([line], delimiter=delimiter), [])


def _date_col(header: list[str]) -> int | None:
    for i, name in enumerate(header):
        w = words(name)
        if any(d <= w and len(w - d - FILLER) == 0 for d in DATE_WORDS):
            return i
    return None


def _match(header: list[str]) -> dict[int, tuple[str, str]]:
    """Column index → (metric, unit); per metric the header with the fewest extra words."""
    best: dict[str, tuple[int, int]] = {}
    for i, name in enumerate(header):
        w = words(name)
        for metric, _unit, sets in COLUMNS:
            extras = [len(w - s - FILLER) for s in sets if s <= w]
            if extras and (metric not in best or min(extras) < best[metric][1]):
                best[metric] = (i, min(extras))
    # one column, one metric: "Body Fat" is body fat, not fat
    taken: dict[int, tuple[str, int]] = {}
    for metric, (i, extra) in sorted(best.items(), key=lambda kv: kv[1][1]):
        if i not in taken:
            taken[i] = (metric, extra)
    units = {m: u for m, u, _ in COLUMNS}
    out = {}
    for i, (metric, extra) in taken.items():
        if extra <= 2:
            unit = units[metric]
            if metric == "weight" and words(header[i]) & {"lb", "lbs", "pounds"}:
                unit = "lb"
            if metric == "nutrition.energy" and "kj" in words(header[i]):
                unit = "kj"
            out[i] = (metric, unit)
    return out


def app_of(header: list[str]) -> str:
    w = set().union(*(words(h) for h in header)) if header else set()
    if "meal" in w and "calories" in w:
        return "myfitnesspal"
    if "energy" in w and "completed" in w:
        return "cronometer"
    return "csv"


def looks_like_daily_csv(head: bytes) -> bool:
    header = _first_line(head)
    if _date_col(header) is None:
        return False
    return bool(set(m for m, _ in _match(header).values()) & ({"weight", "body_fat"} | set(NUTRITION)))


def _number(cell: str) -> float | None:
    cell = cell.strip().replace(" ", "")
    if not cell:
        return None
    if "," in cell and "." in cell:  # 1.234,5 or 1,234.5
        cell = cell.replace(".", "") if cell.rfind(",") > cell.rfind(".") else cell.replace(",", "")
    try:
        return float(cell.replace(",", "."))
    except ValueError:
        return None


def _date(cell: str) -> date | None:
    cell = cell.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(cell[: len(datetime.now().strftime(fmt))], fmt).date()
        except ValueError:
            continue
    return None


def parse(raw: bytes, timezone: str = "UTC") -> DailyParse:
    rows = _rows(raw)
    header = rows[0] if rows else []
    date_col = _date_col(header)
    if date_col is None:
        raise ValueError("no date column")
    mapping = _match(header)
    out = DailyParse(app_of(header))
    for i, name in enumerate(header):
        if i in mapping:
            out.columns[name] = mapping[i][0]
        elif i != date_col:
            out.ignored_columns.append(name)

    totals: dict[date, dict[str, float]] = defaultdict(dict)
    for line_no, row in enumerate(rows[1:], start=2):
        if not row or not any(c.strip() for c in row) or date_col >= len(row):
            continue
        day = _date(row[date_col])
        if day is None:
            if row[date_col].strip().lower() not in ("total", "totals", "summe"):
                out.warnings.append(f"line {line_no}: unreadable date {row[date_col]!r}")
            out.counts["rows_skipped"] += 1
            continue
        out.counts["rows"] += 1
        t = totals[day]
        for i, (metric, unit) in mapping.items():
            v = _number(row[i]) if i < len(row) else None
            if v is None:
                continue
            if unit == "kj":
                v /= 4.184
            if metric in ("weight", "body_fat"):
                t[metric] = v * LB if unit == "lb" else v  # last one of the day
            else:
                t[metric] = t.get(metric, 0.0) + v

    tz = ZoneInfo(timezone)
    units = {m: u for m, u, _ in COLUMNS}
    for day in sorted(totals):
        t = totals[day]
        if "sodium" in t:
            t.setdefault("nutrition.salt", t["sodium"] * 2.5 / 1000)
        logged = any(t.get(m) for m in NUTRITION)
        if not logged and any(m in t for m in NUTRITION):
            out.counts["days_without_food"] += 1
        out.days.append(day)
        noon = datetime.combine(day, time(12, 0), tzinfo=tz)
        for metric, v in t.items():
            if metric == "sodium" or (metric in NUTRITION and not logged):
                continue
            out.samples.append(DaySample(noon, metric, round(v, 3), units[metric]))
            out.counts[metric] += 1
    return out
