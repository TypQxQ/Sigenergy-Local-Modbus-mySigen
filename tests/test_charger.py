"""DC charger settings (step E). Shapes follow the solidfox clients."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from test_api_login import _Response, api
from test_control import _Http, control
from test_settings import _Coordinator, _Session, _no_sleep

CHARGER_STATION = _Response(200, {"code": 0, "data": {"stationId": "42", "dcSnList": ["DC-1"]}})
MODE = {"chargeMode": 1, "enableFromPack": True, "cutoffSocFromPack": 20,
        "vehicleDischargeCutoffSoc": 30, "enableFromGrid": False}
SETTING = {"allowedChargePower": 12, "ratedPower": 25, "vehicleChargingCutoffSoc": 80}


class TestParsing(unittest.TestCase):
    def test_mode(self):
        mode = api.parse_dc_charge_mode(MODE)
        self.assertEqual((mode.mode, mode.discharge_floor), ("solar_surplus", 30))
        self.assertIsNone(api.parse_dc_charge_mode({**MODE, "chargeMode": 7}).mode)
        # The read fields go back unchanged; only the asked one changes.
        self.assertEqual(mode.body(mode="fast_charging"), {
            "enableFromPack": True, "cutoffSocFromPack": 20,
            "vehicleDischargeCutoffSoc": 30, "chargeMode": 0})
        self.assertEqual(mode.body(discharge_floor=40)["vehicleDischargeCutoffSoc"], 40)
        self.assertEqual(mode.body(discharge_floor=40)["chargeMode"], 1)
        for bad in (None, {}, {"chargeMode": None}, {"chargeMode": True}):
            self.assertIsNone(api.parse_dc_charge_mode(bad))

    def test_setting_v2x_and_range(self):
        setting = api.parse_dc_charge_setting(SETTING)
        self.assertEqual((setting.allowed_power, setting.stop_level), (12.0, 80))
        for bad in (None, {}, {"allowedChargePower": True, "vehicleChargingCutoffSoc": 80},
                    {"allowedChargePower": 12}):
            self.assertIsNone(api.parse_dc_charge_setting(bad))
        self.assertIs(api.parse_v2x_enabled({"dischargeEnable": 1}), True)
        self.assertIs(api.parse_v2x_enabled({"dischargeEnable": False}), False)
        self.assertIsNone(api.parse_v2x_enabled({"dischargeEnable": 3}))
        # The range as the development plant answered it.
        self.assertEqual(api.parse_soc_range({"socUpperLimit": 100, "socLowerLimit": 5}), (5, 100))
        self.assertIsNone(api.parse_soc_range({"socUpperLimit": 5, "socLowerLimit": 100}))


class TestRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_serial_and_bodies(self):
        ok = _Response(200, {"code": 0, "data": True})
        http = _Http([CHARGER_STATION, ok, ok, ok])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        await client.async_station_id()
        self.assertEqual(client.dc_serial, "DC-1")
        await client.async_set_dc_charge_mode(api.parse_dc_charge_mode(MODE).body(mode="bidirectional"))
        await client.async_set_dc_stop_level(api.parse_dc_charge_setting(SETTING), 90)
        await client.async_set_v2x_enabled(False)
        self.assertEqual(http.requests[1:], [
            ("POST", "dc", {"stationId": 42, "snCode": "DC-1", "enableFromPack": True,
                            "cutoffSocFromPack": 20, "vehicleDischargeCutoffSoc": 30,
                            "chargeMode": 2}),
            ("POST", "setting", {"stationId": 42, "snCode": "DC-1", "allowedChargePower": 12.0,
                                 "vehicleChargingCutoffSoc": 90}),
            ("POST", "open-close", {"stationId": 42, "snCode": "DC-1", "dischargeEnable": 0}),
        ])

    async def test_a_different_station_is_never_adopted(self):
        other = _Response(200, {"code": 0, "data": {"stationId": "43", "dcSnList": ["DC-9"]}})
        same = _Response(200, {"code": 0, "data": {"stationId": "42", "dcSnList": []}})
        http = _Http([CHARGER_STATION, other, same])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        await client.async_station_id()
        self.assertFalse(await client.async_read_station())
        self.assertEqual((await client.async_station_id(), client.dc_serial), ("42", "DC-1"))
        # The same station may change its charger list.
        self.assertTrue(await client.async_read_station())
        self.assertIsNone(client.dc_serial)

    async def test_station_without_charger_sends_nothing(self):
        http = _Http([_Response(200, {"code": 0, "data": {"stationId": "42", "dcSnList": []}})])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        await client.async_station_id()
        self.assertIsNone(client.dc_serial)
        with self.assertRaises(api.MySigenRejectedError):
            await client.async_set_v2x_enabled(True)
        self.assertEqual(len(http.requests), 1)


class _ChargerSession(_Session):
    async def async_dc_charge_mode(self):
        return self._next("mode")

    async def async_dc_charge_setting(self):
        return self._next("setting")

    async def async_v2x_enabled(self):
        return self._next("v2x")

    async def async_set_dc_charge_mode(self, body):
        await self._write("mode", body["chargeMode"], body.get("vehicleDischargeCutoffSoc"))

    async def async_set_dc_stop_level(self, current, level):
        await self._write("setting", current.allowed_power, level)

    async def async_set_v2x_enabled(self, enabled):
        await self._write("v2x", enabled)


class _ChargerCoordinator(_Coordinator):
    def __init__(self, reads, charger=True, soc_range=(5, 100), **kwargs):
        super().__init__(reads, **kwargs)
        self.session = _ChargerSession(reads, kwargs.get("write_error"))
        self.has_dc_charger, self.soc_range = charger, soc_range

    publish_dc_charge_mode = publish_dc_charge_setting = publish_v2x_enabled = (
        _Coordinator.publish_operating_mode
    )


def mode(code, floor=30):
    return api.parse_dc_charge_mode({**MODE, "chargeMode": code, "vehicleDischargeCutoffSoc": floor})


def setting(level):
    return api.parse_dc_charge_setting({**SETTING, "vehicleChargingCutoffSoc": level})


class TestRules(unittest.IsolatedAsyncioTestCase):
    def ems(self, state="off"):
        return lambda: state

    async def test_changes_are_sent_once_and_confirmed(self):
        coordinator = _ChargerCoordinator({"mode": [mode(1), mode(0)]})
        result = await control.async_set_dc_charge_mode(
            coordinator, mode="fast_charging", remote_ems=self.ems(), sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.writes, [("mode", 0, 30)])

        coordinator = _ChargerCoordinator({"mode": [mode(1), mode(1, 45)]})
        await control.async_set_dc_discharge_floor(
            coordinator, level=45, remote_ems=self.ems(), sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("mode", 1, 45)])

        coordinator = _ChargerCoordinator({"setting": [setting(80), setting(90)]})
        await control.async_set_dc_stop_level(
            coordinator, level=90, remote_ems=self.ems(), sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("setting", 12.0, 90)])

        coordinator = _ChargerCoordinator({"v2x": [False, True]})
        await control.async_set_v2x_enabled(
            coordinator, enabled=True, remote_ems=self.ems(), sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("v2x", True)])
        self.assertEqual([s[0] for s in coordinator.settings], ["v2x_enabled"])

    async def test_nothing_sent_when_already_set(self):
        coordinator = _ChargerCoordinator({"v2x": [True]})
        await control.async_set_v2x_enabled(
            coordinator, enabled=True, remote_ems=self.ems(), sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_refusals_send_nothing(self):
        cases = [
            (dict(charger=False), "mode", dict(mode="fast_charging"), "no DC charger"),
            (dict(), "mode", dict(mode="schedule"), "not available"),
            (dict(), "mode", dict(mode="fast_charging", remote_ems=self.ems("on")), "Remote EMS"),
            (dict(), "mode", dict(mode="fast_charging", remote_ems=self.ems(None)), "Remote EMS"),
            (dict(allow=False), "mode", dict(mode="fast_charging"), "switched off"),
            (dict(), "stop", dict(level=101), "5 to 100"),
            (dict(soc_range=None), "stop", dict(level=80), "allowed charge levels"),
            (dict(), "floor", dict(level=2), "5 to 100"),
        ]
        functions = {
            "mode": control.async_set_dc_charge_mode,
            "stop": control.async_set_dc_stop_level,
            "floor": control.async_set_dc_discharge_floor,
        }
        for coordinator_args, which, call_args, text in cases:
            coordinator = _ChargerCoordinator(
                {"mode": [mode(1)], "setting": [setting(80)]}, **coordinator_args)
            call_args.setdefault("remote_ems", self.ems())
            with self.assertRaisesRegex(control.CommandRefused, text):
                await functions[which](coordinator, sleep=_no_sleep, **call_args)
            self.assertEqual(coordinator.session.writes, [])


if __name__ == "__main__":
    unittest.main()
