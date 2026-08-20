"""SQLite persistence boundary for podcast identities and transcripts."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from podcast_intelligence.audio_transcription import (
    EpisodeTranscript,
    ProviderTranscript,
    ProviderTranscriptPart,
    TranscriptionUsage,
)
from podcast_intelligence.episode_resolution import ResolvedSpotifyEpisode
from podcast_intelligence.intelligence_models import (
    EpisodeAnalysis,
    EpisodeMetadata,
    TranscriptSearchHit,
    TranscriptSegment,
    iter_analysis_evidence,
    segment_transcript_text,
    validate_analysis_evidence,
)

CHUNKER_VERSION = "ffmpeg-segment-v1"
TRANSCRIPTION_PROMPT_VERSION = "continuity-500-v1"


class PersistenceError(RuntimeError):
    """Base class for application-owned persistence failures."""


class PersistenceCorruptionError(PersistenceError):
    """Raised when persisted state violates the application contract."""


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    """Database identity and current source metadata for one RSS episode."""

    id: int
    feed_url: str
    rss_guid: str
    title: str
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class StoredTranscript:
    """Verified canonical transcript loaded from SQLite."""

    run_id: int
    transcript_id: int
    episode_id: int
    feed_url: str
    rss_guid: str
    spotify_episode_id: str | None
    episode_title: str
    source_fingerprint: str
    audio_sha256: str
    audio_bytes: int
    estimated_cost_microusd: int
    provider_transcript: ProviderTranscript
    content_hash: str
    created_at: str

    @property
    def text(self) -> str:
        return self.provider_transcript.text


@dataclass(frozen=True, slots=True)
class StoredEpisodeAnalysis:
    """Validated canonical structured analysis loaded from SQLite."""

    run_id: int
    analysis_id: int
    transcript_id: int
    cache_identity: str
    analysis_type: str
    model: str
    prompt_version: str
    schema_version: str
    segmenter_version: str
    response_id: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    analysis: EpisodeAnalysis
    created_at: str


@dataclass(frozen=True, slots=True)
class _Migration:
    version: int
    statements: tuple[str, ...]


_MIGRATIONS = (
    _Migration(
        version=1,
        statements=(
            """
            CREATE TABLE feeds (
                id INTEGER PRIMARY KEY,
                canonical_url TEXT NOT NULL UNIQUE CHECK (length(canonical_url) > 0),
                title TEXT NOT NULL CHECK (length(title) > 0),
                website_url TEXT,
                observed_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE episodes (
                id INTEGER PRIMARY KEY,
                feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE RESTRICT,
                rss_guid TEXT NOT NULL CHECK (length(rss_guid) > 0),
                spotify_episode_id TEXT,
                title TEXT NOT NULL CHECK (length(title) > 0),
                published_at TEXT,
                web_url TEXT,
                enclosure_url TEXT,
                enclosure_media_type TEXT,
                declared_byte_length INTEGER CHECK (
                    declared_byte_length IS NULL OR declared_byte_length >= 0
                ),
                etag TEXT,
                last_modified TEXT,
                duration_seconds INTEGER CHECK (
                    duration_seconds IS NULL OR duration_seconds >= 0
                ),
                source_fingerprint TEXT NOT NULL CHECK (length(source_fingerprint) = 64),
                observed_at TEXT NOT NULL,
                UNIQUE (feed_id, rss_guid)
            )
            """,
            """
            CREATE TABLE transcription_runs (
                id INTEGER PRIMARY KEY,
                episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE RESTRICT,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'running', 'succeeded', 'failed')
                ),
                source_fingerprint TEXT NOT NULL CHECK (length(source_fingerprint) = 64),
                transcription_identity TEXT CHECK (
                    transcription_identity IS NULL OR length(transcription_identity) = 64
                ),
                provider TEXT NOT NULL CHECK (length(provider) > 0),
                model TEXT NOT NULL CHECK (length(model) > 0),
                chunker_version TEXT NOT NULL CHECK (length(chunker_version) > 0),
                prompt_version TEXT NOT NULL CHECK (length(prompt_version) > 0),
                estimated_cost_microusd INTEGER NOT NULL CHECK (estimated_cost_microusd >= 0),
                audio_bytes INTEGER CHECK (audio_bytes IS NULL OR audio_bytes >= 0),
                audio_sha256 TEXT CHECK (audio_sha256 IS NULL OR length(audio_sha256) = 64),
                error_code TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                CHECK (status != 'failed' OR (
                    error_code IS NOT NULL AND error_message IS NOT NULL
                )),
                CHECK (status != 'succeeded' OR (
                    transcription_identity IS NOT NULL AND audio_bytes IS NOT NULL
                    AND audio_sha256 IS NOT NULL AND completed_at IS NOT NULL
                ))
            )
            """,
        ),
    ),
    _Migration(
        version=2,
        statements=(
            """
            CREATE TABLE transcript_parts (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES transcription_runs(id) ON DELETE RESTRICT,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                request_id TEXT,
                model TEXT NOT NULL CHECK (length(model) > 0),
                language TEXT,
                text TEXT NOT NULL CHECK (length(text) > 0),
                usage_type TEXT,
                input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
                output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
                total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
                audio_seconds REAL CHECK (audio_seconds IS NULL OR audio_seconds >= 0),
                input_token_details_json TEXT,
                UNIQUE (run_id, ordinal)
            )
            """,
            """
            CREATE TABLE transcripts (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL UNIQUE REFERENCES transcription_runs(id) ON DELETE RESTRICT,
                text TEXT NOT NULL CHECK (length(text) > 0),
                content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX transcription_runs_source_cache
            ON transcription_runs (
                episode_id, source_fingerprint, model, chunker_version, prompt_version, status
            )
            """,
            """
            CREATE INDEX transcription_runs_audio_cache
            ON transcription_runs (
                episode_id, transcription_identity, status
            )
            """,
        ),
    ),
    _Migration(
        version=3,
        statements=(
            """
            CREATE TABLE transcript_segments (
                segment_id TEXT PRIMARY KEY CHECK (length(segment_id) = 64),
                transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
                episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE RESTRICT,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
                char_start INTEGER NOT NULL CHECK (char_start >= 0),
                char_end INTEGER NOT NULL CHECK (char_end > char_start),
                text TEXT NOT NULL CHECK (length(text) > 0),
                text_hash TEXT NOT NULL CHECK (length(text_hash) = 64),
                transcript_content_hash TEXT NOT NULL CHECK (
                    length(transcript_content_hash) = 64
                ),
                segmenter_version TEXT NOT NULL CHECK (length(segmenter_version) > 0),
                created_at TEXT NOT NULL,
                UNIQUE (transcript_id, segmenter_version, ordinal)
            )
            """,
            """
            CREATE VIRTUAL TABLE transcript_segments_fts USING fts5(
                segment_id UNINDEXED,
                transcript_id UNINDEXED,
                episode_id UNINDEXED,
                text,
                tokenize = 'unicode61'
            )
            """,
            """
            CREATE TABLE analysis_runs (
                id INTEGER PRIMARY KEY,
                transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE RESTRICT,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'running', 'succeeded', 'failed')
                ),
                cache_identity TEXT NOT NULL CHECK (length(cache_identity) = 64),
                analysis_type TEXT NOT NULL CHECK (length(analysis_type) > 0),
                model TEXT NOT NULL CHECK (length(model) > 0),
                prompt_version TEXT NOT NULL CHECK (length(prompt_version) > 0),
                schema_version TEXT NOT NULL CHECK (length(schema_version) > 0),
                segmenter_version TEXT NOT NULL CHECK (length(segmenter_version) > 0),
                response_id TEXT,
                input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens >= 0),
                output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens >= 0),
                total_tokens INTEGER CHECK (total_tokens IS NULL OR total_tokens >= 0),
                error_code TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                CHECK (status != 'failed' OR (
                    error_code IS NOT NULL AND error_message IS NOT NULL
                )),
                CHECK (status != 'succeeded' OR (
                    response_id IS NOT NULL AND completed_at IS NOT NULL
                ))
            )
            """,
            """
            CREATE TABLE episode_analyses (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL UNIQUE REFERENCES analysis_runs(id) ON DELETE RESTRICT,
                output_json TEXT NOT NULL CHECK (length(output_json) > 0),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE analysis_evidence (
                id INTEGER PRIMARY KEY,
                analysis_id INTEGER NOT NULL REFERENCES episode_analyses(id) ON DELETE CASCADE,
                item_path TEXT NOT NULL CHECK (length(item_path) > 0),
                segment_id TEXT NOT NULL REFERENCES transcript_segments(segment_id)
                    ON DELETE RESTRICT,
                quote TEXT NOT NULL CHECK (length(quote) > 0),
                UNIQUE (analysis_id, item_path, segment_id, quote)
            )
            """,
            """
            CREATE INDEX analysis_runs_cache
            ON analysis_runs (transcript_id, cache_identity, status)
            """,
        ),
    ),
)


class TranscriptStore:
    """Own a SQLite connection with explicit migrations and transactions."""

    def __init__(self, database_path: Path, *, target_version: int | None = None) -> None:
        self.database_path = database_path
        connection: sqlite3.Connection | None = None
        try:
            in_memory = str(database_path) == ":memory:"
            if not in_memory:
                database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            existed = in_memory or database_path.exists()
            connection = sqlite3.connect(database_path, isolation_level=None)
            self._connection = connection
            if not existed and not in_memory:
                database_path.chmod(0o600)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._migrate(target_version=target_version)
        except (OSError, sqlite3.Error) as error:
            if connection is not None:
                connection.close()
            raise PersistenceError("SQLite initialization failed") from error
        except Exception:
            if connection is not None:
                connection.close()
            raise

    def __enter__(self) -> TranscriptStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a transaction that always rolls back on failure."""

        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def _migrate(self, *, target_version: int | None) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                applied_at TEXT NOT NULL
            )
            """
        )
        latest = max(migration.version for migration in _MIGRATIONS)
        desired = latest if target_version is None else target_version
        if desired < 0 or desired > latest:
            raise PersistenceError("invalid target schema version")
        row = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()
        current = int(row["version"])
        if current > latest:
            raise PersistenceError("database schema is newer than this application")
        for migration in _MIGRATIONS:
            if not current < migration.version <= desired:
                continue
            try:
                with self.transaction() as connection:
                    for statement in migration.statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (migration.version, _utc_now()),
                    )
            except sqlite3.Error as error:
                raise PersistenceError(f"SQLite migration {migration.version} failed") from error

    def upsert_episode(self, resolved: ResolvedSpotifyEpisode) -> EpisodeRecord:
        """Persist verified feed/episode identity and return its cache metadata."""

        episode = resolved.episode
        now = _utc_now()
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO feeds (canonical_url, title, website_url, observed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(canonical_url) DO UPDATE SET
                    title = excluded.title,
                    website_url = excluded.website_url,
                    observed_at = excluded.observed_at
                """,
                (resolved.feed_url, resolved.show_title, None, now),
            )
            feed_row = connection.execute(
                "SELECT id FROM feeds WHERE canonical_url = ?", (resolved.feed_url,)
            ).fetchone()
            feed_id = int(feed_row["id"])
            existing = connection.execute(
                """
                SELECT id, enclosure_url, declared_byte_length, duration_seconds,
                       etag, last_modified
                FROM episodes WHERE feed_id = ? AND rss_guid = ?
                """,
                (feed_id, resolved.catalog_episode_guid),
            ).fetchone()
            same_source = bool(
                existing is not None
                and existing["enclosure_url"] == episode.audio_url
                and existing["declared_byte_length"] == episode.audio_size_bytes
                and existing["duration_seconds"] == episode.duration_seconds
            )
            etag = existing["etag"] if same_source else None
            last_modified = existing["last_modified"] if same_source else None
            fingerprint = source_fingerprint(
                enclosure_url=episode.audio_url,
                declared_byte_length=episode.audio_size_bytes,
                etag=etag,
                last_modified=last_modified,
                duration_seconds=episode.duration_seconds,
            )
            connection.execute(
                """
                INSERT INTO episodes (
                    feed_id, rss_guid, spotify_episode_id, title, published_at, web_url,
                    enclosure_url, enclosure_media_type, declared_byte_length, etag,
                    last_modified, duration_seconds, source_fingerprint, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(feed_id, rss_guid) DO UPDATE SET
                    spotify_episode_id = excluded.spotify_episode_id,
                    title = excluded.title,
                    published_at = excluded.published_at,
                    web_url = excluded.web_url,
                    enclosure_url = excluded.enclosure_url,
                    enclosure_media_type = excluded.enclosure_media_type,
                    declared_byte_length = excluded.declared_byte_length,
                    etag = excluded.etag,
                    last_modified = excluded.last_modified,
                    duration_seconds = excluded.duration_seconds,
                    source_fingerprint = excluded.source_fingerprint,
                    observed_at = excluded.observed_at
                """,
                (
                    feed_id,
                    resolved.catalog_episode_guid,
                    resolved.spotify_episode_id,
                    episode.title,
                    episode.published_at.isoformat() if episode.published_at else None,
                    episode.web_url,
                    episode.audio_url,
                    episode.audio_media_type,
                    episode.audio_size_bytes,
                    etag,
                    last_modified,
                    episode.duration_seconds,
                    fingerprint,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT e.id, f.canonical_url, e.rss_guid, e.title, e.source_fingerprint
                FROM episodes e JOIN feeds f ON f.id = e.feed_id
                WHERE e.feed_id = ? AND e.rss_guid = ?
                """,
                (feed_id, resolved.catalog_episode_guid),
            ).fetchone()
        return EpisodeRecord(
            id=int(row["id"]),
            feed_url=str(row["canonical_url"]),
            rss_guid=str(row["rss_guid"]),
            title=str(row["title"]),
            source_fingerprint=str(row["source_fingerprint"]),
        )

    def find_source_cache(
        self,
        episode: EpisodeRecord,
        *,
        model: str,
        chunker_version: str,
        prompt_version: str,
    ) -> StoredTranscript | None:
        row = self._connection.execute(
            """
            SELECT id FROM transcription_runs
            WHERE episode_id = ? AND source_fingerprint = ? AND model = ?
              AND chunker_version = ? AND prompt_version = ? AND status = 'succeeded'
            ORDER BY id DESC LIMIT 1
            """,
            (
                episode.id,
                episode.source_fingerprint,
                model,
                chunker_version,
                prompt_version,
            ),
        ).fetchone()
        return None if row is None else self.get_transcript(int(row["id"]))

    def find_audio_cache(
        self,
        episode_id: int,
        *,
        audio_sha256: str,
        model: str,
        chunker_version: str,
        prompt_version: str,
    ) -> StoredTranscript | None:
        identity = transcription_identity(
            audio_sha256=audio_sha256,
            model=model,
            chunker_version=chunker_version,
            prompt_version=prompt_version,
        )
        row = self._connection.execute(
            """
            SELECT id FROM transcription_runs
            WHERE episode_id = ? AND transcription_identity = ? AND status = 'succeeded'
            ORDER BY id DESC LIMIT 1
            """,
            (episode_id, identity),
        ).fetchone()
        return None if row is None else self.get_transcript(int(row["id"]))

    def create_run(
        self,
        episode: EpisodeRecord,
        *,
        provider: str,
        model: str,
        chunker_version: str,
        prompt_version: str,
        estimated_cost_microusd: int,
    ) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO transcription_runs (
                    episode_id, status, source_fingerprint, provider, model,
                    chunker_version, prompt_version, estimated_cost_microusd, created_at
                ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode.id,
                    episode.source_fingerprint,
                    provider,
                    model,
                    chunker_version,
                    prompt_version,
                    estimated_cost_microusd,
                    _utc_now(),
                ),
            )
            if cursor.lastrowid is None:
                raise PersistenceError("SQLite did not return a transcription run ID")
            return cursor.lastrowid

    def mark_running(self, run_id: int) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE transcription_runs SET status = 'running', started_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (_utc_now(), run_id),
            )
            if cursor.rowcount != 1:
                raise PersistenceError("transcription run is not pending")

    def mark_failed(self, run_id: int, *, error_code: str, safe_message: str) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE transcription_runs
                SET status = 'failed', error_code = ?, error_message = ?, completed_at = ?
                WHERE id = ? AND status IN ('pending', 'running')
                """,
                (error_code, safe_message, _utc_now(), run_id),
            )
            if cursor.rowcount != 1:
                raise PersistenceError("transcription run cannot be marked failed")

    def persist_success(
        self,
        run_id: int,
        episode: EpisodeRecord,
        result: EpisodeTranscript,
        *,
        chunker_version: str,
        prompt_version: str,
    ) -> StoredTranscript:
        """Atomically persist parts, assembly, hashes, provenance, and success state."""

        transcript = result.transcript
        if not transcript.parts:
            raise PersistenceError("provider transcript has no ordered parts")
        content_hash = sha256(transcript.text.encode("utf-8")).hexdigest()
        final_source_fingerprint = source_fingerprint(
            enclosure_url=result.source_url,
            declared_byte_length=self._declared_byte_length(episode.id),
            etag=result.etag,
            last_modified=result.last_modified,
            duration_seconds=result.duration_seconds,
        )
        identity = transcription_identity(
            audio_sha256=result.audio_sha256,
            model=transcript.model,
            chunker_version=chunker_version,
            prompt_version=prompt_version,
        )
        now = _utc_now()
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    UPDATE episodes
                    SET etag = ?, last_modified = ?, source_fingerprint = ?, observed_at = ?
                    WHERE id = ?
                    """,
                    (result.etag, result.last_modified, final_source_fingerprint, now, episode.id),
                )
                for part in transcript.parts:
                    usage = part.usage
                    connection.execute(
                        """
                        INSERT INTO transcript_parts (
                            run_id, ordinal, request_id, model, language, text, usage_type,
                            input_tokens, output_tokens, total_tokens, audio_seconds,
                            input_token_details_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            part.ordinal,
                            part.request_id,
                            part.model,
                            part.language,
                            part.text,
                            usage.usage_type if usage else None,
                            usage.input_tokens if usage else None,
                            usage.output_tokens if usage else None,
                            usage.total_tokens if usage else None,
                            usage.audio_seconds if usage else None,
                            usage.input_token_details_json if usage else None,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO transcripts (run_id, text, content_hash, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (run_id, transcript.text, content_hash, now),
                )
                cursor = connection.execute(
                    """
                    UPDATE transcription_runs
                    SET status = 'succeeded', source_fingerprint = ?, transcription_identity = ?,
                        provider = ?, model = ?, audio_bytes = ?, audio_sha256 = ?, completed_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        final_source_fingerprint,
                        identity,
                        transcript.provider,
                        transcript.model,
                        result.audio_bytes,
                        result.audio_sha256,
                        now,
                        run_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PersistenceError("transcription run is not running")
        except sqlite3.Error as error:
            raise PersistenceError("atomic transcript persistence failed") from error
        return self.get_transcript(run_id)

    def get_transcript(self, run_id: int) -> StoredTranscript:
        row = self._connection.execute(
            """
            SELECT r.id AS run_id, r.episode_id, r.source_fingerprint, r.audio_sha256,
                   r.audio_bytes, r.estimated_cost_microusd, r.provider, r.model,
                   t.id AS transcript_id, t.text, t.content_hash, t.created_at, e.rss_guid,
                   e.spotify_episode_id, e.title AS episode_title, f.canonical_url
            FROM transcription_runs r
            JOIN transcripts t ON t.run_id = r.id
            JOIN episodes e ON e.id = r.episode_id
            JOIN feeds f ON f.id = e.feed_id
            WHERE r.id = ? AND r.status = 'succeeded'
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise PersistenceError("successful transcript was not found")
        part_rows = self._connection.execute(
            """
            SELECT ordinal, request_id, model, language, text, usage_type, input_tokens,
                   output_tokens, total_tokens, audio_seconds, input_token_details_json
            FROM transcript_parts WHERE run_id = ? ORDER BY ordinal
            """,
            (run_id,),
        ).fetchall()
        parts = tuple(_part_from_row(part_row) for part_row in part_rows)
        if tuple(part.ordinal for part in parts) != tuple(range(len(parts))):
            raise PersistenceCorruptionError("stored transcript part ordering is corrupt")
        assembled = "\n\n".join(part.text.strip() for part in parts)
        text = str(row["text"])
        if not parts or assembled != text:
            raise PersistenceCorruptionError("stored transcript assembly is corrupt")
        content_hash = str(row["content_hash"])
        if sha256(text.encode("utf-8")).hexdigest() != content_hash:
            raise PersistenceCorruptionError("stored transcript content hash does not match")
        audio_sha256 = str(row["audio_sha256"])
        request_ids = tuple(part.request_id for part in parts if part.request_id is not None)
        languages = {part.language for part in parts if part.language is not None}
        provider_transcript = ProviderTranscript(
            text=text,
            provider=str(row["provider"]),
            model=str(row["model"]),
            request_ids=request_ids,
            language=next(iter(languages)) if len(languages) == 1 else None,
            chunk_count=len(parts),
            parts=parts,
        )
        return StoredTranscript(
            run_id=int(row["run_id"]),
            transcript_id=int(row["transcript_id"]),
            episode_id=int(row["episode_id"]),
            feed_url=str(row["canonical_url"]),
            rss_guid=str(row["rss_guid"]),
            spotify_episode_id=(
                str(row["spotify_episode_id"]) if row["spotify_episode_id"] is not None else None
            ),
            episode_title=str(row["episode_title"]),
            source_fingerprint=str(row["source_fingerprint"]),
            audio_sha256=audio_sha256,
            audio_bytes=int(row["audio_bytes"]),
            estimated_cost_microusd=int(row["estimated_cost_microusd"]),
            provider_transcript=provider_transcript,
            content_hash=content_hash,
            created_at=str(row["created_at"]),
        )

    def ensure_segments(
        self,
        transcript: StoredTranscript,
        *,
        segmenter_version: str,
        max_chars: int,
    ) -> tuple[TranscriptSegment, ...]:
        """Build or reuse a deterministic FTS derivative for one transcript."""

        expected = segment_transcript_text(
            transcript_id=transcript.transcript_id,
            episode_id=transcript.episode_id,
            content_hash=transcript.content_hash,
            text=transcript.text,
            max_chars=max_chars,
            segmenter_version=segmenter_version,
        )
        existing = self.load_segments(
            transcript.transcript_id,
            segmenter_version=segmenter_version,
        )
        if existing == expected:
            return existing
        now = _utc_now()
        try:
            with self.transaction() as connection:
                old_ids = connection.execute(
                    """
                    SELECT segment_id FROM transcript_segments
                    WHERE transcript_id = ? AND segmenter_version = ?
                    """,
                    (transcript.transcript_id, segmenter_version),
                ).fetchall()
                for row in old_ids:
                    connection.execute(
                        "DELETE FROM transcript_segments_fts WHERE segment_id = ?",
                        (str(row["segment_id"]),),
                    )
                connection.execute(
                    """
                    DELETE FROM transcript_segments
                    WHERE transcript_id = ? AND segmenter_version = ?
                    """,
                    (transcript.transcript_id, segmenter_version),
                )
                for segment in expected:
                    connection.execute(
                        """
                        INSERT INTO transcript_segments (
                            segment_id, transcript_id, episode_id, ordinal, char_start,
                            char_end, text, text_hash, transcript_content_hash,
                            segmenter_version, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            segment.segment_id,
                            segment.transcript_id,
                            segment.episode_id,
                            segment.ordinal,
                            segment.char_start,
                            segment.char_end,
                            segment.text,
                            segment.text_hash,
                            segment.transcript_content_hash,
                            segment.segmenter_version,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO transcript_segments_fts (
                            segment_id, transcript_id, episode_id, text
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            segment.segment_id,
                            segment.transcript_id,
                            segment.episode_id,
                            segment.text,
                        ),
                    )
        except sqlite3.Error as error:
            raise PersistenceError("transcript segment rebuild failed") from error
        return self.load_segments(
            transcript.transcript_id,
            segmenter_version=segmenter_version,
        )

    def load_segments(
        self,
        transcript_id: int,
        *,
        segmenter_version: str,
    ) -> tuple[TranscriptSegment, ...]:
        rows = self._connection.execute(
            """
            SELECT segment_id, transcript_id, episode_id, ordinal, char_start, char_end,
                   text, text_hash, transcript_content_hash, segmenter_version
            FROM transcript_segments
            WHERE transcript_id = ? AND segmenter_version = ?
            ORDER BY ordinal
            """,
            (transcript_id, segmenter_version),
        ).fetchall()
        return tuple(_segment_from_row(row) for row in rows)

    def search_segments(
        self,
        transcript_id: int,
        *,
        query: str,
        limit: int,
        segmenter_version: str,
    ) -> tuple[TranscriptSearchHit, ...]:
        match_query = _fts_match_query(query)
        try:
            rows = self._connection.execute(
                """
                SELECT s.segment_id, s.transcript_id, s.episode_id, s.ordinal,
                       s.char_start, s.char_end, s.text, s.text_hash,
                       s.transcript_content_hash, s.segmenter_version,
                       bm25(transcript_segments_fts) AS score
                FROM transcript_segments_fts
                JOIN transcript_segments s
                  ON s.segment_id = transcript_segments_fts.segment_id
                WHERE transcript_segments_fts MATCH ?
                  AND s.transcript_id = ? AND s.segmenter_version = ?
                ORDER BY score, s.ordinal
                LIMIT ?
                """,
                (match_query, transcript_id, segmenter_version, limit),
            ).fetchall()
        except sqlite3.Error as error:
            raise PersistenceError("transcript search failed") from error
        return tuple(
            TranscriptSearchHit(segment=_segment_from_row(row), score=float(row["score"]))
            for row in rows
        )

    def read_segments(
        self,
        transcript_id: int,
        segment_ids: tuple[str, ...],
    ) -> tuple[TranscriptSegment, ...]:
        if not segment_ids:
            return ()
        placeholders = ",".join("?" for _ in segment_ids)
        rows = self._connection.execute(
            f"""
            SELECT segment_id, transcript_id, episode_id, ordinal, char_start, char_end,
                   text, text_hash, transcript_content_hash, segmenter_version
            FROM transcript_segments
            WHERE transcript_id = ? AND segment_id IN ({placeholders})
            """,
            (transcript_id, *segment_ids),
        ).fetchall()
        by_id = {str(row["segment_id"]): _segment_from_row(row) for row in rows}
        if set(by_id) != set(segment_ids):
            raise PersistenceError("one or more transcript segments were not found")
        return tuple(by_id[segment_id] for segment_id in segment_ids)

    def episode_metadata(
        self,
        transcript: StoredTranscript,
        *,
        segmenter_version: str,
    ) -> EpisodeMetadata:
        row = self._connection.execute(
            """
            SELECT COUNT(*) AS segment_count FROM transcript_segments
            WHERE transcript_id = ? AND segmenter_version = ?
            """,
            (transcript.transcript_id, segmenter_version),
        ).fetchone()
        return EpisodeMetadata(
            episode_id=transcript.episode_id,
            transcript_id=transcript.transcript_id,
            feed_url=transcript.feed_url,
            rss_guid=transcript.rss_guid,
            spotify_episode_id=transcript.spotify_episode_id,
            title=transcript.episode_title,
            transcript_content_hash=transcript.content_hash,
            segmenter_version=segmenter_version,
            segment_count=int(row["segment_count"]),
        )

    def find_analysis_cache(
        self,
        transcript: StoredTranscript,
        *,
        analysis_type: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        segmenter_version: str,
    ) -> StoredEpisodeAnalysis | None:
        identity = analysis_identity(
            transcript_content_hash=transcript.content_hash,
            analysis_type=analysis_type,
            model=model,
            prompt_version=prompt_version,
            schema_version=schema_version,
            segmenter_version=segmenter_version,
        )
        row = self._connection.execute(
            """
            SELECT id FROM analysis_runs
            WHERE transcript_id = ? AND cache_identity = ? AND status = 'succeeded'
            ORDER BY id DESC LIMIT 1
            """,
            (transcript.transcript_id, identity),
        ).fetchone()
        return None if row is None else self.get_analysis(int(row["id"]))

    def create_analysis_run(
        self,
        transcript: StoredTranscript,
        *,
        analysis_type: str,
        model: str,
        prompt_version: str,
        schema_version: str,
        segmenter_version: str,
    ) -> int:
        identity = analysis_identity(
            transcript_content_hash=transcript.content_hash,
            analysis_type=analysis_type,
            model=model,
            prompt_version=prompt_version,
            schema_version=schema_version,
            segmenter_version=segmenter_version,
        )
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO analysis_runs (
                    transcript_id, status, cache_identity, analysis_type, model,
                    prompt_version, schema_version, segmenter_version, created_at
                ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transcript.transcript_id,
                    identity,
                    analysis_type,
                    model,
                    prompt_version,
                    schema_version,
                    segmenter_version,
                    _utc_now(),
                ),
            )
            if cursor.lastrowid is None:
                raise PersistenceError("SQLite did not return an analysis run ID")
            return cursor.lastrowid

    def mark_analysis_running(self, run_id: int) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_runs SET status = 'running', started_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (_utc_now(), run_id),
            )
            if cursor.rowcount != 1:
                raise PersistenceError("analysis run is not pending")

    def mark_analysis_failed(
        self,
        run_id: int,
        *,
        error_code: str,
        safe_message: str,
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE analysis_runs
                SET status = 'failed', error_code = ?, error_message = ?, completed_at = ?
                WHERE id = ? AND status IN ('pending', 'running')
                """,
                (error_code, safe_message, _utc_now(), run_id),
            )
            if cursor.rowcount != 1:
                raise PersistenceError("analysis run cannot be marked failed")

    def persist_analysis_success(
        self,
        run_id: int,
        *,
        response_id: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        analysis: EpisodeAnalysis,
    ) -> StoredEpisodeAnalysis:
        """Atomically persist validated output, evidence links, usage, and success."""

        run = self._connection.execute(
            """
            SELECT transcript_id, segmenter_version FROM analysis_runs
            WHERE id = ? AND status = 'running'
            """,
            (run_id,),
        ).fetchone()
        if run is None:
            raise PersistenceError("analysis run is not running")
        transcript_id = int(run["transcript_id"])
        segments = self.load_segments(
            transcript_id,
            segmenter_version=str(run["segmenter_version"]),
        )
        validate_analysis_evidence(
            analysis,
            transcript_id=transcript_id,
            segments=segments,
        )
        output_json = analysis.model_dump_json()
        now = _utc_now()
        try:
            with self.transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO episode_analyses (run_id, output_json, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (run_id, output_json, now),
                )
                if cursor.lastrowid is None:
                    raise PersistenceError("SQLite did not return an analysis ID")
                analysis_id = cursor.lastrowid
                for item_path, evidence in iter_analysis_evidence(analysis):
                    connection.execute(
                        """
                        INSERT INTO analysis_evidence (
                            analysis_id, item_path, segment_id, quote
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (analysis_id, item_path, evidence.segment_id, evidence.quote),
                    )
                updated = connection.execute(
                    """
                    UPDATE analysis_runs
                    SET status = 'succeeded', response_id = ?, input_tokens = ?,
                        output_tokens = ?, total_tokens = ?, completed_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (
                        response_id,
                        input_tokens,
                        output_tokens,
                        total_tokens,
                        now,
                        run_id,
                    ),
                )
                if updated.rowcount != 1:
                    raise PersistenceError("analysis run is not running")
        except sqlite3.Error as error:
            raise PersistenceError("atomic analysis persistence failed") from error
        return self.get_analysis(run_id)

    def get_analysis(self, run_id: int) -> StoredEpisodeAnalysis:
        row = self._connection.execute(
            """
            SELECT r.id AS run_id, r.transcript_id, r.cache_identity, r.analysis_type,
                   r.model, r.prompt_version, r.schema_version, r.segmenter_version,
                   r.response_id, r.input_tokens, r.output_tokens, r.total_tokens,
                   a.id AS analysis_id, a.output_json, a.created_at
            FROM analysis_runs r
            JOIN episode_analyses a ON a.run_id = r.id
            WHERE r.id = ? AND r.status = 'succeeded'
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise PersistenceError("successful episode analysis was not found")
        try:
            analysis = EpisodeAnalysis.model_validate_json(str(row["output_json"]))
        except ValueError as error:
            raise PersistenceCorruptionError("stored episode analysis schema is corrupt") from error
        transcript_id = int(row["transcript_id"])
        segments = self.load_segments(
            transcript_id,
            segmenter_version=str(row["segmenter_version"]),
        )
        validate_analysis_evidence(
            analysis,
            transcript_id=transcript_id,
            segments=segments,
        )
        evidence_rows = self._connection.execute(
            """
            SELECT item_path, segment_id, quote FROM analysis_evidence
            WHERE analysis_id = ? ORDER BY id
            """,
            (int(row["analysis_id"]),),
        ).fetchall()
        stored_evidence = tuple(
            (str(item["item_path"]), str(item["segment_id"]), str(item["quote"]))
            for item in evidence_rows
        )
        expected_evidence = tuple(
            (path, evidence.segment_id, evidence.quote)
            for path, evidence in iter_analysis_evidence(analysis)
        )
        if stored_evidence != expected_evidence:
            raise PersistenceCorruptionError("stored analysis evidence links are corrupt")
        return StoredEpisodeAnalysis(
            run_id=int(row["run_id"]),
            analysis_id=int(row["analysis_id"]),
            transcript_id=transcript_id,
            cache_identity=str(row["cache_identity"]),
            analysis_type=str(row["analysis_type"]),
            model=str(row["model"]),
            prompt_version=str(row["prompt_version"]),
            schema_version=str(row["schema_version"]),
            segmenter_version=str(row["segmenter_version"]),
            response_id=str(row["response_id"]),
            input_tokens=(int(row["input_tokens"]) if row["input_tokens"] is not None else None),
            output_tokens=(int(row["output_tokens"]) if row["output_tokens"] is not None else None),
            total_tokens=(int(row["total_tokens"]) if row["total_tokens"] is not None else None),
            analysis=analysis,
            created_at=str(row["created_at"]),
        )

    def list_analyses(self) -> tuple[StoredEpisodeAnalysis, ...]:
        rows = self._connection.execute(
            "SELECT id FROM analysis_runs WHERE status = 'succeeded' ORDER BY id"
        ).fetchall()
        return tuple(self.get_analysis(int(row["id"])) for row in rows)

    def analysis_run_status(self, run_id: int) -> str:
        row = self._connection.execute(
            "SELECT status FROM analysis_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise PersistenceError("analysis run was not found")
        return str(row["status"])

    def list_transcripts(self) -> tuple[StoredTranscript, ...]:
        rows = self._connection.execute(
            "SELECT id FROM transcription_runs WHERE status = 'succeeded' ORDER BY id"
        ).fetchall()
        return tuple(self.get_transcript(int(row["id"])) for row in rows)

    def run_status(self, run_id: int) -> str:
        row = self._connection.execute(
            "SELECT status FROM transcription_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise PersistenceError("transcription run was not found")
        return str(row["status"])

    def _declared_byte_length(self, episode_id: int) -> int | None:
        row = self._connection.execute(
            "SELECT declared_byte_length FROM episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if row is None:
            raise PersistenceError("episode was not found")
        value = row["declared_byte_length"]
        return int(value) if value is not None else None


def source_fingerprint(
    *,
    enclosure_url: str | None,
    declared_byte_length: int | None,
    etag: str | None,
    last_modified: str | None,
    duration_seconds: int | None,
) -> str:
    """Hash cheap, normalized enclosure metadata for pre-download cache lookup."""

    return _canonical_hash(
        {
            "declared_byte_length": declared_byte_length,
            "duration_seconds": duration_seconds,
            "enclosure_url": enclosure_url,
            "etag": etag,
            "last_modified": last_modified,
        }
    )


def transcription_identity(
    *,
    audio_sha256: str,
    model: str,
    chunker_version: str,
    prompt_version: str,
) -> str:
    """Hash definitive audio and transcription-contract identity."""

    return _canonical_hash(
        {
            "audio_sha256": audio_sha256,
            "chunker_version": chunker_version,
            "model": model,
            "prompt_version": prompt_version,
        }
    )


def analysis_identity(
    *,
    transcript_content_hash: str,
    analysis_type: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    segmenter_version: str,
) -> str:
    """Hash the complete reusable structured-analysis contract."""

    return _canonical_hash(
        {
            "analysis_type": analysis_type,
            "model": model,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "segmenter_version": segmenter_version,
            "transcript_content_hash": transcript_content_hash,
        }
    )


def dollars_to_microusd(value: Decimal) -> int:
    converted = value * Decimal(1_000_000)
    if not converted.is_finite() or converted < 0 or converted != converted.to_integral_value():
        raise ValueError("estimated cost must convert exactly to non-negative micro-US-dollars")
    return int(converted)


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _part_from_row(row: sqlite3.Row) -> ProviderTranscriptPart:
    usage = None
    if row["usage_type"] is not None:
        usage = TranscriptionUsage(
            usage_type=str(row["usage_type"]),
            input_tokens=int(row["input_tokens"]) if row["input_tokens"] is not None else None,
            output_tokens=(int(row["output_tokens"]) if row["output_tokens"] is not None else None),
            total_tokens=int(row["total_tokens"]) if row["total_tokens"] is not None else None,
            audio_seconds=(
                float(row["audio_seconds"]) if row["audio_seconds"] is not None else None
            ),
            input_token_details_json=(
                str(row["input_token_details_json"])
                if row["input_token_details_json"] is not None
                else None
            ),
        )
    return ProviderTranscriptPart(
        ordinal=int(row["ordinal"]),
        request_id=str(row["request_id"]) if row["request_id"] is not None else None,
        model=str(row["model"]),
        language=str(row["language"]) if row["language"] is not None else None,
        text=str(row["text"]),
        usage=usage,
    )


def _segment_from_row(row: sqlite3.Row) -> TranscriptSegment:
    segment = TranscriptSegment(
        segment_id=str(row["segment_id"]),
        transcript_id=int(row["transcript_id"]),
        episode_id=int(row["episode_id"]),
        ordinal=int(row["ordinal"]),
        char_start=int(row["char_start"]),
        char_end=int(row["char_end"]),
        text=str(row["text"]),
        text_hash=str(row["text_hash"]),
        transcript_content_hash=str(row["transcript_content_hash"]),
        segmenter_version=str(row["segmenter_version"]),
    )
    if sha256(segment.text.encode("utf-8")).hexdigest() != segment.text_hash:
        raise PersistenceCorruptionError("stored transcript segment hash does not match")
    return segment


def _fts_match_query(query: str) -> str:
    terms = re.findall(r"[\w'-]+", query, flags=re.UNICODE)
    if not terms:
        raise ValueError("search query must contain a lexical term")
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
