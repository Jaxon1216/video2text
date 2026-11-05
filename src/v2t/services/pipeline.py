from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import ContextManager

from v2t.storage.audio_cache import AudioCache
from v2t.config import Settings
from v2t.downloaders.base import Downloader
from v2t.inputs import AUDIO_SUFFIXES, VIDEO_SUFFIXES, parse_source, safe_stem
from v2t.models import REMOTE_SOURCE_KINDS, DownloadResult, SourceRef, SubtitleResult, TranscriptResult
from v2t.naming import transcript_filename, write_unique_text
from v2t.progress import ProgressReporter, TaskCancelled
from v2t.segments import join_text
from v2t.services import screenshots
from v2t.services.screenshots import ScreenshotError, probe_video
from v2t.transcribers.base import Transcriber


class V2TPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        downloaders: Mapping[str, Downloader],
        transcriber: Transcriber,
        prefer_subtitles: bool = True,
    ) -> None:
        self.settings = settings
        self.downloaders = dict(downloaders)
        self.transcriber = transcriber
        self.prefer_subtitles = prefer_subtitles

    def transcribe(
        self,
        source_input: str,
        *,
        prompt: str | None = None,
        output: Path | None = None,
        progress: ProgressReporter | None = None,
        force_asr: bool = False,
    ) -> TranscriptResult:
        self.settings.ensure_directories()
        if progress is not None:
            progress.running("preparing", message="preparing")
        source = parse_source(source_input)
        downloaded: DownloadResult | None = None
        subtitle: SubtitleResult | None = None
        audio_path: Path | None = None
        video_path: Path | None = None
        platform_metadata: dict | None = None
        webpage_url: str | None = None

        if source.kind in REMOTE_SOURCE_KINDS:
            downloader = self.downloaders.get(source.kind)
            if downloader is None:
                raise RuntimeError(f"no downloader registered for source kind: {source.kind}")
            if self.prefer_subtitles and not force_asr:
                subtitle = self._try_subtitles(downloader, source, progress)
            if subtitle is not None:
                source = subtitle.source
                base_name = subtitle.title or source.display_name
            else:
                original_source = source
                cache = AudioCache(self.settings)
                with cache.lock_for(source):
                    cached = cache.lookup(source)
                    if cached is not None:
                        audio_path = cached.audio_path
                        base_name = cached.title
                        video_path = cached.video_path
                        platform_metadata = cached.download_metadata
                        webpage_url = cached.webpage_url
                    else:
                        if progress is not None:
                            progress.check_cancelled()
                        downloaded = downloader.download(source, self.settings, progress=progress)
                        source = downloaded.source
                        audio_path = self._extract_audio(
                            downloaded.video_path,
                            safe_stem(f"{downloaded.title or source.display_name}-{uuid.uuid4().hex[:12]}"),
                            progress=progress,
                        )
                        base_name = downloaded.title or source.display_name
                        video_path = downloaded.video_path
                        platform_metadata = downloaded.metadata
                        webpage_url = downloaded.webpage_url
                        if progress is not None:
                            progress.check_cancelled()
                        cached = cache.store(source, audio_path, title=base_name,
                                             webpage_url=webpage_url, download_metadata=platform_metadata,
                                             video_path=video_path,
                                             aliases=[original_source.url or "", original_source.raw_input])
                        if cached is not None:
                            audio_path = cached.audio_path
        elif source.kind == "video":
            assert source.path is not None
            audio_path = self._extract_audio(source.path, safe_stem(f"{source.display_name}-{uuid.uuid4().hex[:12]}"), progress=progress)
            base_name = source.display_name
            video_path = source.path
        else:
            assert source.path is not None
            audio_path = source.path
            base_name = source.display_name

        if subtitle is not None:
            segments = list(subtitle.segments)
            text = ""
            for segment in segments:
                text = join_text(text, str(segment["text"]))
            engine, model, language, transcript_source = "subtitle", subtitle.language, subtitle.language, "subtitle"
            platform_metadata = subtitle.metadata
            webpage_url = subtitle.webpage_url
        else:
            assert audio_path is not None
            transcription = self.transcriber.transcribe(audio_path, prompt=prompt, progress=progress)
            text = transcription.get("text", "").strip()
            if not text:
                raise RuntimeError("transcriber returned an empty transcript")
            segments = list(transcription.get("segments") or [])
            engine = self.transcriber.name
            model = str(transcription.get("model") or "")
            language = transcription.get("language")
            transcript_source = "asr"

        if progress is not None:
            progress.running("writing_outputs", message="writing_outputs", indeterminate=True)
        transcript_path = self._resolve_output_path(base_name, output)
        if output is None:
            transcript_path = write_unique_text(transcript_path, text.strip() + "\n")
        else:
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text(text.strip() + "\n", encoding="utf-8")
        metadata_path = self._resolve_metadata_path(transcript_path)

        metadata = {
            "title": base_name,
            "source": {
                "raw_input": source.raw_input,
                "kind": source.kind,
                "bv": source.bv,
                "page": source.page,
                "video_id": source.video_id or (platform_metadata or {}).get("id"),
                "url": source.url,
                "webpage_url": webpage_url,
                "path": str(source.path) if source.path else None,
            },
            "engine": engine,
            "model": model,
            "audio_path": str(audio_path) if audio_path else None,
            "video_path": str(video_path) if video_path else None,
            "download": platform_metadata,
            "language": language,
            "generated_at": datetime.now().isoformat(),
            "transcript_source": transcript_source,
            "segments": segments,
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

        return TranscriptResult(
            source=source,
            engine=engine,
            model=model,
            text=text.strip(),
            audio_path=audio_path,
            transcript_path=transcript_path,
            metadata_path=metadata_path,
            video_path=video_path,
            metadata=metadata,
        )

    def illustrate(
        self, source: str, asset_key: str, *, video_path: Path | None, progress: ProgressReporter,
        publish_guard: Callable[[], ContextManager],
    ) -> None:
        progress.running("screenshot_media", message="获取配图视频", indeterminate=True)
        media = self.resolve_screenshot_video(source, video_path=video_path, progress=progress)
        progress.running("screenshots", message="抽取配图")
        screenshots.capture_screenshots(
            self.settings, asset_key, media, check_cancelled=progress.check_cancelled, publish_guard=publish_guard,
            on_progress=lambda done, total: progress.running(
                "screenshots", message=f"抽取配图 {done}/{total}", stage_progress=done / total,
            ),
        )

    def resolve_screenshot_video(
        self, source_input: str, *, video_path: Path | None = None, progress: ProgressReporter | None = None,
    ) -> Path:
        """Find or fetch a decodable video only after the opt-in transcript has been registered."""
        check = progress.check_cancelled if progress is not None else None
        if check:
            check()
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                raise ScreenshotError(f"生成配图需要 {tool}，请先安装 FFmpeg")
        local = Path(source_input).expanduser()
        if local.is_absolute() and not local.is_file():
            raise ScreenshotError("原始本地媒体文件已不存在")
        source = parse_source(source_input)
        if source.path is not None:
            probe_video(source.path, check_cancelled=check)
            return source.path

        downloader = self.downloaders.get(source.kind)
        if downloader is None:
            raise RuntimeError("这个来源暂不支持补取视频画面")
        cache = AudioCache(self.settings)
        identity = [source.kind, source.video_id or source.bv or source.url, source.page or 1]
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        root = self.settings.downloads_dir / "screenshot-media"
        target = root / f"{key}.media"
        checked: set[Path] = set()

        def usable(path: Path | None) -> bool:
            if path is None or path in checked or not path.is_file():
                return False
            checked.add(path)
            try:
                probe_video(path, check_cancelled=check)
            except ScreenshotError:
                return False
            return True

        with cache.lock_for(source):
            if check:
                check()
            # A stopped process may have left a partial download. The source lock
            # guarantees that no other worker is writing these staging directories.
            for stale in root.glob(f".{key}-*"):
                if stale.is_dir() and not stale.is_symlink():
                    shutil.rmtree(stale)
            candidates = [video_path, target]
            stems = downloader.existing_media_stems(source)
            candidates.extend(path for path in self.settings.downloads_dir.glob("*")
                              if path.stem in stems and path.suffix.lower() in VIDEO_SUFFIXES | AUDIO_SUFFIXES)
            for candidate in candidates:
                if candidate is not None and usable(candidate):
                    return candidate
            # A subtitle hit can still have associated media in a previous ASR cache.
            cached = cache.lookup(source)
            if cached and cached.video_path and usable(cached.video_path):
                return cached.video_path

            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root, prefix=f".{key}-") as staging:
                # Adapter download progress belongs to the ASR path. Here only forward cancellation.
                reporter = ProgressReporter(progress.task_id, cancel_event=progress.cancel_event) if progress else None
                if check:
                    check()
                result = downloader.download_video(source, self.settings, Path(staging), progress=reporter)
                if check:
                    check()
                media = result.video_path.resolve()
                if not media.is_relative_to(Path(staging).resolve()) or not media.is_file():
                    raise RuntimeError("补取视频没有生成可用的媒体文件")
                probe_video(media, check_cancelled=check)
                if check:
                    check()
                media.replace(target)
                return target

    def _try_subtitles(
        self,
        downloader: Downloader,
        source: SourceRef,
        progress: ProgressReporter | None,
    ) -> SubtitleResult | None:
        try:
            return downloader.fetch_subtitles(source, self.settings, progress=progress)
        except TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - subtitles are an optimisation; ASR is the fallback
            if progress is not None:
                progress.running(
                    "downloading",
                    message="subtitles_unavailable",
                    indeterminate=True,
                    detail={"subtitle_error": str(exc)[:300]},
                )
            return None

    def _extract_audio(self, video_path: Path, stem: str, progress: ProgressReporter | None = None) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required to extract audio but was not found on PATH")

        audio_path = self.settings.audio_dir / f"{stem}.wav"
        if progress is None:
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(video_path),
                    "-vn",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    str(audio_path),
                ],
                capture_output=True,
                encoding="utf-8",
            )
            if result.returncode != 0:
                stderr = result.stderr.strip() or "unknown ffmpeg error"
                raise RuntimeError(f"ffmpeg failed to extract audio: {stderr}")
            return audio_path

        duration = _probe_media_duration_seconds(video_path)
        progress.running(
            "extracting_audio",
            message="extracting_audio",
            stage_progress=0.0 if duration else None,
            indeterminate=duration is None,
        )
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-progress",
            "pipe:1",
            "-nostats",
            str(audio_path),
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
        )
        try:
            assert process.stdout is not None
            for line in process.stdout:
                progress.check_cancelled()
                parsed_seconds = _parse_ffmpeg_progress_seconds(line.strip())
                if parsed_seconds is None or duration in (None, 0):
                    continue
                progress.running(
                    "extracting_audio",
                    message="extracting_audio",
                    stage_progress=min(1.0, parsed_seconds / duration),
                )
            progress.check_cancelled()
            stderr_text = ""
            if process.stderr is not None:
                stderr_text = process.stderr.read()
            returncode = process.wait()
            if returncode != 0:
                stderr = stderr_text.strip() or "unknown ffmpeg error"
                raise RuntimeError(f"ffmpeg failed to extract audio: {stderr}")
            return audio_path
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            audio_path.unlink(missing_ok=True)
            raise

    def _resolve_output_path(self, base_name: str, output: Path | None) -> Path:
        if output is None:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            return self.settings.transcripts_original_dir / transcript_filename(
                base_name, "original", suffix=timestamp,
            )

        output = output.expanduser()
        if output.suffix.lower() != ".txt":
            if output.exists() and output.is_dir():
                return output / f"{safe_stem(base_name)}.txt"
            return output.with_suffix(".txt")
        return output

    def _resolve_metadata_path(self, transcript_path: Path) -> Path:
        if transcript_path.is_relative_to(self.settings.workspace_root):
            return self.settings.metadata_dir / f"{transcript_path.stem}.json"
        return transcript_path.with_suffix(".json")


def _parse_ffmpeg_progress_seconds(line: str) -> float | None:
    if line.startswith("out_time_ms="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    if line.startswith("out_time_us="):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    return None


def _probe_media_duration_seconds(video_path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    try:
        value = float((result.stdout or "").strip())
    except ValueError:
        return None
    return value if value > 0 else None
