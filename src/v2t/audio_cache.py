from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import uuid
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

from v2t.config import Settings
from v2t.inputs import extract_first_url, parse_source
from v2t.models import SourceRef


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _source_key(source: SourceRef, video_id: str | None = None) -> str | None:
    identifier = source.bv if source.kind == "bilibili" else source.video_id
    identifier = identifier or video_id
    if not identifier or source.kind not in ("bilibili", "douyin"):
        return None
    page = source.page or 1 if source.kind == "bilibili" else 0
    return f"{source.kind}:{identifier}:p{page}"


def _alias(value: str | None) -> str | None:
    url = extract_first_url(value or "")
    if not url:
        return None
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", parsed.query, ""))


def _valid_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as audio:
            if not (audio.getnchannels() == 1 and audio.getsampwidth() == 2
                    and audio.getframerate() == 16000 and audio.getnframes() > 0
                    and audio.getcomptype() == "NONE"):
                return False
            remaining = audio.getnframes()
            while remaining:
                frames = audio.readframes(min(remaining, 65536))
                if not frames:
                    return False
                remaining -= len(frames) // 2
            return True
    except (OSError, EOFError, wave.Error):
        return False


@dataclass(slots=True)
class CachedAudio:
    audio_path: Path
    title: str
    webpage_url: str | None
    download_metadata: dict
    video_path: Path | None = None


class AudioCache:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.audio_dir / "cache"

    @contextmanager
    def lock_for(self, source: SourceRef) -> Iterator[None]:
        key = _source_key(source) or _alias(source.url or source.raw_input) or source.raw_input
        lock_key = f"{self.root.resolve()}:{key}"
        with _LOCKS_GUARD:
            lock = _LOCKS.setdefault(lock_key, threading.Lock())
        with lock:
            yield

    def lookup(self, source: SourceRef) -> CachedAudio | None:
        key = _source_key(source)
        alias = _alias(source.url or source.raw_input)
        for manifest_path in self.root.glob("*.json"):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if data.get("kind") != source.kind:
                    continue
                if key is not None:
                    if data.get("key") != key:
                        continue
                elif alias is None or alias not in data.get("aliases", []):
                    continue
                audio_path = self.root / str(data["audio_file"])
                if not _valid_wav(audio_path):
                    continue
                video_text = data.get("video_path")
                video_path = Path(video_text) if video_text and Path(video_text).is_file() else None
                return CachedAudio(audio_path, str(data.get("title") or source.display_name),
                                   data.get("webpage_url"), dict(data.get("download_metadata") or {}), video_path)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def store(
        self, source: SourceRef, audio_path: Path, *, title: str,
        webpage_url: str | None, download_metadata: dict,
        video_path: Path | None = None, aliases: list[str] | None = None,
    ) -> CachedAudio | None:
        key = _source_key(source, str(download_metadata.get("id") or ""))
        if key is None or not _valid_wav(audio_path):
            return None
        self.root.mkdir(parents=True, exist_ok=True)
        stem = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        destination = self.root / f"{stem}.wav"
        manifest = self.root / f"{stem}.json"
        temp_audio = self.root / f"{stem}.{uuid.uuid4().hex}.part"
        temp_manifest = self.root / f"{stem}.{uuid.uuid4().hex}.json.part"
        alias_values = {_alias(value) for value in [source.url, source.raw_input, webpage_url, *(aliases or [])]}
        try:
            shutil.copyfile(audio_path, temp_audio)
            if not _valid_wav(temp_audio):
                return None
            os.replace(temp_audio, destination)
            data = {
                "key": key,
                "kind": source.kind,
                "audio_file": destination.name,
                "title": title,
                "webpage_url": webpage_url,
                "download_metadata": download_metadata,
                "video_path": str(video_path) if video_path else None,
                "aliases": sorted(value for value in alias_values if value),
            }
            temp_manifest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(temp_manifest, manifest)
        finally:
            temp_audio.unlink(missing_ok=True)
            temp_manifest.unlink(missing_ok=True)
        return CachedAudio(destination, title, webpage_url, download_metadata, video_path)

    def import_legacy(self, source: SourceRef, audio_path: Path, metadata: dict) -> CachedAudio | None:
        saved_source = metadata.get("source") or {}
        download = metadata.get("download") or {}
        if saved_source.get("kind") != source.kind or download.get("platform") != source.kind:
            return None
        saved_url = saved_source.get("url")
        if source.kind == "bilibili":
            try:
                saved_ref = parse_source(saved_url)
                if saved_ref.bv != saved_source.get("bv") or saved_ref.bv != str(download.get("id")):
                    return None
                if source.bv:
                    if source.bv != saved_ref.bv or (source.page or 1) != (saved_ref.page or 1):
                        return None
                elif _alias(source.raw_input) != _alias(str(saved_source.get("raw_input") or "")):
                    return None
            except (ValueError, TypeError):
                return None
        elif source.kind == "douyin":
            if source.video_id:
                if str(download.get("id")) != source.video_id:
                    return None
            elif _alias(source.raw_input) != _alias(str(saved_source.get("raw_input") or "")):
                return None
        try:
            if not audio_path.resolve().is_relative_to(self.settings.audio_dir.resolve()):
                return None
        except OSError:
            return None
        cache_source = source if _source_key(source) else SourceRef(
            raw_input=source.raw_input, kind=source.kind, display_name=source.display_name,
            url=source.url, bv=saved_source.get("bv"), page=parse_source(saved_url).page if source.kind == "bilibili" else None,
            video_id=str(download.get("id")),
        )
        with self.lock_for(source):
            return self.store(cache_source, audio_path, title=str(download.get("title") or source.display_name),
                              webpage_url=download.get("webpage_url") or saved_url,
                              download_metadata=download, aliases=[saved_url] if saved_url else [])
