"""ctypes bridge to the authoritative C++ circuit core, ABI version 1.

The bridge intentionally has no Python-solver fallback. Build the C++ library with
CMake or set CIRCUITMIND_ENGINE_LIB to a compatible shared-library path.
"""
from __future__ import annotations

import ctypes
import math
import os
import re
from pathlib import Path
from typing import Any

from .quantities import make_quantity, read_quantity, require_positive
from .results import result


class CppEngineUnavailable(RuntimeError):
    """Raised when the native C++ engine has not been built or cannot be loaded."""


class _Element(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint32),
        ("node_a", ctypes.c_uint32),
        ("node_b", ctypes.c_uint32),
        ("value", ctypes.c_double),
        ("initial_value", ctypes.c_double),
        ("final_value", ctypes.c_double),
        ("step_time_s", ctypes.c_double),
        ("has_step", ctypes.c_uint32),
    ]


class _DcRequest(ctypes.Structure):
    _fields_ = [
        ("request_version", ctypes.c_uint32),
        ("node_count", ctypes.c_uint32),
        ("ground_index", ctypes.c_uint32),
        ("element_count", ctypes.c_uint32),
        ("elements", ctypes.POINTER(_Element)),
    ]


class _DcResult(ctypes.Structure):
    _fields_ = [
        ("node_voltages_v", ctypes.POINTER(ctypes.c_double)),
        ("node_capacity", ctypes.c_size_t),
        ("element_voltages_v", ctypes.POINTER(ctypes.c_double)),
        ("element_voltage_capacity", ctypes.c_size_t),
        ("element_currents_a", ctypes.POINTER(ctypes.c_double)),
        ("element_current_capacity", ctypes.c_size_t),
        ("element_powers_w", ctypes.POINTER(ctypes.c_double)),
        ("element_power_capacity", ctypes.c_size_t),
        ("maximum_equation_residual", ctypes.c_double),
        ("maximum_kcl_residual_a", ctypes.c_double),
        ("maximum_voltage_source_residual_v", ctypes.c_double),
        ("normalized_equation_residual", ctypes.c_double),
        ("total_power_balance_w", ctypes.c_double),
        ("minimum_scaled_pivot", ctypes.c_double),
        ("maximum_scaled_pivot", ctypes.c_double),
        ("unknown_count", ctypes.c_size_t),
    ]


class _TransientRequest(ctypes.Structure):
    _fields_ = [
        ("request_version", ctypes.c_uint32),
        ("node_count", ctypes.c_uint32),
        ("ground_index", ctypes.c_uint32),
        ("element_count", ctypes.c_uint32),
        ("elements", ctypes.POINTER(_Element)),
        ("time_step_s", ctypes.c_double),
        ("stop_time_s", ctypes.c_double),
        ("maximum_steps", ctypes.c_uint32),
    ]


_KIND_CODES = {"resistor": 0, "capacitor": 1, "voltage_source": 2, "current_source": 3}
_MAX_STEPS = 2000


def _library_candidates() -> list[Path]:
    root = Path(__file__).resolve().parents[2]
    candidates = []
    configured = os.environ.get("CIRCUITMIND_ENGINE_LIB")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend([
        root / "core" / "build" / "libcircuitmind_core.so",
        root / "core" / "build" / "core" / "libcircuitmind_core.so",
        root / "core" / "build" / "libcircuitmind_core.dylib",
        root / "core" / "build" / "core" / "libcircuitmind_core.dylib",
        root / "core" / "build" / "circuitmind_core.dll",
        root / "core" / "build" / "core" / "circuitmind_core.dll",
        root / "build" / "core" / "libcircuitmind_core.so",
        root / "build" / "core" / "Release" / "circuitmind_core.dll",
    ])
    return candidates


def _load_library() -> ctypes.CDLL:
    failures = []
    for candidate in _library_candidates():
        if not candidate.is_file():
            continue
        try:
            library = ctypes.CDLL(str(candidate))
            library.cm_engine_version.argtypes = []
            library.cm_engine_version.restype = ctypes.c_char_p
            library.cm_engine_abi_version.argtypes = []
            library.cm_engine_abi_version.restype = ctypes.c_uint32
            if library.cm_engine_abi_version() != 1:
                raise CppEngineUnavailable(f"{candidate} exposes an unsupported C ABI version")
            library.cm_engine_solve_dc_v2.argtypes = [ctypes.POINTER(_DcRequest), ctypes.POINTER(_DcResult),
                                                       ctypes.POINTER(ctypes.c_char), ctypes.c_size_t]
            library.cm_engine_solve_dc_v2.restype = ctypes.c_int
            library.cm_engine_simulate_transient_rc_v1.argtypes = [
                ctypes.POINTER(_TransientRequest),
                ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_char), ctypes.c_size_t,
            ]
            library.cm_engine_simulate_transient_rc_v1.restype = ctypes.c_int
            return library
        except (OSError, AttributeError) as exc:
            failures.append(f"{candidate}: {exc}")
    detail = "; ".join(failures)
    raise CppEngineUnavailable(
        "CIRCUITMIND C++ core library is unavailable. Build it with `cmake -S . -B build && "
        "cmake --build build --config Release`, set CIRCUITMIND_ENGINE_LIB to the shared library path, "
        "and retry." + (f" Load errors: {detail}" if detail else "")
    )


def engine_version() -> str:
    library = _load_library()
    version = library.cm_engine_version()
    return version.decode("ascii") if version else "unknown"


_NODE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,31}$")
_REFERENCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$")


def solve_dc_operating_point(netlist: dict[str, Any]) -> dict[str, Any]:
    """Run an explicit linear DC netlist in the C++ core and format its evidence."""
    if not isinstance(netlist, dict) or set(netlist) != {"ground", "elements"}:
        raise ValueError("MNA input must contain exactly 'ground' and 'elements'")
    ground = netlist.get("ground")
    if not isinstance(ground, str) or not _NODE_RE.fullmatch(ground):
        raise ValueError("ground must be a valid node label")
    elements = netlist.get("elements")
    if not isinstance(elements, list) or not elements or len(elements) > 500:
        raise ValueError("elements must contain between 1 and 500 supported elements")
    parsed: list[dict[str, Any]] = []
    nodes: set[str] = set()
    references: set[str] = set()
    input_quantities: list[dict[str, Any]] = []
    source_count = 0
    field_info = {"resistor": ("resistance", "resistance", 0),
                  "voltage_source": ("voltage", "voltage", 2),
                  "current_source": ("current", "current", 3)}
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"elements[{index}] must be an object")
        kind = element.get("type")
        if not isinstance(kind, str) or kind not in field_info:
            raise ValueError(f"elements[{index}] has unsupported type {kind!r}; only resistor, voltage_source, and current_source are supported")
        value_field, dimension, code = field_info[kind]
        if set(element) != {"type", "reference", "nodes", value_field}:
            raise ValueError(f"elements[{index}] must contain exactly type, reference, nodes, and {value_field}")
        reference = element.get("reference")
        if not isinstance(reference, str) or not _REFERENCE_RE.fullmatch(reference) or reference in references:
            raise ValueError(f"elements[{index}] needs a unique simple reference designator")
        references.add(reference)
        terminals = element.get("nodes")
        if not isinstance(terminals, list) or len(terminals) != 2 or any(not isinstance(node, str) or not _NODE_RE.fullmatch(node) for node in terminals):
            raise ValueError(f"{reference}.nodes must contain exactly two valid node labels")
        first, second = terminals
        if first == second:
            raise ValueError(f"{reference} cannot connect both terminals to the same node")
        nodes.update(terminals)
        quantity = read_quantity(element.get(value_field), dimension, f"{reference} {value_field}")
        if kind == "resistor":
            require_positive(quantity)
        else:
            source_count += kind == "voltage_source"
        input_quantities.append({**quantity, "reference": reference, "element_type": kind})
        parsed.append({"kind": kind, "reference": reference, "first": first, "second": second,
                       "value_si": quantity["si_value"], "code": code})
    if ground not in nodes:
        raise ValueError("The declared ground node must appear in at least one element")
    unknown_nodes = sorted(nodes - {ground})
    if len(unknown_nodes) > 150:
        raise ValueError("network exceeds the 150-unknown-node limit")
    dimension = len(unknown_nodes) + source_count
    if dimension == 0:
        raise ValueError("Network has no non-ground unknowns")
    if dimension > 180:
        raise ValueError("network exceeds the 180-total-MNA-unknown limit")
    node_labels = [ground, *unknown_nodes]
    node_indices = {name: index for index, name in enumerate(node_labels)}
    native = [_Element(item["code"], node_indices[item["first"]], node_indices[item["second"]],
                       item["value_si"], 0.0, 0.0, 0.0, 0) for item in parsed]
    native_array = (_Element * len(native))(*native)
    request = _DcRequest(1, len(node_labels), 0, len(native), native_array)
    node_values = (ctypes.c_double * len(node_labels))()
    element_voltages = (ctypes.c_double * len(native))()
    element_currents = (ctypes.c_double * len(native))()
    element_powers = (ctypes.c_double * len(native))()
    output = _DcResult(node_values, len(node_labels), element_voltages, len(native),
                        element_currents, len(native), element_powers, len(native))
    library = _load_library()
    error = ctypes.create_string_buffer(512)
    status = library.cm_engine_solve_dc_v2(ctypes.byref(request), ctypes.byref(output), error, len(error))
    if status != 0:
        raise ValueError(error.value.decode("utf-8", errors="replace") or f"C++ core returned status {status}")
    node_voltages = {node: node_values[index] for index, node in enumerate(node_labels)}
    element_outputs = [{"reference": item["reference"], "type": item["kind"],
                        "voltage": make_quantity(element_voltages[index], "V"),
                        "current": make_quantity(element_currents[index], "A"),
                        "power_absorbed": make_quantity(element_powers[index], "W")}
                       for index, item in enumerate(parsed)]
    warnings = ["This is a linear DC operating-point solve; it is not transient, nonlinear, thermal, or a SPICE execution.",
                "The netlist is explicit user input and was not generated from or checked against the saved circuit graph."]
    if output.maximum_scaled_pivot > 0 and output.minimum_scaled_pivot / output.maximum_scaled_pivot < 1e-8:
        warnings.append("Scaled pivot spread is large; numerical conditioning may limit significant digits.")
    outputs = {
        "node_voltages": {node: make_quantity(node_voltages[node], "V") for node in sorted(node_voltages)},
        "elements": element_outputs,
        "maximum_kcl_residual": make_quantity(output.maximum_kcl_residual_a, "A"),
        "maximum_voltage_source_residual": make_quantity(output.maximum_voltage_source_residual_v, "V"),
        "normalized_equation_residual": output.normalized_equation_residual,
        "total_power_balance": make_quantity(output.total_power_balance_w, "W"),
        "solver_status": "solved-linear-dc-model",
    }
    return result(
        "Linear DC modified-nodal-analysis operating point",
        "G·v + B·i = j; Bᵀ·v = e",
        input_quantities, outputs,
        substitution=f"Solved {output.unknown_count} MNA unknowns ({len(unknown_nodes)} node voltages, {source_count} voltage-source branch currents) in the C++ core.",
        intermediates={"unknown_node_count": len(unknown_nodes), "voltage_source_count": source_count,
                       "matrix_dimension": output.unknown_count, "minimum_scaled_pivot": output.minimum_scaled_pivot,
                       "maximum_scaled_pivot": output.maximum_scaled_pivot,
                       "maximum_equation_residual": output.maximum_equation_residual},
        assumptions=["Ideal linear resistors and independent DC sources; ground is explicitly specified.",
                     "Current flows from each source's first listed terminal to its second; voltage-source value is the first-terminal voltage minus the second-terminal voltage.",
                     "Positive element power means absorbed; negative source power means delivered."],
        validity=["Only independent resistors, voltage sources, and current sources are supported.",
                  "Capacitors/inductors, dependent sources, nonlinear devices, parasitics, tolerances, and transient behavior are not modeled.",
                  "Dense solve is limited to 150 non-ground nodes and 180 total MNA unknowns; singular or weak-pivot systems are rejected."],
        warnings=warnings,
        scope="Numerical linear DC operating-point solution from the native C++ core for the explicit ideal R/V/I netlist; not transient or nonlinear simulation, datasheet verification, or design approval.",
    )


def simulate_transient_rc(netlist: dict[str, Any]) -> dict[str, Any]:
    """Marshal a normalized Python transient netlist through the C ABI v1.

    This function only validates/converts units and node names. Numerical solving is
    performed in the native engine; absence of that engine is an explicit error.
    """
    if not isinstance(netlist, dict) or set(netlist) - {"ground", "time_step", "stop_time", "elements", "method"}:
        raise ValueError("C++ transient input has unsupported or malformed top-level fields")
    if netlist.get("method", "backward_euler") != "backward_euler":
        raise ValueError("C++ core v1 supports backward_euler only")
    ground = netlist.get("ground")
    elements = netlist.get("elements")
    if not isinstance(ground, str) or not ground or not isinstance(elements, list):
        raise ValueError("C++ transient input requires ground and elements")
    dt_quantity = read_quantity(netlist.get("time_step"), "time", "C++ transient time step")
    stop_quantity = read_quantity(netlist.get("stop_time"), "time", "C++ transient stop time")
    dt = dt_quantity["si_value"]
    stop = stop_quantity["si_value"]
    input_quantities = [dt_quantity, stop_quantity]
    if not math.isfinite(dt) or not math.isfinite(stop) or dt <= 0 or stop <= 0:
        raise ValueError("C++ transient time step and stop time must be positive finite values")
    ratio = stop / dt
    if not math.isfinite(ratio):
        raise ValueError("C++ transient stop_time / time_step is outside the finite numeric range")
    steps = round(ratio)
    if steps < 1 or steps > _MAX_STEPS or not math.isclose(ratio, steps, rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError(f"C++ transient stop_time must be an integer multiple of time_step with at most {_MAX_STEPS} steps")
    node_set = {ground}
    if len(elements) == 0:
        raise ValueError("C++ transient input needs at least one element")
    if len(elements) > 100:
        raise ValueError("C++ transient input exceeds the 100-element core limit")
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"elements[{index}] must be an object")
        terminals = element.get("nodes")
        if not isinstance(terminals, list) or len(terminals) != 2 or any(not isinstance(node, str) or not node for node in terminals):
            raise ValueError(f"elements[{index}].nodes must contain two non-empty node labels")
        node_set.update(terminals)
    if len(node_set) > 25:
        raise ValueError("C++ transient input exceeds the 25-node core limit")
    node_labels = [ground] + sorted(node_set - {ground})
    node_indices = {name: index for index, name in enumerate(node_labels)}

    native_elements = []
    capacitor_refs: dict[str, tuple[str, str]] = {}
    references: set[str] = set()
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"elements[{index}] must be an object")
        kind = element.get("type")
        if not isinstance(kind, str) or kind not in _KIND_CODES:
            raise ValueError(f"elements[{index}] has unsupported type {kind!r}")
        if kind == "resistor":
            expected_fields = {"type", "reference", "nodes", "resistance"}
        elif kind == "capacitor":
            expected_fields = {"type", "reference", "nodes", "capacitance", "initial_voltage"}
        elif "waveform" in element:
            expected_fields = {"type", "reference", "nodes", "waveform"}
        else:
            expected_fields = {"type", "reference", "nodes", "voltage" if kind == "voltage_source" else "current"}
        if set(element) != expected_fields:
            raise ValueError(f"elements[{index}] has unsupported or missing fields for {kind}")
        terminals = element.get("nodes")
        if not isinstance(terminals, list) or len(terminals) != 2 or any(node not in node_indices for node in terminals):
            raise ValueError(f"elements[{index}].nodes must contain two declared node labels")
        reference = element.get("reference")
        if not isinstance(reference, str) or not reference or reference in references:
            raise ValueError(f"elements[{index}] needs a non-empty unique reference")
        references.add(reference)
        value = initial = final = step_time = 0.0
        has_step = 0
        if kind == "resistor":
            resistance = read_quantity(element.get("resistance"), "resistance", f"{reference} resistance")
            value = resistance["si_value"]
            input_quantities.append(resistance)
        elif kind == "capacitor":
            capacitance = read_quantity(element.get("capacitance"), "capacitance", f"{reference} capacitance")
            initial_voltage = read_quantity(element.get("initial_voltage"), "voltage", f"{reference} initial voltage")
            value = capacitance["si_value"]
            initial = initial_voltage["si_value"]
            input_quantities.extend((capacitance, initial_voltage))
            capacitor_refs[reference] = (terminals[0], terminals[1])
        else:
            field, dimension = ("voltage", "voltage") if kind == "voltage_source" else ("current", "current")
            waveform = element.get("waveform")
            if waveform is not None:
                if not isinstance(waveform, dict) or set(waveform) != {"type", "initial", "final", "step_time"} or waveform.get("type") != "step":
                    raise ValueError(f"{reference} waveform must be an explicit single step")
                initial_quantity = read_quantity(waveform["initial"], dimension, f"{reference} waveform initial")
                final_quantity = read_quantity(waveform["final"], dimension, f"{reference} waveform final")
                step_quantity = read_quantity(waveform["step_time"], "time", f"{reference} waveform step time")
                initial = initial_quantity["si_value"]
                final = final_quantity["si_value"]
                step_time = step_quantity["si_value"]
                input_quantities.extend((initial_quantity, final_quantity, step_quantity))
                if step_time < 0 or step_time > stop:
                    raise ValueError(f"{reference} waveform step_time must be between zero and stop_time")
                value = initial
                has_step = 1
            else:
                source_quantity = read_quantity(element.get(field), dimension, f"{reference} {field}")
                value = source_quantity["si_value"]
                input_quantities.append(source_quantity)
        native_elements.append(_Element(_KIND_CODES[kind], node_indices[terminals[0]], node_indices[terminals[1]],
                                        value, initial, final, step_time, has_step))

    library = _load_library()
    engine_version_bytes = library.cm_engine_version()
    version = engine_version_bytes.decode("ascii") if engine_version_bytes else "unknown"
    native_array_type = _Element * len(native_elements)
    native_array = native_array_type(*native_elements)
    request = _TransientRequest(1, len(node_labels), 0, len(native_elements), native_array,
                                dt, stop, _MAX_STEPS)
    sample_capacity = steps + 1
    times = (ctypes.c_double * sample_capacity)()
    voltages = (ctypes.c_double * (sample_capacity * len(node_labels)))()
    written = ctypes.c_size_t(0)
    residual = ctypes.c_double(0.0)
    error = ctypes.create_string_buffer(512)
    status = library.cm_engine_simulate_transient_rc_v1(
        ctypes.byref(request), times, sample_capacity, voltages, sample_capacity * len(node_labels),
        ctypes.byref(written), ctypes.byref(residual), error, len(error)
    )
    if status != 0:
        raise ValueError(error.value.decode("utf-8", errors="replace") or f"C++ core returned status {status}")
    if written.value != sample_capacity:
        raise RuntimeError("C++ core returned an unexpected sample count")
    time_points = [times[index] for index in range(sample_capacity)]
    traces = {node: [voltages[sample * len(node_labels) + node_indices[node]] for sample in range(sample_capacity)]
              for node in node_labels}
    cap_traces = {reference: [traces[first][sample] - traces[second][sample] for sample in range(sample_capacity)]
                  for reference, (first, second) in capacitor_refs.items()}
    return {
        "engine": "circuitmind-cpp-core",
        "engine_version": version,
        "method": "backward_euler",
        "status": "completed-numerical-linear-rc-transient",
        "time_step": {"value": dt, "unit": "s"},
        "stop_time": {"value": stop, "unit": "s"},
        "sample_count": sample_capacity,
        "step_count": steps,
        "non_ground_node_count": len(node_labels) - 1,
        "capacitor_count": len(capacitor_refs),
        "maximum_equation_residual": residual.value,
        "input_quantities": input_quantities,
        "time_points_s": time_points,
        "node_voltage_traces_v": traces,
        "capacitor_voltage_traces_v": cap_traces,
        "scope": "Numerical result computed by the native CircuitMind C++ core using bounded dense MNA and fixed-step backward Euler.",
    }
