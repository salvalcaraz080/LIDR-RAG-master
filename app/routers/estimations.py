import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.sse import EventSourceResponse, ServerSentEvent
from redisvl.extensions.cache.llm import SemanticCache

from app.dependencies import get_semantic_cache
from app.schemas.estimations import (
    DetailLevel,
    EstimationRequest,
    EstimationResponse,
    OutputFormat,
    ProjectType,
    SessionEstimationResponse,
)
from app.services.documents import DocumentExtractionError
from app.services.guardrails import InputGuardrailError
from app.services.llm_service import generate_estimation, generate_estimation_stream, generate_estimation_turn
from app.services.llm_wrapper import LLMError
from app.services.sessions import SessionNotFoundError, create_session, get_session

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
async def create_estimation(
    request: EstimationRequest,
    semantic_cache: SemanticCache = Depends(get_semantic_cache),
) -> EstimationResponse:
    """Endpoint PRINCIPAL: estimación no-stream, respuesta estructurada completa.

    Es el que consume la UI. Devuelve el EstimationResult validado de una sola vez.
    """
    try:
        # Desempaqueta el schema en primitivas y delega en el servicio (dominio).
        result = await generate_estimation(
            request.description,
            request.project_type.value,
            request.detail_level.value,
            request.output_format.value,
            semantic_cache,
        )
    # Traducción de excepciones de dominio a HTTP (única capa que conoce códigos HTTP).
    except InputGuardrailError as exc:
        log.warning("estimation_guardrail_rejected", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        log.error("estimation_endpoint_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    # Validación Pydantic en el borde, sobre el dict plano del servicio.
    return EstimationResponse(**result)


@router.post("/estimate/stream", response_class=EventSourceResponse)
async def create_estimation_stream(
    request: EstimationRequest,
    semantic_cache: SemanticCache = Depends(get_semantic_cache),
):
    """Endpoint SECUNDARIO: estimación por streaming SSE. CONSERVADO, no lo usa la UI.

    Se mantiene como referencia/reutilización (otros proyectos). Emite eventos
    'partial' mientras el modelo genera, luego un 'done' con el resultado validado +
    metadata. Un HIT de caché emite 'done' directamente; un fallo de validación a
    mitad de stream emite 'error'.
    """
    try:
        # Reemite cada evento tipado del servicio como Server-Sent Event.
        async for event in generate_estimation_stream(
            request.description,
            request.project_type.value,
            request.detail_level.value,
            request.output_format.value,
            semantic_cache,
        ):
            if event["type"] == "partial":
                yield ServerSentEvent(data=event["data"], event="partial")
            elif event["type"] == "done":
                yield ServerSentEvent(data=event, event="done")
            elif event["type"] == "error":
                yield ServerSentEvent(data={"error": event["data"]}, event="error")
    # En SSE los errores no son códigos HTTP: se emiten como evento 'error'.
    except InputGuardrailError as exc:
        log.warning("stream_guardrail_rejected", error=str(exc))
        yield ServerSentEvent(data={"error": str(exc)}, event="error")
    except LLMError as exc:
        log.error("stream_endpoint_error", error=str(exc))
        yield ServerSentEvent(data={"error": str(exc)}, event="error")


@router.post("/sessions")
async def create_session_endpoint() -> dict:
    """Crea una sesión conversacional vacía. Devuelve el session_id para turnos posteriores."""
    session = create_session()
    return {"session_id": session.session_id}


@router.post("/sessions/{session_id}/estimate", response_model=SessionEstimationResponse)
async def create_session_estimation(
    session_id: str,
    transcript: str = Form(..., min_length=20, max_length=2000),
    project_type: ProjectType = Form(...),
    detail_level: DetailLevel = Form(...),
    output_format: OutputFormat = Form(...),
    attachments: list[UploadFile] = File(default=[]),
    semantic_cache: SemanticCache = Depends(get_semantic_cache),
) -> SessionEstimationResponse:
    """Turno conversacional: estimación en el contexto de una sesión con historial y memoria."""
    # Lee los archivos a bytes antes de entrar al servicio (el servicio es HTTP-agnóstico).
    try:
        raw_attachments = [(f.filename or "", await f.read()) for f in attachments]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error leyendo adjunto: {exc}")

    try:
        session = get_session(session_id)
        result = await generate_estimation_turn(
            session=session,
            transcript=transcript,
            project_type=project_type.value,
            detail_level=detail_level.value,
            output_format=output_format.value,
            semantic_cache=semantic_cache,
            raw_attachments=raw_attachments,
        )
    # Traducción de excepciones de dominio a HTTP.
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DocumentExtractionError as exc:
        raise HTTPException(status_code=400, detail=f"Error extrayendo adjunto: {exc}")
    except InputGuardrailError as exc:
        log.warning("session_guardrail_rejected", session_id=session_id, error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        log.error("session_endpoint_error", session_id=session_id, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    return SessionEstimationResponse(**result)
