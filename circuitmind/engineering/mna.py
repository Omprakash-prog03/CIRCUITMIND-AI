"""Small, bounded linear-DC modified-nodal-analysis solver.

This solves user-supplied ideal R/V/I networks. It is not a SPICE parser, nonlinear
solver, transient simulator, or converter from the saved circuit graph.
"""
from __future__ import annotations

import math
import re
from typing import Any

from .quantities import make_quantity, read_quantity, require_positive
from .results import result

_NODE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,31}$")
_REFERENCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$")
_MAX_ELEMENTS = 500
_MAX_NODES = 150
_MAX_MNA_DIMENSION = 180
_PIVOT_FLOOR = 1e-12


def _finite(value: float, what: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{what} is outside the finite numerical range")
    return value


def _safe_fsum(values: Any, what: str) -> float:
    try:
        total = math.fsum(values)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{what} is outside the finite numerical range") from exc
    return _finite(total, what)


def _solve_linear(matrix: list[list[float]], rhs: list[float]) -> tuple[list[float], float, float]:
    """Solve a scaled dense system with partial pivoting; reject weak pivots."""
    size = len(rhs)
    if size == 0 or len(matrix) != size or any(len(row) != size for row in matrix):
        raise ValueError("Internal MNA system has invalid dimensions")
    augmented = [row[:] + [rhs[index]] for index, row in enumerate(matrix)]
    for row_index, row in enumerate(augmented):
        scale = max(abs(value) for value in row[:-1])
        if not math.isfinite(scale) or scale == 0:
            raise ValueError("Circuit matrix is singular: an equation has no effective coefficients")
        augmented[row_index] = [value / scale for value in row]
        _finite(augmented[row_index][-1], "Scaled circuit right-hand side")

    pivots: list[float] = []
    for column in range(size):
        pivot_row = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        pivot = abs(augmented[pivot_row][column])
        if not math.isfinite(pivot) or pivot < _PIVOT_FLOOR:
            raise ValueError("Circuit matrix is singular or numerically ill-conditioned; no operating point reported")
        if pivot_row != column:
            augmented[column], augmented[pivot_row] = augmented[pivot_row], augmented[column]
        divisor = augmented[column][column]
        pivots.append(abs(divisor))
        for row in range(column + 1, size):
            factor = augmented[row][column] / divisor
            if factor == 0:
                continue
            augmented[row][column] = 0.0
            for entry in range(column + 1, size + 1):
                augmented[row][entry] -= factor * augmented[column][entry]
                _finite(augmented[row][entry], "Elimination intermediate")

    solution = [0.0] * size
    for row in range(size - 1, -1, -1):
        remainder = augmented[row][-1] - _safe_fsum((augmented[row][column] * solution[column]
                                                       for column in range(row + 1, size)), "Back-substitution sum")
        solution[row] = _finite(remainder / augmented[row][row], "Solved circuit variable")
    return solution, min(pivots), max(pivots)


def solve_dc_operating_point(netlist: dict[str, Any]) -> dict[str, Any]:
    """Solve an explicitly supplied ideal resistor and independent-source network.

    Shape::
        {"ground": "0", "elements": [
          {"type":"resistor", "reference":"R1", "nodes":["VIN","0"],
           "resistance":{"value":1000,"unit":"ohm"}},
          {"type":"voltage_source", "reference":"V1", "nodes":["VIN","0"],
           "voltage":{"value":5,"unit":"V"}},
          {"type":"current_source", "reference":"I1", "nodes":["N1","0"],
           "current":{"value":1,"unit":"mA"}}
        ]}

    Source direction is from nodes[0] to nodes[1]. Voltage source voltage is
    V(nodes[0]) - V(nodes[1]); source power is positive when absorbed.
    """
    if not isinstance(netlist, dict) or set(netlist) != {"ground", "elements"}:
        raise ValueError("MNA input must contain exactly 'ground' and 'elements'")
    ground = netlist.get("ground")
    if not isinstance(ground, str) or not _NODE_RE.fullmatch(ground):
        raise ValueError("ground must be a valid node label")
    elements = netlist.get("elements")
    if not isinstance(elements, list) or not elements or len(elements) > _MAX_ELEMENTS:
        raise ValueError(f"elements must contain between 1 and {_MAX_ELEMENTS} supported elements")

    parsed: list[dict[str, Any]] = []
    nodes: set[str] = set()
    references: set[str] = set()
    input_quantities: list[dict[str, Any]] = []
    allowed_keys = {
        "resistor": {"type", "reference", "nodes", "resistance"},
        "voltage_source": {"type", "reference", "nodes", "voltage"},
        "current_source": {"type", "reference", "nodes", "current"},
    }
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            raise ValueError(f"elements[{index}] must be an object")
        kind = element.get("type")
        if not isinstance(kind, str) or kind not in allowed_keys:
            raise ValueError(f"elements[{index}] has unsupported type {kind!r}; only resistor, voltage_source, and current_source are supported")
        if set(element) != allowed_keys[kind]:
            raise ValueError(f"elements[{index}] must contain exactly {', '.join(sorted(allowed_keys[kind]))}")
        reference = element.get("reference")
        if not isinstance(reference, str) or not _REFERENCE_RE.fullmatch(reference) or reference in references:
            raise ValueError(f"elements[{index}] needs a unique simple reference designator")
        references.add(reference)
        terminals = element.get("nodes")
        if not isinstance(terminals, list) or len(terminals) != 2:
            raise ValueError(f"{reference}.nodes must contain exactly two node labels")
        if any(not isinstance(node, str) or not _NODE_RE.fullmatch(node) for node in terminals):
            raise ValueError(f"{reference}.nodes contains an invalid node label")
        first, second = terminals
        if first == second:
            raise ValueError(f"{reference} cannot connect both terminals to the same node")
        nodes.update(terminals)
        field, dimension = {"resistor": ("resistance", "resistance"),
                            "voltage_source": ("voltage", "voltage"),
                            "current_source": ("current", "current")}[kind]
        item_quantity = read_quantity(element.get(field), dimension, f"{reference} {field}")
        if kind == "resistor":
            require_positive(item_quantity)
        input_quantities.append({**item_quantity, "reference": reference, "element_type": kind})
        parsed.append({"reference": reference, "type": kind, "nodes": (first, second),
                       "value_si": item_quantity["si_value"], "si_unit": item_quantity["si_unit"]})

    if ground not in nodes:
        raise ValueError("The declared ground node must appear in at least one element")
    unknown_nodes = sorted(nodes - {ground})
    if len(unknown_nodes) > _MAX_NODES:
        raise ValueError(f"network exceeds the {_MAX_NODES}-unknown-node limit")
    node_index = {node: index for index, node in enumerate(unknown_nodes)}
    voltage_sources = [item for item in parsed if item["type"] == "voltage_source"]
    size = len(unknown_nodes) + len(voltage_sources)
    if size == 0:
        raise ValueError("Network has no non-ground unknowns")
    if size > _MAX_MNA_DIMENSION:
        raise ValueError(f"network exceeds the {_MAX_MNA_DIMENSION}-unknown dense-solver limit")
    voltage_source_index = {item["reference"]: index for index, item in enumerate(voltage_sources)}
    matrix = [[0.0] * size for _ in range(size)]
    rhs = [0.0] * size

    for item in parsed:
        first, second = item["nodes"]
        kind, value = item["type"], item["value_si"]
        if kind == "resistor":
            conductance = _finite(1.0 / value, f"{item['reference']} conductance")
            for node in (first, second):
                if node != ground:
                    matrix[node_index[node]][node_index[node]] += conductance
                    _finite(matrix[node_index[node]][node_index[node]], "MNA conductance sum")
            if first != ground and second != ground:
                matrix[node_index[first]][node_index[second]] -= conductance
                matrix[node_index[second]][node_index[first]] -= conductance
                _finite(matrix[node_index[first]][node_index[second]], "MNA conductance sum")
                _finite(matrix[node_index[second]][node_index[first]], "MNA conductance sum")
        elif kind == "current_source":
            # RHS is current injected into a node; source current flows first -> second.
            if first != ground:
                rhs[node_index[first]] -= value
                _finite(rhs[node_index[first]], "MNA current-source sum")
            if second != ground:
                rhs[node_index[second]] += value
                _finite(rhs[node_index[second]], "MNA current-source sum")
        else:
            branch = len(unknown_nodes) + voltage_source_index[item["reference"]]
            for node, sign in ((first, 1.0), (second, -1.0)):
                if node != ground:
                    node_col = node_index[node]
                    matrix[node_col][branch] += sign
                    matrix[branch][node_col] += sign
            rhs[branch] = value

    solution, minimum_pivot, maximum_pivot = _solve_linear(matrix, rhs)
    voltages = {ground: 0.0, **{node: solution[index] for node, index in node_index.items()}}
    branch_currents = {item["reference"]: solution[len(unknown_nodes) + index]
                       for index, item in enumerate(voltage_sources)}

    element_outputs = []
    powers: list[float] = []
    for item in parsed:
        first, second = item["nodes"]
        voltage = voltages[first] - voltages[second]
        if item["type"] == "resistor":
            current = voltage / item["value_si"]
        elif item["type"] == "voltage_source":
            current = branch_currents[item["reference"]]
        else:
            current = item["value_si"]
        power = _finite(voltage * current, f"{item['reference']} power")
        powers.append(power)
        element_outputs.append({"reference": item["reference"], "type": item["type"],
                                "voltage": make_quantity(voltage, "V"),
                                "current": make_quantity(current, "A"),
                                "power_absorbed": make_quantity(power, "W")})

    residuals = []
    for coefficients, expected in zip(matrix, rhs):
        actual = _safe_fsum((coefficient * variable for coefficient, variable in zip(coefficients, solution)), "MNA equation sum")
        residuals.append(abs(actual - expected))
    node_equation_count = len(unknown_nodes)
    max_kcl_residual = _finite(max(residuals[:node_equation_count], default=0.0), "MNA KCL residual")
    max_voltage_residual = _finite(max(residuals[node_equation_count:], default=0.0), "MNA voltage-source residual")
    normalized_residual = max((residual / max(1e-30, abs(expected), _safe_fsum((abs(c * x) for c, x in zip(coefficients, solution)), "MNA residual scale"))
                               for residual, coefficients, expected in zip(residuals, matrix, rhs)), default=0.0)
    _finite(normalized_residual, "Normalized MNA residual")
    power_balance = _safe_fsum(powers, "Circuit power balance")
    warnings = ["This is a linear DC operating-point solve; it is not transient, nonlinear, thermal, or a SPICE execution.",
                "The netlist is explicit user input and was not generated from or checked against the saved circuit graph."]
    if minimum_pivot / maximum_pivot < 1e-8:
        warnings.append("Scaled pivot spread is large; numerical conditioning may limit significant digits.")
    outputs = {
        "node_voltages": {node: make_quantity(voltage, "V") for node, voltage in sorted(voltages.items())},
        "elements": element_outputs,
        "maximum_kcl_residual": make_quantity(max_kcl_residual, "A"),
        "maximum_voltage_source_residual": make_quantity(max_voltage_residual, "V"),
        "normalized_equation_residual": normalized_residual,
        "total_power_balance": make_quantity(power_balance, "W"),
        "solver_status": "solved-linear-dc-model",
    }
    return result(
        "Linear DC modified-nodal-analysis operating point",
        "G·v + B·i = j; Bᵀ·v = e",
        input_quantities, outputs,
        substitution=f"Solved {size} MNA unknowns ({len(unknown_nodes)} node voltages, {len(voltage_sources)} voltage-source branch currents).",
        intermediates={"unknown_node_count": len(unknown_nodes), "voltage_source_count": len(voltage_sources),
                       "matrix_dimension": size, "minimum_scaled_pivot": minimum_pivot,
                       "maximum_scaled_pivot": maximum_pivot},
        assumptions=["Ideal linear resistors and independent DC sources; ground is explicitly specified.",
                     "Current flows from each source's first listed terminal to its second; voltage-source value is the first-terminal voltage minus the second-terminal voltage.",
                     "Positive element power means absorbed; negative source power means delivered."],
        validity=["Only independent resistors, voltage sources, and current sources are supported.",
                  "Capacitors/inductors, dependent sources, nonlinear devices, parasitics, tolerances, and transient behavior are not modeled.",
                  "Dense solve is limited to 150 non-ground nodes and 180 total MNA unknowns; singular or weak-pivot systems are rejected."],
        warnings=warnings,
        scope="Numerical linear DC operating-point solution of the explicit ideal R/V/I netlist; not a transient or nonlinear simulation, datasheet verification, or design approval.",
    )
