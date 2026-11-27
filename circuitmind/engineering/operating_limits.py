"""Compare native simulation stresses against caller-entered limits."""
from __future__ import annotations

import math
from typing import Any

from .quantities import make_quantity, read_quantity, require_positive

_DC_LIMITS = {
    "max_abs_voltage": ("voltage", "voltage", "V"),
    "max_abs_current": ("current", "current", "A"),
    "max_abs_power": ("power_absorbed", "power", "W"),
}


def assess_operating_limits(elements: list[dict[str, Any]], limits: list[dict[str, Any]]) -> dict[str, Any]:
    """Check selected DC element outputs against explicit user-provided bounds."""
    if not isinstance(elements, list) or not elements:
        raise ValueError("A graph-bound DC report with element results is required")
    if not isinstance(limits, list) or not limits or len(limits) > 500:
        raise ValueError("limits must be a nonempty array with at most 500 entries")
    measured_by_reference = {}
    for index, element in enumerate(elements):
        if not isinstance(element, dict) or not isinstance(element.get("reference"), str):
            raise ValueError(f"simulation element result {index} is malformed")
        reference = element["reference"]
        if reference in measured_by_reference:
            raise ValueError(f"simulation report contains duplicate element reference {reference}")
        measured_by_reference[reference] = element
    seen: set[str] = set()
    evaluations = []
    check_count = 0
    failure_count = 0
    for index, item in enumerate(limits):
        if not isinstance(item, dict):
            raise ValueError(f"limits[{index}] must be an object")
        allowed = {"reference", *_DC_LIMITS}
        if set(item) - allowed or "reference" not in item:
            raise ValueError(f"limits[{index}] must contain reference and only max_abs_voltage/current/power fields")
        reference = item["reference"]
        if not isinstance(reference, str) or not reference:
            raise ValueError(f"limits[{index}].reference must be a nonempty string")
        if reference in seen:
            raise ValueError(f"duplicate limit entry for {reference}")
        seen.add(reference)
        if reference not in measured_by_reference:
            raise ValueError(f"No measured element named {reference} exists in the selected graph-bound DC report")
        provided = [key for key in _DC_LIMITS if key in item]
        if not provided:
            raise ValueError(f"limits[{index}] must provide at least one explicit maximum")
        element = measured_by_reference[reference]
        checks = []
        for key in provided:
            result_field, dimension, unit = _DC_LIMITS[key]
            limit_quantity = read_quantity(item[key], dimension, f"{reference} {key}")
            limit_si = require_positive(limit_quantity, f"{reference} {key}")
            raw_measurement = element.get(result_field)
            if not isinstance(raw_measurement, dict) or raw_measurement.get("unit") != unit:
                raise ValueError(f"{reference} is missing a usable {result_field} measurement")
            measured_quantity = read_quantity(raw_measurement, dimension, f"{reference} measured {result_field}")
            measured_si = abs(measured_quantity["si_value"])
            status = "pass" if measured_si <= limit_si else "fail"
            checks.append({"metric": result_field, "comparison": "absolute measured value <= caller-supplied maximum",
                           "measured_absolute": make_quantity(measured_si, unit),
                           "user_supplied_maximum": limit_quantity, "status": status})
            check_count += 1
            failure_count += status == "fail"
        evaluations.append({"reference": reference, "element_type": element.get("type", "unspecified"),
                            "status": "fail" if any(check["status"] == "fail" for check in checks) else "pass",
                            "checks": checks})
    return {"status": "fail" if failure_count else "pass", "check_count": check_count,
            "failure_count": failure_count, "evaluated_component_count": len(evaluations),
            "evaluations": evaluations,
            "scope": "Explicit user-entered absolute terminal-voltage, current, and absorbed-power bounds compared with one saved graph-bound DC operating-point report. Limits are not sourced from or verified against part data; this is not component qualification, thermal analysis, or design approval."}


def assess_transient_reactive_limits(outputs: dict[str, Any], limits: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare peak capacitor voltages or inductor currents with explicit bounds."""
    if not isinstance(outputs, dict):
        raise ValueError("A graph-bound transient result is required")
    times = outputs.get("time_points_s")
    if not isinstance(times, list) or not times:
        raise ValueError("Transient report has no time points")
    for index, time in enumerate(times):
        if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(float(time)):
            raise ValueError(f"Transient time point {index} is not finite")
    if not isinstance(limits, list) or not limits or len(limits) > 500:
        raise ValueError("limits must be a nonempty array with at most 500 entries")
    capacitor_traces = outputs.get("capacitor_voltage_traces_v", {})
    inductor_traces = outputs.get("inductor_current_traces_a", {})
    if not isinstance(capacitor_traces, dict) or not isinstance(inductor_traces, dict):
        raise ValueError("Transient reactive-state traces are malformed")
    seen: set[str] = set()
    evaluations = []
    failure_count = 0
    for index, item in enumerate(limits):
        if not isinstance(item, dict) or set(item) - {"reference", "max_abs_voltage", "max_abs_current"} or "reference" not in item:
            raise ValueError(f"limits[{index}] must contain reference and only max_abs_voltage or max_abs_current")
        reference = item["reference"]
        if not isinstance(reference, str) or not reference or reference in seen:
            raise ValueError(f"limits[{index}].reference must be unique nonempty text")
        seen.add(reference)
        has_voltage, has_current = "max_abs_voltage" in item, "max_abs_current" in item
        if has_voltage == has_current:
            raise ValueError(f"limits[{index}] must provide exactly one of max_abs_voltage for a capacitor or max_abs_current for an inductor")
        if has_voltage:
            traces, key, dimension, unit, metric = capacitor_traces, "max_abs_voltage", "voltage", "V", "peak_capacitor_voltage"
        else:
            traces, key, dimension, unit, metric = inductor_traces, "max_abs_current", "current", "A", "peak_inductor_current"
        values = traces.get(reference)
        if not isinstance(values, list) or len(values) != len(times) or not values:
            raise ValueError(f"No aligned {metric} trace exists for {reference}")
        numeric_values = []
        for sample, value in enumerate(values):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{reference} trace sample {sample} is not finite")
            numeric_values.append(float(value))
        peak_index = max(range(len(numeric_values)), key=lambda i: abs(numeric_values[i]))
        measured_si = abs(numeric_values[peak_index])
        limit_quantity = read_quantity(item[key], dimension, f"{reference} {key}")
        limit_si = require_positive(limit_quantity, f"{reference} {key}")
        status = "pass" if measured_si <= limit_si else "fail"
        failure_count += status == "fail"
        evaluations.append({"reference": reference, "metric": metric,
                            "measured_absolute_peak": make_quantity(measured_si, unit),
                            "peak_time": make_quantity(float(times[peak_index]), "s"),
                            "user_supplied_maximum": limit_quantity,
                            "status": status})
    return {"status": "fail" if failure_count else "pass", "check_count": len(evaluations),
            "failure_count": failure_count, "evaluated_component_count": len(evaluations),
            "evaluations": evaluations,
            "scope": "Peak absolute capacitor-voltage or inductor-current samples compared with caller-supplied bounds from one saved graph-bound RC/RLC transient report. No datasheet ratings, inter-sample peaks, thermal behavior, or design approval are inferred."}
