"""Full import of the real export (local only; data/ is gitignored).

Expected numbers come from an audit of the developer's own export. Assertions stay at the
row-count level so no health values end up in test output.
"""

import pytest
from sqlalchemy import text

from tests.conftest import REAL_EXPORT
from vitalocal.ingest import service

pytestmark = [
    pytest.mark.db,
    pytest.mark.realdata,
    pytest.mark.skipif(not REAL_EXPORT.exists(), reason="data/Gadgetbridge.zip not present"),
]

AUDIT_ROWS = {
    "GARMIN_ACTIVITY_SAMPLE": 51_529,
    "GARMIN_BODY_ENERGY_SAMPLE": 50_085,
    "GARMIN_STRESS_SAMPLE": 41_369,
    "GARMIN_SPO2_SAMPLE": 38_762,
    "GARMIN_RESPIRATORY_RATE_SAMPLE": 41_679,
    "GARMIN_HRV_VALUE_SAMPLE": 3_083,
    "GARMIN_HRV_SUMMARY_SAMPLE": 34,
    "GARMIN_SLEEP_STAGE_SAMPLE": 805,
    "GARMIN_HEART_RATE_RESTING_SAMPLE": 942,
}


def test_real_export(engine, store):
    batch_id = service.import_file(engine, store, REAL_EXPORT)
    with engine.connect() as conn:
        batch = conn.execute(text("SELECT * FROM ingest_batch WHERE id = :id"), {"id": batch_id}).one()
        run = conn.execute(
            text("SELECT stats FROM parse_run WHERE batch_id = :id AND parser_name = 'gadgetbridge-sqlite'"),
            {"id": batch_id},
        ).scalar_one()
        members = conn.execute(
            text("""
            SELECT category, count(*) FROM artifact_member
            WHERE batch_id = :id AND nested_in IS NULL GROUP BY 1"""),
            {"id": batch_id},
        ).all()
        fit_errors = conn.execute(
            text("""
            SELECT count(*) FROM artifact_member
            WHERE batch_id = :id AND nested_in IS NULL AND meta->'fit' ? 'fit_error'"""),
            {"id": batch_id},
        ).scalar_one()
        linked = conn.execute(
            text(
                "SELECT count(*) FROM activity"
                " WHERE raw_fit_sha256 IS NOT NULL AND gb_parse_run_id IS NOT NULL"
            )
        ).scalar_one()
        unmatched = (
            conn.execute(
                text("""
            SELECT archive_path FROM artifact_member m
            WHERE batch_id = :id AND category = 'ACTIVITY' AND nested_in IS NULL
              AND NOT EXISTS (SELECT 1 FROM activity a WHERE a.raw_fit_sha256 = m.artifact_sha256
                              AND a.gb_parse_run_id IS NOT NULL)
        """),
                {"id": batch_id},
            )
            .scalars()
            .all()
        )

    assert batch.status == "completed", batch.error
    assert batch.client_meta["version"] == "0.94.0"
    assert run["schema_version"] == 140 and run["schema_known"]
    for table, rows in AUDIT_ROWS.items():
        assert run["tables"][table]["rows_read"] == rows, table
        assert "error" not in run["tables"][table]
    assert run["unmapped_tables"] == {}
    samples = run["write"]["samples"]
    assert samples["inserted"] == samples["staged"]

    cats = dict(members)
    assert sum(cats.values()) == 1_550  # every outer archive entry is preserved
    assert cats["ACTIVITY"] == 25 and cats["MONITOR"] == 927 and cats["METRICS"] == 306
    assert fit_errors == 0

    assert linked == 24
    assert [p.rsplit("/", 1)[1] for p in unmatched] == ["ACTIVITY_2026-08-25_19-00-00.fit"]

    fit = batch.stats["fit_parse"]
    # ACTIVITY, SKIN_TEMP, SLEEP, MONITOR, METRICS, SLP_DISR; identical bytes are parsed once
    assert fit["files"] + fit["skipped_known"] == 25 + 34 + 42 + 927 + 306 + 34 and fit["failed"] == []
    with engine.connect() as conn:
        # every GB summary matched a FIT session by start time; Pilates exists from FIT only
        sources = dict(
            conn.execute(
                text("""
            SELECT (gb_parse_run_id IS NOT NULL)::text || '/' || (fit_parse_run_id IS NOT NULL)::text,
                   count(*) FROM activity GROUP BY 1""")
            ).all()
        )
        active_sets = conn.execute(
            text("""
            SELECT a.summary IS NOT NULL, count(*) FROM activity_set s JOIN activity a ON a.id = s.activity_id
            WHERE s.set_type = 'active' AND a.sub_sport = 'strength_training'
            GROUP BY a.id, 1 ORDER BY a.start_ts""")
        ).all()
        skin_nights = conn.execute(
            text("SELECT count(DISTINCT ts) FROM sample WHERE metric = 'daily.skin_temp_nightly'")
        ).scalar_one()
    assert sources == {"true/true": 24, "false/true": 1}
    assert [n for _, n in active_sets] == [19, 14, 21]  # = Gadgetbridge set_N rows (audit)
    assert skin_nights == 32  # 34 files; the first two nights carry no values (baseline phase)
    with engine.connect() as conn:
        workout_hr = conn.execute(
            text("SELECT count(*) FROM sample WHERE metric = 'workout_heart_rate'")
        ).scalar_one()
        assessed = conn.execute(
            text("""SELECT count(*) FROM sample s JOIN sample g
                    ON g.metric = 'daily.sleep_score' AND g.device_id = s.device_id
                    AND abs(extract(epoch FROM g.ts - s.ts)) < 60
                    WHERE s.metric = 'daily.sleep_quality_score'""")
        ).scalar_one()
    assert workout_hr == 21_122  # per-second heart rate from the 25 ACTIVITY FITs
    assert assessed == 34  # every FIT sleep assessment lands on Gadgetbridge's sleep-end time

    # Stage samples are stamped at the end of their stage; the night starts at the FIT sleep start.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from vitalocal.api.days import _sleep

    tz = ZoneInfo("Europe/Berlin")
    with engine.connect() as conn:
        night = _sleep(conn, datetime(2026, 9, 26, 18, tzinfo=tz), datetime(2026, 9, 27, 14, tzinfo=tz))
    first = night["segments"][0]
    clock = lambda t: t.astimezone(tz).strftime("%H:%M:%S")  # noqa: E731
    assert (clock(first["start"]), clock(first["end"]), first["stage"]) == ("00:47:48", "00:53:48", "light")
    assert clock(night["bed_end"]) == "08:05:48"
    assert night["segments"][-1]["stage"] == "rem"  # the last message closes a REM phase


REAL_WAISTLINE = REAL_EXPORT.parent / "waistline_export.json"


@pytest.mark.skipif(not REAL_WAISTLINE.exists(), reason="data/waistline_export.json not present")
def test_real_waistline(engine, store):
    batch_id = service.import_file(engine, store, REAL_WAISTLINE)
    with engine.connect() as conn:
        batch = conn.execute(text("SELECT * FROM ingest_batch WHERE id = :id"), {"id": batch_id}).one()
        counts = dict(
            conn.execute(
                text("""SELECT 'entries', count(*) FROM nutrition_entry
                        UNION ALL SELECT 'days', count(DISTINCT day) FROM nutrition_entry
                        UNION ALL SELECT 'weights', count(*) FROM sample WHERE metric = 'weight'""")
            ).all()
        )
        raw = conn.execute(
            text("SELECT count(*) FROM artifact_member WHERE batch_id = :id"), {"id": batch_id}
        ).scalar_one()
    assert batch.status == "completed", batch.error
    run = batch.stats["waistline_parse_run"]
    assert run["status"] == "completed"
    assert counts == {"entries": 2169, "days": 147, "weights": 97}  # 149 diary days, 2 without food
    # the app's nutrient goals (kJ duplicates kcal, weight is no daily bound)
    assert len(run["goals_changed"]) == 8
    assert raw == 1


REAL_DIARY_CSV = REAL_EXPORT.parent / "diary_export.csv"


@pytest.mark.skipif(not REAL_DIARY_CSV.exists(), reason="data/diary_export.csv not present")
def test_real_waistline_diary_csv(engine, store):
    batch_id = service.import_file(engine, store, REAL_DIARY_CSV)
    with engine.connect() as conn:
        batch = conn.execute(text("SELECT * FROM ingest_batch WHERE id = :id"), {"id": batch_id}).one()
        counts = dict(conn.execute(text("SELECT metric, count(*) FROM sample GROUP BY 1")).all())
    assert batch.status == "completed", batch.error
    assert counts["weight"] == 97  # identical to the JSON export's body stats
    assert counts["nutrition.energy"] == 147  # 149 days, 2 without food entries


def test_real_snapshots_incremental_equal_full(engine, store, tmp_path):
    """Cuts of the real database (T-72h, T-24h, T-12min, T) parsed one after another give the
    same rows as one full parse of T. Only counts and set equality are asserted."""
    import io
    import shutil
    import sqlite3
    import zipfile
    from datetime import UTC, datetime, timedelta

    from vitalocal.ingest.gb_sqlite import TABLE_SPECS

    with zipfile.ZipFile(REAL_EXPORT) as z:
        (tmp_path / "full.sqlite").write_bytes(z.read("database/Gadgetbridge"))
    with sqlite3.connect(tmp_path / "full.sqlite") as con:
        end = con.execute("SELECT max(TIMESTAMP) FROM GARMIN_ACTIVITY_SAMPLE").fetchone()[0]
    t_end = datetime.fromtimestamp(end, UTC)

    def cut(at: datetime, name: str) -> bytes:
        path = tmp_path / f"{name}.sqlite"
        shutil.copyfile(tmp_path / "full.sqlite", path)
        with sqlite3.connect(path) as con:
            for spec in TABLE_SPECS:
                if con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (spec.table,)).fetchone():
                    limit = int(at.timestamp()) * (1000 if spec.ts_unit == "ms" else 1)
                    con.execute(f'DELETE FROM "{spec.table}" WHERE TIMESTAMP > ?', (limit,))
            con.execute(
                "DELETE FROM BASE_ACTIVITY_SUMMARY WHERE START_TIME > ?", (int(at.timestamp()) * 1000,)
            )
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.comment = (
                "data export from Android application nodomain.freeyourgadget.gadgetbridge version 0.94.0 "
                f"from {at.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            ).encode()
            z.write(path, "database/Gadgetbridge")
        return out.getvalue()

    def dump():
        with engine.connect() as conn:
            return set(conn.execute(text("SELECT ts, device_id, metric, value, unit FROM sample")).all())

    modes = []
    for i, back in enumerate((timedelta(hours=72), timedelta(hours=24), timedelta(minutes=12), timedelta(0))):
        batch_id, _ = service.register_upload(engine, store, io.BytesIO(cut(t_end - back, f"c{i}")))
        service.process_batch(engine, store, batch_id)
        with engine.connect() as conn:
            stats = conn.execute(
                text("SELECT stats FROM ingest_batch WHERE id = :id"), {"id": batch_id}
            ).scalar()
        modes.append(stats["gb_sqlite_parse_run"]["mode"])
    assert modes == ["full", "incremental", "incremental", "incremental"]
    incremental = dump()

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM activity_set; DELETE FROM activity; DELETE FROM sample"))
        conn.execute(text("DELETE FROM gb_snapshot_state"))
    service.process_batch(engine, store, batch_id, force=True)
    full = dump()
    assert len(full) > 200_000
    assert len(incremental ^ full) == 0
