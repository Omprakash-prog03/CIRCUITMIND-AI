"""Deterministic first-pass engineering workflow; intentionally does not invent pinouts."""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
import uuid
import zipfile
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .calculations import led_current_limit_resistor, ne555_astable_frequency
from .models import CircuitModel, InventoryItem, ProjectInput, ProjectRecord, utc_now


_REQUIRED_DISCRETE = {
    "CD4518": (3, "Dual BCD counter; six decade stages need three dual-counter ICs, with additional rollover logic for HH:MM:SS."),
    "CD4511": (6, "One BCD-to-seven-segment driver per digit for six independently decoded displays."),
    "seven-segment display": (6, "Six digits are needed for HH:MM:SS."),
    "push button": (3, "At least time-set controls are needed; exact UI is a requirement decision."),
    "buzzer": (1, "Audible alarm output."),
}


def parse_inventory(text: str) -> list[InventoryItem]:
    """Parse one component per line; quantity may be a trailing integer or `x N`."""
    parsed: list[InventoryItem] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-•* ").strip()
        if not line or re.fullmatch(r"(?i)component\s+quantity", line):
            continue
        qty: int | None = None
        match = re.search(r"(?:\s*[x×]\s*|\s+)(\d+)\s*$", line, re.IGNORECASE)
        if match:
            qty = int(match.group(1))
            name = line[:match.start()].strip(" :\t-–")
        else:
            name = line.rstrip(" :\t")
        if name:
            parsed.append(InventoryItem(name=name, quantity=qty, raw=raw.strip()))
    return parsed


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower())


def _count(items: list[InventoryItem], needle: str) -> int:
    target = _normalize(needle)
    total = 0
    for item in items:
        normalized = _normalize(item.name)
        if target in normalized:
            total += item.quantity if item.quantity is not None else 1
    return total


def _requirements(description: str, architecture: str) -> dict[str, Any]:
    lower = description.lower()
    detected = {
        "six_digit_time_display": any(token in lower for token in ("digital clock", "hh:mm:ss", "six-digit", "six digit")),
        "alarm": "alarm" in lower,
        "time_setting_controls": any(token in lower for token in ("button", "time-set", "time set", "setting")),
        "firmware_required": architecture == "esp32",
    }
    return {"objective": description.strip(), "architecture": architecture, "detected_requirements": detected,
            "unresolved": ["Display electrical type (common-anode/common-cathode) and exact part number are not specified.",
                           "Power source, accuracy target, alarm behavior, and operating environment need confirmation."]}


def analyze(project_input: ProjectInput, root: Path) -> tuple[ProjectRecord, dict[str, bytes]]:
    if project_input.architecture not in {"discrete", "esp32"}:
        raise ValueError("architecture must be 'discrete' or 'esp32'")
    if not project_input.name.strip() or not project_input.description.strip():
        raise ValueError("Project name and description are required")
    inventory = parse_inventory(project_input.inventory_text)
    findings: list[str] = []
    if not inventory:
        findings.append("No component inventory was supplied; architecture feasibility cannot be checked against available stock.")

    if project_input.architecture == "discrete":
        for part, (required, rationale) in _REQUIRED_DISCRETE.items():
            present = _count(inventory, part)
            if present < required:
                findings.append(f"Missing/short: {part} — have {present}, benchmark minimum {required}. {rationale}")
        if _count(inventory, "ne555"):
            findings.append("NE555 can provide an approximate clock source, but is not an accurate timebase without calibration; a crystal/RTC timebase is not listed.")
        findings.append("Discrete rollovers (seconds/minutes modulo 60 and hours modulo 24), alarm comparison/latching, and drive-current margins require a complete logic design and verified datasheets; not established by this intake pass.")
    else:
        for part, rationale in (("esp32", "An exact ESP32 board/module and its voltage/pinout are required."),
                                ("display", "A display interface and driver are needed; six raw displays require an explicit multiplex/driver design."),
                                ("buzzer", "A buzzer/output driver is needed for an alarm.")):
            if not _count(inventory, part):
                findings.append(f"Not found in supplied inventory: {part}. {rationale}")
        findings.append("ESP32 firmware and pin assignments are not generated until exact hardware parts and display interface are resolved.")

    project_id = uuid.uuid4().hex[:12]
    now = utc_now()
    requirements = _requirements(project_input.description, project_input.architecture)
    model = CircuitModel(status="requirements-only", limitations=[
        "No datasheet-derived pin assignments or nets are invented.",
        "This milestone generates a requirements/design-intake model, not an electrical schematic or KiCad file.",
    ])
    checks = {
        "requirements_analysis": "completed-deterministic-intake",
        "inventory_parsing": "completed",
        "component_datasheet_verification": "not-run (no datasheet source configured)",
        "electrical_connectivity": "not-run (no pin-level nets generated)",
        "electrical_rule_check": "not-run (KiCad CLI unavailable or not invoked)",
        "firmware_compile": "not-applicable" if project_input.architecture == "discrete" else "not-run (firmware not generated)",
        "simulation": "not-run (SPICE model/topology not generated)",
        "hardware_test": "not-performed",
        "manufacturing_check": "not-performed",
    }
    validation = {"checks": checks, "overall": "incomplete — intake and feasibility only", "findings": findings}

    # Calculations are illustrative only until actual part values and operating corners are verified.
    calculations = {
        "illustrative_led_resistor": led_current_limit_resistor(5.0, 2.0, 0.005),
        "illustrative_ne555_near_1hz": ne555_astable_frequency(47_000, 47_000, 10e-6),
        "scope": "Examples only, not design-approved values. Supply, actual LED Vf/current, IC output capability, tolerances, and timing accuracy must be validated against exact datasheets.",
    }
    components = [{"reference": f"U{n+1}", "part": item.name, "quantity": item.quantity,
                   "datasheet_status": "unverified", "pinout_status": "unresolved"} for n, item in enumerate(inventory)]
    artifacts: dict[str, bytes] = {
        "engineering_spec.json": _json_bytes(requirements),
        "bom.csv": _bom_csv(inventory),
        "architecture.md": _architecture_text(project_input, findings),
        "circuit_model.json": _json_bytes({**asdict(model), "inventory_components": components}),
        "calculations.json": _json_bytes(calculations),
        "validation_report.json": _json_bytes(validation),
        "README.md": _project_readme(project_input, project_id, findings),
    }
    record = ProjectRecord(project_id=project_id, name=project_input.name.strip(), created_at=now, updated_at=now,
                           description=project_input.description.strip(), architecture=project_input.architecture,
                           status="incomplete", inventory=[asdict(item) for item in inventory], findings=findings,
                           artifacts=sorted(artifacts), validation=validation)
    artifacts["project.json"] = _json_bytes(record.to_dict())
    directory = root / "projects" / project_id
    directory.mkdir(parents=True, exist_ok=False)
    for name, content in artifacts.items():
        (directory / name).write_bytes(content)
    return record, artifacts


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _bom_csv(inventory: list[InventoryItem]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Item", "Quantity", "Source line", "Datasheet status", "Pinout status"])
    for item in inventory:
        writer.writerow([item.name, item.quantity if item.quantity is not None else "unspecified (assumed 1 for presence only)", item.raw, "unverified", "unresolved"])
    return output.getvalue().encode("utf-8")


def _architecture_text(project_input: ProjectInput, findings: list[str]) -> bytes:
    if project_input.architecture == "discrete":
        blocks = ["## Proposed functional blocks", "- Timing source: NE555 is an approximate pulse source, not an accuracy-qualified timebase.",
                  "- Seconds/minutes counters: BCD counter stages with modulo-60 rollover logic.",
                  "- Hours counter: modulo-24 logic and rollover.", "- Display: per-digit BCD-to-seven-segment decoding; display polarity must match the exact driver/display.",
                  "- Alarm: time comparison, user-set alarm state, and a current-rated buzzer driver.",
                  "\nThese are architecture blocks only. No pin-level schematic/netlist is fabricated."]
    else:
        blocks = ["## Proposed functional blocks", "- ESP32 MCU and regulated power stage.",
                  "- Six-digit display subsystem with a verified driver or explicitly designed multiplexing stage.",
                  "- Timebase (RTC module or qualified network/local clock source), controls, and buzzer driver.",
                  "- Firmware maps must be generated from the exact board and actual circuit nets.",
                  "\nNo firmware or pin-level schematic is fabricated without resolved parts and connections."]
    text = f"# Architecture — {project_input.name}\n\n**Architecture choice:** {project_input.architecture}\n\n**Design status:** intake / feasibility; not an electrical design.\n\n" + "\n".join(blocks)
    text += "\n\n## Open feasibility findings\n" + "\n".join(f"- {x}" for x in findings)
    return text.encode("utf-8")


def _project_readme(project_input: ProjectInput, project_id: str, findings: list[str]) -> bytes:
    return (f"# {project_input.name}\n\nProject ID: `{project_id}`  \nArchitecture: `{project_input.architecture}`  \nState: **incomplete — requirements and feasibility only**\n\n"
            "## Important validation boundary\nThis package contains generated intake artifacts, not a completed or electrically validated design. No compilation, simulation, hardware, or manufacturing checks are claimed.\n\n"
            "## Findings\n" + "\n".join(f"- {item}" for item in findings) + "\n").encode("utf-8")


def make_zip(project_dir: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(project_dir.iterdir()):
            if path.is_file():
                archive.write(path, arcname=f"{project_dir.name}/{path.name}")
    return output.getvalue()
