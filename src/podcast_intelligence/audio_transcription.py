"""Guarded orchestration for authorized RSS audio transcription."""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx2

from podcast_intelligence.episode_resolution import resolve_transcript_sources
from podcast_intelligence.ingestion.network import (
    HostResolver,
    PublicHttpDestinationError,
    resolve_host,
    validate_public_http_destination,
)
from podcast_intelligence.models import PodcastEpisode

type TranscriptionCostEstimator = Callable[[int], Decimal]

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_SPOTIFY_AUDIO_DOMAINS = frozenset({"scdn.co", "spotify.com", "spotifycdn.com"})
_MEDIA_TYPE_FORMATS = {
    "audio/m4a": ".m4a",
    "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/mpga": ".mpga",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
    "video/mp4": ".mp4",
}


class AudioTranscriptionError(RuntimeError):
    """Base class for application-owned audio transcription failures."""


class AudioTranscriptionAuthorizationError(AudioTranscriptionError):
    """Raised when the caller has not authorized temporary audio retrieval."""


class AudioTranscriptionEligibilityError(AudioTranscriptionError):
    """Raised when an episode is not eligible for the audio fallback."""


class AudioTranscriptionPolicyError(AudioTranscriptionError):
    """Raised when a URL, redirect, duration, or other policy is violated."""


class AudioTranscriptionTransportError(AudioTranscriptionError):
    """Raised when the audio transport cannot complete a request."""


class AudioTranscriptionHttpError(AudioTranscriptionError):
    """Raised when the audio server returns an unsuccessful response."""


class AudioTranscriptionMediaTypeError(AudioTranscriptionError):
    """Raised when RSS or HTTP declares an unsupported audio format."""


class AudioTranscriptionTooLargeError(AudioTranscriptionError):
    """Raised when an audio response exceeds the configured byte limit."""


class AudioTranscriptionCostError(AudioTranscriptionError):
    """Raised when cost cannot be bounded within the configured budget."""


class AudioTranscriptionProviderError(AudioTranscriptionError):
    """Raised when the injected transcription provider fails."""


@dataclass(frozen=True, slots=True)
class TranscriptionUsage:
    """Normalized provider usage for one transcription request."""

    usage_type: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    audio_seconds: float | None = None
    input_token_details_json: str | None = None

    def __post_init__(self) -> None:
        if not self.usage_type.strip():
            raise ValueError("transcription usage type must not be empty")
        numeric_values = (
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.audio_seconds,
        )
        if any(value is not None and value < 0 for value in numeric_values):
            raise ValueError("transcription usage values must not be negative")


@dataclass(frozen=True, slots=True)
class ProviderTranscriptPart:
    """One ordered provider response used to assemble a transcript."""

    ordinal: int
    text: str
    request_id: str | None
    model: str
    language: str | None = None
    usage: TranscriptionUsage | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise ValueError("transcript part ordinal must not be negative")
        if not self.text.strip():
            raise ValueError("transcript part text must not be empty")
        if not self.model.strip():
            raise ValueError("transcript part model must not be empty")


@dataclass(frozen=True, slots=True)
class AudioTranscriptionPolicy:
    """Explicit duration, network, byte, and estimated-cost limits."""

    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    max_audio_bytes: int = 100_000_000
    max_redirects: int = 3
    max_duration_seconds: int = 2 * 60 * 60
    max_estimated_cost_usd: Decimal = Decimal("1.00")

    def __post_init__(self) -> None:
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("audio retrieval timeouts must be positive")
        if self.max_audio_bytes <= 0:
            raise ValueError("audio response size limit must be positive")
        if self.max_redirects < 0:
            raise ValueError("audio redirect limit must not be negative")
        if self.max_duration_seconds <= 0:
            raise ValueError("audio duration limit must be positive")
        if not self.max_estimated_cost_usd.is_finite() or self.max_estimated_cost_usd < Decimal(0):
            raise ValueError("transcription cost limit must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ProviderTranscript:
    """Provider-independent transcript returned by an injected adapter."""

    text: str
    provider: str
    model: str
    request_ids: tuple[str, ...] = ()
    language: str | None = None
    chunk_count: int = 1
    parts: tuple[ProviderTranscriptPart, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("transcript text must not be empty")
        if not self.provider.strip():
            raise ValueError("transcript provider must not be empty")
        if not self.model.strip():
            raise ValueError("transcript model must not be empty")
        if self.chunk_count <= 0:
            raise ValueError("transcript chunk count must be positive")
        if self.parts:
            if tuple(part.ordinal for part in self.parts) != tuple(range(len(self.parts))):
                raise ValueError("transcript part ordinals must be contiguous and ordered")
            if self.chunk_count != len(self.parts):
                raise ValueError("transcript chunk count must equal the number of parts")
            assembled = "\n\n".join(part.text.strip() for part in self.parts)
            if self.text != assembled:
                raise ValueError("transcript text must equal the deterministic part assembly")
            part_request_ids = tuple(
                part.request_id for part in self.parts if part.request_id is not None
            )
            if self.request_ids != part_request_ids:
                raise ValueError("transcript request IDs must match ordered part provenance")

    @property
    def response_id(self) -> str | None:
        """Return the first request ID for compatibility with single-chunk callers."""

        return self.request_ids[0] if self.request_ids else None


class AudioTranscriber(Protocol):
    """Application-owned boundary implemented by a transcription adapter."""

    def transcribe(
        self,
        audio_path: Path,
        *,
        media_type: str,
        duration_seconds: int,
    ) -> ProviderTranscript:
        """Transcribe one bounded local audio file without retaining it."""
        ...


@dataclass(frozen=True, slots=True)
class EpisodeTranscript:
    """Typed transcript content and auditable RSS audio provenance."""

    episode_id: str
    source_url: str
    source_media_type: str
    duration_seconds: int
    audio_bytes: int
    audio_sha256: str
    etag: str | None
    last_modified: str | None
    estimated_cost_usd: Decimal
    transcript: ProviderTranscript


_DEFAULT_POLICY = AudioTranscriptionPolicy()


def transcribe_episode_audio(
    episode: PodcastEpisode,
    *,
    authorized: bool,
    estimate_cost: TranscriptionCostEstimator,
    transcriber: AudioTranscriber,
    policy: AudioTranscriptionPolicy = _DEFAULT_POLICY,
    transport: httpx2.BaseTransport | None = None,
    resolver: HostResolver | None = None,
) -> EpisodeTranscript:
    """Download and transcribe an eligible RSS enclosure under explicit limits."""

    if not authorized:
        raise AudioTranscriptionAuthorizationError(
            "explicit authorization is required before retrieving episode audio"
        )
    if resolve_transcript_sources(episode).selected_reference is not None:
        raise AudioTranscriptionEligibilityError(
            "audio fallback is not eligible when RSS provides a supported transcript"
        )
    if episode.audio_url is None:
        raise AudioTranscriptionEligibilityError("episode RSS has no audio enclosure")
    if episode.duration_seconds is None or episode.duration_seconds <= 0:
        raise AudioTranscriptionPolicyError(
            "episode RSS must declare a positive duration before audio retrieval"
        )
    if episode.duration_seconds > policy.max_duration_seconds:
        raise AudioTranscriptionPolicyError(
            f"episode duration exceeds the {policy.max_duration_seconds}-second limit"
        )

    media_type = _supported_media_type(episode.audio_media_type, source="RSS")
    estimated_cost = _estimate_cost(estimate_cost, episode.duration_seconds)
    if estimated_cost > policy.max_estimated_cost_usd:
        raise AudioTranscriptionCostError(
            f"estimated transcription cost exceeds the ${policy.max_estimated_cost_usd} limit"
        )

    with TemporaryDirectory(prefix="podcast-intelligence-audio-") as temporary_directory:
        audio_path = Path(temporary_directory) / f"episode{_MEDIA_TYPE_FORMATS[media_type]}"
        audio_result = _retrieve_audio(
            episode.audio_url,
            audio_path,
            rss_media_type=media_type,
            policy=policy,
            transport=transport,
            resolver=resolver or resolve_host,
        )
        try:
            transcript = transcriber.transcribe(
                audio_path,
                media_type=audio_result.media_type,
                duration_seconds=episode.duration_seconds,
            )
        except AudioTranscriptionError:
            raise
        except Exception as error:
            raise AudioTranscriptionProviderError("transcription provider failed") from error

    return EpisodeTranscript(
        episode_id=episode.episode_id,
        source_url=episode.audio_url,
        source_media_type=audio_result.media_type,
        duration_seconds=episode.duration_seconds,
        audio_bytes=audio_result.byte_count,
        audio_sha256=audio_result.sha256,
        etag=audio_result.etag,
        last_modified=audio_result.last_modified,
        estimated_cost_usd=estimated_cost,
        transcript=transcript,
    )


def _estimate_cost(
    estimate_cost: TranscriptionCostEstimator,
    duration_seconds: int,
) -> Decimal:
    try:
        estimated_cost = estimate_cost(duration_seconds)
    except Exception as error:
        raise AudioTranscriptionCostError("transcription cost estimate failed") from error
    if not isinstance(estimated_cost, Decimal):
        raise AudioTranscriptionCostError("transcription cost estimate must be a Decimal")
    if not estimated_cost.is_finite() or estimated_cost < Decimal(0):
        raise AudioTranscriptionCostError(
            "transcription cost estimate must be finite and non-negative"
        )
    return estimated_cost


def _retrieve_audio(
    url: str,
    destination: Path,
    *,
    rss_media_type: str,
    policy: AudioTranscriptionPolicy,
    transport: httpx2.BaseTransport | None,
    resolver: HostResolver,
) -> "_RetrievedAudio":
    timeout = httpx2.Timeout(
        connect=policy.connect_timeout_seconds,
        read=policy.read_timeout_seconds,
        write=policy.connect_timeout_seconds,
        pool=policy.connect_timeout_seconds,
    )
    try:
        with httpx2.Client(
            transport=transport,
            follow_redirects=False,
            timeout=timeout,
            trust_env=False,
            headers={"User-Agent": "Podcast-Intelligence/0.1 (+authorized audio reader)"},
        ) as client:
            return _stream_audio(
                client,
                url,
                destination,
                rss_media_type=rss_media_type,
                policy=policy,
                resolver=resolver,
            )
    except httpx2.RequestError as error:
        raise AudioTranscriptionTransportError("audio request failed") from error


def _stream_audio(
    client: httpx2.Client,
    url: str,
    destination: Path,
    *,
    rss_media_type: str,
    policy: AudioTranscriptionPolicy,
    resolver: HostResolver,
) -> "_RetrievedAudio":
    current_url = url
    redirects_followed = 0

    while True:
        _validate_audio_destination(current_url, resolver=resolver)
        with client.stream(
            "GET",
            current_url,
            headers={"Accept": ", ".join(sorted(_MEDIA_TYPE_FORMATS))},
        ) as response:
            if response.status_code in _REDIRECT_STATUSES:
                if redirects_followed >= policy.max_redirects:
                    raise AudioTranscriptionPolicyError(
                        f"audio redirect limit of {policy.max_redirects} exceeded"
                    )
                location = response.headers.get("location")
                if not location:
                    raise AudioTranscriptionHttpError(
                        "audio redirect response is missing a Location header"
                    )
                current_url = urljoin(str(response.url), location)
                redirects_followed += 1
                continue

            if not 200 <= response.status_code < 300:
                raise AudioTranscriptionHttpError(
                    f"audio request failed with HTTP status {response.status_code}"
                )

            response_media_type = _response_media_type(
                response.headers.get("content-type"),
                rss_media_type=rss_media_type,
            )
            _validate_content_length(
                response.headers.get("content-length"),
                maximum=policy.max_audio_bytes,
            )
            audio_bytes, audio_sha256 = _write_bounded(
                response,
                destination,
                maximum=policy.max_audio_bytes,
            )
            if audio_bytes == 0:
                raise AudioTranscriptionPolicyError("audio response must not be empty")
            return _RetrievedAudio(
                byte_count=audio_bytes,
                media_type=response_media_type,
                sha256=audio_sha256,
                etag=_optional_header(response.headers.get("etag")),
                last_modified=_optional_header(response.headers.get("last-modified")),
            )


def _validate_audio_destination(url: str, *, resolver: HostResolver) -> None:
    try:
        validate_public_http_destination(
            url,
            resolver=resolver,
            resource_name="audio",
        )
    except PublicHttpDestinationError as error:
        raise AudioTranscriptionPolicyError(str(error)) from error

    hostname = urlsplit(url).hostname
    if hostname is not None and any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in _SPOTIFY_AUDIO_DOMAINS
    ):
        raise AudioTranscriptionPolicyError("Spotify audio destinations are not permitted")


def _supported_media_type(value: str | None, *, source: str) -> str:
    if value is None:
        raise AudioTranscriptionMediaTypeError(
            f"{source} audio media type is required for transcription"
        )
    media_type = value.partition(";")[0].strip().lower()
    if media_type not in _MEDIA_TYPE_FORMATS:
        raise AudioTranscriptionMediaTypeError(
            f"{source} audio media type {media_type or '<empty>'} is not supported"
        )
    return media_type


def _response_media_type(header: str | None, *, rss_media_type: str) -> str:
    if header is None:
        return rss_media_type
    response_media_type = _supported_media_type(header, source="HTTP")
    if _MEDIA_TYPE_FORMATS[response_media_type] != _MEDIA_TYPE_FORMATS[rss_media_type]:
        raise AudioTranscriptionMediaTypeError(
            "HTTP audio media type conflicts with the RSS enclosure media type"
        )
    return response_media_type


def _validate_content_length(header: str | None, *, maximum: int) -> None:
    if header is None:
        return
    try:
        declared_length = int(header)
    except ValueError:
        return
    if declared_length > maximum:
        raise AudioTranscriptionTooLargeError(
            f"audio response exceeds the {maximum}-byte size limit"
        )


@dataclass(frozen=True, slots=True)
class _RetrievedAudio:
    byte_count: int
    media_type: str
    sha256: str
    etag: str | None
    last_modified: str | None


def _write_bounded(
    response: httpx2.Response, destination: Path, *, maximum: int
) -> tuple[int, str]:
    audio_bytes = 0
    digest = sha256()
    with destination.open("xb") as audio_file:
        for chunk in response.iter_bytes():
            if audio_bytes + len(chunk) > maximum:
                raise AudioTranscriptionTooLargeError(
                    f"audio response exceeds the {maximum}-byte size limit"
                )
            audio_file.write(chunk)
            digest.update(chunk)
            audio_bytes += len(chunk)
    return audio_bytes, digest.hexdigest()


def _optional_header(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
