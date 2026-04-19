from __future__ import annotations

import math
from typing import Tuple

import numpy as np
import pywt
from scipy import ndimage, signal
from scipy.linalg import toeplitz

from .core import soft_threshold


def kw_czt(x: np.ndarray, beta: float, t0: float, t1: float, fs: float) -> np.ndarray:
    x = np.asarray(x)
    if fs <= 0:
        raise ValueError("fs must be positive.")
    n = x.shape[0]
    window = np.kaiser(n, beta)
    x1 = x * window
    ts = 1.0 / fs
    w = np.exp(1j * 2.0 * np.pi * (t1 - t0) / n / ts)
    a = np.exp(1j * 2.0 * np.pi * (-t0) / ts)
    return signal.czt(x1, m=n, w=w, a=a)


def isdft_soft_phys(
    X: np.ndarray,
    f_start: float,
    f_end: float,
    t: np.ndarray,
    alpha: float,
    th_db: float,
    smooth_len: int,
    t_ground: float = 0.0,
    ramp_ns: float = 2.0,
) -> np.ndarray:
    X = np.asarray(X)
    t = np.asarray(t).reshape(-1)
    n, _ = X.shape
    x = np.zeros_like(X, dtype=np.complex128)
    df = (f_end - f_start) / max(1, n - 1)
    fk = f_start + np.arange(n) * df
    dt = t[1] - t[0]
    n_g = int(np.clip(round((t_ground - t[0]) / dt) + 1, 1, n)) - 1
    ramp_samp = max(1, int(round((ramp_ns * 1e-9) / dt)))
    th = 10 ** (th_db / 20.0)
    c = (n / alpha) * math.log(1 / th)
    Ln = np.full(n, n - 1, dtype=float)

    for ii in range(n_g + 1, n):
        n_rel = ii - n_g
        Ln[ii] = c / n_rel

    Ln = np.clip(Ln, 0, n - 1)
    if smooth_len > 1:
        if smooth_len % 2 == 0:
            smooth_len += 1
        Ln = signal.medfilt(Ln, kernel_size=smooth_len)
        Ln = ndimage.uniform_filter1d(Ln, size=smooth_len, mode="nearest")

    for i in range(1, n):
        if Ln[i] > Ln[i - 1]:
            Ln[i] = Ln[i - 1]

    k = np.arange(n, dtype=float)
    base_tw = max(16, round(0.08 * n))
    for idx in range(n):
        if idx <= n_g:
            gate = 0.0
        else:
            gate = min(1.0, (idx - n_g) / ramp_samp)
        if gate == 0:
            m = np.ones(n, dtype=float)
        else:
            L = Ln[idx]
            tw = base_tw + round(0.02 * L)
            m0 = 1.0 / (1.0 + np.exp((k - L) / tw))
            wexp = np.exp(-(alpha * (idx - n_g) / max(1, (n - n_g))) * (k / max(1, n - 1)))
            m = (1.0 - gate) * 1.0 + gate * (m0 * wexp)
        phase = np.exp(1j * 2.0 * np.pi * fk * t[idx])
        x[idx, :] = (phase * m) @ X / n
    return x.astype(X.dtype, copy=False)


def correct_direct_wave(
    C_freq: np.ndarray, freq_axis: np.ndarray
) -> Tuple[np.ndarray, np.float32]:
    C_freq = np.asarray(C_freq, dtype=np.complex64)
    freq_axis = np.asarray(freq_axis, dtype=np.float32).reshape(-1)
    n_freq, n_traces = C_freq.shape
    phase_raw = np.angle(C_freq)
    phase_unwrap = np.unwrap(phase_raw, axis=0)
    y_var = np.var(phase_unwrap, axis=0)
    tau_direct_all = np.zeros(n_traces, dtype=np.float32)
    is_direct_all = np.zeros(n_traces, dtype=bool)
    valid_y_idx = np.flatnonzero((y_var > 1e-6) & np.isfinite(y_var))

    if valid_y_idx.size:
        phase_valid = phase_unwrap[:, valid_y_idx]
        C_freq_valid = C_freq[:, valid_y_idx]
        y_var_valid = y_var[valid_y_idx]
        amp_freq = np.abs(C_freq_valid)
        amp_max = np.max(amp_freq, axis=0)
        amp_norm = amp_freq / (amp_max + np.finfo(float).eps)
        amp_std = np.std(amp_norm, axis=0)
        amp_sorted = np.sort(amp_norm, axis=0)[::-1]
        top20_len = max(1, round(n_freq * 0.2))
        top20_ratio = np.sum(amp_sorted[:top20_len, :], axis=0) / np.sum(amp_norm, axis=0)
        freq_scaled, mu, sigma = _center_scale(freq_axis.astype(float))
        p_matrix, res_matrix = _fit_linear_phase_batch(phase_valid, freq_scaled, sigma, mu)
        tau_direct_batch = np.abs(p_matrix[0, :]) / (2 * np.pi)
        phase_linearity_batch = 1.0 - res_matrix / y_var_valid
        is_direct_batch = (
            (phase_linearity_batch > 0.5)
            & (amp_std < 0.3)
            & (tau_direct_batch < 100e-9)
            & (top20_ratio > 0.2)
        )
        tau_direct_all[valid_y_idx] = tau_direct_batch.astype(np.float32)
        is_direct_all[valid_y_idx] = is_direct_batch

    valid_idx = np.flatnonzero(is_direct_all)
    tau_base = np.float32(0.0)
    if valid_idx.size == 0:
        top_idx = valid_y_idx[: min(10, valid_y_idx.size)]
        best_linearity = 0.0
        best_tau = 0.0
        freq_scaled, mu, sigma = _center_scale(freq_axis.astype(float))
        if top_idx.size:
            p_matrix, res_matrix = _fit_linear_phase_batch(phase_unwrap[:, top_idx], freq_scaled, sigma, mu)
            linearity_vec = 1.0 - res_matrix / np.maximum(y_var[top_idx], np.finfo(float).eps)
            best_pos = int(np.argmax(linearity_vec))
            best_linearity = float(linearity_vec[best_pos])
            best_tau = abs(float(p_matrix[0, best_pos])) / (2 * np.pi)
        tau_base = np.float32(abs(best_tau))
    else:
        tau_valid = tau_direct_all[valid_idx]
        tau_mean = np.mean(tau_valid)
        tau_std = np.std(tau_valid)
        tau_filtered = tau_valid[np.abs(tau_valid - tau_mean) < 3 * tau_std]
        if tau_filtered.size == 0:
            tau_filtered = tau_valid
        block_size = 20
        tau_local = np.zeros(n_traces, dtype=np.float32)
        for start in range(0, n_traces, block_size):
            end = min(start + block_size, n_traces)
            block_idx = np.intersect1d(np.arange(start, end), valid_idx)
            tau_local[start:end] = (
                np.median(tau_direct_all[block_idx]) if block_idx.size else tau_mean
            )
        tau_local = ndimage.gaussian_filter1d(tau_local, sigma=7 / 6.0, mode="nearest")
        tau_base = np.float32(abs(np.median(tau_local[~np.isnan(tau_local)])))
        tau_direct_all = tau_local

    phase_comp_absolute = np.exp(1j * 2.0 * np.pi * freq_axis[:, None] * tau_base)
    phase_comp_relative = np.exp(
        1j * 2.0 * np.pi * freq_axis[:, None] * (tau_direct_all - tau_base)[None, :]
    )
    corrected = C_freq * phase_comp_absolute * phase_comp_relative
    return corrected.astype(np.complex64, copy=False), tau_base


def pred_decon(
    in_data: np.ndarray, params: Tuple[float, ...], mode: str = "run"
) -> np.ndarray | dict:
    if mode == "info":
        return {
            "name": "预测误差反褶积",
            "params": [
                "算子长度 (点数)",
                "预测步长 (点数)",
                "窗口起点 (点数, 0=后半窗)",
                "窗口终点 (点数, 0=末端)",
            ],
            "defaults": [20, 5, 0, 0],
        }

    data = np.asarray(in_data)
    n_len = int(round(params[0]))
    alph_len = int(round(params[1]))
    win_s = int(round(params[2]))
    win_e = int(round(params[3]))
    ns, _, nl = data.shape
    out = data.copy()
    if win_s <= 0:
        win_s = ns // 2 + 1
    if win_e <= 0 or win_e > ns:
        win_e = ns
    win_s = max(1, min(ns, win_s))
    win_e = max(1, min(ns, win_e))
    if win_e <= win_s:
        return out

    win_len = win_e - win_s + 1
    taper_n = min(50, max(10, round(0.05 * win_len)))
    if win_len < 30:
        taper_n = round(0.15 * win_len)
    taper_n = max(0, min(taper_n, win_len - 1))

    for line in range(nl):
        out[:, :, line] = _run_deconvolution_window(
            data[:, :, line], n_len, alph_len, win_s, win_e, taper_n
        )
    return out


def gpr_cs_wavelet_tv(
    img_in: np.ndarray, lambda_: float = 0.02, niter: int = 50
) -> np.ndarray:
    x = np.asarray(np.real(img_in), dtype=float).copy()
    y = x.copy()
    tau = 0.6
    for iteration in range(max(1, int(niter))):
        x = x - tau * (x - y)
        x = _wavelet2_adaptive_enhance_sub(x, lambda_, "sym4", 3)
        x = _tv_denoise_depth_weighted_sub(x, lambda_, 1)
        if (iteration + 1) % 5 == 0:
            x = ndimage.gaussian_filter(x, sigma=0.2)
    return x


def _center_scale(x: np.ndarray) -> Tuple[np.ndarray, float, float]:
    mu = float(np.mean(x))
    sigma = float(np.std(x))
    if sigma == 0:
        sigma = 1.0
    return (x - mu) / sigma, mu, sigma


def _fit_linear_phase_batch(
    phase_matrix: np.ndarray,
    freq_scaled: np.ndarray,
    sigma: float,
    mu: float,
) -> Tuple[np.ndarray, np.ndarray]:
    y = np.asarray(phase_matrix, dtype=np.float64)
    x = np.asarray(freq_scaled, dtype=np.float64).reshape(-1, 1)
    if y.ndim == 1:
        y = y[:, None]
    x_mean = float(np.mean(x))
    y_mean = np.mean(y, axis=0, keepdims=True)
    x_centered = x - x_mean
    denom = float(np.sum(x_centered * x_centered))
    if denom <= np.finfo(float).eps:
        slopes_scaled = np.zeros(y.shape[1], dtype=np.float64)
    else:
        slopes_scaled = np.sum(x_centered * (y - y_mean), axis=0) / denom
    intercepts_scaled = y_mean.ravel() - slopes_scaled * x_mean
    fit = x * slopes_scaled[None, :] + intercepts_scaled[None, :]
    residual = np.sum((y - fit) ** 2, axis=0) / max(1, y.shape[0])
    p_matrix = np.vstack(
        [
            slopes_scaled / sigma,
            intercepts_scaled - slopes_scaled * mu / sigma,
        ]
    ).astype(np.float32, copy=False)
    return p_matrix, residual.astype(np.float32, copy=False)


def _run_deconvolution_window(
    s: np.ndarray, n: int, alph: int, win_s: int, win_e: int, taper_n: int
) -> np.ndarray:
    out = np.array(s, copy=True)
    win_len = win_e - win_s + 1
    if n >= win_len / 2 or alph >= n or n < 1 or alph < 1:
        return out
    w_in = np.arange(taper_n, dtype=float) / taper_n if taper_n > 0 else np.array([])
    start = win_s - 1
    stop = win_e
    for i in range(s.shape[1]):
        trace = s[:, i]
        seg = trace[start:stop]
        cc = signal.correlate(seg, seg, mode="full")
        mid = win_len - 1
        R1 = toeplitz(cc[mid : mid + n])
        L_vec = cc[mid + alph : mid + alph + n]
        try:
            c = np.linalg.solve(R1, L_vec)
        except np.linalg.LinAlgError:
            continue
        co = np.convolve(c, seg)
        cod = np.concatenate([np.zeros(alph, dtype=co.dtype), co])
        pred = cod[:win_len]
        seg_out = seg - pred
        if taper_n > 0:
            idx = np.arange(start, min(start + taper_n, stop))
            k = idx.size
            a = w_in[:k]
            out[idx, i] = (1.0 - a) * trace[idx] + a * seg_out[:k]
            if start + taper_n < stop:
                out[start + taper_n : stop, i] = seg_out[taper_n:]
        else:
            out[start:stop, i] = seg_out
    return out


def _wavelet2_adaptive_enhance_sub(
    img_in: np.ndarray, lambda_: float, wname: str, level: int
) -> np.ndarray:
    try:
        coeffs = pywt.wavedec2(img_in, wavelet=wname, level=level)
        approx = coeffs[0]
        details = coeffs[1:]
        flat_hf = np.concatenate([np.ravel(c) for detail in details for c in detail])
        if flat_hf.size == 0:
            return img_in
        sigma_hat = np.median(np.abs(flat_hf)) / 0.6745 + np.finfo(float).eps
        thr = lambda_ * sigma_hat * np.sqrt(2.0 * np.log(flat_hf.size))
        new_details = []
        for cH, cV, cD in details:
            new_details.append(
                (
                    pywt.threshold(cH, thr, mode="soft"),
                    pywt.threshold(cV, thr, mode="soft"),
                    pywt.threshold(cD, thr, mode="soft"),
                )
            )
        img_out = pywt.waverec2([approx] + new_details, wavelet=wname)
        return ndimage.gaussian_filter(img_out, sigma=0.2)
    except Exception:
        return img_in


def _tv_denoise_depth_weighted_sub(
    f: np.ndarray, lambda_: float, niter: int
) -> np.ndarray:
    u = np.array(f, copy=True)
    nr, nc = f.shape
    z = np.linspace(0.0, 1.0, nr)
    wz = np.ones(nr)
    wz[z < 0.35] = 0.85
    wz[(z >= 0.35) & (z < 0.7)] = 1.0
    wz[z >= 0.7] = 1.15
    W = np.repeat(wz[:, None], nc, axis=1)
    for _ in range(max(1, niter)):
        ux = np.zeros((nr, nc))
        uy = np.zeros((nr, nc))
        ux[:, :-1] = np.diff(u, axis=1)
        uy[:-1, :] = np.diff(u, axis=0)
        normu = np.sqrt(ux**2 + uy**2 + 1e-6)
        px = W * (ux / normu)
        py = W * (uy / normu)
        div_p = np.zeros((nr, nc))
        div_p[:, 0] = px[:, 0]
        div_p[:, 1:-1] = px[:, 1:-1] - px[:, :-2]
        div_p[:, -1] = -px[:, -2]
        div_p[0, :] += py[0, :]
        div_p[1:-1, :] += py[1:-1, :] - py[:-2, :]
        div_p[-1, :] -= py[-2, :]
        u = u + 0.02 * (div_p - lambda_ * (u - f))
    u_s = ndimage.gaussian_filter(u, sigma=0.25)
    alpha = np.zeros(nr)
    alpha[z >= 0.75] = 0.25
    return (1.0 - alpha[:, None]) * u + alpha[:, None] * u_s
