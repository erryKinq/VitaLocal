"""Server side of the Android companion: upload check, uploader metadata, retention, /today."""

import hashlib
import io
import json
import sqlite3
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.fixtures import make_export_zip
from vitalocal.ingest import retention, service

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}
ORPHAN = "ACTIVITY_2026-09-20_08-00-00.fit"


@pytest.fixture
def client(engine, store):
    from vitalocal.main import app

    return TestClient(app)


def _upload(client, data: bytes, name="export.zip", meta=None):
    extra = {"data": {"client_meta": json.dumps(meta)}} if meta is not None else {}
    return client.post(
        "/api/v1/ingest/upload",
        headers=AUTH,
        files={"file": (name, data, "application/octet-stream")},
        **extra,
    )


def _newer_export(older: bytes, tmp_path) -> bytes:
    """Next day's snapshot: changed DB and preferences, one FIT deleted on the phone."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(older)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        dst.comment = src.comment
        for info in src.infolist():
            data = src.read(info)
            if info.filename.endswith(ORPHAN):
                continue
            if info.filename == "database/Gadgetbridge":
                db = tmp_path / "newer.sqlite"
                db.write_bytes(data)
                with sqlite3.connect(db) as con:
                    con.execute("PRAGMA user_version = 2")
                data = db.read_bytes()
            if info.filename == "preferences/global.json":
                data = b'{"changed": true}'
            dst.writestr(info.filename, data)
    return out.getvalue()


def _members(engine, batch_id) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT archive_path, artifact_sha256 FROM artifact_member WHERE batch_id = :b"),
            {"b": batch_id},
        )
        return {r.archive_path: r.artifact_sha256 for r in rows}


def test_artifact_check_and_client_meta(client, tmp_path):
    data = make_export_zip(tmp_path / "export.zip", tmp_path).read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    assert client.get(f"/api/v1/ingest/artifacts/{sha}", headers=AUTH).status_code == 404
    assert client.get("/api/v1/ingest/artifacts/nothex", headers=AUTH).status_code == 422

    assert _upload(client, data, meta=["not", "an", "object"]).status_code == 422
    assert _upload(client, data, meta={"x": "y" * 5000}).status_code == 422
    r = _upload(client, data, meta={"app": "vitalocal-android", "device": "Pixel 8", "trigger": "auto"})
    assert r.status_code == 202, r.text

    art = client.get(f"/api/v1/ingest/artifacts/{sha.upper()}", headers=AUTH).json()
    assert art["stored"] is True and art["size"] == len(data)
    [batch] = art["batches"]
    assert batch["status"] == "completed"
    # uploader details survive parsing, next to what the parser read from the ZIP comment
    assert batch["client_meta"]["client"]["device"] == "Pixel 8"
    assert batch["client_meta"]["version"] == "0.94.0"


def test_retention_prunes_superseded_snapshots_only(client, engine, store, tmp_path):
    older = make_export_zip(tmp_path / "export.zip", tmp_path).read_bytes()
    first = _upload(client, older).json()["batch_id"]
    old_members = _members(engine, first)

    # Within the window nothing goes, even with a newer export.
    newer = _newer_export(older, tmp_path)
    second = _upload(client, newer).json()["batch_id"]
    assert client.get(f"/api/v1/ingest/batches/{second}", headers=AUTH).json()["status"] == "completed"
    assert client.post("/api/v1/ingest/prune", headers=AUTH).json()["artifacts"] == 0

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE ingest_batch SET received_at = now() - interval '10 days' WHERE id = :b"),
            {"b": first},
        )
    status = client.get("/api/v1/ingest/retention", headers=AUTH).json()
    assert isinstance(status["stored_bytes"], int) and status["pruned_bytes"] == 0
    preview = status["pending"]
    # outer ZIP + old DB; the old preferences file is "{}", same bytes as the kept gadgetbridge.json
    assert preview["dry_run"] and preview["artifacts"] == 2 and preview["batches"] == 1
    result = client.post("/api/v1/ingest/prune", headers=AUTH).json()
    assert result["artifacts"] == 2 and result["batches"] == 1

    outer_old = client.get("/api/v1/ingest/batches", headers=AUTH).json()[-1]["outer_artifact_sha256"]
    new_members = _members(engine, second)
    assert not store.exists(outer_old) and not store.exists(old_members["database/Gadgetbridge"])
    assert store.exists(old_members["preferences/global.json"])  # content still used by the new export
    orphan = next(v for k, v in old_members.items() if k.endswith(ORPHAN))
    assert store.exists(orphan)  # only in the old export: kept
    assert all(store.exists(s) for s in new_members.values())  # the newest export stays complete

    b = client.get(f"/api/v1/ingest/batches/{first}", headers=AUTH).json()
    assert b["raw_pruned_at"] is not None and b["status"] == "completed"
    assert client.post(f"/api/v1/ingest/batches/{first}/reprocess", headers=AUTH).status_code == 410
    assert client.post("/api/v1/ingest/prune", headers=AUTH).json()["artifacts"] == 0

    # Uploading the same bytes again brings the file back.
    again = _upload(client, older)
    assert again.status_code == 200 and again.json()["batch_id"] == first
    assert store.exists(outer_old)
    assert client.get(f"/api/v1/ingest/batches/{first}", headers=AUTH).json()["raw_pruned_at"] is None


def test_retention_keeps_latest_successful_export(engine, store, tmp_path):
    older = make_export_zip(tmp_path / "export.zip", tmp_path).read_bytes()
    first, _ = service.register_upload(engine, store, io.BytesIO(older), service.SOURCE_GADGETBRIDGE)
    service.process_batch(engine, store, first)
    with engine.begin() as conn:
        conn.execute(text("UPDATE ingest_batch SET received_at = now() - interval '30 days'"))
    # A newer upload that failed does not supersede the last good export.
    broken, _ = service.register_upload(
        engine, store, io.BytesIO(b"PK\x03\x04broken"), service.SOURCE_GADGETBRIDGE
    )
    service.process_batch(engine, store, broken)
    assert retention.prune(engine, store, 7)["artifacts"] == 0
    assert retention.prune(engine, store, 0)["skipped"] == "retention disabled"


def test_today_overview(client, engine, store, tmp_path):
    service.import_file(engine, store, make_export_zip(tmp_path / "export.zip", tmp_path))
    day = "2026-09-21"
    for label, ml in (("water", 123), ("tea", 250)):
        r = client.post(
            "/api/v1/checkins",
            headers=AUTH,
            json={
                "id": str(uuid.uuid4()),
                "kind": "water",
                "value": ml,
                "label": label,
                "ts": f"{day}T13:51:00+02:00",
                "source": "android",
            },
        )
        assert r.status_code == 201
    client.post(
        "/api/v1/checkins",
        headers=AUTH,
        json={"id": str(uuid.uuid4()), "kind": "mood", "value": 4, "ts": f"{day}T09:00:00+02:00"},
    )
    hb = client.post("/api/v1/habits", headers=AUTH, json={"name": "Lesen"}).json()
    client.put(f"/api/v1/habits/{hb['id']}/log/{day}", headers=AUTH, json={"value": 1})

    r = client.get("/api/v1/today", headers=AUTH, params={"day": day})
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["day"] == day and t["timezone"] == "Europe/Berlin"
    assert t["drinks"]["total_ml"] == 373 and t["drinks"]["by_drink"] == {"water": 123, "tea": 250}
    assert {(e["drink"], e["ml"], e["label"], e["value"]) for e in t["drinks"]["entries"]} == {
        ("water", 123, "water", 123),
        ("tea", 250, "tea", 250),
    }
    assert [c["kind"] for c in t["checkins"]] == ["mood"]
    assert t["metrics"]["nutrition.drinks"]["value"] == 373
    assert t["metrics"]["steps"]["value"] is not None and t["metrics"]["weight"]["value"] is None
    assert set(t["sleep"]) == {"asleep_minutes", "bed_start", "bed_end", "minutes"}
    [habit] = t["habits"]
    assert habit["today"]["done"] and habit["streak"] == 1
    src = t["sync"]["sources"]["gadgetbridge_export"]
    assert src["status"] == "completed" and src["exported_at"] == "2026-09-27T22:12:25Z"
    assert t["sync"]["last_heart_rate_at"] is not None
    assert "series" not in t and len(r.content) < 20_000
