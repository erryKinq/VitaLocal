from datetime import datetime

from fastapi import APIRouter, Query
from sqlalchemy import Connection, text

from vitalocal.db import CONN

router = APIRouter(prefix="/api/v1/quality", tags=["quality"])


@router.get("/coverage")
def coverage(
    from_: datetime = Query(alias="from"),
    to: datetime = Query(),
    tz: str = Query("Europe/Berlin", description="Time zone for day boundaries"),
    metric: str | None = None,
    conn: Connection = CONN,
) -> list[dict]:
    """Per metric and local day: sample count, first/last sample and the largest gap."""
    rows = conn.execute(
        text("""
        WITH s AS (
            SELECT metric, device_id, ts,
                   ts - lag(ts) OVER (PARTITION BY metric, device_id ORDER BY ts) AS gap
            FROM sample
            WHERE ts >= :from AND ts < :to AND (CAST(:metric AS text) IS NULL OR metric = :metric)
        )
        SELECT metric, device_id, (ts AT TIME ZONE :tz)::date AS day, count(*) AS samples,
               min(ts) AS first, max(ts) AS last,
               round(extract(epoch FROM max(gap)) / 60)::int AS max_gap_minutes
        FROM s GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """),
        {"from": from_, "to": to, "tz": tz, "metric": metric},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/reconciliation")
def reconciliation(
    batch_id: int | None = Query(None, description="One batch; default: the current state of all"),
    conn: Connection = CONN,
) -> dict:
    """Raw artifacts vs. normalized data: what arrived, what was parsed, what not.

    Without `batch_id` the current state: per archive path the newest file of any Gadgetbridge
    batch (file sets only carry new files) and the latest full database parse.
    """
    if batch_id is None:
        scope = """SELECT DISTINCT ON (m.archive_path) m.* FROM artifact_member m
                   JOIN ingest_batch b ON b.id = m.batch_id
                   WHERE b.source IN ('gadgetbridge_export', 'gadgetbridge_files')
                     AND b.status LIKE 'completed%' AND m.nested_in IS NULL
                   ORDER BY m.archive_path, m.batch_id DESC"""
        run_where = "parser_name = 'gadgetbridge-sqlite' AND status = 'completed'"
    else:
        scope = "SELECT * FROM artifact_member WHERE batch_id = :b AND nested_in IS NULL"
        run_where = "batch_id = :b AND parser_name = 'gadgetbridge-sqlite'"
    params = {"b": batch_id}

    categories = conn.execute(
        text(f"""
        SELECT category, count(*) AS files, sum(r.size) AS bytes,
               count(*) FILTER (WHERE m.meta ? 'fit') AS fit_files,
               count(*) FILTER (WHERE m.meta -> 'fit' ? 'fit_error') AS fit_errors
        FROM ({scope}) m JOIN raw_artifact r ON r.sha256 = m.artifact_sha256
        GROUP BY 1 ORDER BY 2 DESC
    """),
        params,
    )

    unmatched = conn.execute(
        text(f"""
        SELECT m.archive_path, m.artifact_sha256, m.meta -> 'fit' -> 'sessions' AS sessions,
               m.meta -> 'fit' -> 'message_counts' AS message_counts
        FROM ({scope}) m
        WHERE m.category = 'ACTIVITY'
          AND NOT EXISTS (SELECT 1 FROM activity a WHERE a.raw_fit_sha256 = m.artifact_sha256
                          AND a.gb_parse_run_id IS NOT NULL)
        ORDER BY m.archive_path
    """),
        params,
    )

    # the latest full parse (an incremental one only reports its window)
    run = conn.execute(
        text(f"""
        SELECT id, parser_name, parser_version, status, stats FROM parse_run
        WHERE {run_where}
        ORDER BY (coalesce(stats->'mode'->>'mode', 'full') = 'full') DESC, id DESC LIMIT 1
    """),
        params,
    ).first()

    activity_fits = conn.execute(
        text(f"SELECT count(*) FROM ({scope}) m WHERE m.category = 'ACTIVITY'"), params
    ).scalar_one()
    activities_linked = conn.execute(
        text(f"""
        SELECT count(DISTINCT a.id) FROM activity a
        JOIN ({scope}) m ON m.artifact_sha256 = a.raw_fit_sha256
        WHERE a.gb_parse_run_id IS NOT NULL
    """),
        params,
    ).scalar_one()

    stats = (run.stats if run else None) or {}
    return {
        "batch_id": batch_id,
        "categories": [dict(r._mapping) for r in categories],
        "activities": {
            "activity_fit_files": activity_fits,
            "db_summaries_linked_to_fit": activities_linked,
            "fit_without_db_summary": [dict(r._mapping) for r in unmatched],
        },
        "gadgetbridge_db": {
            "parse_run_id": run.id if run else None,
            "status": run.status if run else None,
            "schema_version": stats.get("schema_version"),
            "schema_known": stats.get("schema_known"),
            "empty_tables": stats.get("empty_tables"),
            "unmapped_tables": stats.get("unmapped_tables"),
            "missing_tables": stats.get("missing_tables"),
            "tables": stats.get("tables"),
            "warnings": stats.get("warnings"),
        },
    }


def _gb_set_rows(summary: dict | None) -> list[dict]:
    """Gadgetbridge's rendered set table: set_N rows with [index, category, reps, kg, seconds]."""
    rows = []
    for key, row in (summary or {}).items():
        if not key.startswith("set_") or not isinstance(row, dict) or row.get("isHeader"):
            continue
        cols = [c.get("value") for c in row.get("columns", [])]
        if len(cols) < 5:
            continue
        reps = cols[2]
        rows.append(
            {
                "index": cols[0],
                "category_label": cols[1],
                "repetitions": int(reps) if isinstance(reps, str) and reps.isdigit() else reps,
                "weight_kg": cols[3],
                "duration_s": cols[4],
            }
        )
    return sorted(rows, key=lambda r: r["index"] if isinstance(r["index"], int) else 0)


@router.get("/activity-sets")
def activity_sets(conn: Connection = CONN) -> list[dict]:
    """Compare FIT active sets with Gadgetbridge's set table, row by row.

    Durations are compared with a 1 s tolerance because Gadgetbridge truncates to whole seconds.
    Category labels are localized in Gadgetbridge and therefore not compared.
    """
    acts = conn.execute(
        text("""
        SELECT a.id, a.start_ts, a.sub_sport, a.summary,
               a.gb_parse_run_id IS NOT NULL AS has_gb, a.fit_parse_run_id IS NOT NULL AS has_fit
        FROM activity a
        WHERE EXISTS (SELECT 1 FROM activity_set s WHERE s.activity_id = a.id)
           OR a.summary ? 'sets_header'
        ORDER BY a.start_ts
    """)
    ).all()
    out = []
    for a in acts:
        fit_sets = conn.execute(
            text("""SELECT set_index, repetitions, weight_kg, duration_s, exercise_category
                    FROM activity_set WHERE activity_id = :id AND set_type = 'active'
                    ORDER BY set_index"""),
            {"id": a.id},
        ).all()
        gb_rows = _gb_set_rows(a.summary)
        mismatches = []
        for i in range(max(len(fit_sets), len(gb_rows))):
            f = fit_sets[i] if i < len(fit_sets) else None
            g = gb_rows[i] if i < len(gb_rows) else None
            if f is None or g is None:
                mismatches.append(
                    {"position": i + 1, "issue": "missing in " + ("fit" if f is None else "gadgetbridge")}
                )
                continue
            diffs = {}
            if f.repetitions != g["repetitions"]:
                diffs["repetitions"] = [f.repetitions, g["repetitions"]]
            if (
                f.weight_kg is not None
                and g["weight_kg"] is not None
                and abs(f.weight_kg - g["weight_kg"]) > 1e-6
            ):
                diffs["weight_kg"] = [f.weight_kg, g["weight_kg"]]
            if (
                f.duration_s is not None
                and g["duration_s"] is not None
                and abs(f.duration_s - g["duration_s"]) >= 1
            ):
                diffs["duration_s"] = [round(f.duration_s, 1), g["duration_s"]]
            if diffs:
                mismatches.append({"position": i + 1, "fit_set_index": f.set_index, "diffs": diffs})
        out.append(
            {
                "activity_id": a.id,
                "start_ts": a.start_ts,
                "sub_sport": a.sub_sport,
                "sources": {"fit": a.has_fit, "gadgetbridge_db": a.has_gb},
                "fit_active_sets": len(fit_sets),
                "gadgetbridge_set_rows": len(gb_rows),
                "fit_sets_without_category": sum(f.exercise_category is None for f in fit_sets),
                "mismatches": mismatches,
            }
        )
    return out
