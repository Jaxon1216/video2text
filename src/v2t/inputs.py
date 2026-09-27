from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from v2t.models import SourceRef


BV_PATTERN = re.compile(r"(BV[0-9A-Za-z]{10})")
# Share texts mix URLs with CJK punctuation, e.g. "复制打开抖音，看看【xx】 https://v.douyin.com/abc/ A@T.yG"
URL_PATTERN = re.compile(r"https?://[^\s\u3000-\u303f\uff00-\uffef\"'<>]+")
DOUYIN_HOST_SUFFIXES = ("douyin.com", "iesdouyin.com")
BILIBILI_SHORT_HOSTS = ("b23.tv", "bili2233.cn")
DOUYIN_ID_PATTERNS = (
    re.compile(r"/(?:share/)?(?:video|note|slides)/(\d{8,})"),
    re.compile(r"[?&](?:modal_id|aweme_id|vid)=(\d{8,})"),
)
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".flv", ".avi", ".webm"}


def parse_source(raw_input: str) -> SourceRef:
    value = raw_input.strip()
    if not value:
        raise ValueError("source cannot be empty")

    candidate_path = Path(value).expanduser()
    if candidate_path.exists():
        suffix = candidate_path.suffix.lower()
        if suffix in AUDIO_SUFFIXES:
            return SourceRef(
                raw_input=value,
                kind="audio",
                display_name=candidate_path.stem,
                path=candidate_path.resolve(),
            )
        if suffix in VIDEO_SUFFIXES:
            return SourceRef(
                raw_input=value,
                kind="video",
                display_name=candidate_path.stem,
                path=candidate_path.resolve(),
            )
        raise ValueError(f"unsupported local file type: {candidate_path.suffix}")

    extracted_url = extract_first_url(value)
    if extracted_url and _is_douyin_url(extracted_url):
        video_id = extract_douyin_video_id(extracted_url)
        return SourceRef(
            raw_input=value,
            kind="douyin",
            display_name=f"douyin-{video_id}" if video_id else "douyin-share",
            url=extracted_url,
            video_id=video_id,
        )

    match = BV_PATTERN.search(extracted_url or value)
    if extracted_url and not match and _host_matches(extracted_url, BILIBILI_SHORT_HOSTS):
        # b23.tv short links hide the BV id; the downloader resolves the redirect before fetching.
        return SourceRef(raw_input=value, kind="bilibili", display_name="bilibili-share", url=extracted_url)
    if match:
        bv = match.group(1)
        url = extracted_url or f"https://www.bilibili.com/video/{bv}"
        page = _extract_page_from_url(url)
        return SourceRef(
            raw_input=value,
            kind="bilibili",
            display_name=bv,
            url=url,
            bv=bv,
            page=page,
            video_id=bv,
        )

    raise ValueError(
        "source must be a BV id, a Bilibili URL, a Douyin share link, or an existing local audio/video file"
    )


def extract_first_url(text: str) -> str | None:
    match = URL_PATTERN.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;:!?)]")


def extract_douyin_video_id(url: str) -> str | None:
    for pattern in DOUYIN_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def _is_douyin_url(url: str) -> bool:
    return _host_matches(url, DOUYIN_HOST_SUFFIXES)


def is_bilibili_short_url(url: str | None) -> bool:
    return bool(url) and _host_matches(url, BILIBILI_SHORT_HOSTS)


def _host_matches(url: str, suffixes: tuple[str, ...]) -> bool:
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def parse_source_list(raw_input: str) -> list[str]:
    sources = [
        line.strip()
        for line in raw_input.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not sources:
        raise ValueError("source list cannot be empty")
    return sources


def safe_stem(value: str) -> str:
    stem = re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-._")
    return stem or "v2t-output"


def _extract_page_from_url(url: str) -> int | None:
    """Extract a valid 1-based 'p' (page) parameter from URL query string."""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    p_values = query_params.get("p", [])
    if p_values:
        try:
            page = int(p_values[0])
        except ValueError:
            return None
        return page if page >= 1 else None
    return None
