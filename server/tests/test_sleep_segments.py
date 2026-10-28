"""Hypnogram segments from Garmin stage samples (stamped at the end of their stage)."""

from datetime import UTC, datetime, timedelta

from vitalocal.api.days import stage_segments

T0 = datetime(2026, 9, 27, 20, 24, tzinfo=UTC)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def test_stage_ends_at_its_sample_and_first_starts_at_sleep_start():
    stages = [(at(8), 2), (at(59), 3), (at(72), 2), (at(73), 1)]
    segs = stage_segments(stages, [T0])
    assert [(s["start"], s["end"], s["stage"]) for s in segs] == [
        (T0, at(8), "light"), (at(8), at(59), "deep"), (at(59), at(72), "light"), (at(72), at(73), "awake"),
    ]  # fmt: skip


def test_without_sleep_start_the_first_stage_is_left_out():
    segs = stage_segments([(at(8), 2), (at(59), 3)], [])
    assert [(s["start"], s["stage"]) for s in segs] == [(at(8), "deep")]


def test_a_new_sleep_start_cuts_the_gap_between_two_sleeps():
    segs = stage_segments([(at(10), 2), (at(300), 3)], [T0, at(240)])
    assert segs[1]["start"] == at(240)  # not the end of the earlier sleep
