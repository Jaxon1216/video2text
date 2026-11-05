from __future__ import annotations

import errno
import hashlib
import json
import math
import re
import shutil
import subprocess
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import ContextManager

from v2t.config import Settings


INTERVAL_SECONDS = 80
# Compare only this region of the thumbnail. JPEGs keep the full frame.
_ROI_FILTER = "scale=320:180:flags=area,format=gray,crop=308:149:6:9"
_ROI_PIXELS = 308 * 149
Checkpoint = Callable[[], None]


class ScreenshotError(RuntimeError):
    """The video could not supply screenshots; its transcript remains usable."""


def _run(command: list[str], check_cancelled: Checkpoint | None) -> bytes:
    if check_cancelled:
        check_cancelled()
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ScreenshotError(f"无法运行 {command[0]}：{exc}") from exc
    with process:
        try:
            while True:
                try:
                    output, error = process.communicate(timeout=.25 if check_cancelled else None)
                    break
                except subprocess.TimeoutExpired:
                    check_cancelled()
        except BaseException:
            process.kill()
            process.communicate()
            raise
    if check_cancelled:
        check_cancelled()
    if process.returncode:
        detail = error.decode("utf-8", errors="replace").strip()[-500:]
        raise ScreenshotError(f"{command[0]} 读取画面失败：{detail}")
    return output


def _packet_duration(media: Path, check_cancelled: Checkpoint | None) -> float:
    """When stream timing is missing, inspect only video packets (no full decode)."""
    packets = _run(["ffprobe", "-v", "error", "-select_streams", "V:0", "-show_entries",
                    "packet=pts_time,duration_time", "-of", "csv=p=0", str(media)], check_cancelled)
    first, end = math.inf, -math.inf
    for line in packets.splitlines():
        fields = line.split(b",")
        try:
            at = float(fields[0])
        except ValueError:
            continue
        duration = float(fields[1]) if len(fields) > 1 and fields[1] not in (b"", b"N/A") else 0.
        first, end = min(first, at), max(end, at + duration)
    if not math.isfinite(first):
        raise ScreenshotError("无法读取视频帧的时间")
    return end - first


def _frame(media: Path, second: int, *, jpeg: bool, check_cancelled: Checkpoint | None) -> bytes:
    output_args = ["-q:v", "2", "-c:v", "mjpeg", "-f", "image2pipe"] if jpeg else [
        "-pix_fmt", "gray", "-f", "rawvideo",
    ]
    # Keep frames from the preceding seek point. Rounding PTS up to whole seconds
    # selects the frame already on screen at t=0, including held slides in VFR video.
    filters = "fps=1:start_time=0:round=up:eof_action=pass"
    if not jpeg:
        filters += "," + _ROI_FILTER
    frame = _run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(second), "-noaccurate_seek",
                  "-i", str(media), "-map", "0:V:0", "-vf", filters, "-frames:v", "1",
                  *output_args, "pipe:1"], check_cancelled)
    if not frame or (not jpeg and len(frame) != _ROI_PIXELS):
        raise ScreenshotError(f"视频在 {second} 秒没有可解码的画面")
    return frame


def _duration(media: Path, check_cancelled: Checkpoint | None) -> float:
    raw = _run(["ffprobe", "-v", "error", "-select_streams", "V:0", "-show_entries",
                "stream=duration,start_time:stream_tags=DURATION", "-of", "json",
                str(media)], check_cancelled)
    data = json.loads(raw)
    streams = data.get("streams", [])
    if not streams:
        raise ScreenshotError("来源没有视频画面轨道")
    stream = streams[0]
    duration = stream.get("duration")
    if not duration or duration == "N/A":
        # Matroska stores the end timestamp of each track as HH:MM:SS.sss.
        tag = stream.get("tags", {}).get("DURATION")
        if tag:
            try:
                hours, minutes, seconds = tag.split(":")
                duration = (int(hours) * 3600 + int(minutes) * 60 + float(seconds)
                            - float(stream.get("start_time") or 0))
            except ValueError:
                duration = None
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        seconds = _packet_duration(media, check_cancelled)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ScreenshotError("无法确定视频画面的时长")
    return seconds


def probe_video(path: Path, *, check_cancelled: Checkpoint | None = None) -> float:
    duration = _duration(path, check_cancelled)
    _frame(path, 0, jpeg=False, check_cancelled=check_cancelled)
    return duration


def _changed(previous: bytes, current: bytes) -> bool:
    changed = sum(abs(a - b) > 24 for a, b in zip(previous, current))
    return changed > _ROI_PIXELS * .025


def _root(settings: Settings) -> Path:
    return settings.workspace_root / "screenshots"


def screenshot_key(metadata_path: Path) -> str:
    """Keep a transcript's screenshots stable across workspace moves and database rebuilds."""
    digest = hashlib.sha256(metadata_path.name.encode("utf-8") + b"\0")
    with metadata_path.open("rb") as metadata:
        for chunk in iter(lambda: metadata.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _capture_dir(settings: Settings, asset_key: str) -> Path:
    if not isinstance(asset_key, str) or re.fullmatch(r"[0-9a-fA-F]{64}", asset_key) is None:
        raise ValueError("invalid screenshot asset key")
    return _root(settings) / asset_key.lower()


def clean_stale_screenshot_staging(settings: Settings) -> None:
    """Call once under the workspace process lock, before starting workers."""
    for file in _root(settings).glob(".capture-*"):
        if file.is_dir() and not file.is_symlink():
            shutil.rmtree(file)
    for file in _root(settings).glob(".failure-*"):
        file.unlink(missing_ok=True)


def read_screenshots(settings: Settings, asset_key: str) -> dict | None:
    folder = _capture_dir(settings, asset_key)
    for path in (folder / "manifest.json", folder.with_suffix(".error.json")):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
    return None


def record_screenshot_failure(settings: Settings, asset_key: str, message: str) -> None:
    folder = _capture_dir(settings, asset_key)
    folder.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"status": "unavailable", "interval_seconds": INTERVAL_SECONDS,
                "sample_count": 0, "images": [], "error": message}
    with NamedTemporaryFile(dir=folder.parent, prefix=".failure-", delete=False) as temporary:
        temp = Path(temporary.name)
    try:
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(folder.with_suffix(".error.json"))
    finally:
        temp.unlink(missing_ok=True)


def screenshot_path(settings: Settings, asset_key: str, index: int) -> Path:
    folder = _capture_dir(settings, asset_key)
    if type(index) is not int or index < 1:
        raise FileNotFoundError
    manifest = read_screenshots(settings, asset_key)
    if not manifest or not any(image["index"] == index for image in manifest["images"]):
        raise FileNotFoundError
    root = _root(settings).resolve()
    path = folder / f"{index}.jpg"
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise FileNotFoundError
    return path


def capture_screenshots(
    settings: Settings, asset_key: str, media: Path, *, check_cancelled: Checkpoint | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    publish_guard: Callable[[], ContextManager] | None = None,
) -> dict:
    """Sample into a private staging directory, then publish the complete immutable set."""
    folder = _capture_dir(settings, asset_key)
    existing = read_screenshots(settings, asset_key)
    if check_cancelled:
        check_cancelled()
    if existing and existing["status"] == "ready":
        with publish_guard() if publish_guard else nullcontext():
            if check_cancelled:
                check_cancelled()
            return existing
    duration = _duration(media, check_cancelled)
    times = range(0, math.ceil(duration), INTERVAL_SECONDS)
    folder.parent.mkdir(parents=True, exist_ok=True)
    images = []
    previous = None
    with TemporaryDirectory(dir=folder.parent, prefix=".capture-") as temp:
        staging = Path(temp) / "set"
        staging.mkdir()
        for number, second in enumerate(times, 1):
            thumbnail = _frame(media, second, jpeg=False, check_cancelled=check_cancelled)
            if previous is None or _changed(previous, thumbnail):
                index = len(images) + 1
                frame = _frame(media, second, jpeg=True, check_cancelled=check_cancelled)
                (staging / f"{index}.jpg").write_bytes(frame)
                images.append({"index": index, "time": second})
            previous = thumbnail
            if on_progress:
                on_progress(number, len(times))
        manifest = {"status": "ready", "interval_seconds": INTERVAL_SECONDS,
                    "sample_count": len(times), "images": images, "error": None}
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with publish_guard() if publish_guard else nullcontext():
            if check_cancelled:
                check_cancelled()
            # Two retries may share the same registered transcript and race to publish.
            try:
                staging.rename(folder)
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                    raise
                published = read_screenshots(settings, asset_key)
                if not published or published["status"] != "ready":
                    raise
                return published
            folder.with_suffix(".error.json").unlink(missing_ok=True)
    return manifest
