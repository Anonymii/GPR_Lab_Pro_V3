from __future__ import annotations

from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.domain.models.display import DisplayData, DisplayState, SelectionState
from gpr_lab_pro.domain.models.results import ResultSnapshot
from gpr_lab_pro.render.adapters.ascan_adapter_v11 import build_ascan
from gpr_lab_pro.render.adapters.bscan_adapter import build_bscan
from gpr_lab_pro.render.adapters.cscan_adapter import build_cscan


def build_display_data(
    snapshot: ResultSnapshot,
    dataset: DatasetRecord,
    display_state: DisplayState,
    selection_state: SelectionState,
) -> DisplayData:
    bscan, bscan_limits = build_bscan(snapshot.data, display_state, selection_state)
    cscan, cscan_limits = build_cscan(snapshot.data, display_state, selection_state)
    ascan_time_ns, ascan_values, spectrum_freq_mhz, spectrum_values, trace_info = build_ascan(
        dataset,
        snapshot.data,
        selection_state,
    )
    return DisplayData(
        bscan=bscan,
        cscan=cscan,
        ascan_time_ns=ascan_time_ns,
        ascan_values=ascan_values,
        spectrum_freq_mhz=spectrum_freq_mhz,
        spectrum_values=spectrum_values,
        bscan_limits=bscan_limits,
        cscan_limits=cscan_limits,
        selection=selection_state,
        trace_info=trace_info,
        meta={
            "tw_ns": dataset.tw_ns,
            "dt_ns": dataset.dt_ns,
            "line_count": dataset.line_count,
            "trace_count": dataset.trace_count,
            "sample_count": dataset.sample_count,
            "import_transform": dataset.transform_name,
            "import_attribute": dataset.attribute,
            "runtime_domain": snapshot.domain.value,
            "bridge_runtime": "IFFT",
            "bridge_import_stage": "CZT/ISDFT",
        },
    )
