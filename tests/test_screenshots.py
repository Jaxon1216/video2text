from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from threading import Event
import wave

import pytest

from v2t.config import Settings
from v2t.progress import ProgressReporter, TaskCancelled
from v2t.services.screenshots import (
    ScreenshotError, capture_screenshots, clean_stale_screenshot_staging, probe_video,
    read_screenshots, record_screenshot_failure, screenshot_key, screenshot_path,
)


SET_A = "a" * 64
SET_B = "b" * 64


def test_only_same_metadata_can_read_a_screenshot_set_after_reindex(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    original = tmp_path / "original.json"
    original.write_text('{"title":"甲"}', encoding="utf-8")
    key = screenshot_key(original)
    record_screenshot_failure(settings, key, "没有视频轨道")
    restored = tmp_path / "restored" / original.name
    restored.parent.mkdir()
    shutil.copy(original, restored)
    assert read_screenshots(settings, screenshot_key(restored))["error"] == "没有视频轨道"
    restored.write_text('{"title":"乙"}', encoding="utf-8")
    assert read_screenshots(settings, screenshot_key(restored)) is None
    renamed = original.rename(tmp_path / "another.json")
    assert read_screenshots(settings, screenshot_key(renamed)) is None


@pytest.mark.parametrize("invalid_key", ["1", "../" + SET_A, "z" * 64, ""])
def test_only_metadata_hashes_are_accepted(tmp_path: Path, invalid_key: str) -> None:
    with pytest.raises(ValueError, match="invalid screenshot asset key"):
        read_screenshots(Settings.from_workspace(tmp_path), invalid_key)


def _film(path: Path, colors: list[int], framerate: str = "1") -> Path:
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("screenshot sampling needs ffmpeg and ffprobe")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pixel_format", "gray",
                    "-video_size", "64x48", "-framerate", framerate, "-i", "pipe:0", "-c:v", "ffv1", str(path)],
                   input=b"".join(bytes([value]) * (64 * 48) for value in colors), check=True)
    return path


@pytest.mark.parametrize("duration", [1, 80])
def test_short_film_and_exact_boundary_keep_only_first_frame(tmp_path: Path, duration: int) -> None:
    settings = Settings.from_workspace(tmp_path)
    media = _film(tmp_path / "clip.mkv", [80] * duration)
    manifest = capture_screenshots(settings, SET_A, media)
    assert manifest["sample_count"] == 1
    assert manifest["images"] == [{"index": 1, "time": 0}]
    jpeg = screenshot_path(settings, SET_A, 1)
    assert jpeg.read_bytes().startswith(b"\xff\xd8")
    dimensions = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                                          "-of", "json", str(jpeg)])
    assert json.loads(dimensions)["streams"][0] == {"width": 64, "height": 48}


def test_adjacent_dedup_keeps_return_to_previous_page(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    media = _film(tmp_path / "pages.mkv", [16] * 80 + [30] * 80 + [220] * 80 + [16])
    progress = []
    record_screenshot_failure(settings, SET_B, "来源没有视频画面轨道")
    manifest = capture_screenshots(settings, SET_B, media, on_progress=lambda done, total: progress.append((done, total)))
    assert progress == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert manifest["images"] == [{"index": 1, "time": 0}, {"index": 2, "time": 160}, {"index": 3, "time": 240}]
    assert read_screenshots(settings, SET_B) == manifest
    assert screenshot_path(settings, SET_B, 1).read_bytes() == screenshot_path(settings, SET_B, 3).read_bytes()
    with pytest.raises(FileNotFoundError):
        screenshot_path(settings, SET_B, 4)
    with pytest.raises(FileNotFoundError):
        screenshot_path(settings, SET_A, 1)


def test_sample_uses_frame_on_screen_during_long_holds_including_last_frame(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    media = _film(tmp_path / "stills.mkv", [0, 150, 255], framerate="1/60")
    assert probe_video(media) == 180
    manifest = capture_screenshots(settings, SET_A, media)
    assert [image["time"] for image in manifest["images"]] == [0, 80, 160]
    for index, expected in enumerate([0, 150, 255], 1):
        pixels = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(screenshot_path(settings, SET_A, index)),
                                          "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"])
        assert abs(pixels[0] - expected) <= 1


def test_cancel_at_publish_keeps_staged_images_private_and_retry_works(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    reporter = ProgressReporter("capture", cancel_event=Event())
    media = _film(tmp_path / "clip.mkv", [16, 32])

    @contextmanager
    def cancel_before_publication():
        assert read_screenshots(settings, SET_A) is None
        reporter.cancel_event.set()
        yield

    with pytest.raises(TaskCancelled):
        capture_screenshots(settings, SET_A, media, check_cancelled=reporter.check_cancelled,
                            publish_guard=cancel_before_publication)
    assert not list((tmp_path / "screenshots").iterdir())
    reporter.cancel_event.clear()
    assert capture_screenshots(settings, SET_A, media)["status"] == "ready"
    abandoned = tmp_path / "screenshots" / ".capture-crash"
    abandoned.mkdir()
    (abandoned / "partial.jpg").touch()
    clean_stale_screenshot_staging(settings)
    assert not abandoned.exists()
    assert screenshot_path(settings, SET_A, 1).is_file()


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="needs ffprobe")
def test_audio_only_has_no_video_duration_and_no_files_are_published(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    media = tmp_path / "audio.wav"
    with wave.open(str(media), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(32000))
    with pytest.raises(ScreenshotError, match="没有视频画面"):
        probe_video(media)
    with pytest.raises(ScreenshotError):
        capture_screenshots(settings, SET_A, media)
    assert read_screenshots(settings, SET_A) is None
    record_screenshot_failure(settings, SET_A, "来源没有视频画面轨道")
    assert read_screenshots(settings, SET_A)["error"] == "来源没有视频画面轨道"
