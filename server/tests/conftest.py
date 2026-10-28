import os
from pathlib import Path

import pytest

REAL_EXPORT = Path(__file__).resolve().parents[2] / "data" / "Gadgetbridge.zip"
TEST_DB_URL = os.environ.get("VITALOCAL_TEST_DATABASE_URL")


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    from vitalocal import config, db, vault

    monkeypatch.setenv("VITALOCAL_API_TOKEN", "test-token")
    monkeypatch.setenv("VITALOCAL_RAW_STORE_PATH", str(tmp_path / "raw_store"))
    if TEST_DB_URL:
        monkeypatch.setenv("VITALOCAL_DATABASE_URL", TEST_DB_URL)
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()
    vault._fernet.cache_clear()
    yield
    config.get_settings.cache_clear()
    db.get_engine.cache_clear()


@pytest.fixture
def engine(settings_env):
    """Fresh, migrated schema in the test database."""
    if not TEST_DB_URL:
        pytest.skip("VITALOCAL_TEST_DATABASE_URL not set")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from vitalocal.db import get_engine

    eng = get_engine()
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture
def store(settings_env):
    from vitalocal.deps import get_store

    return get_store()
