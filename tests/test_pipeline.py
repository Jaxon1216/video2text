from pathlib import Path
from threading import Event

import pytest

from v2t.config import Settings
from v2t.downloaders.base import Downloader
from v2t.models import DownloadResult, SourceRef
from v2t.pipeline import V2TPipeline, _parse_ffmpeg_progress_seconds
from v2t.progress import ProgressReporter, TaskCancelled
from v2t.transcribers.base import Transcriber


class FakeDownloader(Downloader):
    name = "fake"

    def __init__(self, video_path: Path) -> None:
        self.video_path = video_path

    def download(self, source: SourceRef, settings: Settings, *, progress=None) -> DownloadResult:
        return DownloadResult(
            source=source,
            video_path=self.video_path,
            title="demo-title",
            metadata={"title": "demo-title"},
        )


class FakeTranscriber(Transcriber):
    name = "fake-whisper"

    def transcribe(self, audio_path: Path, *, prompt: str | None = None, progress=None) -> dict[str, str]:
        assert audio_path.exists()
        return {
            "text": "hello from v2t",
            "language": "zh",
            "model": "small",
        }


class PipelineUnderTest(V2TPipeline):
    def _extract_audio(self, video_path: Path, stem: str, progress=None) -> Path:
        audio_path = self.settings.audio_dir / f"{stem}.wav"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"wav")
        return audio_path


def test_pipeline_transcribes_bilibili_source(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    pipeline = PipelineUnderTest(
        settings=settings,
        downloaders={"bilibili": FakeDownloader(video_path)},
        transcriber=FakeTranscriber(),
    )

    result = pipeline.transcribe("BV1xx411c7XD")
    assert result.text == "hello from v2t"
    assert result.transcript_path.exists()
    assert result.metadata_path.exists()
    assert result.video_path == video_path


def test_pipeline_routes_douyin_source_to_douyin_downloader(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    audio_path = tmp_path / "douyin.m4a"
    audio_path.write_bytes(b"audio")

    pipeline = PipelineUnderTest(
        settings=settings,
        downloaders={
            "bilibili": FakeDownloader(tmp_path / "wrong.mp4"),
            "douyin": FakeDownloader(audio_path),
        },
        transcriber=FakeTranscriber(),
    )

    result = pipeline.transcribe("复制打开抖音 https://www.douyin.com/video/7671185082790530347 看看")
    assert result.source.kind == "douyin"
    assert result.video_path == audio_path
    assert result.metadata["source"]["video_id"] == "7671185082790530347"


def test_pipeline_rejects_remote_source_without_downloader(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    pipeline = PipelineUnderTest(
        settings=settings,
        downloaders={"bilibili": FakeDownloader(tmp_path / "unused.mp4")},
        transcriber=FakeTranscriber(),
    )

    with pytest.raises(RuntimeError, match="no downloader registered"):
        pipeline.transcribe("https://v.douyin.com/abc123/")


def test_pipeline_respects_custom_output_file(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"wav")
    output_path = tmp_path / "custom-result"

    pipeline = PipelineUnderTest(
        settings=settings,
        downloaders={"bilibili": FakeDownloader(tmp_path / "unused.mp4")},
        transcriber=FakeTranscriber(),
    )

    result = pipeline.transcribe(str(audio_path), output=output_path)
    assert result.transcript_path == output_path.with_suffix(".txt")
    assert result.transcript_path.exists()


def test_parse_ffmpeg_progress_seconds_supports_us_and_ms() -> None:
    assert _parse_ffmpeg_progress_seconds("out_time_ms=2500000") == 2.5
    assert _parse_ffmpeg_progress_seconds("out_time_us=4000000") == 4.0
    assert _parse_ffmpeg_progress_seconds("progress=continue") is None


def test_cancel_during_subtitle_lookup_does_not_fall_back_to_download(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    cancelled = Event()

    class SubtitleDownloader(FakeDownloader):
        def fetch_subtitles(self, source, settings, *, progress=None):
            cancelled.set()
            progress.running("downloading", message="checking_subtitles")

        def download(self, source, settings, *, progress=None):
            raise AssertionError("cancelled task must not download")

    pipeline = PipelineUnderTest(settings=settings,
                                 downloaders={"bilibili": SubtitleDownloader(tmp_path / "video.mp4")},
                                 transcriber=FakeTranscriber())
    with pytest.raises(TaskCancelled):
        pipeline.transcribe("BV1xx411c7XD", progress=ProgressReporter("task", cancel_event=cancelled))


def test_cancel_during_ffmpeg_terminates_process_and_removes_partial_audio(tmp_path: Path, monkeypatch) -> None:
    settings = Settings.from_workspace(tmp_path)
    settings.ensure_directories()
    cancelled = Event()
    terminated = []

    class FakeProcess:
        def __init__(self, command, **kwargs):
            Path(command[-1]).write_bytes(b"partial")
            self.stderr = None
            self.stdout = self.lines()

        def lines(self):
            cancelled.set()
            yield "out_time_ms=1000000\n"

        def terminate(self):
            terminated.append(True)

        def poll(self):
            return 0 if terminated else None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("v2t.pipeline.shutil.which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr("v2t.pipeline._probe_media_duration_seconds", lambda path: 10.0)
    monkeypatch.setattr("v2t.pipeline.subprocess.Popen", FakeProcess)
    pipeline = V2TPipeline(settings=settings, downloaders={}, transcriber=FakeTranscriber())
    with pytest.raises(TaskCancelled):
        pipeline._extract_audio(tmp_path / "video.mp4", "demo", ProgressReporter("task", cancel_event=cancelled))
    assert terminated
    assert not (settings.audio_dir / "demo.wav").exists()
