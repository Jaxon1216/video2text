from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from v2t.naming import transcript_filename, write_unique_text


def test_concurrent_transcripts_with_the_same_name_preserve_every_result(tmp_path: Path) -> None:
    path = tmp_path / "Redis-原始稿-2026-11-02_170107.txt"
    path.write_text("已有稿件", encoding="utf-8")
    contents = [f"转写结果 {number}" for number in range(8)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        outputs = list(executor.map(lambda text: write_unique_text(path, text), contents))

    assert len(set(outputs)) == len(contents)
    assert path.read_text(encoding="utf-8") == "已有稿件"
    assert [output.read_text(encoding="utf-8") for output in outputs] == contents
    assert (tmp_path / "Redis-原始稿-2026-11-02_170107-2.txt").exists()


def test_long_chinese_title_produces_a_valid_filename(tmp_path: Path) -> None:
    filename = transcript_filename(
        "Redis课程：/持久化与哨兵集群" * 30, "original", suffix="2026-11-02_170107",
    )
    path = write_unique_text(tmp_path / filename, "完整正文")

    assert len(filename.encode("utf-8")) <= 255
    assert path.parent == tmp_path
    assert path.read_text(encoding="utf-8") == "完整正文"
    assert filename.startswith("Redis课程")
    assert "-原始稿-" in filename
