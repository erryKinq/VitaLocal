"""Content-addressed, write-once storage for original source bytes."""

import hashlib
import io
import os
import tempfile
from pathlib import Path
from typing import BinaryIO

_CHUNK = 1 << 20


class RawStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def put_stream(self, src: BinaryIO) -> tuple[str, int]:
        """Store the stream's bytes; returns (sha256, size). Existing content is never rewritten."""
        self.root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        fd, tmp_name = tempfile.mkstemp(dir=self.root, prefix=".incoming-")
        try:
            with os.fdopen(fd, "wb") as tmp:
                while chunk := src.read(_CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                    tmp.write(chunk)
                tmp.flush()
                os.fsync(tmp.fileno())
            sha = digest.hexdigest()
            target = self.path_for(sha)
            if target.exists():
                os.unlink(tmp_name)
            else:
                target.parent.mkdir(exist_ok=True)
                os.chmod(tmp_name, 0o444)
                os.replace(tmp_name, target)
            return sha, size
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    def put_bytes(self, data: bytes) -> tuple[str, int]:
        return self.put_stream(io.BytesIO(data))

    def adopt(self, tmp: Path, sha256: str) -> None:
        """Move a finished, synced temp file (inside the store root) in place under its hash."""
        target = self.path_for(sha256)
        if target.exists():
            os.unlink(tmp)
            return
        target.parent.mkdir(exist_ok=True)
        os.chmod(tmp, 0o444)
        os.replace(tmp, target)

    def verify(self, sha256: str) -> bool:
        digest = hashlib.sha256()
        with open(self.path_for(sha256), "rb") as f:
            while chunk := f.read(_CHUNK):
                digest.update(chunk)
        return digest.hexdigest() == sha256
