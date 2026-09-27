from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

from v2t.factory import build_transcriber, clear_transcriber_cache
from v2t.progress import ProgressReporter
from v2t.tasks import task_workers_from_env
from v2t.transcribers.faster_whisper_local import (
    CHINESE_STYLE_PROMPT,
    FasterWhisperTranscriber,
    build_initial_prompt,
    resolve_device,
)
from v2t.user_config import AppConfig


class FakeWhisperModel:
    instances: list["FakeWhisperModel"] = []

    def __init__(self, model_name: str, *, device: str, compute_type: str, download_root: str | None) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.calls: list[dict[str, Any]] = []
        FakeWhisperModel.instances.append(self)

    def transcribe(self, audio: str, **kwargs: Any):
        self.calls.append({"audio": audio, **kwargs})
        segments = iter(
            [
                SimpleNamespace(start=0.0, end=2.0, text=" 线程池是什么"),
                SimpleNamespace(start=2.0, end=2.5, text="  "),
                SimpleNamespace(start=2.5, end=5.0, text=" 用 Java 举例"),
            ]
        )
        return segments, SimpleNamespace(duration=5.0, language="zh")


def install_fake_module(monkeypatch) -> None:
    FakeWhisperModel.instances = []
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeWhisperModel))


def test_transcribe_returns_segments_text_and_progress(tmp_path, monkeypatch) -> None:
    install_fake_module(monkeypatch)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"wav")
    snapshots = []

    result = FasterWhisperTranscriber(model="small", device="cpu").transcribe(
        audio,
        prompt="线程池 ThreadPoolExecutor",
        progress=ProgressReporter("t", callback=snapshots.append),
    )

    assert result["text"] == "线程池是什么用 Java 举例"
    assert result["segments"] == [
        {"start": 0.0, "end": 2.0, "text": "线程池是什么"},
        {"start": 2.5, "end": 5.0, "text": "用 Java 举例"},
    ]
    assert result["language"] == "zh"
    model = FakeWhisperModel.instances[0]
    assert model.compute_type == "int8"
    call = model.calls[0]
    assert call["language"] == "zh"
    assert call["vad_filter"] is True
    assert call["initial_prompt"] == CHINESE_STYLE_PROMPT + "线程池 ThreadPoolExecutor"
    assert snapshots[-1].percent == 0.9


def test_model_is_loaded_once_per_transcriber(tmp_path, monkeypatch) -> None:
    install_fake_module(monkeypatch)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"wav")
    transcriber = FasterWhisperTranscriber(device="cpu")

    transcriber.transcribe(audio)
    transcriber.transcribe(audio)

    assert len(FakeWhisperModel.instances) == 1


def test_model_load_failure_suggests_hf_mirror(tmp_path, monkeypatch) -> None:
    def broken_model(*args: Any, **kwargs: Any) -> None:
        raise OSError("ConnectTimeout")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=broken_model))
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"wav")

    try:
        FasterWhisperTranscriber(device="cpu").transcribe(audio)
    except RuntimeError as exc:
        assert "HF_ENDPOINT=https://hf-mirror.com" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_build_initial_prompt_only_adds_chinese_style_for_zh() -> None:
    assert build_initial_prompt(None, "zh") == CHINESE_STYLE_PROMPT
    assert build_initial_prompt("React hooks", None) == "React hooks"
    assert build_initial_prompt("  ", "en") is None


def test_resolve_device_picks_int8_on_cpu_and_float16_on_cuda() -> None:
    assert resolve_device("cpu", "auto") == ("cpu", "int8")
    assert resolve_device("cuda", "auto") == ("cuda", "float16")
    assert resolve_device("cpu", "float32") == ("cpu", "float32")


def test_factory_caches_transcribers_per_provider_model_and_options() -> None:
    clear_transcriber_cache()
    config = AppConfig()

    first = build_transcriber(config=config, provider="faster-whisper", model="small")
    again = build_transcriber(config=config, provider="faster-whisper", model="small")
    other_model = build_transcriber(config=config, provider="faster-whisper", model="medium")
    config.faster_whisper.language = "auto"
    other_options = build_transcriber(config=config, provider="faster-whisper", model="small")

    assert first is again
    assert other_model is not first
    assert other_options is not first
    assert isinstance(first, FasterWhisperTranscriber)
    clear_transcriber_cache()


def test_default_config_uses_faster_whisper() -> None:
    config = AppConfig()
    assert config.default_provider == "faster-whisper"
    assert config.enabled_providers == ["faster-whisper"]


def test_task_workers_from_env(monkeypatch) -> None:
    monkeypatch.delenv("V2T_TASK_WORKERS", raising=False)
    assert task_workers_from_env() == 1
    monkeypatch.setenv("V2T_TASK_WORKERS", "3")
    assert task_workers_from_env() == 3
    monkeypatch.setenv("V2T_TASK_WORKERS", "0")
    assert task_workers_from_env() == 1
    monkeypatch.setenv("V2T_TASK_WORKERS", "many")
    assert task_workers_from_env() == 1
