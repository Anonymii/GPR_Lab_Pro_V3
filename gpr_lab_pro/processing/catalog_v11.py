from __future__ import annotations

from gpr_lab_pro.app.operation_text import (
    get_menu_placement,
    get_operation_label,
    get_parameter_label,
)
from gpr_lab_pro.domain.enums import StepKind
from gpr_lab_pro.processing.catalog_full import (
    OPERATION_SPECS as LEGACY_OPERATION_SPECS,
    OperationSpec,
    ParameterSpec,
)
from gpr_lab_pro.processing.module_registry_v11 import module_for_operation


def _default_category(kind: StepKind) -> str:
    if kind is StepKind.FREQUENCY:
        return "频域处理"
    if kind is StepKind.TRANSFORM:
        return "时频域变换"
    return "时域处理"


def _rebuild_spec(spec: OperationSpec) -> OperationSpec:
    placement = get_menu_placement(spec.op_type)
    module = module_for_operation(spec.op_type)
    category = module.title if module is not None else _default_category(spec.kind)
    menu_path = (
        (placement.top_level,)
        if placement.sub_level is None
        else (placement.top_level, placement.sub_level)
    )
    params = tuple(
        ParameterSpec(
            key=param.key,
            label=get_parameter_label(param.key, param.label),
            default=param.default,
            caster=param.caster,
        )
        for param in spec.params
    )
    return OperationSpec(
        op_type=spec.op_type,
        category=category,
        kind=spec.kind,
        menu_path=menu_path,
        title=get_operation_label(spec.op_type, spec.title),
        params=params,
    )


OPERATION_SPECS: tuple[OperationSpec, ...] = tuple(_rebuild_spec(spec) for spec in LEGACY_OPERATION_SPECS)
SPEC_BY_TYPE = {spec.op_type: spec for spec in OPERATION_SPECS}
