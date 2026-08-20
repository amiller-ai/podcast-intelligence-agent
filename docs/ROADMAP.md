# Podcast Intelligence Roadmap

Last updated: 2026-08-20

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

- [x] Add centrally configured SQLite settings with a safe ignored default path and no implicit creation outside the configured runtime directory.
- [x] Add an application-owned persistence boundary using `sqlite3`, foreign-key enforcement, explicit transactions, and deterministic versioned migrations.
- [x] Create the minimum relational model above with uniqueness, foreign-key, status, and non-negative resource constraints enforced in SQLite.
- [x] Persist verified feed and episode identity without using title as a primary or cache key.
- [x] Extend the transcription result contract to preserve ordered provider parts and persist their request IDs, text, language, usage metadata, and model provenance.
- [x] Compute and persist source fingerprints, audio SHA-256, transcript content hashes, model identity, chunker version, prompt version, estimated cost, and byte count.
- [x] Return a matching successful cached transcript before audio retrieval or provider invocation, with deterministic tests proving those boundaries were not called.
- [x] Make repeated ingestion idempotent and preserve prior successful history when an explicit refresh produces a new source or transcription identity.
- [x] Persist success atomically; record safe failed-run state without partial transcript rows or raw content in logs/errors.
- [x] Keep audio and ffmpeg chunks temporary, keep the database and other runtime data ignored by Git, and document local transcript sensitivity and backup expectations.
- [x] Keep the default test suite deterministic and network-free, including fresh migration, upgrade, rollback-on-failure, cache-hit, cache-miss, refresh, and corruption/error cases.
- [x] Pass all repository quality gates, lockfile consistency, and a package build.

Status: complete on 2026-08-19.

Completion evidence:

- Storage contract: schema versions 1 and 2 create the five-table relational model with foreign keys, explicit transactions, database constraints, private `0600` file creation, and the ignored default `data/podcast_intelligence.db`
- Identity contract: feed URL plus RSS GUID identifies an episode; normalized source and definitive transcription hashes cover enclosure metadata, audio SHA-256, model, chunker version, and prompt version without using title as a key
- Provenance contract: every OpenAI chunk persists its ordinal, request ID, model, language, normalized token/duration usage, and text; the canonical transcript is deterministic ordered assembly with a verified SHA-256 hash
- Pipeline contract: an unchanged source returns the successful SQLite transcript before media retrieval; explicit refresh re-downloads while definitive audio identity can skip the provider, and new identities preserve successful history
- Failure contract: pending/running/succeeded/failed state is durable; transcript parts, canonical text, and success state commit atomically; application failures store safe codes/messages without partial content
- Retention contract: SQLite stores transcript content but never audio or ffmpeg chunks; local sensitivity, backup, retention, and derivative-index expectations are documented, and all runtime data remains ignored by Git
- Offline validation: 162 deterministic tests passed with 93.86% total coverage; Ruff formatting and linting, strict mypy, lockfile consistency, and package build all passed
- Live validation: both user-supplied Spotify episodes resolved, downloaded only their verified RSS enclosures, transcribed through OpenAI, persisted with full ordered provenance, passed database hash/assembly readback, and returned as source-cache hits on an immediate second ingestion
- Live episode `0VPwvReM2olZDWl3YOHfqh`: 4,576 seconds, 74,504,048 audio bytes, $0.3432 estimate, 6 provider parts/request IDs, 84,995 transcript characters, content SHA-256 `9f909754e531c42667e042d4e2bb58c340890c1e5a453a59bfdf6287d7b69ec3`
- Live episode `7HH9LCznGvLZHYYuXaVOd9`: 5,970 seconds, 143,266,039 audio bytes, $0.4478 estimate, 8 provider parts/request IDs, 95,929 transcript characters, content SHA-256 `fbf32d42d38b39dd584ae10470318cb697a1204a38eee89be2c8da7ab96daf59`
- Resource-bound evidence: the second supplied enclosure correctly failed the inherited 100,000,000-byte default on the first attempt with no partial transcript; the explicitly authorized live test then used a caller-scoped 150,000,000-byte cap while leaving the production default and all other duration, cost, network, media, temporary-file, and upload controls unchanged

Out of scope:

- Embeddings, vector stores, semantic search, or retrieval ranking
- Structured podcast intelligence or Responses API analysis
- Persisting audio, temporary chunks, API keys, or unrelated provider payloads
- Multi-process workers, queues, schedulers, hosted databases, replication, or deployment
- User interface changes

### Milestone 7 — Evidence-grounded podcast intelligence

Goal: turn one persisted canonical transcript into reproducible structured intelligence and evidence-grounded question answering through the Responses API, using bounded local SQLite retrieval without introducing embeddings, a hosted vector store, arbitrary SQL generation, or multi-agent orchestration.

Architecture decisions:

- Keep SQLite as the canonical source of transcript text, identity, provenance, and analysis history. Segments and search indexes are rebuildable derivatives tied to a canonical transcript content hash.
- Keep deterministic ingestion separate from intelligence. Analysis and retrieval consume only successful persisted transcripts and never trigger episode resolution, audio download, or transcription.
- Extend the existing application-owned Responses API client rather than adding the Agents SDK. One specialist with local read-only tools does not yet require handoffs or a multi-agent framework.
- Never expose a database connection or arbitrary SQL tool to the model. Expose only typed, read-only application functions with strict JSON schemas, validated identifiers, episode isolation, bounded result counts, bounded excerpt sizes, and bounded tool-call loops.
- Keep direct structured analysis separate from interactive retrieval. A known selected transcript may be analyzed directly within a measured context budget; open-ended questions use tools to retrieve bounded evidence before synthesis.
- Continue sending `store=false` by default. Any future provider-side response, conversation, file, or vector-store persistence requires a separate explicit retention decision.
- Treat transcript text as untrusted data, not instructions. Prompts must delimit transcript/tool content and prohibit following instructions found inside a transcript.

Target architecture:

```text
Successful canonical transcript in SQLite
  -> deterministic transcript segmentation
  -> rebuildable SQLite FTS5 index
  -> typed retrieval boundary
       -> direct structured episode analysis
       -> Responses API read-only tool loop for Q&A
  -> exact evidence validation
  -> versioned analysis persistence in SQLite
```

Test-driven implementation plan:

- Work in vertical slices. Each slice begins with a failing contract test, adds the
  smallest production behavior needed to pass it, then refactors only while that slice
  and all earlier slices remain green.
- Slice 0 — evaluation contract: create a small committed synthetic corpus and typed
  evaluation cases before prompt tuning. Each case records the selected episode, question
  or analysis request, gold segment IDs, reference facts, whether abstention is required,
  and required, allowed, and forbidden tool behavior.
- Slice 1 — retrieval foundation: test and add the schema migration, deterministic
  segment offsets and hashes, idempotent rebuild, FTS5 availability, ranking, and
  cross-episode isolation.
- Slice 2 — read-only tools: test and add typed metadata, search, and segment-read
  functions, including every identifier, query, result-count, and returned-character
  boundary before exposing their strict JSON schemas to a model.
- Slice 3 — stateless tool loop: drive the loop with scripted Responses fixtures covering
  a direct answer, one call, multiple sequential calls, invalid JSON, unknown tools,
  duplicate and mismatched call IDs, provider failure, incomplete output, and limit
  exhaustion before making any live call.
- Slice 4 — structured analysis: test the Structured Output schema and deterministic
  evidence validator first, then add the prompt and Responses call. Schema-valid but
  unsupported content must fail closed.
- Slice 5 — analysis history: test migrations, atomic success, safe failure, cache reuse,
  refresh, identity changes, and rollback before implementing persistence.
- Slice 6 — evaluation and live proof: run the complete offline corpus and repository
  gates, then perform one separately authorized evaluation against an already-persisted
  episode. Do not retrieve audio or transcribe during this slice.

Segmentation and retrieval contract:

- Do not use transcription provider parts as retrieval units; they reflect upload chunking rather than stable semantic boundaries.
- Build deterministic, ordered transcript segments with `transcript_id`, ordinal, character start/end offsets, text, text hash, and segmenter version.
- Derive segment identity from the canonical transcript content hash, segmenter version, and ordinal so a transcript or segmenter change produces a new rebuildable index identity.
- Add a SQLite FTS5 derivative over segment text for initial lexical retrieval. Embeddings remain deferred until an evaluated question set demonstrates material lexical-retrieval gaps.
- Return segment IDs, exact excerpts, character offsets, transcript identity, and retrieval scores; never return unbounded transcript text from a search call.
- Provide three initial application-owned tools:
  - `get_episode_metadata(episode_id)` for verified episode and transcript identity
  - `search_transcript(episode_id, query, limit)` for bounded lexical retrieval
  - `read_transcript_segments(segment_ids)` for exact bounded evidence expansion

Responses API and tool-loop contract:

- Define tools with strict schemas and `additionalProperties: false`; validate every tool call again in application code before executing it.
- Keep the loop stateless with `store=false`. Request encrypted reasoning content, retain
  every returned output item in order, append each `function_call_output` with its exact
  `call_id`, and replay the complete item sequence on the next request. Do not replace this
  lineage with `previous_response_id` or flattened text.
- Treat reasoning items as opaque continuity state. Replay them, but do not inspect, grade,
  log, or persist encrypted reasoning or private chain-of-thought. Evaluate observable
  decisions, tool calls, retrieved evidence, and outputs instead.
- Apply explicit limits to tool-call count, query length, result count, segment count, and total returned characters. Convert invalid arguments, unknown IDs, policy violations, and provider failures into safe application-owned errors.
- Keep tool execution sequential initially. Parallel or programmatic tool calling requires separate evidence that it improves the bounded single-episode workflow.
- Produce a final answer only from returned evidence and include stable application citations to transcript and segment IDs. The model must state when evidence is insufficient.

Structured intelligence contract:

- Define one typed Structured Output containing an episode summary, topics, people, claims, evidence, actionable insights, and limitations.
- Require every material claim and actionable insight to reference at least one evidence object containing a segment ID and exact quote. Do not invent timestamps or speaker identity because the current transcript contract does not preserve diarization or time-aligned segments.
- Validate evidence deterministically after the model response: the segment must exist, belong to the analyzed transcript identity, and contain the exact quoted text.
- Build the analysis cache identity from transcript content hash, analysis type, model, prompt version, schema version, and segmenter version. Matching successful analyses are reused; explicit refresh preserves prior history.
- Persist structured outputs only after schema and evidence validation succeed. Failed attempts retain safe state and provenance without partial canonical analysis rows.

Minimum relational extension:

| Table | Purpose |
| --- | --- |
| `transcript_segments` | Deterministic segment text, offsets, hashes, ordinal, transcript identity, and segmenter version |
| `transcript_segments_fts` | Rebuildable SQLite FTS5 lexical index over segment text |
| `analysis_runs` | Pending/running/succeeded/failed analysis attempts, cache identity, model/prompt/schema versions, response ID, usage, and safe errors |
| `episode_analyses` | Validated canonical Structured Output linked to one successful analysis run |
| `analysis_evidence` | Claim/insight evidence links to exact transcript segments and quotes |

Evaluation contract:

Evaluate each surface separately so a fluent answer cannot hide a retrieval or tool failure.
The committed offline cases and local runner are the release source of truth; a hosted eval
service is not required.

| Surface | What is recorded | Release grading |
| --- | --- | --- |
| Retrieval | Query, ranked segment IDs, scores, gold segment IDs, and episode identity | Exact deterministic tests; on the known-answer set report hit rate, recall at 5, reciprocal rank, and every miss separately |
| Agent decision | Whether a tool was needed, required/allowed/forbidden calls, abstention, and prompt-injection resistance | Tool-selection and abstention classification; do not require one exact call sequence when multiple bounded traces are valid |
| Tool calls and trace | Output item order, response IDs, item types, tool names, validated arguments, call IDs, result segment IDs, errors, and limit counters | 100% valid schemas and call-ID pairing; zero unknown, write-capable, cross-episode, over-limit, or unreturned calls |
| Final Q&A response | Answer, cited transcript/segment IDs, exact supporting excerpts, reference facts, and insufficiency behavior | 100% citation validity and claim support; score correctness and completeness separately from retrieval; unsupported claims fail the case |
| Structured episode analysis | Parsed output, field-level schema results, evidence links, exact quotes, reference facts, and limitations | 100% schema validity, evidence ownership, exact quote matching, and evidence coverage for every material claim and actionable insight |
| Operational behavior | Model/prompt/schema/segmenter versions, response IDs, usage, tool-call count, returned characters, latency, and safe error code | Enforce configured budgets; report tokens, latency, and estimated cost as diagnostics rather than quality proxies |

Evaluation and release gates:

- Build deterministic synthetic fixtures covering segmentation, FTS ranking, bounded tools,
  invalid IDs, episode isolation, prompt injection inside transcript text, tool-loop state
  lineage, schema failures, provider failures, cache identity, and persistence rollback.
- Define at least 20 evaluation cases before prompt tuning, spanning direct metadata,
  answerable lexical retrieval, multi-segment synthesis, insufficient evidence, ambiguous
  wording, transcript prompt injection, and invalid or cross-episode access attempts.
- For the deterministic corpus, require 100% segmentation, tool-policy, call-lineage,
  schema, evidence, isolation, and persistence checks. For model-bearing known-answer cases,
  require retrieval recall at 5 of at least 90%, answer correctness of at least 90%, and
  100% citation support and correct abstention. Report the numerator, denominator, and case
  failures rather than only an aggregate score.
- Use deterministic validators for schemas, identifiers, bounds, hashes, exact quotes,
  evidence coverage, and tool traces. A rubric-based model grader may supplement relevance,
  completeness, and usefulness only after agreement is checked against human-reviewed
  labels; it cannot override a deterministic failure. Prefer pairwise grading for prompt or
  model comparisons.
- Test cache hit, cache miss, explicit refresh, prompt/schema/model version changes, and
  transcript-hash changes without network access.
- Keep the default suite deterministic and network-free with injected or mocked Responses
  and retrieval boundaries. The live evaluation is an explicit integration run, not part of
  the default suite and not evidence of broad production reliability.
- Complete one explicit live evaluation on one user-selected already-persisted episode
  after credentials and authorization are confirmed. It must not download audio or
  retranscribe the episode, and its result must pass the same schema and exact-evidence
  gates before persistence.
- Pass all repository quality gates, lockfile consistency, and a package build before
  completion.

Acceptance criteria:

- [x] Add deterministic transcript segmentation and a rebuildable SQLite FTS5 index tied to canonical transcript and segmenter identities.
- [x] Add typed, bounded, read-only metadata, search, and segment-read boundaries without exposing arbitrary SQL or database handles.
- [x] Extend the Responses API boundary with a strict application-owned function-tool loop that preserves complete response items, call IDs, tool outputs, and state lineage.
- [x] Add the typed Structured Output contract for summaries, topics, people, claims, evidence, actionable insights, and limitations.
- [x] Enforce exact evidence ownership and quote matching before returning or persisting intelligence results.
- [x] Persist versioned analysis attempts, validated outputs, evidence links, response IDs, usage metadata, and safe failures transactionally.
- [x] Make successful analysis reuse idempotent across transcript, model, prompt, schema, and segmenter identities while preserving refresh history.
- [x] Treat transcripts as untrusted content, keep provider response storage disabled by default, and document analysis-data sensitivity and retention.
- [x] Add the typed offline evaluation corpus and runner with separate retrieval,
  agent-decision, tool-trace, final-response, structured-analysis, and operational results,
  plus the deterministic persistence, failure, and cache coverage defined above.
- [x] Complete the authorized live evaluation without audio retrieval or retranscription and show the validated persisted result.
- [x] Pass all repository quality gates, lockfile consistency, and a package build.

Status: complete on 2026-08-20.

Implementation evidence:

- Storage and retrieval: schema version 3 adds exact-offset transcript segments, a rebuildable episode-scoped FTS5 index, analysis attempts, validated canonical analyses, and normalized evidence links without changing transcript identity or ingestion behavior.
- Tool-loop contract: strict metadata, search, and segment-read schemas; application validation; sequential bounded calls; `store=false`; complete stateless output-item replay; exact call-ID pairing; and opaque encrypted reasoning continuity without reasoning inspection or persistence.
- Evidence contract: Pydantic Structured Outputs for question answers and episode intelligence; transcript ownership, segment existence, and exact quote matching must pass before return or atomic persistence. A conservative deterministic aligner may replace punctuation, capitalization, apostrophe, or whitespace drift only when the model's complete word sequence occurs exactly once in the cited segment; paraphrases, missing words, ambiguous matches, unknown segments, and cross-transcript evidence still fail closed.
- Cache contract: structured analysis identity covers transcript content hash, analysis type, requested model, prompt version, schema version, and segmenter version; refresh and identity changes preserve prior successful history.
- Evaluation contract: 20 typed synthetic cases across all seven planned categories; 14 retrieval-bearing cases achieved 100% hit rate, 100% recall at 5, 0.9524 mean reciprocal rank, and zero misses. Deterministic tool, trace, schema, evidence, isolation, failure, rollback, and cache gates passed.
- Offline validation: 207 deterministic tests passed with 91.55% total coverage; Ruff formatting and linting, strict mypy, lockfile consistency, and source/wheel builds all passed.
- Live analysis validation: after explicit authorization, the persisted 84,995-character transcript for Spotify episode `0VPwvReM2olZDWl3YOHfqh` was analyzed through `gpt-5.6-sol` with `store=false`, without audio retrieval or retranscription. The successful prompt-v2 run persisted one canonical analysis with response ID, 21,334 input tokens, 6,451 output tokens, 27,785 total tokens, and 70 evidence links; all 70 quotes matched their cited segments exactly and zero evidence links crossed transcript ownership.
- Live failure and cache evidence: an initial sandboxed provider failure and two exact-quote failures were stored as safe failed attempts with no partial analysis or evidence. Those failures motivated the prompt-v2 and conservative source-alignment tests rather than a weaker validator. The successful analysis was then returned from the local cache without another analysis run, and the transcript count remained unchanged at two.
- Live trace and response evaluation: the fixed-label release run passed agent-decision, tool-trace, final-response, structured-analysis, and operational graders. One preliminary graded Q&A response missed a required literal reference term before the passing release run; this single live workflow validates the milestone contract but is not evidence of broad production reliability.

Out of scope:

- Embeddings, local or hosted vector stores, OpenAI File Search, semantic retrieval, or reranking
- Agents SDK adoption, multiple agents, specialist handoffs, or remote MCP servers
- Cross-episode synthesis, external web research, or combining transcript evidence with unrelated sources
- Arbitrary model-generated SQL, write-capable model tools, or transcript mutation
- Diarization, speaker attribution, timestamp reconstruction, or retranscription
- User interface, hosted database, workers, queues, schedulers, or deployment

## Current

### Milestone 8 — Local command-line interface

Goal: validate the complete personal workflow through a small local CLI before adding a web
framework or browser interface.

Acceptance criteria:

- [x] List persisted episodes and their transcript and analysis status without exposing raw transcript
  content by default.
- [x] Select one episode, run or reuse its structured analysis, and ask evidence-grounded questions
  through the existing application services.
- [x] Render answers, exact evidence citations, cache status, safe errors, and an observable trace
  summary without exposing encrypted reasoning or secrets.
- [x] Require an explicit confirmation before any command that may transmit transcript content or
  retrieved excerpts to OpenAI; do not implicitly download audio or retranscribe an episode.
- [x] Keep command behavior deterministic and network-free in the default tests by injecting the
  existing persistence, retrieval, and Responses boundaries.
- [x] Pass all repository quality gates, lockfile consistency, and source/wheel builds.

Status: complete on 2026-08-20.

Completion evidence:

- Command contract: the dependency-free `podcast-intelligence` console entry point provides
  `list`, `analyze TRANSCRIPT_RUN_ID`, and `ask TRANSCRIPT_RUN_ID QUESTION`; selection uses durable
  transcript-run identity rather than a mutable title.
- Local-state contract: `list` uses a typed SQLite status projection that never selects or hydrates
  transcript text and separately reports the latest transcription attempt, usable transcript,
  successful analysis availability, and latest analysis attempt.
- Provider and privacy contract: `analyze` and `ask` require an interactive confirmation or
  explicit `--yes`; they reuse the existing persistence, analysis, retrieval, and Responses
  boundaries and expose no ingestion or transcription command. Untrusted terminal text is escaped,
  and observable traces omit tool arguments, provider objects, and encrypted reasoning.
- Offline contract: listing and cached analysis reuse do not require an API key. Provider adapters
  require `OPENAI_API_KEY` only when constructed for a provider-bound operation, and a missing key
  creates no partial analysis or transcription run.
- Validation: 218 deterministic offline tests passed with 91.43% total coverage; Ruff formatting
  and linting, strict mypy, lockfile consistency, source/wheel builds, and an installed console help
  smoke test all passed.
- Live calls: none required or performed.

Out of scope: a local web server, browser UI, deployment, new retrieval architecture, or provider
SDK objects in presentation code.

## Proposed

These milestones are directional and require approval before becoming current.

### Milestone 9 — Local web application UI

Goal: after the CLI validates the user workflow and presentation contracts, add a minimal local
web application for episode selection, ingestion and analysis status, questions and answers,
evidence inspection, trace summaries, and safe errors.

The web UI must reuse the same application-owned services and typed presentation contracts proven
by the CLI rather than duplicating persistence, retrieval, tool-loop, or provider logic.
