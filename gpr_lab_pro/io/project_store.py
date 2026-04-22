from __future__ import annotations

import json
import pickle
from dataclasses import asdict
from pathlib import Path

from gpr_lab_pro.domain.enums import DataDomain, StepKind
from gpr_lab_pro.domain.models.display import DisplayState, SelectionState
from gpr_lab_pro.domain.models.pipeline import PipelineStep
from gpr_lab_pro.domain.models.project import (
    InterfaceTrace,
    NavigationSample,
    NavigationTrack,
    OverviewState,
    ProjectFileState,
    ProjectRegionState,
    ProjectState,
)
from gpr_lab_pro.domain.models.results import ResultSnapshot
from gpr_lab_pro.io.importer import DataImportParameters, ISDFTParameters


class ProjectStore:
    def save(
        self,
        path: str | Path,
        project: ProjectState,
        *,
        dataset_source_path: str = "",
        dataset_import_params: dict | DataImportParameters | None = None,
        pipeline_draft: list[PipelineStep] | None = None,
        pipeline_applied: list[PipelineStep] | None = None,
        pipeline_dirty: bool = False,
        display_state: DisplayState | None = None,
        selection_state: SelectionState | None = None,
        region_results: dict[str, list[ResultSnapshot]] | None = None,
    ) -> None:
        path = Path(path).resolve()
        project_root = path.parent
        result_relpaths = self._persist_region_results(project_root, region_results or {})
        path.write_text(
            json.dumps(
                {
                    "project": {
                        "name": project.name,
                        "root_path": ".",
                        "is_open": project.is_open,
                        "last_opened_file": project.last_opened_file,
                        "project_file": path.name,
                        "active_file_id": project.active_file_id,
                        "active_region_id": project.active_region_id,
                        "overview_state": self._serialize_overview_state(project.overview_state, project_root),
                        "files": [self._serialize_file(item, project_root, result_relpaths) for item in project.files],
                    },
                    "dataset": {
                        "source_path": self._to_project_relative(dataset_source_path, project_root),
                        "import_params": self._serialize_import_params(dataset_import_params),
                    },
                    "pipeline": {
                        "draft_steps": [self._serialize_step(step) for step in (pipeline_draft or [])],
                        "applied_steps": [self._serialize_step(step) for step in (pipeline_applied or [])],
                        "has_unapplied_changes": bool(pipeline_dirty),
                    },
                    "display": asdict(display_state or DisplayState()),
                    "selection": asdict(selection_state or SelectionState()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def load(self, path: str | Path) -> dict[str, object]:
        source_path = Path(path).resolve()
        project_root = source_path.parent
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if "project" not in payload:
            payload = {"project": payload}
        project_payload = payload.get("project", {})
        dataset_payload = payload.get("dataset", {})
        pipeline_payload = payload.get("pipeline", {})
        display_payload = payload.get("display", {})
        selection_payload = payload.get("selection", {})
        files = [self._deserialize_file(item, project_root) for item in project_payload.get("files", [])]
        region_results: dict[str, list[ResultSnapshot]] = {}
        for file_item in files:
            for region in file_item.regions:
                if region.result_relpath:
                    loaded = self._load_region_result(project_root / region.result_relpath)
                    if loaded:
                        region_results[region.region_id] = loaded
        active_file_id = str(project_payload.get("active_file_id", "") or "")
        active_region_id = str(project_payload.get("active_region_id", "") or "")
        if files and not active_file_id:
            active_file_id = files[0].file_id
        if files and not active_region_id:
            first_region = files[0].regions[0] if files[0].regions else None
            active_region_id = first_region.region_id if first_region is not None else ""
        return {
            "project_state": ProjectState(
                name=project_payload.get("name", "未命名项目"),
                root_path=str(project_root),
                is_open=bool(project_payload.get("is_open", True)),
                last_opened_file=project_payload.get("last_opened_file", ""),
                project_file=str(source_path),
                files=files,
                active_file_id=active_file_id,
                active_region_id=active_region_id,
                overview_state=self._deserialize_overview_state(project_payload.get("overview_state", {}), project_root),
            ),
            "dataset_source_path": self._resolve_project_path(dataset_payload.get("source_path", ""), project_root),
            "dataset_import_params": self._deserialize_import_params(dataset_payload.get("import_params")),
            "draft_steps": [self._deserialize_step(item) for item in pipeline_payload.get("draft_steps", [])],
            "applied_steps": [self._deserialize_step(item) for item in pipeline_payload.get("applied_steps", [])],
            "pipeline_dirty": bool(pipeline_payload.get("has_unapplied_changes", False)),
            "display_state": DisplayState(**display_payload) if display_payload else DisplayState(),
            "selection_state": SelectionState(**selection_payload) if selection_payload else SelectionState(),
            "region_results": region_results,
        }

    @staticmethod
    def _serialize_step(step: PipelineStep) -> dict[str, object]:
        return {
            "op_type": step.op_type,
            "name": step.name,
            "category": step.category,
            "kind": step.kind.value,
            "params": list(step.params),
            "enabled": step.enabled,
            "step_id": step.step_id,
        }

    def _serialize_file(
        self,
        item: ProjectFileState,
        project_root: Path,
        result_relpaths: dict[str, str],
    ) -> dict[str, object]:
        return {
            "file_id": item.file_id,
            "dataset_id": item.dataset_id,
            "name": item.name,
            "source_path": self._to_project_relative(item.source_path, project_root),
            "import_params": self._serialize_import_params(item.import_params),
            "navigation": self._serialize_navigation(item.navigation, project_root),
            "regions": [self._serialize_region(region, result_relpaths) for region in item.regions],
        }

    def _serialize_region(self, item: ProjectRegionState, result_relpaths: dict[str, str]) -> dict[str, object]:
        return {
            "region_id": item.region_id,
            "dataset_id": item.dataset_id,
            "name": item.name,
            "result_relpath": result_relpaths.get(item.region_id, item.result_relpath or ""),
            "trace_start": item.trace_start,
            "trace_stop": item.trace_stop,
            "line_start": item.line_start,
            "line_stop": item.line_stop,
            "sample_start": item.sample_start,
            "sample_stop": item.sample_stop,
            "pipeline_draft": [self._serialize_step(step) for step in item.pipeline_draft],
            "pipeline_applied": [self._serialize_step(step) for step in item.pipeline_applied],
            "pipeline_dirty": bool(item.pipeline_dirty),
            "display_state": asdict(item.display_state),
            "selection_state": asdict(item.selection_state),
            "interfaces": [self._serialize_interface(trace) for trace in item.interfaces],
        }

    @staticmethod
    def _serialize_interface(item: InterfaceTrace) -> dict[str, object]:
        return {
            "interface_id": item.interface_id,
            "name": item.name,
            "color": item.color,
            "visible": bool(item.visible),
            "samples_by_line": item.samples_by_line,
        }

    def _serialize_navigation(self, item: NavigationTrack, project_root: Path) -> dict[str, object]:
        return {
            "mode": item.mode,
            "source_path": self._to_project_relative(item.source_path, project_root),
            "samples": [self._serialize_navigation_sample(sample) for sample in item.samples],
        }

    @staticmethod
    def _serialize_navigation_sample(item: NavigationSample) -> dict[str, object]:
        return {
            "trace_index": int(item.trace_index),
            "x": float(item.x),
            "y": float(item.y),
            "timestamp_s": None if item.timestamp_s is None else float(item.timestamp_s),
            "latitude": None if item.latitude is None else float(item.latitude),
            "longitude": None if item.longitude is None else float(item.longitude),
        }

    def _serialize_overview_state(self, item: OverviewState, project_root: Path) -> dict[str, object]:
        return {
            "depth_sample_index": int(item.depth_sample_index),
            "map_image_path": self._to_project_relative(item.map_image_path, project_root),
            "map_opacity": float(item.map_opacity),
        }

    @staticmethod
    def _deserialize_step(payload: dict[str, object]) -> PipelineStep:
        return PipelineStep(
            op_type=str(payload.get("op_type", "")),
            name=str(payload.get("name", "")),
            category=str(payload.get("category", "")),
            kind=StepKind(str(payload.get("kind", StepKind.TRANSFORM.value))),
            params=tuple(float(value) for value in payload.get("params", [])),
            enabled=bool(payload.get("enabled", True)),
            step_id=str(payload.get("step_id", "")),
        )

    def _deserialize_file(self, payload: dict[str, object], project_root: Path) -> ProjectFileState:
        return ProjectFileState(
            file_id=str(payload.get("file_id", "")),
            dataset_id=str(payload.get("dataset_id", "")),
            name=str(payload.get("name", "")),
            source_path=self._resolve_project_path(payload.get("source_path", ""), project_root),
            import_params=self._serialize_import_params(payload.get("import_params")),
            navigation=self._deserialize_navigation(payload.get("navigation", {}), project_root),
            regions=[self._deserialize_region(item) for item in payload.get("regions", [])],
        )

    def _deserialize_region(self, payload: dict[str, object]) -> ProjectRegionState:
        return ProjectRegionState(
            region_id=str(payload.get("region_id", "")),
            dataset_id=str(payload.get("dataset_id", "")),
            name=str(payload.get("name", "")),
            result_relpath=str(payload.get("result_relpath", "")),
            trace_start=int(payload.get("trace_start", 0)),
            trace_stop=int(payload.get("trace_stop", 0)),
            line_start=int(payload.get("line_start", 0)),
            line_stop=int(payload.get("line_stop", 0)),
            sample_start=int(payload.get("sample_start", 0)),
            sample_stop=int(payload.get("sample_stop", 0)),
            pipeline_draft=[self._deserialize_step(item) for item in payload.get("pipeline_draft", [])],
            pipeline_applied=[self._deserialize_step(item) for item in payload.get("pipeline_applied", [])],
            pipeline_dirty=bool(payload.get("pipeline_dirty", False)),
            display_state=DisplayState(**payload.get("display_state", {})) if payload.get("display_state") else DisplayState(),
            selection_state=SelectionState(**payload.get("selection_state", {})) if payload.get("selection_state") else SelectionState(),
            interfaces=[self._deserialize_interface(item) for item in payload.get("interfaces", [])],
        )

    @staticmethod
    def _deserialize_interface(payload: dict[str, object]) -> InterfaceTrace:
        samples = payload.get("samples_by_line", {})
        return InterfaceTrace(
            interface_id=str(payload.get("interface_id", "")),
            name=str(payload.get("name", "")),
            color=str(payload.get("color", "#ff8c42")),
            visible=bool(payload.get("visible", True)),
            samples_by_line=dict(samples) if isinstance(samples, dict) else {},
        )

    def _deserialize_navigation(self, payload: dict[str, object], project_root: Path) -> NavigationTrack:
        if not isinstance(payload, dict):
            return NavigationTrack()
        return NavigationTrack(
            mode=str(payload.get("mode", "simulated") or "simulated"),
            source_path=self._resolve_project_path(payload.get("source_path", ""), project_root),
            samples=[
                self._deserialize_navigation_sample(item)
                for item in payload.get("samples", [])
                if isinstance(item, dict)
            ],
        )

    @staticmethod
    def _deserialize_navigation_sample(payload: dict[str, object]) -> NavigationSample:
        return NavigationSample(
            trace_index=int(payload.get("trace_index", 0)),
            x=float(payload.get("x", 0.0)),
            y=float(payload.get("y", 0.0)),
            timestamp_s=(None if payload.get("timestamp_s") is None else float(payload.get("timestamp_s"))),
            latitude=(None if payload.get("latitude") is None else float(payload.get("latitude"))),
            longitude=(None if payload.get("longitude") is None else float(payload.get("longitude"))),
        )

    def _deserialize_overview_state(self, payload: dict[str, object], project_root: Path) -> OverviewState:
        if not isinstance(payload, dict):
            return OverviewState()
        return OverviewState(
            depth_sample_index=int(payload.get("depth_sample_index", 0)),
            map_image_path=self._resolve_project_path(payload.get("map_image_path", ""), project_root),
            map_opacity=float(payload.get("map_opacity", 1.0) or 1.0),
        )

    @staticmethod
    def _serialize_import_params(params: dict | DataImportParameters | None) -> dict[str, object]:
        if params is None:
            return asdict(DataImportParameters())
        if isinstance(params, dict):
            return params
        return asdict(params)

    @staticmethod
    def _deserialize_import_params(payload: dict | None) -> DataImportParameters:
        if not payload:
            return DataImportParameters()
        payload = dict(payload)
        isdft_payload = dict(payload.get("isdft", {}))
        payload["isdft"] = ISDFTParameters(**isdft_payload) if isdft_payload else ISDFTParameters()
        return DataImportParameters(**payload)

    @staticmethod
    def _to_project_relative(path_value: str | Path, project_root: Path) -> str:
        if not path_value:
            return ""
        candidate = Path(path_value)
        try:
            resolved = candidate.resolve()
        except Exception:
            return str(candidate).replace("\\", "/")
        try:
            return str(resolved.relative_to(project_root.resolve())).replace("\\", "/")
        except Exception:
            return str(resolved)

    @staticmethod
    def _resolve_project_path(path_value: str | Path, project_root: Path) -> str:
        if not path_value:
            return ""
        candidate = Path(path_value)
        if candidate.is_absolute():
            return str(candidate)
        return str((project_root / candidate).resolve())

    def _persist_region_results(
        self,
        project_root: Path,
        region_results: dict[str, list[ResultSnapshot]],
    ) -> dict[str, str]:
        results_dir = project_root / "results" / "regions"
        results_dir.mkdir(parents=True, exist_ok=True)
        relpaths: dict[str, str] = {}
        keep_files: set[Path] = set()
        for region_id, snapshots in region_results.items():
            if not snapshots or snapshots[-1].pipeline_index <= 0:
                continue
            relpath = Path("results") / "regions" / f"{region_id}.pkl"
            fullpath = project_root / relpath
            keep_files.add(fullpath.resolve())
            payload = [self._serialize_snapshot(snapshot) for snapshot in snapshots]
            with fullpath.open("wb") as handle:
                pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            relpaths[region_id] = str(relpath).replace("\\", "/")
        for existing in results_dir.glob("*.pkl"):
            if existing.resolve() not in keep_files:
                existing.unlink(missing_ok=True)
        return relpaths

    def _load_region_result(self, path: Path) -> list[ResultSnapshot]:
        if not path.exists():
            return []
        with path.open("rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, list):
            return []
        return [self._deserialize_snapshot(item) for item in payload if isinstance(item, dict)]

    @staticmethod
    def _serialize_snapshot(snapshot: ResultSnapshot) -> dict[str, object]:
        return {
            "data": snapshot.data,
            "domain": snapshot.domain.value,
            "step_name": snapshot.step_name,
            "params": list(snapshot.params),
            "meta": snapshot.meta,
            "pipeline_index": snapshot.pipeline_index,
            "parent_snapshot_id": snapshot.parent_snapshot_id,
            "is_cached": bool(snapshot.is_cached),
            "render_ready": bool(snapshot.render_ready),
            "snapshot_id": snapshot.snapshot_id,
            "created_at": snapshot.created_at,
        }

    @staticmethod
    def _deserialize_snapshot(payload: dict[str, object]) -> ResultSnapshot:
        return ResultSnapshot(
            data=payload["data"],
            domain=DataDomain(str(payload.get("domain", DataDomain.TIME.value))),
            step_name=str(payload.get("step_name", "")),
            params=tuple(float(value) for value in payload.get("params", [])),
            meta=dict(payload.get("meta", {})),
            pipeline_index=int(payload.get("pipeline_index", 0)),
            parent_snapshot_id=payload.get("parent_snapshot_id"),
            is_cached=bool(payload.get("is_cached", True)),
            render_ready=bool(payload.get("render_ready", False)),
            snapshot_id=str(payload.get("snapshot_id", "")),
            created_at=str(payload.get("created_at", "")),
        )
