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

## Current

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

Status: complete on 2026-08-19. This remains the single milestone under **Current** until the user approves promoting a proposed milestone.

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

## Proposed

These milestones are directional and require approval before becoming current.

### Milestone 5 — Guarded audio transcription fallback

When earlier transcript resolvers return no usable source, retrieve an authorized RSS audio enclosure within explicit duration, byte, cost, and retention limits and transcribe it behind an application-owned boundary. Never download audio from Spotify. Normalize the result to the same transcript contract and delete temporary audio after use.

### Milestone 6 — Structured podcast intelligence

Define an evaluated Structured Output contract for summaries, topics, people, claims, evidence, and actionable insights. Test the schema and failure behavior before scaling beyond one episode.

### Milestone 7 — Local persistence and retrieval

Persist feed, episode, transcript, and analysis records locally with explicit migrations and idempotent updates. Add search only after the query requirements are defined.

### Milestone 8 — Personal application interface

Choose a minimal interface based on the validated workflow, then expose ingestion status, episode selection, analysis results, and errors without coupling the UI to provider SDK objects.
