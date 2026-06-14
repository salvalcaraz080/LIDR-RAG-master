# CLAUDE.md

Sistema de estimación de software con arquitectura CAG (Context-Augmented Generation).
API REST construida con FastAPI. Parte del máster LIDR RAG & Agentes; evolucionará a RAG y luego a agentes.

## Comandos

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
- `app/services/` — lógica de negocio: construcción del prompt, caché, llamada al LLM, postprocesamiento.
  - `llm_service.py` — orquesta la estimación: construye el prompt, llama a `cache`, mapea el resultado al dominio.
  - `cache.py` — capa de caché Redis sobre `llm_wrapper.complete`. Cache-aside: miss → LLM → write. Fallos de Redis se degradan a miss (no fatales).
  - `llm_wrapper.py` — adaptador LLM agnóstico de dominio. Gestiona un LiteLLM Router singleton con fallback automático OpenAI → Anthropic. Expone `complete` (one-shot async) y `stream` (async generator de eventos tipados). El modelo primario se lee de `LLM_MODEL` en config; el secundario (`claude-haiku-4-5`) está fijo como infraestructura.
- `app/schemas/` — contratos Pydantic (request/response). Son el borde HTTP, no el núcleo.
- `app/context/` — datos de referencia estáticos para CAG. Punto de sustitución futuro para RAG.
- `app/dependencies.py` — dependency providers de FastAPI (`get_redis`: extrae el cliente Redis de `app.state`).
- `app/logging_config.py` — configura structlog una vez al arrancar. Console en dev, JSON en producción. Driven por `APP_ENV` y `LOG_LEVEL`.
- `app/config.py` — `BaseSettings` de Pydantic, cacheado con `@lru_cache`.
- `app/main.py` — punto de entrada: configura logging, registra `lifespan` (Redis), middleware de contexto por request, y routers.

## Infraestructura de soporte

- **Redis** — caché de respuestas LLM. Cliente gestionado vía `lifespan` en `main.py` (`app.state.redis`), inyectado a los endpoints con `Depends(get_redis)`. URL configurable con `REDIS_URL` (default: `redis://redis:6379`). TTL de caché: 24 h.
- **structlog** — logging estructurado. Configurado en `logging_config.py` con `merge_contextvars` para que cada línea de log lleve `request_id` y `path` del request en curso (bindeados por el middleware `bind_request_context`).

## Flujo de una request (`POST /api/v1/estimate`)

```
Router → valida EstimationRequest
       → inject redis (Depends)
       → llm_service.generate_estimation(transcription, redis)
           → construye mensajes (system prompt + ejemplos CAG + transcripción)
           → cache.cached_complete(messages, model, max_tokens, redis)
               → hit:  devuelve resultado cacheado
               → miss: llm_wrapper.complete → guarda en Redis → devuelve
           → mapea a dict plano {estimation, model, provider, usage, cache_hit}
       → EstimationResponse(**result)  ← validación Pydantic en el borde
```

El endpoint `/estimate/stream` **no usa caché** (ver Pendientes).

## Convenciones del proyecto

- **El servicio devuelve dicts planos, no instancia los schemas Pydantic.** Mantiene el núcleo de negocio agnóstico del borde HTTP. La validación contra los schemas ocurre en el router.
- **El formateo de ejemplos para el prompt vive en `llm_service.py`, no en `context/`.** Razón: cuando se migre a RAG, los datos vendrán de la BD vectorial pero el formato lo seguirá construyendo el servicio.
- **Cliente LLM asíncrono** (`litellm.acompletion` + `await`). No usar el cliente síncrono dentro de funciones `async`.
- El archivo de schemas es `app/schemas/estimations.py` (plural). El import correcto es `from app.schemas.estimations import ...`.
- **Imports ordenados:** stdlib → terceros → locales (una línea en blanco entre grupos).
- API versionada con prefijo `/api/v1`.

## Git

- Modelo de ramas: `main` = estado terminado que evoluciona; `sesion-NN` = rama de trabajo por sesión, se fusiona a `main` al cerrar.
- Commits pequeños y descriptivos, no un único commit gigante por sesión.
- Antes de commitear, verificar que `.env` NO entra (`git check-ignore .env` debe devolver `.env`).

## Pendientes

- **Caché del endpoint de streaming (`/estimate/stream`):** actualmente no usa Redis. Se implementará en la próxima sesión, que cubre extracción de datos estructurados desde el LLM — eso simplifica el cacheo del stream al tener respuestas deterministas.

## Restricciones

- No reproducir credenciales ni valores del `.env` en código, logs ni respuestas.
- No introducir RAG, embeddings, base de datos vectorial ni persistencia todavía: el proyecto está en fase CAG. Esas piezas llegan en módulos posteriores.
- No añadir dependencias sin reflejarlas vía `uv add` (deben quedar en `pyproject.toml` y `uv.lock`).
