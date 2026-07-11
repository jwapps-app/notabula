"""FastAPI application entrypoint.

Generic naming throughout — the brand name appears only via settings.app_name
(a display string). The title is set from config so renaming touches nothing here.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import engine
from app.routers import api_router
from app.routers import health
from app.services.notifications import reminder_loop
from app.services.purge import purge_loop

# uvicorn only configures its own loggers; without this, app loggers
# (purge sweeps, push delivery, reminders) never reach docker logs.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Purge expired Recently Deleted notes now and then daily; check for
    # due note reminders every minute.
    purge_task = asyncio.create_task(purge_loop())
    reminder_task = asyncio.create_task(reminder_loop())
    yield
    purge_task.cancel()
    reminder_task.cancel()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    description=settings.app_tagline,
    version="0.1.0",
    lifespan=lifespan,
    debug=settings.debug,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health probes live at the root (not under /api/v1) for load balancers.
app.include_router(health.router)
app.include_router(api_router)

# Uploaded media (note attachments, Phase 2) — served at /media. The
# directory is a persisted volume in compose; nginx proxies /media/ here.
_media_dir = Path(settings.media_root)
try:
    _media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(_media_dir)), name="media")
except OSError:
    # Local dev outside Docker may not be able to create /app/media; the
    # mount only matters where uploads happen (the container).
    pass


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {"name": settings.app_name, "status": "ok", "docs": "/docs"}
