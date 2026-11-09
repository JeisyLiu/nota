"""faster-whisper (CTranslate2) speech-to-text, CPU friendly.

Model weights are NOT in the repository. The first call downloads the chosen
size into ``HF_HOME`` (defaults to ``<repo>/data/models``), or reuses whatever is
already cached. If the dependency or the model is missing we raise STTError and
the pipeline degrades to "please type the text", it never crashes the job.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .base import STTError, Transcript


class FasterWhisperSTT:
    name = "faster_whisper"

    _model_cache: dict[str, object] = {}

    def __init__(self, *, model_size: str = "small", settings=None, local_pylibs: str | None = None):
        self.model_size = model_size
        self._settings = settings
        self._local_pylibs = local_pylibs or os.environ.get("NOTA_PYLIB_DIR", "")

    # ------------------------------------------------------------------ model
    def _load(self):
        key = f"{self.model_size}"
        if key in self._model_cache:
            return self._model_cache[key]

        if self._local_pylibs and Path(self._local_pylibs).is_dir():
            if self._local_pylibs not in sys.path:
                sys.path.insert(0, self._local_pylibs)

        try:
            from faster_whisper import WhisperModel  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise STTError(
                "faster-whisper is not installed; run `pip install faster-whisper` "
                "or set STT_PROVIDER=noop and type the text"
            ) from exc

        download_root = None
        if self._settings is not None:
            download_root = str(Path(self._settings.data_dir) / "models")
            Path(download_root).mkdir(parents=True, exist_ok=True)

        try:
            model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
                download_root=download_root,
            )
        except Exception as exc:  # pragma: no cover
            raise STTError(f"failed to load whisper model '{self.model_size}': {exc}") from exc

        self._model_cache[key] = model
        return model

    # ------------------------------------------------------------- transcribe
    def transcribe(self, audio_path: str) -> Transcript:
        path = Path(audio_path)
        if not path.is_file():
            raise STTError(f"audio file not found: {audio_path}")

        model = self._load()
        target, cleanup = self._ensure_wav(path)
        try:
            segments, info = model.transcribe(
                str(target), language=None, vad_filter=True, beam_size=1
            )
            text = "".join(seg.text for seg in segments).strip()
        except Exception as exc:  # pragma: no cover
            raise STTError(f"transcription failed: {exc}") from exc
        finally:
            if cleanup:
                try:
                    os.unlink(target)
                except OSError:
                    pass

        if not text:
            raise STTError("no speech detected in the audio")
        return Transcript(text=text, lang=getattr(info, "language", None), provider=self.name)

    @staticmethod
    def _ensure_wav(path: Path) -> tuple[str, bool]:
        """Convert to 16k mono wav when ffmpeg is available and the input is not wav."""
        if path.suffix.lower() == ".wav":
            return str(path), False
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return str(path), False
        fd, tmp = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
             "-ar", "16000", "-ac", "1", tmp],
            capture_output=True,
        )
        if proc.returncode != 0:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return str(path), False
        return tmp, True
