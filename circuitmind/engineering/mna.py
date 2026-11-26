"""Compatibility entry point for native linear DC modified-nodal analysis.

The numerical solve is performed exclusively by the C++ core through its versioned
C ABI. Python validates/normalizes typed inputs and formats the evidence report; it
does not assemble or solve the electrical matrix.
"""
from __future__ import annotations

from typing import Any

from .cpp_engine import solve_dc_operating_point as _solve_dc_operating_point_cpp


def solve_dc_operating_point(netlist: dict[str, Any]) -> dict[str, Any]:
    """Solve a bounded explicit ideal R/V/I DC netlist in the native C++ engine."""
    return _solve_dc_operating_point_cpp(netlist)
