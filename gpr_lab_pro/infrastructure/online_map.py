from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class OnlineMapConfig:
    provider: str = "osm"
    amap_js_key: str = ""
    amap_security_js_code: str = ""


class OnlineMapConfigStore:
    FILE_NAME = "online_map.local.json"

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
            provider=str(payload.get("provider", "osm") or "osm"),
            amap_js_key=str(payload.get("amap_js_key", "") or ""),
            amap_security_js_code=str(payload.get("amap_security_js_code", "") or ""),
        )

    @classmethod
    def save(cls, config: OnlineMapConfig) -> Path:
        path = cls.config_path()
        path.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        return path
