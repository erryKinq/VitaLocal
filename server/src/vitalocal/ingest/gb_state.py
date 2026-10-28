"""Which Gadgetbridge database snapshot is applied, and how the next one is parsed.

Gadgetbridge rewrites rows in place (`ON CONFLICT REPLACE` on `WITHOUT ROWID` tables): a change
is only visible through the row's TIMESTAMP. A snapshot is therefore parsed per table from the
newest TIMESTAMP of the snapshot applied before, minus an overlap that covers late rewrites
(a night written at the morning sync, day totals revised later). Anything older than that is
repaired by the nightly full parse.

A snapshot taken before the applied one (an old export uploaded late) is not parsed at all, so
it can never overwrite newer data; its FIT files still are.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Connection, text

LINEAGE = "gadgetbridge"
# The table whose newest row tells "the snapshot reaches further" independent of clocks.
PROGRESS_TABLE = "GARMIN_ACTIVITY_SAMPLE"


@dataclass
class State:
    batch_id: int | None
    artifact_sha256: str
    as_of: datetime
    parser_version: str
    schema_version: int
    table_hwm: dict[str, int]  # raw units of each table (s or ms)
    last_full_at: datetime | None


@dataclass
class Plan:
    mode: str  # "full" or "incremental"
    reason: str
    windows: dict[str, int] | None = None  # table -> lowest TIMESTAMP read (incremental)


def load(conn: Connection) -> State | None:
    r = conn.execute(
        text("""SELECT batch_id, artifact_sha256, as_of, parser_version, schema_version, table_hwm,
                       last_full_at FROM gb_snapshot_state WHERE lineage = :l"""),
        {"l": LINEAGE},
    ).first()
    if r is None:
        return None
    return State(
        r.batch_id,
        r.artifact_sha256,
        r.as_of,
        r.parser_version,
        r.schema_version,
        dict(r.table_hwm),
        r.last_full_at,
    )


def save(
    conn: Connection,
    *,
    batch_id: int,
    sha: str,
    as_of: datetime,
    parser_version: str,
    schema_version: int,
    table_hwm: dict[str, int],
    full: bool,
) -> None:
    conn.execute(
        text("""
            INSERT INTO gb_snapshot_state (lineage, batch_id, artifact_sha256, as_of, parser_version,
                schema_version, table_hwm, last_full_at)
            VALUES (:l, :b, :sha, :as_of, :pv, :sv, CAST(:hwm AS jsonb), CASE WHEN :full THEN now() END)
            ON CONFLICT (lineage) DO UPDATE SET
                batch_id = EXCLUDED.batch_id, artifact_sha256 = EXCLUDED.artifact_sha256,
                as_of = greatest(gb_snapshot_state.as_of, EXCLUDED.as_of),
                parser_version = EXCLUDED.parser_version, schema_version = EXCLUDED.schema_version,
                table_hwm = EXCLUDED.table_hwm,
                last_full_at = CASE WHEN :full THEN now() ELSE gb_snapshot_state.last_full_at END,
                updated_at = now()
        """),
        {
            "l": LINEAGE,
            "b": batch_id,
            "sha": sha,
            "as_of": as_of,
            "pv": parser_version,
            "sv": schema_version,
            "hwm": json.dumps(table_hwm),
            "full": full,
        },
    )


def _scale(table_units: dict[str, str], table: str) -> int:
    return 1000 if table_units.get(table) == "ms" else 1


def is_older(state: State | None, as_of: datetime | None, bounds: dict[str, tuple[int, int]]) -> bool:
    """The snapshot was taken before the applied one and does not reach further: skip it.

    Both conditions are needed: a phone clock that jumped back would otherwise drop a newer
    snapshot, and a reset Gadgetbridge (less data, but newer) must still be applied.
    """
    if state is None or as_of is None or as_of >= state.as_of:
        return False
    newest = bounds.get(PROGRESS_TABLE, (None, None))[1]
    applied = state.table_hwm.get(PROGRESS_TABLE)
    return newest is None or applied is None or newest <= applied


def plan(
    state: State | None,
    *,
    parser_version: str,
    schema_version: int,
    bounds: dict[str, tuple[int, int]],
    table_units: dict[str, str],
    window: timedelta,
    incremental: bool = True,
    force_full: bool = False,
) -> Plan:
    """Full or incremental parse of a snapshot, with the window per table."""
    if force_full:
        return Plan("full", "forced")
    if not incremental:
        return Plan("full", "incremental parsing switched off")
    if state is None:
        return Plan("full", "no snapshot applied yet")
    if state.parser_version != parser_version:
        return Plan("full", f"parser {state.parser_version} -> {parser_version}")
    if state.schema_version != schema_version:
        return Plan("full", f"database schema {state.schema_version} -> {schema_version}")
    overlap_s = int(window.total_seconds())
    progress = state.table_hwm.get(PROGRESS_TABLE)
    newest = bounds.get(PROGRESS_TABLE, (None, None))[1]
    reset_to = None if progress is None else progress - overlap_s * _scale(table_units, PROGRESS_TABLE)
    if newest is not None and reset_to is not None and newest < reset_to:
        return Plan("full", "snapshot ends before the applied one (Gadgetbridge reset?)")
    # A table with its first rows (no mark yet) is read whole, from its first row on.
    windows = {
        table: max(lo, state.table_hwm[table] - overlap_s * _scale(table_units, table))
        if table in state.table_hwm
        else lo
        for table, (lo, _hi) in bounds.items()
    }
    new_tables = sorted(t for t in bounds if t not in state.table_hwm)
    reason = f"new tables read whole: {', '.join(new_tables)}" if new_tables else ""
    return Plan("incremental", reason, windows)


def table_hwm(bounds: dict[str, tuple[int, int]]) -> dict[str, int]:
    return {table: hi for table, (_lo, hi) in bounds.items()}
