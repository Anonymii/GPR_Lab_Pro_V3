from __future__ import annotations

from PySide6 import QtCore


class TaskScheduler:
    def __init__(self, thread_pool: QtCore.QThreadPool | None = None) -> None:
        self.thread_pool = thread_pool or QtCore.QThreadPool.globalInstance()

    def start(self, worker: QtCore.QRunnable) -> None:
        self.thread_pool.start(worker)
