import math
import unittest

from tests.native_core import ensure_native_core
ensure_native_core()

from circuitmind.engineering.cpp_engine import engine_version, simulate_transient_rc


class CppEngineBridgeTests(unittest.TestCase):
    def test_native_abi_runs_real_rc_step_and_matches_backward_euler_reference(self):
        netlist = {
            "ground": "0",
            "time_step": {"value": 100, "unit": "us"},
            "stop_time": {"value": 1, "unit": "ms"},
            "method": "backward_euler",
            "elements": [
                {"type": "voltage_source", "reference": "V1", "nodes": ["VIN", "0"],
                 "waveform": {"type": "step", "initial": {"value": 0, "unit": "V"},
                              "final": {"value": 5, "unit": "V"}, "step_time": {"value": 0, "unit": "s"}}},
                {"type": "resistor", "reference": "R1", "nodes": ["VIN", "OUT"], "resistance": {"value": 1, "unit": "kohm"}},
                {"type": "capacitor", "reference": "C1", "nodes": ["OUT", "0"], "capacitance": {"value": 1, "unit": "uF"},
                 "initial_voltage": {"value": 0, "unit": "V"}},
            ],
        }
        result = simulate_transient_rc(netlist)
        self.assertEqual(result["engine"], "circuitmind-cpp-core")
        self.assertEqual(result["engine_version"], engine_version())
        self.assertEqual(result["sample_count"], 11)
        self.assertEqual(result["node_voltage_traces_v"]["VIN"][0], 0.0)
        self.assertAlmostEqual(result["node_voltage_traces_v"]["OUT"][-1], 5 * (1 - 1.1**-10))
        self.assertLess(result["maximum_equation_residual"], 1e-9)
        self.assertLess(abs(result["node_voltage_traces_v"]["OUT"][-1] - 5 * (1 - math.exp(-1))), 0.1)

    def test_native_bridge_rejects_unsupported_waveforms_and_time_grid(self):
        base = {"ground": "0", "time_step": {"value": .001, "unit": "s"}, "stop_time": {"value": .0021, "unit": "s"},
                "elements": [{"type": "capacitor", "reference": "C1", "nodes": ["N1", "0"],
                              "capacitance": {"value": 1, "unit": "uF"}, "initial_voltage": {"value": 0, "unit": "V"}}]}
        with self.assertRaisesRegex(ValueError, "integer multiple"):
            simulate_transient_rc(base)


if __name__ == "__main__":
    unittest.main()
