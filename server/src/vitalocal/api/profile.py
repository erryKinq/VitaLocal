"""Profile (body data, units, goal), visible areas and what the server can do (for the UI)."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from vitalocal import ai, profile
from vitalocal.db import CONN

router = APIRouter(prefix="/api/v1", tags=["profile"])


class ProfileIn(BaseModel):
    sex: Literal["female", "male"] | None = None
    birth_year: int | None = Field(None, ge=1900, le=2030)
    height_cm: float | None = Field(None, ge=100, le=250)
    pal: float = Field(1.4, ge=1.1, le=2.2)
    energy_source: Literal["auto", "watch", "estimate", "manual"] = "auto"
    manual_tdee_kcal: int | None = Field(None, ge=800, le=6000)
    units: Literal["metric", "imperial"] = "metric"
    lang: Literal["de", "en"] = "de"
    goal: Literal["lose", "keep", "gain"] = "keep"
    setup_done: bool = False


@router.get("/profile")
def get_profile(conn: Connection = CONN) -> dict:
    from vitalocal import energy  # energy builds on the profile

    p = profile.get_profile(conn)
    return {**p, "complete": profile.is_complete(p), "estimate": energy.profile_estimate(conn, p)}


@router.put("/profile", description="Fields left out keep their value.")
def put_profile(body: ProfileIn, conn: Connection = CONN) -> dict:
    profile.put_profile(conn, body.model_dump(exclude_unset=True))  # fields left out stay
    return get_profile(conn)


@router.get("/modules")
def get_modules(conn: Connection = CONN) -> dict[str, bool]:
    return profile.get_modules(conn)


@router.put("/modules")
def put_modules(body: dict[Literal[profile.MODULES], bool], conn: Connection = CONN) -> dict:  # type: ignore[valid-type]
    return profile.put_modules(conn, body)


@router.get("/capabilities")
def capabilities(conn: Connection = CONN) -> dict:
    """What the WebUI and app should offer: AI features, visible areas, setup state."""
    s = ai.load_settings(conn)
    p = profile.get_profile(conn)
    watch = profile.has_watch_data(conn)
    return {
        "ai": {
            "configured": s.configured,
            "external": s.external,
            "vision": s.vision,
            "features": {f: ai.available(s, f) for f in ("summary", "coach", "labs")},
            "journal": s.configured and s.shares("journal"),
        },
        "modules": profile.get_modules(conn, watch),
        "profile_complete": profile.is_complete(p),
        "setup_done": bool(p["setup_done"]),
        "has_watch_data": watch,
        "units": p["units"],
        "lang": p["lang"],
    }
