"""Strict conversion of common engineering quantities to SI units."""
from __future__ import annotations

import math
from typing import Any

# Unit aliases intentionally stay explicit; ambiguous prefixes/case are not guessed.
_UNITS: dict[str, tuple[str, float, str]] = {
    "V": ("voltage", 1.0, "V"), "mV": ("voltage", 1e-3, "V"), "uV": ("voltage", 1e-6, "V"), "µV": ("voltage", 1e-6, "V"),
    "A": ("current", 1.0, "A"), "mA": ("current", 1e-3, "A"), "uA": ("current", 1e-6, "A"), "µA": ("current", 1e-6, "A"), "nA": ("current", 1e-9, "A"),
    "ohm": ("resistance", 1.0, "ohm"), "Ohm": ("resistance", 1.0, "ohm"), "Ω": ("resistance", 1.0, "ohm"),
    "kohm": ("resistance", 1e3, "ohm"), "kΩ": ("resistance", 1e3, "ohm"), "Mohm": ("resistance", 1e6, "ohm"), "MΩ": ("resistance", 1e6, "ohm"),
    "F": ("capacitance", 1.0, "F"), "mF": ("capacitance", 1e-3, "F"), "uF": ("capacitance", 1e-6, "F"), "µF": ("capacitance", 1e-6, "F"), "nF": ("capacitance", 1e-9, "F"), "pF": ("capacitance", 1e-12, "F"),
    "H": ("inductance", 1.0, "H"), "mH": ("inductance", 1e-3, "H"), "uH": ("inductance", 1e-6, "H"), "µH": ("inductance", 1e-6, "H"),
    "W": ("power", 1.0, "W"), "mW": ("power", 1e-3, "W"), "uW": ("power", 1e-6, "W"), "µW": ("power", 1e-6, "W"),
    "s": ("time", 1.0, "s"), "ms": ("time", 1e-3, "s"), "us": ("time", 1e-6, "s"), "µs": ("time", 1e-6, "s"), "ns": ("time", 1e-9, "s"),
    "Hz": ("frequency", 1.0, "Hz"), "kHz": ("frequency", 1e3, "Hz"), "MHz": ("frequency", 1e6, "Hz"),
    "K": ("temperature", 1.0, "K"),
}


def quantity(value: Any, unit: Any, dimension: str, name: str) -> dict[str, Any]:
    """Validate a user quantity and return its original and normalized SI values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} value must be a finite number")
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} value is outside the finite numeric range") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} value must be a finite number")
    if not isinstance(unit, str) or unit not in _UNITS:
        raise ValueError(f"{name} has unsupported unit {unit!r}")
    actual_dimension, scale, si_unit = _UNITS[unit]
    if actual_dimension != dimension:
        raise ValueError(f"{name} requires a {dimension} unit, not {unit}")
    si_value = numeric * scale
    if not math.isfinite(si_value):
        raise ValueError(f"{name} overflows when converted to SI units")
    return {"name": name, "value": numeric, "unit": unit, "si_value": si_value, "si_unit": si_unit}


def read_quantity(value: Any, dimension: str, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object with numeric value and unit")
    return quantity(value.get("value"), value.get("unit"), dimension, name)


def require_positive(item: dict[str, Any], name: str | None = None, allow_zero: bool = False) -> float:
    label = name or item["name"]
    value = item["si_value"]
    if value < 0 or (value == 0 and not allow_zero):
        comparator = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{label} must be {comparator}")
    return value


def make_quantity(value: float, unit: str) -> dict[str, Any]:
    if not math.isfinite(value):
        raise ValueError("Calculated result is not finite")
    return {"value": value, "unit": unit}
