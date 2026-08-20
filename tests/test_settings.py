from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from podcast_intelligence.settings import Settings


def test_settings_have_safe_explicit_defaults() -> None:
    settings = Settings.model_validate({"openai_api_key": "test-key"})

    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "test-key"
    assert settings.openai_model == "gpt-5.6-sol"
    assert settings.openai_reasoning_effort == "medium"
    assert settings.openai_store_responses is False
    assert settings.openai_transcription_model == "gpt-transcribe"
    assert settings.openai_transcription_cost_per_minute_usd == Decimal("0.0045")
    assert settings.openai_transcription_timeout_seconds == 600.0
    assert settings.database_path == Path("data/podcast_intelligence.db")
    assert settings.intelligence_segment_chars == 1_600
    assert settings.intelligence_max_query_chars == 400
    assert settings.intelligence_max_search_results == 5
    assert settings.intelligence_max_read_segments == 8
    assert settings.intelligence_max_tool_output_chars == 16_000
    assert settings.intelligence_max_tool_calls == 6
    assert settings.intelligence_max_output_tokens == 8_000
    assert settings.intelligence_max_analysis_chars == 250_000


@pytest.mark.parametrize(
    "field",
    ["openai_api_key", "openai_model", "openai_transcription_model"],
)
def test_settings_reject_empty_required_values(field: str) -> None:
    values = {"openai_api_key": "test-key", "openai_model": "gpt-5.6-sol", field: " "}

    with pytest.raises(ValidationError):
        Settings.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("openai_transcription_cost_per_minute_usd", Decimal(0)),
        ("openai_transcription_cost_per_minute_usd", Decimal("NaN")),
        ("openai_transcription_timeout_seconds", 0),
    ],
)
def test_settings_reject_invalid_transcription_limits(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"openai_api_key": "test-key", field: value})


@pytest.mark.parametrize(
    "field",
    [
        "intelligence_segment_chars",
        "intelligence_max_query_chars",
        "intelligence_max_search_results",
        "intelligence_max_read_segments",
        "intelligence_max_tool_output_chars",
        "intelligence_max_tool_calls",
        "intelligence_max_output_tokens",
        "intelligence_max_analysis_chars",
    ],
)
def test_settings_reject_non_positive_intelligence_limits(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"openai_api_key": "test-key", field: 0})


def test_settings_reject_too_small_segment_size() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"openai_api_key": "test-key", "intelligence_segment_chars": 63})


def test_secret_is_redacted_from_settings_representation() -> None:
    settings = Settings.model_validate({"openai_api_key": "do-not-display"})

    assert "do-not-display" not in repr(settings)


def test_settings_allow_offline_use_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    assert settings.openai_api_key is None
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        settings.require_openai_api_key()


def test_settings_accept_database_path_override(tmp_path: Path) -> None:
    path = tmp_path / "alternate.db"

    settings = Settings.model_validate({"openai_api_key": "test-key", "database_path": path})

    assert settings.database_path == path
