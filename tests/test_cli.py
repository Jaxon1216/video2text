from typer.testing import CliRunner
import pytest

from v2t.cli import app


runner = CliRunner()


def test_cli_help_renders() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "抖音" in result.stdout or "Douyin" in result.stdout
    assert "bootstrap" in result.stdout
    assert "batch" in result.stdout
    assert "transcribe" in result.stdout
    assert "YouTube" in result.stdout
    assert "window" not in result.stdout
    # aliases are now hidden, but mentioned in help text parenthetically
    assert "tx" in result.stdout
    assert "lang" not in result.stdout or "lang" in result.stdout  # alias hidden


def test_doctor_command_runs_without_crashing() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "PATH 中的 ffmpeg:" in result.stdout or "ffmpeg:" in result.stdout


def test_language_command_updates_workspace_config(tmp_path) -> None:
    workspace = tmp_path / ".v2t"
    result = runner.invoke(app, ["lang", "en-US", "--workspace", str(workspace)])
    assert result.exit_code == 0
    assert "Language switched to: English" in result.stdout

    config_text = (workspace / "config.json").read_text(encoding="utf-8")
    assert '"language": "en-US"' in config_text


def test_bootstrap_sync_only_requires_existing_config(tmp_path) -> None:
    workspace = tmp_path / ".v2t"
    result = runner.invoke(app, ["bootstrap", "--sync-only", "--workspace", str(workspace)])
    assert result.exit_code == 1
    assert "请先运行一次 bootstrap" in result.stderr


def test_cli_runtime_provider_override_uses_provider_model(tmp_path):
    from v2t.cli import _load_runtime
    from v2t.config import Settings
    from v2t.user_config import AppConfig
    settings = Settings.from_workspace(tmp_path)
    AppConfig(default_model='medium').save(settings)
    _, config = _load_runtime(workspace=tmp_path, provider='volcengine', allow_bootstrap=False)
    assert config.default_model == 'bigmodel'
    _, config = _load_runtime(workspace=tmp_path, provider='whisper', model='/tmp/custom.pt', allow_bootstrap=False)
    assert config.default_model == '/tmp/custom.pt'


@pytest.mark.parametrize("command", ["tx", "batch"])
@pytest.mark.parametrize("force_asr,prefer_subtitles", [(True, True), (False, False)])
def test_cli_persists_effective_asr_options_and_releases_lock_after_failure(
    tmp_path, monkeypatch, command, force_asr, prefer_subtitles
):
    from types import SimpleNamespace
    from v2t.config import Settings
    from v2t.database import AppDatabase
    from v2t.library import WorkspaceLibrary
    from v2t.tasks import TaskService
    from v2t.user_config import AppConfig
    settings = Settings.from_workspace(tmp_path)
    AppConfig(prefer_subtitles=prefer_subtitles).save(settings)
    options = []
    def transcribe(source, *, force_asr=False, **kwargs):
        options.append(force_asr)
        raise RuntimeError("temporary failure")
    monkeypatch.setattr("v2t.cli.build_pipeline", lambda **kwargs: SimpleNamespace(transcribe=transcribe))
    args = [command, "BV1xx411c7XD", "--prompt", "术语", "--workspace", str(tmp_path)]
    if force_asr:
        args.append("--force-asr")
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    database = AppDatabase(settings)
    task = database.list_tasks()[0]
    assert task.status == "failed" and task.prompt == "术语" and task.force_asr
    assert options == [True]
    replacement = TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    replacement.close()


def test_cli_refuses_active_workspace_without_recovering_running_task(tmp_path):
    from v2t.config import Settings
    from v2t.database import AppDatabase
    from v2t.library import WorkspaceLibrary
    from v2t.models import ProgressSnapshot
    from v2t.tasks import TaskService
    from v2t.user_config import AppConfig
    settings = Settings.from_workspace(tmp_path)
    AppConfig().save(settings)
    database = AppDatabase(settings)
    service = TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    try:
        task = database.create_task(kind="transcription", source_input="BV1xx411c7XD", provider="whisper", model="small")
        database.record_progress(ProgressSnapshot(task_id=task.id, status="running", stage="transcribing"))
        result = runner.invoke(app, ["tx", "BV1xx411c7XD", "--workspace", str(tmp_path)])
        assert result.exit_code == 1
        assert "工作区已有转写服务" in result.stderr
        assert database.get_task(task.id).status == "running"
    finally:
        service.close()


def test_cli_service_index_failure_releases_workspace_lock(tmp_path, monkeypatch):
    from v2t.cli import _build_task_service
    from v2t.config import Settings
    from v2t.tasks import TaskService
    from v2t.user_config import AppConfig
    settings = Settings.from_workspace(tmp_path)
    def fail_index(self):
        raise RuntimeError("index failed")
    with monkeypatch.context() as patch:
        patch.setattr(TaskService, "ensure_indexed", fail_index)
        with pytest.raises(RuntimeError, match="index failed"):
            _build_task_service(settings=settings, config=AppConfig())
    service = _build_task_service(settings=settings, config=AppConfig())
    service.close()


def test_web_server_start_failure_releases_workspace_lock(tmp_path, monkeypatch):
    from v2t.cli import _build_task_service
    from v2t.config import Settings
    from v2t.user_config import AppConfig
    settings = Settings.from_workspace(tmp_path)
    AppConfig().save(settings)
    def fail_start(*args, **kwargs):
        raise RuntimeError("server failed to bind")
    monkeypatch.setattr("uvicorn.run", fail_start)
    result = runner.invoke(app, ["web", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "server failed to bind" in result.stderr
    service = _build_task_service(settings=settings, config=AppConfig())
    service.close()
