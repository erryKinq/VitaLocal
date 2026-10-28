import io
import json
import re
import zlib
from datetime import datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import Connection, text
from starlette.concurrency import run_in_threadpool

from vitalocal.api.days import sync_state
from vitalocal.config import get_settings
from vitalocal.db import CONN, get_engine
from vitalocal.deps import get_store
from vitalocal.ingest import dbdelta, gb_state, manifest, retention, service, worker
from vitalocal.rawstore import RawStore

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CLIENT_META_MAX = 4096
_CLIENT_META_FIELD = Form(
    None,
    description='Optional JSON object about the uploader, e.g. {"app": "vitalocal-android", '
    '"app_version": "0.1.0", "device": "Pixel 8", "trigger": "auto"}. Stored as client_meta.client.',
)


def _client_meta(raw: str | None) -> dict | None:
    if raw is None or not raw.strip():
        return None
    if len(raw) > _CLIENT_META_MAX:
        raise HTTPException(422, f"client_meta must be at most {_CLIENT_META_MAX} characters")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"client_meta is not valid JSON: {e.msg}") from e
    if not isinstance(value, dict):
        raise HTTPException(422, "client_meta must be a JSON object")
    return value


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_export(
    file: UploadFile,
    background: BackgroundTasks,
    response: Response,
    client_meta: str | None = _CLIENT_META_FIELD,
    store: RawStore = Depends(get_store),
) -> dict:
    """Upload a Gadgetbridge export ZIP, a Waistline JSON export or diary CSV, a Strong or Hevy
    workout CSV, or a CSV of daily values from another app (detected from the content).

    Re-uploading identical bytes returns the existing batch.
    """
    client = _client_meta(client_meta)
    try:
        source = service.detect_source(file.filename, file.file.read(service.DETECT_BYTES))
    except ValueError as e:
        raise HTTPException(415, str(e)) from e
    if source in (service.SOURCE_WAISTLINE, service.SOURCE_WAISTLINE_CSV):
        try:
            with get_engine().connect() as conn:
                service.refuse_retired_waistline(conn)
        except service.WaistlineRetired as e:
            raise HTTPException(409, {"code": "waistline_retired", "message": str(e)}) from e
    file.file.seek(0)
    return _register(file, background, response, store, source, client)


@router.post("/health-connect", status_code=status.HTTP_202_ACCEPTED)
async def upload_health_connect(
    request: Request,
    background: BackgroundTasks,
    response: Response,
    store: RawStore = Depends(get_store),
) -> dict:
    """Records the Android app read from Health Connect for a time window (JSON, see
    `ingest/health_connect.py`). Values of that window that are no longer there are retracted."""
    body = await request.body()
    if len(body) > HC_MAX_BYTES:
        raise HTTPException(413, f"at most {HC_MAX_BYTES // 1024 // 1024} MB per batch")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"not JSON: {e.msg}") from e
    if not isinstance(data, dict) or not isinstance(data.get("range"), dict):
        raise HTTPException(422, "expected an object with a range")
    client = data.get("client") if isinstance(data.get("client"), dict) else None
    batch_id, created = await run_in_threadpool(
        service.register_upload, get_engine(), store, io.BytesIO(body), service.SOURCE_HEALTH_CONNECT, client
    )
    if created:
        worker.schedule(background, get_engine(), store)
    else:
        response.status_code = status.HTTP_200_OK
    return {"batch_id": batch_id, "created": created, "source": service.SOURCE_HEALTH_CONNECT}


HC_MAX_BYTES = 32 * 1024 * 1024


@router.post("/gadgetbridge", status_code=status.HTTP_202_ACCEPTED)
def upload_gadgetbridge_export(
    file: UploadFile,
    background: BackgroundTasks,
    response: Response,
    client_meta: str | None = _CLIENT_META_FIELD,
    store: RawStore = Depends(get_store),
) -> dict:
    """Upload a Gadgetbridge data-export ZIP."""
    client = _client_meta(client_meta)
    return _register(file, background, response, store, service.SOURCE_GADGETBRIDGE, client)


_DB_META_FIELD = Form(
    description='JSON: {"sha256", "size", "encoding": "full"|"vldd1", "base_sha256" (vldd1), '
    '"exported_at", "gadgetbridge": {...}, "client": {...}}',
)
_DB_DATA_FIELD = File(description="gzip: the database, or a VLDD delta on base_sha256")
_DELTA_STATUS = {"base_unavailable": 409, "too_large": 413}


def _db_meta(raw: str) -> dict:
    if len(raw) > _CLIENT_META_MAX:
        raise HTTPException(422, f"meta must be at most {_CLIENT_META_MAX} characters")
    try:
        m = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"meta is not valid JSON: {e.msg}") from e
    if not isinstance(m, dict):
        raise HTTPException(422, "meta must be a JSON object")
    sha = str(m.get("sha256", "")).lower()
    if not _SHA256_RE.match(sha):
        raise HTTPException(422, "meta.sha256 must be 64 hex characters")
    if m.get("encoding") not in ("full", "vldd1"):
        raise HTTPException(422, "meta.encoding must be 'full' or 'vldd1'")
    if m["encoding"] == "vldd1" and not _SHA256_RE.match(str(m.get("base_sha256", "")).lower()):
        raise HTTPException(422, "meta.base_sha256 is required for a delta")
    if m.get("exported_at") is not None:
        try:
            if datetime.fromisoformat(str(m["exported_at"]).replace("Z", "+00:00")).tzinfo is None:
                raise ValueError
        except ValueError as e:
            raise HTTPException(422, "meta.exported_at must be an ISO time with a time zone") from e
    for key in ("client", "gadgetbridge"):
        if m.get(key) is not None and not isinstance(m[key], dict):
            raise HTTPException(422, f"meta.{key} must be a JSON object")
    size = m.get("size")
    if size is not None and (not isinstance(size, int) or size < 0):
        raise HTTPException(422, "meta.size must be a non-negative integer")
    return {**m, "sha256": sha, "base_sha256": str(m.get("base_sha256", "")).lower() or None}


@router.post("/gadgetbridge/db", status_code=status.HTTP_202_ACCEPTED)
def upload_gadgetbridge_db(
    background: BackgroundTasks,
    response: Response,
    meta: str = _DB_META_FIELD,
    data: UploadFile = _DB_DATA_FIELD,
    store: RawStore = Depends(get_store),
) -> dict:
    """Upload Gadgetbridge's database alone, whole or only the blocks that changed since a
    database the server has (see `ingest/dbdelta.py` and docs/API.md). Safe to repeat.

    409 `base_unavailable`: send the whole file. 422: the upload was invalid (`sha_mismatch`,
    `invalid_delta`, `not_sqlite`, `truncated`); send the whole file. 413: too large.
    """
    m = _db_meta(meta)
    try:
        result = service.register_gb_db(
            get_engine(),
            store,
            m,
            data.file,
            max_bytes=get_settings().live_db_max_bytes,
            received_bytes=data.size,
        )
    except dbdelta.DeltaError as e:
        raise HTTPException(_DELTA_STATUS.get(e.code, 422), {"code": e.code, "message": str(e)}) from e
    except (OSError, EOFError, zlib.error) as e:
        raise HTTPException(422, {"code": "invalid_delta", "message": f"not a gzip stream: {e}"}) from e
    if result["created"]:
        worker.schedule(background, get_engine(), store)
    else:
        response.status_code = status.HTTP_200_OK
    return result


@router.post("/artifacts/missing")
def missing_artifacts(
    sha256: list[str] = Body(embed=True, max_length=100_000),  # noqa: B008
    conn: Connection = CONN,
    store: RawStore = Depends(get_store),
) -> dict:
    """Which of these files (SHA-256) the server lacks: a client then uploads only those."""
    shas = [s.lower() for s in sha256]
    if bad := [s for s in shas if not _SHA256_RE.match(s)]:
        raise HTTPException(422, f"not a SHA-256: {bad[0]!r}")
    return {"missing": service.missing_artifacts(conn, store, shas)}


@router.post("/gadgetbridge/files", status_code=status.HTTP_202_ACCEPTED)
async def upload_gadgetbridge_files(
    request: Request, background: BackgroundTasks, response: Response, store: RawStore = Depends(get_store)
) -> dict:
    """A file set by manifest: the files of a Gadgetbridge ZIP export (database excluded) or of
    its FIT export (paths `fit_export/<name>`). Multipart: `manifest` (JSON `{"v": 1, "origin",
    "exported_at", "entries": [{"path", "sha256", "size"}]}`), optional `client_meta`, and a
    `files` part per file the server lacks (see `POST /artifacts/missing`). Safe to repeat.

    409 `missing_blobs` lists the files still to send.
    """
    form = await request.form(max_files=10_000, max_part_size=64 * 1024 * 1024)
    raw = form.get("manifest")
    if not isinstance(raw, str):
        raise HTTPException(422, "manifest (JSON text) is required")
    try:
        m = manifest.parse(raw)
    except manifest.ManifestError as e:
        raise HTTPException(422, {"code": "invalid_manifest", "message": str(e)}) from e
    client = _client_meta(form.get("client_meta") if isinstance(form.get("client_meta"), str) else None)
    blobs = {}
    for part in form.getlist("files"):
        if isinstance(part, str):
            raise HTTPException(422, "files must be file parts named by their SHA-256")
        blobs[(part.filename or "").lower()] = part.file
    try:
        batch_id, created = await run_in_threadpool(
            service.register_files, get_engine(), store, m, blobs, client
        )
    except service.MissingBlobs as e:
        raise HTTPException(409, {"code": "missing_blobs", "missing": e.missing[:5000]}) from e
    except manifest.ManifestError as e:
        raise HTTPException(422, {"code": "invalid_file", "message": str(e)}) from e
    if created:
        worker.schedule(background, get_engine(), store)
    else:
        response.status_code = status.HTTP_200_OK
    return {
        "batch_id": batch_id,
        "created": created,
        "source": service.SOURCE_GB_FILES,
        "entries": len(m.entries),
        "received_files": len(blobs),
    }


def _register(file, background, response, store, source, client=None) -> dict:
    batch_id, created = service.register_upload(get_engine(), store, file.file, source, client)
    if created:
        worker.schedule(background, get_engine(), store)
    else:
        response.status_code = status.HTTP_200_OK
    return {"batch_id": batch_id, "created": created, "source": source}


@router.post("/batches/{batch_id}/reprocess", status_code=status.HTTP_202_ACCEPTED)
def reprocess_batch(
    batch_id: int,
    background: BackgroundTasks,
    conn: Connection = CONN,
    store: RawStore = Depends(get_store),
) -> dict:
    """Re-run inventory and parsers from the preserved raw export (e.g. after parser upgrades).

    The batch is queued again and parses every file anew (also files parsed before with the same
    parser version). An older snapshot than the applied one still does not overwrite newer data.
    """
    row = conn.execute(
        text("SELECT status, raw_pruned_at FROM ingest_batch WHERE id = :id"), {"id": batch_id}
    ).first()
    if row is None:
        raise HTTPException(404, "batch not found")
    if row.status == "processing":
        raise HTTPException(409, "batch is processing")
    if row.raw_pruned_at is not None:
        raise HTTPException(410, "the raw export of this batch was removed by retention")
    # committed before the worker (or a background task) looks for it
    with get_engine().begin() as queue:
        queue.execute(
            text("UPDATE ingest_batch SET status = 'pending', force_reparse = true WHERE id = :id"),
            {"id": batch_id},
        )
    worker.schedule(background, get_engine(), store)
    return {"batch_id": batch_id}


_BATCH_COLS = """id, source, outer_artifact_sha256, status, received_at, finished_at, as_of,
                 client_meta, error, raw_pruned_at"""


@router.get("/batches")
def list_batches(
    limit: int = Query(100, ge=1, le=1000),
    before_id: int | None = Query(None, description="Page: batches older than this id"),
    source: str | None = None,
    conn: Connection = CONN,
) -> list[dict]:
    """Import history, newest first, `limit` per page (next page: `before_id` = last id)."""
    rows = conn.execute(
        text(f"""SELECT {_BATCH_COLS} FROM ingest_batch
                 WHERE (CAST(:before AS bigint) IS NULL OR id < :before)
                   AND (CAST(:src AS text) IS NULL OR source = :src)
                 ORDER BY id DESC LIMIT :lim"""),
        {"before": before_id, "src": source, "lim": limit},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/status")
def ingest_status(conn: Connection = CONN) -> dict:
    """How fresh the data is, cheap enough to poll every minute.

    `data_version` changes whenever an import finished or the nightly full parse ran: a client
    reloads its view only then. `gb_db` is the applied Gadgetbridge database snapshot.
    """
    queue = conn.execute(
        text("""SELECT count(*) FILTER (WHERE status = 'pending') AS pending,
                       count(*) FILTER (WHERE status = 'processing') AS processing
                FROM ingest_batch WHERE status IN ('pending', 'processing')""")
    ).one()
    finished = conn.execute(text("SELECT max(finished_at) FROM ingest_batch")).scalar()
    state = gb_state.load(conn)
    sync = sync_state(conn)
    extra = conn.execute(
        text("""SELECT DISTINCT ON (source) source, as_of, (stats->>'processing_s')::float AS processing_s
                FROM ingest_batch ORDER BY source, received_at DESC, id DESC""")
    )
    for r in extra:
        if r.source in sync["sources"]:
            sync["sources"][r.source].update(as_of=r.as_of, processing_s=r.processing_s)
    w = worker.current()
    return {
        "server_time": service._now(),
        "data_version": f"{finished.isoformat() if finished else '-'}|"
        f"{state.last_full_at.isoformat() if state and state.last_full_at else '-'}",
        "capabilities": ["gb_db_v1", "gb_db_delta_v1", "files_v1"],
        "files_wanted": service.files_wanted(conn),
        "last_heart_rate_at": sync["last_heart_rate_at"],
        "queue": dict(queue._mapping),
        "worker": "off" if w is None else ("ok" if w.alive else "stopped"),
        "sources": sync["sources"],
        "gb_db": None
        if state is None
        else {
            "applied_batch_id": state.batch_id,
            "applied_sha256": state.artifact_sha256,
            "as_of": state.as_of,
            "last_full_at": state.last_full_at,
        },
    }


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int, conn: Connection = CONN) -> dict:
    row = conn.execute(
        text(f"SELECT {_BATCH_COLS}, stats FROM ingest_batch WHERE id = :id"), {"id": batch_id}
    ).first()
    if row is None:
        raise HTTPException(404, "batch not found")
    runs = conn.execute(
        text("""SELECT id, artifact_sha256, parser_name, parser_version, status, started_at,
                       finished_at, stats, error
                FROM parse_run WHERE batch_id = :id ORDER BY id"""),
        {"id": batch_id},
    )
    return {**row._mapping, "parse_runs": [dict(r._mapping) for r in runs]}


@router.get("/artifacts/{sha256}")
def get_artifact(sha256: str, conn: Connection = CONN) -> dict:
    """Is this file already on the server? Lets clients skip uploading an export twice.

    `batches` are the imports of this exact file (as uploaded export); 404 if never seen.
    """
    sha = sha256.lower()
    if not _SHA256_RE.match(sha):
        raise HTTPException(422, "sha256 must be 64 hex characters")
    row = conn.execute(
        text("SELECT sha256, size, media_type, first_seen_at, pruned_at FROM raw_artifact WHERE sha256 = :s"),
        {"s": sha},
    ).first()
    if row is None:
        raise HTTPException(404, "unknown artifact")
    batches = conn.execute(
        text(f"SELECT {_BATCH_COLS} FROM ingest_batch WHERE outer_artifact_sha256 = :s ORDER BY id DESC"),
        {"s": sha},
    )
    return {**row._mapping, "stored": row.pruned_at is None, "batches": [dict(b._mapping) for b in batches]}


@router.get("/retention")
def retention_status(conn: Connection = CONN) -> dict:
    """Raw store size and what the next prune would remove."""
    days = get_settings().raw_retention_days
    totals = conn.execute(
        text("""SELECT count(*) FILTER (WHERE pruned_at IS NULL) AS stored_artifacts,
                       coalesce(sum(size) FILTER (WHERE pruned_at IS NULL), 0)::bigint AS stored_bytes,
                       count(*) FILTER (WHERE pruned_at IS NOT NULL) AS pruned_artifacts,
                       coalesce(sum(size) FILTER (WHERE pruned_at IS NOT NULL), 0)::bigint AS pruned_bytes
                FROM raw_artifact""")
    ).one()
    return {
        **totals._mapping,
        "pending": retention.prune(get_engine(), get_store(), days, dry_run=True),
    }


@router.post("/prune")
def prune_raw(dry_run: bool = False, store: RawStore = Depends(get_store)) -> dict:
    """Apply the retention rule now (it also runs after every successful import)."""
    return retention.prune(get_engine(), store, get_settings().raw_retention_days, dry_run=dry_run)
