from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VITALOCAL_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://vitalocal:vitalocal@localhost:5432/vitalocal"
    raw_store_path: Path = Path("./raw_store")
    api_token: SecretStr
    # Day boundaries for daily values and the day view (IANA name)
    timezone: str = "Europe/Berlin"
    # Days an export's raw bytes stay after a newer export of the same source arrived (0 = forever)
    raw_retention_days: int = 7
    # Imports run one at a time in a background thread (off: only on demand, e.g. in tests)
    ingest_worker: bool = True
    # Gadgetbridge snapshots are parsed from each table's newest row of the last snapshot minus
    # this overlap; once a night the latest snapshot is parsed in full (local time HH:MM)
    gb_incremental: bool = True
    gb_window_hours: int = 72
    gb_full_parse_at: str = "03:30"
    # Largest live Gadgetbridge database accepted (decompressed)
    live_db_max_bytes: int = 256 * 1024 * 1024
    # Optional defaults for the local AI model (OpenAI-compatible API); editable in the WebUI
    ai_base_url: str | None = None
    ai_model: str | None = None
    ai_api_key: SecretStr | None = None
    # Fernet key for stored secrets (e.g. the AI API key); unset = generated in the raw store
    secret_key: SecretStr | None = None
    # Open Food Facts lookups (barcode, search on demand) through the server; the contact goes into
    # the User-Agent as OFF asks of API users
    off_enabled: bool = True
    off_contact: str | None = None
    off_base_url: str = "https://world.openfoodfacts.org"
    # online search: only products sold in this country (OFF countries tag; empty = all)
    off_country: str = "germany"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
