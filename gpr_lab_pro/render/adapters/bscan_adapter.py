from __future__ import annotations

import numpy as np

from gpr_lab_pro.domain.models.display import DisplayState, SelectionState


def _attribute_view(values: np.ndarray, attribute: str) -> np.ndarray:
    if attribute == "Envelope":
        return np.abs(values)
    if attribute == "Phase":
        return np.angle(values)
    if attribute == "Inst Freq":
        unwrapped = np.unwrap(np.angle(values), axis=0)
        return np.vstack([np.diff(unwrapped, axis=0), np.zeros((1, values.shape[1]))])
    return np.real(values)


def build_bscan(
    volume: np.ndarray,
    dt_ns: float,
    display_state: DisplayState,
    selection_state: SelectionState,
    *,
    time_offset_ns: float = 0.0,
) -> tuple[np.ndarray, tuple[float | None, float | None], tuple[float, float]]:
    if volume.size == 0:
        return np.empty((0, 0), dtype=float), (None, None), (0.0, 0.0)
    line_idx = int(np.clip(selection_state.line_index, 0, volume.shape[2] - 1))
    bscan = _attribute_view(volume[:, :, line_idx], display_state.bscan_attr)
    total_start = float(time_offset_ns)
    total_end = total_start + max((volume.shape[0] - 1) * dt_ns, 0.0)
    start_ns = float(np.clip(display_state.start_time_ns, total_start, total_end))
    end_ns = float(
        np.clip(
            display_state.end_time_ns,
            start_ns + dt_ns,
            max(total_end, start_ns + dt_ns),
        )
    )
    visible_range = (start_ns, end_ns)
    if display_state.bscan_attr in {"Envelope", "Abs"}:
        vmax = float(np.nanpercentile(np.abs(bscan), 99.0))
        return bscan, (0.0, vmax if vmax > 0 else None), visible_range
    vmax = float(np.nanstd(np.real(bscan)) * max(display_state.contrast_gain, 0.1))
    if not np.isfinite(vmax) or vmax <= 1e-9:
        vmax = 1.0
    return bscan, (-vmax, vmax), visible_range
