from v2t.progress import ProgressReporter, overall_progress


def test_overall_progress_uses_stage_ranges() -> None:
    assert overall_progress("preparing", 0.0) == 0.0
    assert round(overall_progress("downloading", 0.5), 2) == 0.2
    assert round(overall_progress("transcribing", 0.5), 3) == 0.725


def test_progress_reporter_emits_snapshots() -> None:
    events = []
    reporter = ProgressReporter("task-1", callback=events.append)

    reporter.running("downloading", message="downloading", stage_progress=0.5)
    reporter.completed("done")

    assert events[0].task_id == "task-1"
    assert events[0].status == "running"
    assert events[0].stage == "downloading"
    assert events[-1].status == "completed"
    assert events[-1].percent == 1.0
