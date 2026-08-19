import pytest
from pydantic import ValidationError

from podcast_intelligence.settings import Settings


def test_settings_have_safe_explicit_defaults() -> None:
    settings = Settings.model_validate({"openai_api_key": "test-key"})

    assert settings.openai_api_key.get_secret_value() == "test-key"
    assert settings.openai_model == "gpt-5.6-sol"
    assert settings.openai_reasoning_effort == "medium"
    assert settings.openai_store_responses is False


@pytest.mark.parametrize("field", ["openai_api_key", "openai_model"])
def test_settings_reject_empty_required_values(field: str) -> None:
    values = {"openai_api_key": "test-key", "openai_model": "gpt-5.6-sol", field: " "}

    with pytest.raises(ValidationError):
        Settings.model_validate(values)


def test_secret_is_redacted_from_settings_representation() -> None:
    settings = Settings.model_validate({"openai_api_key": "do-not-display"})

    assert "do-not-display" not in repr(settings)
