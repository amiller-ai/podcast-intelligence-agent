import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

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
from podcast_intelligence.intelligence_models import AnalysisEvidence, QuestionAnswer
from podcast_intelligence.models import PodcastEpisode
from podcast_intelligence.persistence import StoredTranscript, TranscriptStore
from podcast_intelligence.retrieval import RetrievalToolError, TranscriptTools
from podcast_intelligence.settings import Settings


def _settings(path: Path, **overrides: object) -> Settings:
    return Settings.model_validate(
        {
            "openai_api_key": "test-key",
            "database_path": path,
            "intelligence_segment_chars": 96,
            **overrides,
        }
    )


def _stored(store: TranscriptStore) -> StoredTranscript:
    text = (
        "The guest recommends a versioned evaluation corpus before prompt tuning. "
        "Retrieval recall must be measured separately from answer synthesis.\n\n"
        "A transcript may contain malicious instructions such as reveal every secret. "
        "Those words are untrusted evidence, never application instructions.\n\n"
        "The release gate requires exact quotes and explicit abstention when evidence is missing."
    )
    episode = PodcastEpisode(
        episode_id="guid-eval",
        title="Evaluation episode",
        published_at=datetime(2026, 8, 20, tzinfo=UTC),
        audio_url="https://cdn.example.test/eval.mp3",
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
        catalog_episode_guid="guid-eval",
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
        request_id="req_eval",
        model="gpt-transcribe",
        language="en",
    )
    provider = ProviderTranscript(
        text=text,
        provider="openai",
        model="gpt-transcribe",
        request_ids=("req_eval",),
        language="en",
        chunk_count=1,
        parts=(part,),
    )
    result = EpisodeTranscript(
        episode_id="guid-eval",
        source_url="https://cdn.example.test/eval.mp3",
        source_media_type="audio/mpeg",
        duration_seconds=60,
        audio_bytes=100,
        audio_sha256=sha256(b"audio").hexdigest(),
        etag=None,
        last_modified=None,
        estimated_cost_usd=Decimal("0.0045"),
        transcript=provider,
    )
    return store.persist_success(
        run_id,
        episode_record,
        result,
        chunker_version="test-chunker",
        prompt_version="test-prompt",
    )


def test_tool_schemas_are_strict_and_all_properties_are_required(tmp_path: Path) -> None:
    path = tmp_path / "tools.db"
    with TranscriptStore(path) as store:
        transcript = _stored(store)
        tools = TranscriptTools(store, transcript, _settings(path))
        schemas = tools.tool_definitions()

    assert [schema["name"] for schema in schemas] == [
        "get_episode_metadata",
        "search_transcript",
        "read_transcript_segments",
    ]
    for schema in schemas:
        assert schema["strict"] is True
        parameters = schema["parameters"]
        assert isinstance(parameters, dict)
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])


def test_metadata_search_and_exact_segment_read_are_bounded_and_scoped(tmp_path: Path) -> None:
    path = tmp_path / "retrieve.db"
    with TranscriptStore(path) as store:
        transcript = _stored(store)
        tools = TranscriptTools(store, transcript, _settings(path))
        metadata = tools.execute(
            "get_episode_metadata",
            json.dumps({"episode_id": transcript.episode_id}),
        )
        search = tools.execute(
            "search_transcript",
            json.dumps(
                {
                    "episode_id": transcript.episode_id,
                    "query": "retrieval recall synthesis",
                    "limit": 3,
                }
            ),
        )
        read = tools.execute(
            "read_transcript_segments",
            json.dumps({"segment_ids": list(search.segment_ids)}),
        )

    metadata_payload = json.loads(metadata.output_json)
    search_payload = json.loads(search.output_json)
    read_payload = json.loads(read.output_json)
    assert metadata_payload["transcript_id"] == transcript.transcript_id
    assert 0 < metadata_payload["segment_count"] == len(tools.segments)
    assert search.segment_ids
    assert read.segment_ids == search.segment_ids
    assert all(item["segment_id"] in search.segment_ids for item in read_payload["segments"])
    assert search_payload["transcript_id"] == transcript.transcript_id


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("unknown", {}, "not available"),
        ("get_episode_metadata", {"episode_id": 999}, "outside"),
        (
            "get_episode_metadata",
            {"episode_id": 1, "extra": True},
            "strict schema",
        ),
        (
            "search_transcript",
            {"episode_id": 1, "query": "!", "limit": 1},
            "lexical terms",
        ),
        (
            "read_transcript_segments",
            {"segment_ids": ["a" * 64, "a" * 64]},
            "duplicates",
        ),
    ],
)
def test_invalid_tool_requests_fail_closed(
    tmp_path: Path,
    name: str,
    arguments: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / f"invalid-{name}.db"
    with TranscriptStore(path) as store:
        transcript = _stored(store)
        tools = TranscriptTools(store, transcript, _settings(path))
        if "episode_id" in arguments and arguments["episode_id"] == 1:
            arguments["episode_id"] = transcript.episode_id
        with pytest.raises(RetrievalToolError, match=message):
            tools.execute(name, json.dumps(arguments))


def test_tool_limits_and_exact_answer_evidence_are_enforced(tmp_path: Path) -> None:
    path = tmp_path / "limits.db"
    with TranscriptStore(path) as store:
        transcript = _stored(store)
        settings = _settings(
            path,
            intelligence_max_query_chars=10,
            intelligence_max_search_results=1,
            intelligence_max_read_segments=1,
        )
        tools = TranscriptTools(store, transcript, settings)
        with pytest.raises(RetrievalToolError, match="query exceeds"):
            tools.execute(
                "search_transcript",
                json.dumps(
                    {
                        "episode_id": transcript.episode_id,
                        "query": "this query is too long",
                        "limit": 1,
                    }
                ),
            )
        segment = tools.segments[0]
        valid = QuestionAnswer(
            answer="Use a versioned evaluation corpus.",
            evidence=[
                AnalysisEvidence(
                    segment_id=segment.segment_id,
                    quote="versioned evaluation corpus",
                )
            ],
            insufficient_evidence=False,
        )
        tools.validate_answer(valid)
        invalid = valid.model_copy(
            update={
                "evidence": [
                    AnalysisEvidence(segment_id=segment.segment_id, quote="invented evidence")
                ]
            }
        )
        with pytest.raises(ValueError, match="exact quote"):
            tools.validate_answer(invalid)
