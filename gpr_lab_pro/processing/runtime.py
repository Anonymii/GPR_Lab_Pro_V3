from __future__ import annotations

import numpy as np

from gpr_lab_pro.domain.enums import DataDomain, StepKind
from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.domain.models.pipeline import PipelineStep
from gpr_lab_pro.domain.models.results import ResultSnapshot
from gpr_lab_pro.infrastructure.workers import WorkerCancelled
from gpr_lab_pro.models import GPRContext, PipelineOperation
from gpr_lab_pro.processing.engine import PipelineProcessor
from gpr_lab_pro.processing.engines import FrequencyProcessingEngine, TimeDomainProcessingEngine
from gpr_lab_pro.processing.transforms import TimeFrequencyTransformBridge


class PipelineRuntime:
    def __init__(self, clip_sigma: float = 6.0):
        self.clip_sigma = clip_sigma

    def create_initial_snapshot(self, dataset: DatasetRecord) -> ResultSnapshot:
        return ResultSnapshot(
            data=np.asarray(dataset.volume),
            domain=DataDomain.FREQUENCY,
            step_name="原始导入数据",
            meta={
                "tw_ns": float(dataset.tw_ns),
                "dt_ns": float(dataset.dt_ns),
            },
            pipeline_index=0,
            render_ready=False,
        )

    def execute(
        self,
        dataset: DatasetRecord,
        steps: list[PipelineStep],
        progress_callback=None,
        cancel_callback=None,
        previous_steps: list[PipelineStep] | None = None,
        previous_snapshots: list[ResultSnapshot] | None = None,
    ) -> list[ResultSnapshot]:
        context = GPRContext(dt=dataset.dt_ns, fs=dataset.fs_hz, clip_sigma=self.clip_sigma)
        processor = PipelineProcessor(context)
        frequency_engine = FrequencyProcessingEngine().bind(processor)
        transform_bridge = TimeFrequencyTransformBridge().bind(processor).configure_dataset(dataset)
        time_engine = TimeDomainProcessingEngine().bind(processor)

        enabled_steps = [item for item in steps if item.enabled]
        previous_enabled_steps = [item for item in (previous_steps or []) if item.enabled]
        total_steps = len(enabled_steps)
        reusable_prefix = self._matching_prefix_length(enabled_steps, previous_enabled_steps)
        max_reusable = min(reusable_prefix, max(0, len(previous_snapshots or []) - 1))
        if previous_snapshots and max_reusable >= 0:
            snapshots = list(previous_snapshots[: max_reusable + 1])
        else:
            snapshots = [self.create_initial_snapshot(dataset)]
            max_reusable = 0

        if max_reusable > 0:
            current = snapshots[-1].data
            current_domain = snapshots[-1].domain
            current_meta = dict(snapshots[-1].meta)
            reused_percent = int(max_reusable / max(total_steps, 1) * 100)
            self._report_progress(progress_callback, reused_percent, f"复用前 {max_reusable} 步结果")
        else:
            snapshots = [self.create_initial_snapshot(dataset)]
            current = dataset.volume
            current_domain = DataDomain.FREQUENCY
            current_meta = dict(snapshots[-1].meta)
            self._report_progress(progress_callback, 0, "开始执行处理流程")

        for idx, step in enumerate(enabled_steps[max_reusable:], start=max_reusable + 1):
            self._check_cancelled(cancel_callback)
            legacy_op = PipelineOperation.from_sequence(step.op_type, step.params, step.name)
            step_start_percent = int((idx - 1) / max(total_steps, 1) * 100)
            step_end_percent = int(idx / max(total_steps, 1) * 100)
            if step.kind is StepKind.FREQUENCY:
                current = frequency_engine.execute(current, legacy_op)
                current_domain = DataDomain.FREQUENCY
                current_meta = dict(current_meta)
            elif step.kind is StepKind.TRANSFORM:
                def transform_progress(local_percent: int, message: str) -> None:
                    span = max(step_end_percent - step_start_percent, 1)
                    mapped = step_start_percent + int(span * max(0, min(100, local_percent)) / 100)
                    self._report_progress(progress_callback, mapped, message)

                current = transform_bridge.execute(
                    current,
                    legacy_op,
                    progress_callback=transform_progress,
                    cancel_callback=cancel_callback,
                )
                current_domain = DataDomain.TIME
                current_meta = transform_bridge.current_time_meta()
            else:
                current = time_engine.execute(current, legacy_op)
                current_domain = DataDomain.TIME
                current_meta = dict(current_meta)

            snapshots.append(
                ResultSnapshot(
                    data=np.asarray(current),
                    domain=current_domain,
                    step_name=step.name,
                    params=step.params,
                    meta=dict(current_meta),
                    pipeline_index=idx,
                    parent_snapshot_id=snapshots[-1].snapshot_id,
                    render_ready=False,
                )
            )
            percent = step_end_percent
            self._report_progress(progress_callback, percent, f"正在执行第 {idx}/{total_steps} 步：{step.name}")
        self._report_progress(progress_callback, 100, "处理流程执行完成")
        return snapshots

    @staticmethod
    def _matching_prefix_length(current_steps: list[PipelineStep], previous_steps: list[PipelineStep]) -> int:
        length = 0
        for current, previous in zip(current_steps, previous_steps):
            if PipelineRuntime._step_signature(current) != PipelineRuntime._step_signature(previous):
                break
            length += 1
        return length

    @staticmethod
    def _step_signature(step: PipelineStep) -> tuple:
        return step.kind, step.op_type, tuple(step.params)

    @staticmethod
    def _report_progress(progress_callback, percent: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(int(max(0, min(100, percent))), str(message))

    @staticmethod
    def _check_cancelled(cancel_callback) -> None:
        if cancel_callback is not None and cancel_callback():
            raise WorkerCancelled()
