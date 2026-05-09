from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.domain.models.display import DisplayState, SelectionState
from gpr_lab_pro.domain.models.pipeline import PipelineStep


@dataclass
class InterfaceTrace:
    interface_id: str
    name: str
    color: str = "#ff8c42"
    visible: bool = True
    samples_by_line: dict[str, list[float | None]] = field(default_factory=dict)


@dataclass
class NavigationSample:
    trace_index: int
    x: float
    y: float
    timestamp_s: float | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class NavigationTrack:
    mode: str = "simulated"
    source_path: str = ""
    samples: list[NavigationSample] = field(default_factory=list)


@dataclass
class OverviewState:
    depth_sample_index: int = 0
    map_image_path: str = ""
    map_opacity: float = 1.0


@dataclass
class ProjectMapState:
    map_id: str
    name: str
    image_path: str = ""
    world_file_path: str = ""
    coordinate_system: str = ""
    opacity: float = 1.0
    visible: bool = True
    kind: str = "geo_image"


@dataclass
class AnnotationState:
    annotation_id: str
    name: str
    kind: str = "point"
    x: float = 0.0
    y: float = 0.0
    z: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    note: str = ""
    visible: bool = True


@dataclass
class AnnotationGroupState:
    group_id: str
    name: str
    color: str = "#ffb703"
    visible: bool = True
    annotations: list[AnnotationState] = field(default_factory=list)


@dataclass
class StitchingState:
    stitching_id: str
    name: str
    stitching_type: str = "rectangular"
    source_region_ids: list[str] = field(default_factory=list)
    base_region_id: str = ""
    resolution_x: float = 0.0
    resolution_y: float = 0.0
    left_width_m: float = 0.0
    right_width_m: float = 0.0
    status: str = "placeholder"
    visible: bool = True


@dataclass
class ProjectRegionState:
    region_id: str
    dataset_id: str
    name: str
    result_relpath: str = ""
    trace_start: int = 0
    trace_stop: int = 0
    line_start: int = 0
    line_stop: int = 0
    sample_start: int = 0
    sample_stop: int = 0
    pipeline_draft: list[PipelineStep] = field(default_factory=list)
    pipeline_applied: list[PipelineStep] = field(default_factory=list)
    pipeline_dirty: bool = False
    display_state: DisplayState = field(default_factory=DisplayState)
    selection_state: SelectionState = field(default_factory=SelectionState)
    interfaces: list[InterfaceTrace] = field(default_factory=list)

    def trace_count(self) -> int:
        return max(0, int(self.trace_stop) - int(self.trace_start))

    def line_count(self) -> int:
        return max(0, int(self.line_stop) - int(self.line_start))

    def sample_count(self) -> int:
        return max(0, int(self.sample_stop) - int(self.sample_start))


@dataclass
class ProjectFileState:
    file_id: str
    dataset_id: str
    name: str
    source_path: str = ""
    import_params: dict[str, Any] = field(default_factory=dict)
    navigation: NavigationTrack = field(default_factory=NavigationTrack)
    regions: list[ProjectRegionState] = field(default_factory=list)


@dataclass
class ProjectState:
    name: str = "未命名项目"
    root_path: str = ""
    is_open: bool = False
    last_opened_file: str = ""
    project_file: str = ""
    files: list[ProjectFileState] = field(default_factory=list)
    maps: list[ProjectMapState] = field(default_factory=list)
    annotation_groups: list[AnnotationGroupState] = field(default_factory=list)
    stitchings: list[StitchingState] = field(default_factory=list)
    active_file_id: str = ""
    active_region_id: str = ""
    overview_state: OverviewState = field(default_factory=OverviewState)


@dataclass
class DatasetState:
    current_dataset: DatasetRecord | None = None
    import_in_progress: bool = False
    datasets_by_id: dict[str, DatasetRecord] = field(default_factory=dict)
