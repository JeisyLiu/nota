"""Incremental transcription used for the live text preview while recording.

This is deliberately separate from :mod:`faster_whisper_provider`:

* the provider transcribes a finished file once, with the configured (accurate,
  slower) model, and is the authoritative transcript;
* this one runs a smaller model over the audio received so far, over and over,
  and only feeds the "what am I saying right now" hint in the UI.

Measured on this machine (int8 CPU): tiny 0.46s per pass, base 0.9s, small 2.8s.
The base model therefore keeps up with speech (a 3s window transcribes in about
a third of its own duration) while small would fall further behind with every
update. That trade-off is why partial and final use different models.

Whisper only emits traditional Chinese for short clips unless it is primed, so a
simplified-Chinese prompt is prepended -- verified to change the output without
adding measurable latency.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
_SIMPLIFIED_PROMPT = "以下是普通话的句子，请使用简体中文。"

# Model instances are expensive (hundreds of MB) and reusing one across
# connections keeps the first partial from paying the load cost again.
_models: dict[tuple[str, str], object] = {}
_models_lock = threading.Lock()

# ctranslate2 models are not safe to call concurrently from several threads.
_transcribe_lock = threading.Lock()


class StreamingUnavailable(Exception):
    """Raised when the streaming model cannot be loaded."""


class StreamingTranscriber:
    def __init__(self, *, model_size: str = "base", local_pylibs: str | None = None,
                 download_root: str | None = None):
        self.model_size = model_size
        self._local_pylibs = local_pylibs or ""
        self._download_root = download_root

    def _load(self):
        key = (self.model_size, self._download_root or "")
        with _models_lock:
            cached = _models.get(key)
            if cached is not None:
                return cached

            if self._local_pylibs and Path(self._local_pylibs).is_dir():
                if self._local_pylibs not in sys.path:
                    sys.path.insert(0, self._local_pylibs)

            try:
                from faster_whisper import WhisperModel  # type: ignore
            except Exception as exc:  # pragma: no cover - optional dependency
                raise StreamingUnavailable(
                    "faster-whisper 未安装，实时转写不可用"
                ) from exc

            try:
                model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                    download_root=self._download_root,
                )
            except Exception as exc:  # pragma: no cover
                raise StreamingUnavailable(f"无法加载实时转写模型：{exc}") from exc

            _models[key] = model
            return model

    def transcribe(self, samples: np.ndarray) -> str:
        """Transcribe the audio received so far. Returns "" when nothing was heard."""
        if samples.size == 0:
            return ""
        model = self._load()
        with _transcribe_lock:
            segments, _ = model.transcribe(
                samples,
                language="zh",
                beam_size=1,
                vad_filter=False,
                initial_prompt=_SIMPLIFIED_PROMPT,
                condition_on_previous_text=False,
            )
            return "".join(segment.text for segment in segments).strip()


def pcm16_to_float(samples: bytes) -> np.ndarray:
    """Raw little-endian 16-bit mono PCM -> float32 in [-1, 1]."""
    if not samples:
        return np.zeros(0, dtype=np.float32)
    # Trailing odd byte would make frombuffer raise; drop it instead of failing.
    usable = len(samples) - (len(samples) % 2)
    pcm = np.frombuffer(samples[:usable], dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def preload(*, model_size: str, local_pylibs: str | None = None,
            download_root: str | None = None) -> None:
    """Load the preview model ahead of the first recording.

    Measured cold start here is about 20 s. Paying that on the user's first press
    looks like a broken feature, so it is paid at process start instead, on a
    background thread.
    """
    StreamingTranscriber(
        model_size=model_size,
        local_pylibs=local_pylibs,
        download_root=download_root,
    )._load()
