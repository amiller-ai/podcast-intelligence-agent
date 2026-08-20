import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from podcast_intelligence.audio_transcription import (
    AudioTranscriber,
    AudioTranscriptionPolicy,
    AudioTranscriptionProviderError,
    EpisodeTranscript,
    ProviderTranscript,
    ProviderTranscriptPart,
)
from podcast_intelligence.episode_resolution import (
    ResolvedSpotifyEpisode,
    resolve_transcript_sources,
)
from podcast_intelligence.models import PodcastEpisode
from podcast_intelligence.persistence import TranscriptStore
from podcast_intelligence.pipeline import ingest_spotify_episode
from podcast_intelligence.settings import Settings

_SPOTIFY_URL = "https://open.spotify.com/episode/0VPwvReM2olZDWl3YOHfqh"


def _resolved(*, audio_url: str = "https://cdn.example.test/episode.mp3") -> ResolvedSpotifyEpisode:
    episode = PodcastEpisode(
        episode_id="episode-guid",
        title="Synthetic pipeline episode",
        published_at=datetime(2026, 8, 19, tzinfo=UTC),
        audio_url=audio_url,
        audio_media_type="audio/mpeg",
        audio_size_bytes=100,
        duration_seconds=60,
    )
    return ResolvedSpotifyEpisode(
        spotify_episode_id="0VPwvReM2olZDWl3YOHfqh",
        spotify_url=_SPOTIFY_URL,
        show_title="Synthetic show",
        feed_url="https://publisher.example.test/feed.xml",
        catalog_url=None,
        catalog_episode_guid=episode.episode_id,
        episode=episode,
        transcript=resolve_transcript_sources(episode),
    )


class RecordingProvider:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.calls = 0
        self.failure = failure

    def transcribe(
        self,
        audio_path: Path,
        *,
        media_type: str,
        duration_seconds: int,
    ) -> ProviderTranscript:
        del media_type, duration_seconds
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        marker = audio_path.read_bytes().decode()
        text = f"Transcript for {marker}."
        part = ProviderTranscriptPart(
            ordinal=0,
            text=text,
            request_id=f"req_{self.calls}",
            model="gpt-transcribe",
            language="en",
        )
        return ProviderTranscript(
            text=text,
            provider="openai",
            model="gpt-transcribe",
            request_ids=(part.request_id,),  # type: ignore[arg-type]
            language="en",
            chunk_count=1,
            parts=(part,),
        )


class SyntheticAudioPipeline:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.calls = 0
        self.policies: list[AudioTranscriptionPolicy | None] = []

    def __call__(
        self,
        episode: PodcastEpisode,
        *,
        authorized: bool,
        estimate_cost: Callable[[int], Decimal],
        transcriber: AudioTranscriber,
        policy: AudioTranscriptionPolicy | None = None,
    ) -> EpisodeTranscript:
        assert authorized is True
        self.calls += 1
        self.policies.append(policy)
        payload = episode.audio_url.rsplit("/", maxsplit=1)[-1].encode()  # type: ignore[union-attr]
        path = self.tmp_path / f"audio-{self.calls}.mp3"
        path.write_bytes(payload)
        provider_result = transcriber.transcribe(
            path,
            media_type="audio/mpeg",
            duration_seconds=episode.duration_seconds or 0,
        )
        return EpisodeTranscript(
            episode_id=episode.episode_id,
            source_url=episode.audio_url or "",
            source_media_type="audio/mpeg",
            duration_seconds=episode.duration_seconds or 0,
            audio_bytes=len(payload),
            audio_sha256=sha256(payload).hexdigest(),
            etag='"stable-etag"',
            last_modified="Wed, 19 Aug 2026 12:00:00 GMT",
            estimated_cost_usd=estimate_cost(episode.duration_seconds or 0),
            transcript=provider_result,
        )


def _settings(path: Path) -> Settings:
    return Settings.model_validate({"openai_api_key": "test-key", "database_path": path})


def test_pipeline_miss_then_source_cache_skips_audio_and_provider(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.db"
    settings = _settings(path)
    provider = RecordingProvider()
    audio = SyntheticAudioPipeline(tmp_path)
    override_policy = AudioTranscriptionPolicy(max_audio_bytes=150_000_000)
    resolver_calls = 0

    def resolver(_url: str) -> ResolvedSpotifyEpisode:
        nonlocal resolver_calls
        resolver_calls += 1
        return _resolved()

    with TranscriptStore(path) as store:
        first = ingest_spotify_episode(
            _SPOTIFY_URL,
            settings=settings,
            authorized=True,
            store=store,
            episode_resolver=resolver,
            provider_transcriber=provider,
            audio_pipeline=audio,
            audio_policy=override_policy,
        )
        second = ingest_spotify_episode(
            _SPOTIFY_URL,
            settings=settings,
            authorized=True,
            store=store,
            episode_resolver=resolver,
            provider_transcriber=provider,
            audio_pipeline=audio,
        )

    assert first.cache_status == "miss"
    assert second.cache_status == "source"
    assert second.transcript.run_id == first.transcript.run_id
    assert second.transcript.text == first.transcript.text
    assert audio.calls == 1
    assert provider.calls == 1
    assert resolver_calls == 2
    assert audio.policies == [override_policy]


def test_refresh_retrieves_audio_but_reuses_definitive_identity(tmp_path: Path) -> None:
    path = tmp_path / "refresh.db"
    settings = _settings(path)
    provider = RecordingProvider()
    audio = SyntheticAudioPipeline(tmp_path)

    with TranscriptStore(path) as store:
        first = ingest_spotify_episode(
            _SPOTIFY_URL,
            settings=settings,
            authorized=True,
            store=store,
            episode_resolver=lambda _url: _resolved(),
            provider_transcriber=provider,
            audio_pipeline=audio,
        )
        refreshed = ingest_spotify_episode(
            _SPOTIFY_URL,
            settings=settings,
            authorized=True,
            refresh=True,
            store=store,
            episode_resolver=lambda _url: _resolved(),
            provider_transcriber=provider,
            audio_pipeline=audio,
        )
        history = store.list_transcripts()

    assert first.cache_status == "miss"
    assert refreshed.cache_status == "audio"
    assert refreshed.transcript.run_id != first.transcript.run_id
    assert refreshed.transcript.audio_sha256 == first.transcript.audio_sha256
    assert len(history) == 2
    assert audio.calls == 2
    assert provider.calls == 1


def test_refresh_with_new_audio_identity_preserves_history_and_transcribes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "new-source.db"
    settings = _settings(path)
    provider = RecordingProvider()
    audio = SyntheticAudioPipeline(tmp_path)

    with TranscriptStore(path) as store:
        first = ingest_spotify_episode(
            _SPOTIFY_URL,
            settings=settings,
            authorized=True,
            store=store,
            episode_resolver=lambda _url: _resolved(audio_url="https://cdn.example.test/one.mp3"),
            provider_transcriber=provider,
            audio_pipeline=audio,
        )
        second = ingest_spotify_episode(
            _SPOTIFY_URL,
            settings=settings,
            authorized=True,
            refresh=True,
            store=store,
            episode_resolver=lambda _url: _resolved(audio_url="https://cdn.example.test/two.mp3"),
            provider_transcriber=provider,
            audio_pipeline=audio,
        )
        history = store.list_transcripts()

    assert first.transcript.text == "Transcript for one.mp3."
    assert second.transcript.text == "Transcript for two.mp3."
    assert second.cache_status == "miss"
    assert len(history) == 2
    assert provider.calls == audio.calls == 2


def test_pipeline_records_application_failure_without_partial_content(tmp_path: Path) -> None:
    path = tmp_path / "provider-failure.db"
    settings = _settings(path)
    provider = RecordingProvider(failure=AudioTranscriptionProviderError("provider request failed"))

    with (
        TranscriptStore(path) as store,
        pytest.raises(AudioTranscriptionProviderError, match="provider request failed"),
    ):
        ingest_spotify_episode(
            _SPOTIFY_URL,
            settings=settings,
            authorized=True,
            store=store,
            episode_resolver=lambda _url: _resolved(),
            provider_transcriber=provider,
            audio_pipeline=SyntheticAudioPipeline(tmp_path),
        )

    with sqlite3.connect(path) as connection:
        run = connection.execute(
            "SELECT status, error_code, error_message FROM transcription_runs"
        ).fetchone()
        transcript_count = connection.execute("SELECT COUNT(*) FROM transcripts").fetchone()
    assert run == (
        "failed",
        "AudioTranscriptionProviderError",
        "provider request failed",
    )
    assert transcript_count == (0,)


def test_pipeline_redacts_unknown_failure_message(tmp_path: Path) -> None:
    path = tmp_path / "unexpected-failure.db"
    settings = _settings(path)

    def failing_audio(*_args: object, **_kwargs: object) -> EpisodeTranscript:
        raise RuntimeError("raw secret content")

    with (
        TranscriptStore(path) as store,
        pytest.raises(RuntimeError, match="raw secret content"),
    ):
        ingest_spotify_episode(
            _SPOTIFY_URL,
            settings=settings,
            authorized=True,
            store=store,
            episode_resolver=lambda _url: _resolved(),
            provider_transcriber=RecordingProvider(),
            audio_pipeline=failing_audio,
        )

    with sqlite3.connect(path) as connection:
        message = connection.execute("SELECT error_message FROM transcription_runs").fetchone()
    assert message == ("unexpected pipeline failure",)
