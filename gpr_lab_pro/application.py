from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.app.controllers import (
    DatasetController,
    DisplayController,
    ExportController,
    PipelineController,
    ProjectController,
    TaskController,
)
from gpr_lab_pro.domain.enums import DataDomain
from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.io.importers import DataImportParameters


class GPRApplication(QtCore.QObject):
    def __init__(self) -> None:
        super().__init__()
        self.context = ApplicationContext()
        self._pending_project_restore: dict[str, object] | None = None
        self._restoring_project = False
        self._pending_region_selection: str | None = None

        self.signals = self.context.signals
        self.settings = self.context.settings
        self.thread_pool = self.context.thread_pool
        self.pipeline_executor = self.context.pipeline_executor
        self.runtime = self.pipeline_executor.runtime

        self.project_state = self.context.project_state
        self.dataset_state = self.context.dataset_state
        self.pipeline_state = self.context.pipeline_state
        self.result_state = self.context.result_state
        self.display_state = self.context.display_state
        self.selection_state = self.context.selection_state

        self.project_controller = ProjectController(self.context)
        self.dataset_controller = DatasetController(self.context)
        self.pipeline_controller = PipelineController(self.context)
        self.display_controller = DisplayController(self.context)
        self.export_controller = ExportController(self.context)
        self.task_controller = TaskController(self.context)

    @property
    def dataset(self) -> DatasetRecord | None:
        return self.dataset_state.current_dataset

    def create_project(self, name: str, root_path: str) -> None:
        self.project_controller.create_project(name, root_path)

    def open_project(self, project_file: str) -> None:
        loaded = self.project_controller.open_project(project_file)
        self.context.region_runtime_results = dict(loaded.get("region_results", {}))
        dataset_source_path = str(loaded.get("dataset_source_path", "") or "")
        if dataset_source_path:
            self._pending_project_restore = loaded
            self._restoring_project = True
            active_region_id = str(self.project_state.active_region_id or "")
            self._pending_region_selection = active_region_id or None
            self.import_data(dataset_source_path, loaded.get("dataset_import_params"))
            return
        self._pending_project_restore = None
        self._restoring_project = False

    def import_data(self, path: str, params: DataImportParameters | None = None) -> None:
        self.dataset_state.import_in_progress = True
        self.task_controller.run(
            self.dataset_controller.import_dataset_sync,
            path,
            params,
            result_handler=self._on_import_success,
            cancelled_handler=self._on_import_cancelled,
            busy_message="正在导入数据...",
            finished_message="导入完成",
        )

    def _on_import_success(self, dataset: DatasetRecord) -> None:
        active_region = self.project_controller.mark_dataset_loaded(dataset)
        self.dataset_controller.apply_import_result(dataset)
        if self._pending_region_selection:
            pending = self.project_controller.set_active_region(self._pending_region_selection)
            if pending is not None:
                active_region = pending
            self._pending_region_selection = None
        region_dataset = self.project_controller.build_active_region_dataset() or dataset
        self.dataset_state.current_dataset = region_dataset
        self._restore_region_result_state(active_region.region_id, region_dataset)
        if active_region.pipeline_applied or active_region.pipeline_draft:
            self.pipeline_controller.restore_project_state(
                active_region.pipeline_draft,
                active_region.pipeline_applied,
                active_region.pipeline_dirty,
            )
        else:
            self.pipeline_controller.initialize_for_dataset(region_dataset)
        self.display_controller.restore_state(active_region.display_state)
        self.display_controller.select_line(int(active_region.selection_state.line_index))
        self.display_controller.select_trace(int(active_region.selection_state.trace_index))
        self.display_controller.select_sample(int(active_region.selection_state.sample_index))
        if self._has_renderable_result():
            self.display_controller.publish_display()
        else:
            self.signals.display_cleared.emit()
        if self._pending_project_restore is not None:
            self._apply_pending_project_restore()

    def _on_import_cancelled(self) -> None:
        self._pending_project_restore = None
        self._restoring_project = False
        self._pending_region_selection = None
        self.dataset_state.import_in_progress = False
        self.signals.status_message.emit("数据导入已取消")

    def add_pipeline_step(self, op_type: str, params: list[float]) -> None:
        self.pipeline_controller.add_step(op_type, params)

    def set_transform_step(self, op_type: str) -> None:
        self.pipeline_controller.set_transform_step(op_type)

    def move_step_in_kind(self, kind, index: int, offset: int) -> None:
        self.pipeline_controller.move_step_in_kind(kind, index, offset)

    def move_pipeline_step(self, index: int, offset: int) -> None:
        self.pipeline_controller.move_step(index, offset)

    def can_move_pipeline_step(self, index: int, offset: int) -> bool:
        return self.pipeline_controller.can_move_step(index, offset)

    def remove_step_in_kind(self, kind, index: int) -> None:
        self.pipeline_controller.remove_step_in_kind(kind, index)

    def remove_pipeline_step(self, index: int) -> None:
        self.pipeline_controller.remove_step(index)

    def set_step_enabled_in_kind(self, kind, index: int, enabled: bool) -> None:
        self.pipeline_controller.set_step_enabled_in_kind(kind, index, enabled)

    def set_pipeline_step_enabled(self, index: int, enabled: bool) -> None:
        self.pipeline_controller.set_step_enabled(index, enabled)

    def set_step_params_in_kind(self, kind, index: int, params: list[float]) -> None:
        self.pipeline_controller.set_step_params_in_kind(kind, index, params)

    def set_pipeline_step_params(self, index: int, params: list[float]) -> None:
        self.pipeline_controller.set_step_params(index, params)

    def apply_pipeline_draft(self) -> list:
        return self.pipeline_controller.apply_draft()

    def restore_pipeline_draft(self) -> None:
        self.pipeline_controller.restore_draft_from_applied()

    def reset_pipeline(self) -> None:
        self.pipeline_controller.reset_for_current_dataset()
        if self._has_renderable_result():
            self.display_controller.publish_display()

    def validate_processing_ready(self) -> tuple[bool, str]:
        return self.pipeline_controller.validate_for_processing(applied=True)

    def execute_pipeline(self, previous_steps: list | None = None, previous_snapshots: list | None = None) -> None:
        ok, message = self.validate_processing_ready()
        if not ok or self.dataset is None:
            if message:
                self.signals.error.emit(message)
            return
        self.task_controller.run(
            self.pipeline_controller.execute_sync,
            self.dataset,
            list(self.pipeline_state.applied_steps),
            previous_steps=previous_steps,
            previous_snapshots=previous_snapshots,
            result_handler=self._on_pipeline_success,
            cancelled_handler=self._on_processing_cancelled,
            busy_message="正在执行处理流程...",
            finished_message="流程执行完成",
        )

    def _on_pipeline_success(self, snapshots: list) -> None:
        self.pipeline_controller.apply_execution_result(snapshots)
        self._cache_active_region_result_state()
        self.display_controller.publish_display()
        self.signals.processing_finished.emit()

    def _on_processing_cancelled(self) -> None:
        self.signals.status_message.emit("处理流程已取消")

    def cancel_current_task(self) -> bool:
        return self.task_controller.cancel_current_task()

    def current_time_window_ns(self) -> float:
        snapshot = self.result_state.active_snapshot
        if snapshot is not None and snapshot.domain is DataDomain.TIME:
            value = float(snapshot.meta.get("tw_ns", 0.0) or 0.0)
            if value > 0:
                return value
        dataset = self.dataset
        if dataset is not None:
            return dataset.transformed_time_window_ns()
        return max(60.0, self.display_state.end_time_ns)

    def current_dt_ns(self) -> float:
        snapshot = self.result_state.active_snapshot
        if snapshot is not None and snapshot.domain is DataDomain.TIME:
            value = float(snapshot.meta.get("dt_ns", 0.0) or 0.0)
            if value > 0:
                return value
        dataset = self.dataset
        if dataset is not None:
            return dataset.transformed_dt_ns()
        return 1.0

    def publish_display(self) -> None:
        if self._has_renderable_result():
            self.display_controller.publish_display()

    def update_display_settings(
        self,
        *,
        contrast_gain: float | None = None,
        slice_thickness: int | None = None,
        bscan_attr: str | None = None,
        cscan_attr: str | None = None,
        start_time_ns: float | None = None,
        end_time_ns: float | None = None,
        colormap: str | None = None,
        invert: bool | None = None,
        show_axes: bool | None = None,
    ) -> None:
        self.display_controller.update_settings(
            contrast_gain=contrast_gain,
            slice_thickness=slice_thickness,
            bscan_attr=bscan_attr,
            cscan_attr=cscan_attr,
            start_time_ns=start_time_ns,
            end_time_ns=end_time_ns,
            colormap=colormap,
            invert=invert,
            show_axes=show_axes,
        )
        if self._has_renderable_result():
            self.display_controller.publish_display()

    def select_from_bscan(self, trace_index: int, time_ns: float) -> None:
        self.display_controller.select_from_bscan(trace_index, time_ns)
        if self._has_renderable_result():
            self.display_controller.publish_display()

    def select_line(self, line_index: int) -> None:
        self.display_controller.select_line(line_index)
        if self._has_renderable_result():
            self.display_controller.publish_display()

    def select_trace(self, trace_index: int) -> None:
        self.display_controller.select_trace(trace_index)
        if self._has_renderable_result():
            self.display_controller.publish_display()

    def select_sample(self, sample_index: int) -> None:
        self.display_controller.select_sample(sample_index)
        if self._has_renderable_result():
            self.display_controller.publish_display()

    def save_pipeline_template(self, path: str) -> None:
        self.export_controller.save_pipeline_template(path)

    def load_pipeline_template(self, path: str) -> None:
        self.export_controller.load_pipeline_template(path)
        self.pipeline_controller.ensure_transform_present(self.pipeline_state.draft_steps)
        self.pipeline_state.has_unapplied_changes = True
        self.signals.pipeline_changed.emit(list(self.pipeline_state.draft_steps))

    def available_scene_templates(self) -> dict[str, Path]:
        return self.project_controller.available_scene_templates()

    def save_processed_data(self, path: str) -> None:
        self.export_controller.save_processed_data(path)

    def save_project(self) -> str:
        self._cache_active_region_result_state()
        return self.project_controller.save_project()

    def create_project_region(self, file_id: str, *, name: str | None = None, base_region_id: str | None = None) -> str:
        self.project_controller.sync_active_region_runtime()
        self._cache_active_region_result_state()
        region = self.project_controller.create_region(file_id, name=name, base_region_id=base_region_id)
        self.select_project_region(region.region_id)
        return region.region_id

    def rename_project_region(self, region_id: str, new_name: str) -> None:
        self.project_controller.rename_region(region_id, new_name)

    def delete_project_region(self, region_id: str) -> None:
        self.project_controller.sync_active_region_runtime()
        self._cache_active_region_result_state()
        fallback_region_id = self.project_controller.delete_region(region_id)
        self.select_project_region(fallback_region_id)

    def update_project_region_bounds(
        self,
        region_id: str,
        *,
        trace_start: int,
        trace_stop: int,
        line_start: int,
        line_stop: int,
        sample_start: int,
        sample_stop: int,
    ) -> None:
        self.project_controller.sync_active_region_runtime()
        self.context.region_runtime_results.pop(region_id, None)
        self.project_controller.update_region_bounds(
            region_id,
            trace_start=trace_start,
            trace_stop=trace_stop,
            line_start=line_start,
            line_stop=line_stop,
            sample_start=sample_start,
            sample_stop=sample_stop,
        )
        self.select_project_region(region_id)

    def create_region_interface(self, region_id: str, *, name: str | None = None) -> str:
        interface = self.project_controller.create_interface(region_id, name=name)
        return interface.interface_id

    def rename_region_interface(self, region_id: str, interface_id: str, new_name: str) -> None:
        self.project_controller.rename_interface(region_id, interface_id, new_name)

    def delete_region_interface(self, region_id: str, interface_id: str) -> None:
        self.project_controller.delete_interface(region_id, interface_id)

    def set_region_interface_visible(self, region_id: str, interface_id: str, visible: bool) -> None:
        self.project_controller.set_interface_visible(region_id, interface_id, visible)

    def set_region_interface_point(
        self,
        region_id: str,
        interface_id: str,
        *,
        line_index: int,
        trace_index: int,
        sample_index: int | None,
    ) -> None:
        self.project_controller.set_interface_point(
            region_id,
            interface_id,
            line_index=line_index,
            trace_index=trace_index,
            sample_index=sample_index,
        )

    def set_region_interface_line_samples(
        self,
        region_id: str,
        interface_id: str,
        *,
        line_index: int,
        samples: list[float | None],
    ) -> None:
        self.project_controller.set_interface_line_samples(
            region_id,
            interface_id,
            line_index=line_index,
            samples=samples,
        )

    def clear_region_interface_line(self, region_id: str, interface_id: str, *, line_index: int) -> None:
        self.project_controller.clear_interface_line(region_id, interface_id, line_index=line_index)

    def clear_region_interface(self, region_id: str, interface_id: str) -> None:
        self.project_controller.clear_interface(region_id, interface_id)

    def fill_region_interface_line(self, region_id: str, interface_id: str, *, line_index: int) -> None:
        self.project_controller.fill_interface_line(region_id, interface_id, line_index=line_index)

    def smooth_region_interface_line(self, region_id: str, interface_id: str, *, line_index: int) -> None:
        self.project_controller.smooth_interface_line(region_id, interface_id, line_index=line_index)

    def select_project_region(self, region_id: str) -> None:
        self.project_controller.sync_active_region_runtime()
        self._cache_active_region_result_state()
        region = self.project_controller.set_active_region(region_id)
        if region is None:
            return
        region_dataset = self.project_controller.build_active_region_dataset()
        if region_dataset is None:
            file_item = self.project_controller.find_file_by_region_id(region_id)
            if file_item is not None and file_item.source_path:
                self._pending_region_selection = region_id
                self.import_data(file_item.source_path, file_item.import_params)
            return
        self.dataset_state.current_dataset = region_dataset
        self._restore_region_result_state(region.region_id, region_dataset)
        if region.pipeline_applied or region.pipeline_draft:
            self.pipeline_controller.restore_project_state(
                region.pipeline_draft,
                region.pipeline_applied,
                region.pipeline_dirty,
            )
        else:
            self.pipeline_controller.initialize_for_dataset(region_dataset)
        self.display_controller.restore_state(region.display_state)
        self.selection_state.line_index = 0
        self.selection_state.trace_index = 0
        self.selection_state.sample_index = 0
        self.display_controller.select_line(int(region.selection_state.line_index))
        self.display_controller.select_trace(int(region.selection_state.trace_index))
        self.display_controller.select_sample(int(region.selection_state.sample_index))
        if self._has_renderable_result():
            self.display_controller.publish_display()
        else:
            self.signals.display_cleared.emit()

    @property
    def is_restoring_project(self) -> bool:
        return self._restoring_project

    def _apply_pending_project_restore(self) -> None:
        restore = self._pending_project_restore
        self._pending_project_restore = None
        if restore is None:
            self._restoring_project = False
            return
        self.pipeline_controller.restore_project_state(
            restore.get("draft_steps", []),
            restore.get("applied_steps", []),
            bool(restore.get("pipeline_dirty", False)),
        )
        self.display_controller.restore_state(restore.get("display_state", self.display_state))
        selection_state = restore.get("selection_state", self.selection_state)
        self.display_controller.select_line(int(selection_state.line_index))
        self.display_controller.select_trace(int(selection_state.trace_index))
        self.display_controller.select_sample(int(selection_state.sample_index))
        self._restoring_project = False
        self.signals.status_message.emit("工程已恢复，可直接点击“开始处理”。")

    def _has_renderable_result(self) -> bool:
        snapshot = self.result_state.active_snapshot
        return snapshot is not None and snapshot.pipeline_index > 0

    def _cache_active_region_result_state(self) -> None:
        region = self.project_controller.get_active_region()
        if region is None:
            return
        if not self.result_state.history or not self._has_renderable_result():
            self.context.region_runtime_results.pop(region.region_id, None)
            return
        self.context.region_runtime_results[region.region_id] = list(self.result_state.history)

    def _restore_region_result_state(self, region_id: str, dataset: DatasetRecord) -> None:
        cached = self.context.region_runtime_results.get(region_id)
        self.context.snapshot_cache.clear()
        if cached:
            self.result_state.history = list(cached)
            self.result_state.active_snapshot = self.result_state.history[-1]
            self.context.snapshot_cache.store_many(self.result_state.history)
            return
        initial_snapshot = self.context.pipeline_executor.create_initial_snapshot(dataset)
        self.result_state.history = [initial_snapshot]
        self.result_state.active_snapshot = initial_snapshot
