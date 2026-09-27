import pytest

from v2t.formatters import (
    export_document,
    format_srt_timestamp,
    format_timestamp,
    group_segments,
    join_text,
    render_timestamped,
)
from v2t.models import TranscriptDocument
from v2t.segments import normalize_segments


def make_document(**overrides) -> TranscriptDocument:
    values = {
        "video_id": 1,
        "title": "线程池到底是怎么工作的",
        "platform": "douyin",
        "url": "https://www.douyin.com/video/1",
        "uploader": "蜡笔小浩",
        "duration": 95.2,
        "engine": "whisper",
        "model": "small",
        "transcript_source": "asr",
        "version_kind": "original",
        "text": "第一句第二句第三句",
        "segments": [
            {"start": 0.0, "end": 4.0, "text": "第一句"},
            {"start": 12.5, "end": 20.0, "text": "第二句"},
            {"start": 35.0, "end": 40.0, "text": "第三句"},
        ],
    }
    values.update(overrides)
    return TranscriptDocument(**values)


def test_normalize_segments_handles_whisper_and_millisecond_utterances() -> None:
    whisper = [{"id": 0, "start": 0.0, "end": 2.5, "text": " 你好 ", "tokens": [1]}, {"start": 3, "end": 4, "text": ""}]
    assert normalize_segments(whisper) == [{"start": 0.0, "end": 2.5, "text": "你好"}]

    utterances = [{"start_time": 1200, "end_time": 3400, "text": "hello"}, {"text": "no timing"}]
    assert normalize_segments(utterances, start_key="start_time", end_key="end_time", scale=0.001) == [
        {"start": 1.2, "end": 3.4, "text": "hello"}
    ]


def test_format_timestamps() -> None:
    assert format_timestamp(65.9) == "01:05"
    assert format_timestamp(3725) == "1:02:05"
    assert format_timestamp(5, with_hours=True) == "0:00:05"
    assert format_srt_timestamp(3725.042) == "01:02:05,042"


def test_join_text_uses_space_only_between_latin_words() -> None:
    assert join_text("你好", "世界") == "你好世界"
    assert join_text("use", "React") == "use React"
    assert join_text("用", "React") == "用React"


def test_group_segments_merges_into_blocks_without_mutating_input() -> None:
    segments = make_document().segments
    blocks = group_segments(segments, block_seconds=30)
    assert blocks == [
        {"start": 0.0, "end": 20.0, "text": "第一句第二句"},
        {"start": 35.0, "end": 40.0, "text": "第三句"},
    ]
    assert segments[0]["text"] == "第一句"


def test_render_timestamped_uses_hours_for_long_videos() -> None:
    assert render_timestamped([{"start": 3600.0, "end": 3601.0, "text": "晚了"}]) == "[1:00:00] 晚了"


def test_export_txt_is_timestamped_and_plain_is_not() -> None:
    document = make_document()
    assert export_document(document, "txt").content == "[00:00] 第一句第二句\n\n[00:35] 第三句\n"
    assert export_document(document, "plain").content == "第一句第二句第三句\n"


def test_export_markdown_contains_info_and_timestamps() -> None:
    content = export_document(make_document(), "md").content
    assert content.startswith("# 线程池到底是怎么工作的\n")
    assert "- 平台：抖音" in content
    assert "- 作者：蜡笔小浩" in content
    assert "- 时长：01:35" in content
    assert "**[00:35]** 第三句" in content


def test_export_srt_uses_raw_segments() -> None:
    content = export_document(make_document(), "srt").content
    assert content.startswith("1\n00:00:00,000 --> 00:00:04,000\n第一句\n\n2\n")
    assert content.count(" --> ") == 3


def test_export_without_segments_falls_back_to_text_and_rejects_srt() -> None:
    document = make_document(segments=[])
    assert export_document(document, "txt").content == "第一句第二句第三句\n"
    with pytest.raises(ValueError, match="no timestamps"):
        export_document(document, "srt")


def test_export_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="unsupported export format"):
        export_document(make_document(), "docx")
