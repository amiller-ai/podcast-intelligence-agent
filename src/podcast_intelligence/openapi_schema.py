"""Export the deterministic FastAPI schema used to generate frontend types."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from podcast_intelligence.settings import Settings
from podcast_intelligence.web import create_app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the local web API OpenAPI schema.")
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args(argv)
    schema = create_app(settings=Settings(_env_file=None)).openapi()
    arguments.output.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
