from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Self

import pytest

from podcast_intelligence import cli
from podcast_intelligence.audio_transcription import ProviderTranscript, ProviderTranscriptPart
from podcast_intelligence.intelligence import EpisodeIntelligenceResult
from podcast_intelligence.intelligence_models import (
    AnalysisEvidence,
    EpisodeAnalysis,
    EvidenceBackedItem,
    QuestionAnswer,
)
from podcast_intelligence.persistence import EpisodeStatus, StoredEpisodeAnalysis, StoredTranscript
from podcast_intelligence.responses_client import (
    QuestionResponse,
    ResponseUsageSummary,
    ToolCallTrace,
)
from podcast_intelligence.settings import Settings

_SEGMENT_ID = "a" * 64


class _FakeStore:
    def __init__(
        self,
        transcript: StoredTranscript,
        analysis: StoredEpisodeAnalysis,
    ) -> None:
        self.transcript = transcript
        self.analysis = analysis

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def list_episode_statuses(self) -> tuple[EpisodeStatus, ...]:
        return (
            EpisodeStatus(
                episode_id=self.transcript.episode_id,
                spotify_episode_id=self.transcript.spotify_episode_id,
                title=self.transcript.episode_title,
                latest_transcription_status="succeeded",
                transcript_run_id=self.transcript.run_id,
                analysis_available=True,
                latest_analysis_status="succeeded",
            ),
        )

    def get_transcript(self, run_id: int) -> StoredTranscript:
        if run_id != self.transcript.run_id:
            raise AssertionError("unexpected transcript run ID")
        return self.transcript


def _transcript() -> StoredTranscript:
    part = ProviderTranscriptPart(
        ordinal=0,
        text="SENSITIVE FULL TRANSCRIPT",
        request_id="req_1",
        model="gpt-transcribe",
        language="en",
    )
    provider = ProviderTranscript(
        text=part.text,
        provider="openai",
        model="gpt-transcribe",
        request_ids=("req_1",),
        language="en",
        chunk_count=1,
        parts=(part,),
    )
    return StoredTranscript(
        run_id=7,
        transcript_id=11,
        episode_id=13,
        feed_url="https://example.test/feed.xml",
        rss_guid="guid-1",
        spotify_episode_id="0VPwvReM2olZDWl3YOHfqh",
        episode_title="Unsafe\nTitle",
        source_fingerprint="b" * 64,
        audio_sha256="c" * 64,
        audio_bytes=100,
        estimated_cost_microusd=4_500,
        provider_transcript=provider,
        content_hash="d" * 64,
        created_at="2026-08-20T00:00:00+00:00",
    )


def _analysis() -> StoredEpisodeAnalysis:
    evidence = [AnalysisEvidence(segment_id=_SEGMENT_ID, quote="Exact\nquote")]
    item = EvidenceBackedItem(text="Evidence-backed result.", evidence=evidence)
    return StoredEpisodeAnalysis(
        run_id=17,
        analysis_id=19,
        transcript_id=11,
        cache_identity="e" * 64,
        analysis_type="episode_intelligence",
        model="gpt-5.6-sol",
        prompt_version="prompt-v1",
        schema_version="1",
        segmenter_version="segmenter-v1",
        response_id="resp_analysis",
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        analysis=EpisodeAnalysis(
            summary=item,
            topics=[item],
            people=[],
            claims=[item],
            actionable_insights=[item],
            limitations=["No speaker\ntiming."],
        ),
        created_at="2026-08-20T00:01:00+00:00",
    )


def _question_response() -> QuestionResponse:
    return QuestionResponse(
        response_id="resp_answer",
        model="gpt-5.6-sol",
        answer=QuestionAnswer(
            answer="Supported answer.",
            evidence=[AnalysisEvidence(segment_id=_SEGMENT_ID, quote="Exact quote")],
            insufficient_evidence=False,
        ),
        response_ids=("resp_search", "resp_answer"),
        output_item_types=(("reasoning", "function_call"), ("message",)),
        tool_calls=(
            ToolCallTrace(
                response_id="resp_search",
                call_id="call_search",
                tool_name="search_transcript",
                arguments_json='{"query":"secret query"}',
                result_segment_ids=(_SEGMENT_ID,),
            ),
        ),
        usage=ResponseUsageSummary(input_tokens=20, output_tokens=10, total_tokens=30),
    )


def _install_store(monkeypatch: pytest.MonkeyPatch, store: _FakeStore) -> None:
    monkeypatch.setattr(cli, "TranscriptStore", lambda _path: store)


def test_list_is_offline_and_does_not_load_or_print_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore(_transcript(), _analysis())
    _install_store(monkeypatch, store)
    output = StringIO()
    errors = StringIO()

    exit_code = cli.run_cli(
        ["list"],
        stdin=StringIO(),
        stdout=output,
        stderr=errors,
    )

    assert exit_code == 0
    assert "run_id=7" in output.getvalue()
    assert "analysis=available" in output.getvalue()
    assert "latest_analysis_attempt=succeeded" in output.getvalue()
    assert "Unsafe\\\\u000aTitle" in output.getvalue()
    assert "SENSITIVE FULL TRANSCRIPT" not in output.getvalue()
    assert errors.getvalue() == ""


def test_database_environment_override_and_flag_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = _FakeStore(_transcript(), _analysis())
    selected_paths: list[Path] = []

    def store_factory(path: Path) -> _FakeStore:
        selected_paths.append(path)
        return store

    environment_path = tmp_path / "environment.db"
    explicit_path = tmp_path / "explicit.db"
    monkeypatch.setenv("DATABASE_PATH", str(environment_path))
    monkeypatch.setattr(cli, "TranscriptStore", store_factory)

    assert (
        cli.run_cli(
            ["list"],
            stdin=StringIO(),
            stdout=StringIO(),
            stderr=StringIO(),
        )
        == 0
    )
    assert (
        cli.run_cli(
            ["--database-path", str(explicit_path), "list"],
            stdin=StringIO(),
            stdout=StringIO(),
            stderr=StringIO(),
        )
        == 0
    )

    assert selected_paths == [environment_path, explicit_path]


def test_provider_command_requires_explicit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore(_transcript(), _analysis())
    _install_store(monkeypatch, store)
    monkeypatch.setattr(
        cli,
        "_load_settings",
        lambda _path: Settings(_env_file=None, database_path=Path("test.db")),
    )
    called = False

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("provider-bound service must not be called")

    monkeypatch.setattr(cli, "analyze_episode", fail_if_called)
    errors = StringIO()
    exit_code = cli.run_cli(
        ["analyze", "7"],
        stdin=StringIO("no\n"),
        stdout=StringIO(),
        stderr=errors,
    )

    assert exit_code == 2
    assert called is False
    assert "no transcript content was sent" in errors.getvalue()


def test_analyze_renders_cache_evidence_and_safe_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore(_transcript(), _analysis())
    _install_store(monkeypatch, store)
    monkeypatch.setattr(
        cli,
        "_load_settings",
        lambda _path: Settings(_env_file=None, database_path=Path("test.db")),
    )
    monkeypatch.setattr(
        cli,
        "analyze_episode",
        lambda *_args, **_kwargs: EpisodeIntelligenceResult(
            analysis=store.analysis,
            cache_status="analysis",
        ),
    )
    output = StringIO()

    exit_code = cli.run_cli(
        ["analyze", "7", "--yes"],
        stdin=StringIO(),
        stdout=output,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "cache=analysis" in output.getvalue()
    assert "response_id=resp_analysis" in output.getvalue()
    assert "Exact\\\\u000aquote" in output.getvalue()
    assert "SENSITIVE FULL TRANSCRIPT" not in output.getvalue()


def test_ask_renders_exact_citations_and_observable_trace_without_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _FakeStore(_transcript(), _analysis())
    _install_store(monkeypatch, store)
    monkeypatch.setattr(
        cli,
        "_load_settings",
        lambda _path: Settings(_env_file=None, database_path=Path("test.db")),
    )
    monkeypatch.setattr(
        cli,
        "answer_episode_question",
        lambda *_args, **_kwargs: _question_response(),
    )
    output = StringIO()

    exit_code = cli.run_cli(
        ["ask", "7", "What happened?", "--yes"],
        stdin=StringIO(),
        stdout=output,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert "Supported answer." in output.getvalue()
    assert f"[{_SEGMENT_ID}]" in output.getvalue()
    assert "tool=search_transcript" in output.getvalue()
    assert "call_id=call_search" in output.getvalue()
    assert "secret query" not in output.getvalue()
