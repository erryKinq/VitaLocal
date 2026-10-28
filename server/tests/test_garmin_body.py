"""Garmin-serialized request bodies (Connect IQ through Gadgetbridge 0.94) become JSON."""

import struct
import uuid
from collections import deque

import pytest
from fastapi.testclient import TestClient

from vitalocal.garmin_body import GarminBodyError, decode, is_garmin


def encode(obj) -> bytes:
    """Port of Gadgetbridge's GarminJson.encode: strings first, values breadth-first."""
    strings: dict[str, int] = {}
    section = b""

    def collect(o):
        nonlocal section
        if isinstance(o, str) and o not in strings:
            strings[o] = len(section)
            raw = o.encode()
            section += struct.pack(">H", len(raw) + 1) + raw + b"\0"
        elif isinstance(o, dict):
            for k, v in o.items():
                collect(k), collect(v)
        elif isinstance(o, list):
            for v in o:
                collect(v)

    collect(obj)
    data, queue = b"", deque([obj])
    while queue:
        o = queue.popleft()
        if o is None:
            data += b"\x00"
        elif isinstance(o, bool):
            data += b"\x09" + bytes([o])
        elif isinstance(o, int):
            data += b"\x01" + struct.pack(">i", o)
        elif isinstance(o, float):
            data += b"\x02" + struct.pack(">f", o)
        elif isinstance(o, str):
            data += b"\x03" + struct.pack(">I", strings[o])
        elif isinstance(o, list):
            data += b"\x05" + struct.pack(">I", len(o))
            queue.extend(o)
        else:
            data += b"\x0b" + struct.pack(">I", len(o))
            for k, v in o.items():
                queue.extend((k, v))
    head = b"\xab\xcd\xab\xcd" + struct.pack(">I", len(section)) + section if section else b""
    return head + b"\xda\x7a\xda\x7a" + struct.pack(">I", len(data)) + data


def test_decode_nested_values():
    value = {"a": [1, {"b": None, "c": True}], "kg": 64.3, "s": "Grüner Tee", "n": -5, "e": {}}
    raw = encode(value)
    assert is_garmin(raw) and decode(raw) == value  # 64.3 survives the 32-bit float
    assert decode(encode(7)) == 7  # no string section
    with pytest.raises(GarminBodyError):
        decode(raw[:-3])


@pytest.mark.db
def test_watch_writes_with_garmin_bodies(engine):
    from vitalocal.main import app

    client = TestClient(app)
    auth = {"Authorization": "Bearer test-token", "Content-Type": "application/x-garmin"}
    drink = {
        "id": uuid.uuid4().hex,
        "ts": "2026-09-21T09:21:00+02:00",
        "drink": "coffee",
        "ml": 250,
        "source": "watch",
    }
    r = client.post("/api/v1/drinks", headers=auth, content=encode(drink))
    assert r.status_code == 201, r.text
    assert r.json()["ml"] == 250 and r.json()["caffeine"] is True
    weight = {"id": uuid.uuid4().hex, "metric": "weight", "value": 64.3, "source": "watch"}
    assert client.post("/api/v1/body", headers=auth, content=encode(weight)).json()["value"] == 64.3
    # plain JSON is untouched
    assert (
        client.post(
            "/api/v1/drinks", headers=auth | {"Content-Type": "application/json"}, json=drink
        ).status_code
        == 200
    )
