"""OpenAI file-transcription adapter with bounded temporary chunking."""

import json
import subprocess
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

import imageio_ffmpeg  # type: ignore[import-untyped]
from openai import OpenAI, OpenAIError
from openai.types.audio.transcription import Transcription, UsageDuration, UsageTokens

from podcast_intelligence.audio_transcription import (
    AudioTranscriptionProviderError,
    ProviderTranscript,
    ProviderTranscriptPart,
    TranscriptionUsage,
)
from podcast_intelligence.settings import Settings

_API_UPLOAD_LIMIT_BYTES = 25_000_000
_TARGET_CHUNK_BYTES = 20_000_000
_MAX_CHUNK_SECONDS = 15 * 60
_MIN_CHUNK_SECONDS = 60
_CONTEXT_CHARACTERS = 500


class AudioChunker(Protocol):
    """Split one local audio file into API-safe temporary files."""

    def split(
        self,
        audio_path: Path,
        output_directory: Path,
        *,
        duration_seconds: int,
        max_chunk_bytes: int,
    ) -> tuple[Path, ...]:
        """Return ordered chunk paths inside the supplied private directory."""
        ...


@dataclass(frozen=True, slots=True)
class FfmpegAudioChunker:
    """Use the bundled ffmpeg executable to split audio without re-encoding."""

    target_chunk_bytes: int = _TARGET_CHUNK_BYTES

    def __post_init__(self) -> None:
        if self.target_chunk_bytes <= 0:
            raise ValueError("target audio chunk size must be positive")

    def split(
        self,
        audio_path: Path,
        output_directory: Path,
        *,
        duration_seconds: int,
        max_chunk_bytes: int,
    ) -> tuple[Path, ...]:
        if duration_seconds <= 0:
            raise ValueError("audio duration must be positive")
        if max_chunk_bytes <= 0:
            raise ValueError("maximum audio chunk size must be positive")
        source_bytes = audio_path.stat().st_size
        if source_bytes <= max_chunk_bytes:
            return (audio_path,)

        segment_seconds = max(
            _MIN_CHUNK_SECONDS,
            min(
                _MAX_CHUNK_SECONDS,
                duration_seconds * min(self.target_chunk_bytes, max_chunk_bytes) // source_bytes,
            ),
        )
        suffix = audio_path.suffix.lower()
        output_pattern = output_directory / f"chunk-%03d{suffix}"
        command = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-map",
            "0:a:0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-reset_timestamps",
            "1",
            str(output_pattern),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as error:
            raise AudioTranscriptionProviderError("audio chunking failed") from error

        chunks = tuple(sorted(output_directory.glob(f"chunk-*{suffix}")))
        if not chunks:
            raise AudioTranscriptionProviderError("audio chunking produced no files")
        if any(chunk.stat().st_size <= 0 for chunk in chunks):
            raise AudioTranscriptionProviderError("audio chunking produced an empty file")
        if any(chunk.stat().st_size > max_chunk_bytes for chunk in chunks):
            raise AudioTranscriptionProviderError(
                f"audio chunk exceeds the {max_chunk_bytes}-byte API upload limit"
            )
        return chunks


class OpenAIAudioTranscriber:
    """Transcribe bounded chunks sequentially through OpenAI's audio endpoint."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: OpenAI | None = None,
        chunker: AudioChunker | None = None,
    ) -> None:
        self._model = settings.openai_transcription_model
        self._client = client or OpenAI(
            api_key=settings.require_openai_api_key(),
            timeout=settings.openai_transcription_timeout_seconds,
        )
        self._chunker = chunker or FfmpegAudioChunker()

    def transcribe(
        self,
        audio_path: Path,
        *,
        media_type: str,
        duration_seconds: int,
    ) -> ProviderTranscript:
        """Split if needed, transcribe in order, and merge only in memory."""

        del media_type
        with TemporaryDirectory(prefix="podcast-intelligence-chunks-") as chunk_directory:
            chunks = self._chunker.split(
                audio_path,
                Path(chunk_directory),
                duration_seconds=duration_seconds,
                max_chunk_bytes=_API_UPLOAD_LIMIT_BYTES,
            )
            texts: list[str] = []
            request_ids: list[str] = []
            languages: set[str] = set()
            parts: list[ProviderTranscriptPart] = []
            for ordinal, chunk in enumerate(chunks):
                response = self._transcribe_chunk(
                    chunk, context="\n\n".join(texts)[-_CONTEXT_CHARACTERS:]
                )
                text = response.text.strip()
                if not text:
                    raise AudioTranscriptionProviderError(
                        "OpenAI transcription returned an empty chunk"
                    )
                texts.append(text)
                request_id = getattr(response, "_request_id", None)
                if isinstance(request_id, str) and request_id:
                    request_ids.append(request_id)
                response_languages = (
                    {language.code for language in response.languages}
                    if response.languages is not None
                    else set()
                )
                languages.update(response_languages)
                parts.append(
                    ProviderTranscriptPart(
                        ordinal=ordinal,
                        text=text,
                        request_id=request_id
                        if isinstance(request_id, str) and request_id
                        else None,
                        model=self._model,
                        language=(
                            next(iter(response_languages)) if len(response_languages) == 1 else None
                        ),
                        usage=_normalize_usage(response),
                    )
                )

        merged_text = "\n\n".join(text for text in texts if text)
        if not merged_text:
            raise AudioTranscriptionProviderError("OpenAI transcription returned no text")
        return ProviderTranscript(
            text=merged_text,
            provider="openai",
            model=self._model,
            request_ids=tuple(request_ids),
            language=next(iter(languages)) if len(languages) == 1 else None,
            chunk_count=len(chunks),
            parts=tuple(parts),
        )

    def _transcribe_chunk(self, chunk: Path, *, context: str) -> Transcription:
        try:
            with chunk.open("rb") as audio_file:
                if context:
                    response = self._client.audio.transcriptions.create(
                        file=audio_file,
                        model=self._model,
                        prompt=context,
                        response_format="json",
                    )
                else:
                    response = self._client.audio.transcriptions.create(
                        file=audio_file,
                        model=self._model,
                        response_format="json",
                    )
        except (OSError, OpenAIError) as error:
            raise AudioTranscriptionProviderError("OpenAI transcription request failed") from error
        if not isinstance(response, Transcription):
            raise AudioTranscriptionProviderError(
                "OpenAI transcription returned an unexpected response type"
            )
        return response


def _normalize_usage(response: Transcription) -> TranscriptionUsage | None:
    usage = response.usage
    if isinstance(usage, UsageTokens):
        details = usage.input_token_details
        return TranscriptionUsage(
            usage_type="tokens",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            input_token_details_json=(
                json.dumps(details.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
                if details is not None
                else None
            ),
        )
    if isinstance(usage, UsageDuration):
        return TranscriptionUsage(usage_type="duration", audio_seconds=usage.seconds)
    return None


def estimate_openai_transcription_cost(
    duration_seconds: int,
    *,
    price_per_minute_usd: Decimal,
) -> Decimal:
    """Estimate standard transcription cost and round upward to one ten-thousandth."""

    if duration_seconds <= 0:
        raise ValueError("transcription duration must be positive")
    if not price_per_minute_usd.is_finite() or price_per_minute_usd <= Decimal(0):
        raise ValueError("transcription price must be finite and positive")
    cost = Decimal(duration_seconds) * price_per_minute_usd / Decimal(60)
    return cost.quantize(Decimal("0.0001"), rounding=ROUND_CEILING)
