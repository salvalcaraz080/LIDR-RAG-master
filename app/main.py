import uuid

import structlog
from fastapi import FastAPI, Request

from app.logging_config import configure_logging
from app.routers import estimations

configure_logging()  # before anything else logs

app = FastAPI(
    title="Estimador CAG",
    description="Sistema de estimación de software con arquitectura CAG",
    version="0.1.0",
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