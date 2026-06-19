# CLAUDE.md

Sistema de estimación de software con arquitectura CAG (Context-Augmented Generation).
API REST construida con FastAPI. Parte del máster LIDR RAG & Agentes; evolucionará a RAG y luego a agentes.

## Principio rector — máxima reutilización

Al portar el proyecto a otro dominio (futuro: RAG sobre ECSS), debe cambiarse el MÍNIMO de piezas:

| Reutilizable / agnóstico de dominio | Dominio (cambia al portar) |
|--------------------------------------|---------------------------|
| `llm_wrapper`, `guardrails`, `embeddings`, `cache`, `documents`, logging, config | schemas, prompts, ejemplos, validadores de negocio |

Cualquier acoplamiento a proveedor debe quedar contenido en una sola pieza aislada.

## Comandos

> Cuando des comandos, recuerda que la consola es **Powershell**. Adapta la sintaxis.

```bash
# Arrancar servidor en local (desarrollo)
uv run uvicorn app.main:app --reload

# Añadir / quitar dependencias (NUNCA usar pip)
uv add <paquete>
uv remove <paquete>

# Ejecutar cualquier cosa dentro del entorno del proyecto
uv run <comando>

# Docker (flujo con Compose)
docker compose up            # arranca (build si hace falta)
docker compose up --build    # fuerza rebuild
docker compose down          # para y elimina
```

Swagger en `http://127.0.0.1:8000/docs`. Health check en `/health`.

## Entorno

- **Gestor de paquetes: `uv`. Nunca usar `pip` ni `pip install`.**
- Python fijado a **3.11** vía `.python-version` (la máquina global tiene 3.12; uv gestiona la del proyecto).
- SO de desarrollo: Windows + PowerShell.
- Las API keys viven en `.env` (ignorado por git y docker). Nunca hornearlas en la imagen; se inyectan en runtime.

## Estructura por capas

El proyecto separa responsabilidades de forma estricta. Respetar esta separación al añadir código:

- `app/routers/` — endpoints HTTP. Reciben, validan, **delegan**, devuelven. SIN lógica de negocio.
- `app/services/` — lógica de negocio y adaptadores:
  - `llm_service.py` — orquesta la estimación: valida input (guardrails), pide el prompt al loader, llama a `cache`, mapea el resultado al dominio. Recibe primitivas tipadas, no el schema.
  - `guardrails.py` — validación de input **agnóstica de dominio**: moderation (litellm), heurística de inyección Markdown, social engineering. Respeta `GUARDRAILS_ENFORCE` (True en production → raise; False → log-only). `InputGuardrailError` es la excepción de dominio; el router la traduce a HTTP 400.
  - `cache.py` — **caché semántico** Redis (redisvl `SemanticCache`) **agnóstico de dominio**. Reescrito en B5: elimina el exact-match sha256 de S03. Clave compuesta = bucket (TAG filtrable determinista `prompt_version:project_type:detail_level:output_format`) + vector (embedding de la descripción). `semantic_lookup`/`semantic_write` reciben/devuelven strings opacos (el servicio (de)serializa `EstimationResult`+metadata) → cache NO menciona estimación. Modo log-only (`SEMANTIC_CACHE_ENFORCE=False`): hace lookup y loguea vecino+distancia pero devuelve None (no bypassa el LLM). Vector propio pasado a acheck/astore (sin vectorizer HF/torch: `CustomVectorizer` dummy solo fija dims=1536). Fallos de Redis → miss no fatal.
  - `embeddings.py` — **agnóstico de dominio**, semilla del RAG (sesiones 7-8). `embed_text(text)` vía `litellm.aembedding` con `openai/text-embedding-3-small` (1536 dims). Acoplamiento a OpenAI contenido aquí. Solo embebe un string (no retrieval todavía).
  - `documents.py` — **agnóstico de dominio**, semilla del pipeline de adjuntos RAG (sesiones 7-8). `extract_text(file_bytes, filename) -> str`. Dispatch por extensión (`.pdf`, `.docx`). Síncrono (CPU-bound puro); quien lo llame en async debe usar `asyncio.to_thread`. `DocumentExtractionError` envuelve excepciones de pypdf/python-docx. Guarda de texto vacío: nunca devuelve string vacío silencioso. No cableado al flujo de estimación todavía.
  - `llm_wrapper.py` — adaptador LLM **agnóstico de dominio**. Usa `instructor.from_litellm(acompletion)` (AsyncInstructor). Expone `complete_structured` (one-shot, retries Instructor) y `stream_structured` (Partial streaming). Fallback OpenAI→Anthropic vía `fallbacks=["anthropic/..."]` kwarg (formato lista-de-strings; el formato dict del Router NO funciona aquí). Ver nota sobre trade-off vs Router.
- `app/prompts/` — prompts versionados en templates Jinja2 (delimitadores Markdown). `loader.py` (`render_estimation_prompt`) renderiza `estimation/v1/{system.j2, user.j2, examples.j2}` y devuelve la tupla `(system, user)`. Recibe primitivas tipadas para mantenerse desacoplado del borde HTTP. `StrictUndefined`: toda variable del template debe estar en el contexto. Inyecta `out_of_scope_prefix` desde `OUT_OF_SCOPE_PREFIX` del schema.
- `app/schemas/` — contratos Pydantic (request/response) con Enums tipados. Son el borde HTTP, no el núcleo.
  - `OUT_OF_SCOPE_PREFIX` — constante compartida: la importan el validador (`low_confidence_must_be_explicit`) Y el loader (la inyecta en el contexto del template). Un único sitio.
  - `EstimationResult` — output estructurado del LLM, con dos `model_validator`: `total_must_match_sum_of_phases` (computa `total_duration_weeks` y `total_cost_eur` como suma exacta de fases; no los valida, los sobreescribe — el LLM solo necesita definir bien las fases) y `low_confidence_must_be_explicit` (confidence<30 requiere prefijo out-of-scope).
- `app/context/` — datos de referencia estáticos. **Obsoleto en la fase CAG actual** (los ejemplos few-shot viven en `prompts/estimation/v1/examples.j2`); se reintroduce como fuente de datos en el módulo RAG.
- `app/dependencies.py` — dependency providers de FastAPI.
- `app/logging_config.py` — configura structlog una vez al arrancar.
- `app/config.py` — `BaseSettings`. `GUARDRAILS_ENFORCE` y `SEMANTIC_CACHE_ENFORCE` (`bool | None`; None = derivado de APP_ENV: True en production, False/log-only en development). `EMBEDDING_MODEL` (`openai/text-embedding-3-small`). `SEMANTIC_CACHE_DISTANCE_THRESHOLD` (0.15, ≈0.85 similaridad, laxo a propósito).
- `app/main.py` — punto de entrada: logging, lifespan (Redis), middleware, routers.

## Nota: Instructor vs Router (trade-off documentado)

`instructor.from_litellm(acompletion)` sustituye al Router singleton de S03 para la ruta estructurada.

- **PERDIDO**: estado de cooldown entre requests; fail-fast al arrancar.
- **PRESERVADO**: fallback OpenAI→Anthropic en cada llamada, vía `fallbacks=["anthropic/claude-haiku-4-5-20251001"]`.
- **Razón**: `instructor.patch(Router(...))` tiene un bug conocido (carryover de params entre requests). `from_litellm(acompletion)` es la integración estable.
- El formato correcto del kwarg es lista-de-strings: `["anthropic/..."]`. El formato dict del Router (`[{"primary": [...]}]`) no funciona con `acompletion` directo.

## Pipeline de guardrails (capas de defensa)

```
Capa 2 — validate_input()         ← PRIMERO en generate_estimation, antes de caché
  ├─ OpenAI Moderation API (litellm.moderation)  → InputGuardrailError si flagged
  ├─ Inyección Markdown (headers del sistema)     → InputGuardrailError si detecta
  └─ Social engineering phrases                   → InputGuardrailError si detecta

Capa 3 — Prompt scope (system.j2)
  └─ Si descripción vaga/off-topic → summary empieza "Out of scope:", totales=0, phases=[]

Capa 4 — EstimationResult.total_must_match_sum_of_phases
  └─ Coherencia interna: retry de Instructor si totales no cuadran con fases

Capa 5 — EstimationResult.low_confidence_must_be_explicit
  └─ confidence<30 sin prefijo out-of-scope → retry de Instructor
```

En modo log-only (`GUARDRAILS_ENFORCE=False`): las capas 2–5 loguean pero no bloquean.
PENDIENTE: tracker/métricas sobre logs de guardrails.

## Infraestructura de soporte

- **Redis Stack** — `redis/redis-stack` (NO `redis:alpine`): superconjunto con el motor de búsqueda vectorial que necesita `SemanticCache`. El `SemanticCache` se construye **una vez** en `lifespan` (crea el índice), se inyecta con `Depends(get_semantic_cache)`. Abre su propio cliente vía `redis_url` (los bytes binarios del vector son incompatibles con `decode_responses=True`). TTL: 24 h.
- **structlog** — logging estructurado con `request_id` y `path` por request.

## Flujo de una request (`POST /api/v1/estimate`)

```
Router → valida EstimationRequest (description + project_type/detail_level/output_format)
       → desempaqueta en primitivas + inject semantic_cache (Depends)
       → llm_service.generate_estimation(...)
           → validate_input(description)              ← PRIMERO (Capa 2, invariante)
           → vector = embed_text(description)         ← UNA vez, reusado para lookup y write
           → bucket = build_bucket_key(prompt_version, project_type, detail_level, output_format)
           → cache.semantic_lookup(vector, bucket, enforce)
               → hit (enforce): deserializa {result, metadata} cacheado → cache_hit=True
               → miss / log-only: None
           → miss: render_estimation_prompt → messages → llm_wrapper.complete_structured (Instructor retry)
                   → cache.semantic_write(vector, bucket, description, payload)  ← solo tras validación OK
           → mapea a dict plano {result, model, provider, usage, cache_hit, prompt_version}
       → EstimationResponse(**result)  ← validación Pydantic en el borde
```

**Ambos endpoints** usan el cache semántico (B5). El **principal es `/estimate` (no-stream)**, que es el que consume la UI: devuelve el `EstimationResult` validado completo de una vez. El streaming (`/estimate/stream`) se **CONSERVA** como secundario (referencia/reutilización para otros proyectos), no lo usa la UI: HIT emite el cacheado como evento `done`; MISS streamea Partials, valida post-hoc al cerrar y cachea. La mecánica de validación-en-stream quedó como PROVISIONAL.

## Convenciones del proyecto

- **El servicio devuelve dicts planos, no instancia los schemas Pydantic.**
- **`validate_input` es la PRIMERA operación del servicio** (invariante: antes de construir mensajes y antes del cache lookup; solo se cachean outputs que pasaron validación).
- **El prompt vive en templates Jinja2 versionados (`app/prompts/`), no en f-strings.**
- **El loader recibe primitivas tipadas, no `EstimationRequest`.**
- **`llm_wrapper`, `guardrails`, `embeddings` y `cache` NO mencionan "estimación"** — son agnósticos de dominio. El cache opera sobre strings opacos; el servicio (de)serializa el dominio.
- **El embedding se computa UNA vez por request** y se reutiliza para lookup y write.
- **`OUT_OF_SCOPE_PREFIX` en un único sitio** (`schemas/estimations.py`); lo importan validador y loader.
- El archivo de schemas es `app/schemas/estimations.py` (plural). Import: `from app.schemas.estimations import ...`.
- **Imports ordenados:** stdlib → terceros → locales.
- API versionada con prefijo `/api/v1`.

## Documentación del código (comentarios)

El código debe poder entenderse **leyendo solo los comentarios**, sin parsear la lógica.

- **Comentarios agrupados, no línea a línea**: un comentario encabeza un bloque de líneas relacionadas y resume QUÉ hace ese bloque.
- **Resumir, no narrar**: explica la intención del bloque; no repitas literalmente lo que ya dice el código (`# incrementa i` sobre `i += 1` está prohibido).
- **El porqué cuando no es obvio**: si una línea hace algo no evidente (workaround, invariante, decisión de diseño), el comentario explica el motivo.
- **Docstring por módulo/función** para el contrato (qué recibe, qué devuelve, invariantes); comentarios inline para los pasos internos.
- Idioma: comentarios en español (consistente con el resto del proyecto); el código y los nombres, en inglés.

## Git

- Modelo de ramas: `main` = estado terminado; `sesion-NN` = rama de trabajo, se fusiona a `main` al cerrar.
- Commits pequeños y descriptivos.
- Antes de commitear: `git check-ignore .env` debe devolver `.env`.

## Pendientes

- **Validación-en-stream**: la mecánica exacta (validar Partials a mitad de stream vs post-hoc al cerrar) es PROVISIONAL, pendiente de la sesión en vivo. Implementado el mínimo: acumular partials + validar al final. El frontend la marca con TODO.
- **Tracker/métricas de guardrails**: los disparos se loguean con structlog; falta un dashboard/agregador.
- **Tracker de hit-rate del cache semántico**: en modo log-only se loguea (input, vecino top-1, distancia); falta un agregador para calibrar el `distance_threshold` con datos reales.

## Restricciones

- No reproducir credenciales ni valores del `.env` en código, logs ni respuestas.
- Embeddings permitidos SOLO para el cache semántico (`embeddings.py`). NO introducir RAG/retrieval, base de datos vectorial como feature, ni persistencia de dominio todavía: el proyecto sigue en fase CAG (los embeddings son la semilla del RAG, no el RAG).
- No añadir dependencias sin reflejarlas vía `uv add`.
- No usar `instructor.patch(Router(...))` — bug conocido de carryover de params.
- No usar el vectorizer HF local de redisvl (mete torch). Computar el vector vía `litellm.aembedding` y pasarlo a acheck/astore con `vector=`.
