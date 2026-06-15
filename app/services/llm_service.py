import redis.asyncio as aredis
import structlog

from app.prompts.loader import render_estimation_prompt
from app.schemas.estimations import EstimationResult
from app.services import cache
from app.services import llm_wrapper
from app.services.guardrails import validate_input

log = structlog.get_logger()

MAX_TOKENS = 4000  # domain decision: estimations fit comfortably under this
PROMPT_VERSION = "v1"


def _build_messages(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
) -> list[dict]:
    system, user = render_estimation_prompt(
        description=description,
        project_type=project_type,
        detail_level=detail_level,
        output_format=output_format,
        version=PROMPT_VERSION,
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
    """Non-streaming: full validated EstimationResult in one call. For programmatic consumers.

    Invariant: validate_input is ALWAYS first — before building messages and before cache lookup.
    Only outputs that passed validation are cached.
    """
    # 1. Guardrails — MUST be first (invariant 16)
    await validate_input(description)

    # 2. Render prompt → messages
    messages = _build_messages(description, project_type, detail_level, output_format)

    # 3. Cache-aside structured completion
    log.info("generating_estimation")
    result, metadata, cache_hit = await cache.cached_complete_structured(
        messages, EstimationResult, MAX_TOKENS, redis
    )

    return {
        "result": result.model_dump(),
        "model": metadata["model"],
        "provider": metadata["provider"],
        "usage": metadata["usage"],
        "cache_hit": cache_hit,
        "prompt_version": PROMPT_VERSION,
    }


async def generate_estimation_stream(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
):
    """Streaming: async generator of typed events. For programmatic/future use.

    Validators are inactive during streaming (Partial skips them).
    Post-hoc validation fires when the stream closes; emits an error event on failure.
    No cache (pending B5 — semantic cache with schema versioning).
    """
    # 1. Guardrails — MUST be first (invariant 16)
    await validate_input(description)

    messages = _build_messages(description, project_type, detail_level, output_format)
    log.info("generating_estimation_stream")

    accumulated: dict = {}
    metadata: dict = {}

    try:
        async for event in llm_wrapper.stream_structured(
            messages, EstimationResult, MAX_TOKENS
        ):
            if event["type"] == "partial":
                partial = event["data"]
                # Yield token-like event with the partial object serialized
                # (frontend uses /estimate non-stream; this path is for future use)
                yield {"type": "partial", "data": partial.model_dump(exclude_unset=True)}
                # Track latest partial for post-hoc validation
                accumulated = partial.model_dump(exclude_unset=True)
            elif event["type"] == "done":
                metadata = event["metadata"]

    except llm_wrapper.LLMError:
        raise

    # Post-hoc validation: construct final EstimationResult to fire validators
    try:
        final = EstimationResult(**accumulated)
    except Exception as exc:
        log.warning("stream_post_hoc_validation_failed", error=str(exc))
        yield {"type": "error", "data": f"Validation failed: {exc}"}
        return

    yield {
        "type": "done",
        "metadata": {
            **metadata,
            "prompt_version": PROMPT_VERSION,
        },
        "result": final.model_dump(),
    }
