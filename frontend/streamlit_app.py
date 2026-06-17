import requests
import streamlit as st

from config import get_settings

# --- Configuración: endpoint principal (no-stream) y límites de la request ---
settings = get_settings()

ESTIMATE_ENDPOINT = f"{settings.BACKEND_URL}/api/v1/estimate"
MIN_DESCRIPTION_LENGTH = 20
REQUEST_TIMEOUT_SECONDS = 120

# --- Opciones del formulario: value del Enum del backend → label legible en UI ---
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


def render_result(result: dict, meta: dict, output_format: str) -> None:
    """Pinta el resultado de la estimación y el pie con metadatos."""
    # Extrae los campos del EstimationResult (con defaults por si falta alguno).
    summary = result.get("summary", "")
    phases = result.get("phases", [])
    total_cost = result.get("total_cost_eur", 0)
    total_weeks = result.get("total_duration_weeks", 0)
    confidence = result.get("confidence_pct", 0)

    # Caso degradado out-of-scope / baja confianza: solo aviso, sin tabla ni métricas.
    if summary.startswith("Out of scope:") or (confidence == 0 and not phases):
        st.warning(summary or "El proyecto no pudo ser estimado con la informacion proporcionada.")
    else:
        # Resumen siempre; tabla de fases solo en formato phases_table.
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

        # Métricas agregadas (totales calculados en el servidor a partir de las fases).
        col1, col2, col3 = st.columns(3)
        col1.metric("Duracion total", f"{total_weeks} semanas")
        col2.metric("Coste total", f"{total_cost:,} EUR")
        col3.metric("Confianza", f"{confidence}%")

    # Pie de metadatos: proveedor, modelo, tokens reales, versión de prompt y cache hit/miss.
    usage = meta.get("usage", {})
    cache_hit = meta.get("cache_hit", False)
    st.caption(
        f"_{meta.get('provider', '')} · {meta.get('model', '')} · "
        f"{usage.get('total_tokens', 0)} tokens · prompt {meta.get('prompt_version', '')} · "
        f"{'cache hit' if cache_hit else 'cache miss'}_"
    )


# --- Cabecera y formulario de producto (no chat): descripción + 3 parámetros tipados ---
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
    # Validación de longitud en el cliente (mismo mínimo que el schema del backend).
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
        # Llamada al endpoint principal no-stream: respuesta completa de una vez.
        with st.spinner("Generando estimacion..."):
            try:
                response = requests.post(
                    ESTIMATE_ENDPOINT, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
                )
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.HTTPError as exc:
                # 400 = input rechazado por guardrails (enforce); otros = error del backend.
                if exc.response is not None and exc.response.status_code == 400:
                    detail = exc.response.json().get("detail", str(exc))
                    st.error(f"Solicitud rechazada: {detail}")
                else:
                    st.error(f"Error del backend: {exc}")
                st.stop()
            except requests.exceptions.RequestException as exc:
                # Backend inaccesible (caído o URL mal configurada).
                st.error(
                    f"No se pudo conectar con el backend en {settings.BACKEND_URL}. "
                    f"Esta levantado? ({type(exc).__name__})"
                )
                st.stop()

        # La respuesta es un EstimationResponse plano: result + metadatos al mismo nivel.
        render_result(
            data.get("result", {}),
            {k: data.get(k) for k in ("model", "provider", "usage", "cache_hit", "prompt_version")},
            output_format,
        )
