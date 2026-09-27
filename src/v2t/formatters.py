from __future__ import annotations

import re
from dataclasses import dataclass

from v2t.models import TranscriptDocument
from v2t.segments import Segment

DEFAULT_BLOCK_SECONDS = 30.0
EXPORT_FORMATS = ("txt", "plain", "md", "srt")
PLATFORM_LABELS = {"bilibili": "B站", "douyin": "抖音", "video": "本地视频", "audio": "本地音频"}

_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")


@dataclass(slots=True)
class ExportResult:
    content: str
    extension: str
    media_type: str


def format_timestamp(seconds: float, *, with_hours: bool = False) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if with_hours or hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def join_text(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    separator = "" if _CJK.match(left[-1]) or _CJK.match(right[0]) else " "
    return f"{left}{separator}{right}"


def group_segments(segments: list[Segment], block_seconds: float = DEFAULT_BLOCK_SECONDS) -> list[Segment]:
    """Merge short ASR segments into readable paragraphs of roughly `block_seconds` each."""
    blocks: list[Segment] = []
    for segment in segments:
        if blocks and segment["start"] - blocks[-1]["start"] < block_seconds:
            current = blocks[-1]
            current["end"] = segment["end"]
            current["text"] = join_text(current["text"], segment["text"])
        else:
            blocks.append({"start": segment["start"], "end": segment["end"], "text": segment["text"]})
    return blocks


def render_timestamped(segments: list[Segment], *, block_seconds: float = DEFAULT_BLOCK_SECONDS) -> str:
    blocks = group_segments(segments, block_seconds)
    with_hours = bool(blocks) and blocks[-1]["end"] >= 3600
    return "\n\n".join(f"[{format_timestamp(block['start'], with_hours=with_hours)}] {block['text']}" for block in blocks)


def render_srt(segments: list[Segment]) -> str:
    entries = []
    for index, segment in enumerate(segments, start=1):
        entries.append(
            f"{index}\n"
            f"{format_srt_timestamp(segment['start'])} --> {format_srt_timestamp(segment['end'])}\n"
            f"{segment['text']}"
        )
    return "\n\n".join(entries)


def render_body(document: TranscriptDocument) -> str:
    if document.has_timestamps:
        return render_timestamped(document.segments)  # type: ignore[arg-type]
    return document.text.strip()


def render_markdown(document: TranscriptDocument) -> str:
    lines = [f"# {document.title}", ""]
    for label, value in _info_rows(document):
        lines.append(f"- {label}：{value}")
    lines.extend(["", "## 文字稿", ""])
    if document.has_timestamps:
        blocks = group_segments(document.segments)  # type: ignore[arg-type]
        with_hours = bool(blocks) and blocks[-1]["end"] >= 3600
        lines.append(
            "\n\n".join(f"**[{format_timestamp(block['start'], with_hours=with_hours)}]** {block['text']}" for block in blocks)
        )
    else:
        lines.append(document.text.strip())
    return "\n".join(lines).rstrip() + "\n"


def export_document(document: TranscriptDocument, fmt: str) -> ExportResult:
    fmt = fmt.lower()
    if fmt == "txt":
        return ExportResult(render_body(document) + "\n", "txt", "text/plain")
    if fmt == "plain":
        return ExportResult(document.text.strip() + "\n", "txt", "text/plain")
    if fmt == "md":
        return ExportResult(render_markdown(document), "md", "text/markdown")
    if fmt == "srt":
        if not document.has_timestamps:
            raise ValueError("this transcript has no timestamps, SRT export is unavailable")
        return ExportResult(render_srt(document.segments) + "\n", "srt", "application/x-subrip")  # type: ignore[arg-type]
    raise ValueError(f"unsupported export format: {fmt} (choose from {', '.join(EXPORT_FORMATS)})")


def _info_rows(document: TranscriptDocument) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [("平台", PLATFORM_LABELS.get(document.platform, document.platform))]
    if document.url:
        rows.append(("链接", document.url))
    if document.uploader:
        rows.append(("作者", document.uploader))
    if document.duration:
        rows.append(("时长", format_timestamp(document.duration)))
    rows.append(("转写", f"{document.engine} {document.model}".strip()))
    return rows
