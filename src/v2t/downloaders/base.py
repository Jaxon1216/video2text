from __future__ import annotations

from abc import ABC, abstractmethod

from v2t.config import Settings
from v2t.models import DownloadResult, SourceRef
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
