import re
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from vitalocal import exercises
from vitalocal.db import CONN

router = APIRouter(prefix="/api/v1", tags=["data"])

_BUCKET_RE = re.compile(r"^(\d+)(m|h|d)$")
_MAX_RAW_ROWS = 50_000


def _bucket(value: str) -> timedelta:
    m = _BUCKET_RE.match(value)
    if not m:
        raise HTTPException(422, "bucket must look like 15m, 1h or 1d")
    n, unit = int(m[1]), m[2]
    return {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


@router.get("/metrics")
def list_metrics(conn: Connection = CONN) -> list[dict]:
    rows = conn.execute(
        text("""
        SELECT metric, max(unit) AS unit, count(*) AS samples, min(ts) AS first, max(ts) AS last
        FROM sample GROUP BY metric ORDER BY metric
    """)
    )
    return [dict(r._mapping) for r in rows]


@router.get("/samples")
def get_samples(
    metric: str,
    from_: datetime = Query(alias="from"),
    to: datetime = Query(),
    bucket: str | None = Query(None, description="Aggregate into buckets, e.g. 15m, 1h, 1d"),
    device_id: int | None = None,
    conn: Connection = CONN,
) -> dict:
    params = {"metric": metric, "from": from_, "to": to, "dev": device_id}
    where = (
        "metric = :metric AND ts >= :from AND ts < :to AND (CAST(:dev AS int) IS NULL OR device_id = :dev)"
    )
    if bucket:
        params["bucket"] = _bucket(bucket)
        rows = conn.execute(
            text(f"""
            SELECT date_bin(:bucket, ts, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS bucket_start,
                   avg(value) AS avg, min(value) AS min, max(value) AS max, sum(value) AS sum,
                   count(*) AS n
            FROM sample WHERE {where} GROUP BY 1 ORDER BY 1
        """),
            params,
        )
    else:
        rows = conn.execute(
            text(f"""
            SELECT ts, device_id, value, unit FROM sample WHERE {where}
            ORDER BY ts LIMIT {_MAX_RAW_ROWS + 1}
        """),
            params,
        )
    data = [dict(r._mapping) for r in rows]
    truncated = not bucket and len(data) > _MAX_RAW_ROWS
    return {"metric": metric, "bucket": bucket, "truncated": truncated, "data": data[:_MAX_RAW_ROWS]}


_ACTIVITY_COLS = """
    a.id, a.device_id, a.start_ts, a.end_ts, a.name, a.sport, a.sub_sport, a.sport_profile_name,
    a.elapsed_s, a.timer_s, a.distance_m, a.energy_kcal, a.avg_hr, a.max_hr, a.has_gpx,
    a.raw_details_name, a.raw_fit_sha256,
    a.gb_parse_run_id IS NOT NULL AS from_gadgetbridge_db,
    a.fit_parse_run_id IS NOT NULL AS from_fit,
    (SELECT count(*) FROM activity_set s WHERE s.activity_id = a.id AND s.set_type = 'active')
        AS active_sets,
    a.manual, (SELECT t.title FROM training_session t WHERE t.activity_id = a.id) AS title,
    (SELECT t.reviewed_at IS NOT NULL FROM training_session t WHERE t.activity_id = a.id) AS reviewed
"""


@router.get("/activities")
def list_activities(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    sport: str | None = None,
    conn: Connection = CONN,
) -> list[dict]:
    rows = conn.execute(
        text(f"""
        SELECT {_ACTIVITY_COLS} FROM activity a
        WHERE (CAST(:from AS timestamptz) IS NULL OR a.start_ts >= :from)
          AND (CAST(:to AS timestamptz) IS NULL OR a.start_ts < :to)
          AND (CAST(:sport AS text) IS NULL OR a.sport = :sport)
        ORDER BY a.start_ts DESC
    """),
        {"from": from_, "to": to, "sport": sport},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/activities/{activity_id}")
def get_activity(activity_id: int, conn: Connection = CONN) -> dict:
    row = conn.execute(
        text(
            f"SELECT {_ACTIVITY_COLS}, a.fit_attrs, a.summary AS gb_summary FROM activity a WHERE a.id = :id"
        ),
        {"id": activity_id},
    ).first()
    if row is None:
        raise HTTPException(404, "activity not found")
    sets = conn.execute(
        text("""
        SELECT s.set_index, s.set_type, s.start_ts, s.duration_s, s.repetitions, s.weight_kg,
               s.exercise_category, s.category_candidates, s.subtype_candidates, s.wkt_step_index,
               e.exercise_category AS edit_category, e.exercise_name AS edit_name,
               e.repetitions AS edit_repetitions, e.weight_kg AS edit_weight_kg,
               e.updated_at AS edited_at
        FROM activity_set s
        LEFT JOIN activity_set_edit e ON e.activity_id = s.activity_id AND e.set_index = s.set_index
        WHERE s.activity_id = :id ORDER BY s.set_index
    """),
        {"id": activity_id},
    )
    rpe = conn.execute(
        text("""SELECT id, value, note, ts FROM checkin WHERE kind = 'rpe' AND activity_id = :id
                ORDER BY ts DESC LIMIT 1"""),
        {"id": activity_id},
    ).first()
    return {
        **row._mapping,
        "sets": [_effective_set(r) for r in sets],
        "rpe": dict(rpe._mapping) if rpe else None,
    }


def _effective_set(r) -> dict:
    """Original (watch) values plus the user's corrections; corrections win where present."""
    original_name = exercises.name_for(r.exercise_category, r.subtype_candidates)
    edited = r.edited_at is not None
    category = r.edit_category if r.edit_category is not None else r.exercise_category
    name = r.edit_name if r.edit_category is not None or r.edit_name is not None else original_name
    return {
        "set_index": r.set_index,
        "set_type": r.set_type,
        "start_ts": r.start_ts,
        "duration_s": r.duration_s,
        "exercise_category": category,
        "exercise_name": name,
        "repetitions": r.edit_repetitions if r.edit_repetitions is not None else r.repetitions,
        "weight_kg": r.edit_weight_kg if r.edit_weight_kg is not None else r.weight_kg,
        "edited": edited,
        "original": {
            "exercise_category": r.exercise_category,
            "exercise_name": original_name,
            "repetitions": r.repetitions,
            "weight_kg": r.weight_kg,
            "category_candidates": r.category_candidates,
            "subtype_candidates": r.subtype_candidates,
        },
    }


class SetEdit(BaseModel):
    """Correction for one set. Omitted/null fields keep the watch's value."""

    exercise_category: str | None = None
    exercise_name: str | None = None
    repetitions: int | None = Field(None, ge=0, le=1000)
    weight_kg: float | None = Field(None, ge=0, le=1000)


@router.put("/activities/{activity_id}/sets/{set_index}")
def edit_set(activity_id: int, set_index: int, edit: SetEdit, conn: Connection = CONN) -> dict:
    exists = conn.execute(
        text("""SELECT set_type FROM activity_set WHERE activity_id = :a AND set_index = :i"""),
        {"a": activity_id, "i": set_index},
    ).scalar()
    if exists is None:
        raise HTTPException(404, "set not found")
    if exists != "active":
        raise HTTPException(422, "only active sets can be corrected")
    if edit.exercise_category is not None and not exercises.is_category(edit.exercise_category):
        raise HTTPException(422, f"unknown exercise category {edit.exercise_category!r}")
    if edit.exercise_name is not None:
        if edit.exercise_category is None:
            raise HTTPException(422, "exercise_name requires exercise_category")
        if not exercises.is_name(edit.exercise_category, edit.exercise_name):
            raise HTTPException(422, f"{edit.exercise_name!r} is not a {edit.exercise_category} exercise")
    if all(v is None for v in edit.model_dump().values()):
        return reset_set(activity_id, set_index, conn)
    conn.execute(
        text("""
        INSERT INTO activity_set_edit (activity_id, set_index, exercise_category, exercise_name,
                                       repetitions, weight_kg, updated_at)
        VALUES (:a, :i, :c, :n, :r, :w, now())
        ON CONFLICT (activity_id, set_index) DO UPDATE SET
            exercise_category = EXCLUDED.exercise_category, exercise_name = EXCLUDED.exercise_name,
            repetitions = EXCLUDED.repetitions, weight_kg = EXCLUDED.weight_kg, updated_at = now()
    """),
        {
            "a": activity_id,
            "i": set_index,
            "c": edit.exercise_category,
            "n": edit.exercise_name,
            "r": edit.repetitions,
            "w": edit.weight_kg,
        },
    )
    return {"activity_id": activity_id, "set_index": set_index, "edited": True}


@router.delete("/activities/{activity_id}/sets/{set_index}/edit")
def reset_set(activity_id: int, set_index: int, conn: Connection = CONN) -> dict:
    conn.execute(
        text("DELETE FROM activity_set_edit WHERE activity_id = :a AND set_index = :i"),
        {"a": activity_id, "i": set_index},
    )
    return {"activity_id": activity_id, "set_index": set_index, "edited": False}


@router.get("/exercises")
def list_exercises() -> list[dict]:
    """FIT exercise categories with their specific exercise names."""
    return exercises.catalog()
