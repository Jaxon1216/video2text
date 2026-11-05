from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

from v2t.config import Settings
from v2t.formatters import format_timestamp, group_segments, render_markdown
from v2t.models import TranscriptDocument
from v2t.naming import transcript_filename
from v2t.services.screenshots import INTERVAL_SECONDS, read_screenshots, screenshot_key, screenshot_path
from v2t.storage.database import AppDatabase


def video_screenshot_key(database: AppDatabase, video_id: int) -> str:
    video = database.get_video(video_id)
    if video is None:
        raise FileNotFoundError
    return screenshot_key(Path(video["metadata_path"]))


def screenshot_view(database: AppDatabase, document: TranscriptDocument) -> dict:
    """Use the same time-to-paragraph assignments for the reader and the offline edition."""
    video_id = document.video_id
    manifest = read_screenshots(database.settings, video_screenshot_key(database, video_id))
    result = dict(status="disabled", reason=None, sampled_count=0, images=[])
    if manifest and manifest["status"] == "ready":
        blocks = group_segments(document.segments)
        for item in manifest["images"]:
            time = item["time"]
            # [start, end) makes an exact paragraph boundary belong to the following paragraph.
            block = next((i for i, b in enumerate(blocks) if b["start"] <= time < b["end"]), None)
            if block is None and blocks:
                block = min(range(len(blocks)), key=lambda i: max(blocks[i]["start"] - time, time - blocks[i]["end"], 0))
            result["images"].append({**item, "block_index": block,
                                     "url": f"/api/videos/{video_id}/screenshots/{item['index']}"})
        result.update(status="ready", sampled_count=manifest["sample_count"])
    else:
        task = database.screenshot_task(video_id)
        if task and task.status in {"queued", "running"}:
            result["status"] = "pending"
        elif manifest:
            result.update(status="failed", reason=manifest["error"])
        elif task:
            result.update(status="failed", reason=task.error_message or "配图已中断，文字稿已保留")
    return result


def write_illustrated_zip(settings: Settings, asset_key: str, document: TranscriptDocument, view: dict, destination: Path) -> None:
    if view["status"] != "ready":
        raise ValueError(view["reason"] or "暂无配图")
    illustrations: dict[int | None, list[str]] = defaultdict(list)
    manifest = {"video_id": document.video_id, "version_id": document.version_id,
                "interval_seconds": INTERVAL_SECONDS, "sample_count": view["sampled_count"], "images": []}
    with ZipFile(destination, "w") as archive:
        for image in view["images"]:
            index = image["index"]
            name = f"images/{index}.jpg"
            archive.write(screenshot_path(settings, asset_key, index), name)
            label = format_timestamp(image["time"])
            illustrations[image["block_index"]].append(f"![采样截图 {label}]({name})\n\n采样时间：{label}")
            manifest["images"].append({k: image[k] for k in ("index", "time", "block_index")} | {"file": name})
        md = transcript_filename(document.title, document.version_kind, extension="md", suffix=str(document.video_id))
        archive.writestr(md, render_markdown(document, illustrations=illustrations))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
