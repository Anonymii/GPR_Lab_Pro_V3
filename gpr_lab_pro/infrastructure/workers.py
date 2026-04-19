from __future__ import annotations

import inspect
import threading
from typing import Any, Callable
import traceback

from PySide6 import QtCore


class WorkerCancelled(Exception):
    """Raised when a background worker is cancelled by the UI."""


class WorkerSignals(QtCore.QObject):
    result = QtCore.Signal(object)
    progress = QtCore.Signal(int, str)
    error = QtCore.Signal(str)
    cancelled = QtCore.Signal()
    finished = QtCore.Signal()


class FunctionWorker(QtCore.QRunnable):
    def __init__(self, func: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise WorkerCancelled()

    def run(self) -> None:
        try:
            kwargs = dict(self.kwargs)
            try:
                parameters = inspect.signature(self.func).parameters
            except (TypeError, ValueError):
                parameters = {}
            if "progress_callback" in parameters and "progress_callback" not in kwargs:
                kwargs["progress_callback"] = self.signals.progress.emit
            if "cancel_callback" in parameters and "cancel_callback" not in kwargs:
                kwargs["cancel_callback"] = self.is_cancelled
            if "check_cancelled" in parameters and "check_cancelled" not in kwargs:
                kwargs["check_cancelled"] = self._raise_if_cancelled
            self._raise_if_cancelled()
            result = self.func(*self.args, **kwargs)
            self._raise_if_cancelled()
            self.signals.result.emit(result)
        except WorkerCancelled:
            self.signals.cancelled.emit()
        except Exception:
            self.signals.error.emit(traceback.format_exc())
        finally:
            self.signals.finished.emit()
