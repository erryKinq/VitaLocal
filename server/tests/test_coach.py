"""AI coach: the debrief validator is the boundary of what a model answer may contain."""

from vitalocal import coach

GOOD = {
    "summary": "Push mit 3×10 @ 19 kg auf der Bank, alle Sätze geschafft.",
    "score": 8,
    "highlights": ["Bankdrücken 3×10 @ 19 kg wie geplant"],
    "watch": [],
    "next_time": ["3×11 @ 19 kg auf der Bank versuchen, wie vorgeschlagen."],
}


def test_a_well_formed_debrief_is_accepted():
    assert coach.validate_debrief(GOOD) == []


def test_the_validator_refuses_what_a_debrief_must_not_be():
    assert coach.validate_debrief("Gut gemacht!") == ["the answer must be a JSON object"]
    errors = coach.validate_debrief({**GOOD, "changes": [{"type": "sets"}]})
    assert any("unknown fields" in e for e in errors)  # a debrief carries no changes
    assert coach.validate_debrief({**GOOD, "summary": "Gutes Training."})  # no number from the session
    for score in (0, 11, 7.5, "8", True):
        assert coach.validate_debrief({**GOOD, "score": score}), score
    assert coach.validate_debrief({**GOOD, "highlights": []})
    assert coach.validate_debrief({**GOOD, "watch": ["a", "b", "c", "d"]})
    assert coach.validate_debrief({**GOOD, "next_time": [""]})
    assert coach.validate_debrief({**GOOD, "highlights": ["x" * 500]})
    assert coach.validate_debrief({k: v for k, v in GOOD.items() if k != "watch"})


from vitalocal import coach_plan  # noqa: E402

FACTS = {
    "answers": {"minutes": 45, "days_per_week": 2, "focus_keys": ["chest"]},
    "catalog": [
        {
            "id": "lib:push_up",
            "name": "Liegestütz",
            "primary": ["chest"],
            "secondary": ["triceps"],
            "equipment": [],
            "kind": "reps",
        },
        {
            "id": "lib:plank",
            "name": "Plank",
            "primary": ["abs"],
            "secondary": [],
            "equipment": [],
            "kind": "time",
        },
        {
            "id": "own:1",
            "name": "Klimmzug",
            "primary": ["back"],
            "secondary": ["biceps"],
            "equipment": [],
            "kind": "reps",
        },
    ],
}
PLAN = {
    "name": "Ganzkörper 2×",
    "summary": "Zwei Ganzkörpertage.",
    "notes": [],
    "days": [
        {
            "name": "A",
            "focus": "Drücken",
            "exercises": [
                {
                    "id": "lib:push_up",
                    "sets": 3,
                    "reps_min": 8,
                    "reps_max": 12,
                    "seconds_min": None,
                    "seconds_max": None,
                    "rest_s": 90,
                },
                {
                    "id": "lib:plank",
                    "sets": 2,
                    "reps_min": None,
                    "reps_max": None,
                    "seconds_min": 30,
                    "seconds_max": 45,
                    "rest_s": 60,
                },
            ],
        },
        {
            "name": "B",
            "focus": "Ziehen",
            "exercises": [
                {
                    "id": "own:1",
                    "sets": 3,
                    "reps_min": 5,
                    "reps_max": 8,
                    "seconds_min": None,
                    "seconds_max": None,
                    "rest_s": 120,
                },
                {
                    "id": "lib:push_up",
                    "sets": 2,
                    "reps_min": 10,
                    "reps_max": 15,
                    "seconds_min": None,
                    "seconds_max": None,
                    "rest_s": 60,
                },
            ],
        },
    ],
}


def _ex(day, i, **kw):
    import copy

    p = copy.deepcopy(PLAN)
    p["days"][day]["exercises"][i].update(kw)
    return p


def test_a_plan_from_the_catalog_is_accepted():
    assert coach_plan.validate_plan(PLAN, FACTS) == []
    r = coach_plan.resolved(PLAN, FACTS)
    assert [x["name"] for x in r["days"][0]["exercises"]] == ["Liegestütz", "Plank"]
    assert r["hard_sets_per_week"]["chest"] == 5 and r["hard_sets_per_week"]["triceps"] == 2.5
    assert r["days"][0]["minutes"] == round((3 * (35 + 90) + 2 * (45 + 60)) / 60) + 8


def test_the_plan_validator_checks_ids_ranges_and_time():
    v = coach_plan.validate_plan
    assert any("not in the catalog" in e for e in v(_ex(0, 0, id="lib:bench_press"), FACTS))
    assert any("reps_min" in e for e in v(_ex(0, 0, reps_min=12, reps_max=8), FACTS))
    assert any("timed" in e for e in v(_ex(0, 1, reps_min=10, reps_max=12), FACTS))
    assert any("counts reps" in e for e in v(_ex(0, 0, seconds_min=30, seconds_max=40), FACTS))
    assert any("`sets`" in e for e in v(_ex(0, 0, sets=9), FACTS))
    assert any("`rest_s`" in e for e in v(_ex(0, 0, rest_s=10), FACTS))
    short = {**FACTS, "answers": {**FACTS["answers"], "minutes": 20}}
    assert any("takes about" in e for e in v(_ex(0, 0, sets=6, rest_s=300), short))
    p = _ex(0, 0)
    p["days"][1]["name"] = "A"
    assert any("different" in e for e in v(p, FACTS))
    p = _ex(0, 0)
    p["days"][0]["exercises"] = p["days"][0]["exercises"][:1]
    assert any("2–10 exercises" in e for e in v(p, FACTS))
    no_chest = {**FACTS, "answers": {**FACTS["answers"], "focus_keys": ["glutes"]}}
    assert any("focus muscle" in e for e in v(PLAN, no_chest))
    assert v({**PLAN, "changes": []}, FACTS)


def test_review_changes_are_described_in_the_request_language():
    from vitalocal import coach_review

    facts = {
        "plan": {
            "days": [
                {
                    "alias": "D1",
                    "name": "Push",
                    "entries": [
                        {
                            "alias": "D1E1",
                            "exercise": "Bank",
                            "kind": "reps",
                            "sets": 3,
                            "range": "8–12",
                            "rest_s": 90,
                        }
                    ],
                }
            ]
        },
        "catalog": [],
    }
    c = {"type": "sets", "entry": "D1E1", "sets": 2, "seconds_min": None}
    assert coach_review.describe(c, facts, "en")["after"] == "2 sets"
    assert coach_review.describe(c, facts)["before"] == "3 Sätze"
    r = {"type": "reorder", "entry": "D1E1", "position": 2, "seconds_min": None}
    assert coach_review.describe(r, facts, "en")["after"] == "position 2"
