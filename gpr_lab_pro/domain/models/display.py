from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SelectionState:
    line_index: int = 0
    trace_index: int = 0
    sample_index: int = 0


@dataclass
class DisplayState:
    contrast_gain: float = 4.0
    slice_thickness: int = 5
    bscan_attr: str = "Real"
    cscan_attr: str = "Envelope"
    start_time_ns: float = 0.0
    end_time_ns: float = 60.0
    colormap: str = "gray"
    invert: bool = False
    show_axes: bool = True


@dataclass
class DisplayData:
    bscan: np.ndarray
    crossline: np.ndarray
    cscan: np.ndarray
    ascan_time_ns: np.ndarray
    ascan_values: np.ndarray
    spectrum_freq_mhz: np.ndarray
    spectrum_values: np.ndarray
    bscan_limits: tuple[float | None, float | None]
    crossline_limits: tuple[float | None, float | None]
    cscan_limits: tuple[float | None, float | None]
    selection: SelectionState
    trace_info: str
    meta: dict[str, object] = field(default_factory=dict)
