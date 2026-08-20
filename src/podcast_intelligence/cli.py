"""Small local command-line interface over persisted podcast intelligence."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from podcast_intelligence.intelligence import analyze_episode, answer_episode_question
from podcast_intelligence.intelligence_models import AnalysisEvidence, EvidenceBackedItem
from podcast_intelligence.persistence import (
    PersistenceError,
    StoredEpisodeAnalysis,
    StoredTranscript,
    TranscriptStore,
)
from podcast_intelligence.responses_client import QuestionResponse, ResponsesClientError
from podcast_intelligence.settings import Settings


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local CLI and return a process exit code."""

    return run_cli(
        sys.argv[1:] if argv is None else argv,
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def run_cli(
    argv: Sequence[str],
    *,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Parse and execute one command with injectable text streams for offline tests."""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        settings = _load_settings(arguments.database_path)
        database_path = settings.database_path
        if arguments.command == "list":
            with TranscriptStore(database_path) as store:
                _render_episode_list(store, stdout)
            return 0

        if not arguments.yes and not _confirm_provider_transmission(stdin, stderr):
            stderr.write("Cancelled; no transcript content was sent.\n")
            return 2

        with TranscriptStore(database_path) as store:
            transcript = store.get_transcript(arguments.transcript_run_id)
            if arguments.command == "analyze":
                result = analyze_episode(
                    arguments.transcript_run_id,
                    settings=settings,
                    store=store,
                    refresh=arguments.refresh,
                )
                _render_analysis(transcript, result.analysis, result.cache_status, stdout)
                return 0
            response = answer_episode_question(
                arguments.transcript_run_id,
                arguments.question,
                settings=settings,
                store=store,
            )
            _render_question_response(transcript, response, stdout)
            return 0
    except ValidationError:
        stderr.write(
            "Error: configuration is invalid; verify the required environment variables "
            "and configured limits.\n"
        )
    except (PersistenceError, ResponsesClientError, ValueError, OSError) as error:
        stderr.write(f"Error: {error}\n")
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="podcast-intelligence",
        description="Inspect and use locally persisted podcast intelligence.",
    )
    parser.add_argument(
        "--database-path",
        default=None,
        help="SQLite database path (overrides DATABASE_PATH and the configured default)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "list",
        help="List persisted episodes and local transcript/analysis status.",
    )

    analyze = subparsers.add_parser(
        "analyze",
        help="Create or reuse structured intelligence for one transcript run.",
    )
    _add_provider_command_arguments(analyze)
    analyze.add_argument(
        "--refresh",
        action="store_true",
        help="Create a new analysis attempt while preserving successful history.",
    )

    ask = subparsers.add_parser(
        "ask",
        help="Ask an evidence-grounded question about one transcript run.",
    )
    _add_provider_command_arguments(ask)
    ask.add_argument("question", help="Question to answer from transcript evidence.")
    return parser


def _add_provider_command_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("transcript_run_id", type=int, help="Run ID shown by the list command.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Explicitly approve sending selected transcript content to OpenAI.",
    )


def _load_settings(database_path: str | None) -> Settings:
    if database_path is None:
        return Settings()
    return Settings(database_path=Path(database_path))


def _confirm_provider_transmission(stdin: TextIO, stderr: TextIO) -> bool:
    stderr.write(
        "This command may send selected transcript content or excerpts to OpenAI. Continue? [y/N] "
    )
    stderr.flush()
    return stdin.readline().strip().casefold() in {"y", "yes"}


def _render_episode_list(store: TranscriptStore, output: TextIO) -> None:
    statuses = store.list_episode_statuses()
    if not statuses:
        output.write("No persisted episodes found.\n")
        return
    output.write("Persisted episodes:\n")
    for status in statuses:
        output.write(
            f"- run_id={_optional_int(status.transcript_run_id)} "
            f"episode_id={status.episode_id} title={_quoted_text(status.title)} "
            f"transcription={status.latest_transcription_status or 'missing'} "
            f"transcript={'available' if status.transcript_run_id is not None else 'missing'} "
            f"analysis={'available' if status.analysis_available else 'missing'} "
            f"latest_analysis_attempt={status.latest_analysis_status or 'missing'}\n"
        )


def _render_analysis(
    transcript: StoredTranscript,
    stored: StoredEpisodeAnalysis,
    cache_status: str,
    output: TextIO,
) -> None:
    output.write(
        f"Analysis for {_quoted_text(transcript.episode_title)} "
        f"(run_id={transcript.run_id}, cache={cache_status})\n"
    )
    output.write(
        f"Trace: analysis_run_id={stored.run_id} response_id={_safe_text(stored.response_id)} "
        f"model={_safe_text(stored.model)} input_tokens={_optional_int(stored.input_tokens)} "
        f"output_tokens={_optional_int(stored.output_tokens)} "
        f"total_tokens={_optional_int(stored.total_tokens)}\n"
    )
    _render_item("Summary", stored.analysis.summary, output)
    for heading, items in (
        ("Topics", stored.analysis.topics),
        ("People", stored.analysis.people),
        ("Claims", stored.analysis.claims),
        ("Actionable insights", stored.analysis.actionable_insights),
    ):
        output.write(f"{heading}:\n")
        if not items:
            output.write("  (none)\n")
        for item in items:
            _render_item("-", item, output)
    output.write("Limitations:\n")
    if not stored.analysis.limitations:
        output.write("  (none)\n")
    for limitation in stored.analysis.limitations:
        output.write(f"  - {_safe_text(limitation)}\n")


def _render_question_response(
    transcript: StoredTranscript,
    response: QuestionResponse,
    output: TextIO,
) -> None:
    answer = response.answer
    output.write(
        f"Answer for {_quoted_text(transcript.episode_title)} (run_id={transcript.run_id}, "
        "cache=not_persisted)\n"
    )
    output.write(f"{_safe_text(answer.answer)}\n")
    output.write(f"Insufficient evidence: {'yes' if answer.insufficient_evidence else 'no'}\n")
    _render_evidence(answer.evidence, output, indent="")
    output.write(
        f"Trace: responses={len(response.response_ids)} tool_calls={len(response.tool_calls)} "
        f"model={_safe_text(response.model)} input_tokens={response.usage.input_tokens} "
        f"output_tokens={response.usage.output_tokens} "
        f"total_tokens={response.usage.total_tokens}\n"
    )
    for call in response.tool_calls:
        segment_ids = ",".join(_safe_text(value) for value in call.result_segment_ids) or "none"
        output.write(
            f"  - response_id={_safe_text(call.response_id)} "
            f"call_id={_safe_text(call.call_id)} "
            f"tool={_safe_text(call.tool_name)} segment_ids={segment_ids}\n"
        )


def _render_item(label: str, item: EvidenceBackedItem, output: TextIO) -> None:
    if label == "-":
        output.write(f"  - {_safe_text(item.text)}\n")
        indent = "    "
    else:
        output.write(f"{label}: {_safe_text(item.text)}\n")
        indent = "  "
    _render_evidence(item.evidence, output, indent=indent)


def _render_evidence(
    evidence: Sequence[AnalysisEvidence],
    output: TextIO,
    *,
    indent: str,
) -> None:
    if not evidence:
        output.write(f"{indent}Evidence: (none)\n")
        return
    output.write(f"{indent}Evidence:\n")
    for citation in evidence:
        output.write(
            f"{indent}  - [{_safe_text(citation.segment_id)}] {_quoted_text(citation.quote)}\n"
        )


def _optional_int(value: int | None) -> str:
    return str(value) if value is not None else "unknown"


def _safe_text(value: str) -> str:
    return "".join(
        character if character.isprintable() else f"\\u{ord(character):04x}" for character in value
    )


def _quoted_text(value: str) -> str:
    return json.dumps(_safe_text(value), ensure_ascii=False)


if __name__ == "__main__":
    raise SystemExit(main())
