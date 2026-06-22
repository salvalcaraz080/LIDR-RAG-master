# Informe de auditoría — Estado del código previo a memoria conversacional

> Auditoría read-only realizada el 2026-06-20, rama `sesion-05`.
> Objetivo: groundear cinco decisiones de diseño antes de implementar memoria conversacional (Sesión 5, cap. 2).

---

## Bloque 0 — Mapa

```
app/
├── __init__.py
├── config.py
├── dependencies.py
├── logging_config.py
├── main.py
├── context/
│   ├── __init__.py
│   └── examples.py
├── prompts/
│   ├── __init__.py
│   ├── loader.py
│   └── estimation/
│       └── v1/          (system.j2, user.j2, examples.j2)
├── routers/
│   ├── __init__.py
│   └── estimations.py
├── schemas/
│   ├── __init__.py
│   └── estimations.py
└── services/
    ├── __init__.py
    ├── cache.py
    ├── documents.py
    ├── embeddings.py
    ├── guardrails.py
    ├── llm_service.py
    └── llm_wrapper.py

tests/
├── conftest.py
├── test_cache.py
├── test_documents.py
├── test_guardrails.py
├── test_health.py
├── test_llm_wrapper.py
├── test_prompt_rendering.py
└── test_schemas.py
```

---

## Bloque A — Caché semántico

### Firmas exactas en `app/services/cache.py`

```python
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

async def semantic_write(
    cache: SemanticCache,
    vector: list[float],
    bucket: str,
    prompt: str,
    response: str,
) -> None:
```

### Composición del bucket

Se llama desde `llm_service._bucket`:

```python
def _bucket(project_type: str, detail_level: str, output_format: str) -> str:
    return cache.build_bucket_key(
        PROMPT_VERSION, project_type, detail_level, output_format
    )
```

Resultado: `"v1:web_saas:medium:phases_table"` (cuatro partes, `:` como separador).

### El vector

Se computa en `llm_service._validate_embed_and_lookup`:

```python
vector = await embed_text(description)  # once — reused for the write on miss
```

Una sola llamada por request. Se pasa directamente a `semantic_lookup` y `semantic_write`.

### Flujo lookup → miss → wrapper → write (extracto de `generate_estimation`)

```python
vector, bucket, cached = await _validate_embed_and_lookup(
    description, project_type, detail_level, output_format, semantic_cache
)
if cached is not None:
    payload = json.loads(cached)
    return _map_to_response(payload["result"], payload["metadata"], cache_hit=True)

# Miss → LLM (Instructor validates + retries)
messages = _build_messages(description, project_type, detail_level, output_format)
result, metadata = await llm_wrapper.complete_structured(
    messages, EstimationResult, MAX_TOKENS
)

# Write only after successful validation
payload = json.dumps({"result": result.model_dump(), "metadata": metadata})
await cache.semantic_write(semantic_cache, vector, bucket, description, payload)
```

### `distance_threshold` y enforce

`distance_threshold` vive en `Settings.SEMANTIC_CACHE_DISTANCE_THRESHOLD = 0.15`. Se pasa a `make_semantic_cache` en el lifespan. El `enforce` se resuelve en runtime: `get_settings().semantic_cache_enforce` (propiedad derivada de `APP_ENV`, no campo directo). Coincide con el doc.

---

## Bloque B — Wrapper LLM

### Firma exacta de `complete_structured`

```python
async def complete_structured(
    messages: list[dict],
    response_model: type[T],
    max_tokens: int,
    max_retries: int = 2,
) -> tuple[T, dict]:
```

Devuelve `(instancia_validada_de_T, metadata_dict)`. El `metadata_dict` tiene la forma `{"model": str, "provider": str, "usage": {"input_tokens": int, "output_tokens": int, "total_tokens": int}}`.

### Cómo construye `llm_service` los messages

```python
def _build_messages(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
) -> list[dict]:
    system, user = render_estimation_prompt(...)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
```

Lista de dicts `role`/`content`. Siempre dos elementos: `system` + `user`.

### ¿`complete_structured` se llama ya con `response_model` distinto de `EstimationResult`?

No. La única llamada en el código es:

```python
result, metadata = await llm_wrapper.complete_structured(
    messages, EstimationResult, MAX_TOKENS
)
```

El patrón "varios schemas" no existe todavía; `EstimationResult` es el único `response_model` en uso.

---

## Bloque C — Modelos de dominio y frontera de schemas

### ¿Existe `app/services/sessions.py`?

No existe. Ningún módulo de sesión o estado conversacional.

### Modelos en `app/schemas/estimations.py`

| Clase | ¿Contrato HTTP? |
|-------|-----------------|
| `ProjectType(str, Enum)` | Sí (parámetro de request) |
| `DetailLevel(str, Enum)` | Sí |
| `OutputFormat(str, Enum)` | Sí |
| `Phase(BaseModel)` | Parte del output estructurado del LLM; se incrusta en `EstimationResult` |
| `EstimationResult(BaseModel)` | Output estructurado del LLM — **es a la vez schema de dominio Y parte del contrato HTTP** (`EstimationResponse.result`). No es solo borde HTTP. |
| `TokenUsage(BaseModel)` | Parte del contrato de respuesta |
| `EstimationResponse(BaseModel)` | Sí, contrato de respuesta |
| `EstimationRequest(BaseModel)` | Sí, contrato de request |

> ⚠️ **Divergencia con CLAUDE.md**: el doc dice "schemas = contratos HTTP, no el núcleo". En la práctica, `EstimationResult` y `Phase` son modelos de dominio (los instancia Instructor, los validan los `model_validator`) que viven en `schemas/`. No es un problema funcional, pero la convención no se sostiene del todo: `EstimationResult` es dominio alojado en la capa HTTP.

### Patrones Pydantic en uso (para ser idiomático)

- `BaseModel` (no `dataclass`)
- `model_validator(mode="after")` — dos en `EstimationResult`
- `Field(ge=..., le=..., default_factory=list)`
- `model_dump()` — usado en `llm_service` para serializar
- `model_dump(exclude_unset=True)` — en el path de streaming
- `model_config = SettingsConfigDict(...)` en `Settings`
- No se usa `model_copy`, no se usa `model_dump_json`

---

## Bloque D — Orquestación y prompts

### Firma de `generate_estimation`

```python
async def generate_estimation(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
    semantic_cache: SemanticCache,
) -> dict:
```

Recibe primitivas `str` (los Enums ya están desempaquetados por el router con `.value`). Devuelve `dict` plano con shape `{result, model, provider, usage, cache_hit, prompt_version}`.

### Firma de `render_estimation_prompt`

```python
def render_estimation_prompt(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
    version: str = "v1",
) -> tuple[str, str]:
```

Devuelve `(system, user)`. También recibe primitivas, no `EstimationRequest`.

### Contenido de `system.j2`

Secciones con headers `##`:

```
## Role
## Project Context         ← inyecta {{ project_type }}, {{ detail_level }}, {{ output_format }}
## Scope                   ← usa {{ out_of_scope_prefix }}
## Pricing Rules
## Output Structure
{% if output_format == "phases_table" %} ## Presentation Hint {% endif %}
{% if output_format == "narrative" %}   ## Presentation Hint {% endif %}
{% if detail_level == "detailed" %}     ## Additional Detail  {% endif %}
{% include "estimation/v1/examples.j2" %}
```

Variables que recibe el contexto (en `loader.py`):

```python
context = {
    "description": description,
    "project_type": project_type,
    "detail_level": detail_level,
    "output_format": output_format,
    "out_of_scope_prefix": OUT_OF_SCOPE_PREFIX,
}
```

El `Environment` usa `StrictUndefined` — toda variable nueva en el template **debe** pasarse en este dict o el render falla en tiempo de render, no en tiempo de ejecución del LLM.

### `OUT_OF_SCOPE_PREFIX` — dónde vive y quién lo importa

Definido en `app/schemas/estimations.py`:

```python
OUT_OF_SCOPE_PREFIX = "Out of scope:"
```

Importado en:
- `app/schemas/estimations.py` — el validador `low_confidence_must_be_explicit` lo usa directamente
- `app/prompts/loader.py` — `from app.schemas.estimations import OUT_OF_SCOPE_PREFIX`

Exactamente los dos sitios que dice el doc. Sin divergencias.

---

## Bloque E — Router y request

### Firmas de los handlers

```python
@router.post("/estimate", response_model=EstimationResponse)
async def create_estimation(
    request: EstimationRequest,
    semantic_cache: SemanticCache = Depends(get_semantic_cache),
) -> EstimationResponse:

@router.post("/estimate/stream", response_class=EventSourceResponse)
async def create_estimation_stream(
    request: EstimationRequest,
    semantic_cache: SemanticCache = Depends(get_semantic_cache),
):
```

El segundo no declara `response_model` (SSE no es JSON directo).

### `EstimationRequest` — campos y Enums

```python
class EstimationRequest(BaseModel):
    description: str = Field(..., min_length=20, max_length=2000, ...)
    project_type: ProjectType      # "mobile_app" | "web_saas" | "internal_tool" | "data_pipeline"
    detail_level: DetailLevel      # "summary" | "medium" | "detailed"
    output_format: OutputFormat    # "phases_table" | "narrative"
```

### ¿El router desempaqueta en primitivas?

Sí. En `create_estimation`:

```python
result = await generate_estimation(
    request.description,
    request.project_type.value,
    request.detail_level.value,
    request.output_format.value,
    semantic_cache,
)
```

Idéntico en `create_estimation_stream`. Coincide con el doc.

---

## Bloque F — Despliegue / workers

### Docker Compose — comando del backend

```yaml
command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**1 worker, con `--reload`**. El `--reload` implica un proceso hijo que se reinicia al detectar cambios; en términos de estado en memoria, sigue siendo proceso único. Sin `--workers N`, sin gunicorn.

### Dockerfile — CMD de producción

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**1 worker, sin `--reload`**. Sin workers múltiples.

### Arranque local documentado (README / CLAUDE.md)

```powershell
uv run uvicorn app.main:app --reload
```

**1 worker, con `--reload`**.

**Conclusión**: en todos los escenarios (Compose dev, imagen prod, local) uvicorn corre con un solo worker. Un dict `{session_id: ConversationSession}` en memoria de proceso es tolerable en este setup — no hay riesgo de fragmentación entre réplicas.

---

## Bloque G — Módulo de adjuntos

### `app/services/documents.py` — API pública

Existe. Firma pública exacta:

```python
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx"})

class DocumentExtractionError(Exception):
    """Error de dominio del módulo: tipo no soportado, archivo corrupto o sin texto extraíble."""

def extract_text(file_bytes: bytes, filename: str) -> str:
    """Extrae texto plano de un único documento. bytes → str. ..."""
```

Helpers privados: `_extract_pdf(file_bytes: bytes) -> str`, `_extract_docx(file_bytes: bytes) -> str`.

### `tests/test_documents.py` — casos cubiertos (8 tests)

| Clase | Caso |
|-------|------|
| `TestPdfExtraction` | PDF conocido contiene ambas centinelas |
| `TestPdfExtraction` | PDF conocido contiene marcadores `--- Page 1 ---` / `--- Page 2 ---` |
| `TestPdfExtraction` | PDF en blanco → `DocumentExtractionError` |
| `TestPdfExtraction` | Bytes corruptos `.pdf` → `DocumentExtractionError` (no excepción cruda) |
| `TestDocxExtraction` | DOCX conocido contiene centinela |
| `TestExtensionDispatch` | Extensión `.txt` → `DocumentExtractionError` |
| `TestExtensionDispatch` | Extensión `DOC.PDF` (mayúsculas) → extrae correctamente |
| `TestExtensionDispatch` | `SUPPORTED_EXTENSIONS` contiene `.pdf` y `.docx` |

Fixtures generadas en código (sin archivos en disco); sin LLM, sin Redis.

**Estado**: el módulo existe y está testado. No está cableado al flujo de estimación.

---

## Bloque H — Config

Campos de `Settings(BaseSettings)` con sus defaults:

| Campo | Tipo | Default |
|-------|------|---------|
| `OPENAI_API_KEY` | `str` | *(requerida, sin default)* |
| `ANTHROPIC_API_KEY` | `str` | *(requerida, sin default)* |
| `LLM_MODEL` | `str` | `"openai/gpt-4o-mini"` |
| `EMBEDDING_MODEL` | `str` | `"openai/text-embedding-3-small"` |
| `APP_ENV` | `str` | `"development"` |
| `LOG_LEVEL` | `str` | `"DEBUG"` |
| `REDIS_URL` | `str` | `"redis://redis:6379"` |
| `GUARDRAILS_ENFORCE` | `bool \| None` | `None` → derivado de `APP_ENV` |
| `SEMANTIC_CACHE_DISTANCE_THRESHOLD` | `float` | `0.15` |
| `SEMANTIC_CACHE_ENFORCE` | `bool \| None` | `None` → derivado de `APP_ENV` |

Propiedades derivadas (no campos directos, no aparecen en `.env`):

- `guardrails_enforce -> bool`
- `semantic_cache_enforce -> bool`

Ningún campo relacionado con memoria conversacional (`MAX_TURNS`, TTL de sesión, política de caché conversacional). No existe todavía.

---

## Resumen de divergencias con la documentación

| # | Dónde | Divergencia |
|---|-------|-------------|
| ⚠️ 1 | `CLAUDE.md` / `ARCHITECTURE.md` § schemas | Afirman que "schemas = contratos HTTP, no el núcleo del dominio". En el código, `EstimationResult` y `Phase` son modelos de dominio (instanciados por Instructor, con `model_validator` de negocio) que viven en `schemas/estimations.py`. No es un problema funcional, pero la convención documentada no se sostiene al 100%: `EstimationResult` es dominio alojado en la capa HTTP. |

El resto del código está alineado con ambos documentos.
