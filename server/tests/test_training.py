"""Training rules without a database: plan-day and set suggestions, double progression, stats."""

from datetime import date

from vitalocal import training
from vitalocal.training import assign_sets, e1rm, next_target, suggest_day, suggest_flags

DAYS = [
    {"id": "A", "exercises": [{"garmin_category": "bench_press"}, {"garmin_category": "shoulder_press"}]},
    {
        "id": "B",
        "exercises": [
            {"garmin_category": "pull_up"},
            {"garmin_category": "row"},
            {"garmin_category": "curl"},
        ],
    },
    {"id": "C", "exercises": [{"garmin_category": "squat"}, {"garmin_category": "lunge"}]},
]


def test_learned_categories_count_as_a_match():
    # 01.10.: a pull workout after push; the plan's own categories cover too little of it
    days = [
        {"id": "push", "exercises": [
            {"garmin_category": "bench_press"}, {"garmin_category": "lateral_raise"},
            {"garmin_category": "shoulder_press", "garmin_categories": {"pull_up"}}]},
        {"id": "legs", "exercises": [{"garmin_category": "squat"}, {"garmin_category": "lunge"}]},
        {"id": "pull", "exercises": [
            {"garmin_category": "pull_up"}, {"garmin_category": None, "garmin_categories": {"curl"}},
            {"garmin_category": "row"}, {"garmin_category": "flye", "garmin_categories": {"lateral_raise"}},
            {"garmin_category": "curl", "garmin_categories": {"shrug"}}, {"garmin_category": "carry"}]},
    ]  # fmt: skip
    cats = ["cardio"] + ["pull_up"] * 2 + ["curl"] * 4 + ["row"] * 3 + ["lateral_raise"] * 4 + ["shrug"] * 2
    cats += [None] * 3
    assert suggest_day(days, "push", cats) == ("pull", "exercises match")
    # sets go to the entry whose learned category matches
    plan = [{"id": "inc", "garmin_categories": {"curl"}, "sets": 3},
            {"id": "fly", "garmin_categories": {"flye", "lateral_raise"}, "sets": 3}]  # fmt: skip
    got = assign_sets(_sets("lateral_raise", "lateral_raise", "curl", "curl"), plan)
    assert got == ["fly", "fly", "inc", "inc"]


def test_day_follows_the_rotation_unless_the_exercises_clearly_fit_another():
    assert suggest_day(DAYS, None, []) == ("A", "rotation")
    assert suggest_day(DAYS, "A", []) == ("B", "rotation")
    assert suggest_day(DAYS, "C", ["cardio"]) == ("A", "rotation")  # wraps; warm-up says nothing
    assert suggest_day(DAYS, "A", ["pull_up", "row", "curl"]) == ("B", "rotation")
    assert suggest_day(DAYS, "A", ["squat", "lunge"]) == ("C", "exercises match")
    assert suggest_day(DAYS, "A", ["squat", "pull_up", "row"]) == ("B", "rotation")  # not clear enough
    assert suggest_day([], None, ["row"]) == (None, "no active plan")
    # an empty day is skipped: after "Pull" comes "Pull" again, not the empty "C"
    pull = {"id": "P", "exercises": [{"garmin_category": "row"}]}
    assert suggest_day([{"id": "C", "exercises": []}, pull], "P", []) == ("P", "rotation")


PLAN = [
    {"id": "pull", "garmin_category": "pull_up", "sets": 3},
    {"id": "curl", "garmin_category": "curl", "sets": 3},
    {"id": "row", "garmin_category": "row", "sets": 2},
    {"id": "lunge", "garmin_category": "lunge", "sets": 2},
]


def _sets(*cats):
    return [{"garmin_category": c} for c in cats]


def test_sets_fill_the_plan_in_order_and_follow_the_watch_category():
    assert assign_sets(_sets("pull_up", "pull_up", "pull_up", "curl", "curl", "curl"), PLAN) == [
        "pull", "pull", "pull", "curl", "curl", "curl",
    ]  # fmt: skip
    # an unrecognised set counts for the current exercise; an extra set stays with it
    assert assign_sets(_sets(None, "pull_up", "pull_up", "pull_up", "curl"), PLAN) == [
        "pull", "pull", "pull", "pull", "curl",
    ]  # fmt: skip
    # fewer sets than planned: the category moves on
    assert assign_sets(_sets("pull_up", "curl", "curl"), PLAN)[1:] == ["curl", "curl"]


def test_a_single_misrecognised_set_does_not_skip_ahead():
    # 20.09.-pattern: row, "lunge", row: the lunge is one row set the watch got wrong
    sets = _sets("pull_up", "pull_up", "pull_up", "curl", "curl", "curl", "row", "lunge", "row")
    got = assign_sets(sets, PLAN)
    assert got[6:] == ["row", "row", "row"]
    # two lunge sets in a row are lunges
    got = assign_sets(_sets("row", "lunge", "lunge"), PLAN[2:])
    assert got == ["row", "lunge", "lunge"]
    assert assign_sets(_sets("row"), []) == [None]


def test_the_order_of_the_exercises_does_not_matter():
    # curls first, then pull-ups, rows and lunges: every recognised set finds its exercise
    sets = _sets("curl", "curl", "curl", "pull_up", "pull_up", "pull_up", "lunge", "lunge", "row", "row")
    assert assign_sets(sets, PLAN) == ["curl"] * 3 + ["pull"] * 3 + ["lunge"] * 2 + ["row"] * 2
    # unrecognised sets after a finished exercise go to the next one still lacking sets
    assert assign_sets(_sets("curl", "curl", "curl", None, None), PLAN)[3:] == ["row", "row"]
    assert assign_sets(_sets("lunge", "lunge", None), PLAN)[2] == "pull"  # wraps to the start


def test_an_unchanged_weight_is_an_extra_set_of_the_same_exercise():
    # 27.09.-pattern: rows at 28.5 kg, the watch calls two of them lunges; lateral raises follow at 12 kg
    plan = [
        {"id": "row", "garmin_category": "row", "sets": 2},
        {"id": "lat", "garmin_category": "lateral_raise", "sets": 3},
    ]
    pattern = [("row", 28.5), ("lunge", 28.5), ("lunge", 28.5), ("lateral_raise", 12), ("lateral_raise", 12)]
    sets = [{"garmin_category": c, "weight_kg": w} for c, w in pattern]
    assert assign_sets(sets, plan) == ["row", "row", "row", "lat", "lat"]
    # the watch recognised the next exercise: it moves on even at the same weight
    sets = [{"garmin_category": c, "weight_kg": 12} for c in ("row", "row", "lateral_raise")]
    assert assign_sets(sets, plan) == ["row", "row", "lat"]
    # 01.10.: the fourth "lateral raise" at a new weight is the hammer curl that follows the flys
    plan = [{"id": "fly", "garmin_categories": {"lateral_raise"}, "sets": 3},
            {"id": "ham", "garmin_categories": {"curl"}, "sets": 3}]  # fmt: skip
    pattern = [("lateral_raise", 12)] * 3 + [("lateral_raise", 15.5), ("shrug", 15.5), ("shrug", 15.5)]
    sets = [{"garmin_category": c, "weight_kg": w} for c, w in pattern]
    assert assign_sets(sets, plan) == ["fly"] * 3 + ["ham"] * 3


def test_flags_for_warm_up_and_accidental_sets():
    assert suggest_flags({"garmin_category": "cardio", "duration_s": 600, "repetitions": 0}, 0)["warmup"]
    assert suggest_flags({"garmin_category": None, "duration_s": 743, "repetitions": 0}, 0)["warmup"]
    assert suggest_flags({"garmin_category": None, "duration_s": 3, "repetitions": 0}, 5)["hidden"]
    assert suggest_flags({"garmin_category": None, "duration_s": 5.9, "repetitions": 9}, 1)[
        "hidden"
    ]  # artefact
    assert suggest_flags({"garmin_category": None, "duration_s": 60, "repetitions": 0}, 5)["hidden"]
    farmers_walk = {"garmin_category": None, "duration_s": 49, "repetitions": 0, "weight_kg": 30.5}
    assert not suggest_flags(farmers_walk, 17)["hidden"]
    assert suggest_flags({"garmin_category": "curl", "duration_s": 40, "repetitions": 10}, 3) == {
        "warmup": False, "hidden": False,
    }  # fmt: skip


PE = {
    "sets": 3,
    "reps_min": 8,
    "reps_max": 12,
    "target_reps": 10,
    "target_weight_kg": 20.0,
    "duration_s": None,
}
EX = {"kind": "reps", "increment_kg": 2.5}


def _hist(*sessions):
    """(rating, [(reps, weight), …]) newest first."""
    return [{"date": f"2026-09-{20 - i:02d}", "rating": r,
             "sets": [{"reps": x, "weight_kg": w} for x, w in s]}
            for i, (r, s) in enumerate(sessions)]  # fmt: skip


def test_double_progression():
    t = next_target(PE, EX, [])
    assert (t.sets, t.reps, t.weight_kg, t.reason) == (3, 10, 20.0, "plan")
    # good and target reached: one more rep
    t = next_target(PE, EX, _hist(("good", [(10, 20), (10, 20), (11, 20)])))
    assert (t.reps, t.weight_kg) == (11, 20)
    # good at the top of the range in every set: weight up, back to the bottom
    t = next_target({**PE, "target_reps": 12}, EX, _hist(("good", [(12, 20), (12, 20), (12, 20)])))
    assert (t.reps, t.weight_kg) == (8, 22.5)
    # good but target missed: same again
    t = next_target(PE, EX, _hist(("good", [(10, 20), (9, 20), (8, 20)])))
    assert (t.reps, t.weight_kg) == (10, 20)
    # too easy: weight up now
    t = next_target(PE, EX, _hist(("easy", [(10, 20)] * 3)))
    assert (t.reps, t.weight_kg) == (8, 22.5)
    # too hard once: repeat; twice in a row: the volume variant
    t = next_target(PE, EX, _hist(("hard", [(10, 20), (8, 20), (7, 20)])))
    assert (t.reps, t.weight_kg) == (10, 20)
    t = next_target(PE, EX, _hist(("hard", [(9, 20)] * 3), ("hard", [(8, 20)] * 3)))
    assert (t.sets, t.reps, t.weight_kg) == (4, 8, 20)
    # unrated counts as good; the top of the range needs all planned sets
    t = next_target({**PE, "target_reps": 12}, EX, _hist((None, [(12, 20), (12, 20)])))
    assert t.unrated and (t.reps, t.weight_kg) == (12, 20) and "2 of 3 sets done" in t.notes
    # heavier than planned: builds on what was done
    t = next_target(PE, EX, _hist(("good", [(10, 22.5)] * 3)))
    assert (t.reps, t.weight_kg) == (11, 22.5)


def test_reps_follow_the_target_not_the_weakest_set():
    pe = {**PE, "target_reps": 9}
    # 9, 9, 6: missed once, the target stays 3×9 (not 3×7 from the last set)
    t = next_target(pe, EX, _hist(("good", [(9, 20), (9, 20), (6, 20)])))
    assert (t.sets, t.reps, t.weight_kg) == (3, 9, 20)
    # three sets of 9 and a weaker extra set: met
    t = next_target(pe, EX, _hist(("good", [(9, 20), (9, 20), (9, 20), (6, 20)])))
    assert (t.sets, t.reps) == (3, 10)
    # more than the target in every set: builds on what was done
    t = next_target(pe, EX, _hist(("good", [(11, 20)] * 3)))
    assert (t.sets, t.reps) == (3, 12)
    # the next dumbbell instead of the suggested step: starts over at the level done with it
    t = next_target(pe, EX, _hist(("good", [(8, 21.5)] * 3), ("good", [(12, 20)] * 3)))
    assert (t.sets, t.reps, t.weight_kg) == (3, 9, 21.5)


def test_two_misses_lead_to_the_volume_variant_and_back():
    pe = {**PE, "reps_min": 6, "target_reps": 9}
    missed = ("good", [(9, 20), (9, 20), (6, 20)])
    t = next_target(pe, EX, _hist(missed, missed))
    assert (t.sets, t.reps, t.weight_kg) == (4, 7, 20)
    assert t.notes == ["volume variant: 4×7 instead of 3×9"]
    # 4×7 done: back to the 3×9 that was missed
    t = next_target(pe, EX, _hist(("good", [(7, 20)] * 4), missed, missed))
    assert (t.sets, t.reps, t.weight_kg, t.notes) == (3, 9, 20, ["back to 3×9"])
    # 4×7 missed once: same again; twice: one step down at the top of the range
    vol_missed = ("hard", [(7, 20)] * 3 + [(5, 20)])
    t = next_target(pe, EX, _hist(vol_missed, missed, missed))
    assert (t.sets, t.reps) == (4, 7)
    assert t.notes == ["missed once, same again", "volume variant: 4×7 instead of 3×9"]
    t = next_target(pe, EX, _hist(vol_missed, vol_missed, missed, missed))
    assert (t.sets, t.reps, t.weight_kg, t.notes) == (3, 12, 17.5, ["weight down after two misses"])
    # two reps less would leave the range (8–12): one step down at the top of the range right away
    t = next_target({**PE, "target_reps": 9}, EX, _hist(missed, missed))
    assert (t.sets, t.reps, t.weight_kg) == (3, 12, 17.5)
    # a miss after a met target counts once
    t = next_target(pe, EX, _hist(missed, ("good", [(9, 20)] * 3)))
    assert (t.sets, t.reps) == (3, 10)


def _days(*sessions):
    """(date, rating, [(reps, weight), …]) newest first."""
    return [{"date": d, "rating": r, "sets": [{"reps": x, "weight_kg": w} for x, w in s]}
            for d, r, s in sessions]  # fmt: skip


def test_the_volume_variant_as_the_user_does_it():
    pe = {**PE, "target_reps": 11}
    stuck = ("good", [(11, 20), (11, 20), (9, 20)])
    # 3×11 missed twice → 4×9; done → 3×11 again; too easy there: back as well, no weight step
    t = next_target(pe, EX, _hist(stuck, stuck))
    assert (t.sets, t.reps, t.weight_kg, t.notes) == (4, 9, 20, ["volume variant: 4×9 instead of 3×11"])
    t = next_target(pe, EX, _hist(("good", [(9, 20)] * 4), stuck, stuck))
    assert (t.sets, t.reps, t.notes) == (3, 11, ["back to 3×11"])
    t = next_target(pe, EX, _hist(("easy", [(9, 20)] * 4), stuck, stuck))
    assert (t.sets, t.reps, t.weight_kg) == (3, 11, 20)
    # never more reps per set than the best set reached: 9, 9, 9 twice at 3×12 → 4×9, not 4×10
    t = next_target({**PE, "target_reps": 12}, EX, _hist(("good", [(9, 20)] * 3), ("good", [(9, 20)] * 3)))
    assert (t.sets, t.reps) == (4, 9)


def test_more_sets_at_fewer_reps_are_the_volume_variant_done_on_ones_own():
    pe = {**PE, "target_reps": 10}
    # 4×9 instead of 3×10: done → 3×10 again, not two reps above the 4 sets
    t = next_target(pe, EX, _hist(("good", [(9, 20)] * 4)))
    assert (t.sets, t.reps, t.notes) == (3, 10, ["back to 3×10"])
    # 9, 9, 9, 6: 4×9 was tried and missed → 4×9 again, not more
    t = next_target(pe, EX, _hist(("good", [(9, 20)] * 3 + [(6, 20)])))
    assert (t.sets, t.reps, t.weight_kg) == (4, 9, 20)
    assert t.notes == ["missed once, same again", "volume variant: 4×9 instead of 3×10"]
    # a fourth set after a met target changes nothing
    t = next_target(pe, EX, _hist(("good", [(10, 20)] * 3 + [(7, 20)])))
    assert (t.sets, t.reps) == (3, 11)
    # pull-ups after the pause: 3×6 done → 3×7; 4×6 instead → 3×7, not 3×8; then 7, 7, 5 → 3×7 again
    pu = {**PE, "reps_min": 5, "reps_max": 8, "target_reps": 6}
    t = next_target(pu, EX, _hist(("hard", [(7, 15), (7, 15), (5, 15)]), ("good", [(6, 15)] * 4),
                                  ("good", [(6, 15)] * 3)))  # fmt: skip
    assert (t.sets, t.reps, t.weight_kg, t.notes) == (3, 7, 15, ["missed once, same again"])


def test_a_higher_target_of_ones_own_is_the_one_judged():
    # shoulder press after the pause: 4×9, 4×9 (→ 3×10), then 11, 11, 10 for the plan's 3×11, too hard:
    # 3×11 again, not 3×10
    pe = {**PE, "target_reps": 11, "target_weight_kg": 14}
    t = next_target(pe, EX, _days(("2026-10-03", "hard", [(11, 14), (11, 14), (10, 14)]),
                                  ("2026-09-29", None, [(9, 14)] * 4), ("2026-09-24", None, [(9, 14)] * 4),
                                  ("2026-08-14", None, [(9, 14)] * 4)))  # fmt: skip
    assert (t.sets, t.reps, t.weight_kg, t.notes) == (3, 11, 14, ["missed once, same again"])
    # one strong set alone is no higher target: 12, 10, 8 at 3×10 is a miss of 3×10
    t = next_target(PE, EX, _hist(("good", [(12, 20), (10, 20), (8, 20)])))
    assert (t.sets, t.reps) == (3, 10)


def test_after_a_long_pause_the_progression_starts_over():
    pe = {**PE, "target_reps": 12}
    before = [("2026-08-14", "good", [(11, 20)] * 3), ("2026-08-07", "good", [(11, 20)] * 3)]
    # six weeks later 3×9: the new start, not another miss of the old target
    t = next_target(pe, EX, _days(("2026-09-24", "good", [(9, 20)] * 3), *before))
    assert (t.sets, t.reps, t.weight_kg) == (3, 10, 20)
    # one weak set after the pause: the target is what the session went for
    t = next_target(pe, EX, _days(("2026-09-24", "good", [(12, 20), (12, 20), (4, 20)]), *before))
    assert (t.sets, t.reps, t.notes) == (3, 12, ["missed once, same again"])
    # before the first session after the pause: the last level
    t = next_target(pe, EX, _days(*before), today=date(2026, 9, 24))
    assert (t.sets, t.reps, t.weight_kg) == (3, 11, 20)
    assert t.notes == ["5 weeks without it: start at the last level"] and t.based_on == "2026-08-14"
    # also for time exercises
    hold = [{"date": "2026-08-11", "rating": "good", "sets": [{"duration_s": 50}] * 2}]
    t = next_target({"sets": 2, "duration_s": 55}, {"kind": "time"}, hold, today=date(2026, 10, 3))
    assert (t.duration_s, t.notes) == (50, ["7 weeks without it: start at the last level"])


def test_no_step_down_while_the_reps_go_up():
    pe = {**PE, "reps_min": 10, "reps_max": 15, "target_reps": 10}
    # 8, 8, 10 then 9, 9, 9 below the range: two misses, but more reps
    t = next_target(pe, EX, _hist(("good", [(9, 20)] * 3), ("good", [(8, 20), (8, 20), (10, 20)])))
    assert (t.sets, t.reps, t.weight_kg) == (3, 10, 20)
    assert t.notes == ["missed, but more reps than last time: same again"]
    # no progress: one step down at the top of the range
    t = next_target(pe, EX, _hist(("good", [(9, 20)] * 3), ("good", [(9, 20)] * 3)))
    assert (t.sets, t.reps, t.weight_kg) == (3, 15, 17.5)
    # another dumbbell, below the range: starts there, the session counts as met
    t = next_target(pe, EX, _hist(("good", [(8, 21.5), (8, 21.5), (10, 21.5)])))
    assert (t.sets, t.reps, t.weight_kg) == (3, 9, 21.5)


def test_weight_steps_follow_the_plates_and_estimate_the_reps():
    weights = training.plate_weights(0.5, training.PLATES)
    assert [w for w in weights if 22 <= w <= 26] == [22.0, 23.0, 23.5, 24.0, 24.5, 25.5]
    assert {51.0, 61.0} <= set(training.plate_weights(0.5, training.PLATES, 2))  # two dumbbells
    pe = {**PE, "target_reps": 12}
    # the heaviest weight where Epley still expects 8 reps (25.5: 7.9), one rep below the estimate
    t = next_target({**pe, "target_weight_kg": 23}, EX, _hist(("good", [(12, 23)] * 3)), weights=weights)
    assert (t.sets, t.reps, t.weight_kg) == (3, 8, 24.5)
    # a small step starts higher in the range
    t = next_target({**pe, "target_weight_kg": 14}, EX, _hist(("good", [(12, 14)] * 3)), weights=weights)
    assert (t.reps, t.weight_kg) == (9, 14.5)
    # down: the next lighter weight at the top of the range
    missed = ("good", [(8, 23), (8, 23), (6, 23)])
    pe8 = {**pe, "target_reps": 8, "target_weight_kg": 23}
    t = next_target(pe8, EX, _hist(missed, missed), weights=weights)
    assert (t.reps, t.weight_kg, t.notes) == (12, 22.0, ["weight down after two misses"])
    # the first session at the new weight sets the level: Epley's 13 at 8.5 kg, 10, 10, 10 done → 3×11
    pe20 = {**PE, "reps_min": 12, "reps_max": 20, "target_reps": 20, "target_weight_kg": 7.5}
    t = next_target(pe20, EX, _hist(("good", [(20, 7.5)] * 3)), weights=weights)
    assert (t.reps, t.weight_kg) == (13, 8.5)
    t = next_target(pe20, EX, _hist(("good", [(10, 8.5)] * 3), ("good", [(20, 7.5)] * 3)), weights=weights)
    assert (t.sets, t.reps, t.weight_kg) == (3, 11, 8.5)
    # without plates: exactly one step, no rounding to multiples of it
    t = next_target({**pe, "target_weight_kg": 11.5}, EX, _hist(("good", [(12, 11.5)] * 3)))
    assert (t.reps, t.weight_kg) == (8, 14)


def test_body_weight_without_load_stays_at_the_top():
    ex = {**EX, "bodyweight": True}
    pe = {**PE, "target_reps": 12, "target_weight_kg": None}
    t = next_target(pe, ex, _hist(("good", [(12, None)] * 3)))
    assert (t.sets, t.reps, t.weight_kg) == (3, 12, None)
    assert t.notes == ["top of the range: add load or a harder variant"]
    # extra load (pull-ups with a belt) goes up by the step
    t = next_target({**pe, "target_weight_kg": 15}, ex, _hist(("good", [(12, 15)] * 3)))
    assert (t.reps, t.weight_kg) == (8, 17.5)


def test_the_rating_suggestion_counts_the_planned_sets():
    target = {"sets": 3, "reps": 9, "weight_kg": 20}
    sets = [{"repetitions": r, "weight_kg": 20} for r in (9, 9, 9, 6)]
    assert training.suggest_rating(target, sets)["rating"] == "good"
    assert training.suggest_rating(target, sets[1:])["why"] == "target missed"
    assert (
        training.suggest_rating(target, [{"repetitions": 9, "weight_kg": 15}] * 3)["why"] == "lighter weight"
    )
    # one set more, all at fewer reps than planned: the volume variant done; 9, 9, 9, 6 at 3×10: missed
    target = {"sets": 3, "reps": 10, "weight_kg": 20}
    vol = training.suggest_rating(target, [{"repetitions": 9, "weight_kg": 20}] * 4)
    assert vol == {"rating": "good", "why": "volume variant"}
    assert training.suggest_rating(target, sets)["why"] == "target missed"


def test_time_exercises_progress_in_seconds():
    pe = {"sets": 3, "duration_s": 45}
    ex = {"kind": "time"}
    assert next_target(pe, ex, []).duration_s == 45
    hist = [{"date": "2026-09-20", "rating": "good", "sets": [{"duration_s": 45}] * 3}]
    assert next_target(pe, ex, hist).duration_s == 50
    hist[0]["rating"] = "easy"
    assert next_target(pe, ex, hist).duration_s == 55


def _holds(*sessions):
    """(date, rating, weight, [seconds, …]) newest first."""
    return [{"date": d, "rating": r, "sets": [{"duration_s": x, "weight_kg": w} for x in s]}
            for d, r, w, s in sessions]  # fmt: skip


def test_time_ranges_step_the_weight_at_the_top():
    pe = {"sets": 3, "duration_s": 50, "duration_min_s": 30, "duration_max_s": 45, "target_weight_kg": 28.5}
    ex = {"kind": "time", "increment_kg": 2}
    # farmer's walk: 30, 35, 40, 45 s, then the next weight at 30 s
    hist = _holds(("2026-07-28", None, 28.5, [40] * 3), ("2026-07-24", None, 28.5, [35] * 3),
                  ("2026-07-20", None, 28.5, [30] * 3))  # fmt: skip
    t = next_target(pe, ex, hist)
    assert (t.duration_s, t.weight_kg) == (45, 28.5)
    hist = _holds(("2026-08-03", None, 28.5, [45] * 3)) + hist
    t = next_target(pe, ex, hist)
    assert (t.duration_s, t.weight_kg) == (30, 30.5)
    # with the plates: the next loadable weight
    t = next_target(pe, ex, hist, weights=[28.5, 29.5, 30.5])
    assert (t.duration_s, t.weight_kg) == (30, 29.5)
    # the user's own heavier step at 30 s: 35 s next (not the plan's 50)
    hist = _holds(("2026-10-07", "good", 33, [30] * 3), ("2026-10-01", None, 30.5, [45] * 3)) + hist
    t = next_target(pe, ex, hist)
    assert (t.duration_s, t.weight_kg) == (35, 33)
    # missed once: same again; twice: 5 s less, not below the bottom
    t = next_target(pe, ex, _holds(("2026-10-11", "hard", 33, [33] * 3)) + hist)
    assert (t.duration_s, t.notes) == (35, ["missed once, same again"])
    t = next_target(
        pe, ex, _holds(("2026-10-14", None, 33, [32] * 3), ("2026-10-11", "hard", 33, [33] * 3)) + hist
    )
    assert (t.duration_s, t.notes) == (30, ["missed twice: 5 s less"])
    # without a load there is no step: the top stays
    t = next_target(
        {**pe, "target_weight_kg": None}, {"kind": "time"}, _holds(("2026-10-01", None, None, [45] * 3))
    )
    assert (t.duration_s, t.notes) == (45, ["top of the range: add load or a harder variant"])


def test_loaded_holds_keep_their_load():
    pe = {"sets": 3, "duration_s": 40, "target_weight_kg": 30}
    ex = {"kind": "time"}
    assert next_target(pe, ex, []).weight_kg == 30
    hist = [{"date": "2026-09-20", "rating": "good", "sets": [{"duration_s": 45, "weight_kg": 32.5}] * 3}]
    t = next_target(pe, ex, hist)
    assert (t.duration_s, t.weight_kg) == (50, 32.5)


def test_e1rm_and_hard_sets():
    assert e1rm(100, 5) == 116.7 and e1rm(100, 1) == 100 and e1rm(100, 15) is None and e1rm(0, 5) is None
    exs = {
        "bench": {"kind": "reps", "muscles": ["chest"], "muscles_secondary": ["triceps", "shoulders"]},
        "plank": {"kind": "time", "muscles": ["abs"], "muscles_secondary": []},
    }
    sets = [
        {"exercise_id": "bench", "repetitions": 8},
        {"exercise_id": "bench", "repetitions": 8},
        {"exercise_id": "bench", "repetitions": 10, "warmup": True},
        {"exercise_id": "bench", "repetitions": 8, "hidden": True},
        {"exercise_id": "plank", "repetitions": None, "duration_s": 45},
        {"exercise_id": None, "repetitions": 8},
    ]
    assert training.hard_sets(sets, exs) == {"chest": 2, "triceps": 1, "shoulders": 1, "abs": 1}


def test_default_muscles_from_the_garmin_category():
    assert training.default_muscles("curl") == (["biceps"], ["forearms"])
    assert training.default_muscles("unknown") == ([], [])
    for prim, sec in training.CATEGORY_MUSCLES.values():
        assert set(prim) | set(sec) <= set(training.MUSCLES)
    for _, prim, sec in training.NAME_MUSCLES:
        assert set(prim) | set(sec) <= set(training.MUSCLES) and not set(prim) & set(sec)


def test_default_muscles_from_the_garmin_exercise_or_the_name():
    d = training.default_muscles
    # the watch files front raises, dips and rear raises under lateral_raise
    assert d("lateral_raise", "front_raise") == (["delts_front"], ["delts_side"])
    assert d("lateral_raise", "weighted_ring_dip")[0] == ["triceps", "chest_lower"]
    assert d("lateral_raise", "bent_over_lateral_raise") == (["delts_rear"], ["upper_back"])
    assert d("lateral_raise", "dumbbell_lateral_raise") == (["delts_side"], ["traps"])
    assert d("bench_press", "incline_dumbbell_bench_press")[0] == ["chest_upper"]
    assert d("bench_press", "barbell_bench_press") == d("bench_press")
    assert d("push_up", "decline_push_up")[0] == ["chest_upper"]  # feet up: the upper chest
    assert d("push_up", "incline_push_up")[0] == ["chest_lower"]
    assert d("row", "face_pull") == (["delts_rear"], ["rotator_cuff", "upper_back"])
    assert d("shrug", "barbell_upright_row")[0] == ["delts_side"]
    assert d("shoulder_stability", "band_external_rotation")[0] == ["rotator_cuff"]
    # own names, German or English, without a watch exercise
    assert d(None, None, "Bankdrücken (schräg)")[0] == ["chest_upper"]
    assert d(None, None, "Lean-away Seitheben")[0] == ["delts_side"]
    assert d(None, None, "Nackenbeugen mit Scheibe") == (["neck"], [])
    assert d(None, None, "Nackendrücken")[0] == ["delts_front"]  # a press, not a neck exercise
    assert d(None, None, "Incline Curls") == ([], [])  # "incline" alone is no chest exercise
    assert d("curl", None, "Incline Curls") == (["biceps"], ["forearms"])


def test_expand_takes_the_old_coarse_muscles():
    assert training.expand(["shoulders", "triceps"], ["chest", "delts_front"]) == (
        ["delts_front", "delts_side", "delts_rear", "triceps"],
        ["chest_upper", "chest_lower"],
    )
    assert training.expand(["back"]) == (["lats"], [])
    assert set(training.GROUP_OF) == set(training.MUSCLES)
    assert list(training.MUSCLES) == [m for ms in training.MUSCLE_GROUPS.values() for m in ms]


def test_refine_replaces_coarse_muscles_and_keeps_own_choices():
    from vitalocal import muscle_refine

    def r(name, prim, sec, category=None, garmin_name=None, library_key=None):
        return muscle_refine.refine({"name": name, "muscles": prim, "muscles_secondary": sec,
                                     "garmin_category": category, "garmin_name": garmin_name,
                                     "library_key": library_key})  # fmt: skip

    # flat press: the mid/lower chest, the upper chest as a helper; the own choices stay
    assert r("Kurzhantel-Bankdrücken (flach)", ["chest"], ["shoulders", "triceps"], "bench_press") == (
        ["chest_lower"],
        ["chest_upper", "delts_front", "triceps"],
    )
    assert r("Bankdrücken (schräg)", ["chest"], ["shoulders", "triceps"]) == (
        ["chest_upper"],
        ["delts_front", "triceps"],
    )
    # traps on a reverse fly or a row were the mid back; on a lateral raise they stay the upper traps
    assert r(
        "Brustgestützte Reverse Flys (Kurzhantel)",
        ["shoulders"],
        ["traps", "back"],
        "flye",
        "incline_reverse_flye",
    ) == (["delts_rear"], ["upper_back"])
    assert r("Rudern", ["back"], ["biceps", "traps", "shoulders"], "row") == (
        ["lats", "upper_back"],
        ["biceps", "delts_rear"],
    )
    assert r("Seitheben", ["shoulders"], ["traps"], "lateral_raise") == (["delts_side"], ["traps"])
    assert r("Schulterdrücken", ["shoulders"], ["triceps"], "shoulder_press") == (
        ["delts_front"],
        ["delts_side", "triceps"],
    )
    # dips with the chest primary keep it, as the mid/lower chest
    assert r("Dips", ["triceps", "chest"], ["shoulders"]) == (["triceps", "chest_lower"], ["delts_front"])
    # nothing known about the part: what the coarse muscle stands for
    assert r("Farmers walk", ["forearms", "traps"], ["abs", "shoulders"], "carry") == (
        ["forearms", "traps"],
        ["abs", "delts_front", "delts_side", "delts_rear"],
    )
    # the library entry it was taken over from
    assert r(
        "Kreuzheben",
        ["hamstrings", "glutes", "lower_back"],
        ["back", "traps", "forearms", "quads"],
        "deadlift",
        library_key="barbell_deadlift",
    ) == (["hamstrings", "glutes", "lower_back"], ["lats", "upper_back", "traps", "forearms", "quads"])
    # already fine: unchanged
    assert r("Seitheben", ["delts_side"], ["traps"], "lateral_raise") == (["delts_side"], ["traps"])
    assert muscle_refine.coarsen(["delts_rear", "neck"], ["upper_back", "lats"]) == (
        ["shoulders"],
        ["traps", "back"],
    )


def test_suggest_rating_compares_sets_with_the_target():
    target = {"sets": 3, "reps": 10, "weight_kg": 15.0}
    s = lambda r, w=15.0: {"repetitions": r, "weight_kg": w}  # noqa: E731
    # pull-ups 3×10 @ 15 kg planned, 10/10/8 done: target missed → too hard
    assert training.suggest_rating(target, [s(10), s(10), s(8)]) == {"rating": "hard", "why": "target missed"}
    assert training.suggest_rating(target, [s(10), s(10)])["why"] == "fewer sets"
    assert training.suggest_rating(target, [s(10), s(10), s(10, 12.5)])["why"] == "lighter weight"
    assert training.suggest_rating(target, [s(10), s(11), s(12)]) == {"rating": "good", "why": "target met"}
    assert training.suggest_rating(target, [s(13), s(14), s(13)])["rating"] == "easy"
    assert training.suggest_rating(target, []) is None
    plank = {"sets": 2, "duration_s": 45}
    d = lambda x: {"duration_s": x}  # noqa: E731
    assert training.suggest_rating(plank, [d(45), d(40)], "time")["rating"] == "hard"
    assert training.suggest_rating(plank, [d(50), d(46)], "time")["rating"] == "good"
    assert training.suggest_rating(plank, [d(60), d(62)], "time")["rating"] == "easy"


def test_deload_target_is_one_set_less_at_the_bottom_of_the_range():
    pe = {"reps_min": 8, "reps_max": 12, "duration_min_s": 30}
    t = training.deload_target(training.Target(3, reps=11, weight_kg=20), pe, {"kind": "reps"})
    assert (t.sets, t.reps, t.weight_kg, t.reason) == (2, 8, 20, "deload")
    t = training.deload_target(training.Target(1, duration_s=45, weight_kg=10), pe, {"kind": "time"})
    assert (t.sets, t.duration_s, t.weight_kg) == (1, 30, 10)
    # a weight step the regular suggestion would take is not taken in the deload
    t = training.deload_target(training.Target(3, reps=10, weight_kg=10), pe, {"kind": "reps"}, last_weight=8)
    assert (t.sets, t.reps, t.weight_kg) == (2, 8, 8)


def test_balance_compares_opposing_patterns():
    b = training.balance(
        {"h_push": 9, "v_push": 3, "h_pull": 6, "biceps": 6, "triceps": 6}, {"h_push": 60, "h_pull": 45}
    )
    v = {x["key"]: x for x in b["volume"]}
    assert (v["push_pull"]["ratio"], v["push_pull"]["state"]) == (2.0, "high")
    assert v["vertical"]["state"] == "high" and v["arms"]["state"] == "ok"  # pressing overhead only
    assert v["knee_hip"]["state"] == "missing"
    s = {x["key"]: x for x in b["strength"]}
    assert (s["row_bench"]["ratio"], s["row_bench"]["state"]) == (0.75, "low")
    assert s["squat_hinge"]["state"] == "missing"
