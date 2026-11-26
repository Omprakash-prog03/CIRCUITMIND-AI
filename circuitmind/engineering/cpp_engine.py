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


class _DcSweepRequest(ctypes.Structure):
    _fields_ = [
        ("request_version", ctypes.c_uint32),
        ("dc_request", _DcRequest),
        ("swept_element_index", ctypes.c_uint32),
        ("point_count", ctypes.c_size_t),
        ("source_values", ctypes.POINTER(ctypes.c_double)),
    ]


class _DcSweepResult(ctypes.Structure):
    _fields_ = [
        ("node_voltage_traces_v", ctypes.POINTER(ctypes.c_double)),
        ("node_voltage_capacity", ctypes.c_size_t),
        ("maximum_equation_residuals", ctypes.POINTER(ctypes.c_double)),
        ("maximum_residual_capacity", ctypes.c_size_t),
        ("normalized_equation_residuals", ctypes.POINTER(ctypes.c_double)),
        ("normalized_residual_capacity", ctypes.c_size_t),
        ("point_count", ctypes.c_size_t),
        ("node_count", ctypes.c_size_t),
        ("unknown_count", ctypes.c_size_t),
    ]


class _AcElement(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint32),
        ("node_a", ctypes.c_uint32),
        ("node_b", ctypes.c_uint32),
        ("value", ctypes.c_double),
        ("phase_rad", ctypes.c_double),
    ]


class _AcSweepRequest(ctypes.Structure):
    _fields_ = [
        ("request_version", ctypes.c_uint32),
        ("node_count", ctypes.c_uint32),
        ("ground_index", ctypes.c_uint32),
        ("element_count", ctypes.c_uint32),
        ("elements", ctypes.POINTER(_AcElement)),
        ("frequency_count", ctypes.c_size_t),
        ("frequencies_hz", ctypes.POINTER(ctypes.c_double)),
    ]


class _AcSweepResult(ctypes.Structure):
    _fields_ = [
        ("node_voltage_real_v", ctypes.POINTER(ctypes.c_double)),
        ("node_voltage_real_capacity", ctypes.c_size_t),
        ("node_voltage_imag_v", ctypes.POINTER(ctypes.c_double)),
        ("node_voltage_imag_capacity", ctypes.c_size_t),
        ("normalized_equation_residuals", ctypes.POINTER(ctypes.c_double)),
        ("residual_capacity", ctypes.c_size_t),
        ("point_count", ctypes.c_size_t),
        ("node_count", ctypes.c_size_t),
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
_TRANSIENT_KIND_CODES = {**_KIND_CODES, "inductor": 4}
_AC_KIND_CODES = {**_KIND_CODES, "inductor": 4}
_MAX_STEPS = 2000
_MAX_AC_FREQUENCIES = 200


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
            library.cm_engine_simulate_dc_sweep_v1.argtypes = [
                ctypes.POINTER(_DcSweepRequest), ctypes.POINTER(_DcSweepResult),
                ctypes.POINTER(ctypes.c_char), ctypes.c_size_t,
            ]
            library.cm_engine_simulate_dc_sweep_v1.restype = ctypes.c_int
            library.cm_engine_simulate_transient_rc_v1.argtypes = [
                ctypes.POINTER(_TransientRequest),
                ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_double), ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_char), ctypes.c_size_t,
            ]
            library.cm_engine_simulate_transient_rc_v1.restype = ctypes.c_int
            library.cm_engine_simulate_ac_sweep_v1.argtypes = [
                ctypes.POINTER(_AcSweepRequest), ctypes.POINTER(_AcSweepResult),
                ctypes.POINTER(ctypes.c_char), ctypes.c_size_t,
            ]
            library.cm_engine_simulate_ac_sweep_v1.restype = ctypes.c_int
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
    diode_count = 0
    shockley_count = 0
    field_info = {"resistor": ("resistance", "resistance", 0),
                  "voltage_source": ("voltage", "voltage", 2),
                  "current_source": ("current", "current", 3),
                  "diode": ("forward_voltage", "voltage", 5),
                  "shockley_diode": ("saturation_current", "current", 6)}
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"elements[{index}] must be an object")
        kind = element.get("type")
        if not isinstance(kind, str) or kind not in field_info:
            raise ValueError(f"elements[{index}] has unsupported type {kind!r}; supported kinds are resistor, independent sources, piecewise-linear diode, and Shockley diode")
        value_field, dimension, code = field_info[kind]
        expected_fields = {"type", "reference", "nodes", value_field}
        if kind == "diode":
            expected_fields.add("off_resistance")
        elif kind == "shockley_diode":
            expected_fields.update({"ideality_factor", "temperature_kelvin"})
        if set(element) != expected_fields:
            raise ValueError(f"elements[{index}] must contain exactly the fields {', '.join(sorted(expected_fields))}")
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
        initial_si = final_si = 0.0
        if kind == "resistor":
            require_positive(quantity)
        elif kind == "diode":
            require_positive(quantity)
            off_resistance = read_quantity(element["off_resistance"], "resistance", f"{reference} off resistance")
            require_positive(off_resistance)
            input_quantities.append({**off_resistance, "reference": reference, "element_type": "diode_off_resistance"})
            initial_si = off_resistance["si_value"]
            diode_count += 1
        elif kind == "shockley_diode":
            if not 1e-30 <= quantity["si_value"] <= 1e-3:
                raise ValueError(f"{reference} saturation current must be between 1e-30 and 1e-3 A")
            ideality = element["ideality_factor"]
            if isinstance(ideality, bool) or not isinstance(ideality, (int, float)) or not math.isfinite(ideality) or not 0.5 <= ideality <= 2.0:
                raise ValueError(f"{reference} ideality_factor must be finite and between 0.5 and 2")
            temperature = read_quantity(element["temperature_kelvin"], "temperature", f"{reference} temperature")
            if not 200.0 <= temperature["si_value"] <= 500.0:
                raise ValueError(f"{reference} temperature must be between 200 and 500 K")
            input_quantities.append({"name": f"{reference} ideality factor", "value": float(ideality),
                                     "unit": "1", "si_value": float(ideality), "si_unit": "1",
                                     "reference": reference, "element_type": "shockley_ideality_factor"})
            input_quantities.append({**temperature, "reference": reference, "element_type": "shockley_temperature"})
            initial_si = float(ideality)
            final_si = temperature["si_value"]
            shockley_count += 1
        else:
            source_count += kind == "voltage_source"
        input_quantities.append({**quantity, "reference": reference, "element_type": kind})
        parsed.append({"kind": kind, "reference": reference, "first": first, "second": second,
                       "value_si": quantity["si_value"], "initial_si": initial_si,
                       "final_si": final_si, "code": code})
    if diode_count and shockley_count:
        raise ValueError("Combining piecewise-linear and Shockley diode models in one DC solve is not currently supported")
    if ground not in nodes:
        raise ValueError("The declared ground node must appear in at least one element")
    unknown_nodes = sorted(nodes - {ground})
    if len(unknown_nodes) > 150:
        raise ValueError("network exceeds the 150-unknown-node limit")
    dimension = len(unknown_nodes) + source_count + diode_count
    if dimension == 0:
        raise ValueError("Network has no non-ground unknowns")
    if dimension > 180:
        raise ValueError("network exceeds the 180-total-MNA-unknown limit")
    node_labels = [ground, *unknown_nodes]
    node_indices = {name: index for index, name in enumerate(node_labels)}
    native = [_Element(item["code"], node_indices[item["first"]], node_indices[item["second"]],
                       item["value_si"], item["initial_si"], item["final_si"], 0.0, 0) for item in parsed]
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
    has_diodes = diode_count > 0
    has_shockley = shockley_count > 0
    warnings = ["This is a bounded Shockley diode Newton solve, not a general nonlinear SPICE execution." if has_shockley else ("This is a DC solve with an explicit piecewise-linear diode active-set model, not a general nonlinear SPICE execution." if has_diodes else "This is a linear DC operating-point solve; it is not transient, nonlinear, thermal, or a SPICE execution."),
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
        "solver_status": "solved-shockley-diode-newton-dc-model" if has_shockley else ("solved-piecewise-linear-diode-dc-model" if has_diodes else "solved-linear-dc-model"),
    }
    return result(
        "DC modified-nodal-analysis operating point with Shockley diodes" if has_shockley else ("DC modified-nodal-analysis operating point with piecewise-linear diodes" if has_diodes else "Linear DC modified-nodal-analysis operating point"),
        "G·v + B·i = j; Bᵀ·v = e; Shockley diode currents solved by bounded Newton iteration" if has_shockley else ("G·v + B·i = j; Bᵀ·v = e; diode states solved by bounded active-set iteration" if has_diodes else "G·v + B·i = j; Bᵀ·v = e"),
        input_quantities, outputs,
        substitution=f"Solved {output.unknown_count} MNA unknowns ({len(unknown_nodes)} node voltages, {source_count} voltage-source branch currents) in the C++ core.",
        intermediates={"unknown_node_count": len(unknown_nodes), "voltage_source_count": source_count,
                       "matrix_dimension": output.unknown_count, "minimum_scaled_pivot": output.minimum_scaled_pivot,
                       "maximum_scaled_pivot": output.maximum_scaled_pivot,
                       "maximum_equation_residual": output.maximum_equation_residual},
        assumptions=["Ideal linear resistors and independent DC sources; ground is explicitly specified.",
                     "Piecewise-linear diodes use a constant forward drop and finite off resistance; Shockley diodes use explicit Is, ideality, and absolute temperature.",
                     "Shockley Newton iterations use junction-voltage limiting; model parameter and exponent bounds are enforced.",
                     "Current flows from each source's first listed terminal to its second; voltage-source value is the first-terminal voltage minus the second-terminal voltage.",
                     "Positive element power means absorbed; negative source power means delivered."],
        validity=["Supported DC elements are resistors, independent voltage/current sources, piecewise-linear diodes, and Shockley diodes with explicit parameters.",
                  "Capacitors/inductors, dependent sources, charge storage, parasitics, tolerances, and transient behavior are not modeled.",
                  "Mixing the piecewise-linear and Shockley diode models in one solve is currently rejected.",

                  "Dense solve is limited to 150 non-ground nodes and 180 total MNA unknowns; singular or weak-pivot systems are rejected."],
        warnings=warnings,
        scope="Numerical DC solution from the native C++ core for explicit ideal R/V/I elements and bounded diode models; Shockley current-law support is not a general SPICE engine or device qualification.",
    )


def simulate_dc_sweep(request: dict[str, Any]) -> dict[str, Any]:
    """Run a bounded independent-source sweep in one native C++ call."""
    if not isinstance(request, dict) or set(request) != {"netlist", "sweep"}:
        raise ValueError("DC sweep request must contain exactly netlist and sweep")
    netlist, sweep = request["netlist"], request["sweep"]
    if not isinstance(netlist, dict) or set(netlist) != {"ground", "elements"}:
        raise ValueError("DC sweep netlist must contain exactly ground and elements")
    ground, elements = netlist["ground"], netlist["elements"]
    if not isinstance(ground, str) or not _NODE_RE.fullmatch(ground):
        raise ValueError("ground must be a valid node label")
    if not isinstance(elements, list) or not elements or len(elements) > 500:
        raise ValueError("DC sweep elements must contain between 1 and 500 supported elements")
    if not isinstance(sweep, dict) or set(sweep) != {"reference", "start_value", "stop_value", "points", "scale"}:
        raise ValueError("DC sweep must contain reference, start_value, stop_value, points, and scale")
    swept_reference = sweep["reference"]
    if not isinstance(swept_reference, str) or not _REFERENCE_RE.fullmatch(swept_reference):
        raise ValueError("DC sweep reference must be a valid simple element designator")
    points, scale = sweep["points"], sweep["scale"]
    if isinstance(points, bool) or not isinstance(points, int) or not 2 <= points <= 200:
        raise ValueError("DC sweep points must be an integer between 2 and 200")
    if not isinstance(scale, str) or scale not in {"linear", "log"}:
        raise ValueError("DC sweep scale must be 'linear' or 'log'")

    nodes: set[str] = set()
    references: set[str] = set()
    input_quantities: list[dict[str, Any]] = []
    parsed: list[dict[str, Any]] = []
    swept_index = None
    swept_kind = None
    source_count = 0
    diode_count = 0
    shockley_count = 0
    field_info = {"resistor": ("resistance", "resistance", 0),
                  "voltage_source": ("voltage", "voltage", 2),
                  "current_source": ("current", "current", 3),
                  "diode": ("forward_voltage", "voltage", 5),
                  "shockley_diode": ("saturation_current", "current", 6)}
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"elements[{index}] must be an object")
        kind = element.get("type")
        if not isinstance(kind, str) or kind not in field_info:
            raise ValueError(f"elements[{index}] has unsupported type {kind!r}; supported kinds are resistor, independent sources, and explicit diode models")
        value_field, dimension, code = field_info[kind]
        expected_fields = {"type", "reference", "nodes", value_field}
        if kind == "diode":
            expected_fields.add("off_resistance")
        elif kind == "shockley_diode":
            expected_fields.update({"ideality_factor", "temperature_kelvin"})
        if set(element) != expected_fields:
            raise ValueError(f"elements[{index}] must contain exactly the fields {', '.join(sorted(expected_fields))}")
        reference = element.get("reference")
        if not isinstance(reference, str) or not _REFERENCE_RE.fullmatch(reference) or reference in references:
            raise ValueError(f"elements[{index}] needs a unique simple reference designator")
        references.add(reference)
        terminals = element.get("nodes")
        if not isinstance(terminals, list) or len(terminals) != 2 or any(
                not isinstance(node, str) or not _NODE_RE.fullmatch(node) for node in terminals):
            raise ValueError(f"{reference}.nodes must contain exactly two valid node labels")
        if terminals[0] == terminals[1]:
            raise ValueError(f"{reference} cannot connect both terminals to the same node")
        nodes.update(terminals)
        quantity = read_quantity(element[value_field], dimension, f"{reference} {value_field}")
        initial_si = final_si = 0.0
        if kind == "resistor":
            require_positive(quantity)
        elif kind == "diode":
            require_positive(quantity)
            off_resistance = read_quantity(element["off_resistance"], "resistance", f"{reference} off resistance")
            require_positive(off_resistance)
            input_quantities.append({**off_resistance, "reference": reference, "element_type": "diode_off_resistance"})
            initial_si = off_resistance["si_value"]
            diode_count += 1
        elif kind == "shockley_diode":
            if not 1e-30 <= quantity["si_value"] <= 1e-3:
                raise ValueError(f"{reference} saturation current must be between 1e-30 and 1e-3 A")
            ideality = element["ideality_factor"]
            if isinstance(ideality, bool) or not isinstance(ideality, (int, float)) or not math.isfinite(ideality) or not 0.5 <= ideality <= 2.0:
                raise ValueError(f"{reference} ideality_factor must be finite and between 0.5 and 2")
            temperature = read_quantity(element["temperature_kelvin"], "temperature", f"{reference} temperature")
            if not 200.0 <= temperature["si_value"] <= 500.0:
                raise ValueError(f"{reference} temperature must be between 200 and 500 K")
            input_quantities.append({"name": f"{reference} ideality factor", "value": float(ideality),
                                     "unit": "1", "si_value": float(ideality), "si_unit": "1",
                                     "reference": reference, "element_type": "shockley_ideality_factor"})
            input_quantities.append({**temperature, "reference": reference, "element_type": "shockley_temperature"})
            initial_si = float(ideality)
            final_si = temperature["si_value"]
            shockley_count += 1
        else:
            source_count += kind == "voltage_source"
        input_quantities.append({**quantity, "reference": reference, "element_type": kind})
        parsed.append({"kind": kind, "reference": reference, "first": terminals[0],
                       "second": terminals[1], "value_si": quantity["si_value"], "initial_si": initial_si,
                       "final_si": final_si, "code": code})
        if reference == swept_reference:
            swept_index = index
            swept_kind = kind
    if diode_count and shockley_count:
        raise ValueError("Combining piecewise-linear and Shockley diode models in one DC sweep is not currently supported")
    if swept_index is None or swept_kind not in {"voltage_source", "current_source"}:
        raise ValueError("DC sweep reference must identify exactly one independent voltage or current source")
    if ground not in nodes:
        raise ValueError("The declared ground node must appear in at least one element")
    unknown_nodes = sorted(nodes - {ground})
    if len(unknown_nodes) > 150:
        raise ValueError("network exceeds the 150 non-ground-node limit")
    matrix_dimension = len(unknown_nodes) + source_count + diode_count
    if matrix_dimension == 0 or matrix_dimension > 180:
        raise ValueError("network exceeds the 180-total-MNA-unknown limit")
    node_labels = [ground, *unknown_nodes]
    node_indices = {name: index for index, name in enumerate(node_labels)}
    native_elements = [_Element(item["code"], node_indices[item["first"]], node_indices[item["second"]],
                                item["value_si"], item["initial_si"], item["final_si"], 0.0, 0) for item in parsed]
    native_array = (_Element * len(native_elements))(*native_elements)
    dc_request = _DcRequest(1, len(node_labels), 0, len(native_elements), native_array)

    value_field = "voltage" if swept_kind == "voltage_source" else "current"
    sweep_dimension = "voltage" if swept_kind == "voltage_source" else "current"
    start_quantity = read_quantity(sweep["start_value"], sweep_dimension, f"{swept_reference} sweep start")
    stop_quantity = read_quantity(sweep["stop_value"], sweep_dimension, f"{swept_reference} sweep stop")
    start, stop = start_quantity["si_value"], stop_quantity["si_value"]
    if start >= stop:
        raise ValueError("DC sweep stop_value must be greater than start_value")
    if scale == "linear":
        sweep_values = [start + (stop - start) * index / (points - 1) for index in range(points)]
    else:
        if start <= 0 or stop <= 0:
            raise ValueError("logarithmic DC sweep values must be positive")
        log_start, log_stop = math.log(start), math.log(stop)
        try:
            sweep_values = [math.exp(log_start + (log_stop - log_start) * index / (points - 1))
                            for index in range(points)]
        except OverflowError as exc:
            raise ValueError("logarithmic DC sweep values exceed the finite numeric range") from exc
    if any(not math.isfinite(value) for value in sweep_values) or any(
            sweep_values[index] <= sweep_values[index - 1] for index in range(1, points)):
        raise ValueError("generated DC sweep values are not finite and strictly increasing")

    library = _load_library()
    sweep_array = (ctypes.c_double * points)(*sweep_values)
    native_request = _DcSweepRequest(1, dc_request, swept_index, points, sweep_array)
    node_capacity = points * len(node_labels)
    node_values = (ctypes.c_double * node_capacity)()
    maximum_residuals = (ctypes.c_double * points)()
    normalized_residuals = (ctypes.c_double * points)()
    output = _DcSweepResult(node_values, node_capacity, maximum_residuals, points,
                            normalized_residuals, points)
    error = ctypes.create_string_buffer(512)
    status = library.cm_engine_simulate_dc_sweep_v1(ctypes.byref(native_request), ctypes.byref(output),
                                                    error, len(error))
    if status != 0:
        raise ValueError(error.value.decode("utf-8", errors="replace") or f"C++ core returned status {status}")
    if output.point_count != points or output.node_count != len(node_labels):
        raise RuntimeError("C++ core returned an unexpected DC sweep result shape")
    node_traces = {node: [node_values[sample * len(node_labels) + node_indices[node]] for sample in range(points)]
                   for node in unknown_nodes}
    residuals = [maximum_residuals[index] for index in range(points)]
    normalized = [normalized_residuals[index] for index in range(points)]
    version_bytes = library.cm_engine_version()
    version = version_bytes.decode("ascii") if version_bytes else "unknown"
    has_diodes = diode_count > 0
    has_shockley = shockley_count > 0
    nonlinear_label = "bounded Shockley diode Newton model" if has_shockley else (
        "piecewise-linear diode active-set model" if has_diodes else "linear DC model")
    warnings = [f"Each point uses the {nonlinear_label}; this is not a general nonlinear/SPICE execution.",
                "The sweep varies only the named independent source; no interpolation or continuous-time behavior is modeled."]
    return {
        "calculation_schema_version": 1,
        "name": "DC source sweep with bounded diode models" if has_diodes or has_shockley else "Linear DC source sweep",
        "formula": "G·v + B·i = j; Bᵀ·v = e, solved independently at each source value; nonlinear diode states use bounded native iterations" if has_diodes or has_shockley else "G·v + B·i = j; Bᵀ·v = e, solved independently at each source value",
        "inputs": [start_quantity, stop_quantity, *input_quantities],
        "substitution": f"Solved {points} points by the native C++ DC sweep API for {swept_reference} ({scale} sweep).",
        "intermediate_results": {"swept_element_reference": swept_reference, "swept_parameter": value_field,
                                 "sweep_scale": scale, "sweep_point_count": points,
                                 "matrix_dimension": output.unknown_count,
                                 "maximum_equation_residual": max(residuals)},
        "outputs": {"status": "completed-bounded-diode-dc-sweep" if has_diodes or has_shockley else "completed-linear-dc-sweep",
                    "solver_engine": "circuitmind-cpp-core",
                    "solver_version": version, "swept_element_reference": swept_reference,
                    "swept_parameter": value_field, "sweep_unit": start_quantity["si_unit"],
                    "sweep_scale": scale, "sweep_values": sweep_values, "point_count": points,
                    "node_voltage_traces_v": node_traces,
                    "maximum_equation_residuals": residuals,
                    "normalized_equation_residuals": normalized,
                    "maximum_normalized_equation_residual": max(normalized)},
        "assumptions": ["All circuit elements other than the selected independent source remain constant over the sweep.",
                        "Source polarity and current direction follow the ordered nodes in the explicit netlist."],
        "validity": ["Only independent voltage-source and current-source values may be swept; fixed networks may contain either one explicit diode model type (piecewise-linear or bounded Shockley), but not a mixture.",
                     "Every point uses the bounded native DC solver; capacitors/inductors, dependent sources, and transient behavior are unsupported.",
                     "Sweep contains 2–200 points and uses the same bounded DC MNA and diode parameter limits as a single operating-point solve."],
        "warnings": warnings,
        "scope": "Pointwise bounded DC source sweep computed by the native C++ sweep API; not general nonlinear/SPICE validation, datasheet verification, or design approval.",
    }


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
    inductor_specs: dict[str, tuple[str, str, float, float]] = {}
    references: set[str] = set()
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"elements[{index}] must be an object")
        kind = element.get("type")
        if not isinstance(kind, str) or kind not in _TRANSIENT_KIND_CODES:
            raise ValueError(f"elements[{index}] has unsupported type {kind!r}")
        if kind == "resistor":
            expected_fields = {"type", "reference", "nodes", "resistance"}
        elif kind == "capacitor":
            expected_fields = {"type", "reference", "nodes", "capacitance", "initial_voltage"}
        elif kind == "inductor":
            expected_fields = {"type", "reference", "nodes", "inductance", "initial_current"}
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
        elif kind == "inductor":
            inductance = read_quantity(element.get("inductance"), "inductance", f"{reference} inductance")
            initial_current = read_quantity(element.get("initial_current"), "current", f"{reference} initial current")
            value = inductance["si_value"]
            initial = initial_current["si_value"]
            input_quantities.extend((inductance, initial_current))
            inductor_specs[reference] = (terminals[0], terminals[1], value, initial)
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
        native_elements.append(_Element(_TRANSIENT_KIND_CODES[kind], node_indices[terminals[0]], node_indices[terminals[1]],
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
    inductor_current_traces = {}
    for reference, (first, second, inductance, initial_current) in inductor_specs.items():
        currents = [initial_current]
        for sample in range(1, sample_capacity):
            voltage = traces[first][sample] - traces[second][sample]
            currents.append(currents[-1] + dt / inductance * voltage)
        inductor_current_traces[reference] = currents
    has_inductors = bool(inductor_specs)
    return {
        "engine": "circuitmind-cpp-core",
        "engine_version": version,
        "method": "backward_euler",
        "status": "completed-numerical-linear-rlc-transient" if has_inductors else "completed-numerical-linear-rc-transient",
        "time_step": {"value": dt, "unit": "s"},
        "stop_time": {"value": stop, "unit": "s"},
        "sample_count": sample_capacity,
        "step_count": steps,
        "non_ground_node_count": len(node_labels) - 1,
        "capacitor_count": len(capacitor_refs),
        "inductor_count": len(inductor_specs),
        "maximum_equation_residual": residual.value,
        "input_quantities": input_quantities,
        "time_points_s": time_points,
        "node_voltage_traces_v": traces,
        "capacitor_voltage_traces_v": cap_traces,
        "inductor_current_traces_a": inductor_current_traces,
        "scope": "Numerical result computed by the native CircuitMind C++ core using bounded dense MNA and fixed-step backward Euler for ideal linear RLC elements.",
    }


def simulate_ac_sweep(netlist: dict[str, Any]) -> dict[str, Any]:
    """Compute a bounded linear R/L/C frequency response in the native C++ core.

    AC source magnitudes are RMS phasors with explicit phase_degrees. The result is
    small-signal linear analysis, not a general SPICE substitute.
    """
    if not isinstance(netlist, dict) or set(netlist) != {"ground", "frequency_sweep", "elements"}:
        raise ValueError("AC input must contain exactly ground, frequency_sweep, and elements")
    ground = netlist["ground"]
    sweep = netlist["frequency_sweep"]
    elements = netlist["elements"]
    if not isinstance(ground, str) or not _NODE_RE.fullmatch(ground):
        raise ValueError("ground must be a valid node label")
    if not isinstance(sweep, dict) or set(sweep) != {"start_frequency", "stop_frequency", "points", "scale"}:
        raise ValueError("frequency_sweep must contain start_frequency, stop_frequency, points, and scale")
    start_q = read_quantity(sweep["start_frequency"], "frequency", "AC start frequency")
    stop_q = read_quantity(sweep["stop_frequency"], "frequency", "AC stop frequency")
    start_hz, stop_hz = start_q["si_value"], stop_q["si_value"]
    require_positive(start_q)
    require_positive(stop_q)
    points = sweep["points"]
    scale = sweep["scale"]
    if isinstance(points, bool) or not isinstance(points, int) or not 2 <= points <= _MAX_AC_FREQUENCIES:
        raise ValueError(f"AC frequency points must be an integer between 2 and {_MAX_AC_FREQUENCIES}")
    if not isinstance(scale, str) or scale not in {"linear", "log"}:
        raise ValueError("AC sweep scale must be 'linear' or 'log'")
    if start_hz >= stop_hz:
        raise ValueError("AC stop_frequency must be greater than start_frequency")
    if scale == "linear":
        frequencies = [start_hz + (stop_hz - start_hz) * index / (points - 1) for index in range(points)]
    else:
        log_start, log_stop = math.log(start_hz), math.log(stop_hz)
        try:
            frequencies = [math.exp(log_start + (log_stop - log_start) * index / (points - 1))
                           for index in range(points)]
        except OverflowError as exc:
            raise ValueError("logarithmic AC sweep frequencies exceed the finite numeric range") from exc
    if any(not math.isfinite(value) or value <= 0 for value in frequencies) or any(
            frequencies[index] <= frequencies[index - 1] for index in range(1, len(frequencies))):
        raise ValueError("generated AC frequencies are not finite and strictly increasing; choose a wider range")
    if not isinstance(elements, list) or not elements or len(elements) > 200:
        raise ValueError("AC elements must contain between 1 and 200 supported elements")

    node_names: set[str] = set()
    ground_seen = False
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"elements[{index}] must be an object")
        terminals = element.get("nodes")
        if not isinstance(terminals, list) or len(terminals) != 2 or any(
                not isinstance(node, str) or not _NODE_RE.fullmatch(node) for node in terminals):
            raise ValueError(f"elements[{index}].nodes must contain exactly two valid node labels")
        if terminals[0] == terminals[1]:
            raise ValueError(f"elements[{index}] cannot connect both terminals to the same node")
        node_names.update(terminals)
        ground_seen = ground_seen or ground in terminals
    if not ground_seen:
        raise ValueError("ground must appear in at least one element")
    unknown_nodes = sorted(node_names - {ground})
    if len(unknown_nodes) > 64:
        raise ValueError("AC network exceeds the 64 non-ground-node limit")
    node_labels = [ground, *unknown_nodes]
    node_indices = {name: index for index, name in enumerate(node_labels)}

    native_elements: list[_AcElement] = []
    references: set[str] = set()
    source_count = 0
    voltage_count = 0
    input_quantities: list[dict[str, Any]] = [start_q, stop_q]
    for index, element in enumerate(elements):
        kind = element.get("type")
        if not isinstance(kind, str) or kind not in _AC_KIND_CODES:
            raise ValueError(f"elements[{index}] has unsupported AC type {kind!r}")
        if kind in {"resistor", "capacitor", "inductor"}:
            value_field, dimension = {"resistor": ("resistance", "resistance"),
                                     "capacitor": ("capacitance", "capacitance"),
                                     "inductor": ("inductance", "inductance")}[kind]
            expected = {"type", "reference", "nodes", value_field}
        else:
            value_field = "voltage" if kind == "voltage_source" else "current"
            expected = {"type", "reference", "nodes", value_field, "phase_degrees"}
            dimension = "voltage" if kind == "voltage_source" else "current"
        if set(element) != expected:
            raise ValueError(f"elements[{index}] must contain exactly the fields {', '.join(sorted(expected))}")
        reference = element.get("reference")
        if not isinstance(reference, str) or not _REFERENCE_RE.fullmatch(reference) or reference in references:
            raise ValueError(f"elements[{index}] needs a unique simple reference designator")
        references.add(reference)
        terminals = element["nodes"]
        quantity = read_quantity(element[value_field], dimension, f"{reference} {value_field}")
        if kind in {"resistor", "capacitor", "inductor"}:
            require_positive(quantity)
            phase_rad = 0.0
        else:
            if quantity["si_value"] < 0:
                raise ValueError(f"{reference} AC magnitude must not be negative")
            phase_degrees = element["phase_degrees"]
            if isinstance(phase_degrees, bool) or not isinstance(phase_degrees, (int, float)) or not math.isfinite(phase_degrees):
                raise ValueError(f"{reference} phase_degrees must be a finite number")
            if not -360.0 <= phase_degrees <= 360.0:
                raise ValueError(f"{reference} phase_degrees must be between -360 and 360")
            phase_rad = math.radians(phase_degrees)
            source_count += 1
            voltage_count += kind == "voltage_source"
        input_quantities.append({**quantity, "reference": reference, "element_type": kind,
                                 **({"phase_degrees": element["phase_degrees"]} if kind in {"voltage_source", "current_source"} else {})})
        native_elements.append(_AcElement(_AC_KIND_CODES[kind], node_indices[terminals[0]], node_indices[terminals[1]],
                                          quantity["si_value"], phase_rad))
    if source_count == 0:
        raise ValueError("AC analysis requires at least one independent AC source")
    if len(unknown_nodes) + voltage_count > 80:
        raise ValueError("AC network exceeds the 80-total-MNA-unknown limit")

    library = _load_library()
    engine_version_bytes = library.cm_engine_version()
    version = engine_version_bytes.decode("ascii") if engine_version_bytes else "unknown"
    native_array_type = _AcElement * len(native_elements)
    native_array = native_array_type(*native_elements)
    frequency_array = (ctypes.c_double * len(frequencies))(*frequencies)
    request = _AcSweepRequest(1, len(node_labels), 0, len(native_elements), native_array,
                              len(frequencies), frequency_array)
    output_count = len(frequencies) * len(node_labels)
    real_values = (ctypes.c_double * output_count)()
    imag_values = (ctypes.c_double * output_count)()
    residuals = (ctypes.c_double * len(frequencies))()
    output = _AcSweepResult(real_values, output_count, imag_values, output_count, residuals, len(frequencies))
    error = ctypes.create_string_buffer(512)
    status = library.cm_engine_simulate_ac_sweep_v1(ctypes.byref(request), ctypes.byref(output), error, len(error))
    if status != 0:
        raise ValueError(error.value.decode("utf-8", errors="replace") or f"C++ core returned status {status}")
    if (output.point_count != len(frequencies) or output.node_count != len(node_labels) or
            output.unknown_count != len(unknown_nodes) + voltage_count):
        raise RuntimeError("C++ core returned an unexpected AC sweep shape")

    real_traces: dict[str, list[float]] = {}
    imag_traces: dict[str, list[float]] = {}
    magnitude_traces: dict[str, list[float]] = {}
    phase_traces: dict[str, list[float]] = {}
    for node in node_labels:
        reals = [real_values[sample * len(node_labels) + node_indices[node]] for sample in range(len(frequencies))]
        imags = [imag_values[sample * len(node_labels) + node_indices[node]] for sample in range(len(frequencies))]
        real_traces[node] = reals
        imag_traces[node] = imags
        magnitudes = [math.hypot(real, imag) for real, imag in zip(reals, imags)]
        if any(not math.isfinite(value) for value in magnitudes):
            raise ValueError(f"AC magnitude trace for node {node} exceeds the finite numeric range")
        magnitude_traces[node] = magnitudes
        phase_traces[node] = [math.degrees(math.atan2(imag, real)) for real, imag in zip(reals, imags)]
    return {
        "engine": "circuitmind-cpp-core",
        "engine_version": version,
        "status": "completed-linear-ac-sweep",
        "scale": scale,
        "frequency_points_hz": frequencies,
        "node_voltage_real_traces_v": real_traces,
        "node_voltage_imag_traces_v": imag_traces,
        "node_voltage_magnitude_traces_v": magnitude_traces,
        "node_voltage_phase_traces_deg": phase_traces,
        "normalized_equation_residuals": [residuals[index] for index in range(len(frequencies))],
        "maximum_normalized_equation_residual": max(residuals),
        "unknown_count": len(unknown_nodes) + voltage_count,
        "non_ground_node_count": len(unknown_nodes),
        "element_count": len(elements),
        "input_quantities": input_quantities,
        "scope": "Bounded linear R/C frequency-domain nodal response computed by the native C++ core using sinusoidal RMS phasors.",
    }
