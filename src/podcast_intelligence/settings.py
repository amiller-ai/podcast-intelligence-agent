"""Typed application settings loaded from the environment or a local .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    """Runtime configuration for OpenAI-backed application services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: SecretStr
    openai_model: str = "gpt-5.6-sol"
    openai_reasoning_effort: ReasoningEffort = "medium"
    openai_store_responses: bool = False

    @field_validator("openai_api_key")
    @classmethod
    def api_key_must_not_be_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("OPENAI_API_KEY must not be empty")
        return value

    @field_validator("openai_model")
    @classmethod
    def model_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("OPENAI_MODEL must not be empty")
        return value.strip()


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance for the current process."""

    return Settings()
