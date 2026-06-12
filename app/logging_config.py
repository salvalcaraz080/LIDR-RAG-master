import logging

import structlog

from app.config import get_settings


def configure_logging() -> None:
    """Configure structlog once at startup. Console in dev, JSON in production.

    Driven by Settings.APP_ENV. Must be called before the app handles requests.
    """
    settings = get_settings()
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    shared_processors = [
        # Pulls contextvars (e.g. request_id bound in middleware) into every event.
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.EventRenamer("msg"),
    ]

    if settings.APP_ENV == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )