from __future__ import annotations

from gpr_lab_pro.domain.models.dataset import DatasetRecord
from gpr_lab_pro.infrastructure.workers import WorkerCancelled


def build_time_meta(
    dataset: DatasetRecord | None,
    sample_count: int,
    fallback_sample_count: int,
    *,
    fallback_to_dataset: bool = False,
) -> dict[str, float]:
    if dataset is None:
        return {"tw_ns": 0.0, "dt_ns": 1.0}
    if fallback_to_dataset:
        tw_ns = float(dataset.tw_ns)
        dt_ns = float(dataset.dt_ns)
        return {"tw_ns": tw_ns, "dt_ns": dt_ns}

    start_freq = float(dataset.header.get("start_frequency_hz", 0.0))
    end_freq = float(dataset.header.get("end_frequency_hz", 0.0))
    bandwidth = end_freq - start_freq
    source_count = max(int(fallback_sample_count), 1)
    target_count = max(int(sample_count), 1)
    if source_count > 1 and bandwidth > 0:
        df = bandwidth / (source_count - 1)
        tw_ns = (1.0 / df) * 1e9
        dt_ns = tw_ns / max(target_count - 1, 1)
        return {"tw_ns": tw_ns, "dt_ns": dt_ns}

    tw_ns = float(dataset.transformed_time_window_ns())
    dt_ns = float(dataset.transformed_dt_ns())
    return {"tw_ns": tw_ns, "dt_ns": dt_ns}


def check_cancelled(cancel_callback) -> None:
    if cancel_callback is not None and cancel_callback():
        raise WorkerCancelled()
