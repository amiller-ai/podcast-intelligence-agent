# Repository instructions

## Scope

This repository is a personal Podcast Intelligence application built incrementally on the OpenAI Responses API.

## Workflow

1. Read `docs/ROADMAP.md` and confirm the single milestone under **Current**.
2. Work on one user-approved milestone at a time.
3. State or preserve acceptance criteria before implementing behavior.
4. Make the smallest coherent change that satisfies the milestone.
5. Run the relevant offline checks before proposing the next milestone.
6. After validation, update the roadmap with completion evidence, then commit and push the milestone.
7. Do not promote proposed work to **Current** without user approval.
8. Do not make live API calls unless the milestone explicitly requires them and credentials are present.

## Architecture conventions

- Use Python 3.12 and `uv`; commit `uv.lock`.
- Keep importable code under `src/podcast_intelligence/` and tests under `tests/`.
- Keep domain logic independent of OpenAI SDK objects and transport details.
- Centralize environment parsing in one configuration module when configuration code is introduced.
- Centralize Responses API calls behind one client boundary when the first integration is introduced.
- Prefer typed internal models and explicit schemas over unstructured dictionaries at boundaries.
- Keep ingestion, analysis, persistence, and presentation separable as those layers are added.
- Do not add a framework, database, queue, or deployment target before a milestone requires it.

## OpenAI conventions

- Use the Responses API for all model calls.
- Read `OPENAI_API_KEY` from the environment; never accept or commit a key in source or fixtures.
- Read the model ID from `OPENAI_MODEL`; do not scatter model strings through the codebase.
- Keep prompts outcome-oriented with explicit success criteria and output contracts.
- Use Structured Outputs for machine-consumed results.
- Preserve complete response items, call IDs, and state lineage in future tool or multi-turn loops.
- Keep storage and retention choices explicit; do not assume response persistence is acceptable.

## Quality gates

Run before completing a code milestone:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest --cov
```

Unit tests must be deterministic and network-free. Mark live tests with `@pytest.mark.integration`; the default suite excludes them.

## Data and privacy

- Do not commit `.env`, API keys, downloaded audio, full transcripts, local databases, or user-derived runtime data.
- Use small synthetic or explicitly licensed excerpts in tests.
- Avoid logging raw transcript content or model inputs by default.
