"""File sets by manifest: only new bytes are sent, only new files become members and are parsed."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.fixtures import STRENGTH_SETS, T0_S, make_export_zip, make_fit

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(engine, store):
    from vitalocal.main import app

    return TestClient(app)


def _entries(zip_bytes: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        return {
            i.filename: z.read(i)
            for i in z.infolist()
            if not i.is_dir() and i.filename != "database/Gadgetbridge"
        }


def _manifest(files: dict[str, bytes], origin="zip_export") -> dict:
    return {
        "v": 1,
        "origin": origin,
        "exported_at": "2026-09-28T08:00:00Z",
        "entries": [
            {"path": p, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)} for p, b in files.items()
        ],
    }


def _send(client, m: dict, files: dict[str, bytes], only: set[str] | None = None):
    parts = [
        ("files", (hashlib.sha256(b).hexdigest(), b, "application/octet-stream"))
        for p, b in files.items()
        if only is None or hashlib.sha256(b).hexdigest() in only
    ]
    return client.post(
        "/api/v1/ingest/gadgetbridge/files",
        headers=AUTH,
        data={"manifest": json.dumps(m)},
        files=parts or None,
    )


def test_file_set_after_a_zip_adds_only_new_files(client, engine, tmp_path):
    data = make_export_zip(tmp_path / "export.zip", tmp_path).read_bytes()
    assert (
        client.post("/api/v1/ingest/upload", headers=AUTH, files={"file": ("e.zip", data)}).status_code == 202
    )
    files = _entries(data)
    new_fit = make_fit(sport=10, sub_sport=20, sets=STRENGTH_SETS, start_s=T0_S + 7200)
    files["files/AA:BB:CC:DD:EE:01/ACTIVITY/2026/ACTIVITY_new.fit"] = new_fit
    m = _manifest(files)

    shas = [e["sha256"] for e in m["entries"]]
    missing = client.post("/api/v1/ingest/artifacts/missing", headers=AUTH, json={"sha256": shas}).json()[
        "missing"
    ]
    assert missing == [hashlib.sha256(new_fit).hexdigest()]  # everything else came with the ZIP

    r = _send(client, m, files, only=set())  # nothing sent: the new file is missing
    assert r.status_code == 409 and r.json()["detail"]["missing"] == missing
    r = _send(client, m, files, only=set(missing))
    assert r.status_code == 202, r.text
    b = client.get(f"/api/v1/ingest/batches/{r.json()['batch_id']}", headers=AUTH).json()
    assert b["status"] == "completed"
    assert b["stats"]["members"] == {"ACTIVITY": 1}  # only the new file is a member
    assert b["stats"]["fit_parse"]["files"] == 1
    assert _send(client, m, files, only=set()).status_code == 200  # same file set again

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM activity")).scalar() == 3
    rec = client.get("/api/v1/quality/reconciliation", headers=AUTH).json()
    assert rec["activities"]["activity_fit_files"] == 3  # current state over both batches


def test_fit_export_files_and_files_wanted(client, engine, tmp_path):
    data = make_export_zip(tmp_path / "export.zip", tmp_path).read_bytes()
    client.post("/api/v1/ingest/upload", headers=AUTH, files={"file": ("e.zip", data)})
    with engine.begin() as conn:  # a workout the live database knows, without its FIT file yet
        conn.execute(
            text("""INSERT INTO activity (device_id, start_ts, raw_details_name, gb_parse_run_id)
                    SELECT device_id, now() - interval '1 hour', 'ACTIVITY_live.fit', gb_parse_run_id
                    FROM activity WHERE gb_parse_run_id IS NOT NULL LIMIT 1""")
        )
    wanted = client.get("/api/v1/ingest/status", headers=AUTH).json()["files_wanted"]
    assert "files_v1" in client.get("/api/v1/ingest/status", headers=AUTH).json()["capabilities"]
    assert any(r["kind"] == "activity_fit" and r["name"] == "ACTIVITY_live.fit" for r in wanted["reasons"])

    fit = make_fit(sport=10, sub_sport=20, sets=STRENGTH_SETS, start_s=T0_S + 3 * 86400)
    files = {"fit_export/2026-09-24 Krafttraining.fit": fit, "fit_export/notes.txt": b"x"}
    r = _send(client, _manifest(files, origin="fit_export"), files)
    assert r.status_code == 202, r.text
    b = client.get(f"/api/v1/ingest/batches/{r.json()['batch_id']}", headers=AUTH).json()
    assert b["stats"]["members"] == {"ACTIVITY": 1, "fit_export": 1}
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT device_id, sub_sport FROM activity WHERE raw_fit_sha256 = :s"),
            {"s": hashlib.sha256(fit).hexdigest()},
        ).one()
    assert row.sub_sport == "strength_training" and row.device_id is not None  # the watch's device


def test_manifest_validation(client):
    good = {"path": "files/x.fit", "sha256": "a" * 64, "size": 1}
    for m in (
        {"v": 2, "entries": [good]},
        {"v": 1, "entries": []},
        {"v": 1, "entries": [{**good, "path": "../etc/passwd"}]},
        {"v": 1, "entries": [{**good, "path": "/abs"}]},
        {"v": 1, "entries": [good, good]},
        {"v": 1, "entries": [{**good, "sha256": "xyz"}]},
    ):
        r = client.post("/api/v1/ingest/gadgetbridge/files", headers=AUTH, data={"manifest": json.dumps(m)})
        assert r.status_code == 422, m
    f = b"content"
    m = {
        "v": 1,
        "entries": [{"path": "files/a.bin", "sha256": hashlib.sha256(b"other").hexdigest(), "size": 7}],
    }
    r = client.post(
        "/api/v1/ingest/gadgetbridge/files",
        headers=AUTH,
        data={"manifest": json.dumps(m)},
        files=[("files", (hashlib.sha256(b"other").hexdigest(), f, "application/octet-stream"))],
    )
    assert r.status_code == 422
