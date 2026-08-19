from decimal import Decimal

import pytest
from pydantic import ValidationError

from podcast_intelligence.settings import Settings


def test_settings_have_safe_explicit_defaults() -> None:
    settings = Settings.model_validate({"openai_api_key": "test-key"})

    assert settings.openai_api_key.get_secret_value() == "test-key"
    assert settings.openai_model == "gpt-5.6-sol"
    assert settings.openai_reasoning_effort == "medium"
    assert settings.openai_store_responses is False
    assert settings.openai_transcription_model == "gpt-transcribe"
    assert settings.openai_transcription_cost_per_minute_usd == Decimal("0.0045")
    assert settings.openai_transcription_timeout_seconds == 600.0


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


def test_secret_is_redacted_from_settings_representation() -> None:
    settings = Settings.model_validate({"openai_api_key": "do-not-display"})

    assert "do-not-display" not in repr(settings)
