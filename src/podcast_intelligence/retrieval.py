"""Bounded read-only tools over one selected canonical transcript."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast

from podcast_intelligence.intelligence_models import (
    SEGMENTER_VERSION,
    EpisodeMetadata,
    QuestionAnswer,
    TranscriptSearchHit,
    TranscriptSegment,
    validate_question_evidence,
)
from podcast_intelligence.persistence import PersistenceError, StoredTranscript, TranscriptStore
from podcast_intelligence.settings import Settings


class RetrievalToolError(RuntimeError):
    """Raised when a model requests an invalid or disallowed retrieval operation."""


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """Bounded serialized tool result plus observable evidence IDs."""

    name: str
    output_json: str
    segment_ids: tuple[str, ...]


class TranscriptTools:
    """Expose three strict read-only tools scoped to one transcript."""

    def __init__(
        self,
        store: TranscriptStore,
        transcript: StoredTranscript,
        settings: Settings,
        *,
        segmenter_version: str = SEGMENTER_VERSION,
    ) -> None:
        self._store = store
        self.transcript = transcript
        self.settings = settings
        self.segmenter_version = segmenter_version
        self._segments = store.ensure_segments(
            transcript,
            segmenter_version=segmenter_version,
            max_chars=settings.intelligence_segment_chars,
        )

    @property
    def segments(self) -> tuple[TranscriptSegment, ...]:
        return self._segments

    @property
    def metadata(self) -> EpisodeMetadata:
        return self._store.episode_metadata(
            self.transcript,
            segmenter_version=self.segmenter_version,
        )

    def tool_definitions(self) -> tuple[dict[str, object], ...]:
        """Return strict JSON schemas with every argument required."""

        return (
            {
                "type": "function",
                "name": "get_episode_metadata",
                "description": (
                    "Return verified identity for the selected episode and canonical transcript."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "episode_id": {
                            "type": "integer",
                            "description": "The selected local episode ID.",
                        }
                    },
                    "required": ["episode_id"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "search_transcript",
                "description": (
                    "Search the selected transcript lexically and return bounded exact excerpts."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "episode_id": {"type": "integer"},
                        "query": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": self.settings.intelligence_max_query_chars,
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": self.settings.intelligence_max_search_results,
                        },
                    },
                    "required": ["episode_id", "query", "limit"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "read_transcript_segments",
                "description": (
                    "Read exact selected-transcript segments by IDs returned from search."
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "segment_ids": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 64, "maxLength": 64},
                            "minItems": 1,
                            "maxItems": self.settings.intelligence_max_read_segments,
                        }
                    },
                    "required": ["segment_ids"],
                    "additionalProperties": False,
                },
            },
        )

    def execute(self, name: str, arguments_json: str) -> ToolExecution:
        """Validate and execute one model-requested tool call."""

        arguments = _parse_arguments(arguments_json)
        if name == "get_episode_metadata":
            _require_keys(arguments, {"episode_id"})
            self._validate_episode_id(arguments["episode_id"])
            metadata = self.metadata
            return self._bounded_result(
                name,
                {
                    "episode_id": metadata.episode_id,
                    "transcript_id": metadata.transcript_id,
                    "feed_url": metadata.feed_url,
                    "rss_guid": metadata.rss_guid,
                    "spotify_episode_id": metadata.spotify_episode_id,
                    "title": metadata.title,
                    "transcript_content_hash": metadata.transcript_content_hash,
                    "segmenter_version": metadata.segmenter_version,
                    "segment_count": metadata.segment_count,
                },
                (),
            )
        if name == "search_transcript":
            _require_keys(arguments, {"episode_id", "query", "limit"})
            self._validate_episode_id(arguments["episode_id"])
            query = _require_string(arguments["query"], "query")
            if len(query) > self.settings.intelligence_max_query_chars:
                raise RetrievalToolError("search query exceeds the configured limit")
            limit = _require_integer(arguments["limit"], "limit")
            if not 1 <= limit <= self.settings.intelligence_max_search_results:
                raise RetrievalToolError("search result count exceeds the configured limit")
            try:
                hits = self._store.search_segments(
                    self.transcript.transcript_id,
                    query=query,
                    limit=limit,
                    segmenter_version=self.segmenter_version,
                )
            except ValueError as error:
                raise RetrievalToolError("search query contains no usable lexical terms") from error
            except PersistenceError as error:
                raise RetrievalToolError("transcript search could not be completed") from error
            return self._search_result(name, hits)
        if name == "read_transcript_segments":
            _require_keys(arguments, {"segment_ids"})
            raw_ids = arguments["segment_ids"]
            if not isinstance(raw_ids, list) or not raw_ids:
                raise RetrievalToolError("segment_ids must be a non-empty array")
            segment_ids = tuple(_require_string(item, "segment_id") for item in raw_ids)
            if len(segment_ids) > self.settings.intelligence_max_read_segments:
                raise RetrievalToolError("segment read count exceeds the configured limit")
            if len(set(segment_ids)) != len(segment_ids):
                raise RetrievalToolError("segment_ids must not contain duplicates")
            if any(len(segment_id) != 64 for segment_id in segment_ids):
                raise RetrievalToolError("segment IDs must contain 64 characters")
            try:
                segments = self._store.read_segments(
                    self.transcript.transcript_id,
                    segment_ids,
                )
            except PersistenceError as error:
                raise RetrievalToolError("one or more segment IDs are unknown") from error
            return self._segments_result(name, segments)
        raise RetrievalToolError("requested tool is not available")

    def validate_answer(self, answer: QuestionAnswer) -> None:
        segment_ids = tuple(evidence.segment_id for evidence in answer.evidence)
        if not segment_ids:
            validate_question_evidence(
                answer,
                transcript_id=self.transcript.transcript_id,
                segments=(),
            )
            return
        try:
            segments = self._store.read_segments(
                self.transcript.transcript_id,
                tuple(dict.fromkeys(segment_ids)),
            )
        except PersistenceError as error:
            raise RetrievalToolError("answer evidence contains an unknown segment") from error
        validate_question_evidence(
            answer,
            transcript_id=self.transcript.transcript_id,
            segments=segments,
        )

    def _validate_episode_id(self, value: object) -> None:
        episode_id = _require_integer(value, "episode_id")
        if episode_id != self.transcript.episode_id:
            raise RetrievalToolError("episode ID is outside the selected transcript")

    def _search_result(
        self,
        name: str,
        hits: tuple[TranscriptSearchHit, ...],
    ) -> ToolExecution:
        payload = {
            "transcript_id": self.transcript.transcript_id,
            "results": [{**_segment_payload(hit.segment), "score": hit.score} for hit in hits],
        }
        return self._bounded_result(
            name,
            payload,
            tuple(hit.segment.segment_id for hit in hits),
        )

    def _segments_result(
        self,
        name: str,
        segments: tuple[TranscriptSegment, ...],
    ) -> ToolExecution:
        return self._bounded_result(
            name,
            {
                "transcript_id": self.transcript.transcript_id,
                "segments": [_segment_payload(segment) for segment in segments],
            },
            tuple(segment.segment_id for segment in segments),
        )

    def _bounded_result(
        self,
        name: str,
        payload: dict[str, object],
        segment_ids: tuple[str, ...],
    ) -> ToolExecution:
        output = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(output) > self.settings.intelligence_max_tool_output_chars:
            raise RetrievalToolError("tool output exceeds the configured character limit")
        return ToolExecution(name=name, output_json=output, segment_ids=segment_ids)


def _segment_payload(segment: TranscriptSegment) -> dict[str, object]:
    return {
        "segment_id": segment.segment_id,
        "ordinal": segment.ordinal,
        "char_start": segment.char_start,
        "char_end": segment.char_end,
        "text": segment.text,
        "text_hash": segment.text_hash,
    }


def _parse_arguments(arguments_json: str) -> dict[str, object]:
    try:
        value = json.loads(arguments_json)
    except (json.JSONDecodeError, TypeError) as error:
        raise RetrievalToolError("tool arguments are not valid JSON") from error
    if not isinstance(value, dict):
        raise RetrievalToolError("tool arguments must be a JSON object")
    return cast(dict[str, object], value)


def _require_keys(arguments: dict[str, object], expected: set[str]) -> None:
    if set(arguments) != expected:
        raise RetrievalToolError("tool arguments do not match the strict schema")


def _require_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetrievalToolError(f"{name} must be a non-empty string")
    return value.strip()


def _require_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RetrievalToolError(f"{name} must be an integer")
    return value
