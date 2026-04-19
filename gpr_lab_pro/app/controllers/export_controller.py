from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.io import loadmat, savemat

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.app.operation_text import get_operation_label
from gpr_lab_pro.domain.enums import StepKind
from gpr_lab_pro.domain.models.pipeline import PipelineStep
from gpr_lab_pro.processing.catalog_v11 import SPEC_BY_TYPE


class ExportController:
    TRANSFORM_CATEGORY = "时频域桥接"
    TRANSFORM_NAMES = {
        "czt": "CZT",
        "ifft": "IFFT",
        "isdft": "ISDFT",
    }

    def __init__(self, context: ApplicationContext) -> None:
        self.context = context

    def save_pipeline_template(self, path: str) -> None:
        path_obj = Path(path)
        payload = [
            {
                "op_type": step.op_type,
                "name": step.name,
                "category": step.category,
                "kind": step.kind.value,
                "params": list(step.params),
                "enabled": bool(step.enabled),
            }
            for step in self.context.pipeline_state.draft_steps
        ]
        if path_obj.suffix.lower() == ".json":
            path_obj.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return

        tpl = {
            "name": path_obj.stem,
            "scene": "GPR V11 模板",
            "version": "v0.11.0",
            "op_list": np.array(
                [
                    {
                        "type": step.op_type,
                        "params": np.array(step.params, dtype=float),
                        "name": step.name,
                        "enabled": float(step.enabled),
                    }
                    for step in self.context.pipeline_state.draft_steps
                ],
                dtype=object,
            ),
            "bscan_attr": self.context.display_state.bscan_attr,
            "cscan_attr": self.context.display_state.cscan_attr,
            "slice_thick": self.context.display_state.slice_thickness,
            "contrast_gain": self.context.display_state.contrast_gain,
            "start_time_ns": self.context.display_state.start_time_ns,
            "end_time_ns": self.context.display_state.end_time_ns,
        }
        savemat(str(path_obj), {"tpl": tpl})

    def load_pipeline_template(self, path: str) -> None:
        path_obj = Path(path)
        if path_obj.suffix.lower() == ".json":
            payload = json.loads(path_obj.read_text(encoding="utf-8"))
            steps: list[PipelineStep] = []
            for item in payload:
                step = self._step_from_template_item(item)
                if step is not None:
                    steps.append(step)
            self.context.pipeline_state.draft_steps = steps
            self.context.pipeline_state.has_unapplied_changes = True
            return

        mat = loadmat(str(path_obj), squeeze_me=True, struct_as_record=False)
        tpl = mat.get("tpl")
        if tpl is None:
            raise ValueError("模板文件中未找到 tpl 结构。")

        ops = np.atleast_1d(getattr(tpl, "op_list", []))
        steps: list[PipelineStep] = []
        for item in ops:
            op_type = str(getattr(item, "type", ""))
            params = getattr(item, "params", [])
            name = str(getattr(item, "name", op_type))
            param_values = np.atleast_1d(params).astype(float).tolist() if np.size(params) else []
            enabled = bool(getattr(item, "enabled", 1))
            step = self._build_step(
                op_type=op_type,
                name=name or get_operation_label(op_type, op_type),
                category=None,
                kind_value=None,
                params=param_values,
                enabled=enabled,
            )
            if step is not None:
                steps.append(step)
        self.context.pipeline_state.draft_steps = steps
        self.context.pipeline_state.has_unapplied_changes = True

        b_attr = getattr(tpl, "bscan_attr", None)
        c_attr = getattr(tpl, "cscan_attr", None)
        slice_thick = getattr(tpl, "slice_thick", None)
        start_time_ns = getattr(tpl, "start_time_ns", None)
        end_time_ns = getattr(tpl, "end_time_ns", None)
        if b_attr:
            self.context.display_state.bscan_attr = str(b_attr)
        if c_attr:
            self.context.display_state.cscan_attr = str(c_attr)
        if slice_thick is not None:
            self.context.display_state.slice_thickness = int(round(float(slice_thick)))
        if start_time_ns is not None:
            self.context.display_state.start_time_ns = float(start_time_ns)
        if end_time_ns is not None:
            self.context.display_state.end_time_ns = float(end_time_ns)

    def save_processed_data(self, path: str) -> None:
        snapshot = self.context.result_state.active_snapshot
        dataset = self.context.dataset_state.current_dataset
        if snapshot is None or dataset is None:
            return

        path_obj = Path(path)
        data = snapshot.data
        if path_obj.suffix.lower() == ".mat":
            channels = [data[:, :, idx] for idx in range(data.shape[2])]
            savemat(
                str(path_obj),
                {
                    "V_proc": data,
                    "data_2_lte": np.array(channels, dtype=object),
                    "tw": dataset.tw_ns,
                },
            )
            return
        np.save(path_obj, data)

    def _step_from_template_item(self, item: dict) -> PipelineStep | None:
        return self._build_step(
            op_type=str(item.get("op_type", "")),
            name=str(item.get("name", "")),
            category=item.get("category"),
            kind_value=item.get("kind"),
            params=item.get("params", []),
            enabled=bool(item.get("enabled", True)),
        )

    def _build_step(
        self,
        *,
        op_type: str,
        name: str,
        category,
        kind_value,
        params,
        enabled: bool,
    ) -> PipelineStep | None:
        op_key = str(op_type).strip().lower()
        if not op_key:
            return None

        kind: StepKind | None = None
        resolved_category = str(category).strip() if category is not None else ""
        if kind_value:
            try:
                kind = StepKind(str(kind_value))
            except ValueError:
                kind = None

        spec = SPEC_BY_TYPE.get(op_key)
        if spec is not None:
            kind = spec.kind
            resolved_category = resolved_category or spec.category
            resolved_name = name or get_operation_label(op_key, spec.title)
        elif op_key in self.TRANSFORM_NAMES:
            kind = StepKind.TRANSFORM
            resolved_category = resolved_category or self.TRANSFORM_CATEGORY
            resolved_name = name or self.TRANSFORM_NAMES[op_key]
        else:
            return None

        step = PipelineStep.from_sequence(
            op_key,
            resolved_name,
            resolved_category,
            kind,
            params,
        )
        step.enabled = bool(enabled if kind is not StepKind.TRANSFORM else True)
        return step
