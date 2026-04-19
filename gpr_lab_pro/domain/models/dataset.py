from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import uuid

import numpy as np

from gpr_lab_pro.io.importer import ImportedGPRData
from gpr_lab_pro.io.importers import DataImportParameters


@dataclass
class DatasetRecord:
    dataset_id: str
    name: str
    filename: str
    volume: np.ndarray
    tw_ns: float
    dt_ns: float
    fs_hz: float
    attribute: str
    transform_name: str
    header: dict[str, Any] = field(default_factory=dict)
    source_path: str = ""
    import_params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_import(
        cls,
        imported: ImportedGPRData,
        *,
        source_path: str = "",
        import_params: DataImportParameters | None = None,
    ) -> "DatasetRecord":
        header = {
            "magic_number": imported.header.magic_number,
            "offset_binary": imported.header.offset_binary,
            "sample_idx": imported.header.sample_idx,
            "frame_header_size": imported.header.frame_header_size,
            "start_frequency_hz": imported.header.start_frequency_hz,
            "end_frequency_hz": imported.header.end_frequency_hz,
            "sample_count": imported.header.sample_count,
            "file_size": imported.header.file_size,
        }
        return cls(
            dataset_id=uuid.uuid4().hex,
            name=imported.filename,
            filename=imported.filename,
            volume=imported.as_3d(),
            tw_ns=imported.tw_ns,
            dt_ns=imported.dt_ns,
            fs_hz=imported.fs_hz,
            attribute=imported.attribute,
            transform_name=imported.transform_name,
            header=header,
            source_path=source_path,
            import_params=asdict(import_params or DataImportParameters()),
        )

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(int(v) for v in self.volume.shape)

    @property
    def sample_count(self) -> int:
        return int(self.volume.shape[0]) if self.volume.ndim == 3 else 0

    @property
    def trace_count(self) -> int:
        return int(self.volume.shape[1]) if self.volume.ndim == 3 else 0

    @property
    def line_count(self) -> int:
        return int(self.volume.shape[2]) if self.volume.ndim == 3 else 0

    def transformed_time_window_ns(self) -> float:
        start_freq = float(self.header.get("start_frequency_hz", 0.0))
        end_freq = float(self.header.get("end_frequency_hz", 0.0))
        bandwidth = end_freq - start_freq
        if self.sample_count > 1 and bandwidth > 0:
            return (self.sample_count - 1) / bandwidth * 1e9
        return float(self.tw_ns)

    def transformed_dt_ns(self) -> float:
        if self.sample_count <= 1:
            return float(self.dt_ns)
        return self.transformed_time_window_ns() / (self.sample_count - 1)

    def crop_region(
        self,
        *,
        trace_start: int,
        trace_stop: int,
        line_start: int,
        line_stop: int,
        sample_start: int,
        sample_stop: int,
        region_name: str,
    ) -> "DatasetRecord":
        sample0 = int(np.clip(sample_start, 0, max(self.sample_count - 1, 0)))
        sample1 = int(np.clip(sample_stop, sample0 + 1, max(self.sample_count, 1)))
        trace0 = int(np.clip(trace_start, 0, max(self.trace_count - 1, 0)))
        trace1 = int(np.clip(trace_stop, trace0 + 1, max(self.trace_count, 1)))
        line0 = int(np.clip(line_start, 0, max(self.line_count - 1, 0)))
        line1 = int(np.clip(line_stop, line0 + 1, max(self.line_count, 1)))
        cropped = self.volume[sample0:sample1, trace0:trace1, line0:line1].copy()
        header = dict(self.header)
        header["region_sample_start"] = sample0
        header["region_sample_stop"] = sample1
        header["region_trace_start"] = trace0
        header["region_trace_stop"] = trace1
        header["region_line_start"] = line0
        header["region_line_stop"] = line1
        return DatasetRecord(
            dataset_id=self.dataset_id,
            name=region_name,
            filename=self.filename,
            volume=cropped,
            tw_ns=self.tw_ns,
            dt_ns=self.dt_ns,
            fs_hz=self.fs_hz,
            attribute=self.attribute,
            transform_name=self.transform_name,
            header=header,
            source_path=self.source_path,
            import_params=dict(self.import_params),
        )
