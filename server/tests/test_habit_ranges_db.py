"""Bedtime, wake time and caffeine cut-off habits from real data; correcting drinks afterwards."""

import uuid

import pytest
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


def hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _status(client, frm: str, to: str) -> dict:
    rows = client.get("/api/v1/habits/status", headers=AUTH, params={"from": frm, "to": to}).json()
    return {h["name"]: h for h in rows}


def _drink(client, ts: str, drink: str, **extra) -> dict:
    r = client.post(
        "/api/v1/drinks",
        headers=AUTH,
        json={"id": str(uuid.uuid4()), "ts": ts, "drink": drink, "ml": 200, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_bedtime_and_wake_time_windows(client, engine):
    # night 1: in bed Sat 19th 22:30, up Sun 20th 06:30; night 2: in bed Sun 23:30, up Mon 21st 07:30
    with engine.begin() as conn:
        dev, run = conn.execute(text("SELECT device_id, parse_run_id FROM sample LIMIT 1")).one()
        rows = [
            ("2026-09-19T22:30:00+02:00", "sleep_in_bed", 28800),
            ("2026-09-20T02:00:00+02:00", "sleep_stage_code", 2),
            ("2026-09-20T06:30:00+02:00", "sleep_stage_code", 3),
            ("2026-09-20T23:30:00+02:00", "sleep_in_bed", 28800),
            ("2026-09-21T07:00:00+02:00", "sleep_stage_code", 2),
            ("2026-09-21T07:30:00+02:00", "sleep_stage_code", 1),
        ]  # fmt: skip
        for ts, metric, v in rows:
            conn.execute(
                text("""INSERT INTO sample (ts, device_id, metric, value, parse_run_id)
                        VALUES (:ts, :d, :m, :v, :r)"""),
                {"ts": ts, "d": dev, "m": metric, "v": v, "r": run},
            )
    bed = {"name": "Bett", "metric": "sleep.bedtime", "min_value": hm("21:50"), "max_value": hm("22:40")}
    wake = {
        "name": "Aufstehen",
        "metric": "sleep.wake_time",
        "min_value": hm("06:00"),
        "max_value": hm("07:00"),
    }
    for body in (bed, wake):
        assert client.post("/api/v1/habits", headers=AUTH, json=body).status_code == 201

    st = _status(client, "2026-09-18", "2026-09-21")
    # bedtime counts for the evening the night began
    assert st["Bett"]["days"] == {
        "2026-09-19": {"value": hm("22:30"), "done": True, "auto": True, "delta": 0, "display": "22:30"},
        "2026-09-20": {"value": hm("23:30"), "done": False, "auto": True, "delta": 50, "display": "23:30"},
    }  # the 21st is open until the next night is synced; the 18th had no data: both unknown
    assert st["Aufstehen"]["days"] == {
        "2026-09-20": {"value": hm("06:30"), "done": True, "auto": True, "delta": 0, "display": "06:30"},
        "2026-09-21": {"value": hm("07:30"), "done": False, "auto": True, "delta": 30, "display": "07:30"},
    }
    today = client.get("/api/v1/today", headers=AUTH, params={"day": "2026-09-20"}).json()
    h = next(x for x in today["habits"] if x["name"] == "Bett")
    assert (h["min_value"], h["max_value"], h["today"]["display"]) == (hm("21:50"), hm("22:40"), "23:30")


def test_caffeine_cutoff_and_drink_corrections(client):
    habit = {"name": "Kein Koffein nach 14 Uhr", "metric": "drinks.last_caffeine", "max_value": hm("14:00")}
    assert client.post("/api/v1/habits", headers=AUTH, json=habit).status_code == 201
    coffee = _drink(client, "2026-09-21T08:00:00+02:00", "coffee")
    herbal = _drink(client, "2026-09-21T20:00:00+02:00", "tea", caffeine=False)
    widget = _drink(client, "2026-09-19T16:00:00+02:00", "water")  # quick-add logs water
    _drink(client, "2026-09-20T10:00:00+02:00", "water")
    assert (coffee["caffeine"], herbal["caffeine"], widget["caffeine"]) == (True, False, False)

    days = _status(client, "2026-09-18", "2026-09-21")[habit["name"]]["days"]
    assert days["2026-09-21"]["display"] == "08:00" and days["2026-09-21"]["done"]
    assert days["2026-09-20"] == {"value": None, "done": True, "auto": True, "none": True}  # no caffeine
    assert days["2026-09-19"]["done"]  # only water so far
    assert "2026-09-18" not in days  # no drinks at all: unknown

    # the widget entry was really a coffee: correcting the kind makes it caffeinated by default
    fix = {"ts": "2026-09-19T16:00:00+02:00", "drink": "coffee", "ml": 250}
    r = client.put(f"/api/v1/drinks/{widget['id']}", headers=AUTH, json=fix)
    assert r.status_code == 200 and (r.json()["drink"], r.json()["ml"], r.json()["caffeine"]) == (
        "coffee",
        250,
        True,
    )
    assert (
        client.put(f"/api/v1/drinks/{widget['id']}", headers=AUTH, json=fix).json() == r.json()
    )  # replay-safe
    days = _status(client, "2026-09-18", "2026-09-21")[habit["name"]]["days"]
    assert days["2026-09-19"] | {"value": None} == {
        "value": None, "done": False, "auto": True, "delta": 120, "display": "16:00",
    }  # fmt: skip

    # decaf: explicitly not caffeinated
    client.put(f"/api/v1/drinks/{widget['id']}", headers=AUTH, json={**fix, "caffeine": False})
    assert _status(client, "2026-09-19", "2026-09-19")[habit["name"]]["days"]["2026-09-19"]["none"]

    assert client.put(f"/api/v1/drinks/{uuid.uuid4()}", headers=AUTH, json=fix).status_code == 404
    bad = client.put(
        f"/api/v1/drinks/{widget['id']}", headers=AUTH, json={**fix, "ts": "2026-09-19T16:00:00"}
    )
    assert bad.status_code == 422  # timestamps need a timezone
    day = client.get("/api/v1/drinks", headers=AUTH, params={"day": "2026-09-21"}).json()
    assert [(e["drink"], e["caffeine"]) for e in day["entries"]] == [("coffee", True), ("tea", False)]


def test_floors_and_all_supplements_as_habit_values(client, engine):
    # the export has steps on 2026-09-20/21; floors only on the 21st
    with engine.begin() as conn:
        dev, run = conn.execute(text("SELECT device_id, parse_run_id FROM sample LIMIT 1")).one()
        conn.execute(
            text("""INSERT INTO sample (ts, device_id, metric, value, parse_run_id)
                    VALUES ('2026-09-21T10:00:00+02:00', :d, 'floors_climbed', 6, :r),
                           ('2026-09-21T18:00:00+02:00', :d, 'floors_climbed', 5, :r)"""),
            {"d": dev, "r": run},
        )
        steps_days = {
            r.day.isoformat()
            for r in conn.execute(
                text("""SELECT DISTINCT (ts AT TIME ZONE 'Europe/Berlin')::date AS day
                        FROM sample WHERE metric = 'steps'""")
            )
        }
    floors = {"name": "Treppen", "metric": "floors_climbed", "min_value": 10}
    sups = {"name": "Supplements", "metric": "supplements.taken", "min_value": 100}
    for body in (floors, sups):
        assert client.post("/api/v1/habits", headers=AUTH, json=body).status_code == 201
    keys = [m["key"] for m in client.get("/api/v1/habits/metrics", headers=AUTH).json()]
    assert "floors_climbed" in keys and "supplements.taken" in keys

    for name, per_day in (("D3", 2), ("Omega", 1)):
        r = client.post("/api/v1/supplements", headers=AUTH, json={"name": name, "per_day": per_day})
        sid = r.json()["id"]
        if name == "D3":
            client.put(f"/api/v1/supplements/{sid}/log/2026-09-20", headers=AUTH, json={"count": 1})
            client.put(f"/api/v1/supplements/{sid}/log/2026-09-21", headers=AUTH, json={"count": 2})
        else:
            client.put(f"/api/v1/supplements/{sid}/log/2026-09-21", headers=AUTH, json={"count": 1})

    st = _status(client, "2026-09-19", "2026-09-21")
    f = st["Treppen"]["days"]
    assert f["2026-09-21"]["value"] == 11 and f["2026-09-21"]["done"]
    if "2026-09-20" in steps_days:  # watch worn, nothing climbed: 0, a miss rather than unknown
        assert f["2026-09-20"]["value"] == 0 and not f["2026-09-20"]["done"]
    s = st["Supplements"]["days"]
    assert "2026-09-19" not in s  # before anything was logged: unknown
    assert s["2026-09-20"] == {
        "value": 0,
        "done": False,
        "auto": True,
        "delta": -100,
    }  # D3 1 of 2, Omega none
    assert s["2026-09-21"]["value"] == 100 and s["2026-09-21"]["done"]
