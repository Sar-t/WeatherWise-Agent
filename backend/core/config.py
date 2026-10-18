"""Application settings loaded from environment variables and `.env`."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openweather_api_key: SecretStr = SecretStr("")
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
