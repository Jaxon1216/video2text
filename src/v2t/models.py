from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


SourceKind = Literal["bilibili", "douyin", "audio", "video"]
REMOTE_SOURCE_KINDS: frozenset[str] = frozenset({"bilibili", "douyin"})
TaskStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


@dataclass(slots=True)
class SourceRef:
    raw_input: str
    kind: SourceKind
    display_name: str
    url: str | None = None
    bv: str | None = None
    path: Path | None = None
    page: int | None = None
    video_id: str | None = None


@dataclass(slots=True)
class DownloadResult:
    source: SourceRef
    video_path: Path
    title: str | None = None
    webpage_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SubtitleResult:
    """Platform-provided subtitles, used instead of ASR when available."""

    source: SourceRef
    language: str
    segments: list[dict[str, Any]]
    title: str | None = None
    webpage_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TranscriptResult:
    source: SourceRef
    engine: str
    model: str
    text: str
    audio_path: Path | None
    transcript_path: Path
    metadata_path: Path
    video_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TranscriptDocument:
    """Everything an exporter (or a future summarizer / chat feature) needs about one transcript."""

    video_id: int
    title: str
    platform: str
    url: str | None
    uploader: str | None
    duration: float | None
    engine: str
    model: str
    transcript_source: str
    version_kind: str
    text: str
    segments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_timestamps(self) -> bool:
        return bool(self.segments)


@dataclass(slots=True)
class ProgressSnapshot:
    task_id: str
    status: TaskStatus
    stage: str
    message: str = ""
    percent: float = 0.0
    indeterminate: bool = False
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskRecord:
    id: str
    kind: str
    status: TaskStatus
    source_input: str
    provider: str
    model: str
    workspace_root: str
    progress_percent: float = 0.0
    current_stage: str = "queued"
    current_message: str = ""
    error_message: str = ""
    video_id: int | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None


@dataclass(slots=True)
class VideoRecord:
    id: int
    source_kind: str
    source_input: str
    source_url: str | None
    source_bv: str | None
    title: str
    display_name: str
    language: str | None
    engine: str
    model: str
    video_path: str | None
    audio_path: str
    metadata_path: str
    current_transcript_version_id: int | None
    category_id: int | None
    created_at: str
    updated_at: str


@dataclass(slots=True)
class TranscriptVersionRecord:
    id: int
    video_id: int
    kind: str
    file_path: str
    text_sha256: str
    char_count: int
    is_active: bool
    created_at: str
    updated_at: str
