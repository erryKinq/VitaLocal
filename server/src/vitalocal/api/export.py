"""Everything as one ZIP: each table as CSV (complete, for a move or own analyses) plus a few
ready-to-read CSVs (food diary, body values, strength sets, daily values).

Raw uploads and import bookkeeping are left out (they are the source files, not the data);
stored secrets (the AI API key) as well.
"""

import csv
import io
import json
import tempfile
import zipfile
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import Connection, text
from starlette.background import BackgroundTask

from vitalocal import __version__
from vitalocal.api.days import _daily_values, _local_midnight, _tz
from vitalocal.db import CONN
from vitalocal.metrics import METRICS

router = APIRouter(prefix="/api/v1", tags=["export"])

SKIP = {
    "alembic_version",
    "raw_artifact",
    "artifact_member",
    "ingest_batch",
    "parse_run",
    "gb_snapshot_state",
}
# table → SELECT replacing the plain table (secrets out)
SELECTS = {
    "app_setting": "SELECT key, CASE WHEN key = 'ai' THEN value - 'api_key' ELSE value END AS value, "
    "updated_at FROM app_setting"
}

READABLE = {
    "food_diary.csv": """SELECT day, meal, name, brand, amount, unit, quantity,
            (nutrients->>'calories')::float AS kcal, (nutrients->>'proteins')::float AS protein_g,
            (nutrients->>'carbohydrates')::float AS carbs_g, (nutrients->>'fat')::float AS fat_g, source
        FROM nutrition_entry ORDER BY day, meal_index, logged_at""",
    "body.csv": """SELECT ts, metric, value, source FROM (
            SELECT ts, metric, value, source FROM body_entry
            UNION ALL
            SELECT s.ts, s.metric, s.value, d.source FROM sample s JOIN device d ON d.id = s.device_id
                      WHERE s.metric IN ('weight', 'body_fat')) b ORDER BY ts""",
    "strength_sets.csv": """SELECT a.start_ts, coalesce(ts.title, a.name) AS workout, e.name AS exercise,
            t.repetitions, t.weight_kg, t.duration_s, t.warmup
        FROM training_set t JOIN activity a ON a.id = t.activity_id
        LEFT JOIN training_session ts ON ts.activity_id = a.id LEFT JOIN exercise e ON e.id = t.exercise_id
        ORDER BY a.start_ts, t.position""",
}


def _copy(conn: Connection, sql: str, out) -> int:
    """COPY … TO STDOUT as CSV with header into a binary file; returns the bytes written."""
    raw = conn.connection.dbapi_connection
    n = 0
    with raw.cursor() as cur, cur.copy(f"COPY ({sql}) TO STDOUT WITH (FORMAT csv, HEADER)") as copy:
        for chunk in copy:
            out.write(chunk)
            n += len(chunk)
    return n


def _daily_csv(conn: Connection) -> bytes:
    first = conn.execute(text("SELECT min(ts) FROM sample")).scalar()
    buf = io.StringIO()
    w = csv.writer(buf)
    keys = list(METRICS)
    w.writerow(["day", *keys])
    if first is None:
        return buf.getvalue().encode()
    tz = _tz()
    start = first.astimezone(tz).date()
    end = datetime.now(tz).date()
    values = _daily_values(
        conn, keys, _local_midnight(start, tz), _local_midnight(end + timedelta(days=1), tz)
    )
    d = start
    while d <= end:
        row = [values.get(k, {}).get(d.isoformat(), {}).get("value") for k in keys]
        if any(v is not None for v in row):
            w.writerow([d.isoformat(), *row])
        d += timedelta(days=1)
    return buf.getvalue().encode()


@router.get("/export")
def export_all(conn: Connection = CONN) -> FileResponse:
    """A ZIP of all data: `tables/<table>.csv` for every table, readable CSVs and `manifest.json`."""
    tables = [
        t
        for t in conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        ).scalars()
        if t not in SKIP
    ]
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)  # noqa: SIM115 - removed after sending
    counts = {}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            with z.open(f"tables/{t}.csv", "w") as f:
                _copy(conn, SELECTS.get(t, f"SELECT * FROM {t}"), f)
            counts[t] = conn.execute(text(f"SELECT count(*) FROM {t}")).scalar()
        for name, sql in READABLE.items():
            with z.open(name, "w") as f:
                _copy(conn, sql, f)
        z.writestr("daily_values.csv", _daily_csv(conn))
        z.writestr(
            "manifest.json",
            json.dumps(
                {
                    "app": "VitaLocal",
                    "version": __version__,
                    "exported_at": datetime.now(UTC).isoformat(),
                    "timezone": _tz().key,
                    "rows": counts,
                },
                indent=1,
            ),
        )
    tmp.close()
    name = f"vitalocal-export-{date.today().isoformat()}.zip"
    return FileResponse(
        tmp.name, media_type="application/zip", filename=name, background=BackgroundTask(_unlink, tmp.name)
    )


def _unlink(path: str) -> None:
    import os

    os.unlink(path)
