from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

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

    def existing_media_stems(self, source: SourceRef) -> tuple[str, ...]:
        """Names produced by normal downloads for this source (including its part)."""
        return (f"{source.kind}-{source.video_id}",) if source.video_id else ()

    def download_video(
        self, source: SourceRef, settings: Settings, output_dir: Path,
        *, progress: ProgressReporter | None = None,
    ) -> DownloadResult:
        """Fetch a stream with pictures into a separate staging directory for sampling."""
        raise RuntimeError("这个来源暂不支持补取视频画面")
