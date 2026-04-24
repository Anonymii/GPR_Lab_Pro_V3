from __future__ import annotations

import http.server
import socketserver
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtQml, QtQuickWidgets, QtWidgets

from gpr_lab_pro.infrastructure.online_map import OfflineTileCoverage, OnlineMapConfigStore


class _OfflineTileRequestHandler(http.server.BaseHTTPRequestHandler):
    server: "_OfflineTileHTTPServer"

    def do_GET(self) -> None:  # noqa: N802
        tile_path = self.server.tile_server.resolve_request_path(self.path)
        if tile_path is None or not tile_path.exists():
            self.send_error(404)
            return
        try:
            payload = tile_path.read_bytes()
        except OSError:
            self.send_error(500)
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
    def __init__(self, root: Path, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._root = root
        self._httpd: _OfflineTileHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._base_url = ""
        self.request_count = 0
        self.hit_count = 0
        self.miss_count = 0
        self.last_request_path = ""

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
        return self._base_url

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError:
                pass
        self._httpd = None
        self._thread = None
        self._base_url = ""

    def resolve_request_path(self, request_path: str) -> Path | None:
        self.request_count += 1
        self.last_request_path = str(request_path)
        normalized = request_path.split("?", 1)[0].strip("/")
        parts = normalized.split("/")
        if len(parts) != 4 or parts[0] != "tiles":
            self.miss_count += 1
            return None
        try:
            zoom = int(parts[1])
            tile_x = int(parts[2])
            tile_y = int(parts[3].removesuffix(".png"))
        except ValueError:
            self.miss_count += 1
            return None
        tile_name = f"osm_100-l-3-{zoom}-{tile_x}-{tile_y}.png"
        candidate = self._root / tile_name
        if candidate.exists():
            self.hit_count += 1
            return candidate
        self.miss_count += 1
        return None


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
    _MAP_TILE_TEMPLATE_AMAP = "http://wprd03.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scl=1&style=7&x={x}&y={y}&z={z}"

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
        return self._base_url

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except OSError:
                pass
        self._httpd = None
        self._thread = None
        self._base_url = ""

    def load_tile_payload(self, request_path: str) -> bytes | None:
        self.request_count += 1
        self.last_request_path = str(request_path)
        key = self._parse_request_key(request_path)
        if key is None:
            self.miss_count += 1
            return None
        cache_path = self._tile_cache_path(key)
        if cache_path.exists():
            try:
                payload = cache_path.read_bytes()
            except OSError:
                payload = b""
            if payload:
                self.hit_count += 1
                return payload
        payload = self._fetch_remote_tile(key)
        if not payload:
            self.miss_count += 1
            return None
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(payload)
        except OSError:
            pass
        self.fetch_count += 1
        self._cleanup_cache_if_needed()
        return payload

    def _parse_request_key(self, request_path: str) -> tuple[int, int, int] | None:
        normalized = request_path.split("?", 1)[0].strip("/")
        parts = normalized.split("/")
        if len(parts) == 4 and parts[0] == "tiles":
            parts = parts[1:]
        if len(parts) != 3:
            return None
        try:
            zoom = int(parts[0])
            tile_x = int(parts[1])
            tile_y = int(parts[2].removesuffix(".png"))
        except ValueError:
            return None
        return (zoom, tile_x, tile_y)

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
        url = self._MAP_TILE_TEMPLATE_AMAP.format(z=zoom, x=tile_x, y=tile_y)
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
            self.last_error = str(exc)
            return None
        if not payload:
            self.last_error = f"空瓦片返回: {url}"
            return None
        return payload

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


class OverviewOnlineBridge(QtCore.QObject):
    mapReady = QtCore.Signal()
    mapStateChanged = QtCore.Signal(float, float, float)
    mapTapped = QtCore.Signal(float, float)
    onlineTileHostChanged = QtCore.Signal()
    onlineMinZoomChanged = QtCore.Signal()
    onlineMaxZoomChanged = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._online_tile_host = ""
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
    offlineMinZoomChanged = QtCore.Signal()
    offlineMaxZoomChanged = QtCore.Signal()
    sceneBoundsChanged = QtCore.Signal(float, float, float, float)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._offline_directory = ""
        self._offline_tile_host = ""
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
        self._pending_map_state: tuple[float, float, float] | None = None
        self._map_state_timer = QtCore.QTimer(self)
        self._map_state_timer.setSingleShot(True)
        self._map_state_timer.setInterval(16)
        self._map_state_timer.timeout.connect(self._apply_pending_map_state)

    def clear_scene(self) -> None:
        self._files = []
        self._prepared_regions = []
        self._active_region_id = ""
        self._active_file_id = ""
        self._active_trace = 0
        self._layout_rects = []
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
        self.update()

    def set_map_state(self, latitude: float, longitude: float, zoom: float) -> None:
        state = (float(latitude), float(longitude), float(zoom))
        if state == self._pending_map_state:
            return
        self._pending_map_state = state
        if not self._map_state_timer.isActive():
            self._map_state_timer.start()

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
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self._layout_rects = self._compute_layout(self.rect())
        for region_id, path, item in self._layout_rects:
            preview_image = item.get("preview_image")
            if isinstance(preview_image, QtGui.QImage) and not preview_image.isNull():
                painter.save()
                painter.setClipPath(path)
                geometry = self._region_screen_geometry(item)
                if geometry is not None:
                    painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
                    painter.translate(geometry["center"])
                    painter.rotate(geometry["angle_deg"])
                    painter.drawImage(geometry["target_rect_local"], preview_image)
                else:
                    polygon_points = item.get("screen_polygon", [])
                    if polygon_points:
                        polygon = QtGui.QPolygonF(polygon_points)
                        painter.drawImage(polygon.boundingRect(), preview_image)
                painter.restore()
            border_color = QtGui.QColor("#ff9500" if region_id == self._active_region_id else "#ff8c00")
            border_pen = QtGui.QPen(border_color, 2.0 if region_id == self._active_region_id else 1.6)
            painter.setPen(border_pen)
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawPath(path)
            self._draw_region_label(painter, item)

    def _draw_region_label(self, painter: QtGui.QPainter, item: dict[str, object]) -> None:
        geometry = self._region_screen_geometry(item)
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
        rects: list[tuple[str, QtGui.QPainterPath, dict[str, object]]] = []
        canvas_rect = QtCore.QRectF(canvas)
        for prepared in self._prepared_regions:
            polygon_geo_array = prepared.get("geo_polygon_array")
            if not isinstance(polygon_geo_array, np.ndarray) or polygon_geo_array.size == 0:
                continue
            polygon_screen = self._geo_arrays_to_canvas(polygon_geo_array, canvas_rect)
            polygon = QtGui.QPolygonF([QtCore.QPointF(float(x), float(y)) for x, y in polygon_screen])
            path = QtGui.QPainterPath()
            if not polygon.isEmpty():
                path.moveTo(polygon.first())
                for idx in range(1, polygon.size()):
                    path.lineTo(polygon.at(idx))
                path.closeSubpath()
            item = dict(prepared)
            item["screen_polygon"] = [polygon.at(idx) for idx in range(polygon.size())]
            navigation_array = prepared.get("navigation_array")
            trace_indices = prepared.get("navigation_trace_indices", [])
            if isinstance(navigation_array, np.ndarray) and navigation_array.size > 0:
                navigation_screen = self._geo_arrays_to_canvas(navigation_array, canvas_rect)
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
            rects.append((str(prepared.get("region_id", "")), path, item))
        return rects

    @staticmethod
    def _region_screen_geometry(item: dict[str, object]) -> dict[str, object] | None:
        samples = item.get("navigation_samples", [])
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

    def _prepare_regions(self, files: list[dict[str, object]]) -> list[dict[str, object]]:
        prepared_regions: list[dict[str, object]] = []
        for file_item in files:
            file_id = str(file_item.get("file_id", "") or "")
            file_name = str(file_item.get("file_name", "") or "")
            for region in file_item.get("regions", []):
                polygon_points = region.get("navigation_samples", [])
                if not polygon_points:
                    continue
                polygon_geo = self._region_polygon_geo_points(polygon_points, float(region.get("render_width", 0.0)))
                if len(polygon_geo) < 3:
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
                navigation_array = (
                    np.array([[sample["latitude"], sample["longitude"]] for sample in navigation_samples], dtype=float)
                    if navigation_samples
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
                        "navigation_samples": navigation_samples,
                        "navigation_array": navigation_array,
                        "navigation_trace_indices": [sample["trace_index"] for sample in navigation_samples],
                    }
                )
        return prepared_regions

    def _apply_pending_map_state(self) -> None:
        if self._pending_map_state is None:
            return
        self._center_lat, self._center_lon, self._zoom = self._pending_map_state
        self._pending_map_state = None
        self.update()

    @staticmethod
    def _region_label_text(file_item: dict[str, object], region: dict[str, object]) -> str:
        file_name = str(file_item.get("file_name", "") or "")
        file_label = Path(file_name).stem if file_name else ""
        region_label = str(region.get("region_name", "") or "")
        return f"{file_label}  {region_label}".strip()

    def _geo_to_canvas(self, latitude: float, longitude: float, canvas_rect: QtCore.QRectF) -> QtCore.QPointF:
        center_px_x, center_px_y = self._geo_to_global_pixel(self._center_lat, self._center_lon, float(self._zoom))
        pixel_x, pixel_y = self._geo_to_global_pixel(latitude, longitude, float(self._zoom))
        return QtCore.QPointF(
            canvas_rect.center().x() + (pixel_x - center_px_x),
            canvas_rect.center().y() + (pixel_y - center_px_y),
        )

    def _geo_arrays_to_canvas(self, coordinates: np.ndarray, canvas_rect: QtCore.QRectF) -> np.ndarray:
        if coordinates.size == 0:
            return np.empty((0, 2), dtype=float)
        zoom = max(float(self._zoom), 0.0)
        center_px_x, center_px_y = self._geo_to_global_pixel(self._center_lat, self._center_lon, zoom)
        lat = np.clip(coordinates[:, 0].astype(float), -85.05112878, 85.05112878)
        lon = ((coordinates[:, 1].astype(float) + 180.0) % 360.0) - 180.0
        scale = self._TILE_SIZE * (2**zoom)
        pixel_x = (lon + 180.0) / 360.0 * scale
        sin_lat = np.sin(np.deg2rad(lat))
        pixel_y = (0.5 - np.log((1 + sin_lat) / (1 - sin_lat)) / (4 * np.pi)) * scale
        screen_x = canvas_rect.center().x() + (pixel_x - center_px_x)
        screen_y = canvas_rect.center().y() + (pixel_y - center_px_y)
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


class OverviewQuickMapWidget(QtWidgets.QWidget):
    region_activated = QtCore.Signal(str)
    point_selected = QtCore.Signal(str, int, int)

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
        self._bridge.mapStateChanged.connect(self._on_map_state_changed)
        self._bridge.mapTapped.connect(self._on_map_tapped)
        offline_roots = OnlineMapConfigStore.offline_tiles_roots()
        self._offline_root = offline_roots[0] if offline_roots else None
        self._offline_tile_server = OfflineTileServer(self._offline_root, self) if self._offline_root is not None else None
        offline_dir = QtCore.QUrl.fromLocalFile(str(self._offline_root)).toString() if self._offline_root is not None else ""
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
        self._quick.setSource(QtCore.QUrl.fromLocalFile(str(qml_path)))
        layout = QtWidgets.QStackedLayout(self)
        layout.setStackingMode(QtWidgets.QStackedLayout.StackAll)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._quick)
        layout.addWidget(self._overlay)
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
        bounds = self._scene_geo_bounds()
        if bounds is None:
            return
        signature = tuple(round(value, 8) for value in bounds)
        if signature != self._last_bounds_signature:
            self._last_bounds_signature = signature
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
        self._bridge.set_offline_min_zoom(int(coverage.min_zoom))
        self._bridge.set_offline_max_zoom(int(coverage.max_zoom))

    def _shutdown_tile_server(self) -> None:
        if self._offline_tile_server is not None:
            self._offline_tile_server.stop()

    def _on_map_state_changed(self, latitude: float, longitude: float, zoom: float) -> None:
        if hasattr(self, "_overlay"):
            self._overlay.set_map_state(latitude, longitude, zoom)
            self._overlay.raise_()

    def _on_map_tapped(self, x: float, y: float) -> None:
        if hasattr(self, "_overlay"):
            self._overlay.handle_tap(QtCore.QPointF(float(x), float(y)))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._overlay.raise_()


class OverviewOnlineQuickMapWidget(QtWidgets.QWidget):
    region_activated = QtCore.Signal(str)
    point_selected = QtCore.Signal(str, int, int)

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
        self._overlay = OverviewOverlayWidget(self)
        self._overlay.region_activated.connect(self.region_activated)
        self._overlay.point_selected.connect(self.point_selected)
        self._bridge.mapStateChanged.connect(self._on_map_state_changed)
        self._bridge.mapTapped.connect(self._on_map_tapped)
        self._online_tile_server = OnlineTileServer(self._map_config, self)
        self._bridge.set_online_tile_host(self._online_tile_server.start())
        self._quick = QtQuickWidgets.QQuickWidget(self)
        self._quick.setResizeMode(QtQuickWidgets.QQuickWidget.SizeRootObjectToView)
        self._quick.setClearColor(QtCore.Qt.transparent)
        self._quick.rootContext().setContextProperty("overviewBridge", self._bridge)
        qml_path = Path(__file__).resolve().parents[1] / "resources" / "overview" / "overview_online_map.qml"
        self._quick.setSource(QtCore.QUrl.fromLocalFile(str(qml_path)))
        layout = QtWidgets.QStackedLayout(self)
        layout.setStackingMode(QtWidgets.QStackedLayout.StackAll)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._quick)
        layout.addWidget(self._overlay)
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
        self._files = list(files)
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
        bounds = self._scene_geo_bounds()
        if bounds is None:
            return
        signature = tuple(round(value, 8) for value in bounds)
        if signature != self._last_bounds_signature:
            self._last_bounds_signature = signature
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
        self._overlay.raise_()

    def _on_map_tapped(self, x: float, y: float) -> None:
        self._overlay.handle_tap(QtCore.QPointF(float(x), float(y)))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._overlay.raise_()


class OverviewMapHostWidget(QtWidgets.QWidget):
    region_activated = QtCore.Signal(str)
    point_selected = QtCore.Signal(str, int, int)

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

    def clear_scene(self) -> None:
        self._last_scene_kwargs = None
        self._offline_widget.clear_scene()
        if hasattr(self._online_widget, "clear_scene"):
            self._online_widget.clear_scene()

    def set_scene(self, files, **kwargs) -> None:
        payload = {"files": files, **kwargs}
        self._last_scene_kwargs = payload
        target = self._online_widget if self._mode == "online" else self._offline_widget
        target.set_scene(**payload)
