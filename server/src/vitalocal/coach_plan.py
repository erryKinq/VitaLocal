"""AI coach builds a training plan from the questionnaire's answers.

The model gets the answers, the exercises it may use (built-in library and own exercises, only those
the available equipment allows), the current plan and the recent volume per muscle. It answers with
days in rotation order and per day exercises (by id), sets, rep range or seconds, rest. The validator
checks every id, the ranges and the estimated time per session; one repair round, as for debriefs.
Taking a proposal over creates an ordinary plan (library entries become own exercises); targets and
weights then follow VitaLocal's progression from the first session on.
"""

from __future__ import annotations

import json

from sqlalchemy import Connection, text

from vitalocal import ai, coach, exercise_library, training
from vitalocal.api import training as t

PROMPT_VERSION = 2
MAX_DAYS = 7
EXERCISES_PER_DAY = (2, 10)
WARMUP_MIN = 8
WORK_S = 35  # one set of reps, roughly
TIME_SLACK = 1.3  # a session may run this much over the wished length
SECONDARY = 0.5  # share of a set a secondary muscle gets, as in the stats

GOALS = {"muscle": "Muskelaufbau", "strength": "Kraft", "fitness": "Fitness", "fat_loss": "Abnehmen"}
LEVELS = {"beginner": "Einsteiger", "intermediate": "Fortgeschritten", "advanced": "Erfahren"}

NULLABLE_INT = {"type": ["integer", "null"]}
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "summary": {"type": "string"},
        "days": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "focus": {"type": "string"},
                    "exercises": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "sets": {"type": "integer"},
                                "reps_min": NULLABLE_INT,
                                "reps_max": NULLABLE_INT,
                                "seconds_min": NULLABLE_INT,
                                "seconds_max": NULLABLE_INT,
                                "rest_s": {"type": "integer"},
                            },
                            "required": [
                                "id",
                                "sets",
                                "reps_min",
                                "reps_max",
                                "seconds_min",
                                "seconds_max",
                                "rest_s",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "focus", "exercises"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "summary", "days", "notes"],
    "additionalProperties": False,
}

SYSTEM = {
    "de": """Du bist ein erfahrener Krafttrainer und stellst einen Trainingsplan zusammen. Du bekommst \
Fakten als \
JSON und antwortest ausschließlich mit einem JSON-Objekt.

Die Fakten:
- `answers`: Ziel, Erfahrung, Trainingstage pro Woche, Minuten pro Training, Ausrüstung, Einschränkungen, \
Schwerpunkte (Muskeln), Wunsch- und Tabu-Übungen, ob Ausdauer dazukommt.
- `catalog`: die Übungen, die du verwenden darfst – nur diese, nur über ihre `id`. \
`primary`/`secondary` sind \
die Muskeln, `kind` „time“ heißt: wird in Sekunden gemessen (Plank), sonst Wiederholungen.
- `current_plan`: der bisherige Plan (zur Orientierung, nicht zum Kopieren).
- `recent_hard_sets_per_week`: harte Sätze pro Muskel und Woche der letzten 4 Wochen; \
`recent_hard_sets_per_group`: dasselbe je Muskelgruppe (`muscle_groups`).
- `previous_proposal` und `change_request` (nur beim Nachbessern): dein letzter Vorschlag und was die Person \
anders haben will. Ändere gezielt das Gewünschte und lass den Rest stehen.

So baust du den Plan:
- Die Tage laufen als Rotation (A → B → C → A …). Üblich: 2–3 Tage pro Woche Ganzkörper, 4 \
Oberkörper/Unterkörper, \
5–6 Push/Pull/Beine. Weniger verschiedene Tage als Trainingstage (z. B. A/B bei 3 Tagen) sind in Ordnung.
- Wöchentliche harte Sätze pro großem Muskel: Einsteiger 6–10, Fortgeschrittene 10–16, Erfahrene 14–20; \
Schwerpunkt-Muskeln eher oben, Hilfsmuskeln zählen halb. Das gilt je Muskelgruppe (Brust, Schultern, \
Rücken …); innerhalb der Gruppe die Teile abdecken: obere und mittlere/untere Brust, Latissimus und \
oberer Rücken. Die vordere Schulter bekommt vom Drücken genug, seitliche und hintere Schulter brauchen \
eigene Übungen.
- Wiederholungsbereiche nach Ziel: Muskelaufbau 8–12 (kleine Muskeln 10–15), Kraft 4–6 oder 5–8 bei \
Grundübungen und 8–12 bei Ergänzungen, Fitness und Abnehmen 10–15. Pausen: Grundübungen 120–180 s, \
sonst 60–90 s.
- Pro Tag 4–8 Übungen, Grundübungen zuerst; Drücken und Ziehen im Gleichgewicht.
- Die Dauer pro Training (Sätze × (ca. 35 s + Pause) + 8 min Aufwärmen) soll zu `minutes` passen.
- Einschränkungen ernst nehmen: Übungen meiden, die den genannten Bereich belasten, und das in `notes` \
sagen. \
Tabu-Übungen nicht verwenden, Wunsch-Übungen einbauen, wo sie passen. Ausdauer nur als Hinweis in `notes`.
- Körpergewichtsübungen (`bodyweight`): Mit Dip-/Gewichtsgürtel oder Gewichtsweste lassen sie sich über \
Zusatzlast steigern (die Varianten „mit Zusatzgewicht“/„mit Gewichtsweste“ im Katalog), dann passen auch \
mittlere Wiederholungsbereiche; ohne Zusatzlast höhere Bereiche (z. B. 8–15) wählen.
- Gewichte und Zielwiederholungen gibst du nicht vor: Die setzt VitaLocal ab dem ersten Training.
- Du bist kein Arzt; bei Schmerzen oder Verletzungen zur Abklärung raten.

Antwort (JSON, Deutsch):
- `name`: kurzer Planname (z. B. „Oberkörper/Unterkörper 4×“).
- `summary`: 2–4 Sätze: Aufteilung und warum sie zu den Antworten passt.
- `days`: in Rotationsreihenfolge, je `name` (kurz, z. B. „Push“, „A“), `focus` (ein Satz) und \
`exercises` mit \
`id`, `sets` (1–6), `reps_min`/`reps_max` (bei `kind` time null), `seconds_min`/`seconds_max` (nur bei time, \
sonst null), `rest_s`.
- `notes`: 0–5 kurze Hinweise (Einschränkungen, Ausdauer, wie die Progression weitergeht).""",
    "en": """You are an experienced strength coach putting a training plan together. You get facts as \
JSON and \
answer with a JSON object only.

The facts:
- `answers`: goal, experience, training days per week, minutes per session, equipment, limitations, focus \
muscles, liked and disliked exercises, whether cardio is added.
- `catalog`: the exercises you may use – only these, only by their `id`. `primary`/`secondary` are the \
muscles; \
`kind` "time" means measured in seconds (plank), otherwise reps.
- `current_plan`: the plan so far (for orientation, not to copy).
- `recent_hard_sets_per_week`: hard sets per muscle and week over the last 4 weeks; \
`recent_hard_sets_per_group`: the same per muscle group (`muscle_groups`).
- `previous_proposal` and `change_request` (only when refining): your last proposal and what the person \
wants \
changed. Change that specifically and keep the rest.

Building the plan:
- Days run as a rotation (A → B → C → A …). Usual: 2–3 days a week full body, 4 upper/lower, 5–6 \
push/pull/legs. \
Fewer distinct days than training days (e.g. A/B at 3 days) are fine.
- Weekly hard sets per major muscle: beginners 6–10, intermediate 10–16, advanced 14–20; focus muscles \
towards \
the top, secondary muscles count half. This holds per muscle group (chest, shoulders, back …); within a \
group cover its parts: upper and mid/lower chest, lats and upper back. Front delts get enough from \
pressing, side and rear delts need exercises of their own.
- Rep ranges by goal: muscle 8–12 (small muscles 10–15), strength 4–6 or 5–8 on compounds and 8–12 on \
accessories, \
fitness and fat loss 10–15. Rest: compounds 120–180 s, otherwise 60–90 s.
- 4–8 exercises a day, compounds first; pushing and pulling in balance.
- Session length (sets × (about 35 s + rest) + 8 min warm-up) should fit `minutes`.
- Take limitations seriously: avoid exercises loading the named area and say so in `notes`. Do not use \
disliked \
exercises; include liked ones where they fit. Cardio only as a note.
- Bodyweight exercises (`bodyweight`): with a dip/weight belt or a weight vest they progress by extra load \
(the weighted variants in the catalog), so medium rep ranges fit; without extra load choose higher ranges \
(e.g. 8–15).
- You do not set weights or target reps: VitaLocal sets those from the first session on.
- You are not a doctor; for pain or injuries suggest seeing one.

Answer (JSON, English):
- `name`: short plan name (e.g. "Upper/Lower 4×").
- `summary`: 2–4 sentences: the split and why it fits the answers.
- `days`: in rotation order, each with `name` (short, e.g. "Push", "A"), `focus` (one sentence) and \
`exercises` \
with `id`, `sets` (1–6), `reps_min`/`reps_max` (null for time), `seconds_min`/`seconds_max` (only for time, \
otherwise null), `rest_s`.
- `notes`: 0–5 short notes (limitations, cardio, how the progression goes on).""",
}


# --- facts ------------------------------------------------------------------------------------------------


def load_answers(conn: Connection) -> dict | None:
    return conn.execute(text("SELECT answers FROM coach_profile WHERE id = 1")).scalar()


def save_answers(conn: Connection, answers: dict) -> None:
    conn.execute(
        text("""INSERT INTO coach_profile (id, answers) VALUES (1, CAST(:a AS jsonb))
                ON CONFLICT (id) DO UPDATE SET answers = EXCLUDED.answers, updated_at = now()"""),
        {"a": json.dumps(answers, ensure_ascii=False)},
    )


def catalog(conn: Connection, equipment: list[str]) -> list[dict]:
    """The exercises a plan may use: own exercises (not archived) and library entries not taken over
    yet, each needing nothing beyond `equipment`."""
    have = set(equipment)
    out, linked = [], set()
    for e in t.list_exercises(conn, include_archived=False):
        if e["library_key"]:
            linked.add(e["library_key"])
        if not set(e["equipment"] or []) <= have:
            continue
        out.append({
            "id": f"own:{e['id']}", "name": e["name"], "primary": e["muscles"],
            "secondary": e["muscles_secondary"], "bodyweight": e["bodyweight"],
            "equipment": e["equipment"] or [], "kind": e["kind"],
        })  # fmt: skip
    for e in exercise_library.search(equipment=have):
        if e["key"] in linked:
            continue
        out.append({
            "id": f"lib:{e['key']}", "name": e["name"], "primary": e["muscles"],
            "secondary": e["muscles_secondary"], "bodyweight": e["bodyweight"],
            "equipment": e["equipment"], "kind": e["kind"],
        })  # fmt: skip
    return out


def plan_facts(
    conn: Connection, answers: dict, previous: dict | None = None, change: str | None = None
) -> dict:
    plan = t.active_plan(conn)
    current = None
    if plan:
        exmap = t._exercise_map(conn)
        current = {
            "name": plan["name"],
            "days": [
                {
                    "name": d["name"],
                    "exercises": [
                        f"{exmap[pe['exercise_id']]['name']} {pe['sets']}×"
                        + (f"{pe['reps_min']}–{pe['reps_max']}" if pe["reps_min"] else "time")
                        for pe in d["exercises"]
                        if pe["exercise_id"] in exmap
                    ],
                }
                for d in plan["days"]
            ],
        }
    vol = t.stats(conn, weeks=4)["weeks"]
    per_week: dict[str, float] = {}
    for w in vol:
        for m, n in w["hard_sets"].items():
            per_week[m] = per_week.get(m, 0) + n / len(vol)
    out = {
        "answers": {
            **answers,
            "goal": GOALS.get(answers.get("goal"), answers.get("goal")),
            "experience": LEVELS.get(answers.get("experience"), answers.get("experience")),
            "equipment": [training.EQUIPMENT.get(x, x) for x in answers.get("equipment", [])]
            or ["nur Körpergewicht"],
            "focus": [training.MUSCLES.get(m, m) for m in answers.get("focus", [])],
        },
        "catalog": catalog(conn, answers.get("equipment", [])),
        "current_plan": current,
        "recent_hard_sets_per_week": {
            training.MUSCLES[m]: round(v, 1) for m, v in per_week.items() if m in training.MUSCLES
        },
        "recent_hard_sets_per_group": {
            training.GROUP_LABELS[g]: round(sum(per_week.get(m, 0) for m in ms), 1)
            for g, ms in training.MUSCLE_GROUPS.items()
        },
        "muscle_names": training.MUSCLES,
        "muscle_groups": {
            training.GROUP_LABELS[g]: [training.MUSCLES[m] for m in ms]
            for g, ms in training.MUSCLE_GROUPS.items()
        },  # fmt: skip
    }
    if previous:
        out["previous_proposal"] = {
            k: previous[k] for k in ("name", "summary", "days", "notes") if k in previous
        }
        out["change_request"] = change or ""
    return out


# --- answer -----------------------------------------------------------------------------------------------


def session_minutes(day: dict, kinds: dict[str, str]) -> int:
    secs = 0
    for x in day.get("exercises") or []:
        work = (x.get("seconds_max") or 30) if kinds.get(x.get("id")) == "time" else WORK_S
        secs += (x.get("sets") or 0) * (work + (x.get("rest_s") or 0))
    return round(secs / 60) + WARMUP_MIN


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_plan(answer, facts: dict) -> list[str]:
    """Errors in a plan proposal (empty: accepted)."""
    if not isinstance(answer, dict):
        return ["the answer must be a JSON object"]
    errors = []
    extra = set(answer) - set(PLAN_SCHEMA["properties"])
    if extra:
        errors.append(f"unknown fields {sorted(extra)}")
    name = answer.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        errors.append("`name` must be a string of 1–80 characters")
    if not isinstance(answer.get("summary"), str) or not answer["summary"].strip():
        errors.append("`summary` must be a non-empty string")
    notes = answer.get("notes")
    if (
        not isinstance(notes, list)
        or len(notes) > 5
        or any(not isinstance(n, str) or len(n) > 400 for n in notes)
    ):
        errors.append("`notes` must be a list of at most 5 short strings")
    cat = {c["id"]: c for c in facts["catalog"]}
    minutes = facts["answers"].get("minutes") or 60
    days = answer.get("days")
    if not isinstance(days, list) or not 1 <= len(days) <= MAX_DAYS:
        return errors + [f"`days` must be a list of 1–{MAX_DAYS} days"]
    names = [d.get("name") for d in days if isinstance(d, dict)]
    if len(set(names)) != len(names):
        errors.append("day names must be different")
    used = set()
    for i, d in enumerate(days):
        where = f"days[{i}]"
        if (
            not isinstance(d, dict)
            or not isinstance(d.get("name"), str)
            or not 1 <= len(d["name"].strip()) <= 60
        ):
            errors.append(f"{where}.name must be a string of 1–60 characters")
            continue
        exs = d.get("exercises")
        lo, hi = EXERCISES_PER_DAY
        if not isinstance(exs, list) or not lo <= len(exs) <= hi:
            errors.append(f"{where} ({d['name']}) must have {lo}–{hi} exercises")
            continue
        ids = [x.get("id") for x in exs if isinstance(x, dict)]
        if len(set(ids)) != len(ids):
            errors.append(f"{where} ({d['name']}) lists an exercise twice")
        for j, x in enumerate(exs):
            w = f"{where}.exercises[{j}]"
            if not isinstance(x, dict):
                errors.append(f"{w} must be an object")
                continue
            c = cat.get(x.get("id"))
            if c is None:
                errors.append(f"{w}: id {x.get('id')!r} is not in the catalog (use only catalog ids)")
                continue
            used.add(c["id"])
            if not _int(x.get("sets")) or not 1 <= x["sets"] <= 6:
                errors.append(f"{w} ({c['name']}): `sets` must be 1–6")
            if not _int(x.get("rest_s")) or not 30 <= x["rest_s"] <= 300:
                errors.append(f"{w} ({c['name']}): `rest_s` must be 30–300")
            if c["kind"] == "time":
                lo_s, hi_s = x.get("seconds_min"), x.get("seconds_max")
                if not (_int(lo_s) and _int(hi_s) and 10 <= lo_s <= hi_s <= 300):
                    errors.append(f"{w} ({c['name']}) is timed: `seconds_min` ≤ `seconds_max`, 10–300")
                if x.get("reps_min") is not None or x.get("reps_max") is not None:
                    errors.append(f"{w} ({c['name']}) is timed: reps must be null")
            else:
                lo_r, hi_r = x.get("reps_min"), x.get("reps_max")
                if not (_int(lo_r) and _int(hi_r) and 1 <= lo_r <= hi_r <= 30):
                    errors.append(f"{w} ({c['name']}): `reps_min` ≤ `reps_max`, 1–30")
                if x.get("seconds_min") is not None or x.get("seconds_max") is not None:
                    errors.append(f"{w} ({c['name']}) counts reps: seconds must be null")
        est = session_minutes(d, {k: v["kind"] for k, v in cat.items()})
        if est > minutes * TIME_SLACK:
            errors.append(
                f"{where} ({d['name']}) takes about {est} min, the person has {minutes}: "
                "fewer sets or exercises"
            )
    for m in facts["answers"].get("focus_keys", []):
        if not any(m in cat[i]["primary"] for i in used):
            errors.append(
                f"focus muscle {training.MUSCLES.get(m, m)} is not trained as a primary muscle on any day"
            )
    return errors


def resolved(answer: dict, facts: dict) -> dict:
    """The proposal as the WebUI shows it: exercise names and kinds, minutes per day and the weekly
    hard sets per muscle (sets × share, one rotation through the days)."""
    cat = {c["id"]: c for c in facts["catalog"]}
    per_week = max(1, facts["answers"].get("days_per_week") or len(answer["days"]))
    per_rotation: dict[str, float] = {}
    days = []
    for d in answer["days"]:
        exs = []
        for x in d["exercises"]:
            c = cat[x["id"]]
            exs.append(
                {
                    **x,
                    "name": c["name"],
                    "kind": c["kind"],
                    "primary": c["primary"],
                    "secondary": c["secondary"],
                }
            )
            for m in c["primary"]:
                per_rotation[m] = per_rotation.get(m, 0) + x["sets"]
            for m in c["secondary"]:
                per_rotation[m] = per_rotation.get(m, 0) + x["sets"] * SECONDARY
        days.append(
            {**d, "exercises": exs, "minutes": session_minutes(d, {k: v["kind"] for k, v in cat.items()})}
        )
    factor = per_week / len(answer["days"])
    return {
        **answer,
        "days": days,
        "hard_sets_per_week": {
            m: round(v * factor, 1) for m, v in sorted(per_rotation.items(), key=lambda kv: -kv[1])
        },
    }


def ask_plan(s: ai.AiSettings, lang: str, facts: dict) -> tuple[dict, str, bool]:
    task = {
        "de": "Erstelle den Trainingsplan." if "change_request" not in facts else "Bessere den Plan nach.",
        "en": "Build the training plan." if "change_request" not in facts else "Refine the plan.",
    }[lang]
    user = f"{task}\n\nFakten (JSON):\n{json.dumps(facts, ensure_ascii=False, default=str)}"
    answer, model, repaired = coach.ask(
        s, SYSTEM[lang], user, PLAN_SCHEMA, lambda a: validate_plan(a, facts), lang, temperature=0.4
    )
    return resolved(answer, facts), model, repaired


coach.KINDS["plan"] = ask_plan


# --- requests and taking a proposal over ------------------------------------------------------------------


def start_plan(
    conn: Connection,
    answers: dict,
    lang: str = "de",
    refines=None,
    change: str | None = None,
    run_async: bool = True,
) -> dict:
    s = ai.load_settings(conn)
    if not ai.available(s, "coach"):
        raise coach.CoachError(ai.unavailable_reason(s, "coach"))
    previous = None
    if refines is not None:
        prev = coach.message(conn, refines)
        if prev is None or prev["kind"] != "plan" or prev["status"] != "done":
            raise coach.CoachError("only a finished plan proposal can be refined")
        previous = prev["result"]
    save_answers(conn, answers)
    facts = plan_facts(conn, {**answers, "focus_keys": answers.get("focus", [])}, previous, change)
    if not facts["catalog"]:
        raise coach.CoachError("no exercise fits the equipment")
    return coach.create(conn, "plan", facts, lang, PROMPT_VERSION, refines=refines, run_async=run_async)


def apply(conn: Connection, mid, active: bool) -> dict:
    """Makes a finished proposal an ordinary plan; library entries become own exercises."""
    m = coach.message(conn, mid)
    if m is None or m["kind"] != "plan":
        raise coach.CoachError("plan proposal not found")
    if m["status"] != "done":
        raise coach.CoachError("the proposal is not finished")
    if m["applied_plan_id"]:
        raise coach.CoachError("the proposal was taken over already")
    p = m["result"]
    days = []
    for d in p["days"]:
        entries = []
        for x in d["exercises"]:
            kind, ref = x["id"].split(":", 1)
            if kind == "lib":
                eid = t.adopt_library_entry(ref, t.Response(), conn)["id"]
            else:
                eid = ref
                t._exercise(conn, eid)  # still there?
            timed = x["seconds_min"] is not None
            entries.append(t.PlanExerciseIn(
                exercise_id=eid, sets=x["sets"], reps_min=None if timed else x["reps_min"],
                reps_max=None if timed else x["reps_max"], duration_min_s=x["seconds_min"],
                duration_max_s=x["seconds_max"], duration_s=x["seconds_min"], rest_s=x["rest_s"],
            ))  # fmt: skip
        days.append(t.PlanDayIn(name=d["name"][:60], note=d.get("focus") or None, exercises=entries))
    note = " ".join([p["summary"], *p.get("notes", [])])[:1000]
    plan = t.create_plan(t.PlanIn(name=p["name"][:80], note=note, active=active, days=days), conn)
    conn.execute(
        text("UPDATE coach_message SET applied_plan_id = :p WHERE id = :id"), {"p": plan["id"], "id": mid}
    )
    return plan
