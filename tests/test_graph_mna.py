import copy
import unittest

from circuitmind.circuit_schema import new_circuit_model
from circuitmind.engineering import map_circuit_graph_to_mna, solve_dc_operating_point


def clock_model():
    model = new_circuit_model()
    pins = [{"number": "1", "name": "A", "type": "passive"}, {"number": "2", "name": "B", "type": "passive"}]
    model["components"] = [
        {"reference": "V1", "value": "unspecified voltage source", "symbol": None, "footprint": None, "datasheet": None,
         "pins": copy.deepcopy(pins), "position_mm": {"x": 10, "y": 10}, "rotation_deg": 0},
        {"reference": "R1", "value": "10k (description is not parsed)", "symbol": None, "footprint": None, "datasheet": None,
         "pins": copy.deepcopy(pins), "position_mm": {"x": 20, "y": 10}, "rotation_deg": 0},
        {"reference": "R2", "value": "10k (description is not parsed)", "symbol": None, "footprint": None, "datasheet": None,
         "pins": copy.deepcopy(pins), "position_mm": {"x": 30, "y": 10}, "rotation_deg": 0},
    ]
    model["nets"] = [
        {"name": "VIN", "terminals": [{"reference": "V1", "pin": "1"}, {"reference": "R1", "pin": "1"}]},
        {"name": "MID", "terminals": [{"reference": "R1", "pin": "2"}, {"reference": "R2", "pin": "1"}]},
        {"name": "GND", "terminals": [{"reference": "V1", "pin": "2"}, {"reference": "R2", "pin": "2"}]},
    ]
    return model


def mappings():
    return {"ground_net": "GND", "bindings": [
        {"reference": "V1", "type": "voltage_source", "pins": ["1", "2"], "value": {"value": 5, "unit": "V"}},
        {"reference": "R1", "type": "resistor", "pins": ["1", "2"], "value": {"value": 1, "unit": "kohm"}},
        {"reference": "R2", "type": "resistor", "pins": ["1", "2"], "value": {"value": 1, "unit": "kohm"}},
    ], "exclusions": []}


class GraphToMnaMappingTests(unittest.TestCase):
    def test_explicit_mapping_generates_and_solves_netlist_from_saved_connectivity(self):
        resolved = map_circuit_graph_to_mna(clock_model(), mappings())
        self.assertEqual(resolved["net_name_to_solver_node"]["GND"], "0")
        self.assertEqual(resolved["component_coverage"], {"total": 3, "mapped": 3, "excluded": 0,
                                                            "all_components_accounted": True, "has_exclusions": False})
        voltage_binding = next(item for item in resolved["bindings"] if item["reference"] == "V1")
        self.assertEqual(voltage_binding["nodes"], ["N2", "0"])
        report = solve_dc_operating_point(resolved["netlist"])
        output = report["outputs"]
        node_map = resolved["net_name_to_solver_node"]
        self.assertAlmostEqual(output["node_voltages"][node_map["VIN"]]["value"], 5)
        self.assertAlmostEqual(output["node_voltages"][node_map["MID"]]["value"], 2.5)
        self.assertAlmostEqual(output["total_power_balance"]["value"], 0, places=12)

    def test_never_parses_component_descriptions_and_pin_order_is_explicit(self):
        reversed_mapping = mappings()
        reversed_mapping["bindings"][0]["pins"] = ["2", "1"]
        resolved = map_circuit_graph_to_mna(clock_model(), reversed_mapping)
        report = solve_dc_operating_point(resolved["netlist"])
        node = resolved["net_name_to_solver_node"]["VIN"]
        self.assertAlmostEqual(report["outputs"]["node_voltages"][node]["value"], -5)
        self.assertIn("explicit user inputs", " ".join(resolved["limitations"]))

    def test_all_components_must_be_mapped_or_excluded_with_reason(self):
        model = clock_model()
        model["components"].append({"reference": "U1", "value": "unresolved three-pin device", "symbol": None,
                                    "footprint": None, "datasheet": None,
                                    "pins": [{"number": str(index), "name": f"P{index}", "type": "unspecified"} for index in range(1, 4)],
                                    "position_mm": {"x": 40, "y": 10}, "rotation_deg": 0})
        with self.assertRaisesRegex(ValueError, "unaccounted: U1"):
            map_circuit_graph_to_mna(model, mappings())
        request = mappings()
        request["exclusions"] = [{"reference": "U1", "reason": "No verified linear DC model supplied"}]
        resolved = map_circuit_graph_to_mna(model, request)
        self.assertEqual(resolved["component_coverage"]["excluded"], 1)
        self.assertTrue(resolved["component_coverage"]["has_exclusions"])

    def test_bad_ground_binding_pin_list_duplicate_and_value_units_rejected(self):
        bad_cases = []
        bad = mappings(); bad["ground_net"] = "NO_SUCH_NET"; bad_cases.append(bad)
        bad = mappings(); bad["bindings"][0]["pins"] = ["1", "99"]; bad_cases.append(bad)
        bad = mappings(); bad["bindings"][1]["pins"] = ["1", "1"]; bad_cases.append(bad)
        bad = mappings(); bad["bindings"][1]["type"] = "voltage_source"; bad_cases.append(bad)
        bad = mappings(); bad["bindings"][0]["value"] = {"value": 5, "unit": "mA"}; bad_cases.append(bad)
        bad = mappings(); bad["bindings"].append(copy.deepcopy(bad["bindings"][0])); bad_cases.append(bad)
        bad = mappings(); bad["exclusions"] = [{"reference": "R1", "reason": "duplicate"}]; bad_cases.append(bad)
        for mapping in bad_cases:
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                resolved = map_circuit_graph_to_mna(clock_model(), mapping)
                solve_dc_operating_point(resolved["netlist"])

    def test_unconnected_pin_must_be_explicitly_excluded_not_silently_dropped(self):
        model = clock_model()
        model["nets"][0]["terminals"].remove({"reference": "R1", "pin": "1"})
        with self.assertRaisesRegex(ValueError, r"R1\.1 is not connected"):
            map_circuit_graph_to_mna(model, mappings())


if __name__ == "__main__":
    unittest.main()
