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

from tests.native_core import ensure_native_core
ensure_native_core()

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

    def mna_design(self):
        pins = [{"number": "1", "name": "A", "type": "passive"}, {"number": "2", "name": "B", "type": "passive"}]
        components = []
        for reference, description, x in (("V1", "unverified source", 10), ("R1", "10k description", 20), ("R2", "10k description", 30)):
            components.append({"reference": reference, "value": description, "symbol": None, "footprint": None,
                               "datasheet": None, "pins": pins, "position_mm": {"x": x, "y": 10}, "rotation_deg": 0})
        return {"schema_version": 2, "metadata": {"units": "mm", "grid_mm": 1.27}, "status": "draft",
                "components": components,
                "nets": [{"name": "VIN", "terminals": [{"reference": "V1", "pin": "1"}, {"reference": "R1", "pin": "1"}]},
                         {"name": "MID", "terminals": [{"reference": "R1", "pin": "2"}, {"reference": "R2", "pin": "1"}]},
                         {"name": "GND", "terminals": [{"reference": "V1", "pin": "2"}, {"reference": "R2", "pin": "2"}]}],
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

    def test_rc_lowpass_synthesis_generates_candidate_without_mutating_graph_by_default(self):
        _, created = self.call("/api/projects", {"name": "Synthesized RC", "description": "Explicit 1 kHz low-pass requirement", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        inputs = {"cutoff_frequency": {"value": 1, "unit": "kHz"},
                  "preferred_resistance": {"value": 10, "unit": "kohm"}, "series": "E24",
                  "resistor_tolerance_percent": 5, "capacitor_tolerance_percent": 10,
                  "max_cutoff_error_percent": 5}
        code, unsupported = self.call(f"/api/projects/{project_id}/designs/rc-low-pass",
                                      {"request_version": 2, "inputs": inputs, "apply_to_circuit": False}, "POST")
        self.assertEqual(code, 422)
        self.assertIn("request_version 1", unsupported["error"])
        code, report = self.call(f"/api/projects/{project_id}/designs/rc-low-pass",
                                 {"request_version": 1, "inputs": inputs, "apply_to_circuit": False}, "POST")
        self.assertEqual(code, 201)
        self.assertEqual(report["kind"], "synthesized_single_pole_rc_lowpass")
        self.assertFalse(report["applied_to_circuit_model"])
        self.assertTrue(report["design"]["graph_validation"]["topology_valid"])
        self.assertAlmostEqual(report["design"]["selected_values"]["nominal_cutoff_frequency"]["value"], 994.7183943)
        self.assertEqual(report["design"]["transient_test"]["status"], "completed-numerical-linear-rc-transient")
        failed_inputs = {**inputs, "max_cutoff_error_percent": 0.1}
        code, refused = self.call(f"/api/projects/{project_id}/designs/rc-low-pass",
                                  {"request_version": 1, "inputs": failed_inputs, "apply_to_circuit": True}, "POST")
        self.assertEqual(code, 201)
        self.assertTrue(refused["apply_requested"])
        self.assertFalse(refused["applied_to_circuit_model"])
        self.assertIn("failed the user-defined", refused["apply_refused_reason"])
        code, stored = self.call(f"/api/projects/{project_id}/circuit")
        self.assertEqual(code, 200)
        self.assertEqual(stored["model"]["components"], [])
        code, artifact = self.call(f"/api/projects/{project_id}/artifact/{report['artifact']}")
        self.assertEqual(code, 200)
        self.assertIn("candidate_circuit_model_sha256", artifact["content"])

    def test_rc_lowpass_synthesis_applies_only_with_explicit_opt_in_and_snapshots(self):
        _, created = self.call("/api/projects", {"name": "Apply synthesized RC", "description": "Explicit RC filter requirements", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        inputs = {"cutoff_frequency": {"value": 1, "unit": "kHz"},
                  "preferred_resistance": {"value": 10, "unit": "kohm"}, "series": "E24",
                  "resistor_tolerance_percent": 5, "capacitor_tolerance_percent": 10,
                  "max_cutoff_error_percent": 5}
        code, report = self.call(f"/api/projects/{project_id}/designs/rc-low-pass",
                                 {"request_version": 1, "inputs": inputs, "apply_to_circuit": True}, "POST")
        self.assertEqual(code, 201)
        self.assertTrue(report["applied_to_circuit_model"])
        self.assertEqual(report["snapshots"]["count"], 1)
        self.assertTrue(report["candidate_is_current_saved_model"])
        self.assertEqual(report["kicad_export"]["status"], "blocked")
        code, stored = self.call(f"/api/projects/{project_id}/circuit")
        self.assertEqual(code, 200)
        self.assertEqual({item["reference"] for item in stored["model"]["components"]}, {"V1", "R1", "C1"})

    def test_graph_bound_rc_transient_saves_traces_and_bounded_history_summary(self):
        _, created = self.call("/api/projects", {"name": "RC transient", "description": "Explicit RC step response", "inventory": "", "architecture": "esp32"}, "POST")
        project_id = created["project_id"]
        pins = [{"number": "1", "name": "A", "type": "passive"}, {"number": "2", "name": "B", "type": "passive"}]
        components = [{"reference": ref, "value": description, "symbol": None, "footprint": None,
                       "datasheet": None, "pins": pins, "position_mm": {"x": x, "y": 10}, "rotation_deg": 0}
                      for ref, description, x in (("V1", "unverified supply", 10), ("R1", "1000 ohm? descriptive only", 20), ("C1", "1uF? descriptive only", 30))]
        model = {"schema_version": 2, "metadata": {"units": "mm", "grid_mm": 1.27}, "status": "draft", "components": components,
                 "nets": [{"name": "VIN", "terminals": [{"reference": "V1", "pin": "1"}, {"reference": "R1", "pin": "1"}]},
                          {"name": "OUT", "terminals": [{"reference": "R1", "pin": "2"}, {"reference": "C1", "pin": "1"}]},
                          {"name": "GND", "terminals": [{"reference": "V1", "pin": "2"}, {"reference": "C1", "pin": "2"}]}],
                 "inventory": [], "limitations": []}
        self.assertEqual(self.call(f"/api/projects/{project_id}/circuit", {"model": model}, "PUT")[0], 200)
        step_waveform = {"type": "step", "initial": {"value": 0, "unit": "V"},
                         "final": {"value": 5, "unit": "V"}, "step_time": {"value": 0, "unit": "s"}}
        mapping = {"ground_net": "GND", "bindings": [
            {"reference": "V1", "type": "voltage_source", "pins": ["1", "2"], "value": step_waveform},
            {"reference": "R1", "type": "resistor", "pins": ["1", "2"], "value": {"value": 1, "unit": "kohm"}},
            {"reference": "C1", "type": "capacitor", "pins": ["1", "2"], "value": {"value": 1, "unit": "uF"},
             "initial_voltage": {"value": 0, "unit": "V"}},
        ], "exclusions": []}
        code, report = self.call(f"/api/projects/{project_id}/simulations/transient-rc/from-model", {
            "mapping": mapping, "time_step": {"value": .0001, "unit": "s"}, "stop_time": {"value": .001, "unit": "s"}}, "POST")
        self.assertEqual(code, 201)
        self.assertEqual(report["kind"], "circuit_graph_bound_linear_rc_transient")
        self.assertEqual(report["calculation"]["outputs"]["sample_count"], 11)
        output_node = report["mapping_report"]["net_name_to_solver_node"]["OUT"]
        self.assertAlmostEqual(report["calculation"]["outputs"]["node_voltage_traces_v"][output_node][-1], 5 * (1 - 1.1**-10))
        self.assertEqual(report["component_coverage"]["excluded"], 0)
        self.assertIn("descriptive only", model["components"][1]["value"])
        code, history = self.call(f"/api/projects/{project_id}/simulations")
        self.assertEqual(code, 200)
        summary = history["simulations"][-1]
        self.assertEqual(summary["netlist_status"], "intact")
        self.assertEqual(summary["mapping_status"], "intact")
        self.assertEqual(summary["outputs"]["sample_count"], 11)
        self.assertNotIn("node_voltage_traces_v", summary["outputs"])

    def test_graph_bound_mna_requires_explicit_complete_component_mapping(self):
        _, created = self.call("/api/projects", {"name": "Graph-bound MNA", "description": "Explicit net mapping", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        model = self.mna_design()
        self.assertEqual(self.call(f"/api/projects/{project_id}/circuit", {"model": model}, "PUT")[0], 200)
        mapping = {"ground_net": "GND", "bindings": [
            {"reference": "V1", "type": "voltage_source", "pins": ["1", "2"], "value": {"value": 5, "unit": "V"}},
            {"reference": "R1", "type": "resistor", "pins": ["1", "2"], "value": {"value": 1, "unit": "kohm"}},
            {"reference": "R2", "type": "resistor", "pins": ["1", "2"], "value": {"value": 1, "unit": "kohm"}},
        ], "exclusions": []}
        code, report = self.call(f"/api/projects/{project_id}/simulations/dc-operating-point/from-model", {"mapping": mapping}, "POST")
        self.assertEqual(code, 201)
        self.assertEqual(report["kind"], "circuit_graph_bound_linear_dc_mna_operating_point")
        self.assertTrue(report["component_coverage"]["all_components_accounted"])
        self.assertFalse(report["component_coverage"]["has_exclusions"])
        self.assertIn("not datasheet-verified", report["calculation"]["warnings"][1])
        self.assertIn("10k", model["components"][1]["value"])
        self.assertEqual(report["generated_netlist"]["elements"][0]["voltage"], {"value": 5, "unit": "V"})
        mid_node = report["mapping_report"]["net_name_to_solver_node"]["MID"]
        self.assertAlmostEqual(report["calculation"]["outputs"]["node_voltages"][mid_node]["value"], 2.5)
        code, history = self.call(f"/api/projects/{project_id}/simulations")
        self.assertEqual(code, 200)
        self.assertEqual(history["simulations"][-1]["kind"], "circuit_graph_bound_linear_dc_mna_operating_point")
        self.assertEqual(history["simulations"][-1]["netlist_status"], "intact")
        self.assertEqual(history["simulations"][-1]["mapping_status"], "intact")
        incomplete = {"ground_net": "GND", "bindings": mapping["bindings"][:1], "exclusions": []}
        code, rejected = self.call(f"/api/projects/{project_id}/simulations/dc-operating-point/from-model", {"mapping": incomplete}, "POST")
        self.assertEqual(code, 422)
        self.assertIn("unaccounted", rejected["error"])
        partial = {"ground_net": "GND", "bindings": mapping["bindings"][:1],
                   "exclusions": [{"reference": "R1", "reason": "No approved model for this experiment"},
                                  {"reference": "R2", "reason": "No approved model for this experiment"}]}
        code, partial_report = self.call(f"/api/projects/{project_id}/simulations/dc-operating-point/from-model", {"mapping": partial}, "POST")
        self.assertEqual(code, 201)
        self.assertTrue(partial_report["component_coverage"]["has_exclusions"])
        self.assertTrue(any("partial model" in warning for warning in partial_report["calculation"]["warnings"]))

    def test_linear_dc_mna_report_preserves_netlist_hash_and_model_association(self):
        _, created = self.call("/api/projects", {"name": "MNA DC check", "description": "Explicit linear DC model", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        model = self.sample_design()
        self.assertEqual(self.call(f"/api/projects/{project_id}/circuit", {"model": model}, "PUT")[0], 200)
        netlist = {"ground": "0", "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"], "voltage": {"value": 5, "unit": "V"}},
            {"type": "resistor", "reference": "R1", "nodes": ["VIN", "MID"], "resistance": {"value": 1, "unit": "kohm"}},
            {"type": "resistor", "reference": "R2", "nodes": ["MID", "0"], "resistance": {"value": 1, "unit": "kohm"}},
        ]}
        code, report = self.call(f"/api/projects/{project_id}/simulations/dc-operating-point", {"netlist": netlist}, "POST")
        self.assertEqual(code, 201)
        self.assertEqual(report["kind"], "linear_dc_mna_operating_point")
        self.assertAlmostEqual(report["calculation"]["outputs"]["node_voltages"]["MID"]["value"], 2.5)
        self.assertIn("not generated from", report["model_association"])
        code, history = self.call(f"/api/projects/{project_id}/simulations")
        self.assertEqual(code, 200)
        self.assertEqual(history["simulations"][-1]["model_status"], "same-at-run")
        self.assertEqual(history["simulations"][-1]["netlist_status"], "intact")
        changed_model = dict(model)
        changed_model["limitations"] = list(model["limitations"]) + ["Changed separately from explicit MNA input"]
        self.assertEqual(self.call(f"/api/projects/{project_id}/circuit", {"model": changed_model}, "PUT")[0], 200)
        code, history = self.call(f"/api/projects/{project_id}/simulations")
        self.assertEqual(code, 200)
        self.assertEqual(history["simulations"][-1]["model_status"], "model-changed-since-run")
        self.assertEqual(history["simulations"][-1]["netlist_status"], "intact")
        singular = {"ground": "0", "elements": [{"type": "current_source", "reference": "I1", "nodes": ["N1", "0"], "current": {"value": 1, "unit": "mA"}}]}
        code, error = self.call(f"/api/projects/{project_id}/simulations/dc-operating-point", {"netlist": singular}, "POST")
        self.assertEqual(code, 422)
        self.assertIn("singular", error["error"])

    def test_clock_divider_candidate_is_saved_as_math_not_implementation_approval(self):
        _, created = self.call("/api/projects", {"name": "Divider candidate", "description": "Clock rate planning", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        model = self.sample_design()
        self.assertEqual(self.call(f"/api/projects/{project_id}/circuit", {"model": model}, "PUT")[0], 200)
        inputs = {"source_frequency": {"value": 32_768, "unit": "Hz"},
                  "target_frequency": {"value": 1, "unit": "Hz"}, "source_tolerance_ppm": 20}
        code, report = self.call(f"/api/projects/{project_id}/calculations/clock-divider", {"inputs": inputs}, "POST")
        self.assertEqual(code, 201)
        self.assertEqual(report["calculation"]["outputs"]["candidate_divider_modulus"], 32_768)
        self.assertIn("no component specifications inferred", report["input_values_source"])
        self.assertIn("counter width", " ".join(report["calculation"]["validity"]))
        code, history = self.call(f"/api/projects/{project_id}/calculations")
        self.assertEqual(code, 200)
        self.assertEqual(history["calculations"][-1]["kind"], "clock_divider_candidate")
        self.assertEqual(history["calculations"][-1]["status"], "current")

    def test_clock_timebase_report_uses_explicit_rates_and_stales_with_model(self):
        _, created = self.call("/api/projects", {"name": "Clock timebase budget", "description": "Analyze explicit one hertz post-divider rate", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        model = self.sample_design()
        self.assertEqual(self.call(f"/api/projects/{project_id}/circuit", {"model": model}, "PUT")[0], 200)
        inputs = {"nominal_tick_rate": {"value": 1, "unit": "Hz"},
                  "target_tick_rate": {"value": 1, "unit": "Hz"}, "tolerance_ppm": 20}
        code, report = self.call(f"/api/projects/{project_id}/calculations/clock-timebase", {"inputs": inputs}, "POST")
        self.assertEqual(code, 201)
        self.assertEqual(report["kind"], "clock_timebase_error")
        self.assertAlmostEqual(report["calculation"]["outputs"]["maximum_clock_drift_per_day"]["value"], 1.728)
        self.assertIn("no component specifications inferred", report["input_values_source"])
        code, run_log = self.call(f"/api/projects/{project_id}/artifact/calculation_runs.jsonl")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(run_log["content"].splitlines()[-1])["kind"], "clock_timebase_error")
        code, history = self.call(f"/api/projects/{project_id}/calculations")
        self.assertEqual(code, 200)
        self.assertEqual(history["calculations"][-1]["kind"], "clock_timebase_error")
        self.assertEqual(history["calculations"][-1]["status"], "current")
        code, _ = self.call(f"/api/projects/{project_id}/calculations/clock-timebase", {
            "inputs": {**inputs, "nominal_tick_rate": {"value": 1, "unit": "mA"}}}, "POST")
        self.assertEqual(code, 422)
        changed_model = dict(model)
        changed_model["limitations"] = list(model["limitations"]) + ["Revised timebase analysis dependency"]
        self.assertEqual(self.call(f"/api/projects/{project_id}/circuit", {"model": changed_model}, "PUT")[0], 200)
        code, history = self.call(f"/api/projects/{project_id}/calculations")
        self.assertEqual(code, 200)
        clock_run = next(item for item in history["calculations"] if item["kind"] == "clock_timebase_error")
        self.assertEqual(clock_run["status"], "stale")

    def test_led_calculation_is_revision_bound_and_marked_stale_after_model_edit(self):
        _, created = self.call("/api/projects", {"name": "LED resistor math", "description": "Calculate from explicit electrical inputs", "inventory": "", "architecture": "discrete"}, "POST")
        project_id = created["project_id"]
        model = self.sample_design()
        self.assertEqual(self.call(f"/api/projects/{project_id}/circuit", {"model": model}, "PUT")[0], 200)
        inputs = {
            "supply": {"value": 5, "unit": "V"},
            "supply_range": {"min": {"value": 4.75, "unit": "V"}, "max": {"value": 5.25, "unit": "V"}},
            "led_forward": {"value": 2, "unit": "V"},
            "led_forward_range": {"min": {"value": 1.8, "unit": "V"}, "max": {"value": 2.2, "unit": "V"}},
            "target_current": {"value": 10, "unit": "mA"},
            "maximum_current": {"value": 20, "unit": "mA"},
            "resistor_tolerance_percent": 5, "e_series": 24,
            "resistor_power_rating": {"value": .25, "unit": "W"}, "power_derating_fraction": .5,
        }
        code, report = self.call(f"/api/projects/{project_id}/calculations/led-resistor", {"inputs": inputs}, "POST")
        self.assertEqual(code, 201)
        self.assertEqual(report["calculation"]["outputs"]["selected_resistance"], {"value": 300.0, "unit": "ohm"})
        self.assertEqual(report["calculation"]["outputs"]["compliance"], {"maximum_current": "pass", "resistor_power": "pass"})
        self.assertIn("explicit user-provided", report["input_values_source"])
        code, history = self.call(f"/api/projects/{project_id}/calculations")
        self.assertEqual(code, 200)
        self.assertEqual(history["calculations"][-1]["status"], "current")
        code, artifact = self.call(f"/api/projects/{project_id}/artifact/{report['artifact']}")
        self.assertEqual(code, 200)
        self.assertIn("circuit_model_sha256", artifact["content"])
        changed_model = dict(model)
        changed_model["limitations"] = list(model["limitations"]) + ["Changed revision for stale-result test"]
        self.assertEqual(self.call(f"/api/projects/{project_id}/circuit", {"model": changed_model}, "PUT")[0], 200)
        code, history = self.call(f"/api/projects/{project_id}/calculations")
        self.assertEqual(code, 200)
        self.assertEqual(history["calculations"][-1]["status"], "stale")
        invalid = dict(inputs, supply={"value": 5, "unit": "mA"})
        code, rejected = self.call(f"/api/projects/{project_id}/calculations/led-resistor", {"inputs": invalid}, "POST")
        self.assertEqual(code, 422)
        self.assertIn("voltage unit", rejected["error"])

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
