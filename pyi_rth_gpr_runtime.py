from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path


def _log_candidates() -> list[Path]:
    runtime_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    temp_root = Path(os.environ.get("TEMP", ".")) / "GPR_Lab_Pro_V4"
    return [
        runtime_root / "logs" / "pyi_runtime_hook.log",
        temp_root / "logs" / "pyi_runtime_hook.log",
        Path.cwd() / "pyi_runtime_hook.log",
    ]


def _append(message: str) -> None:
    payload = f"{message}\n"
    for path in _log_candidates():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            return
        except Exception:
            continue


def _install_exception_hooks() -> None:
    def _excepthook(exc_type, exc_value, exc_tb):
        _append("runtime-hook: unhandled exception")
        _append("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _threading_excepthook(args):
        _append(f"runtime-hook: thread exception name={args.thread.name}")
        _append("".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
        threading.__excepthook__(args)

    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook


_append(
    "runtime-hook: entered "
    f"frozen={getattr(sys, 'frozen', False)} "
    f"executable={sys.executable} "
    f"cwd={Path.cwd()} "
    f"temp={os.environ.get('TEMP', '')}"
)
_install_exception_hooks()
_append("runtime-hook: exception hooks installed")
