"""Versioned circuit-model validation, migration, and KiCad XML netlist export.

The KiCad adapter emits an XML netlist candidate, never a fabricated schematic.
Electrical-rule checks remain the responsibility of KiCad/ERC and verified libraries.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from typing import Any

CURRENT_SCHEMA_VERSION = 2
_ALLOWED_PIN_TYPES = {"input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified", "power_in", "power_out", "open_collector", "open_emitter", "no_connect"}
_ALLOWED_STATUSES = {"requirements-only", "draft", "connectivity-draft"}
_REF_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*[0-9]+[A-Za-z]?$")
_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9_./+-]+$")


def new_circuit_model() -> dict[str, Any]:
    return {"schema_version": CURRENT_SCHEMA_VERSION,
            "metadata": {"units": "mm", "grid_mm": 1.27},
            "status": "requirements-only",
            "components": [], "nets": [], "inventory": [],
            "limitations": ["Datasheet-derived pin assignments and nets are not inferred from part names.",
                            "Connectivity checking covers graph integrity only, not electrical rules or ERC."]}


def migrate_model(model: dict[str, Any]) -> dict[str, Any]:
    """Upgrade supported v1 project model data to v2 without inventing symbols or pins."""
    if not isinstance(model, dict):
        raise ValueError("Circuit model must be a JSON object")
    version = model.get("schema_version")
    if type(version) is not int:
        raise ValueError(f"Unsupported circuit model schema version: {version!r}")
    if version == CURRENT_SCHEMA_VERSION:
        return copy.deepcopy(model)
    if version != 1:
        raise ValueError(f"Unsupported circuit model schema version: {version!r}")
    old_components, old_nets = model.get("components", []), model.get("nets", [])
    limitations, inventory = model.get("limitations", []), model.get("inventory_components", [])
    if not isinstance(old_components, list) or not isinstance(old_nets, list) or not isinstance(limitations, list) or not isinstance(inventory, list):
        raise ValueError("Malformed schema v1 circuit model collections")
    upgraded = new_circuit_model()
    upgraded["status"] = model.get("status", "draft")
    upgraded["limitations"] = [item for item in limitations if isinstance(item, str)] + upgraded["limitations"]
    upgraded["inventory"] = copy.deepcopy(inventory)
    for index, old in enumerate(old_components):
        if not isinstance(old, dict):
            raise ValueError(f"Malformed schema v1 component at index {index}")
        old_pins = old.get("pins", {})
        if not isinstance(old_pins, (dict, list)):
            raise ValueError(f"Malformed pin map on schema v1 component at index {index}")
        if isinstance(old_pins, dict):
            pins = [{"number": str(number), "name": str(name), "type": "unspecified"} for number, name in old_pins.items()]
        else:
            pins = copy.deepcopy(old_pins)
        upgraded["components"].append({
            "reference": old.get("reference"), "value": old.get("part", old.get("value", "")),
            "symbol": copy.deepcopy(old.get("symbol")), "footprint": old.get("footprint"),
            "datasheet": old.get("datasheet"), "pins": pins,
            "position_mm": copy.deepcopy(old.get("position_mm", {"x": 0.0, "y": 0.0})),
            "rotation_deg": old.get("rotation_deg", 0),
        })
    for index, old_net in enumerate(old_nets):
        if not isinstance(old_net, dict):
            raise ValueError(f"Malformed schema v1 net at index {index}")
        upgraded["nets"].append({"name": old_net.get("name"), "terminals": copy.deepcopy(old_net.get("terminals", []))})
    return upgraded


def validate_model(model: dict[str, Any]) -> dict[str, Any]:
    """Validate model shape and graph references. This is not electrical ERC."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(model, dict) or model.get("schema_version") != CURRENT_SCHEMA_VERSION:
        return {"topology_valid": False, "errors": [f"Expected circuit-model schema v{CURRENT_SCHEMA_VERSION}"], "warnings": [],
                "component_count": 0, "net_count": 0, "connected_pin_count": 0,
                "scope": "Schema and graph checks only; not an electrical rule check."}
    try:
        json.dumps(model, allow_nan=False)
    except (TypeError, ValueError):
        return {"topology_valid": False, "errors": ["Circuit model must contain only finite JSON-compatible values"], "warnings": [],
                "component_count": 0, "net_count": 0, "connected_pin_count": 0,
                "scope": "Schema and graph checks only; not an electrical rule check."}
    if not isinstance(model.get("components"), list) or not isinstance(model.get("nets"), list):
        return {"topology_valid": False, "errors": ["components and nets must be arrays"], "warnings": [],
                "component_count": 0, "net_count": 0, "connected_pin_count": 0,
                "scope": "Schema and graph checks only; not an electrical rule check."}
    metadata = model.get("metadata", {})
    if not isinstance(metadata, dict) or metadata.get("units", "mm") != "mm" or not _finite_number(metadata.get("grid_mm", 1.27)) or metadata.get("grid_mm", 1.27) <= 0:
        errors.append("metadata must define millimetre units and a positive finite grid_mm")
    elif "generated_at" in metadata and not _xml_safe(metadata["generated_at"]):
        errors.append("metadata.generated_at must be XML-safe text")
    if not isinstance(model.get("inventory", []), list):
        errors.append("inventory must be an array")
    if not isinstance(model.get("limitations", []), list) or any(not isinstance(item, str) for item in model.get("limitations", [])):
        errors.append("limitations must be an array of strings")
    status = model.get("status", "draft")
    if not isinstance(status, str) or status not in _ALLOWED_STATUSES:
        errors.append(f"status must be one of: {', '.join(sorted(_ALLOWED_STATUSES))}")
    components: dict[str, dict[str, Any]] = {}
    for index, component in enumerate(model["components"]):
        if not isinstance(component, dict):
            errors.append(f"components[{index}] must be an object")
            continue
        ref = component.get("reference")
        if not isinstance(ref, str) or not _REF_PATTERN.fullmatch(ref):
            errors.append(f"components[{index}] must have a KiCad-style reference designator (for example R1 or U2)")
            continue
        if ref in components:
            errors.append(f"Duplicate component reference: {ref}")
            continue
        components[ref] = component
        if not _nonempty(component.get("value")) or not _xml_safe(component.get("value")):
            errors.append(f"{ref} needs an XML-safe value/part description")
        for optional_text in ("footprint", "datasheet"):
            value = component.get(optional_text)
            if value is not None and not _xml_safe(value):
                errors.append(f"{ref}.{optional_text} must be XML-safe text or null")
        if "electrical_model" in component:
            errors.extend(_validate_electrical_model(component["electrical_model"], ref))
        pins = component.get("pins")
        if not isinstance(pins, list) or not pins:
            errors.append(f"{ref} must declare at least one pin")
            continue
        seen_pins: set[str] = set()
        for pin in pins:
            if not isinstance(pin, dict):
                errors.append(f"{ref} contains a pin that is not an object")
                continue
            number, pin_name, pin_type = pin.get("number"), pin.get("name"), pin.get("type", "unspecified")
            if not isinstance(number, str) or not number.strip() or not _xml_safe(number):
                errors.append(f"{ref} has a pin with no XML-safe number")
                continue
            if number in seen_pins:
                errors.append(f"{ref} has duplicate pin number {number}")
            seen_pins.add(number)
            if not _nonempty(pin_name) or not _xml_safe(pin_name):
                errors.append(f"{ref}.{number} has no XML-safe pin name")
            if not isinstance(pin_type, str) or pin_type not in _ALLOWED_PIN_TYPES:
                errors.append(f"{ref}.{number} has unsupported electrical pin type {pin_type!r}")
        position = component.get("position_mm", {"x": 0, "y": 0})
        if not isinstance(position, dict) or any(not _finite_number(position.get(axis)) for axis in ("x", "y")):
            errors.append(f"{ref} position_mm must contain finite numeric x and y")
        rotation = component.get("rotation_deg", 0)
        if not _finite_number(rotation):
            errors.append(f"{ref} rotation_deg must be finite")
        symbol = component.get("symbol")
        if symbol is not None and (not isinstance(symbol, dict) or not _nonempty(symbol.get("library")) or not _nonempty(symbol.get("name"))):
            errors.append(f"{ref} symbol must have non-empty library and name")
        elif isinstance(symbol, dict) and (not _SYMBOL_PATTERN.fullmatch(symbol["library"]) or not _SYMBOL_PATTERN.fullmatch(symbol["name"])):
            errors.append(f"{ref} symbol library/name contains characters outside the supported KiCad ID syntax")
    symbol_pin_sets: dict[tuple[str, str], tuple[tuple[str, str, str], ...]] = {}
    symbol_prefixes: dict[tuple[str, str], str] = {}
    for ref, component in components.items():
        symbol = component.get("symbol")
        if isinstance(symbol, dict) and _nonempty(symbol.get("library")) and _nonempty(symbol.get("name")):
            key = (symbol["library"], symbol["name"])
            pins = tuple(sorted((repr(pin.get("number")), repr(pin.get("name")), repr(pin.get("type", "unspecified"))) for pin in component.get("pins", []) if isinstance(pin, dict)))
            if key in symbol_pin_sets and symbol_pin_sets[key] != pins:
                errors.append(f"Components using KiCad symbol {key[0]}:{key[1]} have inconsistent pin definitions (at {ref})")
            symbol_pin_sets[key] = pins
            prefix = re.sub(r"\d+[A-Za-z]?$", "", ref)
            if key in symbol_prefixes and symbol_prefixes[key] != prefix:
                errors.append(f"Components using KiCad symbol {key[0]}:{key[1]} have inconsistent reference prefixes")
            symbol_prefixes[key] = prefix
    net_names: set[str] = set()
    assigned: dict[tuple[str, str], str] = {}
    connected: set[tuple[str, str]] = set()
    for index, net in enumerate(model["nets"]):
        if not isinstance(net, dict):
            errors.append(f"nets[{index}] must be an object")
            continue
        name = net.get("name")
        if not _nonempty(name) or not _xml_safe(name):
            errors.append(f"nets[{index}] has no XML-safe name")
            name = f"<unnamed-{index}>"
        if name in net_names:
            errors.append(f"Duplicate net name: {name}")
        net_names.add(name)
        terminals = net.get("terminals")
        if not isinstance(terminals, list):
            errors.append(f"Net {name} terminals must be an array")
            continue
        if len(terminals) < 2:
            warnings.append(f"Net {name} has fewer than two terminals")
        local: set[tuple[str, str]] = set()
        for endpoint in terminals:
            if not isinstance(endpoint, dict):
                errors.append(f"Net {name} has an invalid terminal object")
                continue
            ref, pin = endpoint.get("reference"), endpoint.get("pin")
            if not isinstance(ref, str) or not isinstance(pin, str):
                errors.append(f"Net {name} terminal requires string reference and pin fields")
                continue
            component = components.get(ref)
            if component is None:
                errors.append(f"Net {name} refers to unknown component {ref}")
                continue
            declared = {p.get("number") for p in component.get("pins", []) if isinstance(p, dict) and isinstance(p.get("number"), str)}
            if pin not in declared:
                errors.append(f"Net {name} refers to unknown pin {ref}.{pin}")
                continue
            key = (ref, pin)
            if key in local:
                errors.append(f"Pin {ref}.{pin} appears more than once on net {name}")
            if key in assigned and assigned[key] != name:
                errors.append(f"Pin {ref}.{pin} is assigned to multiple nets: {assigned[key]} and {name}")
            local.add(key)
            assigned[key] = name
            connected.add(key)
    for ref, component in components.items():
        for pin in component.get("pins", []):
            if isinstance(pin, dict) and isinstance(pin.get("number"), str) and (ref, pin["number"]) not in connected:
                warnings.append(f"Unconnected pin {ref}.{pin.get('number')}")
    return {"topology_valid": not errors, "errors": errors, "warnings": warnings,
            "component_count": len(components), "net_count": len(model["nets"]), "connected_pin_count": len(connected),
            "scope": "Schema and graph reference consistency only. No symbol-library resolution, electrical rules, voltage compatibility, or KiCad ERC has run."}


def _validate_electrical_model(model: Any, reference: str) -> list[str]:
    """Validate optional typed model v1 records carried by schema-v2 graph nodes."""
    errors: list[str] = []
    prefix = f"{reference}.electrical_model"
    if not isinstance(model, dict) or set(model) != {"model_version", "kind", "parameters", "provenance"}:
        return [f"{prefix} must contain exactly model_version, kind, parameters, and provenance"]
    if type(model.get("model_version")) is not int or model["model_version"] != 1:
        errors.append(f"{prefix}.model_version must be 1")
    kind = model.get("kind")
    parameter_fields = {"resistor": {"resistance", "tolerance_percent"},
                        "capacitor": {"capacitance", "tolerance_percent", "initial_voltage"},
                        "voltage_source": {"waveform", "source_role"},
                        "current_source": {"waveform", "source_role"}}
    if not isinstance(kind, str) or kind not in parameter_fields:
        errors.append(f"{prefix}.kind must be resistor, capacitor, voltage_source, or current_source")
        return errors
    parameters = model.get("parameters")
    if not isinstance(parameters, dict) or set(parameters) != parameter_fields[kind]:
        errors.append(f"{prefix}.parameters fields do not match model kind {kind}")
        return errors
    if kind in {"resistor", "capacitor"}:
        field = "resistance" if kind == "resistor" else "capacitance"
        units = ({"ohm", "Ohm", "Ω", "kohm", "kΩ", "Mohm", "MΩ"} if kind == "resistor"
                 else {"F", "mF", "uF", "µF", "nF", "pF"})
        quantity = parameters.get(field)
        if not isinstance(quantity, dict) or set(quantity) != {"value", "unit"} or not _finite_number(quantity.get("value")) or quantity["value"] <= 0 or not isinstance(quantity.get("unit"), str) or quantity["unit"] not in units:
            errors.append(f"{prefix}.parameters.{field} must be a positive typed quantity with a supported {field} unit")
        tolerance = parameters.get("tolerance_percent")
        if not _finite_number(tolerance) or tolerance < 0 or tolerance > 100:
            errors.append(f"{prefix}.parameters.tolerance_percent must be between 0 and 100")
        if kind == "capacitor":
            initial = parameters.get("initial_voltage")
            if not isinstance(initial, dict) or set(initial) != {"value", "unit"} or not _finite_number(initial.get("value")) or not isinstance(initial.get("unit"), str) or initial["unit"] not in {"V", "mV", "uV", "µV"}:
                errors.append(f"{prefix}.parameters.initial_voltage must be a typed voltage quantity")
    else:
        waveform = parameters.get("waveform")
        if not isinstance(waveform, dict) or set(waveform) != {"type", "initial", "final", "step_time"} or waveform.get("type") != "step":
            errors.append(f"{prefix}.parameters.waveform must be an explicit single-step waveform")
        else:
            value_units = {"V", "mV", "uV", "µV"} if kind == "voltage_source" else {"A", "mA", "uA", "µA", "nA"}
            for key in ("initial", "final"):
                quantity = waveform.get(key)
                if not isinstance(quantity, dict) or set(quantity) != {"value", "unit"} or not _finite_number(quantity.get("value")) or not isinstance(quantity.get("unit"), str) or quantity["unit"] not in value_units:
                    errors.append(f"{prefix}.parameters.waveform.{key} must be a typed source quantity")
            step = waveform.get("step_time")
            if not isinstance(step, dict) or set(step) != {"value", "unit"} or not _finite_number(step.get("value")) or step["value"] < 0 or not isinstance(step.get("unit"), str) or step["unit"] not in {"s", "ms", "us", "µs", "ns"}:
                errors.append(f"{prefix}.parameters.waveform.step_time must be a nonnegative typed time quantity")
        if not isinstance(parameters.get("source_role"), str) or parameters["source_role"] not in {"testbench", "independent"}:
            errors.append(f"{prefix}.parameters.source_role must be testbench or independent")
    if not isinstance(model.get("provenance"), str) or not model["provenance"].strip():
        errors.append(f"{prefix}.provenance must explain the source of this model")
    return errors


def export_kicad_netlist(model: dict[str, Any], source_name: str = "circuitmind") -> bytes:
    """Emit a KiCad legacy XML netlist candidate from caller-provided symbol IDs."""
    report = validate_model(model)
    if not report["topology_valid"]:
        raise ValueError("Circuit model is invalid: " + "; ".join(report["errors"]))
    if not model["components"]:
        raise ValueError("Cannot export a netlist with no components")
    unresolved = [item["reference"] for item in model["components"] if not item.get("symbol")]
    if unresolved:
        raise ValueError("KiCad symbol library IDs are unresolved for: " + ", ".join(unresolved))
    root = ET.Element("export", {"version": "D"})
    design = ET.SubElement(root, "design")
    ET.SubElement(design, "source").text = source_name
    ET.SubElement(design, "date").text = model.get("metadata", {}).get("generated_at", "")
    ET.SubElement(design, "tool").text = "CIRCUITMIND AI"
    components_node = ET.SubElement(root, "components")
    libparts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    libpart_references: dict[tuple[str, str], str] = {}
    for component in model["components"]:
        symbol = component["symbol"]
        lib_id = (symbol["library"], symbol["name"])
        libparts.setdefault(lib_id, component["pins"])
        libpart_references.setdefault(lib_id, re.sub(r"\d+[A-Za-z]?$", "", component["reference"]))
        comp = ET.SubElement(components_node, "comp", {"ref": component["reference"]})
        ET.SubElement(comp, "value").text = component["value"]
        if component.get("footprint"):
            ET.SubElement(comp, "footprint").text = component["footprint"]
        ET.SubElement(comp, "datasheet").text = component.get("datasheet") or ""
        ET.SubElement(comp, "libsource", {"lib": symbol["library"], "part": symbol["name"]})
        ET.SubElement(comp, "sheetpath", {"names": "/", "tstamps": "/"})
    libparts_node = ET.SubElement(root, "libparts")
    for (library, part_name), pins in sorted(libparts.items()):
        libpart = ET.SubElement(libparts_node, "libpart", {"lib": library, "part": part_name})
        ET.SubElement(libpart, "description").text = f"Model-supplied pin metadata for {library}:{part_name}"
        fields = ET.SubElement(libpart, "fields")
        ET.SubElement(fields, "field", {"name": "Reference"}).text = libpart_references[(library, part_name)]
        ET.SubElement(fields, "field", {"name": "Value"}).text = part_name
        pins_node = ET.SubElement(libpart, "pins")
        for pin in pins:
            ET.SubElement(pins_node, "pin", {"num": pin["number"], "name": pin["name"], "type": _to_kicad_pin_type(pin.get("type", "unspecified"))})
    libraries_node = ET.SubElement(root, "libraries")
    for library in sorted({library for library, _ in libparts}):
        ET.SubElement(libraries_node, "library", {"logical": library})
    nets_node = ET.SubElement(root, "nets")
    for code, net in enumerate(model["nets"], start=1):
        net_node = ET.SubElement(nets_node, "net", {"code": str(code), "name": net["name"]})
        for terminal in net["terminals"]:
            component = next(item for item in model["components"] if item["reference"] == terminal["reference"])
            pin = next(item for item in component["pins"] if item["number"] == str(terminal["pin"]))
            ET.SubElement(net_node, "node", {"ref": terminal["reference"], "pin": str(terminal["pin"]),
                                             "pinfunction": pin["name"], "pintype": _to_kicad_pin_type(pin.get("type", "unspecified"))})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def model_digest(model: dict[str, Any]) -> str:
    raw = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _xml_safe(value: Any) -> bool:
    return isinstance(value, str) and all(ord(char) in (0x9, 0xA, 0xD) or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF for char in value)


def _to_kicad_pin_type(pin_type: str) -> str:
    return {"power_in": "power_in", "power_out": "power_out", "open_collector": "open_collector",
            "open_emitter": "open_emitter", "tri_state": "tri_state", "no_connect": "no_connect"}.get(pin_type, pin_type)
