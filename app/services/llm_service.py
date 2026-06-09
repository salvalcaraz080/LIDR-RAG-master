import structlog

from app.config import get_settings
from app.context.examples import ESTIMATION_EXAMPLES, format_examples_for_prompt
from app.services import llm_wrapper

log = structlog.get_logger()

MAX_TOKENS = 4000  # domain decision: estimations fit comfortably under this


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
    """Assemble the chat messages — domain-specific structure."""
    return [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": transcription},
    ]


def generate_estimation(transcription: str) -> dict:
    """Non-streaming: full estimation in one object. For programmatic consumers."""
    model = get_settings().LLM_MODEL
    messages = _build_messages(transcription)

    log.info("generating_estimation", model=model)
    result = llm_wrapper.complete(messages, model, max_tokens=MAX_TOKENS)

    # Map the wrapper's generic 'content' to the domain term 'estimation'.
    return {
        "estimation": result["content"],
        "model": result["model"],
        "provider": result["provider"],
        "usage": result["usage"],
    }


def generate_estimation_stream(transcription: str):
    """Streaming: yields the wrapper's typed events. For conversational UIs."""
    model = get_settings().LLM_MODEL
    messages = _build_messages(transcription)

    log.info("generating_estimation_stream", model=model)
    yield from llm_wrapper.stream(messages, model, max_tokens=MAX_TOKENS)