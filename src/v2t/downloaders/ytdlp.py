from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable

from v2t.config import Settings
from v2t.downloaders.base import Downloader
from v2t.inputs import is_bilibili_short_url, parse_source
from v2t.models import DownloadResult, SourceRef, SubtitleResult
from v2t.segments import Segment, parse_bilibili_json, parse_srt

# Human-made subtitles first, then Bilibili's AI subtitles; danmaku (bullet comments) is never a transcript.
SUBTITLE_LANGUAGE_PRIORITY = ("zh-CN", "zh-Hans", "zh", "zh-Hant", "zh-TW", "zh-HK", "ai-zh", "en-US", "en", "ai-en")
IGNORED_SUBTITLE_KEYS = frozenset({"danmaku", "live_chat"})
SHORT_LINK_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


class YtDlpDownloader(Downloader):
    name = "yt-dlp"

    def __init__(self, *, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self._opener = opener

    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress=None,
    ) -> DownloadResult:
        if source.kind != "bilibili":
            raise ValueError("yt-dlp downloader only supports bilibili sources")

        settings.ensure_directories()
        source = self.resolve_source(source)
        YoutubeDL = _import_youtube_dl()

        ydl_opts = self._build_ydl_opts(source, settings)
        if progress is not None:
            def progress_hook(data: dict[str, Any]) -> None:
                status = data.get("status")
                if status == "downloading":
                    total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                    downloaded = data.get("downloaded_bytes") or 0
                    stage_progress = (downloaded / total) if total else None
                    progress.running(
                        "downloading",
                        message="downloading",
                        stage_progress=stage_progress,
                        indeterminate=stage_progress is None,
                    )
                elif status == "finished":
                    progress.running("downloading", message="download_finished", stage_progress=1.0)
            ydl_opts["progress_hooks"] = [progress_hook]
            ydl_opts["noprogress"] = False

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(_source_url(source), download=True)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]
            info = ydl.sanitize_info(info)

            video_path = self._resolve_video_path(ydl, info)
            if not video_path.exists():
                raise RuntimeError(f"yt-dlp reported success but no file was found at {video_path}")

        return DownloadResult(
            source=source,
            video_path=video_path,
            title=info.get("title"),
            webpage_url=info.get("webpage_url") or source.url,
            metadata=_info_metadata(info, source),
        )

    def fetch_subtitles(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress=None,
    ) -> SubtitleResult | None:
        if source.kind != "bilibili":
            return None
        settings.ensure_directories()
        source = self.resolve_source(source)
        YoutubeDL = _import_youtube_dl()
        if progress is not None:
            progress.running("downloading", message="checking_subtitles", indeterminate=True)

        ydl_opts = {**self._build_ydl_opts(source, settings), "skip_download": True, "writesubtitles": True}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(_source_url(source), download=False)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]
            picked = pick_subtitle(info.get("subtitles") or {})
            if picked is None:
                return None
            language, entry = picked
            content = entry.get("data")
            if not content and entry.get("url"):
                content = ydl.urlopen(entry["url"]).read().decode("utf-8", errors="replace")

        segments = parse_subtitle_content(content or "", entry.get("ext"))
        if not segments:
            return None
        return SubtitleResult(
            source=source,
            language=language,
            segments=segments,  # type: ignore[arg-type]
            title=info.get("title"),
            webpage_url=info.get("webpage_url") or source.url,
            metadata=_info_metadata(info, source),
        )

    def resolve_source(self, source: SourceRef) -> SourceRef:
        """Expand b23.tv short links into a regular BV source so page numbers and ids work."""
        if source.bv or not is_bilibili_short_url(source.url):
            return source
        request = urllib.request.Request(source.url or "", headers={"User-Agent": SHORT_LINK_USER_AGENT})
        with self._opener(request, timeout=15) as response:
            final_url = response.geturl()
        resolved = parse_source(final_url)
        if resolved.kind != "bilibili" or not resolved.bv:
            raise RuntimeError(f"短链没有跳转到 B站视频页：{final_url}")
        resolved.raw_input = source.raw_input
        return resolved

    def _build_ydl_opts(self, source: SourceRef, settings: Settings) -> dict[str, Any]:
        ydl_opts: dict[str, Any] = {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "outtmpl": str(settings.downloads_dir / "%(id)s.%(ext)s"),
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
        }

        # Cookies unlock subtitles and avoid some 412s.
        # Priority: V2T_COOKIE_FILE > cookies.txt in workspace > V2T_COOKIES_FROM_BROWSER.
        cookie_file = os.getenv("V2T_COOKIE_FILE")
        if cookie_file:
            cookie_path = Path(cookie_file).expanduser()
        else:
            cookie_path = settings.workspace_root / "cookies.txt"
        if cookie_path.exists():
            ydl_opts["cookiefile"] = str(cookie_path)
        else:
            browser = os.getenv("V2T_COOKIES_FROM_BROWSER", "").strip()
            if browser:
                ydl_opts["cookiesfrombrowser"] = (browser,)

        # Bilibili's CDN frequently blocks proxy/VPN nodes, causing 412
        # or SSL errors. Direct connections usually work better.
        # Set V2T_USE_PROXY=1 to re-enable the system proxy if needed.
        use_proxy = os.getenv("V2T_USE_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}
        if not use_proxy:
            ydl_opts["proxy"] = ""

        if source.page is not None:
            ydl_opts["playlist_items"] = str(source.page)
            ydl_opts["noplaylist"] = False
            ydl_opts["outtmpl"] = str(settings.downloads_dir / "%(id)s.%(playlist_index)02d.%(ext)s")
        return ydl_opts

    def _resolve_video_path(self, ydl: Any, info: dict[str, Any]) -> Path:
        requested_downloads = info.get("requested_downloads") or []
        for requested in requested_downloads:
            filepath = requested.get("filepath")
            if filepath:
                return Path(filepath)

        prepared = Path(ydl.prepare_filename(info))
        if prepared.exists():
            return prepared

        merged_mp4 = prepared.with_suffix(".mp4")
        if merged_mp4.exists():
            return merged_mp4

        return prepared


def pick_subtitle(subtitles: dict[str, list[dict[str, Any]]]) -> tuple[str, dict[str, Any]] | None:
    candidates = {lang: entries for lang, entries in subtitles.items() if lang not in IGNORED_SUBTITLE_KEYS and entries}
    if not candidates:
        return None
    ordered = [lang for lang in SUBTITLE_LANGUAGE_PRIORITY if lang in candidates]
    ordered += sorted(lang for lang in candidates if lang not in ordered)
    language = ordered[0]
    entries = candidates[language]
    for preferred_ext in ("srt", "json", "vtt"):
        for entry in entries:
            if entry.get("ext") == preferred_ext:
                return language, entry
    return language, entries[0]


def parse_subtitle_content(content: str, ext: str | None) -> list[Segment]:
    text = content.strip()
    if not text:
        return []
    if ext == "json" or text.startswith("{"):
        try:
            return parse_bilibili_json(json.loads(text))
        except (json.JSONDecodeError, AttributeError):
            return []
    return parse_srt(text)


def _import_youtube_dl() -> Any:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed. Run `uv sync` to install the core dependencies.") from exc
    return YoutubeDL


def _source_url(source: SourceRef) -> str:
    return source.url or f"https://www.bilibili.com/video/{source.bv}"


def _info_metadata(info: dict[str, Any], source: SourceRef) -> dict[str, Any]:
    return {
        "platform": "bilibili",
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "id": info.get("id"),
        "webpage_url": info.get("webpage_url") or source.url,
    }
