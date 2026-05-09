from __future__ import annotations

import http.server
import logging
import math
import re
import socketserver
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtQml, QtQuickWidgets, QtWidgets

from gpr_lab_pro.infrastructure.online_map import OfflineTileCoverage, OnlineMapConfigStore

logger = logging.getLogger("gpr.map")


def _qt_location_cache_directory(name: str) -> str:
    cache_dir = OnlineMapConfigStore.runtime_root() / "cache" / name
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir).replace("\\", "/")


def _is_mainland_china_coordinate(latitude: float, longitude: float) -> bool:
    return 72.004 <= longitude <= 137.8347 and 0.8293 <= latitude <= 55.8271


def _transform_latitude_offset(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_longitude_offset(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def _wgs84_to_gcj02(latitude: float, longitude: float) -> tuple[float, float]:
    if not _is_mainland_china_coordinate(latitude, longitude):
        return latitude, longitude
    semi_major_axis = 6378245.0
    eccentricity_squared = 0.00669342162296594323
    d_lat = _transform_latitude_offset(longitude - 105.0, latitude - 35.0)
    d_lon = _transform_longitude_offset(longitude - 105.0, latitude - 35.0)
    rad_lat = latitude / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - eccentricity_squared * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((semi_major_axis * (1 - eccentricity_squared)) / (magic * sqrt_magic) * math.pi)
    d_lon = (d_lon * 180.0) / (semi_major_axis / sqrt_magic * math.cos(rad_lat) * math.pi)
    return latitude + d_lat, longitude + d_lon


def _transform_sample_coordinate(sample: dict[str, object]) -> dict[str, object]:
    transformed = dict(sample)
    latitude = transformed.get("latitude")
    longitude = transformed.get("longitude")
    if latitude is None or longitude is None:
        return transformed
    try:
        gcj_lat, gcj_lon = _wgs84_to_gcj02(float(latitude), float(longitude))
    except (TypeError, ValueError):
        return transformed
    transformed["latitude"] = gcj_lat
    transformed["longitude"] = gcj_lon
    return transformed


def _transform_files_to_gcj02(files: list[dict[str, object]]) -> list[dict[str, object]]:
    transformed_files: list[dict[str, object]] = []
    for file_item in files:
        transformed_file = dict(file_item)
        navigation_samples = file_item.get("navigation_samples", [])
        if isinstance(navigation_samples, list):
            transformed_file["navigation_samples"] = [
                _transform_sample_coordinate(sample) if isinstance(sample, dict) else sample for sample in navigation_samples
            ]
        regions = file_item.get("regions", [])
        if isinstance(regions, list):
            transformed_regions: list[object] = []
            for region in regions:
                if not isinstance(region, dict):
                    transformed_regions.append(region)
                    continue
                transformed_region = dict(region)
                region_samples = region.get("navigation_samples", [])
                if isinstance(region_samples, list):
                    transformed_region["navigation_samples"] = [
                        _transform_sample_coordinate(sample) if isinstance(sample, dict) else sample for sample in region_samples
                    ]
                transformed_regions.append(transformed_region)
            transformed_file["regions"] = transformed_regions
        transformed_files.append(transformed_file)
    return transformed_files


def _attach_quick_widget_diagnostics(
    quick_widget: QtQuickWidgets.QQuickWidget,
    *,
    widget_name: str,
    qml_path: Path,
) -> None:
    logger.info("%s: loading QML from %s", widget_name, qml_path)

    def _on_status_changed(status) -> None:
        try:
            status_value = getattr(status, "value", status)
            status_name = getattr(status, "name", None) or {
                QtQuickWidgets.QQuickWidget.Null.value: "Null",
                QtQuickWidgets.QQuickWidget.Ready.value: "Ready",
                QtQuickWidgets.QQuickWidget.Loading.value: "Loading",
                QtQuickWidgets.QQuickWidget.Error.value: "Error",
            }.get(status_value, str(status_value))
            logger.info("%s: QQuickWidget status changed to %s", widget_name, status_name)
            if status_value == QtQuickWidgets.QQuickWidget.Error.value:
                errors = [str(item.toString()) for item in quick_widget.errors()]
                if errors:
                    for error_text in errors:
                        logger.error("%s: QML error: %s", widget_name, error_text)
                else:
                    logger.error("%s: QQuickWidget entered Error state without detailed errors", widget_name)
        except Exception:
            logger.exception("%s: failed to process QQuickWidget status change %r", widget_name, status)

    quick_widget.statusChanged.connect(_on_status_changed)


class _OfflineTileRequestHandler(http.server.BaseHTTPRequestHandler):
    server: "_OfflineTileHTTPServer"

    def do_GET(self) -> None:  # noqa: N802
        payload = self.server.tile_server.load_tile_payload(self.path)
        if payload is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args) -> None:
        return


class _OfflineTileHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, *, tile_server: "OfflineTileServer"):
        super().__init__(server_address, RequestHandlerClass)
        self.tile_server = tile_server


class OfflineTileServer(QtCore.QObject):
    _MEMORY_CACHE_LIMIT = 384
    _REQUEST_RE = re.compile(r"^(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)(?:@[0-9]+x)?\.(?:png|jpg|jpeg|webp)$", re.IGNORECASE)

    def __init__(self, roots: Path | list[Path], parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        raw_roots = roots if isinstance(roots, list) else [roots]
        self._roots = [Path(root) for root in raw_roots if root is not None]
        self._root = self._roots[0] if self._roots else Path()
        self._available_zooms = self._scan_available_zooms()
        self._httpd: _OfflineTileHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._base_url = ""
        self.request_count = 0
        self.hit_count = 0
        self.miss_count = 0
        self.last_request_path = ""
        self._payload_cache: OrderedDict[str, bytes] = OrderedDict()
        self._logged_missing_requests: set[str] = set()

    @property
    def base_url(self) -> str:
        return self._base_url

    def start(self) -> str:
        if self._httpd is not None and self._thread is not None and self._thread.is_alive():
            return self._base_url
        self._httpd = _OfflineTileHTTPServer(("127.0.0.1", 0), _OfflineTileRequestHandler, tile_server=self)
        port = int(self._httpd.server_address[1])
        self._base_url = f"http://127.0.0.1:{port}/tiles/"
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="OfflineTileServer", daemon=True)
        self._thread.start()
        logger.info("OfflineTileServer started: roots=%s base_url=%s", [str(root) for root in self._roots], self._base_url)
        return self._base_url

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError:
                pass
        if self._base_url:
            logger.info(
                "OfflineTileServer stopped: requests=%s hits=%s misses=%s last_request=%s",
                self.request_count,
                self.hit_count,
                self.miss_count,
                self.last_request_path,
            )
        self._httpd = None
        self._thread = None
        self._base_url = ""
        self._payload_cache.clear()

    def load_tile_payload(self, request_path: str) -> bytes | None:
        normalized = request_path.split("?", 1)[0].strip("/")
        cached = self._payload_cache.get(normalized)
        if cached is not None:
            self._payload_cache.move_to_end(normalized)
            self.request_count += 1
            self.hit_count += 1
            self.last_request_path = str(request_path)
            return cached
        payload = self.resolve_request_payload(request_path)
        if payload is None:
            self._log_missing_request(request_path)
            return None
        self._remember_payload(normalized, payload)
        return payload

    def resolve_request_payload(self, request_path: str) -> bytes | None:
        tile_path = self.resolve_request_path(request_path)
        if tile_path is not None and tile_path.exists():
            try:
                payload = tile_path.read_bytes()
            except OSError:
                logger.exception("OfflineTileServer failed reading tile for %s", request_path)
                return None
            return payload or None
        return self._render_overzoom_payload(request_path)

    def _render_overzoom_payload(self, request_path: str) -> bytes | None:
        key = self._parse_request_key(request_path)
        if key is None:
            return None
        zoom, tile_x, tile_y = key
        source_zoom = self._best_source_zoom(zoom)
        if source_zoom is None or source_zoom >= zoom:
            return None
        scale = 2 ** (zoom - source_zoom)
        parent_x = tile_x // scale
        parent_y = tile_y // scale
        parent_path = self._find_tile_path(source_zoom, parent_x, parent_y)
        if parent_path is None:
            return None
        image = QtGui.QImage(str(parent_path))
        if image.isNull():
            logger.warning("OfflineTileServer failed loading parent tile for overzoom: %s", parent_path)
            return None
        tile_width = max(image.width(), 1)
        tile_height = max(image.height(), 1)
        crop_x = int((tile_x % scale) * tile_width / scale)
        crop_y = int((tile_y % scale) * tile_height / scale)
        crop_w = max(int(tile_width / scale), 1)
        crop_h = max(int(tile_height / scale), 1)
        cropped = image.copy(crop_x, crop_y, crop_w, crop_h)
        if cropped.isNull():
            return None
        rendered = cropped.scaled(tile_width, tile_height, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        if not rendered.save(buffer, "PNG"):
            return None
        self.hit_count += 1
        logger.debug(
            "OfflineTileServer overzoom tile: request=%s source=%s/%s/%s parent=%s",
            request_path,
            source_zoom,
            parent_x,
            parent_y,
            parent_path,
        )
        return bytes(buffer.data())

    def _best_source_zoom(self, requested_zoom: int) -> int | None:
        candidates = [zoom for zoom in self._available_zooms if zoom < int(requested_zoom)]
        if not candidates:
            return None
        return max(candidates)

    def _scan_available_zooms(self) -> set[int]:
        zooms: set[int] = set()
        pattern = re.compile(r"^osm_100-l-\d+-(\d+)-\d+-\d+\.png$", re.IGNORECASE)
        for root in self._roots:
            for path in root.glob("osm_100-l-*-*-*.png"):
                match = pattern.match(path.name)
                if match is not None:
                    zooms.add(int(match.group(1)))
        return zooms

    def resolve_request_path(self, request_path: str) -> Path | None:
        self.request_count += 1
        self.last_request_path = str(request_path)
        key = self._parse_request_key(request_path)
        if key is None:
            return None
        zoom, tile_x, tile_y = key
        return self._find_tile_path(zoom, tile_x, tile_y)

    def _find_tile_path(self, zoom: int, tile_x: int, tile_y: int) -> Path | None:
        for root in self._roots:
            for map_id in (3, 8, 1):
                candidate = root / f"osm_100-l-{map_id}-{zoom}-{tile_x}-{tile_y}.png"
                if candidate.exists():
                    self.hit_count += 1
                    return candidate
            matches = sorted(root.glob(f"osm_100-l-*-{zoom}-{tile_x}-{tile_y}.png"))
            if matches:
                self.hit_count += 1
                return matches[0]
        return None

    @classmethod
    def _parse_request_key(cls, request_path: str) -> tuple[int, int, int] | None:
        normalized = request_path.split("?", 1)[0].strip("/")
        if normalized.startswith("tiles/"):
            normalized = normalized[6:]
        match = cls._REQUEST_RE.match(normalized)
        if match is None:
            return None
        return (
            int(match.group("z")),
            int(match.group("x")),
            int(match.group("y")),
        )

    def _remember_payload(self, key: str, payload: bytes) -> None:
        self._payload_cache[key] = payload
        self._payload_cache.move_to_end(key)
        while len(self._payload_cache) > self._MEMORY_CACHE_LIMIT:
            self._payload_cache.popitem(last=False)

    def _log_missing_request(self, request_path: str) -> None:
        normalized = request_path.split("?", 1)[0].strip("/")
        if normalized in self._logged_missing_requests:
            return
        self.miss_count += 1
        if len(self._logged_missing_requests) >= 20:
            return
        self._logged_missing_requests.add(normalized)
        logger.warning("OfflineTileServer missing tile request: %s roots=%s", normalized, [str(root) for root in self._roots])


class _OnlineTileRequestHandler(http.server.BaseHTTPRequestHandler):
    server: "_OnlineTileHTTPServer"

    def do_GET(self) -> None:  # noqa: N802
        payload = self.server.tile_server.load_tile_payload(self.path)
        if payload is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args) -> None:
        return


class _OnlineTileHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, *, tile_server: "OnlineTileServer"):
        super().__init__(server_address, RequestHandlerClass)
        self.tile_server = tile_server


class OnlineTileServer(QtCore.QObject):
    _MEMORY_CACHE_LIMIT = 384
    _REQUEST_RE = re.compile(r"^(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)(?:@[0-9]+x)?\.(?:png|jpg|jpeg|webp)$", re.IGNORECASE)
    _MAP_TILE_URLS_AMAP = (
        "https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scl=1&style=7&x={x}&y={y}&z={z}",
        "https://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scl=1&style=7&x={x}&y={y}&z={z}",
        "https://webrd03.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scl=1&style=7&x={x}&y={y}&z={z}",
        "http://wprd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scl=1&style=7&x={x}&y={y}&z={z}",
        "http://wprd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scl=1&style=7&x={x}&y={y}&z={z}",
        "http://wprd03.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scl=1&style=7&x={x}&y={y}&z={z}",
    )

    def __init__(self, config, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._cache_root = OnlineMapConfigStore.runtime_root() / "cache"
        self._httpd: _OnlineTileHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._base_url = ""
        self.request_count = 0
        self.hit_count = 0
        self.miss_count = 0
        self.fetch_count = 0
        self.last_request_path = ""
        self.last_error = ""
        self._last_cleanup = 0.0
        self._payload_cache: OrderedDict[tuple[int, int, int], bytes] = OrderedDict()
        self._logged_failed_requests: set[tuple[int, int, int]] = set()

    @property
    def base_url(self) -> str:
        return self._base_url

    def set_online_map_config(self, config) -> None:
        self._config = config

    def set_cache_root(self, cache_root_path: str) -> None:
        root = Path(cache_root_path).resolve() if cache_root_path else OnlineMapConfigStore.runtime_root()
        self._cache_root = root / "cache"

    def start(self) -> str:
        if self._httpd is not None and self._thread is not None and self._thread.is_alive():
            return self._base_url
        self._httpd = _OnlineTileHTTPServer(("127.0.0.1", 0), _OnlineTileRequestHandler, tile_server=self)
        port = int(self._httpd.server_address[1])
        self._base_url = f"http://127.0.0.1:{port}/tiles/"
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="OnlineTileServer", daemon=True)
        self._thread.start()
        logger.info("OnlineTileServer started: base_url=%s provider=%s cache_root=%s", self._base_url, getattr(self._config, "provider", ""), self._cache_root)
        return self._base_url

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError:
                pass
        if self._base_url:
            logger.info(
                "OnlineTileServer stopped: requests=%s hits=%s misses=%s fetched=%s last_request=%s last_error=%s",
                self.request_count,
                self.hit_count,
                self.miss_count,
                self.fetch_count,
                self.last_request_path,
                self.last_error,
            )
        self._httpd = None
        self._thread = None
        self._base_url = ""
        self._payload_cache.clear()

    def load_tile_payload(self, request_path: str) -> bytes | None:
        self.request_count += 1
        self.last_request_path = str(request_path)
        key = self._parse_request_key(request_path)
        if key is None:
            self.miss_count += 1
            return None
        cached = self._payload_cache.get(key)
        if cached is not None:
            self._payload_cache.move_to_end(key)
            self.hit_count += 1
            return cached
        cache_path = self._tile_cache_path(key)
        if cache_path.exists():
            try:
                payload = cache_path.read_bytes()
            except OSError:
                payload = b""
            if payload:
                self.hit_count += 1
                self._remember_payload(key, payload)
                return payload
        payload = self._fetch_remote_tile(key)
        if not payload:
            self.miss_count += 1
            if key not in self._logged_failed_requests and len(self._logged_failed_requests) < 20:
                self._logged_failed_requests.add(key)
                logger.warning("OnlineTileServer failed request %s: %s", key, self.last_error)
            return None
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(payload)
        except OSError:
            pass
        self.fetch_count += 1
        self._remember_payload(key, payload)
        self._cleanup_cache_if_needed()
        return payload

    def _parse_request_key(self, request_path: str) -> tuple[int, int, int] | None:
        normalized = request_path.split("?", 1)[0].strip("/")
        if normalized.startswith("tiles/"):
            normalized = normalized[6:]
        match = self._REQUEST_RE.match(normalized)
        if match is None:
            return None
        return (
            int(match.group("z")),
            int(match.group("x")),
            int(match.group("y")),
        )

    def _tile_cache_path(self, key: tuple[int, int, int]) -> Path:
        zoom, tile_x, tile_y = key
        provider = (getattr(self._config, "provider", "") or "amap").strip().lower() or "amap"
        return self._cache_root / "map_tiles_online" / provider / str(zoom) / str(tile_x) / f"{tile_y}.png"

    def _fetch_remote_tile(self, key: tuple[int, int, int]) -> bytes | None:
        zoom, tile_x, tile_y = key
        provider = (getattr(self._config, "provider", "") or "amap").strip().lower()
        if provider != "amap":
            self.last_error = f"不支持的在线地图提供器: {provider}"
            return None
        errors: list[str] = []
        for template in self._MAP_TILE_URLS_AMAP:
            url = template.format(z=zoom, x=tile_x, y=tile_y)
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                    "Referer": "https://lbs.amap.com/",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=8.0) as response:
                    payload = response.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                errors.append(f"{url}: {exc}")
                continue
            if payload:
                self.last_error = ""
                logger.info("OnlineTileServer fetched remote tile %s via %s", key, url)
                return payload
            errors.append(f"{url}: empty payload")
        self.last_error = " | ".join(errors[-3:]) if errors else "unknown fetch error"
        return None

    def _cleanup_cache_if_needed(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 60.0:
            return
        self._last_cleanup = now
        root = self._cache_root / "map_tiles_online"
        if not root.exists():
            return
        files = [path for path in root.rglob("*.png") if path.is_file()]
        if len(files) <= 5000:
            return
        files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in files[4000:]:
            try:
                stale.unlink()
            except OSError:
                continue

    def _remember_payload(self, key: tuple[int, int, int], payload: bytes) -> None:
        self._payload_cache[key] = payload
        self._payload_cache.move_to_end(key)
        while len(self._payload_cache) > self._MEMORY_CACHE_LIMIT:
            self._payload_cache.popitem(last=False)


class OverviewOnlineBridge(QtCore.QObject):
    mapReady = QtCore.Signal()
    mapStateChanged = QtCore.Signal(float, float, float)
    mapTapped = QtCore.Signal(float, float)
    onlineTileHostChanged = QtCore.Signal()
    onlineTileCacheDirectoryChanged = QtCore.Signal()
    onlineMinZoomChanged = QtCore.Signal()
    onlineMaxZoomChanged = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._online_tile_host = ""
        self._online_tile_cache_directory = ""
        self._online_min_zoom = 3
        self._online_max_zoom = 19

    @QtCore.Property(str, notify=onlineTileHostChanged)
    def onlineTileHost(self) -> str:
        return self._online_tile_host

    def set_online_tile_host(self, value: str) -> None:
        normalized = str(value or "")
        if normalized == self._online_tile_host:
            return
        self._online_tile_host = normalized
        self.onlineTileHostChanged.emit()

    @QtCore.Property(str, notify=onlineTileCacheDirectoryChanged)
    def onlineTileCacheDirectory(self) -> str:
        return self._online_tile_cache_directory

    def set_online_tile_cache_directory(self, value: str) -> None:
        normalized = str(value or "").replace("\\", "/")
        if normalized == self._online_tile_cache_directory:
            return
        self._online_tile_cache_directory = normalized
        self.onlineTileCacheDirectoryChanged.emit()

    @QtCore.Property(int, notify=onlineMinZoomChanged)
    def onlineMinZoom(self) -> int:
        return int(self._online_min_zoom)

    def set_online_min_zoom(self, value: int) -> None:
        normalized = int(value)
        if normalized == self._online_min_zoom:
            return
        self._online_min_zoom = normalized
        self.onlineMinZoomChanged.emit()

    @QtCore.Property(int, notify=onlineMaxZoomChanged)
    def onlineMaxZoom(self) -> int:
        return int(self._online_max_zoom)

    def set_online_max_zoom(self, value: int) -> None:
        normalized = int(value)
        if normalized == self._online_max_zoom:
            return
        self._online_max_zoom = normalized
        self.onlineMaxZoomChanged.emit()

    @QtCore.Slot()
    def notifyMapReady(self) -> None:
        self.mapReady.emit()

    @QtCore.Slot(float, float, float)
    def notifyMapState(self, latitude: float, longitude: float, zoom: float) -> None:
        self.mapStateChanged.emit(float(latitude), float(longitude), float(zoom))

    @QtCore.Slot(float, float)
    def notifyMapTapped(self, x: float, y: float) -> None:
        self.mapTapped.emit(float(x), float(y))


class OverviewQuickBridge(QtCore.QObject):
    mapReady = QtCore.Signal()
    mapStateChanged = QtCore.Signal(float, float, float)
    mapTapped = QtCore.Signal(float, float)
    offlineDirectoryChanged = QtCore.Signal()
    offlineTileHostChanged = QtCore.Signal()
    offlineTileCacheDirectoryChanged = QtCore.Signal()
    offlineMinZoomChanged = QtCore.Signal()
    offlineMaxZoomChanged = QtCore.Signal()
    sceneBoundsChanged = QtCore.Signal(float, float, float, float)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._offline_directory = ""
        self._offline_tile_host = ""
        self._offline_tile_cache_directory = ""
        self._offline_min_zoom = 9
        self._offline_max_zoom = 15

    @QtCore.Property(str, notify=offlineDirectoryChanged)
    def offlineDirectory(self) -> str:
        return self._offline_directory

    def set_offline_directory(self, value: str) -> None:
        normalized = str(value or "")
        if normalized == self._offline_directory:
            return
        self._offline_directory = normalized
        self.offlineDirectoryChanged.emit()

    @QtCore.Property(str, notify=offlineTileHostChanged)
    def offlineTileHost(self) -> str:
        return self._offline_tile_host

    def set_offline_tile_host(self, value: str) -> None:
        normalized = str(value or "")
        if normalized == self._offline_tile_host:
            return
        self._offline_tile_host = normalized
        self.offlineTileHostChanged.emit()

    @QtCore.Property(str, notify=offlineTileCacheDirectoryChanged)
    def offlineTileCacheDirectory(self) -> str:
        return self._offline_tile_cache_directory

    def set_offline_tile_cache_directory(self, value: str) -> None:
        normalized = str(value or "").replace("\\", "/")
        if normalized == self._offline_tile_cache_directory:
            return
        self._offline_tile_cache_directory = normalized
        self.offlineTileCacheDirectoryChanged.emit()

    @QtCore.Property(int, notify=offlineMinZoomChanged)
    def offlineMinZoom(self) -> int:
        return int(self._offline_min_zoom)

    def set_offline_min_zoom(self, value: int) -> None:
        normalized = int(value)
        if normalized == self._offline_min_zoom:
            return
        self._offline_min_zoom = normalized
        self.offlineMinZoomChanged.emit()

    @QtCore.Property(int, notify=offlineMaxZoomChanged)
    def offlineMaxZoom(self) -> int:
        return int(self._offline_max_zoom)

    def set_offline_max_zoom(self, value: int) -> None:
        normalized = int(value)
        if normalized == self._offline_max_zoom:
            return
        self._offline_max_zoom = normalized
        self.offlineMaxZoomChanged.emit()

    @QtCore.Slot()
    def notifyMapReady(self) -> None:
        self.mapReady.emit()

    @QtCore.Slot(float, float, float)
    def notifyMapState(self, latitude: float, longitude: float, zoom: float) -> None:
        self.mapStateChanged.emit(float(latitude), float(longitude), float(zoom))

    @QtCore.Slot(float, float)
    def notifyMapTapped(self, x: float, y: float) -> None:
        self.mapTapped.emit(float(x), float(y))


class OverviewOverlayWidget(QtWidgets.QWidget):
    region_activated = QtCore.Signal(str)
    point_selected = QtCore.Signal(str, int, int)

    _TILE_SIZE = 256
    _MAX_RENDER_NAV_POINTS = 900
    _MAX_PREVIEW_QUADS = 2400
    _MAX_OVERVIEW_TEXTURE_WIDTH = 4096
    _MAX_OVERVIEW_TEXTURE_HEIGHT = 2160
    _MAX_OVERVIEW_TEXTURE_PIXELS = 3_000_000
    _MAX_CHANNEL_SWEEP_ROWS = 28
    _MAX_CHANNEL_SWEEP_COLUMNS = 260
    _MAX_CHANNEL_SWEEP_CELLS = 6000

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._files: list[dict[str, object]] = []
        self._prepared_regions: list[dict[str, object]] = []
        self._active_region_id = ""
        self._active_file_id = ""
        self._active_trace = 0
        self._layout_rects: list[tuple[str, QtGui.QPainterPath, dict[str, object]]] = []
        self._center_lat = 0.0
        self._center_lon = 0.0
        self._zoom = 15.0
        self._center_world_x = 0.0
        self._center_world_y = 0.0
        self._pending_map_state: tuple[float, float, float] | None = None
        self._layout_cache_key: tuple[float, float, float, int, int, int, bool] | None = None
        self._layout_cache: list[tuple[str, QtGui.QPainterPath, dict[str, object]]] = []
        self._preview_lod_cache: OrderedDict[tuple[int, int, int], QtGui.QImage] = OrderedDict()
        self._raster_overlay_cache: OrderedDict[tuple[object, ...], dict[str, object]] = OrderedDict()
        self._interaction_timer = QtCore.QTimer(self)
        self._interaction_timer.setSingleShot(True)
        self._interaction_timer.setInterval(650)
        self._interaction_timer.timeout.connect(self._finish_map_interaction)

    def clear_scene(self) -> None:
        self._files = []
        self._prepared_regions = []
        self._active_region_id = ""
        self._active_file_id = ""
        self._active_trace = 0
        self._layout_rects = []
        self._clear_layout_cache()
        self._preview_lod_cache.clear()
        self._raster_overlay_cache.clear()
        self.update()

    def set_scene(
        self,
        files: list[dict[str, object]],
        *,
        active_region_id: str,
        active_file_id: str,
        active_trace: int = 0,
        active_region_name: str = "",
        active_interface_name: str = "",
    ) -> None:
        del active_region_name, active_interface_name
        self._files = list(files)
        self._prepared_regions = self._prepare_regions(self._files)
        self._active_region_id = active_region_id
        self._active_file_id = active_file_id
        self._active_trace = int(active_trace)
        self._clear_layout_cache()
        self._preview_lod_cache.clear()
        self._raster_overlay_cache.clear()
        self.update()

    def set_map_state(self, latitude: float, longitude: float, zoom: float) -> None:
        state = (float(latitude), float(longitude), float(zoom))
        if (
            self._pending_map_state == state
            or (
                self._pending_map_state is None
                and abs(state[0] - self._center_lat) < 1e-12
                and abs(state[1] - self._center_lon) < 1e-12
                and abs(state[2] - self._zoom) < 1e-9
            )
        ):
            return
        self._center_lat, self._center_lon, self._zoom = state
        self._center_world_x, self._center_world_y = self._geo_to_world(self._center_lat, self._center_lon)
        self._pending_map_state = None
        self._clear_layout_cache()
        if not self.isVisible():
            self.show()
            self.raise_()
        self._interaction_timer.start()
        self.update()

    def handle_tap(self, point: QtCore.QPointF) -> None:
        region = self._region_at(point)
        if region is None:
            return
        region_id, _path, item = region
        self.region_activated.emit(region_id)
        samples = item.get("navigation_samples", [])
        if not samples:
            return
        target = min(
            samples,
            key=lambda sample: (float(sample.get("screen_x", 0.0)) - point.x()) ** 2
            + (float(sample.get("screen_y", 0.0)) - point.y()) ** 2,
        )
        self.point_selected.emit(region_id, int(target.get("trace_index", 0)), 0)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        fast_mode = self._interaction_timer.isActive()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, not fast_mode)
        canvas_rect = QtCore.QRectF(self.rect())
        raster_region_ids: set[str] = set()
        for prepared in self._prepared_regions:
            region_id = str(prepared.get("region_id", "") or "")
            if self._draw_cached_raster_overlay(
                painter,
                prepared,
                canvas_rect,
                fast_mode=fast_mode,
                allow_build=not fast_mode,
            ):
                raster_region_ids.add(region_id)
        if fast_mode:
            return
        self._layout_rects = self._compute_layout(self.rect())
        for region_id, path, item in self._layout_rects:
            preview_image = item.get("preview_image")
            if region_id not in raster_region_ids and isinstance(preview_image, QtGui.QImage) and not preview_image.isNull():
                self._draw_preview_image(painter, item, path, preview_image, fast_mode=fast_mode)
            border_color = QtGui.QColor("#ff9500" if region_id == self._active_region_id else "#ff8c00")
            border_pen = QtGui.QPen(border_color, 2.0 if region_id == self._active_region_id else 1.6)
            painter.setPen(border_pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawPath(path)
            self._draw_region_label(painter, item)

    def _draw_cached_raster_overlay(
        self,
        painter: QtGui.QPainter,
        prepared: dict[str, object],
        canvas_rect: QtCore.QRectF,
        *,
        fast_mode: bool,
        allow_build: bool,
    ) -> bool:
        overlay = self._raster_overlay_for_region(prepared, canvas_rect, allow_build=allow_build)
        if overlay is None:
            return False
        image = overlay.get("image")
        bounds = overlay.get("world_bounds")
        if not isinstance(image, QtGui.QImage) or image.isNull() or not isinstance(bounds, tuple) or len(bounds) != 4:
            return False
        target_rect = self._world_bounds_to_canvas_rect(bounds, canvas_rect)
        if not target_rect.isValid() or not target_rect.intersects(canvas_rect.adjusted(-16.0, -16.0, 16.0, 16.0)):
            return False
        painter.save()
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, not fast_mode)
        painter.drawImage(target_rect, image)
        painter.restore()
        return True

    def _raster_overlay_for_region(
        self,
        prepared: dict[str, object],
        canvas_rect: QtCore.QRectF,
        *,
        allow_build: bool,
    ) -> dict[str, object] | None:
        preview_image = prepared.get("preview_image")
        if not isinstance(preview_image, QtGui.QImage) or preview_image.isNull():
            return None
        region_id = str(prepared.get("region_id", "") or "")
        polygon_world = prepared.get("geo_polygon_world")
        render_world = prepared.get("render_navigation_world")
        trace_indices = prepared.get("render_navigation_trace_indices", [])
        if (
            not isinstance(polygon_world, np.ndarray)
            or polygon_world.size == 0
            or not isinstance(render_world, np.ndarray)
            or render_world.shape[0] < 2
        ):
            return None
        cache_key = (
            region_id,
            int(preview_image.cacheKey()),
            int(preview_image.width()),
            int(preview_image.height()),
            int(render_world.shape[0]),
            int(polygon_world.shape[0]),
        )
        cached = self._raster_overlay_cache.get(cache_key)
        if cached is not None:
            self._raster_overlay_cache.move_to_end(cache_key)
            return cached
        if not allow_build:
            return None
        overlay = self._build_raster_overlay(
            prepared,
            preview_image,
            polygon_world,
            render_world,
            trace_indices,
            canvas_rect,
        )
        if overlay is None:
            return None
        self._raster_overlay_cache[cache_key] = overlay
        self._raster_overlay_cache.move_to_end(cache_key)
        while len(self._raster_overlay_cache) > 24:
            self._raster_overlay_cache.popitem(last=False)
        return overlay

    def _build_raster_overlay(
        self,
        prepared: dict[str, object],
        preview_image: QtGui.QImage,
        polygon_world: np.ndarray,
        render_world: np.ndarray,
        trace_indices: object,
        canvas_rect: QtCore.QRectF,
    ) -> dict[str, object] | None:
        bounds = self._expanded_world_bounds(polygon_world)
        if bounds is None:
            return None
        image_size = self._raster_overlay_size(bounds, canvas_rect, preview_image)
        if image_size.isEmpty():
            return None
        image = QtGui.QImage(image_size, QtGui.QImage.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.transparent)
        min_x, min_y, max_x, max_y = bounds
        world_width = max(max_x - min_x, 1e-12)
        world_height = max(max_y - min_y, 1e-12)

        def to_local_array(world_array: np.ndarray) -> np.ndarray:
            x = (world_array[:, 0] - min_x) / world_width * float(image.width() - 1)
            y = (world_array[:, 1] - min_y) / world_height * float(image.height() - 1)
            return np.column_stack((x, y))

        polygon_local = to_local_array(polygon_world)
        render_local = to_local_array(render_world)
        polygon_points = [QtCore.QPointF(float(x), float(y)) for x, y in polygon_local]
        trace_values = self._trace_values_for_render_samples(trace_indices, render_local.shape[0])
        render_samples = [
            {
                "trace_index": float(trace_values[idx]),
                "screen_x": float(render_local[idx, 0]),
                "screen_y": float(render_local[idx, 1]),
            }
            for idx in range(render_local.shape[0])
        ]
        centerline_path = self._build_centerline_path(render_samples)
        path = self._build_region_path(polygon_points, render_samples, centerline_path)
        if path.isEmpty():
            return None

        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillPath(path, QtGui.QColor(48, 48, 48, 42))
        texture = self._render_channel_sweep_texture(
            preview_image,
            path,
            render_samples,
            polygon_points,
        )
        if texture is not None and not texture.isNull():
            painter.drawImage(QtCore.QPointF(0.0, 0.0), texture)
        else:
            strip_quads = self._build_preview_strip_quads(
                render_samples,
                polygon_points,
                preview_image.width(),
                preview_image.height(),
                max_quads=self._MAX_PREVIEW_QUADS,
            )
            for source_quad, target_quad in strip_quads:
                transform = QtGui.QTransform.quadToQuad(source_quad, target_quad)
                if transform.isIdentity() and source_quad != target_quad:
                    continue
                painter.save()
                quad_path = QtGui.QPainterPath()
                quad_path.addPolygon(target_quad)
                quad_path.closeSubpath()
                painter.setClipPath(path)
                painter.setClipPath(quad_path, QtCore.Qt.IntersectClip)
                painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
                painter.setTransform(transform, False)
                painter.drawImage(QtCore.QPointF(0.0, 0.0), preview_image)
                painter.restore()
        border_color = QtGui.QColor("#ff9500" if str(prepared.get("region_id", "") or "") == self._active_region_id else "#ff8c00")
        painter.setPen(QtGui.QPen(border_color, 2.0))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawPath(path)
        painter.end()
        return {
            "image": image,
            "world_bounds": bounds,
        }

    def _render_channel_sweep_texture(
        self,
        preview_image: QtGui.QImage,
        path: QtGui.QPainterPath,
        render_samples: list[dict[str, object]],
        screen_polygon: list[QtCore.QPointF],
    ) -> QtGui.QImage | None:
        if preview_image.isNull() or path.isEmpty() or len(render_samples) < 2:
            return None
        source = preview_image.convertToFormat(QtGui.QImage.Format_ARGB32)
        source_array = self._qimage_uint8_view(source, channels=4).copy()
        if source_array.size == 0:
            return None
        source_array = self._smooth_channel_source(source_array)
        source = self._argb32_image_from_array(source_array)
        if source.isNull():
            return None
        source_height, source_width = source_array.shape[:2]
        if source_width < 2 or source_height < 1:
            return None
        centers = np.array(
            [
                [float(sample.get("screen_x", 0.0)), float(sample.get("screen_y", 0.0))]
                for sample in render_samples
            ],
            dtype=float,
        )
        point_count = centers.shape[0]
        if point_count < 2:
            return None
        trace_positions = np.array([float(sample.get("trace_index", 0.0)) for sample in render_samples], dtype=float)
        if float(trace_positions[-1] - trace_positions[0]) <= 1e-9:
            sample_u = np.linspace(0.0, float(source_width - 1), point_count, dtype=float)
        else:
            sample_u = (
                (trace_positions - float(trace_positions[0]))
                / float(trace_positions[-1] - trace_positions[0])
                * float(source_width - 1)
            )
        keep = np.concatenate(([True], np.diff(sample_u) > 1e-6))
        if int(np.count_nonzero(keep)) < 2:
            return None
        half_widths_all = self._estimate_half_widths(screen_polygon, len(render_samples))
        if half_widths_all.size != len(render_samples):
            half_widths_all = np.full((len(render_samples),), 3.0, dtype=float)
        left_all, right_all = self._strip_boundary_vertices(screen_polygon, centers, half_widths_all)
        sample_u = sample_u[keep]
        centers = centers[keep]
        left_vertices = left_all[keep]
        right_vertices = right_all[keep]
        point_count = centers.shape[0]
        if left_vertices.shape[0] != point_count or right_vertices.shape[0] != point_count:
            return None

        output = QtGui.QImage(path.boundingRect().toAlignedRect().size(), QtGui.QImage.Format_ARGB32_Premultiplied)
        if output.isNull():
            return None
        output.fill(QtCore.Qt.transparent)
        origin = path.boundingRect().toAlignedRect().topLeft()
        local_path = QtGui.QPainterPath(path)
        local_path.translate(-float(origin.x()), -float(origin.y()))
        row_count = min(int(source_height), self._MAX_CHANNEL_SWEEP_ROWS)
        row_count = max(row_count, 2)
        column_budget = max(int(self._MAX_CHANNEL_SWEEP_CELLS // max(row_count - 1, 1)) + 1, 8)
        column_count = min(int(source_width), self._MAX_CHANNEL_SWEEP_COLUMNS, column_budget)
        columns = np.linspace(0.0, float(source_width - 1), max(column_count, 2), dtype=float)
        left_x = np.interp(columns, sample_u, left_vertices[:, 0]) - float(origin.x())
        left_y = np.interp(columns, sample_u, left_vertices[:, 1]) - float(origin.y())
        right_x = np.interp(columns, sample_u, right_vertices[:, 0]) - float(origin.x())
        right_y = np.interp(columns, sample_u, right_vertices[:, 1]) - float(origin.y())
        center_x = (left_x + right_x) * 0.5
        center_y = (left_y + right_y) * 0.5
        widths = np.hypot(left_x - right_x, left_y - right_y) * 0.5

        rows = np.linspace(0.0, float(source_height - 1), max(row_count, 2), dtype=float)
        if rows.size < 2:
            return None
        denominator = max(float(source_height - 1), 1.0)
        row_offsets = rows / denominator
        painter = QtGui.QPainter(output)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        source_bottom = float(source_height - 1)
        source_right = float(source_width - 1)
        drawn_cells = 0
        overlap_source = 0.75
        overlap_target = 0.55
        for row_index in range(rows.size - 1):
            row0 = float(rows[row_index])
            row1 = float(rows[row_index + 1])
            offset0 = float(row_offsets[row_index])
            offset1 = float(row_offsets[row_index + 1])
            left_x0 = left_x * (1.0 - offset0) + right_x * offset0
            left_y0 = left_y * (1.0 - offset0) + right_y * offset0
            left_x1 = left_x * (1.0 - offset1) + right_x * offset1
            left_y1 = left_y * (1.0 - offset1) + right_y * offset1
            for col_index in range(columns.size - 1):
                col0 = float(columns[col_index])
                col1 = float(columns[col_index + 1])
                if abs(col1 - col0) < 1e-6:
                    continue
                target_quad = QtGui.QPolygonF(
                    [
                        QtCore.QPointF(float(left_x0[col_index]), float(left_y0[col_index])),
                        QtCore.QPointF(float(left_x0[col_index + 1]), float(left_y0[col_index + 1])),
                        QtCore.QPointF(float(left_x1[col_index + 1]), float(left_y1[col_index + 1])),
                        QtCore.QPointF(float(left_x1[col_index]), float(left_y1[col_index])),
                    ]
                )
                quad_rect = target_quad.boundingRect()
                if quad_rect.width() < 0.4 and quad_rect.height() < 0.4:
                    continue
                if not quad_rect.intersects(output.rect()):
                    continue
                center_step = float(
                    np.hypot(
                        center_x[col_index + 1] - center_x[col_index],
                        center_y[col_index + 1] - center_y[col_index],
                    )
                )
                local_width = max(float(widths[col_index]), float(widths[col_index + 1]), 1.0)
                row_step = abs(float(offset1 - offset0)) * local_width
                if not self._quad_is_stable(target_quad, center_step=center_step, row_step=row_step, local_width=local_width):
                    continue
                draw_quad = self._expanded_quad(target_quad, overlap_target)
                quad_path = self._quad_path(draw_quad)
                source_quad = QtGui.QPolygonF(
                    [
                        QtCore.QPointF(float(np.clip(col0 - overlap_source, 0.0, source_right)), float(np.clip(row0 - overlap_source, 0.0, source_bottom))),
                        QtCore.QPointF(float(np.clip(col1 + overlap_source, 0.0, source_right)), float(np.clip(row0 - overlap_source, 0.0, source_bottom))),
                        QtCore.QPointF(float(np.clip(col1 + overlap_source, 0.0, source_right)), float(np.clip(row1 + overlap_source, 0.0, source_bottom))),
                        QtCore.QPointF(float(np.clip(col0 - overlap_source, 0.0, source_right)), float(np.clip(row1 + overlap_source, 0.0, source_bottom))),
                    ]
                )
                transform = QtGui.QTransform.quadToQuad(source_quad, draw_quad)
                if transform.isIdentity() and source_quad != draw_quad:
                    continue
                painter.save()
                painter.setClipPath(local_path)
                painter.setClipPath(quad_path, QtCore.Qt.IntersectClip)
                painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
                painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
                painter.setTransform(transform, False)
                painter.drawImage(QtCore.QPointF(0.0, 0.0), source)
                painter.restore()
                drawn_cells += 1
        painter.end()
        if drawn_cells <= 0:
            return None
        self._apply_path_alpha_mask(output, local_path)

        full = QtGui.QImage(path.boundingRect().toAlignedRect().right() + 1, path.boundingRect().toAlignedRect().bottom() + 1, QtGui.QImage.Format_ARGB32_Premultiplied)
        if full.isNull():
            return output
        full.fill(QtCore.Qt.transparent)
        full_painter = QtGui.QPainter(full)
        full_painter.drawImage(origin, output)
        full_painter.end()
        return full

    @classmethod
    def _smooth_channel_source(cls, source: np.ndarray) -> np.ndarray:
        if source.ndim != 3 or source.shape[0] <= 1:
            return source
        source_height = int(source.shape[0])
        target_height = min(max(80, source_height * 12), cls._MAX_CHANNEL_SWEEP_ROWS)
        if target_height <= source_height:
            return source
        old_y = np.arange(source_height, dtype=float)
        new_y = np.linspace(0.0, float(source_height - 1), target_height, dtype=float)
        y0 = np.floor(new_y).astype(np.int32)
        y1 = np.minimum(y0 + 1, source_height - 1)
        wy = (new_y - y0).astype(np.float32)
        interpolated = (
            source[y0].astype(np.float32) * (1.0 - wy[:, None, None])
            + source[y1].astype(np.float32) * wy[:, None, None]
        )
        if target_height >= 5:
            kernel = np.array([1.0, 4.0, 6.0, 4.0, 1.0], dtype=np.float32)
            kernel /= float(kernel.sum())
            padded = np.pad(interpolated, ((2, 2), (0, 0), (0, 0)), mode="edge")
            interpolated = (
                padded[:-4] * kernel[0]
                + padded[1:-3] * kernel[1]
                + padded[2:-2] * kernel[2]
                + padded[3:-1] * kernel[3]
                + padded[4:] * kernel[4]
            )
        return np.clip(interpolated, 0, 255).astype(np.uint8)

    def _render_curvilinear_texture(
        self,
        preview_image: QtGui.QImage,
        path: QtGui.QPainterPath,
        render_samples: list[dict[str, object]],
        screen_polygon: list[QtCore.QPointF],
        target_size: QtCore.QSize,
    ) -> QtGui.QImage | None:
        if preview_image.isNull() or path.isEmpty() or len(render_samples) < 2:
            return None
        target_rect = path.boundingRect().toAlignedRect().intersected(
            QtCore.QRect(0, 0, int(target_size.width()), int(target_size.height()))
        )
        if target_rect.isEmpty():
            return None
        image_size = target_rect.size()
        mask = QtGui.QImage(image_size, QtGui.QImage.Format_Grayscale8)
        mask.fill(0)
        mask_painter = QtGui.QPainter(mask)
        mask_painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        mask_painter.translate(-target_rect.left(), -target_rect.top())
        mask_painter.fillPath(path, QtGui.QColor(255, 255, 255))
        mask_painter.end()
        mask_array = self._qimage_uint8_view(mask, channels=1).copy()
        if mask_array.size == 0 or not np.any(mask_array):
            return None

        source = preview_image.convertToFormat(QtGui.QImage.Format_ARGB32)
        source_array = self._qimage_uint8_view(source, channels=4).copy()
        if source_array.size == 0:
            return None
        source_height, source_width = source_array.shape[:2]
        centers = np.array(
            [
                [float(sample.get("screen_x", 0.0)) - float(target_rect.left()), float(sample.get("screen_y", 0.0)) - float(target_rect.top())]
                for sample in render_samples
            ],
            dtype=float,
        )
        point_count = centers.shape[0]
        trace_positions = np.array([float(sample.get("trace_index", 0.0)) for sample in render_samples], dtype=float)
        if float(trace_positions[-1] - trace_positions[0]) <= 1e-9:
            source_u = np.linspace(0.0, float(source_width - 1), point_count, dtype=float)
        else:
            source_u = (
                (trace_positions - float(trace_positions[0]))
                / float(trace_positions[-1] - trace_positions[0])
                * float(source_width - 1)
            )
        local_polygon = [
            QtCore.QPointF(float(point.x() - target_rect.left()), float(point.y() - target_rect.top()))
            for point in screen_polygon
        ]
        half_widths = self._estimate_half_widths(local_polygon, point_count)
        output = np.zeros((image_size.height(), image_size.width(), 4), dtype=np.uint8)
        best_score = np.full((image_size.height(), image_size.width()), np.inf, dtype=np.float32)
        image_bottom = float(source_height - 1)
        for idx in range(point_count - 1):
            start = centers[idx]
            stop = centers[idx + 1]
            segment = stop - start
            segment_len2 = float(np.dot(segment, segment))
            if segment_len2 <= 1e-9:
                continue
            segment_len = float(np.sqrt(segment_len2))
            normal = np.array([-segment[1] / segment_len, segment[0] / segment_len], dtype=float)
            max_half_width = max(float(half_widths[idx]), float(half_widths[idx + 1])) + 2.0
            min_x = int(max(np.floor(min(start[0], stop[0]) - max_half_width), 0))
            max_x = int(min(np.ceil(max(start[0], stop[0]) + max_half_width), image_size.width() - 1))
            min_y = int(max(np.floor(min(start[1], stop[1]) - max_half_width), 0))
            max_y = int(min(np.ceil(max(start[1], stop[1]) + max_half_width), image_size.height() - 1))
            if min_x > max_x or min_y > max_y:
                continue
            yy, xx = np.mgrid[min_y : max_y + 1, min_x : max_x + 1]
            px = xx.astype(float) + 0.5
            py = yy.astype(float) + 0.5
            rel_x = px - float(start[0])
            rel_y = py - float(start[1])
            t = np.clip((rel_x * float(segment[0]) + rel_y * float(segment[1])) / segment_len2, 0.0, 1.0)
            closest_x = float(start[0]) + t * float(segment[0])
            closest_y = float(start[1]) + t * float(segment[1])
            offset_x = px - closest_x
            offset_y = py - closest_y
            signed = offset_x * float(normal[0]) + offset_y * float(normal[1])
            half_width = (1.0 - t) * float(half_widths[idx]) + t * float(half_widths[idx + 1])
            half_width = np.maximum(half_width, 0.5)
            score = np.abs(signed) / half_width
            local_mask = (mask_array[min_y : max_y + 1, min_x : max_x + 1] > 0) & (score <= 1.08)
            current = best_score[min_y : max_y + 1, min_x : max_x + 1]
            update_mask = local_mask & (score < current)
            if not np.any(update_mask):
                continue
            u = (1.0 - t) * float(source_u[idx]) + t * float(source_u[idx + 1])
            v = (0.5 - signed / (2.0 * half_width)) * image_bottom
            sampled = self._sample_argb32_bilinear(source_array, u, v)
            alpha = mask_array[min_y : max_y + 1, min_x : max_x + 1]
            target = output[min_y : max_y + 1, min_x : max_x + 1]
            for channel in range(4):
                channel_values = sampled[:, :, channel]
                if channel == 3:
                    channel_values = alpha
                target_channel = target[:, :, channel]
                target_channel[update_mask] = channel_values[update_mask]
            current[update_mask] = score[update_mask].astype(np.float32)

        missing = (mask_array > 0) & ~np.isfinite(best_score)
        if np.any(missing):
            output[missing, 0] = 48
            output[missing, 1] = 48
            output[missing, 2] = 48
            output[missing, 3] = mask_array[missing]

        result = QtGui.QImage(image_size, QtGui.QImage.Format_ARGB32)
        result_array = self._qimage_uint8_view(result, channels=4)
        result_array[:, :, :] = output
        if target_rect.left() == 0 and target_rect.top() == 0 and image_size == target_size:
            return result
        full = QtGui.QImage(target_size, QtGui.QImage.Format_ARGB32)
        full.fill(QtCore.Qt.transparent)
        full_painter = QtGui.QPainter(full)
        full_painter.drawImage(QtCore.QPoint(target_rect.left(), target_rect.top()), result)
        full_painter.end()
        return full

    @staticmethod
    def _qimage_uint8_view(image: QtGui.QImage, *, channels: int) -> np.ndarray:
        height = int(image.height())
        width = int(image.width())
        if height <= 0 or width <= 0:
            return np.empty((0, 0, channels), dtype=np.uint8) if channels > 1 else np.empty((0, 0), dtype=np.uint8)
        buffer = image.bits()
        array = np.frombuffer(buffer, dtype=np.uint8).reshape((height, int(image.bytesPerLine())))
        if channels == 1:
            return array[:, :width]
        return array[:, : width * channels].reshape((height, width, channels))

    @classmethod
    def _argb32_image_from_array(cls, array: np.ndarray) -> QtGui.QImage:
        if array.ndim != 3 or array.shape[2] != 4 or array.size == 0:
            return QtGui.QImage()
        height, width = array.shape[:2]
        image = QtGui.QImage(int(width), int(height), QtGui.QImage.Format_ARGB32)
        if image.isNull():
            return image
        target = cls._qimage_uint8_view(image, channels=4)
        target[:, :, :] = np.asarray(array, dtype=np.uint8)
        return image

    @staticmethod
    def _quad_path(polygon: QtGui.QPolygonF) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        path.addPolygon(polygon)
        path.closeSubpath()
        return path

    @staticmethod
    def _expanded_quad(polygon: QtGui.QPolygonF, amount: float) -> QtGui.QPolygonF:
        if polygon.size() < 4 or amount <= 0.0:
            return polygon
        points = np.array([[float(polygon.at(idx).x()), float(polygon.at(idx).y())] for idx in range(4)], dtype=float)
        if not np.all(np.isfinite(points)):
            return polygon
        center = np.mean(points, axis=0)
        expanded: list[QtCore.QPointF] = []
        for point in points:
            vector = point - center
            norm = float(np.hypot(vector[0], vector[1]))
            if norm <= 1e-6:
                expanded.append(QtCore.QPointF(float(point[0]), float(point[1])))
            else:
                shifted = point + (vector / norm) * float(amount)
                expanded.append(QtCore.QPointF(float(shifted[0]), float(shifted[1])))
        return QtGui.QPolygonF(expanded)

    @staticmethod
    def _quad_is_stable(
        polygon: QtGui.QPolygonF,
        *,
        center_step: float,
        row_step: float,
        local_width: float,
    ) -> bool:
        if polygon.size() < 4:
            return False
        points = np.array([[float(polygon.at(idx).x()), float(polygon.at(idx).y())] for idx in range(4)], dtype=float)
        if not np.all(np.isfinite(points)):
            return False
        edges = np.roll(points, -1, axis=0) - points
        edge_lengths = np.hypot(edges[:, 0], edges[:, 1])
        if float(np.min(edge_lengths)) < 0.05:
            return False
        cross_values = []
        for idx in range(4):
            first = points[(idx + 1) % 4] - points[idx]
            second = points[(idx + 2) % 4] - points[(idx + 1) % 4]
            cross_values.append(float(first[0] * second[1] - first[1] * second[0]))
        crosses = np.asarray(cross_values, dtype=float)
        if np.any(np.isclose(crosses, 0.0, atol=1e-4)):
            return False
        if not (np.all(crosses > 0.0) or np.all(crosses < 0.0)):
            return False
        rect = polygon.boundingRect()
        max_span = max(float(rect.width()), float(rect.height()))
        allowed_span = max(float(center_step) * 5.0, float(row_step) * 8.0, float(local_width) * 5.0, 64.0)
        if max_span > allowed_span:
            return False
        polygon_area = 0.5 * abs(
            float(np.dot(points[:, 0], np.roll(points[:, 1], -1)) - np.dot(points[:, 1], np.roll(points[:, 0], -1)))
        )
        if polygon_area <= 1e-4:
            return False
        bbox_area = max(float(rect.width()) * float(rect.height()), 1e-4)
        if bbox_area / polygon_area > 18.0:
            return False
        return True

    @classmethod
    def _apply_path_alpha_mask(cls, image: QtGui.QImage, path: QtGui.QPainterPath) -> None:
        if image.isNull() or path.isEmpty():
            return
        mask = QtGui.QImage(image.size(), QtGui.QImage.Format_Grayscale8)
        mask.fill(0)
        painter = QtGui.QPainter(mask)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillPath(path, QtGui.QColor(255, 255, 255))
        painter.end()
        image_array = cls._qimage_uint8_view(image, channels=4)
        mask_array = cls._qimage_uint8_view(mask, channels=1)
        if image_array.size == 0 or mask_array.size == 0:
            return
        image_array[:, :, 3] = np.minimum(image_array[:, :, 3], mask_array)

    @staticmethod
    def _sample_argb32_bilinear(source: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
        height, width = source.shape[:2]
        u = np.clip(u, 0.0, float(max(width - 1, 0)))
        v = np.clip(v, 0.0, float(max(height - 1, 0)))
        x0 = np.floor(u).astype(np.int32)
        y0 = np.floor(v).astype(np.int32)
        x1 = np.minimum(x0 + 1, width - 1)
        y1 = np.minimum(y0 + 1, height - 1)
        wx = (u - x0).astype(np.float32)
        wy = (v - y0).astype(np.float32)
        top = source[y0, x0].astype(np.float32) * (1.0 - wx[:, :, None]) + source[y0, x1].astype(np.float32) * wx[:, :, None]
        bottom = source[y1, x0].astype(np.float32) * (1.0 - wx[:, :, None]) + source[y1, x1].astype(np.float32) * wx[:, :, None]
        return np.clip(top * (1.0 - wy[:, :, None]) + bottom * wy[:, :, None], 0, 255).astype(np.uint8)

    @staticmethod
    def _trace_values_for_render_samples(trace_indices: object, point_count: int) -> np.ndarray:
        try:
            values = np.asarray(list(trace_indices), dtype=float)
        except (TypeError, ValueError):
            values = np.empty((0,), dtype=float)
        if values.size == point_count:
            return values
        if values.size >= 2:
            return np.linspace(float(values[0]), float(values[-1]), point_count, dtype=float)
        return np.arange(point_count, dtype=float)

    @staticmethod
    def _expanded_world_bounds(world_array: np.ndarray) -> tuple[float, float, float, float] | None:
        if world_array.size == 0:
            return None
        min_x = float(np.nanmin(world_array[:, 0]))
        max_x = float(np.nanmax(world_array[:, 0]))
        min_y = float(np.nanmin(world_array[:, 1]))
        max_y = float(np.nanmax(world_array[:, 1]))
        if not all(np.isfinite([min_x, max_x, min_y, max_y])):
            return None
        width = max(max_x - min_x, 1e-12)
        height = max(max_y - min_y, 1e-12)
        margin_x = max(width * 0.04, 1e-9)
        margin_y = max(height * 0.04, 1e-9)
        return (min_x - margin_x, min_y - margin_y, max_x + margin_x, max_y + margin_y)

    def _raster_overlay_size(
        self,
        bounds: tuple[float, float, float, float],
        canvas_rect: QtCore.QRectF,
        preview_image: QtGui.QImage,
    ) -> QtCore.QSize:
        min_x, min_y, max_x, max_y = bounds
        world_width = max(max_x - min_x, 1e-12)
        world_height = max(max_y - min_y, 1e-12)
        aspect = float(np.clip(world_width / world_height, 0.05, 20.0))
        dpr = max(float(self.devicePixelRatioF()), 1.0)
        screen_width = max(world_width * self._world_scale() * dpr, 1.0)
        screen_height = max(world_height * self._world_scale() * dpr, 1.0)
        source_width_hint = min(max(int(preview_image.width()), 256), self._MAX_OVERVIEW_TEXTURE_WIDTH)
        target_width = max(screen_width, float(source_width_hint))
        target_height = max(screen_height, target_width / aspect)
        target_width = max(target_width, target_height * aspect)
        max_width = float(self._MAX_OVERVIEW_TEXTURE_WIDTH)
        max_height = float(self._MAX_OVERVIEW_TEXTURE_HEIGHT)
        scale = min(max_width / target_width, max_height / target_height, 1.0)
        target_width *= scale
        target_height *= scale
        pixel_count = target_width * target_height
        if pixel_count > self._MAX_OVERVIEW_TEXTURE_PIXELS:
            scale = float(np.sqrt(self._MAX_OVERVIEW_TEXTURE_PIXELS / pixel_count))
            target_width *= scale
            target_height *= scale
        width = int(np.clip(round(target_width), 32, self._MAX_OVERVIEW_TEXTURE_WIDTH))
        height = int(np.clip(round(target_height), 32, self._MAX_OVERVIEW_TEXTURE_HEIGHT))
        return QtCore.QSize(width, height)

    def _world_bounds_to_canvas_rect(
        self,
        bounds: tuple[float, float, float, float],
        canvas_rect: QtCore.QRectF,
    ) -> QtCore.QRectF:
        min_x, min_y, max_x, max_y = bounds
        scale = self._world_scale()
        left = canvas_rect.center().x() + (float(min_x) - self._center_world_x) * scale
        top = canvas_rect.center().y() + (float(min_y) - self._center_world_y) * scale
        width = max((float(max_x) - float(min_x)) * scale, 1.0)
        height = max((float(max_y) - float(min_y)) * scale, 1.0)
        return QtCore.QRectF(float(left), float(top), float(width), float(height))

    def _draw_preview_image(
        self,
        painter: QtGui.QPainter,
        item: dict[str, object],
        path: QtGui.QPainterPath,
        preview_image: QtGui.QImage,
        *,
        fast_mode: bool,
    ) -> None:
        target_rect = path.boundingRect()
        lod_image = self._preview_lod_image(preview_image, target_rect, fast_mode=fast_mode)
        if fast_mode:
            self._draw_preview_base_fill(painter, item, path, lod_image, fast_mode=fast_mode)
            return
        strip_quads = self._build_preview_strip_quads(
            item.get("render_navigation_samples", []),
            item.get("screen_polygon", []),
            lod_image.width(),
            lod_image.height(),
            max_quads=self._preview_quad_budget(target_rect),
        )
        if not strip_quads:
            self._draw_preview_base_fill(painter, item, path, lod_image, fast_mode=fast_mode)
            return
        painter.save()
        painter.fillPath(path, QtGui.QColor(48, 48, 48, 48))
        painter.restore()
        for source_quad, target_quad in strip_quads:
            transform = QtGui.QTransform.quadToQuad(source_quad, target_quad)
            if transform.isIdentity() and source_quad != target_quad:
                continue
            painter.save()
            quad_path = QtGui.QPainterPath()
            quad_path.addPolygon(target_quad)
            painter.setClipPath(path)
            painter.setClipPath(quad_path, QtCore.Qt.IntersectClip)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            painter.setTransform(transform, False)
            painter.drawImage(QtCore.QPointF(0.0, 0.0), lod_image)
            painter.restore()

    @classmethod
    def _preview_quad_budget(cls, target_rect: QtCore.QRectF) -> int:
        visible_pixels = max(float(target_rect.width()), float(target_rect.height()), 1.0)
        return int(np.clip(np.ceil(visible_pixels / 12.0), 24, cls._MAX_PREVIEW_QUADS))

    def _preview_lod_image(self, image: QtGui.QImage, target_rect: QtCore.QRectF, *, fast_mode: bool) -> QtGui.QImage:
        if image.isNull():
            return image
        dpr = max(float(self.devicePixelRatioF()), 1.0)
        target_width = int(np.ceil(max(float(target_rect.width()), 1.0) * dpr))
        target_height = int(np.ceil(max(float(target_rect.height()), 1.0) * dpr))
        if fast_mode:
            target_width = min(target_width, 512)
            target_height = min(target_height, 192)
        target_width = int(np.clip(target_width, 1, self._MAX_OVERVIEW_TEXTURE_WIDTH))
        target_height = int(np.clip(target_height, 1, self._MAX_OVERVIEW_TEXTURE_HEIGHT))
        if image.width() <= target_width and image.height() <= target_height:
            return image
        target_size = QtCore.QSize(
            min(max(1, target_width), max(1, image.width())),
            min(max(1, target_height), max(1, image.height())),
        )
        key = (int(image.cacheKey()), target_size.width(), target_size.height())
        cached = self._preview_lod_cache.get(key)
        if cached is not None and not cached.isNull():
            self._preview_lod_cache.move_to_end(key)
            return cached
        scaled = image.scaled(target_size, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.FastTransformation)
        self._preview_lod_cache[key] = scaled
        self._preview_lod_cache.move_to_end(key)
        while len(self._preview_lod_cache) > 96:
            self._preview_lod_cache.popitem(last=False)
        return scaled

    def _draw_preview_base_fill(
        self,
        painter: QtGui.QPainter,
        item: dict[str, object],
        path: QtGui.QPainterPath,
        preview_image: QtGui.QImage,
        *,
        fast_mode: bool,
    ) -> None:
        painter.save()
        painter.setClipPath(path)
        geometry = item.get("screen_geometry")
        if geometry is not None:
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, not fast_mode)
            painter.translate(geometry["center"])
            painter.rotate(geometry["angle_deg"])
            painter.drawImage(geometry["target_rect_local"], preview_image)
        else:
            polygon_points = item.get("screen_polygon", [])
            if polygon_points:
                polygon = QtGui.QPolygonF(polygon_points)
                painter.drawImage(polygon.boundingRect(), preview_image)
        painter.restore()

    def _draw_region_label(self, painter: QtGui.QPainter, item: dict[str, object]) -> None:
        geometry = item.get("screen_geometry")
        if geometry is None:
            return
        text = str(item.get("label_text", "") or item.get("region_name", ""))
        if not text:
            return
        painter.save()
        painter.translate(geometry["label_anchor"])
        painter.rotate(geometry["label_angle_deg"])
        painter.setFont(QtGui.QFont("Microsoft YaHei UI", 10, QtGui.QFont.DemiBold))
        painter.setPen(QtGui.QColor("#734000"))
        painter.drawText(QtCore.QPointF(0.0, -2.0), text)
        painter.restore()

    def _compute_layout(self, canvas: QtCore.QRect) -> list[tuple[str, QtGui.QPainterPath, dict[str, object]]]:
        cache_key = (
            round(self._center_lat, 7),
            round(self._center_lon, 7),
            round(self._zoom, 4),
            int(canvas.width()),
            int(canvas.height()),
            len(self._prepared_regions),
            bool(self._interaction_timer.isActive()),
        )
        if cache_key == self._layout_cache_key:
            return self._layout_cache
        rects: list[tuple[str, QtGui.QPainterPath, dict[str, object]]] = []
        canvas_rect = QtCore.QRectF(canvas)
        for prepared in self._prepared_regions:
            polygon_world_array = prepared.get("geo_polygon_world")
            if not isinstance(polygon_world_array, np.ndarray) or polygon_world_array.size == 0:
                continue
            polygon_screen = self._world_arrays_to_canvas(polygon_world_array, canvas_rect)
            polygon = QtGui.QPolygonF([QtCore.QPointF(float(x), float(y)) for x, y in polygon_screen])
            item = dict(prepared)
            item["screen_polygon"] = [polygon.at(idx) for idx in range(polygon.size())]
            navigation_array = prepared.get("navigation_world")
            trace_indices = prepared.get("navigation_trace_indices", [])
            if isinstance(navigation_array, np.ndarray) and navigation_array.size > 0:
                navigation_screen = self._world_arrays_to_canvas(navigation_array, canvas_rect)
                item["navigation_samples"] = [
                    {
                        "trace_index": int(trace_indices[idx]),
                        "screen_x": float(navigation_screen[idx, 0]),
                        "screen_y": float(navigation_screen[idx, 1]),
                    }
                    for idx in range(min(len(trace_indices), navigation_screen.shape[0]))
                ]
            else:
                item["navigation_samples"] = []
            render_navigation_array = prepared.get("render_navigation_world")
            render_trace_indices = prepared.get("render_navigation_trace_indices", [])
            if isinstance(render_navigation_array, np.ndarray) and render_navigation_array.size > 0:
                render_navigation_screen = self._world_arrays_to_canvas(render_navigation_array, canvas_rect)
                item["render_navigation_samples"] = [
                    {
                        "trace_index": float(render_trace_indices[idx]),
                        "screen_x": float(render_navigation_screen[idx, 0]),
                        "screen_y": float(render_navigation_screen[idx, 1]),
                    }
                    for idx in range(min(len(render_trace_indices), render_navigation_screen.shape[0]))
                ]
            else:
                item["render_navigation_samples"] = []
            centerline_path = self._build_centerline_path(item.get("render_navigation_samples", []))
            path = self._build_region_path(item.get("screen_polygon", []), item.get("render_navigation_samples", []), centerline_path)
            item["screen_centerline_path"] = centerline_path
            item["screen_geometry"] = self._region_screen_geometry(item)
            preview_image = item.get("preview_image")
            item["preview_strip_quads"] = []
            rects.append((str(prepared.get("region_id", "")), path, item))
        self._layout_cache_key = cache_key
        self._layout_cache = rects
        return rects

    @staticmethod
    def _region_screen_geometry(item: dict[str, object]) -> dict[str, object] | None:
        samples = item.get("render_navigation_samples") or item.get("navigation_samples", [])
        screen_polygon = item.get("screen_polygon", [])
        if not isinstance(samples, list) or len(samples) < 2:
            return None
        if not isinstance(screen_polygon, list) or len(screen_polygon) < 4:
            return None
        start = QtCore.QPointF(float(samples[0]["screen_x"]), float(samples[0]["screen_y"]))
        end = QtCore.QPointF(float(samples[-1]["screen_x"]), float(samples[-1]["screen_y"]))
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        length = float(np.hypot(dx, dy))
        if length < 1e-6:
            return None
        direction = np.array([dx / length, dy / length], dtype=float)
        angle = float(np.degrees(np.arctan2(dy, dx)))
        normal = np.array([-direction[1], direction[0]], dtype=float)
        upper_start = screen_polygon[0]
        upper_end = screen_polygon[max((len(screen_polygon) // 2) - 1, 0)]
        lower_start = screen_polygon[-1]
        lower_end = screen_polygon[len(screen_polygon) // 2]
        outward = np.array(
            [
                ((upper_start.x() + upper_end.x()) * 0.5) - ((lower_start.x() + lower_end.x()) * 0.5),
                ((upper_start.y() + upper_end.y()) * 0.5) - ((lower_start.y() + lower_end.y()) * 0.5),
            ],
            dtype=float,
        )
        outward_norm = float(np.hypot(outward[0], outward[1]))
        if outward_norm < 1e-6:
            outward = normal.copy()
            outward_norm = 1.0
        outward /= outward_norm
        if float(np.dot(normal, outward)) < 0.0:
            direction *= -1.0
            normal *= -1.0
            angle += 180.0
        center = QtCore.QPointF((start.x() + end.x()) * 0.5, (start.y() + end.y()) * 0.5)
        local_points: list[tuple[float, float]] = []
        for point in screen_polygon:
            vec = np.array([point.x() - center.x(), point.y() - center.y()], dtype=float)
            local_points.append((float(np.dot(vec, direction)), float(np.dot(vec, normal))))
        min_u = min(point[0] for point in local_points)
        max_u = max(point[0] for point in local_points)
        min_v = min(point[1] for point in local_points)
        max_v = max(point[1] for point in local_points)
        target_rect_local = QtCore.QRectF(
            float(min_u),
            float(min_v),
            float(max(max_u - min_u, 1.0)),
            float(max(max_v - min_v, 1.0)),
        )
        upper_left = upper_start
        upper_right = upper_end
        if upper_left.x() > upper_right.x():
            upper_left, upper_right = upper_right, upper_left
        label_vec = np.array([upper_right.x() - upper_left.x(), upper_right.y() - upper_left.y()], dtype=float)
        label_vec_norm = float(np.hypot(label_vec[0], label_vec[1]))
        if label_vec_norm < 1e-6:
            label_vec = np.array([1.0, 0.0], dtype=float)
            label_vec_norm = 1.0
        label_vec /= label_vec_norm
        label_normal = outward.copy()
        if label_normal[1] > 0.0:
            label_normal *= -1.0
        label_angle = float(np.degrees(np.arctan2(label_vec[1], label_vec[0])))
        label_anchor = QtCore.QPointF(
            float(upper_left.x() + label_normal[0] * 10.0 - label_vec[0] * 2.0),
            float(upper_left.y() + label_normal[1] * 10.0 - label_vec[1] * 2.0),
        )
        return {
            "center": center,
            "angle_deg": angle,
            "target_rect_local": target_rect_local,
            "label_anchor": label_anchor,
            "label_angle_deg": label_angle,
        }

    @staticmethod
    def _build_preview_strip_quads(
        render_navigation_samples: list[dict[str, object]],
        screen_polygon: list[QtCore.QPointF],
        image_width: int,
        image_height: int,
        *,
        max_quads: int | None = None,
    ) -> list[tuple[QtGui.QPolygonF, QtGui.QPolygonF]]:
        if image_width <= 1 or image_height <= 0:
            return []
        if not isinstance(render_navigation_samples, list) or len(render_navigation_samples) < 2:
            return []
        point_count = len(render_navigation_samples)
        trace_positions = np.array([float(sample.get("trace_index", 0.0)) for sample in render_navigation_samples], dtype=float)
        if trace_positions.size < 2:
            return []
        if float(trace_positions[-1] - trace_positions[0]) <= 1e-9:
            source_u = np.linspace(0.0, float(image_width - 1), point_count, dtype=float)
        else:
            source_u = (
                (trace_positions - float(trace_positions[0]))
                / float(trace_positions[-1] - trace_positions[0])
                * float(image_width - 1)
            )
        quads: list[tuple[QtGui.QPolygonF, QtGui.QPolygonF]] = []
        image_bottom = float(image_height - 1)
        centers = np.array(
            [
                [float(sample.get("screen_x", 0.0)), float(sample.get("screen_y", 0.0))]
                for sample in render_navigation_samples
            ],
            dtype=float,
        )
        half_widths = OverviewOverlayWidget._estimate_half_widths(screen_polygon, point_count)
        indices = np.arange(point_count - 1, dtype=int)
        quad_limit = int(max_quads or OverviewOverlayWidget._MAX_PREVIEW_QUADS)
        quad_limit = int(np.clip(quad_limit, 1, OverviewOverlayWidget._MAX_PREVIEW_QUADS))
        if indices.size > quad_limit:
            sample_at = np.linspace(0, indices.size - 1, quad_limit, dtype=int)
            indices = np.unique(indices[sample_at])
        segment_edges: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
        for idx in indices:
            u0 = float(source_u[idx])
            u1 = float(source_u[idx + 1])
            if abs(u1 - u0) < 0.25:
                continue
            start = centers[idx]
            stop = centers[idx + 1]
            segment = stop - start
            segment_length = float(np.hypot(segment[0], segment[1]))
            if segment_length < 1e-6:
                continue
            tangent = segment / segment_length
            normal = np.array([-tangent[1], tangent[0]], dtype=float)
            half_width_start = float(half_widths[idx])
            half_width_stop = float(half_widths[idx + 1])
            left_start = start + normal * half_width_start
            left_stop = stop + normal * half_width_stop
            right_stop = stop - normal * half_width_stop
            right_start = start - normal * half_width_start
            segment_edges[int(idx)] = (left_start, right_start, left_stop, right_stop)
            target_quad = QtGui.QPolygonF(
                [
                    QtCore.QPointF(float(left_start[0]), float(left_start[1])),
                    QtCore.QPointF(float(left_stop[0]), float(left_stop[1])),
                    QtCore.QPointF(float(right_stop[0]), float(right_stop[1])),
                    QtCore.QPointF(float(right_start[0]), float(right_start[1])),
                ]
            )
            if target_quad.boundingRect().width() < 0.5 and target_quad.boundingRect().height() < 0.5:
                continue
            overlap_source = min(1.5, max(0.8, (u1 - u0) * 0.10))
            src_u0 = max(0.0, u0 - overlap_source)
            src_u1 = min(float(image_width - 1), u1 + overlap_source)
            source_quad = QtGui.QPolygonF(
                [
                    QtCore.QPointF(src_u0, 0.0),
                    QtCore.QPointF(src_u1, 0.0),
                    QtCore.QPointF(src_u1, image_bottom),
                    QtCore.QPointF(src_u0, image_bottom),
                ]
            )
            quads.append((source_quad, target_quad))
        main_quads = list(quads)
        connector_quads: list[tuple[QtGui.QPolygonF, QtGui.QPolygonF]] = []
        for idx in indices[:-1]:
            idx = int(idx)
            next_idx = idx + 1
            if next_idx not in segment_edges or idx not in segment_edges:
                continue
            _prev_left_start, _prev_right_start, prev_left_stop, prev_right_stop = segment_edges[idx]
            next_left_start, next_right_start, _next_left_stop, _next_right_stop = segment_edges[next_idx]
            connector = QtGui.QPolygonF(
                [
                    QtCore.QPointF(float(prev_left_stop[0]), float(prev_left_stop[1])),
                    QtCore.QPointF(float(next_left_start[0]), float(next_left_start[1])),
                    QtCore.QPointF(float(next_right_start[0]), float(next_right_start[1])),
                    QtCore.QPointF(float(prev_right_stop[0]), float(prev_right_stop[1])),
                ]
            )
            if connector.boundingRect().width() < 0.5 and connector.boundingRect().height() < 0.5:
                continue
            u_center = float(source_u[next_idx])
            connector_source_width = 2.5
            source_quad = QtGui.QPolygonF(
                [
                    QtCore.QPointF(max(0.0, u_center - connector_source_width), 0.0),
                    QtCore.QPointF(min(float(image_width - 1), u_center + connector_source_width), 0.0),
                    QtCore.QPointF(min(float(image_width - 1), u_center + connector_source_width), image_bottom),
                    QtCore.QPointF(max(0.0, u_center - connector_source_width), image_bottom),
                ]
            )
            connector_quads.append((source_quad, connector))
        return connector_quads + main_quads

    @staticmethod
    def _strip_boundary_vertices(
        screen_polygon: list[QtCore.QPointF],
        centers: np.ndarray,
        half_widths: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        point_count = int(centers.shape[0])
        if isinstance(screen_polygon, list) and len(screen_polygon) >= point_count * 2 and point_count > 0:
            left = np.array(
                [[float(point.x()), float(point.y())] for point in screen_polygon[:point_count]],
                dtype=float,
            )
            right = np.array(
                [[float(point.x()), float(point.y())] for point in reversed(screen_polygon[point_count : point_count * 2])],
                dtype=float,
            )
            if left.shape[0] == point_count and right.shape[0] == point_count:
                return left, right
        normals = OverviewOverlayWidget._centerline_vertex_normals(centers)
        widths = np.asarray(half_widths, dtype=float)
        if widths.size != point_count:
            widths = np.full((point_count,), 3.0, dtype=float)
        return centers + normals * widths[:, None], centers - normals * widths[:, None]

    @staticmethod
    def _centerline_vertex_normals(centers: np.ndarray) -> np.ndarray:
        point_count = int(centers.shape[0])
        normals = np.zeros_like(centers, dtype=float)
        if point_count == 0:
            return normals
        if point_count == 1:
            normals[0] = np.array([0.0, 1.0], dtype=float)
            return normals
        deltas = np.diff(centers, axis=0)
        lengths = np.hypot(deltas[:, 0], deltas[:, 1])
        safe_lengths = np.where(lengths > 1e-9, lengths, 1.0)
        tangents = deltas / safe_lengths[:, None]
        tangents[lengths <= 1e-9] = np.array([1.0, 0.0], dtype=float)
        vertex_tangents = np.zeros_like(centers, dtype=float)
        vertex_tangents[0] = tangents[0]
        vertex_tangents[-1] = tangents[-1]
        if point_count > 2:
            vertex_tangents[1:-1] = tangents[:-1] + tangents[1:]
            tangent_lengths = np.hypot(vertex_tangents[1:-1, 0], vertex_tangents[1:-1, 1])
            sharp_mask = tangent_lengths <= 1e-9
            middle_tangents = vertex_tangents[1:-1]
            middle_tangents[sharp_mask] = tangents[:-1][sharp_mask]
            vertex_tangents[1:-1] = middle_tangents
        tangent_lengths = np.hypot(vertex_tangents[:, 0], vertex_tangents[:, 1])
        tangent_lengths = np.where(tangent_lengths > 1e-9, tangent_lengths, 1.0)
        vertex_tangents = vertex_tangents / tangent_lengths[:, None]
        normals[:, 0] = -vertex_tangents[:, 1]
        normals[:, 1] = vertex_tangents[:, 0]
        return normals

    @staticmethod
    def _estimate_half_widths(screen_polygon: list[QtCore.QPointF], point_count: int) -> np.ndarray:
        default = np.full((point_count,), 3.0, dtype=float)
        if not isinstance(screen_polygon, list) or len(screen_polygon) < point_count * 2 or point_count <= 0:
            return default
        upper_points = screen_polygon[:point_count]
        lower_points = list(reversed(screen_polygon[point_count:]))
        widths = []
        for idx in range(point_count):
            dx = float(upper_points[idx].x() - lower_points[idx].x())
            dy = float(upper_points[idx].y() - lower_points[idx].y())
            widths.append(max(float(np.hypot(dx, dy)) * 0.5, 1.0))
        return np.asarray(widths, dtype=float)

    def _prepare_regions(self, files: list[dict[str, object]]) -> list[dict[str, object]]:
        prepared_regions: list[dict[str, object]] = []
        for file_item in files:
            file_id = str(file_item.get("file_id", "") or "")
            file_name = str(file_item.get("file_name", "") or "")
            for region in file_item.get("regions", []):
                polygon_points = region.get("navigation_samples", [])
                if not polygon_points:
                    continue
                navigation_samples = [
                    {
                        "trace_index": int(sample.get("trace_index", 0)),
                        "latitude": float(sample["latitude"]),
                        "longitude": float(sample["longitude"]),
                    }
                    for sample in polygon_points
                    if sample.get("latitude") is not None and sample.get("longitude") is not None
                ]
                render_width = float(region.get("render_width", 0.0))
                render_navigation_samples = self._build_render_navigation_samples(navigation_samples, render_width)
                polygon_geo = self._region_polygon_geo_points(render_navigation_samples, render_width)
                if len(polygon_geo) < 3:
                    continue
                navigation_array = (
                    np.array([[sample["latitude"], sample["longitude"]] for sample in navigation_samples], dtype=float)
                    if navigation_samples
                    else np.empty((0, 2), dtype=float)
                )
                render_navigation_array = (
                    np.array([[sample["latitude"], sample["longitude"]] for sample in render_navigation_samples], dtype=float)
                    if render_navigation_samples
                    else np.empty((0, 2), dtype=float)
                )
                prepared_regions.append(
                    {
                        "region_id": str(region.get("region_id", "") or ""),
                        "region_name": str(region.get("region_name", "") or ""),
                        "file_id": file_id,
                        "file_name": file_name,
                        "label_text": self._region_label_text(file_item, region),
                        "preview_image": region.get("preview_image"),
                        "geo_polygon": polygon_geo,
                        "geo_polygon_array": np.array(
                            [[point["latitude"], point["longitude"]] for point in polygon_geo],
                            dtype=float,
                        ),
                        "geo_polygon_world": self._geo_arrays_to_world(
                            np.array([[point["latitude"], point["longitude"]] for point in polygon_geo], dtype=float)
                        ),
                        "navigation_samples": navigation_samples,
                        "navigation_array": navigation_array,
                        "navigation_world": self._geo_arrays_to_world(navigation_array),
                        "navigation_trace_indices": [sample["trace_index"] for sample in navigation_samples],
                        "render_navigation_samples": render_navigation_samples,
                        "render_navigation_world": self._geo_arrays_to_world(render_navigation_array),
                        "render_navigation_trace_indices": [float(sample["trace_index"]) for sample in render_navigation_samples],
                    }
                )
        return prepared_regions

    @staticmethod
    def _build_centerline_path(render_navigation_samples: list[dict[str, object]]) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        if not isinstance(render_navigation_samples, list) or not render_navigation_samples:
            return path
        first = render_navigation_samples[0]
        path.moveTo(float(first.get("screen_x", 0.0)), float(first.get("screen_y", 0.0)))
        for sample in render_navigation_samples[1:]:
            path.lineTo(float(sample.get("screen_x", 0.0)), float(sample.get("screen_y", 0.0)))
        return path

    @classmethod
    def _build_region_path(
        cls,
        screen_polygon: list[QtCore.QPointF],
        render_navigation_samples: list[dict[str, object]],
        centerline_path: QtGui.QPainterPath,
    ) -> QtGui.QPainterPath:
        del render_navigation_samples, centerline_path
        if isinstance(screen_polygon, list) and screen_polygon:
            return cls._polygon_path(screen_polygon)
        return QtGui.QPainterPath()

    @staticmethod
    def _polygon_path(points: list[QtCore.QPointF]) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        if not points:
            return path
        path.addPolygon(QtGui.QPolygonF(points))
        path.closeSubpath()
        path.setFillRule(QtCore.Qt.WindingFill)
        return path

    @staticmethod
    def _smooth_polygon_path(points: list[QtCore.QPointF]) -> QtGui.QPainterPath:
        cleaned: list[QtCore.QPointF] = []
        for point in points:
            if not cleaned:
                cleaned.append(point)
                continue
            dx = float(point.x() - cleaned[-1].x())
            dy = float(point.y() - cleaned[-1].y())
            if dx * dx + dy * dy >= 0.04:
                cleaned.append(point)
        if len(cleaned) >= 2:
            dx = float(cleaned[0].x() - cleaned[-1].x())
            dy = float(cleaned[0].y() - cleaned[-1].y())
            if dx * dx + dy * dy < 0.04:
                cleaned.pop()
        path = QtGui.QPainterPath()
        if len(cleaned) < 3:
            if cleaned:
                path.moveTo(cleaned[0])
                for point in cleaned[1:]:
                    path.lineTo(point)
            return path
        start = QtCore.QPointF(
            (cleaned[-1].x() + cleaned[0].x()) * 0.5,
            (cleaned[-1].y() + cleaned[0].y()) * 0.5,
        )
        path.moveTo(start)
        for index, point in enumerate(cleaned):
            next_point = cleaned[(index + 1) % len(cleaned)]
            mid = QtCore.QPointF(
                (point.x() + next_point.x()) * 0.5,
                (point.y() + next_point.y()) * 0.5,
            )
            path.quadTo(point, mid)
        path.closeSubpath()
        path.setFillRule(QtCore.Qt.WindingFill)
        return path

    @classmethod
    def _build_render_navigation_samples(
        cls,
        navigation_samples: list[dict[str, object]],
        width_m: float,
    ) -> list[dict[str, float]]:
        if not isinstance(navigation_samples, list) or len(navigation_samples) < 2:
            return list(navigation_samples or [])
        trace_positions = np.array([float(sample.get("trace_index", 0.0)) for sample in navigation_samples], dtype=float)
        latitudes = np.array([float(sample.get("latitude", 0.0)) for sample in navigation_samples], dtype=float)
        longitudes = np.array([float(sample.get("longitude", 0.0)) for sample in navigation_samples], dtype=float)
        window = min(15, max(5, ((len(navigation_samples) // 600) * 2) + 5))
        if window % 2 == 0:
            window += 1
        smooth_latitudes = cls._smooth_series(latitudes, window)
        smooth_longitudes = cls._smooth_series(longitudes, window)
        center_lat = float(np.mean(smooth_latitudes))
        center_lon = float(np.mean(smooth_longitudes))
        meters_per_deg_lat = 111320.0
        meters_per_deg_lon = max(111320.0 * float(np.cos(np.deg2rad(center_lat))), 1.0)
        x_coords = (smooth_longitudes - center_lon) * meters_per_deg_lon
        y_coords = (smooth_latitudes - center_lat) * meters_per_deg_lat
        cumulative = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(x_coords), np.diff(y_coords)))))
        if cumulative.size >= 2:
            keep_mask = np.concatenate((np.array([True]), np.diff(cumulative) > 1e-6))
            cumulative = cumulative[keep_mask]
            x_coords = x_coords[keep_mask]
            y_coords = y_coords[keep_mask]
            trace_positions = trace_positions[keep_mask]
        step_m = float(np.clip(max(float(width_m) * 0.08, 0.18), 0.18, 0.60))
        if cumulative.size >= 2 and cumulative[-1] > step_m * 2.0:
            target = np.arange(0.0, float(cumulative[-1]), step_m, dtype=float)
            if target.size == 0 or target[-1] < cumulative[-1]:
                target = np.append(target, cumulative[-1])
            if target.size > cls._MAX_RENDER_NAV_POINTS:
                target = np.linspace(0.0, float(cumulative[-1]), cls._MAX_RENDER_NAV_POINTS, dtype=float)
            x_coords = np.interp(target, cumulative, x_coords)
            y_coords = np.interp(target, cumulative, y_coords)
            trace_positions = np.interp(target, cumulative, trace_positions)
        resampled_latitudes = center_lat + (y_coords / meters_per_deg_lat)
        resampled_longitudes = center_lon + (x_coords / meters_per_deg_lon)
        return [
            {
                "trace_index": float(trace_positions[idx]),
                "latitude": float(resampled_latitudes[idx]),
                "longitude": float(resampled_longitudes[idx]),
            }
            for idx in range(len(resampled_latitudes))
        ]

    @staticmethod
    def _smooth_series(values: np.ndarray, window: int) -> np.ndarray:
        array = np.asarray(values, dtype=float)
        if array.size < 3:
            return array.copy()
        size = int(max(3, min(window, array.size if array.size % 2 == 1 else array.size - 1)))
        if size < 3:
            return array.copy()
        if size % 2 == 0:
            size -= 1
        if size < 3:
            return array.copy()
        pad = size // 2
        kernel = np.ones(size, dtype=float) / float(size)
        padded = np.pad(array, (pad, pad), mode="edge")
        smoothed = np.convolve(padded, kernel, mode="valid")
        smoothed[0] = array[0]
        smoothed[-1] = array[-1]
        return smoothed

    def _apply_pending_map_state(self) -> None:
        if self._pending_map_state is None:
            return
        self._center_lat, self._center_lon, self._zoom = self._pending_map_state
        self._center_world_x, self._center_world_y = self._geo_to_world(self._center_lat, self._center_lon)
        self._pending_map_state = None
        self._clear_layout_cache()
        self.update()

    def _finish_map_interaction(self) -> None:
        self._apply_pending_map_state()
        self.show()
        self.raise_()
        self.update()

    @staticmethod
    def _region_label_text(file_item: dict[str, object], region: dict[str, object]) -> str:
        file_name = str(file_item.get("file_name", "") or "")
        file_label = Path(file_name).stem if file_name else ""
        region_label = str(region.get("region_name", "") or "")
        return f"{file_label}  {region_label}".strip()

    def _geo_to_canvas(self, latitude: float, longitude: float, canvas_rect: QtCore.QRectF) -> QtCore.QPointF:
        world_x, world_y = self._geo_to_world(latitude, longitude)
        scale = self._world_scale()
        return QtCore.QPointF(
            canvas_rect.center().x() + (world_x - self._center_world_x) * scale,
            canvas_rect.center().y() + (world_y - self._center_world_y) * scale,
        )

    def _geo_arrays_to_world(self, coordinates: np.ndarray) -> np.ndarray:
        if coordinates.size == 0:
            return np.empty((0, 2), dtype=float)
        lat = np.clip(coordinates[:, 0].astype(float), -85.05112878, 85.05112878)
        lon = ((coordinates[:, 1].astype(float) + 180.0) % 360.0) - 180.0
        world_x = (lon + 180.0) / 360.0
        sin_lat = np.sin(np.deg2rad(lat))
        world_y = 0.5 - np.log((1 + sin_lat) / (1 - sin_lat)) / (4 * np.pi)
        return np.column_stack((world_x, world_y))

    def _world_arrays_to_canvas(self, coordinates: np.ndarray, canvas_rect: QtCore.QRectF) -> np.ndarray:
        if coordinates.size == 0:
            return np.empty((0, 2), dtype=float)
        scale = self._world_scale()
        screen_x = canvas_rect.center().x() + (coordinates[:, 0] - self._center_world_x) * scale
        screen_y = canvas_rect.center().y() + (coordinates[:, 1] - self._center_world_y) * scale
        return np.column_stack((screen_x, screen_y))

    @classmethod
    def _geo_to_global_pixel(cls, latitude: float, longitude: float, zoom: float) -> tuple[float, float]:
        zoom = max(float(zoom), 0.0)
        lat = float(np.clip(latitude, -85.05112878, 85.05112878))
        lon = ((float(longitude) + 180.0) % 360.0) - 180.0
        scale = cls._TILE_SIZE * (2**zoom)
        pixel_x = (lon + 180.0) / 360.0 * scale
        sin_lat = np.sin(np.deg2rad(lat))
        pixel_y = (0.5 - np.log((1 + sin_lat) / (1 - sin_lat)) / (4 * np.pi)) * scale
        return float(pixel_x), float(pixel_y)

    @staticmethod
    def _geo_to_world(latitude: float, longitude: float) -> tuple[float, float]:
        lat = float(np.clip(latitude, -85.05112878, 85.05112878))
        lon = ((float(longitude) + 180.0) % 360.0) - 180.0
        world_x = (lon + 180.0) / 360.0
        sin_lat = np.sin(np.deg2rad(lat))
        world_y = 0.5 - np.log((1 + sin_lat) / (1 - sin_lat)) / (4 * np.pi)
        return float(world_x), float(world_y)

    def _world_scale(self) -> float:
        return float(self._TILE_SIZE * (2 ** max(float(self._zoom), 0.0)))

    def _clear_layout_cache(self) -> None:
        self._layout_cache_key = None
        self._layout_cache = []

    @staticmethod
    def _region_polygon_geo_points(samples: list[dict[str, object]], width_m: float) -> list[dict[str, float]]:
        if len(samples) < 2:
            return []
        width = max(float(width_m), 1.2)
        latitudes = [float(sample["latitude"]) for sample in samples if sample.get("latitude") is not None]
        if not latitudes:
            return []
        center_lat = float(np.mean(latitudes))
        meters_per_deg_lat = 111320.0
        meters_per_deg_lon = max(111320.0 * float(np.cos(np.deg2rad(center_lat))), 1.0)
        half_width = width * 0.5
        left_side: list[dict[str, float]] = []
        right_side: list[dict[str, float]] = []
        points_xy = [
            np.array(
                [
                    float(sample["longitude"]) * meters_per_deg_lon,
                    float(sample["latitude"]) * meters_per_deg_lat,
                ],
                dtype=float,
            )
            for sample in samples
            if sample.get("latitude") is not None and sample.get("longitude") is not None
        ]
        if len(points_xy) < 2:
            return []
        for index, point in enumerate(points_xy):
            if index == 0:
                direction = points_xy[1] - points_xy[0]
            elif index == len(points_xy) - 1:
                direction = points_xy[-1] - points_xy[-2]
            else:
                direction = points_xy[index + 1] - points_xy[index - 1]
            norm = np.linalg.norm(direction)
            if norm <= 1e-6:
                direction = np.array([1.0, 0.0], dtype=float)
            else:
                direction = direction / norm
            normal = np.array([-direction[1], direction[0]], dtype=float)
            left_point = point + normal * half_width
            right_point = point - normal * half_width
            left_side.append(
                {
                    "longitude": float(left_point[0] / meters_per_deg_lon),
                    "latitude": float(left_point[1] / meters_per_deg_lat),
                }
            )
            right_side.append(
                {
                    "longitude": float(right_point[0] / meters_per_deg_lon),
                    "latitude": float(right_point[1] / meters_per_deg_lat),
                }
            )
        return left_side + list(reversed(right_side))

    def _region_at(self, point: QtCore.QPointF) -> tuple[str, QtGui.QPainterPath, dict[str, object]] | None:
        for item in reversed(self._layout_rects):
            if item[1].contains(point):
                return item
        return None


class _OverviewMeasurementOverlay(QtWidgets.QWidget):
    measurement_changed = QtCore.Signal(float, float)

    def __init__(self, parent: QtWidgets.QWidget, map_state_provider=None) -> None:
        super().__init__(parent)
        self._map_state_provider = map_state_provider
        self._enabled = False
        self._points: list[QtCore.QPointF] = []
        self._preview: QtCore.QPointF | None = None
        self._complete = False
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, False)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_measurement_mode(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, not self._enabled)
        if self._enabled:
            self.setCursor(QtCore.Qt.CrossCursor)
            self.show()
            self.raise_()
            self.setFocus(QtCore.Qt.MouseFocusReason)
        else:
            self.unsetCursor()
            self._preview = None
            self.hide()
        self.update()

    def clear_measurement(self) -> None:
        self._points.clear()
        self._preview = None
        self._complete = False
        self.measurement_changed.emit(0.0, 0.0)
        self.update()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._enabled or event.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(event)
            return
        point = self._event_position(event)
        if self._complete:
            self._points.clear()
            self._complete = False
        self._points.append(point)
        self._preview = None
        self._emit_measurement()
        self.update()
        self.setFocus(QtCore.Qt.MouseFocusReason)
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._enabled:
            super().mouseMoveEvent(event)
            return
        if self._points and not self._complete:
            self._preview = self._event_position(event)
            self._emit_measurement()
            self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self._enabled or event.button() != QtCore.Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        point = self._event_position(event)
        if not self._points or self._distance_px(self._points[-1], point) > 1.0:
            self._points.append(point)
        self._preview = None
        self._complete = True
        self._emit_measurement()
        self.update()
        event.accept()

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        if self._preview is not None:
            self._preview = None
            self._emit_measurement()
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if self._enabled and event.key() == QtCore.Qt.Key_Escape:
            self.clear_measurement()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        points = self._draw_points()
        if not points:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if len(points) >= 3:
            polygon = QtGui.QPolygonF(points)
            painter.setPen(QtGui.QPen(QtGui.QColor("#4169e1"), 2.0))
            painter.setBrush(QtGui.QColor(72, 128, 255, 42))
            painter.drawPolygon(polygon)
        if len(points) >= 2:
            painter.setPen(QtGui.QPen(QtGui.QColor("#4169e1"), 2.0, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
            path = QtGui.QPainterPath(points[0])
            for point in points[1:]:
                path.lineTo(point)
            painter.drawPath(path)
        painter.setPen(QtGui.QPen(QtGui.QColor("#2454c7"), 1.5))
        painter.setBrush(QtGui.QColor("#ffffff"))
        for point in self._points:
            painter.drawEllipse(point, 3.5, 3.5)

    def _draw_points(self) -> list[QtCore.QPointF]:
        points = list(self._points)
        if self._preview is not None and points and not self._complete:
            points.append(self._preview)
        return points

    def _emit_measurement(self) -> None:
        points = self._draw_points()
        length_px, area_px = self._screen_measurement_stats(points)
        meters_per_pixel = self._meters_per_pixel()
        self.measurement_changed.emit(length_px * meters_per_pixel, area_px * meters_per_pixel * meters_per_pixel)

    def _meters_per_pixel(self) -> float:
        if self._map_state_provider is None:
            return 1.0
        try:
            latitude, zoom = self._map_state_provider()
            latitude = float(latitude)
            zoom = float(zoom)
        except (TypeError, ValueError):
            return 1.0
        return max(float(math.cos(math.radians(latitude)) * 2.0 * math.pi * 6378137.0 / (256.0 * (2.0**zoom))), 1e-6)

    @staticmethod
    def _screen_measurement_stats(points: list[QtCore.QPointF]) -> tuple[float, float]:
        if len(points) < 2:
            return 0.0, 0.0
        length = 0.0
        for first, second in zip(points, points[1:]):
            length += _OverviewMeasurementOverlay._distance_px(first, second)
        area = 0.0
        if len(points) >= 3:
            xy = [(point.x(), point.y()) for point in points]
            signed = 0.0
            for index, (x1, y1) in enumerate(xy):
                x2, y2 = xy[(index + 1) % len(xy)]
                signed += x1 * y2 - y1 * x2
            area = abs(signed) * 0.5
        return float(length), float(area)

    @staticmethod
    def _distance_px(first: QtCore.QPointF, second: QtCore.QPointF) -> float:
        return float(math.hypot(second.x() - first.x(), second.y() - first.y()))

    @staticmethod
    def _event_position(event: QtGui.QMouseEvent) -> QtCore.QPointF:
        if hasattr(event, "position"):
            return QtCore.QPointF(event.position())
        return QtCore.QPointF(event.pos())


class OverviewQuickMapWidget(QtWidgets.QWidget):
    region_activated = QtCore.Signal(str)
    point_selected = QtCore.Signal(str, int, int)
    measurement_changed = QtCore.Signal(float, float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._files: list[dict[str, object]] = []
        self._active_region_id = ""
        self._active_file_id = ""
        self._active_trace = 0
        self._active_region_name = ""
        self._active_interface_name = ""
        self._bridge = OverviewQuickBridge(self)
        self._overlay = OverviewOverlayWidget(self)
        self._overlay.region_activated.connect(self.region_activated)
        self._overlay.point_selected.connect(self.point_selected)
        self._measurement_overlay = _OverviewMeasurementOverlay(self, self._measurement_map_state)
        self._measurement_overlay.measurement_changed.connect(self.measurement_changed)
        self._bridge.mapStateChanged.connect(self._on_map_state_changed)
        self._bridge.mapTapped.connect(self._on_map_tapped)
        self._bridge.set_offline_tile_cache_directory(_qt_location_cache_directory("qtlocation_offline_tiles_only_v1"))
        offline_roots = OnlineMapConfigStore.offline_tiles_roots()
        self._offline_root = offline_roots[0] if offline_roots else None
        self._offline_tile_server = OfflineTileServer(offline_roots, self) if offline_roots else None
        offline_dir = str(self._offline_root) if self._offline_root is not None else ""
        self._bridge.set_offline_directory(offline_dir)
        if self._offline_tile_server is not None:
            self._bridge.set_offline_tile_host(self._offline_tile_server.start())
        coverage = OnlineMapConfigStore.offline_tiles_coverage()
        if coverage is not None:
            self._apply_offline_coverage(coverage)
        self._quick = QtQuickWidgets.QQuickWidget(self)
        self._quick.setResizeMode(QtQuickWidgets.QQuickWidget.SizeRootObjectToView)
        self._quick.setClearColor(QtCore.Qt.transparent)
        self._quick.rootContext().setContextProperty("overviewBridge", self._bridge)
        qml_path = Path(__file__).resolve().parents[1] / "resources" / "overview" / "overview_map.qml"
        _attach_quick_widget_diagnostics(self._quick, widget_name="OfflineOverviewMap", qml_path=qml_path)
        self._quick.setSource(QtCore.QUrl.fromLocalFile(str(qml_path)))
        layout = QtWidgets.QStackedLayout(self)
        layout.setStackingMode(QtWidgets.QStackedLayout.StackAll)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._quick)
        layout.addWidget(self._overlay)
        layout.addWidget(self._measurement_overlay)
        self._overlay.raise_()
        self._last_bounds_signature: tuple[object, ...] | None = None
        self.destroyed.connect(self._shutdown_tile_server)

    def set_online_map_config(self, _config) -> None:
        return

    def set_map_mode(self, _mode: str) -> None:
        return

    def clear_scene(self) -> None:
        self._files = []
        self._active_region_id = ""
        self._active_file_id = ""
        self._active_trace = 0
        self._active_region_name = ""
        self._active_interface_name = ""
        self._overlay.clear_scene()
        self._overlay.raise_()
        self._measurement_overlay.raise_()

    def set_scene(
        self,
        files: list[dict[str, object]],
        *,
        active_region_id: str,
        active_file_id: str,
        active_trace: int = 0,
        map_image: QtGui.QImage | None = None,
        active_region_name: str = "",
        active_interface_name: str = "",
        cache_root_path: str = "",
    ) -> None:
        del map_image, cache_root_path
        self._files = list(files)
        logger.info("OfflineOverviewMap: set_scene files=%s active_region=%s active_file=%s", len(self._files), active_region_id, active_file_id)
        self._active_region_id = active_region_id
        self._active_file_id = active_file_id
        self._active_trace = int(active_trace)
        self._active_region_name = active_region_name
        self._active_interface_name = active_interface_name
        self._overlay.set_scene(
            self._files,
            active_region_id=self._active_region_id,
            active_file_id=self._active_file_id,
            active_trace=self._active_trace,
            active_region_name=self._active_region_name,
            active_interface_name=self._active_interface_name,
        )
        self._overlay.raise_()
        self._measurement_overlay.raise_()
        bounds = self._scene_geo_bounds()
        if bounds is None:
            logger.info("OfflineOverviewMap: scene bounds unavailable")
            return
        signature = tuple(round(value, 8) for value in bounds)
        if signature != self._last_bounds_signature:
            self._last_bounds_signature = signature
            logger.info("OfflineOverviewMap: scene bounds updated to %s", bounds)
            root = self._quick.rootObject()
            if root is not None:
                root.setProperty("sceneMinLat", bounds[0])
                root.setProperty("sceneMinLon", bounds[1])
                root.setProperty("sceneMaxLat", bounds[2])
                root.setProperty("sceneMaxLon", bounds[3])
                QtCore.QMetaObject.invokeMethod(root, "fitSceneBounds")

    def _scene_geo_bounds(self) -> tuple[float, float, float, float] | None:
        lats: list[float] = []
        lons: list[float] = []
        for file_item in self._files:
            for sample in file_item.get("navigation_samples", []):
                lat = sample.get("latitude")
                lon = sample.get("longitude")
                if lat is None or lon is None:
                    continue
                lats.append(float(lat))
                lons.append(float(lon))
        if not lats or not lons:
            return None
        return (min(lats), min(lons), max(lats), max(lons))

    def _apply_offline_coverage(self, coverage: OfflineTileCoverage) -> None:
        native_max_zoom = int(coverage.max_zoom)
        self._bridge.set_offline_min_zoom(int(coverage.min_zoom))
        self._bridge.set_offline_max_zoom(min(native_max_zoom + 2, 17))

    def _shutdown_tile_server(self) -> None:
        if self._offline_tile_server is not None:
            self._offline_tile_server.stop()

    def _on_map_state_changed(self, latitude: float, longitude: float, zoom: float) -> None:
        if hasattr(self, "_overlay"):
            self._overlay.set_map_state(latitude, longitude, zoom)

    def _on_map_tapped(self, x: float, y: float) -> None:
        if self._measurement_overlay.isVisible():
            return
        if hasattr(self, "_overlay"):
            self._overlay.handle_tap(QtCore.QPointF(float(x), float(y)))

    def set_measurement_mode(self, enabled: bool) -> None:
        self._measurement_overlay.set_measurement_mode(enabled)
        if enabled:
            self._measurement_overlay.raise_()
        else:
            self._overlay.raise_()

    def clear_measurement(self) -> None:
        self._measurement_overlay.clear_measurement()

    def _measurement_map_state(self) -> tuple[float, float]:
        return float(getattr(self._overlay, "_center_lat", 0.0)), float(getattr(self._overlay, "_zoom", 1.0))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._overlay.raise_()
        if self._measurement_overlay.isVisible():
            self._measurement_overlay.raise_()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        logger.info("OfflineOverviewMap: widget shown size=%sx%s", self.width(), self.height())

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        super().hideEvent(event)
        logger.info("OfflineOverviewMap: widget hidden")


class OverviewOnlineQuickMapWidget(QtWidgets.QWidget):
    region_activated = QtCore.Signal(str)
    point_selected = QtCore.Signal(str, int, int)
    measurement_changed = QtCore.Signal(float, float)

    def __init__(self, map_config, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._map_config = map_config
        self._files: list[dict[str, object]] = []
        self._active_region_id = ""
        self._active_file_id = ""
        self._active_trace = 0
        self._active_region_name = ""
        self._active_interface_name = ""
        self._cache_root_path = ""
        self._bridge = OverviewOnlineBridge(self)
        self._bridge.set_online_min_zoom(3)
        self._bridge.set_online_max_zoom(19)
        self._bridge.set_online_tile_cache_directory(_qt_location_cache_directory("qtlocation_online_proxy_only_v1"))
        self._overlay = OverviewOverlayWidget(self)
        self._overlay.region_activated.connect(self.region_activated)
        self._overlay.point_selected.connect(self.point_selected)
        self._measurement_overlay = _OverviewMeasurementOverlay(self, self._measurement_map_state)
        self._measurement_overlay.measurement_changed.connect(self.measurement_changed)
        self._bridge.mapStateChanged.connect(self._on_map_state_changed)
        self._bridge.mapTapped.connect(self._on_map_tapped)
        self._online_tile_server = OnlineTileServer(self._map_config, self)
        self._bridge.set_online_tile_host(self._online_tile_server.start())
        self._quick = QtQuickWidgets.QQuickWidget(self)
        self._quick.setResizeMode(QtQuickWidgets.QQuickWidget.SizeRootObjectToView)
        self._quick.setClearColor(QtCore.Qt.transparent)
        self._quick.rootContext().setContextProperty("overviewBridge", self._bridge)
        qml_path = Path(__file__).resolve().parents[1] / "resources" / "overview" / "overview_online_map.qml"
        _attach_quick_widget_diagnostics(self._quick, widget_name="OnlineOverviewMap", qml_path=qml_path)
        self._quick.setSource(QtCore.QUrl.fromLocalFile(str(qml_path)))
        layout = QtWidgets.QStackedLayout(self)
        layout.setStackingMode(QtWidgets.QStackedLayout.StackAll)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._quick)
        layout.addWidget(self._overlay)
        layout.addWidget(self._measurement_overlay)
        self._overlay.raise_()
        self._last_bounds_signature: tuple[object, ...] | None = None
        self.destroyed.connect(self._shutdown_tile_server)

    def set_online_map_config(self, config) -> None:
        self._map_config = config
        self._online_tile_server.set_online_map_config(config)

    def set_map_mode(self, _mode: str) -> None:
        self._bridge.set_online_tile_host(self._online_tile_server.start())

    def clear_scene(self) -> None:
        self._files = []
        self._active_region_id = ""
        self._active_file_id = ""
        self._active_trace = 0
        self._active_region_name = ""
        self._active_interface_name = ""
        self._overlay.clear_scene()
        self._overlay.raise_()
        self._measurement_overlay.raise_()

    def set_scene(
        self,
        files: list[dict[str, object]],
        *,
        active_region_id: str,
        active_file_id: str,
        active_trace: int = 0,
        map_image: QtGui.QImage | None = None,
        active_region_name: str = "",
        active_interface_name: str = "",
        cache_root_path: str = "",
    ) -> None:
        del map_image
        if cache_root_path != self._cache_root_path:
            self._cache_root_path = cache_root_path
            self._online_tile_server.set_cache_root(cache_root_path)
        raw_files = list(files)
        self._files = _transform_files_to_gcj02(raw_files)
        logger.info(
            "OnlineOverviewMap: set_scene files=%s active_region=%s active_file=%s cache_root=%s coordinate_mode=gcj02",
            len(self._files),
            active_region_id,
            active_file_id,
            self._cache_root_path,
        )
        self._active_region_id = active_region_id
        self._active_file_id = active_file_id
        self._active_trace = int(active_trace)
        self._active_region_name = active_region_name
        self._active_interface_name = active_interface_name
        self._overlay.set_scene(
            self._files,
            active_region_id=self._active_region_id,
            active_file_id=self._active_file_id,
            active_trace=self._active_trace,
            active_region_name=self._active_region_name,
            active_interface_name=self._active_interface_name,
        )
        self._overlay.raise_()
        self._measurement_overlay.raise_()
        bounds = self._scene_geo_bounds()
        if bounds is None:
            logger.info("OnlineOverviewMap: scene bounds unavailable")
            return
        signature = tuple(round(value, 8) for value in bounds)
        if signature != self._last_bounds_signature:
            self._last_bounds_signature = signature
            logger.info("OnlineOverviewMap: scene bounds updated to %s", bounds)
            root = self._quick.rootObject()
            if root is not None:
                root.setProperty("sceneMinLat", bounds[0])
                root.setProperty("sceneMinLon", bounds[1])
                root.setProperty("sceneMaxLat", bounds[2])
                root.setProperty("sceneMaxLon", bounds[3])
                QtCore.QMetaObject.invokeMethod(root, "fitSceneBounds")

    def _scene_geo_bounds(self) -> tuple[float, float, float, float] | None:
        lats: list[float] = []
        lons: list[float] = []
        for file_item in self._files:
            for sample in file_item.get("navigation_samples", []):
                lat = sample.get("latitude")
                lon = sample.get("longitude")
                if lat is None or lon is None:
                    continue
                lats.append(float(lat))
                lons.append(float(lon))
        if not lats or not lons:
            return None
        return (min(lats), min(lons), max(lats), max(lons))

    def _shutdown_tile_server(self) -> None:
        self._online_tile_server.stop()

    def _on_map_state_changed(self, latitude: float, longitude: float, zoom: float) -> None:
        self._overlay.set_map_state(latitude, longitude, zoom)

    def _on_map_tapped(self, x: float, y: float) -> None:
        if self._measurement_overlay.isVisible():
            return
        self._overlay.handle_tap(QtCore.QPointF(float(x), float(y)))

    def set_measurement_mode(self, enabled: bool) -> None:
        self._measurement_overlay.set_measurement_mode(enabled)
        if enabled:
            self._measurement_overlay.raise_()
        else:
            self._overlay.raise_()

    def clear_measurement(self) -> None:
        self._measurement_overlay.clear_measurement()

    def _measurement_map_state(self) -> tuple[float, float]:
        return float(getattr(self._overlay, "_center_lat", 0.0)), float(getattr(self._overlay, "_zoom", 1.0))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._overlay.raise_()
        if self._measurement_overlay.isVisible():
            self._measurement_overlay.raise_()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        logger.info("OnlineOverviewMap: widget shown size=%sx%s", self.width(), self.height())

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        super().hideEvent(event)
        logger.info("OnlineOverviewMap: widget hidden")


class OverviewMapHostWidget(QtWidgets.QWidget):
    region_activated = QtCore.Signal(str)
    point_selected = QtCore.Signal(str, int, int)
    measurement_changed = QtCore.Signal(float, float)

    def __init__(
        self,
        *,
        offline_widget: OverviewQuickMapWidget,
        online_widget: QtWidgets.QWidget,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._offline_widget = offline_widget
        self._online_widget = online_widget
        self._mode = "offline"
        self._measurement_mode = False
        self._last_scene_kwargs: dict[str, object] | None = None
        self._stack = QtWidgets.QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._offline_widget)
        self._stack.addWidget(self._online_widget)
        self._stack.setCurrentWidget(self._offline_widget)
        self._offline_widget.region_activated.connect(self.region_activated)
        self._offline_widget.point_selected.connect(self.point_selected)
        if hasattr(self._online_widget, "region_activated"):
            self._online_widget.region_activated.connect(self.region_activated)
        if hasattr(self._online_widget, "point_selected"):
            self._online_widget.point_selected.connect(self.point_selected)
        for widget in (self._offline_widget, self._online_widget):
            if hasattr(widget, "measurement_changed"):
                widget.measurement_changed.connect(self.measurement_changed)

    def set_online_map_config(self, config) -> None:
        if hasattr(self._online_widget, "set_online_map_config"):
            self._online_widget.set_online_map_config(config)
        if hasattr(self._offline_widget, "set_online_map_config"):
            self._offline_widget.set_online_map_config(config)

    def set_map_mode(self, mode: str) -> None:
        normalized = "online" if str(mode).strip().lower() == "online" else "offline"
        if normalized == self._mode:
            return
        self._mode = normalized
        widget = self._online_widget if self._mode == "online" else self._offline_widget
        if hasattr(widget, "set_map_mode"):
            widget.set_map_mode(self._mode)
        self._stack.setCurrentWidget(widget)
        if self._last_scene_kwargs is not None:
            widget.set_scene(**self._last_scene_kwargs)
        self.set_measurement_mode(self._measurement_mode)

    def clear_scene(self) -> None:
        self._last_scene_kwargs = None
        self._offline_widget.clear_scene()
        if hasattr(self._online_widget, "clear_scene"):
            self._online_widget.clear_scene()

    def set_measurement_mode(self, enabled: bool) -> None:
        self._measurement_mode = bool(enabled)
        target = self._online_widget if self._mode == "online" else self._offline_widget
        for widget in (self._offline_widget, self._online_widget):
            if hasattr(widget, "set_measurement_mode"):
                widget.set_measurement_mode(self._measurement_mode and widget is target)

    def clear_measurement(self) -> None:
        for widget in (self._offline_widget, self._online_widget):
            if hasattr(widget, "clear_measurement"):
                widget.clear_measurement()
        self.measurement_changed.emit(0.0, 0.0)

    def set_scene(self, files, **kwargs) -> None:
        payload = {"files": files, **kwargs}
        self._last_scene_kwargs = payload
        target = self._online_widget if self._mode == "online" else self._offline_widget
        target.set_scene(**payload)
