from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_name: str = "andromot"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: SecretStr = SecretStr("postgresql+asyncpg://andromot:change-me@localhost:5432/andromot")
    device_credential_pepper: SecretStr

    groq_enabled: bool = False
    groq_api_key: SecretStr | None = None
    groq_model: str | None = None
    groq_timeout_seconds: float = 10.0

    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
