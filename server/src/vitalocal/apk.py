"""Storage of the published Android APK, shared by the upload endpoint and the GitHub updater.

The file lives in the raw store's volume (`_app/`, outside its content-addressed `xx/` folders)
next to `android.json` with its version, size and checksum, and is replaced on every publish.
"""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from vitalocal.config import get_settings

MAX_BYTES = 200 * 1024 * 1024
APK_NAME = "VitaLocal.apk"


class ApkRejected(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def app_dir() -> Path:
    return get_settings().raw_store_path / "_app"


def apk_path() -> Path:
    return app_dir() / APK_NAME


def read_meta() -> dict | None:
    try:
        return json.loads((app_dir() / "android.json").read_text())
    except (OSError, ValueError):
        return None


class ApkWriter:
    """Writes an APK to a temporary file chunk by chunk; `publish` makes it the served one."""

    def __init__(self):
        d = app_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.tmp = d / f"{APK_NAME}.tmp"
        self._out = self.tmp.open("wb")
        self.sha = hashlib.sha256()
        self.size = 0

    def write(self, chunk: bytes) -> None:
        if self.size == 0 and chunk and not chunk.startswith(b"PK"):
            self.discard()
            raise ApkRejected(415, "not an APK (ZIP) file")
        self.size += len(chunk)
        if self.size > MAX_BYTES:
            self.discard()
            raise ApkRejected(413, "APK too large")
        self.sha.update(chunk)
        self._out.write(chunk)

    def discard(self) -> None:
        self._out.close()
        self.tmp.unlink(missing_ok=True)

    def publish(self, version: str, version_code: int, source: str = "upload") -> dict:
        if self.size == 0:
            self.discard()
            raise ApkRejected(415, "not an APK (ZIP) file")
        self._out.flush()
        os.fsync(self._out.fileno())
        self._out.close()
        os.replace(self.tmp, apk_path())
        meta = {
            "version": version,
            "version_code": version_code,
            "size": self.size,
            "sha256": self.sha.hexdigest(),
            "uploaded_at": datetime.now(UTC).isoformat(),
            "source": source,
        }
        (app_dir() / "android.json").write_text(json.dumps(meta))
        return meta
