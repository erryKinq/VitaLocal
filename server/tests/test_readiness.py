from datetime import UTC, date, datetime, timedelta

from vitalocal.insights import readiness, readiness_level

DAY = date(2026, 9, 30)
NOW = datetime(2026, 9, 30, 5, 0, tzinfo=UTC)


def _values(sleep=83, weekly=39, lower=39, stress=25):
    days = [DAY - timedelta(days=i) for i in range(4)]
    return {
        "daily.sleep_score": {d: sleep for d in days},
        "daily.hrv_weekly_avg": {DAY: weekly},
        "daily.hrv_baseline_balanced_lower": {DAY: lower},
        "training_load_acute": {DAY: 38},
        "training_load_chronic": {DAY: 90},
        "stress": {d: stress for d in days},
    }


def impacts(r):
    return {c["key"]: c["impact"] for c in r["components"]}


def test_good_factors_cost_little():
    r = readiness(_values(), DAY, NOW)
    assert impacts(r) == {
        "sleep_score": -10,
        "hrv_status": 0,
        "acute_load": 0,
        "sleep_history": -2,
        "stress_history": 0,
    }
    assert r["score"] == 88 and r["level"] == "high"


def test_long_recovery_time_caps_the_score():
    # 2026-09-30: 20.3 h of recovery left on waking; Garmin said 71 (moderate)
    r = readiness(_values(), DAY, NOW, (NOW - timedelta(minutes=1), 20.3 * 60 + 1))
    assert r["score"] == 72 and r["level"] == "moderate"
    assert impacts(r)["recovery_time"] == -16
    # a short one (Garmin: 10 h left hardly mattered) costs nothing
    assert readiness(_values(), DAY, NOW, (NOW, 9 * 60))["score"] == 88


def test_poor_sleep_low_hrv_and_stress_cost_points():
    r = readiness(_values(sleep=50, weekly=35, lower=40, stress=40), DAY, NOW)
    i = impacts(r)
    assert i["sleep_score"] == -42 and i["hrv_status"] == -6 and i["stress_history"] == -15
    assert r["score"] == 30 and r["level"] == "low"


def test_levels_are_garmins_bands():
    assert [readiness_level(x) for x in (100, 95, 94, 75, 74, 50, 49, 25, 24, 0)] == [
        "prime", "prime", "high", "high", "moderate", "moderate", "low", "low", "poor", "poor",
    ]  # fmt: skip


def test_needs_last_nights_sleep_and_two_more():
    assert readiness({"daily.sleep_score": {DAY: 80}}, DAY, NOW)["score"] is None
    v = _values()
    del v["daily.sleep_score"][DAY]
    assert readiness(v, DAY, NOW)["score"] is None
