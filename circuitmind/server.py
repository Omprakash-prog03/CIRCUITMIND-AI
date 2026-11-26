"""Local HTTP API and static application server."""
from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__
from .integrations import discover_integrations
from .models import ProjectInput
from .workflow import analyze, make_zip


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA_DIR = Path(os.environ.get("CIRCUITMIND_DATA_DIR", ROOT / "workspace")).expanduser().resolve()
MAX_BODY = 1_000_000


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
            return self._json(200, {"name": filename, "content": payload.decode("utf-8")})
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
        if urlparse(self.path).path != "/api/projects":
            return self._json(404, {"error": "Not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json(400, {"error": "Invalid Content-Length"})
        if length <= 0 or length > MAX_BODY:
            return self._json(413, {"error": f"Request must be between 1 and {MAX_BODY} bytes"})
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON request must be an object")
            values = ProjectInput(name=str(payload.get("name", "")), description=str(payload.get("description", "")),
                                  inventory_text=str(payload.get("inventory", "")), architecture=str(payload.get("architecture", "discrete")))
            record, _ = analyze(values, DATA_DIR)
            return self._json(201, record.to_dict())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": f"Invalid JSON: {exc}"})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except OSError as exc:
            return self._json(500, {"error": f"Unable to save project: {exc}"})


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
