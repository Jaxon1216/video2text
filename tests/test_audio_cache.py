from __future__ import annotations

import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from v2t.audio_cache import AudioCache
from v2t.config import Settings
from v2t.inputs import parse_source


def write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 160)


def test_cache_reuses_complete_audio_but_separates_bilibili_pages(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    cache = AudioCache(settings)
    audio = tmp_path / "original.wav"
    write_wav(audio)
    source = parse_source("https://www.bilibili.com/video/BV1xx411c7XD?p=1")
    cached = cache.store(source, audio, title="demo", webpage_url=source.url,
                         download_metadata={"id": source.bv, "platform": "bilibili"})
    assert cached.audio_path.exists()
    assert cache.lookup(parse_source("BV1xx411c7XD")).audio_path == cached.audio_path
    assert cache.lookup(parse_source("https://www.bilibili.com/video/BV1xx411c7XD?p=2")) is None


def test_cache_rejects_corrupt_wav_and_incomplete_manifest(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    cache = AudioCache(settings)
    source = parse_source("BV1xx411c7XD")
    bad_audio = tmp_path / "bad.wav"
    bad_audio.write_bytes(b"not wav")
    assert cache.store(source, bad_audio, title="demo", webpage_url=source.url,
                       download_metadata={"id": source.bv}) is None
    assert cache.lookup(source) is None
    write_wav(bad_audio)
    cached = cache.store(source, bad_audio, title="demo", webpage_url=source.url,
                         download_metadata={"id": source.bv})
    cached.audio_path.write_bytes(b"broken")
    assert cache.lookup(source) is None


def test_cache_rejects_truncated_wav_payload(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    cache = AudioCache(settings)
    source = parse_source("BV1xx411c7XD")
    audio = tmp_path / "audio.wav"
    write_wav(audio)
    cached = cache.store(source, audio, title="demo", webpage_url=source.url,
                         download_metadata={"id": source.bv})
    raw = cached.audio_path.read_bytes()
    cached.audio_path.write_bytes(raw[:-12])
    assert cache.lookup(source) is None
    assert cache.store(source, cached.audio_path, title="demo", webpage_url=source.url,
                       download_metadata={"id": source.bv}) is None


def test_cache_imports_matching_legacy_audio(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    audio = settings.audio_dir / "old-title.wav"
    write_wav(audio)
    source = parse_source("https://www.bilibili.com/video/BV1xx411c7XD?p=2")
    metadata = {"source": {"kind": "bilibili", "bv": source.bv, "url": source.url},
                "download": {"platform": "bilibili", "id": source.bv, "title": "old-title"}}
    cache = AudioCache(settings)
    imported = cache.import_legacy(source, audio, metadata)
    assert imported is not None
    assert cache.lookup(source).audio_path.exists()
    assert cache.lookup(parse_source("BV1xx411c7XD")) is None
    wrong = parse_source("BV1yy411c7XD")
    assert cache.import_legacy(wrong, audio, metadata) is None


def test_cache_imports_legacy_audio_from_bilibili_short_link(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    audio = settings.audio_dir / "old-title.wav"
    write_wav(audio)
    short_source = parse_source("https://b23.tv/demo123")
    metadata = {
        "source": {"raw_input": short_source.raw_input, "kind": "bilibili", "bv": "BV1xx411c7XD", "url": "https://www.bilibili.com/video/BV1xx411c7XD?p=2"},
        "download": {"platform": "bilibili", "id": "BV1xx411c7XD", "title": "old-title"},
    }
    cache = AudioCache(settings)
    assert cache.import_legacy(short_source, audio, metadata) is not None
    assert cache.lookup(short_source) is not None
    assert cache.lookup(parse_source("https://www.bilibili.com/video/BV1xx411c7XD?p=2")) is not None


def test_cache_source_lock_serializes_same_source(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    cache = AudioCache(settings)
    source = parse_source("BV1xx411c7XD")
    calls = []
    calls_lock = Lock()

    def prepare():
        with cache.lock_for(source):
            if cache.lookup(source) is None:
                with calls_lock:
                    calls.append(1)
                audio = tmp_path / "input.wav"
                write_wav(audio)
                cache.store(source, audio, title="demo", webpage_url=source.url,
                            download_metadata={"id": source.bv})

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: prepare(), range(2)))
    assert len(calls) == 1
