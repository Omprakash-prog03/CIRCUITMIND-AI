"""Compatibility/report adapter for native C++ linear RC transient analysis.

All numerical state solving is performed in the C++ core via C ABI v1. Python keeps
unit-aware input conversion and the established report contract only.
"""
from __future__ import annotations

from typing import Any

from .cpp_engine import simulate_transient_rc as _simulate_transient_rc_cpp
from .quantities import make_quantity
from .results import result


def simulate_transient_rc(netlist: dict[str, Any]) -> dict[str, Any]:
    """Run an explicit bounded RC transient in C++ and format its report."""
    native = _simulate_transient_rc_cpp(netlist)
    dt = native["time_step"]["value"]
    stop = native["stop_time"]["value"]
    outputs = {
        "method": native["method"],
        "time_points_s": native["time_points_s"],
        "node_voltage_traces_v": native["node_voltage_traces_v"],
        "capacitor_voltage_traces_v": native["capacitor_voltage_traces_v"],
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
    warnings = ["Backward Euler is a first-order, numerically dissipative integration method; timestep adequacy and convergence were not assessed.",
                "This is a linear ideal RC model with explicit piecewise-constant sources; no nonlinear devices, parasitics, thermal behavior, or component tolerances are included."]
    return result(
        "Linear RC transient response (backward Euler)",
        "i_C = C·dv_C/dt; backward-Euler companion: G_C = C/dt with history source I_eq = -(C/dt)·v_C,prev",
        native["input_quantities"], outputs,
        substitution=f"C++ backward Euler with Δt={dt:g} s for {step_count} steps through t={stop:g} s; {capacitor_count} capacitor(s).",
        intermediates={"time_step_s": dt, "stop_time_s": stop, "step_count": step_count,
                       "non_ground_node_count": node_count, "capacitor_count": capacitor_count,
                       "maximum_equation_residual": native["maximum_equation_residual"]},
        assumptions=["Capacitors use explicitly supplied initial voltage; source steps are ideal and piecewise constant.",
                     "Resistors and sources are ideal; node voltages are sampled at t=0 and each fixed timestep."],
        validity=["Only resistors, capacitors, independent DC sources, and single ideal step waveforms are supported.",
                  "Backward Euler can smear fast transitions; repeat with a smaller timestep to assess numerical sensitivity.",
                  "An inconsistent set of ideal capacitor initial voltages and t=0 sources is rejected."],
        warnings=warnings,
        scope="Numerical time-domain simulation computed by the native C++ core for an explicit ideal linear RC network using fixed-step backward Euler; not nonlinear/SPICE equivalence, datasheet verification, or design approval.",
    )
