from v2t.progress import ProgressReporter
from v2t.transcribers.whisper_local import (
    WhisperProgressTqdm,
    build_whisper_import_error_message,
)


def test_build_whisper_import_error_message_reports_missing_install() -> None:
    message = build_whisper_import_error_message(
        whisper_available=False,
    )

    assert "Whisper support is not installed." in message
    assert "uv sync --extra whisper --extra web" in message


def test_build_whisper_import_error_message_reports_broken_environment() -> None:
    message = build_whisper_import_error_message(
        whisper_available=True,
    )

    assert "Whisper is installed, but the Python environment looks broken." in message
    assert ".venv" in message


def test_whisper_progress_tqdm_reports_fractional_progress() -> None:
    events = []
    reporter = ProgressReporter("task-1", callback=events.append)
    bar = WhisperProgressTqdm(reporter, total=100, disable=False)

    with bar:
        bar.update(25)
        bar.update(25)

    assert events[-1].stage == "transcribing"
    assert round(events[-1].percent, 3) == 0.725
