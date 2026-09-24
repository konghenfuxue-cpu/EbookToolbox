"""Reusable QRunnable wrapper for GUI background operations."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class BackgroundTaskSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(object)


class BackgroundTask(QRunnable):
    """Run one callable with shared signals and cooperative cancellation state."""

    def __init__(
        self,
        operation: Callable[[BackgroundTask], object],
        *,
        format_exception: Callable[[Exception], str] | None = None,
    ) -> None:
        super().__init__()
        self._operation = operation
        self._format_exception = format_exception or (
            lambda exc: f"{type(exc).__name__}: {exc}"
        )
        self._cancel_requested = threading.Event()
        self.signals = BackgroundTaskSignals()

    def cancel(self) -> None:
        """Request cooperative cancellation at the operation's next safe boundary."""

        self._cancel_requested.set()

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def report_progress(self, update: object) -> None:
        self.signals.progress.emit(update)

    @Slot()
    def run(self) -> None:
        try:
            result = self._operation(self)
        except Exception as exc:  # pragma: no cover - exercised through page tests
            self.signals.failed.emit(self._format_exception(exc))
            return
        self.signals.finished.emit(result)


__all__ = ["BackgroundTask", "BackgroundTaskSignals"]
