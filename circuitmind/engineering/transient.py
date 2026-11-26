"""Bounded backward-Euler time-domain solver for linear RC networks."""
from __future__ import annotations

import math
from typing import Any

from .mna import solve_dc_operating_point
from .quantities import make_quantity, read_quantity, require_positive
from .results import result

_MAX_ELEMENTS = 100
_MAX_NODES = 24
_MAX_MNA_UNKNOWN_COUNT = 32
_MAX_STEPS = 2_000


def _source_value(element: dict[str, Any], time_s: float, field: str, dimension: str) -> float:
    if "waveform" not in element:
        return read_quantity(element[field], dimension, f"{element['reference']} {field}")["si_value"]
    waveform = element["waveform"]
    initial = read_quantity(waveform.get("initial"), dimension, f"{element['reference']} waveform initial")
    final = read_quantity(waveform.get("final"), dimension, f"{element['reference']} waveform final")
    step = read_quantity(waveform.get("step_time"), "time", f"{element['reference']} step time")
    snapped_to_step = math.isclose(time_s, step["si_value"], rel_tol=1e-12,
                                    abs_tol=1e-12 * max(1e-30, abs(time_s), abs(step["si_value"])))
    return final["si_value"] if time_s >= step["si_value"] or snapped_to_step else initial["si_value"]


def _allocate_reference(prefix: str, ordinal: int, used: set[str]) -> str:
    candidate_number = ordinal
    while True:
        candidate = f"{prefix}BE{candidate_number}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        candidate_number += 1


def simulate_transient_rc(netlist: dict[str, Any]) -> dict[str, Any]:
    """Run a piecewise-constant-source linear RC transient using backward Euler.

    Capacitor initial voltages are explicitly supplied. Voltage/current sources may
    be constant (`voltage` / `current`) or one ideal step (`waveform` with `initial`,
    `final`, and `step_time`). No device or circuit-graph values are inferred.
    """
    allowed_top_keys = {"ground", "time_step", "stop_time", "elements", "method"}
    if not isinstance(netlist, dict) or set(netlist) - allowed_top_keys or not {"ground", "time_step", "stop_time", "elements"} <= set(netlist):
        raise ValueError("Transient RC input needs ground, time_step, stop_time, and elements; optional method must be backward_euler")
    if netlist.get("method", "backward_euler") != "backward_euler":
        raise ValueError("Only the backward_euler transient method is implemented")
    ground = netlist["ground"]
    if not isinstance(ground, str) or not ground:
        raise ValueError("ground must be a non-empty node label")
    time_step = read_quantity(netlist["time_step"], "time", "transient time step")
    stop_time = read_quantity(netlist["stop_time"], "time", "transient stop time")
    require_positive(time_step)
    require_positive(stop_time)
    dt, stop = time_step["si_value"], stop_time["si_value"]
    step_ratio = stop / dt
    if not math.isfinite(step_ratio):
        raise ValueError("stop_time / time_step is outside the finite numeric range")
    steps = round(step_ratio)
    if steps < 1 or steps > _MAX_STEPS or not math.isclose(step_ratio, steps, rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError(f"stop_time must be a positive integer multiple of time_step, with at most {_MAX_STEPS} steps")
    elements = netlist["elements"]
    if not isinstance(elements, list) or not elements or len(elements) > _MAX_ELEMENTS:
        raise ValueError(f"elements must contain between 1 and {_MAX_ELEMENTS} RC/source elements")

    used_refs: set[str] = set()
    nodes: set[str] = set()
    parsed: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = [time_step, stop_time]
    voltage_source_count = 0
    capacitor_count = 0
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"elements[{index}] must be an object")
        kind = element.get("type")
        required = {"resistor": {"type", "reference", "nodes", "resistance"},
                    "capacitor": {"type", "reference", "nodes", "capacitance", "initial_voltage"},
                    "voltage_source": {"type", "reference", "nodes", "voltage"},
                    "current_source": {"type", "reference", "nodes", "current"}}
        if not isinstance(kind, str) or kind not in required:
            raise ValueError(f"elements[{index}] has unsupported type {kind!r}; only resistor, capacitor, and independent sources are supported")
        if kind in {"voltage_source", "current_source"} and "waveform" in element:
            if set(element) != {"type", "reference", "nodes", "waveform"}:
                raise ValueError(f"{kind} with waveform must contain exactly type, reference, nodes, and waveform")
            waveform = element["waveform"]
            if not isinstance(waveform, dict) or set(waveform) != {"type", "initial", "final", "step_time"} or waveform.get("type") != "step":
                raise ValueError(f"{element.get('reference')} waveform must be a single explicit step")
            dimension = "voltage" if kind == "voltage_source" else "current"
            initial = read_quantity(waveform["initial"], dimension, f"{element.get('reference')} waveform initial")
            final = read_quantity(waveform["final"], dimension, f"{element.get('reference')} waveform final")
            step_time = read_quantity(waveform["step_time"], "time", f"{element.get('reference')} step time")
            require_positive(step_time, allow_zero=True)
            if step_time["si_value"] > stop:
                raise ValueError(f"{element.get('reference')} step_time must not exceed stop_time")
            inputs.extend((initial, final, step_time))
            source_quantity = initial
            value_field = "voltage" if kind == "voltage_source" else "current"
        elif kind in {"voltage_source", "current_source"}:
            expected = {"type", "reference", "nodes", "voltage" if kind == "voltage_source" else "current"}
            if set(element) != expected:
                raise ValueError(f"{kind} must have exactly the fields {', '.join(sorted(expected))}")
            dimension = "voltage" if kind == "voltage_source" else "current"
            source_quantity = read_quantity(element[value_field := ("voltage" if kind == "voltage_source" else "current")], dimension,
                                            f"{element.get('reference')} {value_field}")
            inputs.append(source_quantity)
        else:
            if set(element) != required[kind]:
                raise ValueError(f"{kind} must have exactly the fields {', '.join(sorted(required[kind]))}")
            value_field = "resistance" if kind == "resistor" else "capacitance"
            dimension = "resistance" if kind == "resistor" else "capacitance"
            source_quantity = read_quantity(element[value_field], dimension, f"{element.get('reference')} {value_field}")
            inputs.append(source_quantity)
            if kind == "capacitor":
                initial_voltage = read_quantity(element["initial_voltage"], "voltage", f"{element.get('reference')} initial voltage")
                inputs.append(initial_voltage)
                capacitor_count += 1
        reference = element.get("reference")
        if not isinstance(reference, str) or not reference or reference in used_refs:
            raise ValueError(f"elements[{index}] needs a non-empty unique reference")
        used_refs.add(reference)
        terminals = element.get("nodes")
        if not isinstance(terminals, list) or len(terminals) != 2 or any(not isinstance(node, str) or not node for node in terminals):
            raise ValueError(f"{reference}.nodes must contain two non-empty node labels")
        if terminals[0] == terminals[1]:
            raise ValueError(f"{reference} cannot connect both terminals to the same node")
        nodes.update(terminals)
        if kind == "resistor":
            require_positive(source_quantity)
        elif kind == "capacitor":
            require_positive(source_quantity)
        else:
            voltage_source_count += kind == "voltage_source"
        parsed.append({"type": kind, "reference": reference, "nodes": tuple(terminals),
                       "value_si": source_quantity["si_value"],
                       "initial_voltage_si": initial_voltage["si_value"] if kind == "capacitor" else None,
                       "source_field": "voltage" if kind == "voltage_source" else "current" if kind == "current_source" else None,
                       })

    if ground not in nodes:
        raise ValueError("The declared ground node must appear in at least one element")
    node_count = len(nodes - {ground})
    if node_count > _MAX_NODES:
        raise ValueError(f"Transient network exceeds the {_MAX_NODES}-node limit")
    if node_count + voltage_source_count + capacitor_count > _MAX_MNA_UNKNOWN_COUNT:
        raise ValueError(f"Transient network exceeds the {_MAX_MNA_UNKNOWN_COUNT}-unknown solve limit")
    if capacitor_count == 0:
        raise ValueError("Transient RC solver requires at least one capacitor")

    def source_element(item: dict[str, Any], time_s: float, *, pre_step: bool = False) -> dict[str, Any]:
        field = item["source_field"]
        raw = item["raw"]
        if pre_step and "waveform" in raw:
            value = read_quantity(raw["waveform"]["initial"],
                                  "voltage" if field == "voltage" else "current",
                                  f"{item['reference']} waveform initial")["si_value"]
        else:
            value = _source_value(raw, time_s, field, "voltage" if field == "voltage" else "current")
        return {"type": item["type"], "reference": item["reference"], "nodes": list(item["nodes"]),
                field: make_quantity(value, "V" if field == "voltage" else "A")}

    for item, original in zip(parsed, elements):
        item["raw"] = original

    initial_elements = []
    temp_ordinal = 1
    for item in parsed:
        if item["type"] == "resistor":
            initial_elements.append({"type": "resistor", "reference": item["reference"], "nodes": list(item["nodes"]),
                                    "resistance": make_quantity(item["value_si"], "ohm")})
        elif item["type"] == "capacitor":
            ref = _allocate_reference("V", temp_ordinal, used_refs)
            temp_ordinal += 1
            initial_elements.append({"type": "voltage_source", "reference": ref, "nodes": list(item["nodes"]),
                                    "voltage": make_quantity(item["initial_voltage_si"], "V")})
        else:
            initial_elements.append(source_element(item, 0.0, pre_step=True))
    initial_state = solve_dc_operating_point({"ground": ground, "elements": initial_elements})["outputs"]["node_voltages"]

    times = [0.0]
    node_traces = {node: [voltage["value"]] for node, voltage in initial_state.items()}
    capacitor_traces: dict[str, list[float]] = {}
    previous_cap_voltages = {}
    for item in parsed:
        if item["type"] == "capacitor":
            first, second = item["nodes"]
            voltage = initial_state[first]["value"] - initial_state[second]["value"]
            if not math.isclose(voltage, item["initial_voltage_si"], rel_tol=1e-8, abs_tol=1e-10):
                raise ValueError(f"{item['reference']} initial voltage is inconsistent with the t=0 network solution")
            previous_cap_voltages[item["reference"]] = voltage
            capacitor_traces[item["reference"]] = [voltage]

    for sample in range(1, steps + 1):
        time_s = stop if sample == steps else sample * dt
        step_elements = []
        for item in parsed:
            first, second = item["nodes"]
            if item["type"] == "resistor":
                step_elements.append({"type": "resistor", "reference": item["reference"], "nodes": [first, second],
                                     "resistance": make_quantity(item["value_si"], "ohm")})
            elif item["type"] == "capacitor":
                capacitance = item["value_si"]
                equivalent_resistance = dt / capacitance
                equivalent_current = -(capacitance / dt) * previous_cap_voltages[item["reference"]]
                if not math.isfinite(equivalent_resistance) or equivalent_resistance <= 0 or not math.isfinite(equivalent_current):
                    raise ValueError(f"{item['reference']} backward-Euler companion is outside the finite numerical range")
                step_elements.append({"type": "resistor", "reference": _allocate_reference("R", temp_ordinal, used_refs),
                                     "nodes": [first, second], "resistance": make_quantity(equivalent_resistance, "ohm")})
                temp_ordinal += 1
                step_elements.append({"type": "current_source", "reference": _allocate_reference("I", temp_ordinal, used_refs),
                                     "nodes": [first, second], "current": make_quantity(equivalent_current, "A")})
                temp_ordinal += 1
            else:
                step_elements.append(source_element(item, time_s))
        solved = solve_dc_operating_point({"ground": ground, "elements": step_elements})["outputs"]["node_voltages"]
        times.append(time_s)
        for node in node_traces:
            node_traces[node].append(solved[node]["value"])
        for item in parsed:
            if item["type"] == "capacitor":
                first, second = item["nodes"]
                voltage = solved[first]["value"] - solved[second]["value"]
                capacitor_traces[item["reference"]].append(voltage)
                previous_cap_voltages[item["reference"]] = voltage

    warnings = ["Backward Euler is a first-order, numerically dissipative integration method; timestep adequacy and convergence were not assessed.",
                "This is a linear ideal RC model with explicit piecewise-constant sources; no nonlinear devices, parasitics, thermal behavior, or component tolerances are included."]
    outputs = {
        "method": "backward_euler",
        "time_points_s": times,
        "node_voltage_traces_v": node_traces,
        "capacitor_voltage_traces_v": capacitor_traces,
        "time_step": make_quantity(dt, "s"),
        "stop_time": make_quantity(stop, "s"),
        "sample_count": len(times),
        "status": "completed-numerical-linear-rc-transient",
    }
    return result(
        "Linear RC transient response (backward Euler)",
        "i_C = C·dv_C/dt; backward-Euler companion: G_C = C/dt with history source I_eq = -(C/dt)·v_C,prev",
        inputs, outputs,
        substitution=f"Backward Euler with Δt={dt:g} s for {steps} steps through t={stop:g} s; {capacitor_count} capacitor(s).",
        intermediates={"time_step_s": dt, "stop_time_s": stop, "step_count": steps,
                       "non_ground_node_count": node_count, "capacitor_count": capacitor_count},
        assumptions=["Capacitors use explicitly supplied initial voltage; source steps are ideal and piecewise constant.",
                     "Resistors and sources are ideal; node voltages are sampled at t=0 and each fixed timestep."],
        validity=["Only resistors, capacitors, independent DC sources, and single ideal step waveforms are supported.",
                  "Backward Euler can smear fast transitions; repeat with a smaller timestep to assess numerical sensitivity.",
                  "An inconsistent set of ideal capacitor initial voltages and t=0 sources is rejected."],
        warnings=warnings,
        scope="Numerical time-domain simulation of an explicit ideal linear RC network using fixed-step backward Euler; not nonlinear/SPICE equivalence, datasheet verification, or design approval.",
    )
