"""AI coach: debrief of a workout with a fake model – facts, async job, repair round, failure."""

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from vitalocal import ai, coach

pytestmark = pytest.mark.db
AUTH = {"Authorization": "Bearer test-token"}
T = "/api/v1/training"
C = "/api/v1/coach"

GOOD = {
    "summary": "Push mit 3×10 @ 23 kg, alle Sätze geschafft.",
    "score": 8,
    "highlights": ["Bankdrücken 3×10 @ 23 kg"],
    "watch": [],
    "next_time": ["Wie vorgeschlagen weiter."],
}


@pytest.fixture
def client(engine):
    from vitalocal.main import app

    return TestClient(app)


@pytest.fixture
def model(monkeypatch):
    """Answers with the queued replies in order; records the requests."""
    state = {"replies": [], "calls": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state["calls"].append(body)
        reply = state["replies"].pop(0)
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(
            200, json={"model": body["model"], "choices": [{"message": {"content": content}}]}
        )

    monkeypatch.setattr(ai, "TRANSPORT", httpx.MockTransport(handler))
    started = []
    monkeypatch.setattr(coach, "_run_after_commit", started.append)
    state["started"] = started
    return state


def _post(client, path, body, code=201):
    r = client.post(path, headers=AUTH, json=body)
    assert r.status_code == code, r.text
    return r.json()


def _workout(client):
    bench = _post(client, f"{T}/exercises", {"name": "Bankdrücken", "garmin_category": "bench_press"})
    plan = _post(
        client,
        f"{T}/plans",
        {
            "name": "PP",
            "active": True,
            "days": [
                {
                    "name": "Push",
                    "exercises": [
                        {
                            "exercise_id": bench["id"],
                            "sets": 3,
                            "reps_min": 8,
                            "reps_max": 12,
                            "target_weight_kg": 23,
                            "target_reps": 10,
                        }
                    ],
                }
            ],
        },
    )
    day = plan["days"][0]["id"]
    for date_, reps, rating in (("2026-10-17", 9, "hard"), ("2026-10-21", 10, "hard")):
        _post(
            client,
            f"{T}/sessions",
            {
                "manual_key": str(uuid.uuid4()),
                "start_ts": f"{date_}T18:00:00+02:00",
                "duration_min": 45,
                "plan_day_id": day,
                "sets": [{"exercise_id": bench["id"], "repetitions": reps, "weight_kg": 23}] * 3,
                "ratings": [{"exercise_id": bench["id"], "rating": rating}],
            },
        )
    s = _post(
        client,
        f"{T}/sessions",
        {
            "manual_key": str(uuid.uuid4()),
            "start_ts": "2026-10-24T18:00:00+02:00",
            "duration_min": 50,
            "plan_day_id": day,
            "note": "Schulter etwas zwickig",
            "sets": [{"exercise_id": bench["id"], "repetitions": 10, "weight_kg": 23}] * 3,
            "ratings": [{"exercise_id": bench["id"], "rating": "hard"}],
        },
    )
    return s["activity_id"]


def _enable(client):
    r = client.put(
        "/api/v1/settings/ai",
        headers=AUTH,
        json={"enabled": True, "base_url": "http://model.test/v1", "model": "qwen-test"},
    )
    assert r.status_code == 200, r.text


def test_debrief_of_a_workout(client, model):
    aid = _workout(client)
    assert client.get(f"{C}/status", headers=AUTH).json() == {"enabled": False, "model": None}
    r = client.post(f"{C}/debrief", headers=AUTH, json={"activity_id": aid})
    assert r.status_code == 409 and "model" in r.json()["detail"]
    _enable(client)

    m = _post(client, f"{C}/debrief", {"activity_id": aid}, 202)
    assert (m["status"], m["kind"], m["activity_id"], m["title"], m["session_date"]) == (
        "pending",
        "debrief",
        aid,
        "Push",
        "2026-10-24",
    )
    assert model["started"] == [uuid.UUID(m["id"])]

    # the facts: the session, its history, three times "too hard" in a row, the next target
    with coach.get_engine().connect() as conn:
        facts = conn.execute(
            coach.text("SELECT facts FROM coach_message WHERE id = :id"), {"id": m["id"]}
        ).scalar()
    ex = facts["session"]["exercises"][0]
    assert (ex["exercise"], ex["sets"], ex["rating"]) == (
        "Bankdrücken",
        "10 @ 23 kg, 10 @ 23 kg, 10 @ 23 kg",
        "hard",
    )
    assert facts["session"]["note"] == "Schulter etwas zwickig"
    assert [(h["date"], h["days_before"]) for h in facts["history"]["Bankdrücken"]] == [
        ("2026-10-21", 3),
        ("2026-10-17", 7),
    ]
    assert facts["hard_in_a_row"] == {"Bankdrücken": 3}
    assert "Bankdrücken" in facts["next_targets"]

    # first answer invalid (no number, a change smuggled in) → one repair round with the errors
    model["replies"] += [{**GOOD, "summary": "Gut.", "changes": []}, GOOD]
    coach.run(uuid.UUID(m["id"]))
    done = client.get(f"{C}/messages/{m['id']}", headers=AUTH).json()
    assert (done["status"], done["repaired"], done["model"], done["result"]) == (
        "done",
        True,
        "qwen-test",
        GOOD,
    )
    first, second = model["calls"]
    assert first["response_format"]["type"] == "json_schema"
    assert "unknown fields" in second["messages"][1]["content"]
    assert [
        x["id"] for x in client.get(f"{C}/messages", headers=AUTH, params={"activity_id": aid}).json()
    ] == [m["id"]]

    # twice rejected → error, nothing half-accepted
    m2 = _post(client, f"{C}/debrief", {"activity_id": aid, "lang": "en"}, 202)
    model["replies"] += ["not json", {**GOOD, "score": 12}]
    coach.run(uuid.UUID(m2["id"]))
    failed = client.get(f"{C}/messages/{m2['id']}", headers=AUTH).json()
    assert failed["status"] == "error" and "rejected twice" in failed["error"] and failed["result"] is None
    assert "Debrief this workout" in model["calls"][2]["messages"][1]["content"]


def test_a_workout_without_assigned_sets_cannot_be_debriefed(client, model):
    _enable(client)
    s = _post(
        client,
        f"{T}/sessions",
        {
            "manual_key": str(uuid.uuid4()),
            "start_ts": "2026-10-24T18:00:00+02:00",
            "duration_min": 20,
            "sets": [],
        },
    )
    r = client.post(f"{C}/debrief", headers=AUTH, json={"activity_id": s["activity_id"]})
    assert r.status_code == 409 and "review it first" in r.json()["detail"]


ANSWERS = {
    "goal": "muscle",
    "experience": "intermediate",
    "days_per_week": 2,
    "minutes": 60,
    "equipment": ["dumbbell", "bench"],
    "limitations": "",
    "focus": ["chest"],
    "likes": "",
    "dislikes": "",
    "cardio": False,
}


def _proposal(name="Ganzkörper 2×"):
    def ex(i):
        return {
            "id": i,
            "sets": 3,
            "reps_min": 8,
            "reps_max": 12,
            "seconds_min": None,
            "seconds_max": None,
            "rest_s": 90,
        }

    return {
        "name": name,
        "summary": "Zwei Tage.",
        "notes": ["Gewichte setzt VitaLocal."],
        "days": [
            {
                "name": "A",
                "focus": "Drücken",
                "exercises": [ex("lib:dumbbell_bench_press"), ex("lib:dumbbell_row")],
            },
            {
                "name": "B",
                "focus": "Ziehen",
                "exercises": [ex("lib:goblet_squat"), ex("lib:incline_dumbbell_bench_press")],
            },
        ],
    }


def test_plan_from_the_questionnaire(client, model):
    _enable(client)
    r = client.post(f"{C}/plan", headers=AUTH, json={"answers": {**ANSWERS, "equipment": ["anvil"]}})
    assert r.status_code == 422
    m = _post(client, f"{C}/plan", {"answers": ANSWERS}, 202)
    assert client.get(f"{C}/profile", headers=AUTH).json()["focus"] == ["chest_upper", "chest_lower"]
    with coach.get_engine().connect() as conn:
        facts = conn.execute(
            coach.text("SELECT facts FROM coach_message WHERE id = :id"), {"id": m["id"]}
        ).scalar()
    ids = {c["id"] for c in facts["catalog"]}
    assert "lib:dumbbell_bench_press" in ids and "lib:push_up" in ids and "lib:barbell_bench_press" not in ids
    assert facts["answers"]["equipment"] == ["Kurzhanteln", "Bank"]

    # an unknown exercise first → repaired; the result carries names and weekly sets
    bad = _proposal()
    bad["days"][0]["exercises"][0]["id"] = "lib:leg_press"
    model["replies"] += [bad, _proposal()]
    coach.run(uuid.UUID(m["id"]))
    done = client.get(f"{C}/messages/{m['id']}", headers=AUTH).json()
    assert done["status"] == "done" and done["repaired"]
    assert done["result"]["days"][0]["exercises"][0]["name"] == "Kurzhantel-Bankdrücken"
    assert (
        done["result"]["hard_sets_per_week"]["chest_lower"],
        done["result"]["hard_sets_per_week"]["chest_upper"],
    ) == (3, 4.5)
    assert "leg_press" in model["calls"][1]["messages"][1]["content"]

    # refine with a wish: the previous proposal and the wish go along
    r = client.post(f"{C}/plan", headers=AUTH, json={"answers": ANSWERS, "refines": m["id"]})
    assert r.status_code == 422
    m2 = _post(client, f"{C}/plan", {"answers": ANSWERS, "refines": m["id"], "change": "mehr Beine"}, 202)
    assert m2["refines"] == m["id"]
    model["replies"].append(_proposal("Ganzkörper 2× (mehr Beine)"))
    coach.run(uuid.UUID(m2["id"]))
    assert "mehr Beine" in model["calls"][2]["messages"][1]["content"]
    assert "Bessere den Plan nach" in model["calls"][2]["messages"][1]["content"]

    # take it over: library entries become own exercises, an ordinary (active) plan
    plan = _post(client, f"{C}/messages/{m2['id']}/apply", {"active": True}, 201)
    assert (plan["name"], plan["active"], [d["name"] for d in plan["days"]]) == (
        "Ganzkörper 2× (mehr Beine)",
        True,
        ["A", "B"],
    )
    e = plan["days"][0]["exercises"][0]
    assert (e["sets"], e["reps_min"], e["reps_max"], e["rest_s"], e["target_weight_kg"]) == (
        3,
        8,
        12,
        90,
        None,
    )
    names = {x["name"] for x in client.get(f"{T}/exercises", headers=AUTH).json()}
    assert {"Kurzhantel-Bankdrücken", "Einarmiges Kurzhantelrudern", "Goblet Squat"} <= names
    assert client.get(f"{C}/messages/{m2['id']}", headers=AUTH).json()["applied_plan_id"] == plan["id"]
    assert client.post(f"{C}/messages/{m2['id']}/apply", headers=AUTH, json={}).status_code == 409
    assert [x["kind"] for x in client.get(f"{C}/messages", headers=AUTH, params={"kind": "plan"}).json()] == [
        "plan"
    ] * 2


def test_coach_messages_can_be_discarded(client, model):
    _enable(client)
    m = _post(client, f"{C}/plan", {"answers": ANSWERS}, 202)
    assert client.delete(f"{C}/messages/{m['id']}", headers=AUTH).status_code == 204
    assert client.get(f"{C}/messages/{m['id']}", headers=AUTH).status_code == 404
    assert client.delete(f"{C}/messages/{m['id']}", headers=AUTH).status_code == 404
    coach.run(uuid.UUID(m["id"]))  # a job finishing after the discard finds nothing to write
    eq = {e["key"]: e for e in client.get(f"{T}/equipment", headers=AUTH).json()}
    assert "Beinpresse" in eq["machine"]["hint"] and eq["weight_belt"]["label"] == "Dip-/Gewichtsgürtel"


def _change(typ, **kw):
    base = dict.fromkeys(
        [
            "entry",
            "day",
            "exercise",
            "sets",
            "reps_min",
            "reps_max",
            "seconds_min",
            "seconds_max",
            "rest_s",
            "position",
            "name",
            "order",
        ]
    )
    return {**base, "type": typ, "why": "2× in Folge zu schwer (10/10/10 @ 23 kg)", **kw}


def test_plan_review_changes_taken_over_and_undone(client, model, monkeypatch):
    from datetime import datetime

    from vitalocal import coach_review

    monkeypatch.setattr(coach_review, "_now", lambda: datetime.fromisoformat("2026-10-25T12:00:00+02:00"))
    aid = _workout(client)
    _enable(client)
    plans = client.get(f"{T}/plans", headers=AUTH).json()
    plan = next(p for p in plans if p["active"])

    m = _post(client, f"{C}/review", {"note": "Bankdrücken fühlt sich zäh an"}, 202)
    with coach.get_engine().connect() as conn:
        facts = conn.execute(
            coach.text("SELECT facts FROM coach_message WHERE id = :id"), {"id": m["id"]}
        ).scalar()
    e1 = facts["plan"]["days"][0]["entries"][0]
    assert (e1["alias"], e1["exercise"], e1["hard_in_a_row"]) == ("D1E1", "Bankdrücken", 3)
    assert facts["adherence"]["per_day"]["Push"]["trained"] == 3
    assert facts["user_note"] == "Bankdrücken fühlt sich zäh an"

    # a change on an unknown entry and a timed range on a reps exercise → repaired
    bad = {
        "nochange": False,
        "reading": "3 Trainings, Bank 3× zu schwer.",
        "notes": [],
        "changes": [
            _change("sets", entry="D9E1", sets=2),
            _change("duration_range", entry="D1E1", seconds_min=30, seconds_max=40),
        ],
    }
    good = {
        "nochange": False,
        "reading": "3 Trainings, Bank 3× zu schwer.",
        "notes": ["Schlaf beachten"],
        "changes": [
            _change("sets", entry="D1E1", sets=2),
            _change(
                "add_exercise", day="D1", exercise="lib:push_up", sets=3, reps_min=12, reps_max=15, rest_s=60
            ),
            _change("rename_day", day="D1", name="Push (Brust)"),
        ],
    }
    model["replies"] += [bad, good]
    coach.run(uuid.UUID(m["id"]))
    r = client.get(f"{C}/messages/{m['id']}", headers=AUTH).json()
    assert r["status"] == "done" and r["repaired"]
    assert "D9E1" in model["calls"][1]["messages"][1]["content"]
    assert [(c["type"], c["before"], c["after"]) for c in r["result"]["changes"]] == [
        ("sets", "3 Sätze", "2 Sätze"),
        ("add_exercise", None, "Liegestütz 3×12–15, 60 s"),
        ("rename_day", "Push", "Push (Brust)"),
    ]

    # take over the first two; the day keeps its name
    p = client.post(f"{C}/messages/{m['id']}/take-over", headers=AUTH, json={"changes": [0, 1]})
    assert p.status_code == 200, p.text
    day = p.json()["days"][0]
    assert day["name"] == "Push"
    assert [(e["exercise"], e["sets"], e["reps_min"], e["reps_max"]) for e in day["exercises"]] == [
        ("Bankdrücken", 2, 8, 12),
        ("Liegestütz", 3, 12, 15),
    ]
    assert day["exercises"][0]["target_weight_kg"] == 23  # targets untouched
    again = client.post(f"{C}/messages/{m['id']}/take-over", headers=AUTH, json={"changes": [2]})
    assert again.status_code == 409

    # undo puts the plan back
    u = client.post(f"{C}/messages/{m['id']}/undo", headers=AUTH)
    assert u.status_code == 200, u.text
    assert [(e["exercise"], e["sets"]) for e in u.json()["days"][0]["exercises"]] == [("Bankdrücken", 3)]
    assert u.json()["days"][0]["exercises"][0]["id"] == plan["days"][0]["exercises"][0]["id"]

    # a plan edited after the review refuses the review's changes
    m2 = _post(client, f"{C}/review", {}, 202)
    model["replies"].append({**good, "changes": [good["changes"][0]]})  # push-ups are an own exercise by now
    coach.run(uuid.UUID(m2["id"]))
    body = {
        "name": "PP",
        "active": True,
        "days": [
            {
                "id": plan["days"][0]["id"],
                "name": "Push",
                "exercises": [
                    {
                        "id": plan["days"][0]["exercises"][0]["id"],
                        "exercise_id": e1_id(client),
                        "sets": 4,
                        "reps_min": 8,
                        "reps_max": 12,
                        "target_weight_kg": 23,
                        "target_reps": 10,
                    }
                ],
            }
        ],
    }
    assert client.put(f"{T}/plans/{plan['id']}", headers=AUTH, json=body).status_code == 200
    r = client.post(f"{C}/messages/{m2['id']}/take-over", headers=AUTH, json={"changes": [0]})
    assert r.status_code == 409 and "changed since the review" in r.json()["detail"]
    assert aid


def e1_id(client):
    return next(
        e["id"] for e in client.get(f"{T}/exercises", headers=AUTH).json() if e["name"] == "Bankdrücken"
    )


def test_review_without_an_active_plan(client, model):
    _enable(client)
    r = client.post(f"{C}/review", headers=AUTH, json={})
    assert r.status_code == 409 and "no active plan" in r.json()["detail"]
