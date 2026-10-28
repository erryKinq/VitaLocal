"""Habits (manual + metric-derived) and AI summaries against a fake OpenAI-compatible server."""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.fixtures import make_export_zip
from vitalocal import ai
from vitalocal.ingest import service

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(engine, store, tmp_path):
    from vitalocal.main import app

    service.import_file(engine, store, make_export_zip(tmp_path / "export.zip", tmp_path))
    return TestClient(app)


def test_manual_habits_and_streaks(client):
    daily = client.post("/api/v1/habits", headers=AUTH, json={"name": "Stretching"}).json()
    weekly = client.post("/api/v1/habits", headers=AUTH, json={"name": "Sauna", "per_week": 2}).json()
    count = client.post(
        "/api/v1/habits",
        headers=AUTH,
        json={"name": "Pages read", "kind": "count", "unit": "pages", "target": 20},
    ).json()
    for d in ("2026-09-19", "2026-09-20", "2026-09-21"):
        assert (
            client.put(f"/api/v1/habits/{daily['id']}/log/{d}", headers=AUTH, json={"value": 1}).status_code
            == 200
        )
    for d in ("2026-09-15", "2026-09-17", "2026-09-10", "2026-09-12"):  # two full weeks with 2×
        client.put(f"/api/v1/habits/{weekly['id']}/log/{d}", headers=AUTH, json={"value": 1})
    client.put(f"/api/v1/habits/{count['id']}/log/2026-09-21", headers=AUTH, json={"value": 12})

    st = {
        h["name"]: h
        for h in client.get(
            "/api/v1/habits/status", headers=AUTH, params={"from": "2026-09-01", "to": "2026-09-21"}
        ).json()
    }
    assert st["Stretching"]["streak"] == 3 and st["Stretching"]["streak_unit"] == "days"
    # current week (from Mon 21 Sep) has 0 of 2 yet -> counted from last week back
    assert st["Sauna"]["streak"] == 2 and st["Sauna"]["streak_unit"] == "weeks"
    assert st["Pages read"]["days"]["2026-09-21"] == {"value": 12, "done": False}  # partial, below target

    # unchecking removes the log; invalid definitions are rejected
    client.put(f"/api/v1/habits/{daily['id']}/log/2026-09-21", headers=AUTH, json={"value": 0})
    st = {
        h["name"]: h
        for h in client.get(
            "/api/v1/habits/status", headers=AUTH, params={"from": "2026-09-01", "to": "2026-09-21"}
        ).json()
    }
    assert "2026-09-21" not in st["Stretching"]["days"] and st["Stretching"]["streak"] == 2
    assert client.post("/api/v1/habits", headers=AUTH, json={"name": "x", "kind": "count"}).status_code == 422
    assert (
        client.post("/api/v1/habits", headers=AUTH, json={"name": "x", "metric": "steps"}).status_code == 422
    )  # incomplete auto rule

    # listed by type (automatic, tick off, count), then by name
    client.post("/api/v1/habits", headers=AUTH, json={"name": "steps", "metric": "steps", "min_value": 1})
    client.post("/api/v1/habits", headers=AUTH, json={"name": "Abend-Spaziergang"})
    names = [h["name"] for h in client.get("/api/v1/habits", headers=AUTH).json()]
    assert names == ["steps", "Abend-Spaziergang", "Sauna", "Stretching", "Pages read"]

    # archive hides it from the active list
    client.put(f"/api/v1/habits/{weekly['id']}", headers=AUTH, json={**weekly, "archived": True})
    assert "Sauna" not in {h["name"] for h in client.get("/api/v1/habits", headers=AUTH).json()}


def test_metric_habit_is_unknown_without_data(client):
    h = client.post(
        "/api/v1/habits",
        headers=AUTH,
        json={"name": "10 steps", "metric": "steps", "comparator": ">=", "threshold": 10},
    ).json()
    st = client.get(
        "/api/v1/habits/status", headers=AUTH, params={"from": "2026-09-20", "to": "2026-09-21"}
    ).json()
    days = next(x for x in st if x["id"] == h["id"])["days"]
    assert days["2026-09-21"]["done"] and days["2026-09-21"]["auto"]  # 12 steps in the fixture
    assert "2026-09-20" not in days  # no step data that day: unknown, not failed
    assert (
        client.put(f"/api/v1/habits/{h['id']}/log/2026-09-21", headers=AUTH, json={"value": 1}).status_code
        == 422
    )


@pytest.fixture
def fake_model(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen-test"}, {"id": "gemma-test"}]})
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [
                    {"message": {"content": "<think>internal</think>Ein ruhiger Tag mit 12 Schritten."}}
                ],
            },
        )

    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(handler))
    return calls


def test_ai_settings_and_day_summary(client, fake_model):
    r = client.post("/api/v1/ai/summary", headers=AUTH, json={"day": "2026-09-21"})
    assert r.status_code == 502 and "no AI model" in r.json()["detail"]

    models = client.post("/api/v1/ai/models", headers=AUTH, json={"base_url": "http://model.test/v1"}).json()
    assert models == {"ok": True, "models": ["gemma-test", "qwen-test"]}

    s = client.put(
        "/api/v1/settings/ai",
        headers=AUTH,
        json={
            "enabled": True,
            "base_url": "http://model.test/v1",
            "model": "qwen-test",
            "api_key": "sk-local",
        },
    ).json()
    assert s["has_api_key"] and "api_key" not in s
    # null keeps the key, the key is never returned
    s = client.put(
        "/api/v1/settings/ai",
        headers=AUTH,
        json={"enabled": True, "base_url": "http://model.test/v1", "model": "qwen-test", "api_key": None},
    ).json()
    assert s["has_api_key"]
    assert client.put("/api/v1/settings/ai", headers=AUTH, json={"base_url": "ftp://x"}).status_code == 422

    client.put("/api/v1/journal/2026-09-21", headers=AUTH, json={"text": "Guter Tag."})
    out = client.post("/api/v1/ai/summary", headers=AUTH, json={"day": "2026-09-21", "lang": "de"}).json()
    assert out["text"] == "Ein ruhiger Tag mit 12 Schritten."  # reasoning block stripped
    assert out["model"] == "qwen-test"
    facts = out["context"]
    assert facts["facts_version"] == 7 and facts["prompt_version"] == 5
    assert facts["lab_results"] == "no lab results recorded"
    # no weight in this fixture: fallback base 2000 ml plus the day's workouts
    assert [(g["goal"], g["min"]) for g in facts["goals"]] == [("Drinks", 2200)]
    assert "training" in facts["goals"][0]["note"]
    assert facts["date"] == "2026-09-21" and facts["weekday"] == "Monday"
    # only one day of history in the fixture: no "usual range" claims are made
    assert facts["notable_vs_usual"] == "nothing outside the usual range"
    assert facts["training"]["today"][0]["active_sets"] == 3
    assert facts["self_report"]["journal"] == "Guter Tag."
    prompt = fake_model[-1]["messages"]
    assert "Keine Diagnosen" in prompt[0]["content"] and "notable_vs_usual" in prompt[0]["content"]
    assert '"training"' in prompt[1]["content"]

    # a personal focus is appended to the system prompt
    client.put(
        "/api/v1/settings/ai",
        headers=AUTH,
        json={
            "enabled": True,
            "base_url": "http://model.test/v1",
            "model": "qwen-test",
            "focus": "Muskelaufbau",
        },
    )
    out = client.post("/api/v1/ai/summary", headers=AUTH, json={"day": "2026-09-21", "lang": "de"}).json()
    assert "Muskelaufbau" in fake_model[-1]["messages"][0]["content"]
    assert client.get("/api/v1/settings/ai", headers=AUTH).json()["focus"] == "Muskelaufbau"

    cached = client.get("/api/v1/ai/summary", headers=AUTH, params={"day": "2026-09-21"}).json()
    assert cached["summary"]["id"] == out["id"] and cached["enabled"]

    week = client.post(
        "/api/v1/ai/summary", headers=AUTH, json={"day": "2026-09-21", "scope": "week", "lang": "en"}
    ).json()
    assert week["context"]["period"] == "2026-09-15 – 2026-09-21"
    assert [d["date"] for d in week["context"]["days"]][-1] == "2026-09-21"
    assert "No diagnoses" in fake_model[-1]["messages"][0]["content"]
    assert "For next week" in fake_model[-1]["messages"][1]["content"]


def test_ai_errors_are_reported(client, monkeypatch):
    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    client.put(
        "/api/v1/settings/ai",
        headers=AUTH,
        json={"enabled": True, "base_url": "http://model.test/v1", "model": "m"},
    )
    r = client.post("/api/v1/ai/summary", headers=AUTH, json={"day": "2026-09-21"})
    assert r.status_code == 502 and "500" in r.json()["detail"]
    assert (
        client.post("/api/v1/ai/models", headers=AUTH, json={"base_url": "http://model.test/v1"}).json()["ok"]
        is False
    )


def test_habit_ids_can_be_client_generated(client):
    hid = str(uuid.uuid4())
    a = client.post("/api/v1/habits", headers=AUTH, json={"id": hid, "name": "Walk"})
    b = client.post("/api/v1/habits", headers=AUTH, json={"id": hid, "name": "Walk"})
    assert a.json()["id"] == b.json()["id"] == hid
