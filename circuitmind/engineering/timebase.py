"""Timebase rate-error and accumulated clock-drift calculations."""
from __future__ import annotations

import math
from typing import Any

from .quantities import make_quantity, read_quantity, require_positive
from .results import result


_SECONDS_PER_DAY = 86_400.0


def clock_timebase_error(nominal_tick_rate: dict[str, Any], target_tick_rate: dict[str, Any],
                         tolerance_ppm: float) -> dict[str, Any]:
    """Bound steady clock-rate error from explicit post-divider rate inputs.

    ``tolerance_ppm`` is a symmetric bound relative to the supplied nominal tick
    rate. It is not inferred from a crystal, oscillator, MCU, temperature, or circuit.
    """
    nominal = read_quantity(nominal_tick_rate, "frequency", "nominal clock tick rate")
    target = read_quantity(target_tick_rate, "frequency", "target clock tick rate")
    require_positive(nominal)
    require_positive(target)
    if isinstance(tolerance_ppm, bool) or not isinstance(tolerance_ppm, (int, float)):
        raise ValueError("Frequency tolerance must be a finite nonnegative number of ppm")
    try:
        tolerance = float(tolerance_ppm)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Frequency tolerance is outside the finite numeric range") from exc
    if not math.isfinite(tolerance) or not 0 <= tolerance < 1_000_000:
        raise ValueError("Frequency tolerance must be finite and in [0, 1,000,000) ppm")

    nominal_hz, target_hz = nominal["si_value"], target["si_value"]
    fractional_tolerance = tolerance / 1_000_000.0
    minimum_hz = nominal_hz * (1.0 - fractional_tolerance)
    maximum_hz = nominal_hz * (1.0 + fractional_tolerance)
    nominal_error_ppm = (nominal_hz / target_hz - 1.0) * 1_000_000.0
    minimum_error_ppm = (minimum_hz / target_hz - 1.0) * 1_000_000.0
    maximum_error_ppm = (maximum_hz / target_hz - 1.0) * 1_000_000.0
    drift_low = (minimum_hz / target_hz - 1.0) * _SECONDS_PER_DAY
    drift_high = (maximum_hz / target_hz - 1.0) * _SECONDS_PER_DAY
    values = (minimum_hz, maximum_hz, nominal_error_ppm, minimum_error_ppm,
              maximum_error_ppm, drift_low, drift_high)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Calculated clock-rate error is outside the finite numeric range")

    outputs = {
        "nominal_rate_error": make_quantity(nominal_error_ppm, "ppm"),
        "minimum_actual_tick_rate": make_quantity(minimum_hz, "Hz"),
        "maximum_actual_tick_rate": make_quantity(maximum_hz, "Hz"),
        "minimum_rate_error": make_quantity(minimum_error_ppm, "ppm"),
        "maximum_rate_error": make_quantity(maximum_error_ppm, "ppm"),
        "minimum_clock_drift_per_day": make_quantity(drift_low, "s/day"),
        "maximum_clock_drift_per_day": make_quantity(drift_high, "s/day"),
        "minimum_clock_drift_per_30_days": make_quantity(drift_low * 30.0, "s/30 days"),
        "maximum_clock_drift_per_30_days": make_quantity(drift_high * 30.0, "s/30 days"),
        "minimum_clock_drift_per_365_25_days": make_quantity(drift_low * 365.25, "s/365.25 days"),
        "maximum_clock_drift_per_365_25_days": make_quantity(drift_high * 365.25, "s/365.25 days"),
    }
    return result(
        "Clock timebase rate-error budget",
        "f_actual ∈ [f_nom(1 - tolerance_ppm/1e6), f_nom(1 + tolerance_ppm/1e6)]; drift/day = (f_actual/f_target - 1) × 86400 s",
        [nominal, target], outputs,
        substitution=(f"f_nom = {nominal_hz:g} Hz; f_target = {target_hz:g} Hz; "
                      f"tolerance = ±{tolerance:g} ppm"),
        intermediates={"tolerance_fraction": fractional_tolerance,
                       "seconds_per_day": _SECONDS_PER_DAY},
        assumptions=["Rates are steady and the tolerance is a symmetric bound around the user-entered nominal post-divider tick rate.",
                     "Accumulated drift is linear with elapsed time; rate changes, missed ticks, and correction algorithms are excluded."],
        validity=["Enter the actual post-divider clock tick rate and target rate; this does not derive a divider from an oscillator.",
                  "Tolerance must come from an explicit source; no part specifications are inferred."],
        warnings=["This is a mathematical rate-error budget, not an oscillator specification, digital simulation, or timekeeping accuracy guarantee."],
    )
