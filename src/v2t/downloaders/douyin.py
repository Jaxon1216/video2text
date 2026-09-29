from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from v2t.config import Settings
from v2t.downloaders.base import Downloader
from v2t.models import DownloadResult, SourceRef
from v2t.progress import ProgressReporter

DETAIL_API_PATH = "/aweme/v1/web/aweme/detail/"
VIDEO_PAGE_URL = "https://www.douyin.com/video/{video_id}"
REFERER = "https://www.douyin.com/"
_UA_PLATFORMS = {
    "darwin": "Macintosh; Intel Mac OS X 10_15_7",
    "win32": "Windows NT 10.0; Win64; x64",
}


def default_user_agent(platform: str = sys.platform) -> str:
    """The page signs API calls with a browser fingerprint, so the UA must match the real OS.

    A macOS UA inside a Linux container makes Douyin answer the detail API with an empty body.
    """
    system = _UA_PLATFORMS.get(platform, "X11; Linux x86_64")
    return f"Mozilla/5.0 ({system}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


USER_AGENT = default_user_agent()
DEFAULT_TIMEOUT_SECONDS = 30.0
_CHUNK_SIZE = 1 << 16

# A persistent Chrome profile can only be opened by one process at a time.
_BROWSER_LOCK = threading.Lock()

DetailFetcher = Callable[..., dict[str, Any]]


class DouyinError(RuntimeError):
    pass


def fetch_aweme_detail(
    url: str,
    *,
    profile_dir: Path,
    headless: bool = True,
    channel: str = "auto",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Open the video page in a real browser and capture the page's own detail API response.

    Douyin fronts both share pages and the web API with a JS challenge plus request signatures,
    so we let the browser pass the challenge and sign the request, and only read the JSON it gets back.
    """
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DouyinError(
            "抖音解析需要 Playwright：运行 `uv sync --extra douyin`，"
            "如果本机没有 Chrome 再运行 `uv run playwright install chromium`。"
        ) from exc

    profile_dir.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(timeout_seconds * 1000)
    with _BROWSER_LOCK, sync_playwright() as playwright:
        context = _launch_context(playwright, profile_dir=profile_dir, headless=headless, channel=channel)
        try:
            page = context.new_page()
            try:
                with page.expect_response(lambda response: DETAIL_API_PATH in response.url, timeout=timeout_ms) as info:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                response = info.value
                if response.status != 200:
                    raise DouyinError(f"抖音详情接口返回 HTTP {response.status}，可能触发了风控，请稍后重试")
                payload = _parse_json_body(response.body())
            except PlaywrightTimeoutError as exc:
                raise DouyinError(
                    f"打开抖音页面超时（{timeout_seconds:.0f}s），没有等到视频详情数据。"
                    f"最后停留的页面：{page.url}。可能是人机校验未通过，可设置 V2T_DOUYIN_HEADLESS=0 用有头模式重试"
                ) from exc
            except PlaywrightError as exc:
                raise DouyinError(f"浏览器加载抖音页面失败：{exc}") from exc
        finally:
            context.close()
    if not isinstance(payload, dict):
        raise DouyinError("抖音详情接口返回了无法识别的数据")
    return payload


def _parse_json_body(body: bytes) -> Any:
    if not body.strip():
        raise DouyinError("抖音详情接口返回了空数据，通常是浏览器指纹被拒（例如 UA 与实际系统不一致）或触发了风控")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise DouyinError(f"抖音详情接口返回的不是 JSON：{body[:80]!r}") from exc


def _launch_context(playwright: Any, *, profile_dir: Path, headless: bool, channel: str) -> Any:
    options: dict[str, Any] = {
        "headless": headless,
        "user_agent": USER_AGENT,
        "locale": "zh-CN",
        "viewport": {"width": 1280, "height": 800},
    }
    channels = ["chrome", None] if channel == "auto" else [channel or None]
    last_error: Exception | None = None
    for candidate in channels:
        try:
            return playwright.chromium.launch_persistent_context(
                str(profile_dir),
                channel=candidate,
                **options,
            )
        except Exception as exc:  # noqa: BLE001 - fall through to the bundled Chromium
            last_error = exc
    raise DouyinError(
        "无法启动浏览器：本机没有找到 Chrome，也没有安装 Playwright 自带的 Chromium。"
        "运行 `uv run playwright install chromium` 后重试。"
        f"（{last_error}）"
    )


class DouyinDownloader(Downloader):
    name = "douyin"

    def __init__(
        self,
        *,
        fetch_detail: DetailFetcher = fetch_aweme_detail,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self._fetch_detail = fetch_detail
        self._opener = opener

    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress: ProgressReporter | None = None,
    ) -> DownloadResult:
        if source.kind != "douyin":
            raise ValueError("douyin downloader only supports douyin sources")
        settings.ensure_directories()

        page_url = VIDEO_PAGE_URL.format(video_id=source.video_id) if source.video_id else source.url
        if not page_url:
            raise DouyinError("没有可用的抖音链接")

        if progress is not None:
            progress.running("downloading", message="resolving", indeterminate=True)
        payload = self._fetch_detail(
            page_url,
            profile_dir=settings.workspace_root / "browser",
            headless=_env_flag("V2T_DOUYIN_HEADLESS", default=True),
            channel=os.getenv("V2T_DOUYIN_BROWSER", "auto").strip() or "auto",
        )
        detail = extract_detail(payload)
        video_id = str(detail.get("aweme_id") or source.video_id or "")
        audio_urls = select_audio_urls(detail)

        target = settings.downloads_dir / f"douyin-{video_id or 'unknown'}.m4a"
        self._download_first_available(audio_urls, target, progress=progress)

        metadata = build_metadata(detail, fallback_url=page_url)
        return DownloadResult(
            source=source,
            video_path=target,
            title=metadata["title"],
            webpage_url=metadata["webpage_url"],
            metadata=metadata,
        )

    def _download_first_available(
        self,
        urls: list[str],
        target: Path,
        *,
        progress: ProgressReporter | None,
    ) -> None:
        errors: list[str] = []
        for url in urls:
            try:
                self._stream_to_file(url, target, progress=progress)
                return
            except (urllib.error.URLError, OSError) as exc:
                errors.append(str(exc))
                target.unlink(missing_ok=True)
        raise DouyinError(f"抖音音频下载失败（尝试了 {len(urls)} 个地址）：{'; '.join(errors[-2:])}")

    def _stream_to_file(self, url: str, target: Path, *, progress: ProgressReporter | None) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": REFERER})
        tmp_path = target.with_suffix(target.suffix + ".part")
        try:
            with self._opener(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response, tmp_path.open("wb") as handle:
                total = int(response.headers.get("Content-Length") or 0)
                downloaded = 0
                while True:
                    if progress is not None:
                        progress.check_cancelled()
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        stage_progress = downloaded / total if total else None
                        progress.running(
                            "downloading",
                            message="downloading",
                            stage_progress=stage_progress,
                            indeterminate=stage_progress is None,
                        )
            if progress is not None:
                progress.check_cancelled()
            if tmp_path.stat().st_size == 0:
                raise OSError("empty response body")
            shutil.move(str(tmp_path), target)
        finally:
            tmp_path.unlink(missing_ok=True)
        if progress is not None:
            progress.running("downloading", message="download_finished", stage_progress=1.0)


def extract_detail(payload: dict[str, Any]) -> dict[str, Any]:
    detail = payload.get("aweme_detail")
    if isinstance(detail, dict) and detail:
        return detail
    filter_detail = payload.get("filter_detail")
    reason = None
    if isinstance(filter_detail, dict):
        reason = filter_detail.get("filter_reason") or filter_detail.get("detail_msg")
    if reason:
        raise DouyinError(f"抖音视频不可用（{reason}），可能已删除、私密或仅粉丝可见")
    status_msg = payload.get("status_msg")
    raise DouyinError(f"抖音没有返回视频详情{f'：{status_msg}' if status_msg else ''}，可能已删除或需要登录")


def select_audio_urls(detail: dict[str, Any]) -> list[str]:
    """Return candidate URLs, preferring the smallest audio-only DASH stream (plenty for ASR)."""
    if detail.get("images"):
        raise DouyinError("这是抖音图文作品，暂不支持转写")
    video = detail.get("video") or {}
    audio_streams = [item.get("audio_meta") or {} for item in (video.get("bit_rate_audio") or []) if isinstance(item, dict)]
    audio_streams.sort(key=lambda meta: meta.get("bitrate") or 0)

    urls: list[str] = []
    for meta in audio_streams:
        url_list = meta.get("url_list") or {}
        if isinstance(url_list, dict):
            urls.extend(url_list.get(key) for key in ("main_url", "backup_url", "fallback_url"))
        elif isinstance(url_list, list):
            urls.extend(url_list)

    # Older payloads may lack separate audio; the muxed mp4 still works because ffmpeg strips video.
    urls.extend((video.get("play_addr") or {}).get("url_list") or [])

    unique = list(dict.fromkeys(url for url in urls if isinstance(url, str) and url.startswith("http")))
    if unique:
        return unique
    raise DouyinError("抖音详情里没有找到可下载的音频或视频地址")


def build_metadata(detail: dict[str, Any], *, fallback_url: str) -> dict[str, Any]:
    video_id = str(detail.get("aweme_id") or "")
    desc = (detail.get("desc") or "").strip()
    duration_ms = (detail.get("video") or {}).get("duration") or detail.get("duration")
    return {
        "platform": "douyin",
        "id": video_id,
        "title": clean_title(desc) or f"douyin-{video_id}",
        "description": desc,
        "caption": (detail.get("caption") or "").strip(),
        "uploader": (detail.get("author") or {}).get("nickname"),
        "duration": round(duration_ms / 1000, 3) if duration_ms else None,
        "create_time": detail.get("create_time"),
        "webpage_url": VIDEO_PAGE_URL.format(video_id=video_id) if video_id else fallback_url,
    }


def clean_title(desc: str) -> str:
    """Drop hashtags and @mentions so the title stays readable in file names and lists."""
    first_line = desc.splitlines()[0] if desc else ""
    without_tags = re.sub(r"[#@]\S+", "", first_line)
    return re.sub(r"\s+", " ", without_tags).strip()


def _env_flag(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
