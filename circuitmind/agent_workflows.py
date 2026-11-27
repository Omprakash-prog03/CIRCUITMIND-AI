"""Deterministic, bounded workflows built only from registered project tools."""
from __future__ import annotations
from typing import Any


WORKFLOW_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "circuit_review",
        "description": "Review project metadata, validate declared circuit topology, and summarize saved connectivity.",
        "permission": "read-only",
        "requires_approval": False,
        "max_steps": 3,
        "arguments": {},
    },
)

CIRCUIT_REVIEW_STEPS: tuple[str, ...] = (
    "project.summary",
    "circuit.validate",
    "circuit.connectivity_schedule",
)


def evaluate_circuit_review(project_id: str, outputs: dict[str, Any]) -> dict[str, Any]:
    """Check identity, topology validity, and model-hash consistency without judging design intent."""
    summary = outputs.get("project.summary")
    validation = outputs.get("circuit.validate")
    connectivity = outputs.get("circuit.connectivity_schedule")
    summary_ok = isinstance(summary, dict) and summary.get("project_id") == project_id
    topology = validation.get("topology_report") if isinstance(validation, dict) else None
    topology_ok = isinstance(topology, dict) and topology.get("topology_valid") is True
    validation_hash = validation.get("circuit_model_sha256") if isinstance(validation, dict) else None
    connectivity_hash = connectivity.get("circuit_model_sha256") if isinstance(connectivity, dict) else None
    same_model = bool(validation_hash) and validation_hash == connectivity_hash
    checks = [
        {"name": "project_identity_matches", "passed": summary_ok,
         "observed_project_id": summary.get("project_id") if isinstance(summary, dict) else None},
        {"name": "saved_model_schema_and_topology_valid", "passed": topology_ok,
         "errors": topology.get("errors", []) if isinstance(topology, dict) else []},
        {"name": "connectivity_uses_same_saved_model_hash", "passed": same_model,
         "validation_sha256": validation_hash, "connectivity_sha256": connectivity_hash},
    ]
    connectivity_summary = None
    if isinstance(connectivity, dict):
        connectivity_summary = {key: connectivity.get(key) for key in (
            "component_count", "net_count", "endpoint_count", "unconnected_pin_count", "no_connect_pin_count")}
    return {
        "status": "passed" if all(check["passed"] for check in checks) else "failed",
        "project": ({"project_id": summary.get("project_id"), "name": summary.get("name"),
                     "architecture": summary.get("architecture")} if isinstance(summary, dict) else None),
        "circuit_model_sha256": validation_hash if validation_hash == connectivity_hash else None,
        "topology_report": topology,
        "connectivity_summary": connectivity_summary,
        "checks": checks,
        "limitations": [
            "This is a deterministic checklist, not an AI-generated plan or engineering approval.",
            "Connectivity reflects declared graph endpoints only; no physical routing or electrical analysis is performed.",
            "Unconnected-pin counts are reported as observations, not automatic failures.",
        ],
    }
