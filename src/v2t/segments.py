from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, TypedDict


_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")


def join_text(left: str, right: str) -> str:
    """Concatenate two text pieces, adding a space only between Latin words."""
    if not left:
        return right
    if not right:
        return left
    separator = "" if _CJK.match(left[-1]) or _CJK.match(right[0]) else " "
    return f"{left}{separator}{right}"


class Segment(TypedDict):
    start: float
    end: float
    text: str


def normalize_segments(
    items: Iterable[Any] | None,
    *,
    start_key: str = "start",
    end_key: str = "end",
    scale: float = 1.0,
) -> list[Segment]:
    """Convert provider-specific segment dicts into `{start, end, text}` in seconds.

    `scale` converts the provider's time unit to seconds, e.g. 0.001 for milliseconds.
    Items without usable times or text are dropped so callers can rely on the shape.
    """
    segments: list[Segment] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        start = item.get(start_key)
        end = item.get(end_key)
        if not text or not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        start_s = round(float(start) * scale, 3)
        end_s = round(max(float(end), float(start)) * scale, 3)
        segments.append({"start": start_s, "end": end_s, "text": text})
    return segments


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def parse_srt(text: str) -> list[Segment]:
    """Parse SRT / WebVTT cue blocks into segments; numbering lines and styling are ignored."""
    segments: list[Segment] = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        for index, line in enumerate(lines):
            match = _SRT_TIME.search(line)
            if not match:
                continue
            h1, m1, s1, ms1, h2, m2, s2, ms2 = match.groups()
            content = ""
            for item in lines[index + 1 :]:
                content = join_text(content, re.sub(r"<[^>]+>", "", item).strip())
            if content:
                segments.append(
                    {
                        "start": _to_seconds(h1, m1, s1, ms1),
                        "end": _to_seconds(h2, m2, s2, ms2),
                        "text": content,
                    }
                )
            break
    return segments


def parse_bilibili_json(payload: dict[str, Any]) -> list[Segment]:
    """Bilibili's native subtitle JSON: {"body": [{"from": 1.2, "to": 3.4, "content": "..."}]}."""
    items = [
        {"start": item.get("from"), "end": item.get("to"), "text": item.get("content")}
        for item in payload.get("body") or []
        if isinstance(item, dict)
    ]
    return normalize_segments(items)


def _to_seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return round(int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000, 3)
