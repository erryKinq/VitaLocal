"""Lightweight FIT inventory: header metadata and message counts, no full normalization."""

from __future__ import annotations

import io
from collections import Counter
from datetime import datetime
from enum import Enum

import fitdecode

CATALOG_VERSION = "1.0.0"

_SESSION_FIELDS = ("sport", "sub_sport", "start_time", "total_elapsed_time", "total_timer_time")
_FILE_ID_FIELDS = ("type", "manufacturer", "product", "time_created")


def _jsonable(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Enum):
        return v.name
    if isinstance(v, int | float | str | bool) or v is None:
        return v
    return str(v)


def _fields(frame: fitdecode.FitDataMessage, names: tuple[str, ...]) -> dict:
    return {n: _jsonable(frame.get_value(n)) for n in names if frame.has_field(n)}


def is_fit(data: bytes) -> bool:
    return len(data) >= 12 and data[8:12] == b".FIT"


def catalog_fit(data: bytes) -> dict:
    """Return JSON-safe metadata for one FIT file. Never raises for malformed input."""
    meta: dict = {"catalog_version": CATALOG_VERSION}
    counts: Counter[str] = Counter()
    sessions: list[dict] = []
    has_position = False
    try:
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.RAISE) as reader:
            for frame in reader:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                counts[frame.name] += 1
                if frame.name == "file_id" and "file_id" not in meta:
                    meta["file_id"] = _fields(frame, _FILE_ID_FIELDS)
                elif frame.name == "session":
                    sessions.append(_fields(frame, _SESSION_FIELDS))
                elif (
                    frame.name == "record"
                    and not has_position
                    and frame.has_field("position_lat")
                    and frame.get_value("position_lat") is not None
                ):
                    has_position = True
    except Exception as e:  # noqa: BLE001 - catalog must record, not abort, on bad files
        meta["fit_error"] = f"{type(e).__name__}: {e}"
    meta["message_counts"] = dict(counts)
    if sessions:
        meta["sessions"] = sessions
    meta["has_position"] = has_position
    return meta
