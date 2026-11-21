"""Gateway answer parsing (no network, no HA)."""
from __future__ import annotations

import unittest

from test_api_login import api

GRID = [
    {"paramKey": "Phase A Voltage", "paramValue": "231.40 V"},
    {"paramKey": "Phase B Voltage", "paramValue": "0.00 V"},
    {"paramKey": "Phase C Voltage", "paramValue": "--"},
    {"paramKey": "Phase A Current", "paramValue": "-3.21 A"},
    {"paramKey": " phase b current ", "paramValue": "1.5A"},
    {"paramKey": "Voltage Frequency", "paramValue": "50.01 Hz"},
    {"paramKey": "Total Active Power", "paramValue": "1.234 kW"},
    {"paramKey": "Grid Side Contactor Status", "paramValue": "Close"},
    {"paramKey": "Maximum Phase A voltage in the past minute", "paramValue": "236.10 V"},
    {"paramKey": "Minimum Phase A voltage in the past minute", "paramValue": "229.90 V"},
    "not a row",
]
ANSWER = {
    "gridSideInfoList": GRID,
    "newGridSideInfoList": [
        {"paramKey": "Voltage Frequency", "paramValue": "50.01 Hz"},
        {"paramKey": "Voltage Phase Sequence", "paramValue": " Positive Sequence "},
    ],
    "generatorRealTimeInfoVOList": [
        {"key": "voltage", "phaseA": "0.00 V", "phaseB": "0.00 V", "phaseC": "0.00 V"},
        {"key": "current", "phaseA": "0.00 A", "phaseB": "1.20 A", "phaseC": None},
        {"key": "Active Power", "phaseA": "1.250 kW", "phaseB": "-0.400 kW", "phaseC": "x"},
        {"key": "Reactive Power", "phaseA": "0.100 kvar"},
    ],
    "generatorSideInfoList": [
        {"paramKey": "Phase A Voltage", "paramValue": "0.00 V"},
        {"paramKey": "Voltage Frequency", "paramValue": "0.00 Hz"},
        {"paramKey": "Active Power", "paramValue": "0.000 kW"},
        {"paramKey": "Generator Side Contactor Status", "paramValue": "Open"},
    ],
    "softVersion": "V100R001C00",
    "deviceModel": "Sigen Gateway HomeMax TP",
    "snCode": "SECRET123",
}


class TestGateway(unittest.TestCase):
    def test_reads_grid_side_and_keeps_zero_apart_from_missing(self):
        gateway = api.parse_gateway(ANSWER)
        for key, value in {
            "voltage_a": 231.4, "voltage_b": 0.0, "current_a": -3.21, "current_b": 1.5,
            "voltage_max_a": 236.1, "voltage_min_a": 229.9,
        }.items():
            self.assertEqual(gateway.values[key], value, key)
        # A placeholder is no reading; a real zero is.
        self.assertNotIn("voltage_c", gateway.values)

    def test_values_the_modbus_integration_already_has_are_left_out(self):
        gateway = api.parse_gateway(ANSWER)
        self.assertFalse({"frequency", "power", "contactor"} & set(gateway.values))
        self.assertNotIn("contactor", gateway.texts)

    def test_reads_generator_and_texts(self):
        gateway = api.parse_gateway(ANSWER)
        for key, value in {
            "generator_voltage_a": 0.0, "generator_voltage_c": 0.0,
            "generator_current_b": 1.2, "generator_power": 0.0, "generator_frequency": 0.0,
        }.items():
            self.assertEqual(gateway.values[key], value, key)
        self.assertNotIn("generator_current_c", gateway.values)
        # Power per phase, from the same rows as voltage and current.
        self.assertEqual(
            (gateway.values["generator_power_a"], gateway.values["generator_power_b"]),
            (1.25, -0.4),
        )
        self.assertNotIn("generator_power_c", gateway.values)
        self.assertEqual(gateway.texts, {
            "phase_sequence": "Positive Sequence", "generator_contactor": "open",
            "firmware": "V100R001C00", "model": "Sigen Gateway HomeMax TP",
        })
        # Serial numbers never become readings.
        self.assertNotIn("SECRET123", str(gateway))

    def test_unexpected_units_and_formats_are_refused(self):
        for text in ("0.23 kV", "231,40 V", "V", "", None, 231.4, "1e3 V"):
            self.assertIsNone(api.parse_measurement(text, "V"), text)
        self.assertEqual(api.parse_measurement("50 hz", "Hz"), 50.0)

    def test_no_grid_readings_means_no_gateway(self):
        for data in (None, [], {}, {"gridSideInfoList": None}, {"gridSideInfoList": []},
                     {"gridSideInfoList": [{"paramKey": "Other", "paramValue": "1 V"}]},
                     # Texts and generator rows alone do not make a Gateway.
                     {"softVersion": "V1", "generatorSideInfoList": ANSWER["generatorSideInfoList"]}):
            self.assertIsNone(api.parse_gateway(data))

    def test_odd_shapes_do_not_break_parsing(self):
        gateway = api.parse_gateway({
            "gridSideInfoList": [{"paramKey": "Phase A Voltage", "paramValue": "230.00 V"}],
            "newGridSideInfoList": "x", "generatorRealTimeInfoVOList": [1, None],
            "generatorSideInfoList": {"a": 1}, "softVersion": 5, "deviceModel": "",
        })
        self.assertEqual((gateway.values, gateway.texts), ({"voltage_a": 230.0}, {}))


if __name__ == "__main__":
    unittest.main()
