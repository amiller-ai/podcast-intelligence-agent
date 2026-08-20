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
from podcast_intelligence.evaluation import (
    EvaluationCorpus,
    EvaluationCorpusError,
    evaluate_retrieval,
    grade_episode_analysis,
    grade_question_answer,
    grade_question_response,
    load_evaluation_corpus,
    materialize_cases,
)
from podcast_intelligence.intelligence_models import (
    AnalysisEvidence,
    EpisodeAnalysis,
    EvidenceBackedItem,
    QuestionAnswer,
)
from podcast_intelligence.models import PodcastEpisode
from podcast_intelligence.persistence import StoredTranscript, TranscriptStore
from podcast_intelligence.responses_client import (
    QuestionResponse,
    ResponseUsageSummary,
    ToolCallTrace,
)
from podcast_intelligence.retrieval import TranscriptTools
from podcast_intelligence.settings import Settings

_CORPUS_PATH = Path(__file__).parent / "fixtures" / "intelligence_eval_cases.json"


def _persist_text(store: TranscriptStore, text: str) -> StoredTranscript:
    episode = PodcastEpisode(
        episode_id="eval-guid",
        title="Offline evaluation episode",
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
        catalog_episode_guid="eval-guid",
        episode=episode,
        transcript=resolve_transcript_sources(episode),
    )
    record = store.upsert_episode(resolved)
    run_id = store.create_run(
        record,
        provider="openai",
        model="gpt-transcribe",
        chunker_version="eval-chunker",
        prompt_version="eval-prompt",
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
    return store.persist_success(
        run_id,
        record,
        EpisodeTranscript(
            episode_id="eval-guid",
            source_url="https://cdn.example.test/eval.mp3",
            source_media_type="audio/mpeg",
            duration_seconds=60,
            audio_bytes=100,
            audio_sha256=sha256(b"eval-audio").hexdigest(),
            etag=None,
            last_modified=None,
            estimated_cost_usd=Decimal("0.0045"),
            transcript=ProviderTranscript(
                text=text,
                provider="openai",
                model="gpt-transcribe",
                request_ids=("req_eval",),
                language="en",
                chunk_count=1,
                parts=(part,),
            ),
        ),
        chunker_version="eval-chunker",
        prompt_version="eval-prompt",
    )


def _settings(path: Path) -> Settings:
    return Settings.model_validate(
        {
            "openai_api_key": "test-key",
            "database_path": path,
            "intelligence_segment_chars": 220,
        }
    )


def test_committed_corpus_has_twenty_typed_diverse_cases_and_meets_retrieval_gate(
    tmp_path: Path,
) -> None:
    corpus = load_evaluation_corpus(_CORPUS_PATH)
    path = tmp_path / "eval.db"
    with TranscriptStore(path) as store:
        transcript = _persist_text(store, corpus.transcript)
        tools = TranscriptTools(store, transcript, _settings(path))
        cases = materialize_cases(corpus, tools)
        report = evaluate_retrieval(cases, tools)

    assert len(cases) == 20
    assert {case.category for case in cases} == {
        "metadata",
        "lexical",
        "multi_segment",
        "insufficient",
        "ambiguous",
        "prompt_injection",
        "cross_episode",
    }
    assert report.evaluated_cases >= 12
    assert report.hit_rate >= 0.90
    assert report.recall_at_5 >= 0.90
    assert report.mean_reciprocal_rank > 0


def test_response_trace_and_analysis_surfaces_are_graded_independently(tmp_path: Path) -> None:
    corpus = load_evaluation_corpus(_CORPUS_PATH)
    path = tmp_path / "surface-grades.db"
    with TranscriptStore(path) as store:
        transcript = _persist_text(store, corpus.transcript)
        tools = TranscriptTools(store, transcript, _settings(path))
        cases = materialize_cases(corpus, tools)
        case = next(item for item in cases if item.case_id == "lexical-versioned-corpus")
        segment = store.read_segments(
            transcript.transcript_id,
            (case.gold_segment_ids[0],),
        )[0]
        evidence = AnalysisEvidence(
            segment_id=segment.segment_id,
            quote="versioned evaluation corpus",
        )
        answer = QuestionAnswer(
            answer="Behavioral changes remain measurable with a versioned corpus.",
            evidence=[evidence],
            insufficient_evidence=False,
        )
        response = QuestionResponse(
            response_id="resp_final",
            model="gpt-5.6-sol",
            answer=answer,
            response_ids=("resp_search", "resp_final"),
            output_item_types=(("function_call",), ("message",)),
            tool_calls=(
                ToolCallTrace(
                    response_id="resp_search",
                    call_id="call_search",
                    tool_name="search_transcript",
                    arguments_json="{}",
                    result_segment_ids=(segment.segment_id,),
                ),
            ),
            usage=ResponseUsageSummary(input_tokens=10, output_tokens=5, total_tokens=15),
        )
        decision, trace, final, operational = grade_question_response(case, response, tools)
        item = EvidenceBackedItem(text="Use versioned cases.", evidence=[evidence])
        analysis_grade = grade_episode_analysis(
            EpisodeAnalysis(
                summary=item,
                topics=[item],
                people=[],
                claims=[item],
                actionable_insights=[item],
                limitations=["Synthetic corpus."],
            ),
            tools,
        )

    assert decision.passed
    assert trace.passed
    assert final.passed
    assert operational.passed
    assert analysis_grade.passed


def test_unsupported_answer_failure_does_not_hide_passing_trace(tmp_path: Path) -> None:
    corpus = load_evaluation_corpus(_CORPUS_PATH)
    path = tmp_path / "independent-failure.db"
    with TranscriptStore(path) as store:
        transcript = _persist_text(store, corpus.transcript)
        tools = TranscriptTools(store, transcript, _settings(path))
        case = next(
            item
            for item in materialize_cases(corpus, tools)
            if item.case_id == "insufficient-stock-price"
        )
        response = QuestionResponse(
            response_id="resp_final",
            model="gpt-5.6-sol",
            answer=QuestionAnswer(
                answer="I cannot determine this from the transcript.",
                evidence=[],
                insufficient_evidence=True,
            ),
            response_ids=("resp_search", "resp_final"),
            output_item_types=(("function_call",), ("message",)),
            tool_calls=(
                ToolCallTrace(
                    response_id="resp_search",
                    call_id="call_search",
                    tool_name="search_transcript",
                    arguments_json="{}",
                    result_segment_ids=(),
                ),
            ),
            usage=ResponseUsageSummary(),
        )
        grades = grade_question_response(case, response, tools)

    assert [grade.surface for grade in grades] == [
        "agent_decision",
        "tool_trace",
        "final_response",
        "operational",
    ]
    assert grades[0].passed
    assert grades[1].passed
    assert not grades[2].passed
    assert grades[3].passed


def test_invalid_corpus_and_each_failed_surface_remain_diagnosable(tmp_path: Path) -> None:
    with pytest.raises(EvaluationCorpusError, match="could not be loaded"):
        load_evaluation_corpus(tmp_path / "missing.json")

    corpus = load_evaluation_corpus(_CORPUS_PATH)
    path = tmp_path / "failed-surfaces.db"
    with TranscriptStore(path) as store:
        transcript = _persist_text(store, corpus.transcript)
        tools = TranscriptTools(store, transcript, _settings(path))
        broken_template = corpus.cases[0].model_copy(
            update={"gold_phrases": ["phrase that is not present"]}
        )
        with pytest.raises(EvaluationCorpusError, match="exactly one segment"):
            materialize_cases(
                EvaluationCorpus(transcript=corpus.transcript, cases=[broken_template] * 20),
                tools,
            )
        cases = materialize_cases(corpus, tools)
        with pytest.raises(EvaluationCorpusError, match="no retrieval-bearing"):
            evaluate_retrieval(
                tuple(case for case in cases if not case.gold_segment_ids),
                tools,
            )
        case = next(item for item in cases if item.case_id == "metadata-identity")
        unsupported = QuestionAnswer(
            answer="I cannot determine this.",
            evidence=[],
            insufficient_evidence=True,
        )
        response = QuestionResponse(
            response_id="resp_final",
            model="gpt-5.6-sol",
            answer=unsupported,
            response_ids=("resp_final",),
            output_item_types=(("function_call",), ("message",)),
            tool_calls=(
                ToolCallTrace(
                    response_id="outside_lineage",
                    call_id="duplicate",
                    tool_name="search_transcript",
                    arguments_json="{}",
                    result_segment_ids=("short",),
                ),
                ToolCallTrace(
                    response_id="outside_lineage",
                    call_id="duplicate",
                    tool_name="read_transcript_segments",
                    arguments_json="{}",
                    result_segment_ids=(),
                ),
            ),
            usage=ResponseUsageSummary(input_tokens=-1, output_tokens=0, total_tokens=-1),
        )
        decision, trace, final, operational = grade_question_response(case, response, tools)
        direct_answer_grade = grade_question_answer(case, unsupported, tools)

    assert not decision.passed
    assert not trace.passed
    assert not final.passed
    assert any(
        failure.startswith("reference answer term is missing:") for failure in final.failures
    )
    assert not operational.passed
    assert not direct_answer_grade.passed
    assert any(
        failure.startswith("reference answer term is missing:")
        for failure in direct_answer_grade.failures
    )
