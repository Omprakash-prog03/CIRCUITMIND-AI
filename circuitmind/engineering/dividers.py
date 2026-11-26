"""Integer clock-divider candidate calculations (no chip-specific limits)."""
from __future__ import annotations

import math
from typing import Any

from .quantities import make_quantity, read_quantity, require_positive
from .results import result
from .timebase import _read_tolerance

_MAX_EXACT_JSON_INTEGER = (1 << 53) - 1
_SECONDS_PER_DAY = 86_400.0


def clock_divider_candidate(source_frequency: dict[str, Any], target_frequency: dict[str, Any],
                            source_tolerance_ppm: float) -> dict[str, Any]:
    """Choose the nearest positive integer divider modulus to a target rate.

    The inputs are explicit user-supplied rates. This arithmetic does not infer an
    oscillator or assess whether any specific logic IC or MCU can implement it.
    """
    source = read_quantity(source_frequency, "frequency", "source clock frequency")
    target = read_quantity(target_frequency, "frequency", "target clock frequency")
    require_positive(source)
    require_positive(target)
    tolerance = _read_tolerance(source_tolerance_ppm)
    source_hz, target_hz = source["si_value"], target["si_value"]
    ratio = source_hz / target_hz
    if not math.isfinite(ratio):
        raise ValueError("Ideal divider ratio is outside the finite numeric range")
    rounded = math.floor(ratio + 0.5)
    modulus = max(1, rounded)
    if modulus > _MAX_EXACT_JSON_INTEGER:
        raise ValueError("Candidate divider modulus exceeds the exact interoperable integer range")

    nominal_output_hz = source_hz / modulus
    tolerance_fraction = tolerance / 1_000_000.0
    minimum_output_hz = source_hz * (1.0 - tolerance_fraction) / modulus
    maximum_output_hz = source_hz * (1.0 + tolerance_fraction) / modulus
    nominal_error_ppm = (nominal_output_hz / target_hz - 1.0) * 1_000_000.0
    minimum_error_ppm = (minimum_output_hz / target_hz - 1.0) * 1_000_000.0
    maximum_error_ppm = (maximum_output_hz / target_hz - 1.0) * 1_000_000.0
    drift_min = (minimum_output_hz / target_hz - 1.0) * _SECONDS_PER_DAY
    drift_max = (maximum_output_hz / target_hz - 1.0) * _SECONDS_PER_DAY
    numeric_results = (nominal_output_hz, minimum_output_hz, maximum_output_hz,
                       nominal_error_ppm, minimum_error_ppm, maximum_error_ppm,
                       drift_min, drift_max)
    if not all(math.isfinite(value) for value in numeric_results):
        raise ValueError("Calculated divider rate is outside the finite numeric range")

    warnings: list[str] = []
    if source_hz < target_hz:
        warnings.append("Integer division cannot increase frequency; modulus 1 is the nearest candidate and remains below the requested target rate.")
    outputs = {
        "ideal_division_ratio": ratio,
        "candidate_divider_modulus": modulus,
        "nominal_output_frequency": make_quantity(nominal_output_hz, "Hz"),
        "minimum_output_frequency": make_quantity(minimum_output_hz, "Hz"),
        "maximum_output_frequency": make_quantity(maximum_output_hz, "Hz"),
        "nominal_rate_error": make_quantity(nominal_error_ppm, "ppm"),
        "minimum_rate_error": make_quantity(minimum_error_ppm, "ppm"),
        "maximum_rate_error": make_quantity(maximum_error_ppm, "ppm"),
        "minimum_clock_drift_per_day": make_quantity(drift_min, "s/day"),
        "maximum_clock_drift_per_day": make_quantity(drift_max, "s/day"),
        "candidate_status": "nearest-integer-modulus; implementation not assessed",
    }
    return result(
        "Integer clock-divider candidate",
        "N = max(1, round(f_source / f_target)); f_out = f_source / N",
        [source, target], outputs,
        substitution=(f"N_ideal = {source_hz:g} Hz / {target_hz:g} Hz = {ratio:.12g}; "
                      f"nearest positive integer N = {modulus}; nominal f_out = {nominal_output_hz:.12g} Hz"),
        intermediates={"source_tolerance_fraction": tolerance_fraction,
                       "seconds_per_day": _SECONDS_PER_DAY},
        assumptions=["The divider is ideal and emits one output event per integer N source events.",
                     "Source tolerance is a symmetric bound around the supplied nominal source frequency.",
                     "Nearest-integer rounding uses half-up behavior; no fractional divider or calibration is modeled."],
        validity=["This is a mathematical divider candidate only; no IC counter width, logic family, timing, reset behavior, or MCU timer limit is checked.",
                  "The target rate is not guaranteed when integer division quantization produces a nonzero rate error."],
        warnings=warnings,
    )
