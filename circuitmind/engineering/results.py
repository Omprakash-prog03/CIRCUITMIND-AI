"""Stable result envelope shared by deterministic engineering calculations."""
from __future__ import annotations

from typing import Any


def result(name: str, formula: str, inputs: list[dict[str, Any]], outputs: dict[str, Any], *,
           substitution: str, intermediates: dict[str, Any] | None = None,
           assumptions: list[str] | None = None, validity: list[str] | None = None,
           warnings: list[str] | None = None, scope: str | None = None) -> dict[str, Any]:
    return {
        "calculation_schema_version": 1,
        "name": name,
        "formula": formula,
        "inputs": inputs,
        "substitution": substitution,
        "intermediate_results": intermediates or {},
        "outputs": outputs,
        "assumptions": assumptions or [],
        "validity": validity or [],
        "warnings": warnings or [],
        "scope": scope or "Deterministic mathematical calculation from the listed inputs; not a simulation, datasheet verification, or design approval.",
    }
