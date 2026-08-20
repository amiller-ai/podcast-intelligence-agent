"""Deterministic local server used only by the browser acceptance test."""

from __future__ import annotations

from pathlib import Path
from typing import Self, cast

import uvicorn

from podcast_intelligence.intelligence import EpisodeIntelligenceResult
from podcast_intelligence.intelligence_models import (
    AnalysisEvidence,
    EpisodeAnalysis,
    EvidenceBackedItem,
    QuestionAnswer,
)
from podcast_intelligence.persistence import (
    EpisodeStatus,
    StoredEpisodeAnalysis,
    StoredTranscriptMetadata,
)
from podcast_intelligence.responses_client import (
    QuestionResponse,
    ResponsesClientError,
    ResponseUsageSummary,
    ToolCallTrace,
)
from podcast_intelligence.settings import Settings
from podcast_intelligence.web import StoreFactory, create_app

_SEGMENT_ID = "a" * 64


def _stored_analysis() -> StoredEpisodeAnalysis:
    evidence = [AnalysisEvidence(segment_id=_SEGMENT_ID, quote="Exact synthetic evidence quote")]
    item = EvidenceBackedItem(text="Use reproducible evaluation cases.", evidence=evidence)
    return StoredEpisodeAnalysis(
        run_id=17,
        analysis_id=19,
        transcript_id=11,
        cache_identity="e" * 64,
        analysis_type="episode_intelligence",
        model="gpt-5.6-sol",
        prompt_version="episode-intelligence-v2",
        schema_version="1",
        segmenter_version="bounded-text-v1",
        response_id="resp_fixture_analysis",
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        analysis=EpisodeAnalysis(
            summary=item,
            topics=[item],
            people=[],
            claims=[item],
            actionable_insights=[item],
            limitations=["Synthetic fixture has no speaker timing."],
        ),
        created_at="2026-08-20T00:01:00+00:00",
    )


class _FixtureStore:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def list_episode_statuses(self) -> tuple[EpisodeStatus, ...]:
        return (
            EpisodeStatus(
                episode_id=13,
                spotify_episode_id="fixture-episode",
                title="Synthetic evaluation episode",
                latest_transcription_status="succeeded",
                transcript_run_id=7,
                analysis_available=True,
                latest_analysis_status="succeeded",
            ),
        )

    def get_transcript_metadata(self, _run_id: int) -> StoredTranscriptMetadata:
        return StoredTranscriptMetadata(
            run_id=7,
            transcript_id=11,
            episode_id=13,
            feed_url="https://example.test/feed.xml",
            rss_guid="fixture-guid",
            spotify_episode_id="fixture-episode",
            episode_title="Synthetic evaluation episode",
            transcription_model="gpt-transcribe",
            created_at="2026-08-20T00:00:00+00:00",
        )

    def find_analysis_cache_for_run(
        self, *_args: object, **_kwargs: object
    ) -> StoredEpisodeAnalysis:
        return _stored_analysis()


def _analyze(*_args: object, **_kwargs: object) -> EpisodeIntelligenceResult:
    return EpisodeIntelligenceResult(analysis=_stored_analysis(), cache_status="miss")


def _question(_run_id: int, question: str, **_kwargs: object) -> QuestionResponse:
    if question == "Trigger safe error":
        raise ResponsesClientError("SENSITIVE FIXTURE PROVIDER DETAIL")
    return QuestionResponse(
        response_id="resp_fixture_answer",
        model="gpt-5.6-sol",
        answer=QuestionAnswer(
            answer="The episode recommends reproducible evaluation cases.",
            evidence=[
                AnalysisEvidence(
                    segment_id=_SEGMENT_ID,
                    quote="Exact synthetic evidence quote",
                )
            ],
            insufficient_evidence=False,
        ),
        response_ids=("resp_fixture_search", "resp_fixture_answer"),
        output_item_types=(("function_call",), ("message",)),
        tool_calls=(
            ToolCallTrace(
                response_id="resp_fixture_search",
                call_id="call_fixture_search",
                tool_name="search_transcript",
                arguments_json='{"query":"SENSITIVE FIXTURE ARGUMENT"}',
                result_segment_ids=(_SEGMENT_ID,),
            ),
        ),
        usage=ResponseUsageSummary(input_tokens=20, output_tokens=10, total_tokens=30),
    )


_store = _FixtureStore()
app = create_app(
    settings=Settings(_env_file=None, database_path=Path("fixture.db")),
    store_factory=cast(StoreFactory, lambda: _store),
    analyze_service=_analyze,
    question_service=_question,
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
