from __future__ import annotations

import numpy as np

from gpr_lab_pro.domain.enums import StepKind
from gpr_lab_pro.models import PipelineOperation
from gpr_lab_pro.processing.catalog_v11 import OPERATION_SPECS
from gpr_lab_pro.processing.engine import PipelineProcessor
from gpr_lab_pro.processing.module_registry_v11 import MODULE_SPECS, ProcessingModuleSpec
from gpr_lab_pro.processing.modules import V11TimeDomainOperator


class TimeDomainProcessingEngine:
    """
    Time-domain algorithm registry for the formal V11 architecture.

    In the current milestone, all registered time-domain steps are handled by
    the dedicated V11 time-domain operator. The legacy shared processor is
    still kept as a compatibility fallback so future extensions can be added
    without breaking runtime dispatch.
    """

    def __init__(self, processor: PipelineProcessor | None = None) -> None:
        self.processor = processor
        self.direct_operator: V11TimeDomainOperator | None = None

    def bind(self, processor: PipelineProcessor) -> "TimeDomainProcessingEngine":
        self.processor = processor
        self.direct_operator = V11TimeDomainOperator(processor.context)
        return self

    def list_operation_types(self) -> tuple[str, ...]:
        return tuple(spec.op_type for spec in OPERATION_SPECS if spec.kind is StepKind.TIME)

    def modules(self) -> tuple[ProcessingModuleSpec, ...]:
        return tuple(module for module in MODULE_SPECS if module.kind is StepKind.TIME)

    def supports(self, op_type: str) -> bool:
        return op_type in self.list_operation_types()

    def execute(self, data: np.ndarray, operation: PipelineOperation) -> np.ndarray:
        if self.processor is None:
            raise RuntimeError("TimeDomainProcessingEngine is not bound to a processor.")
        if self.direct_operator is not None and self.direct_operator.supports(operation.type):
            return self.direct_operator.execute(data, operation)
        return self.processor.process_step(data, operation.type, operation.params)
