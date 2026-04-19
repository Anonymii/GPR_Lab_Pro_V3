from __future__ import annotations

import numpy as np

from gpr_lab_pro.models import PipelineOperation
from gpr_lab_pro.processing.engine import PipelineProcessor
from gpr_lab_pro.processing.transforms.bridge_v11 import V11TimeFrequencyBridgeOperator


class TimeFrequencyTransformBridge:
    """
    Bridge layer between frequency-domain data and time-domain data.

    In the current milestone, CZT and ISDFT are completed during DAT import,
    while IFFT is kept as a runtime bridge step. This class provides the
    formal dispatch point for transform-stage execution in the V11 framework.
    """

    supported_transforms: tuple[str, ...] = ("CZT", "ISDFT", "IFFT")

    def __init__(self, processor: PipelineProcessor | None = None) -> None:
        self.processor = processor
        self.direct_operator = V11TimeFrequencyBridgeOperator()

    def bind(self, processor: PipelineProcessor) -> "TimeFrequencyTransformBridge":
        self.processor = processor
        return self

    def configure_dataset(self, dataset) -> "TimeFrequencyTransformBridge":
        self.direct_operator.configure(dataset)
        return self

    def describe(self) -> dict[str, tuple[str, ...]]:
        return {
            "role": ("frequency-domain input", "time-frequency transform", "time-domain output"),
            "supported": self.supported_transforms,
            "runtime_direct": ("IFFT", "CZT", "ISDFT"),
            "import_stage": (),
        }

    def execute(self, data: np.ndarray, operation: PipelineOperation, progress_callback=None, cancel_callback=None) -> np.ndarray:
        if self.processor is None:
            raise RuntimeError("TimeFrequencyTransformBridge is not bound to a processor.")
        if self.direct_operator.supports(operation.type):
            return self.direct_operator.execute(
                data,
                operation,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
            )
        return self.processor.process_step(data, operation.type, operation.params)

    def current_time_meta(self) -> dict[str, float]:
        return dict(getattr(self.direct_operator, "_last_time_meta", {}))
