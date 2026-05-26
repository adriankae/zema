from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import install_error_handlers, router
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.services import bootstrap_data, catch_up_episode_phases
from app.scheduler import start_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    try:
        bootstrap_data(db)
        catch_up_episode_phases(db, utc_now(), reason="startup")
    finally:
        db.close()
    thread = start_scheduler()
    yield
    if thread is not None and thread.is_alive():
        # Daemon thread exits with process.
        pass


app = FastAPI(title="Eczema Treatment Tracker", lifespan=lifespan)
install_error_handlers(app)
app.include_router(router)
