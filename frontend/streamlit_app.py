import requests
import streamlit as st

from config import get_settings

# --- Configuración: endpoints y límites ---
settings = get_settings()

BASE_URL = f"{settings.BACKEND_URL}/api/v1"
SESSIONS_ENDPOINT = f"{BASE_URL}/sessions"
ESTIMATE_ENDPOINT = f"{BASE_URL}/estimate"   # turno único (conservado)
MIN_TRANSCRIPT_LENGTH = 20
REQUEST_TIMEOUT_SECONDS = 120

# --- Opciones del formulario: value del Enum del backend → label legible en UI ---
PROJECT_TYPES = {
    "mobile_app": "App móvil",
    "web_saas": "Web / SaaS",
    "internal_tool": "Herramienta interna",
    "data_pipeline": "Pipeline de datos",
}
DETAIL_LEVELS = {
    "summary": "Resumen",
    "medium": "Medio",
    "detailed": "Detallado",
}
OUTPUT_FORMATS = {
    "phases_table": "Tabla por fases",
    "narrative": "Narrativa",
}


def _create_session() -> str:
    """Llama a POST /sessions y devuelve el session_id. Lanza en caso de error."""
    resp = requests.post(SESSIONS_ENDPOINT, timeout=10)
    resp.raise_for_status()
    return resp.json()["session_id"]


def render_result(result: dict, meta: dict, output_format: str) -> None:
    """Pinta el resultado de la estimación y el pie con metadatos."""
    summary = result.get("summary", "")
    phases = result.get("phases", [])
    total_cost = result.get("total_cost_eur", 0)
    total_weeks = result.get("total_duration_weeks", 0)
    confidence = result.get("confidence_pct", 0)

    # Caso out-of-scope / baja confianza: solo aviso, sin tabla ni métricas.
    if summary.startswith("Out of scope:") or (confidence == 0 and not phases):
        st.warning(summary or "El proyecto no pudo ser estimado con la información proporcionada.")
    else:
        st.subheader("Estimación")
        st.write(summary)

        if output_format == "phases_table" and phases:
            st.subheader("Desglose por fases")
            st.table(
                [
                    {
                        "Fase": p["name"],
                        "Duración (semanas)": p["duration_weeks"],
                        "Coste (EUR)": p["cost_eur"],
                        "Confianza (%)": p["confidence_pct"],
                    }
                    for p in phases
                ]
            )

        col1, col2, col3 = st.columns(3)
        col1.metric("Duración total", f"{total_weeks} semanas")
        col2.metric("Coste total", f"{total_cost:,} EUR")
        col3.metric("Confianza", f"{confidence}%")

    usage = meta.get("usage", {})
    cache_hit = meta.get("cache_hit", False)
    st.caption(
        f"_{meta.get('provider', '')} · {meta.get('model', '')} · "
        f"{usage.get('total_tokens', 0)} tokens · prompt {meta.get('prompt_version', '')} · "
        f"{'cache hit' if cache_hit else 'cache miss'}_"
    )


def render_project_metadata(metadata: dict) -> None:
    """Pinta el panel de memoria del proyecto en el sidebar."""
    populated = {k: v for k, v in metadata.items() if v not in (None, [], "")}
    if not populated:
        st.sidebar.info("La memoria del proyecto se irá completando con cada turno.")
        return
    for key, value in populated.items():
        label = key.replace("_", " ").capitalize()
        if isinstance(value, list):
            st.sidebar.markdown(f"**{label}:** {', '.join(str(v) for v in value)}")
        else:
            st.sidebar.markdown(f"**{label}:** {value}")


# ---------------------------------------------------------------------------
# Cabecera y gestión de sesión
# ---------------------------------------------------------------------------

st.title("Estimador de Software")
st.caption("Conversación multi-turno con memoria del proyecto.")

# Inicializar sesión si no existe en session_state.
if "session_id" not in st.session_state:
    try:
        st.session_state.session_id = _create_session()
        st.session_state.project_metadata = {}
        st.session_state.turn_count = 0
    except Exception as exc:
        st.error(f"No se pudo crear sesión en el backend. ¿Está levantado? ({exc})")
        st.stop()

# Sidebar: estado de la sesión + botón de reset.
st.sidebar.header("Memoria del proyecto")
st.sidebar.caption(f"Sesión: `{st.session_state.session_id[:8]}…`  ·  Turnos: {st.session_state.get('turn_count', 0)}")
render_project_metadata(st.session_state.get("project_metadata", {}))

if st.sidebar.button("Nueva conversación"):
    try:
        st.session_state.session_id = _create_session()
        st.session_state.project_metadata = {}
        st.session_state.turn_count = 0
        st.session_state.last_result = None
        st.rerun()
    except Exception as exc:
        st.sidebar.error(f"Error al crear nueva sesión: {exc}")

# ---------------------------------------------------------------------------
# Formulario de turno
# ---------------------------------------------------------------------------

with st.form("turn_form"):
    transcript = st.text_area(
        "Descripción / mensaje del turno",
        placeholder="Describe el proyecto o añade contexto adicional (mínimo 20 caracteres)...",
        height=150,
    )
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        project_type = st.selectbox(
            "Tipo de proyecto",
            options=list(PROJECT_TYPES.keys()),
            format_func=lambda v: PROJECT_TYPES[v],
        )
    with col_b:
        detail_level = st.selectbox(
            "Nivel de detalle",
            options=list(DETAIL_LEVELS.keys()),
            format_func=lambda v: DETAIL_LEVELS[v],
        )
    with col_c:
        output_format = st.selectbox(
            "Formato de salida",
            options=list(OUTPUT_FORMATS.keys()),
            format_func=lambda v: OUTPUT_FORMATS[v],
        )

    attachments = st.file_uploader(
        "Adjuntos opcionales (PDF o DOCX)",
        accept_multiple_files=True,
        # Sin type=: el backend valida la extensión y devuelve HTTP 400 si no está soportada,
        # de modo que el error llega al usuario en lugar de descartarse silenciosamente.
    )

    submitted = st.form_submit_button("Enviar")

if submitted:
    if len(transcript) < MIN_TRANSCRIPT_LENGTH:
        st.error(
            f"El mensaje es demasiado corto ({len(transcript)} caracteres). "
            f"Mínimo {MIN_TRANSCRIPT_LENGTH}."
        )
    else:
        session_url = f"{BASE_URL}/sessions/{st.session_state.session_id}/estimate"

        with st.spinner("Generando estimación..."):
            try:
                # Envío multipart: campos Form como data=, archivos como files=.
                # FastAPI recibe Form(...) + File(...) desde el mismo multipart.
                response = requests.post(
                    session_url,
                    data={
                        "transcript": transcript,
                        "project_type": project_type,
                        "detail_level": detail_level,
                        "output_format": output_format,
                    },
                    files=[("attachments", (f.name, f.getvalue(), f.type)) for f in (attachments or [])],
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    st.error("Sesión expirada. Pulsa 'Nueva conversación' en el panel lateral.")
                elif exc.response is not None and exc.response.status_code == 400:
                    detail = exc.response.json().get("detail", str(exc))
                    st.error(f"Solicitud rechazada: {detail}")
                else:
                    st.error(f"Error del backend: {exc}")
                st.stop()
            except requests.exceptions.RequestException as exc:
                st.error(
                    f"No se pudo conectar con el backend en {settings.BACKEND_URL}. "
                    f"¿Está levantado? ({type(exc).__name__})"
                )
                st.stop()

        # Guardar en session_state y forzar rerun para que el sidebar vea la metadata nueva.
        # El resultado se renderiza en el rerun siguiente desde last_result (no inline),
        # por lo que st.rerun() ya no lo borra.
        st.session_state.project_metadata = data.get("project_metadata", {})
        st.session_state.turn_count = st.session_state.get("turn_count", 0) + 1
        st.session_state.last_result = data.get("result", {})
        st.session_state.last_meta = {k: data.get(k) for k in ("model", "provider", "usage", "cache_hit", "prompt_version")}
        st.session_state.last_output_format = output_format
        st.rerun()

# Renderiza el último resultado si existe (persiste entre reruns sin necesidad de st.rerun()).
if st.session_state.get("last_result"):
    render_result(
        st.session_state.last_result,
        st.session_state.last_meta,
        st.session_state.last_output_format,
    )
