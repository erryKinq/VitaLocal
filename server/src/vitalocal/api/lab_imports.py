"""Import of lab reports: upload → the local model reads it → review → report.

The upload is stored in the raw store and read in the background. The model's transcription is a
proposal; `confirm` saves exactly the values sent with it (the ones confirmed in the review).
Discarding an import deletes it together with the uploaded file.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import Connection, text

from vitalocal import ai, labs_import
from vitalocal.api.ingest import _client_meta
from vitalocal.api.labs import ReportIn, _report, drop_artifact_if_unused, insert_report, store_upload
from vitalocal.db import CONN, get_engine
from vitalocal.deps import get_store
from vitalocal.rawstore import RawStore

router = APIRouter(prefix="/api/v1", tags=["labs"])

_COLS = """id, artifact_sha256, filename, media_type, status, pages, error, model, duration_ms, client_meta,
           report_id, created_at, started_at, finished_at"""
STALE = timedelta(minutes=30)


def _import(conn: Connection, iid, with_proposal: bool = True) -> dict:
    row = conn.execute(
        text(f"SELECT {_COLS}{', proposal' if with_proposal else ''} FROM lab_import WHERE id = :id"),
        {"id": iid},
    ).first()
    if row is None:
        raise HTTPException(404, "import not found")
    return dict(row._mapping)


@router.post("/labs/imports", status_code=status.HTTP_202_ACCEPTED)
def upload_report(
    file: UploadFile,
    background: BackgroundTasks,
    response: Response,
    client_meta: str | None = Form(None, description="Optional JSON object about the uploader"),
    store: RawStore = Depends(get_store),
) -> dict:
    """Upload a lab report (PDF or image). The local model reads it in the background; poll
    `GET /labs/imports/{id}` until `status` is `ready`, then review and confirm.

    The same file while an earlier import of it is still open returns that import (200)."""
    client = _client_meta(client_meta)
    # Own transaction: it must be committed before the background task looks for the import.
    with get_engine().begin() as conn:
        s = ai.load_settings(conn)
        if not ai.available(s, "labs"):
            raise HTTPException(409, ai.unavailable_reason(s, "labs"))
        sha, media = store_upload(conn, store, file)
        existing = conn.execute(
            text("SELECT id FROM lab_import WHERE artifact_sha256 = :s AND status <> 'confirmed' LIMIT 1"),
            {"s": sha},
        ).scalar()
        if existing:
            response.status_code = status.HTTP_200_OK
            return _import(conn, existing, with_proposal=False)
        iid = uuid.uuid4()
        conn.execute(
            text("""INSERT INTO lab_import (id, artifact_sha256, filename, media_type, client_meta)
                    VALUES (:id, :s, :f, :m, CAST(:c AS jsonb))"""),
            {"id": iid, "s": sha, "f": (file.filename or "")[:200] or None, "m": media,
             "c": json.dumps(client) if client else None},
        )  # fmt: skip
        out = _import(conn, iid, with_proposal=False)
    background.add_task(labs_import.run, get_engine(), store, iid)
    return out


@router.get("/labs/imports")
def list_imports(include_done: bool = False, conn: Connection = CONN) -> list[dict]:
    """Open imports (pending, running, ready, failed), newest first; `include_done` adds confirmed ones."""
    rows = conn.execute(
        text(f"""SELECT {_COLS}, jsonb_array_length(COALESCE(proposal->'values', '[]')) AS n_values
                 FROM lab_import WHERE :all OR status <> 'confirmed' ORDER BY created_at DESC"""),
        {"all": include_done},
    )
    return [dict(r._mapping) for r in rows]


@router.get("/labs/imports/{iid}")
def get_import(iid: uuid.UUID, conn: Connection = CONN) -> dict:
    """The import with the model's proposal: header, values with catalog match, status and warnings."""
    return _import(conn, iid)


@router.get("/labs/imports/{iid}/pages/{n}")
def get_page(iid: uuid.UUID, n: int, conn: Connection = CONN, store: RawStore = Depends(get_store)):
    """Page n (1-based) of the upload as PNG, as the model saw it."""
    imp = _import(conn, iid, with_proposal=False)
    try:
        doc, _ = labs_import.pages(
            store.path_for(imp["artifact_sha256"]).read_bytes(), imp["media_type"], n - 1
        )
    except OSError as e:
        raise HTTPException(404, "file missing") from e
    if not doc:
        raise HTTPException(404, "no such page")
    return Response(doc[0].image, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/labs/imports/{iid}/file")
def get_import_file(iid: uuid.UUID, conn: Connection = CONN, store: RawStore = Depends(get_store)):
    imp = _import(conn, iid, with_proposal=False)
    return FileResponse(store.path_for(imp["artifact_sha256"]), media_type=imp["media_type"],
                        filename=imp["filename"] or "befund")  # fmt: skip


@router.post("/labs/imports/{iid}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_import(iid: uuid.UUID, background: BackgroundTasks, store: RawStore = Depends(get_store)) -> dict:
    """Read the upload again (e.g. after changing the model)."""
    with get_engine().begin() as conn:
        imp = _import(conn, iid, with_proposal=False)
        if imp["status"] == "confirmed":
            raise HTTPException(409, "import is already confirmed")
        s = ai.load_settings(conn)
        if not ai.available(s, "labs"):
            raise HTTPException(409, ai.unavailable_reason(s, "labs"))
        since = imp["started_at"] or imp["created_at"]
        if imp["status"] in ("pending", "running") and datetime.now(UTC) - since < STALE:
            raise HTTPException(409, "import is still running")
        conn.execute(
            text("UPDATE lab_import SET status = 'pending', error = NULL, started_at = now() WHERE id = :id"),
            {"id": iid},
        )
        out = _import(conn, iid, with_proposal=False)
    background.add_task(labs_import.run, get_engine(), store, iid)
    return out


@router.post("/labs/imports/{iid}/confirm", status_code=status.HTTP_201_CREATED)
def confirm_import(iid: uuid.UUID, body: ReportIn, conn: Connection = CONN) -> dict:
    """Save the reviewed report: exactly the values in the body, with the upload as original file.

    The model's proposal is kept with the report (`import_meta`) so changes made in the review stay
    traceable. Repeating the call returns the report created the first time."""
    imp = _import(conn, iid)
    if imp["status"] == "confirmed" and imp["report_id"]:
        return _report(conn, imp["report_id"])
    if imp["status"] != "ready":
        raise HTTPException(409, f"import is {imp['status']}, not ready")
    if not body.values:
        raise HTTPException(422, "confirm at least one value")
    meta = {"import_id": str(iid), "model": imp["model"], "duration_ms": imp["duration_ms"],
            "proposal": imp["proposal"]}  # fmt: skip
    rid = insert_report(conn, body, imp["artifact_sha256"], imp["filename"], "import", meta)
    conn.execute(
        text("UPDATE lab_import SET status = 'confirmed', report_id = :r WHERE id = :id"),
        {"r": rid, "id": iid},
    )
    return _report(conn, rid)


@router.delete("/labs/imports/{iid}", status_code=status.HTTP_204_NO_CONTENT)
def discard_import(iid: uuid.UUID, conn: Connection = CONN, store: RawStore = Depends(get_store)) -> Response:
    """Discard an import; the uploaded file goes too unless a report uses it."""
    row = conn.execute(
        text("DELETE FROM lab_import WHERE id = :id RETURNING artifact_sha256"), {"id": iid}
    ).first()
    if row:
        drop_artifact_if_unused(conn, store, row.artifact_sha256)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
