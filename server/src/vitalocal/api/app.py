"""The Android companion APK, published to the server and downloadable from the WebUI.

`android/publish.sh` or the WebUI upload a release; optionally the server fetches new releases
from GitHub itself (`app_updates.py`). Storage in `apk.py`. Downloads need the API token like
every other endpoint, so the WebUI fetches the file with its token instead of linking to it.
"""

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from vitalocal import app_updates
from vitalocal.apk import ApkRejected, ApkWriter, apk_path, read_meta

router = APIRouter(prefix="/api/v1", tags=["system"])

APK_TYPE = "application/vnd.android.package-archive"


@router.get("/app/android")
def android_info() -> dict | None:
    """Version, size and upload time of the published APK, or null when none is published."""
    return read_meta()


@router.get("/app/android/apk")
def android_apk() -> FileResponse:
    meta, path = read_meta(), apk_path()
    if meta is None or not path.exists():
        raise HTTPException(404, "no app published")
    return FileResponse(path, media_type=APK_TYPE, filename=f"VitaLocal-{meta['version']}.apk")


@router.put("/app/android")
async def publish_android(
    file: Annotated[UploadFile, File()],
    version: Annotated[str, Form(pattern=r"^[0-9A-Za-z.+-]{1,40}$")],
    version_code: Annotated[int, Form(ge=1)],
) -> dict:
    # not at the same time as a GitHub download (never wait for it here: this is the event loop)
    if not app_updates.LOCK.acquire(blocking=False):
        raise HTTPException(409, "an app update is being downloaded from GitHub, try again shortly")
    try:
        w = ApkWriter()
        while chunk := await file.read(1 << 20):
            w.write(chunk)
        return w.publish(version, version_code)
    except ApkRejected as e:
        raise HTTPException(e.status, str(e)) from e
    finally:
        app_updates.LOCK.release()


class GithubUpdatesIn(BaseModel):
    enabled: bool
    repo: str = Field(pattern=app_updates.REPO_PATTERN)


@router.get("/app/android/github")
def github_updates() -> dict:
    """The switch for updates from GitHub releases, its repository and the last check."""
    return app_updates.load()


@router.put("/app/android/github")
def set_github_updates(body: GithubUpdatesIn) -> dict:
    return app_updates.configure(body.enabled, body.repo)


@router.post("/app/android/github/check")
def check_github_updates() -> dict:
    """Checks the latest release now (only while the switch is on) and publishes a newer APK."""
    if not app_updates.load()["enabled"]:
        raise HTTPException(409, "updates from GitHub releases are switched off")
    return app_updates.check()
