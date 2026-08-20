"""Explicit live tests for the authorized, persisted podcast transcription pipeline."""

import os

import pytest

from podcast_intelligence.audio_transcription import AudioTranscriptionPolicy
from podcast_intelligence.persistence import TranscriptStore
from podcast_intelligence.pipeline import ingest_spotify_episode
from podcast_intelligence.settings import Settings

_SPOTIFY_URLS = (
    "https://open.spotify.com/episode/0VPwvReM2olZDWl3YOHfqh?si=b163e695da3a4574",
    "https://open.spotify.com/episode/7HH9LCznGvLZHYYuXaVOd9?si=7fe103599b4f4fce",
)
_AUTHORIZED_LIVE_POLICY = AudioTranscriptionPolicy(max_audio_bytes=150_000_000)


@pytest.mark.integration
def test_live_spotify_episodes_are_transcribed_persisted_and_reused() -> None:
    if os.environ.get("RUN_LIVE_PODCAST_TRANSCRIPTION") != "1":
        pytest.skip("set RUN_LIVE_PODCAST_TRANSCRIPTION=1 for the authorized live test")

    settings = Settings()
    with TranscriptStore(settings.database_path) as store:
        first_results = tuple(
            ingest_spotify_episode(
                spotify_url,
                settings=settings,
                authorized=True,
                store=store,
                audio_policy=_AUTHORIZED_LIVE_POLICY,
            )
            for spotify_url in _SPOTIFY_URLS
        )
        cached_results = tuple(
            ingest_spotify_episode(
                spotify_url,
                settings=settings,
                authorized=True,
                store=store,
                audio_policy=_AUTHORIZED_LIVE_POLICY,
            )
            for spotify_url in _SPOTIFY_URLS
        )
        persisted = store.list_transcripts()

    assert {result.transcript.spotify_episode_id for result in first_results} == {
        "0VPwvReM2olZDWl3YOHfqh",
        "7HH9LCznGvLZHYYuXaVOd9",
    }
    assert all(result.transcript.text.strip() for result in first_results)
    assert all(result.transcript.audio_bytes > 0 for result in first_results)
    assert all(result.transcript.provider_transcript.request_ids for result in first_results)
    assert all(result.cache_status == "source" for result in cached_results)
    assert tuple(result.transcript.run_id for result in cached_results) == tuple(
        result.transcript.run_id for result in first_results
    )
    assert {transcript.spotify_episode_id for transcript in persisted} >= {
        "0VPwvReM2olZDWl3YOHfqh",
        "7HH9LCznGvLZHYYuXaVOd9",
    }
