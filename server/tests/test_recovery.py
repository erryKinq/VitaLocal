"""Muscle recovery: stimulus per session, decay with half-life, state and hours until ready."""

from datetime import UTC, datetime, timedelta

import pytest

from vitalocal import recovery
from vitalocal.recovery import Session

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
EX = {
    "bench": {"name": "Bankdrücken", "kind": "reps", "muscles": ["chest"], "muscles_secondary": ["triceps"]},
    "squat": {"name": "Kniebeuge", "kind": "reps", "muscles": ["quads", "glutes"], "muscles_secondary": []},
    "plank": {"name": "Plank", "kind": "time", "muscles": ["abs"], "muscles_secondary": []},
}
MUSCLES = ["chest", "triceps", "quads", "glutes", "abs", "back"]


def sets(ex, n, rating="hard", **kw):
    return [{"exercise_id": ex, "repetitions": 10, "rating": rating, **kw} for _ in range(n)]


def session(hours_ago, s, sid=1, provisional=False):
    return Session(id=sid, ts=NOW - timedelta(hours=hours_ago), sets=s, provisional=provisional)


def test_stimulus_counts_work_sets_weighted_by_rating_and_capped():
    s = (
        sets("bench", 3, "hard")
        + sets("bench", 2, "easy")
        + sets("bench", 1, warmup=True)
        + sets("bench", 1, hidden=True)
        + [{"exercise_id": "bench", "repetitions": None}]
        + [{"exercise_id": "plank", "duration_s": 45}]
        + [{"exercise_id": None, "repetitions": 10}]
    )
    st, names = recovery.stimulus(s, EX)
    assert st["chest"] == pytest.approx(3 + 2 * 0.6)
    assert st["triceps"] == pytest.approx((3 + 2 * 0.6) / 2)
    assert st["abs"] == pytest.approx(recovery.NO_RATING)  # a hold without rating
    assert names == {"chest": ["Bankdrücken"], "triceps": ["Bankdrücken"], "abs": ["Plank"]}
    st, _ = recovery.stimulus(sets("squat", 20), EX)
    assert st["quads"] == recovery.MAX_SETS_PER_SESSION


def test_fatigue_decays_with_the_muscles_half_life():
    # a veteran's usual week: 8 hard sets of bench every 7 days for a month, the last one now
    hist = [session(24 * 7 * k, sets("bench", 8), sid=k) for k in range(5)]
    st = recovery.muscle_state(hist, EX, MUSCLES, NOW)
    assert st["chest"]["fatigue"] == pytest.approx(1.0, abs=0.05)  # one unit, gain ≈ 1, ~2 % of last week
    assert st["chest"]["state"] == "fatigued"
    # 24 h half-life: one unit is ready after two half-lives (log2(1/0.25) = 2) → 48 h
    assert st["chest"]["ready_in_h"] == pytest.approx(48, abs=2)
    later = recovery.muscle_state(hist, EX, MUSCLES, NOW + timedelta(hours=24))
    assert later["chest"]["fatigue"] == pytest.approx(0.5, abs=0.03)  # one half-life
    assert (
        recovery.muscle_state(hist, EX, MUSCLES, NOW + timedelta(hours=29))["chest"]["state"] == "recovering"
    )
    done = recovery.muscle_state(hist, EX, MUSCLES, NOW + timedelta(hours=51))
    assert done["chest"]["state"] == "ready" and done["chest"]["ready_in_h"] == 0
    # legs decay slower than chest
    legs = recovery.muscle_state(
        [session(30, sets("squat", 4)), session(30, sets("bench", 4), sid=2)], EX, MUSCLES, NOW
    )
    assert legs["quads"]["fatigue"] > legs["chest"]["fatigue"]


def test_unaccustomed_work_counts_more_and_is_capped():
    first = recovery.muscle_state([session(0, sets("bench", 8))], EX, MUSCLES, NOW)
    assert first["chest"]["fatigue"] == pytest.approx(1.5)  # 8 sets / novice 4 per week → gain 2, capped
    # a light session after heavy weeks counts less (gain ≥ 0.5)
    hist = [session(24 * 7 * k, sets("bench", 12), sid=k) for k in range(1, 5)]
    light = recovery.muscle_state([*hist, session(0, sets("bench", 2), sid=9)], EX, MUSCLES, NOW)
    only_light = 2 / recovery.SETS_PER_UNIT * 0.5
    old = sum(12 / 8 * 0.5 ** (24 * 7 * k / 24) for k in range(1, 5))
    assert light["chest"]["fatigue"] == pytest.approx(only_light + old, abs=0.05)


def test_state_lists_untrained_muscles_and_contributing_sessions():
    st = recovery.muscle_state(
        [session(200, sets("squat", 3), sid=1), session(20, sets("bench", 3), sid=2, provisional=True)],
        EX,
        MUSCLES,
        NOW,
    )
    assert st["back"] == {
        "fatigue": 0.0, "state": "ready", "ready_in_h": 0.0, "half_life_h": 24.0,
        "last_trained": None, "hard_sets_7d": 0, "sessions": [],
    }  # fmt: skip
    chest = st["chest"]
    assert chest["last_trained"] == NOW - timedelta(hours=20)
    assert chest["hard_sets_7d"] == 3
    (s,) = chest["sessions"]
    assert (s["id"], s["exercises"], s["provisional"]) == (2, ["Bankdrücken"], True)
    assert s["remaining"] == pytest.approx(0.5 ** (20 / 24), abs=0.01)
    # squats 120 h ago: 36 h half-life leaves ~10 % – still shown; 200 h ago: neither shown nor in 7 days
    quads = recovery.muscle_state([session(120, sets("squat", 3))], EX, MUSCLES, NOW)["quads"]
    assert quads["hard_sets_7d"] == 3 and len(quads["sessions"]) == 1
    assert st["quads"]["hard_sets_7d"] == 0 and st["quads"]["sessions"] == []
    assert (
        recovery.muscle_state([session(400, sets("squat", 3))], EX, MUSCLES, NOW)["quads"]["sessions"] == []
    )
    # sessions after `now` are ignored
    assert recovery.muscle_state([session(-5, sets("bench", 3))], EX, MUSCLES, NOW)["chest"]["fatigue"] == 0
