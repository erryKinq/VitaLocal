"""Supplements: X of Y a day, streaks, ingredient totals against EFSA upper levels, day payloads."""

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.fixtures import make_export_zip
from vitalocal.ingest import service

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(engine, store, tmp_path):
    from vitalocal.main import app

    service.import_file(engine, store, make_export_zip(tmp_path / "export.zip", tmp_path))
    return TestClient(app)


def _new(client, **body) -> dict:
    r = client.post("/api/v1/supplements", headers=AUTH, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _log(client, sid, day, count):
    r = client.put(f"/api/v1/supplements/{sid}/log/{day}", headers=AUTH, json={"count": count})
    assert r.status_code == 200, r.text
    return r.json()


def test_x_of_y_streaks_and_ingredients(client):
    d3 = _new(
        client,
        name="Vitamin D3",
        form="drops",
        per_day=2,
        ingredients=[{"key": "vitamin_d", "amount": 1000}],
    )
    multi = _new(
        client,
        name="Multi",
        brand="X",
        form="tablet",
        ingredients=[{"key": "vitamin_d", "amount": 800}, {"key": "zinc", "amount": 10},
                     {"name": "Ashwagandha", "unit": "mg", "amount": 300}],
    )  # fmt: skip
    assert _log(client, d3["id"], "2026-09-21", 1)["done"] is False  # 1 of 2
    _log(client, d3["id"], "2026-09-21", 2)
    _log(client, d3["id"], "2026-09-20", 2)
    _log(client, multi["id"], "2026-09-21", 3)  # 3 tablets: zinc 30 mg > 25

    st = client.get("/api/v1/supplements/status", headers=AUTH, params={"day": "2026-09-21"}).json()
    by = {s["name"]: s for s in st["supplements"]}
    assert (by["Vitamin D3"]["count"], by["Vitamin D3"]["done"], by["Vitamin D3"]["streak"]) == (2, True, 2)
    assert by["Multi"]["done"] and by["Multi"]["days"]["2026-09-21"] == {"value": 3, "done": True}
    ing = {i["label"]: i for i in st["ingredients"]}
    assert ing["Vitamin D"]["amount"] == 2 * 1000 + 3 * 800 and ing["Vitamin D"]["unit"] == "IE"
    assert ing["Vitamin D"]["upper_level"] == 4000 and ing["Vitamin D"]["above_upper_level"] is True
    assert ing["Vitamin D"]["from"] == ["Vitamin D3", "Multi"]
    assert (ing["Zink"]["amount"], ing["Zink"]["share_of_upper_level"]) == (30, 1.2)
    assert ing["Ashwagandha"]["upper_level"] is None and ing["Ashwagandha"]["amount"] == 900

    # 0 removes the day's entry; a day before is unaffected
    _log(client, d3["id"], "2026-09-21", 0)
    st = client.get("/api/v1/supplements/status", headers=AUTH, params={"day": "2026-09-21"}).json()
    d = next(s for s in st["supplements"] if s["name"] == "Vitamin D3")
    assert d["count"] == 0 and "2026-09-21" not in d["days"] and d["streak"] == 1  # yesterday's run

    # compact in /today and /days, in the AI facts
    today = client.get("/api/v1/today", headers=AUTH, params={"day": "2026-09-21"}).json()["supplements"]
    assert {s["name"]: (s["count"], s["per_day"]) for s in today["items"]} == {
        "Vitamin D3": (0, 2),
        "Multi": (3, 1),
    }
    assert "days" not in today["items"][0] and "from" not in today["ingredients"][0]
    assert client.get("/api/v1/days/2026-09-21", headers=AUTH).json()["supplements"] == today

    from vitalocal.ai_facts import day_facts
    from vitalocal.db import get_engine

    with get_engine().connect() as conn:
        facts = day_facts(conn, __import__("datetime").date(2026, 9, 21))["supplements"]
    assert {"name": "Multi (X)", "units": "3 of 1 tablet", "days_complete_last_30": 1} in facts["taken"]
    zinc = next(i for i in facts["ingredients_today"] if i["ingredient"] == "Zink")
    assert zinc == {
        "ingredient": "Zink",
        "amount": "30 mg",
        "efsa_upper_level": "25 mg",
        "above_upper_level": True,
    }


def test_validation_archive_and_ids(client):
    bad = [
        {"name": "x", "ingredients": [{"key": "unobtainium", "amount": 1}]},
        {"name": "x", "ingredients": [{"name": "Free", "amount": 1}]},  # no unit
        {"name": "x", "ingredients": [{"key": "zinc", "name": "Zink", "amount": 1}]},
        {"name": "x", "per_day": 0},
        {"name": "", "per_day": 1},
    ]
    for body in bad:
        assert client.post("/api/v1/supplements", headers=AUTH, json=body).status_code == 422, body
    sid = str(uuid.uuid4())
    a = _new(client, id=sid, name="Magnesium", ingredients=[{"key": "magnesium", "amount": 200}])
    assert (
        client.post("/api/v1/supplements", headers=AUTH, json={"id": sid, "name": "Magnesium"}).json()["id"]
        == sid
    )
    client.put(f"/api/v1/supplements/{sid}", headers=AUTH, json={**a, "archived": True})
    assert client.get("/api/v1/supplements", headers=AUTH).json() == []
    assert len(client.get("/api/v1/supplements", headers=AUTH, params={"include_archived": True}).json()) == 1
    assert (
        client.put(
            f"/api/v1/supplements/{uuid.uuid4()}/log/2026-09-21", headers=AUTH, json={"count": 1}
        ).status_code
        == 404
    )
    cat = {c["key"]: c for c in client.get("/api/v1/supplements/ingredients", headers=AUTH).json()}
    assert cat["vitamin_d"]["unit"] == "IE" and cat["magnesium"]["upper_level"] == 250
    assert client.delete(f"/api/v1/supplements/{sid}", headers=AUTH).status_code == 204
