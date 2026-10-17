from __future__ import annotations

import math
from typing import Any

from requests import RequestException, Session

from v2t.config import Settings
from v2t.downloaders.base import Downloader
from v2t.downloaders.ytdlp import _import_youtube_dl, add_download_progress, build_ydl_options, resolve_media_path
from v2t.models import DownloadResult, SourceRef, SubtitleResult
from v2t.progress import ProgressReporter, TaskCancelled
from v2t.segments import normalize_segments


class _TimeoutSession(Session):
    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", 15)
        return super().request(method, url, **kwargs)


class YouTubeDownloader(Downloader):
    name = "youtube"

    def fetch_subtitles(
        self, source: SourceRef, settings: Settings, *, progress: ProgressReporter | None = None,
    ) -> SubtitleResult | None:
        if source.kind != "youtube":
            return None
        if progress is not None:
            progress.running("downloading", message="checking_subtitles", indeterminate=True)
        API = _import_transcript_api()
        # A session per request avoids sharing requests.Session across task threads.
        with _TimeoutSession() as session:
            tracks = sorted(API(http_client=session).list(source.video_id), key=_subtitle_priority)
            for track in tracks:
                if progress is not None:
                    progress.check_cancelled()
                snippets = track.fetch()
                if progress is not None:
                    progress.check_cancelled()
                segments = _subtitle_segments(snippets)
                if not segments:
                    continue
                metadata = _subtitle_metadata(session, source)
                metadata["is_generated"] = bool(track.is_generated)
                metadata["subtitle_language"] = track.language_code
                return SubtitleResult(
                    source=source, language=track.language_code, segments=segments,
                    title=metadata["title"], webpage_url=source.url, metadata=metadata,
                )
        return None

    def download(
        self, source: SourceRef, settings: Settings, *, progress: ProgressReporter | None = None,
    ) -> DownloadResult:
        if source.kind != "youtube":
            raise ValueError("YouTube downloader only supports youtube sources")
        settings.ensure_directories()
        if progress is not None:
            progress.running("downloading", message="downloading", indeterminate=True)
        from yt_dlp.utils import DownloadError

        options = {
            **build_ydl_options(settings),
            "format": "bestaudio/best",
            "outtmpl": str(settings.downloads_dir / "youtube-%(id)s.%(ext)s"),
            "socket_timeout": 15,
            "retries": 2,
            "extractor_retries": 2,
        }
        add_download_progress(options, progress)
        try:
            with _import_youtube_dl()(options) as ydl:
                info = ydl.extract_info(source.url, download=False)
                if not info or "entries" in info:
                    raise RuntimeError("YouTube MVP 仅支持单视频，暂不支持播放列表。")
                if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
                    raise RuntimeError("暂不支持正在直播、预约或尚未处理完成的 YouTube 直播，请在录播可用后重试。")
                if progress is not None:
                    progress.check_cancelled()
                info = ydl.extract_info(source.url, download=True)
                if progress is not None:
                    progress.check_cancelled()
                if not info or "entries" in info:
                    raise RuntimeError("YouTube 未返回单视频下载结果。")
                info = ydl.sanitize_info(info)
                media_path = resolve_media_path(ydl, info)
                if not media_path.is_file():
                    raise RuntimeError(f"yt-dlp reported success but no file was found at {media_path}")
        except TaskCancelled:
            raise
        except DownloadError as exc:
            raise RuntimeError(
                "YouTube 音频下载失败。请检查网络/系统代理并更新 yt-dlp；若需要登录或遇到风控，"
                "可配置 V2T_COOKIE_FILE / V2T_COOKIES_FROM_BROWSER；运行 `uv sync` 安装 Deno/EJS，受限视频也可能无法下载。"
                f"原始错误：{exc}"
            ) from exc
        return DownloadResult(
            source=source, video_path=media_path, title=info.get("title") or source.display_name,
            webpage_url=source.url,
            metadata={"platform": "youtube", "id": source.video_id, "title": info.get("title") or source.display_name,
                      "uploader": info.get("uploader") or info.get("channel"), "duration": info.get("duration"),
                      "webpage_url": source.url},
        )


def _subtitle_priority(track: Any) -> tuple[int, int, str]:
    code = track.language_code
    base = code.lower().split("-")[0]
    if base == "zh":
        preferred = ("zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "zh-HK")
        return (2 if track.is_generated else 0, preferred.index(code) if code in preferred else len(preferred), code)
    if base == "en":
        return (3 if track.is_generated else 1, 0 if code == "en" else 1, code)
    return (4, int(track.is_generated), code)


def _subtitle_segments(snippets: Any) -> list[dict[str, Any]]:
    items = []
    for snippet in snippets:
        start, duration = snippet.start, snippet.duration
        if not isinstance(start, (int, float)) or not isinstance(duration, (int, float)):
            continue
        if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration < 0:
            continue
        items.append({"start": start, "end": start + duration, "text": snippet.text})
    return normalize_segments(items)  # type: ignore[return-value]


def _subtitle_metadata(session: Session, source: SourceRef) -> dict[str, Any]:
    metadata = {"platform": "youtube", "id": source.video_id, "title": source.display_name,
                "uploader": None, "duration": None, "webpage_url": source.url}
    # oEmbed provides a title without extracting streams; failure must not discard subtitles.
    try:
        response = session.get("https://www.youtube.com/oembed", params={"url": source.url, "format": "json"}, timeout=5)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            metadata["title"] = payload.get("title") or source.display_name
            metadata["uploader"] = payload.get("author_name")
    except (RequestException, ValueError):
        pass
    return metadata


def _import_transcript_api() -> Any:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as exc:
        raise RuntimeError("YouTube 字幕依赖未安装，请运行 `uv sync`。") from exc
    return YouTubeTranscriptApi
