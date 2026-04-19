from __future__ import annotations

from enum import Enum


class DataDomain(str, Enum):
    FREQUENCY = "frequency"
    TIME = "time"


class StepKind(str, Enum):
    FREQUENCY = "frequency"
    TRANSFORM = "transform"
    TIME = "time"
