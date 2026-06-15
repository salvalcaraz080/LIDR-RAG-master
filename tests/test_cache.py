"""Semantic cache tests — no API cost, no real Redis (SemanticCache mocked)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import cache

VECTOR = [0.0] * 1536
BUCKET = "v1:web_saas:medium:phases_table"


# ---------------------------------------------------------------------------
# build_bucket_key
# ---------------------------------------------------------------------------

def test_build_bucket_key_composes_parts():
    key = cache.build_bucket_key("v1", "web_saas", "medium", "phases_table")
    assert key == "v1:web_saas:medium:phases_table"


def test_build_bucket_key_distinguishes_params():
    a = cache.build_bucket_key("v1", "web_saas", "medium", "phases_table")
    b = cache.build_bucket_key("v1", "mobile_app", "medium", "phases_table")
    assert a != b


# ---------------------------------------------------------------------------
# semantic_lookup
# ---------------------------------------------------------------------------

def _cache_with_hit(response: str, distance: float = 0.05) -> MagicMock:
    c = MagicMock()
    c.acheck = AsyncMock(
        return_value=[{"response": response, "vector_distance": distance}]
    )
    return c


@pytest.mark.asyncio
async def test_lookup_enforce_returns_cached_response_on_hit():
    payload = json.dumps({"result": {"summary": "cached"}, "metadata": {}})
    c = _cache_with_hit(payload)
    out = await cache.semantic_lookup(c, VECTOR, BUCKET, enforce=True)
    assert out == payload


@pytest.mark.asyncio
async def test_lookup_log_only_returns_none_despite_neighbor():
    # Decision 23: log-only mode logs the neighbor but does NOT serve it.
    payload = json.dumps({"result": {"summary": "cached"}, "metadata": {}})
    c = _cache_with_hit(payload)
    out = await cache.semantic_lookup(c, VECTOR, BUCKET, enforce=False)
    assert out is None
    c.acheck.assert_awaited_once()  # lookup still ran (for logging)


@pytest.mark.asyncio
async def test_lookup_returns_none_when_no_hits():
    c = MagicMock()
    c.acheck = AsyncMock(return_value=[])
    out = await cache.semantic_lookup(c, VECTOR, BUCKET, enforce=True)
    assert out is None


@pytest.mark.asyncio
async def test_lookup_degrades_to_none_on_redis_error():
    c = MagicMock()
    c.acheck = AsyncMock(side_effect=RuntimeError("redis down"))
    out = await cache.semantic_lookup(c, VECTOR, BUCKET, enforce=True)
    assert out is None


# ---------------------------------------------------------------------------
# semantic_write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_passes_vector_and_bucket_filter():
    c = MagicMock()
    c.astore = AsyncMock(return_value="entry-id")
    await cache.semantic_write(c, VECTOR, BUCKET, "a description", '{"x":1}')
    c.astore.assert_awaited_once()
    kwargs = c.astore.await_args.kwargs
    assert kwargs["vector"] is VECTOR
    assert kwargs["filters"] == {cache.BUCKET_FIELD: BUCKET}


@pytest.mark.asyncio
async def test_write_degrades_silently_on_redis_error():
    c = MagicMock()
    c.astore = AsyncMock(side_effect=RuntimeError("redis down"))
    # Must not raise — cache write is non-fatal
    await cache.semantic_write(c, VECTOR, BUCKET, "a description", '{"x":1}')
