import structlog
from litellm import acompletion, get_llm_provider

log = structlog.get_logger()


class LLMError(Exception):
    """Raised when the underlying LLM provider call fails."""


def _resolve_provider(model: str) -> str:
    """Infer the provider label from the model name (for logging/metadata)."""
    try:
        return get_llm_provider(model=model)[1]
    except Exception:
        return "unknown"


async def complete(messages: list[dict], model: str, max_tokens: int) -> dict:
    """Single-shot async completion. Returns a normalized dict."""
    provider = _resolve_provider(model)
    log.info("llm_call_started", model=model, provider=provider, stream=False)

    try:
        response = await acompletion(model=model, messages=messages, max_tokens=max_tokens)
    except Exception as exc:
        log.error("llm_call_failed", error=str(exc), model=model)
        raise LLMError(f"LLM call failed: {exc}") from exc

    usage = response.usage
    log.info(
        "llm_call_completed",
        model=model,
        provider=provider,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
    )

    return {
        "content": response.choices[0].message.content,
        "model": response.model,
        "provider": provider,
        "usage": {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
    }


async def stream(messages: list[dict], model: str, max_tokens: int):
    """Async streaming completion. Yields transport-agnostic typed events."""
    provider = _resolve_provider(model)
    log.info("llm_call_started", model=model, provider=provider, stream=True)

    try:
        response = await acompletion(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )

        async for chunk in response:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                yield {
                    "type": "done",
                    "metadata": {
                        "model": model,
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
        log.error("llm_stream_failed", error=str(exc), model=model)
        raise LLMError(f"LLM streaming call failed: {exc}") from exc