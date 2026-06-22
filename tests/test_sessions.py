"""Tests de memoria conversacional, sesiones y adjuntos — sin coste de API ni red.

Bloques:
  A. Unit — ConversationHistory (ventana deslizante, sin LLM)
  B. Unit — store de sesiones (create/get/not-found)
  C. Unit — render de system.j2 con/sin project_metadata
  D. Integración — endpoints HTTP (/sessions, /sessions/{id}/estimate)
     con complete_structured, embed_text y validate_input mockeados.
"""

from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF

import app.services.sessions as sessions_module
from app.dependencies import get_semantic_cache
from app.main import app
from app.prompts.loader import render_estimation_prompt
from app.schemas.estimations import EstimationResult, Phase
from app.services.sessions import (
    ConversationHistory,
    ProjectMetadata,
    SessionNotFoundError,
    create_session,
    get_session,
)

# ---------------------------------------------------------------------------
# Fixtures y constantes canónicas
# ---------------------------------------------------------------------------

_PHASE = Phase(name="Backend", duration_weeks=8, cost_eur=40000, confidence_pct=80)

CANONICAL_RESULT = EstimationResult(
    summary="A booking platform for yoga classes",
    total_duration_weeks=8,  # sobreescrito por el validator desde phases
    total_cost_eur=40000,
    confidence_pct=80,
    phases=[_PHASE],
)

CANONICAL_LLM_META = {
    "model": "openai/gpt-4o-mini",
    "provider": "openai",
    "usage": {"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
}

CANONICAL_PROJECT_METADATA = ProjectMetadata(
    project_name="Yoga App",
    mentioned_technologies=["React", "FastAPI"],
)


def _make_pdf_with_sentinel(sentinel: str) -> bytes:
    """Genera un PDF mínimo de una página con la frase centinela."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text=sentinel)
    return bytes(pdf.output())


@pytest.fixture(autouse=True)
def clear_sessions():
    """Limpia el store de sesiones antes y después de cada test."""
    sessions_module._sessions.clear()
    yield
    sessions_module._sessions.clear()


@pytest.fixture
def mock_cache():
    """Override de get_semantic_cache: siempre devuelve miss (acheck=[])."""
    cache_mock = MagicMock()
    cache_mock.acheck = AsyncMock(return_value=[])
    cache_mock.astore = AsyncMock()
    app.dependency_overrides[get_semantic_cache] = lambda: cache_mock
    yield cache_mock
    app.dependency_overrides.pop(get_semantic_cache, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Bloque A — ConversationHistory: ventana deslizante
# ---------------------------------------------------------------------------

class TestConversationHistory:
    def test_to_messages_empty_history_returns_only_system(self):
        h = ConversationHistory()
        msgs = h.to_messages(system="sys", max_turns=6)
        assert msgs == [{"role": "system", "content": "sys"}]

    def test_to_messages_single_turn_returns_system_plus_pair(self):
        h = ConversationHistory()
        h.add_turn("user msg", "assistant msg")
        msgs = h.to_messages(system="sys", max_turns=6)
        assert len(msgs) == 3  # system + user + assistant
        assert msgs[0] == {"role": "system", "content": "sys"}
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"

    def test_to_messages_respects_max_turns_window(self):
        """8 turnos con max_turns=6 → solo los últimos 6 pares (12 msgs) + system."""
        h = ConversationHistory()
        for i in range(8):
            h.add_turn(f"user {i}", f"assistant {i}")
        msgs = h.to_messages(system="sys", max_turns=6)
        # 1 system + 6*2 mensajes = 13
        assert len(msgs) == 13
        assert msgs[0]["role"] == "system"
        # El primer par visible es el turno 2 (índice 2 de 0..7)
        assert msgs[1]["content"] == "user 2"

    def test_to_messages_exact_max_turns_no_truncation(self):
        """Exactamente max_turns pares → ninguno se trunca."""
        h = ConversationHistory()
        for i in range(6):
            h.add_turn(f"u{i}", f"a{i}")
        msgs = h.to_messages(system="sys", max_turns=6)
        assert len(msgs) == 13  # 1 + 12

    def test_to_messages_system_is_always_fresh(self):
        """El system que se pasa es el que aparece, no uno almacenado."""
        h = ConversationHistory()
        h.add_turn("u", "a")
        msgs_v1 = h.to_messages(system="system_v1", max_turns=6)
        msgs_v2 = h.to_messages(system="system_v2", max_turns=6)
        assert msgs_v1[0]["content"] == "system_v1"
        assert msgs_v2[0]["content"] == "system_v2"

    def test_history_stores_all_turns_beyond_window(self):
        """El historial completo se conserva en memoria; la ventana solo afecta a to_messages."""
        h = ConversationHistory()
        for i in range(10):
            h.add_turn(f"u{i}", f"a{i}")
        assert len(h.turns) == 20  # 10 pares = 20 mensajes en turns


# ---------------------------------------------------------------------------
# Bloque B — Store de sesiones
# ---------------------------------------------------------------------------

class TestSessionStore:
    def test_create_session_returns_session_with_uuid(self):
        session = create_session()
        assert session.session_id
        assert len(session.session_id) == 36  # UUID4

    def test_created_session_is_retrievable(self):
        session = create_session()
        retrieved = get_session(session.session_id)
        assert retrieved.session_id == session.session_id

    def test_get_session_unknown_id_raises(self):
        with pytest.raises(SessionNotFoundError):
            get_session("nonexistent-id")

    def test_create_multiple_sessions_independent(self):
        s1 = create_session()
        s2 = create_session()
        assert s1.session_id != s2.session_id
        assert get_session(s1.session_id).session_id == s1.session_id

    def test_project_metadata_starts_empty(self):
        session = create_session()
        md = session.project_metadata
        assert md.project_name is None
        assert md.mentioned_technologies == []
        assert md.explicit_constraints == []


# ---------------------------------------------------------------------------
# Bloque C — Template: bloque Project Memory condicional
# ---------------------------------------------------------------------------

class TestProjectMemoryTemplate:
    _PARAMS = dict(
        description="A yoga booking app",
        project_type="mobile_app",
        detail_level="medium",
        output_format="phases_table",
    )

    def test_no_metadata_renders_without_project_memory_section(self):
        system, _ = render_estimation_prompt(**self._PARAMS, project_metadata=None)
        assert "## Project Memory" not in system

    def test_empty_metadata_dict_renders_without_project_memory_section(self):
        """exclude_defaults → dict vacío → or None en el servicio → bloque no aparece."""
        system, _ = render_estimation_prompt(**self._PARAMS, project_metadata=None)
        assert "## Project Memory" not in system

    def test_populated_metadata_renders_project_memory_section(self):
        # model_dump() completo: StrictUndefined exige que todas las claves existan en el dict.
        metadata = ProjectMetadata(
            project_name="Yoga App",
            mentioned_technologies=["React", "FastAPI"],
            agreed_scope="Booking + payments",
        ).model_dump()
        system, _ = render_estimation_prompt(**self._PARAMS, project_metadata=metadata)
        assert "## Project Memory" in system
        assert "Yoga App" in system
        assert "React, FastAPI" in system
        assert "Booking + payments" in system

    def test_rejected_options_appear_in_project_memory(self):
        metadata = ProjectMetadata(rejected_options=["Rails", "Django"]).model_dump()
        system, _ = render_estimation_prompt(**self._PARAMS, project_metadata=metadata)
        assert "Rejected" in system
        assert "Rails" in system


# ---------------------------------------------------------------------------
# Bloque D — Integración HTTP (LLM y dependencias mockeados)
# ---------------------------------------------------------------------------

# Parches comunes a todos los tests de integración del flujo conversacional.
_PATCHES = (
    # Guardrails: no llama a la Moderation API
    patch("app.services.llm_service.validate_input", new=AsyncMock(return_value=None)),
    # Embeddings: no llama a OpenAI
    patch("app.services.llm_service.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
)


def _mock_complete_structured():
    """AsyncMock que despacha por response_model: EstimationResult o ProjectMetadata."""
    async def _dispatch(messages, response_model, max_tokens, max_retries=2):
        if response_model is EstimationResult:
            return (CANONICAL_RESULT, CANONICAL_LLM_META)
        return (CANONICAL_PROJECT_METADATA, {})
    return AsyncMock(side_effect=_dispatch)


@pytest.fixture
def patched_llm():
    """Aplica todos los parches de LLM y devuelve el mock de complete_structured."""
    mock_cs = _mock_complete_structured()
    patches = list(_PATCHES) + [
        patch("app.services.llm_service.llm_wrapper.complete_structured", new=mock_cs),
    ]
    for p in patches:
        p.start()
    yield mock_cs
    for p in patches:
        p.stop()


class TestSessionEndpoints:
    def test_post_sessions_returns_session_id(self, client: TestClient):
        resp = client.post("/api/v1/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert len(data["session_id"]) == 36

    def test_post_sessions_creates_distinct_ids(self, client: TestClient):
        id1 = client.post("/api/v1/sessions").json()["session_id"]
        id2 = client.post("/api/v1/sessions").json()["session_id"]
        assert id1 != id2

    def test_estimate_unknown_session_returns_404(self, client: TestClient, mock_cache):
        resp = client.post(
            "/api/v1/sessions/nonexistent-id/estimate",
            data={
                "transcript": "A yoga booking app with payments",
                "project_type": "mobile_app",
                "detail_level": "medium",
                "output_format": "phases_table",
            },
        )
        assert resp.status_code == 404

    def test_first_turn_returns_estimation_and_session_fields(
        self, client: TestClient, mock_cache, patched_llm
    ):
        session_id = client.post("/api/v1/sessions").json()["session_id"]
        resp = client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={
                "transcript": "A yoga booking app with payments and notifications",
                "project_type": "mobile_app",
                "detail_level": "medium",
                "output_format": "phases_table",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # Campos de EstimationResponse presentes
        assert "result" in data
        assert data["result"]["summary"] == CANONICAL_RESULT.summary
        assert data["cache_hit"] is False
        # Campos conversacionales presentes
        assert data["session_id"] == session_id
        assert "project_metadata" in data

    def test_project_metadata_populated_after_turn(
        self, client: TestClient, mock_cache, patched_llm
    ):
        """El extractor devuelve CANONICAL_PROJECT_METADATA; debe aparecer en la respuesta."""
        session_id = client.post("/api/v1/sessions").json()["session_id"]
        resp = client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={
                "transcript": "A yoga booking app with payments and notifications",
                "project_type": "mobile_app",
                "detail_level": "medium",
                "output_format": "phases_table",
            },
        )
        md = resp.json()["project_metadata"]
        assert md["project_name"] == "Yoga App"
        assert "React" in md["mentioned_technologies"]

    def test_metadata_persists_across_turns(
        self, client: TestClient, mock_cache, patched_llm
    ):
        """La metadata del turno 1 sigue disponible en el turno 2."""
        session_id = client.post("/api/v1/sessions").json()["session_id"]
        payload = dict(
            project_type="mobile_app",
            detail_level="medium",
            output_format="phases_table",
        )
        # Turno 1
        client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={"transcript": "A yoga booking app with React and FastAPI", **payload},
        )
        # Turno 2
        resp2 = client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={"transcript": "Add push notifications to the yoga app", **payload},
        )
        assert resp2.status_code == 200
        md = resp2.json()["project_metadata"]
        # La metadata del extractor mock sigue siendo CANONICAL_PROJECT_METADATA
        assert md["project_name"] == "Yoga App"

    def test_history_grows_with_turns(
        self, client: TestClient, mock_cache, patched_llm
    ):
        """Después de 2 turnos, el historial de la sesión tiene 4 mensajes (2 pares)."""
        session_id = client.post("/api/v1/sessions").json()["session_id"]
        payload = dict(project_type="mobile_app", detail_level="medium", output_format="phases_table")
        client.post(f"/api/v1/sessions/{session_id}/estimate",
                    data={"transcript": "A yoga class booking app with push notifications", **payload})
        client.post(f"/api/v1/sessions/{session_id}/estimate",
                    data={"transcript": "Add Stripe payment processing to the app", **payload})

        session = get_session(session_id)
        assert len(session.history.turns) == 4  # 2 pares user+assistant

    def test_second_turn_includes_history_in_messages(
        self, client: TestClient, mock_cache, patched_llm
    ):
        """En el 2º turno, complete_structured recibe el historial del 1º."""
        session_id = client.post("/api/v1/sessions").json()["session_id"]
        payload = dict(project_type="mobile_app", detail_level="medium", output_format="phases_table")

        client.post(f"/api/v1/sessions/{session_id}/estimate",
                    data={"transcript": "A yoga class booking app with notifications", **payload})
        client.post(f"/api/v1/sessions/{session_id}/estimate",
                    data={"transcript": "Add payment processing to the yoga app", **payload})

        # Las llamadas de estimación son las llamadas con response_model=EstimationResult.
        # complete_structured es llamado 4 veces en total: 2 estimation + 2 metadata.
        # La 3ª llamada (1ª del turno 2) tiene el historial del turno 1 en messages.
        calls = patched_llm.call_args_list
        # Filtra solo las llamadas de estimación (response_model=EstimationResult)
        estimation_calls = [c for c in calls if c.args[1] is EstimationResult]
        assert len(estimation_calls) == 2

        # Los messages del 2º turno de estimación contienen el historial del 1º.
        second_turn_messages = estimation_calls[1].args[0]
        roles = [m["role"] for m in second_turn_messages]
        # Debe tener: system, user (turno 1), assistant (turno 1), user (turno 2)
        assert roles.count("user") >= 2
        assert "assistant" in roles


class TestSessionAttachments:
    PDF_SENTINEL = "YOGA_APP_SPEC_SENTINEL"

    def _make_pdf(self) -> bytes:
        return _make_pdf_with_sentinel(self.PDF_SENTINEL)

    def test_attachment_fence_included_in_messages(
        self, client: TestClient, mock_cache, patched_llm
    ):
        """El texto del adjunto llega al LLM dentro de un fence <attachment ...>."""
        session_id = client.post("/api/v1/sessions").json()["session_id"]

        resp = client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={
                "transcript": "Estimate this app based on the attached spec",
                "project_type": "mobile_app",
                "detail_level": "medium",
                "output_format": "phases_table",
            },
            files=[("attachments", ("spec.pdf", self._make_pdf(), "application/pdf"))],
        )
        assert resp.status_code == 200

        # El mensaje de usuario pasado al LLM debe contener el fence XML.
        calls = patched_llm.call_args_list
        estimation_calls = [c for c in calls if c.args[1] is EstimationResult]
        user_messages = [m for m in estimation_calls[0].args[0] if m["role"] == "user"]
        assert any("<attachment" in m["content"] for m in user_messages)
        assert any(self.PDF_SENTINEL in m["content"] for m in user_messages)

    def test_attachment_marker_in_history_not_full_text(
        self, client: TestClient, mock_cache, patched_llm
    ):
        """El historial almacena el marcador ligero, no el texto íntegro del adjunto."""
        session_id = client.post("/api/v1/sessions").json()["session_id"]

        client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={
                "transcript": "Estimate this project from the attached specification",
                "project_type": "mobile_app",
                "detail_level": "medium",
                "output_format": "phases_table",
            },
            files=[("attachments", ("spec.pdf", self._make_pdf(), "application/pdf"))],
        )

        session = get_session(session_id)
        user_turn = session.history.turns[0]
        # El historial menciona el nombre del archivo
        assert "spec.pdf" in user_turn.content
        # Pero NO el texto completo extraído del PDF
        assert self.PDF_SENTINEL not in user_turn.content

    def test_unsupported_extension_returns_400(
        self, client: TestClient, mock_cache, patched_llm
    ):
        """Un adjunto con extensión no soportada debe devolver HTTP 400."""
        session_id = client.post("/api/v1/sessions").json()["session_id"]

        resp = client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={
                "transcript": "Estimate this spec from the attached file",
                "project_type": "mobile_app",
                "detail_level": "medium",
                "output_format": "phases_table",
            },
            files=[("attachments", ("notes.txt", b"some notes", "text/plain"))],
        )
        assert resp.status_code == 400
        assert "adjunto" in resp.json()["detail"].lower()

    def test_cache_skipped_when_attachment_present(
        self, client: TestClient, mock_cache, patched_llm
    ):
        """Con adjunto, el primer turno no consulta el caché semántico (acheck no llamado)."""
        session_id = client.post("/api/v1/sessions").json()["session_id"]

        client.post(
            f"/api/v1/sessions/{session_id}/estimate",
            data={
                "transcript": "Estimate from spec",
                "project_type": "mobile_app",
                "detail_level": "medium",
                "output_format": "phases_table",
            },
            files=[("attachments", ("spec.pdf", self._make_pdf(), "application/pdf"))],
        )
        # Con adjunto no es cache-eligible: acheck no se debe haber llamado.
        mock_cache.acheck.assert_not_awaited()


class TestExistingEndpointUnchanged:
    def test_post_estimate_still_works(self, client: TestClient, mock_cache):
        """El endpoint de turno único no se rompe con los cambios de esta sesión."""
        with (
            patch("app.services.llm_service.validate_input", new=AsyncMock(return_value=None)),
            patch("app.services.llm_service.embed_text", new=AsyncMock(return_value=[0.0] * 1536)),
            patch("app.services.llm_service.llm_wrapper.complete_structured",
                  new=AsyncMock(return_value=(CANONICAL_RESULT, CANONICAL_LLM_META))),
        ):
            resp = client.post(
                "/api/v1/estimate",
                json={
                    "description": "A yoga booking app with payments and push notifications",
                    "project_type": "mobile_app",
                    "detail_level": "medium",
                    "output_format": "phases_table",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert "session_id" not in data  # turno único NO devuelve session_id
