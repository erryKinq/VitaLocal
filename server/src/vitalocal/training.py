"""Strength training rules without a database: muscles, plan-day and set suggestions, double
progression, estimated 1RM and hard sets per muscle group.

- A session is matched to a plan day by rotation (the day after the last confirmed or skipped one)
  unless the recognised sets clearly fit another day better.
- A plan exercise matches the watch categories of its exercise plus those the watch reported for it
  in reviewed sessions (an incline curl the watch calls "curl", reverse flys it calls "lateral_raise").
- Watch sets are assigned to the plan day's exercises in order; the watch's exercise category
  moves the assignment to the exercise it matches, also an earlier one (any order of exercises).
- Double progression per plan exercise, replayed over the confirmed performances: a target is met
  when as many sets as planned reach its reps at its weight. Met → +1 rep until the top of the range,
  then a weight step; too easy → the weight step now; missed → same again, never more.
  Missed twice in a row → one set more with two reps less (at most the best set's reps) at the same
  weight (volume variant); met or too easy there → back to the target that was missed. More sets
  than planned at fewer reps are the volume variant done on one's own (at the reps most sets
  reached; back to the target it took the place of). Missed twice in the volume variant, or two reps
  less would leave the range → one weight step down at the top of the range, unless the planned sets
  got more reps than last time.
- Another weight than the target's, the first session after a weight step (its reps were an estimate)
  or a pause of more than BREAK_DAYS starts over at what that session went for; before the first
  session after such a pause the suggestion is the last level.
- A weight step goes to the heaviest weight the dumbbells can be loaded to where Epley still expects
  the bottom of the range, starting one rep below that estimate; without plates by `increment_kg`.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

MUSCLES: dict[str, str] = {
    "chest_upper": "obere Brust",
    "chest_lower": "mittlere/untere Brust",
    "delts_front": "vordere Schulter",
    "delts_side": "seitliche Schulter",
    "delts_rear": "hintere Schulter",
    "rotator_cuff": "Rotatorenmanschette",
    "lats": "Latissimus",
    "upper_back": "oberer Rücken",
    "traps": "oberer Trapez",
    "lower_back": "unterer Rücken",
    "neck": "Nacken",
    "biceps": "Bizeps",
    "triceps": "Trizeps",
    "forearms": "Unterarme",
    "abs": "Bauch",
    "obliques": "seitliche Bauchmuskeln",
    "serratus": "Serratus anterior (vorderer Sägemuskel)",
    "glutes": "Gesäß",
    "quads": "Oberschenkel vorn",
    "hamstrings": "Oberschenkel hinten",
    "adductors": "Adduktoren (Oberschenkel innen)",
    "hip_flexors": "Hüftbeuger",
    "calves": "Waden",
    "tibialis": "Schienbeinmuskel",
}

MUSCLES_EN: dict[str, str] = {
    "chest_upper": "Upper chest",
    "chest_lower": "Mid/lower chest",
    "delts_front": "Front delts",
    "delts_side": "Side delts",
    "delts_rear": "Rear delts",
    "rotator_cuff": "Rotator cuff",
    "lats": "Lats",
    "upper_back": "Upper back",
    "traps": "Upper traps",
    "lower_back": "Lower back",
    "neck": "Neck",
    "biceps": "Biceps",
    "triceps": "Triceps",
    "forearms": "Forearms",
    "abs": "Abs",
    "obliques": "Obliques",
    "serratus": "Serratus anterior",
    "glutes": "Glutes",
    "quads": "Quads",
    "hamstrings": "Hamstrings",
    "adductors": "Adductors (inner thigh)",
    "hip_flexors": "Hip flexors",
    "calves": "Calves",
    "tibialis": "Tibialis",
}

# Muscle groups in display order: headings of the choice, summed volume rows.
MUSCLE_GROUPS: dict[str, tuple[str, ...]] = {
    "chest": ("chest_upper", "chest_lower"),
    "shoulders": ("delts_front", "delts_side", "delts_rear", "rotator_cuff"),
    "back": ("lats", "upper_back", "traps", "lower_back", "neck"),
    "arms": ("biceps", "triceps", "forearms"),
    "core": ("abs", "obliques", "serratus"),
    "legs": ("glutes", "quads", "hamstrings", "adductors", "hip_flexors", "calves", "tibialis"),
}
GROUP_LABELS: dict[str, str] = {
    "chest": "Brust", "shoulders": "Schultern", "back": "Rücken und Nacken", "arms": "Arme", "core": "Rumpf",
    "legs": "Beine und Gesäß",
}  # fmt: skip
GROUP_LABELS_EN: dict[str, str] = {
    "chest": "Chest", "shoulders": "Shoulders", "back": "Back and neck", "arms": "Arms", "core": "Core",
    "legs": "Legs and glutes",
}  # fmt: skip
GROUP_OF: dict[str, str] = {m: g for g, ms in MUSCLE_GROUPS.items() for m in ms}
# The coarse muscles before 2026-10-25, still taken on input: what each stands for.
ALIASES: dict[str, tuple[str, ...]] = {
    "chest": ("chest_upper", "chest_lower"),
    "shoulders": ("delts_front", "delts_side", "delts_rear"),
    "back": ("lats",),
}

# What an exercise needs; an exercise without equipment is done with the body alone.
EQUIPMENT: dict[str, str] = {
    "barbell": "Langhantel",
    "ez_bar": "SZ-Stange",
    "dumbbell": "Kurzhanteln",
    "kettlebell": "Kettlebell",
    "bench": "Bank",
    "cable": "Kabelzug",
    "machine": "Maschinen (Studio)",
    "band": "Widerstandsband",
    "pull_up_bar": "Klimmzugstange",
    "dip_bars": "Dipbarren",
    "weight_belt": "Dip-/Gewichtsgürtel",
    "weight_vest": "Gewichtsweste",
    "ab_wheel": "Bauchroller",
    "rings": "Ringe / Schlingentrainer",
    "rack": "Rack / Hantelablage",
    "trap_bar": "Trap-Bar (Hex-Bar)",
}
# what a piece of equipment stands for (shown with the choice)
EQUIPMENT_HINTS: dict[str, str] = {
    "barbell": "Lange Stange mit Scheiben (Kniebeuge und Bankdrücken brauchen zusätzlich ein Rack)",
    "ez_bar": "Gekröpfte Stange für Curls und Trizepsübungen",
    "dumbbell": "Kurzhanteln, fest oder mit Scheiben verstellbar",
    "kettlebell": "Kugelhantel",
    "bench": "Flach- oder Schrägbank",
    "cable": "Kabelzug mit Griffen oder Seil (Latzug, Kabelturm)",
    "machine": "Geräte im Studio: Beinpresse, Hackenschmidt, Beinstrecker und -beuger, Brust-, Schulter- und "
    "Rudermaschine, Butterfly, Ab-/Adduktoren, Wadenheben sitzend, Multipresse, Hyperextension-Bank",
    "band": "Gummi- oder Stoffband mit Widerstand",
    "pull_up_bar": "Stange zum Hängen, auch Türreck",
    "dip_bars": "Barren oder Dipstation",
    "weight_belt": "Gürtel mit Kette für Zusatzgewicht: Klimmzüge und Dips lassen sich weiter steigern, "
    "wenn die Wiederholungen mit dem Körpergewicht zu leicht werden",
    "weight_vest": "Weste mit Zusatzgewicht für Liegestütz, Klimmzüge und Kniebeugen ohne Hantel",
    "ab_wheel": "Rad mit Griffen für den Bauch",
    "rings": "Turnringe oder Schlingentrainer (TRX): Dips, Rudern, Klimmzüge, Liegestütz, Curls, Fliegende",
    "rack": "Kniebeugeständer oder Power-Rack mit Ablagen – nötig für Kniebeugen und Bankdrücken mit der "
    "Langhantel",
    "trap_bar": "Sechseckige Stange: Kreuzheben mit aufrechterem Oberkörper, schont den unteren Rücken",
}

EQUIPMENT_EN: dict[str, str] = {
    "barbell": "Barbell",
    "ez_bar": "EZ bar",
    "dumbbell": "Dumbbells",
    "kettlebell": "Kettlebell",
    "bench": "Bench",
    "cable": "Cable",
    "machine": "Machines (gym)",
    "band": "Resistance band",
    "pull_up_bar": "Pull-up bar",
    "dip_bars": "Dip bars",
    "weight_belt": "Dip / weight belt",
    "weight_vest": "Weight vest",
    "ab_wheel": "Ab wheel",
    "rings": "Rings / suspension trainer",
    "rack": "Rack / squat stands",
    "trap_bar": "Trap bar (hex bar)",
}
EQUIPMENT_HINTS_EN: dict[str, str] = {
    "barbell": "Long bar with plates (squats and bench press also need a rack)",
    "ez_bar": "Cambered bar for curls and triceps exercises",
    "dumbbell": "Dumbbells, fixed or adjustable with plates",
    "kettlebell": "Kettlebell",
    "bench": "Flat or incline bench",
    "cable": "Cable station with handles or rope (lat pulldown, cable tower)",
    "machine": "Gym machines: leg press, hack squat, leg extension and curl, chest, shoulder and row "
    "machines, pec deck, hip abduction/adduction, seated calf raise, Smith machine, back extension bench",
    "band": "Rubber or fabric band with resistance",
    "pull_up_bar": "Bar to hang from, also a doorway bar",
    "dip_bars": "Parallel bars or a dip station",
    "weight_belt": "Belt with a chain for extra weight: pull-ups and dips keep progressing once body weight "
    "gets too easy",
    "weight_vest": "Vest with extra weight for push-ups, pull-ups and squats without a barbell",
    "ab_wheel": "Wheel with handles for the abs",
    "rings": "Gymnastic rings or a suspension trainer (TRX): dips, rows, pull-ups, push-ups, curls, flyes",
    "rack": "Squat stands or a power rack with safeties – needed for barbell squats and bench press",
    "trap_bar": "Hexagonal bar: deadlifts with a more upright torso, easier on the lower back",
}


def muscles(lang: str = "de") -> dict[str, str]:
    return MUSCLES_EN if lang == "en" else MUSCLES


def groups(lang: str = "de") -> list[dict]:
    labels = GROUP_LABELS_EN if lang == "en" else GROUP_LABELS
    return [{"key": g, "label": labels[g], "muscles": list(ms)} for g, ms in MUSCLE_GROUPS.items()]


def expand(primary: list[str], secondary: list[str] = ()) -> tuple[list[str], list[str]]:
    """Muscles with the old coarse ones (`ALIASES`) replaced by what they stand for, each once (a
    primary muscle is not secondary as well)."""

    def ex(ms):
        return [x for m in ms for x in ALIASES.get(m, (m,))]

    p = list(dict.fromkeys(ex(primary)))
    return p, [m for m in dict.fromkeys(ex(secondary)) if m not in p]


def equipment(lang: str = "de") -> dict[str, str]:
    return EQUIPMENT_EN if lang == "en" else EQUIPMENT


def equipment_hints(lang: str = "de") -> dict[str, str]:
    return EQUIPMENT_HINTS_EN if lang == "en" else EQUIPMENT_HINTS


# Garmin exercise category -> (primary, secondary) muscles; a default for new exercises.
CATEGORY_MUSCLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "bench_press": (("chest_lower",), ("chest_upper", "delts_front", "triceps")),
    "push_up": (("chest_lower",), ("chest_upper", "delts_front", "triceps")),
    "flye": (("chest_lower",), ("chest_upper", "delts_front")),
    "row": (("lats", "upper_back"), ("delts_rear", "biceps")),
    "pull_up": (("lats",), ("upper_back", "biceps")),
    "curl": (("biceps",), ("forearms",)),
    "triceps_extension": (("triceps",), ()),
    "lateral_raise": (("delts_side",), ("traps",)),
    "shoulder_press": (("delts_front",), ("delts_side", "triceps")),
    "shoulder_stability": (("rotator_cuff",), ("delts_rear",)),
    "shrug": (("traps",), ()),
    "squat": (("quads", "glutes"), ("hamstrings", "adductors")),
    "lunge": (("quads", "glutes"), ("hamstrings", "adductors")),
    "deadlift": (("hamstrings", "glutes", "lower_back"), ("upper_back", "traps", "forearms")),
    "hip_raise": (("glutes",), ("hamstrings",)),
    "leg_curl": (("hamstrings",), ()),
    "calf_raise": (("calves",), ()),
    "hyperextension": (("lower_back",), ("glutes", "hamstrings")),
    "crunch": (("abs",), ()),
    "sit_up": (("abs",), ("hip_flexors",)),
    "plank": (("abs",), ("obliques", "delts_front")),
    "core": (("abs",), ("obliques",)),
    "leg_raise": (("abs", "hip_flexors"), ()),
    "chop": (("obliques",), ("abs",)),
    "carry": (("forearms", "traps"), ("abs", "obliques")),
}
# Exercises that train other muscles than their Garmin category says (the watch files front raises,
# dips and rear raises under lateral_raise, face pulls under row …): (pattern, primary, secondary),
# the first match wins. Matched against the Garmin exercise name (words, without "weighted") and the
# exercise's own name (German or English, lower case); a pattern names enough of the movement not to
# catch an "incline curl" with the incline bench press.
NAME_MUSCLES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    (r"neck (curl|flexion|extension|harness)|nacken(beug|streck|heb)|seitliches nacken", ("neck",), ()),
    (r"face ?pull", ("delts_rear",), ("rotator_cuff", "upper_back")),
    (r"(external|internal) rotation|(außen|innen)rotation", ("rotator_cuff",), ("delts_rear",)),
    (r"\b[iytwl] raise|scaption|y-heben|t-heben", ("upper_back", "delts_rear"), ("rotator_cuff",)),
    (
        r"reverse fly|reverse flye|rear (delt|lateral|flye|fly)|rear flye|bent over lateral|reverse butterfly"
        r"|reverse flys|hintere schulter",
        ("delts_rear",),
        ("upper_back",),
    ),
    (r"upright row|aufrechtes rudern", ("delts_side",), ("traps", "delts_front")),
    (r"front raise|frontheben|plate raises", ("delts_front",), ("delts_side",)),
    (r"lateral raise|seitheben|v raise|diagonal raise|combo shoulder raise", ("delts_side",), ("traps",)),
    (r"arnold", ("delts_front", "delts_side"), ("triceps",)),
    (r"serratus|push up plus|scapular push", ("serratus",), ()),
    (r"scapular retraction", ("upper_back",), ()),
    (r"shrug", ("traps",), ()),
    (
        r"handstand push|pike push|shoulder push|inverted shoulder press|military"
        r"|overhead (barbell |dumbbell )?press|shoulder press|push press|strict press"
        r"|schulterdrücken|überkopfdrücken|nackendrücken",
        ("delts_front",),
        ("delts_side", "triceps"),
    ),
    (r"muscle up", ("lats",), ("triceps", "chest_lower", "biceps")),
    (r"rope climb", ("lats", "biceps"), ("forearms",)),
    (r"\bdips?\b|ring dip|barrendips", ("triceps", "chest_lower"), ("delts_front",)),
    (
        r"close (grip|hands).*(bench|push)|diamond push|enges bankdrücken|enge liegestütz",
        ("triceps",),
        ("chest_lower", "delts_front"),
    ),
    (r"decline push|feet elevated push|füße erhöht", ("chest_upper",), ("delts_front", "triceps")),
    (r"incline push|hände erhöht", ("chest_lower",), ("delts_front", "triceps")),
    (
        r"incline.*(bench|press|flye|fly)|schrägbank|schräg.*(bank|drück|flieg)|(drück|flieg).*schräg",
        ("chest_upper",),
        ("delts_front", "triceps"),
    ),
    (r"decline.*(bench|press|flye|fly)|negativ.*(bank|drück|flieg)", ("chest_lower",), ("triceps",)),
    (r"straight arm pulldown|pullover", ("lats",), ("chest_lower", "triceps")),
    (r"pulldown|latzug|pull ?ups?\b|chin ?ups?\b|klimmz", ("lats",), ("upper_back", "biceps")),
    (r"\brow\b|rudern", ("lats", "upper_back"), ("delts_rear", "biceps")),
]
NON_STRENGTH = {"cardio", "warm_up", "run", "run_indoor", "bike", "bike_outdoor", "indoor_bike", "elliptical",
                "stair_stepper", "indoor_row", "move", "pose", "cardio_sensors"}  # fmt: skip
RATINGS = ("easy", "good", "hard")


def default_muscles(
    category: str | None, garmin_name: str | None = None, name: str | None = None
) -> tuple[list[str], list[str]]:
    """What an exercise trains by default: the first of `NAME_MUSCLES` its Garmin exercise name or its
    own name matches, else the default of its Garmin category."""
    words = (garmin_name or "").replace("_", " ").removeprefix("weighted ")
    for text_ in (words, (name or "").lower()):
        if not text_:
            continue
        for pattern, p, s in NAME_MUSCLES:
            if re.search(pattern, text_):
                return list(p), list(s)
    p, s = CATEGORY_MUSCLES.get(category or "", ((), ()))
    return list(p), list(s)


# --- suggestions --------------------------------------------------------------------------------------


def suggest_flags(s: dict, index: int) -> dict:
    """Proposed warm-up/hidden for one watch set: warm-up blocks and empty sets are no work sets."""
    cat = s.get("garmin_category")
    reps = s.get("repetitions")
    if cat in NON_STRENGTH or (index == 0 and (s.get("duration_s") or 0) > 300 and not reps):
        return {"warmup": True, "hidden": False}
    if (s.get("duration_s") or 0) < 10:
        return {"warmup": False, "hidden": True}  # a few seconds: the watch's artefact after the warm-up
    if not reps and (s.get("duration_s") or 0) < 20:
        return {"warmup": False, "hidden": True}  # 0 reps, a few seconds: accidental set
    if not reps and not cat and not s.get("weight_kg"):
        return {"warmup": False, "hidden": True}  # a loaded hold (farmer's walk) is a work set
    return {"warmup": False, "hidden": False}


def entry_categories(e: dict) -> set[str]:
    """Watch categories a plan exercise matches: `garmin_categories` (learned) or its `garmin_category`."""
    cats = set(e.get("garmin_categories") or ())
    if e.get("garmin_category"):
        cats.add(e["garmin_category"])
    return cats


def suggest_day(days: list[dict], last_day_id, categories: list[str]) -> tuple[str | None, str]:
    """(plan day id, reason). days: [{id, exercises: [{garmin_categories | garmin_category}]}] in
    rotation order; categories: the watch category per work set.

    A day scores the share of recognised sets whose category one of its exercises matches. Another
    day than the rotation's wins when it covers at least half of them and a quarter more than the
    rotation's day.
    """
    if not days:
        return None, "no active plan"
    # a day without exercises (e.g. the empty day a new plan starts with) is not suggested
    days = [d for d in days if d["exercises"]] or days
    ids = [d["id"] for d in days]
    rotation = ids[(ids.index(last_day_id) + 1) % len(ids)] if last_day_id in ids else ids[0]
    seen = [c for c in categories if c and c not in NON_STRENGTH]
    if not seen:
        return rotation, "rotation"

    def score(d: dict) -> float:
        cats = set().union(*(entry_categories(e) for e in d["exercises"])) if d["exercises"] else set()
        return sum(c in cats for c in seen) / len(seen)

    best = max(days, key=score)
    rot = next(d for d in days if d["id"] == rotation)
    if best["id"] != rotation and score(best) >= 0.5 and score(best) - score(rot) >= 0.25:
        return best["id"], "exercises match"
    return rotation, "rotation"


def assign_sets(sets: list[dict], plan: list[dict]) -> list[str | None]:
    """Plan exercise id per work set. sets: [{garmin_category}] in order; plan: [{id, sets,
    garmin_categories | garmin_category}].

    The order the exercises were done in does not matter as long as the watch recognises them: a
    recognised category moves to the plan exercise that has it and still lacks sets, a later one
    first, else an earlier one. It moves only when the current exercise has its sets or the next
    set agrees too, so that a single misrecognised set (a row taken for a lunge) does not jump
    away. A set the watch did not recognise stays with the current exercise until that has its
    sets, then goes to the next exercise still lacking sets. A set with the same weight as the one
    before stays with the current exercise unless the watch recognised another one: the weight is
    entered per exercise, so an unchanged weight means an extra set, and a new weight after an
    exercise that has its sets means the next exercise even when the watch kept the category.
    """
    if not plan:
        return [None] * len(sets)
    cats = [s.get("garmin_category") for s in sets]
    has = [entry_categories(pe) for pe in plan]
    done = [0] * len(plan)
    open_ = lambda j: done[j] < plan[j]["sets"]  # noqa: E731
    # plan positions after i first, then the ones before it (wrapping around)
    others = lambda i: [*range(i + 1, len(plan)), *range(i)]  # noqa: E731
    out: list[str | None] = []
    i, prev_w = 0, None
    for k, cat in enumerate(cats):
        full = not open_(i)
        w = sets[k].get("weight_kg")
        same_weight = w is not None and w == prev_w
        match = next((j for j in others(i) if cat and cat in has[j] and open_(j)), None)
        if same_weight and full and match is None:
            pass  # extra set of the current exercise
        elif full and match is None and w is not None and prev_w is not None:
            i = next(
                (j for j in others(i) if open_(j)), i
            )  # a new weight after a done exercise: the next one
        elif not (cat and cat in has[i]):
            nxt = cats[k + 1] if k + 1 < len(cats) else None
            if match is not None and (full or nxt == cat):
                i = match
            elif full:
                i = next((j for j in others(i) if open_(j)), i)
        out.append(plan[i]["id"])
        done[i] += 1
        prev_w = w
    return out


# --- progression --------------------------------------------------------------------------------------


@dataclass
class Target:
    sets: int
    reps: int | None = None
    weight_kg: float | None = None
    duration_s: int | None = None
    reason: str = "plan"
    based_on: str | None = None  # date of the performance it builds on
    unrated: bool = False
    notes: list[str] = field(default_factory=list)


BREAK_DAYS = 28  # a longer pause without the exercise starts the progression over
DELOAD_DAYS = 7  # a deload week


def deload_target(t: Target, pe: dict, ex: dict, last_weight: float | None = None) -> Target:
    """The target in a deload week: one set less (at least one), the same weight – the one trained last
    time, never a step up the regular suggestion would take –, reps (or seconds) at the bottom of the
    range: lighter work that keeps the movement without adding fatigue."""
    sets = max(1, t.sets - 1)
    if last_weight is not None and t.weight_kg is not None:
        t = Target(t.sets, reps=t.reps, weight_kg=min(t.weight_kg, last_weight), duration_s=t.duration_s,
                   reason=t.reason, based_on=t.based_on)  # fmt: skip
    if ex.get("kind") == "time":
        dur = pe.get("duration_min_s") or t.duration_s
        return Target(sets, weight_kg=t.weight_kg, duration_s=dur, reason="deload", based_on=t.based_on)
    reps = pe.get("reps_min") or t.reps
    return Target(sets, reps=reps, weight_kg=t.weight_kg, reason="deload", based_on=t.based_on)


PLATES = {0.5: 4, 1.25: 8, 4: 4, 5: 6, 10: 5}  # default plate set: weight (kg) → how many
HANDLE_KG = 0.5  # one dumbbell handle with its collars, as the weights are entered


def aimed(reps: list[int]) -> int:
    """The reps a session went for: what most of its sets reached (a tie: the higher)."""
    c = Counter(reps)
    return max(c, key=lambda r: (c[r], r))


def plate_weights(handle_kg: float, plates: dict[float, int], per_set: int = 1) -> list[float]:
    """Weights a pair of adjustable dumbbells can be loaded to, both alike, ascending: each of the four
    sides takes a quarter of every plate size. per_set 2: the sum of both dumbbells."""
    sides = {0.0}
    for kg, count in plates.items():
        sides = {round(s + k * kg, 3) for s in sides for k in range(int(count) // 4 + 1)}
    return sorted({round(per_set * (handle_kg + 2 * s), 2) for s in sides})


def epley_reps(reps: float, weight: float, other: float) -> float:
    """Reps at `other` worth `reps` at `weight` (Epley: weight × (1 + reps/30) stays the same)."""
    return (reps + 30) * weight / other - 30


def _as_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def _reps_at(work: list[dict], w: float | None) -> list[int]:
    """Reps of the sets at least as heavy as w, most first."""
    return sorted((s["reps"] for s in work if (s.get("weight_kg") or 0) >= (w or 0) - 1e-9), reverse=True)


def next_target(
    pe: dict, ex: dict, history: list[dict], today: date | None = None, weights: list[float] | None = None
) -> Target:
    """Next target for a plan exercise.

    pe: sets, reps_min, reps_max, target_reps, target_weight_kg, duration_s, duration_min_s, duration_max_s
    ex: kind, increment_kg, bodyweight
    history: confirmed performances, newest first: [{date, rating, sets: [{reps, weight_kg, duration_s}]}]
    today: the day it is for; more than BREAK_DAYS after the last performance it is the last level
    weights: what the exercise can be loaded to (adjustable dumbbells), ascending; None: `increment_kg`
    """
    t = (
        _next_time(pe, ex, history, weights)
        if ex.get("kind") == "time"
        else _next_reps(pe, ex, history, weights)
    )
    if history and today is not None:
        days = (today - _as_date(history[0]["date"])).days
        level = _last_level(pe, ex, history[0]) if days > BREAK_DAYS else None
        if level is not None:
            level.reason, level.based_on, level.unrated = t.reason, t.based_on, t.unrated
            level.notes = [f"{days // 7} weeks without it: start at the last level"]
            return level
    return t


def _last_level(pe: dict, ex: dict, last: dict) -> Target | None:
    """What the last performance reached, as a target (the start after a long pause)."""
    n = pe["sets"]
    if ex.get("kind") == "time":
        done = [s.get("duration_s") or 0 for s in last["sets"] if (s.get("duration_s") or 0) > 0]
        loads = [s["weight_kg"] for s in last["sets"] if s.get("weight_kg")]
        if not done:
            return None
        weight = min(loads) if loads else pe.get("target_weight_kg")
        return Target(n, weight_kg=weight, duration_s=int(min(done)))
    work = [s for s in last["sets"] if (s.get("reps") or 0) > 0]
    if not work:
        return None
    lo = pe.get("reps_min") or pe.get("target_reps") or 8
    w = min(s.get("weight_kg") or 0 for s in work)
    return Target(n, min(max(pe.get("reps_max") or lo, lo), aimed(_reps_at(work, w))), w or None)


def _next_time(pe: dict, ex: dict, history: list[dict], weights: list[float] | None) -> Target:
    """Time exercises, replayed like the reps: met → the shortest set + 5 s (too easy: + 10 s); with a
    range (duration_min_s–duration_max_s) its top met → the next weight, back to the bottom. Missed →
    the same again, missed twice → 5 s less (not below the bottom)."""
    n = pe["sets"]
    lo = pe.get("duration_min_s") or pe.get("duration_s") or 30
    hi = pe.get("duration_max_s")
    start = pe.get("duration_s") or lo
    t = Target(n, weight_kg=pe.get("target_weight_kg"), duration_s=min(hi, start) if hi else start)
    if not history:
        return t

    def heavier(w: float | None) -> float | None:
        if not w:
            return None  # a hold without load: nothing to add
        if weights:
            up = [x for x in weights if x > w + 1e-9]
            return up[0] if up else None
        return round(w + (ex.get("increment_kg") or 2.5), 2)

    misses, prev_day, stepped = 0, None, False
    for perf in reversed(history):
        done = [int(s.get("duration_s") or 0) for s in perf["sets"] if (s.get("duration_s") or 0) > 0]
        rating = perf.get("rating") or "good"
        t.notes = []
        if not done:
            t.notes.append("no work sets recorded")
            continue
        day = _as_date(perf["date"])
        pause = prev_day is not None and (day - prev_day).days > BREAK_DAYS
        prev_day = day
        # a loaded hold (farmer's walk) is judged with the load it was done with
        loads = [s["weight_kg"] for s in perf["sets"] if s.get("weight_kg")]
        w_done = min(loads) if loads else t.weight_kg
        other = w_done is not None and (t.weight_kg is None or abs(w_done - t.weight_kg) > 1e-9)
        base = min(done)
        if other or pause or stepped:
            # another load, the first session after a weight step or a long pause: the target is what
            # this session did, judged as usual
            t = Target(n, weight_kg=w_done, duration_s=min(hi, base) if hi else base)
            misses, stepped = 0, False
        w = t.weight_kg
        if len(done) < n:
            t.notes.append(f"{len(done)} of {n} sets done")
        if rating == "easy" or (rating != "hard" and len(done) >= n and base >= t.duration_s):
            if hi and base >= hi:
                up = heavier(w)
                if up is not None:
                    t, stepped = Target(n, weight_kg=up, duration_s=lo), True
                else:
                    t = Target(
                        n,
                        weight_kg=w,
                        duration_s=hi,
                        notes=["top of the range: add load or a harder variant"],
                    )
            else:
                d = base + (10 if rating == "easy" else 5)
                t = Target(n, weight_kg=w, duration_s=min(hi, d) if hi else d)
            misses = 0
            continue
        misses += 1
        if misses < 2:
            t.notes.append("missed once, same again")
            continue
        misses = 0
        t.duration_s = max(min(lo, t.duration_s), t.duration_s - 5)
        t.notes.append("missed twice: 5 s less")
    last = history[0]
    t.reason, t.based_on, t.unrated = last.get("rating") or "good", str(last["date"]), not last.get("rating")
    return t


def _next_reps(pe: dict, ex: dict, history: list[dict], weights: list[float] | None) -> Target:
    n = pe["sets"]
    inc = ex.get("increment_kg") or 2.5
    lo = pe.get("reps_min") or pe.get("target_reps") or 8
    hi = max(pe.get("reps_max") or lo, lo)
    t = Target(n, pe.get("target_reps") or lo, pe.get("target_weight_kg"))
    if not history:
        return t

    def heavier(w: float | None, r: int) -> Target | None:
        """One weight step up from r reps at w; None: nothing to add (body weight, the heaviest load)."""
        if not w and ex.get("bodyweight"):
            return None
        floor = lo
        if weights and w:
            up = [x for x in weights if x > w + 1e-9]
            if not up:
                return None
            fits = [x for x in up if epley_reps(r, w, x) >= lo]
            w2 = fits[-1] if fits else up[0]
            floor = lo if fits else 1  # even the next dumbbell is a big step: below the range
        else:
            w2 = round((w or 0) + inc, 2)
        if not w or ex.get("bodyweight"):
            return Target(n, lo, w2)  # the body is most of the load: no estimate from the extra weight
        return Target(n, max(floor, min(hi - 1, int(epley_reps(r, w, w2)) - 1)), w2)

    def lighter(w: float | None) -> float | None:
        if weights and w:
            down = [x for x in weights if x < w - 1e-9]
            return down[-1] if down else None
        return round(w - inc, 2) if w and w > inc + 1e-9 else None

    def top(w: float | None) -> Target:
        return Target(n, hi, w, notes=["top of the range: add load or a harder variant"])

    misses, back = 0, None  # back: the reps the volume variant returns to
    prev_day, prev_sum = None, None  # prev_sum: reps of the planned sets in the last miss
    stepped = False  # the target's weight is a step VitaLocal chose, its reps an estimate
    for perf in reversed(history):
        work = [s for s in perf["sets"] if (s.get("reps") or 0) > 0]
        rating = perf.get("rating") or "good"
        t.notes = []
        if not work:
            t.notes.append("no work sets recorded")
            continue
        day = _as_date(perf["date"])
        pause = prev_day is not None and (day - prev_day).days > BREAK_DAYS
        prev_day = day
        w_done = min(s.get("weight_kg") or 0 for s in work)
        other = (w_done and t.weight_kg is None) or (
            t.weight_kg is not None and abs(w_done - t.weight_kg) > 1e-9
        )
        if other or pause or stepped:
            # another weight than the target's (the dumbbell that was there), the first session after a
            # weight step or a long pause: the target is what this session went for, judged as usual
            t = Target(n, min(hi, aimed(_reps_at(work, w_done))), w_done or None)
            misses, back, prev_sum, stepped = 0, None, None, False
        w = t.weight_kg  # None: no load (body weight)
        reached = _reps_at(work, w)
        # more sets than planned at fewer reps: the volume variant, done on one's own
        if back is None and len(reached) > n and reached[n - 1] < t.reps and (v := aimed(reached)) < t.reps:
            back, t, prev_sum = t.reps, Target(n + 1, v, w), None
        # all but one planned set went beyond the target (the plan's own, higher target, 11, 11, 10 at
        # 3×10): that is the target this session is judged against
        elif len(reached) == n and (v := min(hi, reached[max(n - 2, 0)])) > t.reps:
            t, misses, back, prev_sum = Target(n, v, w), 0, None, None
        k = t.sets
        done_sum = sum(reached[:k])
        progressed = rating != "hard" and prev_sum is not None and len(reached) >= k and done_sum > prev_sum
        if rating == "easy":
            if back is not None:
                t = Target(n, back, w, notes=[f"back to {n}×{back}"])
            else:
                up = heavier(w, max(hi, reached[min(n, len(reached)) - 1]))
                t, stepped = up or top(w), up is not None
            misses, back, prev_sum = 0, None, None
            continue
        if len(work) < k:
            t.notes.append(f"{len(work)} of {k} sets done")
        if rating != "hard" and len(reached) >= k and reached[k - 1] >= t.reps:
            if back is not None:
                t = Target(n, back, w, notes=[f"back to {n}×{back}"])
            elif reached[k - 1] >= hi:
                up = heavier(w, reached[k - 1])
                t, stepped = up or top(w), up is not None
            else:
                t = Target(n, min(hi, max(t.reps, reached[k - 1]) + 1), w)
            misses, back, prev_sum = 0, None, None
            continue
        prev_sum = done_sum
        misses += 1
        if misses < 2:
            t.notes.append("missed once, same again")
            continue
        v = min(t.reps - 2, reached[0])
        if back is None and v >= lo:
            misses, back, prev_sum = 0, t.reps, None
            t = Target(n + 1, v, w, notes=[f"volume variant: {n + 1}×{v} instead of {n}×{back}"])
            continue
        if progressed:
            misses = 1  # still getting better: no step down yet
            t.notes.append("missed, but more reps than last time: same again")
            continue
        misses, prev_sum = 0, None
        down = lighter(w)
        if down is not None:
            t, back, stepped = Target(n, hi, down, notes=["weight down after two misses"]), None, True
        else:
            t, back = Target(n, lo, w, notes=["missed twice, check the weight"]), None
    if back is not None and not any(x.startswith("volume variant") for x in t.notes):
        t.notes.append(f"volume variant: {t.sets}×{t.reps} instead of {n}×{back}")
    last = history[0]
    t.reason, t.based_on, t.unrated = last.get("rating") or "good", str(last["date"]), not last.get("rating")
    return t


def suggest_rating(target: dict, sets: list[dict], kind: str = "reps") -> dict | None:
    """Rating proposed from what was done against the planned target, or None without work sets.

    target: sets, reps, weight_kg, duration_s (the session's planned target)
    sets: work sets [{repetitions, weight_kg, duration_s}]
    Missing a set, a rep, the weight or the time → too hard; every set ≥ 3 reps (15 s) over the
    target → too easy; otherwise good. Reps count the planned number of best sets at the weight.
    More sets than planned, all at fewer reps than the target, are the volume variant done → good.
    """
    n = target.get("sets") or 0
    if kind == "time":
        done = [s.get("duration_s") or 0 for s in sets if (s.get("duration_s") or 0) > 0]
        goal = target.get("duration_s")
        if not done or not goal:
            return None
        if len(done) < n:
            return {"rating": "hard", "why": "fewer sets"}
        if min(done) < goal:
            return {"rating": "hard", "why": "target missed"}
        return (
            {"rating": "easy", "why": "target exceeded"}
            if min(done) >= goal + 15
            else {"rating": "good", "why": "target met"}
        )
    work = [s for s in sets if (s.get("repetitions") or 0) > 0]
    reps, weight = target.get("reps"), target.get("weight_kg")
    if not work or not reps:
        return None
    # as the progression counts it: the planned number of sets at the weight; an extra set is a bonus
    at_weight = [s for s in work if not weight or (s.get("weight_kg") or 0) >= weight - 1e-9]
    best = sorted((s["repetitions"] for s in at_weight), reverse=True)[: n or None]
    if len(at_weight) < n and len(at_weight) < len(work):
        return {"rating": "hard", "why": "lighter weight"}
    if len(best) < n:
        return {"rating": "hard", "why": "fewer sets"}
    if min(best) < reps:
        # as next_target counts it: one set more than planned, every set at the reps most of them reached
        done = [s["repetitions"] for s in at_weight]
        if n and len(done) > n and min(done) == aimed(done) < reps:
            return {"rating": "good", "why": "volume variant"}
        return {"rating": "hard", "why": "target missed"}
    if min(best) >= reps + 3:
        return {"rating": "easy", "why": "target exceeded"}
    return {"rating": "good", "why": "target met"}


# --- statistics ---------------------------------------------------------------------------------------


def e1rm(weight_kg: float | None, reps: int | None) -> float | None:
    """Estimated one-rep max (Epley), only from 1–12 reps with a load."""
    if not weight_kg or not reps or reps > 12:
        return None
    return round(weight_kg * (1 + reps / 30), 1) if reps > 1 else float(weight_kg)


def hard_sets(sets: list[dict], exercises: dict) -> dict[str, float]:
    """Hard sets per muscle: work sets (not warm-up/hidden, with reps or time); secondary muscles count ½."""
    out: dict[str, float] = {}
    for s in sets:
        ex = exercises.get(s.get("exercise_id"))
        if ex is None or s.get("warmup") or s.get("hidden"):
            continue
        if not (s.get("repetitions") or (ex.get("kind") == "time" and s.get("duration_s"))):
            continue
        for m in ex.get("muscles") or []:
            out[m] = out.get(m, 0) + 1
        for m in ex.get("muscles_secondary") or []:
            out[m] = out.get(m, 0) + 0.5
    return out


# --- structural balance -------------------------------------------------------------------------------

# Garmin exercise category → movement pattern (only what the balance compares)
PATTERNS: dict[str, str] = {
    "bench_press": "h_push", "push_up": "h_push",
    "row": "h_pull",
    "shoulder_press": "v_push",
    "pull_up": "v_pull",
    "squat": "knee", "lunge": "knee",
    "deadlift": "hip", "hip_raise": "hip", "leg_curl": "hip", "hyperextension": "hip",
}  # fmt: skip
PATTERN_LABELS: dict[str, str] = {
    "h_push": "Drücken horizontal", "h_pull": "Ziehen horizontal", "v_push": "Drücken vertikal",
    "v_pull": "Ziehen vertikal", "knee": "Kniedominant (Kniebeuge, Ausfallschritt)",
    "hip": "Hüftdominant (Kreuzheben, Hip Thrust, Beinbeuger)", "push": "Drücken", "pull": "Ziehen",
    "biceps": "Bizeps", "triceps": "Trizeps", "delts_front": "vordere Schulter",
    "delts_rear": "hintere Schulter",
}  # fmt: skip
# (key, side a, side b, lowest a/b, highest a/b): weekly work sets; biceps, triceps and the delts are
# hard sets (the front delts get more from all the pressing, up to twice the rear delts' sets is fine)
VOLUME_PAIRS = [
    ("push_pull", ("h_push", "v_push"), ("h_pull", "v_pull"), 0.8, 1.25),
    ("horizontal", ("h_push",), ("h_pull",), 0.75, 1.33),
    ("vertical", ("v_push",), ("v_pull",), 0.67, 1.5),
    ("knee_hip", ("knee",), ("hip",), 0.75, 1.33),
    ("arms", ("biceps",), ("triceps",), 0.67, 1.5),
    ("delts", ("delts_front",), ("delts_rear",), 0.5, 2.0),
]
HARD_SET_PAIRS = ("biceps", "triceps", "delts_front", "delts_rear")
# (key, a, b, reference a/b, tolerance): best estimated 1RM, rough references from common strength
# standards (overhead press about 60 % of the bench press, squat about 80 % of the deadlift, a row about
# as heavy as the bench press); dumbbells count as both hands
STRENGTH_PAIRS = [
    ("row_bench", "h_pull", "h_push", 1.0, 0.2),
    ("press_bench", "v_push", "h_push", 0.6, 0.15),
    ("squat_hinge", "knee", "hip", 0.8, 0.15),
]


def balance(sets_per_week: dict[str, float], best_e1rm: dict[str, float]) -> dict:
    """Ratios of opposing patterns. `state`: ok, low (a falls short of b), high (a outweighs b) or
    missing (one side has no data)."""

    def state(ratio, lo, hi):
        return "ok" if lo <= ratio <= hi else "low" if ratio < lo else "high"

    volume = []
    for key, a, b, lo, hi in VOLUME_PAIRS:
        va, vb = sum(sets_per_week.get(x, 0) for x in a), sum(sets_per_week.get(x, 0) for x in b)
        ratio = round(va / vb, 2) if va and vb else None
        # one side without any sets is the clearest imbalance there is
        lone = "high" if va and not vb else "low" if vb and not va else "missing"
        volume.append({
            "key": key, "a": "push" if len(a) > 1 else a[0], "b": "pull" if len(b) > 1 else b[0],
            "a_value": round(va, 1), "b_value": round(vb, 1), "ratio": ratio, "range": [lo, hi],
            "state": state(ratio, lo, hi) if ratio is not None else lone,
        })  # fmt: skip
    strength = []
    for key, a, b, ref, tol in STRENGTH_PAIRS:
        ea, eb = best_e1rm.get(a), best_e1rm.get(b)
        ratio = round(ea / eb, 2) if ea and eb else None
        strength.append({
            "key": key, "a": a, "b": b, "a_kg": ea, "b_kg": eb, "ratio": ratio, "reference": ref,
            "range": [round(ref - tol, 2), round(ref + tol, 2)],
            "state": state(ratio, ref - tol, ref + tol) if ratio is not None else "missing",
        })  # fmt: skip
    return {"volume": volume, "strength": strength, "labels": PATTERN_LABELS}
