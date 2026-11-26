"""Compatibility/report adapter for native C++ linear RC/RLC transient analysis.

All numerical state solving is performed in the C++ core via C ABI v1. Python keeps
unit-aware input conversion and report formatting only.
"""
from __future__ import annotations

from typing import Any

from .cpp_engine import simulate_transient_rc as _simulate_transient_rc_cpp
from .quantities import make_quantity
from .results import result


def simulate_transient_rc(netlist: dict[str, Any]) -> dict[str, Any]:
    """Run an explicit bounded linear RC or RLC transient in C++ and format it."""
    native = _simulate_transient_rc_cpp(netlist)
    has_inductors = native["inductor_count"] > 0
    dt = native["time_step"]["value"]
    stop = native["stop_time"]["value"]
    outputs = {
        "method": native["method"],
        "time_points_s": native["time_points_s"],
        "node_voltage_traces_v": native["node_voltage_traces_v"],
        "capacitor_voltage_traces_v": native["capacitor_voltage_traces_v"],
        "inductor_current_traces_a": native["inductor_current_traces_a"],
        "capacitor_count": native["capacitor_count"],
        "inductor_count": native["inductor_count"],
        "time_step": make_quantity(dt, "s"),
        "stop_time": make_quantity(stop, "s"),
        "sample_count": native["sample_count"],
        "status": native["status"],
        "solver_engine": native["engine"],
        "solver_version": native["engine_version"],
        "maximum_equation_residual": native["maximum_equation_residual"],
    }
    step_count = native["step_count"]
    node_count = native["non_ground_node_count"]
    capacitor_count = native["capacitor_count"]
    inductor_count = native["inductor_count"]
    storage_description = "RLC" if has_inductors else "RC"
    warnings = ["Backward Euler is a first-order, numerically dissipative integration method; timestep adequacy and convergence were not assessed.",
                f"This is a linear ideal {storage_description} model with explicit piecewise-constant sources; no nonlinear devices, parasitics, thermal behavior, or component tolerances are included."]
    return result(
        f"Linear {storage_description} transient response (backward Euler)",
        "i_C = C·dv_C/dt; capacitor Norton companion uses C/dt and prior voltage; v_L = L·di_L/dt; inductor Norton companion uses dt/L with prior current",
        native["input_quantities"], outputs,
        substitution=f"C++ backward Euler with Δt={dt:g} s for {step_count} steps through t={stop:g} s; {capacitor_count} capacitor(s), {inductor_count} inductor(s).",
        intermediates={"time_step_s": dt, "stop_time_s": stop, "step_count": step_count,
                       "non_ground_node_count": node_count, "capacitor_count": capacitor_count,
                       "inductor_count": inductor_count,
                       "maximum_equation_residual": native["maximum_equation_residual"]},
        assumptions=["Capacitors use explicitly supplied initial voltage and inductors use explicitly supplied initial current; source steps are ideal and piecewise constant.",
                     "Resistors, inductors, capacitors, and sources are ideal; node voltages are sampled at t=0 and each fixed timestep."],
        validity=["Only ideal resistors, capacitors, inductors, independent DC sources, and single ideal step waveforms are supported.",
                  "Backward Euler can smear fast transitions; repeat with a smaller timestep to assess numerical sensitivity.",
                  "An inconsistent set of ideal capacitor initial voltages, inductor initial currents, and t=0 source constraints is rejected."],
        warnings=warnings,
        scope=f"Numerical time-domain simulation computed by the native C++ core for an explicit ideal linear {storage_description} network using fixed-step backward Euler; not nonlinear/SPICE equivalence, datasheet verification, or design approval.",
    )
