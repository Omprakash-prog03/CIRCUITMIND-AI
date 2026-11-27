import unittest

from circuitmind.engineering.operating_limits import assess_operating_limits, assess_transient_reactive_limits


class OperatingLimitTests(unittest.TestCase):
    def setUp(self):
        self.elements = [{
            "reference": "R1", "type": "resistor",
            "voltage": {"value": 5.0, "unit": "V"},
            "current": {"value": -0.002, "unit": "A"},
            "power_absorbed": {"value": -0.01, "unit": "W"},
        }]

    def test_explicit_limits_compare_absolute_reported_stresses(self):
        report = assess_operating_limits(self.elements, [{
            "reference": "R1",
            "max_abs_voltage": {"value": 6, "unit": "V"},
            "max_abs_current": {"value": 2, "unit": "mA"},
            "max_abs_power": {"value": 9, "unit": "mW"},
        }])
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["check_count"], 3)
        self.assertEqual(report["failure_count"], 1)
        self.assertEqual([item["status"] for item in report["evaluations"][0]["checks"]], ["pass", "pass", "fail"])
        self.assertAlmostEqual(report["evaluations"][0]["checks"][1]["measured_absolute"]["value"], 0.002)
        self.assertIn("not sourced from or verified", report["scope"])

    def test_dimension_mismatch_duplicate_and_unknown_reference_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "requires a voltage"):
            assess_operating_limits(self.elements, [{"reference": "R1", "max_abs_voltage": {"value": 1, "unit": "A"}}])
        with self.assertRaisesRegex(ValueError, "duplicate limit"):
            assess_operating_limits(self.elements, [{"reference": "R1", "max_abs_voltage": {"value": 1, "unit": "V"}},
                                                     {"reference": "R1", "max_abs_current": {"value": 1, "unit": "A"}}])
        with self.assertRaisesRegex(ValueError, "No measured element"):
            assess_operating_limits(self.elements, [{"reference": "C9", "max_abs_voltage": {"value": 1, "unit": "V"}}])


class TransientLimitTests(unittest.TestCase):
    def setUp(self):
        self.outputs = {
            "time_points_s": [0.0, 0.1, 0.2],
            "capacitor_voltage_traces_v": {"C1": [0.0, 3.0, -2.0]},
            "inductor_current_traces_a": {"L1": [0.0, -0.5, 0.2]},
        }

    def test_transient_peak_limits_include_time_and_absolute_polarity(self):
        report = assess_transient_reactive_limits(self.outputs, [
            {"reference": "C1", "max_abs_voltage": {"value": 2.5, "unit": "V"}},
            {"reference": "L1", "max_abs_current": {"value": 600, "unit": "mA"}},
        ])
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["failure_count"], 1)
        self.assertEqual(report["evaluations"][0]["measured_absolute_peak"], {"value": 3.0, "unit": "V"})
        self.assertEqual(report["evaluations"][0]["peak_time"], {"value": 0.1, "unit": "s"})
        self.assertEqual(report["evaluations"][1]["status"], "pass")
        self.assertIn("inter-sample peaks", report["scope"])

    def test_transient_limits_require_matching_reactive_trace_and_quantity(self):
        with self.assertRaisesRegex(ValueError, "No aligned"):
            assess_transient_reactive_limits(self.outputs, [{"reference": "L1", "max_abs_voltage": {"value": 1, "unit": "V"}}])
        with self.assertRaisesRegex(ValueError, "exactly one"):
            assess_transient_reactive_limits(self.outputs, [{"reference": "C1", "max_abs_voltage": {"value": 1, "unit": "V"}, "max_abs_current": {"value": 1, "unit": "A"}}])


if __name__ == "__main__":
    unittest.main()
