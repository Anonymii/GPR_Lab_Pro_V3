from __future__ import annotations

import numpy as np

from gpr_lab_pro.app.context import ApplicationContext
from gpr_lab_pro.domain.enums import DataDomain
from gpr_lab_pro.domain.models.display import DisplayData
from gpr_lab_pro.render.adapters.ascan_adapter_v11 import build_ascan
from gpr_lab_pro.render.adapters.bscan_adapter import build_bscan
from gpr_lab_pro.render.adapters.cscan_adapter import build_cscan


class DisplayController:
    def __init__(self, context: ApplicationContext) -> None:
        self.context = context
        self._bscan_cache_key = None
        self._bscan_cache_value = None
        self._crossline_cache_key = None
        self._crossline_cache_value = None
        self._cscan_cache_key = None
        self._cscan_cache_value = None

    @property
    def display_state(self):
        return self.context.display_state

    @property
    def selection_state(self):
        return self.context.selection_state

    def publish_display(self) -> None:
        dataset = self.context.dataset_state.current_dataset
        snapshot = self.context.result_state.active_snapshot
        if dataset is None or snapshot is None:
            return
        display_state = self.context.display_state
        selection_state = self.context.selection_state
        time_meta = self._resolve_time_meta(dataset, snapshot)
        dt_ns = float(time_meta["dt_ns"])
        tw_ns = float(time_meta["tw_ns"])

        bscan_key = (
            snapshot.snapshot_id,
            display_state.bscan_attr,
            display_state.contrast_gain,
            display_state.start_time_ns,
            display_state.end_time_ns,
            selection_state.line_index,
            dt_ns,
        )
        if bscan_key == self._bscan_cache_key and self._bscan_cache_value is not None:
            bscan, bscan_limits, bscan_range = self._bscan_cache_value
        else:
            bscan, bscan_limits, bscan_range = build_bscan(
                snapshot.data,
                dt_ns,
                display_state,
                selection_state,
            )
            self._bscan_cache_key = bscan_key
            self._bscan_cache_value = (bscan, bscan_limits, bscan_range)

        crossline_key = (
            snapshot.snapshot_id,
            display_state.bscan_attr,
            display_state.contrast_gain,
            selection_state.trace_index,
        )
        if crossline_key == self._crossline_cache_key and self._crossline_cache_value is not None:
            crossline, crossline_limits = self._crossline_cache_value
        else:
            crossline, crossline_limits = self._build_crossline(snapshot.data, display_state, selection_state)
            self._crossline_cache_key = crossline_key
            self._crossline_cache_value = (crossline, crossline_limits)

        cscan_key = (
            snapshot.snapshot_id,
            display_state.cscan_attr,
            display_state.contrast_gain,
            display_state.slice_thickness,
            selection_state.sample_index,
        )
        if cscan_key == self._cscan_cache_key and self._cscan_cache_value is not None:
            cscan, cscan_limits = self._cscan_cache_value
        else:
            cscan, cscan_limits = build_cscan(snapshot.data, display_state, selection_state)
            self._cscan_cache_key = cscan_key
            self._cscan_cache_value = (cscan, cscan_limits)

        ascan_time_ns, ascan_values, spectrum_freq_mhz, spectrum_values, trace_info = build_ascan(
            dataset,
            snapshot.data,
            selection_state,
            dt_ns=dt_ns,
        )
        display = DisplayData(
            bscan=bscan,
            crossline=crossline,
            cscan=cscan,
            ascan_time_ns=ascan_time_ns,
            ascan_values=ascan_values,
            spectrum_freq_mhz=spectrum_freq_mhz,
            spectrum_values=spectrum_values,
            bscan_limits=bscan_limits,
            crossline_limits=crossline_limits,
            cscan_limits=cscan_limits,
            selection=selection_state,
            trace_info=trace_info,
            meta={
                "tw_ns": tw_ns,
                "dt_ns": dt_ns,
                "line_count": dataset.line_count,
                "trace_count": dataset.trace_count,
                "sample_count": int(snapshot.data.shape[0]) if snapshot.data.ndim >= 1 else dataset.sample_count,
                "bscan_start_ns": bscan_range[0],
                "bscan_end_ns": bscan_range[1],
                "crossline_center": float(selection_state.line_index),
                "import_transform": dataset.transform_name,
                "import_attribute": dataset.attribute,
                "runtime_domain": snapshot.domain.value,
            },
        )
        self.context.signals.display_ready.emit(display)

    def update_settings(
        self,
        *,
        contrast_gain: float | None = None,
        slice_thickness: int | None = None,
        bscan_attr: str | None = None,
        cscan_attr: str | None = None,
        start_time_ns: float | None = None,
        end_time_ns: float | None = None,
        colormap: str | None = None,
        invert: bool | None = None,
        show_axes: bool | None = None,
    ) -> None:
        if contrast_gain is not None:
            self.display_state.contrast_gain = float(max(0.1, contrast_gain))
        if slice_thickness is not None:
            self.display_state.slice_thickness = int(max(0, slice_thickness))
        if bscan_attr is not None:
            self.display_state.bscan_attr = bscan_attr
        if cscan_attr is not None:
            self.display_state.cscan_attr = cscan_attr
        if start_time_ns is not None:
            self.display_state.start_time_ns = float(max(0.0, start_time_ns))
        if end_time_ns is not None:
            self.display_state.end_time_ns = float(max(0.0, end_time_ns))
        if self.display_state.end_time_ns <= self.display_state.start_time_ns:
            self.display_state.end_time_ns = self.display_state.start_time_ns + 1.0
        if colormap is not None:
            self.display_state.colormap = colormap
        if invert is not None:
            self.display_state.invert = invert
        if show_axes is not None:
            self.display_state.show_axes = show_axes

    def restore_state(self, state) -> None:
        self.display_state.contrast_gain = float(max(0.1, state.contrast_gain))
        self.display_state.slice_thickness = int(max(0, state.slice_thickness))
        self.display_state.bscan_attr = state.bscan_attr
        self.display_state.cscan_attr = state.cscan_attr
        self.display_state.start_time_ns = float(max(0.0, state.start_time_ns))
        self.display_state.end_time_ns = float(max(self.display_state.start_time_ns + 1.0, state.end_time_ns))
        self.display_state.colormap = state.colormap
        self.display_state.invert = bool(state.invert)
        self.display_state.show_axes = bool(state.show_axes)

    def select_from_bscan(self, trace_index: int, time_ns: float) -> None:
        dataset = self.context.dataset_state.current_dataset
        snapshot = self.context.result_state.active_snapshot
        if dataset is None or snapshot is None:
            return
        self.selection_state.trace_index = int(np.clip(trace_index, 0, dataset.trace_count - 1))
        dt_ns = max(self._resolve_time_meta(dataset, snapshot)["dt_ns"], 1e-9)
        max_sample_index = int(snapshot.data.shape[0] - 1) if snapshot.data.ndim >= 1 else int(dataset.sample_count - 1)
        self.selection_state.sample_index = int(
            np.clip(round(time_ns / dt_ns), 0, max_sample_index)
        )

    def select_line(self, line_index: int) -> None:
        dataset = self.context.dataset_state.current_dataset
        if dataset is None:
            return
        self.selection_state.line_index = int(np.clip(line_index, 0, dataset.line_count - 1))

    def select_trace(self, trace_index: int) -> None:
        dataset = self.context.dataset_state.current_dataset
        if dataset is None:
            return
        self.selection_state.trace_index = int(np.clip(trace_index, 0, dataset.trace_count - 1))

    def select_sample(self, sample_index: int) -> None:
        dataset = self.context.dataset_state.current_dataset
        snapshot = self.context.result_state.active_snapshot
        if dataset is None or snapshot is None:
            return
        max_sample_index = int(snapshot.data.shape[0] - 1) if snapshot.data.ndim >= 1 else int(dataset.sample_count - 1)
        self.selection_state.sample_index = int(np.clip(sample_index, 0, max_sample_index))

    @staticmethod
    def _resolve_time_meta(dataset, snapshot) -> dict[str, float]:
        if snapshot.domain is DataDomain.TIME:
            dt_ns = float(snapshot.meta.get("dt_ns", 0.0) or 0.0)
            tw_ns = float(snapshot.meta.get("tw_ns", 0.0) or 0.0)
        else:
            dt_ns = 0.0
            tw_ns = 0.0
        if dt_ns <= 0:
            dt_ns = float(dataset.transformed_dt_ns())
        if tw_ns <= 0:
            tw_ns = float(dataset.transformed_time_window_ns())
        return {"dt_ns": dt_ns, "tw_ns": tw_ns}

    @staticmethod
    def _build_crossline(volume: np.ndarray, display_state, selection_state) -> tuple[np.ndarray, tuple[float | None, float | None]]:
        if volume.size == 0:
            return np.empty((0, 0), dtype=float), (None, None)
        trace_idx = int(np.clip(selection_state.trace_index, 0, volume.shape[1] - 1))
        crossline = volume[:, trace_idx, :]
        attr = display_state.bscan_attr
        if attr == "Envelope":
            crossline = np.abs(crossline)
        elif attr == "Phase":
            crossline = np.angle(crossline)
        elif attr == "Inst Freq":
            unwrapped = np.unwrap(np.angle(crossline), axis=0)
            crossline = np.vstack([np.diff(unwrapped, axis=0), np.zeros((1, crossline.shape[1]))])
        else:
            crossline = np.real(crossline)
        if attr in {"Envelope", "Abs"}:
            vmax = float(np.nanpercentile(np.abs(crossline), 99.0))
            return crossline, (0.0, vmax if vmax > 0 else None)
        vmax = float(np.nanstd(np.real(crossline)) * max(display_state.contrast_gain, 0.1))
        if not np.isfinite(vmax) or vmax <= 1e-9:
            vmax = 1.0
        return crossline, (-vmax, vmax)
