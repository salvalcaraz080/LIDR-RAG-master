"""Text embeddings — domain-agnostic. Seed for the future RAG module (sessions 7-8).

Computes a vector for a single string via litellm.aembedding. The only coupling to
OpenAI (the embedding model) is contained here. No retrieval/RAG yet — this module
builds ONLY what the semantic cache needs (embed a string).
"""

import litellm
import structlog

from app.config import get_settings

log = structlog.get_logger()


async def embed_text(text: str) -> list[float]:
    """Return the embedding vector for `text` using the configured embedding model.

    litellm.aembedding returns a dict-shaped response: data[0]["embedding"].
    """
    # Único punto de acoplamiento a OpenAI (vía litellm). Devuelve el vector de 1536 dims.
    model = get_settings().EMBEDDING_MODEL
    response = await litellm.aembedding(model=model, input=text)
    return response.data[0]["embedding"]
