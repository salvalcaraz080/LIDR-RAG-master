import json

import requests
import sseclient
import streamlit as st

from config import get_settings

settings = get_settings()

STREAM_ENDPOINT = f"{settings.BACKEND_URL}/api/v1/estimate/stream"
MIN_TRANSCRIPTION_LENGTH = 50
REQUEST_TIMEOUT_SECONDS = 120  # streaming generation can be long

st.title("Estimador de Software")
st.caption("Pega una transcripción de reunión y obtén una estimación detallada.")

# --- Conversation history (survives Streamlit's full-script re-runs) ---
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- Capture user input ---
if prompt := st.chat_input("Pega aquí la transcripción de la reunión..."):
    if len(prompt) < MIN_TRANSCRIPTION_LENGTH:
        st.error(
            f"La transcripción es demasiado corta ({len(prompt)} caracteres). "
            f"Mínimo {MIN_TRANSCRIPTION_LENGTH}."
        )
    else:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            # Captured side-channel: the bridge generator only yields token TEXT
            # (that's what write_stream paints). Metadata and errors arrive as
            # separate SSE events, so we stash them here to use after the stream ends.
            captured = {"metadata": None, "error": None}

            def token_stream():
                """Bridge: SSE events from the backend → text fragments for write_stream."""
                response = requests.post(
                    STREAM_ENDPOINT,
                    json={"transcription": prompt},
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
                full_text = st.write_stream(token_stream())

                if captured["error"]:
                    # Backend failed mid-generation (arrived as an SSE 'error' event)
                    err = f"⚠️ El backend falló durante la generación: {captured['error']}"
                    st.error(err)
                    st.session_state.messages.append({"role": "assistant", "content": err})
                else:
                    # Append metadata line below the estimation, like in Level 1
                    content = full_text
                    if captured["metadata"]:
                        m = captured["metadata"]
                        content += (
                            f"\n\n_{m['provider']} · {m['model']} · "
                            f"{m['usage']['total_tokens']} tokens_"
                        )
                        st.markdown(
                            f"_{m['provider']} · {m['model']} · "
                            f"{m['usage']['total_tokens']} tokens_"
                        )
                    st.session_state.messages.append({"role": "assistant", "content": content})

            except requests.exceptions.RequestException as exc:
                # Backend unreachable, or non-2xx before streaming started (e.g. 422)
                err = (
                    f"⚠️ No se pudo conectar con el backend en {settings.BACKEND_URL}. "
                    f"¿Está levantado? ({type(exc).__name__})"
                )
                st.error(err)
                st.session_state.messages.append({"role": "assistant", "content": err})