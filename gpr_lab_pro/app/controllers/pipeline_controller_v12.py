from __future__ import annotations

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.app.operation_text import get_operation_label
from gpr_lab_pro.domain.enums import StepKind
from gpr_lab_pro.domain.models.pipeline import PipelineStep
from gpr_lab_pro.processing.catalog_v11 import SPEC_BY_TYPE
from gpr_lab_pro.processing.module_registry_v11 import module_for_operation


TRANSFORM_OPERATION_OPTIONS: tuple[tuple[str, str], ...] = (
    ("czt", "CZT"),
    ("ifft", "IFFT"),
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
            op_type="czt",
            name="CZT",
            category=self.TRANSFORM_CATEGORY,
            kind=StepKind.TRANSFORM,
            params=[],
        )

    def available_transform_options(self) -> tuple[tuple[str, str], ...]:
        return TRANSFORM_OPERATION_OPTIONS

    def get_steps(self, *, applied: bool = False) -> list[PipelineStep]:
        source = self.state.applied_steps if applied else self.state.draft_steps
        return [step.clone() for step in source]

    def get_step_counts(self, *, applied: bool = False) -> dict[StepKind, int]:
        counts = {StepKind.FREQUENCY: 0, StepKind.TRANSFORM: 0, StepKind.TIME: 0}
        for step in self.get_steps(applied=applied):
            counts[step.kind] += 1
        return counts

    def get_steps_by_kind(self, kind: StepKind, *, applied: bool = False) -> list[PipelineStep]:
        return [step.clone() for step in self.get_steps(applied=applied) if step.kind is kind]

    def get_step(self, index: int, *, applied: bool = False) -> PipelineStep | None:
        steps = self.state.applied_steps if applied else self.state.draft_steps
        if 0 <= index < len(steps):
            return steps[index].clone()
        return None

    def add_step(self, op_type: str, params: list[float]) -> None:
        spec = SPEC_BY_TYPE[op_type]
        step = PipelineStep.from_sequence(
            op_type=spec.op_type,
            name=get_operation_label(spec.op_type, spec.title),
            category=spec.category,
            kind=spec.kind,
            params=params,
        )
        if spec.kind is StepKind.FREQUENCY:
            insert_at = next(
                (idx for idx, current in enumerate(self.state.draft_steps) if current.kind is not StepKind.FREQUENCY),
                len(self.state.draft_steps),
            )
            self.state.draft_steps.insert(insert_at, step)
        elif spec.kind is StepKind.TIME:
            self.state.draft_steps.append(step)
        else:
            self.set_transform_step(spec.op_type)
            return
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

    def can_move_step(self, index: int, offset: int) -> bool:
        target = index + offset
        if index < 0 or target < 0 or index >= len(self.state.draft_steps) or target >= len(self.state.draft_steps):
            return False
        current = self.state.draft_steps[index]
        other = self.state.draft_steps[target]
        return current.kind is other.kind and current.kind is not StepKind.TRANSFORM

    def move_step(self, index: int, offset: int) -> None:
        if not self.can_move_step(index, offset):
            return
        target = index + offset
        self.state.draft_steps[index], self.state.draft_steps[target] = (
            self.state.draft_steps[target],
            self.state.draft_steps[index],
        )
        self._mark_draft_changed()

    def remove_step_in_kind(self, kind: StepKind, index: int) -> None:
        if kind is StepKind.TRANSFORM:
            return
        actual_indexes = [idx for idx, step in enumerate(self.state.draft_steps) if step.kind is kind]
        if 0 <= index < len(actual_indexes):
            del self.state.draft_steps[actual_indexes[index]]
            self._mark_draft_changed()

    def remove_step(self, index: int) -> None:
        if index < 0 or index >= len(self.state.draft_steps):
            return
        if self.state.draft_steps[index].kind is StepKind.TRANSFORM:
            return
        del self.state.draft_steps[index]
        self._mark_draft_changed()

    def set_step_enabled_in_kind(self, kind: StepKind, index: int, enabled: bool) -> None:
        actual_indexes = [idx for idx, step in enumerate(self.state.draft_steps) if step.kind is kind]
        if 0 <= index < len(actual_indexes):
            self.state.draft_steps[actual_indexes[index]].enabled = bool(enabled)
            self._mark_draft_changed()

    def set_step_enabled(self, index: int, enabled: bool) -> None:
        if index < 0 or index >= len(self.state.draft_steps):
            return
        if self.state.draft_steps[index].kind is StepKind.TRANSFORM:
            return
        self.state.draft_steps[index].enabled = bool(enabled)
        self._mark_draft_changed()

    def set_step_params_in_kind(self, kind: StepKind, index: int, params: list[float]) -> None:
        actual_indexes = [idx for idx, step in enumerate(self.state.draft_steps) if step.kind is kind]
        if 0 <= index < len(actual_indexes):
            self.state.draft_steps[actual_indexes[index]].params = tuple(params)
            self._mark_draft_changed()

    def set_step_params(self, index: int, params: list[float]) -> None:
        if 0 <= index < len(self.state.draft_steps):
            self.state.draft_steps[index].params = tuple(params)
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

    def restore_project_state(
        self,
        draft_steps: list[PipelineStep],
        applied_steps: list[PipelineStep],
        has_unapplied_changes: bool,
    ) -> None:
        restored_draft = [step.clone() for step in draft_steps]
        restored_applied = [step.clone() for step in applied_steps]
        if not restored_draft and restored_applied:
            restored_draft = [step.clone() for step in restored_applied]
        if not restored_applied and restored_draft:
            restored_applied = [step.clone() for step in restored_draft]
        if not restored_draft:
            default_transform = self.default_transform_step()
            restored_draft = [default_transform.clone()]
            restored_applied = [default_transform.clone()]
        self.ensure_transform_present(restored_draft)
        self.ensure_transform_present(restored_applied)
        self.state.draft_steps = restored_draft
        self.state.applied_steps = restored_applied
        self.state.has_unapplied_changes = bool(has_unapplied_changes)
        self.context.signals.pipeline_changed.emit(list(self.state.draft_steps))
        self.context.signals.pipeline_applied.emit(list(self.state.applied_steps))

    def validate_for_processing(self, *, applied: bool = True) -> tuple[bool, str]:
        dataset = self.context.dataset_state.current_dataset
        if not self.context.project_state.is_open:
            return False, "请先新建工程或打开工程。"
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

    def execute_sync(
        self,
        dataset,
        steps: list[PipelineStep],
        progress_callback=None,
        cancel_callback=None,
        previous_steps: list[PipelineStep] | None = None,
        previous_snapshots: list | None = None,
    ):
        return self.context.pipeline_executor.execute(
            dataset,
            steps,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            previous_steps=previous_steps,
            previous_snapshots=previous_snapshots,
        )

    def apply_execution_result(self, snapshots: list) -> None:
        self.context.result_state.history = snapshots
        self.context.result_state.active_snapshot = snapshots[-1] if snapshots else None
        if self.context.result_state.active_snapshot is not None:
            snapshot = self.context.result_state.active_snapshot
            self.context.signals.status_message.emit(
                f"当前结果: 第 {snapshot.pipeline_index} 步 {snapshot.step_name}"
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
