"""Battery modules: the device tree, the three reads and when they are made.

Answer shapes are as the development plant answered (labels and units);
the values are sigenergy2mqtt's.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from test_api_login import _Response  # also provides aiohttp when missing

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/sigen_mysigen"
PACKAGE = "sigen_mysigen_modules_pkg"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_package = types.ModuleType(PACKAGE)
_package.__path__ = []
sys.modules[PACKAGE] = _package
api = _load("api")
modules = _load("modules")


def _row(key, text, unit=""):
    value = f"{text}{unit}" if unit == "%" else f"{text} {unit}".strip()
    return {"paramKey": key, "paramValue": value, "paramValueText": text, "paramValueUnit": unit}


TREE = {"stationId": 42, "nodeList": [
    {"deviceType": 8, "snCode": "GW1", "nodeList": [
        {"deviceType": 2, "snCode": "aio1", "nodeList": [
            {"deviceType": 4, "snCode": "bat-a", "batPosition": 1},
            {"deviceType": "4", "snCode": "", "showSnCode": "BAT-B", "batPosition": "2"},
            {"deviceType": 4, "snCode": "BAT-A", "batPosition": 9},  # listed twice
        ]},
        {"deviceType": 3, "snCode": "INV2", "nodeList": [
            {"deviceType": 4, "snCode": "BAT-C"},
        ]},
    ]},
    {"deviceType": 4, "snCode": "BAT-D", "batPosition": True},
    {"deviceType": 5, "snCode": "DC1"},
]}
STATIC = {"paramInfoVOList": [
    _row("Device SN", "987B65BC1238"),
    _row("Software Version", "V100R001C00SPC108"),
    _row("Device Model", "SigenStor BAT 8.0"),
    _row("Rated Battery Capacity", "8.06", "kWh"),
]}
LIVE = {"realTimeInfo": [
    _row("Battery SOC", "74.5", "%"),
    _row("Charging & Discharging Power", "-0.655", "kW"),
    _row("Battery Pack Voltage", "29.8", "V"),
    _row("Heating status", "Off"),
], "dataCurrTimeStamp": ""}
PACK = {"paramInfoVOList": [
    _row("Total Discharge Amount", "2.96", "MWh"),
    _row("Average Cell Voltage", "3.338", "V"),
    _row("Average Cell Temperature", "25.3", "℃"),
]}


class TestDeviceTree(unittest.TestCase):
    def test_modules_in_tree_order_with_position_and_inverter(self):
        found = api.parse_battery_modules(TREE)
        self.assertEqual(found, (
            api.BatteryModule("BAT-A", "1", "AIO1"),
            api.BatteryModule("BAT-B", "2", "AIO1"),
            api.BatteryModule("BAT-C", None, "INV2"),
            api.BatteryModule("BAT-D", None, None),
        ))

    def test_odd_answers_give_no_modules(self):
        for bad in (None, "x", 5, [], {"nodeList": "x"}, {"deviceType": 4, "snCode": ""}):
            self.assertEqual(api.parse_battery_modules(bad), ())

    def test_the_station_check_keeps_the_modules_it_read(self):
        # The serial check already reads the tree; setup needs no second read.
        self.assertIn("BAT-A", api.topology_serials(TREE))


class TestReadings(unittest.TestCase):
    def test_static(self):
        self.assertEqual(
            api.parse_module_values(STATIC, "paramInfoVOList", api.MODULE_STATIC_FIELDS),
            {"model": "SigenStor BAT 8.0", "firmware": "V100R001C00SPC108",
             "rated_capacity": 8.06},
        )

    def test_readings(self):
        self.assertEqual(
            api.parse_module_values(LIVE, "realTimeInfo", api.MODULE_LIVE_FIELDS),
            {"soc": 74.5, "power": -0.655, "pack_voltage": 29.8, "heating": "Off"},
        )

    def test_pack(self):
        pack = api.parse_module_values(PACK, "paramInfoVOList", api.MODULE_PACK_FIELDS)
        # MWh is stored as kWh, so the total keeps one unit.
        self.assertAlmostEqual(pack["discharged"], 2960.0)
        self.assertEqual((pack["cell_voltage"], pack["cell_temperature"]), (3.338, 25.3))

    def test_units_are_converted_or_refused(self):
        fields = {"e": api._ModuleField("e", "kWh"), "t": api._ModuleField("t", "°C"),
                  "v": api._ModuleField("v", "V")}

        def parse(*rows):
            return api.parse_module_values({"l": list(rows)}, "l", fields)

        self.assertEqual(parse(_row("E", "1500", "Wh")), {"e": 1.5})
        self.assertEqual(parse(_row("T", "77", "°F")), {"t": 25.0})
        self.assertAlmostEqual(parse(_row("V", "3300", "mV"))["v"], 3.3)
        # An unknown unit is no reading rather than a wrong one.
        self.assertEqual(parse(_row("E", "5", "Ah"), _row("V", "1", "kV")), {})
        # Without the split fields, the combined text is read.
        self.assertEqual(parse({"paramKey": "E", "paramValue": "2,5 MWh"}), {"e": 2500.0})
        self.assertEqual(parse({"paramKey": "E", "paramValue": "--"}), {})
        self.assertEqual(parse({"paramKey": "E", "paramValueText": "x", "paramValueUnit": "kWh"}), {})

    def test_missing_rows_are_unknown(self):
        for bad in (None, {}, {"paramInfoVOList": "x"},
                    {"paramInfoVOList": [None, {"paramKey": 5}]}):
            self.assertEqual(
                api.parse_module_values(bad, "paramInfoVOList", api.MODULE_PACK_FIELDS), {}
            )


class _Http:
    """Records each request's path and query."""

    closed = False

    def __init__(self, replies):
        self.replies, self.requests, self.headers = list(replies), [], []

    def post(self, url, **kwargs):
        return _Response(200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600})

    def request(self, method, url, **kwargs):
        self.requests.append((method, url.split("/", 3)[-1], kwargs.get("params")))
        self.headers.append(kwargs.get("headers") or {})
        return self.replies.pop(0)


def _ok(data):
    return _Response(200, {"code": 0, "data": data})


class TestRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_paths_and_queries(self):
        http = _Http([_ok({"stationId": "42"}), _ok(TREE), _ok(STATIC), _ok(LIVE), _ok(PACK)])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p")
        listed = await client.async_read_battery_modules()
        self.assertEqual(client.battery_modules, listed)
        self.assertEqual((await client.async_module_static("BAT-A"))["rated_capacity"], 8.06)
        self.assertEqual((await client.async_module_readings("BAT-A"))["soc"], 74.5)
        self.assertEqual((await client.async_module_pack("BAT-A"))["cell_temperature"], 25.3)
        self.assertEqual([(path, query) for _m, path, query in http.requests[1:]], [
            ("device/devicetreepanel/topology", {"stationId": "42"}),
            ("device/sigen/device/static/info",
             {"stationId": "42", "snCode": "BAT-A", "deviceType": "4"}),
            ("device/sigen/device/dynamic/info",
             {"stationId": "42", "snCode": "BAT-A", "deviceType": "4", "refreshFlag": ""}),
            ("device/device/pack/queryPackMergeInfo", {"stationId": "42", "snCode": "BAT-A"}),
        ])
        self.assertTrue(all(method == "GET" for method, _p, _q in http.requests))
        # mySigen refuses the readings (code 1000) without a package name.
        # The add-on gives its own, and only on that read.
        packages = [headers.get("sg-pkg") for headers in http.headers[1:]]
        self.assertEqual(packages, [None, None, "sigen_mysigen", None])
        self.assertFalse(any(
            key.lower() in ("sg-platform", "sg-source") for headers in http.headers
            for key in headers
        ))

    async def test_station_check_fills_the_module_list(self):
        http = _Http([_ok({"stationId": "42"}), _ok(TREE), _ok([])])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p")
        self.assertIsNone(client.battery_modules)
        await client.async_device_serials()
        self.assertEqual(len(client.battery_modules), 4)


class _Session:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    async def _answer(self, what, serial):
        self.calls.append((what, serial))
        if (what, serial) in self.fail:
            raise api.MySigenConnectionError("HTTP 500", 500)
        return {
            "static": {"model": f"M-{serial}"},
            "readings": {"soc": 50.0},
            "pack": {"cell_temperature": 20.0},
        }[what]

    async def async_module_static(self, serial):
        return await self._answer("static", serial)

    async def async_module_readings(self, serial):
        return await self._answer("readings", serial)

    async def async_module_pack(self, serial):
        return await self._answer("pack", serial)


MODULES = (api.BatteryModule("A", "1", None), api.BatteryModule("B", "2", None))


def _run(coro):
    return asyncio.run(coro)


class TestSchedule(unittest.TestCase):
    def test_one_module_per_poll_then_quiet_until_due(self):
        reader, session = modules.ModuleReader(MODULES), _Session()
        self.assertEqual(_run(reader.async_read_next(session, 0.0)), "A")
        self.assertEqual(_run(reader.async_read_next(session, 30.0)), "B")
        self.assertIsNone(_run(reader.async_read_next(session, 60.0)))
        self.assertEqual(session.calls, [
            ("static", "A"), ("readings", "A"), ("pack", "A"),
            ("static", "B"), ("readings", "B"), ("pack", "B"),
        ])
        # Readings are due again after the interval; the model after a day.
        session.calls.clear()
        self.assertEqual(_run(reader.async_read_next(session, modules.MODULE_INTERVAL)), "A")
        self.assertEqual(session.calls, [("readings", "A"), ("pack", "A")])
        self.assertEqual(reader.value("A", "model"), "M-A")
        self.assertEqual(reader.value("A", "soc"), 50.0)
        self.assertEqual(reader.value("A", "cell_temperature"), 20.0)
        self.assertTrue(reader.available("A", modules.INFO))
        # B is due first; A's model is read again on the next poll.
        session.calls.clear()
        day = modules.MODULE_STATIC_INTERVAL
        self.assertEqual(_run(reader.async_read_next(session, day)), "B")
        self.assertEqual(_run(reader.async_read_next(session, day + 30)), "A")
        self.assertIn(("static", "A"), session.calls)

    def test_one_failing_read_leaves_the_others(self):
        reader = modules.ModuleReader(MODULES[:1])
        session = _Session(fail={("readings", "A")})
        _run(reader.async_read_next(session, 0.0))
        self.assertFalse(reader.available("A", modules.READINGS))
        self.assertIsNone(reader.value("A", "soc"))
        self.assertTrue(reader.available("A", modules.PACK))
        self.assertEqual(reader.value("A", "cell_temperature"), 20.0)
        self.assertEqual(reader.errors[modules.READINGS]["A"], "MySigenConnectionError: HTTP 500")
        self.assertNotIn("A", reader.errors[modules.PACK])
        # Retried a minute later; a good answer clears the error.
        session.fail.clear()
        _run(reader.async_read_next(session, modules.MODULE_RETRY))
        self.assertTrue(reader.available("A", modules.READINGS))
        self.assertNotIn("A", reader.errors[modules.READINGS])

    def test_a_failed_info_read_leaves_the_pack(self):
        reader = modules.ModuleReader(MODULES[:1])
        session = _Session(fail={("static", "A")})
        _run(reader.async_read_next(session, 0.0))
        self.assertFalse(reader.available("A", modules.INFO))
        self.assertTrue(reader.available("A", modules.PACK))
        self.assertEqual(reader.value("A", "cell_temperature"), 20.0)
        self.assertEqual(reader.errors[modules.INFO]["A"], "MySigenConnectionError: HTTP 500")
        # The info is asked again sooner than a day later.
        session.fail.clear()
        _run(reader.async_read_next(session, modules.MODULE_INTERVAL))
        self.assertTrue(reader.available("A", modules.INFO))
        self.assertNotIn("A", reader.errors[modules.INFO])

    def test_a_failed_reading_is_unavailable_and_retried_once_soon(self):
        reader = modules.ModuleReader(MODULES[:1])
        session = _Session(fail={("pack", "A")})
        _run(reader.async_read_next(session, 0.0))
        self.assertFalse(reader.available("A", modules.PACK))
        self.assertIsNone(reader.value("A", "cell_temperature"))
        # The model still shows.
        self.assertTrue(reader.available("A", modules.INFO))
        self.assertIsNone(_run(reader.async_read_next(session, modules.MODULE_RETRY - 1)))
        self.assertEqual(_run(reader.async_read_next(session, modules.MODULE_RETRY)), "A")
        # After the retry also failed, the normal interval applies.
        self.assertIsNone(
            _run(reader.async_read_next(session, modules.MODULE_RETRY * 2 + 1))
        )

    def test_a_failed_model_read_keeps_the_earlier_one(self):
        reader, session = modules.ModuleReader(MODULES[:1]), _Session()
        _run(reader.async_read_next(session, 0.0))
        session.fail.add(("static", "A"))
        _run(reader.async_read_next(session, modules.MODULE_STATIC_INTERVAL))
        self.assertEqual(reader.value("A", "model"), "M-A")

    def test_rate_limits_are_raised(self):
        class Limited(_Session):
            async def async_module_static(self, serial):
                raise api.MySigenRateLimitError(30)

        with self.assertRaises(api.MySigenRateLimitError):
            _run(modules.ModuleReader(MODULES).async_read_next(Limited(), 0.0))

    def test_no_modules_reads_nothing(self):
        session = _Session()
        self.assertIsNone(_run(modules.ModuleReader(()).async_read_next(session, 0.0)))
        self.assertEqual(session.calls, [])

    def test_device_identifier(self):
        self.assertEqual(modules.module_identifier("E1", "BAT-A"), "E1_battery_BAT-A")


if __name__ == "__main__":
    unittest.main()
