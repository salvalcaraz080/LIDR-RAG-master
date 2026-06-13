import hashlib
import json

import redis.asyncio as aredis
import structlog

from app.services import llm_wrapper

log = structlog.get_logger()

CACHE_TTL_SECONDS = 86400  # 24h — see design note on TTL


def _cache_key(messages: list[dict], model: str, max_tokens: int) -> str:
    """Deterministic cache key from everything that affects the response.

    sort_keys=True → stable serialization regardless of dict key order.
    sha256 → stable across processes (Python's hash() is randomized per run).
    """
    raw = json.dumps(
        {"messages": messages, "model": model, "max_tokens": max_tokens},
        sort_keys=True,
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"llm:{digest}"


async def cached_complete(
    messages: list[dict],
    model: str,
    max_tokens: int,
    redis: aredis.Redis,
) -> dict:
    """Exact-match cache layer over the wrapper's non-streaming completion.

    Cache is an optimization, not the source of truth: any Redis failure
    degrades gracefully to a direct LLM call (logged, not raised).
    """
    key = _cache_key(messages, model, max_tokens)

    # --- Read: a Redis failure is treated as a miss, never fatal ---
    try:
        cached = await redis.get(key)
    except Exception as exc:
        log.warning("cache_read_failed", error=str(exc))
        cached = None

    if cached is not None:
        log.info("cache_hit", key=key)
        result = json.loads(cached)
        result["cache_hit"] = True
        return result

    # --- Miss: call the LLM through the wrapper ---
    log.info("cache_miss", key=key)
    result = await llm_wrapper.complete(messages, model, max_tokens)

    # --- Write: store the clean payload; a failure is non-fatal ---
    try:
        await redis.set(key, json.dumps(result), ex=CACHE_TTL_SECONDS)
    except Exception as exc:
        log.warning("cache_write_failed", error=str(exc))

    result["cache_hit"] = False
    return result