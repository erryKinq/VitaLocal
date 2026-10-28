"""Training: exercises, plans, session review of a watch workout (surviving re-import), manual
workouts, rotation, progression, stats and facts."""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from tests.fixtures import make_export_zip
from vitalocal.ingest import service

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}
T = "/api/v1/training"


@pytest.fixture
def env(engine, store, tmp_path):
    from vitalocal.main import app

    batch = service.import_file(engine, store, make_export_zip(tmp_path / "export.zip", tmp_path))
    return TestClient(app), batch, engine, store


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    """The sessions below are in September 2026: "today" stays next to them (no pause suggestions)."""
    from vitalocal.api import training as api_training

    monkeypatch.setattr(api_training, "_today", lambda: date(2026, 9, 28))


def _post(client, path, body, code=201):
    r = client.post(f"{T}{path}", headers=AUTH, json=body)
    assert r.status_code == code, r.text
    return r.json()


def _setup(client):
    bench = _post(client, "/exercises", {"name": "Bankdrücken", "garmin_category": "bench_press"})
    row = _post(
        client, "/exercises", {"name": "Kurzhantel-Rudern", "garmin_category": "row", "increment_kg": 1}
    )
    plan = _post(client, "/plans", {
        "name": "Push/Pull", "active": True,
        "days": [
            {"name": "Push", "exercises": [{"exercise_id": bench["id"], "sets": 3, "reps_min": 8,
                                            "reps_max": 10,
                                            "target_weight_kg": 23}]},
            {"name": "Pull", "exercises": [{"exercise_id": row["id"], "sets": 3, "reps_min": 10,
                                            "reps_max": 12,
                                            "target_weight_kg": 12}]},
        ],
    })  # fmt: skip
    return bench, row, plan


def test_exercises_and_plans(env):
    client, *_ = env
    bench, row, plan = _setup(client)
    assert (bench["muscles"], bench["muscles_secondary"]) == (
        ["chest_lower"],
        ["chest_upper", "delts_front", "triceps"],
    )
    assert (row["muscles"], row["muscles_secondary"]) == (["lats", "upper_back"], ["delts_rear", "biceps"])
    # the old coarse muscles are taken and stand for their parts; the Garmin exercise sets the default
    old = _post(
        client,
        "/exercises",
        {"name": "Alt", "muscles": ["shoulders"], "muscles_secondary": ["chest", "delts_side"]},
    )
    assert (old["muscles"], old["muscles_secondary"]) == (
        ["delts_front", "delts_side", "delts_rear"],
        ["chest_upper", "chest_lower"],
    )
    face = _post(
        client, "/exercises", {"name": "Face Pull", "garmin_category": "row", "garmin_name": "face_pull"}
    )
    assert (face["muscles"], face["muscles_secondary"]) == (["delts_rear"], ["rotator_cuff", "upper_back"])
    assert _post(client, "/exercises", {"name": "Z", "muscles": ["wings"]}, 422)
    assert _post(client, "/exercises", {"name": "bankdrücken"}, 409)["detail"]
    assert _post(client, "/exercises", {"name": "X", "garmin_category": "nope"}, 422)
    assert _post(client, "/exercises", {"name": "Y", "garmin_category": "curl", "garmin_name": "bench"}, 422)
    assert _post(client, "/exercises", {"id": bench["id"], "name": "egal"}, 201)["name"] == "Bankdrücken"

    assert [d["name"] for d in plan["days"]] == ["Push", "Pull"] and plan["active"]
    push = plan["days"][0]
    # full state: rename a day, keep its id, drop the second day
    body = {"name": "Push/Pull", "active": True, "days": [{"id": push["id"], "name": "Push A",
            "exercises": [{"id": push["exercises"][0]["id"], "exercise_id": bench["id"],
                           "sets": 4}]}]}  # fmt: skip
    p = client.put(f"{T}/plans/{plan['id']}", headers=AUTH, json=body).json()
    assert [(d["id"], d["name"]) for d in p["days"]] == [(push["id"], "Push A")]
    assert p["days"][0]["exercises"][0]["sets"] == 4
    other = _post(client, "/plans", {"name": "Neu", "active": True})
    plans = client.get(f"{T}/plans", headers=AUTH).json()
    assert [(x["name"], x["active"]) for x in plans] == [("Neu", True), ("Push/Pull", False)]
    assert client.delete(f"{T}/exercises/{bench['id']}", headers=AUTH).status_code == 409  # used in a plan
    assert client.delete(f"{T}/plans/{other['id']}", headers=AUTH).status_code == 204
    sugg = client.get(f"{T}/exercises/suggestions", headers=AUTH).json()
    assert all(s["garmin_category"] != "bench_press" for s in sugg)  # covered by Bankdrücken
    muscles = client.get(f"{T}/muscles", headers=AUTH).json()
    assert len(muscles) == 24
    assert muscles[0] == {
        "key": "chest_upper",
        "label": "obere Brust",
        "group": "chest",
        "group_label": "Brust",
    }
    groups = client.get(f"{T}/muscles/groups", headers=AUTH).json()
    assert groups[1] == {
        "key": "shoulders",
        "label": "Schultern",
        "muscles": ["delts_front", "delts_side", "delts_rear", "rotator_cuff"],
    }


def test_review_of_a_watch_workout_survives_reimport(env):
    client, batch, engine, store = env
    bench, row, plan = _setup(client)
    sessions = client.get(f"{T}/sessions", headers=AUTH).json()
    assert len(sessions) == 1 and not sessions[0]["reviewed"]
    aid = sessions[0]["id"]

    s = client.get(f"{T}/sessions/{aid}", headers=AUTH).json()
    prop = s["proposal"]
    assert (prop["plan_day"], prop["reason"]) == ("Push", "rotation")  # first day, and bench press fits
    by = {p["key"]: p for p in prop["sets"]}
    assert [x["key"] for x in s["sets"]] == ["w:0", "w:2", "w:4"]
    assert (
        by["w:0"]["warmup"]
        and by["w:2"]["hidden"]  # 6 s with 9 reps: the watch artefact after the warm-up
        and by["w:4"]["exercise_id"] == bench["id"]
    )
    assert s["planned"][0]["current"] == "3×8 @ 23 kg"

    review = {
        "title": "Push am Sonntag",
        "plan_day_id": prop["plan_day_id"],
        "sets": [
            {"key": "w:0", "warmup": True},
            {"key": "w:2", "exercise_id": bench["id"], "repetitions": 9, "weight_kg": 23},
            {"key": "w:4", "exercise_id": bench["id"], "repetitions": 9, "weight_kg": 23},
            {
                "position": 5,
                "exercise_id": bench["id"],
                "repetitions": 8,
                "weight_kg": 23,
            },  # the watch missed it
        ],
        "ratings": [{"exercise_id": bench["id"], "rating": "good"}],
    }
    r = client.put(f"{T}/sessions/{aid}", headers=AUTH, json=review)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["reviewed"] and s["display_title"] == "Push am Sonntag" and "proposal" not in s
    assert s["exercises"] == [
        {"exercise_id": bench["id"], "plan_exercise_id": None, "exercise": "Bankdrücken", "sets": 3,
         "text": "9 @ 23 kg, 9 @ 23 kg, 8 @ 23 kg", "volume_kg": 598.0, "best_e1rm_kg": 29.9,
         "rating": "good"}
    ]  # fmt: skip
    manual_key = next(x["key"] for x in s["sets"] if x["source"] == "manual")
    assert client.put(f"{T}/sessions/{aid}", headers=AUTH, json={**review, "sets": review["sets"][:3] + [
        {"key": manual_key, "position": 5, "exercise_id": bench["id"], "repetitions": 8, "weight_kg": 23}]}
    ).json()["exercises"][0]["sets"] == 3  # repeat with the key: still one added set  # fmt: skip

    # the workout as a template for a new plan day
    tpl = client.get(f"{T}/sessions/{aid}/as-plan-day", headers=AUTH).json()
    assert tpl["name"] == "Push am Sonntag"
    assert tpl["exercises"] == [
        {"exercise_id": bench["id"], "exercise": "Bankdrücken", "sets": 3, "reps_min": 8, "reps_max": 9,
         "target_reps": 8, "target_weight_kg": 23.0, "duration_s": None, "rest_s": None, "note": None}
    ]  # fmt: skip
    p = _post(client, "/plans", {"name": "Aus Training", "days": [tpl]})
    assert p["days"][0]["exercises"][0]["sets"] == 3

    # re-import: sets are replaced from the FIT file, the review stays
    service.process_batch(engine, store, batch)
    again = client.get(f"{T}/sessions/{aid}", headers=AUTH).json()
    assert again["reviewed"] and again["title"] == "Push am Sonntag"
    assert again["exercises"][0]["text"] == "9 @ 23 kg, 9 @ 23 kg, 8 @ 23 kg"
    act = client.get(f"/api/v1/activities/{aid}", headers=AUTH).json()
    assert act["title"] == "Push am Sonntag" and act["reviewed"]

    # rotation: Pull is next; Push's bench press would go to 3×9 @ 23 kg (good, 8 → 9 reps)
    nxt = client.get(f"{T}/next", headers=AUTH).json()
    assert nxt["plan_day"] == "Pull" and nxt["exercises"][0]["current"] == "3×10 @ 12 kg"
    push = next(d for d in client.get(f"{T}/plans", headers=AUTH).json()[0]["days"] if d["name"] == "Push")
    pe = push["exercises"][0]

    # a manual Pull workout moves the rotation back to Push
    key = str(uuid.uuid4())
    manual = {"manual_key": key, "start_ts": "2026-09-23T18:00:00+02:00", "duration_min": 45,
              "plan_day_id": nxt["plan_day_id"], "ratings": [{"exercise_id": row["id"], "rating": "easy"}],
              "sets": [{"exercise_id": row["id"], "repetitions": 12, "weight_kg": 12}] * 3}  # fmt: skip
    m = _post(client, "/sessions", manual)
    assert m["activity"]["manual"] and m["exercises"][0]["text"] == "12 @ 12 kg, 12 @ 12 kg, 12 @ 12 kg"
    assert _post(client, "/sessions", manual)["activity_id"] == m["activity_id"]  # idempotent
    nxt = client.get(f"{T}/next", headers=AUTH).json()
    assert nxt["plan_day"] == "Push"
    bench_next = nxt["exercises"][0]
    assert (bench_next["current"], bench_next["next"]["text"], bench_next["differs"]) == (
        "3×8 @ 23 kg", "3×9 @ 23 kg", True)  # fmt: skip
    assert bench_next["last"] == {"date": s["activity"]["start_ts"][:10], "rating": "good",
                                  "sets": ["9 @ 23 kg", "9 @ 23 kg", "8 @ 23 kg"]}  # fmt: skip

    # decline it: the target stays and is what the next workout does; offered again on request
    def dec(s):
        return client.put(f"{T}/plan-exercises/{pe['id']}/declined", headers=AUTH, json={"suggestion": s})

    assert dec("3×9 @ 23 kg").status_code == 200
    b = client.get(f"{T}/next", headers=AUTH).json()["exercises"][0]
    assert (b["differs"], b["declined"], b["next"]["text"], b["next"]["reps"]) == (
        False,
        "3×9 @ 23 kg",
        "3×8 @ 23 kg",
        8,
    )
    assert dec("3×10 @ 23 kg").status_code == 200  # an older or other suggestion: this one shows
    assert client.get(f"{T}/next", headers=AUTH).json()["exercises"][0]["differs"] is True
    dec("3×9 @ 23 kg")
    assert dec(None).status_code == 200
    assert client.get(f"{T}/next", headers=AUTH).json()["exercises"][0]["differs"] is True
    # take it over
    r = client.put(f"{T}/plan-exercises/{pe['id']}/target", headers=AUTH,
                   json={"target_reps": 9, "target_weight_kg": 23})  # fmt: skip
    assert r.status_code == 200
    assert client.get(f"{T}/next", headers=AUTH).json()["exercises"][0]["differs"] is False

    # stats and facts
    st = client.get(f"{T}/stats", headers=AUTH, params={"weeks": 104}).json()
    weeks = [w for w in st["weeks"] if w["sessions"]]
    assert weeks[0]["sessions"] == 2  # push on the 21st, pull on the 23rd
    assert weeks[0]["hard_sets"] == {
        "chest_lower": 3, "lats": 3, "upper_back": 3, "chest_upper": 1.5, "delts_front": 1.5, "triceps": 1.5,
        "delts_rear": 1.5, "biceps": 1.5,
    }  # fmt: skip
    assert st["e1rm"][0]["exercise"] == "Bankdrücken" and st["e1rm"][0]["points"][0]["e1rm_kg"] == 29.9
    facts = client.get(f"{T}/facts", headers=AUTH, params={"day": s["activity"]["start_ts"][:10]}).json()
    sess = facts["sessions_last_28_days"][0]
    assert sess["title"] == "Push am Sonntag" and sess["exercises"][0]["rating"] == "good"
    assert facts["next_workout"]["plan_day"] == "Push"

    # removing the review of a watch workout keeps the activity; a manual workout is deleted
    assert client.delete(f"{T}/sessions/{aid}", headers=AUTH).status_code == 204
    s = client.get(f"{T}/sessions/{aid}", headers=AUTH).json()
    assert not s["reviewed"] and s["title"] is None and len(s["sets"]) == 3
    assert client.delete(f"{T}/sessions/{m['activity_id']}", headers=AUTH).status_code == 204
    assert client.get(f"{T}/sessions/{m['activity_id']}", headers=AUTH).status_code == 404


def test_a_workout_reviewed_before_any_exercise_existed_gets_proposals(env):
    client, *_ = env
    aid = client.get(f"{T}/sessions", headers=AUTH).json()[0]["id"]
    s = client.put(
        f"{T}/sessions/{aid}",
        headers=AUTH,
        json={"title": "Push-Day", "sets": [{"key": "w:0", "warmup": True}]},
    )
    s = s.json()
    assert s["reviewed"] and s["unassigned_sets"] == 2 and s["proposal"]["only_unassigned"]
    listed = client.get(f"{T}/sessions", headers=AUTH).json()[0]
    assert listed["reviewed"] and listed["unassigned"] == 2
    bench = _post(client, "/exercises", {"name": "Bankdrücken", "garmin_category": "bench_press"})
    s = client.get(f"{T}/sessions/{aid}", headers=AUTH).json()
    assert {p["key"]: p["exercise_id"] for p in s["proposal"]["sets"]}["w:4"] == bench["id"]
    s = client.put(f"{T}/sessions/{aid}", headers=AUTH, json={"title": "Push-Day", "sets": [
        {"key": "w:2", "hidden": True}, {"key": "w:4", "exercise_id": bench["id"]}]}).json()  # fmt: skip
    assert s["unassigned_sets"] == 0 and "proposal" not in s and s["title"] == "Push-Day"
    assert client.get(f"{T}/sessions", headers=AUTH).json()[0]["unassigned"] == 0


def test_validation_and_summary_facts(env):
    client, *_ = env
    bench, row, plan = _setup(client)
    aid = client.get(f"{T}/sessions", headers=AUTH).json()[0]["id"]
    assert client.get(f"{T}/sessions/{aid}/as-plan-day", headers=AUTH).status_code == 422  # nothing assigned
    bad = client.put(f"{T}/sessions/{aid}", headers=AUTH, json={"sets": [{"key": "w:99"}]})
    assert bad.status_code == 422
    bad = client.put(
        f"{T}/sessions/{aid}", headers=AUTH, json={"ratings": [{"exercise_id": bench["id"], "rating": "meh"}]}
    )
    assert bad.status_code == 422
    bad = client.put(f"{T}/sessions/{aid}", headers=AUTH, json={"plan_day_id": str(uuid.uuid4())})
    assert bad.status_code == 422

    from datetime import date

    from vitalocal import ai_facts
    from vitalocal.db import get_engine

    with get_engine().connect() as conn:
        day = ai_facts.day_facts(conn, date(2026, 9, 21))
        week = ai_facts.week_facts(conn, date(2026, 9, 21))
    tr = day["strength_training"]
    assert tr["sessions_last_7_days"][0]["reviewed"] is False
    assert tr["next_workout"]["plan_day"] == "Push"
    assert week["strength_training"]["next_workout"]["exercises"][0]["target"] == "3×8 @ 23 kg"


def test_the_same_exercise_twice_on_a_day_progresses_per_entry(env):
    client, *_ = env
    curl = _post(client, "/exercises", {"name": "Curl", "garmin_category": "curl", "increment_kg": 1})
    plan = _post(client, "/plans", {"name": "Arme", "active": True, "days": [{"name": "Arme", "exercises": [
        {"exercise_id": curl["id"], "sets": 3, "reps_min": 8, "reps_max": 10, "target_weight_kg": 15},
        {"exercise_id": curl["id"], "sets": 2, "reps_min": 12, "reps_max": 15, "target_weight_kg": 10},
    ]}]})  # fmt: skip
    day = plan["days"][0]
    heavy, light = (e["id"] for e in day["exercises"])
    sets = [{"exercise_id": curl["id"], "plan_exercise_id": heavy, "repetitions": 8, "weight_kg": 15}] * 3 + [
        {"exercise_id": curl["id"], "plan_exercise_id": light, "repetitions": 12, "weight_kg": 10}
    ] * 2
    s = _post(client, "/sessions", {
        "manual_key": str(uuid.uuid4()), "start_ts": "2026-09-25T18:00:00+02:00", "duration_min": 30,
        "plan_day_id": day["id"], "sets": sets,
        "ratings": [{"exercise_id": curl["id"], "plan_exercise_id": heavy, "rating": "good"},
                    {"exercise_id": curl["id"], "plan_exercise_id": light, "rating": "easy"}],
    })  # fmt: skip
    assert [(e["plan_exercise_id"], e["text"], e["rating"]) for e in s["exercises"]] == [
        (heavy, "8 @ 15 kg, 8 @ 15 kg, 8 @ 15 kg", "good"), (light, "12 @ 10 kg, 12 @ 10 kg", "easy"),
    ]  # fmt: skip
    nxt = client.get(f"{T}/next", headers=AUTH).json()
    assert [e["next"]["text"] for e in nxt["exercises"]] == ["3×9 @ 15 kg", "2×12 @ 11 kg"]
    targets = client.get(f"{T}/plan-days/{day['id']}/targets", headers=AUTH).json()
    assert [(e["plan_exercise_id"], e["last"]["rating"]) for e in targets] == [
        (heavy, "good"),
        (light, "easy"),
    ]
    assert client.get(f"{T}/plan-days/{uuid.uuid4()}/targets", headers=AUTH).status_code == 404
    unknown = {"sets": [{"exercise_id": curl["id"], "plan_exercise_id": str(uuid.uuid4())}]}
    bad = client.put(f"{T}/sessions/{s['activity_id']}", headers=AUTH, json=unknown)
    assert bad.status_code == 422


def test_template_splits_an_exercise_done_in_two_blocks(env):
    client, *_ = env
    curl = _post(client, "/exercises", {"name": "Curl", "garmin_category": "curl"})
    row = _post(client, "/exercises", {"name": "Rudern", "garmin_category": "row"})
    sets = [(curl, 10, 11.5), (curl, 9, 11.5), (row, 8, 28.5), (curl, 8, 15.5), (curl, 8, 15.5)]
    s = _post(client, "/sessions", {
        "manual_key": str(uuid.uuid4()), "start_ts": "2026-09-26T18:00:00+02:00", "duration_min": 30,
        "title": "Arme",
        "sets": [{"exercise_id": e["id"], "repetitions": r, "weight_kg": w} for e, r, w in sets],
    })  # fmt: skip
    tpl = client.get(f"{T}/sessions/{s['activity_id']}/as-plan-day", headers=AUTH).json()
    got = [
        (e["exercise"], e["sets"], e["reps_min"], e["reps_max"], e["target_weight_kg"])
        for e in tpl["exercises"]
    ]
    assert got == [("Curl", 2, 9, 10, 11.5), ("Rudern", 1, 8, 8, 28.5), ("Curl", 2, 8, 8, 15.5)]


def test_loaded_holds_history_ratings_and_current_plan(env):
    client, *_ = env
    walk = _post(client, "/exercises", {"name": "Farmers walk", "garmin_category": "carry", "kind": "time"})
    plan = _post(client, "/plans", {"name": "Grip", "active": True, "days": [{"name": "A", "exercises": [
        {"exercise_id": walk["id"], "sets": 3, "reps_min": 30, "duration_s": 40, "target_weight_kg": 30},
    ]}]})  # fmt: skip
    day = plan["days"][0]
    pe = day["exercises"][0]["id"]
    ids = []
    for date_, reps in (("2026-09-20", 0), ("2026-09-27", 1)):  # the watch records 0 or 1 "reps"
        s = _post(client, "/sessions", {
            "manual_key": str(uuid.uuid4()), "start_ts": f"{date_}T18:00:00+02:00", "duration_min": 20,
            "plan_day_id": day["id"],
            "sets": [{"exercise_id": walk["id"], "plan_exercise_id": pe, "repetitions": reps,
                      "weight_kg": 30.5, "duration_s": d} for d in (60, 45, 45)],
        })  # fmt: skip
        ids.append(s["activity_id"])

    # seconds, not reps; the load stays; taking the suggestion over makes it the current target
    e = client.get(f"{T}/next", headers=AUTH).json()["exercises"][0]
    assert (e["current"], e["next"]["text"], e["last"]["sets"]) == ("3×40 s @ 30 kg", "3×50 s @ 30.5 kg",
                                                                    ["60 s @ 30.5 kg", "45 s @ 30.5 kg",
                                                                     "45 s @ 30.5 kg"])  # fmt: skip
    nx = e["next"]
    body = {"target_reps": nx["reps"], "target_weight_kg": nx["weight_kg"], "duration_s": nx["duration_s"]}
    assert client.put(f"{T}/plan-exercises/{pe}/target", headers=AUTH, json=body).status_code == 200
    e = client.get(f"{T}/next", headers=AUTH).json()["exercises"][0]
    assert e["current"] == "3×50 s @ 30.5 kg" and not e["differs"]

    # every session counts for the progression chart (no 1RM from a hold)
    st = client.get(f"{T}/stats", headers=AUTH, params={"weeks": 1, "progress_weeks": 520}).json()
    assert all(x["exercise"] != "Farmers walk" for x in st["e1rm"])
    (hold,) = st["holds"]
    assert [(p["date"], p["duration_s"], p["weight_kg"]) for p in hold["points"]] == [
        ("2026-09-20", 60, 30.5), ("2026-09-27", 60, 30.5),
    ]  # fmt: skip
    assert len(st["weeks"]) == 1

    # paged history with a date filter
    page = client.get(f"{T}/history", headers=AUTH, params={"limit": 1}).json()
    assert page["total"] >= 3 and len(page["items"]) == 1
    page = client.get(f"{T}/history", headers=AUTH, params={"from": "2026-09-19", "to": "2026-09-26"}).json()
    got = [x["id"] for x in page["items"]]
    assert ids[0] in got and ids[1] not in got and page["total"] == len(got)

    # ratings alone, without touching sets or the review
    r = client.put(
        f"{T}/sessions/{ids[1]}/ratings",
        headers=AUTH,
        json={"ratings": [{"exercise_id": walk["id"], "plan_exercise_id": pe, "rating": "easy"}]},
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["exercises"][0]["rating"] == "easy" and len(detail["sets"]) == 3 and detail["reviewed"]
    assert client.put(f"{T}/sessions/999999/ratings", headers=AUTH, json={"ratings": []}).status_code == 404

    cur = client.get(f"{T}/plan/current", headers=AUTH).json()
    assert cur["name"] == "Grip" and cur["next_day_id"] == day["id"]
    # too easy: shortest set + 10 s
    assert cur["days"][0]["exercises"][0]["next"]["text"] == "3×55 s @ 30.5 kg"


def test_session_suggests_a_rating_from_the_sets(env):
    client, *_ = env
    bench, row, plan = _setup(client)
    aid = client.get(f"{T}/sessions", headers=AUTH).json()[0]["id"]
    s = client.get(f"{T}/sessions/{aid}", headers=AUTH).json()
    pe = s["planned"][0]["plan_exercise_id"]  # 3×8 @ 23 kg
    sets = [
        {"key": "w:0", "warmup": True},
        {"key": "w:2", "exercise_id": bench["id"], "plan_exercise_id": pe, "repetitions": 8, "weight_kg": 23},
        {"key": "w:4", "exercise_id": bench["id"], "plan_exercise_id": pe, "repetitions": 8, "weight_kg": 23},
        {
            "position": 5,
            "exercise_id": bench["id"],
            "plan_exercise_id": pe,
            "repetitions": 6,
            "weight_kg": 23,
        },
    ]
    body = {"plan_day_id": s["proposal"]["plan_day_id"], "sets": sets, "ratings": []}
    e = client.put(f"{T}/sessions/{aid}", headers=AUTH, json=body).json()["exercises"][0]
    assert e["planned"] == "3×8 @ 23 kg"
    assert e["suggestion"] == {"rating": "hard", "why": "target missed"} and e["rating"] is None
    sets[3]["repetitions"] = 8
    e = client.put(f"{T}/sessions/{aid}", headers=AUTH, json=body).json()["exercises"][0]
    assert e["suggestion"]["rating"] == "good"


def test_skipping_a_plan_day_moves_the_rotation_and_shows_in_the_history(env):
    client, *_ = env
    _bench, _row, plan = _setup(client)
    pull = plan["days"][1]
    assert client.get(f"{T}/next", headers=AUTH).json()["plan_day"] == "Push"
    skip = _post(client, "/skips", {"note": "krank"})
    assert (skip["plan_day"], skip["note"]) == ("Push", "krank")
    assert client.get(f"{T}/next", headers=AUTH).json()["plan_day"] == "Pull"
    items = client.get(f"{T}/history", headers=AUTH).json()["items"]
    assert [x["kind"] for x in items] == ["skip", "session"] and items[0]["plan_day"] == "Push"
    assert _post(client, "/skips", {"plan_day_id": str(uuid.uuid4())}, 422)
    assert client.delete(f"{T}/skips/{skip['id']}", headers=AUTH).status_code == 204
    assert client.delete(f"{T}/skips/{skip['id']}", headers=AUTH).status_code == 404
    assert client.get(f"{T}/next", headers=AUTH).json()["plan_day"] == "Push"
    # the review proposes for the day picked there
    aid = items[1]["id"]
    s = client.get(f"{T}/sessions/{aid}", headers=AUTH, params={"plan_day_id": pull["id"]}).json()
    assert (s["proposal"]["plan_day"], s["proposal"]["reason"]) == ("Pull", "chosen")
    assert [p["exercise"] for p in s["planned"]] == ["Kurzhantel-Rudern"]


def test_a_taken_over_target_keeps_its_sets(env):
    client, *_ = env
    _bench, _row, plan = _setup(client)
    pe = plan["days"][0]["exercises"][0]
    put = lambda body: client.put(f"{T}/plan-exercises/{pe['id']}/target", headers=AUTH, json=body)  # noqa: E731
    assert put({"target_sets": 4, "target_reps": 7, "target_weight_kg": 23}).status_code == 200
    assert client.get(f"{T}/next", headers=AUTH).json()["exercises"][0]["current"] == "4×7 @ 23 kg"
    put({"target_sets": 3, "target_reps": 9, "target_weight_kg": 23})  # the entry's own sets: stored as none
    days = client.get(f"{T}/plans", headers=AUTH).json()[0]["days"]
    assert days[0]["exercises"][0]["target_sets"] is None


def test_dumbbell_exercises_progress_along_the_plates(env):
    client, *_ = env
    p = client.get(f"{T}/plates", headers=AUTH).json()
    assert p["handle_kg"] == 0.5 and {"kg": 1.25, "count": 8} in p["plates"]
    assert [w for w in p["weights"] if 13 <= w <= 16] == [13.0, 13.5, 14.0, 14.5, 15.5]
    curl = _post(client, "/exercises", {"name": "Curl", "garmin_category": "curl", "dumbbells": 1})
    assert curl["dumbbells"] == 1
    bad = client.put(f"{T}/exercises/{curl['id']}", headers=AUTH, json={**curl, "dumbbells": 3})
    assert bad.status_code == 422
    plan = _post(client, "/plans", {"name": "Arme", "active": True, "days": [{"name": "Arme", "exercises": [
        {"exercise_id": curl["id"], "sets": 3, "reps_min": 8, "reps_max": 12, "target_reps": 12,
         "target_weight_kg": 14},
    ]}]})  # fmt: skip
    day = plan["days"][0]
    _post(client, "/sessions", {
        "manual_key": str(uuid.uuid4()), "start_ts": "2026-09-25T18:00:00+02:00", "duration_min": 30,
        "plan_day_id": day["id"],
        "sets": [{"exercise_id": curl["id"], "repetitions": 12, "weight_kg": 14}] * 3,
    })  # fmt: skip
    # 3×12 @ 14: the heaviest loadable weight where 8 reps are still expected (15.5: 7.9), one rep below
    nxt = client.get(f"{T}/next", headers=AUTH).json()["exercises"][0]["next"]
    assert nxt["text"] == "3×9 @ 14.5 kg"
    # the real handle (1.5 kg): other weights
    r = client.put(f"{T}/plates", headers=AUTH, json={**p, "handle_kg": 1.5})
    assert r.status_code == 200 and 15.0 in r.json()["weights"]
    assert client.get(f"{T}/next", headers=AUTH).json()["exercises"][0]["next"]["text"] == "3×8 @ 15 kg"


def test_bodyweight_exercises_count_the_body_in_the_1rm_chart(env):
    client, *_ = env
    pull = _post(client, "/exercises", {"name": "Klimmzug", "garmin_category": "pull_up", "bodyweight": True})
    for date_, w in (("2026-09-20", None), ("2026-09-27", 15)):
        _post(client, "/sessions", {
            "manual_key": str(uuid.uuid4()), "start_ts": f"{date_}T18:00:00+02:00", "duration_min": 20,
            "sets": [{"exercise_id": pull["id"], "repetitions": 6, "weight_kg": w}] * 3,
        })  # fmt: skip
    params = {"weeks": 1, "progress_weeks": 520}
    # no body weight known: no series
    st = client.get(f"{T}/stats", headers=AUTH, params=params).json()
    assert all(x["exercise"] != "Klimmzug" for x in st["e1rm"])
    # body weight before the session (the latest one counts) plus the extra load
    for ts, kg in (("2026-09-01T07:00:00+02:00", 70), ("2026-09-27T07:00:00+02:00", 65)):
        r = client.post("/api/v1/body", headers=AUTH, json={"id": str(uuid.uuid4()), "ts": ts, "value": kg})
        assert r.status_code == 201, r.text
    st = client.get(f"{T}/stats", headers=AUTH, params=params).json()
    (s,) = [x for x in st["e1rm"] if x["exercise"] == "Klimmzug"]
    assert s["bodyweight"] and [(p["date"], p["e1rm_kg"]) for p in s["points"]] == [
        ("2026-09-20", 84.0), ("2026-09-27", 96.0),
    ]  # fmt: skip


def test_library_entries_become_own_exercises(env):
    client, *_ = env
    assert {"key": "dumbbell", "label": "Kurzhanteln"}.items() <= next(
        e for e in client.get(f"{T}/equipment", headers=AUTH).json() if e["key"] == "dumbbell"
    ).items()
    lib = client.get(
        f"{T}/library", headers=AUTH, params={"muscle": "chest", "equipment": ["dumbbell", "bench"]}
    )
    assert lib.status_code == 200
    keys = [e["key"] for e in lib.json()]
    assert "dumbbell_bench_press" in keys and "push_up" in keys and "barbell_bench_press" not in keys
    assert client.get(f"{T}/library", headers=AUTH, params={"equipment": "anvil"}).status_code == 422
    bare = client.get(f"{T}/library", headers=AUTH, params={"equipment": ""}).json()
    assert bare and all(not e["equipment"] for e in bare)

    # taken over once, then the same exercise again
    ex = _post(client, "/library/dumbbell_curl/adopt", None, 201)
    assert (ex["name"], ex["library_key"], ex["equipment"]) == (
        "Kurzhantel-Curl",
        "dumbbell_curl",
        ["dumbbell"],
    )
    assert (ex["garmin_category"], ex["garmin_name"], ex["muscles"]) == (
        "curl",
        "dumbbell_biceps_curl",
        ["biceps"],
    )
    assert _post(client, "/library/dumbbell_curl/adopt", None, 200)["id"] == ex["id"]
    lib = client.get(f"{T}/library", headers=AUTH, params={"q": "Kurzhantel-Curl"}).json()
    (entry,) = [e for e in lib if e["key"] == "dumbbell_curl"]
    assert entry["exercise_id"] == ex["id"]
    assert _post(client, "/library/nope/adopt", None, 404)

    # an own exercise for the same watch exercise is linked, keeps its name and gets the equipment
    own = _post(client, "/exercises", {"name": "KH Bank", "garmin_category": "bench_press",
                                       "garmin_name": "dumbbell_bench_press", "archived": True})  # fmt: skip
    assert own["equipment"] == [] and own["library_key"] is None
    linked = _post(client, "/library/dumbbell_bench_press/adopt", None, 200)
    assert (linked["id"], linked["name"], linked["archived"]) == (own["id"], "KH Bank", False)
    assert linked["equipment"] == ["dumbbell", "bench"]

    # equipment is kept when an update leaves it out; a library key is used once
    body = {
        k: linked[k]
        for k in ("name", "garmin_category", "garmin_name", "kind", "muscles", "muscles_secondary")
    }
    r = client.put(f"{T}/exercises/{linked['id']}", headers=AUTH, json=body)
    assert r.status_code == 200 and r.json()["equipment"] == ["dumbbell", "bench"]
    r = client.put(f"{T}/exercises/{linked['id']}", headers=AUTH, json={**body, "equipment": ["dumbbell"]})
    assert r.json()["equipment"] == ["dumbbell"]
    assert _post(client, "/exercises", {"name": "Z", "library_key": "dumbbell_curl"}, 409)
    assert _post(client, "/exercises", {"name": "Z", "equipment": ["anvil"]}, 422)


def test_muscle_recovery_volume_and_what_exercises_train(env):
    client, *_ = env
    bench, row, plan = _setup(client)
    s = _post(client, "/sessions", {
        "manual_key": str(uuid.uuid4()), "start_ts": "2026-09-21T10:00:00+02:00", "duration_min": 30,
        "sets": [{"exercise_id": row["id"], "repetitions": 10, "weight_kg": 12}] * 6,
    })  # fmt: skip
    r = client.put(f"{T}/sessions/{s['activity_id']}/ratings", headers=AUTH,
                   json={"ratings": [{"exercise_id": row["id"], "rating": "hard"}]})  # fmt: skip
    assert r.status_code == 200, r.text

    r = client.get(f"{T}/muscles/state", headers=AUTH, params={"at": "2026-09-21T20:00:00+02:00"})
    assert r.status_code == 200, r.text
    st = r.json()
    m = {x["key"]: x for x in st["muscles"]}
    assert len(m) == 24 + 3 and st["thresholds"] == {"ready": 0.25, "fatigued": 0.5}
    # the old app's coarse muscles: the most fatigued part
    assert {k for k, x in m.items() if x.get("alias")} == {"chest", "shoulders", "back"}
    assert m["back"]["fatigue"] == m["lats"]["fatigue"] and m["back"]["label"] == "Rücken und Nacken"
    # the manual rows (reviewed, rated too hard): 6 hard sets, unaccustomed → gain 1.5, 10 h ago
    back = m["lats"]
    assert back["state"] == "fatigued" and back["ready_in_h"] > 0
    assert back["fatigue"] == pytest.approx(6 / 8 * 1.5 * 0.5 ** (10 / 24), abs=0.01)
    assert [(x["id"], x["provisional"], x["exercises"]) for x in back["sessions"]] == [
        (s["activity_id"], False, ["Kurzhantel-Rudern"])
    ]
    assert back["hard_sets_7d"] == 6 and m["biceps"]["hard_sets_7d"] == 3
    # the watch workout is not reviewed: its bench set counts with the proposed exercise
    chest = m["chest_lower"]
    assert chest["fatigue"] > 0 and chest["sessions"][0]["provisional"]
    assert chest["sessions"][0]["exercises"] == ["Bankdrücken"]
    assert m["quads"] == {**m["quads"], "fatigue": 0.0, "state": "ready", "last_trained": None}
    # before both workouts: nothing
    early = client.get(f"{T}/muscles/state", headers=AUTH, params={"at": "2026-09-20T12:00:00+02:00"}).json()
    assert all(x["fatigue"] == 0 for x in early["muscles"])

    vol = client.get(f"{T}/muscles/volume", headers=AUTH, params={"weeks": 8}).json()
    v = {x["key"]: x for x in vol["muscles"]}
    assert (v["lats"]["hard_sets"], v["biceps"]["hard_sets"], v["lats"]["per_week"]) == (6, 3, 0.8)

    of = client.get(f"{T}/muscles/of", headers=AUTH, params={"exercise_id": bench["id"]}).json()
    trains = {"chest_lower": 1, "chest_upper": 0.5, "delts_front": 0.5, "triceps": 0.5}
    assert {x["key"]: x["hard_sets"] for x in of["muscles"]} == trains
    push = plan["days"][0]["id"]
    of = client.get(f"{T}/muscles/of", headers=AUTH, params={"plan_day_id": push}).json()
    assert {x["key"]: x["hard_sets"] for x in of["muscles"]} == {k: v * 3 for k, v in trains.items()}
    assert client.get(f"{T}/muscles/of", headers=AUTH).status_code == 422
    assert (
        client.get(f"{T}/muscles/of", headers=AUTH, params={"plan_day_id": str(uuid.uuid4())}).status_code
        == 404
    )

    facts = client.get(f"{T}/facts", headers=AUTH, params={"day": "2026-09-21", "days": 7}).json()
    assert facts["muscle_recovery"]["not_ready"]["lats"]["state"] == "fatigued"


def test_a_workout_entered_by_hand_merges_into_the_watch_recording(env):
    client, *_ = env
    bench, row, plan = _setup(client)
    (watch,) = [s for s in client.get(f"{T}/sessions", headers=AUTH).json() if not s["manual"]]
    push = plan["days"][0]
    # logged in the app while the watch recorded the same workout (watch: 2026-09-21 14:13 UTC)
    m = _post(client, "/sessions", {
        "manual_key": str(uuid.uuid4()), "start_ts": "2026-09-21T16:05:00+02:00", "duration_min": 40,
        "title": "Push im Studio", "plan_day_id": push["id"],
        "sets": [{"exercise_id": bench["id"], "repetitions": 9, "weight_kg": 23}] * 3,
        "ratings": [{"exercise_id": bench["id"], "rating": "good"}],
    })  # fmt: skip
    assert [o["id"] for o in m["overlaps"]] == [watch["id"]]
    w = client.get(f"{T}/sessions/{watch['id']}", headers=AUTH).json()
    assert [o["id"] for o in w["overlaps"]] == [m["activity_id"]]

    r = client.post(f"{T}/sessions/{watch['id']}/merge", headers=AUTH, json={"into": m["activity_id"]})
    assert r.status_code == 422  # only hand-entered into watch
    r = client.post(f"{T}/sessions/{m['activity_id']}/merge", headers=AUTH, json={"into": watch["id"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["activity_id"], d["reviewed"], d["title"], d["plan_day_id"]) == (
        watch["id"],
        True,
        "Push im Studio",
        push["id"],
    )
    work = [s for s in d["sets"] if not s["hidden"]]
    assert [(s["source"], s["repetitions"], s["weight_kg"]) for s in work] == [("manual", 9, 23)] * 3
    assert all(s["hidden"] for s in d["sets"] if s["source"] == "watch")
    assert d["ratings"] == [{"exercise_id": bench["id"], "plan_exercise_id": None, "rating": "good"}]
    assert d["overlaps"] == []
    assert client.get(f"{T}/sessions/{m['activity_id']}", headers=AUTH).status_code == 404

    # a workout entered by hand on another day does not overlap
    other = _post(client, "/sessions", {
        "manual_key": str(uuid.uuid4()), "start_ts": "2026-09-23T18:00:00+02:00", "duration_min": 30,
        "sets": [{"exercise_id": row["id"], "repetitions": 10, "weight_kg": 12}],
    })  # fmt: skip
    assert other["overlaps"] == []
    r = client.post(f"{T}/sessions/{other['activity_id']}/merge", headers=AUTH, json={"into": watch["id"]})
    assert r.status_code == 422


def test_deload_week(env):
    client, *_ = env
    bench, row, plan = _setup(client)
    push, pull = plan["days"]
    pe = push["exercises"][0]

    def session(day, reps, rating, d, plan_day=push):
        return _post(
            client,
            "/sessions",
            {
                "manual_key": str(uuid.uuid4()),
                "start_ts": f"{d}T18:00:00+02:00",
                "duration_min": 40,
                "plan_day_id": plan_day["id"],
                "sets": [
                    {"exercise_id": day["id"], "repetitions": reps, "weight_kg": 23 if day is bench else 12}
                ]
                * 3,
                "ratings": [{"exercise_id": day["id"], "rating": rating}],
            },
        )

    # bench and rows rated too hard twice in a row → a deload is suggested
    for d in ("2026-09-15", "2026-09-22"):
        session(bench, 8, "hard", d)
        session(row, 10, "hard", d, pull)
    dl = client.get(f"{T}/deload", headers=AUTH).json()
    assert dl["suggested"] and dl["reasons"][0] == {
        "key": "stalls",
        "exercises": ["Bankdrücken", "Kurzhantel-Rudern"],
    }
    before = client.get(f"{T}/plan-days/{push['id']}/targets", headers=AUTH).json()[0]

    # the deload week (from "today", 2026-09-28): one set less, same weight, bottom of the range
    dl = _post(client, "/deload", {}, 200)
    assert (dl["active"], dl["from"], dl["until"], dl["suggested"]) == (
        True,
        "2026-09-28",
        "2026-10-04",
        False,
    )
    t = client.get(f"{T}/plan-days/{push['id']}/targets", headers=AUTH).json()[0]
    assert (t["deload"], t["next"]["sets"], t["next"]["reps"], t["next"]["weight_kg"], t["differs"]) == (
        True, before["next"]["sets"] - 1, 8, before["next"]["weight_kg"], False)  # fmt: skip
    assert t["regular"] == before["next"]["text"]
    assert client.get(f"{T}/next", headers=AUTH).json()["deload"]["active"]

    # a workout in the deload week is marked and left out of the progression
    s = session(bench, 8, "easy", "2026-09-29")
    assert s["deload"] is True
    assert client.get(f"{T}/sessions/{s['activity_id']}", headers=AUTH).json()["deload"]
    dl = client.delete(f"{T}/deload", headers=AUTH).json()
    assert dl["active"] is False and dl["until"] == "2026-09-27" or dl["from"] is None
    after = client.get(f"{T}/plan-days/{push['id']}/targets", headers=AUTH).json()[0]
    assert (after["deload"], after["next"]) == (
        False,
        before["next"],
    )  # the easy deload session did not count

    # a plan may plan deloads; plan writes keep the interval
    body = {"name": "Push/Pull", "active": True, "deload_every_weeks": 6, "days": [
        {"id": push["id"], "name": "Push", "exercises": [
            {"id": pe["id"], "exercise_id": bench["id"], "sets": 3, "reps_min": 8,
             "reps_max": 10}]}]}  # fmt: skip
    p = client.put(f"{T}/plans/{plan['id']}", headers=AUTH, json=body).json()
    assert p["deload_every_weeks"] == 6
    assert (
        client.put(
            f"{T}/plans/{plan['id']}", headers=AUTH, json={**body, "deload_every_weeks": 1}
        ).status_code
        == 422
    )


def test_structural_balance(env):
    client, *_ = env
    bench, row, plan = _setup(client)
    for d in ("2026-09-20", "2026-09-24"):
        _post(client, "/sessions", {
            "manual_key": str(uuid.uuid4()), "start_ts": f"{d}T18:00:00+02:00", "duration_min": 40,
            "sets": [{"exercise_id": bench["id"], "repetitions": 8, "weight_kg": 40}] * 4
            + [{"exercise_id": row["id"], "repetitions": 10, "weight_kg": 15}] * 2,
        })  # fmt: skip
    b = client.get(f"{T}/balance", headers=AUTH, params={"weeks": 2}).json()
    v = {x["key"]: x for x in b["volume"]}
    assert (v["horizontal"]["a_value"], v["horizontal"]["b_value"], v["horizontal"]["state"]) == (
        4,
        2,
        "high",
    )
    s = {x["key"]: x for x in b["strength"]}
    assert s["row_bench"]["state"] == "low" and s["row_bench"]["b_kg"] == 50.7
    assert b["labels"]["h_pull"] == "Ziehen horizontal"


def test_migration_gives_exercises_the_finer_muscles(engine):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.downgrade(cfg, "0031")
    rows = [
        ("Bankdrücken (schräg)", None, None, ["chest"], ["shoulders", "triceps"]),
        ("Rudern", "row", None, ["back"], ["biceps", "traps", "shoulders"]),
        ("Brustgestützte Reverse Flys", "flye", "incline_reverse_flye", ["shoulders"], ["traps", "back"]),
        ("Bizepscurl", "curl", None, ["biceps"], ["forearms"]),
    ]
    with engine.begin() as conn:
        for name, cat, gname, prim, sec in rows:
            conn.execute(
                text("""INSERT INTO exercise (id, name, garmin_category, garmin_name, muscles,
                                              muscles_secondary)
                        VALUES (gen_random_uuid(), :n, :c, :g, :p, :s)"""),
                {"n": name, "c": cat, "g": gname, "p": prim, "s": sec},
            )
        conn.execute(
            text("""INSERT INTO coach_profile (id, answers)
                    VALUES (1, '{"goal": "muscle", "focus": ["shoulders", "biceps"]}')""")
        )
    command.upgrade(cfg, "head")

    def state():
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT name, muscles, muscles_secondary FROM exercise"))
            ex = {r.name: (r.muscles, r.muscles_secondary) for r in rows}
            return ex, conn.execute(text("SELECT answers FROM coach_profile")).scalar()["focus"]

    ex, focus = state()
    assert ex == {
        "Bankdrücken (schräg)": (["chest_upper"], ["delts_front", "triceps"]),
        "Rudern": (["lats", "upper_back"], ["biceps", "delts_rear"]),
        "Brustgestützte Reverse Flys": (["delts_rear"], ["upper_back"]),
        "Bizepscurl": (["biceps"], ["forearms"]),
    }
    assert focus == ["delts_front", "delts_side", "delts_rear", "biceps"]
    command.downgrade(cfg, "0031")
    ex, focus = state()
    assert ex["Bankdrücken (schräg)"] == (["chest"], ["shoulders", "triceps"])
    assert ex["Rudern"] == (["back", "traps"], ["biceps", "shoulders"])
    assert focus == ["shoulders", "biceps"]
    command.upgrade(cfg, "head")
