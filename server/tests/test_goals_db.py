"""Nutrition goals from Waistline (versioned), goals set through the API and the drink goal formula."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tests.fixtures import make_export_zip, waistline_export
from vitalocal.api.goals import sync_goals
from vitalocal.ingest import service

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(engine, store, tmp_path):
    from vitalocal.main import app

    service.import_file(engine, store, make_export_zip(tmp_path / "export.zip", tmp_path))
    c = TestClient(app)
    r = c.post(
        "/api/v1/ingest/upload",
        headers=AUTH,
        files={
            "file": ("waistline_export.json", json.dumps(waistline_export()).encode(), "application/json")
        },
    )
    assert r.status_code == 202, r.text
    return c


def _goals(client, day: str) -> dict:
    r = client.get("/api/v1/goals", headers=AUTH, params={"day": day})
    assert r.status_code == 200, r.text
    return {g["metric"]: g for g in r.json()["goals"]}


def test_waistline_goals_and_day_status(client):
    g = _goals(client, "2026-09-21")
    assert list(g) == ["nutrition.energy", "nutrition.protein", "nutrition.salt", "nutrition.drinks"]
    energy = g["nutrition.energy"]
    assert (energy["min_value"], energy["max_value"], energy["source"], energy["valid_from"]) == (
        2000, None, "waistline", None,  # the first version applies retroactively
    )  # fmt: skip
    assert (energy["value"], energy["status"]) == (810, "below")
    # no entry declares salt: counts as 0
    assert g["nutrition.salt"]["max_value"] == 6 and g["nutrition.salt"]["value"] == 0
    # fiber is missing for one entry that day; protein is declared everywhere
    assert g["nutrition.protein"]["incomplete"] is False

    drinks = g["nutrition.drinks"]
    f = drinks["formula"]
    assert f["weight_kg"] == 64.6 and f["base_ml"] == round(64.6 * 35) and f["training_min"] > 0
    assert drinks["min_value"] == round((f["base_ml"] + f["training_ml"]) / 100) * 100
    assert drinks["source"] == "formula"

    today = client.get("/api/v1/today", headers=AUTH, params={"day": "2026-09-21"}).json()
    assert today["drinks"]["goal_ml"] == drinks["min_value"] and today["drinks"]["goal"] == f
    assert {x["metric"] for x in today["goals"]} == set(g)
    day = client.get("/api/v1/days/2026-09-21", headers=AUTH).json()
    assert [x["metric"] for x in day["goals"]] == list(g)


def test_entries_win_over_a_stale_daily_total(client, engine):
    # e.g. a diary CSV imported in the morning, before the day's later meals
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(
            text("""UPDATE sample SET value = 1 WHERE metric = 'nutrition.protein'
                    AND ts = '2026-09-21T12:00:00+02:00'""")
        )
    assert _goals(client, "2026-09-21")["nutrition.protein"]["value"] > 1


def test_goal_versions(client, engine):
    # protein raised in Waistline, salt goal removed: both from the 21st on
    with engine.begin() as conn:
        changed = sync_goals(
            conn,
            "waistline",
            {"nutrition.energy": (2000, None), "nutrition.protein": (140, None)},
            date(2026, 9, 21),
        )
        assert changed == ["nutrition.protein", "nutrition.salt"]
        # the same snapshot again changes nothing
        same = {"nutrition.energy": (2000, None), "nutrition.protein": (140, None)}
        assert sync_goals(conn, "waistline", same, date(2026, 9, 22)) == []
    before, after = _goals(client, "2026-09-20"), _goals(client, "2026-09-21")
    assert before["nutrition.protein"]["min_value"] == 128 and "nutrition.salt" in before
    assert after["nutrition.protein"]["min_value"] == 140 and "nutrition.salt" not in after
    assert after["nutrition.protein"]["valid_from"] == "2026-09-21"

    # a range set through the API holds until Waistline really changes
    r = client.put(
        "/api/v1/goals/nutrition.protein",
        headers=AUTH,
        json={"min_value": 120, "max_value": 160, "valid_from": "2026-09-22"},
    )
    assert r.status_code == 200 and r.json()["source"] == "vitalocal"
    with engine.begin() as conn:
        snapshot = {"nutrition.energy": (2000, None), "nutrition.protein": (140, None)}
        assert sync_goals(conn, "waistline", snapshot, date(2026, 9, 23)) == []
    p = _goals(client, "2026-09-23")["nutrition.protein"]
    assert (p["min_value"], p["max_value"], p["source"]) == (120, 160, "vitalocal")

    hist = client.get("/api/v1/goals/history", headers=AUTH, params={"metric": "nutrition.protein"}).json()
    assert [(h["valid_from"], h["min_value"], h["source"]) for h in hist] == [
        (None, 128, "waistline"), ("2026-09-21", 140, "waistline"), ("2026-09-22", 120, "vitalocal"),
    ]  # fmt: skip

    assert client.delete(
        "/api/v1/goals/nutrition.protein", headers=AUTH, params={"valid_from": "2026-09-24"}
    ).status_code == 204  # fmt: skip
    assert "nutrition.protein" not in _goals(client, "2026-09-24")
    assert "nutrition.protein" in _goals(client, "2026-09-23")  # earlier days keep theirs

    bad = client.put("/api/v1/goals/steps", headers=AUTH, json={"min_value": 1})
    assert bad.status_code == 422
    bad = client.put("/api/v1/goals/nutrition.fat", headers=AUTH, json={"min_value": 90, "max_value": 60})
    assert bad.status_code == 422


def test_drink_goal_settings(client):
    s = client.get("/api/v1/settings/drink-goal", headers=AUTH).json()
    assert s == {"enabled": True, "ml_per_kg": 35, "ml_per_training_hour": 500, "fallback_ml": 2000}
    client.put(
        "/api/v1/settings/drink-goal", headers=AUTH, json={**s, "ml_per_kg": 30, "ml_per_training_hour": 0}
    )
    d = _goals(client, "2026-09-21")["nutrition.drinks"]
    assert d["min_value"] == round(64.6 * 30 / 100) * 100 and d["formula"]["training_ml"] == 0
    # before the first weight: the fallback
    assert _goals(client, "2026-09-01")["nutrition.drinks"]["formula"]["base_ml"] == 2000

    client.put("/api/v1/settings/drink-goal", headers=AUTH, json={**s, "enabled": False})
    assert "nutrition.drinks" not in _goals(client, "2026-09-21")
    today = client.get("/api/v1/today", headers=AUTH, params={"day": "2026-09-21"}).json()
    assert today["drinks"]["goal_ml"] is None
    assert client.put("/api/v1/settings/drink-goal", headers=AUTH, json={"ml_per_kg": 500}).status_code == 422
