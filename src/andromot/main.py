import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from .api import router as api_router
from .config import get_settings
from .logging import configure_logging
from .telegram import poll_telegram_updates
from .worker import default_processor, process_pending_jobs


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)

    worker_task = None
    telegram_task = None
    if settings.app_env.lower() not in ("test", "testing"):
        worker_task = asyncio.create_task(
            process_pending_jobs(default_processor, idle_delay_seconds=2.0)
        )
        if settings.telegram_enabled and settings.telegram_bot_token:
            telegram_task = asyncio.create_task(poll_telegram_updates())

    try:
        yield
    finally:
        for task in (worker_task, telegram_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


app = FastAPI(
    title="Andromot API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    return {
        "service": "Andromot IoT Platform",
        "status": "online",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health/ready",
    }


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    return {"status": "ok"}

