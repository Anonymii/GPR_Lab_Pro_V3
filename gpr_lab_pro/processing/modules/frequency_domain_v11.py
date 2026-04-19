from __future__ import annotations

import numpy as np
import pywt

from gpr_lab_pro.algorithms import (
    interference_suppression_active,
    interference_suppression_freq,
    phase_correction_core,
    remove_bg_logic,
)
from gpr_lab_pro.models import GPRContext, PipelineOperation


class V11FrequencyDomainOperator:
    DIRECT_OPS: tuple[str, ...] = (
        "phase_correction",
        "interf_active",
        "interf_passive",
        "bg_freq",
        "filt_hann_window",
        "freq_wavelet",
    )

    def __init__(self, context: GPRContext) -> None:
        self.context = context

    def supports(self, op_type: str) -> bool:
        return op_type in self.DIRECT_OPS

    def execute(self, data: np.ndarray, operation: PipelineOperation) -> np.ndarray:
        arr = self._ensure_3d(data)
        params = tuple(operation.params or ())
        op_type = operation.type
        ns_p, nt_p, nl_p = arr.shape
        fs = self.context.fs
        out = np.array(arr, copy=True)

        if op_type == "interf_active":
            thr_db = float(np.clip(params[0] if params else 10.0, 0, 40))
            for line in range(nl_p):
                cleaned, _ = interference_suppression_active(arr[:, :, line], thr_db)
                out[:, :, line] = cleaned

        elif op_type == "bg_freq":
            method_idx = int(round(params[0])) if params else 1
            for line in range(nl_p):
                out[:, :, line] = remove_bg_logic(arr[:, :, line], method_idx)

        elif op_type == "interf_passive":
            thr_db = params[0] if len(params) >= 1 else 10.0
            win_bins = max(5, int(round(params[1]))) if len(params) >= 2 else 31
            expand_bins = max(0, int(round(params[2]))) if len(params) >= 3 else 2
            hit_ratio = float(np.clip(params[3], 0, 1)) if len(params) >= 4 else 0.15
            treat_as_freq = round(params[4]) == 1 if len(params) >= 5 else False
            for line in range(nl_p):
                spectrum = arr[:, :, line] if treat_as_freq else arr[:, :, line]
                cleaned, _, _ = interference_suppression_freq(
                    spectrum,
                    thr_db,
                    win_bins,
                    expand_bins,
                    hit_ratio,
                )
                out[:, :, line] = cleaned

        elif op_type == "freq_wavelet":
            level = max(1, int(round(params[0]))) if params else 5
            for line in range(nl_p):
                data_freq = arr[:, :, line]
                clean = np.zeros_like(data_freq, dtype=np.complex128)
                for trace in range(nt_p):
                    clean[:, trace] = self._wavelet_denoise_complex(data_freq[:, trace], level, "db8")
                out[:, :, line] = clean

        elif op_type == "filt_hann_window":
            win = np.hanning(ns_p)
            out = arr * win[:, None, None]

        elif op_type == "phase_correction":
            f_start_mhz = params[0] if len(params) >= 1 else 500.0
            f_end_mhz = params[1] if len(params) >= 2 else 2500.0
            frequencies = np.arange(ns_p, dtype=float) * (fs / ns_p)
            mask = (frequencies >= f_start_mhz * 1e6) & (frequencies <= f_end_mhz * 1e6)
            for line in range(nl_p):
                out[:, :, line] = phase_correction_core(arr[:, :, line], frequencies, mask)

        return self._limit_output(out)

    @staticmethod
    def _ensure_3d(data: np.ndarray) -> np.ndarray:
        arr = np.asarray(data)
        if arr.ndim == 2:
            return arr[:, :, None]
        if arr.ndim != 3:
            raise ValueError("Expected a 2D or 3D GPR array.")
        return arr

    @staticmethod
    def _wavelet_denoise_complex(signal_1d: np.ndarray, level: int, wavelet: str) -> np.ndarray:
        real_dn = V11FrequencyDomainOperator._wavelet_denoise_real(signal_1d.real, level, wavelet)
        imag_dn = V11FrequencyDomainOperator._wavelet_denoise_real(signal_1d.imag, level, wavelet)
        return real_dn + 1j * imag_dn

    @staticmethod
    def _wavelet_denoise_real(values: np.ndarray, level: int, wavelet: str) -> np.ndarray:
        coeffs = pywt.wavedec(values, wavelet=wavelet, level=level)
        detail = coeffs[-1]
        sigma = np.median(np.abs(detail)) / 0.6745 + np.finfo(float).eps
        thr = sigma * np.sqrt(2.0 * np.log(values.size))
        coeffs[1:] = [pywt.threshold(coeff, thr, mode="soft") for coeff in coeffs[1:]]
        return pywt.waverec(coeffs, wavelet)[: values.size]

    @staticmethod
    def _limit_output(data: np.ndarray) -> np.ndarray:
        return np.clip(data, -1e15, 1e15)
