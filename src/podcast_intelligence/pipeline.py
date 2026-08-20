"""Resumable Spotify-to-transcript pipeline backed by SQLite."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from podcast_intelligence.audio_transcription import (
    AudioTranscriber,
    AudioTranscriptionPolicy,
    EpisodeTranscript,
    ProviderTranscript,
    transcribe_episode_audio,
)
from podcast_intelligence.episode_resolution import (
    ResolvedSpotifyEpisode,
    resolve_spotify_episode,
)
from podcast_intelligence.openai_transcription import (
    OpenAIAudioTranscriber,
    estimate_openai_transcription_cost,
)
from podcast_intelligence.persistence import (
    CHUNKER_VERSION,
    TRANSCRIPTION_PROMPT_VERSION,
    EpisodeRecord,
    StoredTranscript,
    TranscriptStore,
    dollars_to_microusd,
)
from podcast_intelligence.settings import Settings

type EpisodeResolver = Callable[[str], ResolvedSpotifyEpisode]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Canonical persisted result and how it was obtained."""

    transcript: StoredTranscript
    cache_status: str


class _DefinitiveCacheTranscriber:
    def __init__(
        self,
        *,
        store: TranscriptStore,
        episode: EpisodeRecord,
        model: str,
        delegate: AudioTranscriber,
    ) -> None:
        self._store = store
        self._episode = episode
        self._model = model
        self._delegate = delegate
        self.cache_hit = False

    def transcribe(
        self,
        audio_path: Path,
        *,
        media_type: str,
        duration_seconds: int,
    ) -> ProviderTranscript:
        audio_sha256 = _file_sha256(audio_path)
        cached = self._store.find_audio_cache(
            self._episode.id,
            audio_sha256=audio_sha256,
            model=self._model,
            chunker_version=CHUNKER_VERSION,
            prompt_version=TRANSCRIPTION_PROMPT_VERSION,
        )
        if cached is not None:
            self.cache_hit = True
            return cached.provider_transcript
        return self._delegate.transcribe(
            audio_path,
            media_type=media_type,
            duration_seconds=duration_seconds,
        )


def ingest_spotify_episode(
    spotify_url: str,
    *,
    settings: Settings,
    authorized: bool,
    refresh: bool = False,
    store: TranscriptStore | None = None,
    episode_resolver: EpisodeResolver = resolve_spotify_episode,
    provider_transcriber: AudioTranscriber | None = None,
    audio_pipeline: Callable[..., EpisodeTranscript] = transcribe_episode_audio,
    audio_policy: AudioTranscriptionPolicy | None = None,
) -> PipelineResult:
    """Resolve, cache, transcribe when needed, and atomically persist one episode."""

    owned_store = store is None
    active_store = store or TranscriptStore(settings.database_path)
    try:
        resolved = episode_resolver(spotify_url)
        episode = active_store.upsert_episode(resolved)
        if not refresh:
            cached = active_store.find_source_cache(
                episode,
                model=settings.openai_transcription_model,
                chunker_version=CHUNKER_VERSION,
                prompt_version=TRANSCRIPTION_PROMPT_VERSION,
            )
            if cached is not None:
                return PipelineResult(transcript=cached, cache_status="source")

        estimate = estimate_openai_transcription_cost(
            resolved.episode.duration_seconds or 0,
            price_per_minute_usd=settings.openai_transcription_cost_per_minute_usd,
        )
        run_id = active_store.create_run(
            episode,
            provider="openai",
            model=settings.openai_transcription_model,
            chunker_version=CHUNKER_VERSION,
            prompt_version=TRANSCRIPTION_PROMPT_VERSION,
            estimated_cost_microusd=dollars_to_microusd(estimate),
        )
        active_store.mark_running(run_id)
        definitive_cache = _DefinitiveCacheTranscriber(
            store=active_store,
            episode=episode,
            model=settings.openai_transcription_model,
            delegate=provider_transcriber or OpenAIAudioTranscriber(settings),
        )
        try:
            audio_arguments: dict[str, object] = {
                "authorized": authorized,
                "estimate_cost": lambda duration: estimate_openai_transcription_cost(
                    duration,
                    price_per_minute_usd=settings.openai_transcription_cost_per_minute_usd,
                ),
                "transcriber": definitive_cache,
            }
            if audio_policy is not None:
                audio_arguments["policy"] = audio_policy
            result = audio_pipeline(resolved.episode, **audio_arguments)
            stored = active_store.persist_success(
                run_id,
                episode,
                result,
                chunker_version=CHUNKER_VERSION,
                prompt_version=TRANSCRIPTION_PROMPT_VERSION,
            )
        except Exception as error:
            active_store.mark_failed(
                run_id,
                error_code=type(error).__name__,
                safe_message=_safe_failure_message(error),
            )
            raise
        return PipelineResult(
            transcript=stored,
            cache_status="audio" if definitive_cache.cache_hit else "miss",
        )
    finally:
        if owned_store:
            active_store.close()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_failure_message(error: Exception) -> str:
    module = type(error).__module__
    if module.startswith("podcast_intelligence"):
        return str(error)[:500]
    return "unexpected pipeline failure"
