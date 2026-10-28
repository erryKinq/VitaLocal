"""Encryption of stored secrets (API keys of external AI providers) with a server-side key.

The key comes from `VITALOCAL_SECRET_KEY` (a Fernet key) or, if unset, from a file generated once
next to the raw store. Values written before encryption existed are plain text; they are read as
they are and encrypted with the next save.
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from vitalocal.config import get_settings

PREFIX = "fernet:"
KEY_FILE = ".secret_key"


@lru_cache
def _fernet() -> Fernet:
    env = get_settings().secret_key
    if env and env.get_secret_value():  # compose passes an empty value when unset
        return Fernet(env.get_secret_value().encode())
    path = get_settings().raw_store_path / KEY_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def encrypt(value: str) -> str:
    return PREFIX + _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str) -> str:
    """Plain text (legacy) passes through; a value the current key cannot read becomes empty."""
    if not value.startswith(PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        return ""


def is_encrypted(value: str) -> bool:
    return value.startswith(PREFIX)
