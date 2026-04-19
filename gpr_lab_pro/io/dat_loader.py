from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct


SAMPLES_MAP = [128, 256, 512, 1024, 2048, 4096, 8192]


@dataclass(frozen=True)
class DatFileHeader:
    magic_number: str
    offset_binary: int
    sample_idx: int
    frame_header_size: int
    start_frequency_hz: int
    end_frequency_hz: int
    sample_count: int
    file_size: int


def read_dat_header(path: str | Path) -> DatFileHeader:
    """Read the fixed header fields used by MATLAB multi.m."""
    path = Path(path)
    with path.open("rb") as fh:
        magic = fh.read(6).decode("ascii", errors="ignore")
        fh.seek(6)
        offset_binary = struct.unpack("<h", fh.read(2))[0]
        fh.seek(20)
        sample_idx = struct.unpack("<b", fh.read(1))[0]
        fh.seek(offset_binary)
        frame_header_size = struct.unpack("<h", fh.read(2))[0]
        fh.seek(12)
        start_frequency = struct.unpack("<i", fh.read(4))[0] * 10**6
        fh.seek(16)
        end_frequency = struct.unpack("<i", fh.read(4))[0] * 10**6
        fh.seek(0, 2)
        file_size = fh.tell()

    sample_count = SAMPLES_MAP[sample_idx] if 0 <= sample_idx < len(SAMPLES_MAP) else 2048
    return DatFileHeader(
        magic_number=magic,
        offset_binary=offset_binary,
        sample_idx=sample_idx,
        frame_header_size=frame_header_size,
        start_frequency_hz=start_frequency,
        end_frequency_hz=end_frequency,
        sample_count=sample_count,
        file_size=file_size,
    )
