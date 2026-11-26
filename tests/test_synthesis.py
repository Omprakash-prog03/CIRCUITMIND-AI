import math
import unittest

from tests.native_core import ensure_native_core
ensure_native_core()

from circuitmind.engineering import synthesize_rc_lowpass


class RcLowpassSynthesisTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {
            "cutoff_frequency": {"value": 1, "unit": "kHz"},
            "preferred_resistance": {"value": 10, "unit": "kohm"},
            "series": "E24",
            "resistor_tolerance_percent": 5,
            "capacitor_tolerance_percent": 10,
            "max_cutoff_error_percent": 5,
        }

    def test_generates_valid_graph_and_checks_nominal_rc_response(self):
        report = synthesize_rc_lowpass(self.inputs)
        self.assertEqual(report["workflow"], "single-pole-rc-low-pass-synthesis")
        self.assertEqual(report["design_status"], "candidate-meets-nominal-cutoff-constraint")
        self.assertEqual(report["selected_values"]["resistance"]["value"], 10_000)
        self.assertEqual(report["selected_values"]["capacitance"]["value"], 16e-9)
        self.assertAlmostEqual(report["selected_values"]["nominal_cutoff_frequency"]["value"], 994.7183943)
        self.assertTrue(report["graph_validation"]["topology_valid"])
        self.assertEqual(report["graph_validation"]["component_count"], 3)
        test = report["transient_test"]
        expected = 1 - math.exp(-5)
        self.assertEqual(test["sample_count"], 101)
        self.assertAlmostEqual(test["analytic_final_output_voltage"]["value"], expected)
        self.assertAlmostEqual(test["simulated_final_output_voltage"]["value"], 0.9923955100)
        self.assertLess(test["absolute_final_value_error"]["value"], 0.001)
        self.assertIn("a KiCad schematic", report["scope"])
        self.assertIsNone(report["circuit_model_candidate"]["components"][0]["symbol"])

    def test_reports_hard_cutoff_error_failure_and_tolerance_bounds(self):
        inputs = {**self.inputs, "max_cutoff_error_percent": 0.1}
        report = synthesize_rc_lowpass(inputs)
        self.assertEqual(report["design_status"], "candidate-exceeds-nominal-cutoff-constraint")
        self.assertEqual(report["constraints"]["nominal_cutoff_error"], "fail")
        bounds = report["selected_values"]["cutoff_tolerance_bounds"]
        self.assertLess(bounds["minimum"]["value"], report["selected_values"]["nominal_cutoff_frequency"]["value"])
        self.assertGreater(bounds["maximum"]["value"], report["selected_values"]["nominal_cutoff_frequency"]["value"])

    def test_rejects_bad_fields_units_series_and_out_of_range_designs(self):
        bad = [
            {**self.inputs, "surprise": True},
            {**self.inputs, "series": "E96"},
            {**self.inputs, "cutoff_frequency": {"value": 1, "unit": "V"}},
            {**self.inputs, "preferred_resistance": {"value": 1e-3, "unit": "ohm"}},
            {**self.inputs, "resistor_tolerance_percent": 51},
            {**self.inputs, "max_cutoff_error_percent": 0},
            {**self.inputs, "cutoff_frequency": {"value": 1e9, "unit": "Hz"},
             "preferred_resistance": {"value": 100e6, "unit": "ohm"}},
        ]
        for inputs in bad:
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                synthesize_rc_lowpass(inputs)


if __name__ == "__main__":
    unittest.main()
