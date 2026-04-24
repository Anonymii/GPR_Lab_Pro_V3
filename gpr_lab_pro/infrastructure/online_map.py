from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path


@dataclass
class OnlineMapConfig:
    provider: str = "amap"
    amap_js_key: str = ""
    amap_security_js_code: str = ""


@dataclass(frozen=True)
class OfflineTileCoverage:
    min_zoom: int
    max_zoom: int
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    @property
    def center_lat(self) -> float:
        return (self.min_lat + self.max_lat) * 0.5

    @property
    def center_lon(self) -> float:
        return (self.min_lon + self.max_lon) * 0.5


class OnlineMapConfigStore:
    FILE_NAME = "online_map.local.json"
    OFFLINE_TILES_DIR = "offline_tiles"
    _OFFLINE_TILE_RE = re.compile(r"osm_100-l-3-(\d+)-(\d+)-(\d+)\.png$", re.IGNORECASE)

    @classmethod
    def runtime_root(cls) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parents[2]

    @classmethod
    def config_path(cls) -> Path:
        return cls.runtime_root() / cls.FILE_NAME

    @classmethod
    def load(cls) -> OnlineMapConfig:
        path = cls.config_path()
        if not path.exists():
            return OnlineMapConfig()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return OnlineMapConfig()
        if not isinstance(payload, dict):
            return OnlineMapConfig()
        return OnlineMapConfig(
            provider=str(payload.get("provider", "amap") or "amap"),
            amap_js_key=str(payload.get("amap_js_key", "") or ""),
            amap_security_js_code=str(payload.get("amap_security_js_code", "") or ""),
        )

    @classmethod
    def save(cls, config: OnlineMapConfig) -> Path:
        path = cls.config_path()
        path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def offline_tiles_roots(cls) -> list[Path]:
        runtime_root = cls.runtime_root()
        candidates = [
            runtime_root / cls.OFFLINE_TILES_DIR,
            runtime_root.parent / cls.OFFLINE_TILES_DIR,
        ]
        roots: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if normalized in seen:
                continue
            seen.add(normalized)
            if candidate.exists():
                roots.append(candidate)
        return roots

    @classmethod
    def resolve_offline_tile_path(cls, zoom: int, tile_x: int, tile_y: int) -> Path | None:
        file_name = f"osm_100-l-3-{int(zoom)}-{int(tile_x)}-{int(tile_y)}.png"
        for root in cls.offline_tiles_roots():
            candidate = root / file_name
            if candidate.exists():
                return candidate
        return None

    @classmethod
    @lru_cache(maxsize=1)
    def offline_tiles_coverage(cls) -> OfflineTileCoverage | None:
        by_zoom: dict[int, dict[str, list[int]]] = {}
        for root in cls.offline_tiles_roots():
            for tile_path in root.glob("osm_100-l-3-*-*-*.png"):
                match = cls._OFFLINE_TILE_RE.match(tile_path.name)
                if not match:
                    continue
                zoom, tile_x, tile_y = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
                bucket = by_zoom.setdefault(zoom, {"x": [], "y": []})
                bucket["x"].append(tile_x)
                bucket["y"].append(tile_y)
        if not by_zoom:
            return None
        min_zoom = min(by_zoom)
        max_zoom = max(by_zoom)
        bucket = by_zoom[max_zoom]
        min_x, max_x = min(bucket["x"]), max(bucket["x"])
        min_y, max_y = min(bucket["y"]), max(bucket["y"])
        min_lon = cls._tile_x_to_lon(min_x, max_zoom)
        max_lon = cls._tile_x_to_lon(max_x + 1, max_zoom)
        max_lat = cls._tile_y_to_lat(min_y, max_zoom)
        min_lat = cls._tile_y_to_lat(max_y + 1, max_zoom)
        return OfflineTileCoverage(
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            min_lat=min_lat,
            max_lat=max_lat,
            min_lon=min_lon,
            max_lon=max_lon,
        )

    @staticmethod
    def _tile_x_to_lon(tile_x: int, zoom: int) -> float:
        return (float(tile_x) / float(2**zoom)) * 360.0 - 180.0

    @staticmethod
    def _tile_y_to_lat(tile_y: int, zoom: int) -> float:
        n = math.pi - (2.0 * math.pi * float(tile_y)) / float(2**zoom)
        return math.degrees(math.atan(math.sinh(n)))
