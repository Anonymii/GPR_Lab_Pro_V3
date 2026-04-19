from __future__ import annotations

from dataclasses import dataclass

from gpr_lab_pro.domain.models.project import ProjectState


@dataclass
class ProjectViewModel:
    state: ProjectState

    @property
    def title(self) -> str:
        return self.state.name
