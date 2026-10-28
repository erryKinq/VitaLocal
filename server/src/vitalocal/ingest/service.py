"""Ingest pipeline: preserve raw bytes, inventory the archive, run versioned parsers."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import sqlite3
import time
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, text

from vitalocal.api.goals import sync_goals
from vitalocal.config import get_settings
from vitalocal.ingest import (
    daily_csv,
    dbdelta,
    fit_catalog,
    fit_parser,
    gb_sqlite,
    gb_state,
    manifest,
    retention,
    waistline,
    waistline_csv,
    workout_csv,
)
from vitalocal.ingest.gadgetbridge_zip import classify, parse_comment
from vitalocal.rawstore import RawStore

log = logging.getLogger(__name__)

SOURCE_GADGETBRIDGE = "gadgetbridge_export"
SOURCE_GB_DB = "gadgetbridge_db"  # the database alone, uploaded live (whole or as changed blocks)
SOURCE_GB_FILES = "gadgetbridge_files"  # files of a ZIP export or the FIT export, by manifest
GB_SOURCES = (SOURCE_GADGETBRIDGE, SOURCE_GB_FILES)
FIT_EXPORT_DIR = "fit_export"  # manifest paths of Gadgetbridge's FIT export (one file per workout)
SOURCE_WAISTLINE = "waistline_export"
SOURCE_WAISTLINE_CSV = "waistline_diary_csv"
SOURCE_DAILY_CSV = "daily_csv"  # daily totals from other apps (MyFitnessPal, Cronometer, weight lists)
SOURCE_WORKOUT_CSV = "workout_csv"  # strength workouts from Strong or Hevy
SOURCE_HEALTH_CONNECT = "health_connect"  # records the Android app read from Health Connect
# bytes read to recognize a file (CSV headers of other apps are long)
DETECT_BYTES = 4096
_STAGE_CHUNK = 20_000
# pg advisory lock held while a batch is processed (worker, CLI): one import at a time
RUN_LOCK = 0x7654_0002
_TABLE_UNITS = {spec.table: spec.ts_unit for spec in gb_sqlite.TABLE_SPECS}


class RawPrunedError(LookupError):
    """The batch's original export was removed by retention and cannot be replayed."""


def _now() -> datetime:
    return datetime.now(UTC)


def store_artifact(conn: Connection, store: RawStore, src: BinaryIO, media_type: str | None) -> str:
    # Shared with other writers, exclusive for a prune that is deleting files right now.
    conn.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": retention.LOCK_KEY})
    sha, size = store.put_stream(src)
    conn.execute(
        text("""
            INSERT INTO raw_artifact (sha256, size, media_type, storage_path)
            VALUES (:sha, :size, :mt, :path)
            ON CONFLICT (sha256) DO UPDATE SET pruned_at = NULL
                WHERE raw_artifact.pruned_at IS NOT NULL  -- bytes restored after retention
        """),
        {
            "sha": sha,
            "size": size,
            "mt": media_type,
            "path": str(store.path_for(sha).relative_to(store.root)),
        },
    )
    return sha


def store_artifact_bytes(conn: Connection, store: RawStore, data: bytes, media_type: str | None) -> str:
    """Like `store_artifact`, but bytes already stored (most files of a new export) are not
    written and synced again."""
    sha = hashlib.sha256(data).hexdigest()
    conn.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": retention.LOCK_KEY})
    stored = conn.execute(
        text("SELECT pruned_at IS NULL FROM raw_artifact WHERE sha256 = :s"), {"s": sha}
    ).scalar()
    if stored and store.exists(sha):
        return sha
    return store_artifact(conn, store, io.BytesIO(data), media_type)


MEDIA_TYPES = {
    SOURCE_GADGETBRIDGE: "application/zip",
    SOURCE_GB_DB: "application/x-sqlite3",
    SOURCE_GB_FILES: "application/vnd.vitalocal.manifest+json",
    SOURCE_WAISTLINE: "application/json",
    SOURCE_WAISTLINE_CSV: "text/csv",
    SOURCE_DAILY_CSV: "text/csv",
    SOURCE_WORKOUT_CSV: "text/csv",
    SOURCE_HEALTH_CONNECT: "application/json",
}


def register_upload(
    engine: Engine,
    store: RawStore,
    src: BinaryIO,
    source: str = SOURCE_GADGETBRIDGE,
    client: dict | None = None,
) -> tuple[int, bool]:
    """Store an uploaded export and create its batch. Returns (batch_id, created).

    `client` (uploader details such as device and app version) lands in `client_meta.client`.
    """
    with engine.begin() as conn:
        sha = store_artifact(conn, store, src, MEDIA_TYPES[source])
        existing = conn.execute(
            text("""UPDATE ingest_batch SET raw_pruned_at = NULL
                    WHERE source = :src AND outer_artifact_sha256 = :sha RETURNING id"""),
            {"src": source, "sha": sha},
        ).scalar()
        if existing is not None:
            return existing, False
        batch_id = conn.execute(
            text("""
                INSERT INTO ingest_batch (source, outer_artifact_sha256, status, client_meta, as_of)
                VALUES (:src, :sha, 'pending', CAST(:meta AS jsonb), now()) RETURNING id
            """),
            {"src": source, "sha": sha, "meta": json.dumps({"client": client} if client else {})},
        ).scalar_one()
        return batch_id, True


def register_gb_db(
    engine: Engine,
    store: RawStore,
    meta: dict,
    data: BinaryIO,
    *,
    max_bytes: int,
    received_bytes: int | None = None,
) -> dict:
    """Store a live Gadgetbridge database (gzip: the whole file or a VLDD delta on a stored base)
    and queue it. The SHA-256 of the result must be the one the phone computed.

    Raises `dbdelta.DeltaError` (`base_unavailable`: send the whole file; others: invalid upload).
    """
    sha = meta["sha256"]
    with engine.begin() as conn:
        # also keeps a prune from deleting the base while the new file is built from it
        conn.execute(text("SELECT pg_advisory_xact_lock_shared(:k)"), {"k": retention.LOCK_KEY})
        existing = conn.execute(
            text("""SELECT b.id, r.pruned_at IS NULL AS stored FROM ingest_batch b
                    JOIN raw_artifact r ON r.sha256 = b.outer_artifact_sha256
                    WHERE b.source = :src AND b.outer_artifact_sha256 = :sha"""),
            {"src": SOURCE_GB_DB, "sha": sha},
        ).first()
        if existing is not None and existing.stored and store.exists(sha):
            return {"batch_id": existing.id, "created": False, "source": SOURCE_GB_DB, "sha256": sha}

        store.root.mkdir(parents=True, exist_ok=True)
        stream = gzip.GzipFile(fileobj=data, mode="rb")
        if meta["encoding"] == "full":
            tmp, got, size = dbdelta.write_full(stream, store.root, max_bytes)
        else:
            base = meta["base_sha256"]
            stored = conn.execute(
                text("SELECT pruned_at IS NULL FROM raw_artifact WHERE sha256 = :s"), {"s": base}
            ).scalar()
            if not stored or not store.exists(base):
                raise dbdelta.DeltaError("base_unavailable", "the base database is not stored (any more)")
            tmp, got, size = dbdelta.apply(store.path_for(base), stream, store.root, max_bytes)
        try:
            if got != sha or (meta.get("size") is not None and size != meta["size"]):
                raise dbdelta.DeltaError("sha_mismatch", f"the result has SHA-256 {got} and {size} bytes")
            dbdelta.check_sqlite(tmp)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        store.adopt(tmp, sha)
        conn.execute(
            text("""
                INSERT INTO raw_artifact (sha256, size, media_type, storage_path)
                VALUES (:sha, :size, :mt, :path)
                ON CONFLICT (sha256) DO UPDATE SET pruned_at = NULL WHERE raw_artifact.pruned_at IS NOT NULL
            """),
            {
                "sha": sha,
                "size": size,
                "mt": MEDIA_TYPES[SOURCE_GB_DB],
                "path": str(store.path_for(sha).relative_to(store.root)),
            },
        )
        transfer = {"encoding": meta["encoding"], "bytes": received_bytes}
        if existing is not None:  # the same snapshot again after its bytes were pruned
            conn.execute(
                text("UPDATE ingest_batch SET raw_pruned_at = NULL WHERE id = :id"), {"id": existing.id}
            )
            return {"batch_id": existing.id, "created": False, "source": SOURCE_GB_DB, "sha256": sha}
        client_meta = {
            "client": meta.get("client"),
            "gadgetbridge": meta.get("gadgetbridge"),
            "exported_at": meta.get("exported_at"),
            "transfer": transfer,
        }
        batch_id = conn.execute(
            text("""
                INSERT INTO ingest_batch (source, outer_artifact_sha256, status, client_meta, as_of)
                VALUES (:src, :sha, 'pending', CAST(:meta AS jsonb),
                        -- the phone's clock decides, but never more than a few minutes ahead
                        least(coalesce(CAST(:exp AS timestamptz), now()), now() + interval '5 minutes'))
                RETURNING id
            """),
            {
                "src": SOURCE_GB_DB,
                "sha": sha,
                "meta": json.dumps({k: v for k, v in client_meta.items() if v is not None}),
                "exp": meta.get("exported_at"),
            },
        ).scalar_one()
    return {
        "batch_id": batch_id,
        "created": True,
        "source": SOURCE_GB_DB,
        "sha256": sha,
        "stored": "delta" if meta["encoding"] != "full" else "full",
        "bytes_received": received_bytes,
    }


def missing_artifacts(conn: Connection, store: RawStore, shas: list[str]) -> list[str]:
    """Of these SHA-256s, the ones whose bytes the server does not have."""
    stored = set(
        conn.execute(
            text("SELECT sha256 FROM raw_artifact WHERE sha256 = ANY(:s) AND pruned_at IS NULL"), {"s": shas}
        ).scalars()
    )
    return [s for s in dict.fromkeys(shas) if s not in stored or not store.exists(s)]


def register_files(
    engine: Engine, store: RawStore, m: manifest.Manifest, blobs: dict[str, BinaryIO], client: dict | None
) -> tuple[int, bool]:
    """Store the uploaded blobs and queue the file set. Raises `MissingBlobs` when files are
    neither uploaded nor stored."""
    by_sha = {e.sha256: e for e in m.entries}
    with engine.begin() as conn:
        for sha, src in blobs.items():
            if sha not in by_sha:
                raise manifest.ManifestError(f"uploaded file {sha} is not in the manifest")
            got = store_artifact(conn, store, src, _media_type(by_sha[sha].path))
            if got != sha:
                raise manifest.ManifestError(f"{by_sha[sha].path}: content has SHA-256 {got}")
        missing = missing_artifacts(conn, store, list(by_sha))
        if missing:
            raise MissingBlobs(missing)
        outer = store_artifact(conn, store, io.BytesIO(m.canonical()), MEDIA_TYPES[SOURCE_GB_FILES])
        existing = conn.execute(
            text("""UPDATE ingest_batch SET raw_pruned_at = NULL
                    WHERE source = :src AND outer_artifact_sha256 = :sha RETURNING id"""),
            {"src": SOURCE_GB_FILES, "sha": outer},
        ).scalar()
        if existing is not None:
            return existing, False
        meta = {"origin": m.origin, "exported_at": m.exported_at, "files": len(m.entries), "client": client}
        batch_id = conn.execute(
            text("""
                INSERT INTO ingest_batch (source, outer_artifact_sha256, status, client_meta, as_of)
                VALUES (:src, :sha, 'pending', CAST(:meta AS jsonb),
                        least(coalesce(CAST(:exp AS timestamptz), now()), now() + interval '5 minutes'))
                RETURNING id
            """),
            {
                "src": SOURCE_GB_FILES,
                "sha": outer,
                "meta": json.dumps({k: v for k, v in meta.items() if v is not None}),
                "exp": _exported_at({"exported_at": m.exported_at}) if m.exported_at else None,
            },
        ).scalar_one()
        return batch_id, True


class MissingBlobs(LookupError):
    def __init__(self, missing: list[str]):
        super().__init__(f"{len(missing)} files are neither uploaded nor stored")
        self.missing = missing


def _process_files(engine: Engine, store: RawStore, batch_id: int, outer_sha: str, force: bool) -> dict:
    """A file set: only files (path + content) no earlier Gadgetbridge batch had become members;
    their FIT files are parsed. The database comes by the live upload."""
    m = manifest.parse(store.path_for(outer_sha).read_bytes())
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM artifact_member WHERE batch_id = :id"), {"id": batch_id})
        known = set(
            conn.execute(
                text("""SELECT m.archive_path, m.artifact_sha256 FROM artifact_member m
                        JOIN ingest_batch b ON b.id = m.batch_id
                        WHERE b.source = ANY(:src) AND m.nested_in IS NULL
                          AND m.artifact_sha256 = ANY(:shas)"""),
                {"src": list(GB_SOURCES), "shas": [e.sha256 for e in m.entries]},
            ).all()
        )
        devices: dict[str, int] = {}
        for e in m.entries:
            if e.path == "database/Gadgetbridge" or (e.path, e.sha256) in known:
                continue
            data = store.path_for(e.sha256).read_bytes()
            meta: dict = {}
            if fit_catalog.is_fit(data):
                meta["fit"] = _known_catalog(conn, e.sha256) or fit_catalog.catalog_fit(data)
            category, device_id = _classify_file(conn, e.path, meta, devices)
            member_id = _insert_member(conn, batch_id, e.sha256, e.path, category, device_id, meta)
            counts[category] = counts.get(category, 0) + 1
            if e.path.lower().endswith(".zip"):
                _inventory_nested(conn, store, batch_id, member_id, e.path, data, category, device_id)
        # activities from the live database find their FIT file by name
        conn.execute(
            text("""UPDATE activity a SET raw_fit_sha256 = m.artifact_sha256 FROM artifact_member m
                    WHERE m.batch_id = :b AND m.category = 'ACTIVITY' AND a.raw_fit_sha256 IS NULL
                      AND a.raw_details_name = regexp_replace(m.archive_path, '^.*/', '')"""),
            {"b": batch_id},
        )
    stats: dict = {"members": counts, "files_listed": len(m.entries), "origin": m.origin}
    fit = run_fit_parsers(engine, store, batch_id, force=force)
    stats["fit_parse"] = fit
    if fit["failed"]:
        stats["parse_errors"] = [f"{f['archive_path']}: {f['error']}" for f in fit["failed"]]
    return stats


def _classify_file(
    conn: Connection, path: str, meta: dict, devices: dict[str, int]
) -> tuple[str, int | None]:
    """Category and device of a listed file. Files of the FIT export carry neither in their path:
    a workout file (FIT type activity) is an ACTIVITY of the watch that delivered the last ones."""
    if path.startswith(f"{FIT_EXPORT_DIR}/"):
        if (meta.get("fit") or {}).get("file_id", {}).get("type") != "activity":
            return "fit_export", None
        device = conn.execute(
            text("""SELECT device_id FROM artifact_member
                    WHERE category = 'ACTIVITY' AND device_id IS NOT NULL ORDER BY id DESC LIMIT 1""")
        ).scalar()
        return "ACTIVITY", device
    mi = classify(path)
    device_id = None
    if mi.device_identifier:
        device_id = devices.get(mi.device_identifier)
        if device_id is None:
            device_id = devices[mi.device_identifier] = _upsert_device(
                conn, gb_sqlite.device_source_key(mi.device_identifier)
            )
    return mi.category, device_id


def files_wanted(conn: Connection, now: datetime | None = None) -> dict:
    """Should the phone send files: a recent workout without its FIT file, a night without the
    SLEEP file, or (by day) no monitoring file for hours."""
    now = now or _now()
    reasons = []
    for r in conn.execute(
        text("""SELECT raw_details_name, start_ts FROM activity
                WHERE raw_details_name IS NOT NULL AND raw_fit_sha256 IS NULL AND start_ts > :since
                ORDER BY start_ts DESC LIMIT 5"""),
        {"since": now - timedelta(days=7)},
    ):
        reasons.append({"kind": "activity_fit", "name": r.raw_details_name, "start": r.start_ts.isoformat()})
    night = conn.execute(
        text("""SELECT max(ts) FROM sample WHERE metric = 'sleep_stage_code' AND ts > :since"""),
        {"since": now - timedelta(hours=36)},
    ).scalar()
    if night is not None:
        has_start = conn.execute(
            text("""SELECT 1 FROM sample WHERE metric = 'sleep_in_bed' AND ts BETWEEN :a AND :b LIMIT 1"""),
            {"a": night - timedelta(hours=16), "b": night},
        ).first()
        if has_start is None:
            reasons.append({"kind": "sleep_fit", "night_end": night.isoformat()})
    local = now.astimezone(ZoneInfo(get_settings().timezone))
    if 8 <= local.hour < 22:
        last = conn.execute(
            text("""SELECT max(b.received_at) FROM ingest_batch b
                    WHERE b.source = ANY(:src) AND b.status IN ('completed', 'completed_with_errors')
                      AND EXISTS (SELECT 1 FROM artifact_member m
                                  WHERE m.batch_id = b.id AND m.category = 'MONITOR')"""),
            {"src": list(GB_SOURCES)},
        ).scalar()
        if last is None or now - last > timedelta(hours=6):
            reasons.append({"kind": "monitor", "since": last.isoformat() if last else None})
    return {"wanted": bool(reasons), "reasons": reasons}


def process_batch(
    engine: Engine, store: RawStore, batch_id: int, force: bool | None = None, prune: bool = True
) -> None:
    """Run the full pipeline for a batch. Safe to re-run (reprocess after parser upgrades).

    One batch at a time (advisory lock, also against the CLI). `force` parses files again that
    were parsed with the same parser version before (default: the batch's `force_reparse`).
    """
    with engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": RUN_LOCK})
        lock.commit()
        try:
            _process_locked(engine, store, batch_id, force)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": RUN_LOCK})
            lock.commit()
    if prune:
        # A newer snapshot is in: older exports of this source may now be superseded.
        try:
            retention.prune(engine, store, get_settings().raw_retention_days)
        except Exception:
            log.exception("raw store retention failed")


def _process_locked(engine: Engine, store: RawStore, batch_id: int, force: bool | None) -> None:
    with engine.begin() as conn:
        row = conn.execute(
            text("""
                UPDATE ingest_batch SET status = 'processing', error = NULL, finished_at = NULL
                WHERE id = :id AND raw_pruned_at IS NULL
                RETURNING outer_artifact_sha256, source, force_reparse
            """),
            {"id": batch_id},
        ).first()
    if row is None:
        raise RawPrunedError(f"batch {batch_id} not found or its raw export was pruned")
    outer_sha, source, queued_force = row
    force = queued_force if force is None else force
    started = time.monotonic()
    try:
        if source in (SOURCE_WAISTLINE, SOURCE_WAISTLINE_CSV):
            with engine.connect() as conn:
                refuse_retired_waistline(conn)
        if source == SOURCE_WAISTLINE:
            stats = _process_waistline(engine, store, batch_id, outer_sha)
        elif source == SOURCE_WAISTLINE_CSV:
            stats = _process_waistline_csv(engine, store, batch_id, outer_sha)
        elif source in (SOURCE_DAILY_CSV, SOURCE_WORKOUT_CSV, SOURCE_HEALTH_CONNECT):
            from vitalocal.ingest import app_imports  # builds on this module

            process = {
                SOURCE_DAILY_CSV: app_imports.process_daily,
                SOURCE_WORKOUT_CSV: app_imports.process_workouts,
                SOURCE_HEALTH_CONNECT: app_imports.process_health_connect,
            }[source]
            stats = process(engine, store, batch_id, outer_sha)
        elif source == SOURCE_GB_DB:
            stats = _process_gb_db(engine, store, batch_id, outer_sha, force)
        elif source == SOURCE_GB_FILES:
            stats = _process_files(engine, store, batch_id, outer_sha, force)
        else:
            stats = _process(engine, store, batch_id, outer_sha, force)
    except Exception as e:
        log.exception("batch %s failed", batch_id)
        with engine.begin() as conn:
            conn.execute(
                text("""UPDATE ingest_batch SET status = 'failed', error = :err, finished_at = now(),
                        force_reparse = false WHERE id = :id"""),
                {"id": batch_id, "err": f"{type(e).__name__}: {e}"},
            )
        return
    stats["processing_s"] = round(time.monotonic() - started, 2)
    with engine.begin() as conn:
        conn.execute(
            text("""UPDATE ingest_batch SET status = :status, stats = CAST(:stats AS jsonb),
                    finished_at = now(), force_reparse = false WHERE id = :id"""),
            {
                "id": batch_id,
                "stats": json.dumps(stats),
                "status": "completed_with_errors" if stats.get("parse_errors") else "completed",
            },
        )


def reparse_latest_snapshot(engine: Engine, store: RawStore) -> dict | None:
    """Parse the applied Gadgetbridge database snapshot in full (nightly, or `cli reparse --gb-full`).

    Repairs what incremental parses cannot see (rows changed or deleted before their window);
    the result is kept with the snapshot's batch.
    """
    with engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(:k)"), {"k": RUN_LOCK})
        lock.commit()
        try:
            with engine.connect() as conn:
                state = gb_state.load(conn)
            if state is None or state.batch_id is None or not store.exists(state.artifact_sha256):
                return None
            run = run_gb_sqlite(
                engine, store, state.batch_id, state.artifact_sha256, as_of=state.as_of, force_full=True
            )
            summary = {
                "at": _now().isoformat(),
                "parse_run_id": run.get("parse_run_id"),
                "status": run["status"],
                "drift": (run.get("samples") or {}).get("drift"),
            }
            with engine.begin() as conn:
                conn.execute(
                    text("""UPDATE ingest_batch SET stats = coalesce(stats, '{}') || CAST(:s AS jsonb)
                            WHERE id = :id"""),
                    {"id": state.batch_id, "s": json.dumps({"full_reparse": summary})},
                )
            drift = summary["drift"] or {}
            if drift.get("changed") or drift.get("retracted"):
                log.warning("full parse found changes older than the incremental window: %s", drift)
            return summary
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": RUN_LOCK})
            lock.commit()


def _process_gb_db(engine: Engine, store: RawStore, batch_id: int, sha: str, force: bool) -> dict:
    """A live database snapshot: integrity check, then the (incremental) database parse."""
    with engine.begin() as conn:
        as_of = conn.execute(
            text("SELECT as_of FROM ingest_batch WHERE id = :id"), {"id": batch_id}
        ).scalar_one()
        conn.execute(text("DELETE FROM artifact_member WHERE batch_id = :id"), {"id": batch_id})
        _insert_member(conn, batch_id, sha, "database/Gadgetbridge", "gb_database", None, {})
    path = store.path_for(sha)
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        check = con.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()
    if check != "ok":
        raise ValueError(f"database integrity check failed: {check}")
    run = run_gb_sqlite(engine, store, batch_id, sha, as_of=as_of, force_full=force)
    stats: dict = {"members": {"gb_database": 1}, "gb_sqlite_parse_run": run}
    if run["status"] not in ("completed", "skipped"):
        stats["parse_errors"] = [run.get("error")]
    return stats


def _exported_at(client_meta: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(client_meta["exported_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return None


def _process(engine: Engine, store: RawStore, batch_id: int, outer_sha: str, force: bool = False) -> dict:
    with zipfile.ZipFile(store.path_for(outer_sha)) as zf:
        client_meta = parse_comment(zf.comment)
        with engine.begin() as conn:
            as_of = conn.execute(
                text("""UPDATE ingest_batch SET client_meta = client_meta || CAST(:m AS jsonb),
                               as_of = coalesce(CAST(:at AS timestamptz), as_of, received_at)
                        WHERE id = :id RETURNING as_of"""),
                {"id": batch_id, "m": json.dumps(client_meta), "at": _exported_at(client_meta)},
            ).scalar_one()
            conn.execute(text("DELETE FROM artifact_member WHERE batch_id = :id"), {"id": batch_id})
            members, db_sha = _inventory(conn, store, batch_id, zf)

    stats: dict = {"members": members}
    if db_sha is None:
        stats["parse_errors"] = ["no database/Gadgetbridge in archive"]
        return stats
    run = run_gb_sqlite(engine, store, batch_id, db_sha, as_of=as_of, force_full=force)
    stats["gb_sqlite_parse_run"] = run
    errors = [run.get("error")] if run["status"] not in ("completed", "skipped") else []

    # FIT after the DB: sessions enrich (or create) activities keyed by device + start time.
    fit = run_fit_parsers(engine, store, batch_id, force=force)
    stats["fit_parse"] = fit
    errors += [f"{f['archive_path']}: {f['error']}" for f in fit["failed"]]
    if errors:
        stats["parse_errors"] = errors
    return stats


def _inventory(
    conn: Connection, store: RawStore, batch_id: int, zf: zipfile.ZipFile
) -> tuple[dict, str | None]:
    counts: dict[str, int] = {}
    db_sha = None
    devices: dict[str, int] = {}
    for info in zf.infolist():
        if info.is_dir():
            continue
        mi = classify(info.filename)
        data = zf.read(info)
        sha = store_artifact_bytes(conn, store, data, _media_type(info.filename))
        meta: dict = {"zip_mtime": datetime(*info.date_time).isoformat()}
        if fit_catalog.is_fit(data):
            meta["fit"] = _known_catalog(conn, sha) or fit_catalog.catalog_fit(data)
        device_id = None
        if mi.device_identifier:
            device_id = devices.get(mi.device_identifier)
            if device_id is None:
                device_id = devices[mi.device_identifier] = _upsert_device(
                    conn, gb_sqlite.device_source_key(mi.device_identifier)
                )
        member_id = _insert_member(conn, batch_id, sha, info.filename, mi.category, device_id, meta)
        counts[mi.category] = counts.get(mi.category, 0) + 1
        if mi.category == "gb_database":
            db_sha = sha
        elif info.filename.lower().endswith(".zip"):
            _inventory_nested(conn, store, batch_id, member_id, info.filename, data, mi.category, device_id)
    return counts, db_sha


def _inventory_nested(conn, store, batch_id, parent_id, parent_path, data, category, device_id):
    try:
        nested = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        conn.execute(
            text("UPDATE artifact_member SET meta = meta || CAST(:m AS jsonb) WHERE id = :id"),
            {"id": parent_id, "m": json.dumps({"nested_error": str(e)})},
        )
        return
    with nested:
        for info in nested.infolist():
            if info.is_dir():
                continue
            inner = nested.read(info)
            sha = store_artifact_bytes(conn, store, inner, _media_type(info.filename))
            meta = (
                {"fit": _known_catalog(conn, sha) or fit_catalog.catalog_fit(inner)}
                if fit_catalog.is_fit(inner)
                else {}
            )
            _insert_member(
                conn,
                batch_id,
                sha,
                f"{parent_path}!/{info.filename}",
                category,
                device_id,
                meta,
                nested_in=parent_id,
            )


def _known_catalog(conn: Connection, sha: str) -> dict | None:
    """The FIT catalog of these bytes from an earlier export (decoding is the costly part)."""
    return conn.execute(
        text("""SELECT meta->'fit' FROM artifact_member
                WHERE artifact_sha256 = :s AND meta->'fit'->>'catalog_version' = :v LIMIT 1"""),
        {"s": sha, "v": fit_catalog.CATALOG_VERSION},
    ).scalar()


def _insert_member(conn, batch_id, sha, path, category, device_id, meta, nested_in=None) -> int:
    return conn.execute(
        text("""
            INSERT INTO artifact_member
                (batch_id, artifact_sha256, archive_path, category, device_id, nested_in, meta)
            VALUES (:b, :sha, :p, :c, :d, :n, CAST(:m AS jsonb)) RETURNING id
        """),
        {
            "b": batch_id,
            "sha": sha,
            "p": path,
            "c": category,
            "d": device_id,
            "n": nested_in,
            "m": json.dumps(meta),
        },
    ).scalar_one()


def _media_type(name: str) -> str | None:
    suffix = PurePosixPath(name).suffix.lower()
    return {
        ".fit": "application/vnd.ant.fit",
        ".json": "application/json",
        ".zip": "application/zip",
        ".png": "image/png",
        ".bin": "application/octet-stream",
    }.get(suffix, "application/x-sqlite3" if name == "database/Gadgetbridge" else None)


def _upsert_device(
    conn: Connection, source_key: str, vendor=None, model=None, name=None, source: str = "gadgetbridge"
) -> int:
    return conn.execute(
        text("""
            INSERT INTO device (source, source_key, vendor, model, name)
            VALUES (:src, :k, :v, :m, :n)
            ON CONFLICT (source, source_key) DO UPDATE SET
                vendor = COALESCE(EXCLUDED.vendor, device.vendor),
                model = COALESCE(EXCLUDED.model, device.model),
                name = COALESCE(EXCLUDED.name, device.name)
            RETURNING id
        """),
        {"src": source, "k": source_key, "v": vendor, "m": model, "n": name},
    ).scalar_one()


def run_gb_sqlite(
    engine: Engine,
    store: RawStore,
    batch_id: int,
    db_sha: str,
    *,
    as_of: datetime | None = None,
    force_full: bool = False,
) -> dict:
    """Parse one preserved Gadgetbridge database artifact inside a single transaction.

    Incremental by default: each table from the applied snapshot's newest row minus the window
    (see `gb_state`). A snapshot older than the applied one is skipped, so a late upload of an
    old export never overwrites newer data.
    """
    settings = get_settings()
    window = timedelta(hours=settings.gb_window_hours)
    with gb_sqlite.GadgetbridgeDb(store.path_for(db_sha), counter_timezone=settings.timezone) as gdb:
        bounds = gdb.table_bounds()
        with engine.connect() as conn:
            state = gb_state.load(conn)
        if gb_state.is_older(state, as_of, bounds):
            return {"status": "skipped", "reason": "older_than_applied", "applied_batch_id": state.batch_id}
        plan = gb_state.plan(
            state,
            parser_version=gb_sqlite.PARSER_VERSION,
            schema_version=gdb.schema_version,
            bounds=bounds,
            table_units=_TABLE_UNITS,
            window=window,
            incremental=settings.gb_incremental,
            force_full=force_full,
        )
        # A full parse that follows an applied snapshot reports what an incremental one would
        # have missed (changes older than the window): tells whether the window is wide enough.
        drift = None
        if plan.mode == "full" and state is not None:
            probe = gb_state.plan(
                state,
                parser_version=gb_sqlite.PARSER_VERSION,
                schema_version=gdb.schema_version,
                bounds=bounds,
                table_units=_TABLE_UNITS,
                window=window,
            )
            drift = probe.windows

        with engine.begin() as conn:
            run_id = conn.execute(
                text("""
                    INSERT INTO parse_run (batch_id, artifact_sha256, parser_name, parser_version, status)
                    VALUES (:b, :sha, :n, :v, 'running') RETURNING id
                """),
                {"b": batch_id, "sha": db_sha, "n": gb_sqlite.PARSER_NAME, "v": gb_sqlite.PARSER_VERSION},
            ).scalar_one()

        result: dict = {"parse_run_id": run_id, "mode": plan.mode}
        try:
            with engine.begin() as conn:
                device_map = {
                    d.gb_id: _upsert_device(conn, d.source_key, d.vendor, d.model, d.name)
                    for d in gdb.devices()
                }
                sample_stats = _write_samples(conn, run_id, gdb, device_map, plan.windows, drift)
                activity_stats = _write_activities(conn, run_id, gdb, device_map)
                report = gdb.report.as_dict()
                report["mode"] = {
                    "mode": plan.mode,
                    "reason": plan.reason,
                    "window_hours": settings.gb_window_hours,
                    "from": {
                        t: gb_sqlite.to_dt(v, _TABLE_UNITS[t]).isoformat()
                        for t, v in (plan.windows or {}).items()
                    },
                }
                report["write"] = {"samples": sample_stats, "activities": activity_stats}
                conn.execute(
                    text("""UPDATE parse_run SET status = 'completed', finished_at = now(),
                            stats = CAST(:s AS jsonb) WHERE id = :id"""),
                    {"id": run_id, "s": json.dumps(report)},
                )
                gb_state.save(
                    conn,
                    batch_id=batch_id,
                    sha=db_sha,
                    as_of=as_of or _now(),
                    parser_version=gb_sqlite.PARSER_VERSION,
                    schema_version=gdb.schema_version,
                    table_hwm=gb_state.table_hwm(bounds),
                    full=plan.mode == "full",
                )
            result.update(status="completed", **report["write"])
        except Exception as e:
            log.exception("parse run %s failed", run_id)
            with engine.begin() as conn:
                conn.execute(
                    text("""UPDATE parse_run SET status = 'failed', finished_at = now(), error = :e
                            WHERE id = :id"""),
                    {"id": run_id, "e": f"{type(e).__name__}: {e}"},
                )
            result.update(status="failed", error=f"{type(e).__name__}: {e}")
    return result


def _write_samples(
    conn: Connection,
    run_id: int,
    gdb,
    device_map: dict[int, int],
    windows: dict[str, int] | None = None,
    drift: dict[str, int] | None = None,
) -> dict:
    unknown_devices: set[int] = set()

    def rows():
        for s in gdb.samples(windows):
            dev = device_map.get(s.gb_device_id)
            if dev is None:
                unknown_devices.add(s.gb_device_id)
                continue
            yield (s.ts, dev, s.metric, s.value, s.unit)

    def bounds(w: dict[str, int] | None):
        if w is None:
            return None
        return [(device_map[d], m, lo) for d, m, lo in gdb.window_metrics(w) if d in device_map]

    # Each export is a full snapshot: rows an older run of this parser wrote inside the covered
    # time range, and which this run no longer produces, are superseded.
    out = upsert_samples(
        conn,
        run_id,
        rows(),
        retract_parser=gb_sqlite.PARSER_NAME,
        windows=bounds(windows),
        drift_bounds=bounds(drift),
    )
    if unknown_devices:
        out["skipped_unknown_devices"] = sorted(unknown_devices)
    return out


def upsert_samples(
    conn: Connection,
    run_id: int,
    rows: Iterable[tuple],
    retract_parser: str | None = None,
    windows: list[tuple[int, str, datetime]] | None = None,
    drift_bounds: list[tuple[int, str, datetime]] | None = None,
) -> dict:
    """Bulk upsert (ts, device_id, metric, value, unit) rows; only real changes are written.

    Retraction (`retract_parser`): rows of that parser that the snapshot no longer produces are
    deleted, within the snapshot's span per device (full snapshot) or, with `windows`
    ((device_id, metric, lowest ts) of an incremental read), only from each metric's own window
    start on. `drift_bounds` counts changes older than an incremental window would have reached.
    """
    conn.execute(text("DROP TABLE IF EXISTS sample_stage"))
    conn.execute(
        text("""
        CREATE TEMP TABLE sample_stage (
            ts timestamptz, device_id int, metric text, value double precision, unit text
        ) ON COMMIT DROP
    """)
    )
    staged = 0
    raw = conn.connection.dbapi_connection  # psycopg connection, same transaction
    with (
        raw.cursor() as cur,
        cur.copy("COPY sample_stage (ts, device_id, metric, value, unit) FROM STDIN") as copy,
    ):
        for row in rows:
            copy.write_row(row)
            staged += 1
    if retract_parser:
        conn.execute(text("CREATE INDEX ON sample_stage (metric, device_id, ts)"))
    # Rows that differ from what is stored. Unchanged rows are not even locked: an incremental
    # snapshot repeats its overlap every time and must not rewrite it.
    conn.execute(text("DROP TABLE IF EXISTS sample_todo"))
    conn.execute(
        text("""
        CREATE TEMP TABLE sample_todo ON COMMIT DROP AS
        SELECT DISTINCT ON (metric, device_id, ts) ts, device_id, metric, value, unit
        FROM sample_stage st
        WHERE NOT EXISTS (SELECT 1 FROM sample s WHERE s.metric = st.metric AND s.device_id = st.device_id
                            AND s.ts = st.ts AND s.value = st.value AND s.unit IS NOT DISTINCT FROM st.unit)
        ORDER BY metric, device_id, ts
    """)
    )
    out: dict = {"staged": staged}
    if drift_bounds is not None:
        _stage_bounds(conn, "drift_bound", drift_bounds)
        out["drift"] = {
            "changed": conn.execute(
                text("""SELECT count(*) FROM sample_todo t JOIN drift_bound b
                        ON b.device_id = t.device_id AND b.metric = t.metric WHERE t.ts < b.lo""")
            ).scalar_one()
        }
    row = conn.execute(
        text("""
            WITH up AS (
                INSERT INTO sample (ts, device_id, metric, value, unit, parse_run_id)
                SELECT ts, device_id, metric, value, unit, :run FROM sample_todo
                ON CONFLICT (metric, device_id, ts) DO UPDATE
                    SET value = EXCLUDED.value, unit = EXCLUDED.unit,
                        parse_run_id = EXCLUDED.parse_run_id
                RETURNING (xmax = 0) AS inserted
            )
            SELECT count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted) FROM up
        """),
        {"run": run_id},
    ).one()
    out.update(inserted=row[0], changed=row[1], unchanged=staged - row[0] - row[1])
    if retract_parser and windows is not None:
        _stage_bounds(conn, "window_stage", windows)
        deleted = conn.execute(
            text("""
                WITH hi AS (SELECT device_id, max(ts) AS hi FROM sample_stage GROUP BY 1)
                DELETE FROM sample s USING window_stage w, hi, parse_run pr
                WHERE s.device_id = w.device_id AND s.metric = w.metric AND s.ts >= w.lo
                  AND hi.device_id = w.device_id AND s.ts <= hi.hi
                  AND pr.id = s.parse_run_id AND pr.parser_name = :parser AND s.parse_run_id <> :run
                  AND NOT EXISTS (SELECT 1 FROM sample_stage st
                                  WHERE st.metric = s.metric AND st.device_id = s.device_id
                                    AND st.ts = s.ts)
                RETURNING s.ts
            """),
            {"parser": retract_parser, "run": run_id},
        ).all()
        out["retracted"] = len(deleted)
    elif retract_parser:
        deleted = conn.execute(
            text("""
                -- covered range = the snapshot's span per device (not per metric: a value the
                -- parser stopped emitting may lie before the metric's first new value)
                WITH span AS (
                    SELECT device_id, min(ts) AS lo, max(ts) AS hi FROM sample_stage GROUP BY 1
                ), mets AS (
                    SELECT DISTINCT device_id, metric FROM sample_stage
                )
                DELETE FROM sample s USING span, mets, parse_run pr
                WHERE s.device_id = span.device_id AND s.ts BETWEEN span.lo AND span.hi
                  AND s.device_id = mets.device_id AND s.metric = mets.metric
                  AND pr.id = s.parse_run_id AND pr.parser_name = :parser AND s.parse_run_id <> :run
                  AND NOT EXISTS (SELECT 1 FROM sample_stage st
                                  WHERE st.metric = s.metric AND st.device_id = s.device_id
                                    AND st.ts = s.ts)
                RETURNING s.device_id, s.metric, s.ts
            """),
            {"parser": retract_parser, "run": run_id},
        ).all()
        out["retracted"] = len(deleted)
        if drift_bounds is not None:
            lows = {(d, m): lo for d, m, lo in drift_bounds}
            out["drift"]["retracted"] = sum(1 for d, m, ts in deleted if (d, m) in lows and ts < lows[(d, m)])
    return out


def _stage_bounds(conn: Connection, table: str, bounds: list[tuple[int, str, datetime]]) -> None:
    conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
    conn.execute(
        text(f"CREATE TEMP TABLE {table} (device_id int, metric text, lo timestamptz) ON COMMIT DROP")
    )
    if bounds:
        conn.execute(
            text(f"INSERT INTO {table} (device_id, metric, lo) VALUES (:d, :m, :lo)"),
            [{"d": d, "m": m, "lo": lo} for d, m, lo in bounds],
        )
    conn.execute(text(f"CREATE INDEX ON {table} (device_id, metric)"))


def _write_activities(conn, run_id, gdb, device_map) -> dict:
    activities = [a for a in gdb.activities() if a.gb_device_id in device_map]
    names = sorted({a.raw_details_name for a in activities if a.raw_details_name})
    # The FIT file may have come with any earlier export (the live database has none): newest wins.
    fit_by_name = (
        dict(
            conn.execute(
                text("""
                SELECT DISTINCT ON (name) name, artifact_sha256 FROM (
                    SELECT regexp_replace(archive_path, '^.*/', '') AS name, artifact_sha256, batch_id
                    FROM artifact_member
                    WHERE nested_in IS NULL AND regexp_replace(archive_path, '^.*/', '') = ANY(:names)
                ) m ORDER BY name, batch_id DESC
            """),
                {"names": names},
            ).all()
        )
        if names
        else {}
    )
    inserted = updated = unmatched = 0
    for a in activities:
        dev = device_map[a.gb_device_id]
        fit_sha = fit_by_name.get(a.raw_details_name) if a.raw_details_name else None
        if a.raw_details_name and fit_sha is None:
            unmatched += 1
        ins = conn.execute(
            text("""
                INSERT INTO activity (device_id, start_ts, end_ts, name, kind_raw, summary,
                                      raw_details_name, raw_fit_sha256, has_gpx, gb_parse_run_id)
                VALUES (:d, :s, :e, :n, :k, CAST(:sum AS jsonb), :rdn, :fit, :gpx, :run)
                ON CONFLICT (device_id, start_ts) DO UPDATE SET
                    end_ts = EXCLUDED.end_ts, name = EXCLUDED.name, kind_raw = EXCLUDED.kind_raw,
                    summary = EXCLUDED.summary, raw_details_name = EXCLUDED.raw_details_name,
                    raw_fit_sha256 = COALESCE(EXCLUDED.raw_fit_sha256, activity.raw_fit_sha256),
                    has_gpx = EXCLUDED.has_gpx, gb_parse_run_id = EXCLUDED.gb_parse_run_id
                WHERE activity.gb_parse_run_id IS NULL
                   OR (activity.end_ts, activity.name, activity.kind_raw, activity.summary,
                       activity.raw_details_name, activity.raw_fit_sha256, activity.has_gpx)
                      IS DISTINCT FROM
                      (EXCLUDED.end_ts, EXCLUDED.name, EXCLUDED.kind_raw, EXCLUDED.summary,
                       EXCLUDED.raw_details_name,
                       COALESCE(EXCLUDED.raw_fit_sha256, activity.raw_fit_sha256), EXCLUDED.has_gpx)
                RETURNING (xmax = 0)
            """),
            {
                "d": dev,
                "s": a.start_ts,
                "e": a.end_ts,
                "n": a.name,
                "k": a.kind_raw,
                "sum": json.dumps(a.summary) if a.summary is not None else None,
                "rdn": a.raw_details_name,
                "fit": fit_sha,
                "gpx": a.has_gpx,
                "run": run_id,
            },
        ).scalar()
        if ins is True:
            inserted += 1
        elif ins is False:
            updated += 1
    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": len(activities) - inserted - updated,
        "fit_not_in_archive": unmatched,
    }


FIT_PARSED_CATEGORIES = ("ACTIVITY", "SKIN_TEMP", "SLEEP", "MONITOR", "METRICS", "SLP_DISR")


def run_fit_parsers(engine: Engine, store: RawStore, batch_id: int, force: bool = False) -> dict:
    """One parse run per FIT artifact, each in its own transaction (a bad file fails alone).

    A file this parser version already parsed (same bytes, from any export) is skipped unless
    `force`: every export repeats all FIT files ever fetched from the watch.
    """
    with engine.connect() as conn:
        members = conn.execute(
            text("""
                SELECT artifact_sha256, archive_path, device_id FROM artifact_member
                WHERE batch_id = :b AND nested_in IS NULL AND category = ANY(:cats)
                  AND meta ? 'fit' ORDER BY archive_path
            """),
            {"b": batch_id, "cats": list(FIT_PARSED_CATEGORIES)},
        ).all()
        known = (
            set()
            if force
            else set(
                conn.execute(
                    text("""SELECT DISTINCT artifact_sha256 FROM parse_run
                            WHERE status = 'completed' AND parser_name = :n AND parser_version = :v
                              AND artifact_sha256 = ANY(:s)"""),
                    {
                        "n": fit_parser.PARSER_NAME,
                        "v": fit_parser.PARSER_VERSION,
                        "s": [m.artifact_sha256 for m in members],
                    },
                ).scalars()
            )
        )
    totals = {
        "files": 0,
        "skipped_known": 0,
        "activities_inserted": 0,
        "activities_updated": 0,
        "sets": 0,
        "samples": 0,
    }
    failed: list[dict] = []
    for sha, path, device_id in members:
        if sha in known:
            totals["skipped_known"] += 1
            continue
        known.add(sha)  # the same bytes under a second path: once is enough
        totals["files"] += 1
        with engine.begin() as conn:
            run_id = _start_run(conn, batch_id, sha, fit_parser.PARSER_NAME, fit_parser.PARSER_VERSION)
        try:
            if device_id is None:
                raise ValueError("FIT file is not below a device folder")
            parsed = fit_parser.parse_fit(store.path_for(sha).read_bytes())
            with engine.begin() as conn:
                written = _write_fit(conn, run_id, sha, device_id, parsed)
                _finish_run(conn, run_id, {**parsed.stats(), "write": written})
            totals["activities_inserted"] += written["activities_inserted"]
            totals["activities_updated"] += written["activities_updated"]
            totals["sets"] += written["sets"]
            totals["samples"] += written["samples"]["staged"]
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            with engine.begin() as conn:
                _fail_run(conn, run_id, err)
            failed.append({"archive_path": path, "parse_run_id": run_id, "error": err})
    return {**totals, "failed": failed}


def _start_run(conn: Connection, batch_id: int, sha: str, name: str, version: str) -> int:
    return conn.execute(
        text("""
            INSERT INTO parse_run (batch_id, artifact_sha256, parser_name, parser_version, status)
            VALUES (:b, :sha, :n, :v, 'running') RETURNING id
        """),
        {"b": batch_id, "sha": sha, "n": name, "v": version},
    ).scalar_one()


def _finish_run(conn: Connection, run_id: int, stats: dict) -> None:
    conn.execute(
        text("""UPDATE parse_run SET status = 'completed', finished_at = now(),
                stats = CAST(:s AS jsonb) WHERE id = :id"""),
        {"id": run_id, "s": json.dumps(stats)},
    )


def _fail_run(conn: Connection, run_id: int, error: str) -> None:
    conn.execute(
        text("UPDATE parse_run SET status = 'failed', finished_at = now(), error = :e WHERE id = :id"),
        {"id": run_id, "e": error},
    )


def _write_fit(conn: Connection, run_id: int, sha: str, device_id: int, p: fit_parser.FitParse) -> dict:
    inserted = updated = n_sets = 0
    for sess in p.sessions:
        activity_id, was_inserted = conn.execute(
            text("""
                INSERT INTO activity (device_id, start_ts, end_ts, raw_fit_sha256, fit_parse_run_id,
                    fit_session_index, sport, sub_sport, sport_profile_name, elapsed_s, timer_s,
                    distance_m, energy_kcal, avg_hr, max_hr, fit_attrs)
                VALUES (:d, :s, :e, :sha, :run, :idx, :sport, :sub, :prof, :el, :tm, :dist, :kcal,
                    :avg, :max, CAST(:attrs AS jsonb))
                ON CONFLICT (device_id, start_ts) DO UPDATE SET
                    end_ts = COALESCE(activity.end_ts, EXCLUDED.end_ts),
                    raw_fit_sha256 = COALESCE(activity.raw_fit_sha256, EXCLUDED.raw_fit_sha256),
                    fit_parse_run_id = EXCLUDED.fit_parse_run_id,
                    fit_session_index = EXCLUDED.fit_session_index, sport = EXCLUDED.sport,
                    sub_sport = EXCLUDED.sub_sport, sport_profile_name = EXCLUDED.sport_profile_name,
                    elapsed_s = EXCLUDED.elapsed_s, timer_s = EXCLUDED.timer_s,
                    distance_m = EXCLUDED.distance_m, energy_kcal = EXCLUDED.energy_kcal,
                    avg_hr = EXCLUDED.avg_hr, max_hr = EXCLUDED.max_hr, fit_attrs = EXCLUDED.fit_attrs
                RETURNING id, (xmax = 0)
            """),
            {
                "d": device_id,
                "s": sess.start_ts,
                "e": sess.end_ts,
                "sha": sha,
                "run": run_id,
                "idx": sess.index,
                "sport": sess.sport,
                "sub": sess.sub_sport,
                "prof": sess.sport_profile_name,
                "el": sess.elapsed_s,
                "tm": sess.timer_s,
                "dist": sess.distance_m,
                "kcal": sess.energy_kcal,
                "avg": sess.avg_hr,
                "max": sess.max_hr,
                "attrs": json.dumps(sess.attrs),
            },
        ).one()
        if was_inserted:
            inserted += 1
        else:
            updated += 1
        # Sets are derived wholesale from one FIT session: replace, never merge.
        conn.execute(text("DELETE FROM activity_set WHERE activity_id = :a"), {"a": activity_id})
        sets = [s for s in p.sets if s.session_index == sess.index or len(p.sessions) == 1]
        if sets:
            conn.execute(
                text("""
                    INSERT INTO activity_set (activity_id, set_index, set_type, start_ts, duration_s,
                        repetitions, weight_kg, exercise_category, category_candidates,
                        subtype_candidates, wkt_step_index, parse_run_id)
                    VALUES (:a, :i, :t, :s, :d, :r, :w, :c, :cc, :sc, :wkt, :run)
                """),
                [
                    {
                        "a": activity_id,
                        "i": s.index,
                        "t": s.set_type,
                        "s": s.start_ts,
                        "d": s.duration_s,
                        "r": s.repetitions,
                        "w": s.weight_kg,
                        "c": s.exercise_category,
                        "cc": s.category_candidates,
                        "sc": s.subtype_candidates,
                        "wkt": s.wkt_step_index,
                        "run": run_id,
                    }
                    for s in sets
                ],
            )
            n_sets += len(sets)
    samples = upsert_samples(conn, run_id, ((s.ts, device_id, s.metric, s.value, s.unit) for s in p.samples))
    return {
        "activities_inserted": inserted,
        "activities_updated": updated,
        "sets": n_sets,
        "samples": samples,
    }


class WaistlineRetired(Exception):
    """Waistline was replaced by the food diary; its exports are no longer imported."""


def refuse_retired_waistline(conn: Connection) -> None:
    """Raises 409 (API) / WaistlineRetired (worker) once the Waistline takeover happened: a new
    export would overwrite entries edited since."""
    from vitalocal.diary import waistline_retired_at

    if (at := waistline_retired_at(conn)) is not None:
        raise WaistlineRetired(
            f"Waistline was replaced by the VitaLocal food diary on {at[:10]}; Waistline exports are "
            "no longer imported"
        )


def _process_waistline(engine: Engine, store: RawStore, batch_id: int, sha: str) -> dict:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM artifact_member WHERE batch_id = :id"), {"id": batch_id})
        _insert_member(conn, batch_id, sha, "waistline_export.json", "waistline_export", None, {})
        run_id = _start_run(conn, batch_id, sha, waistline.PARSER_NAME, waistline.PARSER_VERSION)
    try:
        parsed = waistline.parse(store.path_for(sha).read_bytes(), get_settings().timezone)
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE ingest_batch SET client_meta = client_meta || CAST(:m AS jsonb) WHERE id = :id"),
                {
                    "id": batch_id,
                    "m": json.dumps({"app": "waistline", "schema_version": parsed.schema_version}),
                },
            )
            device_id = _upsert_device(
                conn, "waistline-app", "Waistline", "app", "Waistline", source="waistline"
            )
            entries = _write_nutrition_entries(conn, run_id, parsed)
            # goals changed in Waistline apply from the day the export arrived
            received = conn.execute(
                text("SELECT (received_at AT TIME ZONE :tz)::date FROM ingest_batch WHERE id = :id"),
                {"tz": get_settings().timezone, "id": batch_id},
            ).scalar_one()
            goals = sync_goals(conn, "waistline", parsed.goals, received)
            samples = upsert_samples(
                conn,
                run_id,
                ((s.ts, device_id, s.metric, s.value, s.unit) for s in parsed.samples),
                retract_parser=waistline.PARSER_NAME,
            )
            stats = {
                **parsed.stats(),
                "write": {"entries": entries, "samples": samples, "goals_changed": goals},
            }
            _finish_run(conn, run_id, stats)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        with engine.begin() as conn:
            _fail_run(conn, run_id, err)
        return {"waistline_parse_run": {"parse_run_id": run_id, "status": "failed"}, "parse_errors": [err]}
    return {"waistline_parse_run": {"parse_run_id": run_id, "status": "completed", **stats["write"]}}


def _process_waistline_csv(engine: Engine, store: RawStore, batch_id: int, sha: str) -> dict:
    """Daily totals and weight only; the JSON export's per-food entries are left untouched."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM artifact_member WHERE batch_id = :id"), {"id": batch_id})
        _insert_member(conn, batch_id, sha, "diary_export.csv", "waistline_diary_csv", None, {})
        run_id = _start_run(conn, batch_id, sha, waistline_csv.PARSER_NAME, waistline_csv.PARSER_VERSION)
    try:
        parsed = waistline_csv.parse(store.path_for(sha).read_bytes(), get_settings().timezone)
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE ingest_batch SET client_meta = client_meta || CAST(:m AS jsonb) WHERE id = :id"),
                {"id": batch_id, "m": json.dumps({"app": "waistline", "export": "diary_csv"})},
            )
            device_id = _upsert_device(
                conn, "waistline-app", "Waistline", "app", "Waistline", source="waistline"
            )
            samples = upsert_samples(
                conn,
                run_id,
                ((s.ts, device_id, s.metric, s.value, s.unit) for s in parsed.samples),
                retract_parser=waistline_csv.PARSER_NAME,
            )
            stats = {**parsed.stats(), "write": {"samples": samples}}
            _finish_run(conn, run_id, stats)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        with engine.begin() as conn:
            _fail_run(conn, run_id, err)
        return {
            "waistline_csv_parse_run": {"parse_run_id": run_id, "status": "failed"},
            "parse_errors": [err],
        }
    return {"waistline_csv_parse_run": {"parse_run_id": run_id, "status": "completed", **stats["write"]}}


def _write_nutrition_entries(conn: Connection, run_id: int, p: waistline.WaistlineParse) -> dict:
    """A Waistline export is a full snapshot: replace all entries of the days it covers."""
    if not p.days:
        return {"deleted": 0, "inserted": 0}
    deleted = conn.execute(
        text("DELETE FROM nutrition_entry WHERE source = 'waistline' AND day = ANY(:days)"),
        {"days": p.days},
    ).rowcount
    if p.entries:
        conn.execute(
            text("""
                INSERT INTO nutrition_entry (source, day, meal_index, meal, logged_at, item_type,
                    source_item_id, name, brand, amount, unit, quantity, nutrients, parse_run_id)
                VALUES ('waistline', :day, :mi, :meal, :at, :type, :sid, :name, :brand, :amount,
                    :unit, :qty, CAST(:nut AS jsonb), :run)
            """),
            [
                {
                    "day": e.day,
                    "mi": e.meal_index,
                    "meal": e.meal,
                    "at": e.logged_at,
                    "type": e.item_type,
                    "sid": e.source_item_id,
                    "name": e.name,
                    "brand": e.brand,
                    "amount": e.amount,
                    "unit": e.unit,
                    "qty": e.quantity,
                    "nut": json.dumps(e.nutrients),
                    "run": run_id,
                }
                for e in p.entries
            ],
        )
    return {"deleted": deleted, "inserted": len(p.entries)}


def detect_source(filename: str | None, head: bytes) -> str:
    """Gadgetbridge: ZIP. Waistline: full JSON export or the diary CSV (daily totals). Other apps:
    workouts from Strong / Hevy, daily totals with a date column (MyFitnessPal, Cronometer, weight)."""
    if head.startswith(b"PK"):
        return SOURCE_GADGETBRIDGE
    if workout_csv.looks_like_workout_csv(head):
        return SOURCE_WORKOUT_CSV
    if waistline_csv.looks_like_diary_csv(head):
        return SOURCE_WAISTLINE_CSV
    if daily_csv.looks_like_daily_csv(head):
        return SOURCE_DAILY_CSV
    if head.lstrip()[:1] == b"{" or (filename or "").lower().endswith(".json"):
        return SOURCE_WAISTLINE
    raise ValueError(
        "unsupported file: expected a Gadgetbridge ZIP, a Waistline export, a Strong or Hevy workout CSV, "
        "or a CSV with a date column and daily values (calories, protein, weight, …)"
    )


def import_file(engine: Engine, store: RawStore, path: Path) -> int:
    """Convenience for CLI/tests: register + process synchronously."""
    with open(path, "rb") as f:
        source = detect_source(path.name, f.read(DETECT_BYTES))
        f.seek(0)
        batch_id, _ = register_upload(engine, store, f, source)
    process_batch(engine, store, batch_id)
    return batch_id
