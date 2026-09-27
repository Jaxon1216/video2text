from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from v2t.progress import ProgressReporter


class Transcriber(ABC):
    name = "transcriber"

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: str | None = None,
        progress: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError
