"""Explicit live test for the user-approved Spotify podcast transcription path."""

import os
from decimal import Decimal

import pytest

from podcast_intelligence.audio_transcription import transcribe_episode_audio
from podcast_intelligence.episode_resolution import resolve_spotify_episode
from podcast_intelligence.openai_transcription import (
    OpenAIAudioTranscriber,
    estimate_openai_transcription_cost,
)
from podcast_intelligence.settings import Settings

_SPOTIFY_URL = "https://open.spotify.com/episode/0VPwvReM2olZDWl3YOHfqh?si=ec6ef828091a4fae"


@pytest.mark.integration
def test_live_spotify_episode_audio_transcription() -> None:
    if os.environ.get("RUN_LIVE_PODCAST_TRANSCRIPTION") != "1":
        pytest.skip("set RUN_LIVE_PODCAST_TRANSCRIPTION=1 for the authorized live test")

    settings = Settings()
    resolved = resolve_spotify_episode(_SPOTIFY_URL)
    transcript = transcribe_episode_audio(
        resolved.episode,
        authorized=True,
        estimate_cost=lambda duration: estimate_openai_transcription_cost(
            duration,
            price_per_minute_usd=settings.openai_transcription_cost_per_minute_usd,
        ),
        transcriber=OpenAIAudioTranscriber(settings),
    )

    assert resolved.spotify_episode_id == "0VPwvReM2olZDWl3YOHfqh"
    assert transcript.audio_bytes > 25_000_000
    assert transcript.estimated_cost_usd <= Decimal("1.00")
    assert transcript.transcript.provider == "openai"
    assert transcript.transcript.model == settings.openai_transcription_model
    assert transcript.transcript.chunk_count > 1
    assert len(transcript.transcript.request_ids) == transcript.transcript.chunk_count
    assert bool(transcript.transcript.text.strip())
