from typing import cast
from unittest.mock import Mock

import pytest
from openai import OpenAI
from openai.types.responses import Response

from podcast_intelligence.responses_client import PodcastResponsesClient, TextResponse
from podcast_intelligence.settings import Settings


def test_create_text_response_uses_configured_request_contract() -> None:
    sdk_response = Mock(spec=Response)
    sdk_response.id = "resp_test"
    sdk_response.model = "gpt-5.6-sol"
    sdk_response.output_text = "podcast-intelligence-ok"
    sdk_client = Mock(spec=OpenAI)
    sdk_client.responses.create.return_value = sdk_response
    settings = Settings.model_validate({"openai_api_key": "test-key"})
    client = PodcastResponsesClient(settings, client=cast(OpenAI, sdk_client))

    result = client.create_text_response("  test the client  ")

    assert result == TextResponse(
        response_id="resp_test",
        model="gpt-5.6-sol",
        text="podcast-intelligence-ok",
    )
    sdk_client.responses.create.assert_called_once_with(
        model="gpt-5.6-sol",
        input="test the client",
        reasoning={"effort": "medium"},
        store=False,
    )


def test_create_text_response_rejects_empty_prompt_without_api_call() -> None:
    sdk_client = Mock(spec=OpenAI)
    settings = Settings.model_validate({"openai_api_key": "test-key"})
    client = PodcastResponsesClient(settings, client=cast(OpenAI, sdk_client))

    with pytest.raises(ValueError, match="prompt must not be empty"):
        client.create_text_response("  ")

    sdk_client.responses.create.assert_not_called()
