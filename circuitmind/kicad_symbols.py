"""Read-only lookup of installed KiCad symbol definitions and pin metadata.

This module reports library source evidence; it never imports pins into a circuit
model or treats symbol resolution as electrical validation.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Iterator

MAX_TABLE_BYTES = 4_000_000
MAX_LIBRARY_BYTES = 32_000_000
_MAX_TOKENS = 2_000_000
_MAX_DEPTH = 512
_LIBRARY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_+-]+$")
_SYMBOL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_./+-]+$")
_VARIABLE_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _parse_sexpr(text: str) -> list[Any]:
    """Parse KiCad's simple S-expression syntax with bounded nesting/tokens."""
    tokens: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if char == ';':
            newline = text.find("\n", i)
            i = len(text) if newline < 0 else newline + 1
            continue
        if char in "()":
            tokens.append(char)
            i += 1
        elif char == '"':
            i += 1
            value: list[str] = []
            while i < len(text) and text[i] != '"':
                if text[i] == "\\":
                    i += 1
                    if i >= len(text):
                        raise ValueError("unterminated escape in quoted S-expression string")
                    escaped = text[i]
                    value.append({"n": "\n", "r": "\r", "t": "\t"}.get(escaped, escaped))
                    i += 1
                else:
                    value.append(text[i])
                    i += 1
            if i >= len(text):
                raise ValueError("unterminated quoted S-expression string")
            tokens.append("".join(value))
            i += 1
        else:
            start = i
            while i < len(text) and not text[i].isspace() and text[i] not in "();":
                i += 1
            tokens.append(text[start:i])
        if len(tokens) > _MAX_TOKENS:
            raise ValueError("S-expression exceeds token limit")

    roots: list[Any] = []
    stack: list[list[Any]] = []
    for token in tokens:
        if token == "(":
            if len(stack) >= _MAX_DEPTH:
                raise ValueError("S-expression exceeds nesting limit")
            node: list[Any] = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif token == ")":
            if not stack:
                raise ValueError("unexpected closing parenthesis in S-expression")
            stack.pop()
        elif stack:
            stack[-1].append(token)
        else:
            roots.append(token)
    if stack:
        raise ValueError("unterminated S-expression list")
    return roots


def _children(node: list[Any], key: str) -> Iterator[list[Any]]:
    for item in node:
        if isinstance(item, list) and item and item[0] == key:
            yield item


def _first_child(node: list[Any], key: str) -> list[Any] | None:
    return next(_children(node, key), None)


def _table_candidates(project_dir: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if project_dir is not None:
        candidates.append(project_dir / "sym-lib-table")
    explicit = os.environ.get("KICAD_SYMBOL_LIB_TABLE")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    config_roots = [Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "kicad",
                     Path("~/.config/kicad").expanduser()]
    appdata = os.environ.get("APPDATA")
    if appdata:
        config_roots.append(Path(appdata) / "kicad")
    for root in dict.fromkeys(config_roots):
        if root.is_dir():
            candidates.extend(sorted(root.glob("*/sym-lib-table"), reverse=True))
            candidates.append(root / "sym-lib-table")
    candidates.extend((Path("/usr/share/kicad/symbols/sym-lib-table"),
                       Path("/usr/local/share/kicad/symbols/sym-lib-table"),
                       Path("/usr/share/kicad/template/sym-lib-table")))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.expanduser().resolve(strict=False))
        if key not in seen:
            seen.add(key)
            unique.append(candidate.expanduser())
    return unique


def _expand_library_uri(uri: str, table_path: Path) -> Path | None:
    def substitute(match: re.Match[str]) -> str:
        variable = match.group(1)
        if variable in os.environ:
            return os.environ[variable]
        if variable.endswith("_SYMBOL_DIR"):
            fallback = os.environ.get("KICAD_SYMBOL_DIR")
            if fallback:
                return fallback
            for base in (Path("/usr/share/kicad/symbols"), Path("/usr/local/share/kicad/symbols")):
                if base.is_dir():
                    return str(base)
        return match.group(0)

    expanded = _VARIABLE_PATTERN.sub(substitute, uri)
    expanded = os.path.expanduser(os.path.expandvars(expanded))
    if "${" in expanded or "://" in expanded:
        return None
    path = Path(expanded)
    if not path.is_absolute():
        path = table_path.parent / path
    return path.resolve(strict=False)


def _extract_pins(node: list[Any]) -> tuple[list[dict[str, Any]], bool]:
    pins_by_identity: dict[tuple[str, str, str], set[str]] = {}
    incomplete = False

    def walk(current: list[Any], sub_symbol: str = "") -> None:
        nonlocal incomplete
        if current and current[0] == "symbol" and len(current) > 1 and isinstance(current[1], str):
            sub_symbol = current[1]
        if current and current[0] == "pin":
            pin_type = current[1] if len(current) > 1 and isinstance(current[1], str) else "unspecified"
            name_node = _first_child(current, "name")
            number_node = _first_child(current, "number")
            if name_node is None or number_node is None or len(name_node) < 2 or len(number_node) < 2:
                incomplete = True
                return
            name, number = name_node[1], number_node[1]
            if not isinstance(name, str) or not isinstance(number, str):
                incomplete = True
                return
            identity = (number, name, pin_type)
            pins_by_identity.setdefault(identity, set()).add(sub_symbol)
            return
        for child in current:
            if isinstance(child, list):
                walk(child, sub_symbol)

    walk(node)
    pins = [{"number": number, "name": name, "type": pin_type, "sub_symbols": sorted(unit for unit in units if unit)}
            for (number, name, pin_type), units in sorted(pins_by_identity.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]))]
    return pins, incomplete


def _resolve_symbol_file(path: Path, library: str, name: str, table_path: Path) -> dict[str, Any]:
    if path.suffix != ".kicad_sym" or not path.is_file():
        return {"status": "error", "reason": "Resolved library path is missing or is not a .kicad_sym file.",
                "library_id": library, "symbol_name": name, "symbol_file": str(path), "table_file": str(table_path)}
    try:
        if path.stat().st_size > MAX_LIBRARY_BYTES:
            raise ValueError("Symbol library exceeds the 32 MB lookup limit")
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="strict")
        roots = _parse_sexpr(text)
    except (OSError, UnicodeError, ValueError) as exc:
        return {"status": "error", "reason": f"Symbol library could not be read: {exc}",
                "library_id": library, "symbol_name": name, "symbol_file": str(path), "table_file": str(table_path)}
    root = next((item for item in roots if isinstance(item, list) and item and item[0] == "kicad_symbol_lib"), None)
    if root is None:
        return {"status": "error", "reason": "File is not a KiCad symbol library S-expression.",
                "library_id": library, "symbol_name": name, "symbol_file": str(path), "table_file": str(table_path)}
    definition = next((item for item in _children(root, "symbol") if len(item) > 1 and item[1] == name), None)
    if definition is None:
        return {"status": "not-found", "reason": "Symbol name was not defined in the resolved library.",
                "library_id": library, "symbol_name": name, "symbol_file": str(path), "table_file": str(table_path),
                "library_sha256": hashlib.sha256(raw).hexdigest()}
    properties: dict[str, str] = {}
    for prop in _children(definition, "property"):
        if len(prop) >= 3 and isinstance(prop[1], str) and isinstance(prop[2], str):
            properties[prop[1]] = prop[2]
    pins, incomplete = _extract_pins(definition)
    inherited = _first_child(definition, "extends") is not None
    status = "resolved-inheritance-unexpanded" if inherited else "resolved"
    notes = []
    if inherited:
        notes.append("The symbol extends another definition; inherited pins were not expanded, so this pin list may be incomplete.")
    if incomplete:
        notes.append("One or more pin nodes were malformed and were excluded from the extracted declaration set.")
    return {"status": status, "library_id": library, "symbol_name": name,
            "symbol_file": str(path), "table_file": str(table_path),
            "library_sha256": hashlib.sha256(raw).hexdigest(), "properties": properties,
            "pins": pins, "pin_manifest_complete": not inherited and not incomplete,
            "notes": notes}


def resolve_kicad_symbol(library: str, name: str, project_dir: Path | None = None) -> dict[str, Any]:
    """Resolve an exact KiCad library ID, returning local-file provenance and pins."""
    if not isinstance(library, str) or not _LIBRARY_ID_PATTERN.fullmatch(library) or not isinstance(name, str) or not _SYMBOL_ID_PATTERN.fullmatch(name) or ".." in name.split("/"):
        return {"status": "invalid-id", "reason": "Library and symbol IDs must match the supported KiCad identifier syntax."}
    table_errors: list[str] = []
    matching_entry: tuple[str, Path, Path] | None = None
    for table_path in _table_candidates(project_dir):
        if not table_path.is_file():
            continue
        try:
            if table_path.stat().st_size > MAX_TABLE_BYTES:
                raise ValueError("symbol table exceeds the 4 MB limit")
            table_roots = _parse_sexpr(table_path.read_text("utf-8-sig"))
        except (OSError, UnicodeError, ValueError) as exc:
            table_errors.append(f"{table_path}: {exc}")
            continue
        root = next((item for item in table_roots if isinstance(item, list) and item and item[0] == "sym_lib_table"), None)
        if root is None:
            table_errors.append(f"{table_path}: missing sym_lib_table root")
            continue
        for entry in _children(root, "lib"):
            entry_name = _first_child(entry, "name")
            entry_uri = _first_child(entry, "uri")
            if entry_name is None or entry_uri is None or len(entry_name) < 2 or len(entry_uri) < 2:
                continue
            if entry_name[1] == library and isinstance(entry_uri[1], str):
                path = _expand_library_uri(entry_uri[1], table_path)
                if path is None:
                    return {"status": "error", "library_id": library, "symbol_name": name,
                            "table_file": str(table_path), "reason": "Library URI uses an unsupported variable or non-local URI."}
                matching_entry = (library, path, table_path)
                break
        if matching_entry:
            break
    if matching_entry:
        return _resolve_symbol_file(matching_entry[1], matching_entry[0], name, matching_entry[2])
    if table_errors:
        return {"status": "error", "library_id": library, "symbol_name": name,
                "reason": "No usable symbol library entry was found; table errors: " + "; ".join(table_errors[:5])}
    return {"status": "unavailable", "library_id": library, "symbol_name": name,
            "reason": "No installed KiCad symbol-library table entry matched this library ID."}
