"""ctypes bridge to the authoritative C++ circuit core, ABI version 1.

The bridge intentionally has no Python-solver fallback. Build the C++ library with
CMake or set CIRCUITMIND_ENGINE_LIB to a compatible shared-library path.
"""
from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
from typing import Any

from .quantities import read_quantity


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
    dt = read_quantity(netlist.get("time_step"), "time", "C++ transient time step")["si_value"]
    stop = read_quantity(netlist.get("stop_time"), "time", "C++ transient stop time")["si_value"]
    if not math.isfinite(dt) or not math.isfinite(stop) or dt <= 0 or stop <= 0:
        raise ValueError("C++ transient time step and stop time must be positive finite values")
    ratio = stop / dt
    steps = round(ratio)
    if not math.isfinite(ratio) or steps < 1 or steps > _MAX_STEPS or not math.isclose(ratio, steps, rel_tol=1e-10, abs_tol=1e-10):
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
        if not isinstance(reference, str) or not reference:
            raise ValueError(f"elements[{index}] needs an explicit reference")
        value = initial = final = step_time = 0.0
        has_step = 0
        if kind == "resistor":
            value = read_quantity(element.get("resistance"), "resistance", f"{reference} resistance")["si_value"]
        elif kind == "capacitor":
            value = read_quantity(element.get("capacitance"), "capacitance", f"{reference} capacitance")["si_value"]
            initial = read_quantity(element.get("initial_voltage"), "voltage", f"{reference} initial voltage")["si_value"]
            capacitor_refs[reference] = (terminals[0], terminals[1])
        else:
            field, dimension = ("voltage", "voltage") if kind == "voltage_source" else ("current", "current")
            waveform = element.get("waveform")
            if waveform is not None:
                if not isinstance(waveform, dict) or set(waveform) != {"type", "initial", "final", "step_time"} or waveform.get("type") != "step":
                    raise ValueError(f"{reference} waveform must be an explicit single step")
                initial = read_quantity(waveform["initial"], dimension, f"{reference} waveform initial")["si_value"]
                final = read_quantity(waveform["final"], dimension, f"{reference} waveform final")["si_value"]
                step_time = read_quantity(waveform["step_time"], "time", f"{reference} waveform step time")["si_value"]
                if step_time < 0 or step_time > stop:
                    raise ValueError(f"{reference} waveform step_time must be between zero and stop_time")
                value = initial
                has_step = 1
            else:
                value = read_quantity(element.get(field), dimension, f"{reference} {field}")["si_value"]
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
        "maximum_equation_residual": residual.value,
        "time_points_s": time_points,
        "node_voltage_traces_v": traces,
        "capacitor_voltage_traces_v": cap_traces,
        "scope": "Numerical result computed by the native CircuitMind C++ core using bounded dense MNA and fixed-step backward Euler.",
    }
