from __future__ import annotations

from pathlib import Path

import pytest

from v2t.audio_cache import AudioCache
from v2t.config import Settings
from v2t.factory import build_downloaders
from v2t.inputs import parse_source
from v2t.models import REMOTE_SOURCE_KINDS
from test_audio_cache import write_wav


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLdemo&t=42",
    "http://youtube.com/watch?v=dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?si=demo",
    "分享：https://www.youtube.com/shorts/dQw4w9WgXcQ。",
    "https://www.youtube.com/live/dQw4w9WgXcQ?feature=share",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
])
def test_youtube_urls_are_normalized_without_network(url: str) -> None:
    source = parse_source(url)
    assert source.kind == "youtube"
    assert source.video_id == "dQw4w9WgXcQ"
    assert source.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert source.raw_input == url
    assert source.bv is None and source.page is None


@pytest.mark.parametrize("url", [
    "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
    "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/playlist?list=PLdemo",
    "https://youtube.com/@channel/live",
    "https://www.youtube.com/watch?v=short",
    "https://youtu.be/dQw4w9WgXcQextra",
    "https://www.youtube.com/watch?v=dQw4w9WgXc!",
    "https://youtu.be/dQw4w9WgXcQ/extra",
    "https://youtube.com/watch?v=BV1xx411c7XD",  # must not be misclassified as B站
])
def test_youtube_rejects_invalid_or_unsupported_links(url: str) -> None:
    with pytest.raises(ValueError):
        parse_source(url)


def test_youtube_factory_registers_remote_downloader() -> None:
    assert "youtube" in REMOTE_SOURCE_KINDS
    assert build_downloaders()["youtube"].name == "youtube"


def test_youtube_cache_reuses_across_url_forms_and_isolates_ids(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    cache = AudioCache(settings)
    audio = tmp_path / "input.wav"
    write_wav(audio)
    source = parse_source("https://youtu.be/dQw4w9WgXcQ")
    cached = cache.store(source, audio, title="demo", webpage_url=source.url,
                         download_metadata={"platform": "youtube", "id": source.video_id})
    assert cached is not None
    assert cache.lookup(parse_source("https://youtube.com/shorts/dQw4w9WgXcQ")).audio_path == cached.audio_path
    assert cache.lookup(parse_source("https://youtu.be/abcdefghijk")) is None


def test_youtube_import_legacy_checks_video_identity(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    settings.ensure_directories()
    source = parse_source("https://youtu.be/dQw4w9WgXcQ")
    audio = settings.audio_dir / "old.wav"
    write_wav(audio)
    metadata = {"source": {"kind": "youtube", "url": source.url, "video_id": source.video_id},
                "download": {"platform": "youtube", "id": "abcdefghijk"}}
    cache = AudioCache(settings)
    assert cache.import_legacy(source, audio, metadata) is None
    metadata["download"]["id"] = source.video_id
    assert cache.import_legacy(source, audio, metadata) is not None
