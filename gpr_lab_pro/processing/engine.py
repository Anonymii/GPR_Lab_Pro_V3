from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_legacy_processor():
    legacy_path = Path(__file__).with_name("pipeline.py")
    spec = importlib.util.spec_from_file_location(
        "gpr_lab_pro.processing._legacy_pipeline",
        legacy_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed to load legacy pipeline engine from {legacy_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PipelineProcessor


PipelineProcessor = _load_legacy_processor()

__all__ = ["PipelineProcessor"]
