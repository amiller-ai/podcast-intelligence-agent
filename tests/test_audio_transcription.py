import socket
from collections.abc import Callable
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from stat import S_IMODE

import httpx2
import pytest

from podcast_intelligence.audio_transcription import (
    AudioTranscriptionAuthorizationError,
    AudioTranscriptionCostError,
    AudioTranscriptionEligibilityError,
    AudioTranscriptionHttpError,
    AudioTranscriptionMediaTypeError,
    AudioTranscriptionPolicy,
    AudioTranscriptionPolicyError,
    AudioTranscriptionProviderError,
    AudioTranscriptionTooLargeError,
    AudioTranscriptionTransportError,
    ProviderTranscript,
    transcribe_episode_audio,
)
from podcast_intelligence.models import PodcastEpisode, TranscriptReference

_AUDIO_URL = "https://cdn.example.test/episode.mp3"
_AUDIO_BYTES = b"synthetic-audio-bytes"


def _public_resolver(_host: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def _transport(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.MockTransport:
    return httpx2.MockTransport(handler)


def _episode(**overrides: object) -> PodcastEpisode:
    values: dict[str, object] = {
        "episode_id": "episode-1",
        "title": "Synthetic episode",
        "audio_url": _AUDIO_URL,
        "audio_media_type": "audio/mpeg",
        "duration_seconds": 1_800,
    }
    values.update(overrides)
    return PodcastEpisode(**values)  # type: ignore[arg-type]


def _audio_transport(
    *,
    content: bytes = _AUDIO_BYTES,
    content_type: str | None = "audio/mpeg",
    content_length: str | None = None,
) -> httpx2.MockTransport:
    headers = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if content_length is not None:
        headers["Content-Length"] = content_length
    return _transport(lambda _request: httpx2.Response(200, headers=headers, content=content))


def _cost(_duration_seconds: int) -> Decimal:
    return Decimal("0.25")


class RecordingTranscriber:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.paths: list[Path] = []
        self.media_types: list[str] = []
        self.payloads: list[bytes] = []
        self.private_during_call = False

    def transcribe(
        self,
        audio_path: Path,
        *,
        media_type: str,
        duration_seconds: int,
    ) -> ProviderTranscript:
        self.paths.append(audio_path)
        self.media_types.append(media_type)
        self.payloads.append(audio_path.read_bytes())
        self.private_during_call = S_IMODE(audio_path.parent.stat().st_mode) & 0o077 == 0
        assert duration_seconds == 1_800
        if self.failure is not None:
            raise self.failure
        return ProviderTranscript(
            text="A small synthetic transcript.",
            provider="test-provider",
            model="test-model",
            request_ids=("transcription-1",),
            language="en",
        )


def test_transcribe_episode_audio_returns_typed_provenance_and_deletes_audio() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={
                "Content-Type": "audio/mpeg",
                "ETag": '"synthetic-etag"',
                "Last-Modified": "Wed, 19 Aug 2026 12:00:00 GMT",
            },
            content=_AUDIO_BYTES,
        )

    transcriber = RecordingTranscriber()
    result = transcribe_episode_audio(
        _episode(),
        authorized=True,
        estimate_cost=_cost,
        transcriber=transcriber,
        transport=_transport(handler),
        resolver=_public_resolver,
    )

    assert result.episode_id == "episode-1"
    assert result.source_url == _AUDIO_URL
    assert result.source_media_type == "audio/mpeg"
    assert result.duration_seconds == 1_800
    assert result.audio_bytes == len(_AUDIO_BYTES)
    assert result.audio_sha256 == sha256(_AUDIO_BYTES).hexdigest()
    assert result.etag == '"synthetic-etag"'
    assert result.last_modified == "Wed, 19 Aug 2026 12:00:00 GMT"
    assert result.estimated_cost_usd == Decimal("0.25")
    assert result.transcript.text == "A small synthetic transcript."
    assert result.transcript.response_id == "transcription-1"
    assert result.transcript.request_ids == ("transcription-1",)
    assert transcriber.payloads == [_AUDIO_BYTES]
    assert transcriber.media_types == ["audio/mpeg"]
    assert transcriber.private_during_call is True
    assert not transcriber.paths[0].exists()
    assert len(requests) == 1
    assert "authorized audio reader" in requests[0].headers["user-agent"]
    assert requests[0].extensions["timeout"] == {
        "connect": 5.0,
        "read": 30.0,
        "write": 5.0,
        "pool": 5.0,
    }


def test_transcribe_episode_audio_requires_authorization_before_other_work() -> None:
    estimated = False
    requested = False

    def estimate(_duration_seconds: int) -> Decimal:
        nonlocal estimated
        estimated = True
        return Decimal(0)

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal requested
        requested = True
        return httpx2.Response(200, content=_AUDIO_BYTES)

    with pytest.raises(AudioTranscriptionAuthorizationError, match="authorization"):
        transcribe_episode_audio(
            _episode(),
            authorized=False,
            estimate_cost=estimate,
            transcriber=RecordingTranscriber(),
            transport=_transport(handler),
            resolver=_public_resolver,
        )

    assert estimated is False
    assert requested is False


def test_transcribe_episode_audio_rejects_episode_with_rss_transcript() -> None:
    episode = _episode(
        transcript_references=(
            TranscriptReference(
                url="https://cdn.example.test/transcript.vtt",
                media_type="text/vtt",
            ),
        )
    )

    with pytest.raises(AudioTranscriptionEligibilityError, match="RSS provides"):
        transcribe_episode_audio(
            episode,
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            resolver=_public_resolver,
        )


@pytest.mark.parametrize(
    ("overrides", "error_type", "message"),
    [
        ({"audio_url": None}, AudioTranscriptionEligibilityError, "no audio enclosure"),
        ({"duration_seconds": None}, AudioTranscriptionPolicyError, "positive duration"),
        ({"duration_seconds": 0}, AudioTranscriptionPolicyError, "positive duration"),
        ({"duration_seconds": 7_201}, AudioTranscriptionPolicyError, "7200-second"),
        ({"audio_media_type": None}, AudioTranscriptionMediaTypeError, "required"),
        ({"audio_media_type": "audio/ogg"}, AudioTranscriptionMediaTypeError, "not supported"),
    ],
)
def test_transcribe_episode_audio_rejects_ineligible_rss_metadata(
    overrides: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        transcribe_episode_audio(
            _episode(**overrides),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            resolver=_public_resolver,
        )


@pytest.mark.parametrize(
    "estimate",
    [Decimal("1.01"), Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01"), 0.1],
)
def test_transcribe_episode_audio_rejects_unbounded_cost_before_download(
    estimate: object,
) -> None:
    requested = False

    def estimate_cost(_duration_seconds: int) -> Decimal:
        return estimate  # type: ignore[return-value]

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal requested
        requested = True
        return httpx2.Response(200, content=_AUDIO_BYTES)

    with pytest.raises(AudioTranscriptionCostError):
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=estimate_cost,
            transcriber=RecordingTranscriber(),
            transport=_transport(handler),
            resolver=_public_resolver,
        )

    assert requested is False


def test_transcribe_episode_audio_converts_cost_estimator_failure() -> None:
    def failed_estimate(_duration_seconds: int) -> Decimal:
        raise RuntimeError("pricing unavailable")

    with pytest.raises(AudioTranscriptionCostError, match="estimate failed"):
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=failed_estimate,
            transcriber=RecordingTranscriber(),
            resolver=_public_resolver,
        )


def test_transcribe_episode_audio_revalidates_redirect_destination() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        return httpx2.Response(302, headers={"Location": "http://127.0.0.1/private.mp3"})

    with pytest.raises(AudioTranscriptionPolicyError, match="public IP addresses"):
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            transport=_transport(handler),
            resolver=_public_resolver,
        )

    assert requested_urls == [_AUDIO_URL]


@pytest.mark.parametrize(
    "audio_url",
    [
        "https://open.spotify.com/episode/id",
        "https://audio-fa.scdn.co/audio.mp3",
        "https://cdn.spotifycdn.com/audio.mp3",
    ],
)
def test_transcribe_episode_audio_never_requests_spotify_audio(audio_url: str) -> None:
    requested = False

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal requested
        requested = True
        return httpx2.Response(200, content=_AUDIO_BYTES)

    with pytest.raises(AudioTranscriptionPolicyError, match="Spotify audio"):
        transcribe_episode_audio(
            _episode(audio_url=audio_url),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            transport=_transport(handler),
            resolver=_public_resolver,
        )

    assert requested is False


def test_transcribe_episode_audio_follows_bounded_public_redirect() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/episode.mp3":
            return httpx2.Response(302, headers={"Location": "/final.mp3"})
        return httpx2.Response(200, headers={"Content-Type": "audio/mpeg"}, content=_AUDIO_BYTES)

    result = transcribe_episode_audio(
        _episode(),
        authorized=True,
        estimate_cost=_cost,
        transcriber=RecordingTranscriber(),
        transport=_transport(handler),
        resolver=_public_resolver,
    )

    assert result.audio_bytes == len(_AUDIO_BYTES)
    assert requested_urls == [_AUDIO_URL, "https://cdn.example.test/final.mp3"]


def test_transcribe_episode_audio_enforces_redirect_limit() -> None:
    with pytest.raises(AudioTranscriptionPolicyError, match="redirect limit of 1"):
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            policy=AudioTranscriptionPolicy(max_redirects=1),
            transport=_transport(
                lambda _request: httpx2.Response(302, headers={"Location": "/next.mp3"})
            ),
            resolver=_public_resolver,
        )


def test_transcribe_episode_audio_rejects_redirect_without_location() -> None:
    with pytest.raises(AudioTranscriptionHttpError, match="missing a Location"):
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            transport=_transport(lambda _request: httpx2.Response(302)),
            resolver=_public_resolver,
        )


@pytest.mark.parametrize(
    ("rss_media_type", "http_media_type"),
    [
        ("audio/mpeg", "text/html"),
        ("audio/mpeg", "audio/wav"),
    ],
)
def test_transcribe_episode_audio_rejects_incompatible_http_media_type(
    rss_media_type: str,
    http_media_type: str,
) -> None:
    with pytest.raises(AudioTranscriptionMediaTypeError):
        transcribe_episode_audio(
            _episode(audio_media_type=rss_media_type),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            transport=_audio_transport(content_type=http_media_type),
            resolver=_public_resolver,
        )


def test_transcribe_episode_audio_accepts_missing_http_media_type() -> None:
    result = transcribe_episode_audio(
        _episode(),
        authorized=True,
        estimate_cost=_cost,
        transcriber=RecordingTranscriber(),
        transport=_audio_transport(content_type=None),
        resolver=_public_resolver,
    )

    assert result.source_media_type == "audio/mpeg"


def test_transcribe_episode_audio_rejects_empty_response() -> None:
    with pytest.raises(AudioTranscriptionPolicyError, match="must not be empty"):
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            transport=_audio_transport(content=b""),
            resolver=_public_resolver,
        )


@pytest.mark.parametrize(
    "transport",
    [
        _audio_transport(content=b"small", content_length="9", content_type="audio/mpeg"),
        _audio_transport(content=b"123456789", content_length="1", content_type="audio/mpeg"),
    ],
)
def test_transcribe_episode_audio_rejects_declared_or_streamed_oversize(
    transport: httpx2.MockTransport,
) -> None:
    transcriber = RecordingTranscriber()

    with pytest.raises(AudioTranscriptionTooLargeError, match="8-byte"):
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=_cost,
            transcriber=transcriber,
            policy=AudioTranscriptionPolicy(max_audio_bytes=8),
            transport=transport,
            resolver=_public_resolver,
        )

    assert transcriber.paths == []


def test_transcribe_episode_audio_converts_http_failure() -> None:
    with pytest.raises(AudioTranscriptionHttpError, match="HTTP status 503"):
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            transport=_transport(lambda _request: httpx2.Response(503)),
            resolver=_public_resolver,
        )


def test_transcribe_episode_audio_converts_transport_failure_without_details() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("secret transport detail", request=request)

    with pytest.raises(AudioTranscriptionTransportError, match="audio request failed") as captured:
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            transport=_transport(handler),
            resolver=_public_resolver,
        )

    assert "secret transport detail" not in str(captured.value)


def test_transcribe_episode_audio_converts_resolution_failure() -> None:
    def failed_resolver(_host: str, _port: int) -> tuple[str, ...]:
        raise socket.gaierror("not found")

    with pytest.raises(AudioTranscriptionPolicyError, match="could not be resolved"):
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=_cost,
            transcriber=RecordingTranscriber(),
            transport=_audio_transport(),
            resolver=failed_resolver,
        )


def test_transcribe_episode_audio_deletes_file_after_provider_failure() -> None:
    transcriber = RecordingTranscriber(failure=RuntimeError("provider detail"))

    with pytest.raises(AudioTranscriptionProviderError, match="provider failed") as captured:
        transcribe_episode_audio(
            _episode(),
            authorized=True,
            estimate_cost=_cost,
            transcriber=transcriber,
            transport=_audio_transport(),
            resolver=_public_resolver,
        )

    assert "provider detail" not in str(captured.value)
    assert len(transcriber.paths) == 1
    assert not transcriber.paths[0].exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"connect_timeout_seconds": 0},
        {"read_timeout_seconds": 0},
        {"max_audio_bytes": 0},
        {"max_redirects": -1},
        {"max_duration_seconds": 0},
        {"max_estimated_cost_usd": Decimal("NaN")},
        {"max_estimated_cost_usd": Decimal("-0.01")},
    ],
)
def test_audio_transcription_policy_rejects_invalid_bounds(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        AudioTranscriptionPolicy(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": " "},
        {"provider": " "},
        {"model": " "},
        {"chunk_count": 0},
    ],
)
def test_provider_transcript_rejects_empty_required_fields(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "text": "Transcript",
        "provider": "Provider",
        "model": "Model",
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        ProviderTranscript(**values)  # type: ignore[arg-type]
