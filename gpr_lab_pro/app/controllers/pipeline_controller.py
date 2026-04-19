from __future__ import annotations

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.app.operation_text import get_operation_label
from gpr_lab_pro.domain.models.pipeline import PipelineStep
from gpr_lab_pro.processing.catalog_v11 import SPEC_BY_TYPE


class PipelineController:
    def __init__(self, context: ApplicationContext) -> None:
        self.context = context

    @property
    def state(self):
        return self.context.pipeline_state

    def initialize_for_dataset(self, dataset) -> None:
        self.state.steps = []
        self.context.snapshot_cache.clear()
        snapshot = self.context.pipeline_executor.create_initial_snapshot(dataset)
        self.context.result_state.history = [snapshot]
        self.context.result_state.active_snapshot = snapshot
        self.context.signals.pipeline_changed.emit(list(self.state.steps))

    def add_step(self, op_type: str, params: list[float]) -> None:
        spec = SPEC_BY_TYPE[op_type]
        step = PipelineStep.from_sequence(
            op_type=spec.op_type,
            name=get_operation_label(spec.op_type, spec.title),
            category=spec.category,
            kind=spec.kind,
            params=params,
        )
        self.state.steps.append(step)
        self.context.signals.pipeline_changed.emit(list(self.state.steps))

    def move_step(self, index: int, offset: int) -> None:
        target = index + offset
        if index < 0 or target < 0 or index >= len(self.state.steps) or target >= len(self.state.steps):
            return
        self.state.steps[index], self.state.steps[target] = (
            self.state.steps[target],
            self.state.steps[index],
        )
        self.context.signals.pipeline_changed.emit(list(self.state.steps))

    def remove_step(self, index: int) -> None:
        if 0 <= index < len(self.state.steps):
            del self.state.steps[index]
            self.context.signals.pipeline_changed.emit(list(self.state.steps))

    def set_step_enabled(self, index: int, enabled: bool) -> None:
        if 0 <= index < len(self.state.steps):
            self.state.steps[index].enabled = bool(enabled)
            self.context.signals.pipeline_changed.emit(list(self.state.steps))

    def set_step_params(self, index: int, params: list[float]) -> None:
        if 0 <= index < len(self.state.steps):
            self.state.steps[index].params = tuple(params)
            self.context.signals.pipeline_changed.emit(list(self.state.steps))

    def reset_for_current_dataset(self) -> None:
        dataset = self.context.dataset_state.current_dataset
        self.state.steps = []
        if dataset is not None:
            self.context.snapshot_cache.clear()
            snapshot = self.context.pipeline_executor.create_initial_snapshot(dataset)
            self.context.result_state.history = [snapshot]
            self.context.result_state.active_snapshot = snapshot
        self.context.signals.pipeline_changed.emit(list(self.state.steps))

    def execute_sync(self, dataset, steps: list[PipelineStep]):
        return self.context.pipeline_executor.execute(dataset, steps)

    def apply_execution_result(self, snapshots: list) -> None:
        self.context.result_state.history = snapshots
        self.context.result_state.active_snapshot = snapshots[-1] if snapshots else None
        if self.context.result_state.active_snapshot is not None:
            snap = self.context.result_state.active_snapshot
            self.context.signals.status_message.emit(
                f"当前结果: 第 {snap.pipeline_index} 步 {snap.step_name}"
            )
