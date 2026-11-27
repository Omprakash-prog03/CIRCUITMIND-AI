"""Deterministic, unit-aware engineering calculations. No result implies validation."""
from .electrical import (
    capacitor_energy,
    capacitor_network,
    inductor_energy,
    ohms_law,
    resistor_network,
    rc_time_constant,
)
from .dividers import clock_divider_candidate
from .ac import simulate_ac_sweep
from .digital import simulate_digital_truth_table, simulate_digital_waveform
from .mna import solve_dc_operating_point
from .dc_sweep import simulate_dc_sweep
from .graph_mna import map_circuit_graph_to_mna
from .graph_ac import map_circuit_graph_to_ac
from .transient import simulate_transient_rc
from .synthesis import synthesize_rc_lowpass
from .resistors import led_series_resistor
from .timebase import clock_timebase_error

__all__ = [
    "capacitor_energy", "capacitor_network", "inductor_energy", "led_series_resistor",
    "clock_divider_candidate", "clock_timebase_error", "ohms_law", "rc_time_constant", "resistor_network",
    "solve_dc_operating_point", "simulate_dc_sweep", "map_circuit_graph_to_mna", "map_circuit_graph_to_ac", "simulate_transient_rc", "simulate_ac_sweep", "simulate_digital_truth_table", "simulate_digital_waveform", "synthesize_rc_lowpass",
]
