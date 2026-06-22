"""Estado de sesión conversacional — módulo puro.

Gestiona el historial de turnos y la memoria destilada del proyecto (ProjectMetadata).
No importa FastAPI, no importa llm_wrapper, no llama al LLM. Quien orquesta las llamadas
al LLM es llm_service; este módulo solo mantiene y expone el estado.

El store `_sessions` es un dict en memoria de proceso, volátil a propósito:
  - El despliegue usa un único worker (confirmado en Compose/prod/local) → no hay
    fragmentación de estado entre réplicas.
  - La persistencia en Redis/PG es del módulo de despliegue (sesiones 9-10); migrar no
    requiere refactor de este módulo porque history y metadata se serializan con model_dump.
  - No hay TTL ni scheduler de archivado en esta fase: son decisiones del directo, no del ejercicio.
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class SessionNotFoundError(Exception):
    """El session_id solicitado no existe (o expiró con un reinicio del proceso)."""


# ---------------------------------------------------------------------------
# Modelos de estado de sesión (dominio, no contratos HTTP)
# ---------------------------------------------------------------------------

class ProjectMetadata(BaseModel):
    """Hechos destilados sobre el proyecto en curso. Sobrevive al truncado del historial.

    Se actualiza tras cada turno mediante el extractor LLM (en llm_service). Los campos
    son todos opcionales: una sesión empieza sin datos y se va poblando progresivamente.
    Para listas, el extractor añade sin duplicar; si el usuario retracta un hecho, el
    extractor lo elimina de su campo y, si corresponde, lo mueve a rejected_options.
    """
    project_name: str | None = None
    assumed_team_size: int | None = None
    mentioned_technologies: list[str] = Field(default_factory=list)
    agreed_scope: str | None = None
    explicit_constraints: list[str] = Field(default_factory=list)
    rejected_options: list[str] = Field(default_factory=list)


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ConversationHistory(BaseModel):
    """Historial bruto de turnos user+assistant.

    Invariantes:
    - El system prompt NUNCA se almacena en `turns`; se regenera cada turno desde la
      metadata actual para reflejar el estado más reciente de ProjectMetadata.
    - El historial completo se conserva en memoria (para auditoría); la ventana deslizante
      solo se aplica en `to_messages` al construir el array para la API.
    - Los textos íntegros de adjuntos NO van en turns (solo un marcador ligero), para no
      disparar el coste reinyectando el contenido extraído en cada turno posterior.
    """
    turns: list[Message] = Field(default_factory=list)

    def add_turn(self, user_content: str, assistant_content: str) -> None:
        """Añade el par user+assistant del turno recién completado al historial."""
        self.turns.append(Message(role="user", content=user_content))
        self.turns.append(Message(role="assistant", content=assistant_content))

    def to_messages(self, system: str, max_turns: int) -> list[dict]:
        """Construye el array messages para la API de chat.

        Estructura: [system fresco] + [últimos max_turns pares user/assistant].
        max_turns pares = últimos 2*max_turns mensajes de `turns`.
        El system se regenera cada llamada (refleja la metadata actualizada del turno previo).
        """
        # Ventana deslizante: los últimos max_turns pares = 2*max_turns mensajes.
        window = self.turns[-(2 * max_turns):] if self.turns else []
        return [{"role": "system", "content": system}] + [
            {"role": m.role, "content": m.content} for m in window
        ]


class Session(BaseModel):
    """Estado completo de una sesión conversacional."""
    session_id: str = Field(default_factory=lambda: str(uuid4()))
    history: ConversationHistory = Field(default_factory=ConversationHistory)
    project_metadata: ProjectMetadata = Field(default_factory=ProjectMetadata)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Store en memoria + accesores
# ---------------------------------------------------------------------------

# Volátil a propósito: ver docstring del módulo.
_sessions: dict[str, Session] = {}


def create_session() -> Session:
    """Crea una sesión nueva, la registra en el store y la devuelve."""
    session = Session()
    _sessions[session.session_id] = session
    return session


def get_session(session_id: str) -> Session:
    """Devuelve la sesión o levanta SessionNotFoundError si no existe."""
    session = _sessions.get(session_id)
    if session is None:
        raise SessionNotFoundError(
            f"Sesión '{session_id}' no encontrada. "
            "Las sesiones son volátiles: no sobreviven a un reinicio del proceso."
        )
    return session
