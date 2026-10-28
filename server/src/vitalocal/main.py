import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from vitalocal import __version__
from vitalocal.api import (
    ai,
    coach,
    dashboard,
    data,
    days,
    drinks,
    export,
    goals,
    habit_rewards,
    habits,
    ingest,
    journal,
    lab_imports,
    labs,
    nutrition,
    profile,
    quality,
    supplements,
    templates,
    today,
    training,
    watch,
)
from vitalocal.api import app as app_api
from vitalocal.auth import require_token
from vitalocal.config import get_settings
from vitalocal.db import get_engine
from vitalocal.deps import get_store
from vitalocal.garmin_body import GarminBodyMiddleware
from vitalocal.ingest import worker

logging.basicConfig(level=logging.INFO)

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Imports run one at a time in the background (see ingest/worker.py).
    if get_settings().ingest_worker:
        worker.start(get_engine(), get_store())
    try:
        yield
    finally:
        worker.stop()


app = FastAPI(title="VitaLocal", version=__version__, lifespan=lifespan)
# the watch app's request bodies arrive Garmin-serialized through Gadgetbridge 0.94
app.add_middleware(GarminBodyMiddleware)

for router in (
    ingest.router,
    data.router,
    days.router,
    today.router,
    dashboard.router,
    drinks.router,
    goals.router,
    nutrition.router,
    templates.router,
    journal.router,
    habits.router,
    habit_rewards.router,
    supplements.router,
    labs.router,
    lab_imports.router,
    training.router,
    ai.router,
    coach.router,
    export.router,
    profile.router,
    app_api.router,
    quality.router,
    watch.router,
):
    app.include_router(router, dependencies=[Depends(require_token)])


@app.get("/health", tags=["system"])
def health() -> JSONResponse:
    checks = {}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["database"] = f"error: {type(e).__name__}"
    root = get_store().root
    try:
        root.mkdir(parents=True, exist_ok=True)
        checks["raw_store"] = "ok" if root.is_dir() else "missing"
    except OSError as e:
        checks["raw_store"] = f"error: {type(e).__name__}"
    w = worker.current()
    if w is not None:
        checks["ingest_worker"] = "ok" if w.alive else "stopped"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "version": __version__, **checks},
        status_code=200 if healthy else 503,
    )


class WebFiles(StaticFiles):
    """Static WebUI; `no-cache` makes browsers revalidate (ETag) so updates show up at once."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# The WebUI is static (no build step) and talks to the API with the bearer token.
# Mounted last so that API routes, /health and /docs take precedence.
app.mount("/", WebFiles(directory=WEB_DIR, html=True), name="web")
