from __future__ import annotations

from PySide6 import QtCore

from gpr_lab_pro.domain.models import (
    DatasetState,
    DisplayState,
    PipelineState,
    ProjectState,
    ResultSnapshot,
    ResultState,
    SelectionState,
)
from gpr_lab_pro.infrastructure.settings import AppSettings
from gpr_lab_pro.io.importers import DatImporterService
from gpr_lab_pro.io.project_store import ProjectStore
from gpr_lab_pro.processing.engines import FrequencyProcessingEngine, TimeDomainProcessingEngine
from gpr_lab_pro.processing.pipeline import PipelineExecutor, SnapshotCacheManager, TaskScheduler
from gpr_lab_pro.processing.transforms import TimeFrequencyTransformBridge
from gpr_lab_pro.signals import AppSignals


class ApplicationContext:
    def __init__(self) -> None:
        self.signals = AppSignals()
        self.settings = AppSettings()
        self.thread_pool = QtCore.QThreadPool.globalInstance()

        self.import_service = DatImporterService()
        self.project_store = ProjectStore()
        self.frequency_engine = FrequencyProcessingEngine()
        self.transform_bridge = TimeFrequencyTransformBridge()
        self.time_engine = TimeDomainProcessingEngine()
        self.pipeline_executor = PipelineExecutor()
        self.snapshot_cache: SnapshotCacheManager = self.pipeline_executor.cache_manager
        self.task_scheduler = TaskScheduler(self.thread_pool)

        self.project_state = ProjectState(name=self.settings.project_name)
        self.dataset_state = DatasetState()
        self.pipeline_state = PipelineState()
        self.result_state = ResultState()
        self.display_state = DisplayState()
        self.selection_state = SelectionState()
        self.region_runtime_results: dict[str, list[ResultSnapshot]] = {}
