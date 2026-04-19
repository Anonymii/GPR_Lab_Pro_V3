from __future__ import annotations

import numpy as np

from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.domain.models.display import SelectionState


def build_ascan(
    dataset: DatasetRecord,
    volume: np.ndarray,
    selection_state: SelectionState,
    *,
    dt_ns: float | None = None,
    time_offset_ns: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, str]:
    if volume.size == 0:
        empty = np.empty((0,), dtype=float)
        return empty, empty, empty, empty, "当前尚未生成可显示结果"

    line_idx = int(np.clip(selection_state.line_index, 0, volume.shape[2] - 1))
    trace_idx = int(np.clip(selection_state.trace_index, 0, volume.shape[1] - 1))
    sample_idx = int(np.clip(selection_state.sample_index, 0, volume.shape[0] - 1))
    trace = volume[:, trace_idx, line_idx]
    time_step_ns = float(dt_ns if dt_ns is not None else dataset.dt_ns)
    time_axis = float(time_offset_ns) + np.arange(volume.shape[0], dtype=float) * time_step_ns
    plot_values = np.real(trace)
    full_spectrum = np.fft.fft(plot_values)
    sample_count = plot_values.size
    spectrum = np.abs(full_spectrum / max(sample_count, 1))
    spectrum = spectrum[: sample_count // 2 + 1]
    if spectrum.size > 2:
        spectrum[1:-1] *= 2.0
    freq_axis = np.fft.rfftfreq(sample_count, d=time_step_ns * 1e-9) / 1e6
    peak_idx = int(np.argmax(spectrum)) if spectrum.size else 0
    trace_info = (
        f"文件: {dataset.filename}\n"
        f"测线: {line_idx + 1}/{dataset.line_count}\n"
        f"道号: {trace_idx + 1}/{dataset.trace_count}\n"
        f"采样点: {sample_idx + 1}/{volume.shape[0]}\n"
        f"时间: {time_axis[sample_idx]:.2f} ns\n"
        f"幅值: {np.real(trace[sample_idx]):.4f}\n"
        f"峰值频率: {freq_axis[peak_idx]:.2f} MHz"
    )
    return time_axis, plot_values, freq_axis, spectrum, trace_info
