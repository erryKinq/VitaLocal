"""Raw store retention: drop bytes of old exports once a newer export has superseded them.

Gadgetbridge and Waistline exports are full snapshots, so an older export adds nothing a newer
one lacks, except files that later vanished from the phone. The rule is therefore conservative:

- kept batches: received within the retention window, still pending/processing, or the latest
  successful batch of their source; of the live Gadgetbridge databases (one every few minutes)
  only the newest two and, within the window, the last successful one of each local day
- a batch expires only when a newer successful batch of the same source exists
- an expired batch loses its outer file (ZIP/JSON/CSV) and those member files whose archive path
  a kept batch also contains (e.g. `database/Gadgetbridge`), i.e. files with a newer version
- bytes still referenced by a kept batch, an activity, a lab report/import or the applied
  Gadgetbridge database snapshot are never touched; files that exist only in old exports (e.g. a
  FIT file deleted on the phone) stay

Only the bytes go. `raw_artifact` rows, parse runs and all normalized data remain; the pruned batch
just can no longer be reprocessed. Re-uploading identical bytes restores the file.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, Engine, text

from vitalocal.config import get_settings
from vitalocal.rawstore import RawStore

log = logging.getLogger(__name__)

# pg advisory lock: a prune holds it exclusively while it marks and deletes files; everything that
# stores raw bytes holds it shared, so a file is never deleted while it is being stored again
LOCK_KEY = 0x7654_0001
_OK = "('completed', 'completed_with_errors')"
LIVE_SOURCE = "gadgetbridge_db"

_CANDIDATES = f"""
WITH latest_ok AS (
    SELECT DISTINCT ON (source) id FROM ingest_batch
    WHERE status IN {_OK} ORDER BY source, received_at DESC, id DESC
), live_keep AS (
    -- live databases arrive every few minutes: within the window only the newest two (the
    -- phone's next delta builds on them) and the last good one of each local day stay
    (SELECT id FROM ingest_batch WHERE source = '{LIVE_SOURCE}' ORDER BY received_at DESC, id DESC LIMIT 2)
    UNION
    (SELECT DISTINCT ON ((received_at AT TIME ZONE :tz)::date) id FROM ingest_batch
     WHERE source = '{LIVE_SOURCE}' AND status IN {_OK} AND received_at >= :cutoff
     ORDER BY (received_at AT TIME ZONE :tz)::date, received_at DESC, id DESC)
), kept AS (
    SELECT id, source FROM ingest_batch
    WHERE (received_at >= :cutoff AND source <> '{LIVE_SOURCE}') OR status IN ('pending', 'processing')
       OR id IN (SELECT id FROM latest_ok) OR id IN (SELECT id FROM live_keep)
), expired AS (
    SELECT b.id, b.source, b.outer_artifact_sha256 FROM ingest_batch b
    WHERE b.id NOT IN (SELECT id FROM kept)
      AND EXISTS (SELECT 1 FROM ingest_batch n WHERE n.source = b.source AND n.status IN {_OK}
                  AND n.received_at > b.received_at)
), kept_sha AS (
    SELECT b.outer_artifact_sha256 AS sha FROM ingest_batch b JOIN kept k ON k.id = b.id
    UNION SELECT m.artifact_sha256 FROM artifact_member m JOIN kept k ON k.id = m.batch_id
    UNION SELECT raw_fit_sha256 FROM activity WHERE raw_fit_sha256 IS NOT NULL
    UNION SELECT artifact_sha256 FROM lab_report WHERE artifact_sha256 IS NOT NULL
    UNION SELECT artifact_sha256 FROM lab_import
    UNION SELECT artifact_sha256 FROM gb_snapshot_state  -- the applied database snapshot
), kept_path AS (
    SELECT DISTINCT k.source, m.archive_path FROM artifact_member m JOIN kept k ON k.id = m.batch_id
), candidate AS (
    SELECT outer_artifact_sha256 AS sha FROM expired
    UNION
    SELECT m.artifact_sha256 FROM artifact_member m JOIN expired e ON e.id = m.batch_id
    WHERE EXISTS (SELECT 1 FROM kept_path p WHERE p.source = e.source AND p.archive_path = m.archive_path)
)
SELECT r.sha256, r.size FROM raw_artifact r JOIN candidate c ON c.sha = r.sha256
WHERE r.pruned_at IS NULL AND NOT EXISTS (SELECT 1 FROM kept_sha k WHERE k.sha = r.sha256)
ORDER BY r.sha256
"""


def _candidates(conn: Connection, retention_days: int) -> list:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    return conn.execute(text(_CANDIDATES), {"cutoff": cutoff, "tz": get_settings().timezone}).all()


def prune(engine: Engine, store: RawStore, retention_days: int, dry_run: bool = False) -> dict:
    """Prune superseded raw bytes. Returns what was (or, with dry_run, would be) removed."""
    result = {"retention_days": retention_days, "dry_run": dry_run, "artifacts": 0, "bytes": 0, "batches": 0}
    if retention_days <= 0:
        return {**result, "skipped": "retention disabled"}
    if dry_run:
        with engine.connect() as conn:
            return _prune(conn, store, retention_days, result, dry_run=True)
    with engine.connect() as lock:
        got = lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
        lock.commit()
        if not got:
            return {**result, "skipped": "prune already running or files being stored"}
        try:
            with engine.begin() as conn:
                result = _prune(conn, store, retention_days, result, dry_run=False)
            # Files go only after the marks are committed: a failed unlink leaves a harmless extra file.
            for sha in result.pop("_shas", []):
                try:
                    store.path_for(sha).unlink(missing_ok=True)
                except OSError as e:
                    log.warning("could not delete raw artifact %s: %s", sha, e)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            lock.commit()
    if result.get("artifacts") and not result.get("skipped"):
        log.info("pruned %d raw artifacts (%d bytes)", result["artifacts"], result["bytes"])
    return result


def _prune(conn: Connection, store: RawStore, retention_days: int, result: dict, dry_run: bool) -> dict:
    # Members of a running import are not committed yet and would look unreferenced.
    busy = conn.execute(
        text("SELECT count(*) FROM ingest_batch WHERE status IN ('pending', 'processing')")
    ).scalar()
    if busy and not dry_run:
        return {**result, "skipped": "import in progress"}
    rows = _candidates(conn, retention_days)
    shas = [r.sha256 for r in rows]
    result = {**result, "artifacts": len(rows), "bytes": sum(r.size for r in rows)}
    if dry_run or not shas:
        result["batches"] = (
            conn.execute(
                text("SELECT count(*) FROM ingest_batch WHERE outer_artifact_sha256 = ANY(:s)"),
                {"s": shas},
            ).scalar()
            if shas
            else 0
        )
        return result
    conn.execute(text("UPDATE raw_artifact SET pruned_at = now() WHERE sha256 = ANY(:s)"), {"s": shas})
    result["batches"] = conn.execute(
        text("""UPDATE ingest_batch SET raw_pruned_at = now()
                WHERE raw_pruned_at IS NULL AND outer_artifact_sha256 = ANY(:s)"""),
        {"s": shas},
    ).rowcount
    return {**result, "_shas": shas}
