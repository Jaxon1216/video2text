from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from v2t.user_config import AppConfig

FASTER_WHISPER_MODELS = ("large-v3-turbo", "large-v3", "medium", "small", "base", "tiny")
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large")
DEFAULT_FASTER_WHISPER_MODEL = "large-v3-turbo"


def provider_default_model(config: AppConfig, provider: str) -> str:
    if provider == "faster-whisper":
        return DEFAULT_FASTER_WHISPER_MODEL
    if provider == "whisper":
        return "small"
    if provider == "sensevoice":
        return config.sensevoice.model_dir.strip()
    if provider == "volcengine":
        return config.volcengine.model_name.strip()
    raise ValueError(f"Unsupported provider: {provider}")


def resolve_model(config: AppConfig, provider: str, model: str | None = None) -> str:
    """An explicit model wins; a different provider never inherits the global model."""
    fallback = provider_default_model(config, provider)
    if model and model.strip():
        return model.strip()
    if provider == config.default_provider and config.default_model.strip():
        return config.default_model.strip()
    return fallback


def model_choices(config: AppConfig, provider: str) -> list[dict[str, str]]:
    models = list(FASTER_WHISPER_MODELS if provider == "faster-whisper" else WHISPER_MODELS if provider == "whisper" else ())
    if provider in ("sensevoice", "volcengine"):
        configured = provider_default_model(config, provider)
        if configured:
            models.append(configured)
    default = resolve_model(config, provider)
    if default and default not in models:
        models.append(default)
    return [{"id": model, "label": model} for model in models]


def model_description_key(model: str) -> str:
    suffix = {"large-v3": "large", "large-v3-turbo": "large_v3_turbo"}.get(model, model)
    return f"whisper_model_{suffix}"
