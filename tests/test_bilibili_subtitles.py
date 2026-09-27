from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from v2t.config import Settings
from v2t.downloaders import ytdlp as ytdlp_module
from v2t.downloaders.base import Downloader
from v2t.downloaders.ytdlp import YtDlpDownloader, parse_subtitle_content, pick_subtitle
from v2t.inputs import parse_source
from v2t.models import DownloadResult, SourceRef, SubtitleResult
from v2t.pipeline import V2TPipeline
from v2t.segments import parse_bilibili_json, parse_srt
from v2t.transcribers.base import Transcriber

SRT = "1\n00:00:01,200 --> 00:00:03,400\n线程池\n是什么\n\n2\n00:00:04,000 --> 00:01:05,500\n<i>React</i> hooks\n"


def bilibili_source(**overrides: Any) -> SourceRef:
    values = {
        "raw_input": "https://www.bilibili.com/video/BV1xx411c7XD",
        "kind": "bilibili",
        "display_name": "BV1xx411c7XD",
        "url": "https://www.bilibili.com/video/BV1xx411c7XD",
        "bv": "BV1xx411c7XD",
        "video_id": "BV1xx411c7XD",
    }
    values.update(overrides)
    return SourceRef(**values)


def test_parse_srt_joins_lines_and_strips_tags() -> None:
    assert parse_srt(SRT) == [
        {"start": 1.2, "end": 3.4, "text": "线程池是什么"},
        {"start": 4.0, "end": 65.5, "text": "React hooks"},
    ]


def test_parse_bilibili_json_body() -> None:
    payload = {"body": [{"from": 0.5, "to": 2.0, "content": "你好"}, {"from": 2.0, "to": 3.0, "content": ""}]}
    assert parse_bilibili_json(payload) == [{"start": 0.5, "end": 2.0, "text": "你好"}]


def test_parse_subtitle_content_detects_json_and_srt() -> None:
    assert parse_subtitle_content('{"body": [{"from": 1, "to": 2, "content": "a"}]}', "json")[0]["text"] == "a"
    assert len(parse_subtitle_content(SRT, "srt")) == 2
    assert parse_subtitle_content("   ", "srt") == []


def test_pick_subtitle_prefers_human_chinese_and_ignores_danmaku() -> None:
    subtitles = {
        "danmaku": [{"ext": "xml", "url": "https://comment"}],
        "ai-zh": [{"ext": "srt", "data": "ai"}],
        "zh-CN": [{"ext": "json", "data": "{}"}, {"ext": "srt", "data": "human"}],
    }
    language, entry = pick_subtitle(subtitles)
    assert language == "zh-CN"
    assert entry["data"] == "human"
    assert pick_subtitle({"ai-zh": [{"ext": "srt", "data": "ai"}]})[0] == "ai-zh"
    assert pick_subtitle({"danmaku": [{"ext": "xml"}]}) is None


def test_parse_b23_short_link_as_bilibili_share() -> None:
    source = parse_source("【华强买瓜但程序员】 https://b23.tv/AbCdEf1")
    assert source.kind == "bilibili"
    assert source.bv is None
    assert source.url == "https://b23.tv/AbCdEf1"


def test_resolve_source_follows_short_link_redirect() -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self) -> str:
            return "https://www.bilibili.com/video/BV1kfDTBXEfu/?p=2&share_source=copy"

    downloader = YtDlpDownloader(opener=lambda request, timeout: Response())
    source = parse_source("https://b23.tv/AbCdEf1")

    resolved = downloader.resolve_source(source)

    assert resolved.bv == "BV1kfDTBXEfu"
    assert resolved.page == 2
    assert resolved.raw_input == "https://b23.tv/AbCdEf1"
    assert downloader.resolve_source(bilibili_source()) == bilibili_source()


def test_cookies_from_browser_used_when_no_cookie_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("V2T_COOKIE_FILE", raising=False)
    monkeypatch.setenv("V2T_COOKIES_FROM_BROWSER", "chrome")
    settings = Settings.from_workspace(tmp_path / ".v2t")

    opts = YtDlpDownloader()._build_ydl_opts(bilibili_source(), settings)
    assert opts["cookiesfrombrowser"] == ("chrome",)

    settings.ensure_directories()
    (settings.workspace_root / "cookies.txt").write_text("# cookie", encoding="utf-8")
    opts = YtDlpDownloader()._build_ydl_opts(bilibili_source(), settings)
    assert "cookiesfrombrowser" not in opts
    assert opts["cookiefile"].endswith("cookies.txt")


def test_fetch_subtitles_reads_inline_srt(tmp_path, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            captured["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url: str, download: bool) -> dict[str, Any]:
            captured["download"] = download
            return {
                "id": "BV1xx411c7XD",
                "title": "Demo",
                "uploader": "UP",
                "duration": 66,
                "webpage_url": url,
                "subtitles": {"danmaku": [{"ext": "xml"}], "ai-zh": [{"ext": "srt", "data": SRT}]},
            }

    monkeypatch.setattr(ytdlp_module, "_import_youtube_dl", lambda: FakeYDL)
    result = YtDlpDownloader().fetch_subtitles(bilibili_source(), Settings.from_workspace(tmp_path / ".v2t"))

    assert result is not None
    assert result.language == "ai-zh"
    assert result.title == "Demo"
    assert len(result.segments) == 2
    assert result.metadata["uploader"] == "UP"
    assert captured["download"] is False
    assert captured["opts"]["writesubtitles"] is True
    assert captured["opts"]["skip_download"] is True


class RecordingDownloader(Downloader):
    name = "recording"

    def __init__(self, tmp_path: Path, subtitle: SubtitleResult | None = None, error: Exception | None = None) -> None:
        self.tmp_path = tmp_path
        self.subtitle = subtitle
        self.error = error
        self.downloads = 0

    def fetch_subtitles(self, source, settings, *, progress=None):  # type: ignore[no-untyped-def]
        if self.error:
            raise self.error
        return self.subtitle

    def download(self, source, settings, *, progress=None) -> DownloadResult:  # type: ignore[no-untyped-def]
        self.downloads += 1
        path = self.tmp_path / "video.mp4"
        path.write_bytes(b"video")
        return DownloadResult(source=source, video_path=path, title="Downloaded", metadata={"title": "Downloaded"})


class CountingTranscriber(Transcriber):
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio_path: Path, *, prompt=None, progress=None) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        self.calls += 1
        return {"text": "asr text", "segments": [{"start": 0.0, "end": 1.0, "text": "asr text"}], "model": "small"}


class PipelineUnderTest(V2TPipeline):
    def _extract_audio(self, video_path: Path, stem: str, progress=None) -> Path:
        audio = self.settings.audio_dir / f"{stem}.wav"
        audio.write_bytes(b"wav")
        return audio


def make_subtitle() -> SubtitleResult:
    return SubtitleResult(
        source=bilibili_source(),
        language="zh-CN",
        segments=[{"start": 0.0, "end": 2.0, "text": "你好"}, {"start": 2.0, "end": 3.0, "text": "世界"}],
        title="Subtitled",
        webpage_url="https://www.bilibili.com/video/BV1xx411c7XD",
        metadata={"id": "BV1xx411c7XD", "title": "Subtitled", "uploader": "UP", "duration": 3},
    )


def test_pipeline_uses_subtitles_and_skips_download_and_asr(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    downloader = RecordingDownloader(tmp_path, subtitle=make_subtitle())
    transcriber = CountingTranscriber()
    pipeline = PipelineUnderTest(settings=settings, downloaders={"bilibili": downloader}, transcriber=transcriber)

    result = pipeline.transcribe("BV1xx411c7XD")

    assert downloader.downloads == 0
    assert transcriber.calls == 0
    assert result.engine == "subtitle"
    assert result.model == "zh-CN"
    assert result.text == "你好世界"
    assert result.audio_path is None
    assert result.metadata["transcript_source"] == "subtitle"
    assert result.metadata["segments"][1]["text"] == "世界"
    assert result.transcript_path.read_text(encoding="utf-8") == "你好世界\n"


@pytest.mark.parametrize(
    ("downloader_kwargs", "prefer_subtitles"),
    [({"subtitle": None}, True), ({"error": RuntimeError("412")}, True), ({"subtitle": make_subtitle()}, False)],
)
def test_pipeline_falls_back_to_asr(tmp_path: Path, downloader_kwargs: dict[str, Any], prefer_subtitles: bool) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    downloader = RecordingDownloader(tmp_path, **downloader_kwargs)
    transcriber = CountingTranscriber()
    pipeline = PipelineUnderTest(
        settings=settings,
        downloaders={"bilibili": downloader},
        transcriber=transcriber,
        prefer_subtitles=prefer_subtitles,
    )

    result = pipeline.transcribe("BV1xx411c7XD")

    assert downloader.downloads == 1
    assert transcriber.calls == 1
    assert result.metadata["transcript_source"] == "asr"
    assert result.engine == "counting"
