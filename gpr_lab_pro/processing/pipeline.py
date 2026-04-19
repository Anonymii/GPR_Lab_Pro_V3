from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pywt
from scipy import ndimage, signal

from gpr_lab_pro.algorithms import (
    bg_remove_adaptive_protect,
    bg_remove_highpass,
    fk_notch_shift,
    gradual_lowpass_filter,
    interference_suppression_active,
    interference_suppression_freq,
    migration_kirchhoff_time,
    migration_stolt,
    phase_correction_core,
    pred_decon,
    remove_bg_logic,
    smoothing_fk_filter,
    soft_threshold,
)
from gpr_lab_pro.algorithms.core import moving_mean_axis, moving_std_axis
from gpr_lab_pro.models import GPRContext, PipelineOperation


class PipelineProcessor:
    """Python version of the MATLAB process_step-based pipeline engine."""

    def __init__(self, context: GPRContext):
        self.context = context

    def execute_pipeline(
        self,
        data: np.ndarray,
        operations: Iterable[PipelineOperation],
    ) -> np.ndarray:
        current = self._ensure_3d(data)
        for op in operations:
            current = self.process_step(current, op.type, op.params)
        return current

    def process_step(
        self, V_in: np.ndarray, op_type: str, params: Sequence[float] | None = None
    ) -> np.ndarray:
        V_in = self._ensure_3d(V_in)
        params = tuple(params or ())
        V_out = np.array(V_in, copy=True)
        ns_p, nt_p, nl_p = V_in.shape
        dt = self.context.dt
        fs = self.context.fs

        if op_type.startswith("ext_"):
            if not self.context.plugin_resolver:
                raise ValueError(f"No plugin resolver configured for {op_type}.")
            plugin = self.context.plugin_resolver(op_type[4:])
            V_out = np.asarray(plugin(V_in, params, "run"))
            return self._limit_output(op_type, self._ensure_3d(V_out))

        if op_type == "dewow":
            win = max(3, int(round(params[0] if params else 20)))
            V_out = V_in - moving_mean_axis(V_in, win, axis=0)

        elif op_type == "interf_active":
            thr_db = float(np.clip(params[0] if params else 10.0, 0, 40))
            for line in range(nl_p):
                spectrum = np.fft.fft(V_in[:, :, line], axis=0)
                cleaned, _ = interference_suppression_active(spectrum, thr_db)
                recovered = np.fft.ifft(cleaned, axis=0)
                V_out[:, :, line] = recovered.real if np.isrealobj(V_in) else recovered

        elif op_type == "t0":
            target_idx = int(round((params[0] if params else 0.0) / dt))
            search_end = max(1, round(ns_p / 3))
            max_idx = np.argmax(np.abs(V_in[:search_end, :, :]), axis=0) + 1
            shift_mat = target_idx - max_idx
            for line in range(nl_p):
                for trace in range(nt_p):
                    sh = int(shift_mat[trace, line])
                    shifted = np.roll(V_out[:, trace, line], sh)
                    if sh > 0:
                        shifted[:sh] = 0
                    V_out[:, trace, line] = shifted

        elif op_type == "t0_fb":
            target_time = params[0] if len(params) >= 1 else 1.0
            K = params[1] if len(params) >= 2 else 5.0
            target_idx = int(np.clip(round(target_time / dt), 1, ns_p))
            search_end = max(5, round(ns_p / 3))
            noise_end = max(5, round(ns_p / 10))
            V_out = np.zeros_like(V_in)
            for line in range(nl_p):
                data2d = V_in[:, :, line]
                fb_idx = np.full(nt_p, np.nan)
                for trace in range(nt_p):
                    tr = data2d[:, trace]
                    nstd = np.std(tr[:noise_end])
                    thr = K * (nstd + np.finfo(float).eps)
                    hits = np.flatnonzero(np.abs(tr[:search_end]) > thr)
                    if hits.size:
                        fb_idx[trace] = hits[0] + 1
                valid = fb_idx[~np.isnan(fb_idx)]
                if valid.size == 0:
                    V_out[:, :, line] = data2d
                    continue
                line_idx = int(round(np.median(valid)))
                sh = target_idx - line_idx
                tmp = np.roll(data2d, sh, axis=0)
                if sh > 0:
                    tmp[:sh, :] = 0
                elif sh < 0:
                    tmp[sh:, :] = 0
                V_out[:, :, line] = tmp

        elif op_type == "bg_mean":
            V_out = V_in - np.mean(V_in, axis=1, keepdims=True)

        elif op_type == "bg_median":
            med = np.median(V_in.real, axis=1, keepdims=True)
            if np.iscomplexobj(V_in):
                med = med + 1j * np.median(V_in.imag, axis=1, keepdims=True)
            V_out = V_in - med

        elif op_type == "bg_move":
            win = int(round(params[0])) if params else 1
            V_out = V_in - moving_mean_axis(V_in, win, axis=1)

        elif op_type == "bg_svd":
            k = int(round(params[0])) if params else 1
            for line in range(nl_p):
                U, s, Vh = np.linalg.svd(V_in[:, :, line], full_matrices=False)
                s[:k] = 0
                V_out[:, :, line] = (U * s) @ Vh

        elif op_type == "bg_rpca":
            lam = 1.0 / np.sqrt(max(ns_p, nt_p))
            for line in range(nl_p):
                low_rank = np.repeat(
                    np.median(V_in[:, :, line].real, axis=1, keepdims=True), nt_p, axis=1
                )
                sparse = V_in[:, :, line].real - low_rank
                recon = np.sign(sparse) * np.maximum(np.abs(sparse) - lam, 0.0)
                if np.iscomplexobj(V_in):
                    recon = recon + 1j * V_in[:, :, line].imag
                V_out[:, :, line] = recon

        elif op_type == "bg_top_mute":
            idx = int(round((params[0] if params else 0.0) / dt))
            if idx > 0:
                V_out[:idx, :, :] = 0

        elif op_type == "bg_freq":
            method_idx = int(round(params[0])) if params else 1
            for line in range(nl_p):
                spectrum = np.fft.fft(V_in[:, :, line], axis=0)
                cleaned = remove_bg_logic(spectrum, method_idx)
                V_out[:, :, line] = np.fft.ifft(cleaned, axis=0).real

        elif op_type == "bg_highpass":
            V_out = bg_remove_highpass(V_in, dt, params[0], params[1], params[2], params[3])

        elif op_type == "bg_adaptive":
            protect_start = params[5] if len(params) >= 7 else 0.0
            protect_end = params[6] if len(params) >= 7 else 0.0
            V_out, _ = bg_remove_adaptive_protect(
                V_in,
                dt,
                params[0],
                params[1],
                params[2],
                params[3],
                params[4],
                protect_start,
                protect_end,
            )

        elif op_type == "gain_lin":
            A0 = max(params[0] if len(params) >= 1 else 1.0, 0.0)
            k = params[1] if len(params) >= 2 else 0.05
            t_ns = np.arange(ns_p, dtype=float) * dt
            g = np.clip(A0 + k * t_ns, 0, 500)
            V_out = V_in * g[:, None, None]

        elif op_type == "gain_exp":
            C = max(params[0] if len(params) >= 1 else 1.0, 0.0)
            pexp = params[1] if len(params) >= 2 else 1.0
            t_norm = np.linspace(0.0, 1.0, ns_p)
            g = np.clip(C * np.exp(pexp * t_norm), None, 500)
            V_out = V_in * g[:, None, None]

        elif op_type == "gain_sph":
            vel = params[0] if params else 0.1
            t = np.arange(ns_p, dtype=float) * dt
            g = 1.0 + (vel * t) * 20.0
            V_out = V_in * g[:, None, None]

        elif op_type == "gain_sec":
            a = params[0] if params else 1.0
            t = np.arange(ns_p, dtype=float) * dt
            g = np.exp(a * t / 500.0) * (t + 10.0) ** 1.2
            g = g / max(np.max(g), np.finfo(float).eps) * 100.0
            V_out = V_in * g[:, None, None]

        elif op_type == "gain_agc":
            env = moving_mean_axis(np.abs(V_in), int(round(params[0])), axis=0)
            V_out = V_in / (env + np.max(env) * 0.01 + 1e-9) * 50.0

        elif op_type == "gain_tgc_agc":
            g = np.exp((params[0] if params else 1.0) * np.linspace(0, 5, ns_p))
            V_tmp = V_in * g[:, None, None]
            env = moving_mean_axis(np.abs(V_tmp), 50, axis=0)
            V_out = V_tmp / (env + 1e-9) * 50.0

        elif op_type == "gain_bg_est":
            sm = moving_mean_axis(
                np.mean(np.abs(V_in), axis=(1, 2)),
                int(round(params[0])) if params else 1,
                axis=0,
            )
            g = np.max(sm) / (sm + 1e-6)
            g = np.clip(g, None, 1000)
            V_out = V_in * g[:, None, None]

        elif op_type == "gain_ada_contrast":
            win_ns = params[0] if len(params) >= 1 else 10.0
            win_size = max(3, int(round(win_ns / max(self.context.dt, 1e-9))))
            strength = float(np.clip(params[1], 1, 10))
            for line in range(nl_p):
                data2d = V_in[:, :, line]
                local_mean = moving_mean_axis(data2d, win_size, axis=0)
                local_std = np.maximum(moving_std_axis(data2d, win_size, axis=0), 1e-9)
                enhanced = (data2d - local_mean) * strength / local_std + local_mean
                max_val = np.max(np.abs(enhanced))
                if max_val > 1e6:
                    enhanced = enhanced / max_val * 1e6
                V_out[:, :, line] = enhanced

        elif op_type == "filt_vert":
            V_out = moving_mean_axis(V_in, int(round(params[0])), axis=0)

        elif op_type == "filt_horz":
            V_out = moving_mean_axis(V_in, int(round(params[0])), axis=1)

        elif op_type == "filt_bp_fft":
            f_lo = params[0] * 1e6
            f_hi = params[1] * 1e6
            freqs = np.arange(ns_p, dtype=float) * fs / ns_p
            mask = ((freqs >= f_lo) & (freqs <= f_hi)) | (
                (freqs >= (fs - f_hi)) & (freqs <= (fs - f_lo))
            )
            for line in range(nl_p):
                F = np.fft.fft(V_in[:, :, line], axis=0)
                V_out[:, :, line] = np.fft.ifft(F * mask[:, None], axis=0).real

        elif op_type == "filt_decon":
            V_out = np.asarray(pred_decon(V_in, tuple(params), "run"))

        elif op_type == "filt_med2":
            ws = max(1, int(round(params[0])))
            for line in range(nl_p):
                V_out[:, :, line] = ndimage.median_filter(
                    V_in[:, :, line].real, size=(ws, ws), mode="nearest"
                )

        elif op_type == "filt_mean2":
            ws = max(1, int(round(params[0])))
            for line in range(nl_p):
                V_out[:, :, line] = ndimage.uniform_filter(
                    V_in[:, :, line], size=(ws, ws), mode="nearest"
                )

        elif op_type == "filt_bp2d":
            r_lo, r_hi = params[:2]
            for line in range(nl_p):
                D = np.fft.fftshift(np.fft.fft2(V_in[:, :, line]))
                ny, nx = D.shape
                cx, cy = nx // 2, ny // 2
                X, Y = np.meshgrid(np.arange(nx), np.arange(ny))
                R = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
                mask = (R >= r_lo) & (R <= r_hi)
                V_out[:, :, line] = np.fft.ifft2(np.fft.ifftshift(D * mask)).real

        elif op_type == "filt_fk":
            width = max(1, int(round(params[0]))) if len(params) >= 1 else 5
            start_time_ns = params[1] if len(params) >= 2 else 1.0
            start_sample = max(0, min(ns_p, int(round(start_time_ns / dt))))
            for line in range(nl_p):
                data = V_in[:, :, line]
                upper = data[:start_sample, :] if start_sample > 0 else data[:0, :]
                lower = data[start_sample:, :]
                if lower.size:
                    D = np.fft.fftshift(np.fft.fft2(lower))
                    nz, nx = D.shape
                    cx = nx // 2
                    mask = np.ones((nz, nx), dtype=np.float32)
                    mask[:, max(0, cx - width) : min(nx, cx + width + 1)] = 0.0
                    filtered = np.fft.ifft2(np.fft.ifftshift(D * mask)).real
                else:
                    filtered = lower
                V_out[:, :, line] = np.vstack([upper, filtered])

        elif op_type == "filt_fk_shift":
            width = params[0] if len(params) >= 1 else 5.0
            shift = params[1] if len(params) >= 2 else 10.0
            V_out = fk_notch_shift(V_in, width, shift)

        elif op_type == "filt_glp":
            mode = params[0] if len(params) >= 1 else 3.0
            strength = params[1] if len(params) >= 2 else 2.0
            V_out = gradual_lowpass_filter(V_in, mode, strength)

        elif op_type == "filt_smooth_fk":
            strength = params[0] if params else 2.0
            V_out = smoothing_fk_filter(V_in, strength)

        elif op_type == "mig_kirchhoff":
            vel = params[0] if len(params) >= 1 else 0.1
            dx = params[1] if len(params) >= 2 else 0.05
            rmax = params[2] if len(params) >= 3 else 0.75
            halfang = params[3] if len(params) >= 4 else 30.0
            for line in range(nl_p):
                V_out[:, :, line] = migration_kirchhoff_time(
                    V_in[:, :, line], dt, dx, vel, rmax, halfang
                )

        elif op_type == "mig_stolt":
            vel = params[0] if len(params) >= 1 else 0.1
            dx = params[1] if len(params) >= 2 else 0.05
            ov = params[2] if len(params) >= 3 else 32
            for line in range(nl_p):
                V_out[:, :, line] = migration_stolt(V_in[:, :, line], dt, dx, vel, ov)

        elif op_type == "filt_grad":
            V_out = np.zeros_like(V_in)
            V_out[:, :-1, :] = np.diff(V_in, axis=1)

        elif op_type == "filt_wiener":
            sz = max(3, int(round(params[0]))) if params else 3
            kernel = (sz, sz, 1)
            if np.iscomplexobj(V_in):
                V_out = signal.wiener(V_in.real, kernel) + 1j * signal.wiener(V_in.imag, kernel)
            else:
                V_out = signal.wiener(V_in, kernel)

        elif op_type == "filt_notch":
            freq_mhz = params[0] if params else 50.0
            wo = (freq_mhz * 1e6) / (fs / 2.0)
            wo = float(np.clip(wo, 1e-6, 0.999999))
            # MATLAB iirnotch uses normalized bandwidth, while SciPy expects Q.
            # The MATLAB code sets bw = wo / 35, so the equivalent SciPy Q is 35.
            q = 35.0
            b, a = signal.iirnotch(wo, q)
            for line in range(nl_p):
                data = V_in[:, :, line]
                if np.iscomplexobj(data):
                    V_out[:, :, line] = signal.filtfilt(b, a, data.real, axis=0) + 1j * signal.filtfilt(
                        b, a, data.imag, axis=0
                    )
                else:
                    V_out[:, :, line] = signal.filtfilt(b, a, data, axis=0)

        elif op_type == "interf_passive":
            thr_db = params[0] if len(params) >= 1 else 10.0
            win_bins = max(5, int(round(params[1]))) if len(params) >= 2 else 31
            expand_bins = max(0, int(round(params[2]))) if len(params) >= 3 else 2
            hit_ratio = float(np.clip(params[3], 0, 1)) if len(params) >= 4 else 0.15
            treat_as_freq = round(params[4]) == 1 if len(params) >= 5 else False
            for line in range(nl_p):
                spectrum = V_in[:, :, line] if treat_as_freq else np.fft.fft(V_in[:, :, line], axis=0)
                cleaned, _, _ = interference_suppression_freq(
                    spectrum, thr_db, win_bins, expand_bins, hit_ratio
                )
                V_out[:, :, line] = cleaned if treat_as_freq else np.fft.ifft(cleaned, axis=0).real

        elif op_type == "filt_adapt":
            for line in range(nl_p):
                img = V_in[:, :, line].real.astype(float)
                mu = ndimage.uniform_filter(img, size=3, mode="nearest")
                sigma_local = np.sqrt(
                    np.maximum(
                        ndimage.uniform_filter(img**2, size=3, mode="nearest") - mu**2,
                        0.0,
                    )
                )
                mask = 1.0 / (1.0 + sigma_local**2)
                V_out[:, :, line] = img * (1.0 - mask) + mu * mask

        elif op_type == "wav_swt1":
            for line in range(nl_p):
                x = V_in[:, :, line].real.astype(float).copy()
                thr = self._minimaxi_threshold(x.ravel())
                for trace in range(nt_p):
                    coeffs = pywt.wavedec(x[:, trace], "sym4", level=3)
                    coeffs[1:] = [pywt.threshold(c, thr, mode="soft") for c in coeffs[1:]]
                    x[:, trace] = pywt.waverec(coeffs, "sym4")[:ns_p]
                V_out[:, :, line] = x

        elif op_type == "wav_dwt2":
            for line in range(nl_p):
                img = V_in[:, :, line].real.astype(float)
                coeffs = pywt.wavedec2(img, "sym4", level=2)
                sigma = self._sigma_from_details(coeffs)
                thr = sigma * np.sqrt(2.0 * np.log(img.size))
                approx = coeffs[0]
                details = [
                    tuple(pywt.threshold(c, thr, mode="soft") for c in detail)
                    for detail in coeffs[1:]
                ]
                V_out[:, :, line] = pywt.waverec2([approx] + details, "sym4")[:ns_p, :nt_p]

        elif op_type == "freq_wavelet":
            level = max(1, int(round(params[0]))) if params else 5
            for line in range(nl_p):
                data_freq = np.fft.fft(V_in[:, :, line], axis=0)
                clean = np.zeros_like(data_freq, dtype=np.complex128)
                for trace in range(nt_p):
                    clean[:, trace] = self._wavelet_denoise_complex(data_freq[:, trace], level, "db8")
                V_out[:, :, line] = np.fft.ifft(clean, axis=0).real

        elif op_type == "filt_hann_window":
            win = np.hanning(ns_p)
            for line in range(nl_p):
                data_freq = np.fft.fft(V_in[:, :, line], axis=0)
                V_out[:, :, line] = np.fft.ifft(data_freq * win[:, None], axis=0).real

        elif op_type == "phase_correction":
            f_start_mhz = params[0] if len(params) >= 1 else 500.0
            f_end_mhz = params[1] if len(params) >= 2 else 2500.0
            frequencies = np.arange(ns_p, dtype=float) * (fs / ns_p)
            mask = (frequencies >= f_start_mhz * 1e6) & (frequencies <= f_end_mhz * 1e6)
            for line in range(nl_p):
                data_freq = np.fft.fft(V_in[:, :, line], axis=0)
                corrected = phase_correction_core(data_freq, frequencies, mask)
                V_out[:, :, line] = np.fft.ifft(corrected, axis=0).real

        elif op_type == "cs_reconstruction":
            sampling_rate = float(np.clip(params[0], 5, 90))
            lambda_ = float(params[1])
            for line in range(nl_p):
                bscan = V_in[:, :, line].real.astype(float)
                ns, nt = bscan.shape
                n_samples = round(ns * nt * sampling_rate / 100.0)
                total = ns * nt
                sample_idx = np.random.permutation(total)[:n_samples]
                mask = np.zeros(total, dtype=float)
                mask[sample_idx] = 1.0
                mask = mask.reshape(ns, nt)
                y = bscan * mask
                x_recon = np.zeros_like(bscan)
                for _ in range(1000):
                    residual = y - x_recon * mask
                    grad = -2.0 * (residual * mask)
                    x_recon = soft_threshold(x_recon - 0.01 * grad, lambda_ * 0.01)
                    if np.linalg.norm(residual) < 1e-6:
                        break
                x_norm = (x_recon - np.min(x_recon)) / (
                    np.max(x_recon) - np.min(x_recon) + np.finfo(float).eps
                )
                orig_min = np.min(bscan)
                orig_max = np.max(bscan)
                V_out[:, :, line] = x_norm * (orig_max - orig_min) + orig_min

        return self._limit_output(op_type, V_out)

    def _ensure_3d(self, data: np.ndarray) -> np.ndarray:
        arr = np.asarray(data)
        if arr.ndim == 2:
            return arr[:, :, None]
        if arr.ndim != 3:
            raise ValueError("Expected a 2D or 3D GPR array.")
        return arr

    def _limit_output(self, op_type: str, data: np.ndarray) -> np.ndarray:
        data = np.array(data, copy=False)
        if "gain" in op_type:
            sd = np.std(data)
            if np.isfinite(sd) and sd > 1e-9:
                lim = sd * self.context.clip_sigma
                data = np.clip(data, -lim, lim)
        else:
            data = np.clip(data, -1e15, 1e15)
        return data

    @staticmethod
    def _sigma_from_details(coeffs: list) -> float:
        detail = coeffs[-1][-1]
        return np.median(np.abs(detail)) / 0.6745 + np.finfo(float).eps

    @staticmethod
    def _minimaxi_threshold(values: np.ndarray) -> float:
        values = np.asarray(values, dtype=float).ravel()
        sigma = np.median(np.abs(values - np.median(values))) / 0.6745 + np.finfo(float).eps
        n = max(1, values.size)
        factor = 0.0 if n <= 32 else 0.3936 + 0.1829 * np.log2(n)
        return sigma * factor

    def _wavelet_denoise_complex(
        self, signal_1d: np.ndarray, level: int, wavelet: str
    ) -> np.ndarray:
        real_dn = self._wavelet_denoise_real(signal_1d.real, level, wavelet)
        imag_dn = self._wavelet_denoise_real(signal_1d.imag, level, wavelet)
        return real_dn + 1j * imag_dn

    @staticmethod
    def _wavelet_denoise_real(values: np.ndarray, level: int, wavelet: str) -> np.ndarray:
        coeffs = pywt.wavedec(values, wavelet=wavelet, level=level)
        detail = coeffs[-1]
        sigma = np.median(np.abs(detail)) / 0.6745 + np.finfo(float).eps
        thr = sigma * np.sqrt(2.0 * np.log(values.size))
        coeffs[1:] = [pywt.threshold(c, thr, mode="soft") for c in coeffs[1:]]
        return pywt.waverec(coeffs, wavelet)[: values.size]
