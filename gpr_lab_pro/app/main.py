from __future__ import annotations

import os
import sys
import traceback
import logging
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


def _append_startup_trace(message: str) -> None:
    candidates = [
        _runtime_root() / "logs" / "startup_trace.log",
        Path(os.environ.get("TEMP", ".")) / "GPR_Lab_Pro_V4" / "logs" / "startup_trace.log",
        Path(".") / "startup_trace.log",
    ]
    for trace_path in candidates:
        try:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            with trace_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{message}\n")
            return
        except Exception:
            continue


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


def _configure_local_network_access() -> None:
    localhost_no_proxy = "127.0.0.1,localhost"
    for key in ("NO_PROXY", "no_proxy"):
        existing = os.environ.get(key, "").strip()
        if not existing:
            os.environ[key] = localhost_no_proxy
        elif "127.0.0.1" not in existing and "localhost" not in existing:
            os.environ[key] = f"{existing},{localhost_no_proxy}"

    try:
        from PySide6.QtNetwork import QNetworkProxy, QNetworkProxyFactory
    except Exception:
        return

    QNetworkProxyFactory.setUseSystemConfiguration(False)
    QNetworkProxy.setApplicationProxy(QNetworkProxy(QNetworkProxy.NoProxy))


def _show_startup_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "GPR Lab Pro V4 启动失败", 0x10)
    except Exception:
        pass


def main() -> None:
    try:
        _append_startup_trace("main: entered")
        if "MPLCONFIGDIR" not in os.environ:
            mpl_cache_dir = Path(os.environ.get("TEMP", ".")) / "GPR_Lab_Pro_V4" / "mplconfig"
            mpl_cache_dir.mkdir(parents=True, exist_ok=True)
            os.environ["MPLCONFIGDIR"] = str(mpl_cache_dir)
        _append_startup_trace(f"main: mplconfigdir={os.environ.get('MPLCONFIGDIR', '')}")
        _configure_qt_runtime()
        _append_startup_trace("main: qt runtime configured")
        _configure_local_network_access()
        _append_startup_trace(f"main: local network access configured no_proxy={os.environ.get('NO_PROXY', '')}")

        from gpr_lab_pro.infrastructure.logging import configure_logging, runtime_log_dir, runtime_log_path

        _append_startup_trace("main: infrastructure.logging imported")
        configure_logging()
        _append_startup_trace(f"main: logging configured path={runtime_log_path()}")
        logging.getLogger("gpr.runtime").info(
            "Application startup: runtime_root=%s log_dir=%s no_proxy=%s",
            _runtime_root(),
            runtime_log_dir(),
            os.environ.get("NO_PROXY", ""),
        )
        logging.getLogger("gpr.runtime").info("Runtime log file: %s", runtime_log_path())

        from gpr_lab_pro.application import GPRApplication
        from gpr_lab_pro.ui.main_window_v12 import launch_main_window

        _append_startup_trace("main: core application modules imported")
        logging.getLogger("gpr.runtime").info("Core application modules imported successfully")
        _append_startup_trace("main: launching main window")
        launch_main_window(GPRApplication())
    except Exception:
        detail = traceback.format_exc()
        _append_startup_trace(f"main: exception\n{detail}")
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
