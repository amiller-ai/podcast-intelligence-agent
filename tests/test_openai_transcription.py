from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from openai import OpenAI
from openai.types.audio.transcription import Transcription

from podcast_intelligence.audio_transcription import AudioTranscriptionProviderError
from podcast_intelligence.openai_transcription import (
    FfmpegAudioChunker,
    OpenAIAudioTranscriber,
    estimate_openai_transcription_cost,
)
from podcast_intelligence.settings import Settings


class SyntheticChunker:
    def __init__(self, payloads: Sequence[bytes]) -> None:
        self.payloads = payloads
        self.paths: list[Path] = []
        self.duration_seconds: int | None = None
        self.max_chunk_bytes: int | None = None

    def split(
        self,
        _audio_path: Path,
        output_directory: Path,
        *,
        duration_seconds: int,
        max_chunk_bytes: int,
    ) -> tuple[Path, ...]:
        self.duration_seconds = duration_seconds
        self.max_chunk_bytes = max_chunk_bytes
        for index, payload in enumerate(self.payloads):
            path = output_directory / f"chunk-{index:03d}.mp3"
            path.write_bytes(payload)
            self.paths.append(path)
        return tuple(self.paths)


def _transcription(text: str, request_id: str) -> Transcription:
    response = Transcription(text=text)
    object.__setattr__(response, "_request_id", request_id)
    return response


def test_openai_transcriber_chunks_in_order_preserves_ids_and_cleans_up(
    tmp_path: Path,
) -> None:
    source = tmp_path / "episode.mp3"
    source.write_bytes(b"source")
    chunker = SyntheticChunker([b"first", b"second"])
    sdk_client = Mock(spec=OpenAI)
    sdk_client.audio.transcriptions.create.side_effect = [
        _transcription("First chunk transcript.", "req_1"),
        _transcription("Second chunk transcript.", "req_2"),
    ]
    transcriber = OpenAIAudioTranscriber(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
        chunker=chunker,
    )

    result = transcriber.transcribe(
        source,
        media_type="audio/mpeg",
        duration_seconds=1_800,
    )

    assert result.text == "First chunk transcript.\n\nSecond chunk transcript."
    assert result.provider == "openai"
    assert result.model == "gpt-transcribe"
    assert result.request_ids == ("req_1", "req_2")
    assert result.response_id == "req_1"
    assert result.chunk_count == 2
    assert chunker.duration_seconds == 1_800
    assert chunker.max_chunk_bytes == 25_000_000
    assert all(not path.exists() for path in chunker.paths)

    first_call, second_call = sdk_client.audio.transcriptions.create.call_args_list
    assert "prompt" not in first_call.kwargs
    assert second_call.kwargs["prompt"] == "First chunk transcript."
    assert first_call.kwargs["model"] == "gpt-transcribe"
    assert first_call.kwargs["response_format"] == "json"


def test_openai_transcriber_rejects_empty_or_unexpected_response(tmp_path: Path) -> None:
    source = tmp_path / "episode.mp3"
    source.write_bytes(b"source")
    sdk_client = Mock(spec=OpenAI)
    sdk_client.audio.transcriptions.create.return_value = "unexpected"
    transcriber = OpenAIAudioTranscriber(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
        chunker=SyntheticChunker([b"chunk"]),
    )

    with pytest.raises(AudioTranscriptionProviderError, match="unexpected response"):
        transcriber.transcribe(source, media_type="audio/mpeg", duration_seconds=60)


def test_openai_transcriber_converts_request_failure(tmp_path: Path) -> None:
    source = tmp_path / "episode.mp3"
    source.write_bytes(b"source")
    sdk_client = Mock(spec=OpenAI)
    sdk_client.audio.transcriptions.create.side_effect = OSError("sensitive detail")
    chunker = SyntheticChunker([b"chunk"])
    transcriber = OpenAIAudioTranscriber(
        Settings.model_validate({"openai_api_key": "test-key"}),
        client=cast(OpenAI, sdk_client),
        chunker=chunker,
    )

    with pytest.raises(AudioTranscriptionProviderError, match="request failed") as captured:
        transcriber.transcribe(source, media_type="audio/mpeg", duration_seconds=60)

    assert "sensitive detail" not in str(captured.value)
    assert all(not path.exists() for path in chunker.paths)


def test_ffmpeg_chunker_skips_file_within_upload_limit(tmp_path: Path) -> None:
    source = tmp_path / "episode.mp3"
    source.write_bytes(b"small")

    chunks = FfmpegAudioChunker().split(
        source,
        tmp_path / "chunks",
        duration_seconds=60,
        max_chunk_bytes=8,
    )

    assert chunks == (source,)


def test_ffmpeg_chunker_builds_bounded_segment_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "episode.mp3"
    source.write_bytes(b"x" * 20)
    output_directory = tmp_path / "chunks"
    output_directory.mkdir()
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> None:
        commands.append(command)
        Path(command[-1].replace("%03d", "000")).write_bytes(b"chunk-1")
        Path(command[-1].replace("%03d", "001")).write_bytes(b"chunk-2")

    monkeypatch.setattr("imageio_ffmpeg.get_ffmpeg_exe", lambda: "/test/ffmpeg")
    monkeypatch.setattr("subprocess.run", fake_run)

    chunks = FfmpegAudioChunker(target_chunk_bytes=8).split(
        source,
        output_directory,
        duration_seconds=100,
        max_chunk_bytes=10,
    )

    assert [chunk.name for chunk in chunks] == ["chunk-000.mp3", "chunk-001.mp3"]
    assert commands[0][0] == "/test/ffmpeg"
    assert commands[0][commands[0].index("-segment_time") + 1] == "60"


def test_ffmpeg_chunker_rejects_oversized_generated_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "episode.mp3"
    source.write_bytes(b"source is larger")
    output_directory = tmp_path / "chunks"
    output_directory.mkdir()

    def fake_run(command: list[str], **_kwargs: object) -> None:
        Path(command[-1].replace("%03d", "000")).write_bytes(b"123456789")

    monkeypatch.setattr("imageio_ffmpeg.get_ffmpeg_exe", lambda: "/test/ffmpeg")
    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(AudioTranscriptionProviderError, match="upload limit"):
        FfmpegAudioChunker(target_chunk_bytes=4).split(
            source,
            output_directory,
            duration_seconds=100,
            max_chunk_bytes=8,
        )


@pytest.mark.parametrize(
    ("target_chunk_bytes", "duration_seconds", "max_chunk_bytes"),
    [
        (0, 60, 25_000_000),
        (20_000_000, 0, 25_000_000),
        (20_000_000, 60, 0),
    ],
)
def test_ffmpeg_chunker_rejects_invalid_bounds(
    tmp_path: Path,
    target_chunk_bytes: int,
    duration_seconds: int,
    max_chunk_bytes: int,
) -> None:
    source = tmp_path / "episode.mp3"
    source.write_bytes(b"source")

    with pytest.raises(ValueError):
        FfmpegAudioChunker(target_chunk_bytes=target_chunk_bytes).split(
            source,
            tmp_path,
            duration_seconds=duration_seconds,
            max_chunk_bytes=max_chunk_bytes,
        )


@pytest.mark.parametrize(
    ("duration_seconds", "price", "expected"),
    [
        (60, Decimal("0.0045"), Decimal("0.0045")),
        (4_576, Decimal("0.0045"), Decimal("0.3432")),
    ],
)
def test_estimate_openai_transcription_cost(
    duration_seconds: int,
    price: Decimal,
    expected: Decimal,
) -> None:
    assert (
        estimate_openai_transcription_cost(
            duration_seconds,
            price_per_minute_usd=price,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("duration_seconds", "price"),
    [
        (0, Decimal("0.0045")),
        (60, Decimal(0)),
        (60, Decimal("NaN")),
    ],
)
def test_estimate_openai_transcription_cost_rejects_invalid_inputs(
    duration_seconds: int,
    price: Decimal,
) -> None:
    with pytest.raises(ValueError):
        estimate_openai_transcription_cost(
            duration_seconds,
            price_per_minute_usd=price,
        )
