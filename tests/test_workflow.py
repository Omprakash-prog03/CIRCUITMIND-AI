import json
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from circuitmind.calculations import led_current_limit_resistor, ne555_astable_frequency, voltage_divider
from circuitmind.circuit import connect_pins, create_component, create_net, validate_connectivity
from circuitmind.circuit_schema import export_kicad_netlist, migrate_model, new_circuit_model, validate_model
from circuitmind.models import CircuitModel, ProjectInput
from circuitmind.project_store import list_snapshots, load_circuit, restore_snapshot, save_circuit
from circuitmind.workflow import analyze, make_zip, parse_inventory


class InventoryTests(unittest.TestCase):
    def test_parses_plain_and_multiplication_quantities(self):
        items = parse_inventory("Component Quantity\nCD4518B × 3\n- Push buttons 4\nResistor assortment")
        self.assertEqual([(item.name, item.quantity) for item in items], [("CD4518B", 3), ("Push buttons", 4), ("Resistor assortment", None)])

    def test_skips_empty_lines_and_header(self):
        self.assertEqual(parse_inventory("\nComponent Quantity\n"), [])


class CalculationTests(unittest.TestCase):
    def test_led_resistor_has_formula_and_correct_result(self):
        result = led_current_limit_resistor(5, 2, .005)
        self.assertAlmostEqual(result["resistance_ohm"], 600)
        self.assertAlmostEqual(result["dissipation_w"], .015)
        self.assertIn("V_supply", result["formula"])

    def test_555_estimate_and_period(self):
        result = ne555_astable_frequency(47_000, 47_000, 10e-6)
        self.assertAlmostEqual(result["frequency_hz"], 1.0213, places=3)
        self.assertAlmostEqual(result["frequency_hz"] * result["period_s"], 1)

    def test_invalid_calculation_inputs_fail_closed(self):
        for fn, args in [(led_current_limit_resistor, (2, 5, .01)), (ne555_astable_frequency, (1, 0, 1)), (voltage_divider, (5, -1, 10))]:
            with self.subTest(fn=fn.__name__), self.assertRaises(ValueError):
                fn(*args)


class CircuitModelTests(unittest.TestCase):
    def test_connected_net_validates_topology_and_unconnected_pin_is_reported(self):
        model = CircuitModel()
        create_component(model, "U1", "Example IC", {"1": "IN", "2": "OUT", "3": "GND"})
        create_component(model, "R1", "10k resistor", {"1": "A", "2": "B"})
        create_net(model, "SIGNAL")
        connect_pins(model, "SIGNAL", "U1", "2")
        connect_pins(model, "SIGNAL", "R1", "1")
        report = validate_connectivity(model)
        self.assertTrue(report["topology_valid"])
        self.assertEqual(report["connected_pin_count"], 2)
        self.assertTrue(any("U1.1" in warning for warning in report["warnings"]))
        self.assertIn("not checked", report["scope"])

    def test_topology_detects_invalid_pin_and_duplicate_net_assignment(self):
        model = CircuitModel()
        create_component(model, "U1", "Example IC", {"1": "IN"})
        create_net(model, "A")
        create_net(model, "B")
        connect_pins(model, "A", "U1", "1")
        model.nets[1]["terminals"].append({"reference": "U1", "pin": "1"})
        report = validate_connectivity(model)
        self.assertFalse(report["topology_valid"])
        self.assertTrue(any("multiple nets" in error for error in report["errors"]))
        with self.assertRaises(ValueError):
            connect_pins(model, "A", "U1", "99")


class VersionedCircuitSchemaTests(unittest.TestCase):
    def test_v1_migration_preserves_connections_without_inventing_symbols(self):
        legacy = {"schema_version": 1, "status": "connectivity-draft",
                  "components": [{"reference": "R1", "part": "10k", "pins": {"1": "A", "2": "B"}}],
                  "nets": [{"name": "SIG", "terminals": [{"reference": "R1", "pin": "1"}]}],
                  "inventory_components": [{"part": "resistor", "quantity": 1}]}
        migrated = migrate_model(legacy)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["components"][0]["pins"][0]["number"], "1")
        self.assertIsNone(migrated["components"][0]["symbol"])
        self.assertEqual(migrated["nets"][0]["terminals"][0]["pin"], "1")
        migrated_report = validate_model(migrated)
        self.assertTrue(migrated_report["topology_valid"])
        self.assertTrue(migrated_report["warnings"])
        with self.assertRaises(ValueError):
            migrate_model({"schema_version": 999})

    def test_netlist_is_xml_and_requires_symbol_ids(self):
        model = new_circuit_model()
        pins = [{"number": "1", "name": "A", "type": "passive"}, {"number": "2", "name": "B", "type": "passive"}]
        for ref in ("R1", "R2"):
            model["components"].append({"reference": ref, "value": "10k", "symbol": {"library": "Device", "name": "R"},
                                         "footprint": "Resistor_SMD:R_0603_1608Metric", "datasheet": "", "pins": pins,
                                         "position_mm": {"x": 10.0 if ref == "R1" else 20.0, "y": 5.0}, "rotation_deg": 0})
        model["nets"].append({"name": "SENSE", "terminals": [{"reference": "R1", "pin": "2"}, {"reference": "R2", "pin": "1"}]})
        self.assertTrue(validate_model(model)["topology_valid"])
        root = ET.fromstring(export_kicad_netlist(model, "test-project"))
        self.assertEqual(root.tag, "export")
        self.assertEqual(root.find("./components/comp[@ref='R1']/libsource").attrib, {"lib": "Device", "part": "R"})
        self.assertEqual(root.find("./libparts/libpart[@part='R']/fields/field[@name='Reference']").text, "R")
        self.assertEqual(root.find("./nets/net[@name='SENSE']/node[@ref='R2']").attrib["pin"], "1")
        model["components"][0]["symbol"] = None
        with self.assertRaisesRegex(ValueError, "unresolved"):
            export_kicad_netlist(model)

    def test_duplicate_pin_assignment_and_untrusted_status_are_rejected(self):
        model = new_circuit_model()
        component = {"reference": "U1", "value": "IC", "symbol": None,
                     "pins": [{"number": "1", "name": "IN", "type": "input"}],
                     "position_mm": {"x": 0, "y": 0}, "rotation_deg": 0}
        model["components"] = [component]
        model["nets"] = [{"name": "A", "terminals": [{"reference": "U1", "pin": "1"}, {"reference": "U1", "pin": "1"}]},
                          {"name": "B", "terminals": [{"reference": "U1", "pin": "1"}, {"reference": "U1", "pin": "1"}]}]
        report = validate_model(model)
        self.assertFalse(report["topology_valid"])
        self.assertTrue(any("multiple nets" in error for error in report["errors"]))
        model["nets"] = []
        model["status"] = "hardware-tested"
        self.assertFalse(validate_model(model)["topology_valid"])

    def test_malformed_nested_values_fail_closed_without_type_errors(self):
        model = new_circuit_model()
        model["components"] = [{"reference": "R1", "value": "x", "symbol": None,
                                "pins": [{"number": [], "name": "x", "type": {}}],
                                "position_mm": {"x": 0, "y": 0}, "rotation_deg": 0}]
        model["nets"] = [{"name": "N", "terminals": [{"reference": [], "pin": {}}]}]
        report = validate_model(model)
        self.assertFalse(report["topology_valid"])
        self.assertTrue(report["errors"])
        model = new_circuit_model()
        model["components"] = [{"reference": "R1", "value": "bad\x00value", "symbol": None,
                                "pins": [{"number": "1", "name": "A", "type": "passive"}],
                                "position_mm": {"x": 0, "y": 0}, "rotation_deg": 0}]
        self.assertFalse(validate_model(model)["topology_valid"])


class SnapshotTests(unittest.TestCase):
    def test_tampered_snapshot_is_rejected_by_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            project_id = "012345abcdef"
            folder = Path(temp) / "projects" / project_id
            folder.mkdir(parents=True)
            original = new_circuit_model()
            (folder / "circuit_model.json").write_text(json.dumps(original), "utf-8")
            updated = json.loads(json.dumps(original))
            updated["limitations"].append("changed")
            snapshots = save_circuit(project_id, folder, updated)
            snapshot_id = snapshots["snapshots"][0]["snapshot_id"]
            damaged = json.loads((folder / "snapshots" / f"{snapshot_id}.json").read_text("utf-8"))
            damaged["limitations"].append("tampered")
            (folder / "snapshots" / f"{snapshot_id}.json").write_text(json.dumps(damaged), "utf-8")
            with self.assertRaisesRegex(ValueError, "integrity"):
                restore_snapshot(project_id, folder, snapshot_id)

    def test_save_and_restore_are_immutable_revisions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project_id = "abcdef123456"
            folder = root / "projects" / project_id
            folder.mkdir(parents=True)
            original = new_circuit_model()
            (folder / "circuit_model.json").write_text(json.dumps(original), "utf-8")
            changed = json.loads(json.dumps(original))
            changed["limitations"].append("edit one")
            snapshots = save_circuit(project_id, folder, changed)
            self.assertEqual(snapshots["count"], 1)
            self.assertEqual(load_circuit(folder)["limitations"][-1], "edit one")
            previous_id = snapshots["snapshots"][0]["snapshot_id"]
            restored, _ = restore_snapshot(project_id, folder, previous_id)
            self.assertEqual(restored["limitations"], original["limitations"])
            self.assertEqual(list_snapshots(project_id, folder)["count"], 2)
            archive_path = root / "sample.zip"
            archive_path.write_bytes(make_zip(folder))
            with zipfile.ZipFile(archive_path) as archive:
                self.assertTrue(any("snapshots/" in name for name in archive.namelist()))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_discrete_clock_reports_short_decoder_stock_and_no_fake_checks(self):
        input_data = ProjectInput("Clock", "Six digit digital clock HH:MM:SS and alarm with buttons", "CD4518 × 3\nCD4511 × 3\nSeven-segment display × 6\nPush buttons × 4\nBuzzer × 1\nNE555 × 1")
        record, artifacts = analyze(input_data, self.root)
        self.assertTrue(any("CD4511" in finding and "have 3" in finding for finding in record.findings))
        self.assertEqual(record.validation["checks"]["electrical_connectivity"], "not-run (no pin-level nets generated)")
        self.assertEqual(record.validation["checks"]["hardware_test"], "not-performed")
        self.assertNotIn("netlist.net", artifacts)
        self.assertIn("engineering_spec.json", artifacts)
        model = json.loads(artifacts["circuit_model.json"])
        self.assertEqual(model["status"], "requirements-only")
        self.assertEqual(len(list((self.root / "projects" / record.project_id).iterdir())), len(artifacts))

    def test_hybrid_does_not_claim_firmware(self):
        record, _ = analyze(ProjectInput("Hybrid", "Clock with alarm", "", "esp32"), self.root)
        self.assertEqual(record.validation["checks"]["firmware_compile"], "not-run (firmware not generated)")
        self.assertTrue(any("ESP32" in finding for finding in record.findings))

    def test_zip_contains_all_generated_files(self):
        record, artifacts = analyze(ProjectInput("Clock", "Digital clock", "CD4518 × 1"), self.root)
        archive_bytes = make_zip(self.root / "projects" / record.project_id)
        target = self.root / "export.zip"
        target.write_bytes(archive_bytes)
        with zipfile.ZipFile(target) as archive:
            names = {Path(name).name for name in archive.namelist()}
        self.assertEqual(names, set(artifacts))

    def test_rejects_unknown_architecture(self):
        with self.assertRaises(ValueError):
            analyze(ProjectInput("x", "y", "", "invented"), self.root)


if __name__ == "__main__":
    unittest.main()
