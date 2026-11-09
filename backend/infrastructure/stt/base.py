"""Speech-to-text interface."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class STTError(RuntimeError):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    lang: str | None = None
    provider: str = ""


class STTProvider(Protocol):
    name: str

    def transcribe(self, audio_path: str) -> Transcript:
        ...


def build_stt(settings):
    from .faster_whisper_provider import FasterWhisperSTT
    from .noop import NoopSTT

    if (settings.stt_provider or "noop").lower() == "faster_whisper":
        return FasterWhisperSTT(model_size=settings.whisper_model, settings=settings)
    return NoopSTT()
