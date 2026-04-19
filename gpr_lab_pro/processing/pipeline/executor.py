from __future__ import annotations

from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.domain.models.pipeline import PipelineStep
from gpr_lab_pro.domain.models.results import ResultSnapshot
from gpr_lab_pro.processing.pipeline.cache_manager import SnapshotCacheManager
from gpr_lab_pro.processing.runtime import PipelineRuntime


class PipelineExecutor:
    def __init__(
        self,
        runtime: PipelineRuntime | None = None,
        cache_manager: SnapshotCacheManager | None = None,
    ) -> None:
        self.runtime = runtime or PipelineRuntime()
        self.cache_manager = cache_manager or SnapshotCacheManager()

    def create_initial_snapshot(self, dataset: DatasetRecord) -> ResultSnapshot:
        snapshot = self.runtime.create_initial_snapshot(dataset)
        self.cache_manager.store(snapshot)
        return snapshot

    def execute(
        self,
        dataset: DatasetRecord,
        steps: list[PipelineStep],
        progress_callback=None,
        cancel_callback=None,
        previous_steps: list[PipelineStep] | None = None,
        previous_snapshots: list[ResultSnapshot] | None = None,
    ) -> list[ResultSnapshot]:
        snapshots = self.runtime.execute(
            dataset,
            steps,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            previous_steps=previous_steps,
            previous_snapshots=previous_snapshots,
        )
        self.cache_manager.clear()
        self.cache_manager.store_many(snapshots)
        return snapshots


__all__ = ["PipelineExecutor"]
