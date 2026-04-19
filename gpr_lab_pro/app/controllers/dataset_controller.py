from __future__ import annotations

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.io.importers import DataImportParameters


class DatasetController:
    def __init__(self, context: ApplicationContext) -> None:
        self.context = context

    @property
    def state(self):
        return self.context.dataset_state

    def import_dataset_sync(
        self,
        path: str,
        params: DataImportParameters | None = None,
        progress_callback=None,
        cancel_callback=None,
    ) -> DatasetRecord:
        resolved_params = params or DataImportParameters()
        imported = self.context.import_service.import_file(
            path,
            resolved_params,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        return DatasetRecord.from_import(imported, source_path=str(path), import_params=resolved_params)

    def apply_import_result(self, dataset: DatasetRecord) -> None:
        self.state.datasets_by_id[dataset.dataset_id] = dataset
        self.state.current_dataset = dataset
        self.state.import_in_progress = False
        self.context.selection_state.line_index = max(0, dataset.line_count // 2)
        self.context.selection_state.trace_index = max(0, dataset.trace_count // 2)
        self.context.selection_state.sample_index = max(0, dataset.sample_count // 4)
        self.context.signals.dataset_loaded.emit(dataset)
        self.context.signals.status_message.emit(
            f"已导入: {dataset.filename} | 原始频域数据已就绪"
        )
