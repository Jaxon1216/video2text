from __future__ import annotations

import sys
import wave
from threading import Event
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from v2t.config import Settings
from v2t.factory import build_transcriber, clear_transcriber_cache
from v2t.model_catalog import QWEN3_ASR_MODEL, model_choices, resolve_model
from v2t.progress import ProgressReporter, TaskCancelled
from v2t.transcribers.qwen3_asr_local import Qwen3ASRTranscriber
from v2t.user_config import AppConfig


def test_qwen_catalog_only_offers_17b() -> None:
    config = AppConfig()
    assert resolve_model(config, "qwen3-asr") == QWEN3_ASR_MODEL
    assert [item["id"] for item in model_choices(config, "qwen3-asr")] == [QWEN3_ASR_MODEL]
    assert QWEN3_ASR_MODEL == "Qwen/Qwen3-ASR-1.7B"
    with pytest.raises(ValueError, match="1.7B"):
        Qwen3ASRTranscriber(model="Qwen/Qwen3-ASR-0.6B")
    with pytest.raises(ValueError, match="1.7B"):
        resolve_model(config, "qwen3-asr", "Qwen/Qwen3-ASR-0.6B")


def test_existing_qwen_default_switches_to_17b_without_rewriting_config(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    old = AppConfig(default_provider="qwen3-asr", default_model="Qwen/Qwen3-ASR-0.6B",
                    enabled_providers=["qwen3-asr"])
    old.save(settings)
    previous = settings.config_path.read_text(encoding="utf-8")
    loaded = AppConfig.load(settings)
    assert loaded.default_model == "Qwen/Qwen3-ASR-1.7B"
    assert settings.config_path.read_text(encoding="utf-8") == previous


def test_qwen_factory_shares_model_instance() -> None:
    clear_transcriber_cache()
    config = AppConfig()
    first = build_transcriber(config=config, provider="qwen3-asr")
    second = build_transcriber(config=config, provider="qwen3-asr", model=QWEN3_ASR_MODEL)
    assert isinstance(first, Qwen3ASRTranscriber)
    assert first is second
    clear_transcriber_cache()


def test_qwen_load_allows_full_length_video_output(monkeypatch) -> None:
    calls = []

    class FakeQwenModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            calls.append((args, kwargs))
            return object()

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(float32="float32"))
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(Qwen3ASRModel=FakeQwenModel))
    Qwen3ASRTranscriber()._load_model("cpu")
    assert calls[0][1]["max_new_tokens"] >= 2048


def test_qwen_transcriber_passes_context_and_returns_plain_text(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 160)
    calls = []

    class FakeModel:
        def transcribe(self, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(text=" 中文结果。 ", language="Chinese")]

    transcriber = Qwen3ASRTranscriber()
    transcriber._model = FakeModel()
    transcriber._device = "mps"
    result = transcriber.transcribe(audio, prompt="线程池")
    assert result == {"text": "中文结果。", "segments": [], "language": "zh", "model": QWEN3_ASR_MODEL}
    assert calls[0]["context"] == "线程池"
    assert calls[0]["language"] == "Chinese"
    assert calls[0]["return_time_stamps"] is False
    assert isinstance(calls[0]["audio"], tuple)
    assert calls[0]["audio"][1] == 16000
    assert np.asarray(calls[0]["audio"][0]).shape == (160,)


def test_qwen_transcribes_long_audio_in_chunks(tmp_path: Path, monkeypatch) -> None:
    from v2t.transcribers import qwen3_asr_local

    monkeypatch.setattr(qwen3_asr_local, "MAX_CHUNK_SECONDS", 1, raising=False)
    transcriber = Qwen3ASRTranscriber()
    monkeypatch.setattr(transcriber, "_read_audio", lambda path: (np.zeros(40000, dtype=np.float32), 16000))
    calls = []

    class FakeModel:
        def transcribe(self, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(text=f"片段{len(calls)}", language="Chinese")]

    transcriber._model = FakeModel()
    result = transcriber.transcribe(tmp_path / "long.wav")
    assert result["text"] == "片段1\n片段2\n片段3"
    assert [len(call["audio"][0]) for call in calls] == [16000, 16000, 8000]


def test_qwen_checks_cancellation_between_chunks(tmp_path: Path, monkeypatch) -> None:
    from v2t.transcribers import qwen3_asr_local

    monkeypatch.setattr(qwen3_asr_local, "MAX_CHUNK_SECONDS", 1, raising=False)
    transcriber = Qwen3ASRTranscriber()
    monkeypatch.setattr(transcriber, "_read_audio", lambda path: (np.zeros(32000, dtype=np.float32), 16000))
    cancel_event = Event()
    calls = []

    class FakeModel:
        def transcribe(self, **kwargs):
            calls.append(kwargs)
            cancel_event.set()
            return [SimpleNamespace(text="片段", language="Chinese")]

    transcriber._model = FakeModel()
    with pytest.raises(TaskCancelled):
        transcriber.transcribe(tmp_path / "long.wav", progress=ProgressReporter("demo", cancel_event=cancel_event))
    assert len(calls) == 1


def test_qwen_mps_failure_retries_on_cpu(tmp_path: Path, monkeypatch) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"dummy")
    modes = []

    class FailingModel:
        def transcribe(self, **kwargs):
            raise RuntimeError("MPS backend does not support this operation")

    class WorkingModel:
        def transcribe(self, **kwargs):
            return [SimpleNamespace(text="成功", language="Chinese")]

    transcriber = Qwen3ASRTranscriber()
    transcriber._model = FailingModel()
    transcriber._device = "mps"
    monkeypatch.setattr(transcriber, "_read_audio", lambda path: (np.zeros(10, dtype=np.float32), 16000))
    monkeypatch.setattr(transcriber, "_empty_mps_cache", lambda: None)

    def load_model(mode):
        modes.append(mode)
        transcriber._device = mode
        transcriber._model = WorkingModel()
        return transcriber._model

    monkeypatch.setattr(transcriber, "_load_model", load_model)
    assert transcriber.transcribe(audio)["text"] == "成功"
    assert modes == ["cpu"]
