"""Local HTTP API and static application server."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .circuit_schema import export_kicad_netlist, migrate_model, validate_model
from .integrations import discover_integrations, run_kicad_drc, run_kicad_erc, run_ngspice, run_pdftotext
from .kicad_symbols import resolve_kicad_symbol
from .engineering import (clock_divider_candidate, clock_timebase_error, led_series_resistor,
                          solve_dc_operating_point, map_circuit_graph_to_mna, simulate_transient_rc,
                          synthesize_rc_lowpass)
from .engineering.cpp_engine import CppEngineUnavailable
from .spice import validate_spice_deck
from .models import ProjectInput
from .project_store import list_snapshots, load_circuit, restore_snapshot, save_circuit
from .workflow import analyze, make_zip


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA_DIR = Path(os.environ.get("CIRCUITMIND_DATA_DIR", ROOT / "workspace")).expanduser().resolve()
MAX_BODY = 1_000_000
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
                report_paths = sorted([*folder.glob("simulation_mna_*.json"), *folder.glob("simulation_transient_rc_*.json")])
                for report_path in report_paths:
                    report = json.loads(report_path.read_text("utf-8"))
                    if report.get("kind") not in {"linear_dc_mna_operating_point", "circuit_graph_bound_linear_dc_mna_operating_point", "circuit_graph_bound_linear_rc_transient"}:
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
                    if isinstance(outputs, dict) and "time_points_s" in outputs:
                        times = outputs.get("time_points_s", [])
                        node_traces = outputs.get("node_voltage_traces_v", {})
                        cap_traces = outputs.get("capacitor_voltage_traces_v", {})
                        outputs = {"method": outputs.get("method"), "status": outputs.get("status"),
                                   "sample_count": outputs.get("sample_count"), "time_step": outputs.get("time_step"),
                                   "stop_time": outputs.get("stop_time"), "final_time_s": times[-1] if times else None,
                                   "final_node_voltages_v": {node: trace[-1] for node, trace in node_traces.items() if trace},
                                   "final_capacitor_voltages_v": {ref: trace[-1] for ref, trace in cap_traces.items() if trace},
                                   "trace_node_count": len(node_traces), "trace_capacitor_count": len(cap_traces)}
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
            if filename.endswith((".raw", ".pdf")):
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf" if filename.endswith(".pdf") else "application/octet-stream")
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
        if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3:] == ["simulations", "transient-rc", "from-model"]:
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
                    resolved = map_circuit_graph_to_mna(model, payload["mapping"], allow_capacitors=True)
                    simulation_input = {"ground": resolved["netlist"]["ground"], "time_step": payload["time_step"],
                                        "stop_time": payload["stop_time"], "method": "backward_euler",
                                        "elements": resolved["netlist"]["elements"]}
                    calculation = simulate_transient_rc(simulation_input)
                    calculation["scope"] = "Numerical backward-Euler time-domain simulation with connectivity from the saved graph and explicit user component types, ordered pins, values, sources, and initial conditions; not datasheet or design approval."
                    calculation["warnings"].append("Graph connectivity came from the saved model; element types, values, source waveform, and capacitor initial conditions came from the per-run user mapping.")
                    if resolved["excluded_components"]:
                        calculation["warnings"].append("Excluded components were omitted; this transient solve covers only the explicitly mapped RC subnetwork.")
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    mapping = payload["mapping"]
                    mapping_hash = hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    netlist_hash = hashlib.sha256(json.dumps(simulation_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"simulation_transient_rc_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report = {"run_id": run_id, "kind": "circuit_graph_bound_linear_rc_transient", "created_at": created_at,
                              "circuit_model_sha256": model_hash, "mapping_sha256": mapping_hash, "netlist_sha256": netlist_hash,
                              "input_mapping": mapping, "mapping_report": resolved,
                              "component_coverage": resolved["component_coverage"],
                              "input_values_source": "saved graph supplies connectivity; per-run explicit mapping supplies R/C/source models, waveform, pin order, and initial conditions",
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
                return self._json(500, {"error": f"RC transient report could not be stored: {exc}"})
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
                    calculation["scope"] = "Numerical linear DC operating-point solution built from saved graph connectivity plus explicit user type/value/pin mappings; not nonlinear/transient simulation or datasheet approval."
                    model_hash = hashlib.sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
                    mapping_hash = hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    netlist = resolved["netlist"]
                    netlist_hash = hashlib.sha256(json.dumps(netlist, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
                    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
                    report_name = f"simulation_mna_{run_id}.json"
                    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    report = {"run_id": run_id, "kind": "circuit_graph_bound_linear_dc_mna_operating_point", "created_at": created_at,
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
                    report = {"run_id": run_id, "kind": "linear_dc_mna_operating_point", "created_at": created_at,
                              "circuit_model_sha256": model_hash, "netlist_sha256": netlist_hash,
                              "model_association": "user-associated saved-model revision; netlist was not generated from or matched to the circuit graph",
                              "input_values_source": "explicit user-supplied linear DC netlist; no component data inferred",
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
