"""Safe, network-free parsing for RSS 2.0 podcast feeds."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from podcast_intelligence.models import PodcastEpisode, PodcastFeed, TranscriptReference

_PODCAST_NAMESPACE = "https://podcastindex.org/namespace/1.0"


class RssFeedParseError(ValueError):
    """Raised when input is not a structurally valid RSS 2.0 podcast feed."""


def parse_rss_feed(xml_text: str | bytes) -> PodcastFeed:
    """Parse RSS 2.0 XML into normalized, immutable podcast models."""

    if not xml_text.strip():
        raise RssFeedParseError("RSS document must not be empty")

    try:
        root = fromstring(xml_text)
    except (DefusedXmlException, ParseError) as error:
        raise RssFeedParseError("RSS document is not safe, well-formed XML") from error

    if _local_name(root.tag) != "rss":
        raise RssFeedParseError("expected an RSS 2.0 root element")

    channel = _child(root, "channel")
    if channel is None:
        raise RssFeedParseError("RSS document is missing a channel")

    feed_title = _required_text(channel, "title", context="RSS channel")
    episodes = tuple(
        _parse_episode(item, index=index)
        for index, item in enumerate(_children(channel, "item"), start=1)
    )

    return PodcastFeed(
        title=feed_title,
        description=_text(channel, "description"),
        website_url=_text(channel, "link"),
        episodes=episodes,
    )


def _parse_episode(item: Element, *, index: int) -> PodcastEpisode:
    title = _required_text(item, "title", context=f"RSS item {index}")
    published_text = _text(item, "pubDate")
    published_at = _parse_published_at(published_text)
    web_url = _text(item, "link")

    enclosure = _child(item, "enclosure")
    audio_url = _attribute(enclosure, "url")
    audio_media_type = _attribute(enclosure, "type")
    guid = _text(item, "guid")

    return PodcastEpisode(
        episode_id=_episode_id(
            guid=guid,
            audio_url=audio_url,
            web_url=web_url,
            title=title,
            published_text=published_text,
        ),
        title=title,
        description=_text(item, "description"),
        published_at=published_at,
        web_url=web_url,
        audio_url=audio_url,
        audio_media_type=audio_media_type,
        duration_seconds=_parse_duration(_text(item, "duration")),
        transcript_references=_parse_transcript_references(item),
    )


def _parse_transcript_references(item: Element) -> tuple[TranscriptReference, ...]:
    references: list[TranscriptReference] = []
    for element in item:
        if element.tag != f"{{{_PODCAST_NAMESPACE}}}transcript":
            continue
        url = _attribute(element, "url")
        media_type = _attribute(element, "type")
        if url is None or media_type is None:
            continue
        references.append(
            TranscriptReference(
                url=url,
                media_type=media_type.lower(),
                language=_attribute(element, "language"),
                relation=_attribute(element, "rel"),
            )
        )
    return tuple(references)


def _episode_id(
    *,
    guid: str | None,
    audio_url: str | None,
    web_url: str | None,
    title: str,
    published_text: str | None,
) -> str:
    if guid:
        return guid
    if audio_url:
        return audio_url
    if web_url:
        return web_url

    fallback = "\x1f".join((title, published_text or ""))
    return f"generated:{sha256(fallback.encode()).hexdigest()}"


def _parse_published_at(value: str | None) -> datetime | None:
    if value is None:
        return None

    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_duration(value: str | None) -> int | None:
    if value is None:
        return None

    parts = value.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return None

    if any(number < 0 for number in numbers):
        return None
    if len(numbers) == 1:
        return numbers[0]
    if len(numbers) == 2 and numbers[1] < 60:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 3 and numbers[1] < 60 and numbers[2] < 60:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    return None


def _required_text(parent: Element, name: str, *, context: str) -> str:
    value = _text(parent, name)
    if value is None:
        raise RssFeedParseError(f"{context} is missing required {name}")
    return value


def _text(parent: Element, name: str) -> str | None:
    child = _child(parent, name)
    if child is None:
        return None

    value = "".join(child.itertext()).strip()
    return value or None


def _attribute(element: Element | None, name: str) -> str | None:
    if element is None:
        return None
    value = element.get(name, "").strip()
    return value or None


def _child(parent: Element, name: str) -> Element | None:
    for child in parent:
        if child.tag == name:
            return child
    return next((child for child in parent if _local_name(child.tag) == name), None)


def _children(parent: Element, name: str) -> tuple[Element, ...]:
    return tuple(child for child in parent if _local_name(child.tag) == name)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]
