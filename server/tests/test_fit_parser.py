import pytest

from tests.fixtures import STRENGTH_SETS, make_fit, make_skin_temp_fit
from vitalocal.ingest.fit_parser import parse_fit


def test_strength_sessions_and_sets():
    p = parse_fit(make_fit(sport=10, sub_sport=20, sets=STRENGTH_SETS))
    (sess,) = p.sessions
    assert (sess.sport, sess.sub_sport, sess.elapsed_s, sess.avg_hr) == (
        "training",
        "strength_training",
        1800.0,
        95,
    )
    assert sess.end_ts is not None

    assert [s.set_type for s in p.sets] == ["active", "rest", "active", "rest", "active"]
    warmup, rest, unclassified, _, bench = p.sets
    assert warmup.exercise_category == "cardio"
    assert bench.exercise_category == "bench_press" and bench.category_candidates == [0, 24, 30]
    assert (bench.repetitions, bench.weight_kg) == (9, 23.0)
    # an active set without recognized exercise keeps reps/weight but has no category
    assert unclassified.exercise_category is None and unclassified.repetitions == 9
    # rest sets never get reps/weight, not even 0
    assert rest.repetitions is None and rest.weight_kg is None
    assert p.stats()["active_sets"] == 3


def test_unknown_first_candidate_is_no_category():
    p = parse_fit(
        make_fit(
            sport=10,
            sub_sport=20,
            sets=[{"type": "active", "duration": 10, "reps": 5, "weight": 10, "category": (65534, 0, 0)}],
        )
    )
    assert p.sets[0].exercise_category is None


def test_skin_temp_nightly_values():
    p = parse_fit(make_skin_temp_fit(nightly=36.25, deviation=-0.5))
    got = {s.metric: round(s.value, 2) for s in p.samples}
    assert got == {"daily.skin_temp_nightly": 36.25, "daily.skin_temp_deviation": -0.5}
    assert p.sessions == [] and p.sets == []


def test_corrupt_file_raises():
    with pytest.raises(Exception):  # noqa: B017 - any decode error must fail the parse run
        parse_fit(make_fit(corrupt=True))


def test_skin_temp_without_values_is_reported_not_stored():
    from tests.fixtures import T0_S, U32, FitBuilder, _fit_ts, fit_file_id

    b = fit_file_id(FitBuilder(), 73).message(398, [(253, U32, "I", _fit_ts(T0_S))])
    p = parse_fit(b.build())
    assert p.samples == [] and "no values" in p.warnings[0]


def test_skin_temperature_curve_and_monitoring_ascent():
    from tests.fixtures import F32, T0_S, U16, U32, FitBuilder, _fit_ts, fit_file_id

    b = fit_file_id(FitBuilder(), 73)
    for i, v in enumerate((-6.5, -1.2)):
        b.message(397, [(253, U32, "I", _fit_ts(T0_S + i * 180)), (1, F32, "f", v)])
    curve = [(s.metric, round(s.value, 1)) for s in parse_fit(b.build()).samples]
    assert curve == [("skin_temp_relative", -6.5), ("skin_temp_relative", -1.2)]

    full = _fit_ts(T0_S)
    b = fit_file_id(FitBuilder(), 32)
    b.message(55, [(253, U32, "I", full)])
    b.message(55, [(26, U16, "H", (full + 90) & 0xFFFF), (31, U32, "I", 3048)])  # 3.048 m, 90 s later
    b.message(55, [(26, U16, "H", (full + 200) & 0xFFFF), (32, U32, "I", 6096)])
    got = {s.metric: (round(s.value, 3), int(s.ts.timestamp()) - T0_S) for s in parse_fit(b.build()).samples}
    assert got == {
        "ascent": (3.048, 90),
        "floors_climbed": (1.0, 90),
        "descent": (6.096, 200),
        "floors_descended": (2.0, 200),
    }


def test_heart_rate_zones_and_workout_extras():
    from tests.fixtures import T0_S, U8, U16, U32, FitBuilder, _fit_ts

    b = FitBuilder()
    b.message(0, [(0, 0x00, "B", 4), (4, U32, "I", _fit_ts(T0_S))])
    for i in range(3):  # respiration every second: kept once per 30 s
        b.message(20, [(253, U32, "I", _fit_ts(T0_S + i * 20)), (3, U8, "B", 100), (108, U16, "H", 1850)])
    zone = [(0, U16, "H", 18), (1, U16, "H", 0), (2, U32, "IIIIII", (60_000, 600_000, 300_000, 0, 0, 0)),
            (6, U8, "BBBBB", (95, 114, 133, 152, 171)), (11, U8, "B", 190), (12, U8, "B", 58)]  # fmt: skip
    b.message(216, [(253, U32, "I", _fit_ts(T0_S)), *zone])
    b.message(216, [(253, U32, "I", _fit_ts(T0_S)), (0, U16, "H", 225), *zone[1:]])  # per set: ignored
    b.message(18, [(2, U32, "I", _fit_ts(T0_S)), (5, 0x00, "B", 10), (7, U32, "I", 1_800_000)])
    p = parse_fit(b.build())
    resp = [s for s in p.samples if s.metric == "workout_respiration_rate"]
    assert [round(s.value, 1) for s in resp] == [18.5, 18.5]  # at 0 s and 40 s
    z = p.sessions[0].attrs["hr_zones"]
    assert z["seconds"][:3] == [60, 600, 300] and z["bounds"] == [95, 114, 133, 152, 171]
    assert (z["max_hr"], z["resting_hr"]) == (190, 58)


def test_metrics_file_readiness_factors_sleep_need_body_energy_and_load_focus():
    from tests.fixtures import ENUM, T0_S, U8, U16, U32, FitBuilder, _fit_ts, fit_file_id

    I32 = 0x85
    ts = (253, U32, "I", _fit_ts(T0_S))
    b = fit_file_id(FitBuilder(), 44)
    values = ((5, 72), (8, 77), (10, 100), (12, 100), (14, 85), (16, 83))
    factors = [f for n, v in values for f in ((n, U8, "B", v), (n + 1, ENUM, "B", 4))]  # value, level
    b.message(369, [ts, (0, U8, "B", 75), (4, U8, "B", 81), *factors])
    # level 0: not available yet (here the sleep factor before the night), skipped
    b.message(369, [(253, U32, "I", _fit_ts(T0_S + 60)), (5, U8, "B", 0), (6, ENUM, "B", 0)])
    b.message(410, [ts, (0, U16, "H", 480), (1, U16, "H", 440)])
    b.message(
        384,
        [ts, (9, U32, "I", _fit_ts(T0_S - 8 * 3600)), (11, U32, "I", _fit_ts(T0_S - 600)),
         (14, U8, "B", 34), (16, U8, "B", 75)],
    )  # fmt: skip
    b.message(294, [ts, (0, I32, "i", int(81.2 * 65536)), (1, I32, "i", 0), (2, I32, "i", int(33.9 * 65536))])
    b.message(471, [ts, (0, ENUM, "B", 0)])
    got = {(s.metric, int(s.ts.timestamp()) - T0_S): s.value for s in parse_fit(b.build()).samples}
    assert got == {
        ("garmin.readiness_factor.sleep_score", 0): 72,
        ("garmin.readiness_factor.recovery_time", 0): 77,
        ("garmin.readiness_factor.acute_load", 0): 100,
        ("garmin.readiness_factor.hrv_status", 0): 100,
        ("garmin.readiness_factor.sleep_history", 0): 85,
        ("garmin.readiness_factor.stress_history", 0): 83,
        ("daily.sleep_need", 0): 440,
        ("daily.sleep_body_energy_start", -600): 34,  # stamped at the sleep end
        ("daily.sleep_body_energy_end", -600): 75,
        ("load_focus.low_aerobic", 0): 81.2,
        ("load_focus.high_aerobic", 0): 0,
        ("load_focus.anaerobic", 0): 33.9,
        ("daily.sleep_disruption_severity", 0): 0,
    }


def test_monitor_altitude_and_workout_body_energy():
    from tests.fixtures import T0_S, U8, U16, U32, FitBuilder, _fit_ts, fit_file_id

    b = fit_file_id(FitBuilder(), 32)
    b.message(279, [(253, U32, "I", _fit_ts(T0_S)), (0, U32, "I", 3130)])
    (alt,) = parse_fit(b.build()).samples
    assert (alt.metric, alt.value, alt.unit) == ("altitude", 126.0, "m")

    b = fit_file_id(FitBuilder(), 4)
    b.message(79, [(253, U32, "I", _fit_ts(T0_S)), (15, U8, "B", 52)])
    b.message(18, [(2, U32, "I", _fit_ts(T0_S)), (5, 0x00, "B", 10), (7, U32, "I", 1_800_000)])
    b.message(140, [(253, U32, "I", _fit_ts(T0_S + 1800)), (9, U16, "H", 696), (25, U8, "B", 44)])
    (sess,) = parse_fit(b.build()).sessions
    assert {k: sess.attrs[k] for k in ("body_energy_start", "body_energy_end", "recovery_time_min")} == {
        "body_energy_start": 52,
        "body_energy_end": 44,
        "recovery_time_min": 696,
    }
