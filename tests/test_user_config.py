from pathlib import Path

from b2t.config import Settings
from b2t.user_config import AppConfig


def test_app_config_round_trip(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
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
    settings = Settings.from_workspace(tmp_path / ".b2t")
    settings.ensure_directories()
    settings.config_path.write_text('{"enabled_features": ["window", "web"]}', encoding="utf-8")

    assert AppConfig.load(settings).enabled_features == ["web"]
