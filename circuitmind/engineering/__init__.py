"""Deterministic, unit-aware engineering calculations. No result implies validation."""
from .electrical import (
    capacitor_energy,
    capacitor_network,
    inductor_energy,
    ohms_law,
    resistor_network,
    rc_time_constant,
)
from .resistors import led_series_resistor

__all__ = [
    "capacitor_energy", "capacitor_network", "inductor_energy", "led_series_resistor",
    "ohms_law", "rc_time_constant", "resistor_network",
]
