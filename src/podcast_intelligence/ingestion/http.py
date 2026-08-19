"""Guarded HTTP retrieval for podcast RSS feeds."""

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx2

from podcast_intelligence.ingestion.network import (
    HostResolver,
    PublicHttpDestinationError,
    resolve_host,
    validate_public_http_destination,
)
from podcast_intelligence.ingestion.rss import parse_rss_feed
from podcast_intelligence.models import PodcastFeed

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_XML_MEDIA_TYPES = frozenset({"application/rss+xml", "application/xml", "text/xml"})


class RssFeedRetrievalError(RuntimeError):
    """Base class for application-owned RSS retrieval failures."""


class RssFeedPolicyError(RssFeedRetrievalError):
    """Raised when a URL or resolved destination violates retrieval policy."""


class RssFeedTransportError(RssFeedRetrievalError):
    """Raised when the HTTP transport cannot complete a request."""


class RssFeedHttpError(RssFeedRetrievalError):
    """Raised when a server returns an unsuccessful HTTP response."""


class RssFeedTooLargeError(RssFeedRetrievalError):
    """Raised when an RSS response exceeds the configured byte limit."""


class RssFeedContentTypeError(RssFeedRetrievalError):
    """Raised when a response declares a clearly incompatible media type."""


@dataclass(frozen=True, slots=True)
class RssRetrievalPolicy:
    """Explicit safety and resource bounds for RSS retrieval."""

    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 10.0
    max_response_bytes: int = 2_000_000
    max_redirects: int = 3

    def __post_init__(self) -> None:
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("RSS retrieval timeouts must be positive")
        if self.max_response_bytes <= 0:
            raise ValueError("RSS response size limit must be positive")
        if self.max_redirects < 0:
            raise ValueError("RSS redirect limit must not be negative")


_DEFAULT_POLICY = RssRetrievalPolicy()


def retrieve_rss_feed(
    url: str,
    *,
    policy: RssRetrievalPolicy = _DEFAULT_POLICY,
    transport: httpx2.BaseTransport | None = None,
    resolver: HostResolver | None = None,
) -> PodcastFeed:
    """Retrieve a bounded public RSS URL and parse it into a podcast feed."""

    resolve_destination = resolver or resolve_host
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
        ) as client:
            payload = _retrieve_payload(
                client,
                url,
                policy=policy,
                resolver=resolve_destination,
            )
    except httpx2.RequestError as error:
        raise RssFeedTransportError(f"RSS request failed: {error}") from error

    return parse_rss_feed(payload)


def _retrieve_payload(
    client: httpx2.Client,
    url: str,
    *,
    policy: RssRetrievalPolicy,
    resolver: HostResolver,
) -> bytes:
    current_url = url
    redirects_followed = 0

    while True:
        _validate_destination(current_url, resolver=resolver)

        with client.stream(
            "GET",
            current_url,
            headers={
                "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
                "User-Agent": "Podcast-Intelligence/0.1 (+personal feed reader)",
            },
        ) as response:
            if response.status_code in _REDIRECT_STATUSES:
                if redirects_followed >= policy.max_redirects:
                    raise RssFeedPolicyError(
                        f"RSS redirect limit of {policy.max_redirects} exceeded"
                    )
                location = response.headers.get("location")
                if not location:
                    raise RssFeedHttpError("RSS redirect response is missing a Location header")
                current_url = urljoin(str(response.url), location)
                redirects_followed += 1
                continue

            if not 200 <= response.status_code < 300:
                raise RssFeedHttpError(
                    f"RSS request failed with HTTP status {response.status_code}"
                )

            _validate_content_type(response.headers.get("content-type"))
            _validate_content_length(
                response.headers.get("content-length"),
                maximum=policy.max_response_bytes,
            )
            return _read_bounded(response, maximum=policy.max_response_bytes)


def _validate_destination(url: str, *, resolver: HostResolver) -> None:
    try:
        validate_public_http_destination(
            url,
            resolver=resolver,
            resource_name="RSS",
        )
    except PublicHttpDestinationError as error:
        message = str(error)
        if message == "destination host could not be resolved":
            message = "RSS destination host could not be resolved"
        raise RssFeedPolicyError(message) from error


def _validate_content_type(header: str | None) -> None:
    if header is None:
        return

    media_type = header.partition(";")[0].strip().lower()
    if media_type not in _XML_MEDIA_TYPES and not media_type.endswith("+xml"):
        raise RssFeedContentTypeError(
            f"RSS response has incompatible Content-Type {media_type or '<empty>'}"
        )


def _validate_content_length(header: str | None, *, maximum: int) -> None:
    if header is None:
        return
    try:
        declared_length = int(header)
    except ValueError:
        return
    if declared_length > maximum:
        raise RssFeedTooLargeError(f"RSS response exceeds the {maximum}-byte size limit")


def _read_bounded(response: httpx2.Response, *, maximum: int) -> bytes:
    payload = bytearray()
    for chunk in response.iter_bytes():
        if len(payload) + len(chunk) > maximum:
            raise RssFeedTooLargeError(f"RSS response exceeds the {maximum}-byte size limit")
        payload.extend(chunk)
    return bytes(payload)
