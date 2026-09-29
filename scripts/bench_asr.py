"""Compare ASR engines on hand-checked Chinese tech-video clips.

Case layout (one directory, files share a stem):
    <name>.wav|.m4a|.mp3|.mp4   audio or video clip (3-5 minutes works well)
    <name>.ref.txt              hand-corrected reference transcript (required)
    <name>.terms.txt            optional: technical terms, one per line (used for term recall and the prompt variant)
    <name>.subtitle.srt         optional: platform subtitles (e.g. Bilibili AI subtitles) scored as a baseline

Example:
    uv run python scripts/bench_asr.py bench/ \\
        --engine faster-whisper:small --engine faster-whisper:large-v3-turbo --engine volcengine \\
        --prompt-variants --price volcengine=2.3
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from v2t.config import Settings  # noqa: E402
from v2t.evaluation import character_error_rate, term_recall  # noqa: E402
from v2t.factory import build_transcriber  # noqa: E402
from v2t.segments import join_text, parse_srt  # noqa: E402
from v2t.user_config import AppConfig  # noqa: E402

MEDIA_SUFFIXES = (".wav", ".m4a", ".mp3", ".flac", ".aac", ".ogg", ".mp4", ".mkv", ".mov", ".webm")
LOCAL_PROVIDERS = {"faster-whisper", "whisper", "qwen3-asr", "sensevoice"}


@dataclass
class Case:
    name: str
    media: Path
    reference: str
    terms: list[str] = field(default_factory=list)
    subtitle: Path | None = None
    wav: Path | None = None
    duration: float = 0.0


@dataclass
class Row:
    case: str
    engine: str
    variant: str
    cer: float | None
    term_hits: int
    term_total: int
    missed_terms: list[str]
    load_seconds: float
    transcribe_seconds: float
    rtf: float | None
    cost_yuan: float | None
    error: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cases_dir", type=Path)
    parser.add_argument("--engine", action="append", default=[], help="provider[:model], repeatable (default: faster-whisper:small)")
    parser.add_argument("--prompt-variants", action="store_true", help="also run each engine with the terms as prompt")
    parser.add_argument("--price", action="append", default=[], help="provider=yuan_per_audio_hour for cloud cost, repeatable")
    parser.add_argument("--workspace", type=Path, default=None, help="workspace whose config.json holds API keys (default: .v2t)")
    parser.add_argument("--out", type=Path, default=None, help="report directory (default: <cases_dir>/reports/<timestamp>)")
    args = parser.parse_args()

    cases = load_cases(args.cases_dir)
    if not cases:
        print(f"no cases found in {args.cases_dir} (need <name>.<audio> + <name>.ref.txt)", file=sys.stderr)
        return 1

    out_dir = args.out or args.cases_dir / "reports" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = args.cases_dir / ".cache"
    cache_dir.mkdir(exist_ok=True)
    for case in cases:
        case.wav = to_wav(case.media, cache_dir)
        case.duration = probe_duration(case.wav)

    settings = Settings.from_workspace(args.workspace)
    config = AppConfig.load(settings)
    prices = parse_prices(args.price)
    engines = args.engine or ["faster-whisper:small"]
    rows: list[Row] = []

    for spec in engines:
        provider, _, model = spec.partition(":")
        for case in cases:
            variants = [("plain", None)]
            if args.prompt_variants and case.terms:
                variants.append(("prompt", "、".join(case.terms) + "。"))
            for variant, prompt in variants:
                row = run_engine(case, provider, model, variant, prompt, config, prices, out_dir)
                rows.append(row)
                print(format_progress(row))

    for case in cases:
        if case.subtitle:
            rows.append(score_subtitle(case, out_dir))
            print(format_progress(rows[-1]))

    (out_dir / "results.json").write_text(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")
    report = render_report(cases, rows)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"\nreport: {out_dir / 'report.md'}")
    print(report)
    return 0


def load_cases(directory: Path) -> list[Case]:
    cases = []
    for reference in sorted(directory.glob("*.ref.txt")):
        name = reference.name[: -len(".ref.txt")]
        media = next((directory / f"{name}{suffix}" for suffix in MEDIA_SUFFIXES if (directory / f"{name}{suffix}").exists()), None)
        if media is None:
            print(f"skip {name}: no audio file next to {reference.name}", file=sys.stderr)
            continue
        terms_path = directory / f"{name}.terms.txt"
        subtitle = directory / f"{name}.subtitle.srt"
        cases.append(
            Case(
                name=name,
                media=media,
                reference=reference.read_text(encoding="utf-8"),
                terms=terms_path.read_text(encoding="utf-8").splitlines() if terms_path.exists() else [],
                subtitle=subtitle if subtitle.exists() else None,
            )
        )
    return cases


def to_wav(media: Path, cache_dir: Path) -> Path:
    target = cache_dir / f"{media.stem}.16k.wav"
    if target.exists() and target.stat().st_mtime >= media.stat().st_mtime:
        return target
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required")
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(media), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(target)],
        check=True,
    )
    return target


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def parse_prices(items: list[str]) -> dict[str, float]:
    prices = {}
    for item in items:
        provider, _, value = item.partition("=")
        prices[provider.strip()] = float(value)
    return prices


def run_engine(
    case: Case,
    provider: str,
    model: str,
    variant: str,
    prompt: str | None,
    config: AppConfig,
    prices: dict[str, float],
    out_dir: Path,
) -> Row:
    engine = f"{provider}:{model}" if model else provider
    load_seconds = 0.0
    try:
        transcriber = build_transcriber(config=config, provider=provider, model=model or None)
        ensure_model = getattr(transcriber, "_ensure_model", None)
        if callable(ensure_model):
            started = time.perf_counter()
            ensure_model()
            load_seconds = time.perf_counter() - started
        started = time.perf_counter()
        assert case.wav is not None
        result = transcriber.transcribe(case.wav, prompt=prompt)
        elapsed = time.perf_counter() - started
    except Exception as exc:  # noqa: BLE001 - report the failure in the table and keep going
        return Row(case.name, engine, variant, None, 0, len(case.terms), [], load_seconds, 0.0, None, None, error=str(exc)[:200])

    text = result.get("text", "")
    (out_dir / f"{case.name}__{engine.replace(':', '_')}__{variant}.txt").write_text(text, encoding="utf-8")
    hits, total, missed = term_recall(case.terms, text)
    cost = 0.0 if provider in LOCAL_PROVIDERS else (prices[provider] * case.duration / 3600 if provider in prices else None)
    return Row(
        case=case.name,
        engine=engine,
        variant=variant,
        cer=character_error_rate(case.reference, text),
        term_hits=hits,
        term_total=total,
        missed_terms=missed,
        load_seconds=round(load_seconds, 2),
        transcribe_seconds=round(elapsed, 2),
        rtf=round(elapsed / case.duration, 3) if case.duration else None,
        cost_yuan=round(cost, 4) if cost is not None else None,
    )


def score_subtitle(case: Case, out_dir: Path) -> Row:
    assert case.subtitle is not None
    text = ""
    for segment in parse_srt(case.subtitle.read_text(encoding="utf-8")):
        text = join_text(text, segment["text"])
    (out_dir / f"{case.name}__platform-subtitle.txt").write_text(text, encoding="utf-8")
    hits, total, missed = term_recall(case.terms, text)
    return Row(case.name, "platform-subtitle", "plain", character_error_rate(case.reference, text), hits, total, missed, 0.0, 0.0, 0.0, 0.0)


def format_progress(row: Row) -> str:
    if row.error:
        return f"[{row.case}] {row.engine} ({row.variant}) FAILED: {row.error}"
    return f"[{row.case}] {row.engine} ({row.variant}) CER={row.cer:.1%} terms={row.term_hits}/{row.term_total} rtf={row.rtf}"


def render_report(cases: list[Case], rows: list[Row]) -> str:
    lines = [
        f"# ASR 评测报告（{datetime.now():%Y-%m-%d %H:%M}）",
        "",
        "样本：" + "；".join(f"{case.name}（{case.duration:.0f}s，{len(case.terms)} 个术语）" for case in cases),
        "",
        "CER 越低越好（已去掉标点和空白，全角转半角）；RTF = 识别耗时 / 音频时长，越低越快，不含模型加载；成本按 `--price` 的每小时单价估算。",
        "",
        "## 汇总（按引擎 + 变体平均）",
        "",
        "| 引擎 | 变体 | 平均 CER | 术语召回 | 平均 RTF | 模型加载(s) | 成本(元) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    groups: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        groups.setdefault((row.engine, row.variant), []).append(row)
    for (engine, variant), group in groups.items():
        ok = [row for row in group if not row.error]
        if not ok:
            lines.append(f"| {engine} | {variant} | 失败：{group[0].error} | | | | |")
            continue
        cer = sum(row.cer or 0 for row in ok) / len(ok)
        hits = sum(row.term_hits for row in ok)
        total = sum(row.term_total for row in ok)
        rtfs = [row.rtf for row in ok if row.rtf is not None]
        costs = [row.cost_yuan for row in ok if row.cost_yuan is not None]
        cost = f"{sum(costs):.3f}" if len(costs) == len(ok) else "需填写单价"
        rtf = f"{sum(rtfs) / len(rtfs):.3f}" if rtfs else "-"
        load = f"{max(row.load_seconds for row in ok):.1f}"
        lines.append(f"| {engine} | {variant} | {cer:.1%} | {hits}/{total} | {rtf} | {load} | {cost} |")
    lines += ["", "## 明细", "", "| 样本 | 引擎 | 变体 | CER | 术语 | 漏掉的术语 | 识别耗时(s) | RTF |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        if row.error:
            lines.append(f"| {row.case} | {row.engine} | {row.variant} | 失败 | | {row.error} | | |")
            continue
        lines.append(
            f"| {row.case} | {row.engine} | {row.variant} | {row.cer:.1%} | {row.term_hits}/{row.term_total} | "
            f"{'、'.join(row.missed_terms) or '-'} | {row.transcribe_seconds} | {row.rtf} |"
        )
    lines += ["", "各引擎的识别原文保存在本目录的 `*.txt`，可以直接对比。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
