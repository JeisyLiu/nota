"""No-speech fallback: the product still works with typed text."""
from __future__ import annotations

from .base import STTError, Transcript


class NoopSTT:
    name = "noop"

    def transcribe(self, audio_path: str) -> Transcript:
        raise STTError("speech-to-text is disabled (STT_PROVIDER=noop); please type the text instead")
