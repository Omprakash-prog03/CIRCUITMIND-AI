import json
import unittest

from circuitmind.agent_audit import append_agent_actions, verify_agent_actions


class AgentAuditChainTests(unittest.TestCase):
    def test_records_are_chained_and_tampering_is_detected(self):
        text, state = append_agent_actions("", [{"tool": "circuit.validate", "status": "completed", "duration_ms": 1.2}])
        self.assertEqual(state["status"], "intact")
        rows, verified = verify_agent_actions(text)
        self.assertTrue(verified["verified"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["previous_record_sha256"], "0" * 64)
        damaged = text.replace('"status":"completed"', '"status":"failed"')
        _, result = verify_agent_actions(damaged)
        self.assertEqual(result["status"], "tampered")
        truncated = "\n".join(text.splitlines()[:-1]) + "\n"
        _, result = verify_agent_actions(truncated)
        self.assertEqual(result["status"], "tampered")
        with self.assertRaisesRegex(ValueError, "Cannot append"):
            append_agent_actions(damaged, [{"tool": "project.summary"}])

    def test_legacy_history_is_explicitly_resealed(self):
        legacy = "\n".join(json.dumps({"tool": name, "status": "completed"})
                            for name in ("project.summary", "circuit.validate")) + "\n"
        text, state = append_agent_actions(legacy, [{"tool": "workflow.circuit_review", "status": "passed"}])
        self.assertEqual(state["status"], "intact")
        self.assertEqual(state["legacy_resealed_count"], 2)
        rows, _ = verify_agent_actions(text)
        self.assertTrue(all(row.get("legacy_resealed") for row in rows[:2]))

    def test_version_one_chain_is_explicitly_upgraded_with_tail_checkpoint(self):
        v2, _ = append_agent_actions("", [{"tool": "project.summary", "status": "completed"}])
        lines = v2.splitlines()
        meta = json.loads(lines[0]); meta["schema_version"] = 1
        meta.pop("total_count"); meta.pop("chain_head_sha256")
        legacy_v1 = "\n".join([json.dumps(meta), *lines[1:]]) + "\n"
        _, state = verify_agent_actions(legacy_v1)
        self.assertEqual(state["status"], "legacy-unsealed")
        upgraded, state = append_agent_actions(legacy_v1, [{"tool": "workflow.circuit_review", "status": "passed"}])
        self.assertEqual(state["status"], "intact")
        self.assertEqual(state["legacy_resealed_count"], 1)
        self.assertEqual(json.loads(upgraded.splitlines()[0])["schema_version"], 2)

    def test_pruned_chain_retains_a_verifiable_anchor(self):
        actions = [{"tool": f"tool-{index}", "status": "completed"} for index in range(1_003)]
        text, state = append_agent_actions("", actions)
        rows, verified = verify_agent_actions(text)
        self.assertEqual(verified["status"], "intact")
        self.assertEqual(verified["retained_count"], 1_000)
        self.assertEqual(verified["total_count"], 1_003)
        self.assertEqual(verified["pruned_count"], 3)
        self.assertEqual(rows[0]["previous_record_sha256"], verified["chain_anchor_sha256"])
        self.assertEqual(rows[0]["tool"], "tool-3")


if __name__ == "__main__":
    unittest.main()
