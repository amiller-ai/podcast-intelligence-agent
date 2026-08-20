"""Typed application settings loaded from the environment or a local .env file."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
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
    openai_transcription_model: str = "gpt-transcribe"
    openai_transcription_cost_per_minute_usd: Decimal = Decimal("0.0045")
    openai_transcription_timeout_seconds: float = 600.0
    database_path: Path = Path("data/podcast_intelligence.db")

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

    @field_validator("openai_transcription_model")
    @classmethod
    def transcription_model_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("OPENAI_TRANSCRIPTION_MODEL must not be empty")
        return value.strip()

    @field_validator("openai_transcription_cost_per_minute_usd")
    @classmethod
    def transcription_price_must_be_positive(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= Decimal(0):
            raise ValueError("OPENAI_TRANSCRIPTION_COST_PER_MINUTE_USD must be finite and positive")
        return value

    @field_validator("openai_transcription_timeout_seconds")
    @classmethod
    def transcription_timeout_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS must be positive")
        return value

    @field_validator("database_path")
    @classmethod
    def database_path_must_name_a_file(cls, value: Path) -> Path:
        if not value.name or value.name in {".", ".."}:
            raise ValueError("DATABASE_PATH must name a database file")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance for the current process."""

    return Settings()
