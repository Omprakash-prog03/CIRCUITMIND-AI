"""Strict validation for imported CIRCUITMIND project ZIP backups."""
from __future__ import annotations

import io
import json
import re
import stat
import zipfile
import zlib
from pathlib import PurePosixPath
from typing import Any

from .circuit_schema import model_digest, validate_model

_PROJECT_ID = re.compile(r"^[a-f0-9]{12}$")
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 2_000
MAX_COMPRESSION_RATIO = 1_000


def read_project_archive(payload: bytes) -> tuple[str, dict[str, Any], dict[str, bytes]]:
    """Validate an exported project archive and return source ID, record, safe files.

    Returned paths are relative POSIX paths with the single archive root stripped.
    Nothing is extracted by this function.
    """
    if not isinstance(payload, bytes) or not payload:
        raise ValueError("Project archive body must be nonempty bytes")
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError(f"Project archive exceeds the {MAX_ARCHIVE_BYTES}-byte upload limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("Upload is not a valid ZIP archive") from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_MEMBERS:
            raise ValueError(f"Archive must contain between 1 and {MAX_MEMBERS} members")
        roots: set[str] = set()
        files: dict[str, bytes] = {}
        seen_paths: set[str] = set()
        seen_files: set[str] = set()
        total_size = 0
        for info in infos:
            name = info.filename
            if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
                raise ValueError("Archive contains an invalid member path")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise ValueError("Archive contains an absolute member path")
            directory = name.endswith("/")
            normalized = name[:-1] if directory else name
            raw_parts = normalized.split("/")
            path = PurePosixPath(normalized)
            if any(part in {"", ".", ".."} for part in raw_parts) or len(raw_parts) > 16:
                raise ValueError(f"Archive member path is not rooted in a project folder: {name!r}")
            root = raw_parts[0]
            if not _PROJECT_ID.fullmatch(root):
                raise ValueError("Archive root must be a 12-character project ID")
            roots.add(root)
            if directory and len(raw_parts) == 1:
                continue
            if len(raw_parts) < 2:
                raise ValueError(f"Archive member path is not rooted in a project folder: {name!r}")
            for part in raw_parts:
                if (any(char in part for char in ':*?"<>|') or part.endswith((".", " ")) or
                        any(ord(char) < 32 for char in part)):
                    raise ValueError(f"Archive member path is not portable across operating systems: {name!r}")
                device = part.split(".", 1)[0].upper()
                if device in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", device):
                    raise ValueError(f"Archive member path uses a reserved device name: {name!r}")
            if info.flag_bits & 0x1:
                raise ValueError("Encrypted project archives are not supported")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("Symbolic links are not permitted in project archives")
            relative = "/".join(path.parts[1:])
            if not relative:
                raise ValueError("Archive contains a file entry with an empty relative path")
            folded_path = relative.casefold()
            if folded_path in seen_paths:
                raise ValueError(f"Archive contains a duplicate or case-colliding path {relative!r}")
            parents = ["/".join(relative.casefold().split("/")[:index])
                       for index in range(1, len(relative.split("/")))]
            if any(parent in seen_files for parent in parents):
                raise ValueError(f"Archive member path conflicts with a file parent: {relative!r}")
            if any(existing.startswith(folded_path + "/") for existing in seen_paths):
                raise ValueError(f"Archive member path conflicts with an existing child path: {relative!r}")
            seen_paths.add(folded_path)
            if directory:
                continue
            seen_files.add(folded_path)
            if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"Archive member {relative!r} exceeds the per-file size limit")
            if total_size + info.file_size > MAX_TOTAL_UNCOMPRESSED:
                raise ValueError("Archive exceeds the total uncompressed size limit")
            if info.file_size and (info.compress_size == 0 or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO):
                raise ValueError(f"Archive member {relative!r} has an excessive compression ratio")
            member = bytearray()
            try:
                with archive.open(info, "r") as stream:
                    while True:
                        chunk = stream.read(64 * 1024)
                        if not chunk:
                            break
                        if len(member) + len(chunk) > MAX_MEMBER_BYTES:
                            raise ValueError(f"Archive member {relative!r} exceeds the per-file size limit")
                        if total_size + len(member) + len(chunk) > MAX_TOTAL_UNCOMPRESSED:
                            raise ValueError("Archive exceeds the total uncompressed size limit")
                        member.extend(chunk)
            except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, zlib.error, NotImplementedError) as exc:
                raise ValueError(f"Archive member {relative!r} could not be read") from exc
            if len(member) != info.file_size:
                raise ValueError(f"Archive member {relative!r} size does not match its ZIP metadata")
            total_size += len(member)
            files[relative] = bytes(member)
        if len(roots) != 1:
            raise ValueError("Archive must contain exactly one project root folder")
        source_id = next(iter(roots))
        if "project.json" not in files or "circuit_model.json" not in files:
            raise ValueError("Archive must contain project.json and circuit_model.json at its root")
        try:
            project = json.loads(files["project.json"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Project record JSON in the archive is invalid") from exc
        try:
            model = json.loads(files["circuit_model.json"].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Circuit model JSON in the archive is invalid") from exc
        if not isinstance(project, dict) or project.get("project_id") != source_id:
            raise ValueError("Project record ID does not match the archive root")
        if (not isinstance(project.get("name"), str) or not isinstance(project.get("architecture"), str) or
                not isinstance(project.get("created_at"), str) or not isinstance(project.get("findings"), list) or
                not isinstance(project.get("inventory"), list) or not isinstance(project.get("validation"), dict)):
            raise ValueError("Project record is missing required name, architecture, date, findings, inventory, or validation data")
        topology = validate_model(model)
        if not topology["topology_valid"]:
            raise ValueError("Archived circuit model failed schema/topology checks: " + "; ".join(topology["errors"]))
        manifest_name = "snapshots/manifest.json"
        if manifest_name in files:
            try:
                manifest = json.loads(files[manifest_name].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Archived snapshot manifest is invalid JSON: {exc}") from exc
            if not isinstance(manifest, dict) or manifest.get("project_id") != source_id or not isinstance(manifest.get("snapshots"), list):
                raise ValueError("Archived snapshot manifest does not match the project root")
            snapshot_ids: set[str] = set()
            for row in manifest["snapshots"]:
                if not isinstance(row, dict):
                    raise ValueError("Archived snapshot manifest contains an invalid record")
                snapshot_id = row.get("snapshot_id")
                digest = row.get("sha256")
                if (not isinstance(snapshot_id, str) or
                        not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}", snapshot_id) or
                        snapshot_id in snapshot_ids or not isinstance(digest, str) or
                        not re.fullmatch(r"[a-f0-9]{64}", digest)):
                    raise ValueError("Archived snapshot manifest contains an invalid or duplicate snapshot reference")
                snapshot_ids.add(snapshot_id)
                snapshot_name = f"snapshots/{snapshot_id}.json"
                if snapshot_name not in files:
                    raise ValueError(f"Archived snapshot model {snapshot_id!r} is missing")
                try:
                    snapshot_model = json.loads(files[snapshot_name].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Archived snapshot model {snapshot_id!r} is invalid JSON") from exc
                snapshot_topology = validate_model(snapshot_model)
                if not snapshot_topology["topology_valid"] or model_digest(snapshot_model) != digest:
                    raise ValueError(f"Archived snapshot model {snapshot_id!r} failed topology or hash validation")
        return source_id, project, files
