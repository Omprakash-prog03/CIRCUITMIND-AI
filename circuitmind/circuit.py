"""Deterministic operations and graph checks for the versioned circuit model.

These helpers validate topology only: they do not infer electrical compatibility,
part pinouts, ERC, or a schematic drawing.
"""
from __future__ import annotations

from typing import Any

from .models import CircuitModel


def create_component(model: CircuitModel, reference: str, part: str, pins: dict[str, str]) -> dict[str, Any]:
    if not reference.strip() or not part.strip() or not pins:
        raise ValueError("A component requires a reference, part description, and at least one pin")
    if any(item.get("reference") == reference for item in model.components):
        raise ValueError(f"Duplicate component reference: {reference}")
    normalized_pins = {str(number): str(name) for number, name in pins.items()}
    if len(normalized_pins) != len(pins) or any(not key.strip() or not value.strip() for key, value in normalized_pins.items()):
        raise ValueError("Pin numbers and names must be non-empty and unique")
    component = {"reference": reference, "part": part, "pins": normalized_pins}
    model.components.append(component)
    if model.status == "requirements-only":
        model.status = "connectivity-draft"
    return component


def create_net(model: CircuitModel, name: str) -> dict[str, Any]:
    if not name.strip():
        raise ValueError("Net name is required")
    if any(net.get("name") == name for net in model.nets):
        raise ValueError(f"Duplicate net name: {name}")
    net = {"name": name, "terminals": []}
    model.nets.append(net)
    return net


def connect_pins(model: CircuitModel, net_name: str, reference: str, pin_number: str) -> None:
    net = next((item for item in model.nets if item.get("name") == net_name), None)
    component = next((item for item in model.components if item.get("reference") == reference), None)
    if net is None:
        raise ValueError(f"Unknown net: {net_name}")
    if component is None:
        raise ValueError(f"Unknown component: {reference}")
    pin = str(pin_number)
    if pin not in component.get("pins", {}):
        raise ValueError(f"Unknown pin {pin} on {reference}")
    endpoint = {"reference": reference, "pin": pin}
    if endpoint in net["terminals"]:
        raise ValueError(f"Pin {reference}.{pin} is already connected to {net_name}")
    net["terminals"].append(endpoint)


def validate_connectivity(model: CircuitModel) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    references: dict[str, dict[str, Any]] = {}
    for component in model.components:
        ref = component.get("reference")
        if not ref:
            errors.append("Component has no reference designator")
        elif ref in references:
            errors.append(f"Duplicate component reference: {ref}")
        else:
            references[ref] = component
    net_names: set[str] = set()
    pin_to_net: dict[tuple[str, str], str] = {}
    connected_pins: set[tuple[str, str]] = set()
    for net in model.nets:
        name = net.get("name")
        if not name:
            errors.append("Net has no name")
        elif name in net_names:
            errors.append(f"Duplicate net name: {name}")
        else:
            net_names.add(name)
        terminals = net.get("terminals", [])
        if len(terminals) < 2:
            warnings.append(f"Net {name or '<unnamed>'} has fewer than two terminals")
        seen_here: set[tuple[str, str]] = set()
        for endpoint in terminals:
            ref, pin = endpoint.get("reference"), str(endpoint.get("pin", ""))
            component = references.get(ref)
            if component is None:
                errors.append(f"Net {name or '<unnamed>'} refers to unknown component {ref}")
                continue
            if pin not in component.get("pins", {}):
                errors.append(f"Net {name or '<unnamed>'} refers to unknown pin {ref}.{pin}")
                continue
            key = (ref, pin)
            if key in seen_here:
                errors.append(f"Pin {ref}.{pin} appears more than once on net {name}")
            if key in pin_to_net and pin_to_net[key] != name:
                errors.append(f"Pin {ref}.{pin} is assigned to multiple nets: {pin_to_net[key]} and {name}")
            pin_to_net[key] = name
            seen_here.add(key)
            connected_pins.add(key)
    for ref, component in references.items():
        for pin in component.get("pins", {}):
            if (ref, str(pin)) not in connected_pins:
                warnings.append(f"Unconnected pin {ref}.{pin}")
    return {"topology_valid": not errors, "errors": errors, "warnings": warnings,
            "component_count": len(references), "net_count": len(model.nets),
            "connected_pin_count": len(connected_pins),
            "scope": "Graph/reference consistency only. Electrical rules, pinout truth, power integrity, and ERC are not checked."}
