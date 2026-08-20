"""Typed contracts for retrieval, question answering, and episode intelligence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SEGMENTER_VERSION = "bounded-text-v1"
ANALYSIS_SCHEMA_VERSION = "1"
ANALYSIS_PROMPT_VERSION = "episode-intelligence-v2"
QUESTION_PROMPT_VERSION = "evidence-qa-v2"

_WORD_PATTERN = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*")


class EvidenceValidationError(ValueError):
    """Raised when model evidence does not match the selected transcript."""


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """One deterministic exact-offset retrieval unit."""

    segment_id: str
    transcript_id: int
    episode_id: int
    ordinal: int
    char_start: int
    char_end: int
    text: str
    text_hash: str
    transcript_content_hash: str
    segmenter_version: str


@dataclass(frozen=True, slots=True)
class EpisodeMetadata:
    """Bounded verified identity returned by the metadata tool."""

    episode_id: int
    transcript_id: int
    feed_url: str
    rss_guid: str
    spotify_episode_id: str | None
    title: str
    transcript_content_hash: str
    segmenter_version: str
    segment_count: int


@dataclass(frozen=True, slots=True)
class TranscriptSearchHit:
    """One bounded lexical retrieval result."""

    segment: TranscriptSegment
    score: float


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisEvidence(_StrictModel):
    """Stable exact evidence emitted by a model."""

    segment_id: str = Field(min_length=64, max_length=64)
    quote: str = Field(
        min_length=1,
        description=(
            "A short, verbatim character-for-character substring from the cited transcript "
            "segment, preserving capitalization, punctuation, filler words, and whitespace."
        ),
    )


class EvidenceBackedItem(_StrictModel):
    """A material statement with one or more exact supporting excerpts."""

    text: str = Field(min_length=1)
    evidence: list[AnalysisEvidence] = Field(min_length=1)


class EpisodeAnalysis(_StrictModel):
    """Machine-consumed Structured Output for one canonical transcript."""

    summary: EvidenceBackedItem
    topics: list[EvidenceBackedItem]
    people: list[EvidenceBackedItem]
    claims: list[EvidenceBackedItem]
    actionable_insights: list[EvidenceBackedItem]
    limitations: list[str]


class QuestionAnswer(_StrictModel):
    """Evidence-grounded answer or explicit insufficiency result."""

    answer: str = Field(min_length=1)
    evidence: list[AnalysisEvidence]
    insufficient_evidence: bool

    @model_validator(mode="after")
    def evidence_required_for_answer(self) -> Self:
        if not self.insufficient_evidence and not self.evidence:
            raise ValueError("a supported answer requires evidence")
        return self


def segment_transcript_text(
    *,
    transcript_id: int,
    episode_id: int,
    content_hash: str,
    text: str,
    max_chars: int,
    segmenter_version: str = SEGMENTER_VERSION,
) -> tuple[TranscriptSegment, ...]:
    """Split transcript text at stable textual boundaries with exact offsets."""

    if transcript_id <= 0 or episode_id <= 0:
        raise ValueError("transcript and episode IDs must be positive")
    if len(content_hash) != 64:
        raise ValueError("transcript content hash must contain 64 characters")
    if not text.strip():
        raise ValueError("transcript text must not be empty")
    if max_chars < 64:
        raise ValueError("segment character limit must be at least 64")
    if not segmenter_version.strip():
        raise ValueError("segmenter version must not be empty")

    segments: list[TranscriptSegment] = []
    cursor = 0
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text):
            break
        start = cursor
        hard_end = min(start + max_chars, len(text))
        end = hard_end if hard_end == len(text) else _preferred_boundary(text, start, hard_end)
        while end > start and text[end - 1].isspace():
            end -= 1
        if end <= start:
            end = hard_end
        segment_text = text[start:end]
        ordinal = len(segments)
        segment_id = _stable_hash(
            {
                "ordinal": ordinal,
                "segmenter_version": segmenter_version,
                "transcript_content_hash": content_hash,
            }
        )
        segments.append(
            TranscriptSegment(
                segment_id=segment_id,
                transcript_id=transcript_id,
                episode_id=episode_id,
                ordinal=ordinal,
                char_start=start,
                char_end=end,
                text=segment_text,
                text_hash=sha256(segment_text.encode("utf-8")).hexdigest(),
                transcript_content_hash=content_hash,
                segmenter_version=segmenter_version,
            )
        )
        cursor = end
    return tuple(segments)


def validate_question_evidence(
    answer: QuestionAnswer,
    *,
    transcript_id: int,
    segments: tuple[TranscriptSegment, ...],
) -> None:
    """Fail unless every answer citation is exact and transcript-owned."""

    _validate_evidence(answer.evidence, transcript_id=transcript_id, segments=segments)


def canonicalize_question_evidence(
    answer: QuestionAnswer,
    *,
    transcript_id: int,
    segments: tuple[TranscriptSegment, ...],
) -> QuestionAnswer:
    """Align punctuation-only quote drift to one unambiguous source substring."""

    return answer.model_copy(
        update={
            "evidence": _canonicalize_evidence(
                answer.evidence,
                transcript_id=transcript_id,
                segments=segments,
            )
        }
    )


def validate_analysis_evidence(
    analysis: EpisodeAnalysis,
    *,
    transcript_id: int,
    segments: tuple[TranscriptSegment, ...],
) -> None:
    """Fail unless every material analysis item has exact transcript evidence."""

    items = [analysis.summary]
    items.extend(analysis.topics)
    items.extend(analysis.people)
    items.extend(analysis.claims)
    items.extend(analysis.actionable_insights)
    for item in items:
        _validate_evidence(item.evidence, transcript_id=transcript_id, segments=segments)


def canonicalize_analysis_evidence(
    analysis: EpisodeAnalysis,
    *,
    transcript_id: int,
    segments: tuple[TranscriptSegment, ...],
) -> EpisodeAnalysis:
    """Align punctuation-only evidence drift without accepting paraphrases."""

    return analysis.model_copy(
        update={
            "summary": _canonicalize_item(
                analysis.summary,
                transcript_id=transcript_id,
                segments=segments,
            ),
            "topics": [
                _canonicalize_item(item, transcript_id=transcript_id, segments=segments)
                for item in analysis.topics
            ],
            "people": [
                _canonicalize_item(item, transcript_id=transcript_id, segments=segments)
                for item in analysis.people
            ],
            "claims": [
                _canonicalize_item(item, transcript_id=transcript_id, segments=segments)
                for item in analysis.claims
            ],
            "actionable_insights": [
                _canonicalize_item(item, transcript_id=transcript_id, segments=segments)
                for item in analysis.actionable_insights
            ],
        }
    )


def iter_analysis_evidence(
    analysis: EpisodeAnalysis,
) -> tuple[tuple[str, AnalysisEvidence], ...]:
    """Return stable field paths for normalized relational evidence links."""

    items: list[tuple[str, EvidenceBackedItem]] = [("summary", analysis.summary)]
    for field_name in ("topics", "people", "claims", "actionable_insights"):
        field_items = getattr(analysis, field_name)
        items.extend((f"{field_name}[{index}]", item) for index, item in enumerate(field_items))
    return tuple(
        (f"{item_path}.evidence[{index}]", evidence)
        for item_path, item in items
        for index, evidence in enumerate(item.evidence)
    )


def _validate_evidence(
    evidence_items: list[AnalysisEvidence],
    *,
    transcript_id: int,
    segments: tuple[TranscriptSegment, ...],
) -> None:
    by_id = {segment.segment_id: segment for segment in segments}
    for evidence in evidence_items:
        segment = by_id.get(evidence.segment_id)
        if segment is None:
            raise EvidenceValidationError("evidence references an unknown segment")
        if segment.transcript_id != transcript_id:
            raise EvidenceValidationError("evidence does not belong to the selected transcript")
        if evidence.quote not in segment.text:
            raise EvidenceValidationError("evidence exact quote was not found in its segment")


def _canonicalize_item(
    item: EvidenceBackedItem,
    *,
    transcript_id: int,
    segments: tuple[TranscriptSegment, ...],
) -> EvidenceBackedItem:
    return item.model_copy(
        update={
            "evidence": _canonicalize_evidence(
                item.evidence,
                transcript_id=transcript_id,
                segments=segments,
            )
        }
    )


def _canonicalize_evidence(
    evidence_items: list[AnalysisEvidence],
    *,
    transcript_id: int,
    segments: tuple[TranscriptSegment, ...],
) -> list[AnalysisEvidence]:
    by_id = {segment.segment_id: segment for segment in segments}
    canonical: list[AnalysisEvidence] = []
    for evidence in evidence_items:
        segment = by_id.get(evidence.segment_id)
        if segment is None:
            raise EvidenceValidationError("evidence references an unknown segment")
        if segment.transcript_id != transcript_id:
            raise EvidenceValidationError("evidence does not belong to the selected transcript")
        if evidence.quote in segment.text:
            canonical.append(evidence)
            continue
        aligned_quote = _align_unique_word_sequence(evidence.quote, segment.text)
        if aligned_quote is None:
            raise EvidenceValidationError(
                "evidence quote could not be aligned to one exact segment substring"
            )
        canonical.append(evidence.model_copy(update={"quote": aligned_quote}))
    return canonical


def _align_unique_word_sequence(quote: str, segment_text: str) -> str | None:
    quote_tokens = [_normalize_word(match.group()) for match in _WORD_PATTERN.finditer(quote)]
    if len(quote_tokens) < 2:
        return None
    segment_matches = list(_WORD_PATTERN.finditer(segment_text))
    segment_tokens = [_normalize_word(match.group()) for match in segment_matches]
    width = len(quote_tokens)
    starts = [
        index
        for index in range(len(segment_tokens) - width + 1)
        if segment_tokens[index : index + width] == quote_tokens
    ]
    if len(starts) != 1:
        return None
    start = starts[0]
    return segment_text[segment_matches[start].start() : segment_matches[start + width - 1].end()]


def _normalize_word(word: str) -> str:
    return word.casefold().replace("\u2019", "'")


def _preferred_boundary(text: str, start: int, hard_end: int) -> int:
    lower_bound = start + ((hard_end - start) // 2)
    for marker, adjustment in (("\n\n", 0), (". ", 1), ("? ", 1), ("! ", 1), (" ", 0)):
        index = text.rfind(marker, lower_bound, hard_end + 1)
        if index >= lower_bound:
            return index + adjustment
    return hard_end


def _stable_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()
