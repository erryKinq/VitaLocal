"""Live Gadgetbridge databases: whole or as changed blocks, queued, kept only as long as useful."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.gb_snapshots import GbDatabase
from tests.test_dbdelta import vldd
from vitalocal.ingest import retention, service, worker

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}
D = datetime(2026, 9, 21, 6, tzinfo=UTC)
PAGE = 4096


@pytest.fixture
def client(engine, store):
    from vitalocal.main import app

    return TestClient(app)


def _minutes(db: GbDatabase, start: datetime, n: int) -> None:
    for i in range(n):
        t = start + timedelta(minutes=i)
        db.minute(t, hr=60 + i % 20, steps=(t.hour * 60 + t.minute) * 5, dist_cm=i * 100, kcal=i // 4)
        db.value("GARMIN_STRESS_SAMPLE", t, 20 + i % 30)
    db.con.commit()


def _post(tc, data: bytes, meta: dict):
    return tc.post(
        "/api/v1/ingest/gadgetbridge/db",
        headers=AUTH,
        data={"meta": json.dumps(meta)},
        files={"data": ("db.gz", gzip.compress(data), "application/gzip")},
    )


def _full(tc, data: bytes, **extra):
    return _post(
        tc,
        data,
        {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "encoding": "full", **extra},
    )


def _delta(tc, base: bytes, new: bytes, **extra):
    meta = {
        "sha256": hashlib.sha256(new).hexdigest(),
        "size": len(new),
        "encoding": "vldd1",
        "base_sha256": hashlib.sha256(base).hexdigest(),
        **extra,
    }
    return _post(tc, vldd(base, new, PAGE), meta)


def _batch(client, batch_id):
    return client.get(f"/api/v1/ingest/batches/{batch_id}", headers=AUTH).json()


def test_live_database_whole_then_as_delta(client, engine, tmp_path):
    db = GbDatabase(tmp_path / "gb.sqlite")
    _minutes(db, D, 120)
    first = (tmp_path / "gb.sqlite").read_bytes()
    r = _full(
        client,
        first,
        exported_at=D.isoformat(),
        gadgetbridge={"version": "0.94.0"},
        client={"trigger": "live"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["created"] and body["stored"] == "full" and body["source"] == "gadgetbridge_db"
    b = _batch(client, body["batch_id"])
    assert b["status"] == "completed" and b["stats"]["gb_sqlite_parse_run"]["mode"] == "full"
    assert (
        b["client_meta"]["transfer"]["encoding"] == "full" and b["client_meta"]["client"]["trigger"] == "live"
    )
    assert _full(client, first).json() == {
        "batch_id": body["batch_id"],
        "created": False,
        "source": "gadgetbridge_db",
        "sha256": hashlib.sha256(first).hexdigest(),
    }

    _minutes(db, D + timedelta(minutes=120), 12)
    second = (tmp_path / "gb.sqlite").read_bytes()
    payload = vldd(first, second, PAGE)
    assert len(payload) < len(second) / 2  # a few pages, not the database
    r = _delta(client, first, second, exported_at=(D + timedelta(minutes=132)).isoformat())
    assert r.status_code == 202, r.text
    assert r.json()["stored"] == "delta"
    b = _batch(client, r.json()["batch_id"])
    assert b["status"] == "completed" and b["stats"]["gb_sqlite_parse_run"]["mode"] == "incremental"
    with engine.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM sample WHERE metric = 'heart_rate'")).scalar()
    assert n == 132

    st = client.get("/api/v1/ingest/status", headers=AUTH).json()
    assert "gb_db_delta_v1" in st["capabilities"]
    assert st["gb_db"]["applied_sha256"] == hashlib.sha256(second).hexdigest()
    assert st["sources"]["gadgetbridge_db"]["status"] == "completed"


def test_live_database_errors(client, tmp_path, monkeypatch):
    db = GbDatabase(tmp_path / "gb.sqlite")
    _minutes(db, D, 30)
    first = (tmp_path / "gb.sqlite").read_bytes()
    _minutes(db, D + timedelta(minutes=30), 30)
    second = (tmp_path / "gb.sqlite").read_bytes()

    r = _delta(client, first, second)  # the server has no base yet
    assert r.status_code == 409 and r.json()["detail"]["code"] == "base_unavailable"
    assert _full(client, first).status_code == 202
    bad = _post(
        client,
        vldd(first, second, PAGE),
        {"sha256": "0" * 64, "encoding": "vldd1", "base_sha256": hashlib.sha256(first).hexdigest()},
    )
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "sha_mismatch"
    junk = b"x" * 8192
    assert _full(client, junk).json()["detail"]["code"] == "not_sqlite"
    not_gzip = client.post(
        "/api/v1/ingest/gadgetbridge/db",
        headers=AUTH,
        data={"meta": json.dumps({"sha256": "1" * 64, "encoding": "full"})},
        files={"data": ("db", b"plain bytes", "application/octet-stream")},
    )
    assert not_gzip.status_code == 422 and not_gzip.json()["detail"]["code"] == "invalid_delta"
    for meta in (
        {"sha256": "x", "encoding": "full"},
        {"sha256": "1" * 64, "encoding": "zip"},
        {"sha256": "1" * 64, "encoding": "vldd1"},
        {"sha256": "1" * 64, "encoding": "full", "exported_at": "2026-10-03T08:00:00"},
    ):
        assert _post(client, first, meta).status_code == 422, meta

    from vitalocal import config

    monkeypatch.setenv("VITALOCAL_LIVE_DB_MAX_BYTES", "1000")
    config.get_settings.cache_clear()
    assert _full(client, second).status_code == 413
    config.get_settings.cache_clear()


def test_export_time_is_clamped_and_queued_snapshots_coalesce(engine, store, tmp_path):
    db = GbDatabase(tmp_path / "gb.sqlite")
    ids = []
    for k in range(3):
        _minutes(db, D + timedelta(minutes=20 * k), 20)
        data = (tmp_path / "gb.sqlite").read_bytes()
        meta = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "encoding": "full"}
        if k == 2:
            meta["exported_at"] = (datetime.now(UTC) + timedelta(days=3)).isoformat()  # phone clock off
        r = service.register_gb_db(engine, store, meta, io.BytesIO(gzip.compress(data)), max_bytes=1 << 26)
        ids.append(r["batch_id"])
    assert worker.drain(engine, store) == 1  # the newest only
    with engine.connect() as conn:
        rows = {r.id: r for r in conn.execute(text("SELECT id, status, as_of FROM ingest_batch"))}
    assert [rows[i].status for i in ids] == ["superseded", "superseded", "completed"]
    assert rows[ids[2]].as_of <= datetime.now(UTC) + timedelta(minutes=6)


def test_retention_keeps_bases_and_one_snapshot_per_day(engine, store, tmp_path):
    db = GbDatabase(tmp_path / "gb.sqlite")
    batches = []
    for k in range(9):  # three days, three snapshots each
        _minutes(db, D + timedelta(minutes=10 * k), 10)
        data = (tmp_path / "gb.sqlite").read_bytes()
        meta = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "encoding": "full"}
        r = service.register_gb_db(engine, store, meta, io.BytesIO(gzip.compress(data)), max_bytes=1 << 26)
        worker.drain(engine, store, prune=False)
        batches.append((r["batch_id"], meta["sha256"]))
    noon = datetime.now(ZoneInfo("Europe/Berlin")).replace(hour=12, minute=0, second=0, microsecond=0)
    with engine.begin() as conn:
        for k, (batch_id, _sha) in enumerate(batches):
            received = noon - timedelta(days=2 - k // 3, hours=3 - k % 3)
            conn.execute(
                text("UPDATE ingest_batch SET received_at = :r WHERE id = :id"),
                {"r": received, "id": batch_id},
            )
    result = retention.prune(engine, store, 7)
    kept = {sha for _id, sha in batches if store.exists(sha)}
    # newest two (delta bases) + the last of each of the two earlier days; the rest goes
    expected = {batches[i][1] for i in (2, 5, 7, 8)}
    assert kept == expected, result
    assert result["artifacts"] == 5
