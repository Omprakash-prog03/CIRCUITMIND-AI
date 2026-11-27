import unittest

from tests.native_core import ensure_native_core
ensure_native_core()

from circuitmind.engineering import simulate_digital_truth_table, simulate_digital_waveform


class DigitalTruthTableTests(unittest.TestCase):
    def test_native_engine_evaluates_gate_chain_for_all_input_combinations(self):
        netlist = {"inputs": ["A", "B"], "outputs": ["SUM", "CARRY"], "gates": [
            {"type": "and", "inputs": ["NAND", "A"], "output": "CARRY"},
            {"type": "nand", "inputs": ["A", "B"], "output": "NAND"},
            {"type": "xor", "inputs": ["A", "B"], "output": "SUM"},
        ]}
        report = simulate_digital_truth_table(netlist)
        outputs = report["outputs"]
        self.assertEqual(outputs["status"], "completed-combinational-digital-truth-table")
        self.assertEqual(outputs["solver_engine"], "circuitmind-cpp-core")
        self.assertEqual(outputs["row_count"], 4)
        self.assertEqual([row["inputs"] for row in outputs["truth_table"]], [
            {"A": 0, "B": 0}, {"A": 0, "B": 1}, {"A": 1, "B": 0}, {"A": 1, "B": 1}])
        self.assertEqual([row["outputs"] for row in outputs["truth_table"]], [
            {"SUM": 0, "CARRY": 0}, {"SUM": 1, "CARRY": 0},
            {"SUM": 1, "CARRY": 1}, {"SUM": 0, "CARRY": 0}])
        self.assertIn("sequential state", " ".join(report["validity"]))

    def test_native_engine_rejects_cycles_undriven_nets_and_bad_gate_shapes(self):
        cases = [
            {"inputs": ["A"], "outputs": ["Y"], "gates": [
                {"type": "buffer", "inputs": ["Z"], "output": "Y"},
                {"type": "not", "inputs": ["Y"], "output": "Z"}]},
            {"inputs": ["A"], "outputs": ["Y"], "gates": [
                {"type": "and", "inputs": ["A"], "output": "Y"}]},
            {"inputs": ["A", "A"], "outputs": ["Y"], "gates": [
                {"type": "not", "inputs": ["A"], "output": "Y"}]},
            {"inputs": ["A"], "outputs": ["Y"], "gates": [
                {"type": "not", "inputs": ["A"], "output": "A"}]},
        ]
        for netlist in cases:
            with self.subTest(netlist=netlist), self.assertRaises(ValueError):
                simulate_digital_truth_table(netlist)

    def test_truth_table_size_limits_are_bounded(self):
        netlist = {"inputs": [f"I{i}" for i in range(11)], "outputs": ["Y"],
                   "gates": [{"type": "buffer", "inputs": ["I0"], "output": "Y"}]}
        with self.assertRaisesRegex(ValueError, "1 and 10"):
            simulate_digital_truth_table(netlist)


class DigitalWaveformTests(unittest.TestCase):
    def test_native_engine_captures_on_sampled_rising_edges_and_holds_state(self):
        netlist = {
            "inputs": ["D", "CLK"], "outputs": ["Q", "NQ"],
            "gates": [
                {"type": "dff", "inputs": ["D", "CLK"], "output": "Q", "initial_value": 0},
                {"type": "not", "inputs": ["Q"], "output": "NQ"},
            ],
            "samples": [
                {"D": 0, "CLK": 0}, {"D": 1, "CLK": 0},
                {"D": 1, "CLK": 1}, {"D": 0, "CLK": 1},
                {"D": 0, "CLK": 0}, {"D": 0, "CLK": 1},
            ],
            "sample_interval_s": 0.25,
        }
        report = simulate_digital_waveform(netlist)
        outputs = report["outputs"]
        self.assertEqual(outputs["status"], "completed-sampled-digital-waveform")
        self.assertEqual([frame["outputs"] for frame in outputs["waveform"]], [
            {"Q": 0, "NQ": 1}, {"Q": 0, "NQ": 1}, {"Q": 1, "NQ": 0},
            {"Q": 1, "NQ": 0}, {"Q": 1, "NQ": 0}, {"Q": 0, "NQ": 1},
        ])
        self.assertEqual([frame["time_s"] for frame in outputs["waveform"]], [0, 0.25, 0.5, 0.75, 1.0, 1.25])

    def test_composed_d_flip_flops_wrap_a_two_bit_counter(self):
        netlist = {
            "inputs": ["CLK"], "outputs": ["Q0", "Q1"],
            "gates": [
                {"type": "dff", "inputs": ["D0", "CLK"], "output": "Q0", "initial_value": 0},
                {"type": "dff", "inputs": ["D1", "CLK"], "output": "Q1", "initial_value": 0},
                {"type": "not", "inputs": ["Q0"], "output": "D0"},
                {"type": "xor", "inputs": ["Q1", "Q0"], "output": "D1"},
            ],
            "samples": [{"CLK": level} for level in [0, 1, 0, 1, 0, 1, 0, 1]],
            "sample_interval_s": 0.5,
        }
        report = simulate_digital_waveform(netlist)
        states = [(frame["outputs"]["Q1"], frame["outputs"]["Q0"])
                  for frame in report["outputs"]["waveform"]]
        self.assertEqual(states, [(0, 0), (0, 1), (0, 1), (1, 0),
                                  (1, 0), (1, 1), (1, 1), (0, 0)])

    def test_native_engine_supports_falling_edge_d_flip_flops(self):
        netlist = {
            "inputs": ["D", "CLK"], "outputs": ["Q"],
            "gates": [{"type": "dff", "inputs": ["D", "CLK"], "output": "Q",
                       "initial_value": 0, "edge": "falling"}],
            "samples": [
                {"D": 0, "CLK": 1}, {"D": 1, "CLK": 0},
                {"D": 0, "CLK": 0}, {"D": 0, "CLK": 1},
                {"D": 0, "CLK": 0},
            ],
            "sample_interval_s": 1.0,
        }
        report = simulate_digital_waveform(netlist)
        self.assertEqual([frame["outputs"]["Q"] for frame in report["outputs"]["waveform"]],
                         [0, 1, 1, 1, 0])

    def test_waveform_rejects_invalid_samples_and_netlists(self):
        base = {"inputs": ["D", "CLK"], "outputs": ["Q"],
                "gates": [{"type": "dff", "inputs": ["D", "CLK"], "output": "Q", "initial_value": 0}],
                "samples": [{"D": 0, "CLK": 0}], "sample_interval_s": 1.0}
        cases = []
        bad_value = {**base, "samples": [{"D": True, "CLK": 0}]}
        cases.append(bad_value)
        bad_interval = {**base, "sample_interval_s": float("nan")}
        cases.append(bad_interval)
        oversized_interval = {**base, "sample_interval_s": 10 ** 1000}
        cases.append(oversized_interval)
        missing_initial = {**base, "gates": [{"type": "dff", "inputs": ["D", "CLK"], "output": "Q"}]}
        cases.append(missing_initial)
        bad_edge = {**base, "gates": [{"type": "dff", "inputs": ["D", "CLK"], "output": "Q",
                                       "initial_value": 0, "edge": "both"}]}
        cases.append(bad_edge)
        bad_shape = {**base, "samples": [{"D": 0}]}
        cases.append(bad_shape)
        unexpected_initial = {**base, "gates": [{"type": "buffer", "inputs": ["D"],
                                                  "output": "Q", "initial_value": 0}]}
        cases.append(unexpected_initial)
        for netlist in cases:
            with self.subTest(netlist=netlist), self.assertRaises(ValueError):
                simulate_digital_waveform(netlist)


if __name__ == "__main__":
    unittest.main()
