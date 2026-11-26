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

    def call_raw(self, path, data, method="PUT", headers=None):
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers or {"Content-Type": "application/pdf"})
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            return exc.code, json.loads(body) if "json" in exc.headers.get("Content-Type", "") else body
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
        code, imported = self.call(f"/api/projects/{project_id}/schematic", {"content": "(kicad_sch (version 20250101))\n"}, "PUT")
        self.assertEqual(code, 201)
        self.assertEqual(imported["status"], "attached; not parsed or electrically validated")
        code, duplicate = self.call(f"/api/projects/{project_id}/schematic", {"content": "(kicad_sch)"}, "PUT")
        self.assertEqual(code, 409)
        code, replaced = self.call(f"/api/projects/{project_id}/schematic", {"content": "(kicad_sch (version 20250101))\\n", "replace": True}, "PUT")
        self.assertEqual(code, 201)
        self.assertTrue(replaced["previous_file"].startswith("user_source.previous-"))
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

    def test_board_import_archives_replacement_and_drc_attempt_is_recorded(self):
        _, created = self.call("/api/projects", {"name": "PCB DRC API", "description": "Continue an existing board", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        board = "(kicad_pcb (version 20250101))\\n"
        code, imported = self.call(f"/api/projects/{project_id}/board", {"content": board}, "PUT")
        self.assertEqual(code, 201)
        self.assertIn("header checked only", imported["status"])
        code, duplicate = self.call(f"/api/projects/{project_id}/board", {"content": board}, "PUT")
        self.assertEqual(code, 409)
        code, replaced = self.call(f"/api/projects/{project_id}/board", {"content": board + "\\n", "replace": True}, "PUT")
        self.assertEqual(code, 201)
        self.assertTrue(replaced["previous_file"].startswith("user_source.previous-"))
        with patch.dict(os.environ, {"PATH": str(Path(self.temp.name) / "empty-path")}):
            code, result = self.call(f"/api/projects/{project_id}/integrations/kicad-drc", method="POST")
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "unavailable")
        code, report = self.call(f"/api/projects/{project_id}/artifact/kicad_drc_result.json")
        self.assertEqual(code, 200)
        self.assertIn("unavailable", report["content"])
        code, validation = self.call(f"/api/projects/{project_id}/artifact/validation_report.json")
        self.assertEqual(code, 200)
        self.assertIn("pcb_design_rule_check", validation["content"])
        code, bad = self.call(f"/api/projects/{project_id}/board", {"content": "not a board"}, "PUT")
        self.assertEqual(code, 422)

    def test_installed_symbol_audit_is_read_only_and_preserves_mismatch_evidence(self):
        _, created = self.call("/api/projects", {"name": "Symbol audit", "description": "Resolve only verified library IDs", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        symbols = Path(self.temp.name) / "symbols"
        symbols.mkdir()
        (symbols / "Device.kicad_sym").write_text('''(kicad_symbol_lib (version 20231120)
          (symbol "R" (property "Reference" "R")
            (symbol "R_1_1"
              (pin passive line (name "A") (number "1"))
              (pin passive line (name "B") (number "2")))))''', "utf-8")
        table = Path(self.temp.name) / "sym-lib-table"
        table.write_text(f'''(sym_lib_table (version 7)
          (lib (name "Device") (type "KiCad") (uri "${{KICAD_SYMBOL_DIR}}/Device.kicad_sym")))''', "utf-8")
        model = self.sample_design()
        with patch.dict(os.environ, {"KICAD_SYMBOL_LIB_TABLE": str(table), "KICAD_SYMBOL_DIR": str(symbols)}):
            code, audit = self.call(f"/api/projects/{project_id}/circuit/symbols/resolve", {"model": model}, "POST")
        self.assertEqual(code, 200)
        self.assertEqual(audit["mismatch_count"], 2)
        self.assertEqual(audit["resolved_count"], 2)
        self.assertTrue(all(item["status"] == "pin-manifest-differs" for item in audit["components"]))
        self.assertEqual(len(audit["model_sha256"]), 64)
        self.assertEqual(audit["artifact"].split(".")[-1], "json")
        code, stored = self.call(f"/api/projects/{project_id}/circuit")
        self.assertEqual(code, 200)
        self.assertEqual(stored["model"]["components"], [])
        code, artifact = self.call(f"/api/projects/{project_id}/artifact/{audit['artifact']}")
        self.assertEqual(code, 200)
        self.assertIn("No model changes", artifact["content"])

    def test_symbol_audit_does_not_guess_missing_library_ids(self):
        _, created = self.call("/api/projects", {"name": "Unresolved symbol", "description": "No implicit part selection", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        model = self.sample_design()
        model["components"] = model["components"][:1]
        model["nets"] = []
        model["components"][0]["symbol"] = None
        code, audit = self.call(f"/api/projects/{project_id}/circuit/symbols/resolve", {"model": model}, "POST")
        self.assertEqual(code, 200)
        self.assertEqual(audit["components"][0]["status"], "no-symbol-id")
        self.assertIn("none was inferred", audit["components"][0]["reason"])
        self.assertEqual(audit["unresolved_count"], 1)

    def test_datasheet_pdf_is_hash_linked_downloadable_and_extraction_attempt_is_honest(self):
        _, created = self.call("/api/projects", {"name": "Datasheet sources", "description": "Keep source evidence linked", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        model = self.sample_design()
        code, _ = self.call(f"/api/projects/{project_id}/circuit", {"model": model}, "PUT")
        self.assertEqual(code, 200)
        pdf = b"%PDF-1.4\ncontrolled fixture only\n%%EOF\n"
        code, uploaded = self.call_raw(f"/api/projects/{project_id}/datasheets?filename=clock%20datasheet.pdf&reference=R1", pdf)
        self.assertEqual(code, 201)
        self.assertEqual(uploaded["component_reference"], "R1")
        self.assertEqual(len(uploaded["sha256"]), 64)
        self.assertEqual(len(uploaded["circuit_model_sha256"]), 64)
        self.assertIn("not run", uploaded["status"])
        code, sources = self.call(f"/api/projects/{project_id}/datasheets")
        self.assertEqual(code, 200)
        self.assertEqual(sources["sources"][0]["sha256"], uploaded["sha256"])
        code, downloaded = self.call(f"/api/projects/{project_id}/artifact/{uploaded['file']}")
        self.assertEqual(code, 200)
        self.assertEqual(downloaded, pdf)
        code, bad_reference = self.call_raw(f"/api/projects/{project_id}/datasheets?filename=bad.pdf&reference=U99", pdf)
        self.assertEqual(code, 422)
        with patch.dict(os.environ, {"PATH": str(Path(self.temp.name) / "empty-path")}):
            code, extraction = self.call(f"/api/projects/{project_id}/datasheets/{uploaded['sha256']}/extract", method="POST")
        self.assertEqual(code, 200)
        self.assertEqual(extraction["status"], "unavailable")
        self.assertEqual(extraction["source_sha256"], uploaded["sha256"])
        code, report = self.call(f"/api/projects/{project_id}/artifact/{extraction['report_file']}")
        self.assertEqual(code, 200)
        self.assertIn("unavailable", report["content"])
        code, bad_header = self.call_raw(f"/api/projects/{project_id}/datasheets?filename=not-pdf.pdf", b"not a PDF")
        self.assertEqual(code, 422)

    def test_spice_deck_import_requires_static_allowlist_and_simulation_is_logged(self):
        _, created = self.call("/api/projects", {"name": "SPICE API", "description": "RC transient test", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        unsafe = "unsafe\n.control\nshell echo bad\n.endc\n.tran 1u 1m\n.end\n"
        code, rejected = self.call(f"/api/projects/{project_id}/simulation-deck", {"content": unsafe}, "PUT")
        self.assertEqual(code, 422)
        self.assertIn("directive allowlist", rejected["error"])
        deck = "RC transient test\nV1 in 0 5\nR1 in out 1k\nC1 out 0 1u\n.tran 1u 5m\n.end\n"
        code, imported = self.call(f"/api/projects/{project_id}/simulation-deck", {"content": deck}, "PUT")
        self.assertEqual(code, 201)
        self.assertIn("static directive checks", imported["status"])
        code, duplicate = self.call(f"/api/projects/{project_id}/simulation-deck", {"content": deck}, "PUT")
        self.assertEqual(code, 409)
        code, replaced = self.call(f"/api/projects/{project_id}/simulation-deck", {"content": deck, "replace": True}, "PUT")
        self.assertEqual(code, 201)
        self.assertTrue(replaced["previous_file"].startswith("user_source.previous-"))
        with patch.dict(os.environ, {"PATH": str(Path(self.temp.name) / "empty-path")}):
            code, result = self.call(f"/api/projects/{project_id}/integrations/ngspice", method="POST")
        self.assertEqual(code, 200)
        self.assertEqual(result["status"], "unavailable")
        raw_fixture = app_server.DATA_DIR / "projects" / project_id / "ngspice_fixture.raw"
        raw_fixture.write_bytes(bytes.fromhex("726177006279746573"))
        code, downloaded = self.call(f"/api/projects/{project_id}/artifact/{raw_fixture.name}")
        self.assertEqual(code, 200)
        self.assertEqual(downloaded, bytes.fromhex("726177006279746573"))
        code, log = self.call(f"/api/projects/{project_id}/artifact/tool_runs.jsonl")
        self.assertEqual(code, 200)
        self.assertIn("ngspice batch simulation", log["content"])

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
