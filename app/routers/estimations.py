import structlog
from fastapi import APIRouter, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.schemas.estimations import EstimationRequest, EstimationResponse
from app.services.llm_service import generate_estimation, generate_estimation_stream
from app.services.llm_wrapper import LLMError

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate", response_model=EstimationResponse)
async def create_estimation(request: EstimationRequest) -> EstimationResponse:
    """Non-streaming estimation — full structured response for programmatic callers."""
    try:
        result = generate_estimation(request.transcription)
    except LLMError as exc:
        log.error("estimation_endpoint_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))
    return EstimationResponse(**result)


@router.post("/estimate/stream", response_class=EventSourceResponse)
async def create_estimation_stream(request: EstimationRequest):
    """Streaming estimation as SSE — for conversational UIs."""
    try:
        for event in generate_estimation_stream(request.transcription):
            if event["type"] == "token":
                yield ServerSentEvent(data=event["data"], event="token")
            elif event["type"] == "done":
                yield ServerSentEvent(data=event["metadata"], event="done")
    except LLMError as exc:
        log.error("stream_endpoint_error", error=str(exc))
        yield ServerSentEvent(data={"error": str(exc)}, event="error")