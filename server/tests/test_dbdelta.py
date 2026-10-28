"""VLDD deltas: applying changed blocks to a stored database, and every way an upload can be wrong."""

import hashlib
import io
import sqlite3
import struct

import pytest

from vitalocal.ingest import dbdelta

BS = 512
BASE = b"A" * BS + b"B" * BS + b"C" * BS
NEW = b"A" * BS + b"b" * BS + b"C" * BS + b"D" * BS


def vldd(base: bytes, new: bytes, bs: int = BS, indices: list[int] | None = None) -> bytes:
    """What the phone sends: every block of `new` that differs from `base` (or lies past it)."""
    n = -(-len(new) // bs)
    if indices is None:
        indices = [i for i in range(n) if new[i * bs : (i + 1) * bs] != base[i * bs : (i + 1) * bs]]
    head = struct.pack(">4sB3sIQQI", b"VLDD", 1, b"\0\0\0", bs, len(base), len(new), len(indices))
    return (
        head
        + struct.pack(f">{len(indices)}I", *indices)
        + b"".join(new[i * bs : (i + 1) * bs] for i in indices)
    )


# The Android app's DbDeltaTest builds the same payload and checks the same hash.
GOLDEN = vldd(BASE, NEW)
GOLDEN_SHA256 = "28d173eb516f7e5009f09a00128dc2289aeb19b684e36085ea5ee69ab61186a3"


def apply(tmp_path, base: bytes, payload: bytes, max_bytes: int = 1 << 20) -> bytes:
    b = tmp_path / "base"
    b.write_bytes(base)
    path, sha, size = dbdelta.apply(b, io.BytesIO(payload), tmp_path, max_bytes)
    data = path.read_bytes()
    assert sha == hashlib.sha256(data).hexdigest() and size == len(data)
    return data


def test_golden_vector():
    assert GOLDEN[:4] == b"VLDD" and len(GOLDEN) == 32 + 8 + 2 * BS
    assert struct.unpack(">2I", GOLDEN[32:40]) == (1, 3)
    assert hashlib.sha256(GOLDEN).hexdigest() == GOLDEN_SHA256


def test_grow_shrink_and_same_size(tmp_path):
    assert apply(tmp_path, BASE, GOLDEN) == NEW
    shorter = b"A" * BS + b"x" * 100
    assert apply(tmp_path, BASE, vldd(BASE, shorter)) == shorter
    same = b"A" * BS + b"B" * BS + b"c" * BS
    assert apply(tmp_path, BASE, vldd(BASE, same)) == same
    assert apply(tmp_path, BASE, vldd(BASE, BASE)) == BASE  # nothing changed: no blocks


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"NOPE" + GOLDEN[4:], "invalid_delta"),
        (GOLDEN[:4] + b"\x02" + GOLDEN[5:], "invalid_delta"),  # version
        (GOLDEN[:5] + b"\0\0\1" + GOLDEN[8:], "invalid_delta"),  # reserved
        (GOLDEN[:8] + struct.pack(">I", 1000) + GOLDEN[12:], "invalid_delta"),  # block size
        (vldd(BASE, NEW, indices=[3, 1]), "invalid_delta"),  # order
        (vldd(BASE, NEW, indices=[1, 1]), "invalid_delta"),  # duplicate
        (vldd(BASE, NEW, indices=[1]), "invalid_delta"),  # the new tail block is missing
        (GOLDEN + b"x", "invalid_delta"),  # trailing bytes
        (GOLDEN[:-10], "truncated"),
        (GOLDEN[:20], "truncated"),
        (vldd(BASE + b"Z" * BS, NEW), "base_unavailable"),  # built on another base
    ],
)
def test_invalid_deltas(tmp_path, payload, code):
    with pytest.raises(dbdelta.DeltaError) as e:
        apply(tmp_path, BASE, payload)
    assert e.value.code == code
    assert not list(tmp_path.glob(".incoming-*"))  # no temp file left behind


def test_index_past_the_end_and_size_limits(tmp_path):
    head = struct.pack(">4sB3sIQQI", b"VLDD", 1, b"\0\0\0", BS, len(BASE), len(BASE), 1)
    with pytest.raises(dbdelta.DeltaError) as e:
        apply(tmp_path, BASE, head + struct.pack(">I", 9) + b"x" * BS)
    assert e.value.code == "invalid_delta"
    with pytest.raises(dbdelta.DeltaError) as e:
        apply(tmp_path, BASE, GOLDEN, max_bytes=1000)
    assert e.value.code == "too_large"
    with pytest.raises(dbdelta.DeltaError) as e:
        dbdelta.write_full(io.BytesIO(b"x" * 2000), tmp_path, 1000)
    assert e.value.code == "too_large"


def test_sqlite_check(tmp_path):
    db = tmp_path / "x.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE t (x)")
        con.executemany("INSERT INTO t VALUES (?)", [("y" * 200,)] * 100)
    dbdelta.check_sqlite(db)
    data = db.read_bytes()
    cut = tmp_path / "cut.sqlite"
    cut.write_bytes(data[:-4096])
    with pytest.raises(dbdelta.DeltaError) as e:
        dbdelta.check_sqlite(cut)
    assert e.value.code == "truncated"
    junk = tmp_path / "junk"
    junk.write_bytes(b"x" * 8192)
    with pytest.raises(dbdelta.DeltaError) as e:
        dbdelta.check_sqlite(junk)
    assert e.value.code == "not_sqlite"
