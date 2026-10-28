"""Processing of exports from other apps: daily totals (MyFitnessPal, Cronometer, weight lists),
strength workouts (Strong, Hevy) and Health Connect batches from the Android app. Parsing lives in
`daily_csv` / `workout_csv` / `health_connect`; this writes.

Daily totals are samples of a per-app device, replaced by every newer export of the same app.
Workouts become manual workouts keyed by app and start time: importing again adds new ones and
leaves the ones already there (and any review made since) alone.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from fastapi import Response
from sqlalchemy import Connection, Engine, text

from vitalocal import exercise_library
from vitalocal.config import get_settings
from vitalocal.ingest import daily_csv, health_connect, workout_csv
from vitalocal.ingest.service import (
    _fail_run,
    _finish_run,
    _insert_member,
    _start_run,
    _upsert_device,
    upsert_samples,
)
from vitalocal.rawstore import RawStore

APP_NAMES = {
    "myfitnesspal": "MyFitnessPal",
    "cronometer": "Cronometer",
    "csv": "CSV import",
    "strong": "Strong",
    "hevy": "Hevy",
}
IMPORT_NS = uuid.UUID("6b0f7a52-2f4e-4c55-9a0e-5f1b7d1c7a11")


def _run(engine: Engine, batch_id: int, sha: str, member: str, parser, work) -> dict:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM artifact_member WHERE batch_id = :id"), {"id": batch_id})
        _insert_member(conn, batch_id, sha, member, parser.PARSER_NAME, None, {})
        run_id = _start_run(conn, batch_id, sha, parser.PARSER_NAME, parser.PARSER_VERSION)
    try:
        with engine.begin() as conn:
            stats = work(conn, run_id)
            _finish_run(conn, run_id, stats)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        with engine.begin() as conn:
            _fail_run(conn, run_id, err)
        return {"parse_run": {"parse_run_id": run_id, "status": "failed"}, "parse_errors": [err]}
    return {"parse_run": {"parse_run_id": run_id, "status": "completed", **stats.get("write", {})}}


def _tag(conn: Connection, batch_id: int, app: str, export: str) -> None:
    conn.execute(
        text("UPDATE ingest_batch SET client_meta = client_meta || CAST(:m AS jsonb) WHERE id = :id"),
        {"id": batch_id, "m": json.dumps({"app": app, "export": export})},
    )


def process_daily(engine: Engine, store: RawStore, batch_id: int, sha: str) -> dict:
    parsed = daily_csv.parse(store.path_for(sha).read_bytes(), get_settings().timezone)

    def work(conn: Connection, run_id: int) -> dict:
        _tag(conn, batch_id, parsed.app, "daily_csv")
        device = _upsert_device(
            conn, f"import-{parsed.app}", APP_NAMES[parsed.app], "app", APP_NAMES[parsed.app], source="import"
        )
        samples = upsert_samples(
            conn,
            run_id,
            ((s.ts, device, s.metric, s.value, s.unit) for s in parsed.samples),
            retract_parser=daily_csv.PARSER_NAME,
        )
        return {**parsed.stats(), "write": {"samples": samples}}

    return _run(engine, batch_id, sha, "daily.csv", daily_csv, work)


def process_workouts(engine: Engine, store: RawStore, batch_id: int, sha: str) -> dict:
    from vitalocal.profile import get_profile

    with engine.connect() as conn:
        unit = "lb" if get_profile(conn)["units"] == "imperial" else "kg"
    parsed = workout_csv.parse(store.path_for(sha).read_bytes(), get_settings().timezone, unit)

    def work(conn: Connection, run_id: int) -> dict:
        _tag(conn, batch_id, parsed.app, "workout_csv")
        exercises = ExerciseResolver(conn)
        added = skipped = 0
        for w in parsed.workouts:
            if write_workout(conn, parsed.app, w, exercises):
                added += 1
            else:
                skipped += 1
        write = {
            "workouts_added": added,
            "workouts_existing": skipped,
            "exercises_created": exercises.created,
            "exercises_matched": exercises.matched,
        }
        return {**parsed.stats(), "write": write}

    return _run(engine, batch_id, sha, "workouts.csv", workout_csv, work)


class ExerciseResolver:
    """Exercise id for a name from another app: own exercise of that name, library entry (adopted
    on first use) or a new own exercise."""

    def __init__(self, conn: Connection):
        self.conn = conn
        self.cache: dict[str, uuid.UUID] = {}
        self.library = exercise_library.entries()
        self.created: list[str] = []
        self.matched: dict[str, str] = {}

    def __call__(self, name: str, timed: bool) -> uuid.UUID:
        from vitalocal.api.training import ExerciseIn, adopt_library_entry, create_exercise

        k = name.strip().lower()
        if k in self.cache:
            return self.cache[k]
        eid = self.conn.execute(text("SELECT id FROM exercise WHERE lower(name) = :n"), {"n": k}).scalar()
        if eid is None:
            key = next((e["key"] for e in self.library if e["name"].lower() == k), None)
            key = key or workout_csv.library_match(name, self.library)
            if key:
                eid = adopt_library_entry(key, Response(), self.conn)["id"]
                self.matched[name] = key
            else:
                body = ExerciseIn(name=name.strip()[:80], kind="time" if timed else "reps")
                eid = create_exercise(body, self.conn)["id"]
                self.created.append(name)
        self.cache[k] = eid
        return eid


def write_workout(conn: Connection, app: str, w: workout_csv.Workout, exercise: ExerciseResolver) -> bool:
    """A manual workout with its sets, reviewed; False when this workout was imported before."""
    from vitalocal.api.training import SessionIn, SetIn, write_session

    key = uuid.uuid5(IMPORT_NS, f"{app}:{w.start.isoformat()}")
    if conn.execute(text("SELECT 1 FROM activity WHERE manual_key = :k"), {"k": key}).first():
        return False
    aid = conn.execute(
        text("""INSERT INTO activity (device_id, start_ts, end_ts, name, sport, sub_sport, elapsed_s, timer_s,
                                      manual, manual_key)
                VALUES (NULL, :s, :e, :n, 'training', 'strength_training', :d, :d, true, :k) RETURNING id"""),
        {
            "s": w.start,
            "e": w.start + timedelta(seconds=w.seconds),
            "n": w.title[:80],
            "d": w.seconds,
            "k": key,
        },
    ).scalar()
    sets = [
        SetIn(
            position=i,
            exercise_id=exercise(s.exercise, timed=s.reps is None),
            repetitions=s.reps,
            weight_kg=s.weight_kg,
            duration_s=s.duration_s,
            warmup=s.warmup,
        )
        for i, s in enumerate(w.sets)
    ]
    note = f"{w.note}\n\n" if w.note else ""
    write_session(
        conn,
        aid,
        SessionIn(title=w.title[:80], note=f"{note}{APP_NAMES[app]}"[:4000], reviewed=True, sets=sets[:300]),
    )
    if w.rpe:
        conn.execute(
            text("""INSERT INTO checkin (id, ts, kind, value, activity_id, source)
                    VALUES (:id, :ts, 'rpe', :v, :a, :src)"""),
            {
                "id": uuid.uuid5(key, "rpe"),
                "ts": workout_csv.end_of(w),
                "v": min(10, w.rpe),
                "a": aid,
                "src": app,
            },
        )
    return True


HC_METRICS = (
    "steps",
    "active_energy",
    "resting_metabolic_rate",
    "weight",
    "body_fat",
    "heart_rate",
    "resting_heart_rate",
    "spo2",
    "hrv_rmssd",
    "sleep_in_bed",
    "sleep_stage_code",
)


def process_health_connect(engine: Engine, store: RawStore, batch_id: int, sha: str) -> dict:
    parsed = health_connect.parse(store.path_for(sha).read_bytes(), get_settings().timezone)

    def work(conn: Connection, run_id: int) -> dict:
        _tag(conn, batch_id, "health_connect", "batch")
        device = _upsert_device(
            conn, "health-connect", "Android", "Health Connect", "Health Connect", source="health_connect"
        )
        # the batch is the whole truth of its range: what vanished there is retracted
        windows = [(device, m, parsed.start) for m in HC_METRICS] if parsed.start else None
        samples = upsert_samples(
            conn,
            run_id,
            ((ts, device, metric, value, unit) for ts, metric, value, unit in parsed.samples),
            retract_parser=health_connect.PARSER_NAME if windows else None,
            windows=windows,
        )
        added = updated = 0
        for e in parsed.exercises:
            key = uuid.uuid5(IMPORT_NS, f"health_connect:{e.id}")
            params = {
                "s": e.start,
                "e": e.end,
                "d": (e.end - e.start).total_seconds(),
                "k": key,
                "n": (e.title or "")[:80] or None,
                "sport": e.sport,
                "sub": e.sub_sport,
                "kcal": e.kcal,
            }
            done = conn.execute(
                text("""UPDATE activity SET start_ts = :s, end_ts = :e, elapsed_s = :d, timer_s = :d,
                            energy_kcal = :kcal WHERE manual_key = :k RETURNING id"""),
                params,
            ).first()
            if done:
                updated += 1
                continue
            conn.execute(
                text("""INSERT INTO activity (device_id, start_ts, end_ts, name, sport, sub_sport, elapsed_s,
                                              timer_s, energy_kcal, manual, manual_key)
                        VALUES (NULL, :s, :e, :n, :sport, :sub, :d, :d, :kcal, true, :k)"""),
                params,
            )
            added += 1
        return {
            **parsed.stats(),
            "write": {"samples": samples, "workouts_added": added, "workouts_updated": updated},
        }

    return _run(engine, batch_id, sha, "health_connect.json", health_connect, work)
