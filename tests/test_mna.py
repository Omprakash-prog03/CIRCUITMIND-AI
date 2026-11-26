import unittest

from circuitmind.engineering import solve_dc_operating_point


class ModifiedNodalAnalysisTests(unittest.TestCase):
    def test_resistive_voltage_divider_operating_point_and_power_balance(self):
        netlist = {
            "ground": "0",
            "elements": [
                {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"], "voltage": {"value": 5, "unit": "V"}},
                {"type": "resistor", "reference": "R1", "nodes": ["VIN", "MID"], "resistance": {"value": 1, "unit": "kohm"}},
                {"type": "resistor", "reference": "R2", "nodes": ["MID", "0"], "resistance": {"value": 1, "unit": "kohm"}},
            ],
        }
        report = solve_dc_operating_point(netlist)
        output = report["outputs"]
        self.assertEqual(output["solver_status"], "solved-linear-dc-model")
        self.assertAlmostEqual(output["node_voltages"]["VIN"]["value"], 5)
        self.assertAlmostEqual(output["node_voltages"]["MID"]["value"], 2.5)
        by_ref = {item["reference"]: item for item in output["elements"]}
        self.assertAlmostEqual(by_ref["R1"]["current"]["value"], .0025)
        self.assertAlmostEqual(by_ref["R2"]["power_absorbed"]["value"], .00625)
        self.assertAlmostEqual(by_ref["V1"]["current"]["value"], -.0025)
        self.assertAlmostEqual(output["total_power_balance"]["value"], 0, places=12)
        self.assertLess(output["normalized_equation_residual"], 1e-12)
        self.assertIn("not transient", " ".join(report["warnings"]))
        self.assertIn("not generated from", " ".join(report["warnings"]))

    def test_current_source_sign_convention_and_reported_power(self):
        netlist = {
            "ground": "0",
            "elements": [
                {"type": "current_source", "reference": "I1", "nodes": ["0", "N1"], "current": {"value": 1, "unit": "mA"}},
                {"type": "resistor", "reference": "R1", "nodes": ["N1", "0"], "resistance": {"value": 1, "unit": "kohm"}},
            ],
        }
        report = solve_dc_operating_point(netlist)
        self.assertAlmostEqual(report["outputs"]["node_voltages"]["N1"]["value"], 1)
        by_ref = {item["reference"]: item for item in report["outputs"]["elements"]}
        self.assertAlmostEqual(by_ref["I1"]["power_absorbed"]["value"], -.001)
        self.assertAlmostEqual(by_ref["R1"]["power_absorbed"]["value"], .001)
        self.assertAlmostEqual(report["outputs"]["total_power_balance"]["value"], 0, places=12)

    def test_zero_values_and_signed_sources_are_supported(self):
        netlist = {"ground": "0", "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["N1", "0"], "voltage": {"value": 0, "unit": "V"}},
            {"type": "resistor", "reference": "R1", "nodes": ["N1", "0"], "resistance": {"value": 10, "unit": "ohm"}},
        ]}
        self.assertEqual(solve_dc_operating_point(netlist)["outputs"]["node_voltages"]["N1"]["value"], 0)

    def test_invalid_shapes_types_units_and_values_fail_closed(self):
        valid = {"ground": "0", "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["N1", "0"], "voltage": {"value": 5, "unit": "V"}},
            {"type": "resistor", "reference": "R1", "nodes": ["N1", "0"], "resistance": {"value": 1000, "unit": "ohm"}},
        ]}
        invalid = []
        invalid.append({**valid, "elements": valid["elements"] + [{"type": "capacitor", "reference": "C1", "nodes": ["N1", "0"], "capacitance": {"value": 1, "unit": "uF"}}]})
        invalid.append({"ground": "0", "elements": [{"type": "resistor", "reference": "R1", "nodes": ["N1", "0"], "resistance": {"value": 0, "unit": "ohm"}}]})
        invalid.append({**valid, "ground": "GND"})
        invalid.append({"ground": "0", "elements": valid["elements"] + [dict(valid["elements"][1])]})
        invalid.append({"ground": "0", "elements": [{"type": "resistor", "reference": "R1", "nodes": ["N1", "0"], "resistance": {"value": 1, "unit": "V"}}]})
        invalid.append({"ground": "0", "elements": [{"type": ["resistor"], "reference": "R1", "nodes": ["N1", "0"], "resistance": {"value": 1, "unit": "ohm"}}]})
        invalid.append({"ground": "0", "elements": [{"type": "resistor", "reference": "R1", "nodes": ["N1", "N1"], "resistance": {"value": 1, "unit": "ohm"}}]})
        for case in invalid:
            with self.subTest(case=case), self.assertRaises(ValueError):
                solve_dc_operating_point(case)

    def test_floating_network_and_inconsistent_ideal_voltage_loop_are_rejected(self):
        floating = {"ground": "0", "elements": [
            {"type": "resistor", "reference": "R1", "nodes": ["A", "B"], "resistance": {"value": 1, "unit": "kohm"}},
            {"type": "voltage_source", "reference": "V1", "nodes": ["A", "B"], "voltage": {"value": 1, "unit": "V"}},
            {"type": "resistor", "reference": "R2", "nodes": ["C", "0"], "resistance": {"value": 1, "unit": "kohm"}},
        ]}
        conflicting = {"ground": "0", "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["A", "0"], "voltage": {"value": 5, "unit": "V"}},
            {"type": "voltage_source", "reference": "V2", "nodes": ["A", "0"], "voltage": {"value": 3, "unit": "V"}},
        ]}
        for case in (floating, conflicting):
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, "singular|ill-conditioned"):
                solve_dc_operating_point(case)

    def test_dense_solve_size_limit_is_enforced(self):
        elements = []
        for index in range(151):
            elements.append({"type": "resistor", "reference": f"R{index}", "nodes": [f"N{index}", "0"], "resistance": {"value": 1, "unit": "kohm"}})
        with self.assertRaisesRegex(ValueError, "150-unknown-node"):
            solve_dc_operating_point({"ground": "0", "elements": elements})


if __name__ == "__main__":
    unittest.main()
