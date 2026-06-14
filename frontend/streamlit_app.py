import json

import requests
import sseclient
import streamlit as st

from config import get_settings

settings = get_settings()

STREAM_ENDPOINT = f"{settings.BACKEND_URL}/api/v1/estimate/stream"
MIN_DESCRIPTION_LENGTH = 20
REQUEST_TIMEOUT_SECONDS = 120  # streaming generation can be long

# Option values MUST match the backend Enum values exactly (case-sensitive — Linux).
# The labels are only for display; format_func maps value → readable label.
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
    "line_items": "Lista de partidas",
    "narrative": "Narrativa",
}

st.title("Estimador de Software")
st.caption("Describe el proyecto y elige los parámetros para obtener una estimación detallada.")

# --- Product form: typed parameters instead of free chat ---
with st.form("estimation_form"):
    description = st.text_area(
        "Descripción del proyecto",
        placeholder="Describe el proyecto a estimar (mínimo 20 caracteres)...",
        height=180,
    )
    project_type = st.selectbox(
        "Tipo de proyecto",
        options=list(PROJECT_TYPES.keys()),
        format_func=lambda v: PROJECT_TYPES[v],
    )
    detail_level = st.selectbox(
        "Nivel de detalle",
        options=list(DETAIL_LEVELS.keys()),
        format_func=lambda v: DETAIL_LEVELS[v],
    )
    output_format = st.selectbox(
        "Formato de salida",
        options=list(OUTPUT_FORMATS.keys()),
        format_func=lambda v: OUTPUT_FORMATS[v],
    )
    submitted = st.form_submit_button("Generar estimación")

if submitted:
    if len(description) < MIN_DESCRIPTION_LENGTH:
        st.error(
            f"La descripción es demasiado corta ({len(description)} caracteres). "
            f"Mínimo {MIN_DESCRIPTION_LENGTH}."
        )
    else:
        # Captured side-channel: the bridge generator only yields token TEXT
        # (that's what write_stream paints). Metadata and errors arrive as
        # separate SSE events, so we stash them here to use after the stream ends.
        captured = {"metadata": None, "error": None}

        def token_stream():
            """Bridge: SSE events from the backend → text fragments for write_stream."""
            response = requests.post(
                STREAM_ENDPOINT,
                json={
                    "description": description,
                    "project_type": project_type,
                    "detail_level": detail_level,
                    "output_format": output_format,
                },
                headers={"Accept": "text/event-stream"},
                stream=True,  # don't buffer the whole response
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()

            client = sseclient.SSEClient(response)
            for event in client.events():
                payload = json.loads(event.data)  # all events are JSON-encoded
                if event.event == "token":
                    yield payload  # the text fragment → painted by write_stream
                elif event.event == "done":
                    captured["metadata"] = payload
                elif event.event == "error":
                    captured["error"] = payload.get("error", "Error desconocido")
                    return  # stop the generator on backend error

        try:
            # write_stream paints each yielded fragment live AND returns the
            # full concatenated text once the generator is exhausted.
            st.write_stream(token_stream())

            if captured["error"]:
                # Backend failed mid-generation (arrived as an SSE 'error' event)
                st.error(f"⚠️ El backend falló durante la generación: {captured['error']}")
            elif captured["metadata"]:
                m = captured["metadata"]
                st.markdown(
                    f"_{m['provider']} · {m['model']} · {m['usage']['total_tokens']} tokens_"
                )

        except requests.exceptions.RequestException as exc:
            # Backend unreachable, or non-2xx before streaming started (e.g. 422)
            st.error(
                f"⚠️ No se pudo conectar con el backend en {settings.BACKEND_URL}. "
                f"¿Está levantado? ({type(exc).__name__})"
            )
