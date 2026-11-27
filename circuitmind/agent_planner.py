"""Strict validation of untrusted local-model read-only plans."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .agent_tools import TOOL_CATALOG

_MAX_PLAN_CALLS = 3
_MAX_SUMMARY_CHARS = 500
_ALLOWED_TOOLS = {tool["name"] for tool in TOOL_CATALOG}


def parse_read_only_plan(content: str) -> dict[str, Any]:
    """Parse a model response into a tiny allowlisted plan; never execute it here."""
    if not isinstance(content, str) or len(content) > 64_000:
        raise ValueError("Provider plan text is missing or exceeds the size limit")
    try:
        plan = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Provider plan must be valid JSON") from exc
    if not isinstance(plan, dict) or set(plan) != {"summary", "calls"}:
        raise ValueError("Provider plan must contain only 'summary' and 'calls'")
    summary = plan["summary"]
    calls = plan["calls"]
    if not isinstance(summary, str) or not summary.strip() or len(summary) > _MAX_SUMMARY_CHARS:
        raise ValueError("Provider plan summary must be a nonempty string of at most 500 characters")
    if not isinstance(calls, list) or not 1 <= len(calls) <= _MAX_PLAN_CALLS:
        raise ValueError("Provider plan must contain 1 to 3 read-only tool calls")
    seen = set()
    normalized = []
    for index, call in enumerate(calls):
        if not isinstance(call, dict) or set(call) != {"tool", "arguments"}:
            raise ValueError(f"Provider plan call {index} must contain exactly 'tool' and 'arguments'")
        name = call["tool"]
        if not isinstance(name, str) or name not in _ALLOWED_TOOLS:
            raise ValueError(f"Provider plan call {index} references a non-read-only or unknown tool")
        if name in seen:
            raise ValueError(f"Provider plan contains duplicate tool {name!r}")
        seen.add(name)
        if not isinstance(call["arguments"], dict) or call["arguments"]:
            raise ValueError(f"Read-only tool {name!r} accepts no arguments")
        normalized.append({"tool": name, "arguments": {}})
    return {"summary": summary.strip(), "calls": normalized}


def build_compact_execution_evidence(project_id: str, calls: list[dict[str, Any]], results: list[dict[str, Any]],
                                     evaluation: dict[str, Any]) -> dict[str, Any]:
    """Keep only bounded identity/topology/connectivity facts for optional local summaries."""
    outputs = {row.get("tool"): row.get("output") for row in results
               if isinstance(row, dict) and row.get("status") == "completed" and isinstance(row.get("output"), dict)}
    evidence: dict[str, Any] = {"execution_status": evaluation.get("status"),
                                "project": {"project_id": project_id},
                                "checks": [{"name": row.get("name"), "passed": row.get("passed")}
                                           for row in evaluation.get("checks", []) if isinstance(row, dict)]}
    summary = outputs.get("project.summary")
    if isinstance(summary, dict):
        evidence["project"].update({key: summary.get(key) for key in ("name", "architecture")})
    validation = outputs.get("circuit.validate")
    if isinstance(validation, dict):
        topology = validation.get("topology_report")
        evidence["model"] = {"sha256": validation.get("circuit_model_sha256")}
        if isinstance(topology, dict):
            errors = topology.get("errors", [])
            evidence["topology"] = {"valid": topology.get("topology_valid"),
                                    "component_count": topology.get("component_count"),
                                    "net_count": topology.get("net_count"),
                                    "errors": errors[:10] if isinstance(errors, list) else []}
    connectivity = outputs.get("circuit.connectivity_schedule")
    if isinstance(connectivity, dict):
        evidence["connectivity"] = {key: connectivity.get(key) for key in (
            "circuit_model_sha256", "component_count", "net_count", "endpoint_count",
            "unconnected_pin_count", "no_connect_pin_count")}
    evidence["available_evidence_keys"] = available_evidence_keys(evidence)
    return evidence


def available_evidence_keys(evidence: dict[str, Any]) -> list[str]:
    keys = []
    for key, value in evidence.items():
        if key == "available_evidence_keys":
            continue
        if isinstance(value, dict):
            keys.extend(f"{key}.{child}" for child in value if value[child] is not None)
        else:
            keys.append(key)
    return keys


def parse_evidence_summary(content: str, evidence: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(content, str) or len(content) > 64_000:
        raise ValueError("Provider summary text is missing or exceeds the size limit")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Provider summary must be valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"summary", "evidence_keys"}:
        raise ValueError("Provider summary must contain only 'summary' and 'evidence_keys'")
    summary = payload["summary"]
    evidence_keys = payload["evidence_keys"]
    allowed = set(available_evidence_keys(evidence))
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1_000:
        raise ValueError("Provider summary must be a nonempty string of at most 1,000 characters")
    if (not isinstance(evidence_keys, list) or not 1 <= len(evidence_keys) <= 8 or
            any(not isinstance(key, str) or key not in allowed for key in evidence_keys) or
            len(set(evidence_keys)) != len(evidence_keys)):
        raise ValueError("Provider summary must cite 1 to 8 distinct available evidence keys")
    return {"summary": summary.strip(), "evidence_keys": evidence_keys}


def resolve_evidence_values(evidence: dict[str, Any], evidence_keys: list[str]) -> dict[str, Any]:
    """Return the exact compact evidence values cited by a validated summary."""
    resolved: dict[str, Any] = {}
    for key in evidence_keys:
        parts = key.split(".", 1)
        value: Any = evidence
        for part in parts:
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f"Evidence key {key!r} is not resolvable")
            value = value[part]
        resolved[key] = value
    return resolved


def _report_digest(report: dict[str, Any], digest_field: str) -> str:
    payload = {key: value for key, value in report.items() if key != digest_field}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def execution_report_digest(report: dict[str, Any]) -> str:
    return _report_digest(report, "execution_report_sha256")


def summary_report_digest(report: dict[str, Any]) -> str:
    return _report_digest(report, "summary_report_sha256")


def agent_plan_digest(plan: dict[str, Any]) -> str:
    """Hash immutable plan identity/content, excluding mutable execution state."""
    fields = ("plan_id", "project_id", "created_at", "provider", "model",
              "prompt_sha256", "completion_sha256", "summary", "calls")
    payload = {key: plan.get(key) for key in fields}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check_read_only_execution(project_id: str, calls: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply deterministic identity/topology/hash checks to explicit read-only tool outputs."""
    by_tool = {item.get("tool"): item for item in results if isinstance(item, dict)}
    all_completed = (len(results) == len(calls) and
                     all(item.get("status") == "completed" for item in results if isinstance(item, dict)))
    checks = [{"name": "all_planned_tools_completed", "passed": all_completed,
               "planned_count": len(calls), "completed_count": sum(item.get("status") == "completed" for item in results)}]
    if "project.summary" in {call.get("tool") for call in calls}:
        item = by_tool.get("project.summary", {})
        output = item.get("output") if isinstance(item, dict) else None
        actual_id = output.get("project_id") if isinstance(output, dict) else None
        checks.append({"name": "project_identity_matches", "passed": actual_id == project_id,
                       "observed_project_id": actual_id})
    validation_item = by_tool.get("circuit.validate")
    if "circuit.validate" in {call.get("tool") for call in calls}:
        output = validation_item.get("output") if isinstance(validation_item, dict) else None
        topology = output.get("topology_report") if isinstance(output, dict) else None
        topology_ok = isinstance(topology, dict) and topology.get("topology_valid") is True
        checks.append({"name": "saved_model_schema_and_topology_valid", "passed": topology_ok,
                       "errors": topology.get("errors", []) if isinstance(topology, dict) else []})
    if "circuit.connectivity_schedule" in {call.get("tool") for call in calls}:
        item = by_tool.get("circuit.connectivity_schedule", {})
        output = item.get("output") if isinstance(item, dict) else None
        connectivity_hash = output.get("circuit_model_sha256") if isinstance(output, dict) else None
        if validation_item is not None:
            validation_output = validation_item.get("output") if isinstance(validation_item, dict) else None
            validation_hash = validation_output.get("circuit_model_sha256") if isinstance(validation_output, dict) else None
            same_hash = bool(validation_hash) and validation_hash == connectivity_hash
            checks.append({"name": "connectivity_matches_validated_model_hash", "passed": same_hash,
                           "validation_sha256": validation_hash, "connectivity_sha256": connectivity_hash})
        else:
            has_hash = isinstance(connectivity_hash, str) and len(connectivity_hash) == 64
            checks.append({"name": "connectivity_model_hash_present", "passed": has_hash,
                           "connectivity_sha256": connectivity_hash})
    return {"status": "passed" if all(check["passed"] for check in checks) else "failed",
            "checks": checks,
            "limitations": ["Checks cover only tool completion, project identity, declared topology, and report hash consistency.",
                            "Unconnected pins remain observations; no electrical simulation, physical routing, or design approval is performed."]}
