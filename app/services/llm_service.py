import asyncio
import json
import time

import structlog
from redisvl.extensions.cache.llm import SemanticCache

from app.config import get_settings
from app.prompts.loader import render_estimation_prompt
from app.schemas.estimations import EstimationResult
from app.services import cache, llm_wrapper
from app.services.documents import extract_text
from app.services.embeddings import embed_text
from app.services.guardrails import validate_input
from app.services.sessions import ProjectMetadata, Session

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


# ---------------------------------------------------------------------------
# Flujo conversacional
# ---------------------------------------------------------------------------

async def _build_attachments_block(raw: list[tuple[str, bytes]]) -> str | None:
    """Extrae texto de cada adjunto y lo envuelve en fences XML.

    El fence XML (<attachment>) contiene contenido externo no confiable; la sanitización
    del cierre evita que un adjunto inyecte </attachment> para salir del fence y añadir
    instrucciones fuera de él.

    Síncrono CPU-bound → se ofrece a un thread para no bloquear el event loop.

    # TODO: decidir si el texto extraído pasa por validate_input (guardrails).
    # Hoy la defensa es estructural (fence + sanitización del cierre).
    # Pasarlo por guardrails tiene riesgo de falsos positivos: una spec legítima puede
    # contener ## headers que el detector de inyección Markdown marcaría como sospechosos.
    """
    if not raw:
        return None
    parts = []
    for filename, data in raw:
        # extract_text es síncrono → thread para no bloquear el event loop.
        text = await asyncio.to_thread(extract_text, data, filename)
        # Neutraliza el cierre del fence si aparece en el contenido; sin esto el fence es teatro.
        safe = text.replace("</attachment>", "<\\/attachment>")
        parts.append(f'<attachment filename="{filename}">\n{safe}\n</attachment>')
    return "\n\n".join(parts)


async def _extract_project_metadata(
    current: ProjectMetadata,
    transcript: str,
    estimation: EstimationResult,
) -> ProjectMetadata:
    """Actualiza la memoria del proyecto con los hechos del turno (2ª llamada estructurada).

    Primer response_model distinto de EstimationResult: reutiliza complete_structured
    de forma agnóstica de dominio (el wrapper no sabe qué schema maneja).

    La entrada del extractor usa el EstimationResult estructurado (model_dump_json) porque
    para extraer hechos conviene el detalle. El historial almacena un render compacto.

    Política de olvido activa aquí:
    - Revisión explícita del usuario → el extractor actualiza/elimina el campo.
    - Opciones rechazadas → se mueven a rejected_options (instrucción en el prompt).
    - TTL y reset de sesión son políticas externas (no se implementan en esta fase).
    """
    system = (
        "You update the ProjectMetadata of a software estimation session. "
        "Only set a field when the turn gives clear evidence. Preserve existing values "
        "unless the user explicitly revises them. If the user retracts a fact, remove it and, "
        "for a rejected technology/option, add it to rejected_options. For list fields, append "
        "without duplicating. Return the full updated ProjectMetadata."
    )
    user = (
        f"Current metadata:\n{current.model_dump_json()}\n\n"
        f"User transcript:\n{transcript}\n\n"
        f"Assistant estimate (structured):\n{estimation.model_dump_json()}"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    updated, _ = await llm_wrapper.complete_structured(messages, ProjectMetadata, MAX_TOKENS)
    return updated


async def generate_estimation_turn(
    session: Session,
    transcript: str,
    project_type: str,
    detail_level: str,
    output_format: str,
    semantic_cache: SemanticCache,
    raw_attachments: list[tuple[str, bytes]] | None = None,
) -> dict:
    """Turno conversacional: estima en el contexto del historial y la memoria de la sesión.

    Invariantes (mismas que generate_estimation + las conversacionales):
    - validate_input es SIEMPRE la primera operación.
    - El caché semántico solo se usa en el primer turno sin adjuntos (semánticamente
      idéntico al turno único; comparte bucket con /estimate sin contaminar).
    - Los adjuntos enriquecen solo el turno en que se suben; al historial va un marcador
      ligero, no el texto íntegro (para no disparar el coste en turnos posteriores).
    - El system prompt se regenera cada turno desde la metadata actual (nunca se almacena).

    Devuelve dict plano con shape:
    {result, model, provider, usage, cache_hit, prompt_version, session_id, project_metadata}
    """
    from datetime import UTC, datetime

    start = time.perf_counter()

    # Guardrails PRIMERO (invariante): solo sobre el transcript del usuario.
    await validate_input(transcript)

    # Extraer adjuntos y construir el bloque XML (puede levantar DocumentExtractionError).
    raw = raw_attachments or []
    attachments_block = await _build_attachments_block(raw)

    # El caché solo aplica al primer turno sin adjuntos.
    is_first_turn = not session.history.turns
    cache_eligible = is_first_turn and attachments_block is None

    vector: list[float] | None = None
    bucket: str | None = None
    cached: str | None = None

    if cache_eligible:
        vector = await embed_text(transcript)
        bucket = _bucket(project_type, detail_level, output_format)
        cached = await cache.semantic_lookup(
            semantic_cache, vector, bucket, enforce=get_settings().semantic_cache_enforce
        )

    # Contenido del turno de usuario que va al array messages.
    user_turn_content = transcript
    if attachments_block:
        user_turn_content = f"{transcript}\n\n{attachments_block}"

    # Marcador ligero para el historial (no el texto íntegro del adjunto).
    attachment_marker = ""
    if raw:
        names = ", ".join(fn for fn, _ in raw if fn)
        attachment_marker = f" [Adjuntos: {names}]" if names else " [Adjuntos]"
    history_user_content = transcript + attachment_marker

    # Render del system con la metadata actual.
    # Se pasa model_dump() completo (todos los campos, incluyendo None) porque StrictUndefined
    # exige que toda clave usada en el template exista en el dict.
    # El bloque ## Project Memory se suprime cuando no hay campo con valor real.
    _md = session.project_metadata.model_dump()
    metadata_dict = _md if any(v not in (None, []) for v in _md.values()) else None
    system, _ = render_estimation_prompt(
        transcript, project_type, detail_level, output_format,
        project_metadata=metadata_dict,
    )

    if cached is not None:
        # HIT de caché: deserializar y sembrar historial + actualizar memoria igual que en miss.
        payload = json.loads(cached)
        result_obj = EstimationResult(**payload["result"])
        metadata = payload["metadata"]
        cache_hit = True
    else:
        # MISS o turno ≥ 2 o con adjuntos → llamar al LLM con historial + turno actual.
        messages = session.history.to_messages(system, get_settings().MAX_TURNS)
        messages.append({"role": "user", "content": user_turn_content})
        result_obj, metadata = await llm_wrapper.complete_structured(
            messages, EstimationResult, MAX_TOKENS
        )
        cache_hit = False

        # Escribir en caché solo si era eligible (primer turno sin adjuntos).
        if cache_eligible and vector is not None and bucket is not None:
            payload_str = json.dumps({"result": result_obj.model_dump(), "metadata": metadata})
            await cache.semantic_write(semantic_cache, vector, bucket, transcript, payload_str)

    # Render compacto del turno del assistant para el historial.
    assistant_render = (
        f"Estimación: {result_obj.summary} · "
        f"{result_obj.total_duration_weeks} semanas · "
        f"{result_obj.total_cost_eur}€ · "
        f"{len(result_obj.phases)} fases · "
        f"confianza {result_obj.confidence_pct}%"
    )
    session.history.add_turn(history_user_content, assistant_render)

    # Actualizar la memoria destilada del proyecto (2ª llamada estructurada).
    session.project_metadata = await _extract_project_metadata(
        session.project_metadata, transcript, result_obj
    )
    session.updated_at = datetime.now(UTC)

    turn_index = len(session.history.turns) // 2
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    log.info(
        "session_turn_completed",
        session_id=session.session_id,
        turn_index=turn_index,
        cache_hit=cache_hit,
        metadata_updated=True,
        duration_ms=duration_ms,
    )

    return {
        **_map_to_response(result_obj.model_dump(), metadata, cache_hit),
        "session_id": session.session_id,
        "project_metadata": session.project_metadata.model_dump(),
    }
