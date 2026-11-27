"""Provider-neutral, read-only project tools for a future bounded agent loop."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .circuit_schema import model_digest, validate_model
from .connectivity_schedule import build_connectivity_schedule
from .project_store import load_circuit


TOOL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "project.summary",
        "description": "Read selected project metadata and artifact names.",
        "permission": "read-only",
        "requires_approval": False,
        "arguments": {},
    },
    {
        "name": "circuit.validate",
        "description": "Validate the saved circuit schema and declared topology.",
        "permission": "read-only",
        "requires_approval": False,
        "arguments": {},
    },
    {
        "name": "circuit.connectivity_schedule",
        "description": "Summarize declared pin-to-net connectivity without inferring physical wiring.",
        "permission": "read-only",
        "requires_approval": False,
        "arguments": {},
    },
)

APPROVAL_TOOL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "circuit.replace_model",
        "description": "Replace the saved circuit model with an explicitly supplied topology-valid model after a separate approval step.",
        "permission": "write-circuit",
        "requires_approval": True,
        "approval_flow": "POST /agent/proposals, review the proposal, then POST /agent/proposals/{proposal_id}/approve",
        "arguments": {"model": "Circuit model object"},
    },
)

_TOOL_NAMES = {tool["name"] for tool in TOOL_CATALOG}


def list_agent_tools() -> list[dict[str, Any]]:
    """Return a copy of the immutable read-only tool catalog."""
    return [dict(tool) for tool in TOOL_CATALOG]


def execute_agent_tool(name: str, arguments: Any, project_id: str, project_dir: Path) -> dict[str, Any]:
    """Execute exactly one registered read-only operation against a project."""
    if name not in _TOOL_NAMES:
        raise ValueError(f"Tool {name!r} is not registered")
    if not isinstance(arguments, dict) or arguments:
        raise ValueError(f"Tool {name!r} accepts no arguments")

    if name == "project.summary":
        try:
            project = json.loads((project_dir / "project.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Project record could not be read: {exc}") from exc
        if not isinstance(project, dict):
            raise ValueError("Project record must contain a JSON object")
        findings = project.get("findings", [])
        artifacts = project.get("artifacts", [])
        if not isinstance(findings, list) or not isinstance(artifacts, list):
            raise ValueError("Project record findings and artifacts must be arrays")
        return {
            "project_id": project.get("project_id"),
            "name": project.get("name"),
            "architecture": project.get("architecture"),
            "status": project.get("status"),
            "finding_count": len(project.get("findings", [])),
            "artifact_count": len(project.get("artifacts", [])),
            "artifacts": project.get("artifacts", []),
        }

    model = load_circuit(project_dir)
    digest = model_digest(model)
    if name == "circuit.validate":
        report = validate_model(model)
        return {"circuit_model_sha256": digest, "topology_report": report,
                "scope": "Schema and declared-topology checks only; no electrical analysis or design approval."}
    if name == "circuit.connectivity_schedule":
        return build_connectivity_schedule(model, digest)
    raise ValueError(f"Tool {name!r} is not implemented")
