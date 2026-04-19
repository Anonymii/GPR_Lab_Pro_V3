from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence
import uuid

from gpr_lab_pro.domain.enums import StepKind


@dataclass
class PipelineStep:
    op_type: str
    name: str
    category: str
    kind: StepKind
    params: tuple[float, ...] = ()
    enabled: bool = True
    step_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @classmethod
    def from_sequence(
        cls,
        op_type: str,
        name: str,
        category: str,
        kind: StepKind,
        params: Sequence[float] | None = None,
    ) -> "PipelineStep":
        return cls(
            op_type=op_type,
            name=name,
            category=category,
            kind=kind,
            params=tuple(params or ()),
        )

    def clone(self) -> "PipelineStep":
        return replace(self, params=tuple(self.params))


@dataclass
class PipelineState:
    draft_steps: list[PipelineStep] = field(default_factory=list)
    applied_steps: list[PipelineStep] = field(default_factory=list)
    has_unapplied_changes: bool = False

    @property
    def steps(self) -> list[PipelineStep]:
        return self.draft_steps

    @steps.setter
    def steps(self, value: list[PipelineStep]) -> None:
        self.draft_steps = list(value)
