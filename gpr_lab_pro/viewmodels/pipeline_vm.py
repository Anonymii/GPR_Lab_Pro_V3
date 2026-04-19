from __future__ import annotations

from dataclasses import dataclass

from gpr_lab_pro.domain.models.pipeline import PipelineState


@dataclass
class PipelineViewModel:
    state: PipelineState

    @property
    def labels(self) -> list[str]:
        return [
            f"{idx}. {'[停用] ' if not step.enabled else ''}{step.name}"
            for idx, step in enumerate(self.state.steps, start=1)
        ]
