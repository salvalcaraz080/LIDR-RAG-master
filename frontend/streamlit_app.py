import json

import requests
import sseclient
import streamlit as st

from config import get_settings

settings = get_settings()

STREAM_ENDPOINT = f"{settings.BACKEND_URL}/api/v1/estimate/stream"
MIN_DESCRIPTION_LENGTH = 20
REQUEST_TIMEOUT_SECONDS = 120

PROJECT_TYPES = {
    "mobile_app": "App movil",
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


def stream_estimation(payload: dict):
    """Bridge the SSE stream. Yields (event_type, data) tuples.

    Events: 'partial' (incremental dict), 'done' (final {result, metadata}),
    'error' ({error}). The UI consumes the stream endpoint always (decision 27).
    """
    response = requests.post(
        STREAM_ENDPOINT,
        json=payload,
        stream=True,
        headers={"Accept": "text/event-stream"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    client = sseclient.SSEClient(response)
    for event in client.events():
        if not event.data:
            continue
        yield event.event, json.loads(event.data)


def render_result(result: dict, metadata: dict, output_format: str) -> None:
    """Render the validated final result + metadata footer (once, on 'done')."""
    summary = result.get("summary", "")
    phases = result.get("phases", [])
    total_cost = result.get("total_cost_eur", 0)
    total_weeks = result.get("total_duration_weeks", 0)
    confidence = result.get("confidence_pct", 0)

    # Out-of-scope / low-confidence degraded case
    if summary.startswith("Out of scope:") or (confidence == 0 and not phases):
        st.warning(summary or "El proyecto no pudo ser estimado con la informacion proporcionada.")
    else:
        st.subheader("Estimacion")
        st.write(summary)

        if output_format == "phases_table" and phases:
            st.subheader("Desglose por fases")
            st.table(
                [
                    {
                        "Fase": p["name"],
                        "Duracion (semanas)": p["duration_weeks"],
                        "Coste (EUR)": p["cost_eur"],
                        "Confianza (%)": p["confidence_pct"],
                    }
                    for p in phases
                ]
            )

        col1, col2, col3 = st.columns(3)
        col1.metric("Duracion total", f"{total_weeks} semanas")
        col2.metric("Coste total", f"{total_cost:,} EUR")
        col3.metric("Confianza", f"{confidence}%")

    # Metadata footer. Token usage on the stream comes from the include_usage final
    # chunk (see llm_wrapper.stream_structured).
    usage = metadata.get("usage", {})
    cache_hit = metadata.get("cache_hit", False)
    st.caption(
        f"_{metadata.get('provider', '')} · {metadata.get('model', '')} · "
        f"{usage.get('total_tokens', 0)} tokens · prompt {metadata.get('prompt_version', '')} · "
        f"{'cache hit' if cache_hit else 'cache miss'}_"
    )


st.title("Estimador de Software")
st.caption("Describe el proyecto y elige los parametros para obtener una estimacion detallada.")

with st.form("estimation_form"):
    description = st.text_area(
        "Descripcion del proyecto",
        placeholder="Describe el proyecto a estimar (minimo 20 caracteres)...",
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
    submitted = st.form_submit_button("Generar estimacion")

if submitted:
    if len(description) < MIN_DESCRIPTION_LENGTH:
        st.error(
            f"La descripcion es demasiado corta ({len(description)} caracteres). "
            f"Minimo {MIN_DESCRIPTION_LENGTH}."
        )
    else:
        payload = {
            "description": description,
            "project_type": project_type,
            "detail_level": detail_level,
            "output_format": output_format,
        }
        # Consumimos el stream mostrando un contador de fases recibidas (no render
        # progresivo: redibujar la tabla en cada partial parpadea). Pintamos el
        # resultado completo al recibir 'done'.
        # TODO (post sesión en vivo): afinar la presentación de un fallo de validación
        # a mitad de stream.
        progress = st.empty()
        try:
            done_event = None
            error_msg = None
            with st.spinner("Generando estimacion..."):
                for event_type, data in stream_estimation(payload):
                    if event_type == "partial":
                        n_phases = len(data.get("phases", []) or [])
                        progress.caption(f"Recibiendo estimacion... ({n_phases} fases)")
                    elif event_type == "done":
                        done_event = data
                    elif event_type == "error":
                        error_msg = data.get("error", "Error desconocido")
            progress.empty()
        except requests.exceptions.RequestException as exc:
            st.error(
                f"No se pudo conectar con el backend en {settings.BACKEND_URL}. "
                f"Esta levantado? ({type(exc).__name__})"
            )
            st.stop()

        if error_msg is not None:
            st.error(f"Solicitud rechazada: {error_msg}")
        elif done_event is not None:
            render_result(
                done_event.get("result", {}),
                done_event.get("metadata", {}),
                output_format,
            )
        else:
            st.error("El stream termino sin un resultado valido.")
