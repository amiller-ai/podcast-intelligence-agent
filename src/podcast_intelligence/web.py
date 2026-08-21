"""Thin local HTTP and static-web boundary for podcast intelligence."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import Response

from podcast_intelligence.intelligence import (
    ANALYSIS_TYPE,
    EpisodeIntelligenceResult,
    analyze_episode,
    answer_episode_question,
)
from podcast_intelligence.intelligence_models import (
    ANALYSIS_PROMPT_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    SEGMENTER_VERSION,
    AnalysisEvidence,
    EpisodeAnalysis,
    EvidenceBackedItem,
)
from podcast_intelligence.persistence import (
    EpisodeStatus,
    PersistenceError,
    StoredEpisodeAnalysis,
    StoredTranscriptMetadata,
    TranscriptNotFoundError,
    TranscriptStore,
)
from podcast_intelligence.responses_client import (
    QuestionResponse,
    ResponsesClientError,
    ResponsesContextBudgetError,
    ResponsesIncompleteError,
    ResponsesOutputLimitError,
    ResponsesOutputValidationError,
    ResponsesToolLoopError,
    ResponseUsageSummary,
    ToolCallTrace,
)
from podcast_intelligence.settings import Settings

StoreFactory = Callable[[], AbstractContextManager[TranscriptStore]]
AnalyzeService = Callable[..., EpisodeIntelligenceResult]
QuestionService = Callable[..., QuestionResponse]


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(_ApiModel):
    code: str
    message: str


class ErrorEnvelope(_ApiModel):
    error: ErrorDetail


class EpisodeSummaryView(_ApiModel):
    episode_id: int
    transcript_run_id: int | None
    spotify_episode_id: str | None
    title: str
    latest_transcription_status: str | None
    transcript_available: bool
    analysis_available: bool
    latest_analysis_status: str | None


class EpisodeListResponse(_ApiModel):
    episodes: list[EpisodeSummaryView]


class TranscriptMetadataView(_ApiModel):
    transcript_run_id: int
    transcript_id: int
    episode_id: int
    feed_url: str
    rss_guid: str
    spotify_episode_id: str | None
    title: str
    transcription_model: str
    created_at: str


class EvidenceView(_ApiModel):
    segment_id: str
    quote: str


class EvidenceBackedItemView(_ApiModel):
    text: str
    evidence: list[EvidenceView]


class EpisodeAnalysisView(_ApiModel):
    summary: EvidenceBackedItemView
    topics: list[EvidenceBackedItemView]
    people: list[EvidenceBackedItemView]
    claims: list[EvidenceBackedItemView]
    actionable_insights: list[EvidenceBackedItemView]
    limitations: list[str]


class UsageView(_ApiModel):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class AnalysisResultView(_ApiModel):
    cache_status: Literal["cached", "created"]
    analysis_run_id: int
    response_id: str
    model: str
    created_at: str
    usage: UsageView
    analysis: EpisodeAnalysisView


class EpisodeDetailResponse(_ApiModel):
    episode: TranscriptMetadataView
    analysis: AnalysisResultView | None
    max_question_chars: int


class AnalysisRequest(_ApiModel):
    consent: Literal[True]
    refresh: bool = False


class QuestionRequest(_ApiModel):
    consent: Literal[True]
    question: str = Field(min_length=1)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question must not be empty")
        return normalized


class ToolTraceView(_ApiModel):
    response_id: str
    call_id: str
    tool_name: str
    result_segment_ids: list[str]


class QuestionResultView(_ApiModel):
    cache_status: Literal["not_persisted"] = "not_persisted"
    response_id: str
    response_ids: list[str]
    model: str
    answer: str
    insufficient_evidence: bool
    evidence: list[EvidenceView]
    usage: UsageView
    tool_calls: list[ToolTraceView]


_READ_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
    500: {"model": ErrorEnvelope},
}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_READ_ERRORS,
    502: {"model": ErrorEnvelope},
    503: {"model": ErrorEnvelope},
}


def create_app(
    *,
    settings: Settings | None = None,
    store_factory: StoreFactory | None = None,
    analyze_service: AnalyzeService = analyze_episode,
    question_service: QuestionService = answer_episode_question,
    static_directory: Path | None = None,
) -> FastAPI:
    """Create the local application with injectable offline boundaries."""

    runtime_settings = settings or Settings()
    make_store = store_factory or (lambda: TranscriptStore(runtime_settings.database_path))
    app = FastAPI(title="Podcast Intelligence", version="0.1.0")
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )

    @app.exception_handler(RequestValidationError)
    async def invalid_request_handler(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(422, "invalid_request", "The request is invalid.")

    @app.get(
        "/api/episodes",
        response_model=EpisodeListResponse,
        responses={500: {"model": ErrorEnvelope}},
    )
    def list_episodes() -> EpisodeListResponse:
        try:
            with make_store() as store:
                statuses = store.list_episode_statuses()
        except (OSError, PersistenceError, sqlite3.Error):
            raise _ApiFailure(
                500, "storage_failed", "Local podcast data could not be read."
            ) from None
        return EpisodeListResponse(episodes=[_episode_summary(status) for status in statuses])

    @app.get(
        "/api/episodes/{transcript_run_id}",
        response_model=EpisodeDetailResponse,
        responses=_READ_ERRORS,
    )
    def episode_detail(transcript_run_id: int) -> EpisodeDetailResponse:
        try:
            with make_store() as store:
                metadata = _metadata_or_not_found(store, transcript_run_id)
                cached = store.find_analysis_cache_for_run(
                    transcript_run_id,
                    analysis_type=ANALYSIS_TYPE,
                    model=runtime_settings.openai_model,
                    prompt_version=ANALYSIS_PROMPT_VERSION,
                    schema_version=ANALYSIS_SCHEMA_VERSION,
                    segmenter_version=SEGMENTER_VERSION,
                )
        except (OSError, PersistenceError, sqlite3.Error):
            raise _ApiFailure(
                500,
                "storage_failed",
                "Local podcast analysis could not be read.",
            ) from None
        return EpisodeDetailResponse(
            episode=_metadata_view(metadata),
            analysis=None if cached is None else _analysis_view(cached, "cached"),
            max_question_chars=runtime_settings.intelligence_max_query_chars,
        )

    @app.post(
        "/api/episodes/{transcript_run_id}/analysis",
        response_model=AnalysisResultView,
        responses=_WRITE_ERRORS,
    )
    def run_analysis(
        transcript_run_id: int,
        request: AnalysisRequest,
    ) -> AnalysisResultView:
        try:
            with make_store() as store:
                _metadata_or_not_found(store, transcript_run_id)
                result = analyze_service(
                    transcript_run_id,
                    settings=runtime_settings,
                    store=store,
                    refresh=request.refresh,
                )
        except ResponsesClientError as error:
            raise _analysis_api_failure(error) from None
        except ValueError:
            raise _ApiFailure(
                503,
                "provider_unavailable",
                "OpenAI is not configured for this operation.",
            ) from None
        except (OSError, PersistenceError, sqlite3.Error):
            raise _ApiFailure(500, "analysis_failed", "Podcast analysis failed safely.") from None
        cache_status: Literal["cached", "created"] = (
            "cached" if result.cache_status == "analysis" else "created"
        )
        return _analysis_view(result.analysis, cache_status)

    @app.post(
        "/api/episodes/{transcript_run_id}/questions",
        response_model=QuestionResultView,
        responses=_WRITE_ERRORS,
    )
    def ask_question(
        transcript_run_id: int,
        request: QuestionRequest,
    ) -> QuestionResultView:
        if len(request.question) > runtime_settings.intelligence_max_query_chars:
            raise _ApiFailure(422, "invalid_request", "The question exceeds the configured limit.")
        try:
            with make_store() as store:
                _metadata_or_not_found(store, transcript_run_id)
                result = question_service(
                    transcript_run_id,
                    request.question,
                    settings=runtime_settings,
                    store=store,
                )
        except ResponsesClientError as error:
            raise _question_api_failure(error) from None
        except ValueError:
            raise _ApiFailure(
                503,
                "provider_unavailable",
                "OpenAI is not configured for this operation.",
            ) from None
        except (OSError, PersistenceError, sqlite3.Error):
            raise _ApiFailure(500, "question_failed", "Question answering failed safely.") from None
        return _question_view(result)

    @app.exception_handler(_ApiFailure)
    async def api_failure_handler(_request: Request, error: _ApiFailure) -> JSONResponse:
        return _error_response(error.status_code, error.code, error.message)

    web_root = static_directory or Path(__file__).with_name("static_ui")
    index = web_root / "index.html"
    assets = web_root / "assets"
    if index.is_file():
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def serve_frontend(path: str) -> Response:
            if path.startswith("api/"):
                return _error_response(404, "not_found", "The requested API route was not found.")
            return FileResponse(index)

    return app


class _ApiFailure(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _analysis_api_failure(error: ResponsesClientError) -> _ApiFailure:
    if isinstance(error, ResponsesContextBudgetError):
        return _ApiFailure(
            422,
            "analysis_context_too_large",
            "The transcript exceeds the configured analysis context limit.",
        )
    if isinstance(error, ResponsesOutputValidationError):
        return _ApiFailure(
            502,
            "analysis_output_invalid",
            (
                "OpenAI completed the request, but its analysis could not be safely "
                "validated. Try again."
            ),
        )
    if isinstance(error, ResponsesOutputLimitError):
        return _ApiFailure(
            502,
            "analysis_output_limit",
            "OpenAI reached the podcast analysis token limit. Try again.",
        )
    if isinstance(error, ResponsesIncompleteError):
        return _ApiFailure(
            502,
            "provider_incomplete",
            "OpenAI did not complete the podcast analysis. Try again.",
        )
    return _ApiFailure(502, "provider_failed", "The OpenAI analysis request failed safely.")


def _question_api_failure(error: ResponsesClientError) -> _ApiFailure:
    if isinstance(error, (ResponsesOutputValidationError, ResponsesToolLoopError)):
        return _ApiFailure(
            502,
            "answer_output_invalid",
            (
                "OpenAI completed the request, but its answer could not be safely "
                "validated. Try again."
            ),
        )
    if isinstance(error, ResponsesOutputLimitError):
        return _ApiFailure(
            502,
            "answer_output_limit",
            "OpenAI reached the answer token limit. Try a narrower question.",
        )
    if isinstance(error, ResponsesIncompleteError):
        return _ApiFailure(
            502,
            "provider_incomplete",
            "OpenAI did not complete the answer. Try again.",
        )
    return _ApiFailure(502, "provider_failed", "Question answering failed safely.")


def _metadata_or_not_found(store: TranscriptStore, run_id: int) -> StoredTranscriptMetadata:
    try:
        return store.get_transcript_metadata(run_id)
    except TranscriptNotFoundError:
        raise _ApiFailure(
            404, "episode_not_found", "The selected transcript was not found."
        ) from None


def _episode_summary(status: EpisodeStatus) -> EpisodeSummaryView:
    return EpisodeSummaryView(
        episode_id=status.episode_id,
        transcript_run_id=status.transcript_run_id,
        spotify_episode_id=status.spotify_episode_id,
        title=status.title,
        latest_transcription_status=status.latest_transcription_status,
        transcript_available=status.transcript_run_id is not None,
        analysis_available=status.analysis_available,
        latest_analysis_status=status.latest_analysis_status,
    )


def _metadata_view(metadata: StoredTranscriptMetadata) -> TranscriptMetadataView:
    return TranscriptMetadataView(
        transcript_run_id=metadata.run_id,
        transcript_id=metadata.transcript_id,
        episode_id=metadata.episode_id,
        feed_url=metadata.feed_url,
        rss_guid=metadata.rss_guid,
        spotify_episode_id=metadata.spotify_episode_id,
        title=metadata.episode_title,
        transcription_model=metadata.transcription_model,
        created_at=metadata.created_at,
    )


def _evidence_view(evidence: AnalysisEvidence) -> EvidenceView:
    return EvidenceView(segment_id=evidence.segment_id, quote=evidence.quote)


def _item_view(item: EvidenceBackedItem) -> EvidenceBackedItemView:
    return EvidenceBackedItemView(
        text=item.text,
        evidence=[_evidence_view(evidence) for evidence in item.evidence],
    )


def _episode_analysis_view(analysis: EpisodeAnalysis) -> EpisodeAnalysisView:
    return EpisodeAnalysisView(
        summary=_item_view(analysis.summary),
        topics=[_item_view(item) for item in analysis.topics],
        people=[_item_view(item) for item in analysis.people],
        claims=[_item_view(item) for item in analysis.claims],
        actionable_insights=[_item_view(item) for item in analysis.actionable_insights],
        limitations=list(analysis.limitations),
    )


def _analysis_view(
    analysis: StoredEpisodeAnalysis,
    cache_status: Literal["cached", "created"],
) -> AnalysisResultView:
    return AnalysisResultView(
        cache_status=cache_status,
        analysis_run_id=analysis.run_id,
        response_id=analysis.response_id,
        model=analysis.model,
        created_at=analysis.created_at,
        usage=UsageView(
            input_tokens=analysis.input_tokens,
            output_tokens=analysis.output_tokens,
            total_tokens=analysis.total_tokens,
        ),
        analysis=_episode_analysis_view(analysis.analysis),
    )


def _usage_view(usage: ResponseUsageSummary) -> UsageView:
    return UsageView(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )


def _trace_view(trace: ToolCallTrace) -> ToolTraceView:
    return ToolTraceView(
        response_id=trace.response_id,
        call_id=trace.call_id,
        tool_name=trace.tool_name,
        result_segment_ids=list(trace.result_segment_ids),
    )


def _question_view(response: QuestionResponse) -> QuestionResultView:
    return QuestionResultView(
        response_id=response.response_id,
        response_ids=list(response.response_ids),
        model=response.model,
        answer=response.answer.answer,
        insufficient_evidence=response.answer.insufficient_evidence,
        evidence=[_evidence_view(evidence) for evidence in response.answer.evidence],
        usage=_usage_view(response.usage),
        tool_calls=[_trace_view(trace) for trace in response.tool_calls],
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    envelope = ErrorEnvelope(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def main() -> None:
    """Serve the packaged local application on loopback only."""

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
