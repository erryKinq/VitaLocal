"""Set corrections, check-ins/journal and the Waistline upload through the API."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.fixtures import make_export_zip, waistline_export
from vitalocal.ingest import service

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(engine, store, tmp_path):
    from vitalocal.main import app

    service.import_file(engine, store, make_export_zip(tmp_path / "export.zip", tmp_path))
    return TestClient(app)


def _strength(client):
    acts = client.get("/api/v1/activities", headers=AUTH).json()
    return next(a for a in acts if a["sub_sport"] == "strength_training")


def test_set_corrections_layer_over_watch_values(client, engine, store):
    act = _strength(client)
    url = f"/api/v1/activities/{act['id']}/sets"
    # set 2 is the unrecognized one in the fixture
    r = client.put(
        f"{url}/2",
        headers=AUTH,
        json={"exercise_category": "pull_up", "exercise_name": "close_grip_chin_up", "repetitions": 7},
    )
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/v1/activities/{act['id']}", headers=AUTH).json()
    s2 = next(s for s in detail["sets"] if s["set_index"] == 2)
    assert (s2["exercise_category"], s2["exercise_name"], s2["repetitions"], s2["weight_kg"]) == (
        "pull_up",
        "close_grip_chin_up",
        7,
        23.0,
    )  # weight not corrected -> watch value
    assert s2["edited"] and s2["original"]["exercise_category"] is None
    assert s2["original"]["repetitions"] == 9

    # corrections survive a full re-parse (sets are replaced wholesale)
    with engine.connect() as conn:
        batch_id = conn.execute(text("SELECT max(id) FROM ingest_batch")).scalar_one()
    service.process_batch(engine, store, batch_id)
    detail = client.get(f"/api/v1/activities/{act['id']}", headers=AUTH).json()
    assert next(s for s in detail["sets"] if s["set_index"] == 2)["exercise_category"] == "pull_up"

    # the FIT-vs-Gadgetbridge comparison keeps using the original values
    cmp = client.get("/api/v1/quality/activity-sets", headers=AUTH).json()
    assert next(c for c in cmp if c["sub_sport"] == "strength_training")["mismatches"] == []

    # validation
    assert client.put(f"{url}/2", headers=AUTH, json={"exercise_category": "yoga_flow"}).status_code == 422
    assert (
        client.put(
            f"{url}/2",
            headers=AUTH,
            json={"exercise_category": "pull_up", "exercise_name": "barbell_biceps_curl"},
        ).status_code
        == 422
    )
    assert client.put(f"{url}/1", headers=AUTH, json={"repetitions": 3}).status_code == 422  # rest set
    assert client.put(f"{url}/99", headers=AUTH, json={"repetitions": 3}).status_code == 404

    # reset restores the watch values
    assert client.delete(f"{url}/2/edit", headers=AUTH).status_code == 200
    detail = client.get(f"/api/v1/activities/{act['id']}", headers=AUTH).json()
    s2 = next(s for s in detail["sets"] if s["set_index"] == 2)
    assert not s2["edited"] and s2["exercise_category"] is None and s2["repetitions"] == 9

    cats = {c["category"]: c["names"] for c in client.get("/api/v1/exercises", headers=AUTH).json()}
    assert "close_grip_chin_up" in cats["pull_up"] and "unknown" not in cats


def test_checkins_are_idempotent_and_feed_trends(client):
    cid = str(uuid.uuid4())
    body = {"id": cid, "kind": "mood", "value": 4, "ts": "2026-09-21T09:00:00+02:00"}
    first = client.post("/api/v1/checkins", headers=AUTH, json=body)
    replay = client.post("/api/v1/checkins", headers=AUTH, json=body)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert first.json()["id"] == replay.json()["id"] == cid

    bad = [
        {"kind": "mood", "value": 7},
        {"kind": "symptom", "value": 2},  # no label
        {"kind": "water"},  # no value
        {"kind": "note", "value": 1, "note": "x"},
        {"kind": "mood", "value": 3, "ts": "2026-09-21T09:00:00"},  # naive time
    ]
    for b in bad:
        r = client.post("/api/v1/checkins", headers=AUTH, json={"id": str(uuid.uuid4()), **b})
        assert r.status_code == 422, b

    act = _strength(client)
    for b in [
        {"kind": "symptom", "label": "Headache", "value": 2, "ts": "2026-09-21T11:00:00+02:00"},
        {"kind": "rpe", "value": 8, "activity_id": act["id"], "ts": "2026-09-21T17:00:00+02:00"},
    ]:
        assert (
            client.post("/api/v1/checkins", headers=AUTH, json={"id": str(uuid.uuid4()), **b}).status_code
            == 201
        )
    drinks = [
        {"id": str(uuid.uuid4()), "drink": "water", "ml": 500, "ts": "2026-09-21T10:00:00+02:00"},
        {"id": str(uuid.uuid4()), "drink": "tea", "ml": 250, "ts": "2026-09-21T15:00:00+02:00"},
    ]
    for d in drinks:
        assert client.post("/api/v1/drinks", headers=AUTH, json=d).status_code == 201
    assert client.post("/api/v1/drinks", headers=AUTH, json=drinks[0]).status_code == 200
    assert client.post("/api/v1/drinks", headers=AUTH, json={**drinks[0], "ml": 0}).status_code == 422
    # legacy clients still send drinks as water check-ins; they land in the drink table
    legacy = str(uuid.uuid4())
    r = client.post(
        "/api/v1/checkins",
        headers=AUTH,
        json={
            "id": legacy,
            "kind": "water",
            "value": 100,
            "label": "coffee",
            "ts": "2026-09-21T16:00:00+02:00",
        },
    )
    assert r.status_code == 201 and r.json()["kind"] == "water"
    day_drinks = client.get("/api/v1/drinks", headers=AUTH, params={"day": "2026-09-21"}).json()
    assert day_drinks["total_ml"] == 850 and day_drinks["by_drink"] == {
        "water": 500,
        "tea": 250,
        "coffee": 100,
    }
    assert client.delete(f"/api/v1/checkins/{legacy}", headers=AUTH).status_code == 204  # legacy delete

    tr = client.get(
        "/api/v1/trends",
        headers=AUTH,
        params={"metrics": "checkin.mood,nutrition.drinks", "from": "2026-09-21", "to": "2026-09-21"},
    ).json()
    assert tr["metrics"]["checkin.mood"]["days"][0]["value"] == 4
    assert tr["metrics"]["nutrition.drinks"]["days"][0]["value"] == 750
    assert client.get(f"/api/v1/activities/{act['id']}", headers=AUTH).json()["rpe"]["value"] == 8
    assert client.get("/api/v1/symptoms", headers=AUTH).json()[0]["label"] == "Headache"

    assert (
        client.put("/api/v1/journal/2026-09-21", headers=AUTH, json={"text": "Slept well."}).status_code
        == 200
    )
    j = client.get("/api/v1/journal/2026-09-21", headers=AUTH).json()
    assert j["text"] == "Slept well." and len(j["checkins"]) == 3  # drinks are not journal entries
    day = client.get("/api/v1/days/2026-09-21", headers=AUTH).json()
    assert day["journal"]["text"] == "Slept well." and len(day["journal"]["checkins"]) == 3
    assert day["drinks"]["total_ml"] == 750 and "last_heart_rate_at" in day["sync"]
    overview = client.get(
        "/api/v1/journal", headers=AUTH, params={"from": "2026-09-01", "to": "2026-09-30"}
    ).json()
    assert overview[0]["symptoms"] == ["Headache"] and "water_ml" not in overview[0]

    assert client.delete(f"/api/v1/checkins/{cid}", headers=AUTH).status_code == 204
    assert client.put("/api/v1/journal/2026-09-21", headers=AUTH, json={"text": "  "}).json()["text"] == ""


def test_waistline_upload_snapshot_semantics(client):
    data = waistline_export()
    r = client.post(
        "/api/v1/ingest/upload",
        headers=AUTH,
        files={"file": ("waistline_export.json", json.dumps(data).encode(), "application/json")},
    )
    assert r.status_code == 202 and r.json()["source"] == "waistline_export", r.text
    batch = client.get(f"/api/v1/ingest/batches/{r.json()['batch_id']}", headers=AUTH).json()
    assert batch["status"] == "completed", batch
    assert "secret-pw" not in json.dumps(batch)

    day = client.get("/api/v1/days/2026-09-21", headers=AUTH).json()
    n = day["nutrition"]
    assert n["entries"] == 3 and n["totals"]["calories"]["value"] == 810
    assert n["totals"]["fiber"]["missing"] == 1
    assert [m["meal"] for m in n["meals"]] == ["Frühstück", "Abendessen", "Snacks"]  # empty name: default
    assert day["summary"]["weight"]["value"] == 64.6
    assert day["summary"]["nutrition.energy"]["value"] == 810

    # a newer export without the apples replaces the day's entries and retracts totals
    data["diary"][0]["items"].pop(1)
    data["diary"][0]["stats"] = {}
    client.post(
        "/api/v1/ingest/upload",
        headers=AUTH,
        files={"file": ("waistline_export.json", json.dumps(data).encode(), "application/json")},
    )
    day = client.get("/api/v1/days/2026-09-21", headers=AUTH).json()
    assert day["nutrition"]["entries"] == 2 and day["summary"]["nutrition.energy"]["value"] == 650
    assert "weight" not in day["summary"]

    assert (
        client.post(
            "/api/v1/ingest/upload", headers=AUTH, files={"file": ("x.png", b"\x89PNG....", "image/png")}
        ).status_code
        == 415
    )


def test_waistline_diary_csv_upload(client):
    from tests.test_waistline import DIARY_CSV

    r = client.post(
        "/api/v1/ingest/upload", headers=AUTH, files={"file": ("diary_export.csv", DIARY_CSV, "text/csv")}
    )
    assert r.status_code == 202 and r.json()["source"] == "waistline_diary_csv", r.text
    assert (
        client.get(f"/api/v1/ingest/batches/{r.json()['batch_id']}", headers=AUTH).json()["status"]
        == "completed"
    )
    tr = client.get(
        "/api/v1/trends",
        headers=AUTH,
        params={"metrics": "nutrition.energy,weight", "from": "2026-09-21", "to": "2026-09-23"},
    ).json()["metrics"]
    assert [d["day"] for d in tr["nutrition.energy"]["days"]] == ["2026-09-21", "2026-09-23"]
    assert [d["value"] for d in tr["weight"]["days"]] == [64.4, 64.6]
    notes = {m["key"]: m.get("note") for m in client.get("/api/v1/meta", headers=AUTH).json()["metrics"]}
    assert "count as 0" in notes["nutrition.fiber"]
