import socket
from collections.abc import Callable

import httpx2
import pytest

from podcast_intelligence.ingestion.http import (
    RssFeedContentTypeError,
    RssFeedHttpError,
    RssFeedPolicyError,
    RssFeedTooLargeError,
    RssFeedTransportError,
    RssRetrievalPolicy,
    retrieve_rss_feed,
)
from podcast_intelligence.models import PodcastFeed

_FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Fetched feed</title>
<item><guid>episode-1</guid><title>Fetched episode</title></item>
</channel></rss>"""


def _public_resolver(_host: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def _transport(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.MockTransport:
    return httpx2.MockTransport(handler)


def test_retrieve_rss_feed_applies_bounds_and_returns_typed_feed() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            headers={"Content-Type": "application/rss+xml; charset=utf-8"},
            content=_FEED_XML,
        )

    policy = RssRetrievalPolicy(
        connect_timeout_seconds=1.5,
        read_timeout_seconds=4.0,
        max_response_bytes=10_000,
    )
    feed = retrieve_rss_feed(
        "https://feeds.example.test/podcast.xml",
        policy=policy,
        transport=_transport(handler),
        resolver=_public_resolver,
    )

    assert isinstance(feed, PodcastFeed)
    assert feed.title == "Fetched feed"
    assert feed.episodes[0].episode_id == "episode-1"
    assert len(requests) == 1
    assert requests[0].headers["accept"].startswith("application/rss+xml")
    assert requests[0].headers["user-agent"].startswith("Podcast-Intelligence/")
    assert requests[0].extensions["timeout"] == {
        "connect": 1.5,
        "read": 4.0,
        "write": 1.5,
        "pool": 1.5,
    }


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://feeds.example.test/feed", "HTTP or HTTPS"),
        ("https://user:secret@feeds.example.test/feed", "embedded credentials"),
        ("https:///feed", "include a host"),
        ("https://feeds.example.test:invalid/feed", "invalid"),
    ],
)
def test_retrieve_rss_feed_rejects_invalid_urls_before_request(
    url: str,
    message: str,
) -> None:
    requested = False

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal requested
        requested = True
        return httpx2.Response(200, content=_FEED_XML)

    with pytest.raises(RssFeedPolicyError, match=message):
        retrieve_rss_feed(
            url,
            transport=_transport(handler),
            resolver=_public_resolver,
        )

    assert requested is False


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/feed",
        "http://10.0.0.1/feed",
        "http://169.254.169.254/feed",
        "http://224.0.0.1/feed",
        "http://[::1]/feed",
        "http://[fc00::1]/feed",
    ],
)
def test_retrieve_rss_feed_rejects_unsafe_literal_addresses(url: str) -> None:
    with pytest.raises(RssFeedPolicyError, match="public IP addresses"):
        retrieve_rss_feed(
            url,
            transport=_transport(lambda _request: httpx2.Response(200)),
            resolver=_public_resolver,
        )


def test_retrieve_rss_feed_rejects_host_if_any_resolved_address_is_unsafe() -> None:
    def mixed_resolver(_host: str, _port: int) -> tuple[str, ...]:
        return ("93.184.216.34", "192.168.1.10")

    with pytest.raises(RssFeedPolicyError, match="public IP addresses"):
        retrieve_rss_feed(
            "https://feeds.example.test/feed",
            transport=_transport(lambda _request: httpx2.Response(200)),
            resolver=mixed_resolver,
        )


def test_retrieve_rss_feed_converts_resolution_failure_to_policy_error() -> None:
    def failed_resolver(_host: str, _port: int) -> tuple[str, ...]:
        raise socket.gaierror("not found")

    with pytest.raises(RssFeedPolicyError, match="could not be resolved"):
        retrieve_rss_feed(
            "https://missing.example.test/feed",
            transport=_transport(lambda _request: httpx2.Response(200)),
            resolver=failed_resolver,
        )


def test_retrieve_rss_feed_revalidates_redirect_before_following() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        return httpx2.Response(302, headers={"Location": "http://127.0.0.1/private"})

    with pytest.raises(RssFeedPolicyError, match="public IP addresses"):
        retrieve_rss_feed(
            "https://feeds.example.test/feed",
            transport=_transport(handler),
            resolver=_public_resolver,
        )

    assert requested_urls == ["https://feeds.example.test/feed"]


def test_retrieve_rss_feed_follows_bounded_public_redirects() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/feed":
            return httpx2.Response(301, headers={"Location": "/final.xml"})
        return httpx2.Response(
            200,
            headers={"Content-Type": "text/xml"},
            content=_FEED_XML,
        )

    feed = retrieve_rss_feed(
        "https://feeds.example.test/feed",
        transport=_transport(handler),
        resolver=_public_resolver,
    )

    assert feed.title == "Fetched feed"
    assert requested_urls == [
        "https://feeds.example.test/feed",
        "https://feeds.example.test/final.xml",
    ]


def test_retrieve_rss_feed_enforces_redirect_limit() -> None:
    request_count = 0

    def handler(_request: httpx2.Request) -> httpx2.Response:
        nonlocal request_count
        request_count += 1
        return httpx2.Response(302, headers={"Location": "/next"})

    with pytest.raises(RssFeedPolicyError, match="redirect limit of 1 exceeded"):
        retrieve_rss_feed(
            "https://feeds.example.test/feed",
            policy=RssRetrievalPolicy(max_redirects=1),
            transport=_transport(handler),
            resolver=_public_resolver,
        )

    assert request_count == 2


def test_retrieve_rss_feed_rejects_redirect_without_location() -> None:
    with pytest.raises(RssFeedHttpError, match="missing a Location"):
        retrieve_rss_feed(
            "https://feeds.example.test/feed",
            transport=_transport(lambda _request: httpx2.Response(302)),
            resolver=_public_resolver,
        )


@pytest.mark.parametrize(
    "content_type",
    ["application/rss+xml", "application/xml", "text/xml", "application/custom+xml"],
)
def test_retrieve_rss_feed_accepts_xml_content_types(content_type: str) -> None:
    feed = retrieve_rss_feed(
        "https://feeds.example.test/feed",
        transport=_transport(
            lambda _request: httpx2.Response(
                200,
                headers={"Content-Type": content_type},
                content=_FEED_XML,
            )
        ),
        resolver=_public_resolver,
    )

    assert feed.title == "Fetched feed"


def test_retrieve_rss_feed_allows_missing_content_type_for_misconfigured_feeds() -> None:
    feed = retrieve_rss_feed(
        "https://feeds.example.test/feed",
        transport=_transport(lambda _request: httpx2.Response(200, content=_FEED_XML)),
        resolver=_public_resolver,
    )

    assert feed.title == "Fetched feed"


def test_retrieve_rss_feed_rejects_incompatible_content_type() -> None:
    with pytest.raises(RssFeedContentTypeError, match="text/html"):
        retrieve_rss_feed(
            "https://feeds.example.test/feed",
            transport=_transport(
                lambda _request: httpx2.Response(
                    200,
                    headers={"Content-Type": "text/html; charset=utf-8"},
                    content=b"<html></html>",
                )
            ),
            resolver=_public_resolver,
        )


def test_retrieve_rss_feed_rejects_declared_oversized_response() -> None:
    with pytest.raises(RssFeedTooLargeError, match="64-byte size limit"):
        retrieve_rss_feed(
            "https://feeds.example.test/feed",
            policy=RssRetrievalPolicy(max_response_bytes=64),
            transport=_transport(
                lambda _request: httpx2.Response(
                    200,
                    headers={"Content-Type": "application/rss+xml", "Content-Length": "65"},
                    content=b"small",
                )
            ),
            resolver=_public_resolver,
        )


def test_retrieve_rss_feed_stops_stream_that_exceeds_size_limit() -> None:
    with pytest.raises(RssFeedTooLargeError, match="64-byte size limit"):
        retrieve_rss_feed(
            "https://feeds.example.test/feed",
            policy=RssRetrievalPolicy(max_response_bytes=64),
            transport=_transport(
                lambda _request: httpx2.Response(
                    200,
                    headers={"Content-Type": "application/rss+xml", "Content-Length": "1"},
                    content=b"x" * 65,
                )
            ),
            resolver=_public_resolver,
        )


def test_retrieve_rss_feed_converts_http_failure() -> None:
    with pytest.raises(RssFeedHttpError, match="HTTP status 503"):
        retrieve_rss_feed(
            "https://feeds.example.test/feed",
            transport=_transport(lambda _request: httpx2.Response(503)),
            resolver=_public_resolver,
        )


def test_retrieve_rss_feed_converts_transport_failure() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    with pytest.raises(RssFeedTransportError, match="connection refused"):
        retrieve_rss_feed(
            "https://feeds.example.test/feed",
            transport=_transport(handler),
            resolver=_public_resolver,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"connect_timeout_seconds": 0},
        {"read_timeout_seconds": 0},
        {"max_response_bytes": 0},
        {"max_redirects": -1},
    ],
)
def test_rss_retrieval_policy_rejects_invalid_bounds(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        RssRetrievalPolicy(**kwargs)
