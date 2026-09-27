from pathlib import Path

from v2t.config import Settings
from v2t.user_config import AppConfig


def test_app_config_round_trip(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    config = AppConfig(
        default_provider="sensevoice",
        default_model="C:/models/sensevoice-small",
        language="en-US",
    )
    config.enabled_features = ["web", "server"]
    config.sensevoice.model_dir = "C:/models/sensevoice-small"
    config.volcengine.api_key = "secret"
    config.save(settings)

    loaded = AppConfig.load(settings)
    assert loaded.language == "en-US"
    assert loaded.enabled_features == ["web", "server"]
    assert loaded.default_provider == "sensevoice"
    assert loaded.default_model == "C:/models/sensevoice-small"
    assert loaded.sensevoice.model_dir == "C:/models/sensevoice-small"
    assert loaded.volcengine.api_key == "secret"


def test_app_config_drops_removed_window_feature(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    settings.config_path.write_text('{"enabled_features": ["window", "web"]}', encoding="utf-8")

    assert AppConfig.load(settings).enabled_features == ["web"]


def test_apply_env_overrides_is_runtime_only(tmp_path: Path, monkeypatch) -> None:
    from v2t.user_config import apply_env_overrides

    settings = Settings.from_workspace(tmp_path / ".v2t")
    AppConfig().save(settings)
    monkeypatch.setenv("V2T_DEFAULT_PROVIDER", "volcengine")
    monkeypatch.setenv("V2T_DEFAULT_MODEL", "bigmodel")
    monkeypatch.setenv("V2T_VOLCENGINE_API_KEY", "secret-key")
    monkeypatch.setenv("V2T_PREFER_SUBTITLES", "0")

    config = apply_env_overrides(AppConfig.load(settings))

    assert config.default_provider == "volcengine"
    assert config.default_model == "bigmodel"
    assert config.volcengine.api_key == "secret-key"
    assert config.enabled_providers == ["faster-whisper", "volcengine"]
    assert config.prefer_subtitles is False
    assert "secret-key" not in settings.config_path.read_text(encoding="utf-8")
