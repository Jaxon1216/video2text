from pathlib import Path
from threading import Event

import pytest

from v2t.config import Settings
from v2t.downloaders import ytdlp
from v2t.downloaders.base import Downloader
from v2t.downloaders.douyin import DouyinDownloader
from v2t.downloaders.youtube import YouTubeDownloader
from v2t.inputs import parse_source
from v2t.models import DownloadResult, SubtitleResult
from v2t.progress import ProgressReporter, TaskCancelled
from v2t.services import pipeline
from v2t.services.screenshots import ScreenshotError
from v2t.storage.audio_cache import AudioCache
from test_audio_cache import write_wav
from test_douyin_downloader import FakeResponse, douyin_source, make_detail
from test_youtube_downloader import URL, ydl


class FakeVideoDownloader(Downloader):
    def __init__(self):
        self.calls = []
        self.fail = False
        self.on_download = None

    def fetch_subtitles(self, source, settings, *, progress=None):
        return SubtitleResult(source=source, language="zh", segments=[{"start": 0, "end": 1, "text": "原文"}])

    def download(self, *args, **kwargs):
        pytest.fail("a subtitle or a cached WAV should supply the transcript")

    def download_video(self, source, settings, output_dir, *, progress=None):
        self.calls.append(source)
        output = output_dir / "sample.webm"
        output.write_bytes(b"video")
        if self.on_download:
            self.on_download()
        if progress:
            progress.running("downloading", stage_progress=0.5)
        if self.fail:
            raise RuntimeError("补取失败")
        return DownloadResult(source, output)


@pytest.fixture
def video_env(tmp_path, monkeypatch):
    settings = Settings.from_workspace(tmp_path / "workspace")
    settings.ensure_directories()
    downloader = FakeVideoDownloader()
    probes = []

    def probe(path, *, check_cancelled=None):
        if check_cancelled:
            check_cancelled()
        probes.append(path)
        if path.read_bytes() != b"video":
            raise ScreenshotError("媒体里没有可解码的视频画面")
        return 1.0

    monkeypatch.setattr(pipeline, "probe_video", probe)
    monkeypatch.setattr(pipeline.shutil, "which", lambda tool: f"/bin/{tool}")
    engine = pipeline.V2TPipeline(settings=settings, downloaders={"youtube": downloader}, transcriber=None)
    return engine, downloader, probes


def test_subtitle_opt_in_fetches_only_after_text_and_reuses_result(video_env):
    engine, downloader, probes = video_env
    result = engine.transcribe(URL)
    assert result.text == "原文" and not downloader.calls and not probes
    audio = engine.settings.downloads_dir / "youtube-dQw4w9WgXcQ.webm"
    audio.write_bytes(b"audio")
    progress_events = []
    media = engine.resolve_screenshot_video(URL, progress=ProgressReporter("screens", progress_events.append))
    assert media != audio and audio.read_bytes() == b"audio"
    assert probes[0] == audio and media.read_bytes() == b"video"
    assert not progress_events  # Supplemental download must not reset the task's visible stage.
    assert engine.resolve_screenshot_video(URL) == media
    assert len(downloader.calls) == 1


def test_audio_cache_wav_does_not_prevent_supplemental_video(video_env):
    class CachedASR:
        name = "test"

        def transcribe(self, audio_path, *, prompt=None, progress=None):
            return {"text": "缓存正文"}

    engine, downloader, _ = video_env
    engine.transcriber = CachedASR()
    settings = engine.settings
    old_audio = settings.downloads_dir / "original.m4a"
    old_audio.write_bytes(b"audio")
    wav = settings.audio_dir / "input.wav"
    write_wav(wav)
    cached = AudioCache(settings).store(parse_source(URL), wav, title="Demo", webpage_url=URL,
                                        download_metadata={"id": "dQw4w9WgXcQ"}, video_path=old_audio)
    result = engine.transcribe(URL, force_asr=True)
    assert result.audio_path == cached.audio_path and result.text == "缓存正文" and not downloader.calls
    assert engine.resolve_screenshot_video(URL, video_path=result.video_path).read_bytes() == b"video"
    assert len(downloader.calls) == 1
    assert AudioCache(settings).lookup(parse_source(URL)).video_path == old_audio


def test_real_video_from_same_source_cache_works_after_subtitle_hit(video_env):
    engine, downloader, _ = video_env
    media = engine.settings.downloads_dir / "legacy.m4a"
    media.write_bytes(b"video")  # Some older Douyin muxed responses used an audio suffix.
    wav = engine.settings.audio_dir / "input.wav"
    write_wav(wav)
    AudioCache(engine.settings).store(parse_source(URL), wav, title="Demo", webpage_url=URL,
                                      download_metadata={"id": "dQw4w9WgXcQ"}, video_path=media)
    result = engine.transcribe(URL)
    assert result.video_path is None and engine.resolve_screenshot_video(URL) == media
    assert not downloader.calls


def test_only_correct_bilibili_part_can_be_reused(video_env, monkeypatch):
    engine, _, probes = video_env
    adapter = ytdlp.YtDlpDownloader()
    engine.downloaders["bilibili"] = adapter
    bv = "BV1xx411c7XD"
    part1 = engine.settings.downloads_dir / f"{bv}.mp4"
    part2 = engine.settings.downloads_dir / f"{bv}_p2.NA.mp4"
    part1.write_bytes(b"video")
    part2.write_bytes(b"video")
    monkeypatch.setattr(adapter, "download_video", FakeVideoDownloader().download_video)
    for p, expected in [(2, part2), (1, part1)]:
        assert engine.resolve_screenshot_video(f"https://www.bilibili.com/video/{bv}?p={p}") == expected
    probes.clear()
    third = engine.resolve_screenshot_video(f"https://www.bilibili.com/video/{bv}?p=3")
    assert third not in {part1, part2} and part1 not in probes and part2 not in probes


def test_pure_audio_and_failed_download_leave_text_and_no_partial_media(video_env):
    engine, downloader, _ = video_env
    local_audio = engine.settings.audio_dir / "audio.mp3"
    local_audio.write_bytes(b"audio")
    with pytest.raises(ScreenshotError, match="画面"):
        engine.resolve_screenshot_video(str(local_audio))
    downloader.fail = True
    result = engine.transcribe(URL)
    with pytest.raises(RuntimeError, match="补取失败"):
        engine.resolve_screenshot_video(URL)
    assert result.transcript_path.read_text().strip() == "原文"
    assert list((engine.settings.downloads_dir / "screenshot-media").iterdir()) == []


def test_cancel_during_supplemental_download_removes_partial_media(video_env):
    engine, downloader, _ = video_env
    cancelled = Event()
    downloader.on_download = cancelled.set
    with pytest.raises(TaskCancelled):
        engine.resolve_screenshot_video(URL, progress=ProgressReporter("test", cancel_event=cancelled))
    assert list((engine.settings.downloads_dir / "screenshot-media").iterdir()) == []


def test_missing_ffmpeg_never_starts_a_supplemental_download(video_env, monkeypatch):
    engine, downloader, probes = video_env
    monkeypatch.setattr(pipeline.shutil, "which", lambda tool: None)
    with pytest.raises(ScreenshotError, match="需要 ffmpeg"):
        engine.resolve_screenshot_video(URL)
    assert not downloader.calls and not probes


def test_missing_local_file_with_bv_name_is_not_a_remote_source(video_env):
    engine, downloader, probes = video_env
    missing = engine.settings.downloads_dir / "BV1o4p568Etd.mp4"
    with pytest.raises(ScreenshotError, match="本地媒体"):
        engine.resolve_screenshot_video(str(missing))
    assert not downloader.calls and not probes


def test_youtube_supplemental_requests_pictures_and_keeps_proxy_settings(tmp_path, ydl):
    settings = Settings.from_workspace(tmp_path)
    output_dir = tmp_path / "supplemental"
    YouTubeDownloader().download_video(parse_source(URL), settings, output_dir)
    options = ydl[1][0]
    assert options["format"] == "bestvideo/best[vcodec!=none]"
    assert Path(options["outtmpl"]).parent == output_dir and "proxy" not in options


def test_bilibili_supplemental_uses_requested_part_and_separate_output(tmp_path, monkeypatch):
    output_dir = tmp_path / "screens"
    output_dir.mkdir()
    source = parse_source("https://www.bilibili.com/video/BV1xx411c7XD?p=2")
    result = output_dir / "video.webm"
    result.write_bytes(b"video")
    options = {}

    class YDL:
        def __init__(self, opts):
            options.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            assert url == source.url and download is True
            return {"requested_downloads": [{"filepath": str(result)}]}

        def sanitize_info(self, info):
            return info

    monkeypatch.setattr(ytdlp, "_import_youtube_dl", lambda: YDL)
    actual = ytdlp.YtDlpDownloader().download_video(source, Settings.from_workspace(tmp_path), output_dir)
    assert actual.video_path == result and options["playlist_items"] == "2"
    assert options["format"] == "bestvideo/best[vcodec!=none]" and Path(options["outtmpl"]).parent == output_dir


def test_douyin_supplemental_uses_muxed_play_addr_and_leaves_original_audio(tmp_path):
    settings = Settings.from_workspace(tmp_path)
    settings.ensure_directories()
    output_dir = tmp_path / "staging"
    output_dir.mkdir()
    original = settings.downloads_dir / "douyin-7671185082790530347.m4a"
    original.write_bytes(b"audio")
    urls = []

    def opener(request, timeout):
        urls.append(request.full_url)
        return FakeResponse(b"video")

    adapter = DouyinDownloader(fetch_detail=lambda *a, **k: {"aweme_detail": make_detail()}, opener=opener)
    video = adapter.download_video(douyin_source(), settings, output_dir).video_path
    assert urls == ["https://a.douyinvod.com/muxed.mp4"]
    assert video == output_dir / "video.mp4" and video.read_bytes() == b"video"
    assert original.read_bytes() == b"audio"
