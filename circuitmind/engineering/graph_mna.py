"""Build an MNA netlist from a saved graph plus explicit, checked user mappings."""
from __future__ import annotations

import re
from typing import Any

from ..circuit_schema import validate_model

_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$")


def map_circuit_graph_to_mna(model: dict[str, Any], mapping: dict[str, Any], *, allow_capacitors: bool = False,
                             allow_inductors: bool = False) -> dict[str, Any]:
    """Map an existing graph to a two-terminal linear DC netlist without inference.

    Each circuit component must be explicitly mapped to an R/C/L/V/I/diode element with
    an explicit quantity and ordered pair of declared pins, or explicitly excluded
    with a reason. Capacitors and inductors require their transient opt-ins and initial
    conditions. The function never reads descriptions as values.
    """
    topology = validate_model(model)
    if not topology.get("topology_valid"):
        raise ValueError("Saved circuit model failed schema/topology checks")
    if not isinstance(mapping, dict) or set(mapping) != {"ground_net", "bindings", "exclusions"}:
        raise ValueError("Mapping must contain exactly ground_net, bindings, and exclusions")
    ground_net = mapping.get("ground_net")
    if not isinstance(ground_net, str) or not ground_net:
        raise ValueError("ground_net must name an existing saved net")
    nets = model["nets"]
    net_names = [item["name"] for item in nets]
    if ground_net not in net_names:
        raise ValueError(f"ground_net {ground_net!r} does not exist in the saved circuit model")

    components = {item["reference"]: item for item in model["components"]}
    bindings = mapping.get("bindings")
    exclusions = mapping.get("exclusions")
    if not isinstance(bindings, list) or not isinstance(exclusions, list):
        raise ValueError("bindings and exclusions must be arrays")
    terminal_to_net: dict[tuple[str, str], str] = {}
    for net in nets:
        for terminal in net["terminals"]:
            terminal_to_net[(terminal["reference"], terminal["pin"])] = net["name"]

    accounted: dict[str, str] = {}
    used_net_names = {ground_net}
    elements: list[dict[str, Any]] = []
    resolved_bindings: list[dict[str, Any]] = []
    value_fields = {"resistor": "resistance", "capacitor": "capacitance", "inductor": "inductance",
                    "voltage_source": "voltage", "current_source": "current", "diode": "forward_voltage",
                    "shockley_diode": "saturation_current"}
    reference_prefix = {"resistor": "R", "capacitor": "C", "inductor": "L", "diode": "D", "shockley_diode": "D",
                        "voltage_source": "V", "current_source": "I"}
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict) or not {"reference", "type", "pins", "value"} <= set(binding):
            raise ValueError(f"bindings[{index}] must contain reference, type, pins, and value")
        reference, kind, pins = binding["reference"], binding["type"], binding["pins"]
        expected_binding_fields = {"reference", "type", "pins", "value"}
        if kind == "capacitor" and allow_capacitors:
            expected_binding_fields.add("initial_voltage")
        if kind == "inductor" and allow_inductors:
            expected_binding_fields.add("initial_current")
        if kind == "diode":
            expected_binding_fields.add("off_resistance")
        elif kind == "shockley_diode":
            expected_binding_fields.update({"ideality_factor", "temperature_kelvin"})
        if set(binding) != expected_binding_fields:
            raise ValueError(f"bindings[{index}] has unsupported or missing fields for mapping type {kind!r}")
        if not isinstance(reference, str) or not _REF_RE.fullmatch(reference) or reference not in components:
            raise ValueError(f"bindings[{index}] must reference an existing component")
        if reference in accounted:
            raise ValueError(f"Component {reference} is accounted for more than once")
        if not isinstance(kind, str) or kind not in value_fields:
            raise ValueError(f"{reference} has unsupported mapping type {kind!r}")
        if kind == "capacitor" and not allow_capacitors:
            raise ValueError(f"{reference} is a capacitor; capacitor mappings require an explicit transient workflow")
        if kind == "inductor" and not allow_inductors:
            raise ValueError(f"{reference} is an inductor; inductor mappings require the transient RLC workflow")
        if reference[0].upper() != reference_prefix[kind]:
            raise ValueError(f"{reference} designator prefix does not match the explicitly selected {kind} type")
        component = components[reference]
        declared_pins = component.get("pins", [])
        declared_numbers = {pin["number"] for pin in declared_pins}
        if len(declared_numbers) != 2 or len(declared_pins) != 2:
            raise ValueError(f"{reference} must declare exactly two unique pins for a two-terminal MNA binding")
        if not isinstance(pins, list) or len(pins) != 2 or any(not isinstance(pin, str) for pin in pins) or len(set(pins)) != 2:
            raise ValueError(f"{reference}.pins must specify two distinct declared pin numbers in terminal order")
        if set(pins) != declared_numbers:
            raise ValueError(f"{reference}.pins must map both declared pins exactly once; declared pins are {sorted(declared_numbers)}")
        net_nodes = []
        for pin in pins:
            net_name = terminal_to_net.get((reference, pin))
            if net_name is None:
                raise ValueError(f"{reference}.{pin} is not connected to a saved net")
            used_net_names.add(net_name)
            net_nodes.append("0" if net_name == ground_net else net_node_name(net_names, ground_net, net_name))
        if net_nodes[0] == net_nodes[1]:
            raise ValueError(f"{reference} maps both terminals to the same saved net")
        value_field = value_fields[kind]
        element = {"type": kind, "reference": reference, "nodes": net_nodes}
        if kind == "diode":
            element[value_field] = binding["value"]
            element["off_resistance"] = binding["off_resistance"]
        elif kind == "shockley_diode":
            element[value_field] = binding["value"]
            element["ideality_factor"] = binding["ideality_factor"]
            element["temperature_kelvin"] = binding["temperature_kelvin"]
        elif kind == "capacitor":
            element[value_field] = binding["value"]
            element["initial_voltage"] = binding["initial_voltage"]
        elif kind == "inductor":
            element[value_field] = binding["value"]
            element["initial_current"] = binding["initial_current"]
        elif kind in {"voltage_source", "current_source"} and isinstance(binding["value"], dict) and binding["value"].get("type") == "step":
            element["waveform"] = binding["value"]
        else:
            element[value_field] = binding["value"]
        # Full dimension/unit/value/waveform validation is performed by the selected solver.
        elements.append(element)
        accounted[reference] = "mapped"
        resolved = {"reference": reference, "type": kind, "pins": list(pins),
                    "nodes": net_nodes, "value": binding["value"]}
        if kind == "diode":
            resolved["off_resistance"] = binding["off_resistance"]
        elif kind == "shockley_diode":
            resolved["ideality_factor"] = binding["ideality_factor"]
            resolved["temperature_kelvin"] = binding["temperature_kelvin"]
        elif kind == "capacitor":
            resolved["initial_voltage"] = binding["initial_voltage"]
        elif kind == "inductor":
            resolved["initial_current"] = binding["initial_current"]
        resolved_bindings.append(resolved)

    resolved_exclusions = []
    for index, exclusion in enumerate(exclusions):
        if not isinstance(exclusion, dict) or set(exclusion) != {"reference", "reason"}:
            raise ValueError(f"exclusions[{index}] must contain exactly reference and reason")
        reference, reason = exclusion["reference"], exclusion["reason"]
        if not isinstance(reference, str) or reference not in components:
            raise ValueError(f"exclusions[{index}] must reference an existing component")
        if reference in accounted:
            raise ValueError(f"Component {reference} is accounted for more than once")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError(f"{reference} exclusion needs a non-empty reason (maximum 500 characters)")
        accounted[reference] = "excluded"
        resolved_exclusions.append({"reference": reference, "reason": reason.strip()})

    missing = sorted(set(components) - set(accounted))
    if missing:
        raise ValueError("Every saved component must be explicitly mapped or excluded; unaccounted: " + ", ".join(missing))
    node_map = {name: ("0" if name == ground_net else net_node_name(net_names, ground_net, name))
                for name in sorted(used_net_names)}
    return {
        "netlist": {"ground": "0", "elements": elements},
        "ground_net": ground_net,
        "net_name_to_solver_node": node_map,
        "bindings": resolved_bindings,
        "excluded_components": resolved_exclusions,
        "component_coverage": {"total": len(components), "mapped": len(resolved_bindings),
                               "excluded": len(resolved_exclusions), "all_components_accounted": True,
                               "has_exclusions": bool(resolved_exclusions)},
        "limitations": ["Component type, numeric value, waveform, and initial condition are explicit user inputs; none is inferred from part descriptions.",
                        "Pin order defines source polarity/direction and must be checked by the user.",
                        "Excluded components are omitted from the numerical network; any exclusion makes the model electrically incomplete.",
                        "Graph mapping checks declared pin/net assignments only, not datasheet or symbol-library correctness."],
    }


def net_node_name(net_names: list[str], ground_net: str, net_name: str) -> str:
    """Generate stable, solver-safe node labels independently of user net names."""
    if net_name == ground_net:
        return "0"
    for index, name in enumerate(sorted(name for name in net_names if name != ground_net), 1):
        if name == net_name:
            return f"N{index}"
    raise ValueError(f"Net {net_name!r} does not exist")
