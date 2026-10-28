"""Profile, energy estimate without a watch, capabilities and the AI gate for external models."""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.fixtures import make_export_zip
from vitalocal import ai, energy, vault
from vitalocal.ingest import service

AUTH = {"Authorization": "Bearer test-token"}
PROFILE = {"sex": "male", "birth_year": 1996, "height_cm": 180, "pal": 1.4, "energy_source": "estimate"}


def test_mifflin_and_met():
    assert energy.bmr("male", 30, 180, 80) == 1780
    assert energy.bmr("female", 30, 165, 60) == 1320.25
    assert energy.met_for("training", "strength_training", "vigorous") == 6.0
    assert energy.met_for("running", None, "moderate") == 9.8
    assert energy.met_for("curling", None, "light") == pytest.approx(energy.DEFAULT_MET * 0.8)
    assert energy.intensity(9, None, None) == "vigorous"
    assert energy.intensity(None, 0.6, 0.0) == "vigorous"
    assert energy.intensity(None, 0.0, 0.7) == "light"
    w = {"seconds": 3600, "energy_kcal": None, "sport": "training", "sub_sport": "strength_training",
         "intensity": "moderate"}  # fmt: skip
    assert energy.workout_kcal(w, 80, 1780) == 320  # (5 − 1) × 80 kg × 1 h
    # the watch's session calories contain the resting share, which the base already counts
    assert energy.workout_kcal({**w, "energy_kcal": 400}, 80, 1800) == 325


def test_external_detection_and_gate():
    assert not ai.is_external("http://localhost:11434/v1")
    assert not ai.is_external("http://192.168.1.4:8080/v1")
    assert not ai.is_external("http://ollama:11434/v1")  # Docker service name
    assert not ai.is_external("http://llm.home.arpa/v1")
    assert ai.is_external("https://api.openai.com/v1")
    assert ai.is_external("http://8.8.8.8/v1")

    s = ai.AiSettings(enabled=True, base_url="https://api.mistral.ai/v1", model="m")
    assert ai.available(s, "summary") and ai.available(s, "coach")
    assert not ai.available(s, "labs")  # external: lab reports not shared by default
    assert not s.shares("journal")
    s.share = {**ai.DEFAULT_SHARE, "labs": True}
    assert not ai.available(s, "labs")  # still needs a model that reads images
    s.vision = True
    assert ai.available(s, "labs")
    local = ai.AiSettings(enabled=True, base_url="http://192.168.1.4/v1", model="m")
    assert local.shares("journal") and local.shares("labs")
    assert not ai.available(ai.AiSettings(enabled=True, base_url="", model="m"), "summary")


@pytest.fixture
def client(engine, store, tmp_path):
    from vitalocal.main import app

    service.import_file(engine, store, make_export_zip(tmp_path / "export.zip", tmp_path))
    return TestClient(app)


@pytest.mark.db
def test_profile_capabilities_and_estimate(client):
    caps = client.get("/api/v1/capabilities", headers=AUTH).json()
    assert caps["ai"]["configured"] is False and not any(caps["ai"]["features"].values())
    assert caps["profile_complete"] is False and caps["setup_done"] is False

    p = client.put("/api/v1/profile", headers=AUTH, json=PROFILE).json()
    assert p["complete"] and p["estimate"]["bmr"] is None  # no weight yet
    client.post(
        "/api/v1/body",
        headers=AUTH,
        json={"id": str(uuid.uuid4()), "metric": "weight", "value": 80, "ts": "2026-09-01T08:00:00Z"},
    )
    est = client.get("/api/v1/profile", headers=AUTH).json()["estimate"]
    assert est["weight_kg"] == 80 and est["bmr"] is not None and est["suggested_kcal"] % 10 == 0

    burn = client.get(
        "/api/v1/energy", headers=AUTH, params={"from": "2026-09-19", "to": "2026-09-21"}
    ).json()
    rest_day, workout_day = burn["2026-09-19"], burn["2026-09-21"]
    assert rest_day["source"] == "estimate" and rest_day["active"] == 0
    assert rest_day["rest"] == round(energy.bmr("male", 30, 180, 80) * 1.4)
    assert workout_day["active"] > 0 and workout_day["total"] == workout_day["rest"] + workout_day["active"]

    # an own TDEE replaces BMR × PAL; workouts still add on top
    client.put(
        "/api/v1/profile", headers=AUTH, json={**PROFILE, "energy_source": "manual", "manual_tdee_kcal": 2400}
    )
    day = client.get("/api/v1/days/2026-09-21", headers=AUTH).json()
    assert (
        day["energy"]["source"] == "manual" and day["energy"]["rest"] == 2400 and day["energy"]["active"] > 0
    )

    mods = client.put("/api/v1/modules", headers=AUTH, json={"journal": False}).json()
    assert mods["journal"] is False and mods["nutrition"] is True
    assert client.get("/api/v1/capabilities", headers=AUTH).json()["modules"]["journal"] is False


@pytest.mark.db
def test_external_model_gets_no_journal_or_labs(client, monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"model": "gpt", "choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(handler))
    client.put(
        "/api/v1/settings/ai",
        headers=AUTH,
        json={"enabled": True, "base_url": "https://api.openai.com/v1", "model": "gpt", "api_key": "sk-x"},
    )
    s = client.get("/api/v1/settings/ai", headers=AUTH).json()
    assert s["external"] and s["share"] == ai.DEFAULT_SHARE
    caps = client.get("/api/v1/capabilities", headers=AUTH).json()["ai"]
    assert caps["external"] and caps["features"] == {"summary": True, "coach": True, "labs": False}

    client.put("/api/v1/journal/2026-09-21", headers=AUTH, json={"text": "Privat."})
    out = client.post("/api/v1/ai/summary", headers=AUTH, json={"day": "2026-09-21"}).json()
    assert out["context"]["self_report"]["journal"] == "not shared with this AI model"
    assert out["context"]["lab_results"] == "not shared with this AI model"
    assert "Privat." not in calls[-1]["messages"][1]["content"]

    # sharing switched off for the coach: the coach is hidden
    client.put(
        "/api/v1/settings/ai",
        headers=AUTH,
        json={"enabled": True, "base_url": "https://api.openai.com/v1", "model": "gpt",
              "share": {"coach": False, "journal": True}},
    )  # fmt: skip
    assert client.get("/api/v1/coach/status", headers=AUTH).json() == {"enabled": False, "model": None}
    out = client.post("/api/v1/ai/summary", headers=AUTH, json={"day": "2026-09-21"}).json()
    assert out["context"]["self_report"]["journal"] == "Privat."


@pytest.mark.db
def test_api_key_is_stored_encrypted(client, engine):
    with engine.begin() as conn:  # a key saved before encryption existed
        conn.execute(
            text("""INSERT INTO app_setting (key, value) VALUES ('ai', CAST(:v AS jsonb))"""),
            {
                "v": json.dumps(
                    {"enabled": True, "base_url": "http://m.test/v1", "model": "m", "api_key": "sk-old"}
                )
            },
        )
    with engine.begin() as conn:
        s = ai.load_settings(conn)
        assert s.api_key == "sk-old" and s.vision  # legacy setups keep reading lab reports
    with engine.begin() as conn:
        stored = conn.execute(text("SELECT value->>'api_key' FROM app_setting WHERE key = 'ai'")).scalar()
        assert vault.is_encrypted(stored) and "sk-old" not in stored
        assert ai.load_settings(conn).api_key == "sk-old"


def test_chat_retries_without_temperature(monkeypatch):
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported value: 'temperature'"}})
        return httpx.Response(200, json={"model": "o", "choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(handler))
    s = ai.AiSettings(enabled=True, base_url="https://api.openai.com/v1", model="o")
    assert ai.chat(s, "sys", "user", json_schema={"type": "object"}) == ("{}", "o")
    assert len(bodies) == 2 and "response_format" in bodies[-1]


def test_english_library_names():
    from vitalocal import exercise_library

    assert exercise_library.english_name("barbell_bench_press") == "Barbell bench press"
    assert exercise_library.english_name("ez_bar_curl") == "EZ bar curl"
    assert exercise_library.english_name("weighted_pull_up") == "Weighted pull-up"
    assert exercise_library.english_name("t_bar_row") == "T-bar row"
    names = [exercise_library.english_name(e["key"]) for e in exercise_library.entries()]
    assert len(set(names)) == len(names)


@pytest.mark.db
def test_language_of_server_texts(client):
    assert client.get("/api/v1/nutrition/2026-09-21", headers=AUTH).json()["meals"][0]["name"] == "Frühstück"
    client.put("/api/v1/profile", headers=AUTH, json={**PROFILE, "units": "imperial"})
    client.put("/api/v1/profile", headers=AUTH, json={"lang": "en"})  # partial: the rest stays
    p = client.get("/api/v1/profile", headers=AUTH).json()
    assert p["lang"] == "en" and p["sex"] == "male" and p["units"] == "imperial"
    assert client.get("/api/v1/nutrition/2026-09-21", headers=AUTH).json()["meals"][0]["name"] == "Breakfast"
    eq = {e["key"]: e["label"] for e in client.get("/api/v1/training/equipment", headers=AUTH).json()}
    assert eq["dumbbell"] == "Dumbbells"
    lib = client.get("/api/v1/training/library", headers=AUTH, params={"q": "bench"}).json()
    assert any(e["name"] == "Barbell bench press" for e in lib)
    ex = client.post("/api/v1/training/library/barbell_bench_press/adopt", headers=AUTH).json()
    assert ex["name"] == "Barbell bench press"


def test_empty_secret_key_falls_back_to_file(tmp_path, monkeypatch):
    from vitalocal import config

    monkeypatch.setenv("VITALOCAL_API_TOKEN", "t")
    monkeypatch.setenv("VITALOCAL_SECRET_KEY", "")
    monkeypatch.setenv("VITALOCAL_RAW_STORE_PATH", str(tmp_path))
    config.get_settings.cache_clear()
    vault._fernet.cache_clear()
    try:
        assert vault.decrypt(vault.encrypt("sk-1")) == "sk-1"
        assert (tmp_path / vault.KEY_FILE).exists()
    finally:
        config.get_settings.cache_clear()
        vault._fernet.cache_clear()


def test_routes_commit_before_responding():
    """A route reading `Depends(get_conn)` would commit after the response (FastAPI's default
    scope): the client's next read could see the old state (e.g. a plan review taken over)."""
    from pathlib import Path

    import vitalocal

    root = Path(vitalocal.__file__).parent
    files = [p for p in root.rglob("*.py") if p.name != "db.py"]
    offenders = [p.name for p in files if "Depends(get_conn" in p.read_text()]
    assert offenders == []
