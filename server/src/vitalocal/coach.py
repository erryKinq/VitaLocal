"""AI coach in the training area. For now: the debrief of one workout.

The model never queries the database. VitaLocal builds the facts (as for the day summary), the
model answers as JSON in a fixed shape, and `validate_debrief` – not the prompt – decides what is
accepted: a wrong answer gets one repair round with the errors, then the request fails. A debrief is
a reading, never a change: progression targets and the plan stay with VitaLocal's rules.

Requests run in a background thread (a local model can take minutes); the row in `coach_message`
is the job: pending → done (result) or error.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Connection, text

from vitalocal import ai
from vitalocal.api import training as t
from vitalocal.api.days import latest_weight
from vitalocal.db import get_engine

PROMPT_VERSION = 2
MAX_ITEM_CHARS = 400
# a request still pending this long after its start was cut off (server restart)
STALE_AFTER = timedelta(minutes=30)

DEBRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "score": {"type": "integer"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "watch": {"type": "array", "items": {"type": "string"}},
        "next_time": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "score", "highlights", "watch", "next_time"],
    "additionalProperties": False,
}

SYSTEM = {
    "de": """Du bist ein erfahrener, nüchterner Krafttrainings-Coach. Du wertest EIN Training aus, so wie es \
erfasst wurde. Du bekommst nur Fakten als JSON und antwortest ausschließlich mit einem JSON-Objekt.

So liest du die Fakten:
- `session`: das Training. Pro Übung die Arbeitssätze (`sets`, „Wdh @ kg“ oder Sekunden), was geplant war \
(`planned`), die Bewertung der Person (`rating`: easy = zu leicht, good = gut, hard = zu schwer) und der \
Vorschlag aus den Sätzen (`suggested_rating`).
- `history`: pro Übung die letzten Male vorher (neueste zuerst). Vergleiche Gewicht, Wiederholungen und \
Sätze \
mit heute und nenne, was sich bewegt hat und was nicht – mit Zahlen. Leer: das erste Mal, dann nur \
heute einordnen.
- `hard_in_a_row`: wie oft die Übung zuletzt in Folge als zu schwer bewertet wurde; ab 2 gehört das in \
`watch`.
- `next_targets`: was VitaLocals Progression als Nächstes vorschlägt. Das ist fest – du änderst es \
nicht, du darfst \
es erklären.
- `muscles_not_ready_before`: Muskeln, die vor dem Training laut VitaLocal noch nicht erholt waren.
- `bodyweight`: Gewicht jetzt und vor 4 Wochen; nur erwähnen, wenn es sich deutlich bewegt.
- Bei Körpergewichtsübungen ist das Gewicht Zusatzlast (oft 0): Fortschritt sind dort Wiederholungen, \
dann Sätze.

Regeln:
- Ein Training ist kein Trend. Keine Planänderungen, keine neuen Übungen, keine Gewichte, die von \
`next_targets` \
abweichen.
- Zeitangaben aus `days_before` (Tage vor diesem Training): 7 Tage = „vor einer Woche“, nicht „Vorjahr“.
- Bewertungen auf Deutsch nennen: „zu leicht“, „gut“, „zu schwer“ – nie easy/good/hard.
- Jede Aussage nennt die Zahl, auf der sie beruht. Kein Lob-Überschwang, keine Floskeln.
- Du bist kein Arzt. Bei Schmerzen (nicht Muskelkater) zur ärztlichen Abklärung raten, keine Diagnosen.

Antwort (JSON, Deutsch):
- `summary`: 2–3 Sätze, wie das Training lief, mit Zahlen.
- `score`: ganze Zahl 1–10 für das Training: 9–10 alles Geplante geschafft und irgendwo Fortschritt; \
7–8 geschafft \
mit kleinen Abstrichen oder ohne Bewegung; 5–6 deutliche Abstriche oder ein Schritt zurück; unter 5 \
großteils \
ausgelassen. Bewertet wird das Training, nicht die Person.
- `highlights`: 1–4 kurze Punkte, was gut lief, jeweils mit Zahl.
- `watch`: 0–3 kurze Punkte, worauf zu achten ist.
- `next_time`: 1–3 konkrete Sätze für das nächste Mal (passend zu `next_targets`).""",
    "en": """You are an experienced, level-headed strength coach. You debrief ONE workout as it was \
logged. You \
get facts as JSON only and answer with a JSON object only.

Reading the facts:
- `session`: the workout. Per exercise the work sets (`sets`, "reps @ kg" or seconds), what was planned \
(`planned`), the person's rating (`rating`: easy = too easy, good, hard = too hard) and the rating the \
sets suggest \
(`suggested_rating`).
- `history`: per exercise the previous times (newest first). Compare weight, reps and sets with today \
and say what \
moved and what did not – with numbers. Empty: the first time; read today on its own.
- `hard_in_a_row`: how often the exercise was rated too hard in a row; from 2 it belongs in `watch`.
- `next_targets`: what VitaLocal's progression suggests next. It is fixed – you do not change it, you \
may explain it.
- `muscles_not_ready_before`: muscles VitaLocal estimated as not yet recovered before the workout.
- `bodyweight`: weight now and 4 weeks ago; mention only a clear change.
- On bodyweight exercises the weight is extra load (often 0): progress there is reps, then sets.

Rules:
- One workout is not a trend. No plan changes, no new exercises, no weights other than `next_targets`.
- Time spans come from `days_before` (days before this workout): 7 days = "a week ago".
- Every statement names the number it rests on. No over-the-top praise, no filler.
- You are not a doctor. For pain (not soreness) suggest seeing one; no diagnoses.

Answer (JSON, English):
- `summary`: 2–3 sentences on how the workout went, with numbers.
- `score`: whole number 1–10 for the workout: 9–10 all planned work done and progress somewhere; 7–8 \
done with minor \
misses or no movement; 5–6 real misses or a step back; below 5 largely skipped. Judge the training, not \
the person.
- `highlights`: 1–4 short points on what went well, each with a number.
- `watch`: 0–3 short points to keep an eye on.
- `next_time`: 1–3 concrete sentences for next time (in line with `next_targets`).""",
}

REPAIR = {
    "de": "Deine Antwort wurde abgelehnt:\n{errors}\n\n"
    "Antworte noch einmal, nur mit dem korrigierten JSON-Objekt.",
    "en": "Your answer was rejected:\n{errors}\n\nAnswer again with the corrected JSON object only.",
}


class CoachError(Exception):
    pass


# --- facts ------------------------------------------------------------------------------------------------


def _sets_text(sets: list[dict], kind: str) -> str:
    return ", ".join(
        t.target_text(
            1,
            None if kind == "time" else s["reps"],
            s["weight_kg"],
            s["duration_s"] if kind == "time" else None,
        )[2:]
        for s in sets
    )


def debrief_facts(conn: Connection, aid: int) -> dict:
    """What the model gets for one workout: the session, each exercise's previous times, the
    progression's next targets, recovery before the workout and the body weight trend."""
    d = t.session_detail(conn, aid)
    act = t._activity(conn, aid)
    exmap = t._exercise_map(conn)
    tz = t._tz()
    start = act["start_ts"]
    exercises, history, hard = [], {}, {}
    for e in d["exercises"]:
        ex = exmap.get(e["exercise_id"], {})
        name = e["exercise"]
        exercises.append({
            "exercise": name, "kind": ex.get("kind"), "bodyweight": bool(ex.get("bodyweight")),
            "sets": e["text"], "planned": e.get("planned"), "rating": e["rating"],
            "suggested_rating": (e.get("suggestion") or {}).get("rating"), "best_e1rm_kg": e["best_e1rm_kg"],
        })  # fmt: skip
        prev = t.performances(
            conn, e["exercise_id"], before=start, limit=4, plan_exercise_id=e["plan_exercise_id"]
        )
        history[name] = [
            {
                "date": p["date"].isoformat(),
                "days_before": (start.astimezone(tz).date() - p["date"]).days,
                "sets": _sets_text(p["sets"], ex.get("kind")),
                "rating": p["rating"],
            }
            for p in prev
        ]
        n = 1 if e["rating"] == "hard" else 0
        if n:
            for p in prev:
                if p["rating"] != "hard":
                    break
                n += 1
        hard[name] = n
    next_targets = {}
    day_id = d["plan_day_id"]
    if day_id:
        pid = conn.execute(text("SELECT plan_id FROM plan_day WHERE id = :d"), {"d": day_id}).scalar()
        day = t._day_of(t.get_plan(conn, pid), day_id) if pid else None
        for x in t.day_targets(conn, day) if day else []:
            next_targets[x["exercise"]] = {"next": x["next"]["text"], "reason": x["next"].get("reason")}
    before = t.muscle_state(conn, start - timedelta(minutes=1))
    w_now = latest_weight(conn, start)
    w_then = latest_weight(conn, start - timedelta(days=28))
    a = d["activity"]
    return {
        "session": {
            "date": start.astimezone(tz).date().isoformat(),
            "weekday": start.astimezone(tz).strftime("%A"),
            "title": d["display_title"],
            "plan_day": d["plan_day"] or (d.get("proposal") or {}).get("plan_day"),
            "reviewed": d["reviewed"],
            "duration_min": round((a["elapsed_s"] or 0) / 60) or None,
            "avg_hr": a["avg_hr"],
            "energy_kcal": a["energy_kcal"],
            "note": d["note"],
            "exercises": exercises,
        },
        "history": history,
        "hard_in_a_row": {k: v for k, v in hard.items() if v},
        "next_targets": next_targets,
        "muscles_not_ready_before": {
            m["label"]: {"state": m["state"], "ready_in_h": m["ready_in_h"]}
            for m in before["muscles"]
            if m["state"] != "ready"
        },
        "bodyweight": {"now_kg": w_now, "four_weeks_ago_kg": w_then},
    }


# --- answer -----------------------------------------------------------------------------------------------


def validate_debrief(answer) -> list[str]:
    """Errors in a debrief answer (empty: accepted). The shape is closed: nothing beyond the fields."""
    if not isinstance(answer, dict):
        return ["the answer must be a JSON object"]
    errors = []
    extra = set(answer) - set(DEBRIEF_SCHEMA["properties"])
    if extra:
        errors.append(f"unknown fields {sorted(extra)}: a debrief carries no changes or other data")
    summary = answer.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("`summary` must be a non-empty string")
    elif len(summary) > 3 * MAX_ITEM_CHARS:
        errors.append("`summary` is too long (2–3 sentences)")
    elif not any(c.isdigit() for c in summary):
        errors.append("`summary` must name at least one number from the session")
    score = answer.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 10:
        errors.append("`score` must be a whole number from 1 to 10")
    for key, lo, hi in (("highlights", 1, 4), ("watch", 0, 3), ("next_time", 1, 3)):
        items = answer.get(key)
        if not isinstance(items, list) or not lo <= len(items) <= hi:
            errors.append(f"`{key}` must be a list of {lo}–{hi} strings")
            continue
        for i, x in enumerate(items):
            if not isinstance(x, str) or not x.strip():
                errors.append(f"`{key}[{i}]` must be a non-empty string")
            elif len(x) > MAX_ITEM_CHARS:
                errors.append(f"`{key}[{i}]` is too long")
    return errors


def ask(
    s: ai.AiSettings, system: str, user: str, schema: dict, validate, lang: str, temperature: float = 0.3
):
    """(answer, model, repaired): the model answers in `schema`; `validate(answer)` lists what is wrong,
    which goes back to the model once; a second rejection fails the request."""
    content, model = ai.chat(s, system, user, json_schema=schema, temperature=temperature)
    try:
        answer = ai.parse_json(content)
        errors = validate(answer)
    except ai.AiError as e:
        answer, errors = None, [str(e)]
    if not errors:
        return {k: answer[k] for k in schema["properties"] if k in answer}, model, False
    repair = REPAIR[lang].format(errors="\n".join(f"- {e}" for e in errors[:15]))
    content, model = ai.chat(
        s, system, f"{user}\n\n{repair}", json_schema=schema, temperature=max(0.1, temperature - 0.1)
    )
    answer = ai.parse_json(content)
    errors = validate(answer)
    if errors:
        raise CoachError("the model's answer was rejected twice: " + "; ".join(errors[:5]))
    return {k: answer[k] for k in schema["properties"] if k in answer}, model, True


def _ask_debrief(s: ai.AiSettings, lang: str, facts: dict) -> tuple[dict, str, bool]:
    task = "Werte dieses Training aus." if lang == "de" else "Debrief this workout."
    user = f"{task}\n\nFakten (JSON):\n{json.dumps(facts, ensure_ascii=False, indent=1, default=str)}"
    return ask(s, SYSTEM[lang], user, DEBRIEF_SCHEMA, validate_debrief, lang)


# kind → ask(settings, lang, facts); coach_plan registers "plan"
KINDS = {"debrief": _ask_debrief}


# --- jobs -------------------------------------------------------------------------------------------------

_COLS = """id, kind, activity_id, refines, applied_plan_id, lang, status, result, model, error, repaired,
           prompt_version, duration_ms,
           created_at, finished_at, facts->'session'->>'title' AS title,
           facts->'session'->>'date' AS session_date"""


def _row(conn: Connection, mid) -> dict | None:
    r = conn.execute(text(f"SELECT {_COLS} FROM coach_message WHERE id = :id"), {"id": mid}).first()
    return dict(r._mapping) if r else None


def expire_stale(conn: Connection) -> None:
    conn.execute(
        text("""UPDATE coach_message SET status = 'error', error = 'interrupted (server restarted)',
                    finished_at = now()
                WHERE status = 'pending' AND created_at < :t"""),
        {"t": datetime.now().astimezone() - STALE_AFTER},
    )


def start_debrief(conn: Connection, aid: int, lang: str = "de", run_async: bool = True) -> dict:
    """Creates the request with its facts and lets the model work on it in the background."""
    s = ai.load_settings(conn)
    if not ai.available(s, "coach"):
        raise CoachError(ai.unavailable_reason(s, "coach"))
    facts = debrief_facts(conn, aid)
    if not facts["session"]["exercises"]:
        raise CoachError("the workout has no sets assigned to exercises yet; review it first")
    return create(conn, "debrief", facts, lang, PROMPT_VERSION, activity_id=aid, run_async=run_async)


def create(
    conn: Connection,
    kind: str,
    facts: dict,
    lang: str,
    version: int,
    activity_id=None,
    refines=None,
    run_async: bool = True,
) -> dict:
    """A pending request of `kind` with its facts; the model works on it in the background."""
    mid = uuid.uuid4()
    conn.execute(
        text("""INSERT INTO coach_message (id, kind, activity_id, refines, lang, facts, prompt_version)
                VALUES (:id, :k, :a, :r, :l, CAST(:f AS jsonb), :v)"""),
        {"id": mid, "k": kind, "a": activity_id, "r": refines, "l": lang, "f": json.dumps(facts, default=str),
         "v": version},
    )  # fmt: skip
    if run_async:
        # the request's own transaction commits before the thread reads the row
        threading.Thread(target=_run_after_commit, args=(mid,), daemon=True).start()
    return _row(conn, mid)


def _run_after_commit(mid) -> None:
    for _ in range(50):
        with get_engine().connect() as conn:
            if _row(conn, mid):
                break
        time.sleep(0.1)
    run(mid)


def run(mid) -> None:
    """Works on one pending request; the model call happens outside any transaction."""
    with get_engine().connect() as conn:
        r = conn.execute(
            text("SELECT kind, lang, facts FROM coach_message WHERE id = :id"), {"id": mid}
        ).first()
        s = ai.load_settings(conn)
    if r is None:
        return
    started = time.monotonic()
    try:
        answer, model, repaired = KINDS[r.kind](s, r.lang, r.facts)
        values = {
            "st": "done",
            "res": json.dumps(answer, ensure_ascii=False),
            "m": model,
            "e": None,
            "rep": repaired,
        }
    except (ai.AiError, CoachError) as e:
        values = {"st": "error", "res": None, "m": s.model, "e": str(e), "rep": False}
    with get_engine().begin() as conn:
        conn.execute(
            text("""UPDATE coach_message SET status = :st, result = CAST(:res AS jsonb), model = :m,
                        error = :e, repaired = :rep, duration_ms = :ms, finished_at = now()
                    WHERE id = :id"""),
            {**values, "ms": int((time.monotonic() - started) * 1000), "id": mid},
        )


def messages(
    conn: Connection, activity_id: int | None = None, limit: int = 20, kind: str | None = None
) -> list[dict]:
    expire_stale(conn)
    rows = conn.execute(
        text(f"""SELECT {_COLS} FROM coach_message
                 WHERE (CAST(:a AS bigint) IS NULL OR activity_id = :a)
                   AND (CAST(:k AS text) IS NULL OR kind = :k)
                 ORDER BY created_at DESC LIMIT :n"""),
        {"a": activity_id, "k": kind, "n": limit},
    )
    return [dict(x._mapping) for x in rows]


def delete(conn: Connection, mid) -> bool:
    """Removes a message; proposals refining it lose the link. A job still running finds no row."""
    return conn.execute(text("DELETE FROM coach_message WHERE id = :id"), {"id": mid}).rowcount > 0


def message(conn: Connection, mid) -> dict | None:
    expire_stale(conn)
    return _row(conn, mid)
