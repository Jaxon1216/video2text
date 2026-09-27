from __future__ import annotations

from abc import ABC, abstractmethod

from v2t.config import Settings
from v2t.models import DownloadResult, SourceRef, SubtitleResult
from v2t.progress import ProgressReporter


class Downloader(ABC):
    name = "downloader"

    @abstractmethod
    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: ProgressReporter | None = None,
    ) -> DownloadResult:
        raise NotImplementedError

    def fetch_subtitles(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: ProgressReporter | None = None,
    ) -> SubtitleResult | None:
        """Return platform subtitles when the platform offers them; `None` means fall back to ASR."""
        return None
