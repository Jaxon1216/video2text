from pathlib import Path

import pytest

from b2t.inputs import parse_source, parse_source_list, safe_stem


def test_parse_bv_identifier() -> None:
    source = parse_source("BV1xx411c7XD")
    assert source.kind == "bilibili"
    assert source.bv == "BV1xx411c7XD"
    assert source.url == "https://www.bilibili.com/video/BV1xx411c7XD"


def test_parse_bilibili_url_keeps_page_information() -> None:
    source = parse_source("https://www.bilibili.com/video/BV1xx411c7XD?p=2")
    assert source.kind == "bilibili"
    assert source.url == "https://www.bilibili.com/video/BV1xx411c7XD?p=2"


def test_parse_bilibili_url_extracts_page_number() -> None:
    source = parse_source("https://www.bilibili.com/video/BV1xx411c7XD?p=2")
    assert source.kind == "bilibili"
    assert source.page == 2


def test_parse_bilibili_url_without_page_sets_page_to_none() -> None:
    source = parse_source("https://www.bilibili.com/video/BV1xx411c7XD")
    assert source.kind == "bilibili"
    assert source.page is None


def test_parse_bv_identifier_without_page_sets_page_to_none() -> None:
    source = parse_source("BV1xx411c7XD")
    assert source.kind == "bilibili"
    assert source.page is None


@pytest.mark.parametrize("page", ["0", "-1", "not-a-number"])
def test_parse_bilibili_url_ignores_invalid_page_number(page: str) -> None:
    source = parse_source(f"https://www.bilibili.com/video/BV1xx411c7XD?p={page}")
    assert source.kind == "bilibili"
    assert source.page is None


def test_parse_bilibili_share_text_keeps_extracted_url() -> None:
    source = parse_source("【讲清楚线程池】 https://www.bilibili.com/video/BV1xx411c7XD/?p=3&share_source=copy_web")
    assert source.kind == "bilibili"
    assert source.bv == "BV1xx411c7XD"
    assert source.url == "https://www.bilibili.com/video/BV1xx411c7XD/?p=3&share_source=copy_web"
    assert source.page == 3


def test_parse_douyin_share_text_with_short_link() -> None:
    share = "2.30 复制打开抖音，看看【蜡笔小浩的作品】线程池到底是怎么工作的 # 计算机 ... https://v.douyin.com/0CzNF8FbQ7s/ 09/29 vFU:/ :6pm B@T.Ym"
    source = parse_source(share)
    assert source.kind == "douyin"
    assert source.url == "https://v.douyin.com/0CzNF8FbQ7s/"
    assert source.video_id is None
    assert source.display_name == "douyin-share"
    assert source.raw_input == share


@pytest.mark.parametrize(
    "url",
    [
        "https://www.douyin.com/video/7671185082790530347",
        "https://www.iesdouyin.com/share/video/7671185082790530347/?from_ssr=1",
        "https://www.douyin.com/jingxuan?modal_id=7671185082790530347",
    ],
)
def test_parse_douyin_url_extracts_video_id(url: str) -> None:
    source = parse_source(url)
    assert source.kind == "douyin"
    assert source.video_id == "7671185082790530347"
    assert source.display_name == "douyin-7671185082790530347"


def test_parse_rejects_unknown_url() -> None:
    with pytest.raises(ValueError, match="Douyin share link"):
        parse_source("https://example.com/video/123")


def test_parse_local_audio_file(tmp_path: Path) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"wav")

    source = parse_source(str(audio_path))
    assert source.kind == "audio"
    assert source.path == audio_path.resolve()


def test_parse_source_list_ignores_blank_lines_and_comments() -> None:
    sources = parse_source_list(
        """
        BV1xx411c7XD

        # optional note
        https://www.bilibili.com/video/BV1yy411c7XD
        """
    )

    assert sources == [
        "BV1xx411c7XD",
        "https://www.bilibili.com/video/BV1yy411c7XD",
    ]


def test_safe_stem_removes_unsafe_characters() -> None:
    assert safe_stem("hello / world?") == "hello-world"
