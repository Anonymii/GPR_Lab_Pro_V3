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
        time_meta = self._dataset_time_meta(dataset)
        return ResultSnapshot(
            data=np.asarray(dataset.volume),
            domain=DataDomain.FREQUENCY,
            step_name="原始导入数据",
            meta=time_meta,
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
        display_time_window_ns: tuple[float, float] | None = None,
    ) -> list[ResultSnapshot]:
        dataset_time_meta = self._dataset_time_meta(dataset)
        context = GPRContext(
            dt=float(dataset_time_meta["dt_ns"]),
            fs=float(dataset_time_meta["fs_hz"]),
            clip_sigma=self.clip_sigma,
        )
        processor = PipelineProcessor(context)
        frequency_engine = FrequencyProcessingEngine().bind(processor)
        transform_bridge = (
            TimeFrequencyTransformBridge()
            .bind(processor)
            .configure_dataset(dataset)
            .configure_display_time_window(display_time_window_ns)
        )
        time_engine = TimeDomainProcessingEngine().bind(processor)

        enabled_steps = [item for item in steps if item.enabled]
        previous_enabled_steps = [item for item in (previous_steps or []) if item.enabled]
        total_steps = len(enabled_steps)
        reusable_prefix = self._matching_prefix_length(enabled_steps, previous_enabled_steps)
        reusable_prefix = min(
            reusable_prefix,
            self._window_compatible_prefix_length(enabled_steps, previous_snapshots, display_time_window_ns),
        )
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
            self._sync_context_sampling(context, current_domain, current_meta, dataset_time_meta)
            reused_percent = int(max_reusable / max(total_steps, 1) * 100)
            self._report_progress(progress_callback, reused_percent, f"复用前 {max_reusable} 步结果")
        else:
            snapshots = [self.create_initial_snapshot(dataset)]
            current = dataset.volume
            current_domain = DataDomain.FREQUENCY
            current_meta = dict(snapshots[-1].meta)
            self._sync_context_sampling(context, current_domain, current_meta, dataset_time_meta)
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
                current, current_meta = self._apply_time_region_crop(dataset, current, current_meta)
                self._sync_context_sampling(context, current_domain, current_meta, dataset_time_meta)
            else:
                current = time_engine.execute(current, legacy_op)
                current_domain = DataDomain.TIME
                current_meta = dict(current_meta)
                self._sync_context_sampling(context, current_domain, current_meta, dataset_time_meta)

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
    def _window_compatible_prefix_length(
        enabled_steps: list[PipelineStep],
        previous_snapshots: list[ResultSnapshot] | None,
        display_time_window_ns: tuple[float, float] | None,
    ) -> int:
        if not previous_snapshots or display_time_window_ns is None:
            return len(enabled_steps)

        target_start_ns = float(display_time_window_ns[0])
        target_end_ns = float(display_time_window_ns[1])
        matched = 0
        for step_index, step in enumerate(enabled_steps, start=1):
            if step.kind is StepKind.TRANSFORM and step.op_type.lower() == "isdft":
                if step_index >= len(previous_snapshots):
                    return matched
                meta = dict(previous_snapshots[step_index].meta)
                prev_start_ns = meta.get("transform_window_start_ns")
                prev_end_ns = meta.get("transform_window_end_ns")
                if prev_start_ns is None or prev_end_ns is None:
                    return matched
                if abs(float(prev_start_ns) - target_start_ns) > 1e-6 or abs(float(prev_end_ns) - target_end_ns) > 1e-6:
                    return matched
            matched += 1
        return matched

    @staticmethod
    def _report_progress(progress_callback, percent: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(int(max(0, min(100, percent))), str(message))

    @staticmethod
    def _check_cancelled(cancel_callback) -> None:
        if cancel_callback is not None and cancel_callback():
            raise WorkerCancelled()

    @staticmethod
    def _dataset_time_meta(dataset: DatasetRecord) -> dict[str, float]:
        dt_ns = float(dataset.transformed_dt_ns())
        tw_ns = float(dataset.transformed_time_window_ns())
        fs_hz = 0.0 if dt_ns <= 0 else 1.0 / (dt_ns * 1e-9)
        return {
            "t0_ns": 0.0,
            "tw_ns": tw_ns,
            "dt_ns": dt_ns,
            "fs_hz": fs_hz,
        }

    @staticmethod
    def _sync_context_sampling(
        context: GPRContext,
        domain: DataDomain,
        meta: dict[str, object],
        dataset_time_meta: dict[str, float],
    ) -> None:
        source_meta = meta if domain is DataDomain.TIME else dataset_time_meta
        dt_ns = float(source_meta.get("dt_ns", 0.0) or 0.0)
        fs_hz = float(source_meta.get("fs_hz", 0.0) or 0.0)
        if fs_hz <= 0.0 and dt_ns > 0.0:
            fs_hz = 1.0 / (dt_ns * 1e-9)
        context.dt = dt_ns
        context.fs = fs_hz

    @staticmethod
    def _apply_time_region_crop(
        dataset: DatasetRecord,
        data: np.ndarray,
        meta: dict[str, object],
    ) -> tuple[np.ndarray, dict[str, object]]:
        arr = np.asarray(data)
        if arr.ndim < 1 or arr.shape[0] <= 0:
            return arr, dict(meta)

        sample_count = int(arr.shape[0])
        start = int(dataset.header.get("region_sample_start", 0) or 0)
        stop = int(dataset.header.get("region_sample_stop", sample_count) or sample_count)
        start = int(np.clip(start, 0, max(sample_count - 1, 0)))
        stop = int(np.clip(stop, start + 1, sample_count))
        if start == 0 and stop == sample_count:
            return arr, dict(meta)

        cropped = arr[start:stop, ...]
        cropped_meta = dict(meta)
        dt_ns = float(cropped_meta.get("dt_ns", 0.0) or 0.0)
        cropped_meta["t0_ns"] = float(cropped_meta.get("t0_ns", 0.0) or 0.0) + start * dt_ns
        cropped_meta["tw_ns"] = dt_ns * max(cropped.shape[0] - 1, 0)
        return cropped, cropped_meta
