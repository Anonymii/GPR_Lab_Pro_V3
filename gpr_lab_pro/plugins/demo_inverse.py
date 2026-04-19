from __future__ import annotations

import numpy as np


def demo_inverse(in_data: np.ndarray, params, mode: str):
    if mode == "info":
        return {
            "name": "信号反转 (Demo)",
            "params": ["强度系数"],
            "defaults": [1.0],
        }
    scale = float(params[0]) if params else 1.0
    return -np.asarray(in_data) * scale
