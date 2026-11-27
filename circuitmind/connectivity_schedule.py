"""Build a traceable pin-to-net schedule from declared circuit-graph connectivity."""
from __future__ import annotations

from typing import Any

from .circuit_schema import validate_model


def build_connectivity_schedule(model: dict[str, Any], model_sha256: str) -> dict[str, Any]:
    """Return declared endpoints and unconnected pins without inferring physical wiring."""
    topology = validate_model(model)
    if not topology["topology_valid"]:
        raise ValueError("Circuit model failed schema/topology checks")
    components = {component["reference"]: component for component in model["components"]}
    pin_names = {
        reference: {str(pin["number"]): pin for pin in component.get("pins", [])}
        for reference, component in components.items()
    }
    connected: set[tuple[str, str]] = set()
    nets: list[dict[str, Any]] = []
    for net in model["nets"]:
        endpoints = []
        for terminal in net["terminals"]:
            reference = terminal["reference"]
            number = str(terminal["pin"])
            connected.add((reference, number))
            pin = pin_names.get(reference, {}).get(number, {})
            endpoints.append({
                "reference": reference,
                "pin": number,
                "pin_name": pin.get("name", ""),
                "pin_type": pin.get("type", "unspecified"),
            })
        nets.append({"name": net["name"], "endpoints": endpoints})
    unconnected = []
    no_connect = []
    for reference, component in components.items():
        for pin in component.get("pins", []):
            number = str(pin["number"])
            if (reference, number) not in connected:
                record = {"reference": reference, "pin": number,
                          "pin_name": pin.get("name", ""),
                          "pin_type": pin.get("type", "unspecified")}
                if record["pin_type"] == "no_connect":
                    no_connect.append(record)
                else:
                    unconnected.append(record)
    return {
        "schema_version": 1,
        "circuit_model_sha256": model_sha256,
        "scope": "Declared component-pin-to-net connectivity only. No physical wire routing, wire gauge, connector selection, electrical-rule checking, or design approval is performed.",
        "component_count": len(components),
        "net_count": len(nets),
        "endpoint_count": sum(len(net["endpoints"]) for net in nets),
        "unconnected_pin_count": len(unconnected),
        "no_connect_pin_count": len(no_connect),
        "nets": nets,
        "unconnected_pins": unconnected,
        "no_connect_pins": no_connect,
    }
