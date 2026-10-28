"""Incremental Gadgetbridge parsing equals a full parse; snapshot order; queue and status."""

from __future__ import annotations

import io
import json
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.fixtures import make_export_zip
from tests.gb_snapshots import GbDatabase
from vitalocal.ingest import service, worker

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}
TZ = ZoneInfo("Europe/Berlin")
D = datetime(2026, 9, 21, tzinfo=UTC)
MIN = timedelta(minutes=1)


def _minutes(db: GbDatabase, start: datetime, end: datetime, *, hr_shift: int = 0) -> None:
    """Monitoring minutes with daily counters that restart at local midnight, plus stress and
    body energy streams."""
    t = start
    while t < end:
        local = (t - timedelta(seconds=1)).astimezone(TZ)  # stamped at the end of the minute
        m = local.hour * 60 + local.minute
        db.minute(t, hr=60 + m % 25 + hr_shift, steps=m * 7, dist_cm=m * 500, kcal=m // 3)
        if m % 3 == 0:
            db.value("GARMIN_STRESS_SAMPLE", t, 20 + m % 40)
        if m % 5 == 0:
            db.value("GARMIN_BODY_ENERGY_SAMPLE", t, 50 + m % 30)
        t += MIN


def _q(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params)


def _scalar(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def _batch(engine, batch_id: int):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT status, stats FROM ingest_batch WHERE id = :id"), {"id": batch_id}
        ).one()


def _import(engine, store, db: GbDatabase, tmp_path, exported_at: datetime, name: str) -> dict:
    path = db.export(tmp_path / f"{name}.zip", exported_at)
    with open(path, "rb") as f:
        batch_id, _ = service.register_upload(engine, store, f)
    service.process_batch(engine, store, batch_id)
    row = _batch(engine, batch_id)
    assert row.status == "completed", row.stats
    return {"batch_id": batch_id, **row.stats}


def _dump(engine) -> tuple[set, set]:
    with engine.connect() as conn:
        samples = {
            tuple(r) for r in conn.execute(text("SELECT ts, device_id, metric, value, unit FROM sample"))
        }
        activities = {
            (r.start_ts, r.end_ts, r.name, json.dumps(r.summary, sort_keys=True), r.raw_details_name)
            for r in conn.execute(text("SELECT * FROM activity"))
        }
    return samples, activities


def _wipe_parsed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""DELETE FROM activity_set; DELETE FROM activity; DELETE FROM sample;
                    DELETE FROM gb_snapshot_state""")
        )


def _mode(stats: dict) -> str:
    return stats["gb_sqlite_parse_run"]["mode"]


def test_incremental_snapshots_equal_a_full_parse(engine, store, tmp_path, monkeypatch):
    # a 12 h overlap, so the later snapshots start reading mid-history (counter baselines,
    # retraction per metric); the default 72 h behaves the same, just later
    from vitalocal import config

    monkeypatch.setenv("VITALOCAL_GB_WINDOW_HOURS", "12")
    config.get_settings.cache_clear()
    db = GbDatabase(tmp_path / "gb.sqlite")
    db.nap(D - timedelta(days=14, hours=-13), D - timedelta(days=14, hours=-13, minutes=-20))  # old nap

    # S1: a morning
    _minutes(db, D.replace(hour=6), D.replace(hour=10))
    db.metric(D.replace(hour=6, minute=30), 8, 55, 3)  # training readiness (day value)
    db.metric(D.replace(hour=6, minute=30), 23, 600)  # recovery time
    db.event(D.replace(hour=7), 54, 0, 1)
    db.activity(1, D.replace(hour=8), D.replace(hour=8, minute=45), "Strength", '{"averageHR": 120}')
    s1 = _import(engine, store, db, tmp_path, D.replace(hour=10), "s1")
    assert _mode(s1) == "full"

    # S2: twelve minutes later; the boundary minute was rewritten by the next sync
    _minutes(db, D.replace(hour=9, minute=59), D.replace(hour=10, minute=12), hr_shift=3)
    s2 = _import(engine, store, db, tmp_path, D.replace(hour=10, minute=12), "s2")
    assert _mode(s2) == "incremental"

    # S3: the next morning; the night is written now, stamped hours back; a day value revised
    _minutes(db, D.replace(hour=10, minute=12), D + timedelta(days=1, hours=5, minutes=30))
    t = D.replace(hour=21)
    for i, code in enumerate([2, 3, 2, 4, 1, 2, 3, 4, 2, 4, 1, 2, 3, 4, 1]):
        db.value("GARMIN_SLEEP_STAGE_SAMPLE", t + timedelta(minutes=30 * (i + 1)), code)
    db.value("GARMIN_SLEEP_STATS_SAMPLE", D + timedelta(days=1, hours=4, minutes=30), 78)
    db.event(D.replace(hour=21), 74, 0, 0)  # sleep start
    db.event(D + timedelta(days=1, hours=4, minutes=30), 74, 1, 0)  # sleep end
    db.metric(D.replace(hour=6, minute=30), 8, 57, 3)  # readiness of yesterday revised
    s3 = _import(engine, store, db, tmp_path, D + timedelta(days=1, hours=5, minutes=30), "s3")
    assert _mode(s3) == "incremental"

    # S4: a late backlog 9 h back, a deleted row, a counter that drops (device restart), an
    # activity summary rewritten
    _minutes(db, D + timedelta(days=1, hours=5, minutes=30), D + timedelta(days=1, hours=6))
    for k in range(10):
        db.value("GARMIN_STRESS_SAMPLE", D.replace(hour=20) + timedelta(minutes=1 + 3 * k), 10 + k)
    db.delete("GARMIN_STRESS_SAMPLE", D + timedelta(days=1, hours=3, minutes=1))  # a stress minute
    db.minute(D + timedelta(days=1, hours=5, minutes=45), hr=70, steps=5, dist_cm=10, kcal=1)
    db.activity(1, D.replace(hour=8), D.replace(hour=8, minute=45), "Strength", '{"averageHR": 121}')
    s4 = _import(engine, store, db, tmp_path, D + timedelta(days=1, hours=6), "s4")
    assert _mode(s4) == "incremental"
    assert s4["gb_sqlite_parse_run"]["samples"]["retracted"] >= 1
    tables = _batch(engine, s4["batch_id"]).stats["gb_sqlite_parse_run"]
    window_from = datetime.fromisoformat(
        _scalar(
            engine,
            "SELECT stats->'mode'->'from'->>'GARMIN_ACTIVITY_SAMPLE' FROM parse_run WHERE id = :r",
            r=tables["parse_run_id"],
        )
    )
    assert window_from > D.replace(hour=12)  # really reads from mid-history

    # S5: after two days without the watch; a new nap while the nap table's mark is two weeks old
    _minutes(db, D + timedelta(days=4, hours=8), D + timedelta(days=4, hours=10))
    db.nap(D + timedelta(days=4, hours=12), D + timedelta(days=4, hours=12, minutes=20))
    s5 = _import(engine, store, db, tmp_path, D + timedelta(days=4, hours=13), "s5")
    assert _mode(s5) == "incremental"
    incremental = _dump(engine)

    _wipe_parsed(engine)
    service.process_batch(engine, store, s5["batch_id"], force=True)
    assert _mode(_batch(engine, s5["batch_id"]).stats) == "full"
    full = _dump(engine)
    assert incremental[0] == full[0]
    assert incremental[1] == full[1]
    assert len(full[0]) > 1500


def test_old_changes_wait_for_the_nightly_full_parse(engine, store, tmp_path):
    db = GbDatabase(tmp_path / "gb.sqlite")
    _minutes(db, D.replace(hour=6), D.replace(hour=8))
    _import(engine, store, db, tmp_path, D.replace(hour=8), "a")
    _minutes(db, D + timedelta(days=5, hours=6), D + timedelta(days=5, hours=8))
    _import(engine, store, db, tmp_path, D + timedelta(days=5, hours=8), "b")

    # Gadgetbridge rewrites a five-day-old value: outside the 72 h window
    db.value("GARMIN_STRESS_SAMPLE", D.replace(hour=7), 99)
    c = _import(engine, store, db, tmp_path, D + timedelta(days=5, hours=9), "c")
    assert _mode(c) == "incremental"

    def stored():
        with engine.connect() as conn:
            return conn.execute(
                text("SELECT value FROM sample WHERE metric = 'stress' AND ts = :t"), {"t": D.replace(hour=7)}
            ).scalar()

    assert stored() != 99
    result = service.reparse_latest_snapshot(engine, store)
    assert result["status"] == "completed" and result["drift"]["changed"] == 1
    assert stored() == 99
    with engine.connect() as conn:
        state = conn.execute(text("SELECT last_full_at FROM gb_snapshot_state")).scalar()
    assert state is not None


def test_an_older_snapshot_never_overwrites_newer_data(engine, store, tmp_path):
    db = GbDatabase(tmp_path / "gb.sqlite")
    _minutes(db, D.replace(hour=6), D.replace(hour=8))
    old_zip = db.export(tmp_path / "old.zip", D.replace(hour=8))
    db.value("GARMIN_STRESS_SAMPLE", D.replace(hour=7), 77)  # changed later
    _minutes(db, D.replace(hour=8), D.replace(hour=9))
    _import(engine, store, db, tmp_path, D.replace(hour=9), "new")
    before = _dump(engine)

    with open(old_zip, "rb") as f:  # the old export arrives late
        batch_id, _ = service.register_upload(engine, store, f)
    service.process_batch(engine, store, batch_id)
    row = _batch(engine, batch_id)
    assert row.status == "completed"
    assert row.stats["gb_sqlite_parse_run"]["status"] == "skipped"
    assert row.stats["gb_sqlite_parse_run"]["reason"] == "older_than_applied"
    assert _dump(engine) == before


def test_queue_coalesces_live_snapshots_and_recovers(engine, store, tmp_path):
    # three queued live snapshots: only the newest is processed, the others are superseded
    ids = []
    with engine.begin() as conn:
        for i in range(3):
            sha = conn.execute(
                text("""INSERT INTO raw_artifact (sha256, size, storage_path) VALUES (:s, 1, 'x')
                        RETURNING sha256"""),
                {"s": str(i) * 64},
            ).scalar()
            ids.append(
                conn.execute(
                    text("""INSERT INTO ingest_batch (source, outer_artifact_sha256, status)
                            VALUES ('gadgetbridge_db', :s, 'pending') RETURNING id"""),
                    {"s": sha},
                ).scalar()
            )
    assert worker.claim_next(engine) == ids[2]
    rows = dict(_q(engine, "SELECT id, status FROM ingest_batch WHERE id = ANY(:i)", i=ids).all())
    assert rows[ids[0]] == rows[ids[1]] == "superseded" and rows[ids[2]] == "pending"

    # a crash left a batch processing and its parse run running
    with engine.begin() as conn:
        conn.execute(text("UPDATE ingest_batch SET status = 'processing' WHERE id = :i"), {"i": ids[2]})
        conn.execute(
            text("""INSERT INTO parse_run (batch_id, artifact_sha256, parser_name, parser_version, status)
                    VALUES (:b, :s, 'x', '1', 'running')"""),
            {"b": ids[2], "s": sha},
        )
    assert worker.recover(engine) == 1
    assert _scalar(engine, "SELECT status FROM ingest_batch WHERE id = :i", i=ids[2]) == "pending"
    assert _scalar(engine, "SELECT status FROM parse_run WHERE batch_id = :i", i=ids[2]) == "failed"


def test_worker_thread_processes_uploads(engine, store, tmp_path):
    data = make_export_zip(tmp_path / "export.zip", tmp_path).read_bytes()
    w = worker.IngestWorker(engine, store)
    w.start()
    try:
        batch_id, created = service.register_upload(engine, store, io.BytesIO(data))
        assert created
        w.kick()
        deadline = time.monotonic() + 30
        status = None
        while time.monotonic() < deadline:
            with engine.connect() as conn:
                status = conn.execute(
                    text("SELECT status FROM ingest_batch WHERE id = :i"), {"i": batch_id}
                ).scalar()
            if status in ("completed", "completed_with_errors", "failed"):
                break
            time.sleep(0.2)
        assert status == "completed"
        assert w.alive and w.owner
    finally:
        w.stop()
    assert not w.alive


def test_status_reprocess_and_paging(engine, store, tmp_path):
    from vitalocal.main import app

    client = TestClient(app)  # no lifespan: imports run as background tasks
    empty = client.get("/api/v1/ingest/status", headers=AUTH).json()
    assert empty["queue"] == {"pending": 0, "processing": 0} and empty["gb_db"] is None

    data = make_export_zip(tmp_path / "export.zip", tmp_path).read_bytes()
    r = client.post("/api/v1/ingest/upload", headers=AUTH, files={"file": ("export.zip", data)})
    batch_id = r.json()["batch_id"]
    st = client.get("/api/v1/ingest/status", headers=AUTH).json()
    assert st["gb_db"]["applied_batch_id"] == batch_id and st["gb_db"]["last_full_at"]
    as_of = datetime.fromisoformat(st["sources"]["gadgetbridge_export"]["as_of"])
    assert as_of == datetime(2026, 9, 27, 22, 12, 25, tzinfo=UTC)  # the export time from the ZIP
    assert st["sources"]["gadgetbridge_export"]["processing_s"] >= 0
    assert st["data_version"] != empty["data_version"]

    r = client.post(f"/api/v1/ingest/batches/{batch_id}/reprocess", headers=AUTH)
    assert r.status_code == 202
    batch = client.get(f"/api/v1/ingest/batches/{batch_id}", headers=AUTH).json()
    assert batch["status"] == "completed"
    fit_runs = [p for p in batch["parse_runs"] if p["parser_name"] == "garmin-fit"]
    assert len(fit_runs) == 6  # parsed again on reprocess

    page = client.get("/api/v1/ingest/batches", headers=AUTH, params={"limit": 1}).json()
    assert [b["id"] for b in page] == [batch_id] and "as_of" in page[0]
    assert client.get("/api/v1/ingest/batches", headers=AUTH, params={"before_id": batch_id}).json() == []
    assert client.get("/health").json().get("ingest_worker") is None  # no worker in this test
