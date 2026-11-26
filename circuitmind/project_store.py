"""Atomic circuit-model persistence and immutable revision snapshots."""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .circuit_schema import migrate_model, model_digest, validate_model

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_MAX_SNAPSHOTS = 100


def _project_lock(project_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(project_id, threading.RLock())


def load_circuit(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "circuit_model.json"
    if not path.is_file():
        raise FileNotFoundError("Project has no circuit model")
    data = json.loads(path.read_text("utf-8"))
    return migrate_model(data)


def save_circuit(project_id: str, project_dir: Path, model: dict[str, Any]) -> dict[str, Any]:
    report = validate_model(model)
    if not report["topology_valid"]:
        raise ValueError("Circuit model failed validation: " + "; ".join(report["errors"]))
    with _project_lock(project_id):
        current = load_circuit(project_dir)
        _snapshot(project_id, project_dir, current)
        _atomic_json(project_dir / "circuit_model.json", model)
        return list_snapshots(project_id, project_dir)


def restore_snapshot(project_id: str, project_dir: Path, snapshot_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}", snapshot_id):
        raise ValueError("Invalid snapshot id")
    with _project_lock(project_id):
        path = project_dir / "snapshots" / f"{snapshot_id}.json"
        if not path.is_file():
            raise FileNotFoundError("Snapshot not found")
        previous = load_circuit(project_dir)
        restored = migrate_model(json.loads(path.read_text("utf-8")))
        manifest = list_snapshots(project_id, project_dir)
        metadata = next((row for row in manifest["snapshots"] if row.get("snapshot_id") == snapshot_id), None)
        if metadata is None or metadata.get("sha256") != model_digest(restored):
            raise ValueError("Snapshot integrity check failed")
        validation = validate_model(restored)
        if not validation["topology_valid"]:
            raise ValueError("Snapshot model failed validation: " + "; ".join(validation["errors"]))
        _snapshot(project_id, project_dir, previous)
        _atomic_json(project_dir / "circuit_model.json", restored)
        return restored, list_snapshots(project_id, project_dir)


def list_snapshots(project_id: str, project_dir: Path) -> dict[str, Any]:
    manifest_path = project_dir / "snapshots" / "manifest.json"
    if not manifest_path.exists():
        return {"project_id": project_id, "count": 0, "snapshots": []}
    manifest = json.loads(manifest_path.read_text("utf-8"))
    return {"project_id": project_id, "count": len(manifest.get("snapshots", [])), "snapshots": manifest.get("snapshots", [])}


def _snapshot(project_id: str, project_dir: Path, model: dict[str, Any]) -> None:
    snapshots_dir = project_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = snapshots_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {"snapshots": []}
    digest = model_digest(model)
    now = datetime.now(timezone.utc)
    snapshot_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + digest[:12]
    snapshot_path = snapshots_dir / f"{snapshot_id}.json"
    if not snapshot_path.exists():
        _atomic_json(snapshot_path, model)
    rows = [row for row in manifest["snapshots"] if row.get("snapshot_id") != snapshot_id]
    rows.append({"snapshot_id": snapshot_id, "created_at": now.isoformat(timespec="seconds"), "sha256": digest})
    rows = rows[-_MAX_SNAPSHOTS:]
    retained = {row["snapshot_id"] for row in rows}
    for old in snapshots_dir.glob("*.json"):
        if old.name != "manifest.json" and old.stem not in retained:
            old.unlink(missing_ok=True)
    _atomic_json(manifest_path, {"project_id": project_id, "snapshots": rows})


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
