"""Strength training: exercise library, plans with split days, the session layer on activities
(title, plan day, exercise per set, added/hidden/warm-up sets, ratings), progression and stats.

A session is an activity (imported from the watch or entered by hand) plus the user's layer. Until
it is reviewed, VitaLocal proposes the plan day (rotation of the active plan) and an exercise per
watch set; the review confirms or corrects them and rates each exercise (too easy / good / too
hard). Only reviewed sessions feed the progression ("next time: 3×10 @ 25 kg") and the stats.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection, text

from vitalocal import exercise_library, profile, recovery, training
from vitalocal import exercises as fit_exercises
from vitalocal.api.days import _tz, latest_weight
from vitalocal.db import CONN
from vitalocal.insights import _latest as insights_latest

router = APIRouter(prefix="/api/v1/training", tags=["training"])

Rating = Literal["easy", "good", "hard"]
_EX_COLS = """id, name, garmin_category, garmin_name, kind, muscles, muscles_secondary, increment_kg,
              dumbbells, bodyweight, equipment, library_key, note, archived, created_at"""
PLATES_KEY = "training_plates"
# An activity counts as a strength session when it is one on the watch, was entered by hand or has sets.
_NON_STRENGTH_SQL = ", ".join(f"'{c}'" for c in sorted(training.NON_STRENGTH))
_IS_SESSION = f"""(a.sub_sport = 'strength_training' OR a.manual
                  OR EXISTS (SELECT 1 FROM training_session t WHERE t.activity_id = a.id)
                  OR EXISTS (SELECT 1 FROM activity_set s WHERE s.activity_id = a.id AND s.set_type = 'active'
                             AND s.exercise_category NOT IN ({_NON_STRENGTH_SQL})))"""


# --- exercises ------------------------------------------------------------------------------------------


class ExerciseIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    garmin_category: str | None = Field(None, description="FIT exercise_category; links watch sets")
    garmin_name: str | None = Field(None, description="FIT exercise name within the category")
    kind: Literal["reps", "time"] = "reps"
    muscles: list[str] | None = Field(
        None,
        description="Primary muscles (`/training/muscles`; the old chest, shoulders and back are taken and "
        "stand for their parts); omitted: the default of the Garmin exercise or category",
    )
    muscles_secondary: list[str] | None = None
    increment_kg: float = Field(2.5, gt=0, le=50, description="Weight step for the progression")
    dumbbells: int = Field(
        0,
        ge=0,
        le=2,
        description="Loaded with the adjustable dumbbells (`/training/plates`): 1 = the weight of one "
        "dumbbell, 2 = the sum of two; the progression steps to weights the plates allow. 0: increment_kg",
    )
    bodyweight: bool = Field(False, description="Weight is extra load (pull-ups, dips)")
    equipment: list[str] | None = Field(
        None,
        description="What the exercise needs (`/training/equipment`); omitted: none on create, "
        "unchanged on update",
    )
    library_key: str | None = Field(
        None, description="Library entry this exercise is (`/training/library`); only on create"
    )
    note: str | None = Field(None, max_length=500)
    archived: bool = False

    @model_validator(mode="after")
    def _check(self):
        if self.garmin_category is not None and not fit_exercises.is_category(self.garmin_category):
            raise ValueError(f"unknown Garmin category {self.garmin_category!r}")
        valid_name = self.garmin_category is not None and fit_exercises.is_name(
            self.garmin_category, self.garmin_name or ""
        )
        if self.garmin_name is not None and not valid_name:
            raise ValueError(f"{self.garmin_name!r} is not an exercise of {self.garmin_category!r}")
        if self.muscles is not None or self.muscles_secondary is not None:
            prim, sec = training.expand(self.muscles or [], self.muscles_secondary or [])
            for m in prim + sec:
                if m not in training.MUSCLES:
                    raise ValueError(f"unknown muscle {m!r}")
            if self.muscles is not None:
                self.muscles = prim
            if self.muscles_secondary is not None:
                self.muscles_secondary = sec
        for x in self.equipment or []:
            if x not in training.EQUIPMENT:
                raise ValueError(f"unknown equipment {x!r}")
        if self.library_key is not None and self.library_key not in exercise_library.by_key():
            raise ValueError(f"unknown library entry {self.library_key!r}")
        return self


def list_exercises(conn: Connection, include_archived: bool = True) -> list[dict]:
    rows = conn.execute(
        text(f"SELECT {_EX_COLS} FROM exercise WHERE :all OR NOT archived ORDER BY archived, lower(name)"),
        {"all": include_archived},
    )
    return [dict(r._mapping) for r in rows]


def _exercise(conn: Connection, eid) -> dict:
    row = conn.execute(text(f"SELECT {_EX_COLS} FROM exercise WHERE id = :id"), {"id": eid}).first()
    if row is None:
        raise HTTPException(404, "exercise not found")
    return dict(row._mapping)


def _ex_params(e: ExerciseIn) -> dict:
    prim, sec = training.default_muscles(e.garmin_category, e.garmin_name, e.name)
    return {
        **e.model_dump(exclude={"id"}),
        "name": e.name.strip(),
        "equipment": list(dict.fromkeys(e.equipment)) if e.equipment is not None else None,
        "muscles": e.muscles if e.muscles is not None else prim,
        "muscles_secondary": e.muscles_secondary if e.muscles_secondary is not None else sec,
    }


def _name_taken(conn: Connection, name: str, eid) -> None:
    other = conn.execute(
        text("SELECT id FROM exercise WHERE lower(name) = lower(:n) AND id <> :id"),
        {"n": name.strip(), "id": eid},
    ).first()
    if other:
        raise HTTPException(409, f"an exercise named {name!r} exists")


def _library_taken(conn: Connection, key: str | None) -> None:
    if key is None:
        return
    other = conn.execute(text("SELECT name FROM exercise WHERE library_key = :k"), {"k": key}).scalar()
    if other:
        raise HTTPException(409, f"the library entry is your exercise {other!r} already")


@router.get("/muscles")
def get_muscles(conn: Connection = CONN) -> list[dict]:
    """Labels in the profile's language, in display order, with the group (`/training/muscles/groups`)
    of each muscle."""
    lang = profile.lang(conn)
    groups = {g["key"]: g["label"] for g in training.groups(lang)}
    return [{"key": k, "label": v, "group": training.GROUP_OF[k], "group_label": groups[training.GROUP_OF[k]]}
            for k, v in training.muscles(lang).items()]  # fmt: skip


@router.get("/muscles/groups")
def get_muscle_groups(conn: Connection = CONN) -> list[dict]:
    """Muscle groups in display order with their muscles (chest: upper and mid/lower chest …)."""
    return training.groups(profile.lang(conn))


@router.get("/equipment")
def get_equipment(conn: Connection = CONN) -> list[dict]:
    lang = profile.lang(conn)
    hints = training.equipment_hints(lang)
    return [{"key": k, "label": v, "hint": hints.get(k)} for k, v in training.equipment(lang).items()]


@router.get("/exercises")
def get_exercises(include_archived: bool = True, conn: Connection = CONN) -> list[dict]:
    return list_exercises(conn, include_archived)


@router.post("/exercises", status_code=status.HTTP_201_CREATED)
def create_exercise(body: ExerciseIn, conn: Connection = CONN) -> dict:
    """Muscles default to the Garmin category's (e.g. curl → biceps, forearms ½). Safe to repeat with `id`."""
    eid = body.id or uuid.uuid4()
    if conn.execute(text("SELECT 1 FROM exercise WHERE id = :id"), {"id": eid}).first():
        return _exercise(conn, eid)
    _name_taken(conn, body.name, eid)
    _library_taken(conn, body.library_key)
    conn.execute(
        text("""INSERT INTO exercise (id, name, garmin_category, garmin_name, kind, muscles,
                                      muscles_secondary, increment_kg, dumbbells, bodyweight, equipment,
                                      library_key, note, archived)
                VALUES (:id, :name, :garmin_category, :garmin_name, :kind, :muscles, :muscles_secondary,
                        :increment_kg, :dumbbells, :bodyweight, COALESCE(CAST(:equipment AS text[]), '{}'),
                        :library_key,
                        :note, :archived)"""),
        {**_ex_params(body), "id": eid},
    )
    return _exercise(conn, eid)


@router.put("/exercises/{eid}")
def update_exercise(eid: uuid.UUID, body: ExerciseIn, conn: Connection = CONN) -> dict:
    _exercise(conn, eid)
    _name_taken(conn, body.name, eid)
    conn.execute(
        text("""UPDATE exercise SET name = :name, garmin_category = :garmin_category,
                    garmin_name = :garmin_name, kind = :kind, muscles = :muscles,
                    muscles_secondary = :muscles_secondary,
                    increment_kg = :increment_kg, dumbbells = :dumbbells, bodyweight = :bodyweight,
                    equipment = COALESCE(CAST(:equipment AS text[]), equipment),
                    note = :note, archived = :archived
                WHERE id = :id"""),
        {**_ex_params(body), "id": eid},
    )
    return _exercise(conn, eid)


@router.delete("/exercises/{eid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exercise(eid: uuid.UUID, conn: Connection = CONN) -> Response:
    """Only unused exercises can be deleted; archive the others."""
    used = conn.execute(
        text("SELECT 1 FROM plan_exercise WHERE exercise_id = :id LIMIT 1"), {"id": eid}
    ).first()
    if used:
        raise HTTPException(409, "the exercise is part of a plan; archive it instead")
    conn.execute(text("DELETE FROM exercise WHERE id = :id"), {"id": eid})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/exercises/suggestions")
def exercise_suggestions(conn: Connection = CONN) -> list[dict]:
    """Exercises the watch recorded (category and name) that no own exercise covers yet, with counts."""
    rows = conn.execute(
        text("""SELECT s.exercise_category AS category, s.subtype_candidates[1] AS subtype, count(*) AS sets,
                       max(a.start_ts) AS last
                FROM activity_set s JOIN activity a ON a.id = s.activity_id
                WHERE s.set_type = 'active' AND s.exercise_category IS NOT NULL
                GROUP BY 1, 2 ORDER BY 3 DESC""")
    )
    own = {(e["garmin_category"], e["garmin_name"]) for e in list_exercises(conn)}
    out: dict[tuple, dict] = {}
    for r in rows:
        if r.category in training.NON_STRENGTH:
            continue
        name = fit_exercises.name_for(r.category, [r.subtype])
        key = (r.category, name)
        if key in own or (r.category, None) in own:
            continue
        d = out.setdefault(
            key, {"garmin_category": r.category, "garmin_name": name, "sets": 0, "last": r.last}
        )
        d["sets"] += r.sets
        d["last"] = max(d["last"], r.last)
    return sorted(out.values(), key=lambda d: -d["sets"])


# --- built-in library ----------------------------------------------------------------------------------


@router.get("/library")
def get_library(
    muscle: str | None = Query(
        None, description="Only entries training this primary muscle, or one of this muscle group"
    ),
    equipment: list[str] | None = Query(
        None,
        description="Available equipment (repeat the parameter; once empty: none): only entries needing "
        "nothing else",
    ),
    q: str | None = Query(None, max_length=80, description="Part of the name"),
    conn: Connection = CONN,
) -> list[dict]:
    """Common exercises with names in the profile's language, watch exercise, muscles and equipment;
    `exercise_id` is the own exercise taken over from the entry, if any. Exercises without equipment
    fit any filter."""
    if muscle is not None and muscle not in training.MUSCLES and muscle not in training.MUSCLE_GROUPS:
        raise HTTPException(422, f"unknown muscle {muscle!r}")
    have = None if equipment is None else {x for x in equipment if x}
    for x in have or ():
        if x not in training.EQUIPMENT:
            raise HTTPException(422, f"unknown equipment {x!r}")
    own = dict(conn.execute(text("SELECT library_key, id FROM exercise WHERE library_key IS NOT NULL")).all())
    found = exercise_library.search(muscle, have, q, profile.lang(conn))
    return [{**e, "exercise_id": own.get(e["key"])} for e in found]


@router.post("/library/{key}/adopt")
def adopt_library_entry(key: str, response: Response, conn: Connection = CONN) -> dict:
    """The own exercise for a library entry, created on first use (201). An own exercise with the same
    name or the same watch exercise is linked instead (and unarchived); safe to repeat."""
    e = exercise_library.by_key().get(key)
    if e is None:
        raise HTTPException(404, "library entry not found")
    e = exercise_library.localized(e, profile.lang(conn))
    eid = conn.execute(text("SELECT id FROM exercise WHERE library_key = :k"), {"k": key}).scalar()
    if eid is None:
        eid = conn.execute(
            text("""SELECT id FROM exercise
                    WHERE library_key IS NULL
                      AND (lower(name) = lower(:name)
                           OR (CAST(:gname AS text) IS NOT NULL
                               AND garmin_category = :gcat AND garmin_name = :gname))
                    ORDER BY lower(name) = lower(:name) DESC, archived, created_at
                    LIMIT 1"""),
            {"name": e["name"], "gcat": e["garmin_category"], "gname": e["garmin_name"]},
        ).scalar()
        if eid is not None:
            conn.execute(
                text("""UPDATE exercise SET library_key = :k, archived = false,
                            equipment = CASE WHEN equipment = '{}' THEN :eq ELSE equipment END
                        WHERE id = :id"""),
                {"k": key, "eq": e["equipment"], "id": eid},
            )
    if eid is not None:
        return _exercise(conn, eid)
    body = ExerciseIn(**{k: v for k, v in e.items() if k != "key"}, library_key=key)
    response.status_code = status.HTTP_201_CREATED
    return create_exercise(body, conn)


# --- adjustable dumbbells ---------------------------------------------------------------------------------


class PlateIn(BaseModel):
    kg: float = Field(gt=0, le=50)
    count: int = Field(ge=0, le=40)


class PlatesIn(BaseModel):
    handle_kg: float = Field(
        training.HANDLE_KG, ge=0, le=50, description="One handle with its collars, as the weights are entered"
    )
    plates: list[PlateIn] = Field(
        default_factory=lambda: [PlateIn(kg=k, count=c) for k, c in training.PLATES.items()],
        max_length=12,
        description="The plates for both dumbbells; each side of a dumbbell takes a quarter of a size",
    )


def plates_settings(conn: Connection) -> PlatesIn:
    raw = conn.execute(text("SELECT value FROM app_setting WHERE key = :k"), {"k": PLATES_KEY}).scalar()
    return PlatesIn(**(raw or {}))


def loadable_weights(conn: Connection) -> dict[int, list[float]]:
    """Per `exercise.dumbbells` (1: one dumbbell, 2: the sum of two) the weights the plates allow."""
    s = plates_settings(conn)
    plates: dict[float, int] = {}
    for p in s.plates:
        plates[p.kg] = plates.get(p.kg, 0) + p.count
    return {k: training.plate_weights(s.handle_kg, plates, k) for k in (1, 2)}


@router.get("/plates")
def get_plates(conn: Connection = CONN) -> dict:
    """Handle and plates of the adjustable dumbbells, with the weights one dumbbell can be loaded to
    (both alike). Exercises with `dumbbells` 1 or 2 progress along these weights."""
    s = plates_settings(conn)
    return {**s.model_dump(), "weights": loadable_weights(conn)[1]}


@router.put("/plates")
def put_plates(body: PlatesIn, conn: Connection = CONN) -> dict:
    conn.execute(
        text("""INSERT INTO app_setting (key, value) VALUES (:k, CAST(:v AS jsonb))
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"""),
        {"k": PLATES_KEY, "v": json.dumps(body.model_dump())},
    )
    return get_plates(conn)


# --- plans ----------------------------------------------------------------------------------------------


class PlanExerciseIn(BaseModel):
    id: uuid.UUID | None = None
    exercise_id: uuid.UUID
    sets: int = Field(3, ge=1, le=20)
    reps_min: int | None = Field(None, ge=1, le=100)
    reps_max: int | None = Field(None, ge=1, le=100)
    duration_s: int | None = Field(None, ge=1, le=3600, description="Time exercises (plank): current target")
    duration_min_s: int | None = Field(None, ge=1, le=3600, description="Time exercises: range bottom")
    duration_max_s: int | None = Field(
        None,
        ge=1,
        le=3600,
        description="Time exercises: range top (reached: next weight, back to the bottom)",
    )
    rest_s: int | None = Field(None, ge=0, le=1800)
    target_reps: int | None = Field(None, ge=1, le=100, description="Current target; defaults to reps_min")
    target_weight_kg: float | None = Field(None, ge=0, le=1000)
    note: str | None = Field(None, max_length=300)

    @model_validator(mode="after")
    def _check(self):
        if self.reps_min and self.reps_max and self.reps_min > self.reps_max:
            raise ValueError("reps_min is above reps_max")
        if self.duration_min_s and self.duration_max_s and self.duration_min_s > self.duration_max_s:
            raise ValueError("duration_min_s is above duration_max_s")
        return self


class PlanDayIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=60)
    note: str | None = Field(None, max_length=500)
    exercises: list[PlanExerciseIn] = Field(default_factory=list, max_length=40)


class PlanIn(BaseModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=80)
    note: str | None = Field(None, max_length=1000)
    active: bool = Field(False, description="The plan whose rotation is suggested (one at a time)")
    archived: bool = False
    deload_every_weeks: int | None = Field(
        None,
        ge=2,
        le=16,
        description="Plan a deload week every N weeks (suggested when due); null: only when needed",
    )
    days: list[PlanDayIn] = Field(default_factory=list, max_length=14, description="In rotation order")


def get_plan(conn: Connection, pid) -> dict:
    row = conn.execute(
        text("""SELECT id, name, note, active, archived, created_at, deload_every_weeks, deload_from,
                       deload_until
                FROM training_plan WHERE id = :id"""),
        {"id": pid},
    ).first()
    if row is None:
        raise HTTPException(404, "plan not found")
    plan = dict(row._mapping)
    days = [dict(r._mapping) for r in conn.execute(
        text("SELECT id, name, note, position FROM plan_day WHERE plan_id = :p ORDER BY position"), {"p": pid}
    )]  # fmt: skip
    exs = conn.execute(
        text("""SELECT pe.*, e.name AS exercise, e.kind, e.garmin_category, e.increment_kg, e.bodyweight
                FROM plan_exercise pe JOIN exercise e ON e.id = pe.exercise_id
                JOIN plan_day d ON d.id = pe.plan_day_id WHERE d.plan_id = :p ORDER BY pe.position"""),
        {"p": pid},
    )
    by_day: dict = {}
    for e in exs:
        by_day.setdefault(e.plan_day_id, []).append(dict(e._mapping))
    for d in days:
        d["exercises"] = by_day.get(d["id"], [])
    plan["days"] = days
    return plan


def active_plan(conn: Connection) -> dict | None:
    pid = conn.execute(text("SELECT id FROM training_plan WHERE active")).scalar()
    return get_plan(conn, pid) if pid else None


def _write_plan(conn: Connection, pid, body: PlanIn) -> None:
    if body.active:
        conn.execute(text("UPDATE training_plan SET active = false WHERE active AND id <> :id"), {"id": pid})
    known = {r.id for r in conn.execute(text("SELECT id FROM exercise"))}
    day_ids, ex_ids = [], []
    for pos, d in enumerate(body.days):
        did = d.id or uuid.uuid4()
        day_ids.append(did)
        conn.execute(
            text("""INSERT INTO plan_day (id, plan_id, name, position, note) VALUES (:id, :p, :n, :pos, :note)
                    ON CONFLICT (id) DO UPDATE SET plan_id = EXCLUDED.plan_id, name = EXCLUDED.name,
                        position = EXCLUDED.position, note = EXCLUDED.note"""),
            {"id": did, "p": pid, "n": d.name.strip(), "pos": pos, "note": d.note},
        )
        for epos, e in enumerate(d.exercises):
            if e.exercise_id not in known:
                raise HTTPException(422, f"unknown exercise {e.exercise_id}")
            eid = e.id or uuid.uuid4()
            ex_ids.append(eid)
            conn.execute(
                text("""INSERT INTO plan_exercise (id, plan_day_id, exercise_id, position, sets, reps_min,
                            reps_max, duration_s, duration_min_s, duration_max_s, rest_s, target_reps,
                            target_weight_kg, note)
                        VALUES (:id, :d, :e, :pos, :sets, :rmin, :rmax, :dur, :dmin, :dmax, :rest, :tr, :tw,
                                :note)
                        ON CONFLICT (id) DO UPDATE SET plan_day_id = EXCLUDED.plan_day_id,
                            exercise_id = EXCLUDED.exercise_id, position = EXCLUDED.position,
                            sets = EXCLUDED.sets, reps_min = EXCLUDED.reps_min, reps_max = EXCLUDED.reps_max,
                            duration_s = EXCLUDED.duration_s, duration_min_s = EXCLUDED.duration_min_s,
                            duration_max_s = EXCLUDED.duration_max_s, rest_s = EXCLUDED.rest_s,
                            target_reps = EXCLUDED.target_reps, target_weight_kg = EXCLUDED.target_weight_kg,
                            note = EXCLUDED.note"""),
                {"id": eid, "d": did, "e": e.exercise_id, "pos": epos, "sets": e.sets, "rmin": e.reps_min,
                 "rmax": e.reps_max, "dur": e.duration_s, "dmin": e.duration_min_s,
                 "dmax": e.duration_max_s, "rest": e.rest_s, "tr": e.target_reps,
                 "tw": e.target_weight_kg, "note": e.note},
            )  # fmt: skip
    conn.execute(
        text("""DELETE FROM plan_exercise pe USING plan_day d
                WHERE d.id = pe.plan_day_id AND d.plan_id = :p AND NOT (pe.id = ANY(:keep))"""),
        {"p": pid, "keep": ex_ids},
    )
    conn.execute(
        text("DELETE FROM plan_day WHERE plan_id = :p AND NOT (id = ANY(:keep))"), {"p": pid, "keep": day_ids}
    )


@router.get("/plans")
def get_plans(conn: Connection = CONN) -> list[dict]:
    """All plans with their days (rotation order) and prescribed exercises; active first, archived last."""
    ids = conn.execute(
        text("SELECT id FROM training_plan ORDER BY active DESC, archived, created_at")
    ).scalars()
    return [get_plan(conn, pid) for pid in ids]


@router.post("/plans", status_code=status.HTTP_201_CREATED)
def create_plan(body: PlanIn, conn: Connection = CONN) -> dict:
    """A plan with its days and exercises in one body. Safe to repeat with `id`."""
    pid = body.id or uuid.uuid4()
    if conn.execute(text("SELECT 1 FROM training_plan WHERE id = :id"), {"id": pid}).first():
        return get_plan(conn, pid)
    if body.active and body.archived:
        raise HTTPException(422, "an archived plan cannot be active")
    conn.execute(
        text(
            """INSERT INTO training_plan (id, name, note, active, archived, deload_every_weeks)
               VALUES (:id, :n, :note, false, :ar, :dw)"""
        ),
        {
            "id": pid,
            "n": body.name.strip(),
            "note": body.note,
            "ar": body.archived,
            "dw": body.deload_every_weeks,
        },
    )
    _write_plan(conn, pid, body)
    conn.execute(text("UPDATE training_plan SET active = :a WHERE id = :id"), {"a": body.active, "id": pid})
    return get_plan(conn, pid)


@router.put("/plans/{pid}")
def update_plan(pid: uuid.UUID, body: PlanIn, conn: Connection = CONN) -> dict:
    """Full state: days and exercises not listed are removed; listed ids are kept (sessions stay linked)."""
    get_plan(conn, pid)
    if body.active and body.archived:
        raise HTTPException(422, "an archived plan cannot be active")
    conn.execute(
        text(
            """UPDATE training_plan SET name = :n, note = :note, active = false, archived = :ar,
                   deload_every_weeks = :dw WHERE id = :id"""
        ),
        {
            "id": pid,
            "n": body.name.strip(),
            "note": body.note,
            "ar": body.archived,
            "dw": body.deload_every_weeks,
        },
    )
    _write_plan(conn, pid, body)
    conn.execute(text("UPDATE training_plan SET active = :a WHERE id = :id"), {"a": body.active, "id": pid})
    return get_plan(conn, pid)


@router.delete("/plans/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan(pid: uuid.UUID, conn: Connection = CONN) -> Response:
    """Deletes the plan; sessions keep their sets but lose the link to its days. Archive to keep it."""
    conn.execute(text("DELETE FROM training_plan WHERE id = :id"), {"id": pid})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class TargetIn(BaseModel):
    target_sets: int | None = Field(None, ge=1, le=20, description="Omitted: the entry's sets")
    target_reps: int | None = Field(None, ge=1, le=100)
    target_weight_kg: float | None = Field(None, ge=0, le=1000)
    duration_s: int | None = Field(None, ge=1, le=3600)


@router.put("/plan-exercises/{peid}/target")
def put_target(peid: uuid.UUID, body: TargetIn, conn: Connection = CONN) -> dict:
    """Take over a "next time" suggestion (or set the target by hand)."""
    row = conn.execute(
        text("""UPDATE plan_exercise SET target_reps = :r, target_weight_kg = :w, declined_suggestion = NULL,
                    target_sets = CASE WHEN :s = sets THEN NULL ELSE :s END,
                    duration_s = COALESCE(:d, duration_s) WHERE id = :id RETURNING id"""),
        {
            "id": peid,
            "s": body.target_sets,
            "r": body.target_reps,
            "w": body.target_weight_kg,
            "d": body.duration_s,
        },
    ).first()
    if row is None:
        raise HTTPException(404, "plan exercise not found")
    return {"id": peid, **body.model_dump()}


class DeclineIn(BaseModel):
    suggestion: str | None = Field(
        max_length=80, description='The suggestion\'s text ("3×10 @ 25 kg"); null offers it again'
    )


@router.put("/plan-exercises/{peid}/declined")
def put_declined(peid: uuid.UUID, body: DeclineIn, conn: Connection = CONN) -> dict:
    """Decline a "next time" suggestion: the target stays, and this suggestion is not offered again
    (the next workout uses the current target). A different suggestion is offered as usual."""
    row = conn.execute(
        text("UPDATE plan_exercise SET declined_suggestion = :s WHERE id = :id RETURNING id"),
        {"id": peid, "s": body.suggestion},
    ).first()
    if row is None:
        raise HTTPException(404, "plan exercise not found")
    return {"id": peid, "declined_suggestion": body.suggestion}


# --- sets & sessions ------------------------------------------------------------------------------------


def _exercise_map(conn: Connection) -> dict:
    return {e["id"]: e for e in list_exercises(conn)}


def effective_sets(conn: Connection, aid: int, exmap: dict) -> list[dict]:
    """Work sets of a session in order: watch sets with the user's layer, plus sets added by hand."""
    out = []
    for r in conn.execute(
        text("""SELECT s.set_index, s.start_ts, s.duration_s, s.repetitions, s.weight_kg,
                       s.exercise_category, s.subtype_candidates, e.exercise_category AS edit_category,
                       e.exercise_name AS edit_name, e.repetitions AS edit_reps, e.weight_kg AS edit_weight,
                       e.duration_s AS edit_duration, e.exercise_id, e.plan_exercise_id,
                       COALESCE(e.hidden, false) AS hidden,
                       COALESCE(e.warmup, false) AS warmup,
                       e.updated_at IS NOT NULL AS edited
                FROM activity_set s
                LEFT JOIN activity_set_edit e ON e.activity_id = s.activity_id AND e.set_index = s.set_index
                WHERE s.activity_id = :a AND s.set_type = 'active' ORDER BY s.set_index"""),
        {"a": aid},
    ):
        cat = r.edit_category if r.edit_category is not None else r.exercise_category
        watch_name = fit_exercises.name_for(r.exercise_category, r.subtype_candidates)
        name = r.edit_name if r.edit_category is not None else watch_name
        ex = exmap.get(r.exercise_id)
        out.append({
            "key": f"w:{r.set_index}",
            "source": "watch",
            "position": float(r.set_index),
            "start_ts": r.start_ts,
            "exercise_id": r.exercise_id,
            "plan_exercise_id": r.plan_exercise_id,
            "exercise": ex["name"] if ex else None,
            "garmin_category": ex["garmin_category"] if ex and ex["garmin_category"] else cat,
            "garmin_name": ex["garmin_name"] if ex and ex["garmin_category"] else name,
            "repetitions": r.edit_reps if r.edit_reps is not None else r.repetitions,
            "weight_kg": r.edit_weight if r.edit_weight is not None else r.weight_kg,
            "duration_s": r.edit_duration if r.edit_duration is not None else r.duration_s,
            "warmup": r.warmup,
            "hidden": r.hidden,
            "edited": r.edited,
            "original": {"repetitions": r.repetitions, "weight_kg": r.weight_kg, "duration_s": r.duration_s,
                         "garmin_category": r.exercise_category},
        })  # fmt: skip
    for r in conn.execute(
        text("""SELECT id, position, exercise_id, plan_exercise_id, repetitions, weight_kg, duration_s, warmup
                FROM training_set WHERE activity_id = :a"""),
        {"a": aid},
    ):
        ex = exmap.get(r.exercise_id)
        out.append({
            "key": f"m:{r.id}", "source": "manual", "position": r.position, "start_ts": None,
            "exercise_id": r.exercise_id, "plan_exercise_id": r.plan_exercise_id,
            "exercise": ex["name"] if ex else None, "garmin_category": ex["garmin_category"] if ex else None,
            "garmin_name": ex["garmin_name"] if ex else None,
            "repetitions": r.repetitions, "weight_kg": r.weight_kg, "duration_s": r.duration_s,
            "warmup": r.warmup, "hidden": False, "edited": True, "original": None,
        })  # fmt: skip
    out.sort(key=lambda s: s["position"])
    return out


def _activity(conn: Connection, aid: int) -> dict:
    row = conn.execute(
        text("""SELECT a.id, a.start_ts, a.end_ts, coalesce(a.sport_profile_name, a.name) AS name, a.sport,
                       a.sub_sport, a.elapsed_s, a.timer_s, a.avg_hr, a.max_hr, a.energy_kcal, a.manual,
                       t.title, t.note, t.plan_day_id, t.reviewed_at, COALESCE(t.deload, false) AS deload,
                       d.name AS plan_day, p.name AS plan, p.id AS plan_id
                FROM activity a LEFT JOIN training_session t ON t.activity_id = a.id
                LEFT JOIN plan_day d ON d.id = t.plan_day_id LEFT JOIN training_plan p ON p.id = d.plan_id
                WHERE a.id = :id"""),
        {"id": aid},
    ).first()
    if row is None:
        raise HTTPException(404, "activity not found")
    return dict(row._mapping)


def _last_done_day(conn: Connection, before) -> uuid.UUID | None:
    """Plan day of the newest reviewed session or skip before `before`: the rotation goes on from it."""
    return conn.execute(
        text("""SELECT day FROM (
                    SELECT t.plan_day_id AS day, a.start_ts AS ts
                    FROM training_session t JOIN activity a ON a.id = t.activity_id
                    WHERE t.reviewed_at IS NOT NULL AND t.plan_day_id IS NOT NULL AND a.start_ts < :b
                    UNION ALL
                    SELECT plan_day_id, skipped_at FROM plan_day_skip WHERE skipped_at < :b
                ) x ORDER BY ts DESC LIMIT 1"""),
        {"b": before},
    ).scalar()


def _day_of(plan: dict | None, day_id) -> dict | None:
    return next((d for d in (plan or {}).get("days", []) if d["id"] == day_id), None)


LEARN_MIN_SESSIONS = 2


def learned_categories(conn: Connection) -> dict:
    """Per exercise the watch categories its confirmed sets were recorded as, in at least two
    reviewed sessions (the watch calls incline curls "curl" and reverse flys "lateral_raise")."""
    out: dict = {}
    for r in conn.execute(
        text("""SELECT e.exercise_id, s.exercise_category
                FROM activity_set s
                JOIN activity_set_edit e ON e.activity_id = s.activity_id AND e.set_index = s.set_index
                JOIN training_session t ON t.activity_id = s.activity_id
                WHERE t.reviewed_at IS NOT NULL AND e.exercise_id IS NOT NULL AND s.set_type = 'active'
                  AND s.exercise_category IS NOT NULL
                  AND NOT COALESCE(e.warmup, false) AND NOT COALESCE(e.hidden, false)
                GROUP BY 1, 2 HAVING count(DISTINCT s.activity_id) >= :n"""),
        {"n": LEARN_MIN_SESSIONS},
    ):
        if r.exercise_category not in training.NON_STRENGTH:
            out.setdefault(r.exercise_id, set()).add(r.exercise_category)
    return out


def _with_categories(days: list[dict], learned: dict) -> list[dict]:
    """Plan days whose entries carry `garmin_categories`: the exercise's own plus the learned ones."""
    return [
        {**d, "exercises": [
            {**pe, "garmin_categories": training.entry_categories(pe) | learned.get(pe["exercise_id"], set())}
            for pe in d["exercises"]
        ]}
        for d in days
    ]  # fmt: skip


def proposal(conn: Connection, act: dict, sets: list[dict], exmap: dict, day_id=None) -> dict:
    """Plan day and per watch set exercise, warm-up and hidden as VitaLocal would fill them in
    (for `day_id` when given: the day the user picked in the review)."""
    plan = active_plan(conn)
    learned = learned_categories(conn)
    day = None
    if day_id or act["plan_day_id"]:
        day_id, reason = day_id or act["plan_day_id"], "chosen"
        pid = conn.execute(text("SELECT plan_id FROM plan_day WHERE id = :d"), {"d": day_id}).scalar()
        day = _day_of(plan, day_id) or (_day_of(get_plan(conn, pid), day_id) if pid else None)
    else:
        cats = [s["garmin_category"] for s in sets]
        last = _last_done_day(conn, act["start_ts"])
        days = _with_categories(plan["days"], learned) if plan else []
        day_id, reason = training.suggest_day(days, last, cats)
        day = _day_of(plan, day_id)
    flags = [training.suggest_flags(s, i) for i, s in enumerate(sets)]
    work = [i for i, s in enumerate(sets) if not flags[i]["warmup"] and not flags[i]["hidden"]]
    pes = _with_categories([day], learned)[0]["exercises"] if day else []
    assigned = training.assign_sets([sets[i] for i in work], pes)
    pe_ex = {pe["id"]: pe["exercise_id"] for pe in pes}
    # without a plan day (or beyond it): the own exercise that covers the watch's category/name
    by_garmin: dict = {}
    for e in exmap.values():
        if e["garmin_category"] and not e["archived"]:
            by_garmin.setdefault((e["garmin_category"], e["garmin_name"]), e["id"])
            by_garmin.setdefault((e["garmin_category"], None), e["id"])
    plan_cats = set().union(*(pe["garmin_categories"] for pe in pes)) if pes else set()
    per_set = []
    prev_w = prev_own = None
    for i, s in enumerate(sets):
        ex_id = pe = None
        if i in work:
            pe = assigned[work.index(i)]
            ex_id = pe_ex.get(pe) if pe else None
            # an exercise the plan day does not have, at a new weight: the own exercise for it
            # (and the following sets of it at that weight)
            own = by_garmin.get((s["garmin_category"], s["garmin_name"])) or by_garmin.get(
                (s["garmin_category"], None)
            )
            if (
                ex_id
                and own
                and s["garmin_category"] not in plan_cats
                and (s["weight_kg"] != prev_w or own == prev_own)
            ):
                ex_id = own
            prev_w, prev_own = s["weight_kg"], ex_id if ex_id == own else None
            if ex_id is None:
                ex_id = by_garmin.get((s["garmin_category"], s["garmin_name"])) or by_garmin.get(
                    (s["garmin_category"], None)
                )
        slot = pe if pe and ex_id == pe_ex.get(pe) else None
        per_set.append({"key": s["key"], "exercise_id": ex_id, "plan_exercise_id": slot, **flags[i]})
    return {
        "plan_day_id": day_id,
        "plan_day": day["name"] if day else None,
        "reason": reason,
        "sets": per_set,
    }


def performances(
    conn: Connection, exercise_id, before=None, limit: int = 6, plan_exercise_id=None
) -> list[dict]:
    """Reviewed sessions with this exercise, newest first: date, rating and the work sets.

    With a plan entry, a session's sets of that entry count (the same exercise can be on a day twice);
    sessions without entry assignments fall back to all sets of the exercise.
    """
    rows = conn.execute(
        text(f"""SELECT a.id, a.start_ts FROM activity a JOIN training_session t ON t.activity_id = a.id
                 WHERE t.reviewed_at IS NOT NULL AND NOT t.deload
                   AND (CAST(:b AS timestamptz) IS NULL OR a.start_ts < :b)
                   AND (EXISTS (SELECT 1 FROM activity_set_edit e
                                WHERE e.activity_id = a.id AND e.exercise_id = :x)
                        OR EXISTS (SELECT 1 FROM training_set s
                                   WHERE s.activity_id = a.id AND s.exercise_id = :x))
                 ORDER BY a.start_ts DESC LIMIT {int(limit)}"""),
        {"x": exercise_id, "b": before},
    ).all()
    exmap = _exercise_map(conn)
    out = []
    for r in rows:
        all_sets = [s for s in effective_sets(conn, r.id, exmap)
                    if s["exercise_id"] == exercise_id and not s["hidden"] and not s["warmup"]]  # fmt: skip
        slot = [s for s in all_sets if plan_exercise_id and s["plan_exercise_id"] == plan_exercise_id]
        if plan_exercise_id and not slot and any(s["plan_exercise_id"] for s in all_sets):
            continue  # this session did the exercise only for another entry
        sets = slot or all_sets
        ratings = {x.plan_exercise_id: x.rating for x in conn.execute(
            text("""SELECT plan_exercise_id, rating FROM exercise_rating
                    WHERE activity_id = :a AND exercise_id = :x"""),
            {"a": r.id, "x": exercise_id})}  # fmt: skip
        rating = ratings.get(plan_exercise_id) if slot else None
        rating = rating or ratings.get(None) or (next(iter(ratings.values())) if len(ratings) == 1 else None)
        out.append({
            "activity_id": r.id,
            "date": r.start_ts.astimezone(_tz()).date(),
            "rating": rating,
            "sets": [{"reps": s["repetitions"], "weight_kg": s["weight_kg"], "duration_s": s["duration_s"]}
                     for s in sets],
        })  # fmt: skip
    return out


def _target_dict(t: training.Target) -> dict:
    d = t.__dict__.copy()
    d["text"] = target_text(t.sets, t.reps, t.weight_kg, t.duration_s)
    return d


def target_text(sets: int, reps: int | None, weight: float | None, duration_s: int | None) -> str:
    """ "3×10 @ 25 kg"; time exercises "3×45 s" (with a load "3×45 s @ 30 kg")."""
    w = f" @ {weight:g} kg" if weight else ""
    if duration_s and not reps:
        return f"{sets}×{int(duration_s)} s{w}"
    return f"{sets}×{reps if reps is not None else '?'}{w}"


def _today() -> date:
    return datetime.now(_tz()).date()


def day_targets(conn: Connection, day: dict, before=None) -> list[dict]:
    """Per plan exercise of a day: the stored target and the next suggestion from the progression
    (as of `before`, a session's start, else today)."""
    exmap = _exercise_map(conn)
    today = before.astimezone(_tz()).date() if before else _today()
    loaded = any(exmap[pe["exercise_id"]]["dumbbells"] for pe in day["exercises"])
    deload = deload_window(conn, plan_day_id=day["id"])
    weights = loadable_weights(conn) if loaded else {}
    out = []
    for pe in day["exercises"]:
        ex = exmap[pe["exercise_id"]]
        hist = performances(conn, pe["exercise_id"], before, limit=12, plan_exercise_id=pe["id"])
        nxt = training.next_target(pe, ex, hist, today, weights.get(ex["dumbbells"]))
        regular = None
        if deload and deload[0] <= today <= deload[1]:
            regular = _target_dict(nxt)["text"]
            last = [s["weight_kg"] for s in hist[0]["sets"] if s["weight_kg"] is not None] if hist else []
            nxt = training.deload_target(nxt, pe, ex, min(last) if last else None)
        timed = ex["kind"] == "time"
        cur_sets = pe.get("target_sets") or pe["sets"]
        cur_reps = None if timed else pe["target_reps"] or pe["reps_min"]
        cur_dur = (pe["duration_s"] or 30) if timed else None
        current = target_text(cur_sets, cur_reps, pe["target_weight_kg"], cur_dur)
        suggestion = _target_dict(nxt)
        # declined: the current target stays what to do next time, until the progression suggests
        # something else
        declined = (
            regular is None
            and suggestion["text"] != current
            and suggestion["text"] == pe.get("declined_suggestion")
        )
        if declined:
            suggestion = {
                **suggestion,
                "sets": cur_sets,
                "reps": cur_reps,
                "weight_kg": pe["target_weight_kg"],
                "duration_s": cur_dur,
                "text": current,
                "reason": "declined",
                "notes": [],
            }
        out.append({
            "plan_exercise_id": pe["id"],
            "exercise_id": pe["exercise_id"],
            "exercise": ex["name"],
            "kind": ex["kind"],
            "sets": pe["sets"],
            "reps_min": pe["reps_min"],
            "reps_max": pe["reps_max"],
            "duration_s": pe["duration_s"],
            "duration_min_s": pe["duration_min_s"],
            "duration_max_s": pe["duration_max_s"],
            "rest_s": pe["rest_s"],
            "current": current,
            "target_reps": pe["target_reps"],
            "target_weight_kg": pe["target_weight_kg"],
            "next": suggestion,
            "deload": regular is not None,
            "regular": regular,
            # in a deload week the suggestion is not a new target to take over
            "differs": regular is None and suggestion["text"] != current,
            "declined": pe.get("declined_suggestion") if declined else None,
            "last": {"date": hist[0]["date"], "rating": hist[0]["rating"],
                     "sets": [target_text(1, None if timed else s["reps"], s["weight_kg"],
                                          s["duration_s"] if timed else None)[2:]
                              for s in hist[0]["sets"]]}
            if hist else None,
        })  # fmt: skip
    return out


def _set_text(s: dict, ex: dict) -> str:
    """ "10 @ 25 kg", "45 s" for time exercises."""
    dur = int(s["duration_s"]) if ex.get("kind") == "time" and s["duration_s"] else None
    return target_text(1, s["repetitions"], s["weight_kg"], dur)[2:]


def exercise_summary(sets: list[dict], exmap: dict, ratings: dict) -> list[dict]:
    """Per exercise (or plan entry) of a session: work sets as text, volume, best estimated 1RM, rating.

    ratings: {(exercise_id, plan_exercise_id or None): rating}
    """
    order: list = []
    by: dict = {}
    for s in sets:
        if s["hidden"] or s["warmup"] or not s["exercise_id"]:
            continue
        k = (s["exercise_id"], s.get("plan_exercise_id"))
        if k not in by:
            order.append(k)
            by[k] = []
        by[k].append(s)
    out = []
    for eid, peid in order:
        ss = by[(eid, peid)]
        ex = exmap.get(eid, {})
        e1 = [x for x in (training.e1rm(s["weight_kg"], s["repetitions"]) for s in ss) if x]
        out.append({
            "exercise_id": eid,
            "plan_exercise_id": peid,
            "exercise": ex.get("name"),
            "sets": len(ss),
            "text": ", ".join(_set_text(s, ex) for s in ss),
            "volume_kg": round(sum((s["repetitions"] or 0) * (s["weight_kg"] or 0) for s in ss), 1),
            "best_e1rm_kg": max(e1) if e1 else None,
            "rating": ratings.get((eid, peid)),
        })  # fmt: skip
    return out


def session_detail(conn: Connection, aid: int, day_id=None) -> dict:
    act = _activity(conn, aid)
    exmap = _exercise_map(conn)
    sets = effective_sets(conn, aid, exmap)
    rows = conn.execute(
        text("SELECT exercise_id, plan_exercise_id, rating FROM exercise_rating WHERE activity_id = :a"),
        {"a": aid},
    )
    ratings = {(r.exercise_id, r.plan_exercise_id): r.rating for r in rows}
    reviewed = act["reviewed_at"] is not None
    out = {
        "activity_id": aid,
        "activity": {
            k: act[k]
            for k in (
                "start_ts",
                "end_ts",
                "name",
                "sport",
                "sub_sport",
                "elapsed_s",
                "timer_s",
                "avg_hr",
                "max_hr",
                "energy_kcal",
                "manual",
            )
        },  # fmt: skip
        "title": act["title"],
        "display_title": act["title"] or act["plan_day"] or act["name"] or "Training",
        "note": act["note"],
        "plan_day_id": act["plan_day_id"],
        "plan_day": act["plan_day"],
        "plan": act["plan"],
        "reviewed": reviewed,
        "deload": act["deload"],
        "reviewed_at": act["reviewed_at"],
        "sets": sets,
        "ratings": [{"exercise_id": e, "plan_exercise_id": p, "rating": r} for (e, p), r in ratings.items()],
        "exercises": exercise_summary(sets, exmap, ratings),
    }
    # also for a reviewed workout whose sets have no exercise yet (e.g. reviewed before any existed);
    # then only the exercises of those sets are meant to be filled in
    unassigned = [x for x in sets if not x["exercise_id"] and not x["hidden"] and not x["warmup"]]
    out["unassigned_sets"] = len(unassigned)
    out["overlaps"] = overlapping(conn, aid)
    if not reviewed or unassigned or day_id:
        out["proposal"] = proposal(conn, act, sets, exmap, day_id)
        out["proposal"]["only_unassigned"] = reviewed and not day_id
    day_id = day_id or act["plan_day_id"] or (out.get("proposal") or {}).get("plan_day_id")
    if day_id:
        pid = conn.execute(text("SELECT plan_id FROM plan_day WHERE id = :d"), {"d": day_id}).scalar()
        day = _day_of(get_plan(conn, pid), day_id) if pid else None
        # what was planned for this session (targets as of before it) and what follows from it
        out["planned"] = day_targets(conn, day, before=act["start_ts"]) if day else []
        _add_suggestions(out, exmap)
    return out


def _add_suggestions(out: dict, exmap: dict) -> None:
    """Per exercise of the session a proposed rating from its work sets against the planned target."""
    planned = {p["plan_exercise_id"]: p for p in out["planned"]}
    for e in out["exercises"]:
        p = planned.get(e["plan_exercise_id"])
        if p is None:
            continue
        work = [
            s
            for s in out["sets"]
            if s["exercise_id"] == e["exercise_id"]
            and s.get("plan_exercise_id") == e["plan_exercise_id"]
            and not s["hidden"]
            and not s["warmup"]
        ]
        e["planned"] = p["next"]["text"]
        e["suggestion"] = training.suggest_rating(
            p["next"], work, exmap.get(e["exercise_id"], {}).get("kind")
        )


def list_sessions(conn: Connection, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    rows = conn.execute(
        text(f"""SELECT a.id, a.start_ts, a.elapsed_s, a.manual,
                        coalesce(a.sport_profile_name, a.name) AS name, t.title, t.reviewed_at,
                        COALESCE(t.deload, false) AS deload,
                        d.name AS plan_day,
                        (SELECT count(*) FROM activity_set s
                         WHERE s.activity_id = a.id AND s.set_type = 'active')
                          + (SELECT count(*) FROM training_set m WHERE m.activity_id = a.id) AS sets,
                        (SELECT count(*) FROM activity_set s LEFT JOIN activity_set_edit e
                           ON e.activity_id = s.activity_id AND e.set_index = s.set_index
                         WHERE s.activity_id = a.id AND s.set_type = 'active' AND e.exercise_id IS NULL
                           AND NOT COALESCE(e.hidden, false) AND NOT COALESCE(e.warmup, false))
                          + (SELECT count(*) FROM training_set m
                             WHERE m.activity_id = a.id AND m.exercise_id IS NULL AND NOT m.warmup)
                          AS unassigned
                 FROM activity a LEFT JOIN training_session t ON t.activity_id = a.id
                 LEFT JOIN plan_day d ON d.id = t.plan_day_id
                 WHERE {_IS_SESSION}
                   AND (CAST(:s AS timestamptz) IS NULL OR a.start_ts >= :s)
                   AND (CAST(:e AS timestamptz) IS NULL OR a.start_ts < :e)
                 ORDER BY a.start_ts DESC"""),
        {"s": start, "e": end},
    )
    return [{**r._mapping, "display_title": r.title or r.plan_day or r.name or "Training",
             "reviewed": r.reviewed_at is not None} for r in rows]  # fmt: skip


class SetIn(BaseModel):
    key: str | None = Field(
        None, description='"w:<set_index>" for a watch set, "m:<uuid>" for an added one, none = new'
    )
    position: float | None = Field(
        None, description="Ordering of added sets among watch sets (set index + fraction)"
    )
    exercise_id: uuid.UUID | None = None
    plan_exercise_id: uuid.UUID | None = Field(
        None, description="Entry of the plan day (needed when the day has the exercise more than once)"
    )
    repetitions: int | None = Field(None, ge=0, le=1000)
    weight_kg: float | None = Field(None, ge=0, le=1000)
    duration_s: float | None = Field(None, ge=0, le=36000)
    warmup: bool = False
    hidden: bool = Field(False, description="Watch sets only: ignore (accidental or duplicate set)")


class RatingIn(BaseModel):
    exercise_id: uuid.UUID
    plan_exercise_id: uuid.UUID | None = None
    rating: Rating


class SessionIn(BaseModel):
    title: str | None = Field(None, max_length=80)
    note: str | None = Field(None, max_length=4000)
    plan_day_id: uuid.UUID | None = None
    reviewed: bool = Field(
        True, description="Confirm sets and plan day; only reviewed sessions feed the progression"
    )
    deload: bool | None = Field(
        None,
        description="A deload workout (left out of the progression); omitted: whether it lies in the "
        "plan's deload week",
    )
    sets: list[SetIn] = Field(
        default_factory=list, max_length=300,
        description="Watch sets listed are updated (others keep their state); added sets are the full list",
    )  # fmt: skip
    ratings: list[RatingIn] = Field(
        default_factory=list, max_length=100, description="Per exercise (or plan entry); full state"
    )


def write_session(conn: Connection, aid: int, body: SessionIn) -> None:
    exmap = _exercise_map(conn)
    if (
        body.plan_day_id
        and not conn.execute(text("SELECT 1 FROM plan_day WHERE id = :d"), {"d": body.plan_day_id}).first()
    ):
        raise HTTPException(422, "unknown plan day")
    watch = {r.set_index: r for r in conn.execute(
        text("""SELECT set_index, repetitions, weight_kg, duration_s FROM activity_set
                WHERE activity_id = :a AND set_type = 'active'"""), {"a": aid})}  # fmt: skip
    deload = body.deload
    if deload is None:
        start = conn.execute(text("SELECT start_ts FROM activity WHERE id = :a"), {"a": aid}).scalar()
        deload = in_deload(conn, start.astimezone(_tz()).date(), body.plan_day_id) if start else False
    conn.execute(
        text("""INSERT INTO training_session (activity_id, title, note, plan_day_id, reviewed_at, deload)
                VALUES (:a, :t, :n, :d, CASE WHEN :r THEN now() END, :dl)
                ON CONFLICT (activity_id) DO UPDATE SET title = EXCLUDED.title, note = EXCLUDED.note,
                    plan_day_id = EXCLUDED.plan_day_id, deload = EXCLUDED.deload,
                    reviewed_at = CASE WHEN :r THEN COALESCE(training_session.reviewed_at, now()) END,
                    updated_at = now()"""),
        {"a": aid, "t": (body.title or "").strip() or None, "n": (body.note or "").strip() or None,
         "d": body.plan_day_id, "r": body.reviewed, "dl": deload},
    )  # fmt: skip
    entries = {r.id for r in conn.execute(text("SELECT id FROM plan_exercise"))}
    keep_manual = []
    for s in body.sets:
        if s.exercise_id is not None and s.exercise_id not in exmap:
            raise HTTPException(422, f"unknown exercise {s.exercise_id}")
        if s.plan_exercise_id is not None and s.plan_exercise_id not in entries:
            raise HTTPException(422, f"unknown plan entry {s.plan_exercise_id}")
        if s.key and s.key.startswith("w:"):
            idx = int(s.key[2:])
            w = watch.get(idx)
            if w is None:
                raise HTTPException(422, f"no watch set {s.key}")

            def diff(new, old):
                return new if new is not None and new != old else None

            conn.execute(
                text("""INSERT INTO activity_set_edit (activity_id, set_index, exercise_id, plan_exercise_id,
                            repetitions, weight_kg, duration_s, hidden, warmup, updated_at)
                        VALUES (:a, :i, :x, :pe, :r, :w, :d, :h, :wu, now())
                        ON CONFLICT (activity_id, set_index) DO UPDATE SET exercise_id = EXCLUDED.exercise_id,
                            plan_exercise_id = EXCLUDED.plan_exercise_id,
                            repetitions = EXCLUDED.repetitions, weight_kg = EXCLUDED.weight_kg,
                            duration_s = EXCLUDED.duration_s, hidden = EXCLUDED.hidden,
                            warmup = EXCLUDED.warmup, updated_at = now()"""),
                {"a": aid, "i": idx, "x": s.exercise_id, "pe": s.plan_exercise_id,
                 "r": diff(s.repetitions, w.repetitions),
                 "w": diff(s.weight_kg, w.weight_kg), "d": diff(s.duration_s, w.duration_s),
                 "h": s.hidden, "wu": s.warmup},
            )  # fmt: skip
        else:
            sid = uuid.UUID(s.key[2:]) if s.key and s.key.startswith("m:") else uuid.uuid4()
            keep_manual.append(sid)
            pos = s.position if s.position is not None else 10_000 + len(keep_manual)
            conn.execute(
                text("""INSERT INTO training_set (id, activity_id, position, exercise_id, plan_exercise_id,
                            repetitions, weight_kg, duration_s, warmup)
                        VALUES (:id, :a, :p, :x, :pe, :r, :w, :d, :wu)
                        ON CONFLICT (id) DO UPDATE SET position = EXCLUDED.position,
                            exercise_id = EXCLUDED.exercise_id, plan_exercise_id = EXCLUDED.plan_exercise_id,
                            repetitions = EXCLUDED.repetitions,
                            weight_kg = EXCLUDED.weight_kg, duration_s = EXCLUDED.duration_s,
                            warmup = EXCLUDED.warmup"""),
                {"id": sid, "a": aid, "p": pos, "x": s.exercise_id, "pe": s.plan_exercise_id,
                 "r": s.repetitions, "w": s.weight_kg,
                 "d": s.duration_s, "wu": s.warmup},
            )  # fmt: skip
    conn.execute(
        text("DELETE FROM training_set WHERE activity_id = :a AND NOT (id = ANY(:keep))"),
        {"a": aid, "keep": keep_manual},
    )
    write_ratings(conn, aid, body.ratings, exmap, entries)


def write_ratings(conn: Connection, aid: int, ratings: list[RatingIn], exmap: dict, entries: set) -> None:
    conn.execute(text("DELETE FROM exercise_rating WHERE activity_id = :a"), {"a": aid})
    seen = set()
    for r in ratings:
        if r.exercise_id not in exmap:
            raise HTTPException(422, f"unknown exercise {r.exercise_id}")
        if r.plan_exercise_id is not None and r.plan_exercise_id not in entries:
            raise HTTPException(422, f"unknown plan entry {r.plan_exercise_id}")
        if (r.exercise_id, r.plan_exercise_id) in seen:
            continue
        seen.add((r.exercise_id, r.plan_exercise_id))
        conn.execute(
            text("""INSERT INTO exercise_rating (activity_id, exercise_id, plan_exercise_id, rating)
                    VALUES (:a, :x, :pe, :r)"""),
            {"a": aid, "x": r.exercise_id, "pe": r.plan_exercise_id, "r": r.rating},
        )


class ManualIn(SessionIn):
    manual_key: uuid.UUID = Field(description="Client-chosen id: repeating the POST changes nothing")
    start_ts: datetime
    duration_min: float = Field(gt=0, le=600)
    sport: str = "training"
    sub_sport: str = "strength_training"


@router.get("/sessions")
def get_sessions(
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    conn: Connection = CONN,
) -> list[dict]:
    """Strength sessions (watch or manual), newest first, with title, plan day, set count and review state."""
    tz = _tz()
    s = datetime.combine(from_, datetime.min.time(), tz) if from_ else None
    e = datetime.combine(to + timedelta(days=1), datetime.min.time(), tz) if to else None
    return list_sessions(conn, s, e)


def list_skips(conn: Connection, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """Skipped plan days, newest first."""
    rows = conn.execute(
        text("""SELECT k.id, k.skipped_at, k.note, k.plan_day_id, d.name AS plan_day, p.name AS plan
                FROM plan_day_skip k JOIN plan_day d ON d.id = k.plan_day_id
                JOIN training_plan p ON p.id = d.plan_id
                WHERE (CAST(:s AS timestamptz) IS NULL OR k.skipped_at >= :s)
                  AND (CAST(:e AS timestamptz) IS NULL OR k.skipped_at < :e)
                ORDER BY k.skipped_at DESC"""),
        {"s": start, "e": end},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/history")
def get_history(
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    conn: Connection = CONN,
) -> dict:
    """One page of the strength sessions and skipped plan days (newest first), optionally within a
    date range. Items have `kind` "session" or "skip" (id, start_ts = when skipped, plan_day, note)."""
    tz = _tz()
    s = datetime.combine(from_, datetime.min.time(), tz) if from_ else None
    e = datetime.combine(to + timedelta(days=1), datetime.min.time(), tz) if to else None
    items = [{"kind": "session", **x} for x in list_sessions(conn, s, e)] + [
        {"kind": "skip", "id": k["id"], "start_ts": k["skipped_at"], "plan_day": k["plan_day"],
         "plan": k["plan"], "plan_day_id": k["plan_day_id"], "note": k["note"]}
        for k in list_skips(conn, s, e)
    ]  # fmt: skip
    items.sort(key=lambda x: x["start_ts"], reverse=True)
    return {"total": len(items), "offset": offset, "limit": limit, "items": items[offset : offset + limit]}


class SkipIn(BaseModel):
    plan_day_id: uuid.UUID | None = Field(None, description="Default: the next day in the rotation")
    note: str | None = Field(None, max_length=500)


@router.post("/skips", status_code=status.HTTP_201_CREATED)
def create_skip(body: SkipIn, conn: Connection = CONN) -> dict:
    """Skip a plan day: the rotation goes on with the day after it; the skip shows in the history."""
    day_id = body.plan_day_id
    if day_id is None:
        nxt = next_workout(conn)
        if nxt is None:
            raise HTTPException(409, "no active plan")
        day_id = nxt["plan_day_id"]
    elif conn.execute(text("SELECT 1 FROM plan_day WHERE id = :d"), {"d": day_id}).first() is None:
        raise HTTPException(422, f"unknown plan day {day_id}")
    sid = uuid.uuid4()
    conn.execute(
        text("INSERT INTO plan_day_skip (id, plan_day_id, note) VALUES (:id, :d, :n)"),
        {"id": sid, "d": day_id, "n": (body.note or "").strip() or None},
    )
    return next(k for k in list_skips(conn) if k["id"] == sid)


@router.delete("/skips/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skip(sid: uuid.UUID, conn: Connection = CONN) -> Response:
    """Undo a skip."""
    if (
        conn.execute(text("DELETE FROM plan_day_skip WHERE id = :id RETURNING id"), {"id": sid}).first()
        is None
    ):
        raise HTTPException(404, "skip not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class RatingsIn(BaseModel):
    ratings: list[RatingIn] = Field(default_factory=list, max_length=100, description="Full state")


@router.put("/sessions/{aid}/ratings")
def put_ratings(aid: int, body: RatingsIn, conn: Connection = CONN) -> dict:
    """Rate the exercises of a session (too easy / good / too hard) without touching its sets or review;
    the rating steers the next target of the progression."""
    _activity(conn, aid)
    entries = {r.id for r in conn.execute(text("SELECT id FROM plan_exercise"))}
    write_ratings(conn, aid, body.ratings, _exercise_map(conn), entries)
    return session_detail(conn, aid)


@router.get("/sessions/{aid}")
def get_session(
    aid: int,
    plan_day_id: uuid.UUID | None = Query(
        None, description="Propose and plan for this day (picked in the review)"
    ),
    conn: Connection = CONN,
) -> dict:
    """The session with its effective sets, per-exercise summary, ratings, the plan day's targets and,
    while not reviewed, VitaLocal's `proposal` (plan day; exercise, warm-up and hidden per watch set)."""
    return session_detail(conn, aid, plan_day_id)


def as_plan_day(conn: Connection, aid: int) -> dict:
    """A plan day built from a workout: its exercises in order with sets, rep range and weight as done."""
    act = _activity(conn, aid)
    exmap = _exercise_map(conn)
    # Sets of a plan entry form one entry; otherwise each uninterrupted block of an exercise does,
    # so an exercise done twice (heavy early, light later) becomes two entries.
    groups: dict = {}
    prev, block = None, 0
    for x in effective_sets(conn, aid, exmap):
        if x["hidden"] or x["warmup"] or not x["exercise_id"]:
            continue
        if x["plan_exercise_id"]:
            key = (x["exercise_id"], x["plan_exercise_id"])
        else:
            if x["exercise_id"] != prev:
                block += 1
            key = (x["exercise_id"], block)
        prev = x["exercise_id"]
        groups.setdefault(key, []).append(x)
    if not groups:
        raise HTTPException(422, "no sets of this workout are assigned to exercises yet")
    entries = []
    for (eid, _), ss in groups.items():
        ex = exmap[eid]
        reps = [x["repetitions"] for x in ss if x["repetitions"]]
        weights = [x["weight_kg"] for x in ss if x["weight_kg"]]
        # the weight used most often (ties: the heavier one) is the working weight
        weight = max(set(weights), key=lambda w: (weights.count(w), w)) if weights else None
        timed = ex["kind"] == "time"
        durations = [int(x["duration_s"]) for x in ss if x["duration_s"]]
        entries.append({
            "exercise_id": eid,
            "exercise": ex["name"],
            "sets": len(ss),
            "reps_min": None if timed or not reps else min(reps),
            "reps_max": None if timed or not reps else max(reps),
            "target_reps": None if timed or not reps else min(reps),
            "target_weight_kg": weight,
            "duration_s": min(durations) if timed and durations else None,
            "rest_s": None,
            "note": None,
        })  # fmt: skip
    name = act["title"] or act["plan_day"] or act["start_ts"].astimezone(_tz()).date().strftime("%d.%m.")
    return {"name": name[:60], "from_activity_id": aid, "exercises": entries}


@router.get("/sessions/{aid}/as-plan-day")
def get_as_plan_day(aid: int, conn: Connection = CONN) -> dict:
    """A workout as a template for a plan day (body of `days[]` in `POST /training/plans`):
    exercises in order, sets as done, rep range = fewest to most reps, target = fewest reps at the
    weight used most often. `422` while no set is assigned to an exercise."""
    return as_plan_day(conn, aid)


@router.put("/sessions/{aid}")
def put_session(aid: int, body: SessionIn, conn: Connection = CONN) -> dict:
    """Review a session: title, note, plan day, sets and ratings. Safe to repeat."""
    _activity(conn, aid)
    write_session(conn, aid, body)
    return session_detail(conn, aid)


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_manual(body: ManualIn, conn: Connection = CONN) -> dict:
    """A workout without the watch. Sets are added in the body (no `key`)."""
    existing = conn.execute(
        text("SELECT id FROM activity WHERE manual_key = :k"), {"k": body.manual_key}
    ).scalar()
    if existing:
        return session_detail(conn, existing)
    start = body.start_ts if body.start_ts.tzinfo else body.start_ts.replace(tzinfo=_tz())
    aid = conn.execute(
        text("""INSERT INTO activity (device_id, start_ts, end_ts, name, sport, sub_sport, elapsed_s, timer_s,
                                      manual, manual_key)
                VALUES (NULL, :s, :e, :n, :sport, :sub, :d, :d, true, :k) RETURNING id"""),
        {"s": start, "e": start + timedelta(minutes=body.duration_min), "n": body.title or "Training",
         "sport": body.sport, "sub": body.sub_sport, "d": body.duration_min * 60, "k": body.manual_key},
    ).scalar()  # fmt: skip
    write_session(conn, aid, body)
    return session_detail(conn, aid)


@router.delete("/sessions/{aid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(aid: int, conn: Connection = CONN) -> Response:
    """A manual workout is deleted; for a watch workout only the review layer is removed."""
    act = _activity(conn, aid)
    if act["manual"]:
        conn.execute(text("DELETE FROM activity WHERE id = :id"), {"id": aid})
    else:
        for t in ("training_session", "training_set", "exercise_rating"):
            conn.execute(text(f"DELETE FROM {t} WHERE activity_id = :a"), {"a": aid})
        conn.execute(
            text("""UPDATE activity_set_edit SET exercise_id = NULL, plan_exercise_id = NULL, hidden = false,
                        warmup = false,
                        duration_s = NULL WHERE activity_id = :a"""),
            {"a": aid},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- workouts entered by hand while the watch recorded too ------------------------------------------------

_END = "COALESCE(a.end_ts, a.start_ts + make_interval(secs => COALESCE(a.elapsed_s, 0)))"


def overlapping(conn: Connection, aid: int) -> list[dict]:
    """Strength workouts of the other kind (watch vs. entered by hand) whose time overlaps this one's:
    the same workout recorded twice."""
    me = conn.execute(
        text(f"SELECT a.start_ts, {_END} AS end_ts, a.manual FROM activity a WHERE a.id = :id"), {"id": aid}
    ).first()
    if me is None:
        return []
    rows = conn.execute(
        text(f"""SELECT a.id, a.start_ts, {_END} AS end_ts, a.manual,
                        coalesce(a.sport_profile_name, a.name) AS name
                 FROM activity a WHERE {_IS_SESSION} AND a.id <> :id AND a.manual <> :manual
                   AND a.start_ts < :e AND {_END} > :s ORDER BY a.start_ts"""),
        {"id": aid, "manual": me.manual, "s": me.start_ts, "e": me.end_ts},
    )
    return [dict(r._mapping) for r in rows]


class MergeIn(BaseModel):
    into: int = Field(description="The watch workout the sets entered by hand go into")


@router.post("/sessions/{aid}/merge")
def merge_session(aid: int, body: MergeIn, conn: Connection = CONN) -> dict:
    """A workout entered by hand (`aid`) and the watch's recording of the same workout become one: the
    watch workout keeps its heart rate and energy and gets the hand-entered sets, ratings, title, note
    and plan day (reviewed); its own sets are hidden. The hand-entered workout is deleted."""
    act, watch = _activity(conn, aid), _activity(conn, body.into)
    if not act["manual"] or watch["manual"]:
        raise HTTPException(422, "merge a workout entered by hand into a watch workout")
    if body.into not in {o["id"] for o in overlapping(conn, aid)}:
        raise HTTPException(422, "the workouts do not overlap")
    conn.execute(
        text("""INSERT INTO training_session (activity_id, title, note, plan_day_id, reviewed_at, deload)
                SELECT :w, title, note, plan_day_id, now(), deload FROM training_session
                WHERE activity_id = :m
                ON CONFLICT (activity_id) DO UPDATE
                SET title = COALESCE(EXCLUDED.title, training_session.title),
                    note = COALESCE(EXCLUDED.note, training_session.note),
                    plan_day_id = COALESCE(EXCLUDED.plan_day_id, training_session.plan_day_id),
                    deload = EXCLUDED.deload, reviewed_at = now(), updated_at = now()"""),
        {"w": body.into, "m": aid},
    )
    conn.execute(
        text("""INSERT INTO activity_set_edit (activity_id, set_index, hidden, updated_at)
                SELECT activity_id, set_index, true, now() FROM activity_set
                WHERE activity_id = :w AND set_type = 'active'
                ON CONFLICT (activity_id, set_index) DO UPDATE SET hidden = true, exercise_id = NULL,
                    plan_exercise_id = NULL, warmup = false, updated_at = now()"""),
        {"w": body.into},
    )
    conn.execute(text("DELETE FROM training_set WHERE activity_id = :w"), {"w": body.into})
    conn.execute(
        text("UPDATE training_set SET activity_id = :w WHERE activity_id = :m"), {"w": body.into, "m": aid}
    )
    conn.execute(text("DELETE FROM exercise_rating WHERE activity_id = :w"), {"w": body.into})
    conn.execute(
        text("UPDATE exercise_rating SET activity_id = :w WHERE activity_id = :m"), {"w": body.into, "m": aid}
    )
    conn.execute(text("DELETE FROM activity WHERE id = :m"), {"m": aid})
    return session_detail(conn, body.into)


# --- next workout, stats, facts -------------------------------------------------------------------------


def next_workout(conn: Connection) -> dict | None:
    """The active plan's next day in the rotation with targets and suggestions."""
    plan = active_plan(conn)
    if not plan or not plan["days"]:
        return None
    last = _last_done_day(conn, datetime.now(UTC) + timedelta(days=1))
    day_id, _ = training.suggest_day(plan["days"], last, [])
    day = _day_of(plan, day_id)
    last_date = conn.execute(
        text("""SELECT max(a.start_ts) FROM activity a JOIN training_session t ON t.activity_id = a.id
                WHERE t.reviewed_at IS NOT NULL AND t.plan_day_id IS NOT NULL""")
    ).scalar()
    unreviewed = conn.execute(
        text(f"""SELECT count(*) FROM activity a LEFT JOIN training_session t ON t.activity_id = a.id
                 WHERE {_IS_SESSION} AND t.reviewed_at IS NULL AND a.start_ts > now() - interval '30 days'""")
    ).scalar()
    return {
        "plan_id": plan["id"],
        "plan": plan["name"],
        "plan_day_id": day["id"],
        "plan_day": day["name"],
        "last_session": last_date,
        "unreviewed_sessions": unreviewed,
        "exercises": day_targets(conn, day),
        "deload": deload_status(conn),
    }


# --- deload -------------------------------------------------------------------------------------------------


class DeloadIn(BaseModel):
    days: int = Field(training.DELOAD_DAYS, ge=3, le=14, description="Length of the deload, from today")


def deload_window(conn: Connection, plan_day_id=None, plan_id=None) -> tuple[date, date] | None:
    """The deload week of the plan (of the plan day, else the given or active plan), if one is set."""
    if plan_day_id is not None:
        row = conn.execute(
            text("""SELECT p.deload_from, p.deload_until FROM plan_day d
                    JOIN training_plan p ON p.id = d.plan_id
                    WHERE d.id = :d"""),
            {"d": plan_day_id},
        ).first()
    else:
        row = conn.execute(
            text("""SELECT deload_from, deload_until FROM training_plan
                    WHERE (CAST(:p AS uuid) IS NULL AND active) OR id = :p"""),
            {"p": plan_id},
        ).first()
    return (row.deload_from, row.deload_until) if row and row.deload_from else None


def in_deload(conn: Connection, day: date, plan_day_id=None) -> bool:
    w = deload_window(conn, plan_day_id=plan_day_id)
    return bool(w and w[0] <= day <= w[1])


def deload_status(conn: Connection, today: date | None = None) -> dict | None:
    """The active plan's deload: the current week (or the last one), the interval and whether one is
    suggested now: when the planned interval is due, two or more exercises were rated too hard twice
    in a row, or most ratings of the last two weeks were "too hard"."""
    plan = active_plan(conn)
    if not plan:
        return None
    today = today or _today()
    active = bool(plan["deload_from"] and plan["deload_from"] <= today <= plan["deload_until"])
    reasons = []
    last_end = plan["deload_until"] if plan["deload_until"] and plan["deload_until"] < today else None
    recent = last_end is not None and (today - last_end).days < 21
    if not active and not recent and not (plan["deload_from"] and plan["deload_from"] > today):
        since = last_end or plan["created_at"].astimezone(_tz()).date()
        weeks = (today - since).days // 7
        if plan["deload_every_weeks"] and weeks >= plan["deload_every_weeks"]:
            reasons.append({"key": "scheduled", "weeks": weeks})
        stalled = []
        for d in plan["days"]:
            for pe in d["exercises"]:
                hist = performances(conn, pe["exercise_id"], limit=2, plan_exercise_id=pe["id"])
                if len(hist) == 2 and all(h["rating"] == "hard" for h in hist):
                    stalled.append(pe["exercise"])
        if len(stalled) >= 2:
            reasons.append({"key": "stalls", "exercises": stalled})
        tz = _tz()
        ratings = [r.rating for r in conn.execute(
            text("""SELECT r.rating FROM exercise_rating r JOIN activity a ON a.id = r.activity_id
                    JOIN training_session t ON t.activity_id = a.id
                    WHERE NOT t.deload AND a.start_ts >= :s"""),
            {"s": datetime.combine(today - timedelta(days=14), datetime.min.time(), tz)})]  # fmt: skip
        if len(ratings) >= 5 and ratings.count("hard") / len(ratings) >= 0.6:
            reasons.append({"key": "hard_ratings", "hard": ratings.count("hard"), "of": len(ratings)})
    return {
        "plan_id": plan["id"],
        "active": active,
        "from": plan["deload_from"],
        "until": plan["deload_until"],
        "every_weeks": plan["deload_every_weeks"],
        "suggested": bool(reasons),
        "reasons": reasons,
    }


@router.get("/deload")
def get_deload(conn: Connection = CONN) -> dict | None:
    """The active plan's deload week and whether one is suggested (`reasons`: scheduled, stalls,
    hard_ratings). In a deload week the targets are one set less at the same weight with reps at the
    bottom of the range, and its workouts are left out of the progression. Null without an active plan."""
    return deload_status(conn)


@router.post("/deload")
def start_deload(body: DeloadIn, conn: Connection = CONN) -> dict:
    """Starts a deload week for the active plan today."""
    plan = active_plan(conn)
    if not plan:
        raise HTTPException(409, "no active plan")
    today = _today()
    conn.execute(
        text("UPDATE training_plan SET deload_from = :f, deload_until = :u WHERE id = :id"),
        {"f": today, "u": today + timedelta(days=body.days - 1), "id": plan["id"]},
    )
    return deload_status(conn)


@router.delete("/deload")
def end_deload(conn: Connection = CONN) -> dict | None:
    """Ends the deload week early (yesterday was its last day); one that has not started is removed."""
    plan = active_plan(conn)
    if not plan or not plan["deload_from"]:
        raise HTTPException(409, "no deload week")
    today = _today()
    if plan["deload_from"] >= today:
        conn.execute(text("UPDATE training_plan SET deload_from = NULL, deload_until = NULL WHERE id = :id"),
                     {"id": plan["id"]})  # fmt: skip
    else:
        conn.execute(text("UPDATE training_plan SET deload_until = :u WHERE id = :id"),
                     {"u": today - timedelta(days=1), "id": plan["id"]})  # fmt: skip
    return deload_status(conn)


@router.get("/plan-days/{day_id}/targets")
def get_day_targets(day_id: uuid.UUID, conn: Connection = CONN) -> list[dict]:
    """Per entry of a plan day: current target, the progression's suggestion and the last performance."""
    pid = conn.execute(text("SELECT plan_id FROM plan_day WHERE id = :d"), {"d": day_id}).scalar()
    if pid is None:
        raise HTTPException(404, "plan day not found")
    return day_targets(conn, _day_of(get_plan(conn, pid), day_id))


@router.get("/next")
def get_next(conn: Connection = CONN) -> dict | None:
    """Next plan day (rotation after the last reviewed session) with each exercise's current target and
    the suggestion ("next time") from the progression; null without an active plan."""
    return next_workout(conn)


@router.get("/plan/current")
def get_current_plan(conn: Connection = CONN) -> dict | None:
    """The active plan with every day's exercises, their current targets and the progression's
    suggestions; `next_day_id` is the day due in the rotation. Null without an active plan."""
    plan = active_plan(conn)
    if not plan:
        return None
    nxt = next_workout(conn)
    return {
        "id": plan["id"],
        "name": plan["name"],
        "note": plan["note"],
        "next_day_id": nxt["plan_day_id"] if nxt else None,
        "days": [
            {"id": d["id"], "name": d["name"], "note": d["note"], "exercises": day_targets(conn, d)}
            for d in plan["days"]
        ],  # fmt: skip
    }


def _week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def stats(
    conn: Connection, weeks: int = 12, until: date | None = None, progress_weeks: int | None = None
) -> dict:
    """Hard sets per muscle group and week, and per exercise the best estimated 1RM per session
    (bodyweight exercises: body weight that day plus the extra load; time exercises: the best set,
    heaviest load first, then longest). The progression series may cover a longer range
    (`progress_weeks`) than the weekly hard sets."""
    tz = _tz()
    end = until or datetime.now(tz).date()
    start = _week(end) - timedelta(weeks=weeks - 1)
    p_start = _week(end) - timedelta(weeks=max(progress_weeks or weeks, 1) - 1)
    exmap = _exercise_map(conn)
    rows = conn.execute(
        text(f"""SELECT a.id, a.start_ts FROM activity a WHERE {_IS_SESSION}
                 AND a.start_ts >= :s AND a.start_ts < :e ORDER BY a.start_ts"""),
        {"s": datetime.combine(min(start, p_start), datetime.min.time(), tz),
         "e": datetime.combine(end + timedelta(days=1), datetime.min.time(), tz)},
    ).all()  # fmt: skip
    per_week: dict[date, dict[str, float]] = {start + timedelta(weeks=i): {} for i in range(weeks)}
    e1rm: dict = {}
    holds: dict = {}
    body: dict[date, float | None] = {}  # body weight per day, for bodyweight exercises
    sessions_per_week: dict[date, int] = {}
    for r in rows:
        day = r.start_ts.astimezone(tz).date()
        wk = _week(day)
        sets = effective_sets(conn, r.id, exmap)
        if not any(s["exercise_id"] for s in sets):
            continue
        if wk in per_week:
            sessions_per_week[wk] = sessions_per_week.get(wk, 0) + 1
            for m, n in training.hard_sets(sets, exmap).items():
                per_week[wk][m] = per_week[wk].get(m, 0) + n
        if day < p_start:
            continue
        for s in sets:
            if s["hidden"] or s["warmup"] or not s["exercise_id"]:
                continue
            ex = exmap[s["exercise_id"]]
            if ex["kind"] == "time":
                if not s["duration_s"]:
                    continue
                empty = {"exercise_id": s["exercise_id"], "exercise": ex["name"], "points": {}}
                series = holds.setdefault(s["exercise_id"], empty)
                best = series["points"].get(day)
                cur = (float(s["duration_s"]), s["weight_kg"] or 0.0)
                if best is None or (cur[1], cur[0]) > (best[1], best[0]):
                    series["points"][day] = cur
                continue
            load = s["weight_kg"]
            if ex["bodyweight"]:
                # the body is most of the load: its weight that day plus the extra load
                if day not in body:
                    end_of_day = datetime.combine(day + timedelta(days=1), datetime.min.time(), tz)
                    body[day] = latest_weight(conn, end_of_day)
                if body[day] is None:
                    continue
                load = body[day] + (load or 0)
            v = training.e1rm(load, s["repetitions"])
            if v is None:
                continue
            empty = {"exercise_id": s["exercise_id"], "exercise": ex["name"], "points": {},
                     "bodyweight": bool(ex["bodyweight"])}  # fmt: skip
            series = e1rm.setdefault(s["exercise_id"], empty)
            series["points"][day] = max(series["points"].get(day, 0), v)
    return {
        "weeks": [
            {
                "week": wk,
                "sessions": sessions_per_week.get(wk, 0),
                "hard_sets": {m: v for m, v in sorted(ms.items(), key=lambda x: -x[1])},
            }
            for wk, ms in per_week.items()
        ],  # fmt: skip
        "muscles": [
            {
                "key": k,
                "label": v,
                "group": training.GROUP_OF[k],
                "group_label": training.GROUP_LABELS[training.GROUP_OF[k]],
            }
            for k, v in training.MUSCLES.items()
        ],  # fmt: skip
        "progress_from": p_start,
        "e1rm": [
            {**v, "points": [{"date": d, "e1rm_kg": x} for d, x in sorted(v["points"].items())]}
            for v in sorted(e1rm.values(), key=lambda v: v["exercise"])
        ],  # fmt: skip
        "holds": [
            {
                **v,
                "points": [
                    {"date": d, "duration_s": x[0], "weight_kg": x[1] or None}
                    for d, x in sorted(v["points"].items())
                ],
            }
            for v in sorted(holds.values(), key=lambda v: v["exercise"])
        ],  # fmt: skip
    }


@router.get("/stats")
def get_stats(
    weeks: int = Query(12, ge=1, le=104),
    progress_weeks: int | None = Query(
        None, ge=1, le=520, description="Range of the progression series (1RM, holds); defaults to `weeks`"
    ),
    conn: Connection = CONN,
) -> dict:
    """Hard sets per muscle group and week (primary 1, secondary ½; warm-up and hidden sets excluded),
    the best estimated 1RM (Epley, 1–12 reps) per exercise and session and, for time exercises
    (plank, farmer's walk), the best set per session: the heaviest load, then the longest time."""
    return stats(conn, weeks, progress_weeks=progress_weeks)


# --- muscles: recovery, volume, what an exercise trains ---------------------------------------------------

# sessions that still carry stimulus (two weeks) plus the 28 days before them for the acute:chronic gain
_RECOVERY_LOOKBACK = timedelta(days=14) + recovery.CHRONIC


def recovery_sessions(conn: Connection, now: datetime) -> list[recovery.Session]:
    """Workouts of the lookback with their work sets and ratings; a workout not reviewed yet counts
    with VitaLocal's proposal for the sets the user has not edited (provisional)."""
    exmap = _exercise_map(conn)
    rows = conn.execute(
        text(f"""SELECT a.id, a.start_ts FROM activity a WHERE {_IS_SESSION}
                 AND a.start_ts >= :s AND a.start_ts <= :e ORDER BY a.start_ts"""),
        {"s": now - _RECOVERY_LOOKBACK, "e": now},
    ).all()
    out = []
    for r in rows:
        sets = effective_sets(conn, r.id, exmap)
        act = _activity(conn, r.id)
        reviewed = act["reviewed_at"] is not None
        if not reviewed or any(not s["exercise_id"] and not s["hidden"] and not s["warmup"] for s in sets):
            prop = {p["key"]: p for p in proposal(conn, act, sets, exmap)["sets"]}
            for s in sets:
                p = prop.get(s["key"])
                if p is None or s["exercise_id"]:
                    continue
                if not reviewed and not s["edited"]:
                    s.update(warmup=p["warmup"], hidden=p["hidden"])
                s["exercise_id"] = p["exercise_id"]
        ratings = {
            (x.exercise_id, x.plan_exercise_id): x.rating
            for x in conn.execute(
                text(
                    "SELECT exercise_id, plan_exercise_id, rating FROM exercise_rating WHERE activity_id = :a"
                ),
                {"a": r.id},
            )
        }
        by_ex = {e: rt for (e, _), rt in ratings.items()}
        for s in sets:
            s["rating"] = ratings.get((s["exercise_id"], s["plan_exercise_id"])) or by_ex.get(
                s["exercise_id"]
            )
        if any(s["exercise_id"] for s in sets):
            out.append(recovery.Session(id=r.id, ts=r.start_ts, sets=sets, provisional=not reviewed))
    return out


def muscle_state(conn: Connection, now: datetime) -> dict:
    exmap = _exercise_map(conn)
    state = recovery.muscle_state(recovery_sessions(conn, now), exmap, training.MUSCLES, now)
    garmin = insights_latest(conn, "recovery_time", now)
    garmin_h = None
    if garmin is not None:
        garmin_h = round(max(0.0, garmin.value / 60 - (now - garmin.ts).total_seconds() / 3600), 1)
    return {
        "at": now,
        "muscles": [{"key": k, "label": v, **state[k]} for k, v in training.MUSCLES.items()],
        "garmin_recovery_h": garmin_h,
        "thresholds": {"ready": recovery.READY, "fatigued": recovery.FATIGUED},
    }


@router.get("/muscles/state")
def get_muscle_state(
    at: datetime | None = Query(None, description="Moment to compute for (default: now)"),
    conn: Connection = CONN,
) -> dict:
    """Recovery per muscle: remaining fatigue (one full session ≈ 1), `state` (ready < 0.25 ≤ recovering
    ≤ 0.5 < fatigued), `ready_in_h`, `last_trained` (within the last six weeks), hard sets of the last
    7 days and the workouts still contributing (a workout not reviewed yet counts with the proposed
    exercises, `provisional`). `garmin_recovery_h`: the watch's own recovery time left, for comparison.
    Entries with `alias` stand for the old coarse chest, shoulders and back (their most fatigued part)
    for apps that still draw those; skip them."""
    now = at or datetime.now(_tz())
    if now.tzinfo is None:
        now = now.replace(tzinfo=_tz())
    out = muscle_state(conn, now)
    # the app up to 1.7.0 draws chest, shoulders and back as one muscle each: its most fatigued part
    by_key = {m["key"]: m for m in out["muscles"]}
    for old, parts in training.ALIASES.items():
        top = max((by_key[m] for m in parts), key=lambda m: m["fatigue"])
        out["muscles"].append({**top, "key": old, "label": training.GROUP_LABELS[old], "alias": True})
    return out


@router.get("/muscles/volume")
def get_muscle_volume(weeks: int = Query(4, ge=1, le=52), conn: Connection = CONN) -> dict:
    """Hard sets per muscle over the last `weeks` weeks (this week included; reviewed sets as in the
    stats) and their weekly average."""
    st = stats(conn, weeks=weeks)
    total: dict[str, float] = {}
    for w in st["weeks"]:
        for m, n in w["hard_sets"].items():
            total[m] = total.get(m, 0) + n
    return {
        "weeks": weeks,
        "from": st["weeks"][0]["week"],
        "muscles": [
            {
                "key": k,
                "label": v,
                "hard_sets": total.get(k, 0),
                "per_week": round(total.get(k, 0) / weeks, 1),
            }
            for k, v in training.MUSCLES.items()
        ],
    }


@router.get("/muscles/of")
def get_muscles_of(
    exercise_id: uuid.UUID | None = Query(None),
    plan_day_id: uuid.UUID | None = Query(None),
    conn: Connection = CONN,
) -> dict:
    """What an exercise (primary 1, secondary ½) or a plan day (its sets × those shares) trains."""
    if (exercise_id is None) == (plan_day_id is None):
        raise HTTPException(422, "give exercise_id or plan_day_id")
    exmap = _exercise_map(conn)
    if exercise_id is not None:
        ex = exmap.get(exercise_id) or _exercise(conn, exercise_id)
        parts = [(ex, 1)]
    else:
        rows = conn.execute(
            text("SELECT exercise_id, sets FROM plan_exercise WHERE plan_day_id = :d ORDER BY position"),
            {"d": plan_day_id},
        ).all()
        if (
            not rows
            and not conn.execute(text("SELECT 1 FROM plan_day WHERE id = :d"), {"d": plan_day_id}).first()
        ):
            raise HTTPException(404, "plan day not found")
        parts = [(exmap[r.exercise_id], r.sets) for r in rows if r.exercise_id in exmap]
    out: dict[str, float] = {}
    for ex, n in parts:
        for m in ex["muscles"]:
            out[m] = out.get(m, 0) + n
        for m in ex["muscles_secondary"]:
            out[m] = out.get(m, 0) + n * recovery.SECONDARY
    return {"muscles": [{"key": k, "label": training.MUSCLES[k], "hard_sets": v} for k, v in out.items()]}


# --- structural balance -----------------------------------------------------------------------------------


def balance(conn: Connection, weeks: int = 8, until: date | None = None) -> dict:
    """Weekly work sets per movement pattern and the best estimated 1RM per pattern over `weeks`
    weeks, compared pairwise (`training.balance`)."""
    tz = _tz()
    end = until or _today()
    start = end - timedelta(weeks=weeks)
    exmap = _exercise_map(conn)
    sets: dict[str, float] = {}
    rows = conn.execute(
        text(f"""SELECT a.id FROM activity a WHERE {_IS_SESSION} AND a.start_ts >= :s AND a.start_ts < :e"""),
        {"s": datetime.combine(start, datetime.min.time(), tz),
         "e": datetime.combine(end + timedelta(days=1), datetime.min.time(), tz)},
    ).all()  # fmt: skip
    for r in rows:
        for s in effective_sets(conn, r.id, exmap):
            ex = exmap.get(s["exercise_id"])
            if ex is None or s["warmup"] or s["hidden"] or not (s["repetitions"] or s["duration_s"]):
                continue
            p = training.PATTERNS.get(ex["garmin_category"])
            if p:
                sets[p] = sets.get(p, 0) + 1 / weeks
    st = stats(conn, weeks=max(1, weeks), until=end)
    for w in st["weeks"]:
        for m in training.HARD_SET_PAIRS:
            sets[m] = sets.get(m, 0) + w["hard_sets"].get(m, 0) / len(st["weeks"])
    best: dict[str, float] = {}
    for series in st["e1rm"]:
        ex = exmap.get(series["exercise_id"])
        p = training.PATTERNS.get(ex["garmin_category"]) if ex else None
        if not p or not series["points"]:
            continue
        kg = max(x["e1rm_kg"] for x in series["points"]) * (2 if ex["dumbbells"] == 1 else 1)
        best[p] = max(best.get(p, 0), kg)
    return {
        "weeks": weeks,
        "sets_per_week": {k: round(v, 1) for k, v in sets.items()},
        **training.balance(sets, best),
    }


@router.get("/balance")
def get_balance(weeks: int = Query(8, ge=2, le=52), conn: Connection = CONN) -> dict:
    """Structural balance over the last `weeks` weeks: work sets per week by movement pattern (from the
    Garmin category of the exercises) in pairs – push/pull, horizontal, vertical, knee/hip, biceps/
    triceps, front/rear delts – each with its sensible range, and the best estimated 1RM per pattern
    against rough
    references (row ≈ bench press, overhead press ≈ 60 % of it, squat ≈ 80 % of the deadlift; dumbbells
    count both hands). `state`: ok, low, high, missing. Hints only; the plan is not changed."""
    return balance(conn, weeks)


def training_facts(conn: Connection, day: date, days: int = 7, detail: bool = False) -> dict | str:
    """Strength training as data for the summaries and agents."""
    tz = _tz()
    start = datetime.combine(day - timedelta(days=days - 1), datetime.min.time(), tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tz)
    sessions = list(reversed(list_sessions(conn, start, end)))
    if not sessions and not active_plan(conn):
        return "no strength training recorded"
    out_sessions = []
    for s in sessions:
        d = session_detail(conn, s["id"])
        item = {
            "date": s["start_ts"].astimezone(tz).date().isoformat(),
            "title": d["display_title"],
            "plan_day": d["plan_day"] or (d.get("proposal") or {}).get("plan_day"),
            "reviewed": d["reviewed"],
            "exercises": [{"exercise": e["exercise"], "sets": e["text"], "rating": e["rating"],
                           **({"best_e1rm_kg": e["best_e1rm_kg"]} if e["best_e1rm_kg"] else {})}
                          for e in d["exercises"]] or "sets not assigned to exercises yet",
        }  # fmt: skip
        if detail and d["note"]:
            item["note"] = d["note"]
        out_sessions.append(item)
    st = stats(conn, weeks=4, until=day)
    nxt = next_workout(conn)
    now = min(datetime.now(tz), end)
    ms = muscle_state(conn, now)
    return {
        f"sessions_last_{days}_days": out_sessions or "none",
        "hard_sets_per_muscle_this_week": st["weeks"][-1]["hard_sets"] or "none",
        "hard_sets_per_muscle_by_week": {w["week"].isoformat(): w["hard_sets"] for w in st["weeks"]} if detail
        else None,
        "next_workout": {
            "plan_day": nxt["plan_day"],
            "exercises": [{"exercise": e["exercise"], "target": e["current"],
                           "suggested_next": e["next"]["text"],
                           **({"last_rating": e["last"]["rating"]} if e["last"] else {})}
                          for e in nxt["exercises"]],
        } if nxt else "no active plan",
        "muscle_recovery": {
            "at": now.isoformat(timespec="minutes"),
            "not_ready": {m["key"]: {"state": m["state"], "ready_in_h": m["ready_in_h"]}
                          for m in ms["muscles"] if m["state"] != "ready"} or "all muscles ready",
            "garmin_recovery_h": ms["garmin_recovery_h"],
        },
        "note": "Ratings: easy = too easy, hard = too hard. Hard sets: work sets, secondary muscles count ½. "
                "Muscle recovery: VitaLocal's estimate from recent sets (ready below 0.25 fatigue).",
    }  # fmt: skip


@router.get("/facts")
def get_facts(
    day: date | None = Query(None), days: int = Query(28, ge=1, le=365), conn: Connection = CONN
) -> dict | str:
    """Strength training prepared for agents: sessions with exercises, sets and ratings, hard sets per
    muscle group by week, the next workout with targets and suggestions."""
    out = training_facts(conn, day or datetime.now(_tz()).date(), days, detail=True)
    if isinstance(out, dict):
        out["e1rm"] = stats(conn, weeks=max(4, days // 7 + 1))["e1rm"]
    return json.loads(json.dumps(out, default=str))
