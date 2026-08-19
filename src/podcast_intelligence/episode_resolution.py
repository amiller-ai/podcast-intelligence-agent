"""Resolve Spotify episode links to canonical RSS episodes and transcript sources."""

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import cast
from urllib.parse import urlsplit

import httpx2

from podcast_intelligence.ingestion.http import (
    RssRetrievalPolicy,
    retrieve_rss_feed,
)
from podcast_intelligence.ingestion.network import HostResolver
from podcast_intelligence.models import PodcastEpisode, TranscriptReference

_SPOTIFY_OEMBED_URL = "https://open.spotify.com/oembed"
_APPLE_PODCAST_SEARCH_URL = "https://itunes.apple.com/search"
_SPOTIFY_EPISODE_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_CATALOG_OMITTED_SUFFIX = re.compile(
    r"\s*-\s*\[[^\]]+,\s*EP(?:ISODE)?\.?\s*\d+\]\s*$",
    flags=re.IGNORECASE,
)
_METADATA_RESPONSE_LIMIT = 1_000_000
_DEFAULT_FEED_POLICY = RssRetrievalPolicy(max_response_bytes=10_000_000)
_SUPPORTED_TRANSCRIPT_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "application/srt",
        "application/x-subrip",
        "text/html",
        "text/plain",
        "text/srt",
        "text/vtt",
    }
)


class EpisodeResolutionError(RuntimeError):
    """Base class for application-owned episode resolution failures."""


class SpotifyEpisodeUrlError(EpisodeResolutionError):
    """Raised when the input is not a canonical Spotify episode URL."""


class EpisodeMetadataError(EpisodeResolutionError):
    """Raised when a metadata source returns an unusable response."""


class EpisodeMatchError(EpisodeResolutionError):
    """Raised when an episode cannot be matched uniquely and exactly."""


class TranscriptSource(StrEnum):
    """Ordered transcript source types in the resolution pipeline."""

    RSS = "rss"
    PUBLISHER_WEBPAGE = "publisher_webpage"
    PROVIDER = "provider"
    AUDIO_TRANSCRIPTION = "audio_transcription"


class TranscriptSourceStatus(StrEnum):
    """Outcome of checking one transcript source stage."""

    AVAILABLE = "available"
    MISSING = "missing"
    DEFERRED = "deferred"
    REQUIRES_AUTHORIZATION = "requires_authorization"


@dataclass(frozen=True, slots=True)
class CatalogEpisode:
    """Normalized episode identity returned by a podcast catalog."""

    title: str
    show_title: str
    feed_url: str
    episode_guid: str
    catalog_url: str | None
    published_at: datetime | None
    duration_seconds: int | None


@dataclass(frozen=True, slots=True)
class TranscriptSourceAttempt:
    """Auditable result from one ordered transcript source stage."""

    source: TranscriptSource
    status: TranscriptSourceStatus
    source_url: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class TranscriptResolution:
    """Selected transcript reference and ordered source outcomes."""

    selected_reference: TranscriptReference | None
    attempts: tuple[TranscriptSourceAttempt, ...]


@dataclass(frozen=True, slots=True)
class ResolvedSpotifyEpisode:
    """Spotify input resolved to one verified episode in a canonical RSS feed."""

    spotify_episode_id: str
    spotify_url: str
    show_title: str
    feed_url: str
    catalog_url: str | None
    catalog_episode_guid: str
    episode: PodcastEpisode
    transcript: TranscriptResolution


def resolve_spotify_episode(
    spotify_url: str,
    *,
    metadata_transport: httpx2.BaseTransport | None = None,
    rss_transport: httpx2.BaseTransport | None = None,
    resolver: HostResolver | None = None,
    feed_policy: RssRetrievalPolicy = _DEFAULT_FEED_POLICY,
) -> ResolvedSpotifyEpisode:
    """Resolve a Spotify episode URL to a verified canonical RSS episode."""

    spotify_episode_id = _spotify_episode_id(spotify_url)
    canonical_spotify_url = f"https://open.spotify.com/episode/{spotify_episode_id}"

    timeout = httpx2.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
    try:
        with httpx2.Client(
            transport=metadata_transport,
            follow_redirects=False,
            timeout=timeout,
            trust_env=False,
            headers={"User-Agent": "Podcast-Intelligence/0.1 (+personal metadata resolver)"},
        ) as client:
            spotify_title = _spotify_episode_title(client, canonical_spotify_url)
            catalog_candidates = _catalog_episode_candidates(client, spotify_title)
    except httpx2.RequestError as error:
        raise EpisodeMetadataError(f"episode metadata request failed: {error}") from error

    catalog_episode = _select_catalog_episode(spotify_title, catalog_candidates)
    feed = retrieve_rss_feed(
        catalog_episode.feed_url,
        policy=feed_policy,
        transport=rss_transport,
        resolver=resolver,
    )
    rss_episode = next(
        (
            episode
            for episode in feed.episodes
            if episode.episode_id == catalog_episode.episode_guid
        ),
        None,
    )
    if rss_episode is None:
        raise EpisodeMatchError("catalog episode GUID was not found in the canonical RSS feed")
    if _normalize_title(rss_episode.title) != _normalize_title(spotify_title):
        raise EpisodeMatchError("verified RSS episode title does not match the Spotify episode")

    return ResolvedSpotifyEpisode(
        spotify_episode_id=spotify_episode_id,
        spotify_url=canonical_spotify_url,
        show_title=feed.title,
        feed_url=catalog_episode.feed_url,
        catalog_url=catalog_episode.catalog_url,
        catalog_episode_guid=catalog_episode.episode_guid,
        episode=rss_episode,
        transcript=resolve_transcript_sources(rss_episode),
    )


def resolve_transcript_sources(episode: PodcastEpisode) -> TranscriptResolution:
    """Return the first viable transcript source and ordered source outcomes."""

    rss_reference = next(
        (
            reference
            for reference in episode.transcript_references
            if _is_supported_public_transcript(reference)
        ),
        None,
    )
    if rss_reference is not None:
        return TranscriptResolution(
            selected_reference=rss_reference,
            attempts=(
                TranscriptSourceAttempt(
                    source=TranscriptSource.RSS,
                    status=TranscriptSourceStatus.AVAILABLE,
                    source_url=rss_reference.url,
                    reason="RSS publishes a supported transcript reference",
                ),
            ),
        )

    attempts = [
        TranscriptSourceAttempt(
            source=TranscriptSource.RSS,
            status=TranscriptSourceStatus.MISSING,
            source_url=None,
            reason="RSS has no supported public transcript reference",
        ),
        TranscriptSourceAttempt(
            source=TranscriptSource.PUBLISHER_WEBPAGE,
            status=(
                TranscriptSourceStatus.DEFERRED
                if episode.web_url is not None
                else TranscriptSourceStatus.MISSING
            ),
            source_url=episode.web_url,
            reason=(
                "publisher webpage extraction requires an explicit adapter"
                if episode.web_url is not None
                else "RSS has no publisher episode webpage"
            ),
        ),
        TranscriptSourceAttempt(
            source=TranscriptSource.PROVIDER,
            status=TranscriptSourceStatus.DEFERRED,
            source_url=None,
            reason="transcript providers require an allowlisted adapter",
        ),
    ]
    if episode.audio_url is None:
        attempts.append(
            TranscriptSourceAttempt(
                source=TranscriptSource.AUDIO_TRANSCRIPTION,
                status=TranscriptSourceStatus.MISSING,
                source_url=None,
                reason="RSS has no audio enclosure for a transcription fallback",
            )
        )
    else:
        attempts.append(
            TranscriptSourceAttempt(
                source=TranscriptSource.AUDIO_TRANSCRIPTION,
                status=TranscriptSourceStatus.REQUIRES_AUTHORIZATION,
                source_url=episode.audio_url,
                reason="RSS audio exists but download and transcription require authorization",
            )
        )

    return TranscriptResolution(selected_reference=None, attempts=tuple(attempts))


def _spotify_episode_id(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise SpotifyEpisodeUrlError("expected a canonical open.spotify.com episode URL") from error

    path_parts = tuple(part for part in parsed.path.split("/") if part)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "open.spotify.com"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.fragment)
        or len(path_parts) != 2
        or path_parts[0] != "episode"
        or _SPOTIFY_EPISODE_ID.fullmatch(path_parts[1]) is None
    ):
        raise SpotifyEpisodeUrlError("expected a canonical open.spotify.com episode URL")
    return path_parts[1]


def _spotify_episode_title(client: httpx2.Client, canonical_url: str) -> str:
    payload = _request_json(
        client,
        _SPOTIFY_OEMBED_URL,
        params={"url": canonical_url},
        source_name="Spotify oEmbed",
    )
    return _required_string(payload, "title", source_name="Spotify oEmbed")


def _catalog_episode_candidates(
    client: httpx2.Client,
    episode_title: str,
) -> tuple[CatalogEpisode, ...]:
    payload = _request_json(
        client,
        _APPLE_PODCAST_SEARCH_URL,
        params={
            "term": episode_title,
            "media": "podcast",
            "entity": "podcastEpisode",
            "limit": "20",
        },
        source_name="podcast catalog",
    )
    results = payload.get("results")
    if not isinstance(results, list):
        raise EpisodeMetadataError("podcast catalog response is missing results")

    candidates: list[CatalogEpisode] = []
    for raw_result in results:
        if not isinstance(raw_result, dict):
            continue
        result = cast(dict[str, object], raw_result)
        candidate = _catalog_episode(result)
        if candidate is not None:
            candidates.append(candidate)
    return tuple(candidates)


def _catalog_episode(result: Mapping[str, object]) -> CatalogEpisode | None:
    title = _optional_string(result.get("trackName"))
    show_title = _optional_string(result.get("collectionName"))
    feed_url = _optional_string(result.get("feedUrl"))
    episode_guid = _optional_string(result.get("episodeGuid"))
    if None in {title, show_title, feed_url, episode_guid}:
        return None

    duration_millis = result.get("trackTimeMillis")
    duration_seconds = (
        duration_millis // 1000
        if isinstance(duration_millis, int) and not isinstance(duration_millis, bool)
        else None
    )
    return CatalogEpisode(
        title=cast(str, title),
        show_title=cast(str, show_title),
        feed_url=cast(str, feed_url),
        episode_guid=cast(str, episode_guid),
        catalog_url=_optional_string(result.get("trackViewUrl")),
        published_at=_optional_datetime(result.get("releaseDate")),
        duration_seconds=duration_seconds,
    )


def _select_catalog_episode(
    spotify_title: str,
    candidates: Sequence[CatalogEpisode],
) -> CatalogEpisode:
    normalized_titles = {_normalize_title(spotify_title)}
    without_catalog_suffix = _CATALOG_OMITTED_SUFFIX.sub("", spotify_title)
    if without_catalog_suffix != spotify_title:
        normalized_titles.add(_normalize_title(without_catalog_suffix))
    matches = {
        (candidate.episode_guid, candidate.feed_url): candidate
        for candidate in candidates
        if _normalize_title(candidate.title) in normalized_titles
    }
    if not matches:
        raise EpisodeMatchError("no safe podcast catalog match was found for the Spotify episode")
    if len(matches) > 1:
        raise EpisodeMatchError("Spotify episode title matched multiple podcast catalog episodes")
    return next(iter(matches.values()))


def _normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


def _is_supported_public_transcript(reference: TranscriptReference) -> bool:
    try:
        parsed = urlsplit(reference.url)
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and reference.media_type.lower() in _SUPPORTED_TRANSCRIPT_MEDIA_TYPES
    )


def _request_json(
    client: httpx2.Client,
    url: str,
    *,
    params: Mapping[str, str],
    source_name: str,
) -> dict[str, object]:
    with client.stream(
        "GET", url, params=params, headers={"Accept": "application/json"}
    ) as response:
        if not 200 <= response.status_code < 300:
            raise EpisodeMetadataError(
                f"{source_name} request failed with HTTP status {response.status_code}"
            )
        payload = bytearray()
        for chunk in response.iter_bytes():
            if len(payload) + len(chunk) > _METADATA_RESPONSE_LIMIT:
                raise EpisodeMetadataError(
                    f"{source_name} response exceeds the metadata size limit"
                )
            payload.extend(chunk)

    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EpisodeMetadataError(f"{source_name} returned invalid JSON") from error
    if not isinstance(decoded, dict):
        raise EpisodeMetadataError(f"{source_name} returned an invalid response object")
    return cast(dict[str, object], decoded)


def _required_string(
    payload: Mapping[str, object],
    key: str,
    *,
    source_name: str,
) -> str:
    value = _optional_string(payload.get(key))
    if value is None:
        raise EpisodeMetadataError(f"{source_name} response is missing {key}")
    return value


def _optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _optional_datetime(value: object) -> datetime | None:
    text = _optional_string(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
