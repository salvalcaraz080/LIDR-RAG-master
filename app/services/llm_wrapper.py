"""LLM adapter — domain-agnostic.

Two modes:
  complete_structured  → single-shot, returns (T, metadata_dict). Uses Instructor
                         for structured output + retry on validation failure.
  stream_structured    → streaming Partial[T], yields fragments then metadata.
                         Validators are inactive during streaming (Partial skips them);
                         the service does post-hoc validation after the stream closes.

Fallback: primary model (from config) → anthropic/claude-haiku-4-5-20251001.
Mechanism: fallbacks=["anthropic/..."] kwarg on each acompletion call (list-of-strings
format required by litellm.acompletion — the Router dict format does NOT work here).

Trade-off vs S03 Router singleton:
  - LOST: cooldown state between requests; fail-fast at startup.
  - KEPT: OpenAI→Anthropic fallback on every call.
Reason: instructor.from_litellm(acompletion) is the only stable integration path;
instructor.patch(Router) has a known bug (carryover of params between requests).
"""

from typing import AsyncGenerator, TypeVar

import instructor
import structlog
from litellm import acompletion, get_llm_provider

from app.config import get_settings

log = structlog.get_logger()

T = TypeVar("T")

# Fallback list-of-strings format (NOT the Router dict format).
_FALLBACK_MODELS = ["anthropic/claude-haiku-4-5-20251001"]

# AsyncInstructor singleton — stateless, safe to share across requests.
_instructor = instructor.from_litellm(acompletion)


class LLMError(Exception):
    """Raised when the underlying LLM provider call fails."""


def _resolve_provider(model: str) -> str:
    try:
        return get_llm_provider(model=model)[1]
    except Exception:
        return "unknown"


def _usage_from_chunks(chunks) -> dict:
    """Extract real token usage from a consumed litellm stream wrapper's chunks.

    With stream_options include_usage, the final chunk carries a Usage object
    (the same numbers the API bills, including tool-schema overhead).
    """
    for chunk in reversed(chunks or []):
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            return {
                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            }
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _model_from_chunks(chunks, default: str) -> str:
    """Read the actual model from the stream chunks (reflects fallback if it fired)."""
    for chunk in chunks or []:
        model = getattr(chunk, "model", None)
        if model:
            return model
    return default


def _extract_metadata(raw) -> dict:
    """Extract model/provider/usage from a litellm ModelResponse."""
    actual_model = getattr(raw, "model", "unknown") or "unknown"
    provider = _resolve_provider(actual_model)
    usage = getattr(raw, "usage", None)
    hidden = getattr(raw, "_hidden_params", {})
    # litellm may expose the real provider in _hidden_params when fallback fired
    if provider == "unknown":
        provider = hidden.get("custom_llm_provider", "unknown")
    return {
        "model": actual_model,
        "provider": provider,
        "usage": {
            "input_tokens": getattr(usage, "prompt_tokens", 0),
            "output_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        },
    }


async def complete_structured(
    messages: list[dict],
    response_model: type[T],
    max_tokens: int,
    max_retries: int = 2,
) -> tuple[T, dict]:
    """Single-shot structured completion. Returns (typed_instance, metadata).

    Instructor retries up to max_retries times on Pydantic validation failure.
    Wraps all provider exceptions in LLMError.
    """
    model = get_settings().LLM_MODEL
    log.info("llm_structured_call_started", model=model, response_model=response_model.__name__)

    try:
        result, raw = await _instructor.chat.completions.create_with_completion(
            model=model,
            messages=messages,
            response_model=response_model,
            max_tokens=max_tokens,
            max_retries=max_retries,
            fallbacks=_FALLBACK_MODELS,
        )
    except Exception as exc:
        log.error("llm_structured_call_failed", error=str(exc))
        raise LLMError(f"LLM structured call failed: {exc}") from exc

    # Log how many retries Instructor needed (if it exposes it via the raw response)
    n_retries = getattr(raw, "_instructor_retry_count", None)
    metadata = _extract_metadata(raw)
    log.info(
        "llm_structured_call_completed",
        model=metadata["model"],
        provider=metadata["provider"],
        input_tokens=metadata["usage"]["input_tokens"],
        output_tokens=metadata["usage"]["output_tokens"],
        retries=n_retries,
    )
    return result, metadata


async def stream_structured(
    messages: list[dict],
    response_model: type[T],
    max_tokens: int,
) -> AsyncGenerator[dict, None]:
    """Streaming structured completion. Yields typed events:

      - {"type": "partial", "data": T_partial}   → incremental Partial[T] object
      - {"type": "done",    "metadata": dict}     → final event with model/provider

    Uses Instructor's create_partial (AsyncGenerator[T]). Validators in response_model
    are INACTIVE during streaming (Partial skips them); the service performs post-hoc
    validation after the stream closes.

    Real token usage: we pass stream_options={"include_usage": True} so litellm's final
    chunk carries a Usage object, captured via a completion:response hook and read from
    the wrapper's chunks. The hook is registered on a PER-CALL client (not the module
    singleton) so concurrent requests don't cross-contaminate captured state.
    """
    model = get_settings().LLM_MODEL
    log.info("llm_structured_stream_started", model=model)

    client = instructor.from_litellm(acompletion)
    captured: dict = {}
    client.on("completion:response", lambda response: captured.__setitem__("wrapper", response))

    try:
        stream = client.chat.completions.create_partial(
            model=model,
            messages=messages,
            response_model=response_model,
            max_tokens=max_tokens,
            fallbacks=_FALLBACK_MODELS,
            stream_options={"include_usage": True},
        )
        async for partial in stream:
            yield {"type": "partial", "data": partial}
    except Exception as exc:
        log.error("llm_structured_stream_failed", error=str(exc))
        raise LLMError(f"LLM streaming call failed: {exc}") from exc

    chunks = getattr(captured.get("wrapper"), "chunks", None)
    actual_model = _model_from_chunks(chunks, model)
    metadata = {
        "model": actual_model,
        "provider": _resolve_provider(actual_model),
        "usage": _usage_from_chunks(chunks),
    }
    log.info(
        "llm_structured_stream_completed",
        model=metadata["model"],
        provider=metadata["provider"],
        total_tokens=metadata["usage"]["total_tokens"],
    )
    yield {"type": "done", "metadata": metadata}
