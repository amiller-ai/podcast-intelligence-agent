"""Provider-independent podcast domain models."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PodcastEpisode:
    """Normalized metadata for one podcast episode."""

    episode_id: str
    title: str
    description: str | None = None
    published_at: datetime | None = None
    web_url: str | None = None
    audio_url: str | None = None
    audio_media_type: str | None = None
    duration_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class PodcastFeed:
    """Normalized metadata for a podcast and its episodes."""

    title: str
    description: str | None
    website_url: str | None
    episodes: tuple[PodcastEpisode, ...]
