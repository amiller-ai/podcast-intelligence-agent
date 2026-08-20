"""Application service for persisted evidence-grounded episode intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from podcast_intelligence.intelligence_models import (
    ANALYSIS_PROMPT_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    SEGMENTER_VERSION,
)
from podcast_intelligence.persistence import (
    PersistenceError,
    StoredEpisodeAnalysis,
    TranscriptStore,
)
from podcast_intelligence.responses_client import (
    PodcastResponsesClient,
    QuestionResponse,
    ResponsesClientError,
)
from podcast_intelligence.retrieval import TranscriptTools
from podcast_intelligence.settings import Settings

ANALYSIS_TYPE = "episode_intelligence"
AnalysisCacheStatus = Literal["miss", "analysis"]


@dataclass(frozen=True, slots=True)
class EpisodeIntelligenceResult:
    """One persisted analysis and whether it came from the local cache."""

    analysis: StoredEpisodeAnalysis
    cache_status: AnalysisCacheStatus


def analyze_episode(
    transcript_run_id: int,
    *,
    settings: Settings,
    store: TranscriptStore,
    responses_client: PodcastResponsesClient | None = None,
    refresh: bool = False,
) -> EpisodeIntelligenceResult:
    """Analyze one persisted transcript without invoking ingestion or transcription."""

    transcript = store.get_transcript(transcript_run_id)
    tools = TranscriptTools(
        store,
        transcript,
        settings,
        segmenter_version=SEGMENTER_VERSION,
    )
    if not refresh:
        cached = store.find_analysis_cache(
            transcript,
            analysis_type=ANALYSIS_TYPE,
            model=settings.openai_model,
            prompt_version=ANALYSIS_PROMPT_VERSION,
            schema_version=ANALYSIS_SCHEMA_VERSION,
            segmenter_version=SEGMENTER_VERSION,
        )
        if cached is not None:
            return EpisodeIntelligenceResult(analysis=cached, cache_status="analysis")

    client = responses_client or PodcastResponsesClient(settings)
    run_id = store.create_analysis_run(
        transcript,
        analysis_type=ANALYSIS_TYPE,
        model=settings.openai_model,
        prompt_version=ANALYSIS_PROMPT_VERSION,
        schema_version=ANALYSIS_SCHEMA_VERSION,
        segmenter_version=SEGMENTER_VERSION,
    )
    store.mark_analysis_running(run_id)
    try:
        response = client.create_episode_analysis(transcript, tools.segments)
        stored = store.persist_analysis_success(
            run_id,
            response_id=response.response_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.total_tokens,
            analysis=response.analysis,
        )
    except ResponsesClientError as error:
        store.mark_analysis_failed(
            run_id,
            error_code=type(error).__name__,
            safe_message=str(error),
        )
        raise
    except (PersistenceError, ValueError) as error:
        if store.analysis_run_status(run_id) == "running":
            store.mark_analysis_failed(
                run_id,
                error_code=type(error).__name__,
                safe_message="episode analysis validation or persistence failed",
            )
        raise
    return EpisodeIntelligenceResult(analysis=stored, cache_status="miss")


def answer_episode_question(
    transcript_run_id: int,
    question: str,
    *,
    settings: Settings,
    store: TranscriptStore,
    responses_client: PodcastResponsesClient | None = None,
) -> QuestionResponse:
    """Answer from one persisted transcript without invoking ingestion or transcription."""

    transcript = store.get_transcript(transcript_run_id)
    tools = TranscriptTools(
        store,
        transcript,
        settings,
        segmenter_version=SEGMENTER_VERSION,
    )
    client = responses_client or PodcastResponsesClient(settings)
    return client.answer_question(question, tools)
