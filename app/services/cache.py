"""Semantic cache over structured LLM outputs — domain-agnostic.

Replaces the S03 exact-match (sha256) cache entirely. Rationale: human/contextual
inputs are almost never byte-identical, so an exact-match cache rarely hits; semantic
similarity subsumes the exact case (distance 0 == identical input).

Backed by redisvl SemanticCache on a Redis Stack instance (needs the vector search
engine — plain redis:alpine does NOT work).

Embeddings are computed by us (services.embeddings) and passed as `vector=` to
acheck/astore. redisvl therefore never loads a local HF/torch vectorizer: a dummy
CustomVectorizer only exists to fix the schema dims (1536) at construction.

Cache key = composite:
  - bucket  (filterable TAG): deterministic exact-match partition. Encodes
            prompt_version + the typed parameters → a prompt/schema bump lands in a
            fresh bucket (old entries are orphaned and TTL-expire). Resolves the S03
            pending "include prompt_version in the cache key".
  - vector  (embedding of the free-text input): similarity search WITHIN the bucket.

Log-only mode (enforce=False): lookup runs and LOGS the nearest neighbor + distance,
but returns None so the caller never bypasses the LLM (observe-before-trust).

Graceful degradation: any Redis failure logs a warning and is treated as a miss.
"""

import structlog
from redisvl.extensions.cache.llm import SemanticCache
from redisvl.query.filter import Tag
from redisvl.utils.vectorize.custom import CustomVectorizer

log = structlog.get_logger()

CACHE_TTL_SECONDS = 86400  # 24h
EMBEDDING_DIMS = 1536  # openai/text-embedding-3-small
CACHE_INDEX_NAME = "llm_semantic_cache"
BUCKET_FIELD = "bucket"


def make_semantic_cache(redis_url: str, distance_threshold: float) -> SemanticCache:
    """Build the SemanticCache and create its Redis index. Call ONCE at startup.

    Uses a dummy CustomVectorizer (returns a zero vector) purely to fix the schema
    dims — it is never invoked because we always pass our own `vector=`.

    Opens its own redis connection via redis_url: redisvl needs raw bytes for the
    vector field, which is incompatible with the app's decode_responses=True client.
    """
    # Vectorizer dummy: solo fija dims=1536 en el schema; nunca se invoca (pasamos vector=).
    dummy = CustomVectorizer(embed=lambda _text: [0.0] * EMBEDDING_DIMS)
    # filterable_fields define el TAG "bucket" por el que filtramos en los lookups.
    return SemanticCache(
        name=CACHE_INDEX_NAME,
        redis_url=redis_url,
        distance_threshold=distance_threshold,
        ttl=CACHE_TTL_SECONDS,
        vectorizer=dummy,
        filterable_fields=[{"name": BUCKET_FIELD, "type": "tag"}],
    )


def build_bucket_key(*parts: str) -> str:
    """Compose the deterministic bucket TAG from caller-supplied parts."""
    return ":".join(parts)


async def semantic_lookup(
    cache: SemanticCache,
    vector: list[float],
    bucket: str,
    *,
    enforce: bool,
) -> str | None:
    """Return the cached response string for a semantic hit within `bucket`, or None.

    enforce=False (log-only): logs the nearest neighbor + distance but returns None,
    so the caller does NOT bypass the LLM.
    """
    # Busca el vecino más cercano dentro del bucket; fallo de Redis → miss no fatal.
    try:
        hits = await cache.acheck(
            vector=vector,
            num_results=1,
            filter_expression=(Tag(BUCKET_FIELD) == bucket),
        )
    except Exception as exc:
        log.warning("semantic_cache_lookup_failed", error=str(exc))
        return None

    # Sin vecino dentro del umbral de distancia → miss.
    if not hits:
        log.info("semantic_cache_miss", bucket=bucket)
        return None

    hit = hits[0]
    distance = hit.get("vector_distance")

    if not enforce:
        # Shadow mode: observe the would-be hit, but don't serve it.
        log.info("semantic_cache_shadow_hit", bucket=bucket, distance=distance)
        return None

    log.info("semantic_cache_hit", bucket=bucket, distance=distance)
    return hit.get("response")


async def semantic_write(
    cache: SemanticCache,
    vector: list[float],
    bucket: str,
    prompt: str,
    response: str,
) -> None:
    """Store `response` under (vector, bucket). Non-fatal on Redis failure.

    `prompt` is the free-text input — redisvl derives the entry id from it, so the
    same input+bucket overwrites rather than duplicating.
    """
    try:
        await cache.astore(
            prompt=prompt,
            response=response,
            vector=vector,
            filters={BUCKET_FIELD: bucket},
        )
    except Exception as exc:
        log.warning("semantic_cache_write_failed", error=str(exc))
