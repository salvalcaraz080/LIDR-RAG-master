# Proyecto Master IA Engineering: Estimator CAG - Servicio de Estimacion de Software con IA

Servicio de estimacion de proyectos de software impulsado por IA, utilizando una arquitectura **Cache Augmented Generation (CAG)**.

## Estructura del proyecto

```
estimator/
├── app/
│   ├── main.py             # App FastAPI: lifespan (SemanticCache), middleware, routers
│   ├── config.py           # Configuracion con Pydantic Settings
│   ├── routers/
│   │   └── estimations.py  # POST /api/v1/estimate y /api/v1/estimate/stream (SSE)
│   ├── services/
│   │   ├── llm_service.py  # Orquestacion del dominio (estimacion)
│   │   ├── guardrails.py   # Validacion de input (moderation + inyeccion)
│   │   ├── embeddings.py   # embed_text via litellm (semilla del RAG)
│   │   ├── cache.py        # Cache semantico (redisvl / Redis Stack)
│   │   └── llm_wrapper.py  # Adaptador LLM (Instructor + fallback)
│   ├── prompts/
│   │   ├── loader.py       # Render de templates Jinja2 versionados
│   │   └── estimation/v1/  # system.j2, user.j2, examples.j2
│   └── schemas/
│       └── estimations.py  # Modelos Pydantic (request/response)
├── frontend/
│   └── streamlit_app.py    # UI: formulario + consumo del endpoint stream (SSE)
├── tests/                  # pytest (sin coste de API; LLM/cache mockeados)
├── Dockerfile              # Build multi-stage con uv
├── docker-compose.yml      # backend + frontend + Redis Stack
└── pyproject.toml          # Dependencias y configuracion
```

## Requisitos previos

- **Docker** (flujo recomendado) o **uv** + **Redis Stack** para correr en local.
- Un fichero **`.env`** en la raiz con las claves (ignorado por git y docker):

```
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

## Como levantar

**Con Docker (recomendado).** Arranca backend, frontend y Redis Stack:

```bash
docker compose up --build      # build + arranque
docker compose down            # parar
```

- API: http://localhost:8000  ·  UI Streamlit: http://localhost:8501

**En local (sin Docker).** Necesita Redis Stack escuchando (p. ej. `docker run -p 6379:6379 redis/redis-stack`):

```powershell
uv run python -m uvicorn app.main:app --reload     # backend
uv run streamlit run frontend/streamlit_app.py     # frontend (otra terminal)
```

## Tests

```powershell
uv run pytest tests/ -v        # tests unitarios (sin coste de API)
uv run ruff check .            # lint
```

## Documentacion interactiva

Con el servicio corriendo, accede a la documentacion Swagger UI en:

- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

---
