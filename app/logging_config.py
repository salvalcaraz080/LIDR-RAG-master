import logging

import structlog

from app.config import get_settings


def configure_logging() -> None:
    """Configure structlog once at startup. Console in dev, JSON in production.

    Driven by Settings.APP_ENV. Must be called before the app handles requests.
    """
    settings = get_settings()
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # Procesadores comunes: inyectan contexto del request, nivel, timestamp y renombran
    # la clave del evento a "msg".
    shared_processors = [
        # Arrastra los contextvars (p. ej. request_id bindeado en el middleware) a cada evento.
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.EventRenamer("msg"),
    ]

    # Renderer según entorno: JSON (agregable) en producción, consola legible en desarrollo.
    # En producción dict_tracebacks convierte exc_info en un traceback estructurado dentro
    # del JSON; en desarrollo ConsoleRenderer ya pinta la excepción de forma legible.
    if settings.APP_ENV == "production":
        render_processors = [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
    else:
        render_processors = [structlog.dev.ConsoleRenderer()]

    # Filtra por nivel y cachea el logger (una sola configuración por proceso).
    structlog.configure(
        processors=shared_processors + render_processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )