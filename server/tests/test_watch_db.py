"""The watch app's day (`/watch/today`) and its writes through the regular endpoints."""

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}
DAY = "2026-09-21"


@pytest.fixture
def client(engine):
    from vitalocal.main import app

    return TestClient(app)


def _post(client, url, body):
    r = client.post(url, headers=AUTH, json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_watch_day_and_writes(client):
    empty = client.get("/api/v1/watch/today", headers=AUTH, params={"day": DAY}).json()
    assert empty["drinks"] == {
        "ml": 0, "goal_ml": 2000, "caffeine_n": 0, "last_caffeine": None,
        "caffeine_limit": None, "entries": [],
    }  # fmt: skip
    assert empty["weight"] is None and empty["supplements"] == [] and empty["habits"] == []

    sup = _post(
        client, "/api/v1/supplements", {"name": "Magnesium", "per_day": 2, "timing": "Mittags & Abends"}
    )
    check = _post(client, "/api/v1/habits", {"name": "Kalt duschen"})
    count = _post(
        client, "/api/v1/habits", {"name": "Lesen", "kind": "count", "target": 10, "unit": "Seiten"}
    )
    _post(client, "/api/v1/habits", {"name": "Schritte", "metric": "steps", "min_value": 5000})
    cutoff = {"name": "Kein Koffein nach 14 Uhr", "metric": "drinks.last_caffeine", "max_value": 840}
    _post(client, "/api/v1/habits", cutoff)

    # the watch generates ids as 32 hex digits without dashes; a replay is a no-op
    drink = {
        "id": uuid.uuid4().hex,
        "ts": f"{DAY}T09:21:00+02:00",
        "drink": "coffee",
        "ml": 250,
        "source": "watch",
    }
    assert client.post("/api/v1/drinks", headers=AUTH, json=drink).status_code == 201
    assert client.post("/api/v1/drinks", headers=AUTH, json=drink).status_code == 200
    _post(
        client,
        "/api/v1/drinks",
        {**drink, "id": uuid.uuid4().hex, "drink": "water", "ml": 500, "ts": f"{DAY}T12:00:00+02:00"},
    )
    weight = {
        "id": uuid.uuid4().hex,
        "ts": f"{DAY}T07:00:00+02:00",
        "metric": "weight",
        "value": 64.34,
        "source": "watch",
    }
    _post(client, "/api/v1/body", weight)
    client.put(f"/api/v1/supplements/{sup['id']}/log/{DAY}", headers=AUTH, json={"count": 1})
    client.put(f"/api/v1/habits/{check['id']}/log/{DAY}", headers=AUTH, json={"value": 1})
    client.put(f"/api/v1/habits/{count['id']}/log/{DAY}", headers=AUTH, json={"value": 4})

    d = client.get("/api/v1/watch/today", headers=AUTH, params={"day": DAY}).json()
    # the goal formula adds to the base for caffeinated drinks
    dr = d["drinks"]
    assert (dr["ml"], dr["goal_ml"], dr["caffeine_n"], dr["last_caffeine"]) == (750, 2300, 1, "09:21")
    assert dr["caffeine_limit"] == "14:00"
    assert [(e["t"], e["drink"], e["ml"]) for e in dr["entries"]] == [
        ("09:21", "coffee", 250),
        ("12:00", "water", 500),
    ]
    assert d["weight"]["kg"] == 64.3 and d["weight"]["days"] == [[0, 64.3]]
    assert d["supplements"] == [
        {
            "id": sup["id"],
            "name": "Magnesium",
            "per_day": 2,
            "count": 1,
            "rest": False,
            "slots": ["noon", "evening"],
        }
    ]
    by = {h["name"]: h for h in d["habits"]}
    assert by["Kalt duschen"] == {
        "id": check["id"],
        "name": "Kalt duschen",
        "done": True,
        "kind": "check",
        "value": 1,
    }
    assert by["Lesen"]["value"] == 4 and by["Lesen"]["target"] == 10 and not by["Lesen"]["done"]
    assert by["Schritte"]["auto"] is True and "value" not in by["Schritte"]
    assert (by["Kein Koffein nach 14 Uhr"]["text"], by["Kein Koffein nach 14 Uhr"]["frac"]) == (
        "09:21 (bis 14:00)",
        1.0,
    )
    assert len(client.get("/api/v1/watch/today", headers=AUTH, params={"day": DAY}).content) < 3000


def test_supplement_slots():
    from vitalocal.api.watch import slots

    assert slots("Morgens") == ["morning"] and slots("Mittags & Abends") == ["noon", "evening"]
    assert slots("vor dem Schlafen") == ["evening"] and slots(None) == slots("zum Essen") == ["other"]
