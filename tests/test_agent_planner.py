import json
import unittest

from circuitmind.agent_planner import (
    build_compact_execution_evidence,
    execution_report_digest,
    parse_evidence_summary,
    parse_read_only_plan,
    resolve_evidence_values,
    summary_report_digest,
)


class AgentPlannerTests(unittest.TestCase):
    def test_accepts_only_bounded_registered_read_only_calls(self):
        result = parse_read_only_plan(json.dumps({
            "summary": "Validate topology and inspect connectivity.",
            "calls": [
                {"tool": "circuit.validate", "arguments": {}},
                {"tool": "circuit.connectivity_schedule", "arguments": {}},
            ],
        }))
        self.assertEqual(len(result["calls"]), 2)
        self.assertEqual(result["calls"][0]["tool"], "circuit.validate")

    def test_execution_evidence_is_compact_and_summary_keys_are_allowlisted(self):
        calls = [{"tool": "circuit.validate", "arguments": {}},
                 {"tool": "circuit.connectivity_schedule", "arguments": {}}]
        results = [
            {"tool": "circuit.validate", "status": "completed", "output": {
                "circuit_model_sha256": "a" * 64,
                "topology_report": {"topology_valid": True, "component_count": 4, "net_count": 2, "errors": []}}},
            {"tool": "circuit.connectivity_schedule", "status": "completed", "output": {
                "circuit_model_sha256": "a" * 64, "component_count": 4, "net_count": 2,
                "endpoint_count": 8, "unconnected_pin_count": 0, "no_connect_pin_count": 0}},
        ]
        evaluation = {"status": "passed", "checks": [{"name": "topology", "passed": True}]}
        evidence = build_compact_execution_evidence("0123456789ab", calls, results, evaluation)
        self.assertNotIn("nets", json.dumps(evidence))
        summary = parse_evidence_summary(json.dumps({"summary": "Topology checks passed.",
            "evidence_keys": ["topology.valid", "connectivity.net_count"]}), evidence)
        self.assertEqual(len(summary["evidence_keys"]), 2)
        self.assertEqual(resolve_evidence_values(evidence, summary["evidence_keys"]),
                         {"topology.valid": True, "connectivity.net_count": 2})
        with self.assertRaisesRegex(ValueError, "available evidence keys"):
            parse_evidence_summary(json.dumps({"summary": "The circuit is safe.",
                "evidence_keys": ["electrical.safety"]}), evidence)

    def test_execution_report_digest_detects_changes(self):
        report = {"execution_id": "run-1", "status": "passed", "checks": []}
        report["execution_report_sha256"] = execution_report_digest(report)
        self.assertEqual(execution_report_digest(report), report["execution_report_sha256"])
        report["status"] = "failed"
        self.assertNotEqual(execution_report_digest(report), report["execution_report_sha256"])

    def test_summary_report_digest_detects_changed_narrative_or_citation(self):
        report = {"summary_id": "summary-1", "summary": "Topology passed.",
                  "evidence_keys": ["topology.valid"], "cited_evidence": {"topology.valid": True}}
        report["summary_report_sha256"] = summary_report_digest(report)
        self.assertEqual(summary_report_digest(report), report["summary_report_sha256"])
        report["cited_evidence"]["topology.valid"] = False
        self.assertNotEqual(summary_report_digest(report), report["summary_report_sha256"])

    def test_rejects_write_tools_arguments_duplicate_calls_and_extra_fields(self):
        invalid_plans = [
            {"summary": "Replace model", "calls": [{"tool": "circuit.replace_model", "arguments": {}}]},
            {"summary": "Pass a path", "calls": [{"tool": "project.summary", "arguments": {"path": "x"}}]},
            {"summary": "Duplicate", "calls": [
                {"tool": "project.summary", "arguments": {}},
                {"tool": "project.summary", "arguments": {}}]},
            {"summary": "Extra", "calls": [{"tool": "project.summary", "arguments": {}}], "mutate": True},
            {"summary": "Too many", "calls": [
                {"tool": "project.summary", "arguments": {}},
                {"tool": "circuit.validate", "arguments": {}},
                {"tool": "circuit.connectivity_schedule", "arguments": {}},
                {"tool": "project.summary", "arguments": {}}]},
        ]
        for plan in invalid_plans:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                parse_read_only_plan(json.dumps(plan))

    def test_rejects_markdown_invalid_json_and_oversized_content(self):
        for text in ("```json\n{}\n```", "not json", " " * 64_001):
            with self.subTest(content_length=len(text)), self.assertRaises(ValueError):
                parse_read_only_plan(text)


if __name__ == "__main__":
    unittest.main()
