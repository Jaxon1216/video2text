from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from v2t.formatters import join_text
from v2t.i18n import dependency_sync_guidance
from v2t.progress import ProgressReporter
from v2t.segments import Segment
from v2t.transcribers.base import Transcriber

# Whisper tends to answer Mandarin in Traditional characters without punctuation; a Simplified,
# punctuated prompt steers both. User prompts (e.g. technical terms) are appended after it.
CHINESE_STYLE_PROMPT = "以下是普通话的句子，使用简体中文，并带有标点符号。"


class FasterWhisperTranscriber(Transcriber):
    name = "faster-whisper"

    def __init__(
        self,
        *,
        model: str = "small",
        language: str = "zh",
        device: str = "auto",
        compute_type: str = "auto",
        beam_size: int = 5,
        vad_filter: bool = True,
        download_root: Path | None = None,
    ) -> None:
        self.model_name = model
        self.language = None if language in ("", "auto") else language
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.download_root = download_root
        self._model: Any | None = None
        self._lock = threading.Lock()

    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        if progress is not None:
            progress.running("transcribing", message="loading_model", indeterminate=True)
        with self._lock:
            model = self._ensure_model()
            if progress is not None:
                progress.running("transcribing", message="transcribing", stage_progress=0.0)
            segment_iter, info = model.transcribe(
                str(audio_path),
                language=self.language,
                initial_prompt=build_initial_prompt(prompt, self.language),
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
            )
            duration = float(getattr(info, "duration", 0) or 0)
            segments: list[Segment] = []
            text = ""
            for item in segment_iter:
                piece = (item.text or "").strip()
                if not piece:
                    continue
                segments.append({"start": round(float(item.start), 3), "end": round(float(item.end), 3), "text": piece})
                text = join_text(text, piece)
                if progress is not None and duration > 0:
                    progress.running(
                        "transcribing",
                        message="transcribing",
                        stage_progress=min(1.0, float(item.end) / duration),
                    )
        return {
            "text": text,
            "segments": segments,
            "language": getattr(info, "language", None) or self.language,
            "model": self.model_name,
            "device": self.device,
        }

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper support is not installed. "
                f"{dependency_sync_guidance('en-US')}"
            ) from exc

        device, compute_type = resolve_device(self.device, self.compute_type)
        self.device = device
        try:
            self._model = WhisperModel(
                self.model_name,
                device=device,
                compute_type=compute_type,
                download_root=str(self.download_root) if self.download_root else None,
            )
        except Exception as exc:
            hint = "" if os.getenv("HF_ENDPOINT") else "国内网络可设置环境变量 HF_ENDPOINT=https://hf-mirror.com 后重试。"
            raise RuntimeError(f"加载 faster-whisper 模型 {self.model_name} 失败（首次使用需要下载模型）：{exc}。{hint}") from exc
        return self._model


def build_initial_prompt(user_prompt: str | None, language: str | None) -> str | None:
    parts = []
    if language == "zh":
        parts.append(CHINESE_STYLE_PROMPT)
    if user_prompt and user_prompt.strip():
        parts.append(user_prompt.strip())
    return "".join(parts) or None


def resolve_device(device: str, compute_type: str) -> tuple[str, str]:
    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:  # noqa: BLE001 - fall back to CPU when CUDA probing fails
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type
