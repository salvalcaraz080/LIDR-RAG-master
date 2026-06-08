import structlog
from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.schemas.estimations import EstimationRequest
from app.services.llm_service import LLMServiceError, generate_estimation_stream

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["estimations"])


@router.post("/estimate/stream", response_class=EventSourceResponse)
async def create_estimation_stream(request: EstimationRequest):
    """Stream a software estimation as Server-Sent Events.

    The endpoint IS the generator: FastAPI's routing layer serializes each
    yielded ServerSentEvent to the SSE wire format (and JSON-encodes the
    `data` field automatically — so newlines in tokens survive, and we must
    NOT json.dumps ourselves or it double-encodes).
    """
    try:
        for event in generate_estimation_stream(request.transcription):
            if event["type"] == "token":
                yield ServerSentEvent(data=event["data"], event="token")
            elif event["type"] == "done":
                yield ServerSentEvent(data=event["metadata"], event="done")
    except LLMServiceError as exc:
        log.error("stream_endpoint_error", error=str(exc))
        yield ServerSentEvent(data={"error": str(exc)}, event="error")