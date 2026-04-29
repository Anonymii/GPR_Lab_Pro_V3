from __future__ import annotations

import numpy as np
from scipy import signal
from scipy.interpolate import interp1d

from gpr_lab_pro.algorithms import correct_direct_wave, isdft_soft_phys
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
    DEFAULT_IFFT_USE_FULL_BW: bool = True
    DEFAULT_IFFT_MIN_FREQ_MHZ: float = 30.0
    DEFAULT_IFFT_MAX_FREQ_MHZ: float = 3000.0

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
            return self._ifft_transform(arr, operation, progress_callback=progress_callback, cancel_callback=cancel_callback)
        if op_type == "czt":
            return self._czt_transform(arr, operation, progress_callback=progress_callback, cancel_callback=cancel_callback)
        if op_type == "isdft":
            return self._isdft_transform(arr, operation, progress_callback=progress_callback, cancel_callback=cancel_callback)
        raise ValueError(f"Unsupported transform operation: {operation.type}")

    def _ifft_transform(self, data: np.ndarray, operation: PipelineOperation, progress_callback=None, cancel_callback=None) -> np.ndarray:
        cfg = self._resolve_ifft_config(operation.params)
        start_freq_hz, end_freq_hz = self._resolve_ifft_frequency_band(cfg["use_full_bw"], cfg["min_freq_mhz"], cfg["max_freq_mhz"])
        sample_new = data.shape[0]
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
        sample_new = data.shape[0]
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
            stage_name="CZT",
        )
        time_meta = self._build_band_time_meta(start_freq_hz, end_freq_hz, sample_new, 0.0)
        t0_s = 0.0
        t1_s = time_meta["tw_ns"] * 1e-9
        bandwidth = max(end_freq_hz - start_freq_hz, 1.0)
        ts_val = 1.0 / (bandwidth / sample_new)
        w_value = np.exp(1j * 2.0 * np.pi * (t1_s - t0_s) / sample_new / ts_val)
        a_value = np.exp(1j * 2.0 * np.pi * (-t0_s) / ts_val)
        check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 75, "正在执行 CZT")
        out = signal.czt(prepared, m=sample_new, w=w_value, a=a_value, axis=0)
        self._report_progress(progress_callback, 100, "CZT 执行完成")
        self._last_time_meta = time_meta
        return np.asarray(out, dtype=np.complex64)

    def _isdft_transform(self, data: np.ndarray, operation: PipelineOperation, progress_callback=None, cancel_callback=None) -> np.ndarray:
        cfg = self._resolve_isdft_config(operation.params)
        start_freq_hz, end_freq_hz = self._resolve_dataset_frequency_band()
        sample_new = data.shape[0]
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
        time_meta = self._build_band_time_meta(start_freq_hz, end_freq_hz, sample_new, 0.0)
        t = np.linspace(0.0, time_meta["tw_ns"] * 1e-9, sample_new)
        out = np.zeros_like(prepared, dtype=np.complex64)
        for line_idx in range(prepared.shape[2]):
            check_cancelled(cancel_callback)
            self._report_line_progress(progress_callback, line_idx, prepared.shape[2], 55, 100, "正在执行 ISDFT")
            tau_base = 0.0 if cfg["zero_correct"] else float(tau_bases[line_idx])
            out[:, :, line_idx] = isdft_soft_phys(
                prepared[:, :, line_idx],
                start_freq_hz,
                end_freq_hz,
                t,
                cfg["alpha"],
                cfg["th_db"],
                cfg["smooth_len"],
                tau_base,
                cfg["ramp_ns"],
            )
        self._last_time_meta = time_meta
        return out.astype(np.complex64, copy=False)

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

        for line_idx in range(total_lines):
            check_cancelled(cancel_callback)
            current = resampled[:, :, line_idx]
            if zero_correct:
                current, _ = correct_direct_wave(current, freq_axis)
            elif need_tau_base:
                _, tau_bases[line_idx] = correct_direct_wave(current, freq_axis)
            prepared[:, :, line_idx] = (current * window[:, None]).astype(np.complex64, copy=False)
            self._report_line_progress(progress_callback, line_idx, total_lines, 10, 50, f"正在准备 {stage_name} 输入")
        return prepared, tau_bases

    def _resolve_czt_config(self, params: tuple[float, ...]) -> dict[str, float | bool]:
        import_params = self._import_params()
        if len(params) >= 4:
            zero_correct = self._bool_param(params, 3, import_params.get("zero_correct", self.DEFAULT_ZERO_CORRECT))
        else:
            zero_correct = self._bool_param(params, 1, import_params.get("zero_correct", self.DEFAULT_ZERO_CORRECT))
        return {
            "beta": self._float_param(params, 0, import_params.get("beta", self.DEFAULT_KAISER_BETA)),
            "zero_correct": zero_correct,
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
