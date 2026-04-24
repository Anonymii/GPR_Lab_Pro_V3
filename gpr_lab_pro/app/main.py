from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _write_startup_error(message: str) -> Path | None:
    candidates = [
        _runtime_root(),
        Path(os.environ.get("TEMP", ".")) / "GPR_Lab_Pro_V4",
    ]
    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            log_path = base / "startup_error.log"
            log_path.write_text(message, encoding="utf-8")
            return log_path
        except Exception:
            continue
    return None


def _configure_qt_runtime() -> None:
    if getattr(sys, "frozen", False):
        return
    try:
        import PySide6
    except Exception:
        return

    pyside_root = Path(PySide6.__file__).resolve().parent
    plugins_dir = pyside_root / "plugins"
    platforms_dir = plugins_dir / "platforms"
    if plugins_dir.exists():
        os.environ["QT_PLUGIN_PATH"] = str(plugins_dir)
    if platforms_dir.exists():
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platforms_dir)
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")


def _show_startup_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "GPR Lab Pro V4 启动失败", 0x10)
    except Exception:
        pass


def main() -> None:
    try:
        if "MPLCONFIGDIR" not in os.environ:
            mpl_cache_dir = Path(os.environ.get("TEMP", ".")) / "GPR_Lab_Pro_V4" / "mplconfig"
            mpl_cache_dir.mkdir(parents=True, exist_ok=True)
            os.environ["MPLCONFIGDIR"] = str(mpl_cache_dir)
        _configure_qt_runtime()

        from gpr_lab_pro.application import GPRApplication
        from gpr_lab_pro.infrastructure.logging import configure_logging
        from gpr_lab_pro.ui.main_window_v12 import launch_main_window

        configure_logging()
        launch_main_window(GPRApplication())
    except Exception:
        detail = traceback.format_exc()
        log_path = _write_startup_error(detail)
        message = (
            "软件启动失败。\n\n"
            "请先完整解压整个发布文件夹，再运行其中的 EXE。\n"
            "如果仍然失败，请把 startup_error.log 发回开发人员。"
        )
        if log_path is not None:
            message += f"\n\n错误日志位置:\n{log_path}"
        _show_startup_error(message)
        raise


if __name__ == "__main__":
    main()
