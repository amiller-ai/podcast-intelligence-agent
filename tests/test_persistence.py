import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from stat import S_IMODE

import pytest

import podcast_intelligence.persistence as persistence_module
from podcast_intelligence.audio_transcription import (
    EpisodeTranscript,
    ProviderTranscript,
    ProviderTranscriptPart,
    TranscriptionUsage,
)
from podcast_intelligence.episode_resolution import (
    ResolvedSpotifyEpisode,
    resolve_transcript_sources,
)
from podcast_intelligence.models import PodcastEpisode
from podcast_intelligence.persistence import (
    CHUNKER_VERSION,
    TRANSCRIPTION_PROMPT_VERSION,
    EpisodeRecord,
    PersistenceCorruptionError,
    PersistenceError,
    TranscriptStore,
    dollars_to_microusd,
    source_fingerprint,
    transcription_identity,
)


def _resolved(
    *,
    guid: str = "episode-guid",
    title: str = "Synthetic episode",
    audio_url: str = "https://cdn.example.test/episode.mp3",
) -> ResolvedSpotifyEpisode:
    episode = PodcastEpisode(
        episode_id=guid,
        title=title,
        published_at=datetime(2026, 8, 19, tzinfo=UTC),
        web_url="https://publisher.example.test/episode",
        audio_url=audio_url,
        audio_media_type="audio/mpeg",
        audio_size_bytes=12_345,
        duration_seconds=1_800,
    )
    return ResolvedSpotifyEpisode(
        spotify_episode_id="0VPwvReM2olZDWl3YOHfqh",
        spotify_url="https://open.spotify.com/episode/0VPwvReM2olZDWl3YOHfqh",
        show_title="Synthetic show",
        feed_url="https://publisher.example.test/feed.xml",
        catalog_url="https://catalog.example.test/episode",
        catalog_episode_guid=guid,
        episode=episode,
        transcript=resolve_transcript_sources(episode),
    )


def _provider_transcript() -> ProviderTranscript:
    parts = (
        ProviderTranscriptPart(
            ordinal=0,
            text="First synthetic part.",
            request_id="req_1",
            model="gpt-transcribe",
            language="en",
            usage=TranscriptionUsage(
                usage_type="tokens",
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
                input_token_details_json='{"audio_tokens":10}',
            ),
        ),
        ProviderTranscriptPart(
            ordinal=1,
            text="Second synthetic part.",
            request_id="req_2",
            model="gpt-transcribe",
            language="en",
            usage=TranscriptionUsage(usage_type="duration", audio_seconds=9.5),
        ),
    )
    return ProviderTranscript(
        text="First synthetic part.\n\nSecond synthetic part.",
        provider="openai",
        model="gpt-transcribe",
        request_ids=("req_1", "req_2"),
        language="en",
        chunk_count=2,
        parts=parts,
    )


def _episode_transcript(*, audio_bytes: bytes = b"synthetic audio") -> EpisodeTranscript:
    return EpisodeTranscript(
        episode_id="episode-guid",
        source_url="https://cdn.example.test/episode.mp3",
        source_media_type="audio/mpeg",
        duration_seconds=1_800,
        audio_bytes=len(audio_bytes),
        audio_sha256=sha256(audio_bytes).hexdigest(),
        etag='"etag-1"',
        last_modified="Wed, 19 Aug 2026 12:00:00 GMT",
        estimated_cost_usd=Decimal("0.1350"),
        transcript=_provider_transcript(),
    )


def _running_run(store: TranscriptStore) -> tuple[int, EpisodeRecord]:
    episode = store.upsert_episode(_resolved())
    run_id = store.create_run(
        episode,
        provider="openai",
        model="gpt-transcribe",
        chunker_version=CHUNKER_VERSION,
        prompt_version=TRANSCRIPTION_PROMPT_VERSION,
        estimated_cost_microusd=135_000,
    )
    store.mark_running(run_id)
    return run_id, episode


def test_fresh_database_applies_versioned_schema_and_enforces_constraints(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "podcasts.db"

    with TranscriptStore(path):
        pass

    assert path.exists()
    assert S_IMODE(path.stat().st_mode) == 0o600
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone() == (0,)
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert versions == [(1,), (2,)]
    assert {
        "feeds",
        "episodes",
        "transcription_runs",
        "transcript_parts",
        "transcripts",
    } <= tables


def test_database_upgrades_from_first_migration(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.db"
    TranscriptStore(path, target_version=1).close()

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (1,)
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'transcripts'"
            ).fetchone()
            is None
        )

    TranscriptStore(path).close()

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (2,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'transcripts'"
        ).fetchone() == ("transcripts",)


def test_failed_migration_rolls_back_all_its_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rollback.db"
    TranscriptStore(path).close()
    failing = persistence_module._Migration(
        version=3,
        statements=("CREATE TABLE should_rollback (id INTEGER)", "INVALID SQL"),
    )
    monkeypatch.setattr(
        persistence_module,
        "_MIGRATIONS",
        (*persistence_module._MIGRATIONS, failing),
    )

    with pytest.raises(PersistenceError, match="migration 3 failed"):
        TranscriptStore(path)

    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'should_rollback'"
            ).fetchone()
            is None
        )
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone() == (2,)


def test_episode_identity_is_feed_and_guid_not_title(tmp_path: Path) -> None:
    with TranscriptStore(tmp_path / "identity.db") as store:
        first = store.upsert_episode(_resolved(guid="guid-1", title="Same title"))
        second = store.upsert_episode(_resolved(guid="guid-2", title="Same title"))
        updated = store.upsert_episode(_resolved(guid="guid-1", title="Renamed title"))

    assert first.id != second.id
    assert updated.id == first.id
    assert updated.title == "Renamed title"


def test_success_round_trip_preserves_parts_usage_hashes_and_cache_keys(
    tmp_path: Path,
) -> None:
    with TranscriptStore(tmp_path / "success.db") as store:
        run_id, episode = _running_run(store)
        stored = store.persist_success(
            run_id,
            episode,
            _episode_transcript(),
            chunker_version=CHUNKER_VERSION,
            prompt_version=TRANSCRIPTION_PROMPT_VERSION,
        )
        source_cached = store.find_source_cache(
            store.upsert_episode(_resolved()),
            model="gpt-transcribe",
            chunker_version=CHUNKER_VERSION,
            prompt_version=TRANSCRIPTION_PROMPT_VERSION,
        )
        audio_cached = store.find_audio_cache(
            stored.episode_id,
            audio_sha256=stored.audio_sha256,
            model="gpt-transcribe",
            chunker_version=CHUNKER_VERSION,
            prompt_version=TRANSCRIPTION_PROMPT_VERSION,
        )

    assert stored.text == "First synthetic part.\n\nSecond synthetic part."
    assert stored.content_hash == sha256(stored.text.encode()).hexdigest()
    assert stored.audio_bytes == len(b"synthetic audio")
    assert stored.estimated_cost_microusd == 135_000
    assert stored.provider_transcript.request_ids == ("req_1", "req_2")
    assert stored.provider_transcript.parts[0].usage == TranscriptionUsage(
        usage_type="tokens",
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        input_token_details_json='{"audio_tokens":10}',
    )
    assert source_cached is not None and source_cached.run_id == run_id
    assert audio_cached is not None and audio_cached.run_id == run_id


def test_success_persistence_rolls_back_partial_parts_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "atomic.db"
    with TranscriptStore(path) as store:
        run_id, episode = _running_run(store)
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_second_part BEFORE INSERT ON transcript_parts
                WHEN NEW.ordinal = 1 BEGIN SELECT RAISE(ABORT, 'rejected'); END
                """
            )
        with pytest.raises(PersistenceError, match="atomic transcript persistence failed"):
            store.persist_success(
                run_id,
                episode,
                _episode_transcript(),
                chunker_version=CHUNKER_VERSION,
                prompt_version=TRANSCRIPTION_PROMPT_VERSION,
            )
        assert store.run_status(run_id) == "running"

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM transcript_parts WHERE run_id = ?", (run_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM transcripts WHERE run_id = ?", (run_id,)
        ).fetchone() == (0,)


def test_failed_run_has_safe_state_and_no_transcript_rows(tmp_path: Path) -> None:
    path = tmp_path / "failed.db"
    with TranscriptStore(path) as store:
        run_id, _episode = _running_run(store)
        store.mark_failed(
            run_id,
            error_code="AudioTranscriptionProviderError",
            safe_message="transcription provider failed",
        )
        assert store.run_status(run_id) == "failed"
        with pytest.raises(PersistenceError, match="not found"):
            store.get_transcript(run_id)

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT error_code, error_message FROM transcription_runs WHERE id = ?", (run_id,)
        ).fetchone()
    assert row == ("AudioTranscriptionProviderError", "transcription provider failed")


def test_content_corruption_is_detected_on_read(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    with TranscriptStore(path) as store:
        run_id, episode = _running_run(store)
        store.persist_success(
            run_id,
            episode,
            _episode_transcript(),
            chunker_version=CHUNKER_VERSION,
            prompt_version=TRANSCRIPTION_PROMPT_VERSION,
        )
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE transcripts SET text = 'tampered' WHERE run_id = ?", (run_id,)
            )
        with pytest.raises(PersistenceCorruptionError, match="assembly"):
            store.get_transcript(run_id)


def test_hash_and_currency_helpers_are_deterministic_and_validate() -> None:
    first = source_fingerprint(
        enclosure_url="https://cdn.example.test/audio.mp3",
        declared_byte_length=100,
        etag='"abc"',
        last_modified=None,
        duration_seconds=60,
    )
    second = source_fingerprint(
        enclosure_url="https://cdn.example.test/audio.mp3",
        declared_byte_length=100,
        etag='"abc"',
        last_modified=None,
        duration_seconds=60,
    )
    identity = transcription_identity(
        audio_sha256="a" * 64,
        model="gpt-transcribe",
        chunker_version=CHUNKER_VERSION,
        prompt_version=TRANSCRIPTION_PROMPT_VERSION,
    )

    assert first == second
    assert len(first) == len(identity) == 64
    assert dollars_to_microusd(Decimal("0.3432")) == 343_200
    with pytest.raises(ValueError):
        dollars_to_microusd(Decimal("0.0000001"))
    with pytest.raises(ValueError):
        dollars_to_microusd(Decimal("NaN"))
