# Proyecto Master IA Engineering: Estimator CAG - Servicio de Estimacion de Software con IA

Servicio de estimacion de proyectos de software impulsado por IA, utilizando una arquitectura **Cache Augmented Generation (CAG)**.

## Estructura del proyecto

```
estimator/
├── app/
│   ├── main.py            # Aplicacion FastAPI, health check, CORS
│   ├── config.py           # Configuracion con Pydantic Settings
│   ├── routers/
│   │   └── estimations.py  # Endpoint POST /api/v1/estimate
│   ├── services/
│   │   └── llm_service.py  # Logica de negocio, llamadas al LLM
│   ├── prompts/
│   │   ├── loader.py       # Render de templates Jinja2 versionados
│   │   └── estimation/v1/  # system.j2, user.j2, examples.j2
│   ├── schemas/
│   │   └── estimations.py  # Modelos Pydantic (request/response)
│   └── context/
│       └── examples.py     # Ejemplos de referencia (obsoleto en CAG, vuelve en RAG)
├── tests/
│   └── test_health.py      # Tests basicos
├── Dockerfile              # Build multi-stage con uv
├── docker-compose.yml      # Configuracion para desarrollo local
└── pyproject.toml          # Dependencias y configuracion
```

## Documentacion interactiva

Con el servicio corriendo, accede a la documentacion Swagger UI en:

- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

---
