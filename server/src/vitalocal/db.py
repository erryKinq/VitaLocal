from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends
from sqlalchemy import Connection, Engine, create_engine

from vitalocal.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


def get_conn() -> Iterator[Connection]:
    """FastAPI dependency: one transaction per request. Routes take it as `CONN` (scope "function"),
    so the transaction commits before the response goes out; with the default scope it would commit
    after, and a client reading right after a write could still see the old state."""
    with get_engine().begin() as conn:
        yield conn


# The connection parameter of a route: `conn: Connection = CONN` (commits before the response).
CONN = Depends(get_conn, scope="function")
