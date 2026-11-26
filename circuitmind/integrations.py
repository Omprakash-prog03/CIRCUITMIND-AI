"""Version probes and bounded, real-tool integration adapters.

These adapters invoke fixed commands without a shell. A private temporary working
folder and timeout are guardrails, not an OS security sandbox; only run trusted
local tools against trusted project files.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .spice import validate_spice_deck


_TOOLS = {
    "KiCad CLI": ("kicad-cli", ["--version"]),
    "ngspice": ("ngspice", ["--version"]),
    "PlatformIO": ("pio", ["--version"]),
    "Arduino CLI": ("arduino-cli", ["version"]),
    "GCC": ("gcc", ["--version"]),
    "Clang": ("clang", ["--version"]),
}


def discover_integrations() -> list[dict[str, Any]]:
    results = []
    for label, (executable, args) in _TOOLS.items():
        path = shutil.which(executable)
        version = None
        status = "missing"
        detail = "Executable not found on PATH."
        if path:
            try:
                proc = subprocess.run([path, *args], capture_output=True, text=True, timeout=3, check=False)
                lines = (proc.stdout or proc.stderr).strip().splitlines()
                version = lines[0][:240] if lines else "version output unavailable"
                status = "detected" if proc.returncode == 0 else "detected-version-check-failed"
                detail = f"Exit code {proc.returncode}. Version probe only; no engineering operation was run."
            except (OSError, subprocess.TimeoutExpired) as exc:
                status, detail = "detected-version-check-failed", str(exc)
        results.append({"name": label, "status": status, "executable": path, "version": version, "detail": detail})
    return results


def run_kicad_erc(project_dir: Path, timeout_seconds: float = 90.0) -> dict[str, Any]:
    """Run KiCad's schematic ERC on the explicitly imported project schematic.

    Returns checked/raw status separately: exit code zero alone is not interpreted
    as an ERC-clean result; the report summary must say zero messages.
    """
    project_dir = project_dir.resolve()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    schematic = (project_dir / "user_source.kicad_sch").resolve()
    if schematic.parent != project_dir or not schematic.is_file():
        return {"tool": "KiCad CLI schematic ERC", "run_id": run_id, "started_at": started_at,
                "status": "not-run", "reason": "No imported user_source.kicad_sch exists."}
    if schematic.stat().st_size > 12_000_000:
        return {"tool": "KiCad CLI schematic ERC", "run_id": run_id, "started_at": started_at, "status": "not-run", "reason": "Schematic exceeds 12 MB limit."}
    try:
        with schematic.open("r", encoding="utf-8-sig", errors="strict") as handle:
            if not handle.read(256).lstrip().startswith("(kicad_sch"):
                return {"tool": "KiCad CLI schematic ERC", "run_id": run_id, "started_at": started_at, "status": "not-run", "reason": "Imported file header is not a KiCad schematic."}
    except (OSError, UnicodeError) as exc:
        return {"tool": "KiCad CLI schematic ERC", "run_id": run_id, "started_at": started_at, "status": "not-run", "reason": f"Schematic could not be read: {exc}"}
    executable = shutil.which("kicad-cli")
    if not executable:
        return {"tool": "KiCad CLI schematic ERC", "run_id": run_id, "started_at": started_at, "status": "unavailable", "reason": "kicad-cli was not found on PATH; no ERC was run."}
    executable = str(Path(executable).resolve())
    report_path = project_dir / f"kicad_erc_report_{run_id}.txt"
    with tempfile.TemporaryDirectory(prefix="circuitmind-kicad-", dir=project_dir) as temp_dir:
        sandbox = Path(temp_dir)
        home = sandbox / "home"
        home.mkdir()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "TMP": str(sandbox),
               "TEMP": str(sandbox), "TMPDIR": str(sandbox)}
        command = [executable, "sch", "erc", "--output", str(report_path), str(schematic)]
        try:
            exit_code, stdout, stderr = _run_bounded(command, sandbox, env, timeout_seconds)
            raw_report = ""
            if report_path.is_file():
                with report_path.open("r", encoding="utf-8", errors="replace") as report_file:
                    raw_report = report_file.read(2_000_000)
            if not raw_report:
                raw_report = stdout or stderr
                report_path.write_text(raw_report, encoding="utf-8")
            match = re.search(r"(?i)(?:ERC\s+messages|violations)\s*:\s*(\d+)", raw_report)
            if exit_code != 0:
                status = "failed"
            elif match and int(match.group(1)) == 0:
                status = "completed-no-messages"
            elif match:
                status = "completed-with-messages"
            else:
                status = "completed-unparsed"
            return {"tool": "KiCad CLI schematic ERC", "run_id": run_id, "started_at": started_at, "status": status,
                    "exit_code": exit_code, "erc_message_count": int(match.group(1)) if match else None,
                    "command": command, "report_file": report_path.name,
                    "report": raw_report[-128_000:], "stdout": stdout, "stderr": stderr,
                    "interpretation": "A zero exit code is not considered ERC-clean unless a recognized report summary explicitly reports zero messages."}
        except subprocess.TimeoutExpired as exc:
            stdout = _as_text(exc.stdout)[-64_000:]
            stderr = _as_text(exc.stderr)[-64_000:]
            report = report_path.read_text("utf-8", errors="replace")[-128_000:] if report_path.is_file() else ""
            if report:
                return {"tool": "KiCad CLI schematic ERC", "run_id": run_id, "started_at": started_at, "status": "timeout", "exit_code": None,
                        "report_file": report_path.name, "report": report, "stdout": stdout, "stderr": stderr,
                        "reason": f"ERC exceeded {timeout_seconds:g} seconds; partial output retained."}
            return {"tool": "KiCad CLI schematic ERC", "run_id": run_id, "started_at": started_at, "status": "timeout", "exit_code": None,
                    "report": "", "stdout": stdout, "stderr": stderr,
                    "reason": f"ERC exceeded {timeout_seconds:g} seconds; no report was produced."}
        except OSError as exc:
            return {"tool": "KiCad CLI schematic ERC", "run_id": run_id, "started_at": started_at, "status": "failed-to-start", "exit_code": None,
                    "reason": f"Unable to start KiCad CLI: {exc}"}


class _TailCapture:
    def __init__(self, limit: int = 64_000):
        self.limit = limit
        self.data = bytearray()

    def drain(self, stream) -> None:
        while True:
            try:
                chunk = stream.read(4096)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            self.data.extend(chunk)
            if len(self.data) > self.limit:
                del self.data[:-self.limit]

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", errors="replace")


def _run_bounded(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: float) -> tuple[int, str, str]:
    """Run without a shell and retain only a bounded tail of each output stream."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout_capture, stderr_capture = _TailCapture(), _TailCapture()
    readers = [threading.Thread(target=stdout_capture.drain, args=(process.stdout,), daemon=True),
               threading.Thread(target=stderr_capture.drain, args=(process.stderr,), daemon=True)]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
        raise subprocess.TimeoutExpired(command, timeout_seconds, output=bytes(stdout_capture.data), stderr=bytes(stderr_capture.data))
    for reader in readers:
        reader.join(timeout=2)
    process.stdout.close()
    process.stderr.close()
    return process.returncode, stdout_capture.text(), stderr_capture.text()


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_ngspice(project_dir: Path, timeout_seconds: float = 60.0) -> dict[str, Any]:
    """Execute an imported, batch-only netlist through local ngspice (not a sandbox)."""
    project_dir = project_dir.resolve()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    deck_path = (project_dir / "user_source.cir").resolve()
    if deck_path.parent != project_dir or not deck_path.is_file():
        return {"tool": "ngspice batch simulation", "run_id": run_id, "started_at": started_at,
                "status": "not-run", "reason": "No imported user_source.cir exists."}
    if deck_path.stat().st_size > 12_000_000:
        return {"tool": "ngspice batch simulation", "run_id": run_id, "started_at": started_at,
                "status": "not-run", "reason": "SPICE deck exceeds 12 MB limit."}
    try:
        deck = deck_path.read_text("utf-8")
    except (OSError, UnicodeError) as exc:
        return {"tool": "ngspice batch simulation", "run_id": run_id, "started_at": started_at,
                "status": "not-run", "reason": f"SPICE deck could not be read: {exc}"}
    static_check = validate_spice_deck(deck)
    deck_sha256 = hashlib.sha256(deck.encode("utf-8")).hexdigest()
    if not static_check["accepted"]:
        return {"tool": "ngspice batch simulation", "run_id": run_id, "started_at": started_at,
                "status": "rejected", "reason": "Deck did not pass the batch-only directive allowlist.",
                "deck_validation": static_check, "input_sha256": deck_sha256}
    executable = shutil.which("ngspice")
    if not executable:
        return {"tool": "ngspice batch simulation", "run_id": run_id, "started_at": started_at,
                "status": "unavailable", "reason": "ngspice was not found on PATH; no simulation was run.",
                "deck_validation": static_check, "input_sha256": deck_sha256}
    executable = str(Path(executable).resolve())
    raw_path = project_dir / f"ngspice_{run_id}.raw"
    log_path = project_dir / f"ngspice_{run_id}.log"
    with tempfile.TemporaryDirectory(prefix="circuitmind-spice-", dir=project_dir) as temp_dir:
        sandbox = Path(temp_dir)
        home = sandbox / "home"
        home.mkdir()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "TMP": str(sandbox),
               "TEMP": str(sandbox), "TMPDIR": str(sandbox)}
        command = [executable, "-b", "-r", str(raw_path), str(deck_path)]
        try:
            exit_code, stdout, stderr = _run_bounded(command, sandbox, env, timeout_seconds)
            output = (stdout + "\n" + stderr)[-128_000:]
            log_path.write_text(output, encoding="utf-8")
            rows_match = re.search(r"(?i)no\.\s+of\s+data\s+rows\s*:\s*(\d+)", output)
            error_seen = re.search(r"(?im)^\s*(?:fatal\s+)?error\b|simulation interrupted", output)
            rows = int(rows_match.group(1)) if rows_match else None
            raw_size = raw_path.stat().st_size if raw_path.is_file() else 0
            if raw_size > 64_000_000:
                raw_path.unlink(missing_ok=True)
                return {"tool": "ngspice batch simulation", "run_id": run_id, "started_at": started_at,
                        "status": "failed-result-too-large", "exit_code": exit_code,
                        "reason": "Raw result exceeded 64 MB and was removed.", "log_file": log_path.name,
                        "stdout": stdout, "stderr": stderr, "deck_validation": static_check, "input_sha256": deck_sha256}
            if exit_code != 0 or error_seen:
                status = "failed"
            elif rows is not None and rows > 0:
                status = "completed"
            else:
                status = "completed-unparsed"
            return {"tool": "ngspice batch simulation", "run_id": run_id, "started_at": started_at,
                    "status": status, "exit_code": exit_code, "data_rows": rows,
                    "command": command, "raw_file": raw_path.name if raw_size else None,
                    "raw_size_bytes": raw_size, "log_file": log_path.name,
                    "stdout": stdout, "stderr": stderr, "deck_validation": static_check,
                    "input_sha256": deck_sha256,
                    "interpretation": "A nonzero exit or explicit error fails; zero exit is completed only when ngspice reports a positive data-row count."}
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = _as_text(exc.stdout)[-64_000:], _as_text(exc.stderr)[-64_000:]
            output = (stdout + "\n" + stderr)[-128_000:]
            log_path.write_text(output, encoding="utf-8")
            raw_size = raw_path.stat().st_size if raw_path.is_file() else 0
            if raw_size > 64_000_000:
                raw_path.unlink(missing_ok=True)
                raw_size = 0
            return {"tool": "ngspice batch simulation", "run_id": run_id, "started_at": started_at,
                    "status": "timeout", "exit_code": None, "reason": f"Simulation exceeded {timeout_seconds:g} seconds.",
                    "log_file": log_path.name, "raw_file": raw_path.name if raw_size else None,
                    "raw_size_bytes": raw_size, "stdout": stdout, "stderr": stderr, "deck_validation": static_check, "input_sha256": deck_sha256}
        except OSError as exc:
            return {"tool": "ngspice batch simulation", "run_id": run_id, "started_at": started_at,
                    "status": "failed-to-start", "exit_code": None, "reason": f"Unable to start ngspice: {exc}",
                    "deck_validation": static_check, "input_sha256": deck_sha256}


def run_kicad_drc(project_dir: Path, timeout_seconds: float = 120.0) -> dict[str, Any]:
    """Run KiCad board DRC on an explicitly imported PCB, preserving outcome evidence."""
    project_dir = project_dir.resolve()
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    board = (project_dir / "user_source.kicad_pcb").resolve()
    if board.parent != project_dir or not board.is_file():
        return {"tool": "KiCad CLI board DRC", "run_id": run_id, "started_at": started_at,
                "status": "not-run", "reason": "No imported user_source.kicad_pcb exists."}
    if board.stat().st_size > 12_000_000:
        return {"tool": "KiCad CLI board DRC", "run_id": run_id, "started_at": started_at,
                "status": "not-run", "reason": "PCB file exceeds 12 MB limit."}
    try:
        with board.open("r", encoding="utf-8-sig", errors="strict") as handle:
            if not handle.read(256).lstrip().startswith("(kicad_pcb"):
                return {"tool": "KiCad CLI board DRC", "run_id": run_id, "started_at": started_at,
                        "status": "not-run", "reason": "Imported file header is not a KiCad PCB board."}
    except (OSError, UnicodeError) as exc:
        return {"tool": "KiCad CLI board DRC", "run_id": run_id, "started_at": started_at,
                "status": "not-run", "reason": f"PCB file could not be read: {exc}"}
    board_sha256 = hashlib.sha256(board.read_bytes()).hexdigest()
    executable = shutil.which("kicad-cli")
    if not executable:
        return {"tool": "KiCad CLI board DRC", "run_id": run_id, "started_at": started_at,
                "status": "unavailable", "board_sha256": board_sha256,
                "reason": "kicad-cli was not found on PATH; no DRC was run."}
    executable = str(Path(executable).resolve())
    report_path = project_dir / f"kicad_drc_report_{run_id}.txt"
    with tempfile.TemporaryDirectory(prefix="circuitmind-kicad-drc-", dir=project_dir) as temp_dir:
        sandbox = Path(temp_dir)
        home = sandbox / "home"
        home.mkdir()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "TMP": str(sandbox),
               "TEMP": str(sandbox), "TMPDIR": str(sandbox)}
        command = [executable, "pcb", "drc", "--output", str(report_path), str(board)]
        try:
            exit_code, stdout, stderr = _run_bounded(command, sandbox, env, timeout_seconds)
            report = ""
            if report_path.is_file():
                with report_path.open("r", encoding="utf-8", errors="replace") as handle:
                    report = handle.read(2_000_000)
            if not report:
                report = stdout or stderr
                report_path.write_text(report, encoding="utf-8")
            violation_match = re.search(r"(?i)(?:found\s+)?(\d+)\s+(?:PCB\s+)?DRC\s+violations|(?:DRC\s+)?violations\s*:\s*(\d+)", report)
            unconnected_match = re.search(r"(?i)(?:found\s+)?(\d+)\s+unconnected\s+pads|unconnected\s+pads\s*:\s*(\d+)", report)
            violations = _first_group_int(violation_match)
            unconnected = _first_group_int(unconnected_match)
            if exit_code != 0:
                status = "failed"
            elif (violations is not None and violations > 0) or (unconnected is not None and unconnected > 0):
                status = "completed-with-violations"
            elif violations == 0 and unconnected == 0:
                status = "completed-no-reported-violations"
            else:
                status = "completed-unparsed"
            return {"tool": "KiCad CLI board DRC", "run_id": run_id, "started_at": started_at,
                    "status": status, "exit_code": exit_code, "board_sha256": board_sha256,
                    "drc_violation_count": violations, "unconnected_pad_count": unconnected, "command": command,
                    "report_file": report_path.name, "report": report[-128_000:],
                    "stdout": stdout, "stderr": stderr,
                    "interpretation": "A zero exit alone is not considered DRC-clean; explicit violation and unconnected-pad summaries are required."}
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = _as_text(exc.stdout)[-64_000:], _as_text(exc.stderr)[-64_000:]
            report = report_path.read_text("utf-8", errors="replace")[-128_000:] if report_path.is_file() else ""
            return {"tool": "KiCad CLI board DRC", "run_id": run_id, "started_at": started_at,
                    "status": "timeout", "exit_code": None, "board_sha256": board_sha256,
                    "report_file": report_path.name if report else None,
                    "report": report, "stdout": stdout, "stderr": stderr,
                    "reason": f"DRC exceeded {timeout_seconds:g} seconds; partial output retained."}
        except OSError as exc:
            return {"tool": "KiCad CLI board DRC", "run_id": run_id, "started_at": started_at,
                    "status": "failed-to-start", "exit_code": None, "board_sha256": board_sha256,
                    "reason": f"Unable to start KiCad CLI: {exc}"}


def _first_group_int(match: re.Match[str] | None) -> int | None:
    if not match:
        return None
    for group in match.groups():
        if group is not None:
            return int(group)
    return None
