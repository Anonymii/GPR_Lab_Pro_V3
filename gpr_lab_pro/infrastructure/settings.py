from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    project_name: str = "GPR V11 PySide"

    @property
    def template_dir(self) -> Path:
        return Path(__file__).resolve().parents[1] / "resources" / "pipeline_templates"

    @property
    def help_dir(self) -> Path:
        return Path(__file__).resolve().parents[1] / "resources" / "help"
