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
- `app/services/` — lógica de negocio: construcción del prompt, llamada al LLM, postprocesamiento.
- `app/schemas/` — contratos Pydantic (request/response). Son el borde HTTP, no el núcleo.
- `app/context/` — datos de referencia estáticos para CAG. Punto de sustitución futuro para RAG.
- `app/config.py` — `BaseSettings` de Pydantic, cacheado con `@lru_cache`.
- `app/main.py` — punto de entrada; solo registra routers y middleware.

## Convenciones del proyecto

- **El servicio devuelve dicts planos, no instancia los schemas Pydantic.** Mantiene el núcleo de negocio agnóstico del borde HTTP. La validación contra los schemas ocurre en el router.
- **El formateo de ejemplos para el prompt vive en el servicio (`llm_service.py`), no en `context/`.** Razón: cuando se migre a RAG, los datos vendrán de la BD vectorial pero el formato lo seguirá construyendo el servicio.
- Cliente LLM **asíncrono** (`AsyncOpenAI` + `await`). No usar el cliente síncrono dentro de funciones `async`.
- El archivo de schemas es `app/schemas/estimations.py` (plural). El import correcto es `from app.schemas.estimations import ...`.
- API versionada con prefijo `/api/v1`.

## Git

- Modelo de ramas: `main` = estado terminado que evoluciona; `sesion-NN` = rama de trabajo por sesión, se fusiona a `main` al cerrar.
- Commits pequeños y descriptivos, no un único commit gigante por sesión.
- Antes de commitear, verificar que `.env` NO entra (`git check-ignore .env` debe devolver `.env`).

## Restricciones

- No reproducir credenciales ni valores del `.env` en código, logs ni respuestas.
- No introducir RAG, embeddings, base de datos vectorial ni persistencia todavía: el proyecto está en fase CAG. Esas piezas llegan en módulos posteriores.
- No añadir dependencias sin reflejarlas vía `uv add` (deben quedar en `pyproject.toml` y `uv.lock`).
