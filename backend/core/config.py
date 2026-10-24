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
    # Per-role overrides, so roles can use models with separate free-tier quotas.
    extraction_model: str = ""  # defaults to llm_model
    extraction_temperature: float = 0.0
    extraction_thinking: str = "minimal"  # minimal|low|medium|high, a token budget, or "" for the model default
    answer_model: str = ""  # defaults to llm_model
    answer_temperature: float = 0.3
    answer_thinking: str = "minimal"
    google_api_key: SecretStr = SecretStr("")

    # Guidelines RAG: a small multilingual local embedding model (users write Hinglish).
    embedding_model: str = "intfloat/multilingual-e5-small"  # ~118M params, ~470 MB download, 384-dim
    vector_store_dir: str = "data/vector_store"

    # HTTP API
    cors_origins: str = "http://localhost:3000"  # comma-separated
    database_url: str = "sqlite+aiosqlite:///data/weatherwise.db"  # conversations and messages
    checkpoint_db: str = "data/checkpoints.sqlite"  # LangGraph conversation state (follow-ups)
    rate_limit_per_minute: int = 10  # chat messages per client ID
    daily_message_cap: int = 40  # chat messages per client ID per day (Asia/Kolkata), protects the LLM quota
    dashboard_rate_per_minute: int = 60  # dashboard requests per client ID
    max_message_chars: int = 500

    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "weatherwise"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
