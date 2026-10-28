"""AI settings and summaries (local model via an OpenAI-compatible API)."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from vitalocal import ai
from vitalocal.db import CONN, get_engine

router = APIRouter(prefix="/api/v1", tags=["ai"])


class AiSettingsIn(BaseModel):
    enabled: bool = False
    base_url: str = Field("", max_length=300)
    model: str = Field("", max_length=200)
    api_key: str | None = Field(None, description="null keeps the stored key, empty string clears it")
    temperature: float = Field(0.3, ge=0, le=2)
    timeout_s: int = Field(180, ge=5, le=900)
    focus: str = Field("", max_length=500)
    vision: bool = False
    share: dict[Literal["summary", "coach", "labs", "journal"], bool] | None = Field(
        None, description="what an external model may be used for / see; null keeps the stored choice"
    )


@router.get("/settings/ai")
def get_ai_settings(conn: Connection = CONN) -> dict:
    return ai.load_settings(conn).public()


@router.put("/settings/ai")
def put_ai_settings(body: AiSettingsIn, conn: Connection = CONN) -> dict:
    if body.base_url and not body.base_url.startswith(("http://", "https://")):
        raise HTTPException(422, "base_url must start with http:// or https://")
    current = ai.load_settings(conn)
    new = ai.AiSettings(
        enabled=body.enabled,
        base_url=body.base_url.strip(),
        model=body.model.strip(),
        api_key=current.api_key if body.api_key is None else body.api_key,
        temperature=body.temperature,
        timeout_s=body.timeout_s,
        focus=body.focus.strip(),
        vision=body.vision,
        share={**ai.DEFAULT_SHARE, **(current.share or {}), **(body.share or {})},
    )
    ai.save_settings(conn, new)
    return new.public()


class TestIn(BaseModel):
    base_url: str
    api_key: str | None = None


@router.post("/ai/models")
def ai_models(body: TestIn, conn: Connection = CONN) -> dict:
    """Connection test: models offered by the endpoint (uses the stored key if none is given)."""
    s = ai.load_settings(conn)
    s.base_url = body.base_url
    if body.api_key is not None:
        s.api_key = body.api_key
    try:
        return {"ok": True, "models": ai.list_models(s)}
    except ai.AiError as e:
        return {"ok": False, "error": str(e)}


class SummaryIn(BaseModel):
    scope: Literal["day", "week"] = "day"
    day: date
    lang: Literal["de", "en"] = "de"


@router.get("/ai/summary")
def get_summary(
    day: date,
    scope: Literal["day", "week"] = "day",
    lang: Literal["de", "en"] = "de",
    conn: Connection = CONN,
) -> dict:
    s = ai.load_settings(conn)
    on = ai.available(s, "summary")
    return {"enabled": on, "model": s.model if on else None, "summary": ai.latest(conn, scope, day, lang)}


@router.post("/ai/summary")
def create_summary(body: SummaryIn) -> dict:
    # Own transaction: the model call can take minutes and must not hold a request transaction.
    with get_engine().begin() as conn:
        try:
            return ai.summarize(conn, body.scope, body.day, body.lang)
        except ai.AiError as e:
            raise HTTPException(502, str(e)) from e
