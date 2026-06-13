import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aredis
import structlog
from fastapi import FastAPI, Request

from app.config import get_settings
from app.logging_config import configure_logging
from app.routers import estimations

configure_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: open the Redis client (lazy — real connection on first command).
    settings = get_settings()
    app.state.redis = aredis.from_url(
        settings.REDIS_URL,
        decode_responses=True, 
    )
    log = structlog.get_logger()
    log.info("redis_client_initialized", url=settings.REDIS_URL)

    yield  # ── app runs here ──

    # Shutdown: close the connection pool cleanly.
    await app.state.redis.aclose()
    log.info("redis_client_closed")


app = FastAPI(
    title="Estimador CAG",
    description="Sistema de estimación de software con arquitectura CAG",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def bind_request_context(request: Request, call_next):
    """Bind a unique request_id so every log in this request carries it."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=str(uuid.uuid4())[:8],
        path=request.url.path,
    )
    return await call_next(request)


app.include_router(estimations.router)


@app.get("/health")
async def health():
    return {"status": "healthy"}




