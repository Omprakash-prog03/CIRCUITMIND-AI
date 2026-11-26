"""Typed domain models for project intake and generated engineering records."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class InventoryItem:
    name: str
    quantity: int | None
    raw: str


@dataclass
class ProjectInput:
    name: str
    description: str
    inventory_text: str
    architecture: str = "discrete"


@dataclass
class CircuitModel:
    """Versioned, editable connectivity data. Empty nets are not implied to exist."""
    schema_version: int = 1
    components: list[dict[str, Any]] = field(default_factory=list)
    nets: list[dict[str, Any]] = field(default_factory=list)
    status: str = "requirements-only"
    limitations: list[str] = field(default_factory=list)


@dataclass
class ProjectRecord:
    project_id: str
    name: str
    created_at: str
    updated_at: str
    description: str
    architecture: str
    status: str
    inventory: list[dict[str, Any]]
    findings: list[str]
    artifacts: list[str]
    validation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
