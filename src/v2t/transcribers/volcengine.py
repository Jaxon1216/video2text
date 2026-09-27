from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

from v2t.i18n import dependency_sync_guidance
from v2t.transcribers.base import Transcriber

# 极速版API：同步返回，支持base64音频
FLASH_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"


class VolcengineFlashTranscriber(Transcriber):
    name = "volcengine"

    def __init__(
        self,
        *,
        api_key: str = "",
        app_key: str = "",
        access_key: str = "",
        resource_id: str = "volc.bigasr.auc_turbo",
        model_name: str = "bigmodel",
        use_itn: bool = True,
    ) -> None:
        self.api_key = api_key.strip()
        self.app_key = app_key.strip()
        self.access_key = access_key.strip()
        self.resource_id = resource_id
        self.model_name = model_name
        self.use_itn = use_itn

    def transcribe(
        self,
        audio_path: Path,
        *,
        prompt: str | None = None,
        progress=None,
    ) -> dict[str, Any]:
        if progress is not None:
            progress.running("transcribing", message="transcribing", indeterminate=True)
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError(
                "Volcengine support is not installed. "
                f"{dependency_sync_guidance('en-US')}"
            ) from exc

        auth_headers = self._build_headers()
        if not auth_headers:
            raise RuntimeError("Volcengine provider requires API credentials. Run `video2text bootstrap` first.")

        task_id = str(uuid.uuid4())

        headers = {
            **auth_headers,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": task_id,
            "X-Api-Sequence": "-1",
        }

        audio_data = base64.b64encode(audio_path.read_bytes()).decode("utf-8")
        payload: dict[str, Any] = {
            "user": {"uid": self._user_uid()},
            "audio": {
                "format": audio_path.suffix.lstrip(".").lower() or "wav",
                "data": audio_data,
            },
            "request": {
                "model_name": self.model_name,
                "show_utterances": True,
                "enable_itn": self.use_itn,
            },
        }

        if prompt:
            payload["request"]["context"] = prompt

        response = requests.post(
            FLASH_URL,
            headers=headers,
            json=payload,
            timeout=300,
        )

        # 检查 HTTP 层面错误
        if response.status_code != 200:
            response.raise_for_status()

        # 检查业务状态码（在 response header 中）
        status_code = response.headers.get("X-Api-Status-Code", "")
        status_msg = response.headers.get("X-Api-Message", "")

        if status_code == "20000003":
            # 静音音频
            return {
                "text": "",
                "segments": [],
                "language": None,
                "model": self.model_name,
                "raw_response": {},
            }

        if status_code != "20000000":
            raise RuntimeError(
                f"Volcengine recognize failed: [{status_code}] {status_msg}"
            )

        data = response.json()
        result = data.get("result", {})
        utterances = result.get("utterances") or []
        text = "\n".join(item.get("text", "") for item in utterances if item.get("text")).strip()
        if not text:
            text = (result.get("text") or "").strip()

        return {
            "text": text,
            "segments": utterances,
            "language": None,
            "model": self.model_name,
            "raw_response": data,
        }

    def _build_headers(self) -> dict[str, str]:
        if self.api_key:
            return {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        if self.app_key and self.access_key:
            return {
                "X-Api-App-Key": self.app_key,
                "X-Api-Access-Key": self.access_key,
                "Content-Type": "application/json",
            }
        return {}

    def _user_uid(self) -> str:
        return self.api_key or self.app_key
