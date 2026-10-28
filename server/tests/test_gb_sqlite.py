from datetime import UTC, datetime

from tests.fixtures import DEVICE_MAC, T0_S, make_gb_database
from vitalocal.ingest.gb_sqlite import GadgetbridgeDb, device_source_key


def _parse(path):
    with GadgetbridgeDb(path) as g:
        samples = list(g.samples())
        return samples, g.activities(), g.devices(), g.report


def test_timestamp_units_and_sentinels(tmp_path):
    samples, *_, report = _parse(make_gb_database(tmp_path / "gb"))
    by = {(s.metric, s.ts): s.value for s in samples}
    t0 = datetime.fromtimestamp(T0_S, UTC)

    # seconds-based table and milliseconds-based table land on the same instant
    assert by[("heart_rate", t0)] == 70
    assert by[("stress", t0)] == 25

    # the "nothing measured" minute emits no heart_rate/steps/distance/energy at all
    t1 = datetime.fromtimestamp(T0_S + 60, UTC)
    assert not [s for s in samples if s.ts == t1 and s.metric != "gb.raw_kind"]
    # daily counters become per-interval increases; the first reading only sets the baseline
    # stamped 1 s before the reading, i.e. inside the interval the increase happened in
    t2, t3 = datetime.fromtimestamp(T0_S + 119, UTC), datetime.fromtimestamp(T0_S + 179, UTC)
    assert ("steps", t0) not in by
    assert (by[("steps", t2)], by[("distance", t2)], by[("active_energy", t2)]) == (12, 9.0, 2)
    assert by[("steps", t3)] == 0  # zero steps are real values, not missing

    act = report.tables["GARMIN_ACTIVITY_SAMPLE"]
    assert act.rows_read == 4
    assert act.missing["heart_rate"] == 1 and act.missing["steps"] == 1
    assert act.counter["no_baseline"] == 3  # steps, distance, energy
    assert report.tables["GARMIN_STRESS_SAMPLE"].missing["stress"] == 1


def test_derived_and_keyed_metrics(tmp_path):
    samples, *_ = _parse(make_gb_database(tmp_path / "gb"))
    metrics = {s.metric: s.value for s in samples}
    assert metrics["nap_duration"] == 1200
    assert metrics["garmin.training_readiness"] == 41.0 and metrics["garmin.training_readiness_level"] == 2
    assert "gb.metric.8" not in metrics
    # 11 repeats the resting metabolic rate table: dropped with its extra
    assert not {"gb.metric.11", "gb.metric.11.extra"} & metrics.keys()


def test_report_lists_empty_and_unmapped_tables(tmp_path):
    *_, report = _parse(make_gb_database(tmp_path / "gb"))
    assert "GENERIC_WEIGHT_SAMPLE" in report.empty_tables
    assert report.unmapped_tables == {"SOME_FUTURE_SAMPLE": 1}
    assert "GARMIN_SPO2_SAMPLE" in report.missing_tables
    assert report.schema_known and not report.warnings


def test_unknown_schema_version_warns_but_parses(tmp_path):
    samples, *_, report = _parse(make_gb_database(tmp_path / "gb", user_version=999))
    assert not report.schema_known
    assert report.warnings and samples


def test_implausible_timestamp_unit_marks_table_error(tmp_path):
    import sqlite3

    path = make_gb_database(tmp_path / "gb")
    with sqlite3.connect(path) as con:  # seconds written into a milliseconds table
        con.execute("UPDATE GARMIN_STRESS_SAMPLE SET TIMESTAMP = TIMESTAMP / 1000")
    samples, *_, report = _parse(path)
    assert "implausible" in report.tables["GARMIN_STRESS_SAMPLE"].error
    assert not [s for s in samples if s.metric == "stress"]


def test_devices_are_pseudonymous_and_activities_reference_fit(tmp_path):
    _, activities, devices, _ = _parse(make_gb_database(tmp_path / "gb", activity_fit="A.fit"))
    assert devices[0].source_key == device_source_key(DEVICE_MAC)
    assert DEVICE_MAC not in repr(devices)
    assert activities[0].raw_details_name == "A.fit"
    assert activities[0].summary["averageHR"]["value"] == 90


def test_daily_counter_resets_at_local_midnight(tmp_path):
    import sqlite3
    from zoneinfo import ZoneInfo

    path = make_gb_database(tmp_path / "gb")
    berlin = ZoneInfo("Europe/Berlin")

    def at(hh, mm, day=27):
        return int(datetime(2026, 9, day, hh, mm, tzinfo=berlin).timestamp())

    rows = [
        (at(23, 58, 26), 3097),  # baseline
        (at(0, 0), 3097),  # stamped at interval end: still the previous day, +0
        (at(0, 25), 38),  # new day: counter restarted from 0 -> +38
        (at(0, 30), 75),  # +37
        (at(0, 35), 20),  # drop within a day (e.g. device restart) -> +20
    ]
    with sqlite3.connect(path) as con:
        con.execute("DELETE FROM GARMIN_ACTIVITY_SAMPLE")
        con.executemany("INSERT INTO GARMIN_ACTIVITY_SAMPLE VALUES (?,1,1,0,?,1,60,0,0)", rows)
    with GadgetbridgeDb(path, counter_timezone="Europe/Berlin") as g:
        steps = [(s.ts, s.value) for s in g.samples() if s.metric == "steps"]
        counter = g.report.tables["GARMIN_ACTIVITY_SAMPLE"].counter
    assert [v for _, v in steps] == [0, 38, 37, 20]
    # the 00:00 reading's (zero) increase belongs to the day that just ended
    assert steps[0][0].astimezone(berlin).date().isoformat() == "2026-09-26"
    assert counter["daily_reset"] == 1 * 3 and counter["unexpected_reset"] == 1


def test_windowed_read_equals_the_full_read_from_there(tmp_path):
    """samples(windows) emits exactly what the full read emits from each window on: daily
    counters start from the last valid reading before the window (sentinels, midnight resets
    and a counter drop included)."""
    from datetime import UTC, datetime, timedelta

    from tests.gb_snapshots import GbDatabase, ts_ms, ts_s
    from vitalocal.ingest.gb_sqlite import GadgetbridgeDb

    db = GbDatabase(tmp_path / "gb.sqlite")
    start = datetime(2026, 9, 20, 18, tzinfo=UTC)  # 20:00 local, the night crosses midnight
    for i in range(8 * 60):
        t = start + timedelta(minutes=i)
        steps = -1 if i % 17 == 0 else (i * 3 if t.hour < 22 else (i - 240) * 3)
        if i == 300:
            steps = 2  # device restart: the counter drops
        db.minute(t, hr=0 if i % 11 == 0 else 60 + i % 9, steps=steps, dist_cm=i * 10, kcal=i // 4)
        if i % 3 == 0:
            db.value("GARMIN_STRESS_SAMPLE", t, -1 if i % 9 == 0 else 30)
        if i % 30 == 0:
            db.event(t, 54 if i % 60 else 74, i % 2, i)
            db.metric(t, 8, 50 + i % 7, 3)
    db.con.commit()

    lo = start + timedelta(hours=4, minutes=7)  # inside the data, past midnight local
    windows = {
        "GARMIN_ACTIVITY_SAMPLE": ts_s(lo),
        "GARMIN_STRESS_SAMPLE": ts_ms(lo),
        "GARMIN_EVENT_SAMPLE": ts_ms(lo),
        "GENERIC_METRIC_SAMPLE": ts_ms(lo),
    }
    cumulative = {"steps", "distance", "active_energy"}
    with GadgetbridgeDb(tmp_path / "gb.sqlite", counter_timezone="Europe/Berlin") as full_db:
        full = [
            s
            for s in full_db.samples()
            if s.ts >= (lo - timedelta(seconds=1) if s.metric in cumulative else lo)
        ]
    with GadgetbridgeDb(tmp_path / "gb.sqlite", counter_timezone="Europe/Berlin") as win_db:
        windowed = list(win_db.samples(windows))
        assert win_db.report.tables["GARMIN_ACTIVITY_SAMPLE"].counter.get("no_baseline", 0) == 0
        covered = {(m, lo_m) for _d, m, lo_m in win_db.window_metrics(windows)}
    assert sorted(windowed) == sorted(full) and len(windowed) > 500
    assert ("steps", lo - timedelta(seconds=1)) in covered and ("stress", lo) in covered
    assert ("garmin.training_readiness", lo) in covered and ("gb.event.74.type", lo) in covered
