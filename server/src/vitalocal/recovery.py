"""Muscle recovery without a database: how much training stimulus each muscle still carries, its
state and the hours until it is ready again.

- A session's stimulus per muscle is counted in effective hard sets: work sets (not warm-up or
  hidden, with reps or, for time exercises, a duration), primary muscles 1, secondary ½ (as
  `training.hard_sets`), weighted by the exercise's rating (too hard 1, good 0.85, too easy 0.6,
  none 0.85) and capped at MAX_SETS_PER_SESSION: beyond that more sets do not add fatigue. One full
  session on a muscle (SETS_PER_UNIT hard sets) is one unit of fatigue.
- Unaccustomed work counts more: the stimulus is scaled by the sets of the 7 days up to the session
  against the weekly average of the 28 days before it (at least NOVICE_WEEKLY_SETS), within
  0.5–1.5. A maintained routine stays at about 1; the first sessions after a break count up to half
  again as much.
- The stimulus decays exponentially with a half-life per muscle: 18 h for small and core muscles
  (delts, rotator cuff, neck, arms), 24 h for the chest, lats, upper back and traps, 36 h for the legs,
  glutes and lower back – so one full session (one unit) is ready after two half-lives: 36 h, 48 h
  and 72 h, the 48–72 h a muscle takes to get its strength back after hard sets.
- Fatigue below 0.25 is ready, up to 0.5 recovering, above fatigued. Hours until ready:
  half-life × log2(fatigue / 0.25).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from vitalocal import training

SETS_PER_UNIT = 8
MAX_SETS_PER_SESSION = 12
SECONDARY = 0.5
RATING_WEIGHT = {"hard": 1.0, "good": 0.85, "easy": 0.6}
NO_RATING = 0.85
NOVICE_WEEKLY_SETS = 4
GAIN_MIN, GAIN_MAX = 0.5, 1.5
ACUTE = timedelta(days=7)
CHRONIC = timedelta(days=28)
READY, FATIGUED = 0.25, 0.5
# below this share a past session no longer shows as contributing
SHOWN_REMAINING = 0.05

H_SMALL, H_MID, H_LEGS = 18.0, 24.0, 36.0
HALF_LIFE_H: dict[str, float] = {
    "chest_upper": H_MID,
    "chest_lower": H_MID,
    "lats": H_MID,
    "upper_back": H_MID,
    "traps": H_MID,
    "delts_front": H_SMALL,
    "delts_side": H_SMALL,
    "delts_rear": H_SMALL,
    "rotator_cuff": H_SMALL,
    "neck": H_SMALL,
    "serratus": H_SMALL,
    "biceps": H_SMALL,
    "triceps": H_SMALL,
    "forearms": H_SMALL,
    "abs": H_SMALL,
    "obliques": H_SMALL,
    "lower_back": H_LEGS,
    "glutes": H_LEGS,
    "quads": H_LEGS,
    "hamstrings": H_LEGS,
    "adductors": H_LEGS,
    "hip_flexors": H_SMALL,
    "calves": H_LEGS,
    "tibialis": H_SMALL,
}


@dataclass
class Session:
    """One workout: its start and work sets ({exercise_id, repetitions, duration_s, warmup, hidden,
    rating}); `provisional` when the sets are VitaLocal's proposal, not yet reviewed."""

    id: int
    ts: datetime
    sets: list[dict]
    provisional: bool = False
    stimulus: dict[str, float] = field(default_factory=dict)  # effective hard sets per muscle
    exercises: dict[str, list[str]] = field(default_factory=dict)  # muscle -> exercise names
    hard_sets: dict[str, float] = field(default_factory=dict)  # plain hard sets per muscle


def half_life_h(muscle: str) -> float:
    return HALF_LIFE_H.get(muscle, H_MID)


def state_of(fatigue: float) -> str:
    return "ready" if fatigue < READY else "recovering" if fatigue <= FATIGUED else "fatigued"


def hours_until_ready(fatigue: float, muscle: str) -> float:
    return 0.0 if fatigue < READY else half_life_h(muscle) * math.log2(fatigue / READY)


def stimulus(sets: list[dict], exercises: dict) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Effective hard sets per muscle of one session (rating-weighted, capped) and the exercises
    that trained each muscle."""
    out: dict[str, float] = {}
    names: dict[str, list[str]] = {}
    for s in sets:
        ex = exercises.get(s.get("exercise_id"))
        if ex is None or s.get("warmup") or s.get("hidden"):
            continue
        if not (s.get("repetitions") or (ex.get("kind") == "time" and s.get("duration_s"))):
            continue
        w = RATING_WEIGHT.get(s.get("rating"), NO_RATING)
        for muscles, share in (
            (ex.get("muscles") or [], 1.0),
            (ex.get("muscles_secondary") or [], SECONDARY),
        ):
            for m in muscles:
                out[m] = out.get(m, 0.0) + w * share
                if ex["name"] not in names.setdefault(m, []):
                    names[m].append(ex["name"])
    return {m: min(v, MAX_SETS_PER_SESSION) for m, v in out.items()}, names


def muscle_state(sessions: list[Session], exercises: dict, muscles, now: datetime) -> dict[str, dict]:
    """Per muscle: `fatigue`, `state`, `ready_in_h`, `half_life_h`, `last_trained` (start of the last
    session that trained it), `hard_sets_7d` (plain hard sets, as in the stats) and the `sessions`
    still contributing ({id, ts, effective sets, exercises, remaining share, provisional}), newest
    first."""
    past = sorted((s for s in sessions if s.ts <= now), key=lambda s: s.ts)
    for s in past:
        s.stimulus, s.exercises = stimulus(s.sets, exercises)
        s.hard_sets = training.hard_sets(s.sets, exercises)
    out = {}
    for m in muscles:
        hl = half_life_h(m)
        fatigue = 0.0
        last = None
        shown = []
        for i, s in enumerate(past):
            sets = s.stimulus.get(m)
            if not sets:
                continue
            last = s.ts
            acute = sum(p.stimulus.get(m, 0) for p in past[: i + 1] if s.ts - p.ts < ACUTE)
            chronic = sum(p.stimulus.get(m, 0) for p in past[:i] if timedelta(0) < s.ts - p.ts <= CHRONIC)
            gain = min(max(acute / max(chronic / 4, NOVICE_WEEKLY_SETS), GAIN_MIN), GAIN_MAX)
            remaining = 0.5 ** ((now - s.ts).total_seconds() / 3600 / hl)
            fatigue += sets / SETS_PER_UNIT * gain * remaining
            if remaining >= SHOWN_REMAINING:
                shown.append({
                    "id": s.id, "ts": s.ts, "sets": round(sets, 1), "exercises": s.exercises.get(m, []),
                    "remaining": round(remaining, 2), "provisional": s.provisional,
                })  # fmt: skip
        fatigue = round(fatigue, 3)
        out[m] = {
            "fatigue": fatigue,
            "state": state_of(fatigue),
            "ready_in_h": round(hours_until_ready(fatigue, m), 1),
            "half_life_h": hl,
            "last_trained": last,
            "hard_sets_7d": round(sum(s.hard_sets.get(m, 0) for s in past if now - s.ts < ACUTE), 1),
            "sessions": shown[::-1],
        }
    return out
