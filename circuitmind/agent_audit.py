"""Tamper-evident, bounded hash-chain storage for agent action metadata.

The chain detects accidental or partial edits. It is not signed and cannot prevent
an attacker who can rewrite the complete file from recomputing the chain.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

_GENESIS = "0" * 64
_MAX_RECORDS = 1_000


def _canonical_hash(record: dict[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key != "record_sha256"}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse(text: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    try:
        parsed = [json.loads(line) for line in text.splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [], f"Invalid JSONL record: {exc}"
    if not parsed:
        return None, [], None
    if not all(isinstance(item, dict) for item in parsed):
        return None, [], "Audit log contains a non-object record"
    if parsed[0].get("record_type") == "chain_meta":
        meta = parsed[0]
        records = parsed[1:]
        if meta.get("schema_version") not in {1, 2}:
            return None, [], "Unsupported audit-chain schema version"
        return meta, records, None
    return None, parsed, None


def verify_agent_actions(text: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return retained action rows and integrity/retention metadata."""
    meta, records, error = _parse(text)
    if error:
        return [], {"status": "invalid", "verified": False, "error": error,
                    "retained_count": 0, "total_count": 0, "pruned_count": 0}
    if meta is None:
        if not records:
            return [], {"status": "intact", "verified": True, "retained_count": 0,
                        "total_count": 0, "pruned_count": 0, "chain_head_sha256": _GENESIS}
        return records, {"status": "legacy-unsealed", "verified": False,
                         "retained_count": len(records), "total_count": len(records),
                         "pruned_count": 0, "error": "Legacy records predate hash chaining"}

    anchor = meta.get("chain_anchor_sha256")
    pruned_count = meta.get("pruned_count")
    legacy_count = meta.get("legacy_resealed_count", 0)
    if (not isinstance(anchor, str) or len(anchor) != 64 or
            any(char not in "0123456789abcdef" for char in anchor) or
            not isinstance(pruned_count, int) or isinstance(pruned_count, bool) or pruned_count < 0 or
            not isinstance(legacy_count, int) or isinstance(legacy_count, bool) or legacy_count < 0):
        return records, {"status": "invalid", "verified": False, "error": "Malformed audit-chain metadata",
                         "retained_count": len(records), "total_count": pruned_count if isinstance(pruned_count, int) else None,
                         "pruned_count": pruned_count}
    if len(records) > _MAX_RECORDS:
        return records, {"status": "invalid", "verified": False, "error": "Audit log exceeds the retention window",
                         "retained_count": len(records), "total_count": pruned_count + len(records),
                         "pruned_count": pruned_count}
    previous = anchor
    for index, record in enumerate(records):
        if record.get("previous_record_sha256") != previous:
            return records, {"status": "tampered", "verified": False,
                             "error": f"Broken previous-record link at retained record {index}",
                             "retained_count": len(records), "total_count": pruned_count + len(records),
                             "pruned_count": pruned_count, "chain_anchor_sha256": anchor}
        claimed = record.get("record_sha256")
        try:
            calculated = _canonical_hash(record)
        except (TypeError, ValueError) as exc:
            return records, {"status": "invalid", "verified": False, "error": f"Invalid audit record: {exc}",
                             "retained_count": len(records), "total_count": pruned_count + len(records),
                             "pruned_count": pruned_count}
        if not isinstance(claimed, str) or claimed != calculated:
            return records, {"status": "tampered", "verified": False,
                             "error": f"Record hash mismatch at retained record {index}",
                             "retained_count": len(records), "total_count": pruned_count + len(records),
                             "pruned_count": pruned_count, "chain_anchor_sha256": anchor}
        previous = claimed
    total_count = pruned_count + len(records)
    if meta.get("schema_version") == 1:
        return records, {"status": "legacy-unsealed", "verified": False,
                         "retained_count": len(records), "total_count": total_count,
                         "pruned_count": pruned_count, "legacy_resealed_count": legacy_count,
                         "chain_anchor_sha256": anchor, "chain_head_sha256": previous,
                         "error": "Version 1 audit chain lacks a tail checkpoint"}
    expected_count = meta.get("total_count")
    expected_head = meta.get("chain_head_sha256")
    if (not isinstance(expected_count, int) or isinstance(expected_count, bool) or
            expected_count != total_count or not isinstance(expected_head, str) or expected_head != previous):
        return records, {"status": "tampered", "verified": False,
                         "error": "Audit log tail or total-count checkpoint does not match",
                         "retained_count": len(records), "total_count": total_count,
                         "pruned_count": pruned_count, "chain_anchor_sha256": anchor}
    return records, {"status": "intact", "verified": True,
                     "retained_count": len(records), "total_count": total_count,
                     "pruned_count": pruned_count, "legacy_resealed_count": legacy_count,
                     "chain_anchor_sha256": anchor, "chain_head_sha256": previous}


def append_agent_actions(text: str, new_records: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """Append rows, reseal legacy history explicitly, and retain a verifiable bounded chain."""
    if not isinstance(new_records, list) or not all(isinstance(row, dict) for row in new_records):
        raise ValueError("Agent audit records must be a list of objects")
    meta, records, error = _parse(text)
    if error:
        raise ValueError(f"Cannot append to an invalid agent audit log: {error}")
    if meta is None:
        if records:
            legacy_count = len(records)
            anchor = _GENESIS
            rebuilt = []
            previous = anchor
            for legacy in records:
                row = {key: value for key, value in legacy.items() if key not in {"record_sha256", "previous_record_sha256"}}
                row["legacy_resealed"] = True
                row["previous_record_sha256"] = previous
                row["record_sha256"] = _canonical_hash(row)
                previous = row["record_sha256"]
                rebuilt.append(row)
            records = rebuilt
            pruned_count = 0
        else:
            legacy_count = 0
            anchor = _GENESIS
            pruned_count = 0
    else:
        _, integrity = verify_agent_actions(text)
        if integrity["status"] == "intact":
            legacy_count = meta.get("legacy_resealed_count", 0)
            anchor = meta["chain_anchor_sha256"]
            pruned_count = meta["pruned_count"]
        elif meta.get("schema_version") == 1 and integrity["status"] == "legacy-unsealed":
            legacy_count = meta.get("legacy_resealed_count", 0) + len(records)
            anchor = meta["chain_anchor_sha256"]
            pruned_count = meta["pruned_count"]
            rebuilt = []
            previous = anchor
            for old_row in records:
                row = {key: value for key, value in old_row.items()
                       if key not in {"record_sha256", "previous_record_sha256"}}
                row["legacy_resealed"] = True
                row["previous_record_sha256"] = previous
                row["record_sha256"] = _canonical_hash(row)
                previous = row["record_sha256"]
                rebuilt.append(row)
            records = rebuilt
        else:
            raise ValueError(f"Cannot append to agent audit log with integrity status {integrity['status']}: {integrity.get('error', 'verification failed')}")

    previous = records[-1]["record_sha256"] if records else anchor
    for supplied in new_records:
        row = {key: value for key, value in supplied.items() if key not in {"record_sha256", "previous_record_sha256", "record_type"}}
        row["previous_record_sha256"] = previous
        row["record_sha256"] = _canonical_hash(row)
        records.append(row)
        previous = row["record_sha256"]

    dropped = max(0, len(records) - _MAX_RECORDS)
    if dropped:
        anchor = records[dropped - 1]["record_sha256"]
        pruned_count += dropped
        records = records[dropped:]
    chain_head = records[-1]["record_sha256"] if records else anchor
    metadata = {"record_type": "chain_meta", "schema_version": 2,
                "chain_anchor_sha256": anchor, "pruned_count": pruned_count,
                "total_count": pruned_count + len(records), "chain_head_sha256": chain_head,
                "legacy_resealed_count": legacy_count}
    encoded = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                         for row in [metadata, *records]) + "\n"
    _, integrity = verify_agent_actions(encoded)
    if integrity["status"] != "intact":
        raise ValueError(f"Generated agent audit chain did not verify: {integrity.get('error')}")
    return encoded, integrity
