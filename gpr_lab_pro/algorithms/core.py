from __future__ import annotations

from typing import Tuple

import numpy as np
from scipy import ndimage
from scipy.interpolate import PchipInterpolator


def _ensure_array(x: np.ndarray) -> np.ndarray:
    return np.asarray(x)


def _median_complex(x: np.ndarray, axis: int, keepdims: bool = False) -> np.ndarray:
    if np.iscomplexobj(x):
        return np.median(x.real, axis=axis, keepdims=keepdims) + 1j * np.median(
            x.imag, axis=axis, keepdims=keepdims
        )
    return np.median(x, axis=axis, keepdims=keepdims)


def moving_mean_axis(x: np.ndarray, window: int, axis: int) -> np.ndarray:
    window = max(1, int(round(window)))
    if np.iscomplexobj(x):
        return ndimage.uniform_filter1d(
            x.real, size=window, axis=axis, mode="nearest"
        ) + 1j * ndimage.uniform_filter1d(
            x.imag, size=window, axis=axis, mode="nearest"
        )
    return ndimage.uniform_filter1d(x, size=window, axis=axis, mode="nearest")


def moving_median_axis(x: np.ndarray, window: int, axis: int) -> np.ndarray:
    window = max(1, int(round(window)))
    size = [1] * x.ndim
    size[axis] = window
    if np.iscomplexobj(x):
        return ndimage.median_filter(x.real, size=size, mode="nearest") + 1j * ndimage.median_filter(
            x.imag, size=size, mode="nearest"
        )
    return ndimage.median_filter(x, size=size, mode="nearest")


def moving_std_axis(x: np.ndarray, window: int, axis: int) -> np.ndarray:
    mean = moving_mean_axis(x, window, axis)
    mean_sq = moving_mean_axis(np.abs(x) ** 2, window, axis)
    var = np.maximum(mean_sq - np.abs(mean) ** 2, 0.0)
    return np.sqrt(var)


def remove_bg_logic(S_f: np.ndarray, method_idx: int) -> np.ndarray:
    S_f = _ensure_array(S_f)
    if S_f.ndim != 2:
        raise ValueError("S_f must be a 2D matrix.")
    if method_idx not in (1, 2):
        return S_f.copy()
    n_traces = S_f.shape[1]
    if method_idx == 1:
        return S_f - np.mean(S_f, axis=1, keepdims=True)

    half_win = 9 // 2
    bg = np.zeros_like(S_f)
    global_bg = np.mean(S_f, axis=1)
    for i in range(n_traces):
        idx1 = max(0, i - half_win)
        idx2 = min(n_traces, i + half_win + 1)
        ref_indices = [k for k in range(idx1, idx2) if k != i]
        if not ref_indices:
            bg[:, i] = global_bg
            continue
        local_block = S_f[:, ref_indices]
        bg[:, i] = _median_complex(local_block, axis=1)
    bg = moving_mean_axis(bg, 5, axis=1)
    return S_f - bg


def phase_correction_core(
    freq_data: np.ndarray, frequencies: np.ndarray, mask: np.ndarray | None = None
) -> np.ndarray:
    freq_data = _ensure_array(freq_data)
    frequencies = np.asarray(frequencies).reshape(-1)
    if frequencies.size != freq_data.shape[0]:
        raise ValueError("frequencies length must match n_freq.")
    if mask is None:
        mask = np.ones(freq_data.shape[0], dtype=bool)
    else:
        mask = np.asarray(mask, dtype=bool).reshape(-1)
    if np.count_nonzero(mask) < 3:
        return freq_data.copy()

    comp_data = freq_data.T.copy()
    n_traces, n_freq = comp_data.shape
    n_strong = max(3, round(n_traces * 0.1))
    prev_phi = np.zeros(n_freq)
    valid_pairs = mask[:-1] & mask[1:]
    dmask = np.diff(np.concatenate(([0], mask.view(np.int8), [0])))
    seg_starts = np.flatnonzero(dmask == 1)
    seg_ends = np.flatnonzero(dmask == -1) - 1

    for iteration in range(50):
        trace_energy = np.sum(np.abs(comp_data) ** 2, axis=1)
        n_keep = min(n_strong, n_traces)
        strong_idx = np.argpartition(trace_energy, -n_keep)[-n_keep:]
        strong = comp_data[strong_idx, :]
        grad = np.zeros(n_freq - 1)
        if np.any(valid_pairs):
            pair_sum = np.sum(strong[:, 1:] * np.conj(strong[:, :-1]), axis=0)
            grad[valid_pairs] = np.angle(pair_sum[valid_pairs])

        phi = np.zeros(n_freq)
        for s0, s1 in zip(seg_starts, seg_ends):
            if s1 > s0:
                phi[s0 : s1 + 1] = np.cumsum(np.concatenate(([0.0], grad[s0:s1])))

        f_sel = frequencies[mask]
        phi_sel = phi[mask]
        if f_sel.size >= 2 and np.max(f_sel) > np.min(f_sel):
            f_norm = (f_sel - np.mean(f_sel)) / (np.max(f_sel) - np.min(f_sel))
            poly = np.polyfit(f_norm, phi_sel, 1)
            phi[mask] = phi_sel - np.polyval(poly, f_norm)

        phi[mask] -= np.mean(phi[mask])
        comp = np.ones(n_freq, dtype=np.complex128)
        comp[mask] = np.exp(-1j * phi[mask])
        comp_data = comp_data * comp
        if iteration > 0 and np.linalg.norm(phi[mask] - prev_phi[mask]) < 1e-4:
            break
        prev_phi = phi
    return comp_data.T


def interference_suppression_active(
    C_in: np.ndarray, power_limit_db: float = 10
) -> Tuple[np.ndarray, np.ndarray]:
    C_in = _ensure_array(C_in)
    C_out = C_in.copy()
    n_freq, n_traces = C_in.shape
    pct_removed = np.zeros(n_traces, dtype=np.float32)
    if n_freq < 8 or n_traces == 0:
        return C_out, pct_removed
    power_limit_db = float(np.clip(power_limit_db, 0, 40))
    mag_db = 20 * np.log10(np.abs(C_in) + 1e-12)
    win = max(9, 2 * (min(101, n_freq) // 10) + 1)
    base = moving_median_axis(mag_db, win, axis=0)
    is_out = (mag_db - base) > power_limit_db
    x = np.arange(n_freq, dtype=float)

    for j in range(n_traces):
        bad = np.flatnonzero(is_out[:, j])
        if bad.size == 0:
            continue
        pct_removed[j] = np.float32(bad.size / n_freq * 100.0)
        good = np.flatnonzero(~is_out[:, j])
        if good.size < 2:
            continue
        r_interp = PchipInterpolator(x[good], C_out[good, j].real, extrapolate=True)
        i_interp = PchipInterpolator(x[good], C_out[good, j].imag, extrapolate=True)
        C_out[bad, j] = r_interp(x[bad]) + 1j * i_interp(x[bad])
    return C_out, pct_removed


def bg_remove_highpass(
    V_in: np.ndarray,
    dt_ns: float,
    removal_pct: float,
    start_ns: float,
    transition_ns: float,
    filter_len_traces: float,
) -> np.ndarray:
    V_in = _ensure_array(V_in)
    V_out = V_in.copy()
    ns_p = V_in.shape[0]
    removal_pct = float(np.clip(removal_pct, 0, 100))
    filter_len_traces = max(1, int(round(filter_len_traces)))
    t_ns = np.arange(ns_p, dtype=float) * dt_ns
    if transition_ns <= 0:
        w = (t_ns >= start_ns).astype(np.float32) * np.float32(removal_pct / 100.0)
    else:
        w = np.clip((t_ns - start_ns) / transition_ns, 0.0, 1.0).astype(np.float32)
        w *= np.float32(removal_pct / 100.0)
    for line in range(V_in.shape[2]):
        bg = moving_mean_axis(V_in[:, :, line], filter_len_traces, axis=1)
        V_out[:, :, line] = V_in[:, :, line] - bg * w[:, None]
    return V_out


def bg_remove_adaptive_protect(
    V_in: np.ndarray,
    dt_ns: float,
    removal_pct: float,
    start_ns: float,
    transition_ns: float,
    filter_len_traces: float,
    surface_search_ns: float,
    protect_start_ns: float,
    protect_end_ns: float,
) -> Tuple[np.ndarray, np.ndarray]:
    V_in = _ensure_array(V_in)
    V_out = V_in.copy()
    ns_p, nt_p, nl_p = V_in.shape
    surf_idx = np.ones(nt_p, dtype=float)
    removal_pct = float(np.clip(removal_pct, 0, 100))
    filter_len_traces = max(1, int(round(filter_len_traces)))
    search_idx = max(1, min(ns_p, int(round(surface_search_ns / dt_ns)) + 1))
    t_ns = np.arange(ns_p, dtype=float) * dt_ns
    protect_active = protect_end_ns > protect_start_ns >= 0
    protect_weight = 0.2 if protect_active else 1.0

    for line in range(nl_p):
        img = np.abs(V_in[:, :, line])
        surf_idx_l = np.ones(nt_p, dtype=float)
        for trace in range(nt_p):
            seg = img[:search_idx, trace].astype(float)
            medv = np.median(seg)
            madv = np.median(np.abs(seg - medv)) + np.finfo(float).eps
            thr = medv + 8.0 * madv
            hits = np.flatnonzero(seg > thr)
            if hits.size:
                surf_idx_l[trace] = float(hits[0] + 1)
        surf_idx = surf_idx_l
        t_surf = (surf_idx_l - 1.0) * dt_ns
        t_rel = t_ns[:, None] - t_surf[None, :]

        if transition_ns <= 0:
            w_base = (t_rel >= start_ns).astype(np.float32)
        else:
            w_base = np.clip((t_rel - start_ns) / transition_ns, 0.0, 1.0).astype(
                np.float32
            )
        if protect_active:
            protect_factor = np.ones_like(t_ns)
            left_start = max(0.0, protect_start_ns - 2.0)
            left_mask = (t_ns >= left_start) & (t_ns < protect_start_ns)
            if np.any(left_mask):
                protect_factor[left_mask] = 1.0 - (1.0 - protect_weight) * (
                    (t_ns[left_mask] - left_start) / 2.0
                )
            core_mask = (t_ns >= protect_start_ns) & (t_ns <= protect_end_ns)
            protect_factor[core_mask] = protect_weight
            right_mask = (t_ns > protect_end_ns) & (t_ns <= protect_end_ns + 2.0)
            if np.any(right_mask):
                protect_factor[right_mask] = protect_weight + (1.0 - protect_weight) * (
                    (t_ns[right_mask] - protect_end_ns) / 2.0
                )
            w_base = w_base * protect_factor[:, None]
        w = w_base * np.float32(removal_pct / 100.0)
        bg = moving_mean_axis(V_in[:, :, line], filter_len_traces, axis=1)
        V_out[:, :, line] = V_in[:, :, line] - bg * w
    return V_out, surf_idx


def fk_notch_shift(V_in: np.ndarray, width: float, shift: float = 0.0) -> np.ndarray:
    V_in = _ensure_array(V_in)
    V_out = V_in.copy()
    width = max(1, int(round(width)))
    shift = int(round(shift))
    for line in range(V_in.shape[2]):
        D = np.fft.fftshift(np.fft.fft2(V_in[:, :, line]))
        nz, nx = D.shape
        cx = nx // 2
        cxs = int(np.clip(cx + shift, 0, nx - 1))
        mask = np.ones((nz, nx), dtype=np.float32)
        mask[:, max(0, cxs - width) : min(nx, cxs + width + 1)] = 0.0
        result = np.fft.ifft2(np.fft.ifftshift(D * mask))
        V_out[:, :, line] = result.real if np.isrealobj(V_in) else result
    return V_out


def smoothing_fk_filter(V_in: np.ndarray, strength: float = 2.0) -> np.ndarray:
    V_in = _ensure_array(V_in)
    V_out = V_in.copy()
    strength = int(np.clip(round(strength), 1, 4))
    k0 = [0.9, 0.7, 0.5, 0.35][strength - 1]
    for line in range(V_in.shape[2]):
        D = np.fft.fftshift(np.fft.fft2(V_in[:, :, line]))
        nz, nx = D.shape
        cx = nx // 2
        k = np.abs(np.arange(nx) - cx) / max(1.0, nx / 2.0)
        mk = 1.0 / (1.0 + (k / k0) ** 4)
        result = np.fft.ifft2(np.fft.ifftshift(D * mk[None, :]))
        V_out[:, :, line] = result.real if np.isrealobj(V_in) else result
    return V_out


def migration_kirchhoff_time(
    V_in: np.ndarray,
    dt_ns: float = 0.2,
    dx_m: float = 0.05,
    vel_m_per_ns: float = 0.1,
    max_radius_m: float = 0.75,
    half_angle_deg: float = 30.0,
) -> np.ndarray:
    V_in = _ensure_array(V_in)
    ns, nt = V_in.shape
    if ns < 8 or nt < 4:
        return V_in.copy()
    half_angle_rad = np.deg2rad(half_angle_deg)
    max_radius_tr = max(1, int(round(max_radius_m / dx_m)))
    t0 = np.arange(ns, dtype=float) * dt_ns
    z0 = (vel_m_per_ns * t0) / 2.0
    tan_a = np.tan(half_angle_rad)
    traces = np.arange(nt)
    V_out = np.zeros_like(V_in, dtype=np.result_type(V_in, np.complex64))

    for ix0 in range(nt):
        ix1 = max(0, ix0 - max_radius_tr)
        ix2 = min(nt, ix0 + max_radius_tr + 1)
        offs = (traces[ix1:ix2] - ix0) * dx_m
        if offs.size == 0:
            continue
        dx_abs = np.abs(offs)[None, :]
        dt_ap = (2.0 * dx_abs) / vel_m_per_ns
        t_in = np.sqrt(t0[:, None] ** 2 + dt_ap**2)
        mask = dx_abs <= (z0[:, None] * tan_a + 1e-12)
        if not np.any(mask):
            continue
        tr_mat = V_in[:, ix1:ix2]
        idx = t_in / dt_ns
        i0 = np.floor(idx).astype(int)
        frac = idx - i0
        valid = (i0 >= 0) & (i0 < ns - 1)
        i0 = np.clip(i0, 0, ns - 2)
        cols = np.broadcast_to(np.arange(offs.size)[None, :], i0.shape)
        v0 = tr_mat[i0, cols]
        v1 = tr_mat[i0 + 1, cols]
        y = (1.0 - frac) * v0 + frac * v1
        y[~valid] = 0
        y[~mask] = 0
        V_out[:, ix0] = np.sum(y, axis=1) / max(1, offs.size)
    return V_out.real.astype(V_in.dtype, copy=False) if np.isrealobj(V_in) else V_out


def migration_stolt(
    V_in: np.ndarray,
    dt_ns: float = 0.2,
    dx_m: float = 0.05,
    vel_m_per_ns: float = 0.1,
    x_overlap_traces: float = 32,
) -> np.ndarray:
    V_in = _ensure_array(V_in)
    ns, nt = V_in.shape
    if ns < 8 or nt < 8:
        return V_in.copy()
    x_overlap_traces = max(0, int(round(x_overlap_traces)))
    block_len = 1024
    if nt <= block_len:
        return stolt_block(V_in, dt_ns, dx_m, vel_m_per_ns)

    hop = max(1, block_len - 2 * x_overlap_traces)
    starts = list(range(0, nt, hop))
    if starts[-1] + block_len < nt:
        starts.append(nt - block_len)
    starts = sorted(set(starts))
    V_acc = np.zeros((ns, nt), dtype=np.result_type(V_in, np.complex64))
    W_acc = np.zeros(nt, dtype=np.float32)

    for s in starts:
        e = min(nt, s + block_len)
        block = V_in[:, s:e]
        if block.shape[1] < block_len:
            pad = np.zeros((ns, block_len - block.shape[1]), dtype=block.dtype)
            block = np.concatenate([block, pad], axis=1)
        block_m = stolt_block(block, dt_ns, dx_m, vel_m_per_ns)
        weights = np.ones(block_len, dtype=np.float32)
        if x_overlap_traces > 0:
            ramp = np.linspace(0.0, 1.0, x_overlap_traces, dtype=np.float32)
            weights[:x_overlap_traces] = ramp
            weights[-x_overlap_traces:] = ramp[::-1]
        idx = np.arange(s, min(s + block_len, nt))
        w_use = weights[: idx.size]
        V_acc[:, idx] += block_m[:, : idx.size] * w_use[None, :]
        W_acc[idx] += w_use

    W_acc[W_acc < 1e-6] = 1.0
    V_out = V_acc / W_acc[None, :]
    return V_out.real.astype(V_in.dtype, copy=False) if np.isrealobj(V_in) else V_out


def stolt_block(
    V: np.ndarray, dt_ns: float, dx_m: float, vel_m_per_ns: float
) -> np.ndarray:
    ns, nx = V.shape
    dt_s = dt_ns * 1e-9
    vel_m_s = vel_m_per_ns * 1e9
    nf = 1 << int(np.ceil(np.log2(ns)))
    nk = 1 << int(np.ceil(np.log2(nx)))
    D = np.fft.fftshift(np.fft.fft2(V, s=(nf, nk)))
    w = np.fft.fftshift(2 * np.pi * np.fft.fftfreq(nf, d=dt_s))
    kx = np.fft.fftshift(2 * np.pi * np.fft.fftfreq(nk, d=dx_m))
    kz = np.abs(w) / max(vel_m_s, np.finfo(float).eps)
    G = np.zeros_like(D)
    for ix, kx_i in enumerate(kx):
        w_in = np.sign(w) * (vel_m_s * np.sqrt(kx_i**2 + kz**2))
        src = D[:, ix]
        src_i = np.interp(w_in, w, src.real, left=0.0, right=0.0) + 1j * np.interp(
            w_in, w, src.imag, left=0.0, right=0.0
        )
        scale = np.sqrt(np.abs(w_in) / np.maximum(np.abs(w), 1e-12))
        G[:, ix] = src_i * scale
    V_m = np.fft.ifft2(np.fft.ifftshift(G))[:ns, :nx]
    return V_m.real.astype(V.dtype, copy=False) if np.isrealobj(V) else V_m


def gradual_lowpass_filter(
    V_in: np.ndarray, mode_vh: float = 3, strength: float = 2
) -> np.ndarray:
    V_in = _ensure_array(V_in)
    V_out = V_in.copy()
    mode_vh = int(mode_vh) if int(mode_vh) in (1, 2, 3) else 3
    strength = int(np.clip(round(strength), 1, 4))
    do_v = mode_vh in (1, 3)
    do_h = mode_vh in (2, 3)
    v_large = [7, 11, 17, 25][strength - 1]
    h_large = [7, 11, 17, 25][strength - 1]
    ns_p = V_in.shape[0]
    wt = (np.arange(ns_p, dtype=np.float32) / max(1, ns_p - 1)) ** 2
    for line in range(V_in.shape[2]):
        data = V_in[:, :, line]
        if do_v:
            vs = moving_mean_axis(data, 3, axis=0)
            vl = moving_mean_axis(data, v_large, axis=0)
            data = vs * (1.0 - wt[:, None]) + vl * wt[:, None]
        if do_h:
            hs = moving_mean_axis(data, 3, axis=1)
            hl = moving_mean_axis(data, h_large, axis=1)
            data = hs * (1.0 - wt[:, None]) + hl * wt[:, None]
        V_out[:, :, line] = data
    return V_out


def interference_suppression_freq(
    S_f: np.ndarray,
    thr_db: float = 10,
    win_bins: float = 31,
    expand_bins: float = 2,
    min_hit_ratio: float = 0.15,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    S_f = _ensure_array(S_f)
    n_freq, n_traces = S_f.shape
    S_clean = S_f.copy()
    win_bins = max(5, int(round(win_bins)))
    expand_bins = max(0, int(round(expand_bins)))
    min_hit_ratio = float(np.clip(min_hit_ratio, 0, 1))
    mag = np.abs(S_f) + np.finfo(float).eps
    p_db = 20.0 * np.log10(mag)
    base_local = moving_median_axis(p_db, win_bins, axis=0)
    residual_db = p_db - base_local
    hit_ratio_vec = np.mean(residual_db > thr_db, axis=1)
    bad_mask = hit_ratio_vec >= min_hit_ratio
    robust_spec_db = 20.0 * np.log10(np.median(mag, axis=1) + np.finfo(float).eps)
    robust_base_db = moving_median_axis(robust_spec_db[:, None], win_bins, axis=0).ravel()
    bad_mask |= (robust_spec_db - robust_base_db) > thr_db
    bad_mask = expand_binary_mask_1d(bad_mask, expand_bins)
    idx_all = np.arange(n_freq, dtype=float)
    good_mask = ~bad_mask
    if np.count_nonzero(good_mask) >= 2:
        good_idx = idx_all[good_mask]
        for trace in range(n_traces):
            xr = S_f[:, trace].real.copy()
            xi = S_f[:, trace].imag.copy()
            xr[bad_mask] = PchipInterpolator(good_idx, xr[good_mask], extrapolate=True)(
                idx_all[bad_mask]
            )
            xi[bad_mask] = PchipInterpolator(good_idx, xi[good_mask], extrapolate=True)(
                idx_all[bad_mask]
            )
            S_clean[:, trace] = xr + 1j * xi
    return S_clean, bad_mask, {
        "hit_ratio_vec": hit_ratio_vec,
        "robust_spec_db": robust_spec_db,
        "robust_base_db": robust_base_db,
        "bad_ratio": float(np.count_nonzero(bad_mask) / max(1, n_freq)),
    }


def expand_binary_mask_1d(mask_in: np.ndarray, expand_bins: int) -> np.ndarray:
    mask_in = np.asarray(mask_in, dtype=bool).reshape(-1)
    if expand_bins <= 0:
        return mask_in
    kernel = np.ones(2 * expand_bins + 1, dtype=int)
    return np.convolve(mask_in.astype(int), kernel, mode="same") > 0


def soft_threshold(x: np.ndarray, threshold: float) -> np.ndarray:
    x = _ensure_array(x)
    return np.sign(x) * np.maximum(np.abs(x) - threshold, 0.0)
