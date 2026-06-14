import structlog
from litellm import Router, get_llm_provider

from app.config import get_settings

log = structlog.get_logger()


class LLMError(Exception):
    """Raised when the underlying LLM provider call fails."""


def _resolve_provider(model: str) -> str:
    try:
        return get_llm_provider(model=model)[1]
    except Exception:
        return "unknown"


def _build_router() -> Router:
    primary_model = get_settings().LLM_MODEL
    log.info("llm_router_initialized", primary=primary_model, secondary="anthropic/claude-haiku-4-5-20251001")
    return Router(
        model_list=[
            {
                "model_name": "primary",
                "litellm_params": {"model": primary_model},
            },
            {
                "model_name": "secondary",
                "litellm_params": {"model": "anthropic/claude-haiku-4-5-20251001"},
            },
        ],
        fallbacks=[{"primary": ["secondary"]}],
    )


# Singleton: constructed once at import time. Preserves cooldown state across requests.
# Fails fast at startup if config is invalid — not on the first production request.
_router = _build_router()


async def complete(messages: list[dict], model: str, max_tokens: int) -> dict:
    """Single-shot async completion with automatic provider fallback.

    `model` is accepted for interface compatibility but the Router owns
    model selection — callers always get the best available provider.
    """
    log.info("llm_call_started", stream=False)

    try:
        response = await _router.acompletion(
            model="primary", messages=messages, max_tokens=max_tokens
        )
    except Exception as exc:
        log.error("llm_call_failed", error=str(exc))
        raise LLMError(f"LLM call failed: {exc}") from exc

    actual_model = response.model
    provider = _resolve_provider(actual_model)
    usage = response.usage
    log.info(
        "llm_call_completed",
        model=actual_model,
        provider=provider,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
    )

    return {
        "content": response.choices[0].message.content,
        "model": actual_model,
        "provider": provider,
        "usage": {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
    }


async def stream(messages: list[dict], model: str, max_tokens: int):
    """Async streaming with automatic provider fallback. Yields typed events:

      - {"type": "token", "data": str}      → text fragment
      - {"type": "done", "metadata": dict}  → final event with model/usage
    """
    log.info("llm_call_started", stream=True)

    try:
        response = await _router.acompletion(
            model="primary",
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )

        async for chunk in response:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                actual_model = getattr(chunk, "model", "unknown") or "unknown"
                provider = _resolve_provider(actual_model)
                yield {
                    "type": "done",
                    "metadata": {
                        "model": actual_model,
                        "provider": provider,
                        "usage": {
                            "input_tokens": usage.prompt_tokens,
                            "output_tokens": usage.completion_tokens,
                            "total_tokens": usage.total_tokens,
                        },
                    },
                }
                continue

            delta = chunk.choices[0].delta.content
            if delta:
                yield {"type": "token", "data": delta}

    except Exception as exc:
        log.error("llm_stream_failed", error=str(exc))
        raise LLMError(f"LLM streaming call failed: {exc}") from exc
