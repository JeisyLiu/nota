"""ASGI app factory."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..application.container import AppContainer
from ..config import load_settings
from .api import router


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.loop = asyncio.get_running_loop()
    yield
    container: AppContainer = app.state.container
    container.pipeline.stop()


def create_app(settings=None, *, start_worker: bool = True) -> FastAPI:
    settings = settings or load_settings()
    container = AppContainer.build(settings, start_worker=start_worker)

    app = FastAPI(title="Nota MVP", version="0.1.0", lifespan=_lifespan)
    app.state.container = container
    app.state.devices = {}
    app.state.loop = None
    app.state.auto_run = start_worker
    app.include_router(router)
    return app
