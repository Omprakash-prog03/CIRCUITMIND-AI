"""Resistor design functions with explicit worst-case tolerance and rating checks."""
from __future__ import annotations

import math
from typing import Any

from .quantities import make_quantity, quantity, read_quantity, require_positive
from .results import result

_E_VALUES = {
    6: (1.0, 1.5, 2.2, 3.3, 4.7, 6.8),
    12: (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2),
    24: (1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
         3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1),
}


def _finite_real(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        numeric = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} is outside the finite numeric range") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite")
    return numeric


def next_e_series_value(resistance_ohm: float, series: int) -> float:
    if type(series) is not int or series not in _E_VALUES:
        raise ValueError("Supported E-series are E6, E12, and E24")
    if not math.isfinite(resistance_ohm) or resistance_ohm <= 0:
        raise ValueError("Required resistance must be positive and finite")
    exponent = math.floor(math.log10(resistance_ohm))
    for decade in range(exponent - 1, exponent + 2):
        try:
            scale = 10.0 ** decade
        except OverflowError:
            continue
        candidates = [base * scale for base in _E_VALUES[series]]
        for candidate in sorted(value for value in candidates if math.isfinite(value)):
            if candidate >= resistance_ohm:
                return candidate
    raise ValueError("Could not select a standard resistor in the supported numeric range")


def _optional_range(value: Any, nominal: dict[str, Any], dimension: str, name: str) -> dict[str, Any]:
    if value is None:
        return {"min": nominal["si_value"], "max": nominal["si_value"], "input": None}
    if not isinstance(value, dict) or set(value) != {"min", "max"}:
        raise ValueError(f"{name} range must contain exactly min and max quantities")
    minimum = read_quantity(value.get("min"), dimension, f"{name} minimum")
    maximum = read_quantity(value.get("max"), dimension, f"{name} maximum")
    require_positive(minimum, allow_zero=True)
    require_positive(maximum, allow_zero=True)
    if minimum["si_value"] > maximum["si_value"]:
        raise ValueError(f"{name} minimum must not exceed its maximum")
    if not minimum["si_value"] <= nominal["si_value"] <= maximum["si_value"]:
        raise ValueError(f"{name} nominal value must lie within its specified range")
    return {"min": minimum["si_value"], "max": maximum["si_value"], "input": [minimum, maximum]}


def led_series_resistor(inputs: dict[str, Any]) -> dict[str, Any]:
    """Select an E-series resistor and report nominal/worst-case current and power.

    Inputs use {value, unit} quantities. Optional supply_range and led_forward_range
    objects contain {min: quantity, max: quantity}. A power check is only reported
    when the user supplies an actual resistor rating and a derating fraction.
    """
    if not isinstance(inputs, dict):
        raise ValueError("LED resistor inputs must be an object")
    allowed = {"supply", "supply_range", "led_forward", "led_forward_range", "target_current", "maximum_current",
               "resistor_tolerance_percent", "e_series", "resistor_power_rating", "power_derating_fraction"}
    unknown = set(inputs) - allowed
    if unknown:
        raise ValueError("Unsupported LED resistor input fields: " + ", ".join(sorted(map(str, unknown))))
    supply = read_quantity(inputs.get("supply"), "voltage", "supply voltage")
    led_vf = read_quantity(inputs.get("led_forward"), "voltage", "LED forward voltage")
    target = read_quantity(inputs.get("target_current"), "current", "target LED current")
    require_positive(supply, allow_zero=True)
    require_positive(led_vf, allow_zero=True)
    require_positive(target)
    if supply["si_value"] <= led_vf["si_value"]:
        raise ValueError("Nominal supply voltage must exceed nominal LED forward voltage")

    supply_range = _optional_range(inputs.get("supply_range"), supply, "voltage", "supply voltage")
    vf_range = _optional_range(inputs.get("led_forward_range"), led_vf, "voltage", "LED forward voltage")
    if supply_range["min"] <= vf_range["max"]:
        # A valid design may have no current at a low-voltage/high-Vf corner; this is
        # reported as zero worst-case minimum current rather than rejected.
        pass

    tolerance = _finite_real(inputs.get("resistor_tolerance_percent", 5.0), "Resistor tolerance")
    if not 0 <= tolerance < 100:
        raise ValueError("Resistor tolerance must be in [0, 100) percent")
    tolerance = tolerance / 100.0
    series = inputs.get("e_series", 24)
    if type(series) is not int or series not in _E_VALUES:
        raise ValueError("e_series must select one of the supported E6, E12, and E24 series")
    try:
        ideal_resistance = (supply["si_value"] - led_vf["si_value"]) / target["si_value"]
        selected_resistance = next_e_series_value(ideal_resistance, series)
        resistor_min = selected_resistance * (1 - tolerance)
        resistor_max = selected_resistance * (1 + tolerance)
        nominal_current = (supply["si_value"] - led_vf["si_value"]) / selected_resistance
        max_voltage_drop = max(0.0, supply_range["max"] - vf_range["min"])
        min_voltage_drop = max(0.0, supply_range["min"] - vf_range["max"])
        worst_current_max = max_voltage_drop / resistor_min
        worst_current_min = min_voltage_drop / resistor_max
        worst_power = max_voltage_drop**2 / resistor_min
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("LED resistor calculation is outside the finite numeric range") from exc
    for name, value in (("nominal current", nominal_current), ("maximum current", worst_current_max), ("minimum current", worst_current_min), ("resistor power", worst_power)):
        if not math.isfinite(value):
            raise ValueError(f"Calculated {name} is outside the finite numerical range")

    warnings: list[str] = []
    current_limit = inputs.get("maximum_current")
    if current_limit is None:
        current_compliance = "not-assessed"
        warnings.append("No maximum LED-current limit was supplied; worst-case current is informational only.")
        current_limit_output = None
    else:
        current_limit_quantity = read_quantity(current_limit, "current", "maximum LED current")
        require_positive(current_limit_quantity)
        current_compliance = "pass" if worst_current_max <= current_limit_quantity["si_value"] else "fail"
        current_limit_output = make_quantity(current_limit_quantity["si_value"], "A")

    rating_input = inputs.get("resistor_power_rating")
    derating = _finite_real(inputs.get("power_derating_fraction", 0.5), "Power derating fraction")
    if not 0 < derating <= 1:
        raise ValueError("power_derating_fraction must be in (0, 1]")
    if rating_input is None:
        power_compliance = "not-assessed"
        rating_quantity = None
        required_rating = None
        warnings.append("No resistor power rating was supplied; resistor power capability is not assessed.")
    else:
        rating_quantity = read_quantity(rating_input, "power", "resistor power rating")
        require_positive(rating_quantity)
        required_rating = worst_power / float(derating)
        power_compliance = "pass" if rating_quantity["si_value"] >= required_rating else "fail"
    if current_compliance == "fail":
        warnings.append("Worst-case LED current exceeds the supplied maximum-current limit.")
    if power_compliance == "fail":
        warnings.append("Resistor rating is below the required rating at the supplied derating fraction.")
    if "fail" in (current_compliance, power_compliance):
        design_status = "fails-supplied-limits"
    elif "not-assessed" in (current_compliance, power_compliance):
        design_status = "limits-incomplete"
    else:
        design_status = "within-supplied-limits"

    normalized_inputs = [supply, led_vf, target]
    if supply_range["input"]:
        normalized_inputs.extend(supply_range["input"])
    if vf_range["input"]:
        normalized_inputs.extend(vf_range["input"])
    if current_limit is not None:
        normalized_inputs.append(current_limit_quantity)
    if rating_quantity is not None:
        normalized_inputs.append(rating_quantity)
    formulas = (
        "R_ideal = (V_supply,nom - V_F,nom) / I_target",
        "R_selected = smallest supported E-series value ≥ R_ideal",
        "I_min = max(0, (V_supply,min - V_F,max) / (R_selected × (1 + tolerance)))",
        "I_max = max(0, (V_supply,max - V_F,min) / (R_selected × (1 - tolerance)))",
        "P_R,max = max(0, V_supply,max - V_F,min)² / (R_selected × (1 - tolerance))",
        "P_rating,required = P_R,max / derating_fraction",
    )
    outputs = {
        "ideal_resistance": make_quantity(ideal_resistance, "ohm"),
        "selected_resistance": make_quantity(selected_resistance, "ohm"),
        "resistor_minimum": make_quantity(resistor_min, "ohm"),
        "resistor_maximum": make_quantity(resistor_max, "ohm"),
        "nominal_current": make_quantity(nominal_current, "A"),
        "worst_case_minimum_current": make_quantity(worst_current_min, "A"),
        "worst_case_maximum_current": make_quantity(worst_current_max, "A"),
        "maximum_current_limit": current_limit_output,
        "resistor_maximum_power": make_quantity(worst_power, "W"),
        "required_power_rating_at_derating": make_quantity(required_rating, "W") if required_rating is not None else None,
        "supplied_power_rating": make_quantity(rating_quantity["si_value"], "W") if rating_quantity else None,
        "compliance": {"maximum_current": current_compliance, "resistor_power": power_compliance},
        "design_status": design_status,
    }
    substitution = (f"R_ideal = ({supply['si_value']:g} V - {led_vf['si_value']:g} V) / "
                    f"{target['si_value']:g} A = {ideal_resistance:g} ohm; E{series} selects {selected_resistance:g} ohm.")
    return result("LED series-resistor design", "; ".join(formulas), normalized_inputs, outputs,
                  substitution=substitution,
                  intermediates={"resistor_tolerance_fraction": tolerance,
                                 "resistor_minimum_ohm": resistor_min, "resistor_maximum_ohm": resistor_max,
                                 "maximum_resistor_voltage_v": max_voltage_drop,
                                 "power_derating_fraction": float(derating)},
                  assumptions=["DC steady state; LED is modeled by constant forward-voltage bounds.",
                               "Resistor tolerance is symmetric about the selected nominal value.",
                               "Supply range and LED Vf range are independent worst-case corners.",
                               "Power rating comparison uses the user-supplied derating fraction."],
                  validity=["This calculation does not model driver output resistance, LED I-V curvature, thermal coupling, transient pulses, or PCB temperature rise.",
                            "Check the selected LED's actual datasheet limits and the driver output-current capability."],
                  warnings=warnings)
