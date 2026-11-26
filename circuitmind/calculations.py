"""Small deterministic engineering calculations with explicit input validation."""
from __future__ import annotations

import math


def led_current_limit_resistor(supply_v: float, led_forward_v: float, target_current_a: float) -> dict[str, float | str]:
    if supply_v <= led_forward_v or target_current_a <= 0:
        raise ValueError("Supply must exceed LED forward voltage and current must be positive")
    resistance = (supply_v - led_forward_v) / target_current_a
    return {
        "name": "LED current-limit resistor",
        "formula": "R = (V_supply - V_forward) / I_LED",
        "substitution": f"({supply_v:g} V - {led_forward_v:g} V) / {target_current_a:g} A",
        "resistance_ohm": resistance,
        "dissipation_w": (supply_v - led_forward_v) * target_current_a,
        "assumptions": "Steady-state ideal LED drop; verify driver output-current limits and worst-case LED Vf before use.",
    }


def ne555_astable_frequency(resistor_a_ohm: float, resistor_b_ohm: float, capacitor_f: float) -> dict[str, float | str]:
    if min(resistor_a_ohm, resistor_b_ohm, capacitor_f) <= 0:
        raise ValueError("Astable component values must be positive")
    frequency = 1.44 / ((resistor_a_ohm + 2 * resistor_b_ohm) * capacitor_f)
    return {
        "name": "NE555 astable frequency estimate",
        "formula": "f ≈ 1.44 / ((RA + 2 RB) C)",
        "frequency_hz": frequency,
        "period_s": 1 / frequency,
        "assumptions": "Ideal astable approximation; real tolerance, supply, temperature, and timing-capacitor leakage affect accuracy.",
    }


def voltage_divider(v_in: float, r_top_ohm: float, r_bottom_ohm: float) -> dict[str, float | str]:
    if v_in < 0 or min(r_top_ohm, r_bottom_ohm) <= 0:
        raise ValueError("Input voltage must be nonnegative and resistances positive")
    v_out = v_in * r_bottom_ohm / (r_top_ohm + r_bottom_ohm)
    return {"name": "Unloaded voltage divider", "formula": "Vout = Vin × Rbottom / (Rtop + Rbottom)", "output_v": v_out,
            "current_a": v_in / (r_top_ohm + r_bottom_ohm), "assumptions": "No load connected; account for load impedance and resistor tolerance in a real design."}
