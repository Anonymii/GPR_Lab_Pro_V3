from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Callable


def load_plugin_from_file(path: str | Path, function_name: str | None = None) -> Callable:
    path = Path(path)
    module_name = f"gpr_plugin_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load plugin module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return resolve_plugin(module, function_name or path.stem)


def resolve_plugin(module: ModuleType, function_name: str) -> Callable:
    if not hasattr(module, function_name):
        raise AttributeError(f"Plugin function {function_name} was not found in {module.__name__}")
    plugin = getattr(module, function_name)
    if not callable(plugin):
        raise TypeError(f"{function_name} is not callable.")
    return plugin
