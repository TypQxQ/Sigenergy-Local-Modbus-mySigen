"""Gateway grid-side parsing (no network, no HA)."""
from __future__ import annotations

import unittest

from test_api_login import api

ROWS = [
    {"paramKey": "Phase A Voltage", "paramValue": "231.40 V"},
    {"paramKey": "Phase B Voltage", "paramValue": "0.00 V"},
    {"paramKey": "Phase C Voltage", "paramValue": "--"},
    {"paramKey": "Phase A Current", "paramValue": "-3.21 A"},
    {"paramKey": " phase b current ", "paramValue": "1.5A"},
    {"paramKey": "Voltage Frequency", "paramValue": "50.01 Hz"},
    {"paramKey": "Total Active Power", "paramValue": "1.234 kW"},
    {"paramKey": "Grid Side Contactor Status", "paramValue": "Close"},
    "not a row",
]


class TestGateway(unittest.TestCase):
    def test_reads_the_grid_side_rows(self):
        gateway = api.parse_gateway({"gridSideInfoList": ROWS})
        self.assertEqual(gateway.values, {
            "voltage_a": 231.4, "voltage_b": 0.0, "current_a": -3.21,
            "current_b": 1.5, "frequency": 50.01,
        })
        self.assertEqual(gateway.contactor, "close")

    def test_zero_is_a_reading_and_a_placeholder_is_not(self):
        gateway = api.parse_gateway({"gridSideInfoList": ROWS})
        self.assertEqual(gateway.values["voltage_b"], 0.0)
        self.assertNotIn("voltage_c", gateway.values)

    def test_unexpected_units_and_formats_are_refused(self):
        for text in ("0.23 kV", "231,40 V", "V", "", None, 231.4, "1e3 V"):
            self.assertIsNone(api.parse_measurement(text, "V"), text)
        self.assertEqual(api.parse_measurement("50 hz", "Hz"), 50.0)

    def test_no_gateway_readings_is_none(self):
        for data in (None, [], {}, {"gridSideInfoList": None}, {"gridSideInfoList": []},
                     {"gridSideInfoList": [{"paramKey": "Other", "paramValue": "1 V"}]}):
            self.assertIsNone(api.parse_gateway(data))

    def test_contactor_alone_counts(self):
        gateway = api.parse_gateway({"gridSideInfoList": [
            {"paramKey": "Grid Side Contactor Status", "paramValue": "Open Circuit"}]})
        self.assertEqual((gateway.values, gateway.contactor), ({}, "open_circuit"))


if __name__ == "__main__":
    unittest.main()
