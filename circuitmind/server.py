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
from urllib.parse import unquote, urlparse

from . import __version__
from .circuit_schema import export_kicad_netlist, migrate_model, validate_model
from .integrations import discover_integrations, run_kicad_drc, run_kicad_erc, run_ngspice
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
            if filename.endswith(".raw"):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
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
        parts = urlparse(self.path).path.strip("/").split("/")
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
