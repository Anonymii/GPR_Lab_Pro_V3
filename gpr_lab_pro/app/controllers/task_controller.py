from __future__ import annotations

from typing import Any, Callable

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.infrastructure.workers import FunctionWorker


class TaskController:
    def __init__(self, context: ApplicationContext) -> None:
        self.context = context
        self._current_worker: FunctionWorker | None = None

    def run(
        self,
        func: Callable[..., Any],
        *args: Any,
        result_handler: Callable[[Any], None] | None = None,
        cancelled_handler: Callable[[], None] | None = None,
        busy_message: str = "",
        finished_message: str = "",
        **func_kwargs: Any,
    ) -> None:
        self._emit_busy(True, busy_message)
        worker = FunctionWorker(func, *args, **func_kwargs)
        self._current_worker = worker
        completion = {"done": False}

        def clear_current_worker() -> None:
            if self._current_worker is worker:
                self._current_worker = None

        def handle_result(result: Any) -> None:
            completion["done"] = True
            clear_current_worker()
            self._emit_busy(False, finished_message or "就绪")
            if result_handler is not None:
                result_handler(result)

        def handle_error(error_text: str) -> None:
            completion["done"] = True
            clear_current_worker()
            self._emit_busy(False, "就绪")
            self._on_worker_error(error_text)

        def handle_cancelled() -> None:
            completion["done"] = True
            clear_current_worker()
            self._emit_busy(False, "已取消")
            if cancelled_handler is not None:
                cancelled_handler()

        def handle_finished() -> None:
            if completion["done"]:
                return
            clear_current_worker()
            self._emit_busy(False, finished_message or "就绪")

        worker.signals.result.connect(handle_result)
        worker.signals.progress.connect(self._on_worker_progress)
        worker.signals.error.connect(handle_error)
        worker.signals.cancelled.connect(handle_cancelled)
        worker.signals.finished.connect(handle_finished)
        self.context.task_scheduler.start(worker)

    def cancel_current_task(self) -> bool:
        if self._current_worker is None:
            return False
        self._current_worker.cancel()
        self.context.signals.status_message.emit("正在取消当前任务...")
        return True

    def _emit_busy(self, busy: bool, message: str) -> None:
        self.context.signals.busy_changed.emit(busy, message)
        if message:
            self.context.signals.status_message.emit(message)

    def _on_worker_error(self, error_text: str) -> None:
        self.context.signals.error.emit(error_text)

    def _on_worker_progress(self, percent: int, message: str) -> None:
        self.context.signals.progress_changed.emit(int(percent), message)
