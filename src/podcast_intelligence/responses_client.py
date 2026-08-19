"""Application-owned boundary around the OpenAI Responses API."""

from dataclasses import dataclass

from openai import OpenAI
from openai.types.shared_params import Reasoning

from podcast_intelligence.settings import Settings


@dataclass(frozen=True, slots=True)
class TextResponse:
    """The minimal provider-independent result needed by the first milestone."""

    response_id: str
    model: str
    text: str


class PodcastResponsesClient:
    """Create single-turn text responses through the Responses API."""

    def __init__(self, settings: Settings, *, client: OpenAI | None = None) -> None:
        self._settings = settings
        self._client = client or OpenAI(api_key=settings.openai_api_key.get_secret_value())

    def create_text_response(self, prompt: str) -> TextResponse:
        """Create a stored-disabled text response for a non-empty prompt."""

        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("prompt must not be empty")

        reasoning: Reasoning = {"effort": self._settings.openai_reasoning_effort}
        response = self._client.responses.create(
            model=self._settings.openai_model,
            input=normalized_prompt,
            reasoning=reasoning,
            store=self._settings.openai_store_responses,
        )

        return TextResponse(
            response_id=response.id,
            model=response.model,
            text=response.output_text,
        )
