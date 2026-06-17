import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request

from app.config import get_settings
from app.logging_config import configure_logging
from app.routers import estimations
from app.services.cache import make_semantic_cache

configure_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: build the semantic cache once. The constructor connects to Redis Stack
    # and creates the vector index (opens its own client via redis_url).
    settings = get_settings()
    log = structlog.get_logger()
    app.state.semantic_cache = make_semantic_cache(
        settings.REDIS_URL, settings.SEMANTIC_CACHE_DISTANCE_THRESHOLD
    )
    log.info(
        "semantic_cache_initialized",
        url=settings.REDIS_URL,
        distance_threshold=settings.SEMANTIC_CACHE_DISTANCE_THRESHOLD,
    )

    yield  # ── app runs here ──

    # Shutdown: nothing to close explicitly — redisvl manages its own pool.
    log.info("semantic_cache_shutdown")


app = FastAPI(
    title="Estimador CAG",
    description="Sistema de estimación de software con arquitectura CAG",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def bind_request_context(request: Request, call_next):
    """Bindea el contexto del request y emite un access-log con latencia y status."""
    # Limpia el contexto previo y bindea request_id + path: toda traza de esta petición
    # queda correlacionada (lo recoge merge_contextvars en logging_config).
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=str(uuid.uuid4())[:8],
        path=request.url.path,
    )
    log = structlog.get_logger()

    # Cronometra la petición. Un fallo no controlado se loguea con traceback y se re-lanza.
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        log.exception("request_failed", method=request.method, duration_ms=duration_ms)
        raise

    # Access-log: una línea por petición con método, status y latencia (clave para análisis).
    # Se omite /health (cada 30s desde Docker) para no inundar los logs.
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    if request.url.path != "/health":
        log.info(
            "request_completed",
            method=request.method,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
    return response


# Monta los endpoints de estimación (/api/v1/...).
app.include_router(estimations.router)


@app.get("/health")
async def health():
    # Health check para Docker/orquestador: no toca dependencias.
    return {"status": "healthy"}




