import unittest

from tests.native_core import ensure_native_core
ensure_native_core()

from circuitmind.engineering import simulate_dc_sweep


def divider_netlist():
    return {"ground": "0", "elements": [
        {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"], "voltage": {"value": 1, "unit": "V"}},
        {"type": "resistor", "reference": "R1", "nodes": ["VIN", "MID"], "resistance": {"value": 1, "unit": "kohm"}},
        {"type": "resistor", "reference": "R2", "nodes": ["MID", "0"], "resistance": {"value": 1, "unit": "kohm"}},
    ]}


class DcSweepTests(unittest.TestCase):
    def test_linear_voltage_source_sweep_matches_divider_reference(self):
        report = simulate_dc_sweep({
            "netlist": divider_netlist(),
            "sweep": {"reference": "V1", "start_value": {"value": 0, "unit": "V"},
                      "stop_value": {"value": 10, "unit": "V"}, "points": 5, "scale": "linear"},
        })
        outputs = report["outputs"]
        self.assertEqual(outputs["status"], "completed-linear-dc-sweep")
        self.assertEqual(outputs["sweep_values"], [0, 2.5, 5, 7.5, 10])
        self.assertEqual(outputs["node_voltage_traces_v"]["MID"], [0, 1.25, 2.5, 3.75, 5])
        self.assertEqual(outputs["solver_engine"], "circuitmind-cpp-core")
        self.assertEqual(report["intermediate_results"]["sweep_point_count"], 5)
        self.assertLess(outputs["maximum_normalized_equation_residual"], 1e-12)

    def test_logarithmic_current_source_sweep_preserves_signed_direction(self):
        netlist = {"ground": "0", "elements": [
            {"type": "current_source", "reference": "I1", "nodes": ["0", "N1"], "current": {"value": 1, "unit": "mA"}},
            {"type": "resistor", "reference": "R1", "nodes": ["N1", "0"], "resistance": {"value": 1, "unit": "kohm"}},
        ]}
        report = simulate_dc_sweep({
            "netlist": netlist,
            "sweep": {"reference": "I1", "start_value": {"value": 1, "unit": "uA"},
                      "stop_value": {"value": 1, "unit": "mA"}, "points": 3, "scale": "log"},
        })
        outputs = report["outputs"]
        self.assertAlmostEqual(outputs["node_voltage_traces_v"]["N1"][0], 0.001)
        self.assertAlmostEqual(outputs["node_voltage_traces_v"]["N1"][1], 0.0316227766017, places=10)
        self.assertAlmostEqual(outputs["node_voltage_traces_v"]["N1"][2], 1.0)

    def test_native_source_sweep_reselects_piecewise_linear_diode_state(self):
        netlist = {"ground": "0", "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
             "voltage": {"value": 0, "unit": "V"}},
            {"type": "resistor", "reference": "R1", "nodes": ["VIN", "OUT"],
             "resistance": {"value": 1, "unit": "kohm"}},
            {"type": "diode", "reference": "D1", "nodes": ["OUT", "0"],
             "forward_voltage": {"value": 0.7, "unit": "V"},
             "off_resistance": {"value": 1e12, "unit": "ohm"}},
        ]}
        report = simulate_dc_sweep({"netlist": netlist,
            "sweep": {"reference": "V1", "start_value": {"value": 0, "unit": "V"},
                      "stop_value": {"value": 2, "unit": "V"}, "points": 3, "scale": "linear"}})
        trace = report["outputs"]["node_voltage_traces_v"]["OUT"]
        self.assertAlmostEqual(trace[0], 0, places=12)
        self.assertAlmostEqual(trace[1], 0.7, places=9)
        self.assertAlmostEqual(trace[2], 0.7, places=9)

    def test_shockley_diode_is_supported_as_a_fixed_sweep_element(self):
        netlist = {"ground": "0", "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
             "voltage": {"value": 0, "unit": "V"}},
            {"type": "resistor", "reference": "R1", "nodes": ["VIN", "OUT"],
             "resistance": {"value": 1, "unit": "kohm"}},
            {"type": "shockley_diode", "reference": "D1", "nodes": ["OUT", "0"],
             "saturation_current": {"value": 1e-12, "unit": "A"},
             "ideality_factor": 1, "temperature_kelvin": {"value": 300, "unit": "K"}},
        ]}
        report = simulate_dc_sweep({"netlist": netlist,
            "sweep": {"reference": "V1", "start_value": {"value": 0, "unit": "V"},
                      "stop_value": {"value": 1, "unit": "V"}, "points": 3, "scale": "linear"}})
        trace = report["outputs"]["node_voltage_traces_v"]["OUT"]
        self.assertEqual(trace[0], 0.0)
        self.assertGreater(trace[1], 0.4)
        self.assertLess(trace[2], 0.7)
        self.assertGreater(trace[0], -1e-12)

    def test_invalid_sweep_and_point_singularity_are_reported(self):
        base = {"netlist": divider_netlist(),
                "sweep": {"reference": "V1", "start_value": {"value": 0, "unit": "V"},
                          "stop_value": {"value": 5, "unit": "V"}, "points": 3, "scale": "linear"}}
        invalid = []
        invalid.append({**base, "sweep": {**base["sweep"], "reference": "R1"}})
        invalid.append({**base, "sweep": {**base["sweep"], "points": 201}})
        invalid.append({**base, "sweep": {**base["sweep"], "stop_value": {"value": 0, "unit": "V"}}})
        invalid.append({**base, "sweep": {**base["sweep"], "scale": "log"}})
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                simulate_dc_sweep(request)

        floating = {"ground": "0", "elements": [
            {"type": "current_source", "reference": "I1", "nodes": ["N1", "0"], "current": {"value": 1, "unit": "mA"}},
        ]}
        with self.assertRaisesRegex(ValueError, r"point 1/2.*singular"):
            simulate_dc_sweep({"netlist": floating,
                               "sweep": {"reference": "I1", "start_value": {"value": 0, "unit": "mA"},
                                         "stop_value": {"value": 1, "unit": "mA"}, "points": 2, "scale": "linear"}})


if __name__ == "__main__":
    unittest.main()
