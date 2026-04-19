from __future__ import annotations

from dataclasses import dataclass

from gpr_lab_pro.domain.models.display import DisplayState


@dataclass
class DisplayViewModel:
    state: DisplayState

    @property
    def summary(self) -> str:
        return (
            f"对比度={self.state.contrast_gain:.1f}, "
            f"C层厚={self.state.slice_thickness}, "
            f"B属性={self.state.bscan_attr}, "
            f"C属性={self.state.cscan_attr}"
        )
