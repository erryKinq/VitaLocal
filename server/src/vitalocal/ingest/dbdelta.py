"""Live Gadgetbridge database uploads: the whole file, or only the blocks that changed (VLDD).

Gadgetbridge's database export is a plain copy of its SQLite file, and SQLite changes a file
page by page in place. Between two exports a few dozen pages change while the file grows by a
few, so the phone sends only those (block size = SQLite page size). The server applies them to
the previous file it has, and the SHA-256 of the result must match what the phone computed.

VLDD v1, all integers big-endian:

    offset  size        field
    0       4           magic "VLDD"
    4       1           version = 1
    5       3           reserved, 0
    8       4           block_size (power of two, 512 … 65536)
    12      8           base_size (must equal the stored base file)
    20      8           new_size
    28      4           count
    32      4 × count   block indices, strictly increasing, index × block_size < new_size
    …                   the blocks in index order: block i has min(block_size, new_size − i·block_size)
                        bytes; nothing follows the last block

The new file is the base cut or extended to new_size with the blocks written over it. Every block
reaching past the end of the base must be included.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import tempfile
from pathlib import Path
from typing import BinaryIO

MAGIC = b"VLDD"
VERSION = 1
_HEADER = struct.Struct(">4sB3sIQQI")  # 32 bytes
_CHUNK = 1 << 20
SQLITE_MAGIC = b"SQLite format 3\x00"


class DeltaError(ValueError):
    """The upload cannot be applied; `code` tells the client what to do next."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _read_exact(src: BinaryIO, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = src.read(n - len(out))
        if not chunk:
            raise DeltaError("truncated", f"upload ends after {len(out)} of {n} bytes")
        out += chunk
    return bytes(out)


def write_full(src: BinaryIO, dest_dir: Path, max_bytes: int) -> tuple[Path, str, int]:
    """Copy a whole (already decompressed) database into a temp file: (path, sha256, size)."""
    digest = hashlib.sha256()
    size = 0
    fd, name = tempfile.mkstemp(dir=dest_dir, prefix=".incoming-")
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := src.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise DeltaError("too_large", f"database larger than {max_bytes} bytes")
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        os.unlink(name)
        raise
    return Path(name), digest.hexdigest(), size


def apply(base: Path, src: BinaryIO, dest_dir: Path, max_bytes: int) -> tuple[Path, str, int]:
    """Apply a VLDD stream (already decompressed) to `base`: (path of the new file, sha256, size)."""
    magic, version, reserved, bs, base_size, new_size, count = _HEADER.unpack(_read_exact(src, _HEADER.size))
    if magic != MAGIC or version != VERSION or reserved != b"\0\0\0":
        raise DeltaError("invalid_delta", "not a VLDD v1 delta")
    if bs < 512 or bs > 65536 or bs & (bs - 1):
        raise DeltaError("invalid_delta", f"block size {bs} is not a power of two in 512…65536")
    if new_size > max_bytes:
        raise DeltaError("too_large", f"database larger than {max_bytes} bytes")
    n_blocks = -(-new_size // bs)
    if count > n_blocks:
        raise DeltaError("invalid_delta", f"{count} blocks for a file of {n_blocks}")
    actual_base = base.stat().st_size
    if base_size != actual_base:
        raise DeltaError("base_unavailable", f"base has {actual_base} bytes, the delta expects {base_size}")
    indices = struct.unpack(f">{count}I", _read_exact(src, 4 * count)) if count else ()
    if any(b <= a for a, b in zip(indices, indices[1:], strict=False)):
        raise DeltaError("invalid_delta", "block indices are not strictly increasing")
    if indices and indices[-1] >= n_blocks:
        raise DeltaError("invalid_delta", "block index past the end of the file")
    if new_size > base_size:
        needed = set(range(base_size // bs, n_blocks))
        if missing := needed - set(indices):
            first = sorted(missing)[:5]
            raise DeltaError("invalid_delta", f"blocks past the end of the base are missing: {first}")

    fd, name = tempfile.mkstemp(dir=dest_dir, prefix=".incoming-")
    try:
        with os.fdopen(fd, "r+b") as out:
            with open(base, "rb") as b:
                shutil.copyfileobj(b, out, _CHUNK)
            out.truncate(new_size)
            for i in indices:
                length = min(bs, new_size - i * bs)
                out.seek(i * bs)
                out.write(_read_exact(src, length))
            if src.read(1):
                raise DeltaError("invalid_delta", "bytes after the last block")
            out.flush()
            os.fsync(out.fileno())
            out.seek(0)
            digest = hashlib.sha256()
            while chunk := out.read(_CHUNK):
                digest.update(chunk)
    except BaseException:
        os.unlink(name)
        raise
    return Path(name), digest.hexdigest(), new_size


def check_sqlite(path: Path) -> None:
    """A complete SQLite file: magic, and the size the header announces."""
    with open(path, "rb") as f:
        head = f.read(100)
    size = path.stat().st_size
    if len(head) < 100 or not head.startswith(SQLITE_MAGIC):
        raise DeltaError("not_sqlite", "not an SQLite database")
    page_size = int.from_bytes(head[16:18], "big")
    page_size = 65536 if page_size == 1 else page_size
    change_counter, pages = int.from_bytes(head[24:28], "big"), int.from_bytes(head[28:32], "big")
    valid_for = int.from_bytes(head[92:96], "big")
    if page_size < 512 or size % page_size:
        raise DeltaError("truncated", f"{size} bytes is not a multiple of the page size {page_size}")
    if pages and valid_for == change_counter and pages * page_size != size:
        raise DeltaError("truncated", f"header: {pages} pages of {page_size} bytes, file: {size} bytes")
