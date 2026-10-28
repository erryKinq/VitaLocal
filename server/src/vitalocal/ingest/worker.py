"""Ingest worker: processes uploaded batches one at a time in a background thread.

- Strict order: batches run one after another (`service.RUN_LOCK`, shared with the CLI), oldest
  first, so two snapshots never write the same rows at once.
- Live snapshots of the same source that queued up while one was processed are coalesced: only
  the newest is parsed (each is a full snapshot), the others become `superseded`.
- After a restart, batches interrupted mid-way go back to the queue.
- Raw-store retention runs at most every few minutes instead of after every batch.
- Once a night the applied Gadgetbridge snapshot is parsed in full, which repairs anything the
  incremental parses could not see (changes older than their window).

Only one worker runs per database (advisory lock), whatever the number of server processes.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks
from sqlalchemy import Engine, text

from vitalocal.config import get_settings
from vitalocal.ingest import gb_state, retention, service
from vitalocal.rawstore import RawStore

log = logging.getLogger(__name__)

# Sources whose batches are complete snapshots of one database: a newer one makes older queued
# ones redundant.
COALESCED_SOURCES = frozenset({"gadgetbridge_db"})
OWNER_LOCK = 0x7654_0003
POLL_S = 30
PRUNE_EVERY = timedelta(minutes=10)
FULL_PARSE_AFTER = timedelta(hours=20)


def recover(engine: Engine) -> int:
    """Batches left `processing` by a crash go back to the queue; their parse runs failed."""
    with engine.connect() as lock:
        got = lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": service.RUN_LOCK}).scalar()
        lock.commit()
        if not got:  # a batch is being processed right now (e.g. by the CLI)
            return 0
        try:
            with engine.begin() as conn:
                n = conn.execute(
                    text("UPDATE ingest_batch SET status = 'pending' WHERE status = 'processing'")
                ).rowcount
                conn.execute(
                    text("""UPDATE parse_run SET status = 'failed', finished_at = now(),
                            error = 'interrupted (server restart)' WHERE status = 'running'""")
                )
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": service.RUN_LOCK})
            lock.commit()
    if n:
        log.warning("requeued %d interrupted import(s)", n)
    return n


def claim_next(engine: Engine) -> int | None:
    """The next batch to process (oldest first); coalesces queued live snapshots."""
    with engine.begin() as conn:
        row = conn.execute(
            text("""SELECT id, source FROM ingest_batch WHERE status = 'pending'
                    ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""")
        ).first()
        if row is None:
            return None
        if row.source not in COALESCED_SOURCES:
            return row.id
        newest = conn.execute(
            text("SELECT max(id) FROM ingest_batch WHERE status = 'pending' AND source = :s"),
            {"s": row.source},
        ).scalar_one()
        conn.execute(
            text("""UPDATE ingest_batch SET status = 'superseded', finished_at = now(),
                           stats = jsonb_build_object('superseded_by', CAST(:n AS bigint))
                    WHERE status = 'pending' AND source = :s AND id < :n"""),
            {"s": row.source, "n": newest},
        )
        return newest


def drain(engine: Engine, store: RawStore, prune: bool = True) -> int:
    """Process every queued batch; returns how many. Used by the worker, the CLI and tests."""
    done = 0
    while (batch_id := claim_next(engine)) is not None:
        try:
            service.process_batch(engine, store, batch_id, prune=False)
        except Exception as e:  # noqa: BLE001 - one bad batch must not stop the queue
            log.exception("batch %s could not be processed", batch_id)
            with engine.begin() as conn:
                conn.execute(
                    text("""UPDATE ingest_batch SET status = 'failed', error = :e, finished_at = now()
                            WHERE id = :id AND status IN ('pending', 'processing')"""),
                    {"id": batch_id, "e": f"{type(e).__name__}: {e}"},
                )
        done += 1
    if prune and done:
        try:
            retention.prune(engine, store, get_settings().raw_retention_days)
        except Exception:
            log.exception("raw store retention failed")
    return done


def full_parse_due(engine: Engine, now: datetime) -> bool:
    """Nightly: past the configured local time, and the last full parse is older than ~a day."""
    s = get_settings()
    local = now.astimezone(ZoneInfo(s.timezone))
    hh, mm = (int(x) for x in s.gb_full_parse_at.split(":"))
    if local.time() < time(hh, mm):
        return False
    with engine.connect() as conn:
        state = gb_state.load(conn)
        busy = conn.execute(text("SELECT count(*) FROM ingest_batch WHERE status = 'pending'")).scalar()
    if state is None or busy:
        return False
    return state.last_full_at is None or now - state.last_full_at > FULL_PARSE_AFTER


class IngestWorker:
    def __init__(self, engine: Engine, store: RawStore):
        self.engine, self.store = engine, store
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_prune: datetime | None = None
        self.owner = False

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ingest-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 30) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def kick(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        # Outer loop: whatever goes wrong (database restart, bug), the worker comes back.
        while not self._stop.is_set():
            try:
                with self.engine.connect() as owner:
                    got = owner.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": OWNER_LOCK}).scalar()
                    owner.commit()
                    if not got:  # another server process runs the worker
                        self.owner = False
                        self._stop.wait(60)
                        continue
                    self.owner = True
                    try:
                        recover(self.engine)
                        self._loop()
                    finally:
                        self.owner = False
                        owner.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": OWNER_LOCK})
                        owner.commit()
            except Exception:
                log.exception("ingest worker failed; restarting in 5 s")
                self._stop.wait(5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            drain(self.engine, self.store, prune=False)
            now = datetime.now(UTC)
            if self._last_prune is None or now - self._last_prune >= PRUNE_EVERY:
                self._last_prune = now
                try:
                    retention.prune(self.engine, self.store, get_settings().raw_retention_days)
                except Exception:
                    log.exception("raw store retention failed")
            if get_settings().gb_incremental and full_parse_due(self.engine, now):
                result = service.reparse_latest_snapshot(self.engine, self.store)
                log.info("nightly full parse of the applied Gadgetbridge snapshot: %s", result)
            self._wake.wait(POLL_S)


_worker: IngestWorker | None = None


def start(engine: Engine, store: RawStore) -> IngestWorker:
    global _worker
    _worker = IngestWorker(engine, store)
    _worker.start()
    return _worker


def stop() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None


def current() -> IngestWorker | None:
    return _worker


def schedule(background: BackgroundTasks, engine: Engine, store: RawStore) -> None:
    """New work is queued: wake the worker, or (no worker, e.g. in tests) process it after the
    response."""
    if _worker is not None and _worker.alive:
        _worker.kick()
    else:
        background.add_task(drain, engine, store)
