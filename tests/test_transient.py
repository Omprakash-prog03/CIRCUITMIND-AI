import math
import unittest

from tests.native_core import ensure_native_core
ensure_native_core()

from circuitmind.engineering import simulate_transient_rc


def step_rc_netlist(step_time=0.0, stop_time=0.001, dt=0.0001):
    return {
        "ground": "0", "time_step": {"value": dt, "unit": "s"}, "stop_time": {"value": stop_time, "unit": "s"},
        "method": "backward_euler",
        "elements": [
            {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
             "waveform": {"type": "step", "initial": {"value": 0, "unit": "V"},
                          "final": {"value": 5, "unit": "V"}, "step_time": {"value": step_time, "unit": "s"}}},
            {"type": "resistor", "reference": "R1", "nodes": ["VIN", "OUT"], "resistance": {"value": 1, "unit": "kohm"}},
            {"type": "capacitor", "reference": "C1", "nodes": ["OUT", "0"], "capacitance": {"value": 1, "unit": "uF"},
             "initial_voltage": {"value": 0, "unit": "V"}},
        ],
    }


class TransientRcTests(unittest.TestCase):
    def test_backward_euler_rc_step_matches_recurrence_and_reports_scope(self):
        report = simulate_transient_rc(step_rc_netlist())
        output = report["outputs"]
        self.assertEqual(output["status"], "completed-numerical-linear-rc-transient")
        self.assertEqual(output["method"], "backward_euler")
        self.assertEqual(output["sample_count"], 11)
        expected = 5 * (1 - (1 + .1) ** -10)
        self.assertAlmostEqual(output["node_voltage_traces_v"]["OUT"][-1], expected)
        self.assertAlmostEqual(output["capacitor_voltage_traces_v"]["C1"][-1], expected)
        self.assertEqual(output["node_voltage_traces_v"]["OUT"][0], 0)
        self.assertEqual(output["node_voltage_traces_v"]["VIN"][0], 0)
        self.assertIn("time-domain simulation", report["scope"])
        self.assertIn("timestep adequacy", report["warnings"][0])

    def test_delayed_step_and_nonzero_initial_condition(self):
        report = simulate_transient_rc(step_rc_netlist(step_time=.0002))
        trace = report["outputs"]["node_voltage_traces_v"]["OUT"]
        self.assertEqual(trace[0], 0)
        self.assertEqual(trace[1], 0)
        self.assertAlmostEqual(trace[2], 5 / 11)
        discharge = {"ground": "0", "time_step": {"value": .0001, "unit": "s"}, "stop_time": {"value": .0002, "unit": "s"},
                     "elements": [
                         {"type": "resistor", "reference": "R1", "nodes": ["N1", "0"], "resistance": {"value": 1, "unit": "kohm"}},
                         {"type": "capacitor", "reference": "C1", "nodes": ["N1", "0"], "capacitance": {"value": 1, "unit": "uF"},
                          "initial_voltage": {"value": 1, "unit": "V"}},
                     ]}
        result = simulate_transient_rc(discharge)["outputs"]["capacitor_voltage_traces_v"]["C1"]
        self.assertAlmostEqual(result[1], 1 / 1.1)
        self.assertAlmostEqual(result[2], 1 / (1.1**2))

    def test_current_source_step_is_integrated_with_signed_direction(self):
        netlist = {"ground": "0", "time_step": {"value": .001, "unit": "s"}, "stop_time": {"value": .002, "unit": "s"},
                   "elements": [
                       {"type": "current_source", "reference": "I1", "nodes": ["0", "N1"],
                        "waveform": {"type": "step", "initial": {"value": 0, "unit": "A"},
                                     "final": {"value": 1, "unit": "mA"}, "step_time": {"value": 0, "unit": "s"}}},
                       {"type": "capacitor", "reference": "C1", "nodes": ["N1", "0"], "capacitance": {"value": 1, "unit": "uF"},
                        "initial_voltage": {"value": 0, "unit": "V"}},
                   ]}
        output = simulate_transient_rc(netlist)["outputs"]
        self.assertAlmostEqual(output["node_voltage_traces_v"]["N1"][1], 1)
        self.assertAlmostEqual(output["node_voltage_traces_v"]["N1"][2], 2)

    def test_invalid_time_grid_models_waveforms_and_inconsistent_initial_conditions_fail(self):
        base = step_rc_netlist()
        cases = []
        cases.append({**base, "time_step": {"value": 0, "unit": "s"}})
        cases.append({**base, "stop_time": {"value": .00105, "unit": "s"}})
        cases.append({**base, "method": "trapezoidal"})
        cases.append({**base, "elements": [item for item in base["elements"] if item["type"] != "capacitor"]})
        duplicate_references = step_rc_netlist()
        duplicate_references["elements"][2]["reference"] = "R1"
        cases.append(duplicate_references)
        cases.append({**base, "time_step": {"value": 1e-320, "unit": "s"}, "stop_time": {"value": 1, "unit": "s"}})
        cases.append({**base, "elements": base["elements"] + [{"type": "inductor", "reference": "L1", "nodes": ["OUT", "0"], "inductance": {"value": 1, "unit": "mH"}}]})
        bad_waveform = step_rc_netlist()
        bad_waveform["elements"][0]["waveform"]["step_time"] = {"value": 2, "unit": "s"}
        cases.append(bad_waveform)
        inconsistent = {"ground": "0", "time_step": {"value": .001, "unit": "s"}, "stop_time": {"value": .001, "unit": "s"},
                        "elements": [
                            {"type": "voltage_source", "reference": "V1", "nodes": ["N1", "0"], "voltage": {"value": 5, "unit": "V"}},
                            {"type": "capacitor", "reference": "C1", "nodes": ["N1", "0"], "capacitance": {"value": 1, "unit": "uF"},
                             "initial_voltage": {"value": 0, "unit": "V"}},
                        ]}
        cases.append(inconsistent)
        for netlist in cases:
            with self.subTest(netlist=netlist), self.assertRaises(ValueError):
                simulate_transient_rc(netlist)

    def test_step_count_is_bounded(self):
        netlist = step_rc_netlist(stop_time=1.0, dt=.0001)
        with self.assertRaisesRegex(ValueError, "2000 steps"):
            simulate_transient_rc(netlist)


if __name__ == "__main__":
    unittest.main()
