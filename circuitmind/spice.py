"""Conservative validator for batch-only SPICE decks (not a process sandbox)."""
from __future__ import annotations

from typing import Any


_ALLOWED_DOT_DIRECTIVES = {
    ".ac", ".dc", ".end", ".ends", ".four", ".func", ".global", ".ic", ".meas", ".measure",
    ".model", ".nodeset", ".op", ".option", ".param", ".print", ".save", ".subckt", ".temp", ".tf", ".tran",
}
_ANALYSES = {".ac", ".dc", ".op", ".tran"}
_FORBIDDEN_DIRECTIVES = {".control", ".endc", ".include", ".inc", ".lib", ".hdl", ".shell", ".exec", ".source", ".spiceinit"}


def validate_spice_deck(text: str) -> dict[str, Any]:
    """Permit a bounded conventional netlist subset and reject control/file commands."""
    errors: list[str] = []
    if not isinstance(text, str) or not text.strip():
        return {"accepted": False, "errors": ["SPICE deck is empty or not text"]}
    try:
        byte_length = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return {"accepted": False, "errors": ["SPICE deck contains invalid Unicode surrogate data"]}
    if byte_length > 12_000_000:
        return {"accepted": False, "errors": ["SPICE deck exceeds the 12 MB limit"]}
    lines = text.splitlines()
    if len(lines) > 200_000:
        return {"accepted": False, "errors": ["SPICE deck exceeds the 200,000-line limit"]}
    if "\x00" in text:
        return {"accepted": False, "errors": ["SPICE deck contains a NUL character"]}
    analyses: list[str] = []
    end_lines: list[int] = []
    # SPICE treats the first line as a title, not a device or directive.
    for line_number, original in enumerate(lines[1:], start=2):
        line = original.strip()
        if not line or line.startswith("*"):
            continue
        line = line.split("$", 1)[0].strip()
        if not line:
            continue
        if "!" in line:
            errors.append(f"Line {line_number}: exclamation/shell escapes are not allowed")
        if line.startswith("."):
            directive = line.split(None, 1)[0].lower()
            if directive in _FORBIDDEN_DIRECTIVES:
                errors.append(f"Line {line_number}: {directive} is not allowed in batch deck uploads")
            elif directive not in _ALLOWED_DOT_DIRECTIVES:
                errors.append(f"Line {line_number}: unsupported directive {directive}")
            if directive in _ANALYSES:
                analyses.append(directive)
            if directive == ".end":
                end_lines.append(line_number)
    if not analyses:
        errors.append("No supported analysis directive (.op, .dc, .ac, or .tran) was found")
    if len(end_lines) != 1:
        errors.append("Deck must contain exactly one .end directive")
    elif any(line.strip() and not line.lstrip().startswith("*") for line in lines[end_lines[0]:]):
        errors.append("No active statements may follow .end")
    return {"accepted": not errors, "errors": errors,
            "supported_analyses": sorted(set(analyses)),
            "scope": "Static directive allowlist only; does not prove the deck is electrically correct or safe against simulator/parser vulnerabilities."}
