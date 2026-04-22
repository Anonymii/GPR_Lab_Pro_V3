from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import uuid

import numpy as np

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.domain.models.display import DisplayState, SelectionState
from gpr_lab_pro.domain.models.project import (
    InterfaceTrace,
    NavigationSample,
    NavigationTrack,
    ProjectFileState,
    ProjectRegionState,
)


class ProjectController:
    _INTERFACE_COLORS = (
        "#ff8c42",
        "#00a8e8",
        "#7b61ff",
        "#00b894",
        "#e63946",
        "#ffb703",
    )

    def __init__(self, context: ApplicationContext) -> None:
        self.context = context

    @property
    def state(self):
        return self.context.project_state

    def create_project(self, name: str, root_path: str) -> None:
        project_name = name.strip() or "未命名项目"
        root = Path(root_path)
        project_root = root if root.name == project_name else root / project_name
        project_root.mkdir(parents=True, exist_ok=True)
        for child in ("data", "results", "templates"):
            (project_root / child).mkdir(exist_ok=True)

        self._reset_runtime_state()
        self.state.name = project_name
        self.state.root_path = str(project_root)
        self.state.is_open = True
        self.state.last_opened_file = ""
        self.state.project_file = str(project_root / f"{self.state.name}.gpr.json")
        self.state.files = []
        self.state.active_file_id = ""
        self.state.active_region_id = ""

        self.context.project_store.save(Path(self.state.project_file), self.state)
        self.context.signals.project_changed.emit(self.state)

    def open_project(self, project_file: str) -> dict[str, object]:
        loaded = self.context.project_store.load(project_file)
        self._reset_runtime_state()
        project_state = loaded["project_state"]
        self.state.name = project_state.name or "未命名项目"
        self.state.root_path = project_state.root_path
        self.state.is_open = True
        self.state.last_opened_file = project_state.last_opened_file
        self.state.project_file = project_state.project_file or str(project_file)
        self.state.files = deepcopy(project_state.files)
        self.state.active_file_id = project_state.active_file_id
        self.state.active_region_id = project_state.active_region_id
        self.context.signals.project_changed.emit(self.state)
        return loaded

    def mark_dataset_loaded(self, dataset: DatasetRecord) -> ProjectRegionState:
        existing_file = self._find_file_for_dataset(dataset)
        if existing_file is None:
            existing_file = self._create_file_entry(dataset)
            self.state.files.append(existing_file)
        else:
            dataset.dataset_id = existing_file.dataset_id
            existing_file.name = dataset.filename
            existing_file.source_path = dataset.source_path or existing_file.source_path
            existing_file.import_params = dict(dataset.import_params)
            if not existing_file.regions:
                existing_file.regions.append(self._create_default_region(dataset))
            for region in existing_file.regions:
                region.dataset_id = existing_file.dataset_id
            self._ensure_navigation_for_file(existing_file, dataset)

        self.state.is_open = True
        self.state.last_opened_file = dataset.filename
        self.state.active_file_id = existing_file.file_id
        self.state.active_region_id = existing_file.regions[0].region_id
        self.context.signals.project_changed.emit(self.state)
        return existing_file.regions[0]

    def get_active_file(self) -> ProjectFileState | None:
        if not self.state.active_file_id:
            return self.state.files[0] if self.state.files else None
        return next((item for item in self.state.files if item.file_id == self.state.active_file_id), None)

    def get_file(self, file_id: str) -> ProjectFileState | None:
        return next((item for item in self.state.files if item.file_id == file_id), None)

    def get_active_region(self) -> ProjectRegionState | None:
        active_file = self.get_active_file()
        if active_file is None:
            return None
        if not self.state.active_region_id and active_file.regions:
            return active_file.regions[0]
        return next((item for item in active_file.regions if item.region_id == self.state.active_region_id), None)

    def get_region(self, region_id: str) -> ProjectRegionState | None:
        for file_item in self.state.files:
            for region in file_item.regions:
                if region.region_id == region_id:
                    return region
        return None

    def set_active_region(self, region_id: str) -> ProjectRegionState | None:
        for file_item in self.state.files:
            for region in file_item.regions:
                if region.region_id != region_id:
                    continue
                self.state.active_file_id = file_item.file_id
                self.state.active_region_id = region.region_id
                self.state.last_opened_file = file_item.name
                self.context.signals.project_changed.emit(self.state)
                return region
        return None

    def find_file_by_region_id(self, region_id: str) -> ProjectFileState | None:
        for file_item in self.state.files:
            if any(region.region_id == region_id for region in file_item.regions):
                return file_item
        return None

    def get_dataset_for_file(self, file_id: str) -> DatasetRecord | None:
        file_item = self.get_file(file_id)
        if file_item is None:
            return None
        return self.context.dataset_state.datasets_by_id.get(file_item.dataset_id)

    def build_active_region_dataset(self) -> DatasetRecord | None:
        region = self.get_active_region()
        if region is None:
            return None
        return self.build_region_dataset(region)

    def build_region_dataset(self, region: ProjectRegionState) -> DatasetRecord | None:
        dataset = self.context.dataset_state.datasets_by_id.get(region.dataset_id)
        if dataset is None:
            return None
        return dataset.crop_region(
            sample_start=region.sample_start,
            sample_stop=region.sample_stop,
            trace_start=region.trace_start,
            trace_stop=region.trace_stop,
            line_start=region.line_start,
            line_stop=region.line_stop,
            region_name=f"{dataset.filename} - {region.name}",
        )

    def overview_depth_sample_index(self) -> int:
        return int(self.state.overview_state.depth_sample_index)

    def set_overview_depth_sample_index(self, sample_index: int) -> int:
        value = max(0, int(sample_index))
        region = self.get_active_region()
        if region is not None:
            value = int(np.clip(value, 0, max(region.sample_count() - 1, 0)))
        self.state.overview_state.depth_sample_index = value
        self.context.signals.overview_changed.emit(self.state.overview_state)
        return value

    def set_overview_map_image_path(self, path: str) -> None:
        self.state.overview_state.map_image_path = path.strip()
        self.context.signals.overview_changed.emit(self.state.overview_state)

    def clear_overview_map_image_path(self) -> None:
        self.state.overview_state.map_image_path = ""
        self.context.signals.overview_changed.emit(self.state.overview_state)

    def delete_file(self, file_id: str) -> str | None:
        index = next((idx for idx, item in enumerate(self.state.files) if item.file_id == file_id), -1)
        if index < 0:
            raise ValueError("未找到数据文件。")
        file_item = self.state.files.pop(index)
        for region in file_item.regions:
            self.context.region_runtime_results.pop(region.region_id, None)
        if not self.state.files:
            self.state.active_file_id = ""
            self.state.active_region_id = ""
            self.state.last_opened_file = ""
            self.context.signals.project_changed.emit(self.state)
            return None
        fallback_file = self.state.files[min(index, len(self.state.files) - 1)]
        fallback_region = fallback_file.regions[0] if fallback_file.regions else None
        self.state.active_file_id = fallback_file.file_id
        self.state.active_region_id = fallback_region.region_id if fallback_region is not None else ""
        self.state.last_opened_file = fallback_file.name
        self.context.signals.project_changed.emit(self.state)
        return self.state.active_region_id or None

    def get_navigation_for_file(self, file_id: str) -> NavigationTrack | None:
        file_item = self.get_file(file_id)
        if file_item is None:
            return None
        dataset = self.get_dataset_for_file(file_id)
        return self._ensure_navigation_for_file(file_item, dataset)

    def sync_active_region_runtime(self) -> None:
        region = self.get_active_region()
        if region is None:
            return
        region.pipeline_draft = [step.clone() for step in self.context.pipeline_state.draft_steps]
        region.pipeline_applied = [step.clone() for step in self.context.pipeline_state.applied_steps]
        region.pipeline_dirty = bool(self.context.pipeline_state.has_unapplied_changes)
        region.display_state = deepcopy(self.context.display_state)
        region.selection_state = deepcopy(self.context.selection_state)

    def create_region(self, file_id: str, *, name: str | None = None, base_region_id: str | None = None) -> ProjectRegionState:
        file_item = self.get_file(file_id)
        if file_item is None:
            raise ValueError("未找到对应数据文件。")
        dataset = self.get_dataset_for_file(file_id)
        if dataset is None:
            raise ValueError("请先激活该文件并完成数据加载。")

        base_region = self.get_region(base_region_id) if base_region_id else None
        region_name = self._next_region_name(file_item, preferred=name)
        if base_region is not None and base_region.dataset_id == file_item.dataset_id:
            region = deepcopy(base_region)
            region.region_id = uuid.uuid4().hex
            region.name = region_name
        else:
            region = self._create_default_region(dataset)
            region.name = region_name
        region.dataset_id = file_item.dataset_id
        file_item.regions.append(region)
        self.context.region_runtime_results.pop(region.region_id, None)
        self.state.active_file_id = file_item.file_id
        self.state.active_region_id = region.region_id
        self.context.signals.project_changed.emit(self.state)
        return region

    def rename_region(self, region_id: str, new_name: str) -> ProjectRegionState:
        region = self.get_region(region_id)
        if region is None:
            raise ValueError("未找到区域。")
        name = new_name.strip()
        if not name:
            raise ValueError("区域名称不能为空。")
        file_item = self.find_file_by_region_id(region_id)
        if file_item is not None:
            for item in file_item.regions:
                if item.region_id != region_id and item.name == name:
                    raise ValueError("同一文件下已存在同名区域。")
        region.name = name
        self.context.signals.project_changed.emit(self.state)
        return region

    def delete_region(self, region_id: str) -> str:
        file_item = self.find_file_by_region_id(region_id)
        if file_item is None:
            raise ValueError("未找到区域。")
        if len(file_item.regions) <= 1:
            raise ValueError("每个文件至少保留一个区域。")
        index = next((idx for idx, item in enumerate(file_item.regions) if item.region_id == region_id), -1)
        if index < 0:
            raise ValueError("未找到区域。")
        file_item.regions.pop(index)
        self.context.region_runtime_results.pop(region_id, None)
        fallback = file_item.regions[min(index, len(file_item.regions) - 1)]
        self.state.active_file_id = file_item.file_id
        self.state.active_region_id = fallback.region_id
        self.context.signals.project_changed.emit(self.state)
        return fallback.region_id

    def update_region_bounds(
        self,
        region_id: str,
        *,
        trace_start: int,
        trace_stop: int,
        line_start: int,
        line_stop: int,
        sample_start: int,
        sample_stop: int,
    ) -> ProjectRegionState:
        region = self.get_region(region_id)
        file_item = self.find_file_by_region_id(region_id)
        if region is None or file_item is None:
            raise ValueError("未找到区域。")
        dataset = self.get_dataset_for_file(file_item.file_id)
        if dataset is None:
            raise ValueError("请先激活该文件并完成数据加载。")

        trace0, trace1 = self._normalize_bounds(trace_start, trace_stop, dataset.trace_count)
        line0, line1 = self._normalize_bounds(line_start, line_stop, dataset.line_count)
        sample0, sample1 = self._normalize_bounds(sample_start, sample_stop, dataset.sample_count)
        region.trace_start = trace0
        region.trace_stop = trace1
        region.line_start = line0
        region.line_stop = line1
        region.sample_start = sample0
        region.sample_stop = sample1
        self.context.region_runtime_results.pop(region_id, None)
        region.selection_state.trace_index = int(np.clip(region.selection_state.trace_index, 0, max(region.trace_count() - 1, 0)))
        region.selection_state.line_index = int(np.clip(region.selection_state.line_index, 0, max(region.line_count() - 1, 0)))
        region.selection_state.sample_index = int(np.clip(region.selection_state.sample_index, 0, max(region.sample_count() - 1, 0)))
        self.context.signals.project_changed.emit(self.state)
        return region

    def create_interface(self, region_id: str, *, name: str | None = None) -> InterfaceTrace:
        region = self.get_region(region_id)
        if region is None:
            raise ValueError("未找到区域。")
        interface_name = self._next_interface_name(region, preferred=name)
        interface = InterfaceTrace(
            interface_id=uuid.uuid4().hex,
            name=interface_name,
            color=self._INTERFACE_COLORS[len(region.interfaces) % len(self._INTERFACE_COLORS)],
            visible=True,
        )
        region.interfaces.append(interface)
        self.context.signals.project_changed.emit(self.state)
        return interface

    def duplicate_interface(self, region_id: str, interface_id: str) -> InterfaceTrace:
        region = self.get_region(region_id)
        interface = self.get_interface(region_id, interface_id)
        if region is None or interface is None:
            raise ValueError("未找到界面。")
        new_interface = deepcopy(interface)
        new_interface.interface_id = uuid.uuid4().hex
        new_interface.name = self._next_interface_name(region, preferred=f"{interface.name}_copy")
        new_interface.color = self._INTERFACE_COLORS[len(region.interfaces) % len(self._INTERFACE_COLORS)]
        new_interface.visible = True
        new_interface.samples_by_line = {
            key: list(values)
            for key, values in interface.samples_by_line.items()
        }
        region.interfaces.append(new_interface)
        self.context.signals.project_changed.emit(self.state)
        return new_interface

    def rename_interface(self, region_id: str, interface_id: str, new_name: str) -> InterfaceTrace:
        region = self.get_region(region_id)
        interface = self.get_interface(region_id, interface_id)
        if region is None or interface is None:
            raise ValueError("未找到界面。")
        name = new_name.strip()
        if not name:
            raise ValueError("界面名称不能为空。")
        if any(item.interface_id != interface_id and item.name == name for item in region.interfaces):
            raise ValueError("当前区域下已存在同名界面。")
        interface.name = name
        self.context.signals.project_changed.emit(self.state)
        return interface

    def delete_interface(self, region_id: str, interface_id: str) -> None:
        region = self.get_region(region_id)
        if region is None:
            raise ValueError("未找到区域。")
        remaining = [item for item in region.interfaces if item.interface_id != interface_id]
        if len(remaining) == len(region.interfaces):
            raise ValueError("未找到界面。")
        region.interfaces = remaining
        self.context.signals.project_changed.emit(self.state)

    def get_interface(self, region_id: str, interface_id: str) -> InterfaceTrace | None:
        region = self.get_region(region_id)
        if region is None:
            return None
        return next((item for item in region.interfaces if item.interface_id == interface_id), None)

    def set_interface_visible(self, region_id: str, interface_id: str, visible: bool) -> InterfaceTrace:
        interface = self.get_interface(region_id, interface_id)
        if interface is None:
            raise ValueError("未找到界面。")
        interface.visible = bool(visible)
        self.context.signals.project_changed.emit(self.state)
        return interface

    def set_interface_point(
        self,
        region_id: str,
        interface_id: str,
        *,
        line_index: int,
        trace_index: int,
        sample_index: int | None,
    ) -> InterfaceTrace:
        region = self.get_region(region_id)
        interface = self.get_interface(region_id, interface_id)
        if region is None or interface is None:
            raise ValueError("未找到界面。")
        if region.trace_count() <= 0 or region.line_count() <= 0:
            raise ValueError("当前区域范围无效。")
        line_idx = int(np.clip(line_index, 0, max(region.line_count() - 1, 0)))
        trace_idx = int(np.clip(trace_index, 0, max(region.trace_count() - 1, 0)))
        sample_value: float | None
        if sample_index is None:
            sample_value = None
        else:
            sample_value = float(np.clip(sample_index, 0, max(region.sample_count() - 1, 0)))
        line_key = str(line_idx)
        values = list(interface.samples_by_line.get(line_key, []))
        if len(values) < region.trace_count():
            values.extend([None] * (region.trace_count() - len(values)))
        elif len(values) > region.trace_count():
            values = values[: region.trace_count()]
        values[trace_idx] = sample_value
        interface.samples_by_line[line_key] = values
        self.context.signals.project_changed.emit(self.state)
        return interface

    def set_interface_line_samples(
        self,
        region_id: str,
        interface_id: str,
        *,
        line_index: int,
        samples: list[float | None],
    ) -> InterfaceTrace:
        region = self.get_region(region_id)
        interface = self.get_interface(region_id, interface_id)
        if region is None or interface is None:
            raise ValueError("未找到界面。")
        line_idx = int(np.clip(line_index, 0, max(region.line_count() - 1, 0)))
        line_key = str(line_idx)
        values = list(samples[: region.trace_count()])
        if len(values) < region.trace_count():
            values.extend([None] * (region.trace_count() - len(values)))
        normalized: list[float | None] = []
        max_sample = max(region.sample_count() - 1, 0)
        for value in values:
            if value is None:
                normalized.append(None)
            else:
                normalized.append(float(np.clip(float(value), 0.0, max_sample)))
        interface.samples_by_line[line_key] = normalized
        self.context.signals.project_changed.emit(self.state)
        return interface

    def clear_interface_line(self, region_id: str, interface_id: str, *, line_index: int) -> InterfaceTrace:
        region = self.get_region(region_id)
        interface = self.get_interface(region_id, interface_id)
        if region is None or interface is None:
            raise ValueError("未找到界面。")
        line_idx = int(np.clip(line_index, 0, max(region.line_count() - 1, 0)))
        interface.samples_by_line[str(line_idx)] = [None] * region.trace_count()
        self.context.signals.project_changed.emit(self.state)
        return interface

    def clear_interface(self, region_id: str, interface_id: str) -> InterfaceTrace:
        region = self.get_region(region_id)
        interface = self.get_interface(region_id, interface_id)
        if region is None or interface is None:
            raise ValueError("未找到界面。")
        interface.samples_by_line = {}
        self.context.signals.project_changed.emit(self.state)
        return interface

    def fill_interface_line(self, region_id: str, interface_id: str, *, line_index: int) -> InterfaceTrace:
        region = self.get_region(region_id)
        interface = self.get_interface(region_id, interface_id)
        if region is None or interface is None:
            raise ValueError("未找到界面。")
        line_idx = int(np.clip(line_index, 0, max(region.line_count() - 1, 0)))
        line_key = str(line_idx)
        values = list(interface.samples_by_line.get(line_key, []))
        if len(values) < region.trace_count():
            values.extend([None] * (region.trace_count() - len(values)))
        known = [(idx, float(value)) for idx, value in enumerate(values) if value is not None]
        if len(known) < 2:
            raise ValueError("当前测线上至少需要两个界面点才能补线。")
        for (idx0, val0), (idx1, val1) in zip(known, known[1:]):
            if idx1 - idx0 <= 1:
                continue
            for fill_idx in range(idx0 + 1, idx1):
                ratio = (fill_idx - idx0) / (idx1 - idx0)
                values[fill_idx] = float(val0 + ratio * (val1 - val0))
        interface.samples_by_line[line_key] = values
        self.context.signals.project_changed.emit(self.state)
        return interface

    def smooth_interface_line(self, region_id: str, interface_id: str, *, line_index: int) -> InterfaceTrace:
        region = self.get_region(region_id)
        interface = self.get_interface(region_id, interface_id)
        if region is None or interface is None:
            raise ValueError("未找到界面。")
        line_idx = int(np.clip(line_index, 0, max(region.line_count() - 1, 0)))
        line_key = str(line_idx)
        values = list(interface.samples_by_line.get(line_key, []))
        if len(values) < region.trace_count():
            values.extend([None] * (region.trace_count() - len(values)))
        known = [idx for idx, value in enumerate(values) if value is not None]
        if len(known) < 3:
            raise ValueError("当前测线上至少需要三个界面点才能平滑。")
        start = known[0]
        stop = known[-1] + 1
        segment = np.array([np.nan if values[idx] is None else float(values[idx]) for idx in range(start, stop)], dtype=float)
        if np.all(np.isnan(segment)):
            raise ValueError("当前测线上没有可平滑的数据。")
        valid_idx = np.flatnonzero(~np.isnan(segment))
        if valid_idx.size >= 2:
            filled = np.interp(np.arange(segment.size, dtype=float), valid_idx.astype(float), segment[valid_idx])
        else:
            filled = np.nan_to_num(segment, nan=0.0)
        kernel = np.array([1.0, 3.0, 5.0, 6.0, 5.0, 3.0, 1.0], dtype=float)
        kernel /= kernel.sum()
        pad = kernel.size // 2
        smoothed = filled.copy()
        for _ in range(5):
            padded = np.pad(smoothed, (pad, pad), mode="edge")
            smoothed = np.convolve(padded, kernel, mode="valid")
        smoothed[0] = filled[0]
        smoothed[-1] = filled[-1]
        for offset, sample_value in enumerate(smoothed):
            values[start + offset] = float(np.clip(sample_value, 0.0, max(region.sample_count() - 1, 0)))
        interface.samples_by_line[line_key] = values
        self.context.signals.project_changed.emit(self.state)
        return interface

    def save_project(self) -> str:
        if not self.state.is_open or not self.state.root_path:
            raise ValueError("请先新建工程或打开工程。")
        self.sync_active_region_runtime()
        project_file = Path(self.state.project_file or (Path(self.state.root_path) / f"{self.state.name}.gpr.json"))
        project_file.parent.mkdir(parents=True, exist_ok=True)
        active_file = self.get_active_file()
        staged_dataset_path = ""
        if active_file is not None:
            dataset = self.context.dataset_state.datasets_by_id.get(active_file.dataset_id)
            staged_dataset_path = self._stage_dataset_file(dataset)
        self._stage_all_dataset_files()
        self.state.project_file = str(project_file)
        self.context.project_store.save(
            project_file,
            self.state,
            dataset_source_path=staged_dataset_path,
            dataset_import_params=(active_file.import_params if active_file is not None else None),
            pipeline_draft=self.context.pipeline_state.draft_steps,
            pipeline_applied=self.context.pipeline_state.applied_steps,
            pipeline_dirty=self.context.pipeline_state.has_unapplied_changes,
            display_state=self.context.display_state,
            selection_state=self.context.selection_state,
            region_results=self.context.region_runtime_results,
        )
        self.context.signals.project_changed.emit(self.state)
        return str(project_file)

    def available_scene_templates(self) -> dict[str, Path]:
        return {path.stem: path for path in sorted(self.context.settings.template_dir.glob("*.mat"))}

    def _find_file_for_dataset(self, dataset: DatasetRecord) -> ProjectFileState | None:
        source_path = str(Path(dataset.source_path).resolve()) if dataset.source_path else ""
        for file_item in self.state.files:
            file_source = str(Path(file_item.source_path).resolve()) if file_item.source_path else ""
            if source_path and file_source and source_path == file_source:
                return file_item
            if not source_path and not file_source and file_item.name == dataset.filename:
                return file_item
        return None

    def _create_file_entry(self, dataset: DatasetRecord) -> ProjectFileState:
        file_id = uuid.uuid4().hex
        dataset_id = dataset.dataset_id or uuid.uuid4().hex
        dataset.dataset_id = dataset_id
        region = self._create_default_region(dataset)
        navigation = self._create_simulated_navigation(dataset, file_index=len(self.state.files))
        return ProjectFileState(
            file_id=file_id,
            dataset_id=dataset_id,
            name=dataset.filename,
            source_path=dataset.source_path,
            import_params=dict(dataset.import_params),
            navigation=navigation,
            regions=[region],
        )

    def _create_default_region(self, dataset: DatasetRecord) -> ProjectRegionState:
        return ProjectRegionState(
            region_id=uuid.uuid4().hex,
            dataset_id=dataset.dataset_id,
            name="Region1",
            trace_start=0,
            trace_stop=max(dataset.trace_count, 1),
            line_start=0,
            line_stop=max(dataset.line_count, 1),
            sample_start=0,
            sample_stop=max(dataset.sample_count, 1),
            display_state=DisplayState(),
            selection_state=SelectionState(
                line_index=max(0, dataset.line_count // 2),
                trace_index=max(0, dataset.trace_count // 2),
                sample_index=max(0, dataset.sample_count // 4),
            ),
        )

    def _stage_all_dataset_files(self) -> None:
        for file_item in self.state.files:
            dataset = self.context.dataset_state.datasets_by_id.get(file_item.dataset_id)
            staged = self._stage_dataset_file(dataset)
            if staged:
                file_item.source_path = staged

    def _stage_dataset_file(self, dataset: DatasetRecord | None) -> str:
        if dataset is None or not dataset.source_path or not self.state.root_path:
            return ""
        source = Path(dataset.source_path)
        if not source.exists():
            return str(source)
        project_data_dir = Path(self.state.root_path) / "data"
        project_data_dir.mkdir(parents=True, exist_ok=True)
        target = project_data_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        return str(target)

    def _ensure_navigation_for_file(
        self,
        file_item: ProjectFileState,
        dataset: DatasetRecord | None,
    ) -> NavigationTrack:
        trace_count = dataset.trace_count if dataset is not None else 0
        if trace_count <= 0:
            return file_item.navigation
        if file_item.navigation.samples and len(file_item.navigation.samples) == trace_count:
            return file_item.navigation
        file_index = next((idx for idx, item in enumerate(self.state.files) if item.file_id == file_item.file_id), 0)
        file_item.navigation = self._create_simulated_navigation(dataset, file_index=file_index)
        return file_item.navigation

    def _create_simulated_navigation(self, dataset: DatasetRecord, *, file_index: int) -> NavigationTrack:
        trace_count = max(int(dataset.trace_count), 1)
        spacing_m = 0.05
        total_length = max(trace_count - 1, 1) * spacing_m
        angle = np.deg2rad(12.0 + (file_index % 5) * 7.5)
        base_x = 12.0 + file_index * 16.0
        base_y = 12.0 + file_index * 10.0
        direction = np.array([np.cos(angle), np.sin(angle)], dtype=float)
        normal = np.array([-direction[1], direction[0]], dtype=float)
        samples: list[NavigationSample] = []
        for trace_idx in range(trace_count):
            t = trace_idx / max(trace_count - 1, 1)
            along = total_length * t
            lateral = 1.2 * np.sin(t * np.pi * 1.35 + file_index * 0.35)
            point = np.array([base_x, base_y], dtype=float) + direction * along + normal * lateral
            samples.append(
                NavigationSample(
                    trace_index=trace_idx,
                    x=float(point[0]),
                    y=float(point[1]),
                    timestamp_s=float(trace_idx * 0.05),
                )
            )
        return NavigationTrack(mode="simulated", samples=samples)

    @staticmethod
    def _normalize_bounds(start: int, stop: int, full_count: int) -> tuple[int, int]:
        if full_count <= 0:
            return (0, 1)
        start_idx = int(max(0, min(int(start), full_count - 1)))
        stop_idx = int(max(start_idx + 1, min(int(stop), full_count)))
        return (start_idx, stop_idx)

    @staticmethod
    def _next_region_name(file_item: ProjectFileState, *, preferred: str | None = None) -> str:
        if preferred:
            clean = preferred.strip()
            if clean and all(region.name != clean for region in file_item.regions):
                return clean
        used = {region.name for region in file_item.regions}
        index = 1
        while True:
            candidate = f"Region{index}"
            if candidate not in used:
                return candidate
            index += 1

    def _reset_runtime_state(self) -> None:
        self.context.snapshot_cache.clear()
        self.context.dataset_state.current_dataset = None
        self.context.dataset_state.import_in_progress = False
        self.context.dataset_state.datasets_by_id = {}
        self.context.pipeline_state.draft_steps = []
        self.context.pipeline_state.applied_steps = []
        self.context.pipeline_state.has_unapplied_changes = False
        self.context.result_state.history = []
        self.context.result_state.active_snapshot = None
        self.context.region_runtime_results = {}
        self.context.selection_state.line_index = 0
        self.context.selection_state.trace_index = 0
        self.context.selection_state.sample_index = 0
        self.context.display_state.contrast_gain = 4.0
        self.context.display_state.slice_thickness = 5
        self.context.display_state.bscan_attr = "Real"
        self.context.display_state.cscan_attr = "Envelope"
        self.context.display_state.start_time_ns = 0.0
        self.context.display_state.end_time_ns = 60.0
        self.context.display_state.colormap = "gray"
        self.context.display_state.invert = False
        self.context.display_state.show_axes = True
        self.context.signals.pipeline_changed.emit([])
        self.context.signals.pipeline_applied.emit([])

    @staticmethod
    def _next_interface_name(region: ProjectRegionState, *, preferred: str | None = None) -> str:
        if preferred:
            clean = preferred.strip()
            if clean and all(item.name != clean for item in region.interfaces):
                return clean
        used = {item.name for item in region.interfaces}
        index = 1
        while True:
            candidate = f"Interface{index}"
            if candidate not in used:
                return candidate
            index += 1
