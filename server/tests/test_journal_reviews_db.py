"""Journal versions and revisions, weekly/monthly reviews, search, insights and the day layout."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.fixtures import make_export_zip
from vitalocal.ingest import service

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(engine, store, tmp_path):
    from vitalocal.main import app

    service.import_file(engine, store, make_export_zip(tmp_path / "export.zip", tmp_path))
    return TestClient(app)


def test_journal_compare_and_swap_and_revisions(client, engine):
    url = "/api/v1/journal/2026-09-21"
    empty = client.get(url, headers=AUTH)
    assert empty.json()["version"] == 0 and empty.headers["etag"] == '"0"'

    first = client.put(url, headers=AUTH, json={"text": "Morgens gelaufen.", "version": 0, "source": "webui"})
    assert first.status_code == 200 and first.json()["version"] == 1
    # a second writer that still holds version 0 must not overwrite silently
    stale = client.put(url, headers=AUTH, json={"text": "Agent's version", "version": 0, "source": "agent"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["text"] == "Morgens gelaufen."
    # If-Match works as well; without any version it stays last-write-wins
    assert client.put(url, headers={**AUTH, "If-Match": '"0"'}, json={"text": "x"}).status_code == 409
    ok = client.put(
        url, headers={**AUTH, "If-Match": '"1"'}, json={"text": "Gelaufen, 5 km.", "source": "agent"}
    )
    assert ok.status_code == 200 and ok.json()["version"] == 2 and ok.headers["etag"] == '"2"'

    # the text replaced by another source is kept; so is the text an empty PUT cleared
    cleared = client.put(url, headers=AUTH, json={"text": "  ", "version": 2, "source": "agent"})
    assert cleared.json() | {"updated_at": None} == {
        "day": "2026-09-21", "text": "", "version": 3, "source": "agent", "updated_at": None,
    }  # fmt: skip
    revs = client.get("/api/v1/journal/revisions", headers=AUTH, params={"day": "2026-09-21"}).json()
    assert [(r["version"], r["text"], r["source"]) for r in revs] == [
        (2, "Gelaufen, 5 km.", "agent"),
        (1, "Morgens gelaufen.", "webui"),
    ]
    # the row stays (version keeps counting), but the day no longer shows up as written
    overview = client.get(
        "/api/v1/journal", headers=AUTH, params={"from": "2026-09-01", "to": "2026-09-30"}
    ).json()
    assert not any(r["excerpt"] for r in overview)
    assert client.put(url, headers=AUTH, json={"text": "neu", "version": 0}).status_code == 409

    # autosave bursts from the same source do not pile up revisions
    for i in range(3):
        client.put(url, headers=AUTH, json={"text": f"Tipp {i}", "source": "webui"})
    revs = client.get("/api/v1/journal/revisions", headers=AUTH, params={"day": "2026-09-21"}).json()
    assert len(revs) == 2


def test_week_and_month_reviews_with_facts(client):
    day = "2026-09-21"  # a Monday in the fixture export
    client.post(
        "/api/v1/checkins",
        headers=AUTH,
        json={"id": str(uuid.uuid4()), "kind": "mood", "value": 4, "ts": f"{day}T09:00:00+02:00"},
    )
    client.post(
        "/api/v1/checkins",
        headers=AUTH,
        json={"id": str(uuid.uuid4()), "kind": "symptom", "label": "Kopfschmerzen", "value": 3,
              "ts": f"{day}T12:00:00+02:00"},
    )  # fmt: skip
    client.post(
        "/api/v1/drinks",
        headers=AUTH,
        json={"id": str(uuid.uuid4()), "ml": 700, "ts": f"{day}T12:00:00+02:00"},
    )

    wk = client.get("/api/v1/journal/week/2026-09-24", headers=AUTH).json()
    assert (wk["start"], wk["end"], wk["version"], wk["facts_saved"]) == ("2026-09-21", "2026-09-27", 0, None)
    facts = wk["facts"]
    assert facts["period"]["days"] == 7
    assert facts["metrics"]["checkin.mood"]["avg"] == 4 and facts["metrics"]["nutrition.drinks"]["avg"] == 700
    assert facts["metrics"]["steps"]["days"] >= 1
    assert facts["symptoms"] == [{"label": "Kopfschmerzen", "days": 1, "max_severity": 3}]

    r = client.put(
        "/api/v1/journal/week/2026-09-27",
        headers=AUTH,
        json={"text": "Gute Woche, viel Laufen.", "version": 0, "source": "agent"},
    )
    assert r.status_code == 200 and r.json()["start"] == "2026-09-21" and r.json()["version"] == 1
    wk = client.get("/api/v1/journal/week/2026-09-21", headers=AUTH).json()
    assert wk["source"] == "agent" and wk["facts_saved"]["metrics"]["checkin.mood"]["avg"] == 4
    assert (
        client.put(
            "/api/v1/journal/week/2026-09-21", headers=AUTH, json={"text": "x", "version": 0}
        ).status_code
        == 409
    )

    mo = client.put(
        "/api/v1/journal/month/2026-09-30", headers=AUTH, json={"text": "September: Laufen."}
    ).json()
    assert (mo["start"], mo["end"]) == ("2026-09-01", "2026-09-30")
    listed = client.get(
        "/api/v1/journal/reviews", headers=AUTH, params={"from": "2026-09-01", "to": "2026-09-30"}
    ).json()
    assert [(x["scope"], x["start"]) for x in listed] == [("month", "2026-09-01"), ("week", "2026-09-21")]

    client.put(f"/api/v1/journal/{day}", headers=AUTH, json={"text": "Abends Kopfschmerzen nach dem Laufen."})
    hits = client.get("/api/v1/journal/search", headers=AUTH, params={"q": "laufen"}).json()
    assert {h["scope"] for h in hits} == {"day", "week", "month"}  # German stemming: Laufen/laufen
    assert any("⟦" in h["snippet"] for h in hits)
    notes = client.get("/api/v1/journal/search", headers=AUTH, params={"q": "kopfschmerzen"}).json()
    assert {h["scope"] for h in notes} == {"day", "note"}


def test_insights_and_day_layout(client, engine):
    # two nights: sleep start (FIT) plus stage samples stamped at the END of each stage
    # (light 2 / deep 3 / rem 4 / awake 1), ending on the 20th and 21st
    with engine.begin() as conn:
        dev, run = conn.execute(text("SELECT device_id, parse_run_id FROM sample LIMIT 1")).one()
        rows = [
            ("2026-09-19T22:30:00+02:00", "sleep_in_bed", 28800),
            ("2026-09-20T01:00:00+02:00", "sleep_stage_code", 2),
            ("2026-09-20T04:00:00+02:00", "sleep_stage_code", 3),
            ("2026-09-20T06:30:00+02:00", "sleep_stage_code", 4),
            ("2026-09-20T23:30:00+02:00", "sleep_in_bed", 28800),
            ("2026-09-21T03:00:00+02:00", "sleep_stage_code", 2),
            ("2026-09-21T07:00:00+02:00", "sleep_stage_code", 3),
            ("2026-09-21T07:30:00+02:00", "sleep_stage_code", 1),
            ("2026-09-20T08:00:00+02:00", "garmin.readiness_factor.acute_load", 90),  # day before
            ("2026-09-21T08:00:00+02:00", "garmin.training_readiness", 75),
            ("2026-09-21T08:00:00+02:00", "garmin.readiness_factor.sleep_score", 72),
            ("2026-09-21T08:00:00+02:00", "garmin.readiness_factor.recovery_time", 77),
        ]  # fmt: skip
        for ts, metric, v in rows:
            conn.execute(
                text("""INSERT INTO sample (ts, device_id, metric, value, parse_run_id)
                        VALUES (:ts, :d, :m, :v, :r)"""),
                {"ts": ts, "d": dev, "m": metric, "v": v, "r": run},
            )
    r = client.get("/api/v1/insights", headers=AUTH, params={"day": "2026-09-21"})
    assert r.status_code == 200, r.text
    ins = r.json()
    assert set(ins) == {"day", "readiness", "sleep_rhythm", "recovery", "energy", "weeks"}
    assert len(ins["weeks"]) == 8 and ins["weeks"][-1]["week_start"] == "2026-09-21"
    assert ins["weeks"][-1]["partial"] and ins["weeks"][-1]["steps"] is not None
    rhythm = ins["sleep_rhythm"]["last_7"]
    # 22:30 and 23:30 average to 23:00 (not across noon); asleep 480 and 450 (+30 min awake)
    assert (rhythm["nights"], rhythm["bedtime"], rhythm["wake_time"], rhythm["asleep_minutes"]) == (
        2, "23:00", "07:00", 465,
    )  # fmt: skip
    day = client.get("/api/v1/days/2026-09-21", headers=AUTH).json()["sleep"]
    assert day["minutes"] == {"light": 210, "deep": 240, "awake": 30} and day["asleep_minutes"] == 450
    readiness = ins["readiness"]
    assert readiness["score"] is None or 0 <= readiness["score"] <= 100
    assert all({"key", "weight", "score"} <= set(c) for c in readiness["components"])
    # the watch's own factor ratings of that day only, keyed like the components
    assert readiness["garmin"]["factors"] == {"sleep_score": 72, "recovery_time": 77}

    assert client.get("/api/v1/settings/day-layout", headers=AUTH).json() is None
    layout = {"today": [{"id": "drinks", "size": "half"}, {"id": "kpis"}], "past": [{"id": "sleep"}]}
    saved = client.put("/api/v1/settings/day-layout", headers=AUTH, json=layout).json()
    assert saved["today"][1] == {"id": "kpis", "size": "full"}
    assert client.get("/api/v1/settings/day-layout", headers=AUTH).json() == saved
    grid = {"today": [{"id": "kpis", "w": 3, "h": 2}], "past": []}
    assert client.put("/api/v1/settings/day-layout", headers=AUTH, json=grid).json()["today"][0]["w"] == 3
    too_big = {"today": [{"id": "kpis", "w": 5, "h": 2}], "past": []}
    assert client.put("/api/v1/settings/day-layout", headers=AUTH, json=too_big).status_code == 422
    heading = {"today": [{"id": "heading_1", "w": 4, "h": 1, "text": "Schlaf & Erholung"}], "past": []}
    saved_heading = client.put("/api/v1/settings/day-layout", headers=AUTH, json=heading).json()
    assert saved_heading["today"][0]["text"] == "Schlaf & Erholung"
    long_heading = {"today": [{"id": "heading_1", "text": "x" * 61}], "past": []}
    assert client.put("/api/v1/settings/day-layout", headers=AUTH, json=long_heading).status_code == 422
    bad = {"today": [{"id": "<script>"}], "past": []}
    assert client.put("/api/v1/settings/day-layout", headers=AUTH, json=bad).status_code == 422
    client.delete("/api/v1/settings/day-layout", headers=AUTH)
    assert client.get("/api/v1/settings/day-layout", headers=AUTH).json() is None


def test_migration_moves_water_checkins_to_drinks(engine):
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.downgrade(cfg, "0005")
    cid = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO checkin (id, ts, kind, value, label, source)
                    VALUES (:id, '2026-09-20T10:00:00+02:00', 'water', 330, 'tea', 'android'),
                           (gen_random_uuid(), '2026-09-20T11:00:00+02:00', 'mood', 3, NULL, 'webui')"""),
            {"id": cid},
        )
        conn.execute(
            text("""INSERT INTO habit (id, name, kind, metric, comparator, threshold)
                    VALUES (gen_random_uuid(), 'Trinken', 'check', 'checkin.water', '>=', 2000)""")
        )
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id, drink, ml, source, caffeine FROM drink")).one() == (
            cid,
            "tea",
            330,
            "android",
            True,  # 0007: tea and coffee default to caffeinated
        )
        assert conn.execute(text("SELECT kind FROM checkin")).scalars().all() == ["mood"]
        # 0007: `>= 2000` became a lower bound
        assert conn.execute(text("SELECT metric, min_value, max_value FROM habit")).one() == (
            "nutrition.drinks",
            2000,
            None,
        )
