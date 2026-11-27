"""Local HTTP API and static application server."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import os
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .circuit_schema import export_kicad_netlist, migrate_model, model_digest, validate_model
from .integrations import discover_integrations, run_kicad_drc, run_kicad_erc, run_ngspice, run_pdftotext
from .kicad_symbols import resolve_kicad_symbol
from .engineering import (clock_divider_candidate, clock_timebase_error, led_series_resistor,
                          solve_dc_operating_point, simulate_dc_sweep, map_circuit_graph_to_mna, map_circuit_graph_to_ac,
                          simulate_transient_rc, simulate_ac_sweep, simulate_digital_truth_table,
                          simulate_digital_waveform, synthesize_rc_lowpass)
from .engineering.cpp_engine import CppEngineUnavailable
from .engineering.operating_limits import assess_operating_limits, assess_transient_reactive_limits
from .spice import validate_spice_deck
from .firmware import generate_esp32_scaffold
from .connectivity_schedule import build_connectivity_schedule
from .models import ProjectInput
from .project_store import list_snapshots, load_circuit, restore_snapshot, save_circuit
from .project_archive import MAX_ARCHIVE_BYTES, read_project_archive
from .agent_tools import (APPROVAL_TOOL_CATALOG, TOOL_CATALOG,
                          execute_agent_tool, list_agent_tools)
from .agent_audit import append_agent_actions, verify_agent_actions
from .agent_providers import (AgentProviderError, generate_agent_plan,
                              generate_evidence_summary, probe_agent_provider,
                              provider_configuration_status)
from .agent_planner import (agent_plan_digest, build_compact_execution_evidence,
                            check_read_only_execution, execution_report_digest,
                            parse_evidence_summary, parse_read_only_plan,
                            resolve_evidence_values, summary_report_digest,
                            available_evidence_keys)
from .agent_workflows import CIRCUIT_REVIEW_STEPS, WORKFLOW_CATALOG, evaluate_circuit_review
from .workflow import analyze, make_zip


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA_DIR = Path(os.environ.get("CIRCUITMIND_DATA_DIR", ROOT / "workspace")).expanduser().resolve()
MAX_BODY = 1_000_000
MAX_AGENT_REPORT_BYTES = 4_000_000
MAX_AGENT_SUMMARY_BYTES = 256_000
MAX_SCHEMATIC_BODY = 24_000_000
MAX_SCHEMATIC_FILE = 12_000_000
MAX_DATASHEET_FILE = 20_000_000
_CIRCUIT_API_LOCK = threading.RLock()
_TOOL_LOCKS: dict[str, threading.Lock] = {}
_TOOL_LOCKS_GUARD = threading.Lock()


def _project_tool_lock(project_id: str) -> threading.Lock:
    with _TOOL_LOCKS_GUARD:
        return _TOOL_LOCKS.setdefault(project_id, threading.Lock())


class RequestTooLarge(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    server_version = f"CircuitMind/{__version__}"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def _json(self, status: int, payload: object) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            return self._json(200, {"ok": True, "version": __version__})
        if parsed.path == "/api/integrations":
            return self._json(200, {"integrations": discover_integrations()})
        if parsed.path == "/api/agent/tools":
            return self._json(200, {"tools": list_agent_tools() + [dict(tool) for tool in APPROVAL_TOOL_CATALOG],
                                    "scope": "Provider-neutral registry only. Read-only tools execute directly; circuit replacement requires a separate review and approval step. No model provider or autonomous planning is connected."})
        if parsed.path == "/api/agent/workflows":
            return self._json(200, {"workflows": [dict(workflow) for workflow in WORKFLOW_CATALOG],
                                    "scope": "Finite deterministic workflows with explicit checks; no LLM planning or electrical design approval."})
        if parsed.path == "/api/agent/providers":
            return self._json(200, {"provider": provider_configuration_status(),
                                    "scope": "Reads environment configuration only; does not contact a provider."})
        if parsed.path == "/api/projects":
            directory = DATA_DIR / "projects"
            records = []
            if directory.exists():
                for path in sorted(directory.glob("*/project.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                    try:
                        records.append(json.loads(path.read_text("utf-8")))
                    except (OSError, json.JSONDecodeError):
                        continue
            return self._json(200, {"projects": records})
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["agent", "proposals"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                proposals = []
                for proposal_path in sorted(folder.glob("agent_proposal_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:100]:
                    proposal_id = proposal_path.stem.removeprefix("agent_proposal_")
                    proposal = _read_agent_proposal(folder, proposal_id)
                    if proposal.get("project_id") != project_id:
                        raise ValueError("Agent proposal belongs to a different project")
                    proposals.append(_agent_proposal_summary(proposal))
                return self._json(200, {"project_id": project_id, "proposals": proposals})
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                return self._json(500, {"error": f"Agent proposals could not be read: {exc}"})
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3] == "agent" and parts[4] == "proposals":
            project_id, proposal_id = parts[2], parts[5]
            if not _valid_id(project_id) or not _valid_agent_proposal_id(proposal_id):
                return self._json(400, {"error": "Invalid project or proposal id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                proposal = _read_agent_proposal(folder, proposal_id)
                if proposal.get("project_id") != project_id:
                    raise ValueError("Agent proposal belongs to a different project")
                return self._json(200, {"proposal": proposal})
            except FileNotFoundError:
                return self._json(404, {"error": "Agent proposal not found"})
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                return self._json(500, {"error": f"Agent proposal could not be read: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["agent", "executions"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            items = []
            try:
                paths = sorted(folder.glob("agent_execution_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]
                for report_path in paths:
                    execution_id = report_path.stem.removeprefix("agent_execution_")
                    if not _valid_agent_plan_id(execution_id):
                        continue
                    try:
                        report, _report_hash, _chain = _load_verified_agent_execution(folder, project_id, execution_id)
                        items.append({"execution_id": execution_id, "created_at": report.get("created_at"),
                                      "status": report.get("status"), "integrity": "verified",
                                      "artifact": report_path.name})
                    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                        items.append({"execution_id": execution_id, "integrity": "invalid-or-unlinked",
                                      "error": str(exc)[:200], "artifact": report_path.name})
                return self._json(200, {"project_id": project_id, "executions": items,
                                        "scope": "Only report digests and intact audit-chain links establish artifact integrity, not tool-result truth or electrical/design correctness."})
            except OSError as exc:
                return self._json(500, {"error": f"Agent executions could not be listed: {exc}"})
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3:5] == ["agent", "executions"]:
            project_id, execution_id = parts[2], parts[5]
            if not _valid_id(project_id) or not _valid_agent_plan_id(execution_id):
                return self._json(400, {"error": "Invalid project or execution id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                report, report_hash, chain = _load_verified_agent_execution(folder, project_id, execution_id)
                return self._json(200, {"report": report, "report_sha256": report_hash,
                                        "chain_integrity": chain,
                                        "scope": "This verifies the saved report digest and audit link only; tool output consistency does not establish electrical correctness or design approval."})
            except FileNotFoundError:
                return self._json(404, {"error": "Agent execution report not found"})
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._json(409, {"error": f"Agent execution report failed integrity verification: {exc}"})
        if len(parts) == 7 and parts[:2] == ["api", "projects"] and parts[3:5] == ["agent", "executions"] and parts[6] == "summaries":
            project_id, execution_id = parts[2], parts[5]
            if not _valid_id(project_id) or not _valid_agent_plan_id(execution_id):
                return self._json(400, {"error": "Invalid project or execution id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                execution_name = f"agent_execution_{execution_id}.json"
                execution_path = folder / execution_name
                if not execution_path.is_file():
                    return self._json(404, {"error": "Agent execution report not found"})
                if execution_path.stat().st_size > MAX_AGENT_REPORT_BYTES:
                    return self._json(409, {"error": "Execution report exceeds the verification size limit"})
                execution_bytes = execution_path.read_bytes()
                execution = json.loads(execution_bytes.decode("utf-8"))
                execution_hash = hashlib.sha256(execution_bytes).hexdigest()
                if (not isinstance(execution, dict) or execution.get("execution_id") != execution_id or
                        execution.get("project_id") != project_id or execution.get("artifact") != execution_name or
                        execution_report_digest(execution) != execution.get("execution_report_sha256")):
                    return self._json(409, {"error": "Execution report identity or integrity check failed"})
                evidence = execution.get("evidence")
                if not isinstance(evidence, dict):
                    return self._json(409, {"error": "Execution report has no verifiable evidence"})
                evidence_bytes = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
                action_path = folder / "agent_actions.jsonl"
                rows, chain = verify_agent_actions(action_path.read_text("utf-8") if action_path.is_file() else "")
                source_linked = any(row.get("tool") == "agent.plan.execute" and
                                    row.get("execution_id") == execution_id and
                                    row.get("result_sha256") == execution_hash for row in rows)
                if chain.get("status") != "intact" or not source_linked:
                    return self._json(409, {"error": "Execution report is not linked to an intact audit chain"})
                summaries = []
                paths = sorted(folder.glob(f"agent_summary_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:50]
                for summary_path in paths:
                    try:
                        if summary_path.stat().st_size > MAX_AGENT_SUMMARY_BYTES:
                            raise ValueError("Saved summary exceeds the verification size limit")
                        raw = summary_path.read_bytes()
                        summary = json.loads(raw.decode("utf-8"))
                        if isinstance(summary, dict) and summary.get("execution_id") != execution_id:
                            continue
                        expected_artifact = summary_path.name
                        audited = any(row.get("tool") == "agent.execution.summarize" and
                                      row.get("status") == "ai-generated-unverified" and
                                      row.get("result_sha256") == hashlib.sha256(raw).hexdigest()
                                      for row in rows)
                        valid = (isinstance(summary, dict) and summary.get("project_id") == project_id and
                                 summary.get("execution_id") == execution_id and summary.get("artifact") == expected_artifact and
                                 summary.get("source_execution_report_sha256") == execution_hash and
                                 summary.get("evidence_sha256") == evidence_hash and
                                 summary_report_digest(summary) == summary.get("summary_report_sha256") and
                                 summary.get("cited_evidence") == resolve_evidence_values(evidence, summary.get("evidence_keys", [])) and
                                 audited)
                        if valid:
                            summaries.append({"integrity": "verified", "report": summary})
                        else:
                            summaries.append({"integrity": "invalid-or-unlinked", "artifact": expected_artifact,
                                              "error": "Summary identity, digest, cited evidence, or audit link did not verify"})
                    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
                        summaries.append({"integrity": "invalid-or-unlinked", "artifact": summary_path.name,
                                          "error": "Saved summary could not be verified"})
                return self._json(200, {"project_id": project_id, "execution_id": execution_id,
                                        "source_execution_report_sha256": execution_hash,
                                        "chain_integrity": chain, "summaries": summaries,
                                        "scope": "Verified summary digests and audit links prove artifact consistency, not narrative truth or electrical/design correctness."})
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(500, {"error": f"Agent summaries could not be verified: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["agent", "actions"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            action_path = folder / "agent_actions.jsonl"
            try:
                text = action_path.read_text("utf-8") if action_path.is_file() else ""
                records, integrity = verify_agent_actions(text)
                visible = records[-500:]
                return self._json(200, {"project_id": project_id, "count": len(visible),
                                        "total_count": integrity.get("total_count"), "actions": visible,
                                        "chain_integrity": integrity,
                                        "scope": "The hash chain detects accidental or partial edits but is unsigned; it does not prevent complete log rewriting. Full tool outputs are not retained."})
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._json(500, {"error": f"Agent action log could not be read: {exc}"})
        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "calculations":
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                model = load_circuit(folder)
                current_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                reports = []
                for report_path in folder.glob("calculation_*.json"):
                    report = json.loads(report_path.read_text("utf-8"))
                    stale = report.get("circuit_model_sha256") != current_hash
                    calculation = report.get("calculation", {})
                    reports.append({"run_id": report.get("run_id"), "kind": report.get("kind"),
                                    "created_at": report.get("created_at"), "artifact": report_path.name,
                                    "status": "stale" if stale else "current", "is_stale": stale,
                                    "circuit_model_sha256": report.get("circuit_model_sha256"),
                                    "inputs_sha256": report.get("inputs_sha256"),
                                    "source_refs": report.get("source_refs", {}),
                                    "outputs": calculation.get("outputs", {})})
                reports.sort(key=lambda item: (item.get("created_at", ""), item.get("run_id", "")))
                return self._json(200, {"circuit_model_sha256": current_hash, "calculations": reports})
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                return self._json(500, {"error": f"Calculation history could not be read: {exc}"})
        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "simulations":
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                model = load_circuit(folder)
                current_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                runs = []
                report_paths = sorted([*folder.glob("simulation_mna_*.json"), *folder.glob("simulation_transient_rc_*.json"), *folder.glob("simulation_transient_rlc_*.json"),
                                       *folder.glob("simulation_ac_sweep_*.json"), *folder.glob("simulation_dc_sweep_*.json"),
                                       *folder.glob("simulation_digital_*.json")])
                for report_path in report_paths:
                    report = json.loads(report_path.read_text("utf-8"))
                    if report.get("kind") not in {"linear_dc_mna_operating_point", "piecewise_linear_diode_dc_operating_point", "shockley_diode_dc_operating_point", "circuit_graph_bound_linear_dc_mna_operating_point", "circuit_graph_bound_piecewise_linear_diode_dc_operating_point", "circuit_graph_bound_shockley_diode_dc_operating_point", "circuit_graph_bound_linear_rc_transient", "circuit_graph_bound_linear_rlc_transient", "linear_ac_sweep", "circuit_graph_bound_linear_ac_sweep", "linear_dc_source_sweep", "bounded_diode_dc_source_sweep", "circuit_graph_bound_linear_dc_source_sweep", "circuit_graph_bound_bounded_diode_dc_source_sweep", "digital_truth_table", "digital_waveform"}:
                        continue
                    netlist = report.get("input_netlist", report.get("generated_netlist"))
                    recorded_netlist_hash = report.get("netlist_sha256")
                    if not isinstance(netlist, dict):
                        netlist_status = "missing-netlist"
                    else:
                        actual_netlist_hash = hashlib.sha256(json.dumps(netlist, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                        netlist_status = "intact" if actual_netlist_hash == recorded_netlist_hash else "hash-mismatch"
                    input_mapping = report.get("input_mapping")
                    recorded_mapping_hash = report.get("mapping_sha256")
                    if recorded_mapping_hash is None:
                        mapping_status = "not-applicable"
                    elif not isinstance(input_mapping, dict):
                        mapping_status = "missing-mapping"
                    else:
                        actual_mapping_hash = hashlib.sha256(json.dumps(input_mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                        mapping_status = "intact" if actual_mapping_hash == recorded_mapping_hash else "hash-mismatch"
                    calculation = report.get("calculation", {})
                    outputs = calculation.get("outputs", {}) if isinstance(calculation, dict) else {}
                    if isinstance(outputs, dict) and "sweep_values" in outputs:
                        values = outputs.get("sweep_values", [])
                        traces = outputs.get("node_voltage_traces_v", {})
                        outputs = {"status": outputs.get("status"), "swept_element_reference": outputs.get("swept_element_reference"),
                                   "swept_parameter": outputs.get("swept_parameter"), "sweep_unit": outputs.get("sweep_unit"),
                                   "sweep_scale": outputs.get("sweep_scale"), "point_count": outputs.get("point_count"),
                                   "start_value": values[0] if values else None, "stop_value": values[-1] if values else None,
                                   "final_node_voltages_v": {node: trace[-1] for node, trace in traces.items() if trace},
                                   "trace_node_count": len(traces),
                                   "maximum_normalized_equation_residual": outputs.get("maximum_normalized_equation_residual")}
                    elif isinstance(outputs, dict) and "time_points_s" in outputs:
                        times = outputs.get("time_points_s", [])
                        node_traces = outputs.get("node_voltage_traces_v", {})
                        cap_traces = outputs.get("capacitor_voltage_traces_v", {})
                        inductor_traces = outputs.get("inductor_current_traces_a", {})
                        outputs = {"method": outputs.get("method"), "status": outputs.get("status"),
                                   "sample_count": outputs.get("sample_count"), "time_step": outputs.get("time_step"),
                                   "stop_time": outputs.get("stop_time"), "final_time_s": times[-1] if times else None,
                                   "final_node_voltages_v": {node: trace[-1] for node, trace in node_traces.items() if trace},
                                   "final_capacitor_voltages_v": {ref: trace[-1] for ref, trace in cap_traces.items() if trace},
                                   "final_inductor_currents_a": {ref: trace[-1] for ref, trace in inductor_traces.items() if trace},
                                   "trace_node_count": len(node_traces), "trace_capacitor_count": len(cap_traces),
                                   "trace_inductor_count": len(inductor_traces)}
                    elif isinstance(outputs, dict) and "frequency_points_hz" in outputs:
                        frequencies = outputs.get("frequency_points_hz", [])
                        magnitudes = outputs.get("node_voltage_magnitude_traces_v", {})
                        phases = outputs.get("node_voltage_phase_traces_deg", {})
                        outputs = {"status": outputs.get("status"), "scale": outputs.get("scale"),
                                   "frequency_point_count": outputs.get("frequency_point_count"),
                                   "start_frequency": outputs.get("start_frequency"),
                                   "stop_frequency": outputs.get("stop_frequency"),
                                   "final_frequency_hz": frequencies[-1] if frequencies else None,
                                   "final_node_voltage_magnitudes_v": {node: trace[-1] for node, trace in magnitudes.items() if trace},
                                   "final_node_voltage_phases_deg": {node: trace[-1] for node, trace in phases.items() if trace},
                                   "trace_node_count": len(magnitudes)}
                    elif isinstance(outputs, dict) and "waveform" in outputs:
                        waveform = outputs.get("waveform", [])
                        outputs = {"status": outputs.get("status"), "sample_count": len(waveform),
                                   "sample_interval_s": outputs.get("sample_interval_s"),
                                   "final_time_s": waveform[-1].get("time_s") if waveform else None,
                                   "primary_input_count": len(outputs.get("primary_inputs", [])),
                                   "primary_output_count": len(outputs.get("primary_outputs", [])),
                                   "gate_count": outputs.get("gate_count"),
                                   "primary_inputs": outputs.get("primary_inputs", []),
                                   "primary_outputs": outputs.get("primary_outputs", []),
                                   "final_outputs": waveform[-1].get("outputs", {}) if waveform else {}}
                    elif isinstance(outputs, dict) and "truth_table" in outputs:
                        outputs = {"status": outputs.get("status"), "row_count": outputs.get("row_count"),
                                   "primary_input_count": len(outputs.get("primary_inputs", [])),
                                   "primary_output_count": len(outputs.get("primary_outputs", [])),
                                   "gate_count": outputs.get("gate_count"),
                                   "primary_inputs": outputs.get("primary_inputs", []),
                                   "primary_outputs": outputs.get("primary_outputs", [])}
                    runs.append({"run_id": report.get("run_id"), "kind": report.get("kind"),
                                 "created_at": report.get("created_at"), "artifact": report_path.name,
                                 "model_status": "same-at-run" if report.get("circuit_model_sha256") == current_hash else "model-changed-since-run",
                                 "netlist_status": netlist_status, "netlist_sha256": recorded_netlist_hash,
                                 "mapping_status": mapping_status,
                                 "circuit_model_sha256": report.get("circuit_model_sha256"),
                                 "outputs": outputs})
                runs.sort(key=lambda item: (item.get("created_at", ""), item.get("run_id", "")))
                return self._json(200, {"current_circuit_model_sha256": current_hash, "simulations": runs})
            except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(500, {"error": f"Simulation history could not be read: {exc}"})
        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "datasheets":
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            sources_path = folder / "datasheet_sources.json"
            if not sources_path.exists():
                return self._json(200, {"sources": []})
            try:
                data = json.loads(sources_path.read_text("utf-8"))
                return self._json(200, {"sources": data.get("sources", [])})
            except (OSError, json.JSONDecodeError, AttributeError) as exc:
                return self._json(500, {"error": f"Datasheet source index could not be read: {exc}"})
        if len(parts) == 4 and parts[0:2] == ["api", "projects"] and parts[3] in {"circuit", "snapshots"}:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            if parts[3] == "snapshots":
                return self._json(200, list_snapshots(project_id, folder))
            try:
                model = load_circuit(folder)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                return self._json(500, {"error": f"Circuit model could not be loaded: {exc}"})
            return self._json(200, {"model": model, "topology_report": validate_model(model)})
        if len(parts) == 4 and parts[0:2] == ["api", "projects"] and parts[3] == "export":
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            archive = make_zip(folder)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="circuitmind-{project_id}.zip"')
            self.send_header("Content-Length", str(len(archive)))
            self.end_headers()
            self.wfile.write(archive)
            return
        if len(parts) == 4 and parts[0:2] == ["api", "projects"] and parts[3] == "artifacts":
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            names = sorted(item.name for item in folder.iterdir() if item.is_file())
            return self._json(200, {"artifacts": names})
        if len(parts) == 5 and parts[0:2] == ["api", "projects"] and parts[3] == "artifact":
            project_id, filename = parts[2], unquote(parts[4])
            if not _valid_id(project_id) or filename not in _allowed_artifacts(project_id):
                return self._json(404, {"error": "Artifact not found"})
            payload = (DATA_DIR / "projects" / project_id / filename).read_bytes()
            if filename.endswith((".raw", ".pdf", ".zip", ".csv")):
                self.send_response(200)
                content_type = ("application/pdf" if filename.endswith(".pdf") else
                                "application/zip" if filename.endswith(".zip") else
                                "text/csv; charset=utf-8" if filename.endswith(".csv") else "application/octet-stream")
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                return self._json(415, {"error": "Artifact is binary; use its download link"})
            return self._json(200, {"name": filename, "content": text})
        return self._static(parsed.path)

    def _static(self, url_path: str) -> None:
        relative = "index.html" if url_path in ("/", "") else unquote(url_path.lstrip("/"))
        candidate = (WEB / relative).resolve()
        if WEB.resolve() not in candidate.parents or not candidate.is_file():
            candidate = WEB / "index.html"
        try:
            body = candidate.read_bytes()
        except OSError:
            return self._json(404, {"error": "Not found"})
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type in ("text/html", "text/css", "application/javascript"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if path == "/api/agent/providers/probe":
            try:
                payload = self._read_payload(2_000)
                if payload:
                    raise ValueError("Provider probe accepts only an empty JSON object")
                return self._json(200, probe_agent_provider())
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3:] == ["agent", "workflows", "run"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(2_000)
                if set(payload) != {"workflow"} or payload.get("workflow") != "circuit_review":
                    raise ValueError("Only the deterministic 'circuit_review' workflow is registered")
                run_id = uuid.uuid4().hex
                created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                outputs = {}
                step_records = []
                audit_rows = []
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    for tool_name in CIRCUIT_REVIEW_STEPS:
                        started = time.perf_counter()
                        try:
                            output = execute_agent_tool(tool_name, {}, project_id, folder)
                            outputs[tool_name] = output
                            output_bytes = json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
                            output_hash = hashlib.sha256(output_bytes).hexdigest()
                            step_status = "completed"
                            error = None
                        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
                            output_hash = hashlib.sha256(str(exc).encode("utf-8")).hexdigest()
                            step_status = "failed"
                            error = str(exc)
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                        step_records.append({"tool": tool_name, "status": step_status,
                                             "result_sha256": output_hash, "duration_ms": elapsed_ms,
                                             "error": error})
                        audit_rows.append({"execution_id": run_id, "call_id": uuid.uuid4().hex,
                                           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                           "tool": tool_name, "permission": "read-only", "status": step_status,
                                           "arguments_sha256": hashlib.sha256(b"{}").hexdigest(),
                                           "result_sha256": output_hash, "duration_ms": elapsed_ms})
                    result = evaluate_circuit_review(project_id, outputs)
                    report = {"schema_version": 1, "workflow": "circuit_review", "run_id": run_id,
                              "project_id": project_id, "created_at": created_at, "status": result["status"],
                              "plan": {"steps": list(CIRCUIT_REVIEW_STEPS),
                                       "policy": "Execute registered read-only tools in fixed order, then check identity, saved topology, and model-hash consistency."},
                              "steps": step_records, "result": result}
                    report_name = f"agent_workflow_{run_id}.json"
                    report["artifact"] = report_name
                    report_bytes = (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                    _atomic_bytes(folder / report_name, report_bytes)
                    report_hash = hashlib.sha256(report_bytes).hexdigest()
                    audit_rows.append({"execution_id": run_id, "call_id": uuid.uuid4().hex,
                                       "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       "tool": "workflow.circuit_review", "permission": "read-only",
                                       "status": result["status"], "arguments_sha256": hashlib.sha256(b"circuit_review").hexdigest(),
                                       "result_sha256": report_hash, "duration_ms": 0.0})
                    _append_agent_actions(folder, audit_rows)
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Agent workflow could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["agent", "proposals"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(MAX_BODY)
                if set(payload) != {"tool", "arguments"} or payload.get("tool") != "circuit.replace_model":
                    raise ValueError("Proposal request must target circuit.replace_model with explicit arguments")
                arguments = payload["arguments"]
                if not isinstance(arguments, dict) or set(arguments) != {"model"} or not isinstance(arguments["model"], dict):
                    raise ValueError("circuit.replace_model arguments must contain one circuit model object")
                candidate = migrate_model(arguments["model"])
                validation = validate_model(candidate)
                if not validation["topology_valid"]:
                    return self._json(422, {"error": "Proposed circuit model failed schema/topology checks", "topology_report": validation})
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    current_hash = model_digest(load_circuit(folder))
                    candidate_hash = model_digest(candidate)
                    if candidate_hash == current_hash:
                        raise ValueError("Proposed circuit model is identical to the current saved model")
                    now = datetime.now(timezone.utc)
                    proposal_id = uuid.uuid4().hex
                    proposal = {"proposal_id": proposal_id, "project_id": project_id,
                                "tool": "circuit.replace_model", "status": "awaiting-approval",
                                "created_at": now.isoformat(timespec="seconds"),
                                "expires_at": (now + timedelta(minutes=30)).isoformat(timespec="seconds"),
                                "base_model_sha256": current_hash, "candidate_model_sha256": candidate_hash,
                                "validation": validation, "model": candidate,
                                "scope": "Explicit candidate circuit model; this proposal does not infer values or perform electrical analysis."}
                    proposal_path = folder / f"agent_proposal_{proposal_id}.json"
                    _atomic_bytes(proposal_path, (json.dumps(proposal, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                    audit = _agent_audit_row("circuit.replace_model", "awaiting-approval", candidate_hash, candidate_hash)
                    _append_agent_actions(folder, [audit])
                    _update_project_record(folder)
                return self._json(201, {"proposal": _agent_proposal_summary(proposal),
                                        "review_url": f"/api/projects/{project_id}/agent/proposals/{proposal_id}",
                                        "approval_url": f"/api/projects/{project_id}/agent/proposals/{proposal_id}/approve",
                                        "requires_separate_approval": True})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})
            except (FileNotFoundError, OSError) as exc:
                return self._json(500, {"error": f"Agent proposal could not be stored: {exc}"})
        if len(parts) == 7 and parts[:2] == ["api", "projects"] and parts[3] == "agent" and parts[4] == "proposals" and parts[6] == "approve":
            project_id, proposal_id = parts[2], parts[5]
            if not _valid_id(project_id) or not _valid_agent_proposal_id(proposal_id):
                return self._json(400, {"error": "Invalid project or proposal id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(2_000)
                if set(payload) != {"approve"} or payload.get("approve") is not True:
                    raise ValueError("Approval requires the explicit JSON body {\"approve\": true}")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    proposal = _read_agent_proposal(folder, proposal_id)
                    if proposal.get("project_id") != project_id:
                        return self._json(409, {"error": "Agent proposal belongs to a different project"})
                    if proposal.get("status") != "awaiting-approval":
                        return self._json(409, {"error": "Agent proposal is no longer awaiting approval"})
                    expires_at = datetime.fromisoformat(proposal["expires_at"])
                    if datetime.now(timezone.utc) >= expires_at:
                        proposal["status"] = "expired"
                        _atomic_bytes(folder / f"agent_proposal_{proposal_id}.json",
                                      (json.dumps(proposal, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                        _update_project_record(folder)
                        return self._json(410, {"error": "Agent proposal expired; submit a new proposal"})
                    current_hash = model_digest(load_circuit(folder))
                    if current_hash != proposal.get("base_model_sha256"):
                        proposal["status"] = "stale"
                        _atomic_bytes(folder / f"agent_proposal_{proposal_id}.json",
                                      (json.dumps(proposal, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                        _update_project_record(folder)
                        return self._json(409, {"error": "Saved circuit changed since proposal creation; review a fresh proposal"})
                    candidate = migrate_model(proposal.get("model"))
                    candidate_hash = model_digest(candidate)
                    if candidate_hash != proposal.get("candidate_model_sha256"):
                        return self._json(409, {"error": "Proposal model hash no longer matches its reviewed hash"})
                    validation = validate_model(candidate)
                    if not validation["topology_valid"]:
                        return self._json(422, {"error": "Proposed circuit model no longer passes validation", "topology_report": validation})
                    snapshots = save_circuit(project_id, folder, candidate)
                    export_report = _refresh_circuit_artifacts(project_id, folder, candidate)
                    approved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    proposal.update({"status": "approved-and-applied", "approved_at": approved_at,
                                     "applied_model_sha256": candidate_hash})
                    _atomic_bytes(folder / f"agent_proposal_{proposal_id}.json",
                                  (json.dumps(proposal, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                    audit = _agent_audit_row("circuit.replace_model", "approved-and-applied", candidate_hash, candidate_hash)
                    _append_agent_actions(folder, [audit])
                    _update_project_record(folder)
                return self._json(200, {"status": "approved-and-applied", "proposal_id": proposal_id,
                                        "circuit_model_sha256": candidate_hash, "topology_report": validation,
                                        "snapshots": snapshots, "kicad_export": export_report,
                                        "scope": "The explicitly proposed model was applied after a separate approval request. Topology checks are not electrical analysis or design approval."})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except FileNotFoundError:
                return self._json(404, {"error": "Agent proposal not found"})
            except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Agent proposal could not be applied: {exc}"})
        if len(parts) == 7 and parts[:2] == ["api", "projects"] and parts[3] == "agent" and parts[4] == "executions" and parts[6] == "summarize":
            project_id, execution_id = parts[2], parts[5]
            if not _valid_id(project_id) or not _valid_agent_plan_id(execution_id):
                return self._json(400, {"error": "Invalid project or execution id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(2_000)
                if payload:
                    raise ValueError("Evidence summary request accepts only an empty JSON object")
                report_name = f"agent_execution_{execution_id}.json"
                report_path = folder / report_name
                if not report_path.is_file():
                    return self._json(404, {"error": "Agent execution report not found"})
                if report_path.stat().st_size > MAX_AGENT_REPORT_BYTES:
                    return self._json(409, {"error": "Execution report exceeds the verification size limit"})
                report_bytes = report_path.read_bytes()
                execution_report = json.loads(report_bytes.decode("utf-8"))
                if (not isinstance(execution_report, dict) or execution_report.get("execution_id") != execution_id or
                        execution_report.get("project_id") != project_id or execution_report.get("artifact") != report_name):
                    return self._json(409, {"error": "Execution report identity check failed"})
                if execution_report_digest(execution_report) != execution_report.get("execution_report_sha256"):
                    return self._json(409, {"error": "Execution report integrity check failed"})
                with _CIRCUIT_API_LOCK:
                    action_path = folder / "agent_actions.jsonl"
                    audit_rows, audit_state = verify_agent_actions(action_path.read_text("utf-8") if action_path.is_file() else "")
                    report_hash = hashlib.sha256(report_bytes).hexdigest()
                    linked_execution = any(row.get("tool") == "agent.plan.execute" and
                                           row.get("execution_id") == execution_id and
                                           row.get("result_sha256") == report_hash for row in audit_rows)
                    if audit_state.get("status") != "intact" or not linked_execution:
                        return self._json(409, {"error": "Execution report is not linked to a verified audit-chain record"})
                evidence = execution_report.get("evidence")
                if not isinstance(evidence, dict):
                    return self._json(409, {"error": "Execution report has no compact evidence for summarization"})
                evidence_bytes = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
                started = time.perf_counter()
                try:
                    generated = generate_evidence_summary(evidence)
                except AgentProviderError as exc:
                    audit = _agent_audit_row("agent.execution.summarize", "provider-error", evidence_hash,
                                              hashlib.sha256(str(exc).encode("utf-8")).hexdigest(), permission="summary-only")
                    audit["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    with _CIRCUIT_API_LOCK:
                        _append_agent_actions(folder, [audit]); _update_project_record(folder)
                    return self._json(exc.status_code, {"error": str(exc)})
                try:
                    if generated.get("evidence_sha256") != evidence_hash:
                        raise ValueError("Provider summary evidence hash does not match the checked execution record")
                    parsed_summary = parse_evidence_summary(generated["content"], evidence)
                except ValueError as exc:
                    audit = _agent_audit_row("agent.execution.summarize", "summary-rejected", evidence_hash,
                                              generated["completion_sha256"], permission="summary-only")
                    audit["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    with _CIRCUIT_API_LOCK:
                        _append_agent_actions(folder, [audit]); _update_project_record(folder)
                    return self._json(422, {"error": f"Provider evidence summary rejected by validator: {exc}",
                                            "completion_sha256": generated["completion_sha256"]})
                summary_id = uuid.uuid4().hex
                summary_report = {"schema_version": 1, "summary_id": summary_id,
                                  "project_id": project_id, "execution_id": execution_id,
                                  "source_execution_report_sha256": report_hash,
                                  "evidence_sha256": evidence_hash,
                                  "provider": generated["provider"], "model": generated["model"],
                                  "completion_sha256": generated["completion_sha256"],
                                  "status": "ai-generated-unverified", "summary": parsed_summary["summary"],
                                  "evidence_keys": parsed_summary["evidence_keys"],
                                  "cited_evidence": resolve_evidence_values(evidence, parsed_summary["evidence_keys"]),
                                  "limitations": ["AI-generated explanation of compact tool evidence; not a tool result.",
                                                  "No electrical, safety, rating, physical-routing, or design-approval conclusion is established."],
                                  "artifact": f"agent_summary_{summary_id}.json"}
                summary_report["summary_report_sha256"] = summary_report_digest(summary_report)
                summary_bytes = (json.dumps(summary_report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                audit = _agent_audit_row("agent.execution.summarize", "ai-generated-unverified",
                                          evidence_hash, hashlib.sha256(summary_bytes).hexdigest(),
                                          permission="summary-only")
                audit["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                with _CIRCUIT_API_LOCK:
                    current_report_bytes = report_path.read_bytes()
                    current_rows, current_audit_state = verify_agent_actions(
                        (folder / "agent_actions.jsonl").read_text("utf-8")
                        if (folder / "agent_actions.jsonl").is_file() else "")
                    still_linked = any(row.get("tool") == "agent.plan.execute" and
                                       row.get("execution_id") == execution_id and
                                       row.get("result_sha256") == report_hash for row in current_rows)
                    if (hashlib.sha256(current_report_bytes).hexdigest() != report_hash or
                            current_audit_state.get("status") != "intact" or not still_linked):
                        return self._json(409, {"error": "Execution evidence changed while the local summary was being generated"})
                    _atomic_bytes(folder / summary_report["artifact"], summary_bytes)
                    _append_agent_actions(folder, [audit])
                    _update_project_record(folder)
                return self._json(201, summary_report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except FileNotFoundError:
                return self._json(404, {"error": "Agent execution report not found"})
            except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Execution evidence could not be summarized: {exc}"})
        if len(parts) == 7 and parts[:2] == ["api", "projects"] and parts[3] == "agent" and parts[4] == "plans" and parts[6] == "execute":
            project_id, plan_id = parts[2], parts[5]
            if not _valid_id(project_id) or not _valid_agent_plan_id(plan_id):
                return self._json(400, {"error": "Invalid project or plan id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(2_000)
                if set(payload) != {"execute"} or payload.get("execute") is not True:
                    raise ValueError("Plan execution requires the explicit JSON body {\"execute\": true}")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    plan_record = _read_agent_plan(folder, project_id, plan_id)
                    if plan_record.get("status") != "awaiting-user-review":
                        return self._json(409, {"error": "Agent plan is no longer awaiting review"})
                    parsed_plan = parse_read_only_plan(json.dumps({"summary": plan_record.get("summary"),
                                                                   "calls": plan_record.get("calls")}))
                    if agent_plan_digest(plan_record) != plan_record.get("plan_sha256"):
                        return self._json(409, {"error": "Agent plan hash verification failed; it may have changed since review"})
                    execution_id = uuid.uuid4().hex
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    results = []
                    step_records = []
                    audit_rows = []
                    for call in parsed_plan["calls"]:
                        started = time.perf_counter()
                        try:
                            output = execute_agent_tool(call["tool"], call["arguments"], project_id, folder)
                            result_item = {"tool": call["tool"], "status": "completed", "output": output}
                            digest_bytes = json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
                            output_hash = hashlib.sha256(digest_bytes).hexdigest()
                            error = None
                        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
                            result_item = {"tool": call["tool"], "status": "failed", "error": str(exc)}
                            output_hash = hashlib.sha256(str(exc).encode("utf-8")).hexdigest()
                            error = str(exc)
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                        result_item["audit"] = {"result_sha256": output_hash, "duration_ms": elapsed_ms}
                        results.append(result_item)
                        step_records.append({"tool": call["tool"], "status": result_item["status"],
                                             "result_sha256": output_hash, "duration_ms": elapsed_ms,
                                             "error": error})
                        audit_rows.append({"execution_id": execution_id, "call_id": uuid.uuid4().hex,
                                           "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                           "tool": call["tool"], "permission": "read-only",
                                           "status": result_item["status"],
                                           "arguments_sha256": hashlib.sha256(b"{}").hexdigest(),
                                           "result_sha256": output_hash, "duration_ms": elapsed_ms})
                    evaluation = check_read_only_execution(project_id, parsed_plan["calls"], results)
                    report_name = f"agent_execution_{execution_id}.json"
                    execution_report = {"schema_version": 1, "execution_id": execution_id,
                                        "plan_id": plan_id, "plan_sha256": plan_record["plan_sha256"],
                                        "project_id": project_id, "created_at": created_at,
                                        "status": evaluation["status"], "steps": step_records,
                                        "checks": evaluation["checks"],
                                        "evidence": build_compact_execution_evidence(project_id, parsed_plan["calls"], results, evaluation),
                                        "limitations": evaluation["limitations"],
                                        "artifact": report_name}
                    execution_report["execution_report_sha256"] = execution_report_digest(execution_report)
                    report_bytes = (json.dumps(execution_report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                    report_hash = hashlib.sha256(report_bytes).hexdigest()
                    plan_record.update({"status": "executed", "executed_at": created_at,
                                        "execution_status": evaluation["status"],
                                        "execution_report": report_name})
                    plan_bytes = (json.dumps(plan_record, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                    _atomic_bytes(folder / report_name, report_bytes)
                    _atomic_bytes(folder / f"agent_plan_{plan_id}.json", plan_bytes)
                    audit_rows.append({"execution_id": execution_id, "call_id": uuid.uuid4().hex,
                                       "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                       "tool": "agent.plan.execute", "permission": "read-only",
                                       "status": evaluation["status"],
                                       "arguments_sha256": plan_record["plan_sha256"],
                                       "result_sha256": report_hash, "duration_ms": 0.0})
                    _append_agent_actions(folder, audit_rows)
                    _update_project_record(folder)
                return self._json(201, {"report": execution_report, "calls": results,
                                        "plan_status": "executed", "scope": "Reviewed read-only calls ran against saved project state. Circuit data was not modified."})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except FileNotFoundError:
                return self._json(404, {"error": "Agent plan not found"})
            except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Agent plan execution could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["agent", "plan"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(16_000)
                if set(payload) != {"prompt"}:
                    raise ValueError("Plan request must contain only a 'prompt' string")
                prompt = payload["prompt"]
                if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 2_000:
                    raise ValueError("Prompt must contain 1 to 2,000 characters")
                started = time.perf_counter()
                try:
                    generated = generate_agent_plan(prompt)
                except AgentProviderError as exc:
                    audit = _agent_audit_row("agent.plan", "provider-error",
                                              hashlib.sha256(prompt.strip().encode("utf-8")).hexdigest(),
                                              hashlib.sha256(str(exc).encode("utf-8")).hexdigest(),
                                              permission="plan-only")
                    audit["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    with _CIRCUIT_API_LOCK:
                        _append_agent_actions(folder, [audit])
                        _update_project_record(folder)
                    return self._json(exc.status_code, {"error": str(exc)})
                try:
                    plan = parse_read_only_plan(generated["content"])
                except ValueError as exc:
                    audit = _agent_audit_row("agent.plan", "plan-rejected", generated["prompt_sha256"],
                                              generated["completion_sha256"], permission="plan-only")
                    audit["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    with _CIRCUIT_API_LOCK:
                        _append_agent_actions(folder, [audit])
                        _update_project_record(folder)
                    return self._json(422, {"error": f"Provider plan rejected by the read-only plan validator: {exc}",
                                            "completion_sha256": generated["completion_sha256"]})
                plan_id = uuid.uuid4().hex
                created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                report = {"schema_version": 1, "plan_id": plan_id, "project_id": project_id,
                          "created_at": created_at, "status": "awaiting-user-review",
                          "provider": generated["provider"], "model": generated["model"],
                          "prompt_sha256": generated["prompt_sha256"],
                          "completion_sha256": generated["completion_sha256"],
                          "summary": plan["summary"], "calls": plan["calls"],
                          "artifact": f"agent_plan_{plan_id}.json",
                          "execution_url": f"/api/projects/{project_id}/agent/plans/{plan_id}/execute",
                          "scope": "A local model proposed read-only tool calls from the user prompt and tool descriptions only. No project files were sent and no tool has been executed. Review before execution."}
                report["plan_sha256"] = agent_plan_digest(report)
                report_bytes = (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                audit = _agent_audit_row("agent.plan", "awaiting-user-review", generated["prompt_sha256"],
                                          generated["completion_sha256"], permission="plan-only")
                audit["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                with _CIRCUIT_API_LOCK:
                    _atomic_bytes(folder / report["artifact"], report_bytes)
                    _append_agent_actions(folder, [audit])
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Agent plan could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["agent", "execute"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(64_000)
                if not isinstance(payload, dict) or set(payload) != {"calls"}:
                    raise ValueError("Request must contain only a 'calls' array")
                calls = payload["calls"]
                if not isinstance(calls, list) or not 1 <= len(calls) <= 8:
                    raise ValueError("Provide between 1 and 8 tool calls per request")
                validated_calls = []
                registered = {tool["name"] for tool in TOOL_CATALOG}
                for index, call in enumerate(calls):
                    if not isinstance(call, dict) or set(call) != {"tool", "arguments"}:
                        raise ValueError(f"Call {index} must contain exactly 'tool' and 'arguments'")
                    if not isinstance(call["tool"], str):
                        raise ValueError(f"Call {index} tool name must be a string")
                    if call["tool"] in {tool["name"] for tool in APPROVAL_TOOL_CATALOG}:
                        raise ValueError(f"Tool {call['tool']!r} requires the separate proposal and approval flow")
                    if call["tool"] not in registered:
                        raise ValueError(f"Call {index} references an unregistered tool")
                    if not isinstance(call["arguments"], dict) or call["arguments"]:
                        raise ValueError(f"Tool {call['tool']!r} accepts no arguments")
                    validated_calls.append(call)
                execution_id = uuid.uuid4().hex
                outcomes = []
                audit_rows = []
                for call in validated_calls:
                    started = time.perf_counter()
                    try:
                        output = execute_agent_tool(call["tool"], call["arguments"], project_id, folder)
                        outcome = {"tool": call["tool"], "status": "completed", "output": output}
                        output_bytes = json.dumps(output, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
                        result_hash = hashlib.sha256(output_bytes).hexdigest()
                    except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
                        outcome = {"tool": call["tool"], "status": "failed", "error": str(exc)}
                        result_hash = hashlib.sha256(str(exc).encode("utf-8")).hexdigest()
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                    audit = {"execution_id": execution_id, "call_id": uuid.uuid4().hex,
                             "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "tool": call["tool"], "permission": "read-only", "status": outcome["status"],
                             "arguments_sha256": hashlib.sha256(b"{}").hexdigest(),
                             "result_sha256": result_hash, "duration_ms": elapsed_ms}
                    outcomes.append({**outcome, "audit": audit})
                    audit_rows.append(audit)
                with _CIRCUIT_API_LOCK:
                    _append_agent_actions(folder, audit_rows)
                    _update_project_record(folder)
                failures = sum(item["status"] != "completed" for item in outcomes)
                return self._json(200, {"execution_id": execution_id,
                                        "status": "completed" if not failures else "partial",
                                        "calls": outcomes,
                                        "policy": "Only registered read-only tools can execute. No provider, autonomous planner, circuit mutation, or electrical-value inference is enabled."})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Agent action log could not be stored: {exc}"})
        if path == "/api/projects/import":
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/zip":
                return self._json(415, {"error": "Project backup upload must use Content-Type application/zip"})
            temporary = None
            try:
                raw = self._read_raw_body(MAX_ARCHIVE_BYTES)
                source_id, source_project, files = read_project_archive(raw)
                project_id = uuid.uuid4().hex[:12]
                created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                project = dict(source_project)
                project.update({"project_id": project_id, "imported_from_project_id": source_id,
                                "imported_at": created_at})
                projects_dir = DATA_DIR / "projects"
                projects_dir.mkdir(parents=True, exist_ok=True)
                temporary = Path(tempfile.mkdtemp(prefix=".project-import-", dir=DATA_DIR))
                for relative, contents in files.items():
                    if relative in {"project.json", "snapshots/manifest.json"}:
                        continue
                    destination = temporary.joinpath(*relative.split("/"))
                    _atomic_bytes(destination, contents)
                manifest = files.get("snapshots/manifest.json")
                if manifest is not None:
                    snapshot_manifest = json.loads(manifest.decode("utf-8"))
                    snapshot_manifest["project_id"] = project_id
                    _atomic_bytes(temporary / "snapshots" / "manifest.json",
                                  (json.dumps(snapshot_manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                _atomic_bytes(temporary / "project.json",
                              (json.dumps(project, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                _update_project_record(temporary)
                target = projects_dir / project_id
                with _CIRCUIT_API_LOCK:
                    if target.exists():
                        raise OSError("Generated project ID already exists; retry the import")
                    os.replace(temporary, target)
                    temporary = None
                imported_project = json.loads((target / "project.json").read_text("utf-8"))
                return self._json(201, {"project": imported_project, "source_project_id": source_id,
                                         "imported_at": created_at,
                                         "artifact_count": len(imported_project.get("artifacts", [])),
                                         "scope": "Project archive was imported as a new project ID. Circuit topology was validated; no electrical or artifact-specific validation was performed."})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Project archive could not be imported: {exc}"})
            finally:
                if temporary is not None:
                    shutil.rmtree(temporary, ignore_errors=True)
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["designs", "rc-low-pass"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                if set(payload) != {"request_version", "inputs", "apply_to_circuit"} or not isinstance(payload.get("inputs"), dict):
                    raise ValueError("Request must contain exactly request_version, inputs, and apply_to_circuit")
                if type(payload.get("request_version")) is not int or payload["request_version"] != 1:
                    raise ValueError("Only RC low-pass synthesis request_version 1 is supported")
                if not isinstance(payload.get("apply_to_circuit"), bool):
                    raise ValueError("apply_to_circuit must be an explicit boolean")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    current_model = load_circuit(folder)
                    current_topology = validate_model(current_model)
                    if not current_topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model failed schema/topology checks", "topology_report": current_topology})
                    result = synthesize_rc_lowpass(payload["inputs"])
                    candidate = result["circuit_model_candidate"]
                    apply_refused_reason = None
                    applied = payload["apply_to_circuit"]
                    if applied and result["constraints"]["nominal_cutoff_error"] != "pass":
                        applied = False
                        apply_refused_reason = "Candidate failed the user-defined nominal cutoff error constraint; it was retained as an artifact but not applied to the saved graph."
                    if applied:
                        snapshots = save_circuit(project_id, folder, candidate)
                        export_report = _refresh_circuit_artifacts(project_id, folder, candidate)
                    else:
                        snapshots = None
                        export_report = None
                    current_hash = hashlib.sha256(json.dumps(load_circuit(folder), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    request_document = {"request_version": payload["request_version"], "inputs": payload["inputs"],
                                        "apply_to_circuit": applied}
                    request_hash = hashlib.sha256(json.dumps(request_document, sort_keys=True, separators=(",", ":"),
                                                               ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    report_name = f"design_rc_lowpass_{run_id}.json"
                    report = {"run_id": run_id, "kind": "synthesized_single_pole_rc_lowpass", "created_at": created_at,
                              "request_version": payload["request_version"], "request_sha256": request_hash,
                              "workflow_version": result["workflow_version"], "requirements": result["requirements"],
                              "candidate_circuit_model_sha256": result["circuit_model_sha256"],
                              "current_saved_circuit_model_sha256": current_hash,
                              "candidate_is_current_saved_model": result["circuit_model_sha256"] == current_hash,
                              "apply_requested": payload["apply_to_circuit"], "applied_to_circuit_model": applied,
                              "apply_refused_reason": apply_refused_reason,
                              "snapshots": snapshots,
                              "kicad_export": export_report, "design": result, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    with (folder / "design_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "request_sha256": request_hash,
                                              "candidate_circuit_model_sha256": result["circuit_model_sha256"],
                                              "current_saved_circuit_model_sha256": current_hash,
                                              "applied_to_circuit_model": applied, "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except CppEngineUnavailable as exc:
                return self._json(503, {"error": str(exc)})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"RC low-pass synthesis report could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["firmware", "scaffold"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(100_000)
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    project = json.loads((folder / "project.json").read_text("utf-8"))
                    if project.get("architecture") != "esp32":
                        raise ValueError("Firmware scaffolding is only applicable to projects explicitly declared with architecture 'esp32'")
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model failed schema/topology checks", "topology_report": topology})
                    generated = generate_esp32_scaffold(payload, model)
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    request_hash = generated["manifest"]["configuration_sha256"]
                    manifest_name = f"firmware_scaffold_{run_id}.json"
                    source_name = f"firmware_main_{run_id}.cpp"
                    config_name = f"platformio_{run_id}.ini"
                    archive_name = f"firmware_project_{run_id}.zip"
                    manifest = {**generated["manifest"], "run_id": run_id, "artifact_names": {
                        "manifest": manifest_name, "source": source_name, "platformio_config": config_name,
                        "project_zip": archive_name}}
                    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                    _atomic_bytes(folder / manifest_name, manifest_bytes)
                    _atomic_bytes(folder / source_name, generated["files"]["src/main.cpp"])
                    _atomic_bytes(folder / config_name, generated["files"]["platformio.ini"])
                    _atomic_bytes(folder / archive_name, generated["files"]["firmware_project.zip"])
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report = {"run_id": run_id, "kind": "esp32_firmware_scaffold", "created_at": created_at,
                              "status": "scaffold-generated-uncompiled", "request_sha256": request_hash,
                              "circuit_model_sha256": model_hash,
                              "generated_sha256": {"manifest": hashlib.sha256(manifest_bytes).hexdigest(),
                                                   "source": generated["sha256"]["src/main.cpp"],
                                                   "platformio_config": generated["sha256"]["platformio.ini"],
                                                   "project_zip": generated["sha256"]["firmware_project.zip"]},
                              "target": generated["manifest"]["target"],
                              "pin_mappings": generated["manifest"]["pin_mappings"],
                              "warnings": generated["manifest"]["limitations"],
                              "artifacts": {"manifest": manifest_name, "source": source_name,
                                            "platformio_config": config_name, "project_zip": archive_name}}
                    with (folder / "firmware_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps(report, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    validation_path = folder / "validation_report.json"
                    validation = json.loads(validation_path.read_text("utf-8")) if validation_path.exists() else {"checks": {}, "findings": []}
                    checks = validation.setdefault("checks", {})
                    checks["firmware_scaffold"] = "generated from explicit GPIO-to-net mapping"
                    checks["firmware_compile"] = "not-run (scaffold generated; no compiler invoked)"
                    _atomic_bytes(validation_path, (json.dumps(validation, indent=2, ensure_ascii=False) + "\n").encode())
                    _update_project_record(folder, validation)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Firmware scaffold could not be stored: {exc}"})
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3] == "simulations" and parts[4] in {"transient-rc", "transient-rlc"} and parts[5] == "from-model":
            is_rlc = parts[4] == "transient-rlc"
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                if set(payload) != {"mapping", "time_step", "stop_time"} or not isinstance(payload.get("mapping"), dict):
                    raise ValueError("Request must contain exactly mapping, time_step, and stop_time")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model failed schema/topology checks", "topology_report": topology})
                    resolved = map_circuit_graph_to_mna(model, payload["mapping"], allow_capacitors=True,
                                                       allow_inductors=is_rlc)
                    simulation_input = {"ground": resolved["netlist"]["ground"], "time_step": payload["time_step"],
                                        "stop_time": payload["stop_time"], "method": "backward_euler",
                                        "elements": resolved["netlist"]["elements"]}
                    calculation = simulate_transient_rc(simulation_input)
                    calculation["scope"] = "Numerical backward-Euler time-domain simulation with connectivity from the saved graph and explicit user component types, ordered pins, values, sources, and initial conditions; not datasheet or design approval."
                    calculation["warnings"].append("Graph connectivity came from the saved model; element types, values, source waveform, and reactive-element initial conditions came from the per-run user mapping.")
                    if resolved["excluded_components"]:
                        calculation["warnings"].append("Excluded components were omitted; this transient solve covers only the explicitly mapped subnetwork.")
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    mapping = payload["mapping"]
                    mapping_hash = hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    netlist_hash = hashlib.sha256(json.dumps(simulation_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    transient_kind = "circuit_graph_bound_linear_rlc_transient" if is_rlc else "circuit_graph_bound_linear_rc_transient"
                    report_prefix = "simulation_transient_rlc" if is_rlc else "simulation_transient_rc"
                    report_name = f"{report_prefix}_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report = {"run_id": run_id, "kind": transient_kind, "created_at": created_at,
                              "circuit_model_sha256": model_hash, "mapping_sha256": mapping_hash, "netlist_sha256": netlist_hash,
                              "input_mapping": mapping, "mapping_report": resolved,
                              "component_coverage": resolved["component_coverage"],
                              "input_values_source": "saved graph supplies connectivity; per-run explicit mapping supplies R/C/L/source models, waveform, pin order, and storage-element initial conditions",
                              "calculation": calculation, "generated_netlist": simulation_input, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    with (folder / "simulation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "mapping_sha256": mapping_hash,
                                              "netlist_sha256": netlist_hash, "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Transient report could not be stored: {exc}"})
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3:] == ["simulations", "dc-operating-point", "from-model"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                mapping = payload.get("mapping")
                if not isinstance(mapping, dict):
                    raise ValueError("Request must include an explicit 'mapping' object")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model failed schema/topology checks", "topology_report": topology})
                    resolved = map_circuit_graph_to_mna(model, mapping)
                    calculation = solve_dc_operating_point(resolved["netlist"])
                    calculation["warnings"][1] = "Netlist topology derives from saved graph nets; component type, ordered pins, and numeric values are explicit user mappings, not datasheet-verified."
                    if resolved["excluded_components"]:
                        calculation["warnings"].append("Excluded components were omitted; this is a partial model of the saved circuit graph.")
                    calculation["scope"] = "Numerical bounded DC operating-point solution built from saved graph connectivity plus explicit user type/value/pin/model mappings; diode models are limited to the selected piecewise-linear or Shockley law, not general SPICE validation or datasheet approval."
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    mapping_hash = hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    netlist = resolved["netlist"]
                    netlist_hash = hashlib.sha256(json.dumps(netlist, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"simulation_mna_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report_kind = ("circuit_graph_bound_shockley_diode_dc_operating_point"
                                   if calculation["outputs"]["solver_status"] == "solved-shockley-diode-newton-dc-model" else
                                   "circuit_graph_bound_piecewise_linear_diode_dc_operating_point"
                                   if calculation["outputs"]["solver_status"] == "solved-piecewise-linear-diode-dc-model"
                                   else "circuit_graph_bound_linear_dc_mna_operating_point")
                    report = {"run_id": run_id, "kind": report_kind, "created_at": created_at,
                              "circuit_model_sha256": model_hash, "mapping_sha256": mapping_hash, "netlist_sha256": netlist_hash,
                              "input_mapping": mapping, "mapping_report": resolved,
                              "component_coverage": resolved["component_coverage"],
                              "input_values_source": "explicit user-supplied element types, values, terminal order, ground net, and exclusions; graph connectivity is read from saved model",
                              "calculation": calculation, "generated_netlist": netlist, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    with (folder / "simulation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "mapping_sha256": mapping_hash,
                                              "netlist_sha256": netlist_hash, "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Graph-bound MNA report could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "simulations" and parts[4] in {"operating-limits", "transient-limits"}:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                if set(payload) != {"simulation_artifact", "limits"} or not isinstance(payload.get("simulation_artifact"), str):
                    raise ValueError("Request must contain exactly simulation_artifact and limits")
                source_name = payload["simulation_artifact"]
                if source_name not in _allowed_artifacts(project_id) or not source_name.startswith("simulation_") or not source_name.endswith(".json"):
                    raise ValueError("simulation_artifact must name an existing saved simulation JSON report")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    source_path = folder / source_name
                    source_bytes = source_path.read_bytes()
                    source_report = json.loads(source_bytes.decode("utf-8"))
                    dc_kinds = {"circuit_graph_bound_linear_dc_mna_operating_point",
                                "circuit_graph_bound_piecewise_linear_diode_dc_operating_point",
                                "circuit_graph_bound_shockley_diode_dc_operating_point"}
                    transient_kinds = {"circuit_graph_bound_linear_rc_transient", "circuit_graph_bound_linear_rlc_transient"}
                    source_kind = source_report.get("kind")
                    if source_kind not in dc_kinds | transient_kinds:
                        raise ValueError("Limit checks require a saved graph-bound DC operating point or RC/RLC transient report")
                    transient_endpoint = parts[4] == "transient-limits"
                    if transient_endpoint != (source_kind in transient_kinds):
                        raise ValueError("Select the operating-limits endpoint for DC reports or transient-limits for RC/RLC reports")
                    calculation = source_report.get("calculation")
                    outputs = calculation.get("outputs") if isinstance(calculation, dict) else None
                    if not isinstance(outputs, dict):
                        raise ValueError("Selected simulation report has no usable output data")
                    assessment = (assess_operating_limits(outputs.get("elements"), payload["limits"])
                                  if source_kind in dc_kinds else assess_transient_reactive_limits(outputs, payload["limits"]))
                    model = load_circuit(folder)
                    current_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report = {"run_id": run_id, "kind": "simulation_operating_limit_check",
                              "simulation_kind": source_kind,
                              "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "status": assessment["status"], "assessment": assessment,
                              "simulation_artifact": source_name,
                              "simulation_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
                              "simulation_circuit_model_sha256": source_report.get("circuit_model_sha256"),
                              "current_circuit_model_sha256": current_hash,
                              "model_status": "same-as-simulation-model" if source_report.get("circuit_model_sha256") == current_hash else "saved-model-changed-since-simulation",
                              "component_coverage": source_report.get("component_coverage"),
                              "limits": payload["limits"],
                              "warnings": ["All limits are caller-supplied and unverified; no ratings are inferred from part labels, inventory, or datasheets.",
                                           "A passing bound check is not component qualification, thermal analysis, electrical safety review, or design approval."]}
                    if isinstance(report["component_coverage"], dict) and report["component_coverage"].get("has_exclusions"):
                        report["warnings"].append("The source simulation excluded components; comparisons cover only elements present in that partial model.")
                    report["artifact"] = f"operating_limit_check_{run_id}.json"
                    _atomic_bytes(folder / report["artifact"], (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Operating-limit report could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["simulations", "dc-sweep"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                if set(payload) != {"netlist", "sweep"} or not isinstance(payload.get("netlist"), dict) or not isinstance(payload.get("sweep"), dict):
                    raise ValueError("Request must contain exactly netlist and sweep objects")
                simulation_input = {"netlist": payload["netlist"], "sweep": payload["sweep"]}
                calculation = simulate_dc_sweep(simulation_input)
                normalized_input = json.loads(json.dumps(simulation_input, sort_keys=True, separators=(",", ":"),
                                                         ensure_ascii=False, allow_nan=False))
                input_hash = hashlib.sha256(json.dumps(normalized_input, sort_keys=True, separators=(",", ":"),
                                                        ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model is invalid; DC sweep report is not associated with an invalid revision", "topology_report": topology})
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"simulation_dc_sweep_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    sweep_kind = ("bounded_diode_dc_source_sweep"
                                  if calculation["outputs"]["status"] == "completed-bounded-diode-dc-sweep"
                                  else "linear_dc_source_sweep")
                    report = {"run_id": run_id, "kind": sweep_kind, "created_at": created_at,
                              "circuit_model_sha256": model_hash, "netlist_sha256": input_hash,
                              "model_association": "user-associated saved-model revision; swept netlist was not generated from or matched to the circuit graph",
                              "input_values_source": "explicit linear DC netlist and selected independent-source sweep range; no component data inferred",
                              "calculation": calculation, "input_netlist": normalized_input, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    with (folder / "simulation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "netlist_sha256": input_hash,
                                              "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except CppEngineUnavailable as exc:
                return self._json(503, {"error": str(exc)})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"DC sweep report could not be stored: {exc}"})
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3:] == ["simulations", "dc-sweep", "from-model"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                if set(payload) != {"mapping", "sweep"} or not isinstance(payload.get("mapping"), dict) or not isinstance(payload.get("sweep"), dict):
                    raise ValueError("Request must contain exactly mapping and sweep objects")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model failed schema/topology checks", "topology_report": topology})
                    resolved = map_circuit_graph_to_mna(model, payload["mapping"])
                    simulation_input = {"netlist": resolved["netlist"], "sweep": payload["sweep"]}
                    calculation = simulate_dc_sweep(simulation_input)
                    calculation["scope"] = "Pointwise bounded DC source sweep using saved graph connectivity and explicitly mapped ideal R/V/I/diode models; not general nonlinear/SPICE validation, datasheet qualification, or design approval."
                    calculation["warnings"].append("Graph connectivity came from the saved model; element type, values, terminal order, and sweep range came from the explicit mapping.")
                    if resolved["excluded_components"]:
                        calculation["warnings"].append("Excluded components were omitted; this sweep covers only the explicitly mapped subnetwork.")
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    mapping = payload["mapping"]
                    mapping_hash = hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    netlist_hash = hashlib.sha256(json.dumps(simulation_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"simulation_dc_sweep_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    sweep_kind = ("circuit_graph_bound_bounded_diode_dc_source_sweep"
                                  if calculation["outputs"]["status"] == "completed-bounded-diode-dc-sweep"
                                  else "circuit_graph_bound_linear_dc_source_sweep")
                    report = {"run_id": run_id, "kind": sweep_kind, "created_at": created_at,
                              "circuit_model_sha256": model_hash, "mapping_sha256": mapping_hash, "netlist_sha256": netlist_hash,
                              "input_mapping": mapping, "mapping_report": resolved, "component_coverage": resolved["component_coverage"],
                              "input_values_source": "saved graph supplies connectivity; explicit mapping supplies R/V/I types, values, source direction, and sweep range",
                              "calculation": calculation, "generated_netlist": simulation_input, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    with (folder / "simulation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "mapping_sha256": mapping_hash,
                                              "netlist_sha256": netlist_hash, "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except CppEngineUnavailable as exc:
                return self._json(503, {"error": str(exc)})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Graph-bound DC sweep report could not be stored: {exc}"})
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3:] == ["simulations", "ac-sweep", "from-model"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                if set(payload) != {"mapping", "frequency_sweep"} or not isinstance(payload.get("mapping"), dict):
                    raise ValueError("Request must contain exactly mapping and frequency_sweep")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model failed schema/topology checks", "topology_report": topology})
                    resolved = map_circuit_graph_to_ac(model, payload["mapping"])
                    simulation_input = {"ground": resolved["netlist"]["ground"],
                                        "frequency_sweep": payload["frequency_sweep"],
                                        "elements": resolved["netlist"]["elements"]}
                    calculation = simulate_ac_sweep(simulation_input)
                    calculation["scope"] = "Linear AC frequency response using saved graph connectivity and explicit per-run R/C/source models and phases; not datasheet or design approval."
                    calculation["warnings"].append("Graph connectivity came from the saved model; element values, AC magnitudes, source phases, and ordered pins came from the explicit run mapping.")
                    if resolved["excluded_components"]:
                        calculation["warnings"].append("Excluded components were omitted; this sweep covers only the explicitly mapped subnetwork.")
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    mapping = payload["mapping"]
                    mapping_hash = hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    netlist_hash = hashlib.sha256(json.dumps(simulation_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"simulation_ac_sweep_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report = {"run_id": run_id, "kind": "circuit_graph_bound_linear_ac_sweep", "created_at": created_at,
                              "circuit_model_sha256": model_hash, "mapping_sha256": mapping_hash, "netlist_sha256": netlist_hash,
                              "input_mapping": mapping, "mapping_report": resolved,
                              "component_coverage": resolved["component_coverage"],
                              "input_values_source": "saved graph supplies connectivity; explicit mapping supplies component types, values, source magnitudes/phases, terminal order, and exclusions",
                              "calculation": calculation, "generated_netlist": simulation_input, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    with (folder / "simulation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "mapping_sha256": mapping_hash,
                                              "netlist_sha256": netlist_hash, "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except CppEngineUnavailable as exc:
                return self._json(503, {"error": str(exc)})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Graph-bound AC sweep report could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["simulations", "ac-sweep"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                if set(payload) != {"netlist"} or not isinstance(payload.get("netlist"), dict):
                    raise ValueError("Request must contain exactly one 'netlist' object")
                netlist = payload["netlist"]
                calculation = simulate_ac_sweep(netlist)
                normalized_netlist = json.loads(json.dumps(netlist, sort_keys=True, separators=(",", ":"),
                                                            ensure_ascii=False, allow_nan=False))
                netlist_hash = hashlib.sha256(json.dumps(normalized_netlist, sort_keys=True, separators=(",", ":"),
                                                         ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model is invalid; AC report is not attached to an invalid revision", "topology_report": topology})
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"),
                                                           ensure_ascii=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"simulation_ac_sweep_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report = {"run_id": run_id, "kind": "linear_ac_sweep", "created_at": created_at,
                              "circuit_model_sha256": model_hash, "netlist_sha256": netlist_hash,
                              "model_association": "user-associated saved-model revision; AC netlist was not generated from or matched to the circuit graph",
                              "input_values_source": "explicit AC netlist values, RMS source magnitudes, phases, and frequency sweep; no component data inferred",
                              "calculation": calculation, "input_netlist": normalized_netlist, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False,
                                                                     allow_nan=False) + "\n").encode())
                    with (folder / "simulation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "netlist_sha256": netlist_hash,
                                              "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except CppEngineUnavailable as exc:
                return self._json(503, {"error": str(exc)})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"AC sweep report could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "simulations" and parts[4] in {"digital-truth-table", "digital-waveform"}:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(16_000_000 if parts[4] == "digital-waveform" else 100_000)
                if set(payload) != {"netlist"} or not isinstance(payload.get("netlist"), dict):
                    raise ValueError("Request must contain exactly one 'netlist' object")
                normalized_netlist = json.loads(json.dumps(payload["netlist"], sort_keys=True, separators=(",", ":"),
                                                            ensure_ascii=False, allow_nan=False))
                is_waveform = parts[4] == "digital-waveform"
                calculation = (simulate_digital_waveform if is_waveform else simulate_digital_truth_table)(normalized_netlist)
                netlist_hash = hashlib.sha256(json.dumps(normalized_netlist, sort_keys=True, separators=(",", ":"),
                                                         ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model is invalid; digital report is not attached to an invalid revision", "topology_report": topology})
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"),
                                                           ensure_ascii=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_kind = "digital_waveform" if is_waveform else "digital_truth_table"
                    report_name = f"simulation_digital_{'waveform_' if is_waveform else ''}{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report = {"run_id": run_id, "kind": report_kind, "created_at": created_at,
                              "status": "completed", "circuit_model_sha256": model_hash,
                              "netlist_sha256": netlist_hash,
                              "model_association": "user-associated saved-model revision; Boolean netlist was not generated from or matched to the circuit graph",
                              "calculation": calculation, "input_netlist": normalized_netlist,
                              "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False,
                                                                     allow_nan=False) + "\n").encode())
                    with (folder / "simulation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "netlist_sha256": netlist_hash,
                                              "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except CppEngineUnavailable as exc:
                return self._json(503, {"error": str(exc)})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Digital simulation report could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["simulations", "dc-operating-point"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                netlist = payload.get("netlist")
                if not isinstance(netlist, dict):
                    raise ValueError("Request must include an explicit 'netlist' object")
                calculation = solve_dc_operating_point(netlist)
                normalized_netlist = json.loads(json.dumps(netlist, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
                netlist_hash = hashlib.sha256(json.dumps(normalized_netlist, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model is invalid; operating-point report is not attached to an invalid revision", "topology_report": topology})
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"simulation_mna_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report_kind = ("shockley_diode_dc_operating_point"
                                   if calculation["outputs"]["solver_status"] == "solved-shockley-diode-newton-dc-model" else
                                   "piecewise_linear_diode_dc_operating_point"
                                   if calculation["outputs"]["solver_status"] == "solved-piecewise-linear-diode-dc-model"
                                   else "linear_dc_mna_operating_point")
                    report = {"run_id": run_id, "kind": report_kind, "created_at": created_at,
                              "circuit_model_sha256": model_hash, "netlist_sha256": netlist_hash,
                              "model_association": "user-associated saved-model revision; netlist was not generated from or matched to the circuit graph",
                              "input_values_source": "explicit user-supplied bounded DC netlist and diode parameters; no component data inferred",
                              "calculation": calculation, "input_netlist": normalized_netlist, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    with (folder / "simulation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "netlist_sha256": netlist_hash,
                                              "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"DC operating-point report could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["calculations", "clock-divider"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                inputs = payload.get("inputs")
                if not isinstance(inputs, dict) or set(inputs) != {"source_frequency", "target_frequency", "source_tolerance_ppm"}:
                    raise ValueError("inputs must contain source_frequency, target_frequency, and source_tolerance_ppm")
                calculation = clock_divider_candidate(inputs["source_frequency"], inputs["target_frequency"], inputs["source_tolerance_ppm"])
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model is invalid; calculations are not attached to an invalid revision", "topology_report": topology})
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    input_document = {"kind": "clock_divider_candidate", "inputs": inputs}
                    input_hash = hashlib.sha256(json.dumps(input_document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"calculation_clock_divider_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report = {"run_id": run_id, "kind": "clock_divider_candidate", "created_at": created_at,
                              "circuit_model_sha256": model_hash, "inputs_sha256": input_hash,
                              "input_values_source": "explicit user-provided source rate, target rate, and source tolerance; no component specifications inferred",
                              "dependency_status": "current-at-creation",
                              "staleness_rule": "GET /api/projects/<id>/calculations compares circuit_model_sha256 to the current saved model; a changed model marks this report stale.",
                              "calculation": calculation, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    with (folder / "calculation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "inputs_sha256": input_hash,
                                              "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Calculation report could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["calculations", "clock-timebase"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                inputs = payload.get("inputs")
                if not isinstance(inputs, dict) or set(inputs) != {"nominal_tick_rate", "target_tick_rate", "tolerance_ppm"}:
                    raise ValueError("inputs must contain nominal_tick_rate, target_tick_rate, and tolerance_ppm")
                calculation = clock_timebase_error(inputs["nominal_tick_rate"], inputs["target_tick_rate"], inputs["tolerance_ppm"])
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model is invalid; calculations are not attached to an invalid revision", "topology_report": topology})
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    input_document = {"kind": "clock_timebase_error", "inputs": inputs}
                    input_hash = hashlib.sha256(json.dumps(input_document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"calculation_clock_timebase_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report = {"run_id": run_id, "kind": "clock_timebase_error", "created_at": created_at,
                              "circuit_model_sha256": model_hash, "inputs_sha256": input_hash,
                              "input_values_source": "explicit user-provided post-divider rates and tolerance; no component specifications inferred",
                              "dependency_status": "current-at-creation",
                              "staleness_rule": "GET /api/projects/<id>/calculations compares circuit_model_sha256 to the current saved model; a changed model marks this report stale.",
                              "calculation": calculation, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())
                    with (folder / "calculation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": created_at,
                                              "circuit_model_sha256": model_hash, "inputs_sha256": input_hash,
                                              "artifact": report_name}, ensure_ascii=False, allow_nan=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Calculation report could not be stored: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["calculations", "led-resistor"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(200_000)
                inputs = payload.get("inputs")
                if not isinstance(inputs, dict):
                    raise ValueError("Request must include an 'inputs' object")
                source_refs = payload.get("source_refs", {})
                if not isinstance(source_refs, dict) or any(key not in {"supply", "led", "resistor"} for key in source_refs):
                    raise ValueError("source_refs may contain only supply, led, and resistor component references")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model is invalid; calculations are not attached to an invalid revision", "topology_report": topology})
                    refs = {component.get("reference") for component in model.get("components", [])}
                    clean_refs = {}
                    for role, reference in source_refs.items():
                        if not isinstance(reference, str) or reference not in refs:
                            raise ValueError(f"source_refs.{role} must name an existing saved component reference")
                        clean_refs[role] = reference
                    calculation = led_series_resistor(inputs)
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    input_document = {"kind": "led_series_resistor", "inputs": inputs, "source_refs": clean_refs}
                    input_hash = hashlib.sha256(json.dumps(input_document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"calculation_led_resistor_{run_id}.json"
                    report = {"run_id": run_id, "kind": "led_series_resistor", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "circuit_model_sha256": model_hash, "inputs_sha256": input_hash,
                              "source_refs": clean_refs, "input_values_source": "explicit user-provided quantities; not inferred from component descriptions",
                              "dependency_status": "current-at-creation",
                              "staleness_rule": "GET /api/projects/<id>/calculations compares circuit_model_sha256 to the current saved model; a changed model marks this report stale.",
                              "calculation": calculation, "artifact": report_name}
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode())
                    with (folder / "calculation_runs.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({"run_id": run_id, "kind": report["kind"], "created_at": report["created_at"],
                                              "circuit_model_sha256": model_hash, "inputs_sha256": input_hash, "artifact": report_name}, ensure_ascii=False) + "\n")
                        log.flush()
                        os.fsync(log.fileno())
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Calculation report could not be stored: {exc}"})
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3] == "datasheets" and parts[5] == "extract":
            project_id, source_sha256 = parts[2], parts[4]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            with _project_tool_lock(project_id):
                result = run_pdftotext(folder, source_sha256)
                sources_path = folder / "datasheet_sources.json"
                if sources_path.is_file():
                    try:
                        sources = json.loads(sources_path.read_text("utf-8")).get("sources", [])
                        source_record = next((item for item in reversed(sources) if item.get("sha256") == source_sha256), None)
                        if source_record:
                            result["source_id"] = source_record.get("source_id")
                            result["source_filename"] = source_record.get("filename")
                            result["component_reference"] = source_record.get("component_reference")
                            result["circuit_model_sha256"] = source_record.get("circuit_model_sha256")
                    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
                        pass
                extracted = result.pop("extracted_text", "")
                if extracted:
                    text_name = f"datasheet_text_{result['run_id']}.txt"
                    _atomic_bytes(folder / text_name, extracted.encode("utf-8"))
                    result["text_file"] = text_name
                report_name = f"datasheet_extraction_{result['run_id']}.json"
                result["report_file"] = report_name
                with _CIRCUIT_API_LOCK:
                    _append_tool_run(folder, result)
                    _atomic_bytes(folder / report_name, (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode())
                    _update_project_record(folder)
            return self._json(200, result)
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["integrations", "ngspice"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            with _project_tool_lock(project_id):
                result = run_ngspice(folder)
            with _CIRCUIT_API_LOCK:
                _append_tool_run(folder, result)
                _atomic_bytes(folder / "ngspice_result.json", (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode())
                validation_path = folder / "validation_report.json"
                validation = json.loads(validation_path.read_text("utf-8")) if validation_path.exists() else {"checks": {}, "findings": []}
                validation.setdefault("checks", {})["simulation"] = f"ngspice status: {result.get('status', 'not-run')}"
                _atomic_bytes(validation_path, (json.dumps(validation, indent=2, ensure_ascii=False) + "\n").encode())
                _update_project_record(folder, validation)
            return self._json(200, result)
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["integrations", "kicad-drc"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            with _project_tool_lock(project_id):
                result = run_kicad_drc(folder)
            with _CIRCUIT_API_LOCK:
                _append_tool_run(folder, result)
                _atomic_bytes(folder / "kicad_drc_result.json", (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode())
                validation_path = folder / "validation_report.json"
                validation = json.loads(validation_path.read_text("utf-8")) if validation_path.exists() else {"checks": {}, "findings": []}
                status = result.get("status", "not-run")
                violations = result.get("drc_violation_count")
                unconnected = result.get("unconnected_pad_count")
                if status == "completed-no-reported-violations":
                    summary = f"KiCad board DRC completed; reported {violations} violations and {unconnected} unconnected pads"
                elif status == "completed-with-violations":
                    summary = f"KiCad board DRC reported violations: {violations}; unconnected pads: {unconnected}"
                else:
                    summary = f"KiCad board DRC status: {status}"
                validation.setdefault("checks", {})["pcb_design_rule_check"] = summary
                _atomic_bytes(validation_path, (json.dumps(validation, indent=2, ensure_ascii=False) + "\n").encode())
                _update_project_record(folder, validation)
            return self._json(200, result)
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["integrations", "kicad-erc"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            with _project_tool_lock(project_id):
                result = run_kicad_erc(folder)
            with _CIRCUIT_API_LOCK:
                _append_tool_run(folder, result)
                _atomic_bytes(folder / "kicad_erc_result.json", (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode())
                validation_path = folder / "validation_report.json"
                validation = json.loads(validation_path.read_text("utf-8")) if validation_path.exists() else {"checks": {}, "findings": []}
                checks = validation.setdefault("checks", {})
                status = result.get("status", "not-run")
                if status == "completed-no-messages":
                    checks["electrical_rule_check"] = f"KiCad ERC completed; report summary: {result.get('erc_message_count')} messages"
                else:
                    checks["electrical_rule_check"] = f"KiCad ERC status: {status}"
                _atomic_bytes(validation_path, (json.dumps(validation, indent=2, ensure_ascii=False) + "\n").encode())
                _update_project_record(folder, validation)
            return self._json(200, result)
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["circuit", "connectivity-schedule"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(2_000)
                if payload != {}:
                    raise ValueError("Connectivity schedule request body must be an empty JSON object")
                with _project_tool_lock(project_id), _CIRCUIT_API_LOCK:
                    model = load_circuit(folder)
                    topology = validate_model(model)
                    if not topology["topology_valid"]:
                        return self._json(409, {"error": "Saved circuit model failed schema/topology checks; no schedule was generated", "topology_report": topology})
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    report = build_connectivity_schedule(model, model_hash)
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    csv_buffer = io.StringIO(newline="")
                    writer = csv.DictWriter(csv_buffer, fieldnames=["net", "reference", "pin", "pin_name", "pin_type"])
                    writer.writeheader()
                    for net in report["nets"]:
                        for endpoint in net["endpoints"]:
                            writer.writerow({"net": net["name"], **endpoint})
                    csv_text = csv_buffer.getvalue()
                    csv_artifact = f"connectivity_schedule_{run_id}.csv"
                    report.update({"run_id": run_id, "created_at": created_at,
                                   "artifact": f"connectivity_schedule_{run_id}.json",
                                   "csv_artifact": csv_artifact,
                                   "csv_sha256": hashlib.sha256(csv_text.encode("utf-8")).hexdigest()})
                    _atomic_bytes(folder / report["artifact"], (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                    _atomic_bytes(folder / csv_artifact, csv_text.encode("utf-8"))
                    _update_project_record(folder)
                return self._json(201, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
                return self._json(422, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Connectivity schedule could not be stored: {exc}"})
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3:] == ["circuit", "symbols", "resolve"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload()
                model = migrate_model(payload.get("model"))
                topology = validate_model(model)
                if not topology["topology_valid"]:
                    return self._json(422, {"error": "Circuit model failed schema/topology checks", "topology_report": topology})
                components = model.get("components", [])
                if len(components) > 200:
                    return self._json(413, {"error": "Symbol audit is limited to 200 components per request"})
                by_symbol: dict[tuple[str, str], dict[str, object]] = {}
                results = []
                for component in components:
                    reference = component["reference"]
                    symbol = component.get("symbol")
                    if not isinstance(symbol, dict):
                        results.append({"reference": reference, "status": "no-symbol-id", "reason": "No explicit KiCad library and symbol ID is assigned; none was inferred."})
                        continue
                    key = (symbol["library"], symbol["name"])
                    if key not in by_symbol:
                        by_symbol[key] = resolve_kicad_symbol(*key, project_dir=folder)
                    resolved = by_symbol[key]
                    item = {"reference": reference, **resolved}
                    if resolved.get("status") == "resolved":
                        actual_pins = resolved.get("pins", [])
                        actual_by_number: dict[str, list[dict[str, object]]] = {}
                        for pin in actual_pins:
                            actual_by_number.setdefault(str(pin.get("number")), []).append(pin)
                        declared = component.get("pins", [])
                        missing = [pin.get("number") for pin in declared if str(pin.get("number")) not in actual_by_number]
                        mismatched = []
                        for pin in declared:
                            candidates = actual_by_number.get(str(pin.get("number")), [])
                            if candidates and not all(pin.get("name") == actual.get("name") and pin.get("type", "unspecified") == actual.get("type") for actual in candidates):
                                mismatched.append({"number": pin.get("number"), "declared_name": pin.get("name"),
                                                   "declared_type": pin.get("type", "unspecified"),
                                                   "library_pins": [{"name": actual.get("name"), "type": actual.get("type")} for actual in candidates]})
                        declared_numbers = {str(pin.get("number")) for pin in declared}
                        additional = sorted(number for number in actual_by_number if number not in declared_numbers)
                        item.update({"missing_declared_pins": missing, "declared_pin_mismatches": mismatched,
                                     "library_pins_not_declared": additional,
                                     "status": "pin-manifest-match" if not missing and not mismatched and not additional else "pin-manifest-differs"})
                    results.append(item)
                model_sha256 = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                report = {"status": "completed", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "model_sha256": model_sha256,
                          "scope": "Read-only installed-library lookup and declared-pin comparison. No model changes, electrical checks, ERC/DRC, or datasheet validation were performed.",
                          "component_count": len(results),
                          "resolved_count": sum(item["status"] in {"pin-manifest-match", "pin-manifest-differs"} for item in results),
                          "mismatch_count": sum(item["status"] == "pin-manifest-differs" for item in results),
                          "unresolved_count": sum(item["status"] not in {"pin-manifest-match", "pin-manifest-differs"} for item in results),
                          "components": results}
                report["run_id"] = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                report_name = f"kicad_symbol_audit_{report['run_id']}.json"
                report["artifact"] = report_name
                with _CIRCUIT_API_LOCK:
                    _atomic_bytes(folder / report_name, (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode())
                    _update_project_record(folder)
                return self._json(200, report)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                return self._json(400, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Unable to audit KiCad symbols: {exc}"})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["circuit", "validate"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            if not (DATA_DIR / "projects" / project_id).is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload()
                model = migrate_model(payload.get("model"))
                report = validate_model(model)
                if not report["topology_valid"]:
                    return self._json(422, {"error": "Circuit model failed schema/topology checks", "topology_report": report})
                try:
                    export_kicad_netlist(model, source_name=f"CIRCUITMIND-{project_id}")
                    export_state = "netlist generation available; library resolution and KiCad CLI validation not run"
                except ValueError as exc:
                    export_state = f"blocked: {exc}"
                return self._json(200, {"topology_report": report, "kicad_export": export_state})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})
        if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["snapshots", "restore"]:
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            try:
                payload = self._read_payload()
                snapshot_id = payload.get("snapshot_id")
                if not isinstance(snapshot_id, str):
                    raise ValueError("snapshot_id is required")
                folder = DATA_DIR / "projects" / project_id
                if not folder.is_dir():
                    return self._json(404, {"error": "Project not found"})
                with _CIRCUIT_API_LOCK:
                    model, snapshots = restore_snapshot(project_id, folder, snapshot_id)
                    export_report = _refresh_circuit_artifacts(project_id, folder, model)
                return self._json(200, {"model": model, "topology_report": validate_model(model), "kicad_export": export_report, "snapshots": snapshots})
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})
            except FileNotFoundError as exc:
                return self._json(404, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Unable to restore snapshot: {exc}"})
        if path != "/api/projects":
            return self._json(404, {"error": "Not found"})
        try:
            payload = self._read_payload()
            values = ProjectInput(name=str(payload.get("name", "")), description=str(payload.get("description", "")),
                                  inventory_text=str(payload.get("inventory", "")), architecture=str(payload.get("architecture", "discrete")))
            record, _ = analyze(values, DATA_DIR)
            return self._json(201, record.to_dict())
        except RequestTooLarge as exc:
            return self._json(413, {"error": str(exc)})
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": f"Invalid JSON: {exc}"})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except OSError as exc:
            return self._json(500, {"error": f"Unable to save project: {exc}"})

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "datasheets":
            project_id = parts[2]
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            query = parse_qs(parsed.query, keep_blank_values=True)
            raw_filename = (query.get("filename") or ["datasheet.pdf"])[0]
            filename = raw_filename.replace("\\", "/").split("/")[-1].strip()
            reference = (query.get("reference") or [""])[0].strip()
            if not filename or len(filename) > 180 or any(ord(char) < 32 for char in filename) or not filename.lower().endswith(".pdf"):
                return self._json(422, {"error": "Datasheet upload requires a .pdf filename (maximum 180 characters)"})
            if len(reference) > 24:
                return self._json(422, {"error": "Component reference is too long"})
            circuit_model_sha256 = None
            if reference:
                try:
                    model = load_circuit(folder)
                except (OSError, json.JSONDecodeError, ValueError) as exc:
                    return self._json(500, {"error": f"Circuit model could not be loaded for source linking: {exc}"})
                if reference not in {component.get("reference") for component in model.get("components", [])}:
                    return self._json(422, {"error": f"Reference {reference} is not a component in the saved circuit model"})
                circuit_model_sha256 = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/pdf":
                return self._json(415, {"error": "Datasheet body must use Content-Type application/pdf"})
            try:
                raw = self._read_raw_body(MAX_DATASHEET_FILE)
                if not raw.startswith(b"%PDF-"):
                    return self._json(422, {"error": "Datasheet upload is not marked with a PDF header"})
                source_sha256 = hashlib.sha256(raw).hexdigest()
                target_name = f"datasheet_{source_sha256}.pdf"
                run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                item = {"source_id": run_id, "filename": filename, "file": target_name,
                        "component_reference": reference or None, "circuit_model_sha256": circuit_model_sha256,
                        "sha256": source_sha256, "size_bytes": len(raw), "imported_at": imported_at,
                        "status": "attached; PDF header checked only; text extraction not run"}
                report_name = f"datasheet_import_{run_id}.json"
                item["import_report"] = report_name
                with _project_tool_lock(project_id):
                    target = folder / target_name
                    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != source_sha256:
                        return self._json(409, {"error": "Stored datasheet hash path does not match its file contents"})
                    if not target.exists():
                        _atomic_bytes(target, raw)
                    index_path = folder / "datasheet_sources.json"
                    if index_path.exists():
                        index = json.loads(index_path.read_text("utf-8"))
                        if not isinstance(index, dict) or not isinstance(index.get("sources"), list):
                            return self._json(500, {"error": "Datasheet source index has an unsupported format"})
                    else:
                        index = {"schema_version": 1, "sources": []}
                    index["sources"].append(item)
                    with _CIRCUIT_API_LOCK:
                        _atomic_bytes(index_path, (json.dumps(index, indent=2, ensure_ascii=False) + "\n").encode())
                        _atomic_bytes(folder / report_name, (json.dumps(item, indent=2, ensure_ascii=False) + "\n").encode())
                        _update_project_record(folder)
                return self._json(201, item)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except (OSError, json.JSONDecodeError) as exc:
                return self._json(500, {"error": f"Unable to attach datasheet: {exc}"})
        if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] in {"schematic", "simulation-deck", "board"}:
            project_id = parts[2]
            is_schematic = parts[3] == "schematic"
            is_board = parts[3] == "board"
            if not _valid_id(project_id):
                return self._json(400, {"error": "Invalid project id"})
            folder = DATA_DIR / "projects" / project_id
            if not folder.is_dir():
                return self._json(404, {"error": "Project not found"})
            try:
                payload = self._read_payload(MAX_SCHEMATIC_BODY)
                content = payload.get("content")
                if not isinstance(content, str):
                    raise ValueError("Source upload requires text field 'content'")
                raw = content.encode("utf-8")
                if len(raw) > MAX_SCHEMATIC_FILE:
                    return self._json(413, {"error": "Source file exceeds the 12 MB limit"})
                if is_schematic:
                    if not content.lstrip("\ufeff \t\r\n").startswith("(kicad_sch"):
                        return self._json(422, {"error": "Expected a KiCad S-expression schematic beginning with (kicad_sch"})
                    source_name, import_report_name = "user_source.kicad_sch", "schematic_import.json"
                    import_status = "attached; not parsed or electrically validated"
                    static_report = None
                elif is_board:
                    if not content.lstrip("\ufeff \t\r\n").startswith("(kicad_pcb"):
                        return self._json(422, {"error": "Expected a KiCad S-expression PCB beginning with (kicad_pcb"})
                    source_name, import_report_name = "user_source.kicad_pcb", "board_import.json"
                    import_status = "attached; header checked only; not parsed, electrically checked, or manufacturing validated"
                    static_report = None
                else:
                    static_report = validate_spice_deck(content)
                    if not static_report["accepted"]:
                        return self._json(422, {"error": "SPICE deck rejected by the batch-only directive allowlist", "deck_validation": static_report})
                    source_name, import_report_name = "user_source.cir", "simulation_deck_import.json"
                    import_status = "attached; static directive checks passed; not simulated"
                target = folder / source_name
                with _project_tool_lock(project_id):
                    previous_file = None
                    if target.exists():
                        if payload.get("replace") is not True:
                            return self._json(409, {"error": "A source file is already attached. Confirm replacement to archive the current source before updating it."})
                        previous = target.read_bytes()
                        previous_hash = hashlib.sha256(previous).hexdigest()
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        previous_file = f"{target.stem}.previous-{stamp}-{previous_hash[:8]}-{uuid.uuid4().hex[:6]}{target.suffix}"
                        _atomic_bytes(folder / previous_file, previous)
                    _atomic_bytes(target, raw)
                    imported = {"status": import_status, "file": target.name,
                                "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                                "previous_file": previous_file}
                    if static_report is not None:
                        imported["deck_validation"] = static_report
                    _atomic_bytes(folder / import_report_name, (json.dumps(imported, indent=2, ensure_ascii=False) + "\n").encode())
                    _update_project_record(folder)
                return self._json(201, imported)
            except RequestTooLarge as exc:
                return self._json(413, {"error": str(exc)})
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                return self._json(400, {"error": str(exc)})
            except OSError as exc:
                return self._json(500, {"error": f"Unable to attach source file: {exc}"})
        if len(parts) != 4 or parts[:2] != ["api", "projects"] or parts[3] != "circuit":
            return self._json(404, {"error": "Not found"})
        project_id = parts[2]
        if not _valid_id(project_id):
            return self._json(400, {"error": "Invalid project id"})
        folder = DATA_DIR / "projects" / project_id
        if not folder.is_dir():
            return self._json(404, {"error": "Project not found"})
        try:
            payload = self._read_payload()
            incoming = payload.get("model")
            if not isinstance(incoming, dict):
                raise ValueError("Request must contain a circuit model object under 'model'")
            model = migrate_model(incoming)
            report = validate_model(model)
            if not report["topology_valid"]:
                return self._json(422, {"error": "Circuit model failed schema/topology checks", "topology_report": report})
            with _CIRCUIT_API_LOCK:
                snapshots = save_circuit(project_id, folder, model)
                export_report = _refresh_circuit_artifacts(project_id, folder, model)
            return self._json(200, {"model": model, "topology_report": report, "kicad_export": export_report, "snapshots": snapshots})
        except RequestTooLarge as exc:
            return self._json(413, {"error": str(exc)})
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return self._json(400, {"error": str(exc)})
        except OSError as exc:
            return self._json(500, {"error": f"Unable to save circuit model: {exc}"})

    def _read_raw_body(self, max_bytes: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0:
            raise ValueError("Request body must not be empty")
        if length > max_bytes:
            raise RequestTooLarge(f"Request exceeds the {max_bytes}-byte limit")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("Request body ended before Content-Length bytes were received")
        return body

    def _read_payload(self, max_bytes: int = MAX_BODY) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0:
            raise ValueError("Request body must not be empty")
        if length > max_bytes:
            raise RequestTooLarge(f"Request exceeds the {max_bytes}-byte limit")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request must be an object")
        return payload


def _refresh_circuit_artifacts(project_id: str, folder: Path, model: dict[str, object]) -> dict[str, object]:
    """Update graph/export reports from the persisted model without claiming ERC."""
    with _CIRCUIT_API_LOCK:
        topology = validate_model(model)
        _atomic_bytes(folder / "connectivity_report.json", (json.dumps(topology, indent=2, ensure_ascii=False) + "\n").encode())
        try:
            netlist = export_kicad_netlist(model, source_name=f"CIRCUITMIND-{project_id}")
            _atomic_bytes(folder / "kicad_netlist.net", netlist)
            export_report: dict[str, object] = {"status": "generated; symbol-library resolution and KiCad CLI validation not run", "format": "KiCad legacy XML netlist", "file": "kicad_netlist.net", "sha256": hashlib.sha256(netlist).hexdigest()}
        except ValueError as exc:
            (folder / "kicad_netlist.net").unlink(missing_ok=True)
            export_report = {"status": "blocked", "format": "KiCad legacy XML netlist", "reasons": [str(exc)], "kicad_cli_validation": "not-run"}
        _atomic_bytes(folder / "kicad_export_report.json", (json.dumps(export_report, indent=2, ensure_ascii=False) + "\n").encode())
        validation_path = folder / "validation_report.json"
        validation = json.loads(validation_path.read_text("utf-8")) if validation_path.exists() else {"checks": {}, "findings": []}
        checks = validation.setdefault("checks", {})
        if topology["component_count"] and topology["net_count"]:
            checks["electrical_connectivity"] = "topology-only pass; electrical ERC not run"
        else:
            checks["electrical_connectivity"] = "not-run (no pin-level connected circuit graph)"
        checks["kicad_netlist_export"] = export_report["status"]
        checks["electrical_rule_check"] = "not-run (KiCad CLI/ERC not invoked)"
        _atomic_bytes(validation_path, (json.dumps(validation, indent=2, ensure_ascii=False) + "\n").encode())
        _update_project_record(folder, validation)
        return export_report


def _append_tool_run(folder: Path, result: dict[str, object]) -> None:
    log_path = folder / "tool_runs.jsonl"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _update_project_record(folder: Path, validation: dict[str, object] | None = None) -> None:
    project_path = folder / "project.json"
    if not project_path.is_file():
        return
    record = json.loads(project_path.read_text("utf-8"))
    record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if validation is not None:
        record["validation"] = validation
    artifacts = {path.name for path in folder.iterdir() if path.is_file() and path.name != "project.json"}
    record["artifacts"] = sorted(artifacts)
    _atomic_bytes(project_path, (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode())


def _load_verified_agent_execution(folder: Path, project_id: str, execution_id: str) -> tuple[dict[str, object], str, dict[str, object]]:
    """Load one bounded, digest-checked execution artifact with an intact audit-chain link."""
    if not _valid_agent_plan_id(execution_id):
        raise ValueError("Invalid execution id")
    path = folder / f"agent_execution_{execution_id}.json"
    if not path.is_file():
        raise FileNotFoundError("Agent execution report not found")
    if path.stat().st_size > MAX_AGENT_REPORT_BYTES:
        raise ValueError("Execution report exceeds the verification size limit")
    raw = path.read_bytes()
    report = json.loads(raw.decode("utf-8"))
    if (not isinstance(report, dict) or report.get("execution_id") != execution_id or
            report.get("project_id") != project_id or report.get("artifact") != path.name):
        raise ValueError("Execution report identity check failed")
    try:
        digest = execution_report_digest(report)
    except (TypeError, ValueError) as exc:
        raise ValueError("Execution report content could not be hashed") from exc
    if digest != report.get("execution_report_sha256"):
        raise ValueError("Execution report content digest does not match")
    action_path = folder / "agent_actions.jsonl"
    rows, chain = verify_agent_actions(action_path.read_text("utf-8") if action_path.is_file() else "")
    if chain.get("status") != "intact":
        raise ValueError("Agent audit chain is not intact")
    report_hash = hashlib.sha256(raw).hexdigest()
    linked = any(row.get("tool") == "agent.plan.execute" and
                 row.get("execution_id") == execution_id and
                 row.get("result_sha256") == report_hash for row in rows)
    if not linked:
        raise ValueError("Execution report has no matching audit-chain record")
    return report, report_hash, chain


def _valid_agent_plan_id(plan_id: str) -> bool:
    return len(plan_id) == 32 and all(char in "0123456789abcdef" for char in plan_id)


def _read_agent_plan(folder: Path, project_id: str, plan_id: str) -> dict[str, object]:
    if not _valid_agent_plan_id(plan_id):
        raise ValueError("Invalid plan id")
    path = folder / f"agent_plan_{plan_id}.json"
    if not path.is_file():
        raise FileNotFoundError("Agent plan not found")
    plan = json.loads(path.read_text("utf-8"))
    if (not isinstance(plan, dict) or plan.get("plan_id") != plan_id or
            plan.get("project_id") != project_id or
            not isinstance(plan.get("summary"), str) or not isinstance(plan.get("calls"), list) or
            plan.get("artifact") != path.name):
        raise ValueError("Agent plan record is malformed or belongs to another project")
    digest = plan.get("plan_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("Agent plan hash metadata is malformed")
    try:
        calculated = agent_plan_digest(plan)
    except (TypeError, ValueError) as exc:
        raise ValueError("Agent plan content could not be hashed") from exc
    if calculated != digest:
        raise ValueError("Agent plan content hash does not match its reviewed hash")
    return plan


def _valid_agent_proposal_id(proposal_id: str) -> bool:
    return len(proposal_id) == 32 and all(char in "0123456789abcdef" for char in proposal_id)


def _read_agent_proposal(folder: Path, proposal_id: str) -> dict[str, object]:
    if not _valid_agent_proposal_id(proposal_id):
        raise ValueError("Invalid proposal id")
    path = folder / f"agent_proposal_{proposal_id}.json"
    if not path.is_file():
        raise FileNotFoundError("Agent proposal not found")
    proposal = json.loads(path.read_text("utf-8"))
    if (not isinstance(proposal, dict) or proposal.get("proposal_id") != proposal_id or
            not isinstance(proposal.get("project_id"), str) or not _valid_id(proposal["project_id"]) or
            proposal.get("tool") != "circuit.replace_model" or
            not isinstance(proposal.get("status"), str) or
            proposal["status"] not in {"awaiting-approval", "approved-and-applied", "expired", "stale"} or
            not isinstance(proposal.get("model"), dict)):
        raise ValueError("Agent proposal record is malformed")
    for key in ("base_model_sha256", "candidate_model_sha256"):
        digest = proposal.get(key)
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Agent proposal hash metadata is malformed")
    try:
        expiry = datetime.fromisoformat(proposal["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Agent proposal expiry metadata is malformed") from exc
    if expiry.tzinfo is None:
        raise ValueError("Agent proposal expiry must include a timezone")
    return proposal


def _agent_proposal_summary(proposal: dict[str, object]) -> dict[str, object]:
    keys = ("proposal_id", "project_id", "tool", "status", "created_at", "expires_at",
            "base_model_sha256", "candidate_model_sha256", "approved_at", "applied_model_sha256")
    return {key: proposal[key] for key in keys if key in proposal}


def _agent_audit_row(tool: str, status: str, input_hash: str, result_hash: str,
                     permission: str = "write-circuit") -> dict[str, object]:
    return {"execution_id": uuid.uuid4().hex, "call_id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tool": tool, "permission": permission, "requires_approval": permission != "read-only",
            "status": status, "arguments_sha256": input_hash,
            "result_sha256": result_hash, "duration_ms": 0.0}


def _append_agent_actions(folder: Path, rows: list[dict[str, object]]) -> None:
    """Append bounded, hash-linked action records atomically."""
    path = folder / "agent_actions.jsonl"
    prior = path.read_text("utf-8") if path.is_file() else ""
    encoded, _ = append_agent_actions(prior, rows)
    _atomic_bytes(path, encoded.encode("utf-8"))


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _valid_id(project_id: str) -> bool:
    return len(project_id) == 12 and all(c in "0123456789abcdef" for c in project_id)


def _allowed_artifacts(project_id: str) -> set[str]:
    directory = DATA_DIR / "projects" / project_id
    return {item.name for item in directory.iterdir() if item.is_file()} if directory.is_dir() else set()


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    (DATA_DIR / "projects").mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"CIRCUITMIND AI {__version__} listening on http://{host}:{port}")
    print(f"Project storage: {DATA_DIR / 'projects'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
    finally:
        server.server_close()
