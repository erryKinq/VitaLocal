"""The Android companion APK, published to the server and downloadable from the WebUI.

`android/publish.sh` uploads a freshly built release; the file lives in the raw store's volume
(`_app/`, outside its content-addressed `xx/` folders) and is replaced on every upload. Downloads
need the API token like every other endpoint, so the WebUI fetches the file with its token
instead of linking to it.
"""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from vitalocal.config import get_settings

router = APIRouter(prefix="/api/v1", tags=["system"])

MAX_BYTES = 200 * 1024 * 1024
APK_TYPE = "application/vnd.android.package-archive"


def _dir() -> Path:
    return get_settings().raw_store_path / "_app"


def _meta() -> dict | None:
    try:
        return json.loads((_dir() / "android.json").read_text())
    except (OSError, ValueError):
        return None


@router.get("/app/android")
def android_info() -> dict | None:
    """Version, size and upload time of the published APK, or null when none is published."""
    return _meta()


@router.get("/app/android/apk")
def android_apk() -> FileResponse:
    meta, path = _meta(), _dir() / "VitaLocal.apk"
    if meta is None or not path.exists():
        raise HTTPException(404, "no app published")
    return FileResponse(path, media_type=APK_TYPE, filename=f"VitaLocal-{meta['version']}.apk")


@router.put("/app/android")
async def publish_android(
    file: Annotated[UploadFile, File()],
    version: Annotated[str, Form(pattern=r"^[0-9A-Za-z.+-]{1,40}$")],
    version_code: Annotated[int, Form(ge=1)],
) -> dict:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "VitaLocal.apk.tmp"
    sha, size = hashlib.sha256(), 0
    with tmp.open("wb") as out:
        while chunk := await file.read(1 << 20):
            if size == 0 and not chunk.startswith(b"PK"):
                tmp.unlink(missing_ok=True)
                raise HTTPException(415, "not an APK (ZIP) file")
            size += len(chunk)
            if size > MAX_BYTES:
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, "APK too large")
            sha.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, d / "VitaLocal.apk")
    meta = {
        "version": version,
        "version_code": version_code,
        "size": size,
        "sha256": sha.hexdigest(),
        "uploaded_at": datetime.now(UTC).isoformat(),
    }
    (d / "android.json").write_text(json.dumps(meta))
    return meta
