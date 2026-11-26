"""Deterministic bounded synthesis for a single-pole RC low-pass candidate.

This is a deliberately narrow first vertical slice: typed requirements are turned
into a versioned circuit graph, checked analytically, then exercised by the
native C++ backward-Euler solver through C ABI v1. It does not select manufacturer
parts or infer requirements from free text.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from ..circuit_schema import validate_model
from .graph_mna import map_circuit_graph_to_mna
from .quantities import read_quantity
from .cpp_engine import simulate_transient_rc as simulate_cpp_transient_rc

_E_SERIES: dict[str, tuple[float, ...]] = {
    "E6": (1.0, 1.5, 2.2, 3.3, 4.7, 6.8),
    "E12": (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2),
    "E24": (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
           3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1),
}
_MIN_RESISTANCE_OHM = 0.1
_MAX_RESISTANCE_OHM = 100_000_000.0
_MIN_CAPACITANCE_F = 1e-12
_MAX_CAPACITANCE_F = 0.1


def _percent(value: Any, label: str, *, allow_zero: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite percentage")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite percentage") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite percentage")
    if number < 0 or (not allow_zero and number == 0) or number > 50:
        raise ValueError(f"{label} must be {'greater than zero and ' if not allow_zero else ''}at most 50 percent")
    return number


def _series_values(series: str, low: float, high: float) -> list[float]:
    values = []
    for exponent in range(-12, 9):
        decade = 10.0 ** exponent
        for base in _E_SERIES[series]:
            value = base * decade
            if low <= value <= high and math.isfinite(value):
                values.append(value)
    return values


def _nearest_standard(target: float, values: list[float]) -> float:
    if not values:
        raise ValueError("No supported E-series values are available in the required range")
    # Relative/log distance treats rounding upward and downward symmetrically.
    return min(values, key=lambda value: (abs(math.log(value / target)), value))


def _quantity(value: float, unit: str) -> dict[str, Any]:
    return {"value": value, "unit": unit}


def synthesize_rc_lowpass(inputs: dict[str, Any]) -> dict[str, Any]:
    """Synthesize, validate, and transient-check a bounded ideal RC low-pass.

    All requirement fields are explicit. The output contains a canonical schema-v2
    graph candidate plus its graph-bound simulation evidence; it never reads the
    project inventory, descriptions, or an external component catalogue.
    """
    required = {"cutoff_frequency", "preferred_resistance", "series",
                "resistor_tolerance_percent", "capacitor_tolerance_percent",
                "max_cutoff_error_percent"}
    if not isinstance(inputs, dict) or set(inputs) != required:
        raise ValueError("inputs must contain exactly cutoff_frequency, preferred_resistance, series, resistor_tolerance_percent, capacitor_tolerance_percent, and max_cutoff_error_percent")
    cutoff = read_quantity(inputs["cutoff_frequency"], "frequency", "target cutoff frequency")["si_value"]
    preferred_r = read_quantity(inputs["preferred_resistance"], "resistance", "preferred resistance")["si_value"]
    if cutoff < 1e-3 or cutoff > 1e9:
        raise ValueError("target cutoff frequency must be between 1 mHz and 1 GHz")
    if preferred_r < _MIN_RESISTANCE_OHM or preferred_r > _MAX_RESISTANCE_OHM:
        raise ValueError("preferred resistance must be between 0.1 ohm and 100 Mohm")
    series = inputs["series"]
    if not isinstance(series, str) or series not in _E_SERIES:
        raise ValueError("series must be one of E6, E12, or E24")
    r_tol = _percent(inputs["resistor_tolerance_percent"], "resistor_tolerance_percent") / 100
    c_tol = _percent(inputs["capacitor_tolerance_percent"], "capacitor_tolerance_percent") / 100
    max_error = _percent(inputs["max_cutoff_error_percent"], "max_cutoff_error_percent", allow_zero=False)

    resistance_candidates = _series_values(series, _MIN_RESISTANCE_OHM, _MAX_RESISTANCE_OHM)
    capacitance_candidates = _series_values(series, _MIN_CAPACITANCE_F, _MAX_CAPACITANCE_F)
    # For each allowed R, the log-nearest E-series C dominates every other C at
    # that R for both cutoff error and feasibility. This reduces the exhaustive
    # R×C search to a deterministic scan over every supported R candidate.
    candidates = []
    for candidate_r in resistance_candidates:
        ideal_c = 1.0 / (2.0 * math.pi * cutoff * candidate_r)
        candidate_c = _nearest_standard(ideal_c, capacitance_candidates)
        candidate_fc = 1.0 / (2.0 * math.pi * candidate_r * candidate_c)
        signed_error = 100.0 * (candidate_fc - cutoff) / cutoff
        log_cutoff_error = abs(math.log(candidate_fc / cutoff))
        preferred_r_distance = abs(math.log(candidate_r / preferred_r))
        candidates.append((candidate_r, candidate_c, candidate_fc, signed_error,
                           log_cutoff_error, preferred_r_distance))
    feasible_candidates = [candidate for candidate in candidates if abs(candidate[3]) <= max_error]
    if feasible_candidates:
        # Treat the requested cutoff bound as hard, then honor preferred R as the
        # soft objective; use cutoff accuracy and values for stable tie-breaking.
        selected = min(feasible_candidates, key=lambda item: (item[5], item[4], item[0], item[1]))
        optimization_status = "preferred-resistance-optimized-within-cutoff-bound"
    else:
        # No pair meets the user cutoff limit: return the globally closest available
        # cutoff candidate as an explicit failure, rather than claiming a pass.
        selected = min(candidates, key=lambda item: (item[4], item[5], item[0], item[1]))
        optimization_status = "best-cutoff-effort-no-feasible-pair"
    resistance, capacitance, achieved, relative_error, cutoff_log_error, preferred_r_distance = selected
    if abs(relative_error) > 50.0:
        raise ValueError("No supported E-series resistance/capacitance pair is within the maximum 50 percent cutoff-error range")
    ideal_capacitance = 1.0 / (2.0 * math.pi * cutoff * resistance)
    tau = resistance * capacitance
    cutoff_min = 1.0 / (2.0 * math.pi * resistance * (1 + r_tol) * capacitance * (1 + c_tol))
    cutoff_max = 1.0 / (2.0 * math.pi * resistance * (1 - r_tol) * capacitance * (1 - c_tol))
    constraint_met = abs(relative_error) <= max_error
    if not all(math.isfinite(item) and item > 0 for item in (achieved, tau, cutoff_min, cutoff_max)):
        raise ValueError("Selected values produce results outside the finite numerical range")

    pins = {
        "V1": [{"number": "1", "name": "VIN", "type": "passive"}, {"number": "2", "name": "GND", "type": "passive"}],
        "R1": [{"number": "1", "name": "A", "type": "passive"}, {"number": "2", "name": "B", "type": "passive"}],
        "C1": [{"number": "1", "name": "A", "type": "passive"}, {"number": "2", "name": "B", "type": "passive"}],
    }
    graph = {
        "schema_version": 2,
        "metadata": {"units": "mm", "grid_mm": 1.27},
        "status": "connectivity-draft",
        "components": [
            {"reference": "V1", "value": "unit-step simulation stimulus (not selected hardware)", "symbol": None, "footprint": None, "datasheet": None, "pins": pins["V1"], "position_mm": {"x": 10.0, "y": 20.0}, "rotation_deg": 0,
             "electrical_model": {"model_version": 1, "kind": "voltage_source", "parameters": {"waveform": {"type": "step", "initial": _quantity(0.0, "V"), "final": _quantity(1.0, "V"), "step_time": _quantity(0.0, "s")}, "source_role": "testbench"}, "provenance": "Fixed ideal unit-step test stimulus for the generated RC analysis; not a hardware selection."}},
            {"reference": "R1", "value": f"{resistance:.8g} ohm nominal ({series})", "symbol": None, "footprint": None, "datasheet": None, "pins": pins["R1"], "position_mm": {"x": 30.0, "y": 20.0}, "rotation_deg": 0,
             "electrical_model": {"model_version": 1, "kind": "resistor", "parameters": {"resistance": _quantity(resistance, "ohm"), "tolerance_percent": 100 * r_tol}, "provenance": f"Nominal value rounded to {series} from explicit preferred resistance; generic candidate, no part selected."}},
            {"reference": "C1", "value": f"{capacitance:.8g} F nominal ({series})", "symbol": None, "footprint": None, "datasheet": None, "pins": pins["C1"], "position_mm": {"x": 50.0, "y": 30.0}, "rotation_deg": 0,
             "electrical_model": {"model_version": 1, "kind": "capacitor", "parameters": {"capacitance": _quantity(capacitance, "F"), "tolerance_percent": 100 * c_tol, "initial_voltage": _quantity(0.0, "V")}, "provenance": f"Nominal value rounded to {series} to approach requested cutoff; generic candidate, no part selected."}},
        ],
        "nets": [
            {"name": "VIN", "terminals": [{"reference": "V1", "pin": "1"}, {"reference": "R1", "pin": "1"}]},
            {"name": "VOUT", "terminals": [{"reference": "R1", "pin": "2"}, {"reference": "C1", "pin": "1"}]},
            {"name": "GND", "terminals": [{"reference": "V1", "pin": "2"}, {"reference": "C1", "pin": "2"}]},
        ],
        "inventory": [],
        "limitations": [
            "Generated by a deterministic single-pole RC template from typed requirements; no free-text requirement parsing was performed.",
            "V1 is an ideal unit-step test stimulus, not a selected or recommended physical source component.",
            "Generic R1/C1 candidates are nominal E-series values only; no manufacturer, stock, rating, footprint, or datasheet has been verified.",
            "The ideal-source/no-load model omits source impedance, load impedance, parasitics, temperature behavior, and nonlinear effects.",
            "A generated graph candidate is not a KiCad schematic, ERC result, or design approval.",
        ],
    }
    graph_validation = validate_model(graph)
    if not graph_validation["topology_valid"]:
        raise ValueError("Generated candidate graph failed schema validation: " + "; ".join(graph_validation["errors"]))

    # The typed model emitted on each graph component is the input to this workflow's
    # test mapping. Generic user-authored saved graphs still require explicit mapping.
    mapping_bindings = []
    for component in graph["components"]:
        electrical = component["electrical_model"]
        parameters = electrical["parameters"]
        binding = {"reference": component["reference"], "type": electrical["kind"],
                   "pins": [pin["number"] for pin in component["pins"]]}
        if electrical["kind"] in {"voltage_source", "current_source"}:
            binding["value"] = parameters["waveform"]
        elif electrical["kind"] == "resistor":
            binding["value"] = parameters["resistance"]
        else:
            binding["value"] = parameters["capacitance"]
            binding["initial_voltage"] = parameters["initial_voltage"]
        mapping_bindings.append(binding)
    mapping = {"ground_net": "GND", "bindings": mapping_bindings, "exclusions": []}
    mapped = map_circuit_graph_to_mna(graph, mapping, allow_capacitors=True)
    dt = tau / 20.0
    stop = 5.0 * tau
    transient = simulate_cpp_transient_rc({"ground": mapped["netlist"]["ground"], "time_step": _quantity(dt, "s"),
                                           "stop_time": _quantity(stop, "s"), "method": "backward_euler",
                                           "elements": mapped["netlist"]["elements"]})
    expected_final = 1.0 - math.exp(-5.0)
    output_solver_node = mapped["net_name_to_solver_node"]["VOUT"]
    simulated_final = transient["node_voltage_traces_v"][output_solver_node][-1]
    graph_json = json.dumps(graph, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    graph_hash = hashlib.sha256(graph_json.encode("utf-8")).hexdigest()
    findings = []
    findings.append("Nominal cutoff is within the stated maximum error." if constraint_met else "Nominal cutoff exceeds the stated maximum error; revise the target, preferred resistance, series, or allowed error before using this candidate.")
    findings.append("Tolerance cutoff bounds assume independent, symmetric resistor and capacitor tolerances; they are not guaranteed unless the selected parts' specifications support them.")
    findings.append("No manufacturer part, footprint, stock availability, source/load impedance, voltage rating, power rating, or datasheet has been checked.")
    return {
        "workflow": "single-pole-rc-low-pass-synthesis",
        "workflow_version": 2,
        "design_status": "candidate-meets-nominal-cutoff-constraint" if constraint_met else "candidate-exceeds-nominal-cutoff-constraint",
        "requirements": {"target_cutoff_frequency": _quantity(cutoff, "Hz"), "preferred_resistance": _quantity(preferred_r, "ohm"),
                         "series": series, "resistor_tolerance_percent": 100 * r_tol,
                         "capacitor_tolerance_percent": 100 * c_tol, "max_cutoff_error_percent": max_error},
        "architecture": {"description": "One-pole passive RC low-pass: ideal input source -> series resistor -> output node; capacitor from output to ground.",
                         "transfer_function": "H(s) = 1 / (1 + s R C)",
                         "assumptions": ["ideal zero-impedance source", "no load at VOUT", "lumped linear R and C", "nominal values for nominal response"]},
        "optimization": {"algorithm": "bounded exhaustive E-series search over every supported resistance and its dominating nearest-log-distance capacitance",
                         "objective_order": ["meet the user maximum cutoff-error bound when possible", "minimize logarithmic distance from preferred resistance", "minimize logarithmic cutoff error", "stable numeric tie-break"],
                         "status": optimization_status, "resistance_candidate_count": len(resistance_candidates),
                         "evaluated_resistance_candidates": len(candidates),
                         "cutoff_feasible_resistance_count": len(feasible_candidates),
                         "selected_log_cutoff_error": cutoff_log_error,
                         "selected_log_preferred_resistance_distance": preferred_r_distance},
        "selected_values": {"resistance": _quantity(resistance, "ohm"), "capacitance": _quantity(capacitance, "F"),
                            "ideal_capacitance_before_series_rounding": _quantity(ideal_capacitance, "F"),
                            "time_constant": _quantity(tau, "s"), "nominal_cutoff_frequency": _quantity(achieved, "Hz"),
                            "nominal_cutoff_error_percent": relative_error,
                            "cutoff_tolerance_bounds": {"minimum": _quantity(cutoff_min, "Hz"), "maximum": _quantity(cutoff_max, "Hz")},
                            "nominal_resistor_peak_step_power": _quantity(1.0 / resistance, "W")},
        "constraints": {"nominal_cutoff_error": "pass" if constraint_met else "fail", "maximum_error_percent": max_error,
                        "tolerance_bound_status": "not-assessed-against-requirement"},
        "findings": findings,
        "graph_validation": graph_validation,
        "circuit_model_sha256": graph_hash,
        "circuit_model_candidate": graph,
        "explicit_testbench_mapping": mapping,
        "transient_test": {"engine": transient["engine"], "engine_version": transient["engine_version"],
                            "method": transient["method"], "status": transient["status"],
                            "time_step": transient["time_step"], "stop_time": transient["stop_time"],
                            "sample_count": transient["sample_count"], "simulated_final_output_voltage": _quantity(simulated_final, "V"),
                            "analytic_final_output_voltage": _quantity(expected_final, "V"),
                            "absolute_final_value_error": _quantity(abs(simulated_final - expected_final), "V"),
                            "full_numerical_result": transient},
        "scope": "A deterministic, bounded single-pole RC synthesis and numerical test workflow. It is not general natural-language circuit synthesis, a manufacturer BOM, a KiCad schematic, ngspice, physical validation, or design approval.",
    }
