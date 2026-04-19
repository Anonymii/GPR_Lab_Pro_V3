from __future__ import annotations

from typing import Protocol, Sequence

import numpy as np


class PythonPluginProtocol(Protocol):
    def __call__(
        self, in_data: np.ndarray, params: Sequence[float], mode: str
    ) -> np.ndarray | dict:
        ...
