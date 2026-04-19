from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import uuid

import numpy as np

from gpr_lab_pro.domain.enums import DataDomain


@dataclass
class ResultSnapshot:
    data: np.ndarray
    domain: DataDomain
    step_name: str
    params: tuple[float, ...] = ()
    meta: dict[str, object] = field(default_factory=dict)
    pipeline_index: int = 0
    parent_snapshot_id: str | None = None
    is_cached: bool = True
    render_ready: bool = False
    snapshot_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@dataclass
class ResultState:
    history: list[ResultSnapshot] = field(default_factory=list)
    active_snapshot: ResultSnapshot | None = None
