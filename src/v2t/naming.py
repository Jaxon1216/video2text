from __future__ import annotations

from pathlib import Path

from v2t.inputs import safe_stem


def transcript_filename(
    title: str, kind: str, *, extension: str = "txt", suffix: str = "",
) -> str:
    title_stem = safe_stem(title)[:60].rstrip("-._") or "v2t-output"
    label = {"original": "原始稿", "edited": "编辑稿", "ai_optimized": "AI优化"}[kind]
    stem = f"{title_stem}-{label}"
    if suffix:
        stem += f"-{suffix}"
    return f"{stem}.{extension}"


def write_unique_text(path: Path, text: str) -> Path:
    """Create a new transcript, adding a sequence number if the name is taken."""
    path.parent.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        candidate = path if number == 1 else path.with_name(f"{path.stem}-{number}{path.suffix}")
        try:
            handle = candidate.open("x", encoding="utf-8")
        except FileExistsError:
            number += 1
            continue
        with handle:
            handle.write(text)
        return candidate
