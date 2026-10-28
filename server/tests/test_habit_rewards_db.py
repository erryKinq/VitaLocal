"""Habit gamification: templates, flames, levels, records, perfect days and achievements."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.fixtures import make_export_zip
from vitalocal.api.habit_rewards import TEMPLATES, level_of
from vitalocal.api.habits import HabitIn
from vitalocal.ingest import service

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}
TODAY = date(2026, 9, 21)  # a Monday


@pytest.fixture
def client(engine):
    from vitalocal.main import app

    return TestClient(app)


def _days(last: date, n: int) -> list[str]:
    return [(last - timedelta(days=i)).isoformat() for i in range(n)]


def _log(client, habit, days, value=1):
    for d in days:
        assert (
            client.put(
                f"/api/v1/habits/{habit['id']}/log/{d}", headers=AUTH, json={"value": value}
            ).status_code
            == 200
        )


def _rewards(client, today=TODAY):
    return client.get("/api/v1/habits/rewards", headers=AUTH, params={"today": today.isoformat()}).json()


def test_levels():
    assert (level_of(0)["level"], level_of(0)["next"]) == (1, 10)
    assert (level_of(9)["level"], level_of(10)["level"], level_of(30)["level"]) == (1, 2, 3)
    assert {k: level_of(45)[k] for k in ("level", "floor", "next", "progress")} == {
        "level": 3,
        "floor": 30,
        "next": 60,
        "progress": 0.5,
    }
    assert level_of(100)["title"] == "Ember" and level_of(450)["title"] == "Blaze"


def test_templates_are_valid_habits():
    for t in TEMPLATES:
        HabitIn(**{k: v for k, v in t.items() if k not in ("key", "category", "why")}, template=t["key"])
    assert len({t["key"] for t in TEMPLATES}) == len(TEMPLATES)


def test_templates_mark_added(client):
    t = next(t for t in TEMPLATES if t["key"] == "floss")
    body = {k: t[k] for k in ("name", "icon", "kind", "per_week")} | {"template": "floss"}
    created = client.post("/api/v1/habits", headers=AUTH, json=body).json()
    assert created["icon"] == "🦷" and created["template"] == "floss"
    added = {x["key"] for x in client.get("/api/v1/habits/templates", headers=AUTH).json() if x["added"]}
    assert added == {"floss"}
    # an empty icon is stored as none
    edited = client.put(f"/api/v1/habits/{created['id']}", headers=AUTH, json={**body, "icon": " "}).json()
    assert edited["icon"] is None


def test_flames_streaks_and_achievements(client):
    floss = client.post("/api/v1/habits", headers=AUTH, json={"name": "Floss", "icon": "🦷"}).json()
    read = client.post(
        "/api/v1/habits",
        headers=AUTH,
        json={"name": "Read", "kind": "count", "unit": "pages", "target": 10},
    ).json()
    sauna = client.post("/api/v1/habits", headers=AUTH, json={"name": "Sauna", "per_week": 2}).json()

    # floss: 8 days in a row up to yesterday, after an earlier run of 2 and a 4-day break
    _log(client, floss, _days(TODAY - timedelta(days=1), 8) + ["2026-09-07", "2026-09-08"])
    _log(client, read, _days(TODAY, 3), 12)
    _log(client, read, ["2026-09-17"], 4)  # partial: no flame
    _log(client, sauna, ["2026-09-08", "2026-09-10", "2026-09-15", "2026-09-16"])

    r = _rewards(client)
    assert {k: r["flames"][k] for k in ("total", "today", "week", "best_day", "bonus")} == {
        "total": 10 + 3 + 4,
        "today": 1,
        "week": 1,
        "best_day": 2,
        "bonus": 0,
    }
    assert (r["level"]["level"], r["level"]["floor"], r["level"]["next"]) == (2, 10, 30)
    by = {h["name"]: h for h in r["habits"]}
    assert (by["Floss"]["flames"], by["Floss"]["streak"], by["Floss"]["best_streak"]) == (10, 8, 8)
    assert by["Floss"]["best_from"] == "2026-09-13" and by["Floss"]["tier"]["title"] == "One week"
    assert by["Floss"]["next_tier"]["n"] == 21
    assert (by["Sauna"]["unit"], by["Sauna"]["best_streak"]) == ("weeks", 2)
    # records compare by time span: two weeks of sauna beat eight days of flossing
    assert r["best_current"]["name"] == "Sauna" and r["best_ever"]["name"] == "Sauna"

    unlocked = {a["key"]: a for a in r["achievements"] if a["unlocked_on"]}
    fid = floss["id"]
    assert unlocked[f"habit:{fid}:streak:7"]["unlocked_on"] == "2026-09-19"  # 13th + 6 days
    assert f"habit:{fid}:streak:21" not in unlocked
    assert unlocked["flames:10"]["unlocked_on"] == "2026-09-16"  # 1+2+1+1+1+2+2
    assert unlocked["comeback"]["unlocked_on"] == "2026-09-15"  # 4 days off, then 3 in a row
    assert unlocked[f"habit:{sauna['id']}:streak:2"]["unlocked_on"] == "2026-09-16"
    assert all(a["new"] for a in unlocked.values()) and len(r["new"]) == len(unlocked)
    locked = next(a for a in r["achievements"] if a["key"] == "flames:50")
    assert (locked["progress"], locked["target"], locked["unlocked_on"]) == (17, 50, None)
    # the flame badges form one series with ranks from bronze up
    assert (locked["series"], locked["rank"], locked["ranks"], locked["tier"]) == ("flames", 2, 8, "gold")
    assert next(a for a in r["achievements"] if a["key"] == "flames:2500")["tier"] == "legend"

    # seen: no longer new; removing logs later keeps what was earned
    assert client.post("/api/v1/habits/rewards/seen", headers=AUTH).json()["seen"] == len(unlocked)
    _log(client, floss, _days(TODAY - timedelta(days=1), 8), 0)
    r = _rewards(client)
    assert not r["new"] and f"habit:{fid}:streak:7" in {
        a["key"] for a in r["achievements"] if a["unlocked_on"]
    }
    assert r["flames"]["total"] == 2 + 3 + 4


def test_perfect_days(client):
    a = client.post("/api/v1/habits", headers=AUTH, json={"name": "A"}).json()
    b = client.post("/api/v1/habits", headers=AUTH, json={"name": "B"}).json()
    weekly = client.post("/api/v1/habits", headers=AUTH, json={"name": "W", "per_week": 1}).json()
    _log(client, a, _days(TODAY, 4))  # 18..21
    _log(client, b, _days(TODAY - timedelta(days=1), 3))  # 18..20, today still open
    _log(client, weekly, ["2026-09-01"])
    r = _rewards(client)
    p = r["perfect"]
    assert (p["current"], p["best"], p["total"], p["today"]) == (3, 3, 3, False)
    assert p["open_today"] == [b["id"]]
    unlocked = {x["key"]: x["unlocked_on"] for x in r["achievements"] if x["unlocked_on"]}
    assert unlocked["perfect:1"] == "2026-09-18" and unlocked["perfect_streak:3"] == "2026-09-20"
    assert unlocked["perfect_weekend"] == "2026-09-20"  # Saturday 19 + Sunday 20
    _log(client, b, [TODAY.isoformat()])
    assert _rewards(client)["perfect"]["current"] == 4


def test_automatic_habit_counts_from_creation(client, engine, store, tmp_path):
    service.import_file(engine, store, make_export_zip(tmp_path / "export.zip", tmp_path))
    body = {"name": "Steps", "metric": "steps", "min_value": 1}
    steps = client.post("/api/v1/habits", headers=AUTH, json=body).json()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE habit SET created_at = '2026-09-20 12:00+02' WHERE id = :id"), {"id": steps["id"]}
        )
        dev, run = conn.execute(text("SELECT device_id, parse_run_id FROM sample LIMIT 1")).one()
        conn.execute(text("DELETE FROM sample WHERE metric = 'steps'"))
        for d in _days(TODAY, 5):
            conn.execute(
                text("""INSERT INTO sample (ts, device_id, metric, value, parse_run_id)
                        VALUES (:ts, :d, 'steps', 500, :r)"""),
                {"ts": f"{d}T10:00:00+02:00", "d": dev, "r": run},
            )
    # the history shows all five days, but streak and flames count from the day it was created
    st = client.get(
        "/api/v1/habits/status", headers=AUTH, params={"from": "2026-09-01", "to": TODAY.isoformat()}
    ).json()[0]
    assert len([d for d in st["days"].values() if d["done"]]) == 5 and st["streak"] == 2
    r = _rewards(client)
    assert r["flames"]["total"] == 2 and r["habits"][0]["tracked_from"] == "2026-09-20"


def test_jokers_bridge_a_missed_day(client):
    floss = client.post("/api/v1/habits", headers=AUTH, json={"name": "Floss"}).json()
    # 7 days (earns a joker), one missed day (bridged), 3 more days up to today
    _log(client, floss, _days(date(2026, 9, 17), 7) + _days(TODAY, 3))
    st = client.get(
        "/api/v1/habits/status", headers=AUTH, params={"from": "2026-09-01", "to": TODAY.isoformat()}
    ).json()[0]
    assert (st["streak"], st["jokers"]) == (10, 0)
    assert st["days"]["2026-09-18"] == {"value": None, "done": False, "joker": True}
    r = _rewards(client)
    h = r["habits"][0]
    assert (h["streak"], h["best_streak"], h["best_from"], h["best_to"]) == (
        10,
        10,
        "2026-09-11",
        TODAY.isoformat(),
    )
    assert "joker" in {a["key"] for a in r["achievements"] if a["unlocked_on"]}
    # a second missed day without a joker ends the streak
    _log(client, floss, ["2026-09-21"], 0)
    _log(client, floss, ["2026-09-20"], 0)
    assert (
        client.get(
            "/api/v1/habits/status", headers=AUTH, params={"from": "2026-09-01", "to": TODAY.isoformat()}
        ).json()[0]["streak"]
        == 0
    )


def test_hard_habit_routine_and_garden(client):
    a = client.post("/api/v1/habits", headers=AUTH, json={"name": "A", "hard": True}).json()
    b = client.post("/api/v1/habits", headers=AUTH, json={"name": "B"}).json()
    assert a["hard"] and a["hard_since"] and not b["hard"]
    from sqlalchemy import text as sql

    from vitalocal.db import get_engine

    with get_engine().begin() as conn:
        conn.execute(sql("UPDATE habit SET hard_since = '2026-09-19' WHERE hard_since IS NOT NULL"))  # A
        conn.execute(sql("UPDATE habit SET created_at = '2026-09-01 12:00+02'"))
    routine = client.post(
        "/api/v1/habits/routines",
        headers=AUTH,
        json={"name": "Evening", "icon": "🌙", "habit_ids": [b["id"], a["id"]]},
    )
    assert routine.status_code == 201, routine.text
    with get_engine().begin() as conn:
        conn.execute(sql("UPDATE habit_routine SET created_at = '2026-09-01 12:00+02'"))
    bad = client.post(
        "/api/v1/habits/routines", headers=AUTH, json={"name": "x", "habit_ids": [a["id"], a["id"]]}
    )
    assert bad.status_code == 422

    _log(client, a, _days(TODAY, 4))  # 18..21: 18 counts once, 19..21 twice
    _log(client, b, _days(TODAY, 2))  # 20, 21
    r = _rewards(client)
    by = {h["name"]: h for h in r["habits"]}
    assert by["A"]["flames"] == 1 + 3 * 2 and by["B"]["flames"] == 2
    rt = r["routines"][0]
    assert (rt["completions"], rt["streak"], rt["done_today"], rt["bonus"]) == (2, 2, True, 2)
    # habit flames 9 + routine bonus 2 per complete day
    assert (r["flames"]["habits"], r["flames"]["bonus"]) == (9, 4) and r["flames"]["total"] == 13
    assert "routines:1" in {x["key"] for x in r["achievements"] if x["unlocked_on"]}

    plants = {g["name"]: g for g in r["garden"]}
    assert plants["A"]["plant"] == "🌿" and plants["A"]["health"] == "healthy"  # growth 4
    assert plants["B"]["stage"] == "sprout"
    # B missed two days → wilting, and growth shrinks
    _log(client, b, ["2026-09-21", "2026-09-20"], 0)
    _log(client, b, ["2026-09-19"])
    g = {x["name"]: x for x in _rewards(client, TODAY + timedelta(days=2))["garden"]}["B"]
    assert g["health"] == "wilting" and g["growth"] == 0


def test_weekly_challenges(client):
    a = client.post("/api/v1/habits", headers=AUTH, json={"name": "A"}).json()
    b = client.post("/api/v1/habits", headers=AUTH, json={"name": "B"}).json()
    _log(client, a, _days(date(2026, 9, 20), 7))  # last week every day
    _log(client, b, ["2026-09-15"])  # last week once
    r = _rewards(client)
    ch = {c["kind"]: c for c in r["challenges"]}
    assert set(ch) == {"habit", "perfect", "flames"}
    assert (ch["habit"]["habit"], ch["habit"]["target"], ch["habit"]["bonus"]) == ("B", 3, 15)
    # two perfect days last week (the 14th, before B was tracked, and the 15th) + 1
    assert ch["perfect"]["target"] == 3
    assert ch["flames"]["target"] == 10
    # B three times this week → challenge done, 15 bonus flames on that day
    _log(client, b, ["2026-09-21", "2026-09-22", "2026-09-23"])
    r = _rewards(client, date(2026, 9, 23))
    ch = {c["kind"]: c for c in r["challenges"]}
    assert ch["habit"]["completed_on"] == "2026-09-23" and ch["habit"]["progress"] == 3
    assert r["flames"]["by_day"]["2026-09-23"] >= 1 + 15
    assert "challenges:1" in {x["key"] for x in r["achievements"] if x["unlocked_on"]}
    # the same week is not generated again
    assert len(_rewards(client, date(2026, 9, 24))["challenges"]) == 3
    # weekly review facts carry the week's flames, challenges and new achievements
    rv = client.get("/api/v1/journal/week/2026-09-23", headers=AUTH).json()["facts"]["rewards"]
    assert rv["challenges"]["done"] >= 1 and rv["flames"] >= 16 and rv["achievements"]


def test_workout_minutes_habit(client, engine):
    body = {"name": "Training", "metric": "activities.minutes", "min_value": 20, "per_week": 3}
    assert client.post("/api/v1/habits", headers=AUTH, json=body).status_code == 201
    assert "activities.minutes" in {
        m["key"] for m in client.get("/api/v1/habits/metrics", headers=AUTH).json()
    }
    with engine.begin() as conn:
        for ts, minutes in (("2026-09-15T18:00:00+02:00", 45), ("2026-09-17T07:00:00+02:00", 10)):
            conn.execute(
                text("""INSERT INTO activity (device_id, start_ts, name, sport, elapsed_s, timer_s, manual,
                                              manual_key)
                        VALUES (NULL, :ts, 'x', 'training', :s, :s, true, gen_random_uuid())"""),
                {"ts": ts, "s": minutes * 60},
            )
    days = client.get(
        "/api/v1/habits/status", headers=AUTH, params={"from": "2026-09-14", "to": "2026-09-20"}
    ).json()[0]["days"]
    assert (days["2026-09-15"]["done"], days["2026-09-15"]["value"]) == (True, 45)
    assert (days["2026-09-17"]["done"], days["2026-09-17"]["value"]) == (False, 10)
    assert "2026-09-16" not in days  # no watch data, no workout: unknown


def test_supplement_some_days_a_week(client):
    daily = {"name": "Magnesium", "per_day": 1}
    weekly = {"name": "Vitamin D 20000", "per_day": 1, "per_week": 1}
    d = client.post("/api/v1/supplements", headers=AUTH, json=daily).json()
    w = client.post("/api/v1/supplements", headers=AUTH, json=weekly).json()
    assert w["per_week"] == 1
    for day in ("2026-09-14", "2026-09-15", "2026-09-16"):
        client.put(f"/api/v1/supplements/{d['id']}/log/{day}", headers=AUTH, json={"count": 1})
    client.put(f"/api/v1/supplements/{w['id']}/log/2026-09-15", headers=AUTH, json={"count": 1})
    hb = {"name": "Supplements", "metric": "supplements.taken", "min_value": 100}
    client.post("/api/v1/habits", headers=AUTH, json=hb)
    days = client.get(
        "/api/v1/habits/status", headers=AUTH, params={"from": "2026-09-14", "to": "2026-09-28"}
    ).json()[0]["days"]
    # the weekly one does not make the other days incomplete
    assert all(days[x]["done"] for x in ("2026-09-14", "2026-09-15", "2026-09-16"))
    # week of the 21st: weekly goal missed → counts as missed on Sunday the 27th
    assert days["2026-09-27"]["value"] == 0
    st = client.get("/api/v1/supplements/status", headers=AUTH, params={"day": "2026-09-16"}).json()
    vd = next(x for x in st["supplements"] if x["name"].startswith("Vitamin D"))
    assert (vd["done"], vd["week_count"], vd["week_done"], vd["streak_unit"]) == (False, 1, True, "weeks")
