# Podcast Intelligence Agent

A personal application for turning podcast content into searchable, useful intelligence with the OpenAI Responses API.

## Project status

Milestone 6 adds a resumable local pipeline around the authorized RSS audio fallback. Verified feed and episode identities, run state, ordered provider parts, canonical transcripts, hashes, and provenance are stored in SQLite. An unchanged successful episode is returned from the database before audio retrieval or provider invocation; explicit refreshes preserve prior successful history. See the [`docs/ROADMAP.md`](docs/ROADMAP.md) source of truth for completion evidence and proposed work.

## Development setup

Prerequisites:

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- An OpenAI API key for later live-integration milestones

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

## Working conventions

- Build one observable milestone at a time and define its acceptance check first.
- Keep OpenAI model selection configurable through `OPENAI_MODEL`.
- Keep speech-to-text model and price configuration separate through `OPENAI_TRANSCRIPTION_MODEL` and `OPENAI_TRANSCRIPTION_COST_PER_MINUTE_USD`.
- Keep the SQLite location configurable through `DATABASE_PATH`; its safe ignored default is `data/podcast_intelligence.db`.
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
