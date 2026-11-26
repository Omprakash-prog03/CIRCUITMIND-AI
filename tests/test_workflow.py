import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from circuitmind.calculations import led_current_limit_resistor, ne555_astable_frequency, voltage_divider
from circuitmind.circuit import connect_pins, create_component, create_net, validate_connectivity
from circuitmind.models import CircuitModel
from circuitmind.models import ProjectInput
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
