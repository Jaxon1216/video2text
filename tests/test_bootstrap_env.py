from pathlib import Path

from b2t import bootstrap as bootstrap_module
from b2t.bootstrap import (
    build_uv_sync_command,
    collect_required_extras,
    run_bootstrap,
    sync_environment_for_config,
    sync_selected_environment,
    uv_available,
)
from b2t.config import Settings
from b2t.user_config import AppConfig


def test_collect_required_extras_combines_providers_and_features() -> None:
    assert collect_required_extras(
        providers=["whisper", "volcengine"],
        features=["web"],
    ) == ["whisper", "volcengine", "web", "douyin"]


def test_collect_required_extras_deduplicates() -> None:
    assert collect_required_extras(
        providers=["sensevoice"],
        features=["server", "server"],
    ) == ["sensevoice", "server", "douyin"]


def test_build_uv_sync_command_is_stable() -> None:
    command = build_uv_sync_command(
        workspace=Path("D:/repo"),
        extras=["whisper", "web"],
    )
    assert command == ["uv", "sync", "--extra", "whisper", "--extra", "web"]


def test_uv_available_uses_path_lookup() -> None:
    assert uv_available(lambda _name: "C:/bin/uv.exe") is True
    assert uv_available(lambda _name: None) is False


def test_sync_selected_environment_reports_missing_uv(tmp_path: Path) -> None:
    result = sync_selected_environment(
        workspace=tmp_path,
        extras=["whisper", "web"],
        which=lambda _name: None,
        runner=None,
    )
    assert result.ok is False
    assert result.reason == "missing_uv"
    assert result.command == ["uv", "sync", "--extra", "whisper", "--extra", "web"]


def test_sync_selected_environment_runs_uv_sync(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_runner(command, **kwargs):
        calls.append(command)
        return Completed()

    result = sync_selected_environment(
        workspace=tmp_path,
        extras=["whisper", "web"],
        which=lambda _name: "C:/bin/uv.exe",
        runner=fake_runner,
    )
    assert result.ok is True
    assert result.reason == "ok"
    assert calls == [["uv", "sync", "--extra", "whisper", "--extra", "web"]]


def test_sync_environment_for_config_uses_saved_provider_and_feature_selection(tmp_path: Path) -> None:
    config = AppConfig()
    config.enabled_providers = ["sensevoice", "volcengine"]
    config.enabled_features = ["server"]

    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_runner(command, **kwargs):
        calls.append(command)
        return Completed()

    result = sync_environment_for_config(
        project_root=tmp_path,
        config=config,
        which=lambda _name: "C:/bin/uv.exe",
        runner=fake_runner,
    )
    assert result.ok is True
    assert calls == [["uv", "sync", "--extra", "sensevoice", "--extra", "volcengine", "--extra", "server", "--extra", "douyin"]]


def test_run_bootstrap_updates_default_model_when_whisper_becomes_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings.from_workspace(tmp_path / ".b2t")
    existing = AppConfig(
        default_provider="volcengine",
        default_model="bigmodel",
        enabled_providers=["volcengine"],
    )
    existing.save(settings)

    class StubPrompt:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    select_values = iter(["zh-CN", "medium", "whisper"])
    checkbox_values = iter([["whisper", "volcengine"], []])
    confirm_values = iter([True, True])
    secret_values = iter(["", "", ""])
    text_values = iter(["volc.bigasr.auc_turbo", "bigmodel"])

    monkeypatch.setattr(
        bootstrap_module.inquirer,
        "select",
        lambda **kwargs: StubPrompt(next(select_values)),
    )
    monkeypatch.setattr(
        bootstrap_module.inquirer,
        "checkbox",
        lambda **kwargs: StubPrompt(next(checkbox_values)),
    )
    monkeypatch.setattr(
        bootstrap_module.inquirer,
        "confirm",
        lambda **kwargs: StubPrompt(next(confirm_values)),
    )
    monkeypatch.setattr(
        bootstrap_module.inquirer,
        "secret",
        lambda **kwargs: StubPrompt(next(secret_values)),
    )
    monkeypatch.setattr(
        bootstrap_module.inquirer,
        "text",
        lambda **kwargs: StubPrompt(next(text_values)),
    )
    monkeypatch.setattr(bootstrap_module, "_show_next_steps", lambda **kwargs: None)

    updated = run_bootstrap(settings=settings, interactive=True)

    assert updated.default_provider == "whisper"
    assert updated.default_model == "medium"
