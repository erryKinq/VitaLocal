"""File sets of a Gadgetbridge ZIP export, uploaded by manifest: the phone lists every file
(path, SHA-256, size) and sends only the bytes the server lacks.

The canonical manifest (entries sorted by path, compact JSON) is stored as the batch's outer
artifact, so the same file set uploaded twice is the same batch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath

VERSION = 1
MAX_ENTRIES = 100_000
_HEX = frozenset("0123456789abcdef")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Manifest:
    origin: str
    exported_at: str | None
    entries: tuple[Entry, ...]

    def canonical(self) -> bytes:
        body = {
            "v": VERSION,
            "origin": self.origin,
            "exported_at": self.exported_at,
            "entries": [[e.path, e.sha256, e.size] for e in self.entries],
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _path(p) -> str:
    if not isinstance(p, str) or not p or len(p) > 1024:
        raise ManifestError("entry path missing or too long")
    pp = PurePosixPath(p)
    if p.startswith("/") or "\\" in p or any(part in ("", ".", "..") for part in p.split("/")):
        raise ManifestError(f"invalid path {p!r}")
    return str(pp)


def parse(raw: str | bytes) -> Manifest:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest is not valid JSON: {e.msg}") from e
    if not isinstance(data, dict) or data.get("v") != VERSION:
        raise ManifestError("manifest must be an object with v = 1")
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries or len(raw_entries) > MAX_ENTRIES:
        raise ManifestError(f"entries must be a list of 1 to {MAX_ENTRIES} files")
    seen: set[str] = set()
    entries = []
    for e in raw_entries:
        if isinstance(e, list) and len(e) == 3:
            e = {"path": e[0], "sha256": e[1], "size": e[2]}
        if not isinstance(e, dict):
            raise ManifestError("an entry must be {path, sha256, size}")
        path = _path(e.get("path"))
        sha = str(e.get("sha256", "")).lower()
        size = e.get("size")
        if len(sha) != 64 or not set(sha) <= _HEX:
            raise ManifestError(f"{path}: sha256 must be 64 hex characters")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ManifestError(f"{path}: size must be a non-negative integer")
        if path in seen:
            raise ManifestError(f"duplicate path {path}")
        seen.add(path)
        entries.append(Entry(path, sha, size))
    exported_at = data.get("exported_at")
    if exported_at is not None and not isinstance(exported_at, str):
        raise ManifestError("exported_at must be an ISO time")
    origin = str(data.get("origin") or "zip_export")[:64]
    return Manifest(origin, exported_at, tuple(sorted(entries, key=lambda x: x.path)))
