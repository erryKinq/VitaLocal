"""AI coach reviews the active plan against what was trained and proposes changes.

The model sees the plan (days and entries under short aliases), each entry's last sessions with
ratings, how often it was rated too hard in a row and what the progression suggests next, how many
sessions per week happened and which days were skipped or never trained, session lengths, weekly
hard sets per muscle, the questionnaire's answers and the exercises it may add or swap in.

It may only propose changes from a closed list; weights and targets stay with the progression. The
validator checks each change against the plan; the person takes them over one by one. Taking them
over keeps the plan as it was for undo, and both refuse when the plan changed in between.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Connection, text

from vitalocal import ai, coach, coach_plan, training
from vitalocal.api import training as t

PROMPT_VERSION = 3
WINDOW = timedelta(weeks=12)
MAX_SESSIONS = 60
MAX_CHANGES = 6
HISTORY_PER_ENTRY = 6

TYPES = (
    "swap_exercise",
    "add_exercise",
    "remove_exercise",
    "sets",
    "rep_range",
    "duration_range",
    "rest",
    "reorder",
    "rename_day",
    "remove_day",
    "reorder_days",
)

NULL_INT = {"type": ["integer", "null"]}
NULL_STR = {"type": ["string", "null"]}
CHANGE = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(TYPES)},
        "entry": NULL_STR,
        "day": NULL_STR,
        "exercise": NULL_STR,
        "sets": NULL_INT,
        "reps_min": NULL_INT,
        "reps_max": NULL_INT,
        "seconds_min": NULL_INT,
        "seconds_max": NULL_INT,
        "rest_s": NULL_INT,
        "position": NULL_INT,
        "name": NULL_STR,
        "order": {"type": ["array", "null"], "items": {"type": "string"}},
        "why": {"type": "string"},
    },
    "required": [
        "type",
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
        "why",
    ],
    "additionalProperties": False,
}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "nochange": {"type": "boolean"},
        "reading": {"type": "string"},
        "changes": {"type": "array", "items": CHANGE},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["nochange", "reading", "changes", "notes"],
    "additionalProperties": False,
}

SYSTEM = {
    "de": """Du bist ein erfahrener, nüchterner Krafttrainer. Du prüfst einen Trainingsplan anhand \
dessen, was \
tatsächlich trainiert wurde, und entscheidest, ob der PLAN geändert werden sollte. Du bekommst Fakten \
als JSON und \
antwortest ausschließlich mit einem JSON-Objekt.

Die Fakten:
- `plan.days`: die Tage (Alias wie „D1“) in Rotationsreihenfolge, je Tag die Einträge (Alias wie \
„D1E2“) mit Übung, \
Sätzen, Wiederholungsbereich oder Sekunden, Pause, aktuellem Ziel (`target`), Vorschlag der Progression \
(`next`, mit Begründung), den letzten Malen (`history`, neueste zuerst, mit Bewertung zu leicht/gut/zu \
schwer) \
und `hard_in_a_row` (wie oft zuletzt in Folge zu schwer).
- `adherence`: Trainings pro Woche im Fenster, je Tag wie oft trainiert und übersprungen, zuletzt wann.
- `minutes`: übliche Dauer je Tag und gewünschte Dauer (`profile.minutes`, falls bekannt).
- `hard_sets_per_week`: harte Sätze pro Muskel und Woche (Hilfsmuskeln ½); `hard_sets_per_group`: \
dasselbe je Muskelgruppe.
- `balance`: Sätze pro Woche gegenüberliegender Bewegungsmuster (Drücken/Ziehen, Knie/Hüfte, Bizeps/Trizeps) \
mit sinnvollem Bereich, `state` low/high = Schieflage; `deload`: Deload-Woche und ob eine empfohlen ist. In \
einer laufenden Deload-Woche nichts ändern; eine empfohlene Deload-Woche gehört in `notes`.
- `profile`: die Antworten aus dem Fragenkatalog (falls vorhanden); `catalog`: Übungen, die du \
hinzufügen oder \
eintauschen darfst (nur über ihre `id`); `user_note`: was die Person dazu sagt.

Wann ändern:
- Eine Übung mit `hard_in_a_row` ≥ 2 oder wiederholt „zu schwer“ trotz Volumen-Variante: Sätze \
reduzieren oder \
Übung tauschen.
- Ein Tag, der dauernd übersprungen oder nie trainiert wird: umsortieren, umbenennen oder entfernen.
- Trainings deutlich länger als gewünscht: Sätze oder Übungen kürzen.
- Muskeln ohne Arbeit, während andere viel bekommen, oder ein Schwerpunkt aus `profile` zu kurz kommt: \
ergänzen \
oder umverteilen.
- Körpergewicht oder Schlaf sind höchstens ein Hinweis in `notes`, kein Grund für Planänderungen.

Wann NICHT ändern:
- Ein Training ist kein Trend. Mit weniger als 3 Trainings im Fenster nur bei `hard_in_a_row` ≥ 2 oder einem \
klaren Wunsch in `user_note` ändern; sonst `nochange`.
- Ein Plan, der funktioniert, braucht keine Änderung. Erfinde nichts, um nützlich zu wirken.
- Gewichte und Zielwiederholungen änderst du NIE: Die setzt VitaLocals Progression (inkl. der \
Volumen-Variante \
4×(R−2) nach zwei Fehlversuchen) – arbeite nicht dagegen.
- Höchstens 6 Änderungen, lieber wenige mit klarer Begründung.

Änderungsarten (`type`) – nur diese, andere Felder null:
- `swap_exercise`: `entry`, `exercise` (Katalog-id, gleiche Art reps/time)
- `add_exercise`: `day`, `exercise`, `sets`, `reps_min`/`reps_max` oder `seconds_min`/`seconds_max`, \
`rest_s`, \
optional `position` (1 = zuerst)
- `remove_exercise`: `entry`
- `sets`: `entry`, `sets` (1–6)
- `rep_range`: `entry`, `reps_min`, `reps_max`
- `duration_range`: `entry`, `seconds_min`, `seconds_max`
- `rest`: `entry`, `rest_s` (30–300)
- `reorder`: `entry`, `position` (Platz im Tag, 1 = zuerst)
- `rename_day`: `day`, `name`
- `remove_day`: `day`
- `reorder_days`: `order` (alle Tag-Aliase in neuer Reihenfolge)
Jede Änderung braucht `why` mit der Zahl, auf der sie beruht (Bewertungen deutsch: zu leicht, gut, zu \
schwer). \
Schreib für Menschen: keine Feldnamen wie `hard_in_a_row` oder Aliase wie „D1E2“ im Text.

Antwort (JSON, Deutsch):
- `nochange`: true, wenn nichts geändert werden sollte (dann `changes` leer).
- `reading`: 2–4 Sätze, wie der Plan im Fenster lief, mit Zahlen.
- `changes`: die Änderungen.
- `notes`: 0–4 Hinweise ohne Planänderung.""",
    "en": """You are an experienced, level-headed strength coach. You review a training plan against \
what was \
actually trained and decide whether the PLAN should change. You get facts as JSON and answer with a \
JSON object \
only.

The facts:
- `plan.days`: the days (alias like "D1") in rotation order, per day the entries (alias like "D1E2") \
with exercise, \
sets, rep range or seconds, rest, current target (`target`), the progression's suggestion (`next`, with \
reason), \
the last times (`history`, newest first, with rating easy/good/hard) and `hard_in_a_row`.
- `adherence`: sessions per week in the window, per day how often trained and skipped, last date.
- `minutes`: usual length per day and the wished length (`profile.minutes`, if known).
- `hard_sets_per_week`: hard sets per muscle and week (secondary muscles ½); `hard_sets_per_group`: the same \
per muscle group.
- `balance`: weekly sets of opposing movement patterns (push/pull, knee/hip, biceps/triceps) with a sensible \
range, `state` low/high = imbalance; `deload`: the deload week and whether one is suggested. During a deload \
week change nothing; a suggested deload belongs in `notes`.
- `profile`: the questionnaire's answers (if any); `catalog`: exercises you may add or swap in (by `id` \
only); \
`user_note`: what the person says.

When to change:
- An exercise with `hard_in_a_row` ≥ 2 or repeatedly "hard" despite the volume variant: fewer sets or swap it.
- A day always skipped or never trained: reorder, rename or remove it.
- Sessions clearly longer than wished: cut sets or exercises.
- Muscles without work while others get plenty, or a focus from `profile` falling short: add or redistribute.
- Body weight or sleep are at most a note, never a reason for plan changes.

When NOT to change:
- One workout is not a trend. With fewer than 3 sessions in the window change only on `hard_in_a_row` ≥ \
2 or a \
clear wish in `user_note`; otherwise `nochange`.
- A plan that works needs no change. Do not invent one to look useful.
- You NEVER change weights or target reps: VitaLocal's progression sets them (including the volume \
variant 4×(R−2) \
after two misses) – do not work against it.
- At most 6 changes; prefer few with clear reasons.

Change types (`type`) – only these, other fields null:
- `swap_exercise`: `entry`, `exercise` (catalog id, same kind reps/time)
- `add_exercise`: `day`, `exercise`, `sets`, `reps_min`/`reps_max` or `seconds_min`/`seconds_max`, `rest_s`, \
optional `position` (1 = first)
- `remove_exercise`: `entry`
- `sets`: `entry`, `sets` (1–6)
- `rep_range`: `entry`, `reps_min`, `reps_max`
- `duration_range`: `entry`, `seconds_min`, `seconds_max`
- `rest`: `entry`, `rest_s` (30–300)
- `reorder`: `entry`, `position` (place in the day, 1 = first)
- `rename_day`: `day`, `name`
- `remove_day`: `day`
- `reorder_days`: `order` (all day aliases in the new order)
Every change needs `why` naming the number it rests on. Write for people: no field names like \
`hard_in_a_row` or aliases like "D1E2" in the text.

Answer (JSON, English):
- `nochange`: true when nothing should change (then `changes` empty).
- `reading`: 2–4 sentences on how the plan went in the window, with numbers.
- `changes`: the changes.
- `notes`: 0–4 notes without plan changes.""",
}


# --- the plan as data -------------------------------------------------------------------------------------

_ENTRY_KEYS = (
    "id",
    "exercise_id",
    "sets",
    "reps_min",
    "reps_max",
    "duration_s",
    "duration_min_s",
    "duration_max_s",
    "rest_s",
    "target_reps",
    "target_weight_kg",
    "note",
)


def plan_state(plan: dict) -> dict:
    """What a plan write needs (ids kept): the snapshot for undo and the base of the fingerprint."""
    return {
        "id": str(plan["id"]),
        "name": plan["name"],
        "note": plan["note"],
        "active": plan["active"],
        "deload_every_weeks": plan.get("deload_every_weeks"),
        "days": [
            {
                "id": str(d["id"]),
                "name": d["name"],
                "note": d["note"],
                "exercises": [
                    {k: (str(e[k]) if k in ("id", "exercise_id") else e[k]) for k in _ENTRY_KEYS}
                    for e in d["exercises"]
                ],
            }
            for d in plan["days"]
        ],
    }


def fingerprint(state: dict) -> str:
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()[:16]


def write_state(conn: Connection, state: dict) -> dict:
    body = t.PlanIn(
        name=state["name"],
        note=state["note"],
        active=state["active"],
        deload_every_weeks=state.get("deload_every_weeks"),
        days=[
            t.PlanDayIn(
                id=d.get("id"),
                name=d["name"],
                note=d.get("note"),
                exercises=[t.PlanExerciseIn(**e) for e in d["exercises"]],
            )
            for d in state["days"]
        ],
    )
    return t.update_plan(uuid.UUID(state["id"]), body, conn)


# --- facts ------------------------------------------------------------------------------------------------


def _range(e: dict) -> str:
    if e.get("duration_min_s") or e.get("duration_max_s"):
        return f"{e.get('duration_min_s')}–{e.get('duration_max_s')} s"
    return f"{e.get('reps_min')}–{e.get('reps_max')}" if e.get("reps_min") else "–"


def _now() -> datetime:
    return datetime.now(t._tz())


def review_facts(conn: Connection, user_note: str | None = None) -> dict:
    plan = t.active_plan(conn)
    if not plan or not plan["days"]:
        raise coach.CoachError("there is no active plan to review")
    tz = t._tz()
    now = _now()
    state = plan_state(plan)
    targets = {}
    for d in plan["days"]:
        for x in t.day_targets(conn, d):
            targets[x["plan_exercise_id"]] = x
    aliases: dict[str, dict] = {}
    days = []
    for i, d in enumerate(plan["days"], 1):
        da = f"D{i}"
        aliases[da] = {"day": str(d["id"])}
        entries = []
        for j, e in enumerate(d["exercises"], 1):
            ea = f"{da}E{j}"
            aliases[ea] = {"entry": str(e["id"]), "day": str(d["id"])}
            x = targets.get(e["id"]) or {}
            hist = t.performances(conn, e["exercise_id"], limit=HISTORY_PER_ENTRY, plan_exercise_id=e["id"])
            hard = 0
            for p in hist:
                if p["rating"] != "hard":
                    break
                hard += 1
            entries.append(
                {
                    "alias": ea,
                    "exercise": e["exercise"],
                    "kind": e["kind"],
                    "sets": e["sets"],
                    "range": _range(e),
                    "rest_s": e["rest_s"],
                    "target": x.get("current"),
                    "next": {
                        "text": (x.get("next") or {}).get("text"),
                        "reason": (x.get("next") or {}).get("reason"),
                    },
                    "history": [
                        {
                            "date": p["date"].isoformat(),
                            "days_ago": (now.date() - p["date"]).days,
                            "sets": coach._sets_text(p["sets"], e["kind"]),
                            "rating": p["rating"],
                        }
                        for p in hist
                    ],
                    "hard_in_a_row": hard,
                }
            )
        days.append({"alias": da, "name": d["name"], "entries": entries})
    sessions = t.list_sessions(conn, now - WINDOW, now)[:MAX_SESSIONS]
    skips = t.list_skips(conn, now - WINDOW, now)
    weeks = max(1, min(12, ((now - min((s["start_ts"] for s in sessions), default=now)).days // 7) + 1))
    per_day = {}
    for d in plan["days"]:
        done = [s for s in sessions if s["plan_day"] == d["name"]]
        mins = [round(s["elapsed_s"] / 60) for s in done if s["elapsed_s"]]
        per_day[d["name"]] = {
            "trained": len(done),
            "skipped": sum(1 for k in skips if k["plan_day_id"] == d["id"]),
            "last": done[0]["start_ts"].astimezone(tz).date().isoformat() if done else None,
            "usual_minutes": sorted(mins)[len(mins) // 2] if mins else None,
        }
    profile = coach_plan.load_answers(conn)
    have = set((profile or {}).get("equipment") or [])
    if not profile:  # without a questionnaire: what the own exercises use
        for e in t.list_exercises(conn, include_archived=False):
            have |= set(e["equipment"] or [])
    vol = t.stats(conn, weeks=4)["weeks"]
    hard_sets: dict[str, float] = {}
    for w in vol:
        for m, n in w["hard_sets"].items():
            hard_sets[m] = hard_sets.get(m, 0) + n / len(vol)
    return {
        "plan": {"name": plan["name"], "days": days},
        "adherence": {
            "window_weeks": weeks,
            "sessions": len(sessions),
            "sessions_per_week": round(len(sessions) / weeks, 1),
            "per_day": per_day,
        },
        "hard_sets_per_week": {training.MUSCLES.get(m, m): round(v, 1) for m, v in hard_sets.items()},
        "hard_sets_per_group": {
            training.GROUP_LABELS[g]: round(sum(hard_sets.get(m, 0) for m in ms), 1)
            for g, ms in training.MUSCLE_GROUPS.items()
        },
        "balance": [
            {k: x[k] for k in ("a", "b", "a_value", "b_value", "ratio", "range", "state")}
            for x in t.balance(conn, 8)["volume"]
        ],
        "deload": json.loads(json.dumps(t.deload_status(conn), default=str)),
        "profile": profile,
        "catalog": coach_plan.catalog(conn, sorted(have)),
        "user_note": (user_note or "").strip() or None,
        "_aliases": aliases,
        "_state": state,
        "_fingerprint": fingerprint(state),
    }


def _for_model(facts: dict) -> dict:
    return {k: v for k, v in facts.items() if not k.startswith("_")}


# --- answer -----------------------------------------------------------------------------------------------


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate_review(answer, facts: dict) -> list[str]:
    if not isinstance(answer, dict):
        return ["the answer must be a JSON object"]
    errors = []
    extra = set(answer) - set(REVIEW_SCHEMA["properties"])
    if extra:
        errors.append(f"unknown fields {sorted(extra)}")
    if not isinstance(answer.get("reading"), str) or not answer["reading"].strip():
        errors.append("`reading` must be a non-empty string")
    notes = answer.get("notes")
    if not isinstance(notes, list) or len(notes) > 4 or any(not isinstance(n, str) for n in notes):
        errors.append("`notes` must be a list of at most 4 strings")
    changes = answer.get("changes")
    if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
        return errors + [f"`changes` must be a list of at most {MAX_CHANGES} changes"]
    if answer.get("nochange") is True and changes:
        errors.append("`nochange` is true but there are changes")
    if answer.get("nochange") is False and not changes:
        errors.append("no changes: set `nochange` to true")
    cat = {c["id"]: c for c in facts["catalog"]}
    entries = {e["alias"]: e for d in facts["plan"]["days"] for e in d["entries"]}
    days = [d["alias"] for d in facts["plan"]["days"]]
    seen = set()
    for i, c in enumerate(changes):
        w = f"changes[{i}]"
        if not isinstance(c, dict) or c.get("type") not in TYPES:
            errors.append(f"{w}.type must be one of {', '.join(TYPES)}")
            continue
        typ = c["type"]
        if not isinstance(c.get("why"), str) or not c["why"].strip():
            errors.append(f"{w} ({typ}) needs `why`")
        key = (typ, c.get("entry"), c.get("day"))
        if key in seen:
            errors.append(f"{w}: the same change twice")
        seen.add(key)
        needs_entry = typ in (
            "swap_exercise",
            "remove_exercise",
            "sets",
            "rep_range",
            "duration_range",
            "rest",
            "reorder",
        )
        e = entries.get(c.get("entry"))
        if needs_entry and e is None:
            errors.append(f"{w} ({typ}): `entry` {c.get('entry')!r} is not an entry of the plan")
            continue
        if typ in ("add_exercise", "rename_day", "remove_day") and c.get("day") not in days:
            errors.append(f"{w} ({typ}): `day` {c.get('day')!r} is not a day of the plan")
            continue
        if typ in ("swap_exercise", "add_exercise"):
            x = cat.get(c.get("exercise"))
            if x is None:
                errors.append(f"{w} ({typ}): `exercise` {c.get('exercise')!r} is not in the catalog")
                continue
            if typ == "swap_exercise" and x["kind"] != e["kind"]:
                errors.append(
                    f"{w}: swap only between the same kind ({e['kind']}), {x['name']} is {x['kind']}"
                )
        kind = e["kind"] if e else (cat[c["exercise"]]["kind"] if typ == "add_exercise" else None)
        if typ in ("sets", "add_exercise") and not (_int(c.get("sets")) and 1 <= c["sets"] <= 6):
            errors.append(f"{w} ({typ}): `sets` must be 1–6")
        if typ == "rep_range" or (typ == "add_exercise" and kind == "reps"):
            if kind != "reps":
                errors.append(f"{w}: {e['exercise']} is timed, use duration_range")
            elif not (
                _int(c.get("reps_min"))
                and _int(c.get("reps_max"))
                and 1 <= c["reps_min"] <= c["reps_max"] <= 30
            ):
                errors.append(f"{w} ({typ}): `reps_min` ≤ `reps_max`, 1–30")
        if typ == "duration_range" or (typ == "add_exercise" and kind == "time"):
            if kind != "time":
                errors.append(f"{w}: {e['exercise']} counts reps, use rep_range")
            elif not (
                _int(c.get("seconds_min"))
                and _int(c.get("seconds_max"))
                and 10 <= c["seconds_min"] <= c["seconds_max"] <= 300
            ):
                errors.append(f"{w} ({typ}): `seconds_min` ≤ `seconds_max`, 10–300")
        if typ in ("rest", "add_exercise") and not (_int(c.get("rest_s")) and 30 <= c["rest_s"] <= 300):
            errors.append(f"{w} ({typ}): `rest_s` must be 30–300")
        if typ == "reorder" and not (_int(c.get("position")) and c["position"] >= 1):
            errors.append(f"{w}: `position` must be 1 or more")
        if typ == "rename_day" and not (isinstance(c.get("name"), str) and 1 <= len(c["name"].strip()) <= 60):
            errors.append(f"{w}: `name` must be 1–60 characters")
        if typ == "remove_day" and len(days) < 2:
            errors.append(f"{w}: the only day cannot be removed")
        if typ == "reorder_days" and (
            not isinstance(c.get("order"), list) or sorted(c["order"]) != sorted(days)
        ):
            errors.append(f"{w}: `order` must list every day alias exactly once")
    return errors


WORDS = {
    "de": {"sets": "{n} Sätze", "position": "Platz {n}"},
    "en": {"sets": "{n} sets", "position": "position {n}"},
}


def describe(c: dict, facts: dict, lang: str = "de") -> dict:
    """Names for the WebUI: what the change is about, before and after."""
    entries = {e["alias"]: e for d in facts["plan"]["days"] for e in d["entries"]}
    days = {d["alias"]: d["name"] for d in facts["plan"]["days"]}
    cat = {x["id"]: x for x in facts["catalog"]}
    e = entries.get(c.get("entry"))
    rng = (
        f"{c['seconds_min']}–{c['seconds_max']} s"
        if c.get("seconds_min") is not None
        else f"{c.get('reps_min')}–{c.get('reps_max')}"
    )
    typ = c["type"]
    where = (
        f"{days.get(c['entry'][: c['entry'].index('E')], '')} · {e['exercise']}"
        if e
        else days.get(c.get("day"), "")
    )
    before = after = None
    if typ == "swap_exercise":
        before, after = e["exercise"], cat[c["exercise"]]["name"]
    elif typ == "add_exercise":
        after = f"{cat[c['exercise']]['name']} {c['sets']}×{rng}, {c['rest_s']} s"
    elif typ == "remove_exercise":
        before = f"{e['exercise']} {e['sets']}×{e['range']}"
    elif typ == "sets":
        w = WORDS.get(lang, WORDS["de"])["sets"]
        before, after = w.format(n=e["sets"]), w.format(n=c["sets"])
    elif typ in ("rep_range", "duration_range"):
        before, after = e["range"], rng
    elif typ == "rest":
        before, after = f"{e['rest_s'] or '–'} s", f"{c['rest_s']} s"
    elif typ == "reorder":
        after = WORDS.get(lang, WORDS["de"])["position"].format(n=c["position"])
    elif typ == "rename_day":
        before, after = days[c["day"]], c["name"]
    elif typ == "remove_day":
        before = days[c["day"]]
    elif typ == "reorder_days":
        before, after = " → ".join(days.values()), " → ".join(days[a] for a in c["order"])
    return {**c, "where": where, "before": before, "after": after}


def ask_review(s: ai.AiSettings, lang: str, facts: dict) -> tuple[dict, str, bool]:
    task = {"de": "Prüfe den Plan.", "en": "Review the plan."}[lang]
    user = f"{task}\n\nFakten (JSON):\n{json.dumps(_for_model(facts), ensure_ascii=False, default=str)}"
    answer, model, repaired = coach.ask(
        s, SYSTEM[lang], user, REVIEW_SCHEMA, lambda a: validate_review(a, facts), lang, temperature=0.3
    )
    answer["changes"] = [describe(c, facts, lang) for c in answer["changes"]]
    answer["fingerprint"] = facts["_fingerprint"]
    answer["plan_id"] = facts["_state"]["id"]
    return answer, model, repaired


coach.KINDS["review"] = ask_review


# --- taking changes over ----------------------------------------------------------------------------------


def start_review(conn: Connection, lang: str = "de", note: str | None = None, run_async: bool = True) -> dict:
    s = ai.load_settings(conn)
    if not ai.available(s, "coach"):
        raise coach.CoachError(ai.unavailable_reason(s, "coach"))
    facts = review_facts(conn, note)
    return coach.create(conn, "review", facts, lang, PROMPT_VERSION, run_async=run_async)


def _current(conn: Connection, plan_id: str) -> dict:
    return plan_state(t.get_plan(conn, uuid.UUID(plan_id)))


def apply_changes(state: dict, changes: list[dict], aliases: dict, catalog_ids: dict[str, str]) -> dict:
    """The plan state with the changes made, in order (pure)."""
    s = json.loads(json.dumps(state))
    days = {d["id"]: d for d in s["days"]}

    def find(entry_id):
        for d in s["days"]:
            for e in d["exercises"]:
                if e["id"] == entry_id:
                    return d, e
        raise coach.CoachError("an entry of the change is no longer in the plan")

    for c in changes:
        typ = c["type"]
        a = aliases.get(c.get("entry")) or aliases.get(c.get("day")) or {}
        if typ == "swap_exercise":
            _, e = find(a["entry"])
            e.update(exercise_id=catalog_ids[c["exercise"]], target_reps=None, target_weight_kg=None)
        elif typ == "add_exercise":
            d = days[a["day"]]
            timed = c.get("seconds_min") is not None
            new = {
                "id": None,
                "exercise_id": catalog_ids[c["exercise"]],
                "sets": c["sets"],
                "reps_min": None if timed else c["reps_min"],
                "reps_max": None if timed else c["reps_max"],
                "duration_s": c.get("seconds_min"),
                "duration_min_s": c.get("seconds_min"),
                "duration_max_s": c.get("seconds_max"),
                "rest_s": c["rest_s"],
                "target_reps": None,
                "target_weight_kg": None,
                "note": None,
            }
            pos = (c.get("position") or len(d["exercises"]) + 1) - 1
            d["exercises"].insert(max(0, min(pos, len(d["exercises"]))), new)
        elif typ == "remove_exercise":
            d, e = find(a["entry"])
            d["exercises"].remove(e)
        elif typ == "sets":
            find(a["entry"])[1]["sets"] = c["sets"]
        elif typ == "rep_range":
            find(a["entry"])[1].update(reps_min=c["reps_min"], reps_max=c["reps_max"])
        elif typ == "duration_range":
            _, e = find(a["entry"])
            e.update(
                duration_min_s=c["seconds_min"],
                duration_max_s=c["seconds_max"],
                duration_s=min(max(e["duration_s"] or c["seconds_min"], c["seconds_min"]), c["seconds_max"]),
            )
        elif typ == "rest":
            find(a["entry"])[1]["rest_s"] = c["rest_s"]
        elif typ == "reorder":
            d, e = find(a["entry"])
            d["exercises"].remove(e)
            d["exercises"].insert(max(0, min(c["position"] - 1, len(d["exercises"]))), e)
        elif typ == "rename_day":
            days[a["day"]]["name"] = c["name"].strip()[:60]
        elif typ == "remove_day":
            s["days"] = [d for d in s["days"] if d["id"] != a["day"]]
        elif typ == "reorder_days":
            order = [aliases[x]["day"] for x in c["order"]]
            s["days"].sort(key=lambda d: order.index(d["id"]) if d["id"] in order else len(order))
    return s


def take_over(conn: Connection, mid, indexes: list[int]) -> dict:
    """Makes the chosen changes (by index) in the plan; keeps the plan as it was for undo."""
    m = coach.message(conn, mid)
    if m is None or m["kind"] != "review" or m["status"] != "done":
        raise coach.CoachError("finished plan review not found")
    r = m["result"]
    if r.get("applied"):
        raise coach.CoachError("changes of this review were taken over already")
    chosen = sorted(set(indexes))
    if not chosen or any(not 0 <= i < len(r["changes"]) for i in chosen):
        raise coach.CoachError("choose at least one of the proposed changes")
    before = _current(conn, r["plan_id"])
    if fingerprint(before) != r["fingerprint"]:
        raise coach.CoachError("the plan was changed since the review; ask for a new review")
    facts = conn.execute(text("SELECT facts FROM coach_message WHERE id = :id"), {"id": mid}).scalar()
    catalog_ids = {}
    for i in chosen:
        c = r["changes"][i]
        if c.get("exercise") and c["exercise"] not in catalog_ids:
            kind, ref = c["exercise"].split(":", 1)
            catalog_ids[c["exercise"]] = (
                t.adopt_library_entry(ref, t.Response(), conn)["id"] if kind == "lib" else ref
            )
    after = apply_changes(
        before,
        [r["changes"][i] for i in chosen],
        facts["_aliases"],
        {k: str(v) for k, v in catalog_ids.items()},
    )
    plan = write_state(conn, after)
    result = {**r, "applied": chosen, "snapshot": before, "after_fingerprint": fingerprint(plan_state(plan))}
    conn.execute(
        text("UPDATE coach_message SET result = CAST(:r AS jsonb), applied_plan_id = :p WHERE id = :id"),
        {"r": json.dumps(result, ensure_ascii=False, default=str), "p": plan["id"], "id": mid},
    )
    return plan


def undo(conn: Connection, mid) -> dict:
    """Puts the plan back as it was before the changes were taken over."""
    m = coach.message(conn, mid)
    if m is None or m["kind"] != "review" or not (m["result"] or {}).get("applied"):
        raise coach.CoachError("nothing of this review was taken over")
    r = m["result"]
    if fingerprint(_current(conn, r["plan_id"])) != r["after_fingerprint"]:
        raise coach.CoachError(
            "the plan was changed after the review's changes; undo by hand in the plan editor"
        )
    plan = write_state(conn, r["snapshot"])
    result = {k: v for k, v in r.items() if k not in ("applied", "snapshot", "after_fingerprint")}
    conn.execute(
        text("UPDATE coach_message SET result = CAST(:r AS jsonb), applied_plan_id = NULL WHERE id = :id"),
        {"r": json.dumps(result, ensure_ascii=False, default=str), "id": mid},
    )
    return plan
