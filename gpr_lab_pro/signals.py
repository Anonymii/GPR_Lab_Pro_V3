from __future__ import annotations

from PySide6 import QtCore


class AppSignals(QtCore.QObject):
    project_changed = QtCore.Signal(object)
    overview_changed = QtCore.Signal(object)
    dataset_loaded = QtCore.Signal(object)
    display_ready = QtCore.Signal(object)
    display_cleared = QtCore.Signal()
    pipeline_changed = QtCore.Signal(object)
    pipeline_applied = QtCore.Signal(object)
    processing_finished = QtCore.Signal()
    progress_changed = QtCore.Signal(int, str)
    status_message = QtCore.Signal(str)
    busy_changed = QtCore.Signal(bool, str)
    error = QtCore.Signal(str)
