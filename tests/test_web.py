from __future__ import annotations

from pathlib import Path
from typing import Self, cast

from fastapi.testclient import TestClient

from podcast_intelligence.intelligence import EpisodeIntelligenceResult
from podcast_intelligence.intelligence_models import (
    AnalysisEvidence,
    EpisodeAnalysis,
    EvidenceBackedItem,
    QuestionAnswer,
)
from podcast_intelligence.persistence import (
    EpisodeStatus,
    PersistenceError,
    StoredEpisodeAnalysis,
    StoredTranscriptMetadata,
    TranscriptNotFoundError,
)
from podcast_intelligence.responses_client import (
    QuestionResponse,
    ResponsesClientError,
    ResponseUsageSummary,
    ToolCallTrace,
)
from podcast_intelligence.settings import Settings
from podcast_intelligence.web import StoreFactory, create_app

_SEGMENT_ID = "a" * 64


def _analysis() -> StoredEpisodeAnalysis:
    evidence = [AnalysisEvidence(segment_id=_SEGMENT_ID, quote="Exact quote")]
    item = EvidenceBackedItem(text="Evidence-backed result.", evidence=evidence)
    return StoredEpisodeAnalysis(
        run_id=17,
        analysis_id=19,
        transcript_id=11,
        cache_identity="e" * 64,
        analysis_type="episode_intelligence",
        model="gpt-5.6-sol",
        prompt_version="episode-intelligence-v2",
        schema_version="1",
        segmenter_version="bounded-text-v1",
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
            limitations=["No speaker timing."],
        ),
        created_at="2026-08-20T00:01:00+00:00",
    )


def _question() -> QuestionResponse:
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
                arguments_json='{"query":"SENSITIVE TOOL ARGUMENT"}',
                result_segment_ids=(_SEGMENT_ID,),
            ),
        ),
        usage=ResponseUsageSummary(input_tokens=20, output_tokens=10, total_tokens=30),
    )


class _FakeStore:
    def __init__(self, *, cached: StoredEpisodeAnalysis | None = None) -> None:
        self.cached = cached
        self.metadata_calls = 0
        self.transcript_calls = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def list_episode_statuses(self) -> tuple[EpisodeStatus, ...]:
        return (
            EpisodeStatus(
                episode_id=13,
                spotify_episode_id="spotify-episode",
                title="Synthetic episode",
                latest_transcription_status="succeeded",
                transcript_run_id=7,
                analysis_available=self.cached is not None,
                latest_analysis_status="succeeded" if self.cached else None,
            ),
        )

    def get_transcript_metadata(self, run_id: int) -> StoredTranscriptMetadata:
        self.metadata_calls += 1
        if run_id != 7:
            raise TranscriptNotFoundError("not found")
        return StoredTranscriptMetadata(
            run_id=7,
            transcript_id=11,
            episode_id=13,
            feed_url="https://example.test/feed.xml",
            rss_guid="guid-1",
            spotify_episode_id="spotify-episode",
            episode_title="Synthetic episode",
            transcription_model="gpt-transcribe",
            created_at="2026-08-20T00:00:00+00:00",
        )

    def find_analysis_cache_for_run(
        self, *_args: object, **_kwargs: object
    ) -> StoredEpisodeAnalysis | None:
        return self.cached

    def get_transcript(self, _run_id: int) -> None:
        self.transcript_calls += 1
        raise AssertionError("web reads must not load raw transcript content")


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, database_path=tmp_path / "web.db")


def _store_factory(store: _FakeStore) -> StoreFactory:
    return cast(StoreFactory, lambda: store)


def test_episode_list_and_detail_do_not_load_or_expose_transcript(
    tmp_path: Path,
) -> None:
    store = _FakeStore(cached=_analysis())
    app = create_app(settings=_settings(tmp_path), store_factory=_store_factory(store))
    client = TestClient(app)

    listing = client.get("/api/episodes")
    detail = client.get("/api/episodes/7")

    assert listing.status_code == 200
    assert listing.json()["episodes"][0]["transcript_available"] is True
    assert detail.status_code == 200
    assert detail.json()["episode"]["transcript_run_id"] == 7
    assert detail.json()["max_question_chars"] == 400
    assert detail.json()["analysis"]["cache_status"] == "cached"
    assert detail.json()["analysis"]["analysis"]["summary"]["evidence"][0] == {
        "segment_id": _SEGMENT_ID,
        "quote": "Exact quote",
    }
    assert "provider_transcript" not in detail.text
    assert "SENSITIVE FULL TRANSCRIPT" not in detail.text
    assert store.transcript_calls == 0


def test_unknown_detail_returns_safe_not_found(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(tmp_path),
        store_factory=_store_factory(_FakeStore()),
    )

    response = TestClient(app).get("/api/episodes/999")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "episode_not_found",
            "message": "The selected transcript was not found.",
        }
    }


def test_analysis_requires_literal_server_validated_consent(tmp_path: Path) -> None:
    called = False

    def analyze(*_args: object, **_kwargs: object) -> EpisodeIntelligenceResult:
        nonlocal called
        called = True
        raise AssertionError("analysis must not run")

    app = create_app(
        settings=_settings(tmp_path),
        store_factory=_store_factory(_FakeStore()),
        analyze_service=analyze,
    )
    client = TestClient(app)

    missing = client.post("/api/episodes/7/analysis", json={})
    rejected = client.post("/api/episodes/7/analysis", json={"consent": False})

    assert missing.status_code == 422
    assert rejected.status_code == 422
    assert missing.json()["error"]["code"] == "invalid_request"
    assert called is False


def test_analysis_maps_cache_status_and_refresh(tmp_path: Path) -> None:
    captured: list[bool] = []

    def analyze(*_args: object, **kwargs: object) -> EpisodeIntelligenceResult:
        captured.append(bool(kwargs["refresh"]))
        return EpisodeIntelligenceResult(analysis=_analysis(), cache_status="miss")

    app = create_app(
        settings=_settings(tmp_path),
        store_factory=_store_factory(_FakeStore()),
        analyze_service=analyze,
    )

    response = TestClient(app).post(
        "/api/episodes/7/analysis",
        json={"consent": True, "refresh": True},
    )

    assert response.status_code == 200
    assert response.json()["cache_status"] == "created"
    assert captured == [True]


def test_question_returns_evidence_and_safe_trace_without_arguments(tmp_path: Path) -> None:
    def question(*_args: object, **_kwargs: object) -> QuestionResponse:
        return _question()

    app = create_app(
        settings=_settings(tmp_path),
        store_factory=_store_factory(_FakeStore()),
        question_service=question,
    )

    response = TestClient(app).post(
        "/api/episodes/7/questions",
        json={"consent": True, "question": "What happened?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Supported answer."
    assert payload["evidence"][0]["segment_id"] == _SEGMENT_ID
    assert payload["tool_calls"][0]["tool_name"] == "search_transcript"
    assert "arguments_json" not in response.text
    assert "SENSITIVE TOOL ARGUMENT" not in response.text
    assert "output_item_types" not in response.text


def test_provider_errors_are_redacted(tmp_path: Path) -> None:
    def question(*_args: object, **_kwargs: object) -> QuestionResponse:
        raise ResponsesClientError("SENSITIVE PROVIDER DETAIL")

    app = create_app(
        settings=_settings(tmp_path),
        store_factory=_store_factory(_FakeStore()),
        question_service=question,
    )

    response = TestClient(app).post(
        "/api/episodes/7/questions",
        json={"consent": True, "question": "What happened?"},
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_failed"
    assert "SENSITIVE PROVIDER DETAIL" not in response.text


def test_openapi_contains_only_the_public_contract(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(tmp_path),
        store_factory=_store_factory(_FakeStore()),
    )

    schema = app.openapi()
    serialized = str(schema)

    assert set(schema["paths"]) == {
        "/api/episodes",
        "/api/episodes/{transcript_run_id}",
        "/api/episodes/{transcript_run_id}/analysis",
        "/api/episodes/{transcript_run_id}/questions",
    }
    assert "provider_transcript" not in serialized
    assert "arguments_json" not in serialized


def test_static_frontend_is_same_origin_and_api_fallback_stays_json(
    tmp_path: Path,
) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<main>Podcast Intelligence</main>", encoding="utf-8")
    app = create_app(
        settings=_settings(tmp_path),
        store_factory=_store_factory(_FakeStore()),
        static_directory=static,
    )
    client = TestClient(app)

    root = client.get("/")
    nested = client.get("/episodes/7")
    missing_api = client.get("/api/not-a-route")

    assert root.status_code == 200
    assert nested.text == "<main>Podcast Intelligence</main>"
    assert missing_api.status_code == 404
    assert missing_api.json()["error"]["code"] == "not_found"


def test_rejects_untrusted_hosts(tmp_path: Path) -> None:
    app = create_app(
        settings=_settings(tmp_path),
        store_factory=_store_factory(_FakeStore()),
    )

    response = TestClient(app).get(
        "/api/episodes",
        headers={"host": "attacker.example"},
    )

    assert response.status_code == 400
    assert "invalid host" in response.text.lower()


def test_unexpected_store_failures_return_safe_json(tmp_path: Path) -> None:
    def failing_store() -> object:
        raise PersistenceError("SENSITIVE SQLITE DETAIL")

    app = create_app(
        settings=_settings(tmp_path),
        store_factory=cast(StoreFactory, failing_store),
    )
    client = TestClient(app, raise_server_exceptions=False)

    responses = [
        client.get("/api/episodes"),
        client.get("/api/episodes/7"),
        client.post("/api/episodes/7/analysis", json={"consent": True}),
        client.post(
            "/api/episodes/7/questions",
            json={"consent": True, "question": "What happened?"},
        ),
    ]

    assert all(response.status_code == 500 for response in responses)
    assert [response.json()["error"]["code"] for response in responses] == [
        "storage_failed",
        "storage_failed",
        "analysis_failed",
        "question_failed",
    ]
    assert all("SENSITIVE SQLITE DETAIL" not in response.text for response in responses)
