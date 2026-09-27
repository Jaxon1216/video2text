from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypedDict


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
