"""Classification of members of a Gadgetbridge data-export ZIP."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_COMMENT_RE = re.compile(rb"application (?P<app>\S+) version (?P<version>\S+) from (?P<exported_at>\S+)")
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


@dataclass(frozen=True)
class MemberInfo:
    category: str
    device_identifier: str | None = None  # folder name under files/, usually the MAC


def classify(archive_path: str) -> MemberInfo:
    parts = PurePosixPath(archive_path).parts
    if parts == ("database", "Gadgetbridge"):
        return MemberInfo("gb_database")
    if parts and parts[0] == "preferences":
        return MemberInfo("preferences")
    if parts == ("gadgetbridge.json",):
        return MemberInfo("gb_manifest")
    if len(parts) >= 3 and parts[0] == "files" and _MAC_RE.match(parts[1]):
        # files/<device>/<CATEGORY>/[year/]<file>
        category = parts[2] if len(parts) >= 4 else "device_file"
        return MemberInfo(category, parts[1])
    return MemberInfo("other")


def parse_comment(comment: bytes) -> dict:
    m = _COMMENT_RE.search(comment or b"")
    if not m:
        return {}
    return {k: v.decode(errors="replace") for k, v in m.groupdict().items()}
