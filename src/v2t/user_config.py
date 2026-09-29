from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from v2t.config import Settings
from v2t.model_catalog import DEFAULT_FASTER_WHISPER_MODEL, resolve_model
from v2t.i18n import DEFAULT_LANGUAGE, normalize_language

ALL_PROVIDERS = ("faster-whisper", "whisper", "qwen3-asr", "sensevoice", "volcengine")
DEFAULT_PROVIDER = "faster-whisper"
ALL_FEATURES = ("web", "server")


@dataclass(slots=True)
class FasterWhisperConfig:
    language: str = "zh"
    device: str = "auto"
    compute_type: str = "auto"
    beam_size: int = 5
    vad_filter: bool = True
    download_root: str = ""


@dataclass(slots=True)
class SenseVoiceConfig:
    model_dir: str = ""
    language: str = "auto"
    use_itn: bool = True


@dataclass(slots=True)
class VolcengineConfig:
    api_key: str = ""
    app_key: str = ""
    access_key: str = ""
    resource_id: str = "volc.bigasr.auc_turbo"
    model_name: str = "bigmodel"
    use_itn: bool = True


@dataclass(slots=True)
class AppConfig:
    language: str = DEFAULT_LANGUAGE
    enabled_providers: list[str] = field(default_factory=lambda: [DEFAULT_PROVIDER])
    enabled_features: list[str] = field(default_factory=lambda: ["web"])
    default_provider: str = DEFAULT_PROVIDER
    default_model: str = DEFAULT_FASTER_WHISPER_MODEL
    prefer_subtitles: bool = True
    faster_whisper: FasterWhisperConfig = field(default_factory=FasterWhisperConfig)
    sensevoice: SenseVoiceConfig = field(default_factory=SenseVoiceConfig)
    volcengine: VolcengineConfig = field(default_factory=VolcengineConfig)

    @classmethod
    def load(cls, settings: Settings) -> "AppConfig":
        if not settings.config_path.exists():
            return cls()

        data = json.loads(settings.config_path.read_text(encoding="utf-8"))
        enabled = data.get("enabled_providers")
        if enabled is None:
            # backwards compat: old configs only had default_provider
            enabled = [data.get("default_provider", DEFAULT_PROVIDER)]
        features = [name for name in data.get("enabled_features", ["web"]) if name in ALL_FEATURES]
        return cls(
            language=normalize_language(data.get("language")),
            enabled_providers=enabled,
            enabled_features=features,
            default_provider=data.get("default_provider", DEFAULT_PROVIDER),
            default_model=data.get("default_model", "small"),
            prefer_subtitles=bool(data.get("prefer_subtitles", True)),
            faster_whisper=FasterWhisperConfig(**data.get("faster_whisper", {})),
            sensevoice=SenseVoiceConfig(**data.get("sensevoice", {})),
            volcengine=VolcengineConfig(**data.get("volcengine", {})),
        )

    def save(self, settings: Settings) -> None:
        settings.ensure_directories()
        settings.config_path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def apply_env_overrides(config: AppConfig) -> AppConfig:
    """Runtime-only overrides (Docker / CI); never written back to config.json."""
    provider = os.getenv("V2T_DEFAULT_PROVIDER", "").strip()
    model = os.getenv("V2T_DEFAULT_MODEL", "").strip()
    if provider or model:
        selected_provider = provider or config.default_provider
        config.default_model = resolve_model(config, selected_provider, model)
        config.default_provider = selected_provider
        if selected_provider not in config.enabled_providers:
            config.enabled_providers.append(selected_provider)
    api_key = os.getenv("V2T_VOLCENGINE_API_KEY", "").strip()
    if api_key:
        config.volcengine.api_key = api_key
        if "volcengine" not in config.enabled_providers:
            config.enabled_providers.append("volcengine")
    prefer_subtitles = os.getenv("V2T_PREFER_SUBTITLES", "").strip().lower()
    if prefer_subtitles:
        config.prefer_subtitles = prefer_subtitles in {"1", "true", "yes", "on"}
    return config
