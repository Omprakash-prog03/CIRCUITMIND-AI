import unittest

from circuitmind.connectivity_schedule import build_connectivity_schedule


class ConnectivityScheduleTests(unittest.TestCase):
    def setUp(self):
        pins = [
            {"number": "1", "name": "A", "type": "passive"},
            {"number": "2", "name": "B", "type": "passive"},
            {"number": "3", "name": "NC", "type": "no_connect"},
        ]
        self.model = {
            "schema_version": 2,
            "metadata": {"units": "mm", "grid_mm": 1.27},
            "status": "draft",
            "components": [{"reference": "R1", "value": "unverified resistor label",
                            "symbol": None, "footprint": None, "datasheet": None,
                            "pins": pins, "position_mm": {"x": 0, "y": 0}, "rotation_deg": 0}],
            "nets": [{"name": "INPUT", "terminals": [{"reference": "R1", "pin": "1"}]}],
            "inventory": [],
            "limitations": [],
        }

    def test_schedule_lists_exact_declared_endpoints_and_unconnected_pins(self):
        result = build_connectivity_schedule(self.model, "abc123")
        self.assertEqual(result["circuit_model_sha256"], "abc123")
        self.assertEqual(result["nets"], [{"name": "INPUT", "endpoints": [
            {"reference": "R1", "pin": "1", "pin_name": "A", "pin_type": "passive"}]}])
        self.assertEqual(result["endpoint_count"], 1)
        self.assertEqual(result["unconnected_pins"], [
            {"reference": "R1", "pin": "2", "pin_name": "B", "pin_type": "passive"},
        ])
        self.assertEqual(result["no_connect_pins"], [
            {"reference": "R1", "pin": "3", "pin_name": "NC", "pin_type": "no_connect"},
        ])
        self.assertIn("No physical wire routing", result["scope"])
        self.assertNotIn("unverified resistor label", repr(result))

    def test_invalid_topology_fails_closed(self):
        self.model["nets"][0]["terminals"][0]["pin"] = "missing"
        with self.assertRaisesRegex(ValueError, "schema/topology"):
            build_connectivity_schedule(self.model, "abc123")


if __name__ == "__main__":
    unittest.main()
