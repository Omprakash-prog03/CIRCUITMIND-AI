"""Compatibility helpers for the original intake examples.

New user-driven calculations should use :mod:`circuitmind.engineering`, whose
result envelope includes units, formula substitutions, assumptions and validity.
"""
from __future__ import annotations

import math
from typing import Any

from .engineering.quantities import quantity, require_positive


def led_current_limit_resistor(supply_v: float, led_forward_v: float, target_current_a: float) -> dict[str, Any]:
    supply = quantity(supply_v, "V", "voltage", "supply voltage")
    forward = quantity(led_forward_v, "V", "voltage", "LED forward voltage")
    current = quantity(target_current_a, "A", "current", "target LED current")
    require_positive(supply, allow_zero=True)
    require_positive(forward, allow_zero=True)
    require_positive(current)
    if supply["si_value"] <= forward["si_value"]:
        raise ValueError("Supply must exceed LED forward voltage and current must be positive")
    resistance = (supply_v - led_forward_v) / target_current_a
    dissipation = (supply_v - led_forward_v) * target_current_a
    if not math.isfinite(resistance) or not math.isfinite(dissipation):
        raise ValueError("Calculated LED resistor result is outside the finite numeric range")
    return {
        "name": "LED current-limit resistor (illustrative nominal estimate)",
        "formula": "R = (V_supply - V_forward) / I_LED",
        "substitution": f"({supply_v:g} V - {led_forward_v:g} V) / {target_current_a:g} A",
        "resistance_ohm": resistance,
        "dissipation_w": dissipation,
        "assumptions": "Steady-state ideal LED drop; verify driver output-current limits and worst-case LED Vf before use.",
    }


def ne555_astable_frequency(resistor_a_ohm: float, resistor_b_ohm: float, capacitor_f: float) -> dict[str, Any]:
    ra = quantity(resistor_a_ohm, "ohm", "resistance", "RA")
    rb = quantity(resistor_b_ohm, "ohm", "resistance", "RB")
    capacitance = quantity(capacitor_f, "F", "capacitance", "timing capacitance")
    for value in (ra, rb, capacitance):
        require_positive(value)
    try:
        denominator = (resistor_a_ohm + 2 * resistor_b_ohm) * capacitor_f
        frequency = 1.44 / denominator
        period = 1 / frequency
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("Calculated 555 frequency is outside the finite numeric range") from exc
    if not math.isfinite(frequency) or frequency <= 0 or not math.isfinite(period):
        raise ValueError("Calculated 555 frequency is outside the finite numeric range")
    return {
        "name": "NE555 astable frequency estimate (illustrative)",
        "formula": "f ≈ 1.44 / ((RA + 2 RB) C)",
        "frequency_hz": frequency,
        "period_s": period,
        "assumptions": "Ideal astable approximation; real tolerance, supply, temperature, and timing-capacitor leakage affect accuracy.",
    }


def voltage_divider(v_in: float, r_top_ohm: float, r_bottom_ohm: float) -> dict[str, Any]:
    voltage = quantity(v_in, "V", "voltage", "input voltage")
    top = quantity(r_top_ohm, "ohm", "resistance", "top resistance")
    bottom = quantity(r_bottom_ohm, "ohm", "resistance", "bottom resistance")
    require_positive(voltage, allow_zero=True)
    require_positive(top)
    require_positive(bottom)
    total = r_top_ohm + r_bottom_ohm
    if not math.isfinite(total):
        raise ValueError("Calculated divider result is outside the finite numeric range")
    v_out = v_in * r_bottom_ohm / total
    current = v_in / total
    if not all(math.isfinite(value) for value in (v_out, current)):
        raise ValueError("Calculated divider result is outside the finite numeric range")
    return {"name": "Unloaded voltage divider", "formula": "Vout = Vin × Rbottom / (Rtop + Rbottom)", "output_v": v_out,
            "current_a": current, "assumptions": "No load connected; account for load impedance and resistor tolerance in a real design."}
