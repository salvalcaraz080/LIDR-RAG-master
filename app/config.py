from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Credenciales y modelos (las keys se inyectan en runtime desde .env, nunca hardcoded).
    OPENAI_API_KEY: str
    ANTHROPIC_API_KEY: str
    LLM_MODEL: str = "openai/gpt-4o-mini"
    EMBEDDING_MODEL: str = "openai/text-embedding-3-small"  # 1536 dims
    # Entorno e infraestructura.
    APP_ENV: str = "development"
    LOG_LEVEL: str = "DEBUG"
    REDIS_URL: str = "redis://redis:6379"
    # Flags de enforcement: None = derivar de APP_ENV (True en production, log-only en dev).
    GUARDRAILS_ENFORCE: bool | None = None
    SEMANTIC_CACHE_DISTANCE_THRESHOLD: float = 0.15  # ~0.85 similarity, laxo a propósito
    SEMANTIC_CACHE_ENFORCE: bool | None = None
    # Ventana deslizante del historial conversacional: pares user+assistant conservados.
    MAX_TURNS: int = 6

    @property
    def guardrails_enforce(self) -> bool:
        # Flag explícito si se fijó; si no, enforce solo en production.
        if self.GUARDRAILS_ENFORCE is not None:
            return self.GUARDRAILS_ENFORCE
        return self.APP_ENV == "production"

    @property
    def semantic_cache_enforce(self) -> bool:
        # Misma lógica que guardrails: en dev queda en modo log-only (no sirve hits).
        if self.SEMANTIC_CACHE_ENFORCE is not None:
            return self.SEMANTIC_CACHE_ENFORCE
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()