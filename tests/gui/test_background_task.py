from __future__ import annotations

from ebook_toolbox.ui.background_task import BackgroundTask


def test_background_task_emits_result() -> None:
    results: list[object] = []
    task = BackgroundTask(lambda _task: {"status": "ok"})
    task.signals.finished.connect(results.append)

    task.run()

    assert results == [{"status": "ok"}]


def test_background_task_formats_unexpected_exception() -> None:
    failures: list[str] = []

    def fail(_task: BackgroundTask) -> object:
        raise ValueError("broken")

    task = BackgroundTask(fail)
    task.signals.failed.connect(failures.append)

    task.run()

    assert failures == ["ValueError: broken"]


def test_background_task_exposes_progress_and_thread_safe_cancel_state() -> None:
    progress: list[object] = []

    def operate(task: BackgroundTask) -> bool:
        task.report_progress((1, 3))
        return task.is_cancel_requested()

    task = BackgroundTask(operate)
    task.signals.progress.connect(progress.append)
    results: list[object] = []
    task.signals.finished.connect(results.append)

    task.cancel()
    task.run()

    assert progress == [(1, 3)]
    assert results == [True]
