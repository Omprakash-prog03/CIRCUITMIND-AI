"""Versioned report adapter for bounded native linear AC frequency sweeps."""
from __future__ import annotations

from typing import Any

from .cpp_engine import simulate_ac_sweep as _simulate_ac_sweep_cpp
from .quantities import make_quantity
from .results import result


def simulate_ac_sweep(netlist: dict[str, Any]) -> dict[str, Any]:
    """Run a bounded explicit R/C AC frequency sweep using the C++ core."""
    native = _simulate_ac_sweep_cpp(netlist)
    sweep = netlist["frequency_sweep"]
    outputs = {
        "status": native["status"],
        "solver_engine": native["engine"],
        "solver_version": native["engine_version"],
        "scale": native["scale"],
        "frequency_points_hz": native["frequency_points_hz"],
        "frequency_point_count": len(native["frequency_points_hz"]),
        "start_frequency": make_quantity(native["frequency_points_hz"][0], "Hz"),
        "stop_frequency": make_quantity(native["frequency_points_hz"][-1], "Hz"),
        "node_voltage_real_traces_v": native["node_voltage_real_traces_v"],
        "node_voltage_imag_traces_v": native["node_voltage_imag_traces_v"],
        "node_voltage_magnitude_traces_v": native["node_voltage_magnitude_traces_v"],
        "node_voltage_phase_traces_deg": native["node_voltage_phase_traces_deg"],
        "normalized_equation_residuals": native["normalized_equation_residuals"],
        "maximum_normalized_equation_residual": native["maximum_normalized_equation_residual"],
        "unknown_count": native["unknown_count"],
        "non_ground_node_count": native["non_ground_node_count"],
        "element_count": native["element_count"],
    }
    warnings = [
        "This is a bounded linear frequency-domain R/C analysis using ideal sinusoidal RMS phasors; it is not nonlinear or transient simulation and is not an ngspice run.",
        "Element values are treated as frequency-independent ideal R, L, and C values; parasitics, tolerances, noise, and component qualification are not modeled.",
    ]
    return result(
        "Linear R/C AC frequency sweep",
        "Y(ω)·V(ω) = I(ω), with Y_C = jωC, Y_L = 1/(jωL), and explicit independent-source phasors",
        native["input_quantities"], outputs,
        substitution=(f"Native C++ complex MNA at {len(native['frequency_points_hz'])} {native['scale']} spaced frequencies; "
                      f"{native['unknown_count']} total unknowns and {native['non_ground_node_count']} non-ground nodes."),
        intermediates={"frequency_count": len(native["frequency_points_hz"]),
                       "start_frequency_hz": native["frequency_points_hz"][0],
                       "stop_frequency_hz": native["frequency_points_hz"][-1],
                       "unknown_count": native["unknown_count"],
                       "maximum_normalized_equation_residual": native["maximum_normalized_equation_residual"]},
        assumptions=["Independent AC source values are RMS magnitudes with explicit phase_degrees; positive phase follows the e^(jωt) convention.",
                     "All component connectivity, values, and source phases are explicit inputs; nothing is inferred from descriptions or the saved graph."],
        validity=["Only ideal resistors, capacitors, inductors, independent voltage sources, and independent current sources are supported.",
                  "Sweep has 2–200 strictly increasing positive frequency points, at most 200 elements, 64 non-ground nodes, and 80 total MNA unknowns.",
                  "Singular or weak-pivot systems are rejected; residuals are normalized algebraic equation residuals, not model-accuracy estimates."],
        warnings=warnings,
        scope="Bounded linear R/L/C frequency-domain response computed by the native C++ core; not a general SPICE substitute, nonlinear analysis, datasheet verification, or design approval.",
    )
