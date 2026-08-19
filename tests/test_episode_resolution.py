from collections.abc import Callable

import httpx2
import pytest

from podcast_intelligence.episode_resolution import (
    EpisodeMatchError,
    EpisodeMetadataError,
    SpotifyEpisodeUrlError,
    TranscriptSource,
    TranscriptSourceStatus,
    resolve_spotify_episode,
    resolve_transcript_sources,
)
from podcast_intelligence.models import PodcastEpisode, TranscriptReference

_SPOTIFY_ID = "7HH9LCznGvLZHYYuXaVOd9"
_EPISODE_TITLE = "Anthropic's $2T IPO, Zuck's AI Manifesto"
_EPISODE_GUID = "episode-guid-285"
_FEED_URL = "https://feeds.example.test/show.xml"


def _public_resolver(_host: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def _transport(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.MockTransport:
    return httpx2.MockTransport(handler)


def _catalog_result(
    *,
    title: str = _EPISODE_TITLE,
    guid: str = _EPISODE_GUID,
    feed_url: str = _FEED_URL,
) -> dict[str, object]:
    return {
        "trackName": title,
        "collectionName": "Example Show",
        "feedUrl": feed_url,
        "episodeGuid": guid,
        "trackViewUrl": "https://podcasts.apple.com/episode/285",
        "releaseDate": "2026-08-14T20:11:00Z",
        "trackTimeMillis": 5_970_000,
    }


def _metadata_transport(
    *,
    spotify_payload: object | None = None,
    catalog_results: list[object] | None = None,
    requests: list[httpx2.Request] | None = None,
) -> httpx2.MockTransport:
    resolved_spotify_payload = (
        {"title": _EPISODE_TITLE} if spotify_payload is None else spotify_payload
    )
    resolved_catalog_results = [_catalog_result()] if catalog_results is None else catalog_results

    def handler(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path == "/oembed":
            return httpx2.Response(200, json=resolved_spotify_payload)
        if request.url.path == "/search":
            return httpx2.Response(200, json={"results": resolved_catalog_results})
        raise AssertionError(f"unexpected metadata request: {request.url}")

    return _transport(handler)


def _rss_transport(
    *,
    guid: str = _EPISODE_GUID,
    transcript_element: str = (
        '<podcast:transcript url="https://cdn.example.test/episode.vtt" '
        'type="text/vtt" language="en" />'
    ),
    include_audio: bool = True,
) -> httpx2.MockTransport:
    enclosure = (
        '<enclosure url="https://cdn.example.test/episode.mp3" type="audio/mpeg" />'
        if include_audio
        else ""
    )
    xml = f"""<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
    <channel><title>Example Show</title><item><guid>{guid}</guid>
    <title>{_EPISODE_TITLE}</title><link>https://publisher.example.test/285</link>
    {enclosure}{transcript_element}</item></channel></rss>"""
    return _transport(
        lambda _request: httpx2.Response(
            200,
            headers={"Content-Type": "application/rss+xml"},
            content=xml.encode(),
        )
    )


def test_resolve_spotify_episode_verifies_rss_guid_and_prefers_rss_transcript() -> None:
    metadata_requests: list[httpx2.Request] = []

    resolved = resolve_spotify_episode(
        f"https://open.spotify.com/episode/{_SPOTIFY_ID}?si=tracking-value",
        metadata_transport=_metadata_transport(
            catalog_results=[
                _catalog_result(title="A different episode", guid="different-guid"),
                _catalog_result(),
            ],
            requests=metadata_requests,
        ),
        rss_transport=_rss_transport(),
        resolver=_public_resolver,
    )

    assert resolved.spotify_episode_id == _SPOTIFY_ID
    assert resolved.spotify_url == f"https://open.spotify.com/episode/{_SPOTIFY_ID}"
    assert resolved.show_title == "Example Show"
    assert resolved.feed_url == _FEED_URL
    assert resolved.catalog_episode_guid == _EPISODE_GUID
    assert resolved.episode.episode_id == _EPISODE_GUID
    assert resolved.transcript.selected_reference == TranscriptReference(
        url="https://cdn.example.test/episode.vtt",
        media_type="text/vtt",
        language="en",
    )
    assert resolved.transcript.attempts[0].source is TranscriptSource.RSS
    assert resolved.transcript.attempts[0].status is TranscriptSourceStatus.AVAILABLE

    assert len(metadata_requests) == 2
    assert metadata_requests[0].url.params["url"] == resolved.spotify_url
    assert metadata_requests[1].url.params["entity"] == "podcastEpisode"
    assert metadata_requests[1].url.params["term"] == _EPISODE_TITLE
    assert "personal metadata resolver" in metadata_requests[0].headers["user-agent"]


def test_resolve_spotify_episode_normalizes_punctuation_for_exact_match() -> None:
    resolved = resolve_spotify_episode(
        f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
        metadata_transport=_metadata_transport(
            spotify_payload={"title": "Builder\u2019s Guide \u2014 Part 1"},
            catalog_results=[_catalog_result(title="Builder's Guide - Part 1")],
        ),
        rss_transport=_rss_transport(),
        resolver=_public_resolver,
    )

    assert resolved.episode.episode_id == _EPISODE_GUID


@pytest.mark.parametrize(
    "url",
    [
        f"http://open.spotify.com/episode/{_SPOTIFY_ID}",
        f"https://example.com/episode/{_SPOTIFY_ID}",
        f"https://user@open.spotify.com/episode/{_SPOTIFY_ID}",
        f"https://open.spotify.com:443/episode/{_SPOTIFY_ID}",
        f"https://open.spotify.com:invalid/episode/{_SPOTIFY_ID}",
        f"https://open.spotify.com/episode/{_SPOTIFY_ID}#fragment",
        "https://open.spotify.com/show/2IqXAVFR4e0Bmyjsdc8QzF",
        "https://open.spotify.com/episode/not-an-id",
        f"https://open.spotify.com/intl-us/episode/{_SPOTIFY_ID}",
    ],
)
def test_resolve_spotify_episode_rejects_noncanonical_urls_before_request(url: str) -> None:
    requested = False

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal requested
        requested = True
        return httpx2.Response(200, json={})

    with pytest.raises(SpotifyEpisodeUrlError, match="canonical"):
        resolve_spotify_episode(url, metadata_transport=_transport(handler))

    assert requested is False


def test_resolve_spotify_episode_rejects_missing_oembed_title() -> None:
    with pytest.raises(EpisodeMetadataError, match="missing title"):
        resolve_spotify_episode(
            f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
            metadata_transport=_metadata_transport(spotify_payload={"provider_name": "Spotify"}),
        )


def test_resolve_spotify_episode_converts_metadata_http_failure() -> None:
    with pytest.raises(EpisodeMetadataError, match="HTTP status 429"):
        resolve_spotify_episode(
            f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
            metadata_transport=_transport(lambda _request: httpx2.Response(429)),
        )


def test_resolve_spotify_episode_converts_metadata_transport_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("metadata offline", request=request)

    with pytest.raises(EpisodeMetadataError, match="metadata offline"):
        resolve_spotify_episode(
            f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
            metadata_transport=_transport(handler),
        )


@pytest.mark.parametrize("payload", [[], {"unexpected": []}, {"results": "not-a-list"}])
def test_resolve_spotify_episode_rejects_invalid_catalog_response(payload: object) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/oembed":
            return httpx2.Response(200, json={"title": _EPISODE_TITLE})
        return httpx2.Response(200, json=payload)

    with pytest.raises(EpisodeMetadataError):
        resolve_spotify_episode(
            f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
            metadata_transport=_transport(handler),
        )


def test_resolve_spotify_episode_rejects_no_exact_catalog_match() -> None:
    with pytest.raises(EpisodeMatchError, match="no exact"):
        resolve_spotify_episode(
            f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
            metadata_transport=_metadata_transport(
                catalog_results=[_catalog_result(title="Similar but different")]
            ),
        )


def test_resolve_spotify_episode_rejects_ambiguous_catalog_matches() -> None:
    with pytest.raises(EpisodeMatchError, match="multiple"):
        resolve_spotify_episode(
            f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
            metadata_transport=_metadata_transport(
                catalog_results=[
                    _catalog_result(),
                    _catalog_result(guid="another-guid", feed_url="https://other.test/rss"),
                ]
            ),
        )


def test_resolve_spotify_episode_deduplicates_identical_catalog_matches() -> None:
    resolved = resolve_spotify_episode(
        f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
        metadata_transport=_metadata_transport(
            catalog_results=[_catalog_result(), _catalog_result()]
        ),
        rss_transport=_rss_transport(),
        resolver=_public_resolver,
    )

    assert resolved.catalog_episode_guid == _EPISODE_GUID


def test_resolve_spotify_episode_requires_catalog_guid_in_feed() -> None:
    with pytest.raises(EpisodeMatchError, match="GUID was not found"):
        resolve_spotify_episode(
            f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
            metadata_transport=_metadata_transport(),
            rss_transport=_rss_transport(guid="wrong-guid"),
            resolver=_public_resolver,
        )


def test_resolve_transcript_sources_reports_deferred_stages_and_audio_fallback() -> None:
    episode = PodcastEpisode(
        episode_id="episode-1",
        title="Episode",
        web_url="https://publisher.example.test/episode",
        audio_url="https://cdn.example.test/episode.mp3",
        transcript_references=(
            TranscriptReference(
                url="file:///private/transcript.txt",
                media_type="text/plain",
            ),
            TranscriptReference(
                url="https://cdn.example.test/transcript.pdf",
                media_type="application/pdf",
            ),
        ),
    )

    resolution = resolve_transcript_sources(episode)

    assert resolution.selected_reference is None
    assert [attempt.source for attempt in resolution.attempts] == [
        TranscriptSource.RSS,
        TranscriptSource.PUBLISHER_WEBPAGE,
        TranscriptSource.PROVIDER,
        TranscriptSource.AUDIO_TRANSCRIPTION,
    ]
    assert resolution.attempts[-1].status is TranscriptSourceStatus.REQUIRES_AUTHORIZATION
    assert resolution.attempts[-1].source_url == "https://cdn.example.test/episode.mp3"


def test_resolve_transcript_sources_reports_missing_audio_fallback() -> None:
    resolution = resolve_transcript_sources(PodcastEpisode(episode_id="episode-1", title="Episode"))

    assert resolution.selected_reference is None
    assert resolution.attempts[1].source is TranscriptSource.PUBLISHER_WEBPAGE
    assert resolution.attempts[1].status is TranscriptSourceStatus.MISSING
    assert resolution.attempts[-1].source is TranscriptSource.AUDIO_TRANSCRIPTION
    assert resolution.attempts[-1].status is TranscriptSourceStatus.MISSING


def test_resolve_spotify_episode_rejects_oversized_metadata_response() -> None:
    oversized_title = "x" * 1_000_001

    with pytest.raises(EpisodeMetadataError, match="size limit"):
        resolve_spotify_episode(
            f"https://open.spotify.com/episode/{_SPOTIFY_ID}",
            metadata_transport=_transport(
                lambda _request: httpx2.Response(200, content=oversized_title.encode())
            ),
        )
