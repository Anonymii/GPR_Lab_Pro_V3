from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from gpr_lab_pro.domain.enums import StepKind
from gpr_lab_pro.domain.models.display import DisplayState, SelectionState
from gpr_lab_pro.domain.models.pipeline import PipelineStep
from gpr_lab_pro.domain.models.project import (
    InterfaceTrace,
    ProjectFileState,
    ProjectRegionState,
    ProjectState,
)
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
    ) -> None:
        path = Path(path)
        path.write_text(
            json.dumps(
                {
                    "project": {
                        "name": project.name,
                        "root_path": project.root_path,
                        "is_open": project.is_open,
                        "last_opened_file": project.last_opened_file,
                        "project_file": str(path),
                        "active_file_id": project.active_file_id,
                        "active_region_id": project.active_region_id,
                        "files": [self._serialize_file(item) for item in project.files],
                    },
                    "dataset": {
                        "source_path": dataset_source_path,
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
        source_path = Path(path)
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        if "project" not in payload:
            payload = {"project": payload}
        project_payload = payload.get("project", {})
        dataset_payload = payload.get("dataset", {})
        pipeline_payload = payload.get("pipeline", {})
        display_payload = payload.get("display", {})
        selection_payload = payload.get("selection", {})
        files = [self._deserialize_file(item) for item in project_payload.get("files", [])]
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
                root_path=project_payload.get("root_path", ""),
                is_open=bool(project_payload.get("is_open", True)),
                last_opened_file=project_payload.get("last_opened_file", ""),
                project_file=project_payload.get("project_file", str(source_path)),
                files=files,
                active_file_id=active_file_id,
                active_region_id=active_region_id,
            ),
            "dataset_source_path": dataset_payload.get("source_path", ""),
            "dataset_import_params": self._deserialize_import_params(dataset_payload.get("import_params")),
            "draft_steps": [self._deserialize_step(item) for item in pipeline_payload.get("draft_steps", [])],
            "applied_steps": [self._deserialize_step(item) for item in pipeline_payload.get("applied_steps", [])],
            "pipeline_dirty": bool(pipeline_payload.get("has_unapplied_changes", False)),
            "display_state": DisplayState(**display_payload) if display_payload else DisplayState(),
            "selection_state": SelectionState(**selection_payload) if selection_payload else SelectionState(),
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

    def _serialize_file(self, item: ProjectFileState) -> dict[str, object]:
        return {
            "file_id": item.file_id,
            "dataset_id": item.dataset_id,
            "name": item.name,
            "source_path": item.source_path,
            "import_params": self._serialize_import_params(item.import_params),
            "regions": [self._serialize_region(region) for region in item.regions],
        }

    def _serialize_region(self, item: ProjectRegionState) -> dict[str, object]:
        return {
            "region_id": item.region_id,
            "dataset_id": item.dataset_id,
            "name": item.name,
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

    def _deserialize_file(self, payload: dict[str, object]) -> ProjectFileState:
        return ProjectFileState(
            file_id=str(payload.get("file_id", "")),
            dataset_id=str(payload.get("dataset_id", "")),
            name=str(payload.get("name", "")),
            source_path=str(payload.get("source_path", "")),
            import_params=self._serialize_import_params(payload.get("import_params")),
            regions=[self._deserialize_region(item) for item in payload.get("regions", [])],
        )

    def _deserialize_region(self, payload: dict[str, object]) -> ProjectRegionState:
        return ProjectRegionState(
            region_id=str(payload.get("region_id", "")),
            dataset_id=str(payload.get("dataset_id", "")),
            name=str(payload.get("name", "")),
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
