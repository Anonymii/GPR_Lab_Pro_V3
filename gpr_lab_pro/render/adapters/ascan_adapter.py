from __future__ import annotations

import numpy as np

from gpr_lab_pro.domain.models.display import SelectionState
from gpr_lab_pro.domain.models.dataset import DatasetRecord


def build_ascan(
    dataset: DatasetRecord,
    volume: np.ndarray,
    selection_state: SelectionState,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, str]:
    if volume.size == 0:
        empty = np.empty((0,), dtype=float)
        return empty, empty, empty, empty, "当前尚未导入数据"
    line_idx = int(np.clip(selection_state.line_index, 0, volume.shape[2] - 1))
    trace_idx = int(np.clip(selection_state.trace_index, 0, volume.shape[1] - 1))
    sample_idx = int(np.clip(selection_state.sample_index, 0, volume.shape[0] - 1))
    trace = volume[:, trace_idx, line_idx]
    time_axis = np.arange(volume.shape[0], dtype=float) * dataset.dt_ns
    plot_values = np.real(trace)
    spectrum = np.fft.rfft(plot_values)
    freq_axis = np.fft.rfftfreq(plot_values.size, d=dataset.dt_ns * 1e-9) / 1e6
    peak_idx = int(np.argmax(np.abs(spectrum))) if spectrum.size else 0
    trace_info = (
        f"文件: {dataset.filename}\n"
        f"测线: {line_idx + 1}/{dataset.line_count}\n"
        f"道号: {trace_idx + 1}/{dataset.trace_count}\n"
        f"时间: {sample_idx * dataset.dt_ns:.2f} ns\n"
        f"幅值: {plot_values[sample_idx]:.4f}\n"
        f"峰值频率: {freq_axis[peak_idx]:.1f} MHz\n"
        f"数据维度: {dataset.shape}"
    )
    return time_axis, plot_values, freq_axis, np.abs(spectrum), trace_info
