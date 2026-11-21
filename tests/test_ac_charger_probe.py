"""The read-only AC charger probe in the diagnostics.

No AC charger answer has been seen yet; the request paths and parameters
come from the mySigen web app's code. The probe only reads.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from test_api_login import _Response, api
from test_smart_loads import _ParamsHttp

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/sigen_mysigen"
STATION = _Response(200, {"code": 0, "data": {
    "stationId": "42", "dcSnList": [], "acSnList": ["AC-1"],
    "hasAcCharger": True, "acNum": 1, "stationName": "Home",
}})


def _load_diagnostics():
    """diagnostics.py with stand-ins for Home Assistant, the real api module."""
    stubs = {
        "homeassistant": {},
        "homeassistant.components": {},
        "homeassistant.components.diagnostics": {"async_redact_data": lambda data, keys: data},
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.const": {"CONF_PASSWORD": "password", "CONF_USERNAME": "username"},
        "homeassistant.core": {"HomeAssistant": object},
        "homeassistant.loader": {"async_get_integration": None},
        "sigen_mysigen_diag_pkg": {"__path__": []},
        "sigen_mysigen_diag_pkg.api": None,
        "sigen_mysigen_diag_pkg.link": {"local_inverter_serials": lambda hass, entry: set()},
        "sigen_mysigen_diag_pkg.modules": {"SOURCES": ()},
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    saved_const = sys.modules.get("sigen_mysigen_diag_pkg.const")
    try:
        for name, attrs in stubs.items():
            if attrs is None:
                sys.modules[name] = api
                continue
            module = types.ModuleType(name)
            module.__dict__.update(attrs)
            sys.modules[name] = module
        const = importlib.util.spec_from_file_location(
            "sigen_mysigen_diag_pkg.const", COMPONENT / "const.py")
        const_module = importlib.util.module_from_spec(const)
        sys.modules[const.name] = const_module
        const.loader.exec_module(const_module)
        spec = importlib.util.spec_from_file_location(
            "sigen_mysigen_diag_pkg.diagnostics", COMPONENT / "diagnostics.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in {**saved, "sigen_mysigen_diag_pkg.const": saved_const}.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


diagnostics = _load_diagnostics()


class TestRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_station_lists_ac_chargers_without_other_fields(self):
        http = _ParamsHttp([STATION])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        await client.async_station_id()
        self.assertEqual(client.ac_serials, ("AC-1",))
        self.assertEqual(client.ac_station_fields, {"hasAcCharger": True, "acNum": 1})
        self.assertIsNone(client.dc_serial)

    async def test_each_probe_is_one_get_with_the_charger(self):
        ok = _Response(200, {"code": 0, "data": {}})
        http = _ParamsHttp([STATION, *[ok] * len(api.AC_CHARGER_PROBES)])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        await client.async_station_id()
        for name, _path in api.AC_CHARGER_PROBES:
            await client.async_ac_charger_raw(name, "AC-1")
        charger = {"stationId": "42", "snCode": "AC-1"}
        self.assertEqual(http.sent[1:], [
            ("GET", "mode", {**charger, "deviceType": "6"}, False),
            ("GET", "generation", charger, False),
            ("GET", "mode", charger, False),
            ("GET", "ac", charger, False),
            ("GET", "realtime", charger, False),
        ])


class _ProbeSession:
    def __init__(self, answers, serials=("AC-1",)):
        self.answers, self.ac_serials, self.asked = dict(answers), serials, []
        self.ac_station_fields = {"hasAcCharger": bool(serials)}

    async def async_ac_charger_raw(self, name, serial):
        self.asked.append((name, serial))
        answer = self.answers[name]
        if isinstance(answer, Exception):
            raise answer
        return answer


def _coordinator(session, wait=0.0):
    return types.SimpleNamespace(
        session=session, rate_limit_wait=wait, note_rate_limit=lambda err: 60.0)


class TestProbe(unittest.IsolatedAsyncioTestCase):
    async def test_codes_are_kept_and_everything_else_masked(self):
        session = _ProbeSession({
            "supported_modes": [0, 1, "2"],
            "generation": 2,
            "charge_mode": {"chargeMode": 1, "minKeepChargeTime": 10, "snCode": "AC-1"},
            "charge_mode_ac": {"chargeMode": 0, "enableFromPack": True, "cutoffSocFromPack": 50,
                               "elecUsePriority": [{"deviceType": 6, "priority": 1}]},
            "live": {"acChargeRealTimeVO": {"singleChargingPower": 3.2, "runningTime": "01:02:03",
                                            "chargingStartTimeStr": "2026-11-22 07:00"}},
        })
        result = await diagnostics._ac_charger_probe(_coordinator(session))
        probe = result["probe"]
        self.assertEqual(result["listed"], 1)
        self.assertEqual(probe["supported_modes"], {"value": [0, 1, 2]})
        self.assertEqual(probe["generation"], {"value": 2})
        self.assertEqual(probe["charge_mode"]["chargeMode"], "value:1")
        self.assertNotIn("AC-1", str(probe))
        self.assertEqual(probe["charge_mode_ac"]["enableFromPack"], "value:True")
        self.assertEqual(probe["charge_mode_ac"]["elecUsePriority"][0]["deviceType"], "value:6")
        live = probe["live"]["acChargeRealTimeVO"]
        self.assertEqual(live["runningTime"], "value:'01:02:03'")
        self.assertNotIn("2026", live["chargingStartTimeStr"])
        # Read once each, for the first charger only.
        self.assertEqual([name for name, _ in session.asked],
                         [name for name, _ in api.AC_CHARGER_PROBES])

    async def test_odd_code_lists_are_masked_not_shown(self):
        session = _ProbeSession({name: ["AC-1"] for name, _ in api.AC_CHARGER_PROBES})
        probe = (await diagnostics._ac_charger_probe(_coordinator(session)))["probe"]
        self.assertNotIn("AC-1", str(probe))
        self.assertNotIn("value", probe["supported_modes"])

    async def test_without_a_charger_nothing_is_read(self):
        session = _ProbeSession({}, serials=())
        result = await diagnostics._ac_charger_probe(_coordinator(session))
        self.assertEqual(result, {"listed": 0, "station_fields": {"hasAcCharger": False}})
        self.assertEqual(session.asked, [])

    async def test_failures_and_rate_limits(self):
        answers = {name: {} for name, _ in api.AC_CHARGER_PROBES}
        answers["generation"] = api.MySigenConnectionError("code 1000", 200)
        answers["charge_mode"] = api.MySigenRateLimitError(None)
        session = _ProbeSession(answers)
        probe = (await diagnostics._ac_charger_probe(_coordinator(session)))["probe"]
        self.assertTrue(probe["generation"].startswith("unavailable"))
        self.assertTrue(probe["charge_mode"].startswith("rate limited"))
        # A rate limit stops the probe.
        self.assertNotIn("live", probe)

        session = _ProbeSession(answers)
        result = await diagnostics._ac_charger_probe(_coordinator(session, wait=30.0))
        self.assertTrue(result["probe"].startswith("rate limited"))
        self.assertEqual(session.asked, [])


if __name__ == "__main__":
    unittest.main()
