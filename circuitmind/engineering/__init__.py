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
from .mna import solve_dc_operating_point
from .resistors import led_series_resistor
from .timebase import clock_timebase_error

__all__ = [
    "capacitor_energy", "capacitor_network", "inductor_energy", "led_series_resistor",
    "clock_divider_candidate", "clock_timebase_error", "ohms_law", "rc_time_constant", "resistor_network",
    "solve_dc_operating_point",
]
