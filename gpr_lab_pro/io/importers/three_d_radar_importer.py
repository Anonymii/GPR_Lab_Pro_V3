from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import struct
import subprocess
import xml.etree.ElementTree as ET

import numpy as np

from gpr_lab_pro.infrastructure.workers import WorkerCancelled
from gpr_lab_pro.io.dat_loader import DatFileHeader
from gpr_lab_pro.io.importer import (
    DataImportParameters,
    GPRDataImporter,
    ImportedGPRData,
    ImportedNavigationSample,
)


THREE_D_RADAR_METADATA_NS = "http://www.3d-radar.com/schemas/metaInfo3dra"
DEFAULT_CUBE_NAME = "data0.cube"


@dataclass(frozen=True)
class CubeHeader:
    magic: str
    header_size: int
    version: int
    trace_count: int
    channel_count: int
    frequency_count: int
    data_type: int
    payload_bytes: int
    entry_size: int


@dataclass(frozen=True)
class TriggerSample:
    trace_index: int
    timestamp: int


@dataclass(frozen=True)
class NmeaNavigationSample:
    timestamp: int
    latitude: float
    longitude: float
    fix_quality: int | None = None


class ThreeDRadarImporter:
    """Import 3D Radar .3dra archives or already extracted archive folders."""

    def import_file(
        self,
        path: str | Path,
        params: DataImportParameters | None = None,
        progress_callback=None,
        cancel_callback=None,
    ) -> ImportedGPRData:
        del params
        source_path = Path(path)
        self._check_cancelled(cancel_callback)
        self._report_progress(progress_callback, 0, "正在读取 3D Radar 数据")
        extract_dir = self._resolve_extracted_dir(source_path)
        self._report_progress(progress_callback, 8, "已定位 3D Radar 解包目录")

        cube_path = extract_dir / DEFAULT_CUBE_NAME
        metadata_path = extract_dir / "metadata.xml"
        positions_path = extract_dir / "positions_internal.tsv"
        triggers_path = extract_dir / "triggers.trg"
        if not cube_path.exists():
            raise FileNotFoundError(f"3D Radar cube file not found: {cube_path}")
        if not metadata_path.exists():
            raise FileNotFoundError(f"3D Radar metadata file not found: {metadata_path}")

        cube_header = self.parse_cube_header(cube_path)
        metadata = self.parse_metadata(metadata_path)
        self._report_progress(progress_callback, 18, "已读取 3D Radar 元数据")
        cube = self.load_cube_complex64(cube_path, cube_header, cancel_callback=cancel_callback)
        channels = self._cube_to_channels(cube, progress_callback=progress_callback, cancel_callback=cancel_callback)
        del cube

        triggers = self.parse_triggers(triggers_path)
        nmea_samples = self.parse_positions_internal(positions_path)
        navigation_samples = self._build_navigation_samples(
            trace_count=cube_header.trace_count,
            triggers=triggers,
            nmea_samples=nmea_samples,
        )
        start_frequency_hz = int(metadata.get("nominal_start_frequency_hz") or 40_000_000)
        frequency_step_hz = int(metadata.get("frequency_step_hz") or 20_000_000)
        end_frequency_hz = start_frequency_hz + max(cube_header.frequency_count - 1, 0) * frequency_step_hz
        header = DatFileHeader(
            magic_number="3DRA",
            offset_binary=cube_header.header_size,
            sample_idx=-1,
            frame_header_size=0,
            start_frequency_hz=start_frequency_hz,
            end_frequency_hz=end_frequency_hz,
            sample_count=cube_header.frequency_count,
            file_size=int(cube_path.stat().st_size),
        )
        tw_ns, dt_ns, fs_hz = GPRDataImporter._build_frequency_time_meta(header, channels)
        self._report_progress(progress_callback, 100, "3D Radar 导入完成")
        return ImportedGPRData(
            channels=channels,
            tw_ns=tw_ns,
            filename=source_path.name,
            header=header,
            dt_ns=dt_ns,
            fs_hz=fs_hz,
            attribute="RawComplex",
            transform_name="3D Radar Cube",
            navigation_samples=navigation_samples,
            gps_metadata_present=bool(navigation_samples),
        )

    @classmethod
    def parse_cube_header(cls, path: str | Path) -> CubeHeader:
        path = Path(path)
        header = path.read_bytes()[:33]
        if len(header) < 33:
            raise ValueError(f"Cube header is too short: {path}")
        magic = header[:6].decode("ascii", errors="ignore").rstrip("\x00")
        header_size = struct.unpack_from("<I", header, 8)[0]
        version = struct.unpack_from("<I", header, 12)[0]
        frequency_count = struct.unpack_from("<I", header, 16)[0]
        channel_count = struct.unpack_from("<I", header, 20)[0]
        trace_count = struct.unpack_from("<I", header, 24)[0]
        packed_tail = struct.unpack_from("<I", header, 28)[0]
        data_type = packed_tail & 0xFF
        payload_bytes = packed_tail >> 8
        total_entries = trace_count * channel_count * frequency_count
        if total_entries <= 0:
            raise ValueError(f"Invalid cube dimensions in {path}: {(trace_count, channel_count, frequency_count)}")
        body_bytes = path.stat().st_size - header_size
        if body_bytes % total_entries != 0:
            raise ValueError(f"Cube payload size is incompatible with dimensions: {path}")
        entry_size = body_bytes // total_entries
        return CubeHeader(
            magic=magic,
            header_size=header_size,
            version=version,
            trace_count=trace_count,
            channel_count=channel_count,
            frequency_count=frequency_count,
            data_type=data_type,
            payload_bytes=payload_bytes,
            entry_size=entry_size,
        )

    @classmethod
    def load_cube_complex64(
        cls,
        path: str | Path,
        header: CubeHeader,
        *,
        cancel_callback=None,
    ) -> np.ndarray:
        cls._check_cancelled(cancel_callback)
        path = Path(path)
        if header.entry_size != 8:
            raise ValueError(f"Only 8-byte complex64 cube entries are supported; {path.name} uses {header.entry_size}.")
        raw = np.fromfile(path, dtype=np.float32, offset=header.header_size)
        expected = header.trace_count * header.channel_count * header.frequency_count * 2
        if raw.size != expected:
            raise ValueError(f"Unexpected cube payload length in {path.name}: {raw.size}, expected {expected}.")
        return raw.view(np.complex64).reshape(header.trace_count, header.channel_count, header.frequency_count)

    @staticmethod
    def parse_metadata(path: str | Path) -> dict[str, object]:
        path = Path(path)
        ns = {"m": THREE_D_RADAR_METADATA_NS}
        root = ET.parse(path).getroot()

        def text(xpath: str, default: str = "") -> str:
            node = root.find(xpath, ns)
            if node is not None and node.text:
                return node.text.strip()
            return default

        def float_text(xpath: str, default: float = 0.0) -> float:
            value = text(xpath)
            return float(value) if value else default

        return {
            "schema_version": text("m:schema_version"),
            "file_generator": text("m:file_generator"),
            "acquisition_started": text("m:acquisition_info/m:acquisition_started"),
            "geoscope_type": text("m:system_info/m:geoscope/m:type"),
            "antenna_type": text("m:system_info/m:antenna/m:type"),
            "frequency_profile": text("m:acquisition_info/m:scan_configuration/m:frequency_profile"),
            "nominal_start_frequency_hz": float_text(
                "m:acquisition_info/m:scan_configuration/m:nominal_start_frequency"
            ),
            "frequency_step_hz": float_text("m:acquisition_info/m:scan_configuration/m:frequency_step"),
            "number_of_frequencies": int(
                text("m:acquisition_info/m:scan_configuration/m:number_of_frequencies", "0") or 0
            ),
            "dwell_time_s": float_text("m:acquisition_info/m:scan_configuration/m:dwell_time"),
        }

    @classmethod
    def parse_triggers(cls, path: str | Path) -> list[TriggerSample]:
        path = Path(path)
        if not path.exists():
            return []
        data = path.read_bytes()
        if len(data) < 25:
            return []
        magic = data[:6].decode("ascii", errors="ignore")
        if magic != "3drtrg":
            return []
        header_size = struct.unpack_from("<I", data, 8)[0]
        trigger_count = struct.unpack_from("<I", data, 16)[0]
        if header_size <= 0 or trigger_count <= 0 or len(data) <= header_size:
            return []
        entry_size = (len(data) - header_size) // trigger_count
        if entry_size < 12:
            return []
        samples: list[TriggerSample] = []
        for index in range(trigger_count):
            offset = header_size + index * entry_size
            if offset + 12 > len(data):
                break
            timestamp = struct.unpack_from("<q", data, offset)[0]
            trace_index = struct.unpack_from("<i", data, offset + 8)[0]
            if timestamp > 0 and trace_index >= 0:
                samples.append(TriggerSample(trace_index=int(trace_index), timestamp=int(timestamp)))
        return samples

    @classmethod
    def parse_positions_internal(cls, path: str | Path) -> list[NmeaNavigationSample]:
        path = Path(path)
        if not path.exists():
            return []
        samples: list[NmeaNavigationSample] = []
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("radar3d.geoscope.positions.version="):
                    continue
                parts = line.split("\t", 1)
                if len(parts) != 2:
                    continue
                try:
                    timestamp = int(parts[0])
                except ValueError:
                    continue
                nav = cls._parse_nmea_sentence(timestamp, parts[1])
                if nav is not None:
                    samples.append(nav)
        samples.sort(key=lambda item: item.timestamp)
        return samples

    @classmethod
    def _parse_nmea_sentence(cls, timestamp: int, sentence: str) -> NmeaNavigationSample | None:
        payload = sentence.split("*", 1)[0]
        fields = payload.split(",")
        sentence_type = fields[0].upper() if fields else ""
        if sentence_type.endswith("RMC") and len(fields) >= 7:
            if fields[2].upper() != "A":
                return None
            lat = cls._nmea_component_to_decimal(fields[3], fields[4])
            lon = cls._nmea_component_to_decimal(fields[5], fields[6])
            fix_quality = 1
        elif sentence_type.endswith("GGA") and len(fields) >= 7:
            try:
                fix_quality = int(fields[6] or "0")
            except ValueError:
                fix_quality = 0
            if fix_quality <= 0:
                return None
            lat = cls._nmea_component_to_decimal(fields[2], fields[3])
            lon = cls._nmea_component_to_decimal(fields[4], fields[5])
        else:
            return None
        if not cls._is_valid_geo(lat, lon):
            return None
        return NmeaNavigationSample(
            timestamp=int(timestamp),
            latitude=float(lat),
            longitude=float(lon),
            fix_quality=fix_quality,
        )

    @staticmethod
    def _nmea_component_to_decimal(value: str, hemisphere: str) -> float | None:
        if not value:
            return None
        try:
            raw = float(value)
        except ValueError:
            return None
        decoded = GPRDataImporter._degree_minute_to_decimal_degrees(raw)
        if decoded is None:
            return None
        hemi = (hemisphere or "").upper()
        if hemi in {"S", "W"}:
            decoded = -abs(decoded)
        elif hemi in {"N", "E"}:
            decoded = abs(decoded)
        return float(decoded)

    @staticmethod
    def _is_valid_geo(latitude: float | None, longitude: float | None) -> bool:
        if latitude is None or longitude is None:
            return False
        lat = float(latitude)
        lon = float(longitude)
        return math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90.0 and abs(lon) <= 180.0

    @classmethod
    def _build_navigation_samples(
        cls,
        *,
        trace_count: int,
        triggers: list[TriggerSample],
        nmea_samples: list[NmeaNavigationSample],
    ) -> list[ImportedNavigationSample]:
        if trace_count <= 0 or not nmea_samples:
            return []

        nav_timestamps = np.array([sample.timestamp for sample in nmea_samples], dtype=float)
        latitudes = np.array([sample.latitude for sample in nmea_samples], dtype=float)
        longitudes = np.array([sample.longitude for sample in nmea_samples], dtype=float)
        if triggers:
            trace_timestamps = np.full(trace_count, np.nan, dtype=float)
            for trigger in triggers:
                if 0 <= trigger.trace_index < trace_count:
                    trace_timestamps[trigger.trace_index] = float(trigger.timestamp)
            valid = np.flatnonzero(np.isfinite(trace_timestamps))
            if valid.size >= 2:
                all_indices = np.arange(trace_count, dtype=float)
                trace_timestamps = np.interp(all_indices, valid.astype(float), trace_timestamps[valid])
            elif valid.size == 1:
                trace_timestamps[:] = trace_timestamps[valid[0]]
            else:
                trace_timestamps = np.linspace(nav_timestamps[0], nav_timestamps[-1], trace_count)
        else:
            trace_timestamps = np.linspace(nav_timestamps[0], nav_timestamps[-1], trace_count)

        filled_latitudes = np.interp(trace_timestamps, nav_timestamps, latitudes)
        filled_longitudes = np.interp(trace_timestamps, nav_timestamps, longitudes)
        return [
            ImportedNavigationSample(
                trace_index=index,
                latitude=float(filled_latitudes[index]),
                longitude=float(filled_longitudes[index]),
                gps_status=1,
                gps_timestamp=int(trace_timestamps[index]) if math.isfinite(float(trace_timestamps[index])) else None,
            )
            for index in range(trace_count)
        ]

    @classmethod
    def _cube_to_channels(
        cls,
        cube: np.ndarray,
        *,
        progress_callback=None,
        cancel_callback=None,
    ) -> list[np.ndarray]:
        trace_count, channel_count, _frequency_count = cube.shape
        channels: list[np.ndarray] = []
        for channel_index in range(channel_count):
            cls._check_cancelled(cancel_callback)
            channel = cube[:, channel_index, :].T.astype(np.complex64, copy=True)
            channels.append(GPRDataImporter._sanitize_frequency_channel(channel))
            cls._report_progress(
                progress_callback,
                20 + int((channel_index + 1) / max(channel_count, 1) * 62),
                f"正在转换 3D Radar 通道 {channel_index + 1}/{channel_count}",
            )
        if trace_count <= 0:
            return []
        return channels

    @classmethod
    def _resolve_extracted_dir(cls, source_path: Path) -> Path:
        source_path = source_path.resolve()
        if source_path.is_dir():
            return source_path
        if not source_path.exists():
            raise FileNotFoundError(f"3D Radar input not found: {source_path}")
        adjacent_extract_dir = source_path.with_name(f"{source_path.name}.extracted")
        if adjacent_extract_dir.exists():
            return adjacent_extract_dir
        archiver = cls._find_archiver(source_path)
        if archiver is None:
            raise FileNotFoundError(
                "Archiver.exe was not found and no adjacent .extracted directory exists for "
                f"{source_path}"
            )
        result = subprocess.run(
            [str(archiver), str(source_path)],
            cwd=str(archiver.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Archiver.exe failed while extracting 3D Radar data.\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )
        if not adjacent_extract_dir.exists():
            raise FileNotFoundError(f"Expected extracted directory was not created: {adjacent_extract_dir}")
        return adjacent_extract_dir

    @staticmethod
    def _find_archiver(source_path: Path) -> Path | None:
        candidates = [
            source_path.parent / "Archiver.exe",
            source_path.parent.parent / "Archiver.exe",
            Path(__file__).resolve().parents[4] / "3dr Examiner" / "3dr_exe" / "Archiver.exe",
            Path(r"E:\code_management\GPR_V12_Pyside\3dr Examiner\3dr_exe\Archiver.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _report_progress(progress_callback, value: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(int(max(0, min(100, value))), message)

    @staticmethod
    def _check_cancelled(cancel_callback) -> None:
        if cancel_callback is not None and cancel_callback():
            raise WorkerCancelled()
