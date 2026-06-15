import redis.asyncio as aredis
import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.dependencies import get_redis
from app.schemas.estimations import EstimationRequest, EstimationResponse
from app.services.guardrails import InputGuardrailError
from app.services.llm_service import generate_estimation, generate_estimation_stream
from app.services.llm_wrapper import LLMError

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
async def create_estimation(
    request: EstimationRequest,
    redis: aredis.Redis = Depends(get_redis),
) -> EstimationResponse:
    """Non-streaming estimation — full validated structured response."""
    try:
        result = await generate_estimation(
            request.description,
            request.project_type.value,
            request.detail_level.value,
            request.output_format.value,
            redis,
        )
    except InputGuardrailError as exc:
        log.warning("estimation_guardrail_rejected", error=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        log.error("estimation_endpoint_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    return EstimationResponse(**result)


@router.post("/estimate/stream", response_class=EventSourceResponse)
async def create_estimation_stream(request: EstimationRequest):
    """Streaming estimation as SSE — for future/programmatic use."""
    try:
        async for event in generate_estimation_stream(
            request.description,
            request.project_type.value,
            request.detail_level.value,
            request.output_format.value,
        ):
            if event["type"] == "partial":
                yield ServerSentEvent(data=event["data"], event="partial")
            elif event["type"] == "done":
                yield ServerSentEvent(data=event, event="done")
            elif event["type"] == "error":
                yield ServerSentEvent(data={"error": event["data"]}, event="error")
    except InputGuardrailError as exc:
        log.warning("stream_guardrail_rejected", error=str(exc))
        yield ServerSentEvent(data={"error": str(exc)}, event="error")
    except LLMError as exc:
        log.error("stream_endpoint_error", error=str(exc))
        yield ServerSentEvent(data={"error": str(exc)}, event="error")
