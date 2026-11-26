"""Foundational DC and passive-component calculations."""
from __future__ import annotations

import math
from typing import Any

from .quantities import make_quantity, read_quantity, require_positive
from .results import result


def ohms_law(voltage: dict[str, Any], resistance: dict[str, Any]) -> dict[str, Any]:
    v = read_quantity(voltage, "voltage", "voltage")
    r = read_quantity(resistance, "resistance", "resistance")
    require_positive(r)
    current = v["si_value"] / r["si_value"]
    power = v["si_value"] * current
    return result("Ohm's law", "I = V / R; P = V × I", [v, r],
                  {"current": make_quantity(current, "A"), "power": make_quantity(power, "W")},
                  substitution=f"I = {v['si_value']:g} V / {r['si_value']:g} ohm",
                  intermediates={"current_a": current},
                  assumptions=["Steady-state DC; resistance is treated as constant."],
                  validity=["Resistance must be positive; voltage polarity is signed and current follows the stated reference direction."])


def resistor_network(mode: str, values: list[dict[str, Any]]) -> dict[str, Any]:
    if mode not in {"series", "parallel"}:
        raise ValueError("Resistor network mode must be 'series' or 'parallel'")
    if not isinstance(values, list) or len(values) < 2:
        raise ValueError("A resistor network requires at least two resistor quantities")
    resistors = [read_quantity(item, "resistance", f"R{index + 1}") for index, item in enumerate(values)]
    for resistor in resistors:
        require_positive(resistor)
    try:
        if mode == "series":
            equivalent = math.fsum(item["si_value"] for item in resistors)
            formula = "R_eq = Σ R_i"
        else:
            equivalent = 1.0 / math.fsum(1.0 / item["si_value"] for item in resistors)
            formula = "R_eq = 1 / Σ(1 / R_i)"
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("Equivalent resistance is outside the finite numerical range") from exc
    if not math.isfinite(equivalent) or equivalent <= 0:
        raise ValueError("Equivalent resistance is outside the finite numerical range")
    return result(f"Resistors in {mode}", formula, resistors,
                  {"equivalent_resistance": make_quantity(equivalent, "ohm")},
                  substitution=f"{formula} using " + ", ".join(f"{item['si_value']:g} ohm" for item in resistors),
                  assumptions=["Ideal resistors; parasitic lead/contact resistance is ignored."],
                  validity=["All resistance values must be finite and positive."])


def capacitor_network(mode: str, values: list[dict[str, Any]]) -> dict[str, Any]:
    if mode not in {"series", "parallel"}:
        raise ValueError("Capacitor network mode must be 'series' or 'parallel'")
    if not isinstance(values, list) or len(values) < 2:
        raise ValueError("A capacitor network requires at least two capacitance quantities")
    capacitors = [read_quantity(item, "capacitance", f"C{index + 1}") for index, item in enumerate(values)]
    for capacitor in capacitors:
        require_positive(capacitor)
    try:
        if mode == "parallel":
            equivalent = math.fsum(item["si_value"] for item in capacitors)
            formula = "C_eq = Σ C_i"
        else:
            equivalent = 1.0 / math.fsum(1.0 / item["si_value"] for item in capacitors)
            formula = "C_eq = 1 / Σ(1 / C_i)"
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("Equivalent capacitance is outside the finite numerical range") from exc
    if not math.isfinite(equivalent) or equivalent <= 0:
        raise ValueError("Equivalent capacitance is outside the finite numerical range")
    return result(f"Capacitors in {mode}", formula, capacitors,
                  {"equivalent_capacitance": make_quantity(equivalent, "F")},
                  substitution=f"{formula} using " + ", ".join(f"{item['si_value']:g} F" for item in capacitors),
                  assumptions=["Ideal capacitors; ESR, ESL, leakage, voltage coefficient, and tolerance are ignored."],
                  validity=["All capacitance values must be finite and positive."])


def rc_time_constant(resistance: dict[str, Any], capacitance: dict[str, Any]) -> dict[str, Any]:
    r = read_quantity(resistance, "resistance", "resistance")
    c = read_quantity(capacitance, "capacitance", "capacitance")
    require_positive(r)
    require_positive(c)
    tau = r["si_value"] * c["si_value"]
    if not math.isfinite(tau):
        raise ValueError("RC time constant is outside the finite numerical range")
    return result("RC time constant", "τ = R × C", [r, c],
                  {"time_constant": make_quantity(tau, "s"), "one_percent_settling_approx": make_quantity(4.605170186 * tau, "s")},
                  substitution=f"τ = {r['si_value']:g} ohm × {c['si_value']:g} F",
                  intermediates={"tau_s": tau, "settling_time_factor": 4.605170186},
                  assumptions=["Single-pole ideal RC network; the 1% settling estimate assumes a first-order step response."],
                  validity=["Positive resistance and capacitance; the 1% settling estimate is approximately 4.605τ."])


def capacitor_energy(capacitance: dict[str, Any], voltage: dict[str, Any]) -> dict[str, Any]:
    c = read_quantity(capacitance, "capacitance", "capacitance")
    v = read_quantity(voltage, "voltage", "voltage")
    require_positive(c)
    try:
        energy = 0.5 * c["si_value"] * v["si_value"] ** 2
    except OverflowError as exc:
        raise ValueError("Capacitor energy is outside the finite numerical range") from exc
    if not math.isfinite(energy):
        raise ValueError("Capacitor energy is outside the finite numerical range")
    return result("Capacitor stored energy", "E = ½ C V²", [c, v],
                  {"energy": make_quantity(energy, "J")},
                  substitution=f"E = 0.5 × {c['si_value']:g} F × ({v['si_value']:g} V)²",
                  assumptions=["Ideal capacitor; leakage and ESR are ignored."],
                  validity=["Positive capacitance; voltage is squared, so either polarity gives the same stored energy."])


def inductor_energy(inductance: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    l = read_quantity(inductance, "inductance", "inductance")
    i = read_quantity(current, "current", "current")
    require_positive(l)
    try:
        energy = 0.5 * l["si_value"] * i["si_value"] ** 2
    except OverflowError as exc:
        raise ValueError("Inductor energy is outside the finite numerical range") from exc
    if not math.isfinite(energy):
        raise ValueError("Inductor energy is outside the finite numerical range")
    return result("Inductor stored energy", "E = ½ L I²", [l, i],
                  {"energy": make_quantity(energy, "J")},
                  substitution=f"E = 0.5 × {l['si_value']:g} H × ({i['si_value']:g} A)²",
                  assumptions=["Ideal linear inductor; saturation and core losses are ignored."],
                  validity=["Positive inductance; reference-current sign does not change stored energy."])
