"""Compatibility entry point for native pointwise DC source sweeps."""
from __future__ import annotations

from typing import Any

from .cpp_engine import simulate_dc_sweep as _simulate_dc_sweep_cpp


def simulate_dc_sweep(request: dict[str, Any]) -> dict[str, Any]:
    """Sweep one independent source with every operating point solved by C++."""
    return _simulate_dc_sweep_cpp(request)
