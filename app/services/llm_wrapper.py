import structlog
from litellm import completion, get_llm_provider

log = structlog.get_logger()


class LLMError(Exception):
    """Raised when the underlying LLM provider call fails."""


def _resolve_provider(model: str) -> str:
    """Infer the provider label from the model name (for logging/metadata).

    Defensive: a provider label is metadata, not control flow — never crash
    on a name LiteLLM can't map. Use the explicit 'provider/model' form for
    names outside LiteLLM's registry.
    """
    try:
        return get_llm_provider(model=model)[1]
    except Exception:
        return "unknown"


def complete(messages: list[dict], model: str, max_tokens: int) -> dict:
    """Single-shot completion. Returns a normalized dict (provider-agnostic).

    Knows nothing about estimations — speaks only 'messages in, content out'.
    """
    provider = _resolve_provider(model)
    log.info("llm_call_started", model=model, provider=provider, stream=False)

    try:
        response = completion(model=model, messages=messages, max_tokens=max_tokens)
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


def stream(messages: list[dict], model: str, max_tokens: int):
    """Streaming completion. Yields transport-agnostic typed events:

      - {"type": "token", "data": str}      → text fragment, many of these
      - {"type": "done", "metadata": dict}  → final event with model/usage

    Raises LLMError on failure (possibly mid-stream); the caller decides how
    to surface it (the router turns it into an SSE 'error' event).
    """
    provider = _resolve_provider(model)
    log.info("llm_call_started", model=model, provider=provider, stream=True)

    try:
        response = completion(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )

        for chunk in response:
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