from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil
import uuid

import numpy as np

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.domain.models.display import DisplayState, SelectionState
from gpr_lab_pro.domain.models.project import ProjectFileState, ProjectRegionState


class ProjectController:
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
        return ProjectFileState(
            file_id=file_id,
            dataset_id=dataset_id,
            name=dataset.filename,
            source_path=dataset.source_path,
            import_params=dict(dataset.import_params),
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
