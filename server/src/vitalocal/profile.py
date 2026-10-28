"""The person behind the data: body data for energy estimates, units, goal and which areas they use.

Stored as `app_setting` rows (`profile`, `modules`). Weight is not part of the profile: it comes
from the latest body measurement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, text

from vitalocal.diary import put_setting, setting

PROFILE_KEY = "profile"
MODULES_KEY = "modules"

DEFAULT_PROFILE = {
    "sex": None,  # "female" | "male"
    "birth_year": None,
    "height_cm": None,
    "pal": 1.4,  # everyday activity without training (1.2 sitting … 1.9 physical work)
    # where the daily burn comes from: watch, estimate (profile + training), manual (own TDEE)
    # or auto (watch where it has both values, otherwise estimate / manual)
    "energy_source": "auto",
    "manual_tdee_kcal": None,  # own estimate of a day without training
    "units": "metric",  # "metric" | "imperial"
    "lang": "de",  # language of texts the server makes (meal names, exercise names, labels)
    "goal": "keep",  # "lose" | "keep" | "gain"
    "setup_done": False,
}
MODULES = ("nutrition", "drinks", "training", "habits", "journal", "health", "supplements", "watch")


def get_profile(conn: Connection) -> dict:
    v = setting(conn, PROFILE_KEY)
    return {**DEFAULT_PROFILE, **(v if isinstance(v, dict) else {})}


def put_profile(conn: Connection, values: dict) -> dict:
    p = {**get_profile(conn), **{k: v for k, v in values.items() if k in DEFAULT_PROFILE}}
    put_setting(conn, PROFILE_KEY, json.dumps(p))
    return p


def lang(conn: Connection) -> str:
    return "en" if get_profile(conn).get("lang") == "en" else "de"


def is_complete(p: dict) -> bool:
    return bool(p["sex"] and p["birth_year"] and p["height_cm"])


def has_watch_data(conn: Connection, days: int = 30) -> bool:
    """Whether a watch (or Health Connect) delivered energy data recently."""
    since = datetime.now(UTC) - timedelta(days=days)
    return bool(
        conn.execute(
            text("""SELECT 1 FROM sample WHERE metric IN ('active_energy', 'resting_metabolic_rate')
                    AND ts >= :s LIMIT 1"""),
            {"s": since},
        ).first()
    )


def get_modules(conn: Connection, watch_data: bool | None = None) -> dict[str, bool]:
    """Areas shown in the WebUI and app; watch tiles default to off without watch data."""
    v = setting(conn, MODULES_KEY)
    stored = v if isinstance(v, dict) else {}
    out = {m: bool(stored.get(m, True)) for m in MODULES}
    if "watch" not in stored:
        out["watch"] = has_watch_data(conn) if watch_data is None else watch_data
    return out


def put_modules(conn: Connection, values: dict[str, bool]) -> dict[str, bool]:
    v = setting(conn, MODULES_KEY)
    stored = {**(v if isinstance(v, dict) else {}), **{k: bool(b) for k, b in values.items() if k in MODULES}}
    put_setting(conn, MODULES_KEY, json.dumps(stored))
    return get_modules(conn)
