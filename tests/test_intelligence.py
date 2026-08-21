import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest

from podcast_intelligence.audio_transcription import (
    EpisodeTranscript,
    ProviderTranscript,
    ProviderTranscriptPart,
)
from podcast_intelligence.episode_resolution import (
    ResolvedSpotifyEpisode,
    resolve_transcript_sources,
)
from podcast_intelligence.intelligence import analyze_episode, answer_episode_question
from podcast_intelligence.intelligence_models import (
    AnalysisEvidence,
    EpisodeAnalysis,
    EvidenceBackedItem,
)
from podcast_intelligence.models import PodcastEpisode
from podcast_intelligence.persistence import TranscriptStore
from podcast_intelligence.responses_client import (
    EpisodeAnalysisResponse,
    PodcastResponsesClient,
    QuestionResponse,
    ResponsesClientError,
    ResponsesOutputValidationError,
    ResponseUsageSummary,
)
from podcast_intelligence.settings import Settings


def _persist_transcript(store: TranscriptStore) -> int:
    text = (
        "Versioned evaluation cases make prompt changes measurable. "
        "Exact evidence prevents unsupported claims from being persisted."
    )
    episode = PodcastEpisode(
        episode_id="analysis-guid",
        title="Analysis episode",
        published_at=datetime(2026, 8, 20, tzinfo=UTC),
        audio_url="https://cdn.example.test/analysis.mp3",
        audio_media_type="audio/mpeg",
        audio_size_bytes=100,
        duration_seconds=60,
    )
    resolved = ResolvedSpotifyEpisode(
        spotify_episode_id="0VPwvReM2olZDWl3YOHfqh",
        spotify_url="https://open.spotify.com/episode/0VPwvReM2olZDWl3YOHfqh",
        show_title="Synthetic show",
        feed_url="https://publisher.example.test/feed.xml",
        catalog_url="https://catalog.example.test/episode",
        catalog_episode_guid="analysis-guid",
        episode=episode,
        transcript=resolve_transcript_sources(episode),
    )
    episode_record = store.upsert_episode(resolved)
    run_id = store.create_run(
        episode_record,
        provider="openai",
        model="gpt-transcribe",
        chunker_version="test-chunker",
        prompt_version="test-prompt",
        estimated_cost_microusd=4_500,
    )
    store.mark_running(run_id)
    part = ProviderTranscriptPart(
        ordinal=0,
        text=text,
        request_id="req_analysis",
        model="gpt-transcribe",
        language="en",
    )
    provider = ProviderTranscript(
        text=text,
        provider="openai",
        model="gpt-transcribe",
        request_ids=("req_analysis",),
        language="en",
        chunk_count=1,
        parts=(part,),
    )
    store.persist_success(
        run_id,
        episode_record,
        EpisodeTranscript(
            episode_id="analysis-guid",
            source_url="https://cdn.example.test/analysis.mp3",
            source_media_type="audio/mpeg",
            duration_seconds=60,
            audio_bytes=100,
            audio_sha256=sha256(b"audio").hexdigest(),
            etag=None,
            last_modified=None,
            estimated_cost_usd=Decimal("0.0045"),
            transcript=provider,
        ),
        chunker_version="test-chunker",
        prompt_version="test-prompt",
    )
    return run_id


def _client() -> Mock:
    client = Mock(spec=PodcastResponsesClient)

    def create_analysis(_transcript: object, segments: tuple[object, ...]) -> object:
        segment = segments[0]
        segment_id = segment.segment_id  # type: ignore[attr-defined]
        quote = "Versioned evaluation cases"
        evidence = [AnalysisEvidence(segment_id=segment_id, quote=quote)]
        item = EvidenceBackedItem(
            text="Use versioned evaluation cases.",
            evidence=evidence,
        )
        return EpisodeAnalysisResponse(
            response_id=f"resp_{client.create_episode_analysis.call_count}",
            model="gpt-5.6-sol",
            analysis=EpisodeAnalysis(
                summary=item,
                topics=[item],
                people=[],
                claims=[item],
                actionable_insights=[item],
                limitations=["No speaker timing is available."],
            ),
            usage=ResponseUsageSummary(input_tokens=100, output_tokens=50, total_tokens=150),
        )

    client.create_episode_analysis.side_effect = create_analysis
    return client


def _settings(path: Path, *, model: str = "gpt-5.6-sol") -> Settings:
    return Settings.model_validate(
        {
            "openai_api_key": "test-key",
            "openai_model": model,
            "database_path": path,
            "intelligence_segment_chars": 200,
        }
    )


def test_analysis_pipeline_reuses_cache_without_second_provider_call(tmp_path: Path) -> None:
    path = tmp_path / "analysis-cache.db"
    client = _client()
    with TranscriptStore(path) as store:
        transcript_run_id = _persist_transcript(store)
        first = analyze_episode(
            transcript_run_id,
            settings=_settings(path),
            store=store,
            responses_client=client,
        )
        second = analyze_episode(
            transcript_run_id,
            settings=_settings(path),
            store=store,
            responses_client=client,
        )

    assert first.cache_status == "miss"
    assert second.cache_status == "analysis"
    assert second.analysis.run_id == first.analysis.run_id
    assert client.create_episode_analysis.call_count == 1


def test_cached_analysis_can_be_read_without_provider_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "offline-analysis-cache.db"
    client = _client()
    with TranscriptStore(path) as store:
        transcript_run_id = _persist_transcript(store)
        first = analyze_episode(
            transcript_run_id,
            settings=_settings(path),
            store=store,
            responses_client=client,
        )
        cached = analyze_episode(
            transcript_run_id,
            settings=Settings(_env_file=None, database_path=path),
            store=store,
        )

    assert cached.cache_status == "analysis"
    assert cached.analysis.run_id == first.analysis.run_id


def test_missing_credentials_do_not_create_analysis_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    path = tmp_path / "missing-analysis-credentials.db"
    with TranscriptStore(path) as store:
        transcript_run_id = _persist_transcript(store)
        with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
            analyze_episode(
                transcript_run_id,
                settings=Settings(_env_file=None, database_path=path),
                store=store,
            )

        with sqlite3.connect(path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()

    assert count == (0,)


def test_question_service_uses_only_selected_persisted_transcript(tmp_path: Path) -> None:
    path = tmp_path / "question-service.db"
    client = Mock(spec=PodcastResponsesClient)
    expected = Mock(spec=QuestionResponse)
    client.answer_question.return_value = expected
    with TranscriptStore(path) as store:
        transcript_run_id = _persist_transcript(store)
        result = answer_episode_question(
            transcript_run_id,
            "What makes evaluation measurable?",
            settings=_settings(path),
            store=store,
            responses_client=client,
        )

    assert result is expected
    client.answer_question.assert_called_once()
    question, tools = client.answer_question.call_args.args
    assert question == "What makes evaluation measurable?"
    assert tools.transcript.run_id == transcript_run_id


def test_refresh_and_model_change_preserve_prior_successful_history(tmp_path: Path) -> None:
    path = tmp_path / "analysis-history.db"
    client = _client()
    with TranscriptStore(path) as store:
        transcript_run_id = _persist_transcript(store)
        first = analyze_episode(
            transcript_run_id,
            settings=_settings(path),
            store=store,
            responses_client=client,
        )
        refreshed = analyze_episode(
            transcript_run_id,
            settings=_settings(path),
            store=store,
            responses_client=client,
            refresh=True,
        )
        changed_model = analyze_episode(
            transcript_run_id,
            settings=_settings(path, model="gpt-5.6-sol-versioned"),
            store=store,
            responses_client=client,
        )
        history = store.list_analyses()

    assert first.analysis.run_id != refreshed.analysis.run_id
    assert refreshed.analysis.run_id != changed_model.analysis.run_id
    assert len(history) == 3
    assert client.create_episode_analysis.call_count == 3


def test_provider_or_evidence_failure_records_safe_failed_run(tmp_path: Path) -> None:
    path = tmp_path / "analysis-failure.db"
    client = Mock(spec=PodcastResponsesClient)
    client.create_episode_analysis.side_effect = ResponsesOutputValidationError(
        "episode analysis failed evidence validation",
        response_id="resp_rejected",
        usage=ResponseUsageSummary(input_tokens=100, output_tokens=25, total_tokens=125),
    )
    with TranscriptStore(path) as store:
        transcript_run_id = _persist_transcript(store)
        with pytest.raises(ResponsesClientError, match="evidence validation"):
            analyze_episode(
                transcript_run_id,
                settings=_settings(path),
                store=store,
                responses_client=client,
            )
        assert store.list_analyses() == ()

    import sqlite3

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """
            SELECT status, error_code, error_message, response_id,
                input_tokens, output_tokens, total_tokens
            FROM analysis_runs
            """
        ).fetchone()
    assert row == (
        "failed",
        "ResponsesOutputValidationError",
        "episode analysis failed evidence validation",
        "resp_rejected",
        100,
        25,
        125,
    )
