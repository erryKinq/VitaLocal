import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.fixtures import T0_S, make_export_zip
from vitalocal.ingest import service

pytestmark = pytest.mark.db


def _count(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def test_import_is_complete_and_idempotent(engine, store, tmp_path):
    zip_path = make_export_zip(tmp_path / "export.zip", tmp_path)
    batch_id = service.import_file(engine, store, zip_path)

    with engine.connect() as conn:
        batch = conn.execute(text("SELECT * FROM ingest_batch WHERE id = :id"), {"id": batch_id}).one()
    assert batch.status == "completed", batch.error
    assert batch.client_meta["version"] == "0.94.0"
    write = batch.stats["gb_sqlite_parse_run"]["samples"]
    assert write["inserted"] == write["staged"] > 0

    # every member is preserved byte-for-byte, nested backup members are inventoried
    assert _count(engine, "SELECT count(*) FROM artifact_member WHERE batch_id = :b", b=batch_id) == 9
    assert _count(engine, "SELECT count(*) FROM artifact_member WHERE nested_in IS NOT NULL") == 1
    with engine.connect() as conn:
        shas = conn.execute(text("SELECT sha256 FROM raw_artifact")).scalars().all()
    assert all(store.verify(s) for s in shas)
    assert _count(engine, "SELECT count(*) FROM artifact_member WHERE meta->'fit' ? 'fit_error'") == 1

    # GB summary and FIT session merge into one activity; the orphan FIT becomes a FIT-only one
    assert _count(engine, "SELECT count(*) FROM activity") == 2
    assert (
        _count(
            engine,
            """SELECT count(*) FROM activity WHERE gb_parse_run_id IS NOT NULL
                             AND fit_parse_run_id IS NOT NULL AND sub_sport = 'strength_training'""",
        )
        == 1
    )
    assert _count(engine, "SELECT count(*) FROM activity WHERE gb_parse_run_id IS NULL") == 1
    assert _count(engine, "SELECT count(*) FROM activity_set") == 5
    assert _count(engine, "SELECT count(*) FROM sample WHERE metric LIKE 'daily.skin_temp%'") == 2
    fit = batch.stats["fit_parse"]
    assert fit["files"] == 3 and fit["failed"] == []

    # a row an older run produced but the parser no longer emits (inside the covered range)
    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO sample (ts, device_id, metric, value, unit, parse_run_id)
            SELECT to_timestamp(:t), device_id, 'steps', 999, 'count', parse_run_id FROM sample
            WHERE metric = 'steps' LIMIT 1"""),
            {"t": T0_S},  # before the first new steps value (first counter reading)
        )

    # second run over the same raw bytes changes nothing, but supersedes the stale row
    service.process_batch(engine, store, batch_id)
    with engine.connect() as conn:
        stats = conn.execute(
            text("SELECT stats FROM ingest_batch WHERE id = :id"), {"id": batch_id}
        ).scalar_one()
    again = stats["gb_sqlite_parse_run"]["samples"]
    again_fit = stats["fit_parse"]
    assert again["inserted"] == 0 and again["changed"] == 0
    assert again["unchanged"] == write["staged"]
    assert again["retracted"] == 1
    assert _count(engine, "SELECT count(*) FROM activity") == 2
    assert _count(engine, "SELECT count(*) FROM activity_set") == 5
    # 1 gadgetbridge-sqlite + 3 garmin-fit, then 1 gadgetbridge-sqlite: the FIT files are known
    assert again_fit["files"] == 0 and again_fit["skipped_known"] == 3
    assert _count(engine, "SELECT count(*) FROM parse_run WHERE batch_id = :b", b=batch_id) == 5
    # the second parse of the same snapshot reads it incrementally, with the same result
    assert stats["gb_sqlite_parse_run"]["mode"] == "incremental"

    # a forced reprocess parses the FIT files again and the database in full
    service.process_batch(engine, store, batch_id, force=True)
    with engine.connect() as conn:
        forced = conn.execute(
            text("SELECT stats FROM ingest_batch WHERE id = :id"), {"id": batch_id}
        ).scalar_one()
    assert forced["fit_parse"]["files"] == 3 and forced["gb_sqlite_parse_run"]["mode"] == "full"


def test_api_flow(engine, tmp_path):
    from vitalocal.main import app

    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/api/v1/ingest/batches").status_code == 401

    auth = {"Authorization": "Bearer test-token"}
    zip_bytes = make_export_zip(tmp_path / "export.zip", tmp_path).read_bytes()
    r = client.post(
        "/api/v1/ingest/gadgetbridge",
        headers=auth,
        files={"file": ("export.zip", zip_bytes, "application/zip")},
    )
    assert r.status_code == 202, r.text
    batch_id = r.json()["batch_id"]  # TestClient runs background tasks before returning

    r2 = client.post(
        "/api/v1/ingest/gadgetbridge",
        headers=auth,
        files={"file": ("again.zip", zip_bytes, "application/zip")},
    )
    assert r2.status_code == 200 and r2.json() == {
        "batch_id": batch_id,
        "created": False,
        "source": "gadgetbridge_export",
    }

    assert client.get(f"/api/v1/ingest/batches/{batch_id}", headers=auth).json()["status"] == "completed"

    rec = client.get("/api/v1/quality/reconciliation", headers=auth).json()
    acts = rec["activities"]
    assert acts["activity_fit_files"] == 2 and acts["db_summaries_linked_to_fit"] == 1
    assert [f["archive_path"].rsplit("/", 1)[1] for f in acts["fit_without_db_summary"]] == [
        "ACTIVITY_2026-09-20_08-00-00.fit"
    ]
    assert "GENERIC_WEIGHT_SAMPLE" in rec["gadgetbridge_db"]["empty_tables"]

    metrics = {m["metric"] for m in client.get("/api/v1/metrics", headers=auth).json()}
    assert {"heart_rate", "stress", "steps"} <= metrics

    q = {"metric": "heart_rate", "from": "2026-09-21T00:00:00Z", "to": "2026-09-22T00:00:00Z"}
    raw = client.get("/api/v1/samples", headers=auth, params=q).json()
    assert [d["value"] for d in raw["data"]] == [70, 65, 66]
    agg = client.get("/api/v1/samples", headers=auth, params={**q, "bucket": "1h"}).json()
    assert agg["data"][0]["n"] == 3 and agg["data"][0]["avg"] == 67

    acts = client.get("/api/v1/activities", headers=auth, params={"sport": "training"}).json()
    assert len(acts) == 2
    merged = next(a for a in acts if a["from_gadgetbridge_db"])
    assert merged["from_fit"] and merged["sub_sport"] == "strength_training"
    assert merged["active_sets"] == 3
    fit_only = next(a for a in acts if not a["from_gadgetbridge_db"])
    assert fit_only["from_fit"] and fit_only["sub_sport"] == "pilates"
    detail = client.get(f"/api/v1/activities/{merged['id']}", headers=auth).json()
    assert [s["exercise_category"] for s in detail["sets"] if s["set_type"] == "active"] == [
        "cardio",
        None,
        "bench_press",
    ]
    assert client.get("/api/v1/activities/999999", headers=auth).status_code == 404

    meta = client.get("/api/v1/meta", headers=auth).json()
    assert meta["last_day"] == "2026-09-21" and meta["timezone"] == "Europe/Berlin"
    assert "steps" in {m["key"] for m in meta["metrics"]}

    day = client.get("/api/v1/days/2026-09-21", headers=auth).json()
    assert day["summary"]["steps"]["value"] == 12  # sum of counter increases
    assert day["summary"]["heart_rate"]["value"] == 67  # mean of 70, 65, 66
    assert day["coverage"]["heart_rate_minutes"] == 3
    assert [a["sub_sport"] for a in day["activities"]] == ["strength_training"]
    assert "daily.skin_temp_nightly" in day["summary"]

    tr = client.get(
        "/api/v1/trends",
        headers=auth,
        params={"metrics": "steps,stress", "from": "2026-09-19", "to": "2026-09-21"},
    ).json()
    assert tr["metrics"]["steps"]["days"] == [{"day": "2026-09-21", "value": 12, "n": 2}]
    assert (
        client.get(
            "/api/v1/trends",
            headers=auth,
            params={"metrics": "steps", "from": "2026-09-21", "to": "2026-09-01"},
        ).status_code
        == 422
    )

    page = client.get("/")
    assert page.status_code == 200 and "VitaLocal" in page.text
    assert client.get("/js/app.js").status_code == 200

    cmp = client.get("/api/v1/quality/activity-sets", headers=auth).json()
    strength = next(c for c in cmp if c["sub_sport"] == "strength_training")
    assert strength["fit_active_sets"] == strength["gadgetbridge_set_rows"] == 3
    assert strength["mismatches"] == [] and strength["fit_sets_without_category"] == 1

    cov = client.get(
        "/api/v1/quality/coverage",
        headers=auth,
        params={"from": q["from"], "to": q["to"], "metric": "heart_rate"},
    ).json()
    assert cov[0]["samples"] == 3 and cov[0]["max_gap_minutes"] == 2
