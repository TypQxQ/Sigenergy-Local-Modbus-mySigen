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
        # A missing or odd end is 0 or 100 %, as the mySigen app reads it.
        self.assertEqual(api.parse_soc_range({}), (0, 100))
        self.assertEqual(api.parse_soc_range({"socUpperLimit": 90}), (0, 90))
        self.assertEqual(api.parse_soc_range({"socUpperLimit": 5, "socLowerLimit": 100}), (0, 100))
        self.assertIsNone(api.parse_soc_range(None))

    def test_battery_boost(self):
        mode = api.parse_dc_charge_mode(MODE)
        self.assertEqual((mode.boost, mode.boost_cutoff), (True, 20))
        self.assertIs(api.parse_dc_charge_mode({**MODE, "enableFromPack": 0}).boost, False)
        self.assertIsNone(api.parse_dc_charge_mode({**MODE, "enableFromPack": None}).boost)
        self.assertIsNone(api.parse_dc_charge_mode({**MODE, "cutoffSocFromPack": True}).boost_cutoff)
        self.assertEqual(api.parse_dc_charge_mode({**MODE, "cutoffSocFromPack": 35.0}).boost_cutoff, 35)
        # The cut-off range is the charger's own, 0 to 100 % when not reported.
        self.assertEqual(mode.cutoff_range, (0, 100))
        self.assertEqual(api.parse_dc_charge_mode(
            {**MODE, "socLowerLimit": 10, "socUpperLimit": 90}).cutoff_range, (10, 90))
        # Boost goes back in the form mySigen gave it; the rest unchanged.
        self.assertEqual(mode.body(boost=False), {
            "enableFromPack": False, "cutoffSocFromPack": 20,
            "vehicleDischargeCutoffSoc": 30, "chargeMode": 1})
        self.assertEqual(
            api.parse_dc_charge_mode({**MODE, "enableFromPack": 0}).body(boost=True)["enableFromPack"],
            1,
        )
        self.assertEqual(mode.body(boost_cutoff=35), {
            "enableFromPack": True, "cutoffSocFromPack": 35.0,
            "vehicleDischargeCutoffSoc": 30, "chargeMode": 1})

    def test_v2x_floor_range(self):
        # The charger's own limits, as StephenE's plant sends the fields.
        self.assertEqual(api.parse_dc_charge_mode(
            {**MODE, "minimumDischargeCutoffSoc": 10, "maximumDischargeCutoffSoc": 80}
        ).floor_range, (10, 80))
        # Missing: 20 to 100 %, as the mySigen app assumes.
        self.assertEqual(api.parse_dc_charge_mode(MODE).floor_range, (20, 100))


class TestRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_serial_and_bodies(self):
        ok = _Response(200, {"code": 0, "data": True})
        http = _Http([CHARGER_STATION, ok, ok, ok, ok])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        await client.async_station_id()
        self.assertEqual(client.dc_serial, "DC-1")
        await client.async_set_dc_charge_mode(api.parse_dc_charge_mode(MODE).body(mode="bidirectional"))
        await client.async_set_dc_stop_level(api.parse_dc_charge_setting(SETTING), 90)
        await client.async_set_v2x_enabled(False)
        await client.async_set_dc_charge_mode(api.parse_dc_charge_mode(MODE).body(boost=False))
        self.assertEqual(http.requests[1:], [
            ("POST", "dc", {"stationId": 42, "snCode": "DC-1", "enableFromPack": True,
                            "cutoffSocFromPack": 20, "vehicleDischargeCutoffSoc": 30,
                            "chargeMode": 2}),
            ("POST", "setting", {"stationId": 42, "snCode": "DC-1", "allowedChargePower": 12.0,
                                 "vehicleChargingCutoffSoc": 90}),
            ("POST", "open-close", {"stationId": 42, "snCode": "DC-1", "dischargeEnable": 0}),
            ("POST", "dc", {"stationId": 42, "snCode": "DC-1", "enableFromPack": False,
                            "cutoffSocFromPack": 20, "vehicleDischargeCutoffSoc": 30,
                            "chargeMode": 1}),
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
        self.__dict__.setdefault("mode_bodies", []).append(body)
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


def boost_mode(enabled, cutoff=20, **extra):
    return api.parse_dc_charge_mode(
        {**MODE, "enableFromPack": enabled, "cutoffSocFromPack": cutoff, **extra}
    )


def setting(level):
    return api.parse_dc_charge_setting({**SETTING, "vehicleChargingCutoffSoc": level})


class TestRules(unittest.IsolatedAsyncioTestCase):
    def ems(self, state="off"):
        return lambda: state

    async def test_changes_are_sent_once_and_confirmed(self):
        coordinator = _ChargerCoordinator({"mode": [mode(1), mode(0)]})
        result = await control.async_set_dc_charge_mode(
            coordinator, mode="fast_charging", sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.writes, [("mode", 0, 30)])

        coordinator = _ChargerCoordinator({"mode": [mode(1), mode(1, 45)]})
        await control.async_set_dc_discharge_floor(
            coordinator, level=45, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("mode", 1, 45)])

        # Reported in discussion 38: the floor showed only on a later read.
        coordinator = _ChargerCoordinator({"mode": [mode(1), mode(1), mode(1), mode(1, 45)]})
        result = await control.async_set_dc_discharge_floor(
            coordinator, level=45, sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.writes, [("mode", 1, 45)])

        coordinator = _ChargerCoordinator({"setting": [setting(80), setting(90)]})
        await control.async_set_dc_stop_level(
            coordinator, level=90, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("setting", 12.0, 90)])

        coordinator = _ChargerCoordinator({"v2x": [False, True]})
        await control.async_set_v2x_enabled(
            coordinator, enabled=True, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("v2x", True)])
        self.assertEqual([s[0] for s in coordinator.settings], ["v2x_enabled"])

    async def test_battery_boost_is_sent_once_and_confirmed(self):
        coordinator = _ChargerCoordinator({"mode": [boost_mode(True), boost_mode(False)]})
        result = await control.async_set_dc_battery_boost(
            coordinator, enabled=False, sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(len(coordinator.session.writes), 1)
        self.assertIs(coordinator.session.mode_bodies[0]["enableFromPack"], False)
        self.assertEqual(coordinator.session.mode_bodies[0]["chargeMode"], 1)
        self.assertEqual([s[0] for s in coordinator.settings], ["dc_battery_boost"])

        coordinator = _ChargerCoordinator({"mode": [boost_mode(True), boost_mode(True, 40)]})
        result = await control.async_set_dc_boost_cutoff(
            coordinator, level=40, sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.mode_bodies[0]["cutoffSocFromPack"], 40.0)
        self.assertIs(coordinator.session.mode_bodies[0]["enableFromPack"], True)

    async def test_nothing_sent_when_already_set(self):
        coordinator = _ChargerCoordinator({"v2x": [True]})
        await control.async_set_v2x_enabled(
            coordinator, enabled=True, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])
        coordinator = _ChargerCoordinator({"mode": [boost_mode(True)]})
        await control.async_set_dc_battery_boost(coordinator, enabled=True, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_refusals_send_nothing(self):
        cases = [
            (dict(charger=False), "mode", dict(mode="fast_charging"), "no DC charger"),
            (dict(), "mode", dict(mode="schedule"), "not available"),
            (dict(allow=False), "mode", dict(mode="fast_charging"), "switched off"),
            (dict(), "stop", dict(level=101), "5 to 100"),
            # The floor follows the charger's limits, 20 to 100 % by default.
            (dict(), "floor", dict(level=10), "20 to 100"),
            (dict(charger=False), "boost", dict(enabled=False), "no DC charger"),
            (dict(allow=False), "boost", dict(enabled=False), "switched off"),
            (dict(charger=False), "cutoff", dict(level=40), "no DC charger"),
            # The cut-off follows the charger's limits, 0 to 100 % by default.
            (dict(), "cutoff", dict(level=101), "0 to 100"),
        ]
        functions = {
            "mode": control.async_set_dc_charge_mode,
            "stop": control.async_set_dc_stop_level,
            "floor": control.async_set_dc_discharge_floor,
            "boost": control.async_set_dc_battery_boost,
            "cutoff": control.async_set_dc_boost_cutoff,
        }
        for coordinator_args, which, call_args, text in cases:
            coordinator = _ChargerCoordinator(
                {"mode": [mode(1)], "setting": [setting(80)]}, **coordinator_args)
            with self.assertRaisesRegex(control.CommandRefused, text):
                await functions[which](coordinator, sleep=_no_sleep, **call_args)
            self.assertEqual(coordinator.session.writes, [])


class TestNoRemoteEmsRule(unittest.IsolatedAsyncioTestCase):
    """Remote EMS does not control these settings and the app does not block them."""

    async def test_charger_changes_take_no_remote_ems_check(self):
        for function in (
            control.async_set_dc_charge_mode, control.async_set_dc_discharge_floor,
            control.async_set_dc_stop_level, control.async_set_v2x_enabled,
            control.async_set_dc_battery_boost, control.async_set_dc_boost_cutoff,
        ):
            self.assertNotIn("remote_ems", function.__code__.co_varnames, function.__name__)

    async def test_boost_refused_when_not_reported(self):
        coordinator = _ChargerCoordinator({"mode": [boost_mode(None)]})
        with self.assertRaisesRegex(control.CommandRefused, "did not report whether"):
            await control.async_set_dc_battery_boost(coordinator, enabled=True, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_the_cutoff_uses_the_limits_just_read(self):
        narrow = boost_mode(True, socLowerLimit=10, socUpperLimit=90)
        coordinator = _ChargerCoordinator({"mode": [narrow]})
        with self.assertRaisesRegex(control.CommandRefused, "10 to 90"):
            await control.async_set_dc_boost_cutoff(coordinator, level=5, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])
        coordinator.dc_charge_mode = narrow
        self.assertEqual(control.dc_cutoff_range(coordinator), (10, 90))

    async def test_without_a_reported_range_the_limit_is_0_to_100(self):
        coordinator = _ChargerCoordinator({"setting": [setting(80), setting(95)]}, soc_range=None)
        self.assertEqual(control.dc_stop_range(coordinator), (0, 100))
        await control.async_set_dc_stop_level(coordinator, level=95, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("setting", 12.0, 95)])

    async def test_the_floor_uses_the_limits_just_read(self):
        wide = api.parse_dc_charge_mode({**MODE, "chargeMode": 2, "minimumDischargeCutoffSoc": 5,
                                         "maximumDischargeCutoffSoc": 90})
        set_to_10 = api.parse_dc_charge_mode({**wide.raw, "vehicleDischargeCutoffSoc": 10})
        coordinator = _ChargerCoordinator({"mode": [wide, set_to_10]})
        await control.async_set_dc_discharge_floor(coordinator, level=10, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("mode", 2, 10)])
        coordinator = _ChargerCoordinator({"mode": [wide]})
        with self.assertRaisesRegex(control.CommandRefused, "5 to 90"):
            await control.async_set_dc_discharge_floor(coordinator, level=95, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])


if __name__ == "__main__":
    unittest.main()
