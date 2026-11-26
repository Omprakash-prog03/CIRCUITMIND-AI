import math
import unittest

from tests.native_core import ensure_native_core
ensure_native_core()

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

    def test_piecewise_linear_diode_forward_and_reverse_operating_points(self):
        forward = {"ground": "0", "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
             "voltage": {"value": 5, "unit": "V"}},
            {"type": "resistor", "reference": "R1", "nodes": ["VIN", "OUT"],
             "resistance": {"value": 1, "unit": "kohm"}},
            {"type": "diode", "reference": "D1", "nodes": ["OUT", "0"],
             "forward_voltage": {"value": 0.7, "unit": "V"},
             "off_resistance": {"value": 1e12, "unit": "ohm"}},
        ]}
        report = solve_dc_operating_point(forward)
        self.assertEqual(report["outputs"]["solver_status"], "solved-piecewise-linear-diode-dc-model")
        self.assertAlmostEqual(report["outputs"]["node_voltages"]["OUT"]["value"], 0.7, places=9)
        by_ref = {item["reference"]: item for item in report["outputs"]["elements"]}
        self.assertAlmostEqual(by_ref["D1"]["current"]["value"], 4.3e-3, places=9)
        self.assertAlmostEqual(report["outputs"]["total_power_balance"]["value"], 0, places=10)
        self.assertIn("piecewise-linear diode", " ".join(report["warnings"]))

        reverse = {**forward, "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
             "voltage": {"value": -5, "unit": "V"}},
            forward["elements"][1], forward["elements"][2],
        ]}
        reverse_report = solve_dc_operating_point(reverse)
        reverse_diode = {item["reference"]: item for item in reverse_report["outputs"]["elements"]}["D1"]
        self.assertAlmostEqual(reverse_diode["current"]["value"], -5e-12, delta=1e-18)

    def test_shockley_diode_operating_point_reports_parameters_and_converges(self):
        netlist = {"ground": "0", "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
             "voltage": {"value": 5, "unit": "V"}},
            {"type": "resistor", "reference": "R1", "nodes": ["VIN", "OUT"],
             "resistance": {"value": 1, "unit": "kohm"}},
            {"type": "shockley_diode", "reference": "D1", "nodes": ["OUT", "0"],
             "saturation_current": {"value": 1e-12, "unit": "A"},
             "ideality_factor": 1.0, "temperature_kelvin": {"value": 300, "unit": "K"}},
        ]}
        report = solve_dc_operating_point(netlist)
        outputs = report["outputs"]
        self.assertEqual(outputs["solver_status"], "solved-shockley-diode-newton-dc-model")
        diode_voltage = outputs["node_voltages"]["OUT"]["value"]
        by_ref = {item["reference"]: item for item in outputs["elements"]}
        diode_current = by_ref["D1"]["current"]["value"]
        thermal_voltage = 1.380649e-23 * 300 / 1.602176634e-19
        self.assertGreater(diode_voltage, 0.5)
        self.assertLess(diode_voltage, 0.7)
        self.assertAlmostEqual(diode_current, 1e-12 * math.expm1(diode_voltage / thermal_voltage), delta=1e-10)
        self.assertAlmostEqual(diode_current, by_ref["R1"]["current"]["value"], delta=1e-9)
        self.assertLess(abs(outputs["total_power_balance"]["value"]), 1e-8)
        self.assertIn("bounded Shockley", " ".join(report["warnings"]))

    def test_shockley_parameter_bounds_and_mixed_models_are_rejected(self):
        source = {"type": "voltage_source", "reference": "V1", "nodes": ["N1", "0"],
                  "voltage": {"value": 1, "unit": "V"}}
        resistor = {"type": "resistor", "reference": "R1", "nodes": ["N1", "0"],
                    "resistance": {"value": 1, "unit": "kohm"}}
        diode = {"type": "shockley_diode", "reference": "D1", "nodes": ["N1", "0"],
                 "saturation_current": {"value": 1e-12, "unit": "A"},
                 "ideality_factor": 1.0, "temperature_kelvin": {"value": 300, "unit": "K"}}
        with self.assertRaisesRegex(ValueError, "saturation current"):
            solve_dc_operating_point({"ground": "0", "elements": [source, resistor,
                {**diode, "saturation_current": {"value": 1e-31, "unit": "A"}}]})
        with self.assertRaisesRegex(ValueError, "ideality_factor"):
            solve_dc_operating_point({"ground": "0", "elements": [source, resistor, {**diode, "ideality_factor": 2.1}]})
        with self.assertRaisesRegex(ValueError, "temperature"):
            solve_dc_operating_point({"ground": "0", "elements": [source, resistor,
                {**diode, "temperature_kelvin": {"value": 150, "unit": "K"}}]})
        pwl = {"type": "diode", "reference": "D2", "nodes": ["N1", "0"],
               "forward_voltage": {"value": 0.7, "unit": "V"},
               "off_resistance": {"value": 1e12, "unit": "ohm"}}
        with self.assertRaisesRegex(ValueError, "Combining"):
            solve_dc_operating_point({"ground": "0", "elements": [source, resistor, diode, pwl]})

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
