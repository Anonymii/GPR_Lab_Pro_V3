from __future__ import annotations

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.app.operation_text import get_operation_label
from gpr_lab_pro.domain.enums import StepKind
from gpr_lab_pro.domain.models.pipeline import PipelineStep
from gpr_lab_pro.processing.catalog_v11 import SPEC_BY_TYPE
from gpr_lab_pro.processing.module_registry_v11 import module_for_operation


TRANSFORM_OPERATION_OPTIONS: tuple[tuple[str, str], ...] = (
    ("ifft", "IFFT"),
    ("czt", "CZT"),
    ("isdft", "ISDFT"),
)

TRANSFORM_LABELS = {op_type: title for op_type, title in TRANSFORM_OPERATION_OPTIONS}


class PipelineController:
    TRANSFORM_CATEGORY = "时频域桥接"

    def __init__(self, context: ApplicationContext) -> None:
        self.context = context

    @property
    def state(self):
        return self.context.pipeline_state

    def initialize_for_dataset(self, dataset) -> None:
        default_transform = self.default_transform_step()
        self.state.draft_steps = [default_transform.clone()]
        self.state.applied_steps = [default_transform.clone()]
        self.state.has_unapplied_changes = False
        self.context.snapshot_cache.clear()
        snapshot = self.context.pipeline_executor.create_initial_snapshot(dataset)
        self.context.result_state.history = [snapshot]
        self.context.result_state.active_snapshot = snapshot
        self.context.signals.pipeline_changed.emit(list(self.state.draft_steps))
        self.context.signals.pipeline_applied.emit(list(self.state.applied_steps))

    def default_transform_step(self) -> PipelineStep:
        return PipelineStep.from_sequence(
            op_type="ifft",
            name="IFFT",
            category=self.TRANSFORM_CATEGORY,
            kind=StepKind.TRANSFORM,
            params=[],
        )

    def available_transform_options(self) -> tuple[tuple[str, str], ...]:
        return TRANSFORM_OPERATION_OPTIONS

    def get_steps(self, *, applied: bool = False) -> list[PipelineStep]:
        source = self.state.applied_steps if applied else self.state.draft_steps
        return [step.clone() for step in source]

    def get_steps_by_kind(self, kind: StepKind, *, applied: bool = False) -> list[PipelineStep]:
        return [step.clone() for step in self.get_steps(applied=applied) if step.kind is kind]

    def add_step(self, op_type: str, params: list[float]) -> None:
        spec = SPEC_BY_TYPE[op_type]
        step = PipelineStep.from_sequence(
            op_type=spec.op_type,
            name=get_operation_label(spec.op_type, spec.title),
            category=spec.category,
            kind=spec.kind,
            params=params,
        )
        self.state.draft_steps.append(step)
        self._mark_draft_changed()

    def set_transform_step(self, op_type: str) -> None:
        op_key = op_type.lower()
        title = TRANSFORM_LABELS.get(op_key, op_key.upper())
        transform_step = PipelineStep.from_sequence(
            op_type=op_key,
            name=title,
            category=self.TRANSFORM_CATEGORY,
            kind=StepKind.TRANSFORM,
            params=[],
        )
        non_transform = [step for step in self.state.draft_steps if step.kind is not StepKind.TRANSFORM]
        insert_at = sum(1 for step in non_transform if step.kind is StepKind.FREQUENCY)
        self.state.draft_steps = non_transform[:insert_at] + [transform_step] + non_transform[insert_at:]
        self._mark_draft_changed()

    def move_step_in_kind(self, kind: StepKind, index: int, offset: int) -> None:
        actual_indexes = [idx for idx, step in enumerate(self.state.draft_steps) if step.kind is kind]
        target_index = index + offset
        if index < 0 or target_index < 0 or index >= len(actual_indexes) or target_index >= len(actual_indexes):
            return
        left = actual_indexes[index]
        right = actual_indexes[target_index]
        self.state.draft_steps[left], self.state.draft_steps[right] = (
            self.state.draft_steps[right],
            self.state.draft_steps[left],
        )
        self._mark_draft_changed()

    def remove_step_in_kind(self, kind: StepKind, index: int) -> None:
        if kind is StepKind.TRANSFORM:
            return
        actual_indexes = [idx for idx, step in enumerate(self.state.draft_steps) if step.kind is kind]
        if 0 <= index < len(actual_indexes):
            del self.state.draft_steps[actual_indexes[index]]
            self._mark_draft_changed()

    def set_step_enabled_in_kind(self, kind: StepKind, index: int, enabled: bool) -> None:
        actual_indexes = [idx for idx, step in enumerate(self.state.draft_steps) if step.kind is kind]
        if 0 <= index < len(actual_indexes):
            self.state.draft_steps[actual_indexes[index]].enabled = bool(enabled)
            self._mark_draft_changed()

    def set_step_params_in_kind(self, kind: StepKind, index: int, params: list[float]) -> None:
        actual_indexes = [idx for idx, step in enumerate(self.state.draft_steps) if step.kind is kind]
        if 0 <= index < len(actual_indexes):
            self.state.draft_steps[actual_indexes[index]].params = tuple(params)
            self._mark_draft_changed()

    def apply_draft(self) -> list[PipelineStep]:
        self.ensure_transform_present(self.state.draft_steps)
        self.state.applied_steps = [step.clone() for step in self.state.draft_steps]
        self.state.has_unapplied_changes = False
        self.context.signals.pipeline_changed.emit(list(self.state.draft_steps))
        self.context.signals.pipeline_applied.emit(list(self.state.applied_steps))
        return [step.clone() for step in self.state.applied_steps]

    def restore_draft_from_applied(self) -> None:
        self.state.draft_steps = [step.clone() for step in self.state.applied_steps]
        self.state.has_unapplied_changes = False
        self.context.signals.pipeline_changed.emit(list(self.state.draft_steps))

    def load_draft_steps(self, steps: list[PipelineStep]) -> None:
        self.state.draft_steps = [step.clone() for step in steps]
        self.ensure_transform_present(self.state.draft_steps)
        self.state.has_unapplied_changes = True
        self.context.signals.pipeline_changed.emit(list(self.state.draft_steps))

    def validate_for_processing(self, *, applied: bool = True) -> tuple[bool, str]:
        dataset = self.context.dataset_state.current_dataset
        if not self.context.project_state.is_open:
            return False, "请先新建工程。"
        if dataset is None:
            return False, "请先导入 DAT 数据。"
        steps = self.state.applied_steps if applied else self.state.draft_steps
        transform_steps = [step for step in steps if step.kind is StepKind.TRANSFORM]
        if len(transform_steps) != 1:
            return False, "时频转换部分必须且只能选择一个算法。"
        if not transform_steps[0].enabled:
            return False, "时频转换算法不能被禁用。"
        return True, ""

    def ensure_transform_present(self, steps: list[PipelineStep]) -> None:
        transform_steps = [step for step in steps if step.kind is StepKind.TRANSFORM]
        if not transform_steps:
            steps.insert(
                sum(1 for step in steps if step.kind is StepKind.FREQUENCY),
                self.default_transform_step(),
            )
            return
        if len(transform_steps) > 1:
            first = transform_steps[0]
            retained = [step for step in steps if step.kind is not StepKind.TRANSFORM]
            insert_at = sum(1 for step in retained if step.kind is StepKind.FREQUENCY)
            steps[:] = retained[:insert_at] + [first] + retained[insert_at:]

    def reset_for_current_dataset(self) -> None:
        dataset = self.context.dataset_state.current_dataset
        default_transform = self.default_transform_step()
        self.state.draft_steps = [default_transform.clone()]
        self.state.applied_steps = [default_transform.clone()]
        self.state.has_unapplied_changes = False
        if dataset is not None:
            self.context.snapshot_cache.clear()
            snapshot = self.context.pipeline_executor.create_initial_snapshot(dataset)
            self.context.result_state.history = [snapshot]
            self.context.result_state.active_snapshot = snapshot
        self.context.signals.pipeline_changed.emit(list(self.state.draft_steps))
        self.context.signals.pipeline_applied.emit(list(self.state.applied_steps))

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

    def section_title_for_step(self, step: PipelineStep) -> str:
        module = module_for_operation(step.op_type)
        if module is not None:
            return module.title
        if step.kind is StepKind.TRANSFORM:
            return self.TRANSFORM_CATEGORY
        return step.category

    def _mark_draft_changed(self) -> None:
        self.ensure_transform_present(self.state.draft_steps)
        self.state.has_unapplied_changes = True
        self.context.signals.pipeline_changed.emit(list(self.state.draft_steps))
