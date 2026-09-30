from __future__ import annotations

import gc
import shutil
import subprocess
import threading
import wave
from pathlib import Path
from typing import Any

from v2t.model_catalog import QWEN3_ASR_MODEL
from v2t.progress import ProgressReporter
from v2t.segments import normalize_segments
from v2t.transcribers.base import Transcriber


MAX_CHUNK_SECONDS = 60


class Qwen3ASRTranscriber(Transcriber):
    name = "qwen3-asr"

    def __init__(self, *, model: str = QWEN3_ASR_MODEL) -> None:
        if model != QWEN3_ASR_MODEL:
            raise ValueError("Qwen3-ASR currently supports only the 1.7B model")
        self.model_name = model
        self._model: Any | None = None
        self._device: str | None = None
        self._lock = threading.Lock()

    def transcribe(self, audio_path: Path, *, prompt: str | None = None,
                   progress: ProgressReporter | None = None) -> dict[str, Any]:
        if progress is not None:
            progress.running("transcribing", message="loading_model", indeterminate=True)
        with self._lock:
            samples = self._read_audio(audio_path)
            if progress is not None:
                progress.check_cancelled()
            model = self._ensure_model()
            if progress is not None:
                progress.running("transcribing", message="transcribing", indeterminate=True)
            chunk_size = MAX_CHUNK_SECONDS * samples[1]
            texts: list[str] = []
            language = "zh"
            for offset in range(0, len(samples[0]), chunk_size):
                if progress is not None:
                    progress.check_cancelled()
                options = {"audio": (samples[0][offset:offset + chunk_size], samples[1]),
                           "context": (prompt or "").strip(), "language": "Chinese",
                           "return_time_stamps": False}
                try:
                    results = model.transcribe(**options)
                except (RuntimeError, NotImplementedError) as exc:
                    if self._device != "mps" or not _mps_failure(exc):
                        raise
                    if progress is not None:
                        progress.check_cancelled()
                    self._model = None
                    del model
                    gc.collect()
                    self._empty_mps_cache()
                    model = self._load_model("cpu")
                    results = model.transcribe(**options)
                if progress is not None:
                    progress.check_cancelled()
                if not results:
                    raise RuntimeError("Qwen3-ASR returned no transcription")
                texts.append(str(results[0].text).strip())
                language = "zh" if results[0].language == "Chinese" else str(results[0].language)
                if progress is not None:
                    progress.running("transcribing", message="transcribing",
                                     stage_progress=min(1.0, (offset + chunk_size) / len(samples[0])))
            if progress is not None:
                progress.running("transcribing", message="transcribing", stage_progress=1.0)
        if not texts:
            raise RuntimeError("Qwen3-ASR returned no transcription")
        return {"text": "\n".join(text for text in texts if text), "segments": normalize_segments([]),
                "language": language, "model": self.model_name}

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("Qwen3-ASR support is not installed; run `uv sync --extra qwen3-asr`.") from exc
        mode = "mps" if torch.backends.mps.is_available() else "cpu"
        try:
            return self._load_model(mode)
        except (RuntimeError, NotImplementedError) as exc:
            if mode != "mps" or not _mps_failure(exc):
                raise
            gc.collect()
            self._empty_mps_cache()
            return self._load_model("cpu")

    def _load_model(self, mode: str) -> Any:
        try:
            import torch
            from qwen_asr import Qwen3ASRModel
        except ImportError as exc:
            raise RuntimeError("Qwen3-ASR support is not installed; run `uv sync --extra qwen3-asr`.") from exc
        dtype = torch.float16 if mode == "mps" else torch.float32
        self._model = Qwen3ASRModel.from_pretrained(
            self.model_name, device_map=mode, dtype=dtype, max_inference_batch_size=1,
            max_new_tokens=2048,
        )
        self._device = mode
        return self._model

    @staticmethod
    def _empty_mps_cache() -> None:
        import torch

        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    @staticmethod
    def _read_audio(audio_path: Path) -> tuple[Any, int]:
        import numpy as np

        try:
            with wave.open(str(audio_path), "rb") as wav:
                if (wav.getnchannels() == 1 and wav.getsampwidth() == 2
                        and wav.getframerate() == 16000 and wav.getcomptype() == "NONE"):
                    raw = wav.readframes(wav.getnframes())
                    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, 16000
        except (OSError, EOFError, wave.Error):
            pass
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to read this audio format")
        completed = subprocess.run([ffmpeg, "-v", "error", "-i", str(audio_path), "-vn", "-ac", "1",
                                    "-ar", "16000", "-f", "f32le", "pipe:1"], capture_output=True)
        if completed.returncode != 0:
            raise RuntimeError(f"ffmpeg failed to read audio: {completed.stderr.decode(errors='replace').strip()}")
        return np.frombuffer(completed.stdout, dtype=np.float32), 16000


def _mps_failure(exc: Exception) -> bool:
    if isinstance(exc, NotImplementedError):
        return True
    message = str(exc).lower()
    return any(term in message for term in ("mps", "metal", "not implemented", "out of memory"))
