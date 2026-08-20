# Podcast Intelligence Agent

A personal application for turning podcast content into searchable, useful intelligence with the OpenAI Responses API.

## Project status

Milestone 8 adds a small local CLI for listing persisted episode state, running or reusing structured analysis, and asking evidence-grounded questions. It reuses the Milestone 7 SQLite, retrieval, evidence-validation, and Responses boundaries without adding ingestion, transcription, or a web framework. See the [`docs/ROADMAP.md`](docs/ROADMAP.md) source of truth for completion evidence and proposed work.

## Development setup

Prerequisites:

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- An OpenAI API key for commands that send transcript content or excerpts to OpenAI

Install the project and its development dependencies:

```bash
uv sync --all-groups
```

Copy the environment template and add your key locally:

```bash
cp .env.example .env
```

`.env` is ignored by Git and loaded by the typed application settings. Secrets are represented with Pydantic's redacted `SecretStr` type.

## Verify the environment

Run the complete offline verification suite:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest --cov
```

Tests that call a network service must use the `integration` marker and are excluded from the normal test run. Run them only when a milestone explicitly requires live verification.

Run the explicit Responses API smoke test with:

```bash
uv run pytest -o "addopts=--strict-config --strict-markers" -m integration tests/integration
```

## Repository layout

```text
src/podcast_intelligence/  Application package
src/podcast_intelligence/ingestion/  Podcast source adapters
docs/                       Durable project state and design notes
tests/                     Unit tests and synthetic fixtures
tests/integration/         Explicit live-service smoke tests
```

Runtime data, downloaded media, transcripts, local databases, and secrets are not committed.

## Local transcript storage

The default SQLite database is `data/podcast_intelligence.db`. Override it with `DATABASE_PATH` for tests or another explicitly selected local runtime directory. The configured parent directory is created when the store is initialized; no database service or ORM is required.

Transcript text and provider provenance are sensitive user-derived data. The entire `data/` directory is ignored by Git. Protect any backup of the database like the original transcripts, restrict access to the current user, and define your own retention/deletion policy. Audio enclosures and ffmpeg chunks remain temporary and are deleted after success or failure; they are never stored in SQLite.

SQLite is the canonical local source of truth. Future search or vector indexes should be treated as rebuildable derivatives rather than independent transcript stores.

## Local podcast intelligence

Milestone 7 derives exact-offset transcript segments and an FTS5 index from each canonical transcript. The index is tied to the transcript content hash and segmenter version, is episode-scoped, and can be rebuilt without changing the canonical transcript. Models never receive a database handle or arbitrary SQL capability; they can only request bounded metadata, lexical search results, and exact selected-transcript segments through strict read-only schemas.

Structured analyses are persisted only after their schema, transcript ownership, segment IDs, and exact quoted evidence pass deterministic validation. Analysis cache identity includes the transcript hash, requested model, prompt version, schema version, and segmenter version. Explicit refreshes preserve prior successful analysis history.

The committed 20-case synthetic evaluation corpus keeps retrieval, tool decisions, call lineage, final-answer evidence, structured analysis, and operational diagnostics separately observable. It runs within the default offline suite and does not send transcript content to a provider.

## Local command-line interface

List persisted episode, transcript, and analysis state without loading or printing transcript text:

```bash
uv run podcast-intelligence list
```

Use the reported transcript run ID to create or reuse structured analysis, or ask a question:

```bash
uv run podcast-intelligence analyze 7
uv run podcast-intelligence ask 7 "What are the episode's main evidence-backed claims?"
```

`analyze` and `ask` request explicit confirmation because they may transmit selected transcript content or excerpts to OpenAI. Pass `--yes` only when that transmission has already been approved. A cached analysis can be reused without an API key; a cache miss and every question require `OPENAI_API_KEY`. These commands never resolve an episode, download audio, or invoke transcription.

Place `--database-path /path/to/database.db` before the subcommand to select an alternate local database.

### Live-analysis privacy boundary

A live Milestone 7 evaluation sends the full selected canonical transcript, episode metadata, and subsequent retrieved excerpts to the OpenAI Responses API. `OPENAI_STORE_RESPONSES=false` disables response persistence through the API request, but it does not avoid transmitting that content to OpenAI for processing. The run does not resolve an episode, download audio, or invoke transcription.

Run it only after explicitly approving that transmission:

```bash
RUN_LIVE_PODCAST_INTELLIGENCE=1 uv run pytest \
  -o "addopts=--strict-config --strict-markers" \
  -m integration tests/integration/test_podcast_intelligence.py
```

## Working conventions

- Build one observable milestone at a time and define its acceptance check first.
- Keep OpenAI model selection configurable through `OPENAI_MODEL`.
- Keep speech-to-text model and price configuration separate through `OPENAI_TRANSCRIPTION_MODEL` and `OPENAI_TRANSCRIPTION_COST_PER_MINUTE_USD`.
- Keep the SQLite location configurable through `DATABASE_PATH`; its safe ignored default is `data/podcast_intelligence.db`.
- Keep segmentation, retrieval, tool-loop, output, and context limits centrally configurable through the `INTELLIGENCE_*` settings.
- Keep reasoning effort configurable through `OPENAI_REASONING_EFFORT`; the initial baseline is `medium`.
- Disable API response storage by default through `OPENAI_STORE_RESPONSES=false`.
- Put future OpenAI SDK calls behind one application-owned client boundary.
- Use the Responses API for reasoning and podcast-intelligence interactions. Use the Audio Transcriptions API only for bounded speech-to-text ingestion; do not introduce Chat Completions.
- Keep unit tests deterministic and offline. Separate live API checks from the default suite.
- Use typed data models and Structured Outputs for contracts that downstream code consumes.
- Preserve response and tool-call identifiers when multi-turn or tool workflows are added.
- Add a dependency with `uv add` or `uv add --dev`, then commit both `pyproject.toml` and `uv.lock`.

Durable contributor and coding-agent instructions live in [`AGENTS.md`](AGENTS.md).

## OpenAI references

- [Developer quickstart](https://developers.openai.com/api/docs/quickstart)
- [Responses API guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [File transcription](https://developers.openai.com/api/docs/guides/speech-to-text)
- [API pricing](https://developers.openai.com/api/docs/pricing)
- [API data controls](https://developers.openai.com/api/docs/guides/your-data)

The initial model default was resolved from current OpenAI guidance on 2026-08-19. It remains configuration so it can be evaluated and changed without rewriting application code.

Current OpenAI documentation routes bounded file transcription through `/v1/audio/transcriptions`, recommends `gpt-transcribe`, and limits files to 25 MB. The application therefore treats transcription as a narrowly scoped ingestion exception while retaining Responses for later reasoning. The current data-control table lists the transcription endpoint as not used for training, with no abuse-monitoring or application-state retention.

Run the explicitly authorized live podcast test only when you intend to download and transcribe its RSS enclosure:

```bash
RUN_LIVE_PODCAST_TRANSCRIPTION=1 uv run pytest \
  -o "addopts=--strict-config --strict-markers" \
  -m integration tests/integration/test_podcast_transcription.py
```

The live test persists transcript content and provenance to the configured SQLite database, then verifies a repeated ingestion is a cache hit. It never persists audio. It incurs OpenAI transcription charges for episodes that do not already have a matching successful cached transcript.
