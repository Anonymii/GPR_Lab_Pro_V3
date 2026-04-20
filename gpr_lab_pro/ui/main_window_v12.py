from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from matplotlib import cm
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from PySide6 import QtCore, QtGui, QtWidgets

from gpr_lab_pro.application import GPRApplication
from gpr_lab_pro.domain.enums import StepKind
from gpr_lab_pro.domain.models.display import DisplayData
from gpr_lab_pro.processing.catalog_v11 import OperationSpec, SPEC_BY_TYPE
from gpr_lab_pro.processing.module_registry_v11 import MODULE_SPECS

if "MPLCONFIGDIR" not in os.environ:
    mpl_cache_dir = Path(os.environ.get("TEMP", ".")) / "GPR_Lab_Pro_V3" / "mplconfig"
    mpl_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MPLCONFIGDIR"] = str(mpl_cache_dir)


TITLE_FONT = FontProperties(family=["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "sans-serif"])


class NewProjectDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建工程")
        self.resize(520, 180)
        layout = QtWidgets.QFormLayout(self)
        layout.setLabelAlignment(QtCore.Qt.AlignRight)
        self.name_edit = QtWidgets.QLineEdit("GPR_Lab_Pro_V3_Project")
        self.path_edit = QtWidgets.QLineEdit(r"E:\code_management\GPR_V12_Pyside\GPR_Lab_Pro_V3\projects")
        browse_button = QtWidgets.QPushButton("浏览...")
        browse_button.clicked.connect(self._browse_dir)
        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self.path_edit)
        path_row.addWidget(browse_button)
        layout.addRow("工程名称", self.name_edit)
        layout.addRow("工程路径", path_row)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _browse_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择工程目录", self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def values(self) -> tuple[str, str]:
        return self.name_edit.text().strip(), self.path_edit.text().strip()


class ImportSummaryDialog(QtWidgets.QDialog):
    def __init__(self, dataset, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导入成果")
        self.resize(480, 240)
        layout = QtWidgets.QVBoxLayout(self)
        summary = QtWidgets.QLabel(
            "\n".join(
                [
                    f"文件名: {dataset.filename}",
                    f"数据规模: {dataset.sample_count} × {dataset.trace_count} × {dataset.line_count}",
                    f"起止频率: {dataset.header.get('start_frequency_hz', 0) / 1e6:.1f} MHz - {dataset.header.get('end_frequency_hz', 0) / 1e6:.1f} MHz",
                    f"默认时频转换: {dataset.transform_name.upper()}",
                ]
            )
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class RegionBoundsDialog(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        region_name: str,
        trace_count: int,
        line_count: int,
        sample_count: int,
        current_bounds: dict[str, int],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"区域范围 - {region_name}")
        self.resize(420, 240)
        layout = QtWidgets.QFormLayout(self)
        layout.setLabelAlignment(QtCore.Qt.AlignRight)

        self.trace_start_spin = self._make_spinbox(1, max(trace_count, 1), current_bounds["trace_start"] + 1)
        self.trace_stop_spin = self._make_spinbox(1, max(trace_count, 1), current_bounds["trace_stop"])
        self.line_start_spin = self._make_spinbox(1, max(line_count, 1), current_bounds["line_start"] + 1)
        self.line_stop_spin = self._make_spinbox(1, max(line_count, 1), current_bounds["line_stop"])
        self.sample_start_spin = self._make_spinbox(1, max(sample_count, 1), current_bounds["sample_start"] + 1)
        self.sample_stop_spin = self._make_spinbox(1, max(sample_count, 1), current_bounds["sample_stop"])

        layout.addRow("起始道", self._pair_row(self.trace_start_spin, self.trace_stop_spin))
        layout.addRow("起始测线", self._pair_row(self.line_start_spin, self.line_stop_spin))
        layout.addRow("起始采样", self._pair_row(self.sample_start_spin, self.sample_stop_spin))

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @staticmethod
    def _make_spinbox(minimum: int, maximum: int, value: int) -> QtWidgets.QSpinBox:
        spin = QtWidgets.QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(int(np.clip(value, minimum, maximum)))
        spin.setAccelerated(True)
        return spin

    @staticmethod
    def _pair_row(start_spin: QtWidgets.QSpinBox, stop_spin: QtWidgets.QSpinBox) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        end_label = QtWidgets.QLabel("到")
        row.addWidget(start_spin, stretch=1)
        row.addWidget(end_label)
        row.addWidget(stop_spin, stretch=1)
        return widget

    def values(self) -> dict[str, int]:
        trace_start = min(self.trace_start_spin.value(), self.trace_stop_spin.value()) - 1
        trace_stop = max(self.trace_start_spin.value(), self.trace_stop_spin.value())
        line_start = min(self.line_start_spin.value(), self.line_stop_spin.value()) - 1
        line_stop = max(self.line_start_spin.value(), self.line_stop_spin.value())
        sample_start = min(self.sample_start_spin.value(), self.sample_stop_spin.value()) - 1
        sample_stop = max(self.sample_start_spin.value(), self.sample_stop_spin.value())
        return {
            "trace_start": trace_start,
            "trace_stop": trace_stop,
            "line_start": line_start,
            "line_stop": line_stop,
            "sample_start": sample_start,
            "sample_stop": sample_stop,
        }


class OperationDialog(QtWidgets.QDialog):
    def __init__(self, spec: OperationSpec, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self.setWindowTitle(spec.title)
        self.resize(360, max(180, 110 + 44 * len(spec.params)))
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        self.edits: list[QtWidgets.QLineEdit] = []
        for param in spec.params:
            edit = QtWidgets.QLineEdit(str(param.default))
            form.addRow(param.label, edit)
            self.edits.append(edit)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> list[str]:
        return [edit.text().strip() for edit in self.edits]


class DisplaySettingsDialog(QtWidgets.QDialog):
    def __init__(self, app_controller: GPRApplication, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.app_controller = app_controller
        self.setWindowTitle("显示处理设置")
        self.resize(860, 480)
        layout = QtWidgets.QVBoxLayout(self)
        state = self.app_controller.display_state

        self.contrast_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.contrast_slider.setRange(1, 80)
        self.contrast_slider.setValue(int(round(max(0.1, state.contrast_gain) * 10)))
        self.slice_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slice_slider.setRange(0, 40)
        self.slice_slider.setValue(int(max(0, state.slice_thickness)))
        self.start_time_edit = QtWidgets.QLineEdit(f"{max(0.0, state.start_time_ns):.2f}")
        self.end_time_edit = QtWidgets.QLineEdit(f"{max(state.end_time_ns, 0.0):.2f}")

        self.b_attr_combo = QtWidgets.QComboBox()
        self.b_attr_combo.addItems(["Real", "Envelope", "Phase", "Inst Freq"])
        self.b_attr_combo.setCurrentText(state.bscan_attr)
        self.c_attr_combo = QtWidgets.QComboBox()
        self.c_attr_combo.addItems(["Real", "Envelope", "Phase", "Abs"])
        self.c_attr_combo.setCurrentText(state.cscan_attr)
        self.cmap_combo = QtWidgets.QComboBox()
        self.cmap_combo.addItems(["gray", "viridis", "plasma", "inferno", "magma"])
        self.cmap_combo.setCurrentText(state.colormap)
        self.invert_check = QtWidgets.QCheckBox("反色显示")
        self.invert_check.setChecked(state.invert)
        self.axes_check = QtWidgets.QCheckBox("显示坐标轴")
        self.axes_check.setChecked(state.show_axes)
        self.summary_label = QtWidgets.QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            "color: #66727f; background: #f5f7fa; border: 1px solid #d4dbe3; border-radius: 10px; padding: 8px 10px;"
        )

        tasks_group = QtWidgets.QGroupBox("Tasks")
        tasks_layout = QtWidgets.QVBoxLayout(tasks_group)
        tasks_body = QtWidgets.QHBoxLayout()
        tasks_layout.addLayout(tasks_body, stretch=1)

        self.display_task_list = QtWidgets.QListWidget()
        self.display_task_list.setMinimumWidth(250)
        self.display_task_list.addItems(["Rendering", "Range Gain"])
        self.display_task_list.setCurrentRow(0)
        self.display_task_list.currentRowChanged.connect(self._on_display_task_changed)
        tasks_body.addWidget(self.display_task_list, stretch=2)

        action_column = QtWidgets.QVBoxLayout()
        self.display_up_button = QtWidgets.QPushButton("上移")
        self.display_down_button = QtWidgets.QPushButton("下移")
        self.display_delete_button = QtWidgets.QPushButton("删除")
        for button in (self.display_up_button, self.display_down_button, self.display_delete_button):
            button.setEnabled(False)
            action_column.addWidget(button)
        action_column.addStretch(1)
        tasks_body.addLayout(action_column)

        self.display_stack = QtWidgets.QStackedWidget()
        self.display_stack.addWidget(self._build_rendering_panel())
        self.display_stack.addWidget(self._build_gain_panel())
        tasks_body.addWidget(self.display_stack, stretch=4)

        layout.addWidget(tasks_group, stretch=1)
        layout.addWidget(self.summary_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("确定")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for signal in (
            self.contrast_slider.valueChanged,
            self.slice_slider.valueChanged,
            self.b_attr_combo.currentIndexChanged,
            self.c_attr_combo.currentIndexChanged,
            self.start_time_edit.textChanged,
            self.end_time_edit.textChanged,
            self.cmap_combo.currentIndexChanged,
            self.invert_check.toggled,
            self.axes_check.toggled,
        ):
            signal.connect(self._update_summary)
        self._on_display_task_changed(0)
        self._update_summary()

    def _time_window(self) -> tuple[float, float]:
        total_tw_ns = self.app_controller.current_time_window_ns()
        try:
            start_ns = float(self.start_time_edit.text().strip() or 0.0)
        except ValueError:
            start_ns = 0.0
        try:
            end_ns = float(self.end_time_edit.text().strip() or total_tw_ns)
        except ValueError:
            end_ns = total_tw_ns
        start_ns = max(0.0, min(start_ns, max(total_tw_ns, start_ns)))
        end_ns = max(start_ns + 0.1, min(end_ns, max(total_tw_ns, start_ns + 0.1)))
        return start_ns, end_ns

    def _build_rendering_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(panel)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.addRow("B-scan 属性", self.b_attr_combo)
        form.addRow("C-scan 属性", self.c_attr_combo)
        form.addRow("色图", self.cmap_combo)
        form.addRow("", self.invert_check)
        form.addRow("", self.axes_check)
        return panel

    def _build_gain_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(panel)
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        form.addRow("对比增益", self.contrast_slider)
        form.addRow("C-scan 层厚", self.slice_slider)
        form.addRow("起始时间(ns)", self.start_time_edit)
        form.addRow("终止时间(ns)", self.end_time_edit)
        return panel

    def _on_display_task_changed(self, row: int) -> None:
        self.display_stack.setCurrentIndex(max(0, row))

    def _update_summary(self) -> None:
        start_ns, end_ns = self._time_window()
        self.summary_label.setText(
            "待应用显示链: "
            f"Rendering({self.b_attr_combo.currentText()} / {self.c_attr_combo.currentText()} / {self.cmap_combo.currentText()}) | "
            f"Range Gain(对比增益 {max(0.1, self.contrast_slider.value() / 10.0):.1f}, 层厚 {self.slice_slider.value()}, "
            f"时间窗 {start_ns:.2f}-{end_ns:.2f} ns)"
        )

    def values(self) -> dict[str, object]:
        start_ns, end_ns = self._time_window()
        return {
            "contrast_gain": max(0.1, self.contrast_slider.value() / 10.0),
            "slice_thickness": self.slice_slider.value(),
            "bscan_attr": self.b_attr_combo.currentText(),
            "cscan_attr": self.c_attr_combo.currentText(),
            "start_time_ns": start_ns,
            "end_time_ns": end_ns,
            "colormap": self.cmap_combo.currentText(),
            "invert": self.invert_check.isChecked(),
            "show_axes": self.axes_check.isChecked(),
        }


class MplCanvas(FigureCanvas):
    def __init__(self, width: float = 5.0, height: float = 4.0) -> None:
        self.figure = Figure(figsize=(width, height), constrained_layout=False)
        self.figure.set_facecolor("#f3f4f6")
        super().__init__(self.figure)


class OverviewMapWidget(QtWidgets.QWidget):
    point_selected = QtCore.Signal(str, int, int)
    region_activated = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._regions: list[dict[str, object]] = []
        self._active_region_id = ""
        self._active_trace = 0
        self._active_line = 0
        self._active_image: QtGui.QImage | None = None
        self._layout_rects: list[tuple[str, QtCore.QRectF, dict[str, object]]] = []
        self.setMinimumHeight(240)
        self.setMouseTracking(True)

    def clear_scene(self) -> None:
        self._regions = []
        self._active_region_id = ""
        self._active_trace = 0
        self._active_line = 0
        self._active_image = None
        self._layout_rects = []
        self.update()

    def set_scene(
        self,
        regions: list[dict[str, object]],
        *,
        active_region_id: str,
        active_trace: int = 0,
        active_line: int = 0,
        active_image: QtGui.QImage | None = None,
    ) -> None:
        self._regions = list(regions)
        self._active_region_id = active_region_id
        self._active_trace = int(active_trace)
        self._active_line = int(active_line)
        self._active_image = active_image
        self._layout_rects = []
        self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor("#ffffff"))
        if not self._regions:
            return

        canvas = self.rect().adjusted(14, 14, -14, -14)
        rects = self._compute_layout(canvas)
        self._layout_rects = rects
        for region_id, rect, item in rects:
            active = region_id == self._active_region_id
            painter.save()
            painter.setPen(QtGui.QPen(QtGui.QColor("#c7d0da"), 1.0))
            painter.setBrush(QtGui.QColor("#eef2f5"))
            painter.drawRoundedRect(rect, 10, 10)
            if active and self._active_image is not None and not self._active_image.isNull():
                painter.setClipRect(rect.adjusted(1, 1, -1, -1))
                painter.drawImage(rect, self._active_image)
                painter.setClipping(False)
            border = QtGui.QColor("#1d8f63") if active else QtGui.QColor("#6d7782")
            painter.setPen(QtGui.QPen(border, 2.0 if active else 1.0))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(rect, 10, 10)
            if active:
                trace_count = max(int(item.get("trace_count", 1)), 1)
                line_count = max(int(item.get("line_count", 1)), 1)
                cx = rect.left() + np.clip(self._active_trace / max(trace_count - 1, 1), 0.0, 1.0) * rect.width()
                cy = rect.top() + np.clip(self._active_line / max(line_count - 1, 1), 0.0, 1.0) * rect.height()
                painter.setPen(QtGui.QPen(QtGui.QColor("#0d63ff"), 1.2))
                painter.drawLine(QtCore.QPointF(cx, rect.top()), QtCore.QPointF(cx, rect.bottom()))
                painter.drawLine(QtCore.QPointF(rect.left(), cy), QtCore.QPointF(rect.right(), cy))
                painter.setBrush(QtGui.QColor("#0d63ff"))
                painter.drawEllipse(QtCore.QPointF(cx, cy), 4.0, 4.0)
            painter.restore()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        region = self._region_at(event.position())
        if region is None:
            super().mouseDoubleClickEvent(event)
            return
        region_id, rect, item = region
        self.region_activated.emit(region_id)
        trace_count = max(int(item.get("trace_count", 1)), 1)
        line_count = max(int(item.get("line_count", 1)), 1)
        x_ratio = np.clip((event.position().x() - rect.left()) / max(rect.width(), 1e-9), 0.0, 1.0)
        y_ratio = np.clip((event.position().y() - rect.top()) / max(rect.height(), 1e-9), 0.0, 1.0)
        trace_index = int(round(x_ratio * max(trace_count - 1, 0)))
        line_index = int(round(y_ratio * max(line_count - 1, 0)))
        self.point_selected.emit(region_id, trace_index, line_index)
        event.accept()

    def _region_at(self, point: QtCore.QPointF) -> tuple[str, QtCore.QRectF, dict[str, object]] | None:
        for item in self._layout_rects:
            if item[1].contains(point):
                return item
        return None

    def _compute_layout(self, canvas: QtCore.QRect) -> list[tuple[str, QtCore.QRectF, dict[str, object]]]:
        gap = 14.0
        total_width_units = 0.0
        max_height_units = 1.0
        for item in self._regions:
            total_width_units += max(float(item.get("trace_count", 1)), 1.0)
            max_height_units = max(max_height_units, max(float(item.get("line_count", 1)), 1.0))
        total_width_units += gap * max(len(self._regions) - 1, 0)
        scale = min(canvas.width() / max(total_width_units, 1.0), canvas.height() / max(max_height_units, 1.0))
        scale = max(scale, 1.0)
        x = float(canvas.left())
        y = float(canvas.top()) + (canvas.height() - max_height_units * scale) / 2.0
        rects: list[tuple[str, QtCore.QRectF, dict[str, object]]] = []
        for item in self._regions:
            width = max(float(item.get("trace_count", 1)), 1.0) * scale
            height = max(float(item.get("line_count", 1)), 1.0) * scale
            rect = QtCore.QRectF(x, y, width, height)
            rects.append((str(item.get("region_id", "")), rect, item))
            x += width + gap
        return rects


class RasterViewportWidget(QtWidgets.QWidget):
    view_changed = QtCore.Signal(object, object, object)
    point_selected = QtCore.Signal(object, float, float)
    guide_moved = QtCore.Signal(object, float, float)

    def __init__(
        self,
        view_key: str,
        *,
        title: str,
        x_label: str = "",
        y_label: str = "",
        margins: tuple[int, int, int, int] = (50, 12, 34, 18),
        allow_pan_x: bool = True,
        allow_pan_y: bool = True,
        allow_zoom_x: bool = True,
        allow_zoom_y: bool = True,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.view_key = view_key
        self.title = title
        self.x_label = x_label
        self.y_label = y_label
        self.left_margin, self.right_margin, self.top_margin, self.bottom_margin = margins
        self.allow_pan_x = allow_pan_x
        self.allow_pan_y = allow_pan_y
        self.allow_zoom_x = allow_zoom_x
        self.allow_zoom_y = allow_zoom_y
        self._image: QtGui.QImage | None = None
        self._data_x = (0.0, 1.0)
        self._data_y = (0.0, 1.0)
        self._viewport_x = (0.0, 1.0)
        self._viewport_y = (0.0, 1.0)
        self._viewport_limit_y = (0.0, 1.0)
        self._vline: tuple[float, QtGui.QColor] | None = None
        self._hline: tuple[float, QtGui.QColor] | None = None
        self._show_axes = True
        self._drag_state: dict[str, object] | None = None
        self._guide_grab_mode: str | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(120, 120)

    def clear_content(self) -> None:
        self._image = None
        self._vline = None
        self._hline = None
        self.update()

    def set_content(
        self,
        image: QtGui.QImage,
        *,
        data_x: tuple[float, float],
        data_y: tuple[float, float],
        reset_view: bool,
        show_axes: bool,
        initial_viewport_x: tuple[float, float] | None = None,
        initial_viewport_y: tuple[float, float] | None = None,
        viewport_limit_y: tuple[float, float] | None = None,
        vertical_line: tuple[float, str] | None = None,
        horizontal_line: tuple[float, str] | None = None,
    ) -> None:
        self._image = image
        self._data_x = tuple(sorted((float(data_x[0]), float(data_x[1]))))
        self._data_y = tuple(sorted((float(data_y[0]), float(data_y[1]))))
        self._viewport_limit_y = tuple(sorted((float((viewport_limit_y or self._data_y)[0]), float((viewport_limit_y or self._data_y)[1]))))
        self._show_axes = show_axes
        if reset_view or not self._is_viewport_valid():
            self._viewport_x = self._clamp_range(initial_viewport_x or self._data_x, self._data_x)
            self._viewport_y = self._clamp_y_range(initial_viewport_y or self._viewport_limit_y, self._viewport_limit_y)
        else:
            self._viewport_x = self._clamp_range(self._viewport_x, self._data_x)
            self._viewport_y = self._clamp_y_range(self._viewport_y, self._viewport_limit_y)
        self._vline = self._make_line(vertical_line)
        self._hline = self._make_line(horizontal_line)
        self.update()

    def set_overlay_lines(
        self,
        *,
        vertical_line: tuple[float, str] | None = None,
        horizontal_line: tuple[float, str] | None = None,
    ) -> None:
        self._vline = self._make_line(vertical_line)
        self._hline = self._make_line(horizontal_line)
        self.update()

    def set_viewport(self, *, xlim: tuple[float, float] | None = None, ylim: tuple[float, float] | None = None) -> None:
        if xlim is not None:
            self._viewport_x = self._clamp_range(xlim, self._data_x)
        if ylim is not None:
            self._viewport_y = self._clamp_y_range(ylim, self._viewport_limit_y)
        self.update()

    def viewport(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return self._viewport_x, self._viewport_y

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor("#ffffff"))
        plot_rect = self._plot_rect()
        self._draw_title(painter)
        self._draw_frame(painter, plot_rect)
        self._draw_axes(painter, plot_rect)
        if self._image is not None and not self._image.isNull():
            self._draw_image_region(painter, plot_rect)
        self._draw_crosshair(painter, plot_rect)
        self._draw_border(painter, plot_rect)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton or self._image is None:
            super().mousePressEvent(event)
            return
        plot_rect = self._plot_rect()
        if not plot_rect.contains(event.position()):
            super().mousePressEvent(event)
            return
        guide_mode = self._hit_test_guides(event.position(), plot_rect)
        if guide_mode is not None:
            self._guide_grab_mode = guide_mode
            self._drag_state = {"mode": guide_mode}
            event.accept()
            return
        self._drag_state = {
            "mode": "pan",
            "pos": QtCore.QPointF(event.position()),
            "xlim": self._viewport_x,
            "ylim": self._viewport_y,
        }
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        plot_rect = self._plot_rect()
        if self._drag_state is None:
            self._update_hover_cursor(event.position(), plot_rect)
            super().mouseMoveEvent(event)
            return
        if plot_rect.width() <= 1 or plot_rect.height() <= 1:
            return
        mode = str(self._drag_state.get("mode", "pan"))
        if mode != "pan":
            data_x, data_y = self._data_from_point(event.position(), plot_rect)
            current_x = self._vline[0] if self._vline is not None else data_x
            current_y = self._hline[0] if self._hline is not None else data_y
            if mode == "guide_v":
                current_x = data_x
            elif mode == "guide_h":
                current_y = data_y
            else:
                current_x = data_x
                current_y = data_y
            self.guide_moved.emit(self.view_key, float(current_x), float(current_y))
            event.accept()
            return
        anchor = self._drag_state["pos"]
        dx_px = float(event.position().x() - anchor.x())
        dy_px = float(event.position().y() - anchor.y())
        xlim = self._drag_state["xlim"]
        ylim = self._drag_state["ylim"]
        span_x = xlim[1] - xlim[0]
        span_y = ylim[1] - ylim[0]
        new_xlim = xlim
        new_ylim = ylim
        if self.allow_pan_x:
            new_xlim = (xlim[0] - dx_px * span_x / plot_rect.width(), xlim[1] - dx_px * span_x / plot_rect.width())
        if self.allow_pan_y:
            new_ylim = (ylim[0] - dy_px * span_y / plot_rect.height(), ylim[1] - dy_px * span_y / plot_rect.height())
        self._viewport_x = self._clamp_range(new_xlim, self._data_x)
        self._viewport_y = self._clamp_y_range(new_ylim, self._viewport_limit_y)
        self.view_changed.emit(self.view_key, self._viewport_x, self._viewport_y)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_state = None
        self._guide_grab_mode = None
        self._update_hover_cursor(event.position(), self._plot_rect())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton or self._image is None:
            super().mouseDoubleClickEvent(event)
            return
        plot_rect = self._plot_rect()
        if not plot_rect.contains(event.position()):
            super().mouseDoubleClickEvent(event)
            return
        data_x, data_y = self._data_from_point(event.position(), plot_rect)
        self.point_selected.emit(self.view_key, data_x, data_y)
        event.accept()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self._image is None:
            super().wheelEvent(event)
            return
        plot_rect = self._plot_rect()
        pos = event.position()
        if not plot_rect.contains(pos):
            super().wheelEvent(event)
            return
        data_x, data_y = self._data_from_point(pos, plot_rect)
        scale = 0.85 if event.angleDelta().y() > 0 else 1.18
        x0, x1 = self._viewport_x
        y0, y1 = self._viewport_y
        new_xlim = self._viewport_x
        new_ylim = self._viewport_y
        if self.allow_zoom_y:
            desired_ylim = (
                data_y - (data_y - y0) * scale,
                data_y + (y1 - data_y) * scale,
            )
            new_ylim = self._clamp_y_range(desired_ylim, self._viewport_limit_y)
        effective_scale = scale
        if self.allow_zoom_y:
            old_span_y = max(y1 - y0, 1e-9)
            effective_scale = (new_ylim[1] - new_ylim[0]) / old_span_y
        if self.allow_zoom_x:
            new_xlim = (
                data_x - (data_x - x0) * effective_scale,
                data_x + (x1 - data_x) * effective_scale,
            )
        self._viewport_x = self._clamp_range(new_xlim, self._data_x)
        self._viewport_y = self._clamp_y_range(new_ylim, self._viewport_limit_y)
        self.view_changed.emit(self.view_key, self._viewport_x, self._viewport_y)
        self.update()
        event.accept()

    def _plot_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(
            float(self.left_margin),
            float(self.top_margin),
            max(1.0, float(self.width() - self.left_margin - self.right_margin)),
            max(1.0, float(self.height() - self.top_margin - self.bottom_margin)),
        )

    def _draw_title(self, painter: QtGui.QPainter) -> None:
        painter.save()
        painter.setPen(QtGui.QColor("#1f2f42"))
        title_font = QtGui.QFont("Microsoft YaHei UI", 10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(QtCore.QRectF(0.0, 4.0, float(self.width()), float(self.top_margin - 8)), QtCore.Qt.AlignCenter, self.title)
        painter.restore()

    def _draw_frame(self, painter: QtGui.QPainter, plot_rect: QtCore.QRectF) -> None:
        painter.save()
        painter.fillRect(plot_rect, QtGui.QColor("#ffffff"))
        painter.restore()

    def _draw_axes(self, painter: QtGui.QPainter, plot_rect: QtCore.QRectF) -> None:
        if not self._show_axes:
            return
        painter.save()
        painter.setPen(QtGui.QPen(QtGui.QColor("#aebccc"), 1))
        painter.drawLine(plot_rect.topLeft(), plot_rect.topRight())
        painter.drawLine(plot_rect.topLeft(), plot_rect.bottomLeft())

        tick_font = QtGui.QFont("Microsoft YaHei UI", 7)
        painter.setFont(tick_font)
        painter.setPen(QtGui.QColor("#5c6f85"))
        tick_count = 4 if plot_rect.width() < 220.0 else 5
        for idx in range(tick_count):
            ratio = idx / max(tick_count - 1, 1)
            y = plot_rect.top() + ratio * plot_rect.height()
            painter.drawLine(QtCore.QPointF(plot_rect.left() - 4, y), QtCore.QPointF(plot_rect.left(), y))
            value = self._viewport_y[0] + ratio * (self._viewport_y[1] - self._viewport_y[0])
            painter.drawText(
                QtCore.QRectF(0.0, y - 8, plot_rect.left() - 8, 16),
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                self._format_tick(value),
            )
        if self.x_label:
            painter.drawText(
                QtCore.QRectF(plot_rect.left(), plot_rect.bottom() + 2, plot_rect.width(), max(12.0, self.bottom_margin - 4.0)),
                QtCore.Qt.AlignCenter | QtCore.Qt.AlignTop,
                self.x_label,
            )
        if self.y_label:
            painter.save()
            painter.translate(14, plot_rect.center().y())
            painter.rotate(-90)
            painter.drawText(
                QtCore.QRectF(-plot_rect.height() / 2, -14, plot_rect.height(), 20),
                QtCore.Qt.AlignCenter,
                self.y_label,
            )
            painter.restore()
        painter.restore()

    def _draw_crosshair(self, painter: QtGui.QPainter, plot_rect: QtCore.QRectF) -> None:
        painter.save()
        painter.setClipRect(plot_rect)
        if self._vline is not None:
            x, color = self._vline
            if self._viewport_x[0] <= x <= self._viewport_x[1]:
                px = plot_rect.left() + (x - self._viewport_x[0]) / max(self._viewport_x[1] - self._viewport_x[0], 1e-9) * plot_rect.width()
                painter.setPen(QtGui.QPen(color, 1.2))
                painter.drawLine(QtCore.QPointF(px, plot_rect.top()), QtCore.QPointF(px, plot_rect.bottom()))
        if self._hline is not None:
            y, color = self._hline
            if self._viewport_y[0] <= y <= self._viewport_y[1]:
                py = plot_rect.top() + (y - self._viewport_y[0]) / max(self._viewport_y[1] - self._viewport_y[0], 1e-9) * plot_rect.height()
                painter.setPen(QtGui.QPen(color, 1.0))
                painter.drawLine(QtCore.QPointF(plot_rect.left(), py), QtCore.QPointF(plot_rect.right(), py))
        painter.restore()

    def _draw_border(self, painter: QtGui.QPainter, plot_rect: QtCore.QRectF) -> None:
        painter.save()
        painter.setPen(QtGui.QPen(QtGui.QColor("#c7d0da"), 1))
        painter.drawRect(plot_rect)
        painter.restore()

    def _draw_image_region(self, painter: QtGui.QPainter, plot_rect: QtCore.QRectF) -> None:
        if self._image is None or self._image.isNull():
            return
        width = max(1.0, float(self._image.width()))
        height = max(1.0, float(self._image.height()))
        full_x0, full_x1 = self._data_x
        full_y0, full_y1 = self._data_y
        view_x0, view_x1 = self._viewport_x
        view_y0, view_y1 = self._viewport_y
        inter_x0 = max(view_x0, full_x0)
        inter_x1 = min(view_x1, full_x1)
        inter_y0 = max(view_y0, full_y0)
        inter_y1 = min(view_y1, full_y1)
        if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
            return
        span_view_x = max(view_x1 - view_x0, 1e-9)
        span_view_y = max(view_y1 - view_y0, 1e-9)
        target = QtCore.QRectF(
            plot_rect.left() + (inter_x0 - view_x0) / span_view_x * plot_rect.width(),
            plot_rect.top() + (inter_y0 - view_y0) / span_view_y * plot_rect.height(),
            (inter_x1 - inter_x0) / span_view_x * plot_rect.width(),
            (inter_y1 - inter_y0) / span_view_y * plot_rect.height(),
        )
        source = QtCore.QRectF(
            (inter_x0 - full_x0) / max(full_x1 - full_x0, 1e-9) * width,
            (inter_y0 - full_y0) / max(full_y1 - full_y0, 1e-9) * height,
            (inter_x1 - inter_x0) / max(full_x1 - full_x0, 1e-9) * width,
            (inter_y1 - inter_y0) / max(full_y1 - full_y0, 1e-9) * height,
        )
        painter.save()
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.drawImage(target, self._image, source)
        painter.restore()

    def _data_from_point(self, point: QtCore.QPointF, plot_rect: QtCore.QRectF) -> tuple[float, float]:
        x_ratio = np.clip((point.x() - plot_rect.left()) / max(plot_rect.width(), 1e-9), 0.0, 1.0)
        y_ratio = np.clip((point.y() - plot_rect.top()) / max(plot_rect.height(), 1e-9), 0.0, 1.0)
        data_x = self._viewport_x[0] + x_ratio * (self._viewport_x[1] - self._viewport_x[0])
        data_y = self._viewport_y[0] + y_ratio * (self._viewport_y[1] - self._viewport_y[0])
        return float(data_x), float(data_y)

    def _hit_test_guides(self, point: QtCore.QPointF, plot_rect: QtCore.QRectF) -> str | None:
        tolerance = 6.0
        near_v = False
        near_h = False
        if self._vline is not None and self._viewport_x[0] <= self._vline[0] <= self._viewport_x[1]:
            px = plot_rect.left() + (self._vline[0] - self._viewport_x[0]) / max(self._viewport_x[1] - self._viewport_x[0], 1e-9) * plot_rect.width()
            near_v = abs(point.x() - px) <= tolerance
        if self._hline is not None and self._viewport_y[0] <= self._hline[0] <= self._viewport_y[1]:
            py = plot_rect.top() + (self._hline[0] - self._viewport_y[0]) / max(self._viewport_y[1] - self._viewport_y[0], 1e-9) * plot_rect.height()
            near_h = abs(point.y() - py) <= tolerance
        if near_v and near_h:
            return "guide_both"
        if near_v:
            return "guide_v"
        if near_h:
            return "guide_h"
        return None

    def _update_hover_cursor(self, point: QtCore.QPointF, plot_rect: QtCore.QRectF) -> None:
        if not plot_rect.contains(point):
            self.unsetCursor()
            return
        mode = self._hit_test_guides(point, plot_rect)
        if mode == "guide_v":
            self.setCursor(QtCore.Qt.SizeHorCursor)
        elif mode == "guide_h":
            self.setCursor(QtCore.Qt.SizeVerCursor)
        elif mode == "guide_both":
            self.setCursor(QtCore.Qt.SizeAllCursor)
        else:
            self.unsetCursor()

    def _is_viewport_valid(self) -> bool:
        return (
            self._viewport_x[1] > self._viewport_x[0]
            and self._viewport_y[1] > self._viewport_y[0]
            and self._data_x[1] > self._data_x[0]
            and self._data_y[1] > self._data_y[0]
        )

    @staticmethod
    def _clamp_range(view_range: tuple[float, float], full_range: tuple[float, float]) -> tuple[float, float]:
        start, end = sorted((float(view_range[0]), float(view_range[1])))
        full_start, full_end = sorted((float(full_range[0]), float(full_range[1])))
        full_span = max(full_end - full_start, 1e-9)
        span = max(end - start, 1e-9)
        if span >= full_span:
            center = (full_start + full_end) / 2.0
            return (center - span / 2.0, center + span / 2.0)
        start = min(max(start, full_start), full_end - span)
        return (start, start + span)

    @staticmethod
    def _clamp_y_range(view_range: tuple[float, float], limit_range: tuple[float, float]) -> tuple[float, float]:
        start, end = sorted((float(view_range[0]), float(view_range[1])))
        limit_start, limit_end = sorted((float(limit_range[0]), float(limit_range[1])))
        limit_span = max(limit_end - limit_start, 1e-9)
        span = max(end - start, 1e-9)
        if span >= limit_span:
            center = (limit_start + limit_end) / 2.0
            return (center - span / 2.0, center + span / 2.0)
        start = min(max(start, limit_start), limit_end - span)
        return (start, start + span)

    @staticmethod
    def _make_line(line: tuple[float, str] | None) -> tuple[float, QtGui.QColor] | None:
        if line is None:
            return None
        return (float(line[0]), QtGui.QColor(line[1]))

    @staticmethod
    def _format_tick(value: float) -> str:
        return f"{int(round(value))}"


class TraceViewportWidget(QtWidgets.QWidget):
    view_changed = QtCore.Signal(object, object, object)
    point_selected = QtCore.Signal(object, float, float)
    guide_moved = QtCore.Signal(object, float, float)

    def __init__(
        self,
        view_key: str,
        *,
        title: str,
        x_label: str = "Amplitude",
        margins: tuple[int, int, int, int] = (18, 12, 34, 18),
        allow_pan_x: bool = True,
        allow_pan_y: bool = True,
        allow_zoom_x: bool = True,
        allow_zoom_y: bool = True,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.view_key = view_key
        self.title = title
        self.x_label = x_label
        self.left_margin, self.right_margin, self.top_margin, self.bottom_margin = margins
        self.allow_pan_x = allow_pan_x
        self.allow_pan_y = allow_pan_y
        self.allow_zoom_x = allow_zoom_x
        self.allow_zoom_y = allow_zoom_y
        self._x_data = np.empty((0,), dtype=float)
        self._y_data = np.empty((0,), dtype=float)
        self._data_x = (-1.0, 1.0)
        self._data_y = (0.0, 1.0)
        self._viewport_x = (-1.0, 1.0)
        self._viewport_y = (0.0, 1.0)
        self._viewport_limit_y = (0.0, 1.0)
        self._marker: tuple[float, float] | None = None
        self._hline: tuple[float, QtGui.QColor] | None = None
        self._show_axes = True
        self._drag_state: dict[str, object] | None = None
        self._guide_grab_mode: str | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(100, 120)

    def clear_content(self) -> None:
        self._x_data = np.empty((0,), dtype=float)
        self._y_data = np.empty((0,), dtype=float)
        self._marker = None
        self._hline = None
        self.update()

    def set_content(
        self,
        x_data: np.ndarray,
        y_data: np.ndarray,
        *,
        data_y: tuple[float, float],
        reset_view: bool,
        show_axes: bool,
        initial_viewport_x: tuple[float, float] | None = None,
        initial_viewport_y: tuple[float, float] | None = None,
        viewport_limit_y: tuple[float, float] | None = None,
        marker: tuple[float, float] | None = None,
        horizontal_line: tuple[float, str] | None = None,
    ) -> None:
        self._x_data = np.asarray(x_data, dtype=float)
        self._y_data = np.asarray(y_data, dtype=float)
        self._show_axes = show_axes
        self._data_y = tuple(sorted((float(data_y[0]), float(data_y[1]))))
        self._viewport_limit_y = tuple(sorted((float((viewport_limit_y or self._data_y)[0]), float((viewport_limit_y or self._data_y)[1]))))
        if self._x_data.size:
            finite_values = self._x_data[np.isfinite(self._x_data)]
            if finite_values.size:
                max_abs = float(np.nanmax(np.abs(finite_values)))
            else:
                max_abs = 1.0
            if marker is not None and np.isfinite(marker[0]):
                max_abs = max(max_abs, abs(float(marker[0])))
            if max_abs <= 1e-9:
                max_abs = 1.0
            # 3D Radar's trace line keeps the whole waveform visible, so we use a
            # symmetric amplitude window with a small safety margin per trace.
            pad = max(max_abs * 0.12, 1e-6)
            self._data_x = (-(max_abs + pad), max_abs + pad)
        else:
            self._data_x = (-1.0, 1.0)
        if reset_view or not self._is_viewport_valid():
            self._viewport_x = self._clamp_range(initial_viewport_x or self._data_x, self._data_x)
            self._viewport_y = self._clamp_y_range(initial_viewport_y or self._viewport_limit_y, self._viewport_limit_y)
        else:
            if self.allow_zoom_x:
                self._viewport_x = self._clamp_range(self._viewport_x, self._data_x)
            else:
                self._viewport_x = self._data_x
            self._viewport_y = self._clamp_y_range(self._viewport_y, self._viewport_limit_y)
        self._marker = (float(marker[0]), float(marker[1])) if marker is not None else None
        self._hline = None if horizontal_line is None else (float(horizontal_line[0]), QtGui.QColor(horizontal_line[1]))
        self.update()

    def set_viewport(self, *, xlim: tuple[float, float] | None = None, ylim: tuple[float, float] | None = None) -> None:
        if xlim is not None:
            self._viewport_x = self._clamp_range(xlim, self._data_x)
        if ylim is not None:
            self._viewport_y = self._clamp_y_range(ylim, self._viewport_limit_y)
        self.update()

    def viewport(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return self._viewport_x, self._viewport_y

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor("#ffffff"))
        plot_rect = self._plot_rect()
        painter.setPen(QtGui.QColor("#1f2f42"))
        title_font = QtGui.QFont("Microsoft YaHei UI", 10)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(QtCore.QRectF(0.0, 4.0, float(self.width()), float(self.top_margin - 8)), QtCore.Qt.AlignCenter, self.title)
        painter.fillRect(plot_rect, QtGui.QColor("#ffffff"))
        self._draw_axes(painter, plot_rect)
        self._draw_curve(painter, plot_rect)
        self._draw_border(painter, plot_rect)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(event)
            return
        plot_rect = self._plot_rect()
        if not plot_rect.contains(event.position()):
            super().mousePressEvent(event)
            return
        if self._hit_test_hline(event.position(), plot_rect):
            self._guide_grab_mode = "guide_h"
            self._drag_state = {"mode": "guide_h"}
            event.accept()
            return
        self._drag_state = {
            "mode": "pan",
            "pos": QtCore.QPointF(event.position()),
            "xlim": self._viewport_x,
            "ylim": self._viewport_y,
        }
        event.accept()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        plot_rect = self._plot_rect()
        if self._drag_state is None:
            self._update_hover_cursor(event.position(), plot_rect)
            super().mouseMoveEvent(event)
            return
        mode = str(self._drag_state.get("mode", "pan"))
        if mode == "guide_h":
            data_x, data_y = self._data_from_point(event.position(), plot_rect)
            self.guide_moved.emit(self.view_key, float(data_x), float(data_y))
            event.accept()
            return
        anchor = self._drag_state["pos"]
        dx_px = float(event.position().x() - anchor.x())
        dy_px = float(event.position().y() - anchor.y())
        xlim = self._drag_state["xlim"]
        ylim = self._drag_state["ylim"]
        span_x = xlim[1] - xlim[0]
        span_y = ylim[1] - ylim[0]
        new_xlim = xlim
        new_ylim = ylim
        if self.allow_pan_x:
            new_xlim = (xlim[0] - dx_px * span_x / max(plot_rect.width(), 1e-9), xlim[1] - dx_px * span_x / max(plot_rect.width(), 1e-9))
        if self.allow_pan_y:
            new_ylim = (ylim[0] - dy_px * span_y / max(plot_rect.height(), 1e-9), ylim[1] - dy_px * span_y / max(plot_rect.height(), 1e-9))
        self._viewport_x = self._clamp_range(new_xlim, self._data_x)
        self._viewport_y = self._clamp_y_range(new_ylim, self._viewport_limit_y)
        self.view_changed.emit(self.view_key, self._viewport_x, self._viewport_y)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._drag_state = None
        self._guide_grab_mode = None
        self._update_hover_cursor(event.position(), self._plot_rect())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() != QtCore.Qt.LeftButton or not self._plot_rect().contains(event.position()):
            super().mouseDoubleClickEvent(event)
            return
        data_x, data_y = self._data_from_point(event.position(), self._plot_rect())
        self.point_selected.emit(self.view_key, data_x, data_y)
        event.accept()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        plot_rect = self._plot_rect()
        pos = event.position()
        if not plot_rect.contains(pos):
            super().wheelEvent(event)
            return
        data_x, data_y = self._data_from_point(pos, plot_rect)
        scale = 0.85 if event.angleDelta().y() > 0 else 1.18
        x0, x1 = self._viewport_x
        y0, y1 = self._viewport_y
        new_xlim = self._viewport_x
        new_ylim = self._viewport_y
        if self.allow_zoom_y:
            desired_ylim = (data_y - (data_y - y0) * scale, data_y + (y1 - data_y) * scale)
            new_ylim = self._clamp_y_range(desired_ylim, self._viewport_limit_y)
        effective_scale = scale
        if self.allow_zoom_y:
            old_span_y = max(y1 - y0, 1e-9)
            effective_scale = (new_ylim[1] - new_ylim[0]) / old_span_y
        if self.allow_zoom_x:
            new_xlim = (data_x - (data_x - x0) * effective_scale, data_x + (x1 - data_x) * effective_scale)
        self._viewport_x = self._clamp_range(new_xlim, self._data_x)
        self._viewport_y = self._clamp_y_range(new_ylim, self._viewport_limit_y)
        self.view_changed.emit(self.view_key, self._viewport_x, self._viewport_y)
        self.update()
        event.accept()

    def _plot_rect(self) -> QtCore.QRectF:
        return QtCore.QRectF(
            float(self.left_margin),
            float(self.top_margin),
            max(1.0, float(self.width() - self.left_margin - self.right_margin)),
            max(1.0, float(self.height() - self.top_margin - self.bottom_margin)),
        )

    def _draw_axes(self, painter: QtGui.QPainter, plot_rect: QtCore.QRectF) -> None:
        painter.save()
        if self._show_axes:
            painter.setPen(QtGui.QPen(QtGui.QColor("#aebccc"), 1))
            painter.drawLine(plot_rect.topLeft(), plot_rect.bottomLeft())
            tick_font = QtGui.QFont("Microsoft YaHei UI", 7)
            painter.setFont(tick_font)
            painter.setPen(QtGui.QColor("#5c6f85"))
            tick_count = 4 if plot_rect.width() < 220.0 else 5
            for idx in range(tick_count):
                ratio = idx / max(tick_count - 1, 1)
                y = plot_rect.top() + ratio * plot_rect.height()
                value = self._viewport_y[0] + ratio * (self._viewport_y[1] - self._viewport_y[0])
                painter.drawLine(QtCore.QPointF(plot_rect.left() - 4, y), QtCore.QPointF(plot_rect.left(), y))
                painter.drawText(
                    QtCore.QRectF(0.0, y - 8, plot_rect.left() - 6, 16),
                    QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                    self._format_tick(value),
                )
            painter.drawText(
                QtCore.QRectF(plot_rect.left(), plot_rect.bottom() + 2, plot_rect.width(), max(12.0, self.bottom_margin - 4.0)),
                QtCore.Qt.AlignCenter | QtCore.Qt.AlignTop,
                self.x_label,
            )
        painter.restore()

    def _draw_curve(self, painter: QtGui.QPainter, plot_rect: QtCore.QRectF) -> None:
        painter.save()
        painter.setClipRect(plot_rect)
        if self._x_data.size and self._y_data.size:
            x0, x1 = self._viewport_x
            y0, y1 = self._viewport_y
            mask = (self._y_data >= y0) & (self._y_data <= y1)
            xs = self._x_data[mask]
            ys = self._y_data[mask]
            if xs.size >= 2:
                path = QtGui.QPainterPath()
                first = True
                span_x = max(x1 - x0, 1e-9)
                span_y = max(y1 - y0, 1e-9)
                for x_val, y_val in zip(xs, ys):
                    px = plot_rect.left() + (x_val - x0) / span_x * plot_rect.width()
                    py = plot_rect.top() + (y_val - y0) / span_y * plot_rect.height()
                    if first:
                        path.moveTo(px, py)
                        first = False
                    else:
                        path.lineTo(px, py)
                painter.setPen(QtGui.QPen(QtGui.QColor("#1f5fbf"), 1.3))
                painter.drawPath(path)
        if self._hline is not None:
            y_val, color = self._hline
            if self._viewport_y[0] <= y_val <= self._viewport_y[1]:
                py = plot_rect.top() + (y_val - self._viewport_y[0]) / max(self._viewport_y[1] - self._viewport_y[0], 1e-9) * plot_rect.height()
                painter.setPen(QtGui.QPen(color, 1.0))
                painter.drawLine(QtCore.QPointF(plot_rect.left(), py), QtCore.QPointF(plot_rect.right(), py))
        if self._marker is not None:
            x_val, y_val = self._marker
            if self._viewport_x[0] <= x_val <= self._viewport_x[1] and self._viewport_y[0] <= y_val <= self._viewport_y[1]:
                px = plot_rect.left() + (x_val - self._viewport_x[0]) / max(self._viewport_x[1] - self._viewport_x[0], 1e-9) * plot_rect.width()
                py = plot_rect.top() + (y_val - self._viewport_y[0]) / max(self._viewport_y[1] - self._viewport_y[0], 1e-9) * plot_rect.height()
                painter.setBrush(QtGui.QColor("#d1495b"))
                painter.setPen(QtCore.Qt.NoPen)
                painter.drawEllipse(QtCore.QPointF(px, py), 4.0, 4.0)
        painter.restore()

    def _draw_border(self, painter: QtGui.QPainter, plot_rect: QtCore.QRectF) -> None:
        painter.save()
        painter.setPen(QtGui.QPen(QtGui.QColor("#c7d0da"), 1))
        painter.drawRect(plot_rect)
        painter.restore()

    def _data_from_point(self, point: QtCore.QPointF, plot_rect: QtCore.QRectF) -> tuple[float, float]:
        x_ratio = np.clip((point.x() - plot_rect.left()) / max(plot_rect.width(), 1e-9), 0.0, 1.0)
        y_ratio = np.clip((point.y() - plot_rect.top()) / max(plot_rect.height(), 1e-9), 0.0, 1.0)
        x_val = self._viewport_x[0] + x_ratio * (self._viewport_x[1] - self._viewport_x[0])
        y_val = self._viewport_y[0] + y_ratio * (self._viewport_y[1] - self._viewport_y[0])
        return float(x_val), float(y_val)

    def _hit_test_hline(self, point: QtCore.QPointF, plot_rect: QtCore.QRectF) -> bool:
        if self._hline is None:
            return False
        y_val, _color = self._hline
        if not (self._viewport_y[0] <= y_val <= self._viewport_y[1]):
            return False
        py = plot_rect.top() + (y_val - self._viewport_y[0]) / max(self._viewport_y[1] - self._viewport_y[0], 1e-9) * plot_rect.height()
        return abs(point.y() - py) <= 6.0

    def _update_hover_cursor(self, point: QtCore.QPointF, plot_rect: QtCore.QRectF) -> None:
        if plot_rect.contains(point) and self._hit_test_hline(point, plot_rect):
            self.setCursor(QtCore.Qt.SizeVerCursor)
        else:
            self.unsetCursor()

    def _is_viewport_valid(self) -> bool:
        return self._viewport_x[1] > self._viewport_x[0] and self._viewport_y[1] > self._viewport_y[0]

    @staticmethod
    def _clamp_range(view_range: tuple[float, float], full_range: tuple[float, float]) -> tuple[float, float]:
        start, end = sorted((float(view_range[0]), float(view_range[1])))
        full_start, full_end = sorted((float(full_range[0]), float(full_range[1])))
        full_span = max(full_end - full_start, 1e-9)
        span = min(max(end - start, 1e-9), full_span)
        start = min(max(start, full_start), full_end - span)
        return (start, start + span)

    @staticmethod
    def _clamp_y_range(view_range: tuple[float, float], limit_range: tuple[float, float]) -> tuple[float, float]:
        start, end = sorted((float(view_range[0]), float(view_range[1])))
        limit_start, limit_end = sorted((float(limit_range[0]), float(limit_range[1])))
        limit_span = max(limit_end - limit_start, 1e-9)
        span = max(end - start, 1e-9)
        if span >= limit_span:
            center = (limit_start + limit_end) / 2.0
            return (center - span / 2.0, center + span / 2.0)
        start = min(max(start, limit_start), limit_end - span)
        return (start, start + span)

    @staticmethod
    def _format_tick(value: float) -> str:
        return f"{int(round(value))}"

class ClosableProgressDialog(QtWidgets.QProgressDialog):
    close_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.emit_on_close = True

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.emit_on_close:
            self.close_requested.emit()
        event.accept()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, app_controller: GPRApplication):
        super().__init__()
        self.app_controller = app_controller
        self.display_data: DisplayData | None = None
        self._is_busy = False
        self._progress_dialog: QtWidgets.QProgressDialog | None = None
        self._progress_mode = ""
        self._progress_cancel_requested = False
        self._pipeline_configured = False
        self._display_configured = False
        self._has_custom_viewport = False
        self._ordered_steps = []
        self._selected_step_index = -1
        self.pipeline_dialog: QtWidgets.QDialog | None = None
        self._settings_html_cache: str | None = None

        self.action_new_project: QtGui.QAction | None = None
        self.action_open_project: QtGui.QAction | None = None
        self.action_save_project: QtGui.QAction | None = None
        self.action_import_data: QtGui.QAction | None = None
        self.action_load_template: QtGui.QAction | None = None
        self.action_save_processed: QtGui.QAction | None = None
        self._project_panel_collapsed = False
        self._project_panel_last_width = 240

        self.resize(1680, 940)
        self._build_ui()
        self._apply_window_style()
        self._connect_signals()
        self._refresh_pipeline_draft()
        self._update_project_title()
        self._update_top_actions()
        self._refresh_settings_info()
        self._show_waiting_plots()
        QtCore.QTimer.singleShot(0, self._apply_default_splitter_ratio)

    def _apply_window_style(self) -> None:
        self.setFont(QtGui.QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #eef1f4;
                color: #273340;
            }
            QToolButton, QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffffff, stop:0.52 #f2f4f6, stop:1 #dde3e9);
                border: 1px solid #c2cad3;
                border-radius: 11px;
                padding: 9px 18px;
                min-height: 22px;
                font-weight: 600;
                color: #273340;
            }
            QToolButton:hover, QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffffff, stop:1 #e8edf2);
                border-color: #99a5b2;
            }
            QToolButton:pressed, QPushButton:pressed, QPushButton:checked {
                background: #dde3e8;
                border-color: #8693a1;
            }
            QPushButton:disabled, QToolButton:disabled {
                color: #9ba7b3;
                background: #eceff3;
                border-color: #d9dee5;
            }
            QPushButton#primaryAction {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #49596c, stop:0.5 #374554, stop:1 #2b3641);
                color: #ffffff;
                border: 1px solid #26313c;
            }
            QPushButton#primaryAction:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #526579, stop:1 #313e4b);
                border-color: #2e3b4b;
            }
            QPushButton#primaryAction:pressed {
                background: #263241;
                border-color: #202a36;
            }
            QPushButton#sectionAddButton {
                min-width: 34px;
                max-width: 34px;
                min-height: 30px;
                max-height: 30px;
                padding: 0;
                font-size: 18px;
                font-weight: 700;
                color: #314150;
                border-radius: 9px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffffff, stop:1 #e6ebf0);
            }
            QPushButton#sectionAddButton:hover {
                border-color: #8f9baa;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ffffff, stop:1 #edf1f5);
            }
            QPushButton#panelAction {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #fbfcfd, stop:1 #e5eaf0);
            }
            QFrame#toolbarCard, QFrame#viewCard, QFrame#sideCard {
                background: #f8fafb;
                border: 1px solid #d5dce4;
                border-radius: 18px;
            }
            QFrame#toolbarCard {
                background: #fcfdfe;
            }
            QDialog#pipelineDialog, QDialog#displayDialog, QProgressDialog#progressDialog {
                background: #eef1f4;
            }
            QDialog#pipelineDialog QLabel, QDialog#displayDialog QLabel, QProgressDialog#progressDialog QLabel {
                color: #2c3947;
            }
            QGroupBox {
                background: #fafbfc;
                border: 1px solid #d3dae2;
                border-radius: 14px;
                margin-top: 12px;
                padding-top: 12px;
                font-weight: 700;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
                color: #344454;
            }
            QListWidget, QTableWidget, QPlainTextEdit, QTextBrowser, QLineEdit, QComboBox {
                background: #fdfefe;
                border: 1px solid #cdd5dd;
                border-radius: 10px;
                alternate-background-color: #f4f6f8;
                selection-background-color: #d9e0e8;
                selection-color: #1f2b38;
                padding: 5px 7px;
            }
            QHeaderView::section {
                background: #e7ebf0;
                color: #324353;
                border: none;
                border-bottom: 1px solid #cfd6de;
                padding: 7px 9px;
                font-weight: 700;
            }
            QLabel {
                color: #2a3744;
            }
            QLabel#panelTitle {
                font-size: 16px;
                font-weight: 700;
                color: #273442;
            }
            QLabel#panelSubtitle {
                color: #7a8591;
                font-size: 10pt;
            }
            QComboBox::drop-down {
                border: none;
                width: 22px;
            }
            QComboBox::down-arrow {
                width: 10px;
                height: 10px;
            }
            QCheckBox {
                spacing: 8px;
            }
            QMenu {
                background: #fbfcfd;
                border: 1px solid #d2d8df;
                padding: 8px;
                border-radius: 12px;
            }
            QMenu::item {
                padding: 7px 26px 7px 12px;
                border-radius: 8px;
            }
            QMenu::item:selected {
                background: #dde3ea;
                color: #243241;
            }
            QStatusBar {
                background: #e8edf1;
                border-top: 1px solid #d1d8df;
            }
            QStatusBar QLabel {
                color: #334351;
                padding: 0 6px;
            }
            QSplitter::handle {
                background: #d7dde4;
            }
            QSplitter::handle:hover {
                background: #bcc7d2;
            }
            QProgressDialog {
                border: 1px solid #cfd6de;
                border-radius: 16px;
            }
            QProgressDialog QProgressBar {
                min-height: 18px;
                border: 1px solid #c7ced7;
                border-radius: 9px;
                background: #f6f8fa;
                text-align: center;
                color: #33414f;
            }
            QProgressDialog QProgressBar::chunk {
                border-radius: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #8c9cab, stop:1 #586979);
            }
            """
        )

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root_layout = QtWidgets.QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        toolbar_card = QtWidgets.QFrame()
        toolbar_card.setObjectName("toolbarCard")
        root_layout.addWidget(toolbar_card)
        top_bar = QtWidgets.QHBoxLayout(toolbar_card)
        top_bar.setContentsMargins(12, 10, 12, 10)
        top_bar.setSpacing(10)

        self.file_button = QtWidgets.QToolButton()
        self.file_button.setText("文件")
        self.file_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.file_menu = QtWidgets.QMenu(self)
        self.file_button.setMenu(self.file_menu)
        top_bar.addWidget(self.file_button)

        self.btn_pipeline_panel = QtWidgets.QPushButton("处理流程设置")
        self.btn_pipeline_panel.clicked.connect(self._open_pipeline_settings)
        top_bar.addWidget(self.btn_pipeline_panel)

        self.btn_display_settings = QtWidgets.QPushButton("显示设置")
        self.btn_display_settings.clicked.connect(self._open_display_settings)
        top_bar.addWidget(self.btn_display_settings)

        self.btn_start = QtWidgets.QPushButton("开始处理")
        self.btn_start.setObjectName("primaryAction")
        self.btn_start.clicked.connect(self._start_processing)
        top_bar.addWidget(self.btn_start)

        self.btn_help = QtWidgets.QPushButton("帮助")
        self.btn_help.clicked.connect(self._open_help_dir)
        top_bar.addWidget(self.btn_help)
        top_bar.addStretch(1)
        self._apply_soft_shadow(toolbar_card, blur_radius=18, y_offset=3, alpha=20)

        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        root_layout.addWidget(self.main_splitter, stretch=1)

        self.project_panel = QtWidgets.QFrame()
        self.project_panel.setObjectName("sideCard")
        project_layout = QtWidgets.QVBoxLayout(self.project_panel)
        project_layout.setContentsMargins(10, 10, 10, 10)
        project_layout.setSpacing(8)
        project_header = QtWidgets.QHBoxLayout()
        project_header.setContentsMargins(0, 0, 0, 0)
        project_header.setSpacing(6)
        self.project_title_label = QtWidgets.QLabel("工程")
        self.project_title_label.setObjectName("panelTitle")
        project_header.addWidget(self.project_title_label)
        project_header.addStretch(1)
        self.project_panel_toggle = QtWidgets.QToolButton()
        self.project_panel_toggle.setText("◀")
        self.project_panel_toggle.setAutoRaise(False)
        self.project_panel_toggle.setFixedSize(28, 28)
        self.project_panel_toggle.clicked.connect(self._toggle_project_panel)
        project_header.addWidget(self.project_panel_toggle)
        project_layout.addLayout(project_header)
        self.project_tree = QtWidgets.QTreeWidget()
        self.project_tree.setHeaderHidden(True)
        self.project_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.project_tree.itemActivated.connect(self._on_project_tree_item_activated)
        self.project_tree.itemClicked.connect(self._on_project_tree_item_clicked)
        self.project_tree.customContextMenuRequested.connect(self._show_project_tree_menu)
        project_layout.addWidget(self.project_tree, stretch=1)
        self.main_splitter.addWidget(self.project_panel)
        self._apply_soft_shadow(self.project_panel)

        self.view_tabs = QtWidgets.QTabWidget()
        self.view_tabs.setDocumentMode(True)
        self.view_tabs.setTabPosition(QtWidgets.QTabWidget.North)

        overview_panel = QtWidgets.QFrame()
        overview_panel.setObjectName("viewCard")
        overview_layout = QtWidgets.QVBoxLayout(overview_panel)
        overview_layout.setContentsMargins(14, 14, 14, 14)
        overview_layout.setSpacing(8)
        self.overview_map = OverviewMapWidget()
        self.overview_map.setStyleSheet("background: #fafbfc; border: 1px solid #d0d7df; border-radius: 14px;")
        self.overview_map.region_activated.connect(self._on_overview_region_activated)
        self.overview_map.point_selected.connect(self._on_overview_point_selected)
        overview_layout.addWidget(self.overview_map, stretch=1)
        self.view_tabs.addTab(overview_panel, "Overview")

        explore_panel = QtWidgets.QFrame()
        explore_panel.setObjectName("viewCard")
        explore_layout = QtWidgets.QVBoxLayout(explore_panel)
        explore_layout.setContentsMargins(14, 14, 14, 14)
        explore_layout.setSpacing(8)
        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)
        self.bscan_view = RasterViewportWidget(
            "bscan",
            title="Distance (m)",
            y_label="Time (ns)",
            margins=(52, 10, 34, 18),
            allow_zoom_x=True,
            allow_zoom_y=True,
        )
        self.bscan_view.setStyleSheet("background: #fafbfc; border: 1px solid #d0d7df; border-radius: 14px;")
        top_row.addWidget(self.bscan_view, stretch=90)
        self.width_view = RasterViewportWidget(
            "width",
            title="Width (m)",
            y_label="Time (ns)",
            margins=(28, 8, 30, 16),
            allow_zoom_x=False,
            allow_zoom_y=True,
        )
        self.width_view.setStyleSheet("background: #fafbfc; border: 1px solid #d0d7df; border-radius: 14px;")
        top_row.addWidget(self.width_view, stretch=14)
        self.trace_view = TraceViewportWidget(
            "trace",
            title="Trace line",
            margins=(20, 8, 30, 16),
            allow_zoom_x=False,
            allow_zoom_y=True,
        )
        self.trace_view.setStyleSheet("background: #fafbfc; border: 1px solid #d0d7df; border-radius: 14px;")
        top_row.addWidget(self.trace_view, stretch=14)
        explore_layout.addLayout(top_row, stretch=7)

        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(10)

        self.cscan_view = RasterViewportWidget(
            "cscan",
            title="Distance (m)",
            y_label="Width (m)",
            margins=(52, 10, 34, 22),
            allow_zoom_x=True,
            allow_zoom_y=False,
        )
        self.cscan_view.setStyleSheet("background: #fafbfc; border: 1px solid #d0d7df; border-radius: 14px;")
        bottom_row.addWidget(self.cscan_view, stretch=88)

        bottom_right_panel = QtWidgets.QFrame()
        bottom_right_panel.setObjectName("sideCard")
        right_layout = QtWidgets.QVBoxLayout(bottom_right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(6)
        self.ax_s = None

        slider_group = QtWidgets.QGroupBox("联动切片")
        slider_layout = QtWidgets.QFormLayout(slider_group)
        slider_layout.setLabelAlignment(QtCore.Qt.AlignLeft)
        slider_layout.setHorizontalSpacing(6)
        slider_layout.setVerticalSpacing(4)
        self.trace_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.trace_slider.valueChanged.connect(self._on_trace_slider_changed)
        self.trace_value_edit = QtWidgets.QLineEdit("0")
        self.trace_value_edit.setReadOnly(True)
        self.trace_value_edit.setMaximumWidth(86)
        trace_row = QtWidgets.QHBoxLayout()
        trace_row.setSpacing(4)
        trace_row.addWidget(self.trace_slider, stretch=1)
        trace_row.addWidget(self.trace_value_edit)
        self.line_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.line_slider.valueChanged.connect(self._on_line_slider_changed)
        self.line_value_edit = QtWidgets.QLineEdit("0")
        self.line_value_edit.setReadOnly(True)
        self.line_value_edit.setMaximumWidth(86)
        line_row = QtWidgets.QHBoxLayout()
        line_row.setSpacing(4)
        line_row.addWidget(self.line_slider, stretch=1)
        line_row.addWidget(self.line_value_edit)
        self.sample_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sample_slider.valueChanged.connect(self._on_sample_slider_changed)
        self.sample_value_edit = QtWidgets.QLineEdit("0")
        self.sample_value_edit.setReadOnly(True)
        self.sample_value_edit.setMaximumWidth(86)
        sample_row = QtWidgets.QHBoxLayout()
        sample_row.setSpacing(4)
        sample_row.addWidget(self.sample_slider, stretch=1)
        sample_row.addWidget(self.sample_value_edit)
        slider_layout.addRow("Crossline slice (X)", trace_row)
        slider_layout.addRow("Inline slice (Y)", line_row)
        slider_layout.addRow("Horizontal slice (Z)", sample_row)
        slider_group.setStyleSheet(
            "QGroupBox { font-size: 11px; } "
            "QLabel { font-size: 11px; } "
            "QLineEdit { font-size: 11px; padding: 2px 4px; min-height: 18px; }"
        )
        right_layout.addWidget(slider_group, stretch=1)

        info_group = QtWidgets.QGroupBox("当前设置")
        info_layout = QtWidgets.QVBoxLayout(info_group)
        info_layout.setContentsMargins(8, 10, 8, 8)
        self.trace_info = QtWidgets.QTextBrowser()
        self.trace_info.setOpenLinks(False)
        self.trace_info.setReadOnly(True)
        self.trace_info.setStyleSheet("padding: 8px; background: #fafbfc; border: 1px solid #d0d7df; border-radius: 12px;")
        info_layout.addWidget(self.trace_info)
        info_group.setStyleSheet("QGroupBox { font-size: 11px; } QTextBrowser { font-size: 11px; }")
        right_layout.addWidget(info_group, stretch=1)
        bottom_row.addWidget(bottom_right_panel, stretch=28)
        explore_layout.addLayout(bottom_row, stretch=3)

        self.view_tabs.addTab(explore_panel, "Explore")
        self.main_splitter.addWidget(self.view_tabs)
        self._apply_soft_shadow(explore_panel)
        self._apply_soft_shadow(overview_panel)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.splitterMoved.connect(self._on_main_splitter_moved)
        self.main_splitter.setSizes([self._project_panel_last_width, 1400])

        self.status_project = QtWidgets.QLabel("工程: ---")
        self.status_filename = QtWidgets.QLabel("文件: ---")
        self.status_stage = QtWidgets.QLabel("阶段: 等待导入")
        self.status_selection = QtWidgets.QLabel("选点: ---")
        self.status_busy = QtWidgets.QLabel("状态: 就绪")
        self.statusBar().addPermanentWidget(self.status_project)
        self.statusBar().addPermanentWidget(self.status_filename)
        self.statusBar().addPermanentWidget(self.status_stage)
        self.statusBar().addPermanentWidget(self.status_selection)
        self.statusBar().addPermanentWidget(self.status_busy)

        for widget in (self.bscan_view, self.width_view, self.cscan_view, self.trace_view):
            widget.view_changed.connect(self._on_view_changed)
            widget.point_selected.connect(self._on_view_selected)
            widget.guide_moved.connect(self._on_view_selected)
        self.pipeline_dialog = self._build_pipeline_dialog()
        self._refresh_file_menu()

    def _build_pipeline_dialog(self) -> QtWidgets.QDialog:
        dialog = QtWidgets.QDialog(self)
        dialog.setObjectName("pipelineDialog")
        dialog.setWindowTitle("处理流程设置")
        dialog.setModal(False)
        dialog.setWindowModality(QtCore.Qt.NonModal)
        self._apply_soft_shadow(dialog, blur_radius=28, y_offset=8, alpha=30)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(0)
        layout.addWidget(self._build_pipeline_panel())
        dialog.rejected.connect(self._on_pipeline_dialog_rejected)
        return dialog

    def _apply_default_splitter_ratio(self) -> None:
        if self.main_splitter.count() < 2:
            return
        total = max(self.main_splitter.size().width(), 1000)
        left = min(max(200, self._project_panel_last_width), int(total * 0.22))
        self.main_splitter.setSizes([left, max(total - left, 1)])

    def _on_main_splitter_moved(self, pos: int, index: int) -> None:
        if index != 1 or self._project_panel_collapsed:
            return
        self._project_panel_last_width = max(200, min(pos, 460))

    def _toggle_project_panel(self) -> None:
        self._set_project_panel_collapsed(not self._project_panel_collapsed)

    def _set_project_panel_collapsed(self, collapsed: bool) -> None:
        self._project_panel_collapsed = collapsed
        if collapsed:
            current = self.main_splitter.sizes()[0] if self.main_splitter.sizes() else self._project_panel_last_width
            self._project_panel_last_width = max(200, current)
            self.project_tree.hide()
            self.project_title_label.hide()
            self.project_panel.setMinimumWidth(36)
            self.project_panel.setMaximumWidth(36)
            self.project_panel_toggle.setText("▶")
            self.main_splitter.setSizes([36, max(self.main_splitter.width() - 36, 1)])
        else:
            self.project_tree.show()
            self.project_title_label.show()
            self.project_panel.setMinimumWidth(200)
            self.project_panel.setMaximumWidth(16777215)
            self.project_panel_toggle.setText("◀")
            width = max(200, self._project_panel_last_width)
            self.main_splitter.setSizes([width, max(self.main_splitter.width() - width, 1)])

    def _prepare_pipeline_dialog_geometry(self) -> None:
        if self.pipeline_dialog is None:
            return
        screen = self.windowHandle().screen() if self.windowHandle() is not None else QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            self.pipeline_dialog.resize(900, 680)
            return
        available = screen.availableGeometry()
        width = min(max(760, int(available.width() * 0.38)), 980)
        height = min(max(560, int(available.height() * 0.72)), 780)
        self.pipeline_dialog.resize(width, height)

        anchor = self.frameGeometry()
        x = min(anchor.right() - width - 24, available.right() - width - 12)
        y = min(max(anchor.top() + 48, available.top() + 12), available.bottom() - height - 12)
        x = max(x, available.left() + 12)
        y = max(y, available.top() + 12)
        self.pipeline_dialog.move(x, y)

    def _on_pipeline_dialog_rejected(self) -> None:
        if self.app_controller.pipeline_state.has_unapplied_changes:
            self.app_controller.restore_pipeline_draft()

    def _build_pipeline_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setMinimumWidth(760)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        title_label = QtWidgets.QLabel("处理流程设置")
        title_label.setObjectName("panelTitle")
        layout.addWidget(title_label)

        self.pipeline_state_label = QtWidgets.QLabel("当前草稿已应用")
        self.pipeline_state_label.setObjectName("panelSubtitle")
        layout.addWidget(self.pipeline_state_label)

        self.pipeline_counts_label = QtWidgets.QLabel("频域 0 步 | 时频转换 0 步 | 时域 0 步")
        self.pipeline_counts_label.setObjectName("panelSubtitle")
        layout.addWidget(self.pipeline_counts_label)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(12)
        layout.addLayout(body, stretch=1)

        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        body.addWidget(left_panel, stretch=3)

        toolbar = QtWidgets.QHBoxLayout()
        self.btn_add_frequency = QtWidgets.QPushButton("添加频域步骤")
        self.btn_add_frequency.clicked.connect(lambda: self._add_operation(StepKind.FREQUENCY))
        self.btn_set_transform = QtWidgets.QPushButton("设置时频转换")
        self.btn_set_transform.clicked.connect(lambda: self._add_operation(StepKind.TRANSFORM))
        self.btn_add_time = QtWidgets.QPushButton("添加时域步骤")
        self.btn_add_time.clicked.connect(lambda: self._add_operation(StepKind.TIME))
        toolbar.addWidget(self.btn_add_frequency)
        toolbar.addWidget(self.btn_set_transform)
        toolbar.addWidget(self.btn_add_time)
        toolbar.addStretch(1)
        left_layout.addLayout(toolbar)

        self.pipeline_sequence_label = QtWidgets.QLabel("当前执行顺序共 0 步")
        self.pipeline_sequence_label.setStyleSheet("color: #415467; font-weight: 600;")
        left_layout.addWidget(self.pipeline_sequence_label)

        self.pipeline_step_list = QtWidgets.QListWidget()
        self.pipeline_step_list.setAlternatingRowColors(True)
        self.pipeline_step_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.pipeline_step_list.setMinimumHeight(320)
        self.pipeline_step_list.currentRowChanged.connect(self._select_pipeline_row)
        self.pipeline_step_list.itemDoubleClicked.connect(lambda _item: self._toggle_selected_step_enabled_from_list())
        self.pipeline_step_list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.pipeline_step_list.customContextMenuRequested.connect(self._show_pipeline_context_menu)
        left_layout.addWidget(self.pipeline_step_list, stretch=1)

        self.pipeline_scope_label = QtWidgets.QLabel(
            "当前版本先作用于当前 DAT 的处理草稿。跨文件或跨区域复用请使用“另存为模板”。"
        )
        self.pipeline_scope_label.setWordWrap(True)
        self.pipeline_scope_label.setStyleSheet("color: #6f7c89;")
        left_layout.addWidget(self.pipeline_scope_label)

        body.addWidget(self._build_parameter_panel(), stretch=2)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.btn_cancel_draft = QtWidgets.QPushButton("取消")
        self.btn_cancel_draft.clicked.connect(self._cancel_pipeline_dialog)
        self.btn_apply_draft = QtWidgets.QPushButton("确定")
        self.btn_apply_draft.clicked.connect(self._apply_pipeline_draft_and_close)
        self.btn_save_template = QtWidgets.QPushButton("另存为模板")
        self.btn_save_template.clicked.connect(self._save_template)
        buttons.addWidget(self.btn_cancel_draft)
        buttons.addWidget(self.btn_save_template)
        buttons.addWidget(self.btn_apply_draft)
        layout.addLayout(buttons)
        return panel

    def _build_parameter_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QGroupBox("Properties / 参数设置")
        panel.setStyleSheet("QGroupBox { font-weight: 600; color: #334351; }")
        layout = QtWidgets.QVBoxLayout(panel)
        self.parameter_summary_label = QtWidgets.QLabel("参数信息")
        self.parameter_summary_label.setWordWrap(True)
        self.parameter_summary_label.setStyleSheet("color: #6b7784;")
        layout.addWidget(self.parameter_summary_label)
        self.parameter_state_label = QtWidgets.QLabel("当前未选中算法")
        self.parameter_state_label.setStyleSheet("color: #6f7c89; background: #f5f7fa; border: 1px solid #d4dbe3; border-radius: 10px; padding: 6px 8px;")
        layout.addWidget(self.parameter_state_label)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight)
        self.step_name_label = QtWidgets.QLabel("---")
        self.step_module_label = QtWidgets.QLabel("---")
        self.step_kind_label = QtWidgets.QLabel("---")
        self.step_order_label = QtWidgets.QLabel("---")
        self.step_enabled_check = QtWidgets.QCheckBox("启用当前算法")
        self.step_enabled_check.toggled.connect(self._toggle_selected_step_enabled)
        form.addRow("名称", self.step_name_label)
        form.addRow("模块", self.step_module_label)
        form.addRow("阶段", self.step_kind_label)
        form.addRow("顺序", self.step_order_label)
        form.addRow("", self.step_enabled_check)
        layout.addLayout(form)

        self.step_params_table = QtWidgets.QTableWidget(0, 2)
        self.step_params_table.setHorizontalHeaderLabels(["参数", "值"])
        self.step_params_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.step_params_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.step_params_table.verticalHeader().setVisible(False)
        self.step_params_table.setShowGrid(False)
        self.step_params_table.setAlternatingRowColors(True)
        self.step_params_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.step_params_table.setEditTriggers(
            QtWidgets.QAbstractItemView.DoubleClicked
            | QtWidgets.QAbstractItemView.EditKeyPressed
            | QtWidgets.QAbstractItemView.SelectedClicked
        )
        layout.addWidget(self.step_params_table)

        button_row = QtWidgets.QHBoxLayout()
        self.btn_apply_params = QtWidgets.QPushButton("应用参数")
        self.btn_apply_params.clicked.connect(self._apply_step_params)
        self.btn_reset_params = QtWidgets.QPushButton("恢复默认")
        self.btn_reset_params.clicked.connect(self._reset_selected_step_params)
        button_row.addWidget(self.btn_apply_params)
        button_row.addWidget(self.btn_reset_params)
        layout.addLayout(button_row)
        action_row = QtWidgets.QHBoxLayout()
        self.btn_step_up = QtWidgets.QPushButton("上移")
        self.btn_step_up.clicked.connect(lambda: self._move_selected_step(-1))
        self.btn_step_down = QtWidgets.QPushButton("下移")
        self.btn_step_down.clicked.connect(lambda: self._move_selected_step(1))
        self.btn_step_delete = QtWidgets.QPushButton("删除该算法")
        self.btn_step_delete.clicked.connect(self._delete_selected_step)
        action_row.addWidget(self.btn_step_up)
        action_row.addWidget(self.btn_step_down)
        action_row.addWidget(self.btn_step_delete)
        layout.addLayout(action_row)
        return panel

    def _connect_signals(self) -> None:
        signals = self.app_controller.signals
        signals.project_changed.connect(self._on_project_changed)
        signals.dataset_loaded.connect(self._on_dataset_loaded)
        signals.display_ready.connect(self._refresh_display)
        signals.pipeline_changed.connect(lambda _steps: self._refresh_pipeline_draft())
        signals.pipeline_applied.connect(lambda _steps: self._refresh_pipeline_draft())
        signals.processing_finished.connect(self._on_processing_finished)
        signals.progress_changed.connect(self._on_progress_changed)
        signals.status_message.connect(self.statusBar().showMessage)
        signals.error.connect(self._show_error)
        signals.busy_changed.connect(self._on_busy_changed)

    def _refresh_file_menu(self) -> None:
        self.file_menu.clear()
        self.action_new_project = self.file_menu.addAction("新建工程", self._new_project)
        self.action_open_project = self.file_menu.addAction("打开工程", self._open_project)
        if self.app_controller.project_state.is_open:
            self.action_save_project = self.file_menu.addAction("保存工程", self._save_project)
            self.file_menu.addSeparator()
            self.action_import_data = self.file_menu.addAction("导入数据", self._load_data)
            self.action_load_template = self.file_menu.addAction("加载模板", self._load_template)
            self.action_save_processed = self.file_menu.addAction("保存处理结果", self._save_processed)
        else:
            self.action_save_project = None
            self.action_import_data = None
            self.action_load_template = None
            self.action_save_processed = None

    def _new_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        name, root_path = dialog.values()
        if not name or not root_path:
            QtWidgets.QMessageBox.warning(self, "提示", "请填写工程名称和工程路径。")
            return
        self.app_controller.create_project(name, root_path)

    def _open_project(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "打开工程", "", "GPR Project (*.gpr.json)")
        if not path:
            return
        self.app_controller.open_project(path)

    def _save_project(self) -> None:
        try:
            project_file = self.app_controller.save_project()
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "提示", str(exc))
            return
        self.statusBar().showMessage(f"工程已保存到 {project_file}", 4000)

    def _load_data(self) -> None:
        if not self.app_controller.project_state.is_open:
            QtWidgets.QMessageBox.warning(self, "提示", "请先新建工程或打开工程。")
            return
        project_root = self.app_controller.project_state.root_path or ""
        start_dir = str(Path(project_root) / "data") if project_root else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 DAT 数据文件", start_dir, "DAT Files (*.dat)")
        if not path:
            return
        self.app_controller.import_data(path, None)

    def _load_template(self) -> None:
        if self.app_controller.dataset is None:
            QtWidgets.QMessageBox.warning(self, "提示", "请先导入 DAT 数据，再加载流程模板。")
            return
        project_root = self.app_controller.project_state.root_path or ""
        start_dir = str(Path(project_root) / "templates") if project_root else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "加载流程模板", start_dir, "Templates (*.json *.mat)")
        if not path:
            return
        self.app_controller.load_pipeline_template(path)
        self._refresh_settings_info()
        self.statusBar().showMessage("流程模板已载入，可在“处理流程设置”中继续调整。", 3000)

    def _save_template(self) -> None:
        project_root = self.app_controller.project_state.root_path or ""
        start_dir = str(Path(project_root) / "templates") if project_root else ""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "另存为模板", start_dir, "JSON (*.json);;MAT 文件 (*.mat)")
        if path:
            self.app_controller.save_pipeline_template(path)

    def _save_processed(self) -> None:
        snapshot = self.app_controller.result_state.active_snapshot
        if snapshot is None or snapshot.pipeline_index <= 0:
            QtWidgets.QMessageBox.warning(self, "提示", "当前还没有可保存的处理结果。")
            return
        project_root = self.app_controller.project_state.root_path or ""
        start_dir = str(Path(project_root) / "results") if project_root else ""
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "保存处理结果", start_dir, "NumPy (*.npy);;MAT 文件 (*.mat)")
        if path:
            self.app_controller.save_processed_data(path)

    def _open_pipeline_settings(self) -> None:
        if self.app_controller.dataset is None:
            QtWidgets.QMessageBox.warning(self, "提示", "请先导入 DAT 数据，再设置处理流程。")
            return
        self._show_pipeline_panel(True)

    def _show_pipeline_panel(self, visible: bool) -> None:
        if self.pipeline_dialog is None:
            return
        if not visible:
            if self.pipeline_dialog.isVisible():
                self.pipeline_dialog.reject()
            return
        self._refresh_pipeline_draft()
        self._prepare_pipeline_dialog_geometry()
        self.pipeline_dialog.show()
        self.pipeline_dialog.raise_()
        self.pipeline_dialog.activateWindow()

    def _open_display_settings(self) -> None:
        dialog = DisplaySettingsDialog(self.app_controller, self)
        dialog.setObjectName("displayDialog")
        self._apply_soft_shadow(dialog, blur_radius=28, y_offset=8, alpha=30)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        self.app_controller.update_display_settings(**dialog.values())
        self._has_custom_viewport = False
        self._display_configured = True
        self._refresh_settings_info()
        self.statusBar().showMessage("显示设置已应用。", 3000)

    def _start_processing(self) -> None:
        if self.app_controller.pipeline_state.has_unapplied_changes:
            choice = QtWidgets.QMessageBox.question(
                self,
                "应用流程草稿",
                "当前流程草稿尚未应用，是否先应用当前草稿再开始处理？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Yes,
            )
            if choice == QtWidgets.QMessageBox.Cancel:
                return
            if choice == QtWidgets.QMessageBox.Yes:
                self.app_controller.apply_pipeline_draft()
        ok, message = self.app_controller.validate_processing_ready()
        if not ok:
            QtWidgets.QMessageBox.warning(self, "无法开始处理", message)
            return
        step_count = sum(1 for step in self.app_controller.pipeline_state.applied_steps if step.enabled)
        self._set_stage_status("处理中")
        self.statusBar().showMessage(f"已开始执行处理流程，本次共 {step_count} 步。", 4000)
        self.app_controller.execute_pipeline()

    def _apply_pipeline_draft_and_close(self) -> None:
        had_processed_result = self._has_processed_result()
        previous_steps = [step.clone() for step in self.app_controller.pipeline_state.applied_steps]
        previous_snapshots = list(self.app_controller.result_state.history)
        self.app_controller.apply_pipeline_draft()
        self._pipeline_configured = True
        self._refresh_settings_info()
        if had_processed_result and previous_steps and previous_snapshots:
            self._set_stage_status("处理中")
            self.statusBar().showMessage("已应用当前流程草稿，正在根据新流程快速刷新图像。", 4000)
            self.app_controller.execute_pipeline(
                previous_steps=previous_steps,
                previous_snapshots=previous_snapshots,
            )
            return
        self._set_stage_status("草稿已应用")
        self.statusBar().showMessage("已应用当前流程草稿。可点击顶部“开始处理”。", 4000)

    def _cancel_pipeline_dialog(self) -> None:
        if self.pipeline_dialog is not None:
            self.pipeline_dialog.reject()

    def _refresh_pipeline_draft(self) -> None:
        controller = self.app_controller.pipeline_controller
        previous_index = self._selected_step_index
        self._ordered_steps = controller.get_steps()
        counts = controller.get_step_counts()

        self.pipeline_step_list.blockSignals(True)
        self.pipeline_step_list.clear()
        for index, step in enumerate(self._ordered_steps, start=1):
            item = QtWidgets.QListWidgetItem(self._format_step_label(step, index))
            if step.kind is StepKind.FREQUENCY:
                item.setForeground(QtGui.QColor("#1f5fbf"))
            elif step.kind is StepKind.TRANSFORM:
                item.setForeground(QtGui.QColor("#7a4a00"))
            elif not step.enabled:
                item.setForeground(QtGui.QColor("#8c97a3"))
            else:
                item.setForeground(QtGui.QColor("#2d3b48"))
            self.pipeline_step_list.addItem(item)
        selected_index = -1
        if self._ordered_steps:
            selected_index = min(previous_index, len(self._ordered_steps) - 1) if previous_index >= 0 else 0
        self._selected_step_index = selected_index
        self.pipeline_step_list.setCurrentRow(selected_index)
        self.pipeline_step_list.blockSignals(False)

        dirty = self.app_controller.pipeline_state.has_unapplied_changes
        self.pipeline_state_label.setText(
            f"当前草稿有未应用修改 | 已应用流程 {len(self.app_controller.pipeline_state.applied_steps)} 步"
            if dirty
            else f"当前草稿已应用 | 已应用流程 {len(self.app_controller.pipeline_state.applied_steps)} 步"
        )
        self.pipeline_state_label.setStyleSheet("color: #6a4d2d;" if dirty else "color: #6b7784;")
        self.pipeline_counts_label.setText(
            f"频域 {counts[StepKind.FREQUENCY]} 步 | "
            f"时频转换 {counts[StepKind.TRANSFORM]} 步 | "
            f"时域 {counts[StepKind.TIME]} 步"
        )
        self.pipeline_sequence_label.setText(
            f"当前执行顺序共 {len(self._ordered_steps)} 步，按列表从上到下运行。"
        )
        self._refresh_settings_info()
        self._refresh_step_details()
        self._update_top_actions()

    def _format_step_label(self, step, index: int) -> str:
        stage = {
            StepKind.FREQUENCY: "频域",
            StepKind.TRANSFORM: "时频转换",
            StepKind.TIME: "时域",
        }[step.kind]
        state = "必选" if step.kind is StepKind.TRANSFORM else ("启用" if step.enabled else "停用")
        return f"{index:02d}. [{stage}] {step.name}  ({state})"

    def _select_pipeline_row(self, row: int) -> None:
        if row < 0:
            self._selected_step_index = -1
            self._refresh_step_details()
            self._update_top_actions()
            return
        self._selected_step_index = row
        self._refresh_step_details()
        self._update_top_actions()

    def _current_selected_step(self):
        if self._selected_step_index < 0:
            return None
        if self._selected_step_index >= len(self._ordered_steps):
            return None
        return self._ordered_steps[self._selected_step_index]

    def _refresh_step_details(self) -> None:
        step = self._current_selected_step()
        if step is None:
            self.step_name_label.setText("---")
            self.step_module_label.setText("---")
            self.step_kind_label.setText("---")
            self.step_order_label.setText("---")
            self.step_enabled_check.blockSignals(True)
            self.step_enabled_check.setChecked(False)
            self.step_enabled_check.blockSignals(False)
            self.step_enabled_check.setEnabled(False)
            self.step_params_table.setRowCount(0)
            self.step_params_table.setEnabled(False)
            self.btn_apply_params.setEnabled(False)
            self.btn_reset_params.setEnabled(False)
            self.parameter_summary_label.setText("未选择算法")
            self.parameter_state_label.setText("当前未选中算法")
            return

        module = self.app_controller.pipeline_controller.section_title_for_step(step)
        self.step_name_label.setText(step.name)
        self.step_module_label.setText(module)
        self.step_kind_label.setText(self._kind_text(step.kind))
        self.step_order_label.setText(f"第 {self._selected_step_index + 1} 步 / 共 {len(self._ordered_steps)} 步")
        can_toggle = step.kind is not StepKind.TRANSFORM and not self._is_busy
        self.step_enabled_check.blockSignals(True)
        self.step_enabled_check.setChecked(step.enabled)
        self.step_enabled_check.blockSignals(False)
        self.step_enabled_check.setEnabled(can_toggle)

        spec = SPEC_BY_TYPE.get(step.op_type)
        param_specs = list(spec.params) if spec is not None else []
        has_params = bool(param_specs)
        self.parameter_state_label.setText(
            f"当前选中: {step.name} | {'已启用' if step.enabled else '已停用'}"
            if step.kind is not StepKind.TRANSFORM
            else f"当前选中: {step.name} | 必选步骤"
        )
        self.parameter_summary_label.setText(
            f"共 {len(param_specs)} 个可编辑参数"
            if has_params
            else "当前算法无可编辑参数"
        )
        self.step_params_table.setEnabled(has_params and not self._is_busy)
        self.btn_apply_params.setEnabled(has_params and not self._is_busy)
        self.btn_reset_params.setEnabled(has_params and not self._is_busy)
        self.step_params_table.setRowCount(len(param_specs))
        for idx, param_spec in enumerate(param_specs):
            label_item = QtWidgets.QTableWidgetItem(param_spec.label)
            label_item.setFlags(label_item.flags() & ~QtCore.Qt.ItemIsEditable)
            current_value = step.params[idx] if idx < len(step.params) else param_spec.default
            value_item = QtWidgets.QTableWidgetItem(str(current_value))
            self.step_params_table.setItem(idx, 0, label_item)
            self.step_params_table.setItem(idx, 1, value_item)
        can_move_up = self.app_controller.can_move_pipeline_step(self._selected_step_index, -1) and not self._is_busy
        can_move_down = self.app_controller.can_move_pipeline_step(self._selected_step_index, 1) and not self._is_busy
        self.btn_step_up.setEnabled(can_move_up)
        self.btn_step_down.setEnabled(can_move_down)
        self.btn_step_delete.setEnabled(step.kind is not StepKind.TRANSFORM and not self._is_busy)

    def _add_operation(self, kind: StepKind) -> None:
        if kind is StepKind.TRANSFORM:
            menu = QtWidgets.QMenu(self)
            for op_type, title in self.app_controller.pipeline_controller.available_transform_options():
                menu.addAction(title, lambda checked=False, op=op_type: self.app_controller.set_transform_step(op))
            menu.exec(QtGui.QCursor.pos())
            return

        menu = QtWidgets.QMenu(self)
        for module in [module for module in MODULE_SPECS if module.kind is kind]:
            sub_menu = menu.addMenu(module.title)
            for op_type in module.operation_types:
                spec = SPEC_BY_TYPE.get(op_type)
                if spec is None:
                    continue
                sub_menu.addAction(spec.title, lambda checked=False, op=spec.op_type: self._open_operation_dialog(op))
        menu.exec(QtGui.QCursor.pos())

    def _open_operation_dialog(self, op_type: str) -> None:
        spec = SPEC_BY_TYPE[op_type]
        if not spec.params:
            self.app_controller.add_pipeline_step(op_type, [])
            return
        dialog = OperationDialog(spec, self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            params = spec.parse_params(dialog.values())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "参数错误", str(exc))
            return
        self.app_controller.add_pipeline_step(op_type, params)

    def _show_pipeline_context_menu(self, pos) -> None:
        row = self.pipeline_step_list.currentRow()
        if row < 0:
            return
        step = self._ordered_steps[row]
        menu = QtWidgets.QMenu(self)
        if step.kind is not StepKind.TRANSFORM:
            menu.addAction("切换启用状态", self._toggle_selected_step_enabled_from_list)
            menu.addAction("删除该算法", self._delete_selected_step)
        menu.exec(self.pipeline_step_list.mapToGlobal(pos))

    def _toggle_selected_step_enabled_from_list(self) -> None:
        step = self._current_selected_step()
        if step is None or step.kind is StepKind.TRANSFORM:
            return
        self.app_controller.set_pipeline_step_enabled(self._selected_step_index, not step.enabled)

    def _delete_selected_step(self) -> None:
        step = self._current_selected_step()
        if step is None or step.kind is StepKind.TRANSFORM:
            return
        self.app_controller.remove_pipeline_step(self._selected_step_index)

    def _toggle_selected_step_enabled(self, checked: bool) -> None:
        if self._selected_step_index < 0:
            return
        step = self._current_selected_step()
        if step is None or step.kind is StepKind.TRANSFORM:
            return
        self.app_controller.set_pipeline_step_enabled(self._selected_step_index, checked)

    def _move_selected_step(self, offset: int) -> None:
        if self._selected_step_index >= 0:
            self.app_controller.move_pipeline_step(self._selected_step_index, offset)
            self._selected_step_index = max(0, self._selected_step_index + offset)

    def _apply_step_params(self) -> None:
        step = self._current_selected_step()
        if step is None or self._selected_step_index < 0:
            return
        spec = SPEC_BY_TYPE.get(step.op_type)
        if spec is None:
            return
        try:
            values = []
            for idx in range(self.step_params_table.rowCount()):
                item = self.step_params_table.item(idx, 1)
                values.append("" if item is None else item.text().strip())
            params = spec.parse_params(values)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "参数错误", str(exc))
            return
        self.app_controller.set_pipeline_step_params(self._selected_step_index, params)
        self._refresh_settings_info()

    def _reset_selected_step_params(self) -> None:
        step = self._current_selected_step()
        if step is None:
            return
        spec = SPEC_BY_TYPE.get(step.op_type)
        if spec is None:
            return
        for idx, param_spec in enumerate(spec.params):
            item = self.step_params_table.item(idx, 1)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.step_params_table.setItem(idx, 1, item)
            item.setText(str(param_spec.default))

    def _on_project_changed(self, _project) -> None:
        self._update_project_title()
        self._refresh_file_menu()
        self._refresh_project_tree()
        self._refresh_overview_scene()
        if self.app_controller.dataset is None:
            self.display_data = None
            self._pipeline_configured = False
            self._display_configured = False
            self.status_filename.setText("文件: ---")
            self.status_selection.setText("选点: ---")
            self._set_stage_status("等待导入")
            self._show_pipeline_panel(False)
            self._show_waiting_plots()
            self._refresh_settings_info()
        self._update_top_actions()

    def _update_project_title(self) -> None:
        project_name = self.app_controller.project_state.name
        self.setWindowTitle(f"{project_name} - GPR Lab Pro V3")
        self.status_project.setText(f"工程: {project_name}")

    def _refresh_project_tree(self) -> None:
        current_region_id = self.app_controller.project_state.active_region_id
        self.project_tree.blockSignals(True)
        self.project_tree.clear()
        project_state = self.app_controller.project_state
        if not project_state.files:
            self.project_tree.blockSignals(False)
            return
        for file_item in project_state.files:
            file_node = QtWidgets.QTreeWidgetItem([file_item.name or "未命名文件"])
            file_node.setData(0, QtCore.Qt.UserRole, ("file", file_item.file_id))
            file_node.setExpanded(True)
            self.project_tree.addTopLevelItem(file_node)
            for region in file_item.regions:
                trace_count = max(region.trace_count(), 0)
                line_count = max(region.line_count(), 0)
                region_text = f"{region.name} [{trace_count}×{line_count}]"
                region_node = QtWidgets.QTreeWidgetItem([region_text])
                region_node.setData(0, QtCore.Qt.UserRole, ("region", region.region_id))
                file_node.addChild(region_node)
                if region.region_id == current_region_id:
                    self.project_tree.setCurrentItem(region_node)
        self.project_tree.expandAll()
        if self.project_tree.currentItem() is None and self.project_tree.topLevelItemCount() > 0:
            first_file = self.project_tree.topLevelItem(0)
            if first_file is not None and first_file.childCount() > 0:
                self.project_tree.setCurrentItem(first_file.child(0))
            else:
                self.project_tree.setCurrentItem(first_file)
        self.project_tree.blockSignals(False)

    def _refresh_overview_scene(self) -> None:
        project_state = self.app_controller.project_state
        if not project_state.files:
            self.overview_map.clear_scene()
            return
        regions: list[dict[str, object]] = []
        active_region_id = project_state.active_region_id
        active_trace = int(self.app_controller.selection_state.trace_index)
        active_line = int(self.app_controller.selection_state.line_index)
        active_image: QtGui.QImage | None = None
        if self.display_data is not None and self.display_data.cscan.size:
            cmap = self.app_controller.display_state.colormap + (
                "_r" if self.app_controller.display_state.invert and not self.app_controller.display_state.colormap.endswith("_r") else ""
            )
            active_image = self._array_to_qimage(self.display_data.cscan, cmap, self.display_data.cscan_limits)
        for file_item in project_state.files:
            for region in file_item.regions:
                regions.append(
                    {
                        "file_id": file_item.file_id,
                        "file_name": file_item.name,
                        "region_id": region.region_id,
                        "region_name": region.name,
                        "trace_count": max(region.trace_count(), 1),
                        "line_count": max(region.line_count(), 1),
                    }
                )
        self.overview_map.set_scene(
            regions,
            active_region_id=active_region_id,
            active_trace=active_trace,
            active_line=active_line,
            active_image=active_image,
        )

    def _activate_tree_item(self, item: QtWidgets.QTreeWidgetItem | None) -> None:
        if item is None:
            return
        payload = item.data(0, QtCore.Qt.UserRole)
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        kind, object_id = payload
        if kind == "region":
            self.app_controller.select_project_region(str(object_id))
            return
        if kind == "file":
            file_id = str(object_id)
            file_item = next((entry for entry in self.app_controller.project_state.files if entry.file_id == file_id), None)
            if file_item is not None and file_item.regions:
                self.app_controller.select_project_region(file_item.regions[0].region_id)

    def _on_project_tree_item_clicked(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        self._activate_tree_item(item)

    def _on_project_tree_item_activated(self, item: QtWidgets.QTreeWidgetItem, _column: int) -> None:
        self._activate_tree_item(item)

    def _show_project_tree_menu(self, pos: QtCore.QPoint) -> None:
        if self._is_busy:
            return
        item = self.project_tree.itemAt(pos)
        if item is None:
            return
        self.project_tree.setCurrentItem(item)
        payload = item.data(0, QtCore.Qt.UserRole)
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        kind, object_id = payload
        menu = QtWidgets.QMenu(self)
        if kind == "file":
            create_action = menu.addAction("新建区域")
            chosen = menu.exec(self.project_tree.viewport().mapToGlobal(pos))
            if chosen is create_action:
                base_region_id = None
                active_file = self.app_controller.project_controller.get_active_file()
                active_region = self.app_controller.project_controller.get_active_region()
                if active_file is not None and active_region is not None and active_file.file_id == str(object_id):
                    base_region_id = active_region.region_id
                self._create_region(str(object_id), base_region_id=base_region_id)
            return
        if kind == "region":
            create_action = menu.addAction("新建区域")
            rename_action = menu.addAction("重命名")
            bounds_action = menu.addAction("编辑范围")
            delete_action = menu.addAction("删除区域")
            chosen = menu.exec(self.project_tree.viewport().mapToGlobal(pos))
            region_id = str(object_id)
            if chosen is create_action:
                file_item = self.app_controller.project_controller.find_file_by_region_id(region_id)
                if file_item is not None:
                    self._create_region(file_item.file_id, base_region_id=region_id)
            elif chosen is rename_action:
                self._rename_region(region_id)
            elif chosen is bounds_action:
                self._edit_region_bounds(region_id)
            elif chosen is delete_action:
                self._delete_region(region_id)

    def _create_region(self, file_id: str, *, base_region_id: str | None = None) -> None:
        default_name = ""
        if base_region_id:
            base_region = self.app_controller.project_controller.get_region(base_region_id)
            if base_region is not None:
                default_name = f"{base_region.name}_copy"
        name, ok = QtWidgets.QInputDialog.getText(self, "新建区域", "区域名称", text=default_name)
        if not ok:
            return
        try:
            region_id = self.app_controller.create_project_region(file_id, name=name.strip() or None, base_region_id=base_region_id)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "区域", str(exc))
            return
        self._edit_region_bounds(region_id, allow_cancel=True)

    def _rename_region(self, region_id: str) -> None:
        region = self.app_controller.project_controller.get_region(region_id)
        if region is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "重命名区域", "区域名称", text=region.name)
        if not ok:
            return
        try:
            self.app_controller.rename_project_region(region_id, name)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "区域", str(exc))

    def _delete_region(self, region_id: str) -> None:
        region = self.app_controller.project_controller.get_region(region_id)
        if region is None:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "删除区域",
            f"删除区域“{region.name}”？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            self.app_controller.delete_project_region(region_id)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "区域", str(exc))

    def _edit_region_bounds(self, region_id: str, *, allow_cancel: bool = False) -> None:
        region = self.app_controller.project_controller.get_region(region_id)
        file_item = self.app_controller.project_controller.find_file_by_region_id(region_id)
        if region is None or file_item is None:
            return
        dataset = self.app_controller.project_controller.get_dataset_for_file(file_item.file_id)
        if dataset is None:
            QtWidgets.QMessageBox.warning(self, "区域", "请先激活该文件并完成数据加载。")
            return
        dialog = RegionBoundsDialog(
            region_name=region.name,
            trace_count=dataset.trace_count,
            line_count=dataset.line_count,
            sample_count=dataset.sample_count,
            current_bounds={
                "trace_start": region.trace_start,
                "trace_stop": region.trace_stop,
                "line_start": region.line_start,
                "line_stop": region.line_stop,
                "sample_start": region.sample_start,
                "sample_stop": region.sample_stop,
            },
            parent=self,
        )
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            if not allow_cancel:
                return
            return
        try:
            self.app_controller.update_project_region_bounds(region_id, **dialog.values())
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "区域", str(exc))

    def _on_overview_region_activated(self, region_id: str) -> None:
        self.app_controller.select_project_region(region_id)

    def _on_overview_point_selected(self, region_id: str, trace_index: int, line_index: int) -> None:
        if region_id != self.app_controller.project_state.active_region_id:
            self.app_controller.select_project_region(region_id)
            if self.app_controller.project_state.active_region_id != region_id:
                return
        self.app_controller.select_trace(int(trace_index))
        self.app_controller.select_line(int(line_index))

    def _on_dataset_loaded(self, dataset) -> None:
        self.display_data = None
        restoring_project = self.app_controller.is_restoring_project
        self._pipeline_configured = restoring_project
        self._display_configured = restoring_project
        self.status_filename.setText(f"文件: {dataset.filename}")
        self.status_selection.setText("选点: ---")
        self._show_pipeline_panel(False)
        self._refresh_pipeline_draft()
        self._refresh_settings_info()
        self._show_waiting_plots()
        self._refresh_project_tree()
        self._refresh_overview_scene()
        if restoring_project:
            self._set_stage_status("工程恢复中")
            QtCore.QTimer.singleShot(0, self._complete_project_restore_ui)
        else:
            self._set_stage_status("已导入待处理")
            ImportSummaryDialog(dataset, self).exec()
        self._update_top_actions()

    def _complete_project_restore_ui(self) -> None:
        self._pipeline_configured = True
        self._display_configured = True
        self._refresh_pipeline_draft()
        self._refresh_settings_info()
        self._set_stage_status("工程已恢复")
        self._update_top_actions()

    def _update_top_actions(self) -> None:
        has_project = self.app_controller.project_state.is_open
        has_dataset = self.app_controller.dataset is not None
        has_result = self._has_processed_result()
        dirty = self.app_controller.pipeline_state.has_unapplied_changes
        self._refresh_file_menu()
        if self.action_new_project is not None:
            self.action_new_project.setEnabled(not self._is_busy)
        if self.action_open_project is not None:
            self.action_open_project.setEnabled(not self._is_busy)
        if self.action_save_project is not None:
            self.action_save_project.setEnabled(has_project and not self._is_busy)
        if self.action_import_data is not None:
            self.action_import_data.setEnabled(has_project and not self._is_busy)
        if self.action_load_template is not None:
            self.action_load_template.setEnabled(has_dataset and not self._is_busy)
        if self.action_save_processed is not None:
            self.action_save_processed.setEnabled(has_result and not self._is_busy)
        self.btn_pipeline_panel.setEnabled(has_dataset and not self._is_busy)
        self.btn_display_settings.setEnabled(has_dataset and not self._is_busy)
        self.btn_start.setEnabled(has_project and has_dataset and not self._is_busy)
        self.btn_help.setEnabled(not self._is_busy)
        self.btn_pipeline_panel.setText("处理流程设置 *" if dirty else "处理流程设置")
        self.btn_cancel_draft.setEnabled(has_dataset and not self._is_busy)
        self.btn_apply_draft.setEnabled(has_dataset and dirty and not self._is_busy)
        self.btn_save_template.setEnabled(has_dataset and not self._is_busy)
        self.btn_add_frequency.setEnabled(has_dataset and not self._is_busy)
        self.btn_set_transform.setEnabled(has_dataset and not self._is_busy)
        self.btn_add_time.setEnabled(has_dataset and not self._is_busy)
        self.pipeline_step_list.setEnabled(has_dataset and not self._is_busy)
        self._refresh_step_details()
    def _refresh_display(self, display: DisplayData) -> None:
        self.display_data = display
        state = self.app_controller.display_state
        cmap = state.colormap + ("_r" if state.invert and not state.colormap.endswith("_r") else "")
        trace_count = int(display.meta.get("trace_count", display.bscan.shape[1] if display.bscan.ndim == 2 else 0))
        line_count = int(display.meta.get("line_count", display.cscan.shape[0] if display.cscan.ndim == 2 else 0))
        tw_ns = float(display.meta.get("tw_ns", display.ascan_time_ns[-1] if display.ascan_time_ns.size else 0.0))
        bscan_start_ns = float(display.meta.get("bscan_start_ns", 0.0))
        bscan_end_ns = float(display.meta.get("bscan_end_ns", tw_ns))
        dt_ns = float(display.meta.get("dt_ns", 1.0))
        selection = display.selection
        min_sample_idx, max_sample_idx = self._display_sample_bounds(display)
        clamped_sample_idx = int(np.clip(selection.sample_index, min_sample_idx, max_sample_idx))
        if clamped_sample_idx != selection.sample_index:
            self.app_controller.select_sample(clamped_sample_idx)
            return
        time_ns = selection.sample_index * dt_ns

        self._update_bscan_view(
            display,
            cmap,
            trace_count,
            tw_ns,
            bscan_start_ns,
            bscan_end_ns,
            selection.trace_index,
            time_ns,
        )
        self._update_crossline_view(display, cmap, line_count, tw_ns, selection.line_index, time_ns)
        self._update_cscan_view(display, cmap, trace_count, line_count, selection.trace_index, selection.line_index)
        self._update_ascan_view(display, selection.sample_index, time_ns)
        self._sync_slice_controls(display)
        self.status_selection.setText(self._selection_text(display))
        self._refresh_overview_scene()

    def _update_bscan_view(
        self,
        display: DisplayData,
        cmap: str,
        trace_count: int,
        total_tw_ns: float,
        start_ns: float,
        end_ns: float,
        trace_index: int,
        time_ns: float,
    ) -> None:
        bscan_view, visible_start_ns, visible_end_ns = self._crop_time_image(display.bscan, display.ascan_time_ns, start_ns, end_ns)
        initial_trace_window = self._default_trace_viewport(trace_count, selection_index=trace_index)
        image = self._array_to_qimage(bscan_view, cmap, display.bscan_limits)
        self.bscan_view.set_content(
            image,
            data_x=(0.0, float(max(trace_count - 1, 1))),
            data_y=(visible_start_ns, visible_end_ns),
            reset_view=not self._has_custom_viewport,
            show_axes=self.app_controller.display_state.show_axes,
            initial_viewport_x=initial_trace_window,
            initial_viewport_y=(visible_start_ns, visible_end_ns),
            viewport_limit_y=(visible_start_ns, visible_end_ns),
            vertical_line=(trace_index, "#00c16a"),
            horizontal_line=(time_ns, "#ff8a00"),
        )

    def _update_crossline_view(
        self,
        display: DisplayData,
        cmap: str,
        line_count: int,
        total_tw_ns: float,
        line_index: int,
        time_ns: float,
    ) -> None:
        half_width = max((line_count - 1) / 2.0, 0.5)
        cropped_crossline, visible_start_ns, visible_end_ns = self._crop_time_image(
            display.crossline,
            display.ascan_time_ns,
            float(display.meta.get("bscan_start_ns", 0.0)),
            float(display.meta.get("bscan_end_ns", total_tw_ns)),
        )
        crossline_view = self._smooth_image(cropped_crossline, axis=1)
        image = self._array_to_qimage(crossline_view, cmap, display.crossline_limits)
        self.width_view.set_content(
            image,
            data_x=(-half_width, half_width),
            data_y=(visible_start_ns, visible_end_ns),
            reset_view=not self._has_custom_viewport,
            show_axes=self.app_controller.display_state.show_axes,
            initial_viewport_y=(visible_start_ns, visible_end_ns),
            viewport_limit_y=(visible_start_ns, visible_end_ns),
            vertical_line=(float(line_index) - half_width, "#195fbf"),
            horizontal_line=(time_ns, "#195fbf"),
        )

    def _update_cscan_view(
        self,
        display: DisplayData,
        cmap: str,
        trace_count: int,
        line_count: int,
        trace_index: int,
        line_index: int,
    ) -> None:
        cscan_view = self._smooth_image(display.cscan, axis=0)
        image = self._array_to_qimage(cscan_view, cmap, display.cscan_limits)
        initial_trace_window = self._default_trace_viewport(trace_count, selection_index=trace_index)
        self.cscan_view.set_content(
            image,
            data_x=(0.0, float(max(trace_count - 1, 1))),
            data_y=(0.0, float(max(line_count - 1, 1))),
            reset_view=not self._has_custom_viewport,
            show_axes=self.app_controller.display_state.show_axes,
            initial_viewport_x=initial_trace_window,
            vertical_line=(trace_index, "#195fbf"),
            horizontal_line=(line_index, "#195fbf"),
        )

    def _update_ascan_view(self, display: DisplayData, sample_index: int, time_ns: float) -> None:
        visible_time_ns, visible_values = self._visible_ascan_segment(display)
        if display.ascan_values.size and display.ascan_time_ns.size:
            marker_idx = int(np.clip(sample_index, 0, display.ascan_values.size - 1))
            marker_x = display.ascan_values[marker_idx]
            marker_y = float(display.ascan_time_ns[marker_idx])
        else:
            marker_x = 0.0
            marker_y = time_ns
        start_ns = float(visible_time_ns[0]) if visible_time_ns.size else 0.0
        end_ns = float(visible_time_ns[-1]) if visible_time_ns.size else max(start_ns, 1e-6)
        self.trace_view.set_content(
            visible_values,
            visible_time_ns,
            data_y=(start_ns, end_ns),
            reset_view=not self._has_custom_viewport,
            show_axes=self.app_controller.display_state.show_axes,
            initial_viewport_y=(start_ns, end_ns),
            viewport_limit_y=(start_ns, end_ns),
            marker=(marker_x, marker_y),
            horizontal_line=(marker_y, "#195fbf"),
        )

    def _sync_slice_controls(self, display: DisplayData) -> None:
        trace_count = int(display.meta.get("trace_count", 0))
        line_count = int(display.meta.get("line_count", 0))
        selection = display.selection
        sample_min, sample_max = self._display_sample_bounds(display)
        controls = (
            (self.trace_slider, max(trace_count - 1, 0), selection.trace_index, self.trace_value_edit, f"{selection.trace_index + 1}"),
            (self.line_slider, max(line_count - 1, 0), selection.line_index, self.line_value_edit, f"{selection.line_index + 1}"),
            (
                self.sample_slider,
                sample_max,
                int(np.clip(selection.sample_index, sample_min, sample_max)),
                self.sample_value_edit,
                f"{float(display.ascan_time_ns[min(selection.sample_index, max(display.ascan_time_ns.size - 1, 0))]) if display.ascan_time_ns.size else 0.0:.3f} ns",
                sample_min,
            ),
        )
        for control in controls:
            if len(control) == 5:
                slider, maximum, value, edit, text = control
                minimum = 0
            else:
                slider, maximum, value, edit, text, minimum = control
            slider.blockSignals(True)
            slider.setRange(minimum, maximum)
            slider.setValue(int(np.clip(value, minimum, maximum)))
            slider.blockSignals(False)
            edit.setText(text)

    def _refresh_settings_info(self) -> None:
        stage_text = self.status_stage.text().replace("阶段: ", "")
        if not (self._pipeline_configured and self._display_configured):
            rows = [
                ("当前阶段", stage_text),
                ("当前时频转换", ""),
                ("B-scan 属性", ""),
                ("C-scan 属性", ""),
                ("起始时间(ns)", ""),
                ("终止时间(ns)", ""),
                ("对比增益", ""),
                ("C-scan 层厚", ""),
                ("色图", ""),
                ("反色显示", ""),
                ("显示坐标轴", ""),
            ]
            self._set_trace_info_html(self._settings_html(rows))
            return
        state = self.app_controller.display_state
        rows = [
            ("当前阶段", stage_text),
            ("当前时频转换", self._current_transform_name()),
            ("B-scan 属性", state.bscan_attr),
            ("C-scan 属性", state.cscan_attr),
            ("起始时间(ns)", f"{state.start_time_ns:.2f}"),
            ("终止时间(ns)", f"{state.end_time_ns:.2f}"),
            ("对比增益", f"{state.contrast_gain:.1f}"),
            ("C-scan 层厚", str(state.slice_thickness)),
            ("色图", state.colormap),
            ("反色显示", "是" if state.invert else "否"),
            ("显示坐标轴", "是" if state.show_axes else "否"),
        ]
        self._set_trace_info_html(self._settings_html(rows))

    @staticmethod
    def _settings_html(rows: list[tuple[str, str]]) -> str:
        cells = []
        for label, value in rows:
            safe_value = value or "&nbsp;"
            cells.append(
                "<tr>"
                f"<td style='padding:7px 10px; color:#5b6e84; font-weight:600; width:126px; border-bottom:1px solid #e6edf5;'>{label}</td>"
                f"<td style='padding:7px 10px; color:#1f2f42; border-bottom:1px solid #e6edf5;'>{safe_value}</td>"
                "</tr>"
            )
        return (
            "<html><body style='font-family:\"Microsoft YaHei UI\"; font-size:10pt; background:#fdfefe; color:#2a3744; margin:0;'>"
            "<table cellspacing='0' cellpadding='0' style='width:100%; border-collapse:collapse;'>"
            + "".join(cells)
            + "</table></body></html>"
        )

    def _current_transform_name(self) -> str:
        steps = self.app_controller.pipeline_state.draft_steps or self.app_controller.pipeline_state.applied_steps
        transform = next((step.name for step in steps if step.kind is StepKind.TRANSFORM), "CZT")
        if self.app_controller.pipeline_state.has_unapplied_changes:
            return f"{transform}（草稿未应用）"
        return transform

    def _show_waiting_plots(self) -> None:
        self.bscan_view.clear_content()
        self.width_view.clear_content()
        self.cscan_view.clear_content()
        self.trace_view.clear_content()
        for slider, edit in (
            (self.trace_slider, self.trace_value_edit),
            (self.line_slider, self.line_value_edit),
            (self.sample_slider, self.sample_value_edit),
        ):
            slider.blockSignals(True)
            slider.setRange(0, 0)
            slider.setValue(0)
            slider.blockSignals(False)
            edit.setText("0")
        self._has_custom_viewport = False
        self._refresh_overview_scene()

    def _set_trace_info_html(self, html: str) -> None:
        if html == self._settings_html_cache:
            return
        self._settings_html_cache = html
        self.trace_info.setHtml(html)

    @staticmethod
    def _array_to_qimage(
        data: np.ndarray,
        cmap_name: str,
        limits: tuple[float | None, float | None],
    ) -> QtGui.QImage:
        array = np.asarray(data, dtype=float)
        if array.ndim != 2 or array.size == 0:
            return QtGui.QImage()
        vmin = limits[0] if limits[0] is not None else float(np.nanmin(array))
        vmax = limits[1] if limits[1] is not None else float(np.nanmax(array))
        if not np.isfinite(vmin):
            vmin = 0.0
        if not np.isfinite(vmax) or vmax <= vmin:
            vmax = vmin + 1.0
        normalized = np.clip((array - vmin) / (vmax - vmin), 0.0, 1.0)
        rgba = cm.get_cmap(cmap_name)(normalized, bytes=True)
        rgba = np.ascontiguousarray(rgba)
        image = QtGui.QImage(
            rgba.data,
            rgba.shape[1],
            rgba.shape[0],
            rgba.strides[0],
            QtGui.QImage.Format_RGBA8888,
        )
        return image.copy()

    @staticmethod
    def _visible_ascan_segment(display: DisplayData) -> tuple[np.ndarray, np.ndarray]:
        time_axis = display.ascan_time_ns
        values = display.ascan_values
        if time_axis.size == 0 or values.size == 0:
            empty = np.empty((0,), dtype=float)
            return empty, empty
        start_ns = float(display.meta.get("bscan_start_ns", time_axis[0]))
        end_ns = float(display.meta.get("bscan_end_ns", time_axis[-1]))
        if end_ns <= start_ns:
            end_ns = start_ns + 1e-6
        start_idx = int(np.searchsorted(time_axis, start_ns, side="left"))
        end_idx = int(np.searchsorted(time_axis, end_ns, side="right"))
        start_idx = int(np.clip(start_idx, 0, time_axis.size - 1))
        end_idx = int(np.clip(end_idx, start_idx + 1, time_axis.size))
        return time_axis[start_idx:end_idx], values[start_idx:end_idx]

    @staticmethod
    def _display_sample_bounds(display: DisplayData) -> tuple[int, int]:
        time_axis = display.ascan_time_ns
        if time_axis.size == 0:
            return (0, 0)
        start_ns = float(display.meta.get("bscan_start_ns", time_axis[0]))
        end_ns = float(display.meta.get("bscan_end_ns", time_axis[-1]))
        start_idx = int(np.searchsorted(time_axis, start_ns, side="left"))
        end_idx = int(np.searchsorted(time_axis, end_ns, side="right")) - 1
        start_idx = int(np.clip(start_idx, 0, time_axis.size - 1))
        end_idx = int(np.clip(end_idx, start_idx, time_axis.size - 1))
        return (start_idx, end_idx)

    def _default_trace_viewport(self, trace_count: int, *, selection_index: int = 0) -> tuple[float, float]:
        max_trace = float(max(trace_count - 1, 1))
        if trace_count <= 1:
            return (0.0, max_trace)
        plot_width = max(self.bscan_view.width() - self.bscan_view.left_margin - self.bscan_view.right_margin, 640)
        # 3D Radar's docs describe Explore as page-oriented navigation (drag and page left/right),
        # so we initialize one "page" of traces rather than the whole line.
        target_visible = int(round(plot_width / 1.4))
        target_visible = int(np.clip(target_visible, 512, 1600))
        visible_count = min(trace_count, max(64, target_visible))
        if visible_count >= trace_count:
            return (0.0, max_trace)
        half = visible_count / 2.0
        center = float(np.clip(selection_index, half, max_trace - half))
        start = center - half
        end = start + visible_count
        if end > max_trace:
            end = max_trace
            start = max(0.0, end - visible_count)
        return (start, end)

    @staticmethod
    def _crop_time_image(
        data: np.ndarray,
        time_axis: np.ndarray,
        start_ns: float,
        end_ns: float,
    ) -> tuple[np.ndarray, float, float]:
        array = np.asarray(data)
        if array.ndim != 2 or array.shape[0] == 0:
            return array, float(start_ns), float(max(end_ns, start_ns + 1e-6))
        axis = np.asarray(time_axis, dtype=float)
        if axis.size != array.shape[0]:
            return array, float(start_ns), float(max(end_ns, start_ns + 1e-6))
        clipped_start = float(np.clip(start_ns, axis[0], axis[-1]))
        clipped_end = float(np.clip(end_ns, clipped_start + 1e-9, max(axis[-1], clipped_start + 1e-9)))
        start_idx = int(np.searchsorted(axis, clipped_start, side="left"))
        end_idx = int(np.searchsorted(axis, clipped_end, side="right"))
        start_idx = int(np.clip(start_idx, 0, max(axis.size - 1, 0)))
        end_idx = int(np.clip(end_idx, start_idx + 1, axis.size))
        visible = array[start_idx:end_idx, :]
        visible_start = float(axis[start_idx])
        visible_end = float(axis[end_idx - 1]) if end_idx - 1 < axis.size else float(axis[-1])
        if visible_end <= visible_start:
            visible_end = visible_start + 1e-6
        return visible, visible_start, visible_end

    @staticmethod
    def _smooth_image(data: np.ndarray, axis: int, *, min_size: int = 160) -> np.ndarray:
        if data.ndim != 2:
            return data
        source_len = data.shape[axis]
        if source_len <= 1:
            return data
        target_len = min(max(min_size, source_len * 12), 512)
        if target_len <= source_len:
            return data
        src = np.moveaxis(np.asarray(data, dtype=float), axis, -1)
        flat = src.reshape(-1, source_len)
        old_x = np.arange(source_len, dtype=float)
        new_x = np.linspace(0.0, source_len - 1.0, target_len, dtype=float)
        interpolated = np.vstack([np.interp(new_x, old_x, row) for row in flat])
        if target_len >= 5:
            kernel = np.array([1.0, 4.0, 6.0, 4.0, 1.0], dtype=float)
            kernel /= kernel.sum()
            interpolated = np.vstack([np.convolve(row, kernel, mode="same") for row in interpolated])
        smoothed = interpolated.reshape(*src.shape[:-1], target_len)
        return np.moveaxis(smoothed, -1, axis)

    @staticmethod
    def _set_line_axes_limits(ax, x_data: np.ndarray, y_data: np.ndarray) -> None:
        if x_data.size == 0 or y_data.size == 0:
            return
        x_min = float(np.nanmin(x_data))
        x_max = float(np.nanmax(x_data))
        if not np.isfinite(x_min) or not np.isfinite(x_max):
            return
        if x_min == x_max:
            x_max = x_min + 1.0
        y_min = float(np.nanmin(y_data))
        y_max = float(np.nanmax(y_data))
        if not np.isfinite(y_min) or not np.isfinite(y_max):
            return
        if y_min == y_max:
            pad = max(abs(y_min) * 0.05, 1.0)
        else:
            pad = (y_max - y_min) * 0.05
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min - pad, y_max + pad)

    def _apply_axes_visibility(self, ax, show_axes: bool, x_label: str, y_label: str) -> None:
        if show_axes:
            ax.set_xlabel(x_label, fontproperties=TITLE_FONT)
            ax.set_ylabel(y_label, fontproperties=TITLE_FONT)
            ax.tick_params(labelbottom=True, labelleft=True, colors="#5c6f85", labelsize=9)
            for spine in ax.spines.values():
                spine.set_color("#aebccc")
                spine.set_linewidth(0.9)
            return
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_xticks([])
        ax.set_yticks([])

    def _kind_text(self, kind: StepKind) -> str:
        if kind is StepKind.FREQUENCY:
            return "频域处理"
        if kind is StepKind.TRANSFORM:
            return "时频转换"
        return "时域处理"

    def _selection_text(self, display: DisplayData) -> str:
        selection = display.selection
        if display.ascan_values.size == 0:
            return "选点: ---"
        dt_ns = float(display.meta.get("dt_ns", 1.0))
        selected_time_ns = selection.sample_index * dt_ns
        if display.ascan_time_ns.size:
            local_idx = int(np.argmin(np.abs(display.ascan_time_ns - selected_time_ns)))
            time_ns = float(display.ascan_time_ns[local_idx])
            amplitude = float(display.ascan_values[local_idx])
        else:
            local_idx = 0
            time_ns = selected_time_ns
            amplitude = 0.0
        return (
            f"选点: 测线 {selection.line_index + 1} | 道 {selection.trace_index + 1} | "
            f"采样点 {selection.sample_index + 1} | 时间 {time_ns:.2f} ns | 幅值 {amplitude:.4f}"
        )

    def _on_trace_slider_changed(self, value: int) -> None:
        if self.display_data is None or not self._has_processed_result():
            return
        self.app_controller.select_trace(int(value))

    def _on_line_slider_changed(self, value: int) -> None:
        if self.display_data is None or not self._has_processed_result():
            return
        self.app_controller.select_line(int(value))

    def _on_sample_slider_changed(self, value: int) -> None:
        if self.display_data is None or not self._has_processed_result():
            return
        self.app_controller.select_sample(int(value))

    def _on_view_changed(
        self,
        view_key: object,
        xlim: tuple[float, float],
        ylim: tuple[float, float],
    ) -> None:
        if self.display_data is None or not self._has_processed_result():
            return
        key = str(view_key)
        self._has_custom_viewport = True
        if key == "bscan":
            self.cscan_view.set_viewport(xlim=xlim)
            self.width_view.set_viewport(ylim=ylim)
            self.trace_view.set_viewport(ylim=ylim)
            return
        if key == "cscan":
            self.bscan_view.set_viewport(xlim=xlim)
            return
        if key == "width":
            self.bscan_view.set_viewport(ylim=ylim)
            self.trace_view.set_viewport(ylim=ylim)
            return
        if key == "trace":
            self.bscan_view.set_viewport(ylim=ylim)
            self.width_view.set_viewport(ylim=ylim)

    def _on_view_selected(self, view_key: object, data_x: float, data_y: float) -> None:
        if self.display_data is None or not self._has_processed_result():
            return
        key = str(view_key)
        if key == "bscan":
            self.app_controller.select_from_bscan(int(round(data_x)), float(data_y))
            return
        if key == "width":
            line_count = int(self.display_data.meta.get("line_count", 1))
            half_width = max((line_count - 1) / 2.0, 0.5)
            line_index = int(round(np.clip(data_x + half_width, 0, max(line_count - 1, 0))))
            if self.display_data.ascan_time_ns.size:
                sample_index = int(np.argmin(np.abs(self.display_data.ascan_time_ns - float(data_y))))
            else:
                sample_index = 0
            self.app_controller.select_line(line_index)
            self.app_controller.select_sample(sample_index)
            return
        if key == "cscan":
            self.app_controller.select_trace(int(round(data_x)))
            self.app_controller.select_line(int(round(data_y)))
            return
        if key == "trace":
            if self.display_data.ascan_time_ns.size == 0:
                return
            sample_index = int(np.argmin(np.abs(self.display_data.ascan_time_ns - float(data_y))))
            self.app_controller.select_sample(sample_index)

    def _open_help_dir(self) -> None:
        target = self.app_controller.project_state.root_path or str(Path.cwd())
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))

    def _apply_soft_shadow(
        self,
        widget: QtWidgets.QWidget,
        *,
        blur_radius: int = 24,
        y_offset: int = 5,
        alpha: int = 28,
    ) -> None:
        effect = QtWidgets.QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur_radius)
        effect.setOffset(0, y_offset)
        effect.setColor(QtGui.QColor(24, 35, 46, alpha))
        widget.setGraphicsEffect(effect)

    def _on_busy_changed(self, busy: bool, message: str) -> None:
        self._is_busy = busy
        self._update_top_actions()
        if busy:
            self._progress_mode = "import" if self.app_controller.dataset_state.import_in_progress else "process"
            self.status_busy.setText("状态: 处理中" if self._progress_mode == "process" else "状态: 导入中")
            if self._progress_dialog is None:
                self._progress_dialog = ClosableProgressDialog(self)
                self._progress_dialog.setObjectName("progressDialog")
                self._progress_dialog.setCancelButton(None)
                self._progress_dialog.setRange(0, 100)
                self._progress_dialog.setAutoClose(False)
                self._progress_dialog.setAutoReset(False)
                self._progress_dialog.setMinimumDuration(0)
                self._progress_dialog.setWindowModality(QtCore.Qt.ApplicationModal)
                self._progress_dialog.setMinimumWidth(460)
                self._progress_dialog.close_requested.connect(self._cancel_current_task_from_progress)
                self._apply_soft_shadow(self._progress_dialog, blur_radius=28, y_offset=8, alpha=30)
            if self._progress_mode == "process":
                self._progress_dialog.setWindowTitle("处理流程执行中")
                self._progress_dialog.setLabelText("正在执行处理流程... 0%")
            else:
                self._progress_dialog.setWindowTitle("数据导入中")
                self._progress_dialog.setLabelText("正在导入数据... 0%")
            self._progress_dialog.setValue(0)
            self._progress_dialog.show()
            return
        self.status_busy.setText("状态: 就绪")
        if self._progress_dialog is not None:
            if isinstance(self._progress_dialog, ClosableProgressDialog):
                self._progress_dialog.emit_on_close = False
            self._progress_dialog.setValue(100)
            self._progress_dialog.close()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None
        self._progress_mode = ""
        self._progress_cancel_requested = False

    def _on_progress_changed(self, percent: int, message: str) -> None:
        value = max(0, min(100, int(percent)))
        if self._progress_mode == "process":
            label_text = f"{message or '正在执行处理流程...'} ({value}%)"
            status_text = f"状态: 处理中 {value}%"
        else:
            label_text = f"正在导入数据... {value}%"
            status_text = f"状态: 导入中 {value}%"
        if self._progress_dialog is not None:
            self._progress_dialog.setLabelText(label_text)
            self._progress_dialog.setValue(value)
        self.status_busy.setText(status_text)

    def _cancel_current_task_from_progress(self) -> None:
        if self._progress_cancel_requested:
            return
        self._progress_cancel_requested = True
        if self._progress_dialog is not None:
            if isinstance(self._progress_dialog, ClosableProgressDialog):
                self._progress_dialog.emit_on_close = False
            self._progress_dialog.hide()
            self._progress_dialog.deleteLater()
            self._progress_dialog = None
        cancelled = self.app_controller.cancel_current_task()
        if cancelled:
            self.status_busy.setText("状态: 正在取消")
            self.statusBar().showMessage("正在取消当前任务...", 3000)
        else:
            self._progress_cancel_requested = False

    def _on_processing_finished(self) -> None:
        self._set_stage_status("处理完成")
        self._refresh_settings_info()
        self.statusBar().showMessage("处理完成，结果已刷新到 A/B/C-scan。", 4000)
        self._update_top_actions()

    def _set_stage_status(self, text: str) -> None:
        self.status_stage.setText(f"阶段: {text}")

    def _show_error(self, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, "错误", message)

    def _has_processed_result(self) -> bool:
        snapshot = self.app_controller.result_state.active_snapshot
        return snapshot is not None and snapshot.pipeline_index > 0


def launch_main_window(app_controller: GPRApplication) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow(app_controller)
    window.showMaximized()
    app.exec()

