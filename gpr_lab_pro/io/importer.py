from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import struct
from typing import List, Sequence

import numpy as np
from gpr_lab_pro.infrastructure.workers import WorkerCancelled
from gpr_lab_pro.io.dat_loader import DatFileHeader, read_dat_frame_header_from_handle, read_dat_header


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
    # Deprecated compatibility-only fields kept for old project payloads.
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


@dataclass(frozen=True)
class ImportedNavigationSample:
    trace_index: int
    latitude: float
    longitude: float
    gps_status: int | None = None
    gps_timestamp: int | None = None


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
    navigation_samples: list[ImportedNavigationSample] = field(default_factory=list)
    gps_metadata_present: bool = False

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
        parsed_channels, navigation_samples, gps_metadata_present = self._read_frames(
            path,
            header,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        self._check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 92, "正在整理原始频域数据")
        frequency_channels = self._to_frequency_channels(parsed_channels)
        if frequency_channels:
            min_nt = min(ch.shape[1] for ch in frequency_channels if ch.size)
            frequency_channels = [ch[:, :min_nt] if ch.size else ch for ch in frequency_channels]
        tw_ns, dt_ns, fs_hz = self._build_frequency_time_meta(header, frequency_channels)
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
            navigation_samples=navigation_samples,
            gps_metadata_present=bool(gps_metadata_present),
        )

    def _read_frames(
        self,
        path: Path,
        header: DatFileHeader,
        progress_callback=None,
        cancel_callback=None,
    ) -> tuple[List[np.ndarray], list[ImportedNavigationSample], bool]:
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
            navigation_rows: list[ImportedNavigationSample | None] = []
            gps_metadata_present = False

            while current_p + 32 < file_end_pos:
                self._check_cancelled(cancel_callback)
                frame_ok = True
                frame_blocks: list[bytes | None] = [None] * num_blocks
                frame_navigation_meta: list[tuple[int | None, int | None, float | None, float | None]] = []
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
                    frame_header = read_dat_frame_header_from_handle(fh, current_p)
                    gps_status = int(frame_header.gps_status)
                    gps_timestamp = int(frame_header.gps_timestamp)
                    longitude = float(frame_header.longitude)
                    latitude = float(frame_header.latitude)
                    if gps_status != 0 or gps_timestamp != 0 or self._is_valid_geo(latitude, longitude):
                        gps_metadata_present = True
                    frame_navigation_meta.append((gps_status, gps_timestamp, latitude, longitude))
                    payload_p = current_p + header.frame_header_size
                    if frame_ok:
                        fh.seek(payload_p)
                        frame_blocks[block_idx] = fh.read(frame_size)
                    current_p += header.frame_header_size + frame_size
                    block_idx += 1
                    if current_p >= file_end_pos:
                        break

                if (not frame_ok) or block_idx < num_blocks:
                    while current_p + 32 < file_end_pos:
                        sync_send = self._read_uint16(fh, current_p + 4)
                        if sync_send is None or int(sync_send) == first_send_expected:
                            break
                        sz = self._read_int32(fh, current_p + 23)
                        if sz is None or sz <= 0:
                            break
                        current_p += header.frame_header_size + sz
                    continue

                trace_index = keep_frame_cnt
                keep_frame_cnt += 1
                navigation_rows.append(self._extract_navigation_sample(trace_index, frame_navigation_meta))
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
            return data_array, self._finalize_navigation_samples(navigation_rows), gps_metadata_present

    def _to_frequency_channels(self, data_array: Sequence[np.ndarray]) -> List[np.ndarray]:
        channels: List[np.ndarray] = []
        for raw_mat in data_array:
            if raw_mat.size == 0:
                channels.append(np.empty((0, 0), dtype=np.complex64))
                continue
            i_part = raw_mat[0::2, :]
            q_part = raw_mat[1::2, :]
            channel = (i_part + 1j * q_part).astype(np.complex64)
            channels.append(self._sanitize_frequency_channel(channel))
        return channels

    @staticmethod
    def _build_frequency_time_meta(
        header: DatFileHeader,
        frequency_channels: Sequence[np.ndarray],
    ) -> tuple[float, float, float]:
        sample_count = next(
            (int(channel.shape[0]) for channel in frequency_channels if channel.ndim >= 1 and channel.shape[0] > 1),
            int(header.sample_count),
        )
        bandwidth_hz = float(header.end_frequency_hz) - float(header.start_frequency_hz)
        if sample_count > 1 and bandwidth_hz > 0:
            dt_ns = 1e9 / bandwidth_hz
            tw_ns = dt_ns * (sample_count - 1)
            fs_hz = 1.0 / (dt_ns * 1e-9)
            return tw_ns, dt_ns, fs_hz
        return 0.0, 0.0, 0.0

    @staticmethod
    def _sanitize_frequency_channel(channel: np.ndarray) -> np.ndarray:
        arr = np.asarray(channel, dtype=np.complex64).copy()
        invalid = ~np.isfinite(arr)
        if not np.any(invalid):
            return arr
        for trace_idx in range(arr.shape[1]):
            trace = arr[:, trace_idx]
            mask = np.isfinite(trace)
            if np.all(mask):
                continue
            if not np.any(mask):
                arr[:, trace_idx] = 0.0
                continue
            valid_idx = np.flatnonzero(mask)
            invalid_idx = np.flatnonzero(~mask)
            real = trace.real
            imag = trace.imag
            real[invalid_idx] = np.interp(invalid_idx, valid_idx, real[valid_idx]).astype(np.float32, copy=False)
            imag[invalid_idx] = np.interp(invalid_idx, valid_idx, imag[valid_idx]).astype(np.float32, copy=False)
            arr[:, trace_idx] = real + 1j * imag
        arr[~np.isfinite(arr)] = 0.0
        return arr

    @classmethod
    def _extract_navigation_sample(
        cls,
        trace_index: int,
        frame_navigation_meta: Sequence[tuple[int | None, int | None, float | None, float | None]],
    ) -> ImportedNavigationSample | None:
        best_sample: ImportedNavigationSample | None = None
        best_rank: tuple[int, int, int] | None = None
        for gps_status, gps_timestamp, latitude, longitude in frame_navigation_meta:
            if not cls._is_valid_geo(latitude, longitude):
                continue
            timestamp_present = int((gps_timestamp or 0) != 0)
            rank = (
                int(gps_status or 0),
                timestamp_present,
                int(abs(float(latitude)) + abs(float(longitude)) > 0.0),
            )
            candidate = ImportedNavigationSample(
                trace_index=trace_index,
                latitude=float(latitude),
                longitude=float(longitude),
                gps_status=(None if gps_status is None else int(gps_status)),
                gps_timestamp=(None if gps_timestamp in (None, 0) else int(gps_timestamp)),
            )
            if best_rank is None or rank > best_rank:
                best_sample = candidate
                best_rank = rank
        return best_sample

    @staticmethod
    def _finalize_navigation_samples(
        navigation_rows: Sequence[ImportedNavigationSample | None],
    ) -> list[ImportedNavigationSample]:
        if not navigation_rows:
            return []
        valid_rows = [
            (index, sample)
            for index, sample in enumerate(navigation_rows)
            if sample is not None
        ]
        if not valid_rows:
            return []

        total = len(navigation_rows)
        if len(valid_rows) == 1:
            _, sample = valid_rows[0]
            return [
                ImportedNavigationSample(
                    trace_index=index,
                    latitude=float(sample.latitude),
                    longitude=float(sample.longitude),
                    gps_status=sample.gps_status,
                    gps_timestamp=sample.gps_timestamp,
                )
                for index in range(total)
            ]

        trace_positions = np.array([index for index, _ in valid_rows], dtype=float)
        latitude_values = np.array([float(sample.latitude) for _, sample in valid_rows], dtype=float)
        longitude_values = np.array([float(sample.longitude) for _, sample in valid_rows], dtype=float)
        all_positions = np.arange(total, dtype=float)
        filled_latitudes = np.interp(all_positions, trace_positions, latitude_values)
        filled_longitudes = np.interp(all_positions, trace_positions, longitude_values)

        finalized: list[ImportedNavigationSample] = []
        for index in range(total):
            original = navigation_rows[index]
            finalized.append(
                ImportedNavigationSample(
                    trace_index=index,
                    latitude=float(filled_latitudes[index]),
                    longitude=float(filled_longitudes[index]),
                    gps_status=(None if original is None else original.gps_status),
                    gps_timestamp=(None if original is None else original.gps_timestamp),
                )
            )
        return finalized

    @staticmethod
    def _is_valid_geo(latitude: float | None, longitude: float | None) -> bool:
        if latitude is None or longitude is None:
            return False
        lat = float(latitude)
        lon = float(longitude)
        if not math.isfinite(lat) or not math.isfinite(lon):
            return False
        if abs(lat) > 90.0 or abs(lon) > 180.0:
            return False
        return abs(lat) > 1e-9 or abs(lon) > 1e-9

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
