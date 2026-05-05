from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_QT_MESSAGE_HANDLER_INSTALLED = False


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def runtime_log_dir() -> Path:
    candidates = [
        _runtime_root() / "logs",
        Path(os.environ.get("TEMP", ".")) / "GPR_Lab_Pro_V4" / "logs",
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            continue
    return Path(".")


def runtime_log_path() -> Path:
    candidates = [
        _runtime_root() / "logs" / "gpr_runtime.log",
        Path(os.environ.get("TEMP", ".")) / "GPR_Lab_Pro_V4" / "logs" / "gpr_runtime.log",
        Path(".") / "gpr_runtime.log",
    ]
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.touch(exist_ok=True)
            return candidate
        except Exception:
            continue
    return Path("gpr_runtime.log")


def _install_qt_message_handler() -> None:
    global _QT_MESSAGE_HANDLER_INSTALLED
    if _QT_MESSAGE_HANDLER_INSTALLED:
        return
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return

    qt_logger = logging.getLogger("qt")

    def _handler(message_type, context, message) -> None:
        category = getattr(context, "category", "") or "qt"
        file_name = getattr(context, "file", "") or ""
        line_no = getattr(context, "line", 0) or 0
        location = f"{file_name}:{line_no}" if file_name else ""
        payload = f"[{category}] {message}"
        if location:
            payload = f"{payload} ({location})"
        if message_type == QtMsgType.QtDebugMsg:
            qt_logger.info(payload)
        elif message_type == QtMsgType.QtInfoMsg:
            qt_logger.info(payload)
        elif message_type == QtMsgType.QtWarningMsg:
            qt_logger.warning(payload)
        elif message_type == QtMsgType.QtCriticalMsg:
            qt_logger.error(payload)
        else:
            qt_logger.critical(payload)

    qInstallMessageHandler(_handler)
    _QT_MESSAGE_HANDLER_INSTALLED = True


def configure_logging() -> None:
    log_path = runtime_log_path()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    has_stream = any(isinstance(handler, logging.StreamHandler) for handler in root_logger.handlers)
    if not has_stream:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    file_handler_exists = any(
        isinstance(handler, logging.FileHandler) and Path(getattr(handler, "baseFilename", "")) == log_path
        for handler in root_logger.handlers
    )
    if not file_handler_exists:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger("gpr.runtime").info("Runtime logging initialized at %s", log_path)
    _install_qt_message_handler()
