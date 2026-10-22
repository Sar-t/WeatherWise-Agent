"""Application settings loaded from environment variables and `.env`."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openweather_api_key: SecretStr = SecretStr("")
    geoapify_api_key: SecretStr = SecretStr("")

    llm_provider: str = "google_genai"  # any LangChain init_chat_model provider
    llm_model: str = ""  # e.g. a Gemini Flash model; empty -> list what the key can use
    llm_temperature: float = 0.0
    google_api_key: SecretStr = SecretStr("")

    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "weatherwise"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
