import json

import structlog
from redisvl.extensions.cache.llm import SemanticCache

from app.config import get_settings
from app.prompts.loader import render_estimation_prompt
from app.schemas.estimations import EstimationResult
from app.services import cache, llm_wrapper
from app.services.embeddings import embed_text
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
    # Renderiza el prompt versionado y lo empaqueta como mensajes system+user separados.
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


def _bucket(project_type: str, detail_level: str, output_format: str) -> str:
    # Partición determinista de la caché: incluye prompt_version (un bump invalida de facto).
    return cache.build_bucket_key(
        PROMPT_VERSION, project_type, detail_level, output_format
    )


def _map_to_response(result: dict, metadata: dict, cache_hit: bool) -> dict:
    # Aplana el resultado de dominio + metadatos al shape que espera EstimationResponse.
    return {
        "result": result,
        "model": metadata["model"],
        "provider": metadata["provider"],
        "usage": metadata["usage"],
        "cache_hit": cache_hit,
        "prompt_version": PROMPT_VERSION,
    }


async def _validate_embed_and_lookup(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
    semantic_cache: SemanticCache,
) -> tuple[list[float], str, str | None]:
    """Shared prefix for both entry points. Centralizes the ordering invariant:
    guardrails FIRST, then embed ONCE (vector reused for lookup and write), then probe
    the semantic cache. Returns (vector, bucket, cached_payload_or_None).
    """
    await validate_input(description)  # invariant: MUST be first
    vector = await embed_text(description)  # once — reused for the write on miss
    bucket = _bucket(project_type, detail_level, output_format)
    cached = await cache.semantic_lookup(
        semantic_cache, vector, bucket, enforce=get_settings().semantic_cache_enforce
    )
    return vector, bucket, cached


async def generate_estimation(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
    semantic_cache: SemanticCache,
) -> dict:
    """Non-streaming: full validated EstimationResult. For programmatic consumers.

    Invariants:
      - validate_input is ALWAYS first (before embedding, lookup and LLM).
      - the embedding is computed ONCE and reused for lookup and write.
      - only validated outputs are cached.
    """
    vector, bucket, cached = await _validate_embed_and_lookup(
        description, project_type, detail_level, output_format, semantic_cache
    )
    if cached is not None:
        payload = json.loads(cached)
        return _map_to_response(payload["result"], payload["metadata"], cache_hit=True)

    # Miss → LLM (Instructor validates + retries)
    log.info("generating_estimation")
    messages = _build_messages(description, project_type, detail_level, output_format)
    result, metadata = await llm_wrapper.complete_structured(
        messages, EstimationResult, MAX_TOKENS
    )

    # Write only after successful validation
    payload = json.dumps({"result": result.model_dump(), "metadata": metadata})
    await cache.semantic_write(semantic_cache, vector, bucket, description, payload)

    return _map_to_response(result.model_dump(), metadata, cache_hit=False)


async def generate_estimation_stream(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
    semantic_cache: SemanticCache,
):
    """Streaming: async generator of typed events. The UI consumes this endpoint.

    On a cache HIT, emits the cached (validated) result as the final event.
    On a MISS, streams Partial[EstimationResult] (validators inactive), then runs
    POST-HOC validation when the stream closes; on success caches and emits done,
    on failure emits an error event.

    NOTE (PROVISIONAL): the exact in-stream validation mechanics are pending the live
    session — this is the minimal version (accumulate partials, validate at the end).
    """
    vector, bucket, cached = await _validate_embed_and_lookup(
        description, project_type, detail_level, output_format, semantic_cache
    )
    if cached is not None:
        payload = json.loads(cached)
        yield {
            "type": "done",
            "result": payload["result"],
            "metadata": {
                **payload["metadata"],
                "prompt_version": PROMPT_VERSION,
                "cache_hit": True,
            },
        }
        return

    # Miss → stream
    log.info("generating_estimation_stream")
    messages = _build_messages(description, project_type, detail_level, output_format)

    accumulated: dict = {}
    metadata: dict = {}

    async for event in llm_wrapper.stream_structured(
        messages, EstimationResult, MAX_TOKENS
    ):
        if event["type"] == "partial":
            accumulated = event["data"].model_dump(exclude_unset=True)
            yield {"type": "partial", "data": accumulated}
        elif event["type"] == "done":
            metadata = event["metadata"]

    # 5. Post-hoc validation (Partial skipped the validators)
    try:
        final = EstimationResult(**accumulated)
    except Exception as exc:
        log.warning("stream_post_hoc_validation_failed", error=str(exc))
        yield {"type": "error", "data": f"Validation failed: {exc}"}
        return

    # 6. Write only after successful validation
    payload = json.dumps({"result": final.model_dump(), "metadata": metadata})
    await cache.semantic_write(semantic_cache, vector, bucket, description, payload)

    yield {
        "type": "done",
        "result": final.model_dump(),
        "metadata": {
            **metadata,
            "prompt_version": PROMPT_VERSION,
            "cache_hit": False,
        },
    }
