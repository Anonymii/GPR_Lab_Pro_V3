from __future__ import annotations

import numpy as np

from gpr_lab_pro.domain.enums import StepKind
from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.domain.models.display import DisplayState, SelectionState
from gpr_lab_pro.domain.models.pipeline import PipelineStep
from gpr_lab_pro.processing.runtime import PipelineRuntime
from gpr_lab_pro.render.adapters.volume_adapter import build_display_data


def _dummy_dataset() -> DatasetRecord:
    volume = np.random.default_rng(0).normal(size=(128, 64, 8)).astype(np.float32)
    return DatasetRecord(
        dataset_id="demo",
        name="demo",
        filename="demo.dat",
        volume=volume,
        tw_ns=60.0,
        dt_ns=60.0 / 127.0,
        fs_hz=1.0 / ((60.0 / 127.0) * 1e-9),
        attribute="Real",
        transform_name="CZT",
        header={},
    )


def test_runtime_builds_snapshots() -> None:
    dataset = _dummy_dataset()
    runtime = PipelineRuntime()
    steps = [
        PipelineStep.from_sequence("dewow", "Dewow", "时域处理", StepKind.TIME, [20]),
        PipelineStep.from_sequence("filt_mean2", "二维均值滤波", "时域处理", StepKind.TIME, [3]),
    ]
    snapshots = runtime.execute(dataset, steps)
    assert len(snapshots) == 3
    assert snapshots[-1].data.shape == dataset.volume.shape


def test_display_adapter_builds_three_views() -> None:
    dataset = _dummy_dataset()
    runtime = PipelineRuntime()
    snapshot = runtime.create_initial_snapshot(dataset)
    display = build_display_data(snapshot, dataset, DisplayState(), SelectionState())
    assert display.bscan.shape == (128, 64)
    assert display.cscan.shape == (8, 64)
    assert display.ascan_time_ns.shape == (128,)
    assert display.spectrum_freq_mhz.ndim == 1
