"""Typed local evaluation corpus and deterministic surface-specific graders."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from podcast_intelligence.intelligence_models import (
    EpisodeAnalysis,
    QuestionAnswer,
    validate_analysis_evidence,
)
from podcast_intelligence.responses_client import QuestionResponse
from podcast_intelligence.retrieval import TranscriptTools

EvaluationCategory = Literal[
    "metadata",
    "lexical",
    "multi_segment",
    "insufficient",
    "ambiguous",
    "prompt_injection",
    "cross_episode",
]


class EvaluationCorpusError(ValueError):
    """Raised when committed evaluation ground truth is invalid."""


class EvaluationCaseTemplate(BaseModel):
    """Human-readable committed case before stable segment IDs are materialized."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    category: EvaluationCategory
    question: str = Field(min_length=1)
    gold_phrases: list[str]
    reference_answer_terms: list[str]
    should_abstain: bool
    required_tools: list[str]
    allowed_tools: list[str]
    forbidden_tools: list[str]


class EvaluationCorpus(BaseModel):
    """Committed synthetic transcript and its evaluation templates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transcript: str = Field(min_length=1)
    cases: list[EvaluationCaseTemplate] = Field(min_length=20)


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """Runtime case with deterministic gold segment identities."""

    case_id: str
    category: EvaluationCategory
    episode_id: int
    question: str
    gold_segment_ids: tuple[str, ...]
    reference_answer_terms: tuple[str, ...]
    should_abstain: bool
    required_tools: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SurfaceGrade:
    """Deterministic result for one independently diagnosable surface."""

    surface: str
    passed: bool
    score: float
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationReport:
    """Aggregate lexical retrieval metrics with every miss retained."""

    evaluated_cases: int
    hit_rate: float
    recall_at_5: float
    mean_reciprocal_rank: float
    failed_case_ids: tuple[str, ...]


def load_evaluation_corpus(path: Path) -> EvaluationCorpus:
    """Load and strictly validate a committed local JSON corpus."""

    try:
        return EvaluationCorpus.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EvaluationCorpusError("evaluation corpus could not be loaded") from error


def materialize_cases(
    corpus: EvaluationCorpus,
    tools: TranscriptTools,
) -> tuple[EvaluationCase, ...]:
    """Resolve human-readable gold phrases to stable selected-transcript segment IDs."""

    materialized: list[EvaluationCase] = []
    for template in corpus.cases:
        gold_ids: list[str] = []
        for phrase in template.gold_phrases:
            matching = [
                segment.segment_id
                for segment in tools.segments
                if phrase.casefold() in segment.text.casefold()
            ]
            if len(matching) != 1:
                raise EvaluationCorpusError(
                    f"case {template.case_id} gold phrase must match exactly one segment"
                )
            gold_ids.append(matching[0])
        materialized.append(
            EvaluationCase(
                case_id=template.case_id,
                category=template.category,
                episode_id=tools.transcript.episode_id,
                question=template.question,
                gold_segment_ids=tuple(dict.fromkeys(gold_ids)),
                reference_answer_terms=tuple(template.reference_answer_terms),
                should_abstain=template.should_abstain,
                required_tools=tuple(template.required_tools),
                allowed_tools=tuple(template.allowed_tools),
                forbidden_tools=tuple(template.forbidden_tools),
            )
        )
    return tuple(materialized)


def evaluate_retrieval(
    cases: tuple[EvaluationCase, ...],
    tools: TranscriptTools,
) -> RetrievalEvaluationReport:
    """Score lexical retrieval without allowing answer synthesis to hide misses."""

    scored = [case for case in cases if case.gold_segment_ids]
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    hits = 0
    failures: list[str] = []
    for case in scored:
        execution = tools.execute(
            "search_transcript",
            json.dumps(
                {
                    "episode_id": case.episode_id,
                    "query": case.question,
                    "limit": min(5, tools.settings.intelligence_max_search_results),
                }
            ),
        )
        retrieved = execution.segment_ids
        relevant = set(case.gold_segment_ids)
        matched = relevant.intersection(retrieved)
        recall = len(matched) / len(relevant)
        recalls.append(recall)
        if matched:
            hits += 1
            first_rank = min(
                index + 1 for index, segment_id in enumerate(retrieved) if segment_id in relevant
            )
            reciprocal_ranks.append(1 / first_rank)
        else:
            reciprocal_ranks.append(0.0)
        if recall < 1.0:
            failures.append(case.case_id)
    count = len(scored)
    if count == 0:
        raise EvaluationCorpusError("evaluation corpus has no retrieval-bearing cases")
    return RetrievalEvaluationReport(
        evaluated_cases=count,
        hit_rate=hits / count,
        recall_at_5=sum(recalls) / count,
        mean_reciprocal_rank=sum(reciprocal_ranks) / count,
        failed_case_ids=tuple(failures),
    )


def grade_question_response(
    case: EvaluationCase,
    response: QuestionResponse,
    tools: TranscriptTools,
) -> tuple[SurfaceGrade, SurfaceGrade, SurfaceGrade, SurfaceGrade]:
    """Grade decision, trace, answer, and operational surfaces independently."""

    decision_failures: list[str] = []
    trace_failures: list[str] = []
    answer_failures: list[str] = []
    operational_failures: list[str] = []
    names = tuple(trace.tool_name for trace in response.tool_calls)
    if not set(case.required_tools).issubset(names):
        decision_failures.append("required tool was not called")
    if set(case.forbidden_tools).intersection(names):
        decision_failures.append("forbidden tool was called")
    permitted = set(case.required_tools).union(case.allowed_tools)
    if (permitted and not set(names).issubset(permitted)) or (not permitted and names):
        decision_failures.append("unexpected tool was called")
    if response.answer.insufficient_evidence != case.should_abstain:
        decision_failures.append("abstention decision did not match the case")

    call_ids = [trace.call_id for trace in response.tool_calls]
    if len(call_ids) != len(set(call_ids)):
        trace_failures.append("function call IDs are not unique")
    if any(not trace.response_id for trace in response.tool_calls):
        trace_failures.append("tool call is missing response lineage")
    if any(trace.response_id not in response.response_ids for trace in response.tool_calls):
        trace_failures.append("tool call response ID is outside the response lineage")
    if any(not trace.call_id for trace in response.tool_calls):
        trace_failures.append("tool call ID is empty")
    if any(
        any(len(segment_id) != 64 for segment_id in trace.result_segment_ids)
        for trace in response.tool_calls
    ):
        trace_failures.append("tool result contains an invalid segment ID")
    if len(response.response_ids) != len(response.output_item_types):
        trace_failures.append("response item lineage is incomplete")

    try:
        tools.validate_answer(response.answer)
    except ValueError:
        answer_failures.append("answer evidence failed deterministic validation")
    normalized_answer = response.answer.answer.casefold()
    answer_failures.extend(
        f"reference answer term is missing: {term}"
        for term in case.reference_answer_terms
        if term.casefold() not in normalized_answer
    )

    if len(response.tool_calls) > tools.settings.intelligence_max_tool_calls:
        operational_failures.append("tool-call budget was exceeded")
    if (
        min(
            response.usage.input_tokens,
            response.usage.output_tokens,
            response.usage.total_tokens,
        )
        < 0
    ):
        operational_failures.append("usage contains a negative value")

    return (
        _grade("agent_decision", decision_failures),
        _grade("tool_trace", trace_failures),
        _grade("final_response", answer_failures),
        _grade("operational", operational_failures),
    )


def grade_episode_analysis(
    analysis: EpisodeAnalysis,
    tools: TranscriptTools,
) -> SurfaceGrade:
    """Apply schema and exact evidence gates to structured analysis."""

    failures: list[str] = []
    try:
        validate_analysis_evidence(
            analysis,
            transcript_id=tools.transcript.transcript_id,
            segments=tools.segments,
        )
    except ValueError:
        failures.append("structured analysis evidence failed deterministic validation")
    return _grade("structured_analysis", failures)


def grade_question_answer(
    case: EvaluationCase,
    answer: QuestionAnswer,
    tools: TranscriptTools,
) -> SurfaceGrade:
    """Grade an answer independently when no full trace object is available."""

    failures: list[str] = []
    try:
        tools.validate_answer(answer)
    except ValueError:
        failures.append("answer evidence failed deterministic validation")
    if answer.insufficient_evidence != case.should_abstain:
        failures.append("abstention decision did not match the case")
    normalized = answer.answer.casefold()
    failures.extend(
        f"reference answer term is missing: {term}"
        for term in case.reference_answer_terms
        if term.casefold() not in normalized
    )
    return _grade("final_response", failures)


def _grade(surface: str, failures: list[str]) -> SurfaceGrade:
    return SurfaceGrade(
        surface=surface,
        passed=not failures,
        score=1.0 if not failures else 0.0,
        failures=tuple(failures),
    )
