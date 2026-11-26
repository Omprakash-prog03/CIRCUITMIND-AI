import math
import unittest

from circuitmind.engineering import (
    capacitor_energy,
    capacitor_network,
    inductor_energy,
    clock_timebase_error,
    led_series_resistor,
    ohms_law,
    rc_time_constant,
    resistor_network,
)
from circuitmind.engineering.resistors import next_e_series_value


class ElectricalFundamentalTests(unittest.TestCase):
    def test_ohms_law_normalizes_units_and_keeps_substitution(self):
        result = ohms_law({"value": 5, "unit": "V"}, {"value": 1, "unit": "kohm"})
        self.assertAlmostEqual(result["outputs"]["current"]["value"], .005)
        self.assertAlmostEqual(result["outputs"]["power"]["value"], .025)
        self.assertEqual(result["inputs"][1]["si_value"], 1000)
        self.assertIn("I = 5 V / 1000 ohm", result["substitution"])
        self.assertIn("not a simulation", result["scope"])
        reversed_polarity = ohms_law({"value": -5, "unit": "V"}, {"value": 1, "unit": "kohm"})
        self.assertAlmostEqual(reversed_polarity["outputs"]["current"]["value"], -.005)
        self.assertAlmostEqual(reversed_polarity["outputs"]["power"]["value"], .025)

    def test_series_and_parallel_resistance(self):
        values = [{"value": 100, "unit": "ohm"}, {"value": 0.2, "unit": "kohm"}]
        self.assertAlmostEqual(resistor_network("series", values)["outputs"]["equivalent_resistance"]["value"], 300)
        self.assertAlmostEqual(resistor_network("parallel", values)["outputs"]["equivalent_resistance"]["value"], 66.6666667)

    def test_capacitor_networks_rc_constant_and_energy(self):
        caps = [{"value": 10, "unit": "uF"}, {"value": 10, "unit": "uF"}]
        self.assertAlmostEqual(capacitor_network("parallel", caps)["outputs"]["equivalent_capacitance"]["value"], 20e-6)
        self.assertAlmostEqual(capacitor_network("series", caps)["outputs"]["equivalent_capacitance"]["value"], 5e-6)
        tau = rc_time_constant({"value": 1, "unit": "kohm"}, {"value": 100, "unit": "uF"})
        self.assertAlmostEqual(tau["outputs"]["time_constant"]["value"], .1)
        self.assertAlmostEqual(tau["outputs"]["one_percent_settling_approx"]["value"], .460517)
        self.assertAlmostEqual(capacitor_energy({"value": 100, "unit": "uF"}, {"value": 5, "unit": "V"})["outputs"]["energy"]["value"], .00125)
        self.assertAlmostEqual(capacitor_energy({"value": 100, "unit": "uF"}, {"value": -5, "unit": "V"})["outputs"]["energy"]["value"], .00125)

    def test_inductor_energy(self):
        result = inductor_energy({"value": 10, "unit": "mH"}, {"value": 2, "unit": "A"})
        self.assertAlmostEqual(result["outputs"]["energy"]["value"], .02)
        reverse_current = inductor_energy({"value": 10, "unit": "mH"}, {"value": -2, "unit": "A"})
        self.assertAlmostEqual(reverse_current["outputs"]["energy"]["value"], .02)

    def test_invalid_dimensions_nonfinite_and_nonpositive_values_fail_closed(self):
        cases = [
            lambda: ohms_law({"value": 5, "unit": "A"}, {"value": 100, "unit": "ohm"}),
            lambda: ohms_law({"value": float("nan"), "unit": "V"}, {"value": 100, "unit": "ohm"}),
            lambda: resistor_network("parallel", [{"value": 0, "unit": "ohm"}, {"value": 1, "unit": "ohm"}]),
            lambda: capacitor_network("unknown", [{"value": 1, "unit": "F"}, {"value": 1, "unit": "F"}]),
            lambda: rc_time_constant({"value": 1, "unit": "ohm"}, "not a quantity"),
        ]
        for calculate in cases:
            with self.subTest(calculate=calculate), self.assertRaises(ValueError):
                calculate()


class ClockTimebaseTests(unittest.TestCase):
    def test_symmetric_tolerance_becomes_signed_daily_and_long_term_drift(self):
        result = clock_timebase_error({"value": 1, "unit": "Hz"}, {"value": 1, "unit": "Hz"}, 20)
        output = result["outputs"]
        self.assertAlmostEqual(output["nominal_rate_error"]["value"], 0)
        self.assertAlmostEqual(output["minimum_clock_drift_per_day"]["value"], -1.728)
        self.assertAlmostEqual(output["maximum_clock_drift_per_day"]["value"], 1.728)
        self.assertAlmostEqual(output["maximum_clock_drift_per_30_days"]["value"], 51.84)
        self.assertAlmostEqual(output["maximum_clock_drift_per_365_25_days"]["value"], 631.152)
        self.assertIn("post-divider", " ".join(result["validity"]))
        self.assertIn("digital simulation", result["warnings"][0])

    def test_nominal_rate_offset_and_units_are_preserved(self):
        result = clock_timebase_error({"value": 1.00001, "unit": "Hz"}, {"value": 1, "unit": "Hz"}, 20)
        output = result["outputs"]
        self.assertAlmostEqual(output["nominal_rate_error"]["value"], 10)
        self.assertAlmostEqual(output["minimum_rate_error"]["value"], -10.0002, places=3)
        self.assertAlmostEqual(output["maximum_rate_error"]["value"], 30.0002, places=3)
        self.assertEqual(result["inputs"][0]["unit"], "Hz")
        kHz = clock_timebase_error({"value": 1, "unit": "kHz"}, {"value": 1000, "unit": "Hz"}, 0)
        self.assertAlmostEqual(kHz["outputs"]["nominal_rate_error"]["value"], 0)

    def test_invalid_rate_tolerance_and_overflow_fail_closed(self):
        invalid = [
            lambda: clock_timebase_error({"value": 0, "unit": "Hz"}, {"value": 1, "unit": "Hz"}, 10),
            lambda: clock_timebase_error({"value": 1, "unit": "A"}, {"value": 1, "unit": "Hz"}, 10),
            lambda: clock_timebase_error({"value": 1, "unit": "Hz"}, {"value": 1, "unit": "Hz"}, -1),
            lambda: clock_timebase_error({"value": 1, "unit": "Hz"}, {"value": 1, "unit": "Hz"}, 1_000_000),
            lambda: clock_timebase_error({"value": 1, "unit": "Hz"}, {"value": 1, "unit": "Hz"}, True),
            lambda: clock_timebase_error({"value": 1e308, "unit": "Hz"}, {"value": 1e-308, "unit": "Hz"}, 0),
        ]
        for calculate in invalid:
            with self.subTest(calculate=calculate), self.assertRaises(ValueError):
                calculate()


class ResistorDesignTests(unittest.TestCase):
    INPUTS = {
        "supply": {"value": 5, "unit": "V"},
        "supply_range": {"min": {"value": 4.75, "unit": "V"}, "max": {"value": 5.25, "unit": "V"}},
        "led_forward": {"value": 2, "unit": "V"},
        "led_forward_range": {"min": {"value": 1.8, "unit": "V"}, "max": {"value": 2.2, "unit": "V"}},
        "target_current": {"value": 10, "unit": "mA"},
        "maximum_current": {"value": 20, "unit": "mA"},
        "resistor_tolerance_percent": 5,
        "e_series": 24,
        "resistor_power_rating": {"value": .25, "unit": "W"},
        "power_derating_fraction": .5,
    }

    def test_e_series_rounding_and_worst_case_current_power(self):
        self.assertEqual(next_e_series_value(300, 24), 300)
        self.assertEqual(next_e_series_value(301, 24), 330)
        result = led_series_resistor(self.INPUTS)
        outputs = result["outputs"]
        self.assertEqual(outputs["selected_resistance"]["value"], 300)
        self.assertAlmostEqual(outputs["nominal_current"]["value"], .01)
        self.assertAlmostEqual(outputs["worst_case_maximum_current"]["value"], 3.45 / 285)
        self.assertAlmostEqual(outputs["resistor_maximum_power"]["value"], 3.45**2 / 285)
        self.assertEqual(outputs["compliance"], {"maximum_current": "pass", "resistor_power": "pass"})
        self.assertTrue(result["formula"])
        self.assertTrue(result["inputs"])
        self.assertTrue(result["assumptions"])
        self.assertTrue(result["validity"])

    def test_limit_failure_and_missing_limits_are_not_called_pass(self):
        unsafe = dict(self.INPUTS, maximum_current={"value": 5, "unit": "mA"},
                      resistor_power_rating={"value": .05, "unit": "W"})
        result = led_series_resistor(unsafe)
        self.assertEqual(result["outputs"]["compliance"], {"maximum_current": "fail", "resistor_power": "fail"})
        no_ratings = {key: value for key, value in self.INPUTS.items() if key not in {"maximum_current", "resistor_power_rating"}}
        result = led_series_resistor(no_ratings)
        self.assertEqual(result["outputs"]["compliance"], {"maximum_current": "not-assessed", "resistor_power": "not-assessed"})

    def test_corner_ranges_tolerance_and_invalid_specs_are_checked(self):
        invalid = dict(self.INPUTS, supply_range={"min": {"value": 5.1, "unit": "V"}, "max": {"value": 5.2, "unit": "V"}})
        with self.assertRaisesRegex(ValueError, "nominal value"):
            led_series_resistor(invalid)
        invalid = dict(self.INPUTS, resistor_tolerance_percent=100)
        with self.assertRaisesRegex(ValueError, "tolerance"):
            led_series_resistor(invalid)
        invalid = dict(self.INPUTS, e_series=48)
        with self.assertRaisesRegex(ValueError, "E6, E12, and E24"):
            led_series_resistor(invalid)
        invalid = dict(self.INPUTS, target_current={"value": 0, "unit": "mA"})
        with self.assertRaises(ValueError):
            led_series_resistor(invalid)


if __name__ == "__main__":
    unittest.main()
