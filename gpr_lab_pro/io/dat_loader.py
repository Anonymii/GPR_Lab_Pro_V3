from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct


SAMPLES_MAP = [128, 256, 512, 1024, 2048, 4096, 8192]

DAT_FILE_HEADER_SIZE = 350
DAT_FRAME_HEADER_SIZE = 50

FRAME_HEADER_OFFSET_HEADER_SIZE = 0
FRAME_HEADER_OFFSET_FLAG = 2
FRAME_HEADER_OFFSET_TX_CHANNEL = 4
FRAME_HEADER_OFFSET_RX_CHANNEL = 6
FRAME_HEADER_OFFSET_GPS_STATUS = 10
FRAME_HEADER_OFFSET_GPS_PPS_COUNT = 11
FRAME_HEADER_OFFSET_GPS_TIMESTAMP = 15
FRAME_HEADER_OFFSET_FRAME_SIZE = 23
FRAME_HEADER_OFFSET_RX_SWITCH = 27
FRAME_HEADER_OFFSET_ENCODER_DIRECTION = 28
FRAME_HEADER_OFFSET_BOARD_VERSION = 29
FRAME_HEADER_OFFSET_LONGITUDE = 30
FRAME_HEADER_OFFSET_LATITUDE = 38
FRAME_HEADER_OFFSET_RESERVED = 46


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


@dataclass(frozen=True)
class DatFrameHeader:
    header_length: int
    flag: int
    tx_channel: int
    rx_channel: int
    gps_status: int
    gps_pps_count: int
    gps_timestamp: int
    frame_size: int
    rx_switch: int
    encoder_direction: int
    board_version: int
    longitude: float
    latitude: float
    reserved: bytes


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


def read_dat_frame_header(path: str | Path, position: int) -> DatFrameHeader:
    path = Path(path)
    with path.open("rb") as fh:
        return read_dat_frame_header_from_handle(fh, position)


def read_dat_frame_header_from_handle(fh, position: int) -> DatFrameHeader:
    fh.seek(position)
    header = fh.read(DAT_FRAME_HEADER_SIZE)
    if len(header) < DAT_FRAME_HEADER_SIZE:
        raise ValueError(f"Incomplete frame header at byte offset {position}.")
    return DatFrameHeader(
        header_length=struct.unpack("<H", header[FRAME_HEADER_OFFSET_HEADER_SIZE:FRAME_HEADER_OFFSET_HEADER_SIZE + 2])[0],
        flag=struct.unpack("<H", header[FRAME_HEADER_OFFSET_FLAG:FRAME_HEADER_OFFSET_FLAG + 2])[0],
        tx_channel=struct.unpack("<H", header[FRAME_HEADER_OFFSET_TX_CHANNEL:FRAME_HEADER_OFFSET_TX_CHANNEL + 2])[0],
        rx_channel=struct.unpack("<I", header[FRAME_HEADER_OFFSET_RX_CHANNEL:FRAME_HEADER_OFFSET_RX_CHANNEL + 4])[0],
        gps_status=struct.unpack("<B", header[FRAME_HEADER_OFFSET_GPS_STATUS:FRAME_HEADER_OFFSET_GPS_STATUS + 1])[0],
        gps_pps_count=struct.unpack("<i", header[FRAME_HEADER_OFFSET_GPS_PPS_COUNT:FRAME_HEADER_OFFSET_GPS_PPS_COUNT + 4])[0],
        gps_timestamp=struct.unpack("<q", header[FRAME_HEADER_OFFSET_GPS_TIMESTAMP:FRAME_HEADER_OFFSET_GPS_TIMESTAMP + 8])[0],
        frame_size=struct.unpack("<i", header[FRAME_HEADER_OFFSET_FRAME_SIZE:FRAME_HEADER_OFFSET_FRAME_SIZE + 4])[0],
        rx_switch=struct.unpack("<B", header[FRAME_HEADER_OFFSET_RX_SWITCH:FRAME_HEADER_OFFSET_RX_SWITCH + 1])[0],
        encoder_direction=struct.unpack("<B", header[FRAME_HEADER_OFFSET_ENCODER_DIRECTION:FRAME_HEADER_OFFSET_ENCODER_DIRECTION + 1])[0],
        board_version=struct.unpack("<B", header[FRAME_HEADER_OFFSET_BOARD_VERSION:FRAME_HEADER_OFFSET_BOARD_VERSION + 1])[0],
        longitude=struct.unpack("<d", header[FRAME_HEADER_OFFSET_LONGITUDE:FRAME_HEADER_OFFSET_LONGITUDE + 8])[0],
        latitude=struct.unpack("<d", header[FRAME_HEADER_OFFSET_LATITUDE:FRAME_HEADER_OFFSET_LATITUDE + 8])[0],
        reserved=header[FRAME_HEADER_OFFSET_RESERVED:DAT_FRAME_HEADER_SIZE],
    )
