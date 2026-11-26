import copy
import math
import unittest

from tests.native_core import ensure_native_core
ensure_native_core()

from circuitmind.engineering import simulate_ac_sweep


def lowpass_netlist(phase_degrees=0):
    cutoff_hz = 1000.0
    capacitance_f = 1.0 / (2.0 * math.pi * cutoff_hz * 1000.0)
    return {
        "ground": "0",
        "frequency_sweep": {
            "start_frequency": {"value": 100, "unit": "Hz"},
            "stop_frequency": {"value": 10000, "unit": "Hz"},
            "points": 3,
            "scale": "log",
        },
        "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
             "voltage": {"value": 1, "unit": "V"}, "phase_degrees": phase_degrees},
            {"type": "resistor", "reference": "R1", "nodes": ["VIN", "OUT"],
             "resistance": {"value": 1, "unit": "kohm"}},
            {"type": "capacitor", "reference": "C1", "nodes": ["OUT", "0"],
             "capacitance": {"value": capacitance_f, "unit": "F"}},
        ],
    }


class AcSweepTests(unittest.TestCase):
    def test_first_order_lowpass_matches_complex_reference_at_cutoff(self):
        report = simulate_ac_sweep(lowpass_netlist())
        outputs = report["outputs"]
        self.assertEqual(outputs["status"], "completed-linear-ac-sweep")
        for actual, expected in zip(outputs["frequency_points_hz"], [100.0, 1000.0, 10000.0]):
            self.assertAlmostEqual(actual, expected, places=9)
        self.assertAlmostEqual(outputs["node_voltage_magnitude_traces_v"]["OUT"][1], 1 / math.sqrt(2), places=10)
        self.assertAlmostEqual(outputs["node_voltage_phase_traces_deg"]["OUT"][1], -45.0, places=9)
        self.assertAlmostEqual(outputs["node_voltage_real_traces_v"]["OUT"][1], 0.5, places=10)
        self.assertAlmostEqual(outputs["node_voltage_imag_traces_v"]["OUT"][1], -0.5, places=10)
        self.assertLess(outputs["maximum_normalized_equation_residual"], 1e-12)
        self.assertIn("not an ngspice run", report["warnings"][0])

    def test_ideal_inductor_is_supported_in_native_ac_analysis(self):
        cutoff_hz = 1000.0
        inductance_h = 1000.0 / (2 * math.pi * cutoff_hz)
        netlist = lowpass_netlist()
        netlist["elements"] = [
            {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
             "voltage": {"value": 1, "unit": "V"}, "phase_degrees": 0},
            {"type": "inductor", "reference": "L1", "nodes": ["VIN", "OUT"],
             "inductance": {"value": inductance_h, "unit": "H"}},
            {"type": "resistor", "reference": "R1", "nodes": ["OUT", "0"],
             "resistance": {"value": 1, "unit": "kohm"}},
        ]
        outputs = simulate_ac_sweep(netlist)["outputs"]
        self.assertAlmostEqual(outputs["node_voltage_magnitude_traces_v"]["OUT"][1], 1 / math.sqrt(2), places=10)
        self.assertAlmostEqual(outputs["node_voltage_phase_traces_deg"]["OUT"][1], -45.0, places=9)

    def test_explicit_source_phase_is_preserved_and_current_direction_is_signed(self):
        outputs = simulate_ac_sweep(lowpass_netlist(phase_degrees=30))["outputs"]
        self.assertAlmostEqual(outputs["node_voltage_phase_traces_deg"]["VIN"][1], 30.0, places=9)
        self.assertAlmostEqual(outputs["node_voltage_phase_traces_deg"]["OUT"][1], -15.0, places=9)

        current_netlist = {
            "ground": "0",
            "frequency_sweep": {"start_frequency": {"value": 500, "unit": "Hz"},
                                "stop_frequency": {"value": 1000, "unit": "Hz"}, "points": 2, "scale": "linear"},
            "elements": [
                {"type": "current_source", "reference": "I1", "nodes": ["0", "N1"],
                 "current": {"value": 1, "unit": "mA"}, "phase_degrees": 0},
                {"type": "resistor", "reference": "R1", "nodes": ["N1", "0"],
                 "resistance": {"value": 1, "unit": "kohm"}},
                {"type": "capacitor", "reference": "C1", "nodes": ["N1", "0"],
                 "capacitance": {"value": 1 / (2 * math.pi * 1000 * 1000), "unit": "F"}},
            ],
        }
        current_outputs = simulate_ac_sweep(current_netlist)["outputs"]
        self.assertAlmostEqual(current_outputs["node_voltage_real_traces_v"]["N1"][1], 0.5, places=10)
        self.assertAlmostEqual(current_outputs["node_voltage_imag_traces_v"]["N1"][1], -0.5, places=10)

    def test_malformed_or_unbounded_ac_requests_fail_closed(self):
        base = lowpass_netlist()
        cases = []
        missing_phase = copy.deepcopy(base)
        del missing_phase["elements"][0]["phase_degrees"]
        cases.append(missing_phase)
        negative_magnitude = copy.deepcopy(base)
        negative_magnitude["elements"][0]["voltage"]["value"] = -1
        cases.append(negative_magnitude)
        invalid_phase = copy.deepcopy(base)
        invalid_phase["elements"][0]["phase_degrees"] = 361
        cases.append(invalid_phase)
        invalid_scale = copy.deepcopy(base)
        invalid_scale["frequency_sweep"]["scale"] = []
        cases.append(invalid_scale)
        too_many_points = copy.deepcopy(base)
        too_many_points["frequency_sweep"]["points"] = 201
        cases.append(too_many_points)
        no_ground = copy.deepcopy(base)
        for element in no_ground["elements"]:
            element["nodes"] = [f"X{no_ground['elements'].index(element)}A", f"X{no_ground['elements'].index(element)}B"]
        cases.append(no_ground)
        no_source = copy.deepcopy(base)
        no_source["elements"] = no_source["elements"][1:]
        cases.append(no_source)
        unsupported = copy.deepcopy(base)
        unsupported["elements"].append({"type": "diode", "reference": "D1", "nodes": ["OUT", "0"],
                                        "model": "explicitly unsupported"})
        cases.append(unsupported)
        for netlist in cases:
            with self.subTest(netlist=netlist), self.assertRaises(ValueError):
                simulate_ac_sweep(netlist)


if __name__ == "__main__":
    unittest.main()
