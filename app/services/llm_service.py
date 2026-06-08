import structlog
from litellm import completion, get_llm_provider

from app.config import get_settings
from app.context.examples import ESTIMATION_EXAMPLES, format_examples_for_prompt

log = structlog.get_logger()

MAX_TOKENS = 4000


class LLMServiceError(Exception):
    """Raised when the LLM provider call fails."""


def build_system_prompt() -> str:
    """Construct the system prompt with role definition and reference examples."""
    examples_text = format_examples_for_prompt(ESTIMATION_EXAMPLES)
    return (
        "You are a senior software consultant with 15+ years of experience in project "
        "estimation. Your task is to produce a detailed software project estimation based "
        "on a meeting transcription provided by the user.\n\n"
        "Below are reference estimations from previous projects. Use them as a guide for "
        "structure, level of detail, and realistic pricing. Adapt the content to match the "
        "specific project described in the transcription.\n\n"
        "Your output MUST follow this exact format:\n"
        "- Project title as an H2 heading\n"
        "- A task breakdown table with columns: Task, Hours, Cost (EUR)\n"
        "- Total hours\n"
        "- Total cost in EUR\n"
        "- Recommended team composition\n"
        "- Estimated duration in weeks\n\n"
        "Use a developer rate of approximately 62.50 EUR/hour (500 EUR/day) and a designer "
        "rate of approximately 50 EUR/hour (400 EUR/day). Provide realistic, well-justified "
        "numbers.\n\n"
        f"{examples_text}"
    )


def _build_messages(transcription: str) -> list[dict]:
    """Assemble the chat messages shared by streaming and non-streaming calls."""
    return [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": transcription},
    ]


def _resolve_provider(model: str) -> str:
    """Infer the provider label from the model name for logging/metadata.

    LiteLLM infers the provider from its model registry. Known aliases
    ('gpt-4o-mini', 'claude-haiku-4-5') resolve directly; for unmapped names
    use the explicit 'provider/model' form. Defensive: never crash on a name
    LiteLLM can't map — the provider label is metadata, not control flow.
    """
    try:
        return get_llm_provider(model=model)[1]
    except Exception:
        return "unknown"


def generate_estimation_stream(transcription: str):
    """Generate a software estimation as a stream of typed events (provider-agnostic).

    Yields dicts of two shapes:
      - {"type": "token", "data": str}      → a text fragment, many of these
      - {"type": "done", "metadata": dict}  → final event with model/usage info
    """
    settings = get_settings()
    model = settings.LLM_MODEL
    messages = _build_messages(transcription)
    provider = _resolve_provider(model)

    log.info("generating_estimation_stream", model=model, provider=provider)

    try:
        stream = completion(
            model=model,
            messages=messages,
            max_tokens=MAX_TOKENS,
            stream=True,
            stream_options={"include_usage": True},  
        )

        for chunk in stream:
            # Final usage-only chunk: no text, usage populated.
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

            # Normal content chunk. LiteLLM mirrors OpenAI's structure for all providers.
            delta = chunk.choices[0].delta.content
            if delta:
                yield {"type": "token", "data": delta}

    except Exception as exc:
        log.error("llm_stream_failed", error=str(exc), model=model)
        raise LLMServiceError(f"LLM streaming call failed: {exc}") from exc