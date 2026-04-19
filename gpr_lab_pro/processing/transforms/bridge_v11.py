from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy import signal
from scipy.interpolate import interp1d

from gpr_lab_pro.algorithms import correct_direct_wave, isdft_soft_phys
from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.models import PipelineOperation
from gpr_lab_pro.processing.transforms.bridge_support import build_time_meta, check_cancelled


class V11TimeFrequencyBridgeOperator:
    DIRECT_OPS: tuple[str, ...] = ("ifft", "czt", "isdft")
    DEFAULT_KAISER_BETA: float = 6.0
    MAX_PARALLEL_WORKERS: int = 4

    def __init__(self, dataset: DatasetRecord | None = None) -> None:
        self.dataset = dataset
        self._last_time_meta: dict[str, float] = {}

    def configure(self, dataset: DatasetRecord) -> "V11TimeFrequencyBridgeOperator":
        self.dataset = dataset
        return self

    def supports(self, op_type: str) -> bool:
        return op_type.lower() in self.DIRECT_OPS

    def execute(self, data: np.ndarray, operation: PipelineOperation, progress_callback=None, cancel_callback=None) -> np.ndarray:
        if self.dataset is None:
            raise RuntimeError("Bridge operator requires dataset metadata before execution.")
        arr = self._ensure_3d(data)
        op_type = operation.type.lower()
        if op_type == "ifft":
            return self._ifft_transform(arr, progress_callback=progress_callback, cancel_callback=cancel_callback)
        if op_type == "czt":
            return self._czt_transform(arr, progress_callback=progress_callback, cancel_callback=cancel_callback)
        if op_type == "isdft":
            return self._isdft_transform(arr, progress_callback=progress_callback, cancel_callback=cancel_callback)
        raise ValueError(f"Unsupported transform operation: {operation.type}")

    def _ifft_transform(self, data: np.ndarray, progress_callback=None, cancel_callback=None) -> np.ndarray:
        corrected = self._apply_direct_wave_correction(data, progress_callback=progress_callback, cancel_callback=cancel_callback, start_percent=0, end_percent=80)
        check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 90, "正在执行 IFFT")
        sample_new = self._target_sample_count(corrected.shape[0])
        corrected = self._resample_frequency_data(corrected, sample_new)
        out = np.fft.ifft(corrected, n=sample_new, axis=0).astype(np.complex64, copy=False)
        self._last_time_meta = build_time_meta(self.dataset, sample_new, data.shape[0], fallback_to_dataset=False)
        return out

    def _czt_transform(self, data: np.ndarray, progress_callback=None, cancel_callback=None) -> np.ndarray:
        corrected = self._prepare_frequency_data(data, progress_callback=progress_callback, cancel_callback=cancel_callback)
        sample_new = self._target_sample_count(corrected.shape[0])
        corrected = self._resample_frequency_data(corrected, sample_new)
        time_meta = build_time_meta(self.dataset, sample_new, data.shape[0], fallback_to_dataset=False)
        t0 = 0.0
        t1 = time_meta["tw_ns"] * 1e-9
        f_start = float(self.dataset.header.get("start_frequency_hz", 0.0))
        f_end = float(self.dataset.header.get("end_frequency_hz", 0.0))
        bandwidth = max(f_end - f_start, 1.0)
        ts_val = 1.0 / (bandwidth / sample_new)
        w_value = np.exp(1j * 2.0 * np.pi * (t1 - t0) / sample_new / ts_val)
        a_value = np.exp(1j * 2.0 * np.pi * (-t0) / ts_val)
        check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 60, "正在执行 CZT")
        out = signal.czt(corrected, m=sample_new, w=w_value, a=a_value, axis=0)
        self._report_progress(progress_callback, 100, "CZT 执行完成")
        self._last_time_meta = time_meta
        return np.asarray(out, dtype=np.complex64)

    def _isdft_transform(self, data: np.ndarray, progress_callback=None, cancel_callback=None) -> np.ndarray:
        corrected = self._prepare_frequency_data(data, progress_callback=progress_callback, cancel_callback=cancel_callback)
        sample_new = self._target_sample_count(corrected.shape[0])
        corrected = self._resample_frequency_data(corrected, sample_new)
        time_meta = build_time_meta(self.dataset, sample_new, data.shape[0], fallback_to_dataset=False)
        t = np.linspace(0.0, time_meta["tw_ns"] * 1e-9, sample_new)
        f_start = float(self.dataset.header.get("start_frequency_hz", 0.0))
        f_end = float(self.dataset.header.get("end_frequency_hz", 0.0))
        out = np.zeros_like(corrected, dtype=np.complex64)
        for line_idx in range(corrected.shape[2]):
            check_cancelled(cancel_callback)
            self._report_line_progress(progress_callback, line_idx, corrected.shape[2], 50, 100, "正在执行 ISDFT")
            out[:, :, line_idx] = isdft_soft_phys(
                corrected[:, :, line_idx],
                f_start,
                f_end,
                t,
                0.02,
                -3.0,
                9,
                0.0,
                3.0,
            )
        self._last_time_meta = time_meta
        return out.astype(np.complex64, copy=False)

    def _prepare_frequency_data(self, data: np.ndarray, progress_callback=None, cancel_callback=None) -> np.ndarray:
        corrected = self._apply_direct_wave_correction(data, progress_callback=progress_callback, cancel_callback=cancel_callback, start_percent=0, end_percent=40)
        check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 48, "正在加窗预处理")
        window = np.kaiser(corrected.shape[0], self.DEFAULT_KAISER_BETA).astype(np.float32)
        return (corrected * window[:, None, None]).astype(np.complex64, copy=False)

    def _target_sample_count(self, fallback_count: int) -> int:
        if self.dataset is None:
            return max(int(fallback_count), 1)
        target_tw_ns = float(self.dataset.transformed_time_window_ns())
        base_dt_ns = float(self.dataset.dt_ns if self.dataset.dt_ns > 0 else self.dataset.transformed_dt_ns())
        if target_tw_ns <= 0 or base_dt_ns <= 0:
            return max(int(fallback_count), 1)
        estimated = int(round(target_tw_ns / base_dt_ns)) + 1
        return max(int(fallback_count), estimated)

    def _resample_frequency_data(self, data: np.ndarray, target_count: int) -> np.ndarray:
        source_count = int(data.shape[0])
        if target_count <= 0 or target_count == source_count:
            return np.asarray(data, dtype=np.complex64)
        start_freq = float(self.dataset.header.get("start_frequency_hz", 0.0))
        end_freq = float(self.dataset.header.get("end_frequency_hz", 0.0))
        if end_freq <= start_freq:
            return np.asarray(data, dtype=np.complex64)
        orig_freq = np.linspace(start_freq, end_freq, source_count, dtype=np.float64)
        target_freq = np.linspace(start_freq, end_freq, target_count, dtype=np.float64)
        reshaped = np.asarray(data, dtype=np.complex64).reshape(source_count, -1)
        interpolator = interp1d(
            orig_freq,
            reshaped,
            kind="cubic",
            axis=0,
            bounds_error=False,
            fill_value="extrapolate",
            assume_sorted=True,
        )
        out = interpolator(target_freq).reshape((target_count, data.shape[1], data.shape[2]))
        return np.asarray(out, dtype=np.complex64)

    def _apply_direct_wave_correction(
        self,
        data: np.ndarray,
        progress_callback=None,
        cancel_callback=None,
        start_percent: int = 0,
        end_percent: int = 100,
    ) -> np.ndarray:
        start_freq = float(self.dataset.header.get("start_frequency_hz", 0.0))
        end_freq = float(self.dataset.header.get("end_frequency_hz", 0.0))
        freq_axis = np.linspace(start_freq, end_freq, data.shape[0], dtype=float)
        corrected = np.zeros_like(data, dtype=np.complex64)
        total_lines = data.shape[2]
        if total_lines <= 1:
            check_cancelled(cancel_callback)
            corrected[:, :, 0], _ = correct_direct_wave(data[:, :, 0], freq_axis)
            self._report_progress(progress_callback, end_percent, "正在进行直达波校正 (1/1 条测线)")
            return corrected

        max_workers = min(total_lines, max(1, min(self.MAX_PARALLEL_WORKERS, os.cpu_count() or 1)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(correct_direct_wave, data[:, :, line_idx], freq_axis)
                for line_idx in range(total_lines)
            ]
            for line_idx, future in enumerate(futures):
                check_cancelled(cancel_callback)
                corrected[:, :, line_idx], _ = future.result()
                self._report_line_progress(progress_callback, line_idx, total_lines, start_percent, end_percent, "正在进行直达波校正")
        return corrected

    @staticmethod
    def _ensure_3d(data: np.ndarray) -> np.ndarray:
        arr = np.asarray(data)
        if arr.ndim == 2:
            return arr[:, :, None]
        if arr.ndim != 3:
            raise ValueError("Expected a 2D or 3D GPR array.")
        return arr

    @staticmethod
    def _report_progress(progress_callback, percent: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(int(max(0, min(100, percent))), str(message))

    def _report_line_progress(
        self,
        progress_callback,
        line_idx: int,
        total_lines: int,
        start_percent: int,
        end_percent: int,
        message: str,
    ) -> None:
        if progress_callback is None or total_lines <= 0:
            return
        progress = (line_idx + 1) / max(total_lines, 1)
        percent = start_percent + int((end_percent - start_percent) * progress)
        progress_callback(int(max(0, min(100, percent))), f"{message} ({line_idx + 1}/{total_lines} 条测线)")
