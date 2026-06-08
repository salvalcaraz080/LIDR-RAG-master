from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Default points to the backend's Docker Compose service name, since the
    # primary scenario is running inside Docker. Override via env var when
    # running Streamlit locally (e.g. BACKEND_URL=http://localhost:8000).
    BACKEND_URL: str = "http://estimator:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()