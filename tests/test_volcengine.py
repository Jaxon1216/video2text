from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

from v2t.transcribers.volcengine import FLASH_URL, VolcengineFlashTranscriber


class FakeResponse:
    status_code = 200
    headers = {
        "X-Api-Status-Code": "20000000",
        "X-Api-Message": "OK",
    }

    def json(self) -> dict[str, Any]:
        return {
            "result": {
                "text": "hello",
                "utterances": [{"text": "hello"}],
            }
        }


def test_volcengine_flash_uses_api_key_as_user_uid(tmp_path, monkeypatch) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"audio")
    calls: list[dict[str, Any]] = []

    def post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
    ) -> FakeResponse:
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=post))

    result = VolcengineFlashTranscriber(api_key=" app-key ").transcribe(audio_path)

    assert result["text"] == "hello"
    assert calls[0]["url"] == FLASH_URL
    assert calls[0]["headers"]["X-Api-Key"] == "app-key"
    assert calls[0]["json"]["user"]["uid"] == "app-key"


def test_volcengine_flash_uses_legacy_app_key_as_user_uid(tmp_path, monkeypatch) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"audio")
    calls: list[dict[str, Any]] = []

    def post(
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
    ) -> FakeResponse:
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=post))

    transcriber = VolcengineFlashTranscriber(app_key=" legacy-app ", access_key=" access-token ")
    result = transcriber.transcribe(audio_path)

    assert result["text"] == "hello"
    assert calls[0]["headers"]["X-Api-App-Key"] == "legacy-app"
    assert calls[0]["headers"]["X-Api-Access-Key"] == "access-token"
    assert calls[0]["json"]["user"]["uid"] == "legacy-app"


def test_volcengine_flash_normalizes_utterance_timestamps(tmp_path, monkeypatch) -> None:
    audio_path = tmp_path / "sample.wav"
    audio_path.write_bytes(b"audio")

    class TimedResponse(FakeResponse):
        def json(self) -> dict[str, Any]:
            return {
                "result": {
                    "text": "你好世界",
                    "utterances": [
                        {"text": "你好", "start_time": 0, "end_time": 1500},
                        {"text": "世界", "start_time": 1500, "end_time": 2750},
                    ],
                }
            }

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=lambda url, **kwargs: TimedResponse()))

    result = VolcengineFlashTranscriber(api_key="key").transcribe(audio_path)

    assert result["segments"] == [
        {"start": 0.0, "end": 1.5, "text": "你好"},
        {"start": 1.5, "end": 2.75, "text": "世界"},
    ]
