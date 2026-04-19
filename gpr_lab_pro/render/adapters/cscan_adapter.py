from __future__ import annotations

import numpy as np

from gpr_lab_pro.domain.models.display import DisplayState, SelectionState


def build_cscan(
    volume: np.ndarray,
    display_state: DisplayState,
    selection_state: SelectionState,
) -> tuple[np.ndarray, tuple[float | None, float | None]]:
    if volume.size == 0:
        return np.empty((0, 0), dtype=float), (None, None)
    depth_idx = int(np.clip(selection_state.sample_index, 0, volume.shape[0] - 1))
    thick = max(0, int(display_state.slice_thickness))
    lo = max(0, depth_idx - thick)
    hi = min(volume.shape[0], depth_idx + thick + 1)
    slab = volume[lo:hi, :, :]
    cscan = np.mean(slab, axis=0).T
    attr = display_state.cscan_attr
    if attr == "Envelope":
        cscan = np.abs(cscan)
    elif attr == "Phase":
        cscan = np.angle(cscan)
    elif attr == "Abs":
        cscan = np.abs(np.real(cscan))
    else:
        cscan = np.real(cscan)

    if attr in {"Envelope", "Abs"}:
        vmax = float(np.nanpercentile(np.abs(cscan), 99.0))
        return cscan, (0.0, vmax if vmax > 0 else None)
    vmax = float(np.nanstd(cscan) * max(display_state.contrast_gain, 0.1))
    if not np.isfinite(vmax) or vmax <= 1e-9:
        vmax = 1.0
    return cscan, (-vmax, vmax)
