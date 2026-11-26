"""Map a saved circuit graph to AC analysis only through explicit user bindings."""
from __future__ import annotations

import re
from typing import Any

from ..circuit_schema import validate_model
from .graph_mna import net_node_name

_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$")


def map_circuit_graph_to_ac(model: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    """Resolve graph connectivity and explicit R/L/C/AC-source values for a sweep."""
    topology = validate_model(model)
    if not topology.get("topology_valid"):
        raise ValueError("Saved circuit model failed schema/topology checks")
    if not isinstance(mapping, dict) or set(mapping) != {"ground_net", "bindings", "exclusions"}:
        raise ValueError("AC mapping must contain exactly ground_net, bindings, and exclusions")
    ground_net = mapping["ground_net"]
    if not isinstance(ground_net, str) or ground_net not in {item["name"] for item in model["nets"]}:
        raise ValueError("ground_net must name an existing saved net")
    components = {item["reference"]: item for item in model["components"]}
    terminal_to_net = {(terminal["reference"], terminal["pin"]): net["name"]
                       for net in model["nets"] for terminal in net["terminals"]}
    bindings, exclusions = mapping["bindings"], mapping["exclusions"]
    if not isinstance(bindings, list) or not isinstance(exclusions, list):
        raise ValueError("AC bindings and exclusions must be arrays")

    value_fields = {"resistor": "resistance", "capacitor": "capacitance", "inductor": "inductance",
                    "voltage_source": "voltage", "current_source": "current"}
    prefixes = {"resistor": "R", "capacitor": "C", "inductor": "L", "voltage_source": "V", "current_source": "I"}
    accounted: set[str] = set()
    used_nets = {ground_net}
    elements: list[dict[str, Any]] = []
    resolved_bindings: list[dict[str, Any]] = []
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            raise ValueError(f"bindings[{index}] must be an object")
        kind = binding.get("type")
        if not isinstance(kind, str) or kind not in value_fields:
            raise ValueError(f"bindings[{index}] has unsupported AC type {kind!r}")
        expected = {"reference", "type", "pins", "value", "phase_degrees"} if kind in {"voltage_source", "current_source"} else {"reference", "type", "pins", "value"}
        if set(binding) != expected:
            raise ValueError(f"bindings[{index}] fields do not match the selected AC element type")
        reference = binding["reference"]
        if not isinstance(reference, str) or not _REF_RE.fullmatch(reference) or reference not in components:
            raise ValueError(f"bindings[{index}] must reference an existing component")
        if reference in accounted:
            raise ValueError(f"Component {reference} is accounted for more than once")
        if reference[0].upper() != prefixes[kind]:
            raise ValueError(f"{reference} designator prefix does not match the explicitly selected {kind} type")
        declared_pins = components[reference].get("pins", [])
        pin_numbers = {pin["number"] for pin in declared_pins}
        pins = binding["pins"]
        if len(declared_pins) != 2 or len(pin_numbers) != 2:
            raise ValueError(f"{reference} must declare exactly two unique pins for a two-terminal AC binding")
        if not isinstance(pins, list) or len(pins) != 2 or any(not isinstance(pin, str) for pin in pins) or len(set(pins)) != 2 or set(pins) != pin_numbers:
            raise ValueError(f"{reference}.pins must map both declared pins exactly once in terminal order")
        net_names = [terminal_to_net.get((reference, pin)) for pin in pins]
        if any(name is None for name in net_names):
            raise ValueError(f"{reference} has an unconnected mapped pin")
        if net_names[0] == net_names[1]:
            raise ValueError(f"{reference} maps both terminals to the same saved net")
        used_nets.update(net_names)
        nodes = ["0" if name == ground_net else net_node_name([item["name"] for item in model["nets"]], ground_net, name)
                 for name in net_names]
        element = {"type": kind, "reference": reference, "nodes": nodes,
                   value_fields[kind]: binding["value"]}
        resolved = {"reference": reference, "type": kind, "pins": list(pins), "nodes": nodes,
                    "value": binding["value"]}
        if kind in {"voltage_source", "current_source"}:
            element["phase_degrees"] = binding["phase_degrees"]
            resolved["phase_degrees"] = binding["phase_degrees"]
        elements.append(element)
        resolved_bindings.append(resolved)
        accounted.add(reference)

    resolved_exclusions = []
    for index, exclusion in enumerate(exclusions):
        if not isinstance(exclusion, dict) or set(exclusion) != {"reference", "reason"}:
            raise ValueError(f"exclusions[{index}] must contain exactly reference and reason")
        reference, reason = exclusion["reference"], exclusion["reason"]
        if not isinstance(reference, str) or reference not in components or reference in accounted:
            raise ValueError(f"exclusions[{index}] must reference an existing, not-yet-accounted component")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ValueError(f"{reference} exclusion needs a non-empty reason (maximum 500 characters)")
        accounted.add(reference)
        resolved_exclusions.append({"reference": reference, "reason": reason.strip()})

    missing = sorted(set(components) - accounted)
    if missing:
        raise ValueError("Every saved component must be explicitly mapped or excluded; unaccounted: " + ", ".join(missing))
    all_net_names = [item["name"] for item in model["nets"]]
    return {
        "netlist": {"ground": "0", "elements": elements},
        "ground_net": ground_net,
        "net_name_to_solver_node": {name: ("0" if name == ground_net else net_node_name(all_net_names, ground_net, name))
                                    for name in sorted(used_nets)},
        "bindings": resolved_bindings,
        "excluded_components": resolved_exclusions,
        "component_coverage": {"total": len(components), "mapped": len(resolved_bindings),
                               "excluded": len(resolved_exclusions), "all_components_accounted": True,
                               "has_exclusions": bool(resolved_exclusions)},
        "limitations": ["Connectivity comes from the saved graph; every electrical type, value, source magnitude, phase, and terminal order is explicitly user supplied.",
                        "Excluded components are omitted from the AC network; exclusions can make it electrically incomplete.",
                        "This mapping validates declared graph topology, not electrical pin function, datasheet values, or symbol-library correctness."],
    }
