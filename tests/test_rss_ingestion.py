from datetime import UTC, datetime

import pytest

from podcast_intelligence.ingestion.rss import RssFeedParseError, parse_rss_feed


def test_parse_rss_feed_normalizes_podcast_and_episode_metadata() -> None:
    feed = parse_rss_feed(
        """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
          <channel>
            <title> Practical Intelligence </title>
            <description>A synthetic test feed.</description>
            <link>https://example.com/podcast</link>
            <item>
              <guid>episode-001</guid>
              <title>Building the foundation</title>
              <description><![CDATA[The <strong>first</strong> episode.]]></description>
              <link>https://example.com/episodes/1</link>
              <pubDate>Tue, 19 Aug 2025 10:30:00 -0700</pubDate>
              <enclosure url="https://cdn.example.com/1.mp3" type="audio/mpeg" />
              <itunes:duration>01:02:03</itunes:duration>
            </item>
            <item>
              <title>Second episode</title>
              <pubDate>not-a-date</pubDate>
              <enclosure url="https://cdn.example.com/2.mp3" type="audio/mpeg" />
              <itunes:duration>95</itunes:duration>
            </item>
          </channel>
        </rss>
        """
    )

    assert feed.title == "Practical Intelligence"
    assert feed.description == "A synthetic test feed."
    assert feed.website_url == "https://example.com/podcast"
    assert len(feed.episodes) == 2

    first, second = feed.episodes
    assert first.episode_id == "episode-001"
    assert first.description == "The <strong>first</strong> episode."
    assert first.published_at == datetime(2025, 8, 19, 17, 30, tzinfo=UTC)
    assert first.audio_url == "https://cdn.example.com/1.mp3"
    assert first.audio_media_type == "audio/mpeg"
    assert first.duration_seconds == 3723

    assert second.episode_id == "https://cdn.example.com/2.mp3"
    assert second.published_at is None
    assert second.duration_seconds == 95


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        ("07:30", 450),
        ("1:02:03", 3723),
        ("1:90", None),
        ("1:60:00", None),
        ("one minute", None),
        ("-1", None),
        ("1:2:3:4", None),
    ],
)
def test_parse_rss_feed_handles_duration_formats(duration: str, expected: int | None) -> None:
    feed = parse_rss_feed(
        f"""<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
        <channel><title>Test feed</title><item><title>Episode</title>
        <itunes:duration>{duration}</itunes:duration></item></channel></rss>"""
    )

    assert feed.episodes[0].duration_seconds == expected


def test_parse_rss_feed_generates_stable_id_when_source_has_no_identifier() -> None:
    xml = """<rss version="2.0"><channel><title>Test feed</title><item>
    <title>Unidentified episode</title><pubDate>Tue, 19 Aug 2025 10:30:00</pubDate>
    </item></channel></rss>"""

    first = parse_rss_feed(xml).episodes[0]
    second = parse_rss_feed(xml).episodes[0]

    assert first.episode_id == second.episode_id
    assert first.episode_id.startswith("generated:")
    assert first.published_at == datetime(2025, 8, 19, 10, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        ("", "must not be empty"),
        ("<rss>", "not safe, well-formed XML"),
        ("<feed />", "expected an RSS 2.0 root"),
        ("<rss version='2.0' />", "missing a channel"),
        ("<rss><channel /></rss>", "channel is missing required title"),
        (
            "<rss><channel><title>Feed</title><item /></channel></rss>",
            "item 1 is missing required title",
        ),
    ],
)
def test_parse_rss_feed_rejects_invalid_documents(xml: str, message: str) -> None:
    with pytest.raises(RssFeedParseError, match=message):
        parse_rss_feed(xml)


def test_parse_rss_feed_rejects_xml_entities() -> None:
    xml = """<!DOCTYPE rss [<!ENTITY unsafe SYSTEM "file:///etc/passwd">]>
    <rss><channel><title>&unsafe;</title></channel></rss>"""

    with pytest.raises(RssFeedParseError, match="not safe, well-formed XML"):
        parse_rss_feed(xml)


def test_parse_rss_feed_accepts_xml_bytes() -> None:
    feed = parse_rss_feed(b"<rss><channel><title>Byte feed</title></channel></rss>")

    assert feed.title == "Byte feed"
