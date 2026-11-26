"""Build the native core in a temporary location for Python/native integration tests."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TEMP: tempfile.TemporaryDirectory[str] | None = None
_LOCK = threading.Lock()


def ensure_native_core() -> Path:
    global _TEMP
    configured = os.environ.get("CIRCUITMIND_ENGINE_LIB")
    if configured and Path(configured).is_file():
        return Path(configured)
    with _LOCK:
        configured = os.environ.get("CIRCUITMIND_ENGINE_LIB")
        if configured and Path(configured).is_file():
            return Path(configured)
        if _TEMP is None:
            _TEMP = tempfile.TemporaryDirectory(prefix="circuitmind-native-")
            output = Path(_TEMP.name) / ("circuitmind_core.dll" if os.name == "nt" else "libcircuitmind_core.so")
            compiler = shutil.which("g++") or shutil.which("clang++")
            if compiler and os.name != "nt":
                command = [compiler, "-std=c++20", "-O2", "-fPIC", "-shared",
                           "-I", str(_ROOT / "core" / "include"),
                           str(_ROOT / "core" / "src" / "engine.cpp"),
                           str(_ROOT / "core" / "src" / "c_api.cpp"), "-o", str(output)]
            else:
                cmake = shutil.which("cmake")
                if not cmake:
                    raise RuntimeError("C++ integration tests require g++/clang++ or CMake on PATH")
                build_dir = Path(_TEMP.name) / "build"
                subprocess.run([cmake, "-S", str(_ROOT), "-B", str(build_dir), "-DCIRCUITMIND_BUILD_TESTS=OFF"],
                               check=True, capture_output=True, text=True)
                subprocess.run([cmake, "--build", str(build_dir), "--config", "Release"],
                               check=True, capture_output=True, text=True)
                names = ("circuitmind_core.dll", "libcircuitmind_core.dylib", "libcircuitmind_core.so")
                matches = [path for name in names for path in build_dir.rglob(name)]
                if not matches:
                    raise RuntimeError("CMake built successfully but the native shared library was not found")
                output = matches[0]
                os.environ["CIRCUITMIND_ENGINE_LIB"] = str(output)
                return output
            subprocess.run(command, check=True, capture_output=True, text=True)
            os.environ["CIRCUITMIND_ENGINE_LIB"] = str(output)
        return Path(os.environ["CIRCUITMIND_ENGINE_LIB"])
