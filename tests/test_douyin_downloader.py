from __future__ import annotations

import io
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from b2t.config import Settings
from b2t.downloaders.douyin import (
    DouyinDownloader,
    DouyinError,
    build_metadata,
    clean_title,
    extract_detail,
    select_audio_urls,
)
from b2t.models import SourceRef


def make_detail(**overrides: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "aweme_id": "7671185082790530347",
        "desc": "线程池到底是怎么工作的 #计算机 #知识分享 #ai",
        "caption": "#计算机 #知识分享 #ai",
        "author": {"nickname": "蜡笔小浩"},
        "create_time": 1786086963,
        "video": {
            "duration": 288021,
            "bit_rate_audio": [
                {"audio_meta": {"bitrate": 193578, "url_list": {"main_url": "https://a.douyinvod.com/high", "backup_url": "https://b.douyinvod.com/high", "fallback_url": ""}}},
                {"audio_meta": {"bitrate": 48893, "url_list": {"main_url": "https://a.douyinvod.com/low", "backup_url": "https://b.douyinvod.com/low", "fallback_url": "https://c.douyinvod.com/low"}}},
            ],
            "play_addr": {"url_list": ["https://a.douyinvod.com/muxed.mp4"]},
        },
    }
    detail.update(overrides)
    return detail


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def douyin_source(video_id: str | None = "7671185082790530347") -> SourceRef:
    return SourceRef(
        raw_input="https://v.douyin.com/0CzNF8FbQ7s/",
        kind="douyin",
        display_name=f"douyin-{video_id}" if video_id else "douyin-share",
        url="https://v.douyin.com/0CzNF8FbQ7s/",
        video_id=video_id,
    )


def test_select_audio_urls_prefers_lowest_bitrate_audio_then_muxed_video() -> None:
    urls = select_audio_urls(make_detail())
    assert urls[:3] == [
        "https://a.douyinvod.com/low",
        "https://b.douyinvod.com/low",
        "https://c.douyinvod.com/low",
    ]
    assert urls[-1] == "https://a.douyinvod.com/muxed.mp4"
    assert "" not in urls


def test_select_audio_urls_rejects_image_posts() -> None:
    with pytest.raises(DouyinError, match="图文"):
        select_audio_urls(make_detail(images=[{"url_list": ["https://img"]}]))


def test_select_audio_urls_requires_some_media_url() -> None:
    with pytest.raises(DouyinError, match="没有找到"):
        select_audio_urls(make_detail(video={"duration": 1000}))


def test_extract_detail_reports_filtered_video() -> None:
    with pytest.raises(DouyinError, match="不可用"):
        extract_detail({"aweme_detail": None, "filter_detail": {"filter_reason": "status_self_see"}})


def test_extract_detail_reports_missing_detail() -> None:
    with pytest.raises(DouyinError, match="没有返回视频详情"):
        extract_detail({"status_code": 0})


def test_build_metadata_cleans_title_and_converts_duration() -> None:
    metadata = build_metadata(make_detail(), fallback_url="https://v.douyin.com/x/")
    assert metadata["platform"] == "douyin"
    assert metadata["title"] == "线程池到底是怎么工作的"
    assert metadata["uploader"] == "蜡笔小浩"
    assert metadata["duration"] == 288.021
    assert metadata["webpage_url"] == "https://www.douyin.com/video/7671185082790530347"


def test_clean_title_falls_back_to_empty_for_hashtag_only_desc() -> None:
    assert clean_title("#计算机 #知识分享") == ""


def test_download_opens_canonical_page_and_saves_audio(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("B2T_DOUYIN_HEADLESS", raising=False)
    monkeypatch.delenv("B2T_DOUYIN_BROWSER", raising=False)
    settings = Settings.from_workspace(tmp_path / ".b2t")
    fetch_calls: list[tuple[str, dict[str, Any]]] = []
    requested: list[Any] = []

    def fake_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        fetch_calls.append((url, kwargs))
        return {"aweme_detail": make_detail()}

    def fake_opener(request, timeout):  # type: ignore[no-untyped-def]
        requested.append(request)
        return FakeResponse(b"aac-bytes")

    result = DouyinDownloader(fetch_detail=fake_fetch, opener=fake_opener).download(douyin_source(), settings)

    url, kwargs = fetch_calls[0]
    assert url == "https://www.douyin.com/video/7671185082790530347"
    assert kwargs["profile_dir"] == settings.workspace_root / "browser"
    assert kwargs["headless"] is True
    assert kwargs["channel"] == "auto"
    assert requested[0].full_url == "https://a.douyinvod.com/low"
    assert requested[0].get_header("Referer") == "https://www.douyin.com/"
    assert result.video_path == settings.downloads_dir / "douyin-7671185082790530347.m4a"
    assert result.video_path.read_bytes() == b"aac-bytes"
    assert result.title == "线程池到底是怎么工作的"
    assert result.metadata["id"] == "7671185082790530347"


def test_download_uses_short_link_when_video_id_unknown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("B2T_DOUYIN_HEADLESS", "0")
    settings = Settings.from_workspace(tmp_path / ".b2t")
    seen: dict[str, Any] = {}

    def fake_fetch(url: str, **kwargs: Any) -> dict[str, Any]:
        seen["url"] = url
        seen["headless"] = kwargs["headless"]
        return {"aweme_detail": make_detail()}

    DouyinDownloader(fetch_detail=fake_fetch, opener=lambda request, timeout: FakeResponse(b"x")).download(
        douyin_source(video_id=None), settings
    )

    assert seen == {"url": "https://v.douyin.com/0CzNF8FbQ7s/", "headless": False}


def test_download_falls_back_to_next_url_on_error(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    attempts: list[str] = []

    def flaky_opener(request, timeout):  # type: ignore[no-untyped-def]
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise urllib.error.URLError("connection reset")
        return FakeResponse(b"ok")

    result = DouyinDownloader(
        fetch_detail=lambda url, **kwargs: {"aweme_detail": make_detail()},
        opener=flaky_opener,
    ).download(douyin_source(), settings)

    assert attempts == ["https://a.douyinvod.com/low", "https://b.douyinvod.com/low"]
    assert result.video_path.read_bytes() == b"ok"
    assert not list(settings.downloads_dir.glob("*.part"))


def test_download_raises_when_all_urls_fail(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")

    def failing_opener(request, timeout):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("blocked")

    downloader = DouyinDownloader(
        fetch_detail=lambda url, **kwargs: {"aweme_detail": make_detail()},
        opener=failing_opener,
    )
    with pytest.raises(DouyinError, match="音频下载失败"):
        downloader.download(douyin_source(), settings)


def test_download_rejects_non_douyin_source(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    source = SourceRef(raw_input="BV1xx411c7XD", kind="bilibili", display_name="BV1xx411c7XD")
    with pytest.raises(ValueError):
        DouyinDownloader(fetch_detail=lambda url, **kwargs: {}).download(source, settings)
