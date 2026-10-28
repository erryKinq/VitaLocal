"""Range rules of automatic habits, including clock metrics that order across midnight."""

import pytest
from pydantic import ValidationError

from vitalocal.api.habits import HabitIn, deviation, within
from vitalocal.metrics import hhmm


def hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def test_plain_metric_bounds():
    assert within("steps", 8000, 8000, None) and not within("steps", 7999, 8000, None)
    assert within("nutrition.protein", 140, 120, 160)
    assert deviation("steps", 7000, 8000, None) == -1000
    assert deviation("nutrition.salt", 7.5, None, 6) == 1.5
    assert deviation("nutrition.protein", 140, 120, 160) == 0


def test_bedtime_window_across_midnight():
    lo, hi = hm("23:40"), hm("00:20")
    assert within("sleep.bedtime", hm("23:50"), lo, hi)
    assert within("sleep.bedtime", hm("00:05"), lo, hi)
    assert not within("sleep.bedtime", hm("01:00"), lo, hi)
    assert deviation("sleep.bedtime", hm("01:00"), lo, hi) == 40  # 40 min late
    assert deviation("sleep.bedtime", hm("23:00"), lo, hi) == -40  # 40 min early


def test_one_sided_bedtime_counts_after_midnight_as_late():
    # "in bed by 23:00": 00:30 is 90 minutes late, not "early in the day"
    assert not within("sleep.bedtime", hm("00:30"), None, hm("23:00"))
    assert deviation("sleep.bedtime", hm("00:30"), None, hm("23:00")) == 90
    # wake time: 13:00 is late, not early (the sleep day runs from 18:00)
    assert not within("sleep.wake_time", hm("13:00"), None, hm("06:30"))


def test_caffeine_cutoff_uses_the_calendar_day():
    assert within("drinks.last_caffeine", hm("08:00"), None, hm("14:00"))
    assert deviation("drinks.last_caffeine", hm("15:00"), None, hm("14:00")) == 60
    assert hhmm(hm("07:05")) == "07:05"


def test_rule_validation():
    ok = HabitIn(name="Bett", metric="sleep.bedtime", min_value=hm("23:40"), max_value=hm("00:20"))
    assert (ok.min_value, ok.max_value) == (1420, 20)
    with pytest.raises(ValidationError, match="must not be above"):
        HabitIn(name="Bett", metric="sleep.bedtime", min_value=hm("00:20"), max_value=hm("23:40"))
    with pytest.raises(ValidationError, match="minutes after midnight"):
        HabitIn(name="Bett", metric="sleep.bedtime", max_value=1440)
    with pytest.raises(ValidationError, match="must not be above"):
        HabitIn(name="Protein", metric="nutrition.protein", min_value=160, max_value=120)
    with pytest.raises(ValidationError, match="need a metric"):
        HabitIn(name="x", min_value=1)
    with pytest.raises(ValidationError, match="min_value and/or max_value"):
        HabitIn(name="x", metric="steps")
    # the one-sided rule of API ≤ 0.2 still works
    legacy = HabitIn(name="Salz", metric="nutrition.salt", comparator="<=", threshold=6)
    assert (legacy.min_value, legacy.max_value) == (None, 6)
    assert "comparator" not in legacy.model_dump()
