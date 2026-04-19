from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence, Tuple


PluginCallable = Callable[..., object]


@dataclass(frozen=True)
class PipelineOperation:
    """A single processing step in the GPR pipeline."""

    type: str
    params: Tuple[float, ...] = ()
    name: Optional[str] = None

    @classmethod
    def from_sequence(
        cls,
        op_type: str,
        params: Sequence[float] | None = None,
        name: Optional[str] = None,
    ) -> "PipelineOperation":
        return cls(type=op_type, params=tuple(params or ()), name=name)


@dataclass
class GPRContext:
    """Execution context matching the MATLAB state needed by process_step."""

    dt: float
    fs: float
    clip_sigma: float = 4.0
    plugin_resolver: Optional[Callable[[str], PluginCallable]] = None
    metadata: dict = field(default_factory=dict)
