import redis.asyncio as aredis
import structlog

from app.config import get_settings
from app.prompts.loader import render_estimation_prompt
from app.services import cache
from app.services import llm_wrapper

log = structlog.get_logger()

MAX_TOKENS = 4000  # domain decision: estimations fit comfortably under this


def _build_messages(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
) -> list[dict]:
    """Assemble the chat messages via the versioned prompt templates."""
    system, user = render_estimation_prompt(
        description=description,
        project_type=project_type,
        detail_level=detail_level,
        output_format=output_format,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def generate_estimation(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
    redis: aredis.Redis,
) -> dict:
    """Non-streaming: full estimation in one object. For programmatic consumers."""
    model = get_settings().LLM_MODEL
    messages = _build_messages(description, project_type, detail_level, output_format)

    log.info("generating_estimation", model=model)
    result = await cache.cached_complete(messages, model, MAX_TOKENS, redis)

    return {
        "estimation": result["content"],
        "model": result["model"],
        "provider": result["provider"],
        "usage": result["usage"],
        "cache_hit": result["cache_hit"],
    }


async def generate_estimation_stream(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
):
    """Streaming: async generator of typed events. For conversational UIs."""
    model = get_settings().LLM_MODEL
    messages = _build_messages(description, project_type, detail_level, output_format)

    log.info("generating_estimation_stream", model=model)
    async for event in llm_wrapper.stream(messages, model, max_tokens=MAX_TOKENS):
        yield event
