"""Request bodies of Connect IQ watch apps in Garmin's serialization, turned into JSON.

A watch app's `makeWebRequest` sends its parameters serialized by the watch. Garmin Connect (and
Gadgetbridge from Sep 2026 on, commit #6774) turns them into JSON on the phone; Gadgetbridge 0.94.0
forwards the raw bytes. This middleware decodes such bodies so the regular JSON endpoints serve
the VitaLocal watch app either way. Format after Gadgetbridge's GarminJson: an optional string
section (magic ABCDABCD, length, entries of u16 length + UTF-8 + NUL), then a data section (magic
DA7ADA7A, length) whose values are decoded breadth-first: containers first give their size, their
children follow after all values of the same level. Big-endian throughout.
"""

import json
import struct
from collections import deque

STRING_MAGIC = b"\xab\xcd\xab\xcd"
DATA_MAGIC = b"\xda\x7a\xda\x7a"
_WRITE_METHODS = {"POST", "PUT", "PATCH"}


class GarminBodyError(ValueError):
    pass


class _Container:
    def __init__(self, value, size: int):
        self.value, self.size = value, size


def is_garmin(body: bytes) -> bool:
    return body[:4] in (STRING_MAGIC, DATA_MAGIC)


def decode(body: bytes):
    """Garmin-serialized bytes -> Python value (dict, list, str, int, float, bool, None)."""
    try:
        return _decode(body)
    except (struct.error, IndexError, KeyError, UnicodeDecodeError) as e:
        raise GarminBodyError(f"not a valid Garmin body: {e}") from e


def _decode(body: bytes):
    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(body):
            raise GarminBodyError("truncated")
        pos += n
        return body[pos - n : pos]

    strings: dict[int, str] = {}
    magic = take(4)
    if magic == STRING_MAGIC:
        (length,) = struct.unpack(">I", take(4))
        start, end = pos, pos + length
        while pos < end:
            offset = pos - start
            (n,) = struct.unpack(">H", take(2))
            strings[offset] = take(n)[:-1].decode()  # minus the NUL terminator
        magic = take(4)
    if magic != DATA_MAGIC:
        raise GarminBodyError("no data section")
    take(4)  # data section length

    def value():
        t = take(1)[0]
        if t == 0x00:
            return None
        if t == 0x01:
            return struct.unpack(">i", take(4))[0]
        if t == 0x02:
            # 32-bit float: keep the digits the watch meant (64.3, not 64.30000305)
            return float(f"{struct.unpack('>f', take(4))[0]:.7g}")
        if t == 0x03:
            return strings[struct.unpack(">I", take(4))[0]]
        if t == 0x05:
            return _Container([], struct.unpack(">I", take(4))[0])
        if t == 0x09:
            return take(1)[0] != 0
        if t == 0x0B:
            return _Container({}, struct.unpack(">I", take(4))[0])
        if t == 0x0E:
            return struct.unpack(">q", take(8))[0]
        if t == 0x0F:
            return struct.unpack(">d", take(8))[0]
        raise GarminBodyError(f"unsupported type 0x{t:02x}")

    def unwrap(v, queue: deque):
        if isinstance(v, _Container):
            queue.append(v)
            return v.value
        return v

    queue: deque = deque()
    root = unwrap(value(), queue)
    while queue:
        c = queue.popleft()
        for _ in range(c.size):
            if isinstance(c.value, dict):
                key = value()
                c.value[str(key)] = unwrap(value(), queue)
            else:
                c.value.append(unwrap(value(), queue))
    return root


class GarminBodyMiddleware:
    """ASGI: a write request whose body is Garmin-serialized reaches the app as JSON."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in _WRITE_METHODS:
            return await self.app(scope, receive, send)
        chunks, more = [], True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":
                return await self.app(scope, _replay([msg], receive), send)
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        body = b"".join(chunks)
        if is_garmin(body):
            try:
                body = json.dumps(decode(body)).encode()
            except GarminBodyError as e:
                body = json.dumps({"garmin_body_error": str(e)}).encode()
            headers = [(k, v) for k, v in scope["headers"] if k not in (b"content-type", b"content-length")]
            headers += [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
            scope = {**scope, "headers": headers}
        message = {"type": "http.request", "body": body, "more_body": False}
        return await self.app(scope, _replay([message], receive), send)


def _replay(messages: list, then):
    """The already read messages once, then the client's own receive (e.g. its disconnect)."""
    pending = deque(messages)

    async def receive():
        return pending.popleft() if pending else await then()

    return receive
