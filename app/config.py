from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    OPENAI_API_KEY: str
    ANTHROPIC_API_KEY: str
    LLM_MODEL: str = "openai/gpt-4o-mini"
    EMBEDDING_MODEL: str = "openai/text-embedding-3-small"  # 1536 dims
    APP_ENV: str = "development"
    LOG_LEVEL: str = "DEBUG"
    REDIS_URL: str = "redis://redis:6379"
    GUARDRAILS_ENFORCE: bool | None = None  # None = derive from APP_ENV
    SEMANTIC_CACHE_DISTANCE_THRESHOLD: float = 0.15  # ~0.85 similarity, laxo a propósito
    SEMANTIC_CACHE_ENFORCE: bool | None = None  # None = derive from APP_ENV (log-only en dev)

    @property
    def guardrails_enforce(self) -> bool:
        if self.GUARDRAILS_ENFORCE is not None:
            return self.GUARDRAILS_ENFORCE
        return self.APP_ENV == "production"

    @property
    def semantic_cache_enforce(self) -> bool:
        if self.SEMANTIC_CACHE_ENFORCE is not None:
            return self.SEMANTIC_CACHE_ENFORCE
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()