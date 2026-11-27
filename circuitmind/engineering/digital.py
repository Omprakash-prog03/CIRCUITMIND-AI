"""Python request/result adapters for native digital simulation."""
from __future__ import annotations

from typing import Any

from .cpp_engine import (
    simulate_digital_truth_table as _simulate_digital_truth_table,
    simulate_digital_waveform as _simulate_digital_waveform,
)


def simulate_digital_truth_table(netlist: dict[str, Any]) -> dict[str, Any]:
    """Evaluate an explicit combinational gate network using the C++ core."""
    return _simulate_digital_truth_table(netlist)


def simulate_digital_waveform(netlist: dict[str, Any]) -> dict[str, Any]:
    """Sample an explicit Boolean netlist with rising-edge D flip-flops in the C++ core."""
    return _simulate_digital_waveform(netlist)
