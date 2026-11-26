"""Read-only discovery for optional engineering executables."""
from __future__ import annotations

import shutil
import subprocess
from typing import Any


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
