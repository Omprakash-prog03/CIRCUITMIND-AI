import json
import tempfile
import unittest
from pathlib import Path

from circuitmind.agent_tools import execute_agent_tool, list_agent_tools
from circuitmind.circuit_schema import new_circuit_model


class AgentToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.project_id = "0123456789ab"
        (self.folder / "project.json").write_text(json.dumps({
            "project_id": self.project_id, "name": "Read-only test", "architecture": "discrete",
            "status": "draft", "findings": [], "artifacts": []
        }), encoding="utf-8")
        (self.folder / "circuit_model.json").write_text(json.dumps(new_circuit_model()), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_catalog_contains_only_read_only_tools_without_approval(self):
        tools = list_agent_tools()
        self.assertEqual({tool["name"] for tool in tools}, {
            "project.summary", "circuit.validate", "circuit.connectivity_schedule"
        })
        self.assertTrue(all(tool["permission"] == "read-only" and not tool["requires_approval"] for tool in tools))

    def test_summary_and_validation_return_explicit_scope(self):
        summary = execute_agent_tool("project.summary", {}, self.project_id, self.folder)
        self.assertEqual(summary["project_id"], self.project_id)
        self.assertEqual(summary["name"], "Read-only test")
        validation = execute_agent_tool("circuit.validate", {}, self.project_id, self.folder)
        self.assertTrue(validation["topology_report"]["topology_valid"])
        self.assertIn("no electrical analysis", validation["scope"])

    def test_dispatch_rejects_unknown_tools_and_any_arguments(self):
        with self.assertRaisesRegex(ValueError, "not registered"):
            execute_agent_tool("shell.run", {}, self.project_id, self.folder)
        with self.assertRaisesRegex(ValueError, "accepts no arguments"):
            execute_agent_tool("project.summary", {"path": "../../secret"}, self.project_id, self.folder)


if __name__ == "__main__":
    unittest.main()
