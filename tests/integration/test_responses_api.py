import pytest

from podcast_intelligence.responses_client import PodcastResponsesClient
from podcast_intelligence.settings import get_settings


@pytest.mark.integration
def test_live_responses_api_smoke() -> None:
    result = PodcastResponsesClient(get_settings()).create_text_response(
        "Respond with exactly this text and nothing else: podcast-intelligence-ok"
    )

    assert result.response_id.startswith("resp_")
    assert result.model
    assert result.text.strip() == "podcast-intelligence-ok"
