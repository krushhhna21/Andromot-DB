import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from .api import router as api_router
from .config import get_settings
from .logging import configure_logging
from .worker import default_processor, process_pending_jobs


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)

    worker_task = None
    if settings.app_env.lower() not in ("test", "testing"):
        worker_task = asyncio.create_task(
            process_pending_jobs(default_processor, idle_delay_seconds=2.0)
        )

    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task


app = FastAPI(
    title="Andromot API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)


@app.get("/health/live", tags=["health"])
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness() -> dict[str, str]:
    return {"status": "ok"}
