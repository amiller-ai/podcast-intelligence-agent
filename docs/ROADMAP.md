# Podcast Intelligence Roadmap

Last updated: 2026-08-19

This file is the durable source of truth for milestone state. `AGENTS.md` defines how work is performed; this roadmap records what is complete, what is current, and what is only proposed.

## Milestone workflow

- Keep exactly one milestone in **Current**.
- Agree on acceptance criteria before implementation.
- Keep unit tests offline and deterministic; make live checks explicit.
- Complete the repository quality gates before marking a milestone complete.
- Update this roadmap with validation evidence, then commit and push the milestone.
- Do not promote a proposed milestone to **Current** without user approval.

## Completed

### Milestone 0 — Development foundation

Established Python 3.12, `uv`, package layout, linting, strict typing, tests, coverage, secret handling, repository instructions, and reproducible dependency locking.

- Commit: `a603cd7` (`Establish Responses API project foundation`)
- Validation: Ruff formatting and linting, strict mypy, pytest with coverage, lockfile consistency, and package build

### Milestone 1 — Responses API client

Added typed environment configuration and a minimal application-owned OpenAI Responses API boundary.

- Commit: `a603cd7` (`Establish Responses API project foundation`)
- Runtime contract: configurable model and reasoning effort, response storage disabled by default
- Validation: deterministic mocked unit tests plus an explicit successful live Responses API smoke test

### Milestone 2 — Offline RSS parsing

Added provider-independent podcast models and safe RSS 2.0 parsing for feed and episode metadata.

- Commit: `1f8fda5` (`Add safe RSS feed parsing`)
- Runtime contract: no network access; unsafe XML entities are rejected
- Validation: 23 offline tests passing at 98.5% coverage, Ruff, strict mypy, lockfile consistency, and package build

### Milestone 3 — Guarded HTTP RSS retrieval

Goal: retrieve a user-provided podcast RSS URL and pass its bounded XML payload to the existing parser without exposing the application to unrestricted network or memory use.

Acceptance criteria:

- [x] Accept only valid HTTP or HTTPS feed URLs without embedded credentials.
- [x] Reject loopback, private, link-local, and otherwise unsafe destinations before requesting them.
- [x] Revalidate every redirect destination and enforce a small redirect limit.
- [x] Apply explicit connection/read timeouts and a maximum response size.
- [x] Reject clearly incompatible response content while accepting standard RSS/XML content types.
- [x] Convert transport, policy, size, and HTTP failures into clear application-owned errors.
- [x] Compose retrieval with `parse_rss_feed` and return a typed `PodcastFeed`.
- [x] Use an injected or mocked transport so the default test suite remains network-free.
- [x] Pass all repository quality gates and a package build.

Status: complete on 2026-08-19.

Completion evidence:

- Runtime contract: public HTTP(S) destinations only, every redirect revalidated, at most three redirects, 5-second connect and 10-second read timeouts, and a 2,000,000-byte decoded response limit by default
- Failure contract: application-owned policy, transport, HTTP, content-type, and response-size errors; XML parse failures remain application-owned `RssFeedParseError` values
- Validation: 55 deterministic offline tests passed with 96.32% total coverage; Ruff formatting and linting, strict mypy, lockfile consistency, and package build all passed
- Live network calls: none required or performed

Out of scope:

- Audio download or transcription
- Persistent storage or search
- OpenAI-based podcast analysis
- User interface or deployment

### Milestone 4 — Episode resolution and transcript source discovery

Goal: resolve a user-provided Spotify episode URL to the exact episode in its canonical RSS feed, then report the first viable transcript source without scraping Spotify audio or guessing across ambiguous matches.

Acceptance criteria:

- [x] Accept only canonical Spotify episode URLs and extract a validated episode ID.
- [x] Resolve the episode title through Spotify's public oEmbed boundary without accepting or logging Spotify playback credentials.
- [x] Discover candidate podcast episodes through an application-owned catalog boundary and require one unique normalized title match.
- [x] Retrieve the candidate's canonical RSS feed through the existing guarded HTTP boundary and verify the catalog GUID exists in that feed.
- [x] Parse typed Podcasting 2.0 `<podcast:transcript>` references from RSS episode metadata.
- [x] Return typed episode identity, source provenance, and ordered transcript-resolution outcomes.
- [x] Prefer an RSS transcript, preserve publisher-page and provider stages as explicit future adapter boundaries, and report when authorized audio transcription is required.
- [x] Keep raw transcript text, audio download, Spotify playback URLs, generic webpage scraping, persistence, and model calls out of scope.
- [x] Use injected or mocked transports so the default test suite remains deterministic and network-free.
- [x] Pass all repository quality gates and a package build.

Status: complete on 2026-08-19.

Completion evidence:

- Identity contract: canonical Spotify episode URL to public oEmbed title, unique normalized Apple Podcasts catalog match, guarded canonical feed retrieval, and exact catalog-GUID verification in RSS
- Resolution contract: typed Podcasting 2.0 transcript references; ordered RSS, publisher-page, provider, and authorized-audio outcomes without fetching transcript or audio content
- Resource contract: metadata responses capped at 1,000,000 bytes and discovery feeds at 10,000,000 bytes, with explicit connection/read timeouts and the existing public-destination/redirect policy
- Validation: 80 deterministic offline tests passed with 95.58% total coverage; Ruff formatting and linting, strict mypy, lockfile consistency, and package build all passed
- Live application checks: none required; all application transports were mocked

Privacy and retention contract:

- Do not persist catalog responses, feed payloads, transcript content, or audio.
- Do not log Spotify embed payloads, playback URLs, anonymous tokens, or raw episode descriptions.
- Preserve only normalized identifiers and source URLs in returned domain models.

### Milestone 5 — Guarded audio transcription fallback

Goal: when RSS exposes no usable transcript, retrieve an explicitly authorized RSS audio enclosure within strict network, duration, byte, cost, and retention limits and pass it to an application-owned transcription boundary. Never download audio from Spotify.

Acceptance criteria:

- [x] Require explicit caller authorization and an episode with no supported public RSS transcript.
- [x] Accept only the episode's verified HTTP(S) RSS audio enclosure; never accept a Spotify playback URL or arbitrary replacement URL.
- [x] Reject loopback, private, link-local, and otherwise unsafe destinations before requesting them, and revalidate every redirect destination.
- [x] Require declared RSS duration no greater than two hours and an estimated transcription cost no greater than $1.00 before download.
- [x] Apply explicit connection/read timeouts, at most three redirects, and a 100,000,000-byte enclosure limit.
- [x] Accept only supported audio media types and reject incompatible or conflicting RSS/HTTP media types.
- [x] Tolerate a narrowly defined catalog title suffix omission only when the catalog match is unique and the verified RSS GUID and normalized RSS title match the Spotify title.
- [x] Configure the transcription model and price centrally, defaulting to the documented `gpt-transcribe` model and $0.0045/minute standard price.
- [x] Split large enclosures into temporary ffmpeg-generated chunks below the 25 MB API upload limit and reject any oversized generated chunk before upload.
- [x] Transcribe chunks sequentially through OpenAI's `/v1/audio/transcriptions` endpoint, carry bounded prior text as continuity context, and return one typed in-memory transcript with all request IDs.
- [x] Keep the temporary audio private and delete it after success or every failure; do not persist or log audio or transcript content.
- [x] Convert authorization, eligibility, policy, transport, HTTP, media-type, size, cost, and provider failures into clear application-owned errors.
- [x] Use injected or mocked network and transcription boundaries so the default test suite remains deterministic and network-free.
- [x] Pass all repository quality gates and a package build.
- [x] Complete one explicitly authorized live test on the supplied Spotify episode without persisting or printing audio or transcript content.

Status: complete on 2026-08-19.

Completion evidence:

- Eligibility contract: explicit authorization, no supported public RSS transcript, a typed RSS audio enclosure, and positive declared duration are required before retrieval
- Runtime contract: public non-Spotify HTTP(S) destinations only, every redirect revalidated, at most three redirects, 5-second connect and 30-second read timeouts, a two-hour declared-duration limit, a 100,000,000-byte decoded enclosure limit, a 25,000,000-byte per-upload limit, and a $1.00 preflight estimate cap
- Boundary contract: `gpt-transcribe` and its $0.0045/minute price are centrally configurable; ffmpeg chunks large files without re-encoding; chunk requests carry bounded continuity context and preserve every OpenAI request ID
- Identity contract: a catalog-only omission matching `- [show, EP.number]` is allowed only for a unique candidate followed by exact normalized Spotify-to-RSS title and catalog-GUID verification
- Retention contract: the enclosure and generated chunks live only in private temporary directories and are deleted after success, retrieval failure, chunking failure, or provider failure; transcript text remains in memory and is not logged or persisted
- OpenAI data contract verified 2026-08-19: the current endpoint table lists `/v1/audio/transcriptions` as not used for training, with no abuse-monitoring or application-state retention, and as Zero Data Retention eligible
- Offline validation: 143 deterministic tests passed with 96.01% total coverage; Ruff formatting and linting, strict mypy, lockfile consistency, and package build all passed
- Live validation: supplied Spotify episode `0VPwvReM2olZDWl3YOHfqh` resolved to its verified RSS episode, downloaded a 74,504,048-byte enclosure with declared duration 4,576 seconds, passed the $0.3432 estimate gate, produced multiple sub-25 MB chunks, returned one non-empty transcript with one request ID per chunk, and completed in 138.64 seconds
- Live content handling: neither audio nor transcript content was printed or persisted

Approved provider boundary:

- Current OpenAI documentation routes bounded file transcription through `/v1/audio/transcriptions`, recommends `gpt-transcribe`, and limits each upload to 25 MB. The user's 2026-08-19 request explicitly approved implementing and live-testing that speech-to-text ingestion endpoint; Responses remains the required API for later reasoning and intelligence calls.

Out of scope:

- Spotify audio or playback credential access
- Diarization or translation
- Persistent audio or transcript storage
- Podcast intelligence analysis, search, user interface, or deployment

## Current

### Milestone 6 — SQLite transcript persistence and idempotent reuse

Goal: run episode resolution and transcription as a resumable local data pipeline whose successful outputs are durably stored and reused, so the same unchanged episode is not downloaded or transcribed again.

Architecture decisions:

- Use Python's built-in `sqlite3` module with explicit, versioned migrations; do not add an ORM or separate database service.
- Keep the SQLite database as the canonical source of truth. Vector indexes, search indexes, and other retrieval structures remain future rebuildable derivatives.
- Default the database to the ignored runtime path `data/podcast_intelligence.db`, with one centrally configured override for tests and alternate environments.
- Keep feeds, episodes, transcription attempts, ordered provider parts, and assembled transcripts relational and independently addressable.
- Store transcript text as UTF-8 SQLite `TEXT`, timestamps as UTC ISO 8601 text, durations and byte counts as integers, estimated costs as integer micro-US-dollars, and hashes as lowercase hexadecimal strings.
- Preserve typed provider provenance per transcription part: ordinal, request ID, model, language, usage metadata, and text. Assemble the canonical transcript deterministically from those ordered parts.
- Never persist downloaded audio or ffmpeg chunks. Continue deleting all temporary media on success and failure.

Pipeline and cache contract:

```text
Spotify URL
  -> verified RSS episode
  -> source metadata fingerprint lookup
  -> cached successful transcript, when unchanged
  -> otherwise guarded transcript resolution or audio transcription
  -> transactional SQLite persistence
  -> canonical transcript returned to the caller
```

- Identify an episode by its canonical feed URL and RSS GUID, not by title alone.
- Build the cheap pre-download source fingerprint from the enclosure URL, declared byte length, ETag, Last-Modified value, and declared duration when available.
- Build the definitive transcription identity from the downloaded audio SHA-256, transcription model, chunker version, and transcription prompt version.
- Treat a matching completed transcription as a cache hit and skip both audio download and OpenAI transcription. An explicit refresh may revalidate or replace it without destroying history.
- Record pipeline state as pending, running, succeeded, or failed. Commit transcript parts and the assembled transcript atomically before marking a run succeeded.
- Store application-owned failure codes and safe messages without raw transcript, audio, provider payload, or secret content.

Minimum relational model:

| Table | Purpose |
| --- | --- |
| `schema_migrations` | Applied migration versions and timestamps |
| `feeds` | Canonical feed identity, URL, title, and observed metadata |
| `episodes` | Feed-scoped RSS GUID, title, publication data, enclosure metadata, and source fingerprint |
| `transcription_runs` | Attempt state, cache identity, model/chunker/prompt versions, cost estimate, byte count, audio hash, and timestamps |
| `transcript_parts` | Ordered text, request ID, language, and usage metadata for each provider chunk |
| `transcripts` | One canonical assembled transcript, content hash, and provenance link per successful run |

Acceptance criteria:

- [ ] Add centrally configured SQLite settings with a safe ignored default path and no implicit creation outside the configured runtime directory.
- [ ] Add an application-owned persistence boundary using `sqlite3`, foreign-key enforcement, explicit transactions, and deterministic versioned migrations.
- [ ] Create the minimum relational model above with uniqueness, foreign-key, status, and non-negative resource constraints enforced in SQLite.
- [ ] Persist verified feed and episode identity without using title as a primary or cache key.
- [ ] Extend the transcription result contract to preserve ordered provider parts and persist their request IDs, text, language, usage metadata, and model provenance.
- [ ] Compute and persist source fingerprints, audio SHA-256, transcript content hashes, model identity, chunker version, prompt version, estimated cost, and byte count.
- [ ] Return a matching successful cached transcript before audio retrieval or provider invocation, with deterministic tests proving those boundaries were not called.
- [ ] Make repeated ingestion idempotent and preserve prior successful history when an explicit refresh produces a new source or transcription identity.
- [ ] Persist success atomically; record safe failed-run state without partial transcript rows or raw content in logs/errors.
- [ ] Keep audio and ffmpeg chunks temporary, keep the database and other runtime data ignored by Git, and document local transcript sensitivity and backup expectations.
- [ ] Keep the default test suite deterministic and network-free, including fresh migration, upgrade, rollback-on-failure, cache-hit, cache-miss, refresh, and corruption/error cases.
- [ ] Pass all repository quality gates, lockfile consistency, and a package build.

Status: approved and ready for implementation on 2026-08-19.

Out of scope:

- Embeddings, vector stores, semantic search, or retrieval ranking
- Structured podcast intelligence or Responses API analysis
- Persisting audio, temporary chunks, API keys, or unrelated provider payloads
- Multi-process workers, queues, schedulers, hosted databases, replication, or deployment
- User interface changes

## Proposed

These milestones are directional and require approval before becoming current.

### Milestone 7 — Structured podcast intelligence

Define an evaluated Structured Output contract for summaries, topics, people, claims, evidence, and actionable insights. Test the schema and failure behavior before scaling beyond one episode.

### Milestone 8 — Personal application interface

Choose a minimal interface based on the validated workflow, then expose ingestion status, episode selection, analysis results, and errors without coupling the UI to provider SDK objects.
