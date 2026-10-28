"""Parser `workout-csv`: strength workouts from Strong and Hevy CSV exports (one row per set).

Strong: `Date;Workout Name;Duration;Exercise Name;Set Order;Weight;Reps;Distance;Seconds;Notes;
Workout Notes;RPE` (weight in the unit set in the app; `Set Order` W = warm-up). Hevy: `title,
start_time,end_time,…,exercise_title,…,set_index,set_type,weight_kg|weight_lbs,reps,…,
duration_seconds,rpe`. Each workout becomes a manual VitaLocal workout; exercise names are matched
to the built-in library (by its English key words, equipment must agree) or to own exercises by
name, otherwise an own exercise of that name is created.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PARSER_NAME = "workout-csv"
PARSER_VERSION = "1.0.0"
LB = 0.45359237

EQUIPMENT = {"barbell", "dumbbell", "cable", "machine", "band", "kettlebell", "ez", "smith", "ring", "trap"}
# words that describe but do not tell exercises apart ("Bent Over Row (Barbell)" = barbell row)
STOP = {
    "bent",
    "over",
    "bicep",
    "biceps",
    "tricep",
    "triceps",
    "the",
    "a",
    "with",
    "standing",
    "flat",
    "assisted",
}
SYNONYMS = {
    "dumbbells": "dumbbell",
    "db": "dumbbell",
    "bb": "barbell",
    "pulldowns": "pulldown",
    "ups": "up",
    "squats": "squat",
    "curls": "curl",
    "rows": "row",
    "raises": "raise",
    "presses": "press",
    "fly": "flye",
    "flyes": "flye",
    "pushup": "push",
    "pullup": "pull",
    "chinup": "chin",
    "bodyweight": "air",
    "pushdown": "pressdown",
    "pushdowns": "pressdown",
}
DEFAULTS = {
    frozenset({"squat", "barbell"}): "barbell_back_squat",
    frozenset({"squat"}): "air_squat",
    frozenset({"deadlift"}): "barbell_deadlift",
    frozenset({"bench", "press"}): "barbell_bench_press",
    frozenset({"overhead", "press"}): "overhead_barbell_press",
    frozenset({"pull", "up"}): "pull_up",
    frozenset({"chin", "up"}): "chin_up",
    frozenset({"push", "up"}): "push_up",
}


@dataclass
class SetRow:
    exercise: str
    order: int
    reps: int | None
    weight_kg: float | None
    duration_s: float | None
    warmup: bool
    rpe: float | None


@dataclass
class Workout:
    start: datetime
    seconds: float
    title: str
    note: str | None
    sets: list[SetRow] = field(default_factory=list)

    @property
    def rpe(self) -> float | None:
        vals = [s.rpe for s in self.sets if s.rpe]
        return round(sum(vals) / len(vals), 1) if vals else None


@dataclass
class WorkoutParse:
    app: str
    workouts: list[Workout] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def stats(self) -> dict:
        return {
            "parser": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "app": self.app,
            "workouts": len(self.workouts),
            "sets": sum(len(w.sets) for w in self.workouts),
            "counts": dict(self.counts),
            "warnings": self.warnings[:50],
        }


def _key(h: str) -> str:
    return re.sub(r"[^a-z]", "", h.lower())


def _header(head: bytes) -> list[str]:
    line = head.decode("utf-8-sig", errors="ignore").splitlines()[0] if head else ""
    delimiter = max((";", ",", "\t"), key=line.count)
    return [_key(h) for h in next(csv.reader([line], delimiter=delimiter), [])]


def app_of(header: list[str]) -> str | None:
    h = set(header)
    if {"exercisetitle", "starttime", "setindex"} <= h:
        return "hevy"
    if {"exercisename", "setorder", "date"} <= h and ("reps" in h or "weight" in h):
        return "strong"
    return None


def looks_like_workout_csv(head: bytes) -> bool:
    return app_of(_header(head)) is not None


def _num(cell: str | None) -> float | None:
    cell = (cell or "").strip()
    if not cell:
        return None
    try:
        return float(cell.replace(",", "."))
    except ValueError:
        return None


def _time(cell: str) -> datetime | None:
    cell = cell.strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d %b %Y, %H:%M",
        "%d %B %Y, %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%m/%d/%Y %H:%M",
    ):
        try:
            return datetime.strptime(cell, fmt)
        except ValueError:
            continue
    return None


def _duration(cell: str) -> float | None:
    """Strong: "1h 5m", "45m", "50s"; plain numbers are seconds."""
    cell = (cell or "").strip().lower()
    if not cell:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", cell):
        return float(cell)
    parts = dict((u, float(v)) for v, u in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", cell))
    return parts.get("h", 0) * 3600 + parts.get("m", 0) * 60 + parts.get("s", 0) or None


def parse(raw: bytes, timezone: str = "UTC", weight_unit: str = "kg") -> WorkoutParse:
    """`weight_unit` applies to Strong, whose export does not say (the unit set in the app)."""
    text = raw.decode("utf-8-sig", errors="replace")
    first = text.splitlines()[0] if text else ""
    delimiter = max((";", ",", "\t"), key=first.count)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    header = [_key(h) for h in rows[0]] if rows else []
    app = app_of(header)
    if app is None:
        raise ValueError("not a Strong or Hevy CSV export")
    out = WorkoutParse(app)
    col = {h: i for i, h in enumerate(header)}
    tz = ZoneInfo(timezone)

    def cell(row: list[str], *names: str) -> str | None:
        for n in names:
            i = col.get(n)
            if i is not None and i < len(row):
                return row[i]
        return None

    by_start: dict[datetime, Workout] = {}
    for line_no, row in enumerate(rows[1:], start=2):
        if not row or not any(c.strip() for c in row):
            continue
        start = _time(cell(row, "date", "starttime") or "")
        name = (cell(row, "exercisename", "exercisetitle") or "").strip()
        if start is None or not name:
            out.warnings.append(f"line {line_no}: no start time or exercise")
            out.counts["rows_skipped"] += 1
            continue
        start = start.replace(tzinfo=tz)
        w = by_start.get(start)
        if w is None:
            end = _time(cell(row, "endtime") or "")
            seconds = (
                (end.replace(tzinfo=tz) - start).total_seconds()
                if end
                else _duration(cell(row, "duration") or "")
            )
            w = by_start[start] = Workout(
                start,
                seconds or 0,
                (cell(row, "workoutname", "title") or "").strip() or "Training",
                (cell(row, "workoutnotes", "description") or "").strip() or None,
            )
        if app == "hevy":
            kg = _num(cell(row, "weightkg"))
            if kg is None and (lbs := _num(cell(row, "weightlbs"))) is not None:
                kg = lbs * LB
            warmup = (cell(row, "settype") or "").strip().lower() == "warmup"
            order = int(_num(cell(row, "setindex")) or 0)
        else:
            weight = _num(cell(row, "weight"))
            unit = (cell(row, "weightunit") or weight_unit).strip().lower()
            kg = weight * LB if weight is not None and unit.startswith("lb") else weight
            order_cell = (cell(row, "setorder") or "").strip().upper()
            warmup = order_cell == "W"
            order = int(_num(order_cell) or 0)
        reps = _num(cell(row, "reps"))
        secs = _num(cell(row, "seconds", "durationseconds"))
        if not reps and not secs:
            out.counts["sets_without_reps_or_time"] += 1
            continue
        w.sets.append(
            SetRow(
                name,
                order,
                int(reps) if reps else None,
                round(kg, 2) if kg else None,
                secs or None,
                warmup,
                _num(cell(row, "rpe")),
            )
        )
        out.counts["sets"] += 1
    for w in sorted(by_start.values(), key=lambda w: w.start):
        if not w.sets:
            continue
        if not w.seconds:  # no duration in the export: sets × 2.5 min
            w.seconds = len(w.sets) * 150
        out.workouts.append(w)
    return out


def end_of(w: Workout) -> datetime:
    return w.start + timedelta(seconds=w.seconds)


# --- exercise names ------------------------------------------------------------------------------------


def tokens(name: str) -> frozenset[str]:
    words = re.findall(r"[a-z]+", name.lower())
    return frozenset(SYNONYMS.get(w, w) for w in words if SYNONYMS.get(w, w) not in STOP)


def library_match(name: str, library: list[dict]) -> str | None:
    """The library key whose words best match an exercise name from another app, or None."""
    want = tokens(name)
    if not want:
        return None
    if want in DEFAULTS:
        return DEFAULTS[want]
    gear = want & EQUIPMENT
    best, score = None, 0.0
    for e in library:
        have = tokens(e["key"].replace("_", " "))
        if gear and not (gear <= have) and have & EQUIPMENT:
            continue  # "(Dumbbell)" is never the barbell variant
        core = want - EQUIPMENT
        if not core <= have:
            continue
        s = len(want & have) / len(want | have)
        if s > score:
            best, score = e["key"], s
    return best if score >= 0.5 else None
