import hashlib
import json
from typing import TypeVar

import redis.asyncio as aredis
import structlog
from pydantic import BaseModel

from app.config import get_settings
from app.services import llm_wrapper

log = structlog.get_logger()

CACHE_TTL_SECONDS = 86400  # 24h

T = TypeVar("T", bound=BaseModel)


def _cache_key(messages: list[dict], model: str, max_tokens: int) -> str:
    """Deterministic cache key from everything that affects the response.

    sort_keys=True → stable serialization regardless of dict key order.
    sha256 → stable across processes (Python's hash() is randomized per run).

    PENDING (B5): include prompt_version in key so a schema/prompt change
    auto-invalidates existing cache entries.
    """
    raw = json.dumps(
        {"messages": messages, "model": model, "max_tokens": max_tokens},
        sort_keys=True,
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"llm:{digest}"


async def cached_complete_structured(
    messages: list[dict],
    response_model: type[T],
    max_tokens: int,
    redis: aredis.Redis,
) -> tuple[T, dict, bool]:
    """Exact-match cache over structured completion. Returns (instance, metadata, cache_hit).

    On hit, deserializes the cached JSON back to response_model.
    Cache is an optimization: any Redis failure degrades gracefully to a direct call.
    """
    model = get_settings().LLM_MODEL
    key = _cache_key(messages, model, max_tokens)

    # --- Read ---
    try:
        cached_raw = await redis.get(key)
    except Exception as exc:
        log.warning("cache_read_failed", error=str(exc))
        cached_raw = None

    if cached_raw is not None:
        log.info("cache_hit", key=key)
        payload = json.loads(cached_raw)
        instance = response_model.model_validate(payload["result"])
        metadata = payload["metadata"]
        return instance, metadata, True

    # --- Miss ---
    log.info("cache_miss", key=key)
    instance, metadata = await llm_wrapper.complete_structured(
        messages, response_model, max_tokens
    )

    # --- Write ---
    payload = {
        "result": instance.model_dump(),
        "metadata": metadata,
    }
    try:
        await redis.set(key, json.dumps(payload), ex=CACHE_TTL_SECONDS)
    except Exception as exc:
        log.warning("cache_write_failed", error=str(exc))

    return instance, metadata, False
