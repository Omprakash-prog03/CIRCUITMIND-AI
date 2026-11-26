import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import circuitmind.server as app_server
from circuitmind.server import Handler


class QuietHandler(Handler):
    def log_message(self, fmt, *args):
        pass


class CircuitApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_data_dir = app_server.DATA_DIR
        app_server.DATA_DIR = Path(self.temp.name)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        app_server.DATA_DIR = self.previous_data_dir
        self.temp.cleanup()

    def call(self, path, payload=None, method="GET"):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
        with response:
            body = response.read()
            return response.status, json.loads(body) if "json" in response.headers.get("Content-Type", "") else body

    def sample_design(self):
        pins = [{"number": "1", "name": "~", "type": "passive"}, {"number": "2", "name": "~", "type": "passive"}]
        return {"schema_version": 2, "metadata": {"units": "mm", "grid_mm": 1.27}, "status": "draft",
                "components": [{"reference": ref, "value": "10k", "symbol": {"library": "Device", "name": "R"},
                                "footprint": "", "datasheet": "", "pins": pins,
                                "position_mm": {"x": x, "y": 10}, "rotation_deg": 0}
                               for ref, x in (("R1", 10), ("R2", 20))],
                "nets": [{"name": "A", "terminals": [{"reference": "R1", "pin": "1"}, {"reference": "R2", "pin": "1"}]},
                         {"name": "B", "terminals": [{"reference": "R1", "pin": "2"}, {"reference": "R2", "pin": "2"}]}],
                "inventory": [], "limitations": []}

    def test_end_to_end_create_validate_save_export_snapshot_restore(self):
        code, created = self.call("/api/projects", {"name": "API clock", "description": "Clock with alarm", "inventory": "CD4511 × 3", "architecture": "discrete"}, "POST")
        self.assertEqual(code, 201)
        project_id = created["project_id"]
        code, loaded = self.call(f"/api/projects/{project_id}/circuit")
        self.assertEqual(code, 200)
        self.assertEqual(loaded["model"]["schema_version"], 2)
        model = self.sample_design()
        code, checked = self.call(f"/api/projects/{project_id}/circuit/validate", {"model": model}, "POST")
        self.assertEqual(code, 200)
        self.assertTrue(checked["topology_report"]["topology_valid"])
        code, saved = self.call(f"/api/projects/{project_id}/circuit", {"model": model}, "PUT")
        self.assertEqual(code, 200)
        self.assertEqual(saved["snapshots"]["count"], 1)
        self.assertIn("generated", saved["kicad_export"]["status"])
        code, artifact = self.call(f"/api/projects/{project_id}/artifact/kicad_netlist.net")
        self.assertEqual(code, 200)
        parsed = ET.fromstring(artifact["content"])
        self.assertEqual(parsed.find("./nets/net[@name='A']/node[@ref='R1']").attrib["pin"], "1")
        code, snapshot_list = self.call(f"/api/projects/{project_id}/snapshots")
        self.assertEqual(code, 200)
        snapshot_id = snapshot_list["snapshots"][0]["snapshot_id"]
        code, restored = self.call(f"/api/projects/{project_id}/snapshots/restore", {"snapshot_id": snapshot_id}, "POST")
        self.assertEqual(code, 200)
        self.assertEqual(restored["model"]["components"], [])
        self.assertEqual(restored["topology_report"]["component_count"], 0)
        code, report = self.call(f"/api/projects/{project_id}/artifact/kicad_export_report.json")
        self.assertEqual(code, 200)
        self.assertIn("blocked", report["content"])

    def test_schematic_import_is_immutable_and_erc_unavailability_is_recorded(self):
        _, created = self.call("/api/projects", {"name": "Existing KiCad", "description": "Continue schematic", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        code, imported = self.call(f"/api/projects/{project_id}/schematic", {"content": "(kicad_sch (version 20250101))\\n"}, "PUT")
        self.assertEqual(code, 201)
        self.assertEqual(imported["status"], "attached; not parsed or electrically validated")
        code, duplicate = self.call(f"/api/projects/{project_id}/schematic", {"content": "(kicad_sch)"}, "PUT")
        self.assertEqual(code, 409)
        with patch.dict(os.environ, {"PATH": str(Path(self.temp.name) / "empty-path")}):
            code, result = self.call(f"/api/projects/{project_id}/integrations/kicad-erc", method="POST")
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "unavailable")
        code, log = self.call(f"/api/projects/{project_id}/artifact/tool_runs.jsonl")
        self.assertEqual(code, 200)
        self.assertIn("unavailable", log["content"])
        code, artifact = self.call(f"/api/projects/{project_id}/artifact/user_source.kicad_sch")
        self.assertEqual(code, 200)
        self.assertIn("kicad_sch", artifact["content"])

    def test_invalid_model_is_rejected_without_snapshot(self):
        _, created = self.call("/api/projects", {"name": "API clock", "description": "Clock", "inventory": "", "architecture": "discrete"}, "POST")
        bad_model = self.sample_design()
        bad_model["nets"][0]["terminals"][0]["pin"] = "not-a-pin"
        code, result = self.call(f"/api/projects/{created['project_id']}/circuit", {"model": bad_model}, "PUT")
        self.assertEqual(code, 422)
        self.assertTrue(result["topology_report"]["errors"])
        _, snapshots = self.call(f"/api/projects/{created['project_id']}/snapshots")
        self.assertEqual(snapshots["count"], 0)


if __name__ == "__main__":
    unittest.main()
