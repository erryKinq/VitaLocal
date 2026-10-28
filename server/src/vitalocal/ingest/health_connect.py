"""Parser `health-connect`: records the Android app read from Health Connect, as one JSON batch.

Health Connect gathers what other apps write (Samsung Health, Fitbit, Google Fit, Garmin Connect,
Withings, …). The app sends everything of a time window (`range`); the batch is the full truth for
that window, so values that disappeared there are retracted.

Mapping onto the metrics the watch path uses, so energy, sleep and trends work the same:
- steps, active calories: interval sums stamped at the interval's end (`steps`, `active_energy`)
- basal metabolic rate (kcal/day) → `resting_metabolic_rate`; without it but with total
  calories, the day's total minus its active calories
- weight, body fat, heart rate, resting heart rate, SpO₂, HRV (RMSSD): point samples
- sleep: stages stamped at their end with the Garmin codes, the session start as `sleep_in_bed`
- exercise sessions → workouts (see app_imports)

Expected JSON (times ISO 8601 with offset):
{"range": {"from", "to"}, "source_apps": [...],
 "steps": [{"start", "end", "count"}], "active_calories": [{"start", "end", "kcal"}],
 "total_calories": [{"start", "end", "kcal"}], "basal_metabolic_rate": [{"time", "kcal_per_day"}],
 "weight": [{"time", "kg"}], "body_fat": [{"time", "percent"}], "heart_rate": [{"time", "bpm"}],
 "resting_heart_rate": [{"time", "bpm"}], "oxygen_saturation": [{"time", "percent"}],
 "hrv_rmssd": [{"time", "ms"}],
 "sleep": [{"start", "end", "stages": [{"start", "end", "stage"}]}],
 "exercise": [{"id", "start", "end", "type", "title", "kcal"}]}
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

PARSER_NAME = "health-connect"
PARSER_VERSION = "1.0.0"

# Health Connect SleepSessionRecord stage → Garmin sleep code (metrics.SLEEP_STAGES)
STAGES = {1: 1, 2: 2, 3: 1, 4: 2, 5: 3, 6: 4, 0: 0}  # awake, sleeping, out of bed, light, deep, REM, unknown
# ExerciseSessionRecord types → (sport, sub_sport) as FIT names; others: training/generic
EXERCISE = {
    8: ("cycling", None),
    9: ("cycling", "indoor_cycling"),
    56: ("running", None),
    57: ("running", "treadmill"),
    79: ("walking", None),
    37: ("hiking", None),
    74: ("swimming", None),
    75: ("swimming", None),
    70: ("training", "strength_training"),
    81: ("training", "strength_training"),
    80: ("training", "strength_training"),
    36: ("training", "hiit"),
    13: ("training", "cardio_training"),
    83: ("training", "yoga"),
    48: ("training", "pilates"),
    25: ("fitness_equipment", "elliptical"),
    53: ("fitness_equipment", "indoor_rowing"),
    68: ("fitness_equipment", "stair_climbing"),
    61: ("training", "flexibility_training"),
}
POINTS = {
    "weight": ("weight", "kg", "kg"),
    "body_fat": ("body_fat", "percent", "%"),
    "heart_rate": ("heart_rate", "bpm", "bpm"),
    "resting_heart_rate": ("resting_heart_rate", "bpm", "bpm"),
    "oxygen_saturation": ("spo2", "percent", "%"),
    "hrv_rmssd": ("hrv_rmssd", "ms", "ms"),
    "basal_metabolic_rate": ("resting_metabolic_rate", "kcal_per_day", "kcal"),
}


@dataclass
class Exercise:
    id: str
    start: datetime
    end: datetime
    sport: str
    sub_sport: str | None
    title: str | None
    kcal: float | None


@dataclass
class HcParse:
    start: datetime | None = None
    end: datetime | None = None
    samples: list[tuple[datetime, str, float, str | None]] = field(default_factory=list)
    exercises: list[Exercise] = field(default_factory=list)
    source_apps: list[str] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def stats(self) -> dict:
        return {
            "parser": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "range": [
                self.start.isoformat() if self.start else None,
                self.end.isoformat() if self.end else None,
            ],
            "samples": len(self.samples),
            "exercises": len(self.exercises),
            "source_apps": self.source_apps,
            "counts": dict(self.counts),
            "warnings": self.warnings[:50],
        }


def _t(v) -> datetime:
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError(f"time without offset: {v!r}")
    return t


def parse(raw: bytes, timezone: str = "UTC") -> HcParse:
    d = json.loads(raw)
    if not isinstance(d, dict):
        raise ValueError("expected a JSON object")
    out = HcParse()
    rng = d.get("range") or {}
    out.start = _t(rng["from"]) if rng.get("from") else None
    out.end = _t(rng["to"]) if rng.get("to") else None
    out.source_apps = [str(a) for a in d.get("source_apps") or []][:20]
    tz = ZoneInfo(timezone)

    def add(ts: datetime, metric: str, value, unit: str | None) -> None:
        if value is None:
            return
        out.samples.append((ts, metric, float(value), unit))
        out.counts[metric] += 1

    active_by_day: dict[date, float] = defaultdict(float)
    total_by_day: dict[date, float] = defaultdict(float)
    for r in d.get("steps") or []:
        add(_t(r["end"]), "steps", r.get("count"), "steps")
    for r in d.get("active_calories") or []:
        end = _t(r["end"])
        add(end, "active_energy", r.get("kcal"), "kcal")
        active_by_day[end.astimezone(tz).date()] += float(r.get("kcal") or 0)
    for r in d.get("total_calories") or []:
        total_by_day[_t(r["end"]).astimezone(tz).date()] += float(r.get("kcal") or 0)
    for key, (metric, field_name, unit) in POINTS.items():
        for r in d.get(key) or []:
            add(_t(r["time"]), metric, r.get(field_name), unit)
    # resting energy from total − active on days without a basal rate
    bmr_days = {ts.astimezone(tz).date() for ts, m, _, _ in out.samples if m == "resting_metabolic_rate"}
    for day, total in total_by_day.items():
        if day not in bmr_days and total > active_by_day.get(day, 0):
            add(
                datetime.combine(day, time(23, 59), tz),
                "resting_metabolic_rate",
                total - active_by_day[day],
                "kcal",
            )
            out.counts["resting_from_total"] += 1

    for s in d.get("sleep") or []:
        add(_t(s["start"]), "sleep_in_bed", (_t(s["end"]) - _t(s["start"])).total_seconds(), "s")
        stages = sorted(s.get("stages") or [], key=lambda x: x["start"])
        if not stages:  # no stages: the whole session as "light" sleep
            stages = [{"start": s["start"], "end": s["end"], "stage": 4}]
        for st in stages:
            add(_t(st["end"]), "sleep_stage_code", STAGES.get(int(st.get("stage") or 0), 0), None)

    for e in d.get("exercise") or []:
        try:
            sport, sub = EXERCISE.get(int(e.get("type") or 0), ("training", "generic"))
            out.exercises.append(
                Exercise(
                    str(e["id"]),
                    _t(e["start"]),
                    _t(e["end"]),
                    sport,
                    sub,
                    (e.get("title") or None),
                    e.get("kcal"),
                )
            )
        except (KeyError, ValueError) as err:
            out.warnings.append(f"exercise skipped: {err}")
    out.counts["exercises"] = len(out.exercises)
    return out
