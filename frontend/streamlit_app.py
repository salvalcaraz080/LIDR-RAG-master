import requests
import streamlit as st

from config import get_settings

settings = get_settings()

ESTIMATE_ENDPOINT = f"{settings.BACKEND_URL}/api/v1/estimate"
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
        with st.spinner("Generando estimacion..."):
            try:
                response = requests.post(
                    ESTIMATE_ENDPOINT,
                    json={
                        "description": description,
                        "project_type": project_type,
                        "detail_level": detail_level,
                        "output_format": output_format,
                    },
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 400:
                    st.error(
                        f"Solicitud rechazada: {exc.response.json().get('detail', str(exc))}"
                    )
                else:
                    st.error(f"Error del backend: {exc}")
                st.stop()
            except requests.exceptions.RequestException as exc:
                st.error(
                    f"No se pudo conectar con el backend en {settings.BACKEND_URL}. "
                    f"Esta levantado? ({type(exc).__name__})"
                )
                st.stop()

        result = data.get("result", {})
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
                table_data = [
                    {
                        "Fase": p["name"],
                        "Duracion (semanas)": p["duration_weeks"],
                        "Coste (EUR)": p["cost_eur"],
                        "Confianza (%)": p["confidence_pct"],
                    }
                    for p in phases
                ]
                st.table(table_data)

            col1, col2, col3 = st.columns(3)
            col1.metric("Duracion total", f"{total_weeks} semanas")
            col2.metric("Coste total", f"{total_cost:,} EUR")
            col3.metric("Confianza", f"{confidence}%")

        # Metadata footer
        m_model = data.get("model", "")
        m_provider = data.get("provider", "")
        m_usage = data.get("usage", {})
        m_cache = data.get("cache_hit", False)
        m_version = data.get("prompt_version", "")
        st.caption(
            f"_{m_provider} · {m_model} · {m_usage.get('total_tokens', 0)} tokens · "
            f"prompt {m_version} · {'cache hit' if m_cache else 'cache miss'}_"
        )
