from __future__ import annotations

import numpy as np
import pywt
from scipy import ndimage, signal

from gpr_lab_pro.algorithms import (
    bg_remove_adaptive_protect,
    bg_remove_highpass,
    fk_notch_shift,
    gradual_lowpass_filter,
    migration_kirchhoff_time,
    migration_stolt,
    pred_decon,
    smoothing_fk_filter,
    soft_threshold,
)
from gpr_lab_pro.algorithms.core import moving_mean_axis, moving_std_axis
from gpr_lab_pro.models import GPRContext, PipelineOperation


class V11TimeDomainOperator:
    DIRECT_OPS: tuple[str, ...] = (
        "dewow",
        "t0",
        "t0_fb",
        "bg_mean",
        "bg_median",
        "bg_move",
        "bg_svd",
        "bg_rpca",
        "bg_top_mute",
        "bg_highpass",
        "bg_adaptive",
        "gain_lin",
        "gain_exp",
        "gain_sph",
        "gain_sec",
        "gain_agc",
        "gain_tgc_agc",
        "gain_bg_est",
        "gain_ada_contrast",
        "filt_vert",
        "filt_horz",
        "filt_bp_fft",
        "filt_decon",
        "filt_med2",
        "filt_mean2",
        "filt_bp2d",
        "filt_fk",
        "filt_fk_shift",
        "filt_glp",
        "filt_smooth_fk",
        "mig_kirchhoff",
        "mig_stolt",
        "filt_grad",
        "filt_wiener",
        "filt_notch",
        "filt_adapt",
        "wav_swt1",
        "wav_dwt2",
        "cs_reconstruction",
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
        dt = self.context.dt
        fs = self.context.fs
        out = np.array(arr, copy=True)

        if op_type == "dewow":
            win = max(3, int(round(params[0] if params else 20)))
            out = arr - moving_mean_axis(arr, win, axis=0)

        elif op_type == "t0":
            target_idx = int(round((params[0] if params else 0.0) / dt))
            search_end = max(1, round(ns_p / 3))
            max_idx = np.argmax(np.abs(arr[:search_end, :, :]), axis=0) + 1
            shift_mat = target_idx - max_idx
            for line in range(nl_p):
                for trace in range(nt_p):
                    sh = int(shift_mat[trace, line])
                    shifted = np.roll(out[:, trace, line], sh)
                    if sh > 0:
                        shifted[:sh] = 0
                    elif sh < 0:
                        shifted[sh:] = 0
                    out[:, trace, line] = shifted

        elif op_type == "t0_fb":
            target_time = params[0] if len(params) >= 1 else 1.0
            k_val = params[1] if len(params) >= 2 else 5.0
            target_idx = int(np.clip(round(target_time / dt), 1, ns_p))
            search_end = max(5, round(ns_p / 3))
            noise_end = max(5, round(ns_p / 10))
            out = np.zeros_like(arr)
            for line in range(nl_p):
                data2d = arr[:, :, line]
                fb_idx = np.full(nt_p, np.nan)
                for trace in range(nt_p):
                    tr = data2d[:, trace]
                    nstd = np.std(tr[:noise_end])
                    thr = k_val * (nstd + np.finfo(float).eps)
                    hits = np.flatnonzero(np.abs(tr[:search_end]) > thr)
                    if hits.size:
                        fb_idx[trace] = hits[0] + 1
                valid = fb_idx[~np.isnan(fb_idx)]
                if valid.size == 0:
                    out[:, :, line] = data2d
                    continue
                line_idx = int(round(np.median(valid)))
                sh = target_idx - line_idx
                tmp = np.roll(data2d, sh, axis=0)
                if sh > 0:
                    tmp[:sh, :] = 0
                elif sh < 0:
                    tmp[sh:, :] = 0
                out[:, :, line] = tmp

        elif op_type == "bg_mean":
            out = arr - np.mean(arr, axis=1, keepdims=True)

        elif op_type == "bg_median":
            if np.iscomplexobj(arr):
                med = np.median(arr.real, axis=1, keepdims=True) + 1j * np.median(
                    arr.imag, axis=1, keepdims=True
                )
            else:
                med = np.median(arr, axis=1, keepdims=True)
            out = arr - med

        elif op_type == "bg_move":
            win = int(round(params[0])) if params else 1
            out = arr - moving_mean_axis(arr, win, axis=1)

        elif op_type == "bg_svd":
            k_rank = int(round(params[0])) if params else 1
            for line in range(nl_p):
                u_val, s_val, vh_val = np.linalg.svd(arr[:, :, line], full_matrices=False)
                s_val[:k_rank] = 0
                out[:, :, line] = (u_val * s_val) @ vh_val

        elif op_type == "bg_rpca":
            lam = 1.0 / np.sqrt(max(ns_p, nt_p))
            for line in range(nl_p):
                low_rank = np.repeat(
                    np.median(arr[:, :, line].real, axis=1, keepdims=True), nt_p, axis=1
                )
                sparse = arr[:, :, line].real - low_rank
                recon = np.sign(sparse) * np.maximum(np.abs(sparse) - lam, 0.0)
                if np.iscomplexobj(arr):
                    recon = recon + 1j * arr[:, :, line].imag
                out[:, :, line] = recon

        elif op_type == "bg_top_mute":
            idx = int(round((params[0] if params else 0.0) / dt))
            if idx > 0:
                out[:idx, :, :] = 0

        elif op_type == "bg_highpass":
            removal = params[0] if len(params) >= 1 else 100.0
            start = params[1] if len(params) >= 2 else 2.0
            transition = params[2] if len(params) >= 3 else 4.0
            win = int(round(params[3])) if len(params) >= 4 else 100
            out = bg_remove_highpass(arr, dt, removal, start, transition, win)

        elif op_type == "bg_adaptive":
            removal = params[0] if len(params) >= 1 else 100.0
            start = params[1] if len(params) >= 2 else 2.0
            transition = params[2] if len(params) >= 3 else 4.0
            win = int(round(params[3])) if len(params) >= 4 else 100
            surface = params[4] if len(params) >= 5 else 20.0
            protect_start = params[5] if len(params) >= 6 else 0.0
            protect_end = params[6] if len(params) >= 7 else 30.0
            out, _ = bg_remove_adaptive_protect(
                arr,
                dt,
                removal,
                start,
                transition,
                win,
                surface,
                protect_start,
                protect_end,
            )

        elif op_type == "gain_lin":
            a0 = max(params[0] if len(params) >= 1 else 1.0, 0.0)
            slope = params[1] if len(params) >= 2 else 0.05
            t_ns = np.arange(ns_p, dtype=float) * dt
            gain = np.clip(a0 + slope * t_ns, 0, 500)
            out = arr * gain[:, None, None]

        elif op_type == "gain_exp":
            c_val = max(params[0] if len(params) >= 1 else 1.0, 0.0)
            pexp = params[1] if len(params) >= 2 else 1.0
            t_norm = np.linspace(0.0, 1.0, ns_p)
            gain = np.clip(c_val * np.exp(pexp * t_norm), None, 500)
            out = arr * gain[:, None, None]

        elif op_type == "gain_sph":
            vel = params[0] if params else 0.1
            t_ns = np.arange(ns_p, dtype=float) * dt
            gain = 1.0 + (vel * t_ns) * 20.0
            out = arr * gain[:, None, None]

        elif op_type == "gain_sec":
            db_gain = params[0] if params else 1.0
            t_ns = np.arange(ns_p, dtype=float) * dt
            gain = np.exp(db_gain * t_ns / 500.0) * (t_ns + 10.0) ** 1.2
            gain = gain / max(np.max(gain), np.finfo(float).eps) * 100.0
            out = arr * gain[:, None, None]

        elif op_type == "gain_agc":
            env = moving_mean_axis(np.abs(arr), int(round(params[0])), axis=0)
            out = arr / (env + np.max(env) * 0.01 + 1e-9) * 50.0

        elif op_type == "gain_tgc_agc":
            alpha = params[0] if params else 1.0
            gain = np.exp(alpha * np.linspace(0, 5, ns_p))
            tmp = arr * gain[:, None, None]
            env = moving_mean_axis(np.abs(tmp), 50, axis=0)
            out = tmp / (env + 1e-9) * 50.0

        elif op_type == "gain_bg_est":
            smooth = moving_mean_axis(
                np.mean(np.abs(arr), axis=(1, 2)),
                int(round(params[0])) if params else 1,
                axis=0,
            )
            gain = np.max(smooth) / (smooth + 1e-6)
            gain = np.clip(gain, None, 1000)
            out = arr * gain[:, None, None]

        elif op_type == "gain_ada_contrast":
            win_ns = params[0] if len(params) >= 1 else 10.0
            win_size = max(3, int(round(win_ns / max(dt, 1e-9))))
            strength = float(np.clip(params[1], 1, 10))
            for line in range(nl_p):
                data2d = arr[:, :, line]
                local_mean = moving_mean_axis(data2d, win_size, axis=0)
                local_std = np.maximum(moving_std_axis(data2d, win_size, axis=0), 1e-9)
                enhanced = (data2d - local_mean) * strength / local_std + local_mean
                max_val = np.max(np.abs(enhanced))
                if max_val > 1e6:
                    enhanced = enhanced / max_val * 1e6
                out[:, :, line] = enhanced

        elif op_type == "filt_vert":
            out = moving_mean_axis(arr, int(round(params[0])), axis=0)

        elif op_type == "filt_horz":
            out = moving_mean_axis(arr, int(round(params[0])), axis=1)

        elif op_type == "filt_bp_fft":
            f_lo = params[0] * 1e6
            f_hi = params[1] * 1e6
            freqs = np.arange(ns_p, dtype=float) * fs / ns_p
            mask = ((freqs >= f_lo) & (freqs <= f_hi)) | (
                (freqs >= (fs - f_hi)) & (freqs <= (fs - f_lo))
            )
            for line in range(nl_p):
                freq_domain = np.fft.fft(arr[:, :, line], axis=0)
                out[:, :, line] = np.fft.ifft(freq_domain * mask[:, None], axis=0).real

        elif op_type == "filt_decon":
            out = np.asarray(pred_decon(arr, tuple(params), "run"))

        elif op_type == "filt_med2":
            win = max(1, int(round(params[0])))
            for line in range(nl_p):
                out[:, :, line] = ndimage.median_filter(arr[:, :, line].real, size=(win, win), mode="nearest")

        elif op_type == "filt_mean2":
            win = max(1, int(round(params[0])))
            for line in range(nl_p):
                out[:, :, line] = ndimage.uniform_filter(arr[:, :, line], size=(win, win), mode="nearest")

        elif op_type == "filt_bp2d":
            r_lo = params[0] if len(params) >= 1 else 5.0
            r_hi = params[1] if len(params) >= 2 else 50.0
            for line in range(nl_p):
                dft = np.fft.fftshift(np.fft.fft2(arr[:, :, line]))
                ny, nx = dft.shape
                cx, cy = nx // 2, ny // 2
                grid_x, grid_y = np.meshgrid(np.arange(nx), np.arange(ny))
                radius = np.sqrt((grid_x - cx) ** 2 + (grid_y - cy) ** 2)
                mask = (radius >= r_lo) & (radius <= r_hi)
                out[:, :, line] = np.fft.ifft2(np.fft.ifftshift(dft * mask)).real

        elif op_type == "filt_fk":
            width = max(1, int(round(params[0]))) if len(params) >= 1 else 5
            start_time_ns = params[1] if len(params) >= 2 else 1.0
            start_sample = max(0, min(ns_p, int(round(start_time_ns / dt))))
            for line in range(nl_p):
                data2d = arr[:, :, line]
                upper = data2d[:start_sample, :] if start_sample > 0 else data2d[:0, :]
                lower = data2d[start_sample:, :]
                if lower.size:
                    dft = np.fft.fftshift(np.fft.fft2(lower))
                    nz, nx = dft.shape
                    center_x = nx // 2
                    mask = np.ones((nz, nx), dtype=np.float32)
                    mask[:, max(0, center_x - width) : min(nx, center_x + width + 1)] = 0.0
                    filtered = np.fft.ifft2(np.fft.ifftshift(dft * mask)).real
                else:
                    filtered = lower
                out[:, :, line] = np.vstack([upper, filtered])

        elif op_type == "filt_fk_shift":
            width = params[0] if len(params) >= 1 else 5.0
            shift = params[1] if len(params) >= 2 else 10.0
            out = fk_notch_shift(arr, width, shift)

        elif op_type == "filt_glp":
            mode = params[0] if len(params) >= 1 else 3.0
            strength = params[1] if len(params) >= 2 else 2.0
            out = gradual_lowpass_filter(arr, mode, strength)

        elif op_type == "filt_smooth_fk":
            strength = params[0] if params else 2.0
            out = smoothing_fk_filter(arr, strength)

        elif op_type == "mig_kirchhoff":
            vel = params[0] if len(params) >= 1 else 0.1
            dx = params[1] if len(params) >= 2 else 0.05
            rmax = params[2] if len(params) >= 3 else 0.75
            halfang = params[3] if len(params) >= 4 else 30.0
            for line in range(nl_p):
                out[:, :, line] = migration_kirchhoff_time(
                    arr[:, :, line],
                    dt,
                    dx,
                    vel,
                    rmax,
                    halfang,
                )

        elif op_type == "mig_stolt":
            vel = params[0] if len(params) >= 1 else 0.1
            dx = params[1] if len(params) >= 2 else 0.05
            overlap = params[2] if len(params) >= 3 else 32
            for line in range(nl_p):
                out[:, :, line] = migration_stolt(arr[:, :, line], dt, dx, vel, overlap)

        elif op_type == "filt_grad":
            out = np.zeros_like(arr)
            out[:, :-1, :] = np.diff(arr, axis=1)

        elif op_type == "filt_wiener":
            win = max(3, int(round(params[0]))) if params else 3
            kernel = (win, win, 1)
            if np.iscomplexobj(arr):
                out = signal.wiener(arr.real, kernel) + 1j * signal.wiener(arr.imag, kernel)
            else:
                out = signal.wiener(arr, kernel)

        elif op_type == "filt_notch":
            freq_mhz = params[0] if params else 50.0
            w0 = (freq_mhz * 1e6) / (fs / 2.0)
            w0 = float(np.clip(w0, 1e-6, 0.999999))
            # MATLAB iirnotch uses normalized bandwidth, while SciPy expects Q.
            # The MATLAB code sets bw = w0 / 35, so the equivalent SciPy Q is 35.
            q_factor = 35.0
            b_val, a_val = signal.iirnotch(w0, q_factor)
            for line in range(nl_p):
                data2d = arr[:, :, line]
                if np.iscomplexobj(data2d):
                    out[:, :, line] = signal.filtfilt(b_val, a_val, data2d.real, axis=0) + 1j * signal.filtfilt(
                        b_val,
                        a_val,
                        data2d.imag,
                        axis=0,
                    )
                else:
                    out[:, :, line] = signal.filtfilt(b_val, a_val, data2d, axis=0)

        elif op_type == "filt_adapt":
            for line in range(nl_p):
                img = arr[:, :, line].real.astype(float)
                mu = ndimage.uniform_filter(img, size=3, mode="nearest")
                sigma_local = np.sqrt(
                    np.maximum(ndimage.uniform_filter(img**2, size=3, mode="nearest") - mu**2, 0.0)
                )
                mask = 1.0 / (1.0 + sigma_local**2)
                out[:, :, line] = img * (1.0 - mask) + mu * mask

        elif op_type == "wav_swt1":
            for line in range(nl_p):
                values = arr[:, :, line].real.astype(float).copy()
                thr = self._minimaxi_threshold(values.ravel())
                for trace in range(nt_p):
                    coeffs = pywt.wavedec(values[:, trace], "sym4", level=3)
                    coeffs[1:] = [pywt.threshold(coeff, thr, mode="soft") for coeff in coeffs[1:]]
                    values[:, trace] = pywt.waverec(coeffs, "sym4")[:ns_p]
                out[:, :, line] = values

        elif op_type == "wav_dwt2":
            for line in range(nl_p):
                img = arr[:, :, line].real.astype(float)
                coeffs = pywt.wavedec2(img, "sym4", level=2)
                sigma = self._sigma_from_details(coeffs)
                thr = sigma * np.sqrt(2.0 * np.log(img.size))
                approx = coeffs[0]
                details = [
                    tuple(pywt.threshold(coeff, thr, mode="soft") for coeff in detail)
                    for detail in coeffs[1:]
                ]
                out[:, :, line] = pywt.waverec2([approx] + details, "sym4")[:ns_p, :nt_p]

        elif op_type == "cs_reconstruction":
            sampling_rate = float(np.clip(params[0], 5, 90)) if len(params) >= 1 else 10.0
            lambda_val = float(params[1]) if len(params) >= 2 else 0.1
            for line in range(nl_p):
                bscan = arr[:, :, line].real.astype(float)
                ns, nt = bscan.shape
                n_samples = round(ns * nt * sampling_rate / 100.0)
                total = ns * nt
                sample_idx = np.random.permutation(total)[:n_samples]
                mask = np.zeros(total, dtype=float)
                mask[sample_idx] = 1.0
                mask = mask.reshape(ns, nt)
                y_val = bscan * mask
                x_recon = np.zeros_like(bscan)
                for _ in range(1000):
                    residual = y_val - x_recon * mask
                    grad = -2.0 * (residual * mask)
                    x_recon = soft_threshold(x_recon - 0.01 * grad, lambda_val * 0.01)
                    if np.linalg.norm(residual) < 1e-6:
                        break
                x_norm = (x_recon - np.min(x_recon)) / (np.max(x_recon) - np.min(x_recon) + np.finfo(float).eps)
                orig_min = np.min(bscan)
                orig_max = np.max(bscan)
                out[:, :, line] = x_norm * (orig_max - orig_min) + orig_min

        return self._limit_output(op_type, out)

    @staticmethod
    def _ensure_3d(data: np.ndarray) -> np.ndarray:
        arr = np.asarray(data)
        if arr.ndim == 2:
            return arr[:, :, None]
        if arr.ndim != 3:
            raise ValueError("Expected a 2D or 3D GPR array.")
        return arr

    def _limit_output(self, op_type: str, data: np.ndarray) -> np.ndarray:
        if "gain" in op_type:
            std_val = np.std(data)
            if np.isfinite(std_val) and std_val > 1e-9:
                lim = std_val * self.context.clip_sigma
                return np.clip(data, -lim, lim)
        return np.clip(data, -1e15, 1e15)

    @staticmethod
    def _sigma_from_details(coeffs: list) -> float:
        detail = coeffs[-1][-1]
        return np.median(np.abs(detail)) / 0.6745 + np.finfo(float).eps

    @staticmethod
    def _minimaxi_threshold(values: np.ndarray) -> float:
        values = np.asarray(values, dtype=float).ravel()
        sigma = np.median(np.abs(values - np.median(values))) / 0.6745 + np.finfo(float).eps
        n_val = max(1, values.size)
        factor = 0.0 if n_val <= 32 else 0.3936 + 0.1829 * np.log2(n_val)
        return sigma * factor
