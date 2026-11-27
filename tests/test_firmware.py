import unittest

from circuitmind.firmware import generate_esp32_scaffold


class FirmwareScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.model = {"schema_version": 2, "nets": [{"name": "DISPLAY_DATA"}, {"name": "BUTTON"}]}
        self.request = {"target": "esp32dev", "pins": [
            {"signal": "display_data", "gpio": 23, "direction": "output", "net": "DISPLAY_DATA"},
            {"signal": "set_button", "gpio": 34, "direction": "input", "net": "BUTTON"},
        ]}

    def test_generates_hashed_scaffold_from_explicit_gpio_net_map(self):
        result = generate_esp32_scaffold(self.request, self.model)
        source = result["files"]["src/main.cpp"].decode()
        platformio = result["files"]["platformio.ini"].decode()
        self.assertIn("GPIO_DISPLAY_DATA = 23", source)
        self.assertIn("pinMode(GPIO_SET_BUTTON, INPUT)", source)
        self.assertIn("board = esp32dev", platformio)
        self.assertEqual(result["manifest"]["compile_status"], "not-run")
        self.assertEqual(len(result["sha256"]["src/main.cpp"]), 64)
        self.assertEqual(next(item for item in result["manifest"]["pin_mappings"] if item["signal"] == "set_button")["net"], "BUTTON")
        self.assertIn("GPIO34", " ".join(result["manifest"]["limitations"]))

    def test_invalid_or_unmapped_gpio_assignments_are_rejected(self):
        cases = [
            {**self.request, "target": "unknown-board"},
            {**self.request, "pins": [{**self.request["pins"][0], "gpio": 34}]},
            {**self.request, "pins": [self.request["pins"][0],
                                        {**self.request["pins"][1], "gpio": 23}]},
            {**self.request, "pins": [{**self.request["pins"][0], "net": "NO_NET"}]},
            {**self.request, "pins": [{**self.request["pins"][0], "signal": "bad-name"}]},
        ]
        for request in cases:
            with self.subTest(request=request), self.assertRaises(ValueError):
                generate_esp32_scaffold(request, self.model)

    def test_strapping_pin_and_uart_collisions_are_warned_not_silenced(self):
        request = {"target": "esp32dev", "pins": [
            {"signal": "boot_mode", "gpio": 0, "direction": "input", "net": "BUTTON"},
            {"signal": "serial_tx", "gpio": 1, "direction": "output", "net": "DISPLAY_DATA"},
        ]}
        warnings = " ".join(generate_esp32_scaffold(request, self.model)["manifest"]["limitations"])
        self.assertIn("boot-strapping", warnings)
        self.assertIn("UART0", warnings)


if __name__ == "__main__":
    unittest.main()
