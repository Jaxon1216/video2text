from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from threading import Lock

from v2t.audio_cache import AudioCache
from v2t.config import Settings
from v2t.database import AppDatabase
from v2t.inputs import parse_source
from v2t.models import REMOTE_SOURCE_KINDS, TranscriptDocument, TranscriptResult
from v2t.naming import transcript_filename, write_unique_text


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class WorkspaceLibrary:
    def __init__(self, settings: Settings, database: AppDatabase) -> None:
        self.settings = settings
        self.database = database
        self._ai_import_lock = Lock()
        self.settings.ensure_directories()

    def prepare_retranscription_source(self, video_id: int) -> str:
        video = self.database.get_video(video_id)
        if video is None:
            raise KeyError(video_id)
        source_input = str(video["source_input"])
        source = parse_source(source_input)
        if source.kind in REMOTE_SOURCE_KINDS and video.get("audio_path"):
            try:
                metadata = self.load_video_metadata(video_id)
                AudioCache(self.settings).import_legacy(source, Path(str(video["audio_path"])), metadata)
            except (OSError, ValueError):
                pass
        return source_input

    def register_transcript_result(self, result: TranscriptResult) -> int:
        transcript_path = self._ensure_original_transcript(result)
        metadata_path = self._ensure_metadata_file(result, transcript_path)
        text = transcript_path.read_text(encoding="utf-8")

        video_id = self.database.create_video(
            source_kind=result.source.kind,
            source_input=result.source.raw_input,
            source_url=result.source.url,
            source_bv=result.source.bv,
            title=result.metadata.get("title") or (result.metadata.get("download") or {}).get("title") or result.source.display_name,
            display_name=result.source.display_name,
            language=result.metadata.get("language"),
            engine=result.engine,
            model=result.model,
            video_path=str(result.video_path) if result.video_path else None,
            audio_path=str(result.audio_path) if result.audio_path else "",
            metadata_path=str(metadata_path),
        )
        if self.database.get_active_transcript_version(video_id) is None:
            self.database.create_transcript_version(
                video_id=video_id,
                kind="original",
                file_path=str(transcript_path),
                text_sha256=sha256_text(text),
                char_count=len(text),
                is_active=True,
            )
        return video_id

    def save_edited_transcript(self, video_id: int, text: str) -> int:
        video = self.database.get_video(video_id)
        if video is None:
            raise RuntimeError(f"video not found: {video_id}")

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = write_unique_text(
            self.settings.transcripts_edited_dir / transcript_filename(
                str(video["title"]), "edited", suffix=f"{video_id}-{timestamp}",
            ),
            text.rstrip() + "\n",
        )
        return self.database.create_transcript_version(
            video_id=video_id,
            kind="edited",
            file_path=str(path),
            text_sha256=sha256_text(text.rstrip() + "\n"),
            char_count=len(text.rstrip() + "\n"),
            is_active=True,
        )

    def ai_output_path(self, video_id: int) -> Path:
        video = self.database.get_video(video_id)
        if video is None:
            raise KeyError(video_id)
        return (self.settings.transcripts_ai_dir / transcript_filename(
            str(video["title"]), "ai_optimized", suffix=str(video_id),
        )).absolute()

    def sync_ai_transcript(self, video_id: int) -> int | None:
        """Snapshot a changed AI delivery file without changing the active transcript."""
        if self.database.get_video(video_id) is None:
            raise KeyError(video_id)
        with self._ai_import_lock:
            path = self.ai_output_path(video_id)
            try:
                text = path.read_text(encoding="utf-8-sig").strip()
            except FileNotFoundError:
                legacy_path = self.settings.transcripts_ai_dir / f"video-{video_id}.txt"
                try:
                    text = legacy_path.read_text(encoding="utf-8-sig").strip()
                except FileNotFoundError:
                    return None
            if not text:
                raise ValueError("AI 优化稿为空，请保存 UTF-8 纯文本正文后刷新。")
            text += "\n"
            text_hash = sha256_text(text)
            latest = next(
                (version for version in self.database.list_transcript_versions(video_id) if version.kind == "ai_optimized"),
                None,
            )
            if latest is not None and latest.text_sha256 == text_hash:
                return latest.id
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            snapshot = write_unique_text(path.with_name(f"{path.stem}-{timestamp}.txt"), text)
            return self.database.create_transcript_version(
                video_id=video_id,
                kind="ai_optimized",
                file_path=str(snapshot),
                text_sha256=text_hash,
                char_count=len(text),
                is_active=False,
            )

    def load_active_transcript(self, video_id: int) -> dict[str, object]:
        version = self.database.get_active_transcript_version(video_id)
        if version is None:
            raise RuntimeError(f"active transcript not found for video {video_id}")
        return self.load_transcript_version(video_id, version.id)

    def load_transcript_version(self, video_id: int, version_id: int) -> dict[str, object]:
        version = self.database.get_transcript_version(video_id, version_id)
        if version is None:
            raise RuntimeError(f"transcript version not found: video={video_id} version={version_id}")
        path = Path(version.file_path)
        return {
            "version_id": version.id,
            "kind": version.kind,
            "file_path": version.file_path,
            "is_active": version.is_active,
            "text": path.read_text(encoding="utf-8"),
        }

    def load_video_metadata(self, video_id: int) -> dict[str, object]:
        video = self.database.get_video(video_id)
        if video is None:
            raise RuntimeError(f"video not found: {video_id}")
        metadata_path = Path(str(video["metadata_path"]))
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def load_document(self, video_id: int, version_id: int | None = None) -> TranscriptDocument:
        video = self.database.get_video(video_id)
        if video is None:
            raise RuntimeError(f"video not found: {video_id}")
        metadata = self.load_video_metadata(video_id)
        transcript = (
            self.load_active_transcript(video_id)
            if version_id is None else self.load_transcript_version(video_id, version_id)
        )
        source = metadata.get("source") or {}
        download = metadata.get("download") or {}
        version_kind = str(transcript["kind"])
        # Changed text no longer lines up with the ASR timings.
        segments = list(metadata.get("segments") or []) if version_kind == "original" else []
        return TranscriptDocument(
            video_id=video_id,
            title=str(video["title"]),
            platform=str(video["source_kind"]),
            url=source.get("webpage_url") or download.get("webpage_url") or source.get("url"),
            uploader=download.get("uploader"),
            duration=download.get("duration"),
            engine=str(video["engine"]),
            model=str(video["model"]),
            transcript_source=str(metadata.get("transcript_source") or "asr"),
            version_kind=version_kind,
            text=str(transcript["text"]),
            segments=segments,
            version_id=int(transcript["version_id"]),
            ai_output_path=str(self.ai_output_path(video_id)),
        )

    def index_existing_workspace(self) -> None:
        self.settings.ensure_directories()
        for metadata_path in sorted(self.settings.metadata_dir.glob("*.json")):
            transcript_path = self.settings.transcripts_original_dir / f"{metadata_path.stem}.txt"
            if not transcript_path.exists():
                fallback = metadata_path.with_suffix(".txt")
                if fallback.exists():
                    transcript_path = fallback
            if not transcript_path.exists():
                continue

            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            source = data.get("source") or {}
            video_id = self.database.create_video(
                source_kind=source.get("kind") or "audio",
                source_input=source.get("raw_input") or transcript_path.stem,
                source_url=source.get("url"),
                source_bv=source.get("bv"),
                title=data.get("title") or (data.get("download") or {}).get("title") or transcript_path.stem,
                display_name=transcript_path.stem,
                language=data.get("language"),
                engine=data.get("engine") or "unknown",
                model=str(data.get("model") or ""),
                video_path=data.get("video_path"),
                audio_path=data.get("audio_path") or "",
                metadata_path=str(metadata_path),
            )
            if self.database.get_active_transcript_version(video_id) is not None:
                continue
            text = transcript_path.read_text(encoding="utf-8")
            self.database.create_transcript_version(
                video_id=video_id,
                kind="original",
                file_path=str(transcript_path),
                text_sha256=sha256_text(text),
                char_count=len(text),
                is_active=True,
            )

    def _ensure_original_transcript(self, result: TranscriptResult) -> Path:
        target = self.settings.transcripts_original_dir / result.transcript_path.name
        if result.transcript_path.resolve() != target.resolve():
            target.write_text(result.transcript_path.read_text(encoding="utf-8"), encoding="utf-8")
        return target

    def _ensure_metadata_file(self, result: TranscriptResult, transcript_path: Path) -> Path:
        target = self.settings.metadata_dir / f"{transcript_path.stem}.json"
        metadata = {
            **result.metadata,
            "engine": result.engine,
            "model": result.model,
            "audio_path": str(result.audio_path) if result.audio_path else None,
            "video_path": str(result.video_path) if result.video_path else None,
            "transcript_path": str(transcript_path),
        }
        target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
