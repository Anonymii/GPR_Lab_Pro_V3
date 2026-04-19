from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
from typing import List, Sequence

import numpy as np
from scipy import interpolate, signal

from gpr_lab_pro.algorithms import correct_direct_wave, isdft_soft_phys
from gpr_lab_pro.infrastructure.workers import WorkerCancelled
from gpr_lab_pro.io.dat_loader import DatFileHeader, read_dat_header


ATTRIBUTES = {
    1: "Complex",
    2: "Real",
    3: "Envelope",
    4: "FirstDerivative",
    5: "SecondDerivative",
    6: "InstantPhase",
    7: "CosPhase",
    8: "InstantFrequency",
}


@dataclass(frozen=True)
class ISDFTParameters:
    alpha: float = 0.02
    th_db: float = -3.0
    ramp_ns: float = 3.0
    smooth_len: int = 9


@dataclass(frozen=True)
class DataImportParameters:
    beta: float = 6.0
    tw_start_ns: float = 0.0
    tw_end_ns: float = 60.0
    selected_attr_idx: int = 1
    tf_method: int = 1
    zero_correct: bool = True
    f_start_hz: int | None = None
    f_end_hz: int | None = None
    sample_count: int | None = None
    isdft: ISDFTParameters = ISDFTParameters()
    chunk_size: int = 10000


@dataclass
class ImportedGPRData:
    channels: List[np.ndarray]
    tw_ns: float
    filename: str
    header: DatFileHeader
    dt_ns: float
    fs_hz: float
    attribute: str
    transform_name: str

    def as_3d(self) -> np.ndarray:
        if not self.channels:
            return np.empty((0, 0, 0), dtype=np.float32)
        ns, nt = self.channels[0].shape
        is_complex = any(np.iscomplexobj(ch) for ch in self.channels)
        out = np.zeros((ns, nt, len(self.channels)), dtype=np.complex64 if is_complex else np.float32)
        for idx, channel in enumerate(self.channels):
            out[:, :, idx] = channel
        return out


class GPRDataImporter:
    """Python port of DAT parsing with deferred transform execution."""

    def import_file(
        self,
        path: str | Path,
        params: DataImportParameters,
        progress_callback=None,
        cancel_callback=None,
    ) -> ImportedGPRData:
        self._check_cancelled(cancel_callback)
        path = Path(path)
        self._report_progress(progress_callback, 0, "开始读取 DAT 文件")
        header = read_dat_header(path)
        self._check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 5, "已读取文件头，正在扫描数据帧")
        parsed_channels = self._read_frames(path, header, progress_callback=progress_callback, cancel_callback=cancel_callback)
        self._check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 92, "正在整理原始频域数据")
        frequency_channels = self._to_frequency_channels(parsed_channels)
        if frequency_channels:
            min_nt = min(ch.shape[1] for ch in frequency_channels if ch.size)
            frequency_channels = [ch[:, :min_nt] if ch.size else ch for ch in frequency_channels]
        tw_ns = params.tw_end_ns - params.tw_start_ns
        dt_ns = tw_ns / max(1, (params.sample_count or header.sample_count) - 1)
        fs_hz = 1.0 / (dt_ns * 1e-9)
        self._report_progress(progress_callback, 100, "导入完成")
        return ImportedGPRData(
            channels=frequency_channels,
            tw_ns=tw_ns,
            filename=path.name,
            header=header,
            dt_ns=dt_ns,
            fs_hz=fs_hz,
            attribute="RawComplex",
            transform_name="未转换",
        )

    def _read_frames(self, path: Path, header: DatFileHeader, progress_callback=None, cancel_callback=None) -> List[np.ndarray]:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            file_end_pos = fh.tell()

            current_p = header.offset_binary
            first_send = self._read_uint16(fh, current_p + 4)
            if first_send is None:
                raise ValueError("Unable to read the first send channel.")

            block_defs: list[tuple[int, int, int]] = []
            expected_send_seq: list[int] = []
            max_scan = 20000
            for _ in range(max_scan):
                self._check_cancelled(cancel_callback)
                send_channel = self._read_uint16(fh, current_p + 4)
                if send_channel is None:
                    break
                if expected_send_seq and send_channel == first_send:
                    break
                frame_size = self._read_int32(fh, current_p + 23)
                receive_channel = self._read_uint32(fh, current_p + 6)
                if frame_size is None or frame_size <= 0 or receive_channel is None:
                    raise ValueError("Invalid frame size or receive channel in frame definition.")
                expected_send_seq.append(int(send_channel))
                block_defs.append((int(send_channel), int(receive_channel), int(frame_size)))
                current_p += header.frame_header_size + frame_size
                self._report_progress(
                    progress_callback,
                    min(15, int(current_p / max(file_end_pos, 1) * 15)),
                    "正在扫描数据帧结构",
                )
                if current_p >= file_end_pos:
                    break

            if not block_defs:
                raise ValueError("No valid block definitions were found.")

            num_blocks = len(block_defs)
            start_p_data = current_p
            first_send_expected = expected_send_seq[0]

            block_mapping: list[tuple[int, int]] = []
            array_ptr = 0
            channel_counts: list[int] = []
            for _, receive_channel, _ in block_defs:
                one_n = bin(receive_channel).count("1")
                channel_counts.append(one_n)
                block_mapping.append((array_ptr, one_n))
                array_ptr += one_n
            total_lines = sum(channel_counts)

            data_cols: list[list[np.ndarray]] = [[] for _ in range(total_lines)]
            current_p = start_p_data
            keep_frame_cnt = 0
            drop_frame_cnt = 0

            while current_p + 32 < file_end_pos:
                self._check_cancelled(cancel_callback)
                frame_ok = True
                frame_blocks: list[bytes | None] = [None] * num_blocks
                block_idx = 0
                while block_idx < num_blocks:
                    self._check_cancelled(cancel_callback)
                    if current_p + 32 >= file_end_pos:
                        frame_ok = False
                        break
                    send_channel = self._read_uint16(fh, current_p + 4)
                    frame_size = self._read_int32(fh, current_p + 23)
                    if send_channel is None or frame_size is None or frame_size <= 0:
                        frame_ok = False
                        break
                    if int(send_channel) != expected_send_seq[block_idx]:
                        frame_ok = False
                    payload_p = current_p + header.frame_header_size
                    if frame_ok:
                        fh.seek(payload_p)
                        frame_blocks[block_idx] = fh.read(frame_size)
                    current_p += header.frame_header_size + frame_size
                    block_idx += 1
                    if current_p >= file_end_pos:
                        break

                if (not frame_ok) or block_idx < num_blocks:
                    drop_frame_cnt += 1
                    while current_p + 32 < file_end_pos:
                        sync_send = self._read_uint16(fh, current_p + 4)
                        if sync_send is None or int(sync_send) == first_send_expected:
                            break
                        sz = self._read_int32(fh, current_p + 23)
                        if sz is None or sz <= 0:
                            break
                        current_p += header.frame_header_size + sz
                    continue

                keep_frame_cnt += 1
                for bb in range(num_blocks):
                    self._check_cancelled(cancel_callback)
                    payload = frame_blocks[bb]
                    if payload is None:
                        raise ValueError("A complete frame contained an empty payload.")
                    block_data = np.frombuffer(payload, dtype=np.float32).copy()
                    start_arr_idx, n_parts = block_mapping[bb]
                    pts_part = block_data.size / max(1, n_parts)
                    if abs(pts_part - round(pts_part)) > 1e-9:
                        raise ValueError("block_data length cannot be evenly divided by channel parts.")
                    pts_part = int(round(pts_part))
                    for part in range(n_parts):
                        seg = block_data[part * pts_part : (part + 1) * pts_part]
                        data_cols[start_arr_idx + part].append(seg)
                self._report_progress(
                    progress_callback,
                    min(90, 15 + int((current_p / max(file_end_pos, 1)) * 75)),
                    f"正在读取数据帧，已保留 {keep_frame_cnt} 帧",
                )

            if keep_frame_cnt == 0:
                raise ValueError("No complete frames were retained from the DAT file.")

            data_array: List[np.ndarray] = []
            for cols in data_cols:
                self._check_cancelled(cancel_callback)
                if not cols:
                    data_array.append(np.empty((0, 0), dtype=np.float32))
                else:
                    data_array.append(np.column_stack(cols).astype(np.float32, copy=False))
            return data_array

    def _to_frequency_channels(self, data_array: Sequence[np.ndarray]) -> List[np.ndarray]:
        channels: List[np.ndarray] = []
        for raw_mat in data_array:
            if raw_mat.size == 0:
                channels.append(np.empty((0, 0), dtype=np.complex64))
                continue
            i_part = raw_mat[0::2, :]
            q_part = raw_mat[1::2, :]
            channels.append((i_part + 1j * q_part).astype(np.complex64))
        return channels

    def _transform_channels(
        self,
        data_array: Sequence[np.ndarray],
        header: DatFileHeader,
        params: DataImportParameters,
    ) -> List[np.ndarray]:
        t0 = params.tw_start_ns * 1e-9
        t1 = params.tw_end_ns * 1e-9
        f_start_usr = params.f_start_hz or header.start_frequency_hz
        f_end_usr = params.f_end_hz or header.end_frequency_hz
        sample_new = params.sample_count or header.sample_count
        fs_bandwidth = f_end_usr - f_start_usr
        kw = np.kaiser(sample_new, params.beta).astype(np.float32)
        chunk_size = max(1, int(params.chunk_size))

        if params.tf_method == 1:
            ts_val = 1.0 / (fs_bandwidth / sample_new)
            W = np.exp(1j * 2.0 * np.pi * (t1 - t0) / sample_new / ts_val)
            A = np.exp(1j * 2.0 * np.pi * (-t0) / ts_val)
        else:
            t = np.linspace(t0, t1, sample_new).reshape(-1, 1)

        data_out: List[np.ndarray] = []
        for raw_mat in data_array:
            if raw_mat.size == 0:
                data_out.append(np.empty((0, 0), dtype=np.float32))
                continue

            I = raw_mat[0::2, :]
            Q = raw_mat[1::2, :]
            C = (I + 1j * Q).astype(np.complex64)
            C, _, _, _ = trim_bad_tail_by_energy(C, 0.85, 100, 21)
            n_freq, n_traces = C.shape
            final_res = np.zeros((sample_new, n_traces), dtype=np.complex64)
            orig_freq = np.linspace(header.start_frequency_hz, header.end_frequency_hz, n_freq)
            target_freq = np.linspace(f_start_usr, f_end_usr, sample_new)
            need_interp = len(orig_freq) != len(target_freq) or np.any(np.abs(orig_freq - target_freq) > 1)

            for start in range(0, n_traces, chunk_size):
                end = min(start + chunk_size, n_traces)
                curr_cols = slice(start, end)
                C_sub = C[:, curr_cols]

                if need_interp:
                    try:
                        real_interp = interpolate.interp1d(
                            orig_freq, C_sub.real, axis=0, kind="cubic", fill_value="extrapolate"
                        )
                        imag_interp = interpolate.interp1d(
                            orig_freq, C_sub.imag, axis=0, kind="cubic", fill_value="extrapolate"
                        )
                        C_sub_interp = real_interp(target_freq) + 1j * imag_interp(target_freq)
                    except Exception:
                        C_sub_interp = C_sub
                else:
                    C_sub_interp = C_sub

                if params.zero_correct:
                    C_sub_corrected, _ = correct_direct_wave(C_sub_interp, target_freq)
                    C_windowed = C_sub_corrected * kw[:, None]
                else:
                    C_windowed = C_sub_interp * kw[:, None]

                if params.tf_method == 1:
                    res_chunk = signal.czt(C_windowed, m=sample_new, w=W, a=A, axis=0)
                else:
                    if params.zero_correct:
                        res_chunk = isdft_soft_phys(
                            C_windowed,
                            f_start_usr,
                            f_end_usr,
                            t.ravel(),
                            params.isdft.alpha,
                            params.isdft.th_db,
                            params.isdft.smooth_len,
                            0,
                            params.isdft.ramp_ns,
                        )
                    else:
                        _, tau_base = correct_direct_wave(C_sub_interp, target_freq)
                        res_chunk = isdft_soft_phys(
                            C_windowed,
                            f_start_usr,
                            f_end_usr,
                            t.ravel(),
                            params.isdft.alpha,
                            params.isdft.th_db,
                            params.isdft.smooth_len,
                            tau_base,
                            params.isdft.ramp_ns,
                        )
                final_res[:, curr_cols] = self._extract_attribute(res_chunk, params.selected_attr_idx)

            if params.selected_attr_idx != 1:
                final_res = final_res.real.astype(np.float32)
            data_out.append(final_res)
        return data_out

    def _extract_attribute(self, res_chunk: np.ndarray, attr_idx: int) -> np.ndarray:
        if attr_idx == 1:
            return res_chunk
        if attr_idx == 2:
            return np.real(res_chunk)
        if attr_idx == 3:
            return np.abs(res_chunk)
        if attr_idx == 4:
            temp = np.abs(res_chunk)
            return np.vstack([np.diff(temp, axis=0), np.zeros((1, temp.shape[1]), dtype=temp.dtype)])
        if attr_idx == 5:
            temp = np.abs(res_chunk)
            return np.vstack([np.diff(temp, n=2, axis=0), np.zeros((2, temp.shape[1]), dtype=temp.dtype)])
        if attr_idx == 6:
            return np.angle(res_chunk)
        if attr_idx == 7:
            return np.cos(np.angle(res_chunk))
        if attr_idx == 8:
            u_ph = np.unwrap(np.angle(res_chunk), axis=0)
            return np.vstack([np.diff(u_ph, axis=0), np.zeros((1, u_ph.shape[1]), dtype=u_ph.dtype)])
        return res_chunk

    @staticmethod
    def _read_uint16(fh, pos: int) -> int | None:
        fh.seek(pos)
        data = fh.read(2)
        return struct.unpack("<H", data)[0] if len(data) == 2 else None

    @staticmethod
    def _read_uint32(fh, pos: int) -> int | None:
        fh.seek(pos)
        data = fh.read(4)
        return struct.unpack("<I", data)[0] if len(data) == 4 else None

    @staticmethod
    def _read_int32(fh, pos: int) -> int | None:
        fh.seek(pos)
        data = fh.read(4)
        return struct.unpack("<i", data)[0] if len(data) == 4 else None

    @staticmethod
    def _check_cancelled(cancel_callback) -> None:
        if cancel_callback is not None and cancel_callback():
            raise WorkerCancelled()

    @staticmethod
    def _report_progress(progress_callback, percent: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(int(max(0, min(100, percent))), str(message))


def trim_bad_tail_by_energy(
    data_in: np.ndarray,
    drop_ratio: float = 0.93,
    min_bad_run: int = 120,
    smooth_win: int = 51,
):
    data_out = data_in
    _, nt = data_in.shape
    energy = np.sum(np.abs(data_in), axis=0)
    kernel = np.ones(max(1, smooth_win), dtype=float) / max(1, smooth_win)
    energy_s = np.convolve(energy, kernel, mode="same")
    ref_end = max(10, round(0.2 * nt))
    ref_energy = np.median(energy_s[:ref_end])
    thr = drop_ratio * ref_energy
    bad_mask = energy_s < thr

    cnt = 0
    for i in range(nt - 1, -1, -1):
        if bad_mask[i]:
            cnt += 1
        else:
            break
    if cnt >= min_bad_run:
        margin = 20
        last_good_idx = max(1, nt - cnt - margin)
        data_out = data_in[:, :last_good_idx]
    else:
        last_good_idx = nt
    return data_out, last_good_idx, energy, bad_mask
