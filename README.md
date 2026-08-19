# Podcast Intelligence Agent

A personal application for turning podcast content into searchable, useful intelligence with the OpenAI Responses API.

## Project status

Milestone 1 adds typed environment configuration and a minimal, single-turn Responses API client. Podcast ingestion and analysis behavior have not been added yet.

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
tests/                     Unit tests and synthetic fixtures
tests/integration/         Explicit live-service smoke tests
```

Runtime data, downloaded media, transcripts, local databases, and secrets are not committed.

## Working conventions

- Build one observable milestone at a time and define its acceptance check first.
- Keep OpenAI model selection configurable through `OPENAI_MODEL`.
- Keep reasoning effort configurable through `OPENAI_REASONING_EFFORT`; the initial baseline is `medium`.
- Disable API response storage by default through `OPENAI_STORE_RESPONSES=false`.
- Put future OpenAI SDK calls behind one application-owned client boundary.
- Use the Responses API for model interactions; do not introduce Chat Completions alongside it.
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

The initial model default was resolved from current OpenAI guidance on 2026-08-19. It remains configuration so it can be evaluated and changed without rewriting application code.
