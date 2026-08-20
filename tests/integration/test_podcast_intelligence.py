"""Explicit live evaluation for persisted evidence-grounded podcast intelligence."""

import os

import pytest

from podcast_intelligence.evaluation import (
    EvaluationCase,
    grade_episode_analysis,
    grade_question_response,
)
from podcast_intelligence.intelligence import analyze_episode
from podcast_intelligence.persistence import TranscriptStore
from podcast_intelligence.responses_client import PodcastResponsesClient
from podcast_intelligence.retrieval import TranscriptTools
from podcast_intelligence.settings import Settings

_SELECTED_SPOTIFY_EPISODE_ID = "0VPwvReM2olZDWl3YOHfqh"


@pytest.mark.integration
def test_live_persisted_episode_analysis_and_question_answering() -> None:
    if os.environ.get("RUN_LIVE_PODCAST_INTELLIGENCE") != "1":
        pytest.skip("set RUN_LIVE_PODCAST_INTELLIGENCE=1 for the authorized live evaluation")

    settings = Settings()
    client = PodcastResponsesClient(settings)
    with TranscriptStore(settings.database_path) as store:
        selected = next(
            transcript
            for transcript in store.list_transcripts()
            if transcript.spotify_episode_id == _SELECTED_SPOTIFY_EPISODE_ID
        )
        transcription_count_before = len(store.list_transcripts())
        first = analyze_episode(
            selected.run_id,
            settings=settings,
            store=store,
            responses_client=client,
        )
        cached = analyze_episode(
            selected.run_id,
            settings=settings,
            store=store,
            responses_client=client,
        )
        tools = TranscriptTools(store, selected, settings)
        answer = client.answer_question(
            "According to this transcript, what are the main concerns about the scale and "
            "sustainability of AI investment?",
            tools,
        )
        live_case = EvaluationCase(
            case_id="live-ai-investment-sustainability",
            category="multi_segment",
            episode_id=selected.episode_id,
            question=(
                "According to this transcript, what are the main concerns about the scale and "
                "sustainability of AI investment?"
            ),
            gold_segment_ids=(),
            reference_answer_terms=("scale", "capital"),
            should_abstain=False,
            required_tools=("search_transcript",),
            allowed_tools=("get_episode_metadata", "read_transcript_segments"),
            forbidden_tools=(),
        )
        analysis_grade = grade_episode_analysis(first.analysis.analysis, tools)
        question_grades = grade_question_response(live_case, answer, tools)
        transcription_count_after = len(store.list_transcripts())

    assert first.cache_status in {"miss", "analysis"}
    assert cached.cache_status == "analysis"
    assert cached.analysis.run_id == first.analysis.run_id
    assert first.analysis.analysis.summary.evidence
    assert first.analysis.response_id.startswith("resp_")
    assert answer.response_id.startswith("resp_")
    assert answer.answer.answer.strip()
    assert answer.answer.evidence or answer.answer.insufficient_evidence
    assert answer.tool_calls
    assert analysis_grade.passed
    assert all(grade.passed for grade in question_grades), question_grades
    assert transcription_count_after == transcription_count_before
