from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os

import numpy as np
from scipy import signal
from scipy.interpolate import interp1d

from gpr_lab_pro.algorithms import (
    apply_isdft_soft_phys_operator,
    build_isdft_soft_phys_operator,
    correct_direct_wave,
    estimate_direct_wave_tau_base,
    isdft_soft_phys,
)
from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.models import PipelineOperation
from gpr_lab_pro.processing.transforms.bridge_support import check_cancelled


class V11TimeFrequencyBridgeOperator:
    DIRECT_OPS: tuple[str, ...] = ("ifft", "czt", "isdft")
    DEFAULT_KAISER_BETA: float = 6.0
    DEFAULT_ZERO_CORRECT: bool = True
    DEFAULT_ISDFT_ALPHA: float = 0.02
    DEFAULT_ISDFT_TH_DB: float = -3.0
    DEFAULT_ISDFT_SMOOTH_LEN: int = 9
    DEFAULT_ISDFT_RAMP_NS: float = 3.0
    DEFAULT_ISDFT_MAX_WORKERS: int = 4
    DEFAULT_ISDFT_TRACE_BLOCK_SIZE: int = 4096
    DEFAULT_PREPARE_MAX_WORKERS: int = 4
    DEFAULT_IFFT_USE_FULL_BW: bool = True
    DEFAULT_IFFT_MIN_FREQ_MHZ: float = 30.0
    DEFAULT_IFFT_MAX_FREQ_MHZ: float = 3000.0
    DEFAULT_TARGET_START_NS: float = 0.0
    DEFAULT_TARGET_END_NS: float = 40.0

    def __init__(self, dataset: DatasetRecord | None = None) -> None:
        self.dataset = dataset
        self._last_time_meta: dict[str, float] = {}
        self._display_time_window_ns: tuple[float, float] | None = None

    def configure(self, dataset: DatasetRecord) -> "V11TimeFrequencyBridgeOperator":
        self.dataset = dataset
        return self

    def configure_display_time_window(self, window_ns: tuple[float, float] | None) -> "V11TimeFrequencyBridgeOperator":
        self._display_time_window_ns = window_ns
        return self

    def supports(self, op_type: str) -> bool:
        return op_type.lower() in self.DIRECT_OPS

    def execute(self, data: np.ndarray, operation: PipelineOperation, progress_callback=None, cancel_callback=None) -> np.ndarray:
        if self.dataset is None:
            raise RuntimeError("Bridge operator requires dataset metadata before execution.")
        arr = self._ensure_3d(data)
        op_type = operation.type.lower()
        if op_type == "ifft":
            return self._ifft_transform(arr, operation, progress_callback=progress_callback, cancel_callback=cancel_callback)
        if op_type == "czt":
            return self._czt_transform(arr, operation, progress_callback=progress_callback, cancel_callback=cancel_callback)
        if op_type == "isdft":
            return self._isdft_transform(arr, operation, progress_callback=progress_callback, cancel_callback=cancel_callback)
        raise ValueError(f"Unsupported transform operation: {operation.type}")

    def _ifft_transform(self, data: np.ndarray, operation: PipelineOperation, progress_callback=None, cancel_callback=None) -> np.ndarray:
        cfg = self._resolve_ifft_config(operation.params)
        start_freq_hz, end_freq_hz = self._resolve_ifft_frequency_band(cfg["use_full_bw"], cfg["min_freq_mhz"], cfg["max_freq_mhz"])
        sample_new = self._resolve_transform_sample_count(data)
        resampled = self._resample_frequency_data(data, sample_new, start_freq_hz, end_freq_hz)
        check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 40, "正在进行 IFFT 频域加窗")
        window = np.kaiser(sample_new, cfg["beta"]).astype(np.float32)
        windowed = (resampled * window[:, None, None]).astype(np.complex64, copy=False)
        check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 85, "正在执行 IFFT")
        out = np.fft.ifft(windowed, n=sample_new, axis=0).astype(np.complex64, copy=False)
        self._last_time_meta = self._build_band_time_meta(start_freq_hz, end_freq_hz, sample_new, 0.0)
        return out

    def _czt_transform(self, data: np.ndarray, operation: PipelineOperation, progress_callback=None, cancel_callback=None) -> np.ndarray:
        cfg = self._resolve_czt_config(operation.params)
        start_freq_hz, end_freq_hz = self._resolve_dataset_frequency_band()
        sample_new = self._resolve_transform_sample_count(data)
        prepared, _ = self._prepare_transform_input(
            data,
            sample_new=sample_new,
            target_start_hz=start_freq_hz,
            target_end_hz=end_freq_hz,
            beta=cfg["beta"],
            zero_correct=cfg["zero_correct"],
            need_tau_base=False,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            stage_name="时域窗变换",
        )
        target_start_ns = float(cfg["start_ns"])
        target_end_ns = float(cfg["end_ns"])
        time_meta = self._build_requested_time_meta(target_start_ns, target_end_ns, sample_new)
        t0_s = target_start_ns * 1e-9
        t1_s = target_end_ns * 1e-9
        bandwidth = max(end_freq_hz - start_freq_hz, 1.0)
        ts_val = 1.0 / (bandwidth / sample_new)
        w_value = np.exp(1j * 2.0 * np.pi * (t1_s - t0_s) / sample_new / ts_val)
        a_value = np.exp(1j * 2.0 * np.pi * (-t0_s) / ts_val)
        check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 75, "正在执行时域窗变换")
        out = signal.czt(prepared, m=sample_new, w=w_value, a=a_value, axis=0)
        self._report_progress(progress_callback, 100, "时域窗变换执行完成")
        self._last_time_meta = time_meta
        return np.asarray(out, dtype=np.complex64)

    def _isdft_transform(self, data: np.ndarray, operation: PipelineOperation, progress_callback=None, cancel_callback=None) -> np.ndarray:
        cfg = self._resolve_isdft_config(operation.params)
        start_freq_hz, end_freq_hz = self._resolve_dataset_frequency_band()
        sample_new = self._resolve_transform_sample_count(data)
        prepared, tau_bases = self._prepare_transform_input(
            data,
            sample_new=sample_new,
            target_start_hz=start_freq_hz,
            target_end_hz=end_freq_hz,
            beta=cfg["beta"],
            zero_correct=cfg["zero_correct"],
            need_tau_base=not cfg["zero_correct"],
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            stage_name="ISDFT",
        )
        target_start_ns, target_end_ns = self._resolve_target_time_window_ns()
        time_meta = self._build_requested_time_meta(target_start_ns, target_end_ns, sample_new)
        t = np.linspace(target_start_ns * 1e-9, target_end_ns * 1e-9, sample_new)
        out = self._run_isdft_lines(
            prepared,
            tau_bases,
            start_freq_hz,
            end_freq_hz,
            t,
            sample_new,
            cfg,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        self._last_time_meta = time_meta
        return out.astype(np.complex64, copy=False)

    def _run_isdft_lines(
        self,
        prepared: np.ndarray,
        tau_bases: np.ndarray,
        start_freq_hz: float,
        end_freq_hz: float,
        t: np.ndarray,
        sample_new: int,
        cfg: dict[str, float | bool | int],
        progress_callback=None,
        cancel_callback=None,
    ) -> np.ndarray:
        out = np.zeros_like(prepared, dtype=np.complex64)
        total_lines = int(prepared.shape[2])
        shared_operator = None
        if cfg["zero_correct"]:
            shared_operator = build_isdft_soft_phys_operator(
                sample_new,
                start_freq_hz,
                end_freq_hz,
                t,
                cfg["alpha"],
                cfg["th_db"],
                cfg["smooth_len"],
                0.0,
                cfg["ramp_ns"],
            )

        max_workers = self._resolve_isdft_workers(total_lines)
        if max_workers <= 1:
            for line_idx in range(total_lines):
                check_cancelled(cancel_callback)
                self._report_line_progress(progress_callback, line_idx, total_lines, 55, 100, "正在执行 ISDFT")
                out[:, :, line_idx] = self._execute_isdft_line(
                    prepared[:, :, line_idx],
                    start_freq_hz,
                    end_freq_hz,
                    t,
                    cfg,
                    0.0 if cfg["zero_correct"] else float(tau_bases[line_idx]),
                    shared_operator,
                )
            return out

        def run_line(line_idx: int) -> tuple[int, np.ndarray]:
            check_cancelled(cancel_callback)
            tau_base = 0.0 if cfg["zero_correct"] else float(tau_bases[line_idx])
            result = self._execute_isdft_line(
                prepared[:, :, line_idx],
                start_freq_hz,
                end_freq_hz,
                t,
                cfg,
                tau_base,
                shared_operator,
            )
            check_cancelled(cancel_callback)
            return line_idx, result

        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gpr-isdft") as executor:
            futures = [executor.submit(run_line, line_idx) for line_idx in range(total_lines)]
            try:
                for future in as_completed(futures):
                    check_cancelled(cancel_callback)
                    line_idx, result = future.result()
                    out[:, :, line_idx] = result
                    self._report_line_progress(progress_callback, completed, total_lines, 55, 100, "正在执行 ISDFT")
                    completed += 1
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        return out

    def _execute_isdft_line(
        self,
        line_data: np.ndarray,
        start_freq_hz: float,
        end_freq_hz: float,
        t: np.ndarray,
        cfg: dict[str, float | bool | int],
        tau_base: float,
        shared_operator: np.ndarray | None,
    ) -> np.ndarray:
        if shared_operator is not None:
            return apply_isdft_soft_phys_operator(
                line_data,
                shared_operator,
                block_size=self.DEFAULT_ISDFT_TRACE_BLOCK_SIZE,
            )
        return isdft_soft_phys(
            line_data,
            start_freq_hz,
            end_freq_hz,
            t,
            float(cfg["alpha"]),
            float(cfg["th_db"]),
            int(cfg["smooth_len"]),
            tau_base,
            float(cfg["ramp_ns"]),
            block_size=self.DEFAULT_ISDFT_TRACE_BLOCK_SIZE,
        )

    def _prepare_transform_input(
        self,
        data: np.ndarray,
        *,
        sample_new: int,
        target_start_hz: float,
        target_end_hz: float,
        beta: float,
        zero_correct: bool,
        need_tau_base: bool,
        progress_callback=None,
        cancel_callback=None,
        stage_name: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        resampled = self._resample_frequency_data(data, sample_new, target_start_hz, target_end_hz)
        freq_axis = np.linspace(target_start_hz, target_end_hz, sample_new, dtype=float)
        window = np.kaiser(sample_new, beta).astype(np.float32)
        prepared = np.zeros_like(resampled, dtype=np.complex64)
        tau_bases = np.zeros(resampled.shape[2], dtype=np.float32)
        total_lines = resampled.shape[2]

        if zero_correct:
            self._report_progress(progress_callback, 10, f"正在进行 {stage_name} 零点校正")
        else:
            self._report_progress(progress_callback, 10, f"正在进行 {stage_name} 频域加窗")

        if not zero_correct and not need_tau_base:
            prepared = (resampled * window[:, None, None]).astype(np.complex64, copy=False)
            self._report_progress(progress_callback, 50, f"正在准备 {stage_name} 输入")
            return prepared, tau_bases

        max_workers = self._resolve_prepare_workers(total_lines)
        if max_workers > 1:
            return self._prepare_transform_input_parallel(
                resampled,
                freq_axis,
                window,
                zero_correct=zero_correct,
                need_tau_base=need_tau_base,
                max_workers=max_workers,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                stage_name=stage_name,
            )

        for line_idx in range(total_lines):
            check_cancelled(cancel_callback)
            current = resampled[:, :, line_idx]
            if zero_correct:
                current, _ = correct_direct_wave(current, freq_axis)
            elif need_tau_base:
                tau_bases[line_idx] = estimate_direct_wave_tau_base(current, freq_axis)
            prepared[:, :, line_idx] = (current * window[:, None]).astype(np.complex64, copy=False)
            self._report_line_progress(progress_callback, line_idx, total_lines, 10, 50, f"正在准备 {stage_name} 输入")
        return prepared, tau_bases

    def _prepare_transform_input_parallel(
        self,
        resampled: np.ndarray,
        freq_axis: np.ndarray,
        window: np.ndarray,
        *,
        zero_correct: bool,
        need_tau_base: bool,
        max_workers: int,
        progress_callback=None,
        cancel_callback=None,
        stage_name: str,
    ) -> tuple[np.ndarray, np.ndarray]:
        prepared = np.zeros_like(resampled, dtype=np.complex64)
        tau_bases = np.zeros(resampled.shape[2], dtype=np.float32)
        total_lines = int(resampled.shape[2])

        def run_line(line_idx: int) -> tuple[int, np.ndarray, np.float32]:
            check_cancelled(cancel_callback)
            current = resampled[:, :, line_idx]
            tau_base = np.float32(0.0)
            if zero_correct:
                current, tau_base = correct_direct_wave(current, freq_axis)
            elif need_tau_base:
                tau_base = estimate_direct_wave_tau_base(current, freq_axis)
            line_prepared = (current * window[:, None]).astype(np.complex64, copy=False)
            check_cancelled(cancel_callback)
            return line_idx, line_prepared, np.float32(tau_base)

        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gpr-prepare") as executor:
            futures = [executor.submit(run_line, line_idx) for line_idx in range(total_lines)]
            try:
                for future in as_completed(futures):
                    check_cancelled(cancel_callback)
                    line_idx, line_prepared, tau_base = future.result()
                    prepared[:, :, line_idx] = line_prepared
                    if need_tau_base:
                        tau_bases[line_idx] = tau_base
                    self._report_line_progress(progress_callback, completed, total_lines, 10, 50, f"正在准备 {stage_name} 输入")
                    completed += 1
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        return prepared, tau_bases

    def _resolve_czt_config(self, params: tuple[float, ...]) -> dict[str, float | bool]:
        import_params = self._import_params()
        if len(params) >= 4:
            zero_correct = self._bool_param(params, 3, import_params.get("zero_correct", self.DEFAULT_ZERO_CORRECT))
            start_ns = self._float_param(params, 1, self.DEFAULT_TARGET_START_NS)
            end_ns = self._float_param(params, 2, self.DEFAULT_TARGET_END_NS)
        elif len(params) == 3:
            zero_correct = bool(import_params.get("zero_correct", self.DEFAULT_ZERO_CORRECT))
            start_ns = self._float_param(params, 1, self.DEFAULT_TARGET_START_NS)
            end_ns = self._float_param(params, 2, self.DEFAULT_TARGET_END_NS)
        else:
            zero_correct = self._bool_param(params, 1, import_params.get("zero_correct", self.DEFAULT_ZERO_CORRECT))
            start_ns = self.DEFAULT_TARGET_START_NS
            end_ns = self.DEFAULT_TARGET_END_NS
        start_ns = max(0.0, float(start_ns))
        end_ns = max(start_ns + 1e-6, float(end_ns))
        return {
            "beta": self._float_param(params, 0, import_params.get("beta", self.DEFAULT_KAISER_BETA)),
            "zero_correct": zero_correct,
            "start_ns": start_ns,
            "end_ns": end_ns,
        }

    def _resolve_isdft_config(self, params: tuple[float, ...]) -> dict[str, float | bool | int]:
        import_params = self._import_params()
        isdft_import = import_params.get("isdft", {})
        if len(params) >= 7:
            zero_correct = self._bool_param(params, 3, import_params.get("zero_correct", self.DEFAULT_ZERO_CORRECT))
            alpha = self._float_param(params, 4, isdft_import.get("alpha", self.DEFAULT_ISDFT_ALPHA))
            th_db = self._float_param(params, 5, isdft_import.get("th_db", self.DEFAULT_ISDFT_TH_DB))
            ramp_ns = self._float_param(params, 6, isdft_import.get("ramp_ns", self.DEFAULT_ISDFT_RAMP_NS))
        else:
            zero_correct = self._bool_param(params, 1, import_params.get("zero_correct", self.DEFAULT_ZERO_CORRECT))
            alpha = self._float_param(params, 2, isdft_import.get("alpha", self.DEFAULT_ISDFT_ALPHA))
            th_db = self._float_param(params, 3, isdft_import.get("th_db", self.DEFAULT_ISDFT_TH_DB))
            ramp_ns = self._float_param(params, 4, isdft_import.get("ramp_ns", self.DEFAULT_ISDFT_RAMP_NS))
        return {
            "beta": self._float_param(params, 0, import_params.get("beta", self.DEFAULT_KAISER_BETA)),
            "zero_correct": zero_correct,
            "alpha": alpha,
            "th_db": th_db,
            "ramp_ns": ramp_ns,
            "smooth_len": int(isdft_import.get("smooth_len", self.DEFAULT_ISDFT_SMOOTH_LEN)),
        }

    def _resolve_ifft_config(self, params: tuple[float, ...]) -> dict[str, float | bool]:
        return {
            "beta": self._float_param(params, 0, self.DEFAULT_KAISER_BETA),
            "use_full_bw": self._bool_param(params, 1, self.DEFAULT_IFFT_USE_FULL_BW),
            "min_freq_mhz": self._float_param(params, 2, self.DEFAULT_IFFT_MIN_FREQ_MHZ),
            "max_freq_mhz": self._float_param(params, 3, self.DEFAULT_IFFT_MAX_FREQ_MHZ),
        }

    def _resolve_target_time_window_ns(self) -> tuple[float, float]:
        if self._display_time_window_ns is not None:
            start_ns = float(self._display_time_window_ns[0])
            end_ns = float(self._display_time_window_ns[1])
        else:
            start_ns = self.DEFAULT_TARGET_START_NS
            end_ns = self.DEFAULT_TARGET_END_NS
        start_ns = max(0.0, start_ns)
        end_ns = max(start_ns + 1e-6, end_ns)
        return start_ns, end_ns

    def _resolve_transform_sample_count(self, data: np.ndarray) -> int:
        if self.dataset is not None:
            header_sample_count = int(self.dataset.header.get("sample_count", 0) or 0)
            if header_sample_count > 1:
                return header_sample_count
        return max(int(data.shape[0]), 1)

    def _resolve_isdft_workers(self, total_lines: int) -> int:
        if total_lines <= 1:
            return 1
        cpu_count = os.cpu_count() or 1
        cpu_limited = max(1, cpu_count // 2)
        return max(1, min(int(total_lines), self.DEFAULT_ISDFT_MAX_WORKERS, cpu_limited))

    def _resolve_prepare_workers(self, total_lines: int) -> int:
        if total_lines <= 1:
            return 1
        cpu_count = os.cpu_count() or 1
        cpu_limited = max(1, cpu_count // 2)
        return max(1, min(int(total_lines), self.DEFAULT_PREPARE_MAX_WORKERS, cpu_limited))

    def _import_params(self) -> dict[str, object]:
        if self.dataset is None or not isinstance(self.dataset.import_params, dict):
            return {}
        return dict(self.dataset.import_params)

    def _resolve_dataset_frequency_band(self) -> tuple[float, float]:
        if self.dataset is None:
            return 0.0, 0.0
        start_freq = float(self.dataset.header.get("start_frequency_hz", 0.0))
        end_freq = float(self.dataset.header.get("end_frequency_hz", 0.0))
        if end_freq <= start_freq:
            import_params = self._import_params()
            start_freq = float(import_params.get("f_start_hz") or start_freq)
            end_freq = float(import_params.get("f_end_hz") or end_freq)
        return start_freq, end_freq

    def _resolve_ifft_frequency_band(self, use_full_bw: bool, min_freq_mhz: float, max_freq_mhz: float) -> tuple[float, float]:
        start_freq, end_freq = self._resolve_dataset_frequency_band()
        if use_full_bw:
            return start_freq, end_freq

        min_freq_hz = min_freq_mhz * 1e6
        max_freq_hz = max_freq_mhz * 1e6
        clipped_start = max(start_freq, min_freq_hz)
        clipped_end = min(end_freq, max_freq_hz)
        if clipped_end <= clipped_start:
            return start_freq, end_freq
        return clipped_start, clipped_end

    def _resample_frequency_data(
        self,
        data: np.ndarray,
        target_count: int,
        target_start_hz: float,
        target_end_hz: float,
    ) -> np.ndarray:
        safe_data = np.asarray(data, dtype=np.complex64)
        if not np.all(np.isfinite(safe_data)):
            safe_data = np.nan_to_num(safe_data, nan=0.0, posinf=0.0, neginf=0.0).astype(np.complex64, copy=False)

        source_count = int(safe_data.shape[0])
        source_start_hz, source_end_hz = self._resolve_dataset_frequency_band()
        if source_count <= 0 or source_end_hz <= source_start_hz:
            return safe_data

        same_count = target_count == source_count
        same_band = abs(target_start_hz - source_start_hz) <= 1e-6 and abs(target_end_hz - source_end_hz) <= 1e-6
        if target_count <= 0 or (same_count and same_band):
            return safe_data

        orig_freq = np.linspace(source_start_hz, source_end_hz, source_count, dtype=np.float64)
        target_freq = np.linspace(target_start_hz, target_end_hz, target_count, dtype=np.float64)
        reshaped = safe_data.reshape(source_count, -1)
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

    @staticmethod
    def _build_band_time_meta(start_freq_hz: float, end_freq_hz: float, sample_count: int, t0_ns: float) -> dict[str, float]:
        bandwidth = float(end_freq_hz) - float(start_freq_hz)
        if sample_count > 1 and bandwidth > 0:
            dt_ns = 1e9 / bandwidth
            tw_ns = dt_ns * (sample_count - 1)
        else:
            dt_ns = 0.0
            tw_ns = 0.0
        return {
            "t0_ns": float(t0_ns),
            "tw_ns": tw_ns,
            "dt_ns": dt_ns,
        }

    @staticmethod
    def _build_requested_time_meta(start_ns: float, end_ns: float, sample_count: int) -> dict[str, float]:
        if sample_count > 1:
            dt_ns = (float(end_ns) - float(start_ns)) / float(sample_count - 1)
            tw_ns = dt_ns * float(sample_count - 1)
        else:
            dt_ns = 0.0
            tw_ns = 0.0
        return {
            "t0_ns": float(start_ns),
            "tw_ns": tw_ns,
            "dt_ns": dt_ns,
            "transform_window_start_ns": float(start_ns),
            "transform_window_end_ns": float(end_ns),
        }

    @staticmethod
    def _float_param(params: tuple[float, ...], index: int, default: object) -> float:
        if index < len(params):
            return float(params[index])
        return float(default)

    @staticmethod
    def _bool_param(params: tuple[float, ...], index: int, default: object) -> bool:
        if index < len(params):
            return bool(int(round(float(params[index]))))
        return bool(default)

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
