from __future__ import annotations

from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
import requests

from v2t.config import Settings
from v2t.inputs import parse_source
from v2t.progress import ProgressReporter, TaskCancelled
from v2t.downloaders import youtube


URL = "https://youtu.be/dQw4w9WgXcQ"


def track(language="en", *, generated=False, snippets=None):
    return SimpleNamespace(language_code=language, is_generated=generated,
                           fetch=lambda: snippets if snippets is not None else [SimpleNamespace(start=1.25, duration=2.5, text="hello")])


@pytest.fixture
def subtitles(monkeypatch):
    tracks = []
    calls = []

    class API:
        def __init__(self, *, http_client):
            self.client = http_client

        def list(self, video_id):
            calls.append(video_id)
            return tracks

    monkeypatch.setattr(youtube, "_import_transcript_api", lambda: API)
    monkeypatch.setattr(requests.Session, "get", lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"title": "A real title", "author_name": "Creator"}))
    return tracks, calls


@pytest.mark.parametrize("available,expected,generated", [
    ([track("en"), track("zh-Hans", generated=True), track("zh-CN"), track("zh-Hans")], "zh-Hans", False),
    ([track("zh", generated=True), track("en")], "en", False),
    ([track("en", generated=True), track("zh-Hant", generated=True)], "zh-Hant", True),
    ([track("ja"), track("en", generated=True)], "en", True),
    ([track("ja", generated=True)], "ja", True),
])
def test_youtube_subtitle_language_and_type_priority(tmp_path, subtitles, available, expected, generated):
    tracks, calls = subtitles
    tracks.extend(available)
    result = youtube.YouTubeDownloader().fetch_subtitles(parse_source(URL), Settings.from_workspace(tmp_path))
    assert result.language == expected
    assert result.segments == [{"start": 1.25, "end": 3.75, "text": "hello"}]
    assert result.title == "A real title"
    assert result.metadata["platform"] == "youtube"
    assert result.metadata["id"] == "dQw4w9WgXcQ"
    assert result.metadata["uploader"] == "Creator"
    assert result.metadata["is_generated"] is generated
    assert result.webpage_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert calls == ["dQw4w9WgXcQ"]


def test_youtube_empty_and_invalid_subtitles_fall_through(tmp_path, subtitles):
    tracks, _ = subtitles
    downloader = youtube.YouTubeDownloader()
    settings = Settings.from_workspace(tmp_path)
    assert downloader.fetch_subtitles(parse_source(URL), settings) is None
    tracks.extend([track("zh", snippets=[]), track("en", snippets=[
        SimpleNamespace(start=-1, duration=2, text="bad"),
        SimpleNamespace(start=float("nan"), duration=2, text="bad"),
        SimpleNamespace(start=1, duration=-2, text="bad"),
        SimpleNamespace(start=1, duration=2, text="  "),
    ]), track("ja")])
    assert downloader.fetch_subtitles(parse_source(URL), settings).language == "ja"


def test_youtube_title_failure_keeps_successful_subtitles(tmp_path, subtitles, monkeypatch):
    tracks, _ = subtitles
    tracks.append(track())
    def unavailable(*args, **kwargs):
        raise requests.Timeout("offline")
    monkeypatch.setattr(requests.Session, "get", unavailable)
    result = youtube.YouTubeDownloader().fetch_subtitles(parse_source(URL), Settings.from_workspace(tmp_path))
    assert result.segments
    assert result.title == "youtube-dQw4w9WgXcQ"


def test_subtitle_cancellation_after_network_does_not_fetch_metadata(tmp_path, subtitles, monkeypatch):
    tracks, _ = subtitles
    cancelled = Event()
    def fetch():
        cancelled.set()
        return [SimpleNamespace(start=0, duration=1, text="hello")]
    tracks.append(SimpleNamespace(language_code="en", is_generated=False, fetch=fetch))
    monkeypatch.setattr(requests.Session, "get", lambda *args, **kwargs: pytest.fail("metadata after cancellation"))
    with pytest.raises(TaskCancelled):
        youtube.YouTubeDownloader().fetch_subtitles(parse_source(URL), Settings.from_workspace(tmp_path),
                                                   progress=ProgressReporter("demo", cancel_event=cancelled))


@pytest.fixture
def ydl(monkeypatch, tmp_path):
    media = tmp_path / "audio.webm"
    media.write_bytes(b"audio")
    options, calls = [], []
    info = {"id": "dQw4w9WgXcQ", "title": "Demo", "uploader": "Creator", "duration": 15,
            "ext": "webm", "requested_downloads": [{"filepath": str(media)}]}

    class YDL:
        def __init__(self, opts):
            options.append(opts)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download):
            calls.append((url, download))
            if download:
                for hook in options[-1].get("progress_hooks", []):
                    hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10})
                    hook({"status": "finished"})
            return info
        def sanitize_info(self, value):
            return value
        def prepare_filename(self, value):
            return str(media)

    monkeypatch.setattr(youtube, "_import_youtube_dl", lambda: YDL)
    return info, options, calls, media


def test_youtube_download_is_audio_only_with_metadata_and_progress(tmp_path, ydl, monkeypatch):
    _, options, calls, media = ydl
    monkeypatch.delenv("V2T_USE_PROXY", raising=False)
    snapshots = []
    result = youtube.YouTubeDownloader().download(parse_source(URL), Settings.from_workspace(tmp_path),
                                                progress=ProgressReporter("demo", snapshots.append))
    assert options[0]["format"] == "bestaudio/best"
    assert options[0]["noplaylist"] is True
    assert "proxy" not in options[0]
    assert "youtube-" in options[0]["outtmpl"]
    assert result.video_path == media
    assert result.metadata["duration"] == 15
    assert result.metadata["platform"] == "youtube"
    assert any(snapshot.percent == pytest.approx(0.2) for snapshot in snapshots)
    assert calls == [("https://www.youtube.com/watch?v=dQw4w9WgXcQ", False),
                     ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", True)]


def test_youtube_download_reuses_cookie_options(tmp_path, ydl, monkeypatch):
    cookie = tmp_path / "session.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setenv("V2T_COOKIE_FILE", str(cookie))
    monkeypatch.setenv("V2T_COOKIES_FROM_BROWSER", "chrome")
    youtube.YouTubeDownloader().download(parse_source(URL), Settings.from_workspace(tmp_path))
    options = ydl[1][0]
    assert options["cookiefile"] == str(cookie)
    assert "cookiesfrombrowser" not in options


@pytest.mark.parametrize("status", ["is_live", "is_upcoming", "post_live"])
def test_youtube_refuses_unfinished_livestream_before_downloading(tmp_path, ydl, status):
    info, _, calls, _ = ydl
    info["live_status"] = status
    with pytest.raises(RuntimeError, match="直播"):
        youtube.YouTubeDownloader().download(parse_source(URL), Settings.from_workspace(tmp_path))
    assert len(calls) == 1 and calls[0][1] is False


def test_youtube_missing_download_is_actionable(tmp_path, ydl):
    ydl[3].unlink()
    with pytest.raises(RuntimeError, match="file|文件"):
        youtube.YouTubeDownloader().download(parse_source(URL), Settings.from_workspace(tmp_path))


def test_youtube_download_errors_explain_recovery(tmp_path, ydl, monkeypatch):
    from yt_dlp.utils import DownloadError
    def unavailable(*args, **kwargs):
        raise DownloadError("Sign in to confirm you're not a bot")
    monkeypatch.setattr(youtube, "_import_youtube_dl", unavailable)
    with pytest.raises(RuntimeError, match="V2T_COOKIE_FILE"):
        youtube.YouTubeDownloader().download(parse_source(URL), Settings.from_workspace(tmp_path))


def test_youtube_download_cancellation_is_not_wrapped_as_failure(tmp_path, ydl):
    cancelled = Event()
    def callback(snapshot):
        if snapshot.message == "downloading":
            cancelled.set()
    with pytest.raises(TaskCancelled):
        youtube.YouTubeDownloader().download(parse_source(URL), Settings.from_workspace(tmp_path),
            progress=ProgressReporter("demo", callback, cancel_event=cancelled))


def test_youtube_rejects_other_source_kinds(tmp_path):
    downloader = youtube.YouTubeDownloader()
    source = parse_source("BV1xx411c7XD")
    assert downloader.fetch_subtitles(source, Settings.from_workspace(tmp_path)) is None
    with pytest.raises(ValueError):
        downloader.download(source, Settings.from_workspace(tmp_path))
