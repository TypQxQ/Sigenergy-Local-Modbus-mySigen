"""AC charger: charging mode, battery boost and session energy.

The answers are the ones two testers read on plants with a generation 1
Sigen AC charger (discussion 38), each mode set in the app in turn. The
change request is the app's own (the same function and settings object as
the DC charger); no AC change has been captured yet.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from test_api_login import _Response, api
from test_control import _Http, control
from test_settings import _Coordinator, _Session, _no_sleep
from test_smart_loads import _ParamsHttp


def mode_answer(code, boost=False, cutoff=10.0):
    """`device/charge/mode/ac` as both plants answered it."""
    return {
        "chargeMode": code, "enableFromPack": boost, "cutoffSocFromPack": cutoff,
        "enableFromGrid": False, "maxPowerFromGrid": 0.0,
    }


STATION = _Response(200, {"code": 0, "data": {
    "stationId": "42", "dcSnList": [], "acSnList": ["AC-1"], "hasAcCharger": True, "acNum": 1,
}})


class TestParsing(unittest.TestCase):
    def test_both_plants_modes(self):
        # Fast charging, PV surplus, Sigen AI: the same codes on both plants.
        for code, name in ((0, "fast_charging"), (1, "solar_surplus"), (2, "sigen_ai")):
            self.assertEqual(api.parse_ac_charge_mode(mode_answer(code)).mode, name)
        self.assertIsNone(api.parse_ac_charge_mode(mode_answer(7)).mode)

    def test_battery_boost(self):
        # The second plant: boost to 70 %.
        mode = api.parse_ac_charge_mode(mode_answer(0, boost=True, cutoff=70.0))
        self.assertTrue(mode.boost)
        self.assertEqual(mode.boost_cutoff, 70)
        mode = api.parse_ac_charge_mode(mode_answer(2))
        self.assertFalse(mode.boost)
        self.assertEqual(mode.boost_cutoff, 10)

    def test_missing_and_odd_fields(self):
        for bad in (None, [], {}, {"chargeMode": None}, {"chargeMode": "x"}):
            self.assertIsNone(api.parse_ac_charge_mode(bad))
        odd = api.parse_ac_charge_mode({"chargeMode": 1, "enableFromPack": "yes",
                                        "cutoffSocFromPack": 140})
        self.assertIsNone(odd.boost)
        self.assertIsNone(odd.boost_cutoff)
        bare = api.parse_ac_charge_mode({"chargeMode": 1})
        self.assertEqual((bare.boost, bare.boost_cutoff), (None, None))
        as_int = api.parse_ac_charge_mode({"chargeMode": 1, "enableFromPack": 1})
        self.assertTrue(as_int.boost)

    def test_supported_modes(self):
        self.assertEqual(api.parse_ac_supported_modes([0, 1, 2]), frozenset({0, 1, 2}))
        self.assertEqual(api.parse_ac_supported_modes(["0", 2, 9, None]), frozenset({0, 2}))
        for bad in (None, {}, "x"):
            self.assertEqual(api.parse_ac_supported_modes(bad), frozenset())

    def test_session_energy(self):
        # Named a power, but kWh: it rose with the energy charged.
        self.assertEqual(api.parse_ac_session_energy({"singleChargingPower": 0.36}), 0.36)
        self.assertEqual(api.parse_ac_session_energy({"singleChargingPower": "35.4"}), 35.4)
        # Review finding: the app also reads a copy inside acChargeRealTimeVO.
        nested = {"chargingPower": 0.0, "acChargeRealTimeVO": {"singleChargingPower": 3.2}}
        self.assertEqual(api.parse_ac_session_energy(nested), 3.2)
        both = {"singleChargingPower": 0.22, "acChargeRealTimeVO": {"singleChargingPower": 9.9}}
        self.assertEqual(api.parse_ac_session_energy(both), 0.22)
        missing_top = {"singleChargingPower": None, "acChargeRealTimeVO": {"singleChargingPower": 1.5}}
        self.assertEqual(api.parse_ac_session_energy(missing_top), 1.5)
        self.assertIsNone(api.parse_ac_session_energy({"acChargeRealTimeVO": "x"}))
        for bad in (None, {}, {"singleChargingPower": None}, {"singleChargingPower": True},
                    {"singleChargingPower": -1}, {"singleChargingPower": "x"}):
            self.assertIsNone(api.parse_ac_session_energy(bad))

    def test_body_sends_the_settings_back_with_one_changed(self):
        current = api.parse_ac_charge_mode({**mode_answer(2, cutoff=70.0), "extra": 1})
        self.assertEqual(current.body(mode="solar_surplus"), {**mode_answer(1, cutoff=70.0)})
        self.assertEqual(current.body(boost=True), mode_answer(2, boost=True, cutoff=70.0))
        self.assertEqual(current.body(boost_cutoff=40), mode_answer(2, cutoff=40.0))
        as_int = api.parse_ac_charge_mode({"chargeMode": 0, "enableFromPack": 0})
        self.assertEqual(as_int.body(boost=True), {"chargeMode": 0, "enableFromPack": 1})


class TestRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_reads_go_with_station_and_charger(self):
        http = _ParamsHttp([
            STATION,
            _Response(200, {"code": 0, "data": mode_answer(2)}),
            _Response(200, {"code": 0, "data": [0, 1, 2]}),
            _Response(200, {"code": 0, "data": {"singleChargingPower": 0.22}}),
        ])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p")
        await client.async_station_id()
        self.assertEqual(client.ac_serial, "AC-1")
        self.assertEqual((await client.async_ac_charge_mode()).mode, "sigen_ai")
        self.assertEqual(await client.async_ac_supported_modes(), frozenset({0, 1, 2}))
        self.assertEqual(await client.async_ac_session_energy(), 0.22)
        self.assertEqual([entry[:3] for entry in http.sent[1:]], [
            ("GET", "ac", {"stationId": "42", "snCode": "AC-1"}),
            ("GET", "mode", {"stationId": "42", "snCode": "AC-1", "deviceType": "6"}),
            ("GET", "realtime", {"stationId": "42", "snCode": "AC-1"}),
        ])

    async def test_change_is_the_apps_post(self):
        ok = _Response(200, {"code": 0, "msg": "success", "data": True})
        http = _Http([STATION, ok])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        await client.async_station_id()
        await client.async_set_ac_charge_mode(mode_answer(1))
        self.assertEqual(http.requests[1:], [
            ("POST", "ac", {"stationId": 42, "snCode": "AC-1", **mode_answer(1)}),
        ])

    async def test_no_charger_sends_nothing(self):
        station = _Response(200, {"code": 0, "data": {"stationId": "42", "acSnList": []}})
        http = _Http([station])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        await client.async_station_id()
        with self.assertRaises(api.MySigenRejectedError):
            await client.async_set_ac_charge_mode(mode_answer(1))
        with self.assertRaises(api.MySigenConnectionError):
            await client.async_ac_charge_mode()
        self.assertEqual(len(http.requests), 1)

    async def test_a_refusal_and_a_lost_answer_are_not_sent_again(self):
        for reply, error in (
            (_Response(200, {"code": 1001, "msg": "fail", "data": None}), api.MySigenRejectedError),
            (TimeoutError(), api.MySigenUnknownOutcome),
        ):
            http = _Http([STATION, reply])
            client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
            await client.async_station_id()
            with self.assertRaises(error):
                await client.async_set_ac_charge_mode(mode_answer(1))
            self.assertEqual(len(http.requests), 2)


class _AcSession(_Session):
    async def async_ac_charge_mode(self):
        answer = self._next("ac")
        return api.parse_ac_charge_mode(answer) if isinstance(answer, dict) else answer

    async def async_set_ac_charge_mode(self, body):
        await self._write("ac", body)


class _AcCoordinator(_Coordinator):
    def __init__(
        self, answers, has_ac=True, supported=frozenset({0, 1, 2}), soc_range=None, **kwargs
    ):
        super().__init__({}, **kwargs)
        self.session = _AcSession({"ac": answers}, kwargs.get("write_error"))
        self.has_ac_charger = has_ac
        self.ac_supported_modes = supported
        self.soc_range = soc_range

    def publish_ac_charge_mode(self, value):
        self.published.append(value.raw)


class TestRules(unittest.IsolatedAsyncioTestCase):
    async def test_mode_sent_once_and_confirmed(self):
        coordinator = _AcCoordinator([mode_answer(2), mode_answer(1)])
        result = await control.async_set_ac_charge_mode(
            coordinator, mode="solar_surplus", sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.writes, [("ac", mode_answer(1))])
        self.assertEqual(coordinator.settings, [("ac_charge_mode", "confirmed")])
        self.assertEqual(coordinator.published, [mode_answer(1)])

    async def test_nothing_is_sent_when_already_set(self):
        coordinator = _AcCoordinator([mode_answer(1)])
        await control.async_set_ac_charge_mode(coordinator, mode="solar_surplus", sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_a_mode_the_charger_does_not_offer_is_refused(self):
        coordinator = _AcCoordinator([mode_answer(0)], supported=frozenset({0, 1}))
        with self.assertRaisesRegex(control.CommandRefused, "not available"):
            await control.async_set_ac_charge_mode(coordinator, mode="sigen_ai", sleep=_no_sleep)
        with self.assertRaisesRegex(control.CommandRefused, "not available"):
            await control.async_set_ac_charge_mode(coordinator, mode="bidirectional", sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])
        # Before the list is read, all three are offered.
        coordinator = _AcCoordinator([mode_answer(0), mode_answer(2)], supported=frozenset())
        await control.async_set_ac_charge_mode(coordinator, mode="sigen_ai", sleep=_no_sleep)
        self.assertEqual(len(coordinator.session.writes), 1)

    async def test_boost_on_and_off(self):
        coordinator = _AcCoordinator([mode_answer(0), mode_answer(0, boost=True)])
        await control.async_set_ac_battery_boost(coordinator, enabled=True, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("ac", mode_answer(0, boost=True))])
        coordinator = _AcCoordinator([{"chargeMode": 0}])
        with self.assertRaisesRegex(control.CommandRefused, "did not report"):
            await control.async_set_ac_battery_boost(coordinator, enabled=True, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_cutoff_within_range(self):
        coordinator = _AcCoordinator([mode_answer(0, cutoff=70.0), mode_answer(0, cutoff=40.0)])
        await control.async_set_ac_boost_cutoff(coordinator, level=40, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("ac", mode_answer(0, cutoff=40.0))])
        # The station's level range limits it, as in the app; nothing is read first.
        coordinator = _AcCoordinator([mode_answer(0, cutoff=70.0)], soc_range=(20, 90))
        with self.assertRaisesRegex(control.CommandRefused, "20 to 90"):
            await control.async_set_ac_boost_cutoff(coordinator, level=10, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])
        # Limits in the mode answer are not used: the app ignores them.
        coordinator = _AcCoordinator([
            {**mode_answer(0, cutoff=70.0), "socLowerLimit": 20, "socUpperLimit": 90},
            {**mode_answer(0, cutoff=10.0), "socLowerLimit": 20, "socUpperLimit": 90},
        ])
        await control.async_set_ac_boost_cutoff(coordinator, level=10, sleep=_no_sleep)
        self.assertEqual(len(coordinator.session.writes), 1)

    async def test_a_lost_answer_is_unknown_and_not_sent_again(self):
        coordinator = _AcCoordinator(
            [mode_answer(2)], write_error=api.MySigenUnknownOutcome("set AC charging mode: TimeoutError"))
        with self.assertRaises(control.CommandUnknown):
            await control.async_set_ac_charge_mode(coordinator, mode="fast_charging", sleep=_no_sleep)
        self.assertEqual(len(coordinator.session.writes), 1)
        self.assertEqual(coordinator.settings, [("ac_charge_mode", "unknown")])

    async def test_only_counts_when_mysigen_shows_it(self):
        # The fresh read, then three read-backs: a charger confirms late.
        coordinator = _AcCoordinator([mode_answer(2)] * 4)
        with self.assertRaises(control.CommandNotConfirmed):
            await control.async_set_ac_charge_mode(coordinator, mode="fast_charging", sleep=_no_sleep)
        self.assertEqual(coordinator.session.reads["ac"], [])

    async def test_a_late_charger_is_still_confirmed(self):
        waits = []

        async def sleep(seconds):
            waits.append(seconds)

        coordinator = _AcCoordinator([mode_answer(2), mode_answer(2), mode_answer(2), mode_answer(0)])
        result = await control.async_set_ac_charge_mode(coordinator, mode="fast_charging", sleep=sleep)
        self.assertEqual(result, "confirmed")
        # Waits before every read-back, the first included.
        self.assertEqual(waits, [3.0, 3.0, 3.0])

    async def test_refused_without_control_or_charger(self):
        coordinator = _AcCoordinator([mode_answer(2)], allow=False)
        with self.assertRaisesRegex(control.CommandRefused, "Control is switched off"):
            await control.async_set_ac_charge_mode(coordinator, mode="fast_charging", sleep=_no_sleep)
        coordinator = _AcCoordinator([mode_answer(2)], has_ac=False)
        with self.assertRaisesRegex(control.CommandRefused, "no AC charger"):
            await control.async_set_ac_battery_boost(coordinator, enabled=True, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_remote_ems_is_not_checked(self):
        # Like the DC charger: these settings exist only in mySigen.
        for change in (control.async_set_ac_charge_mode, control.async_set_ac_battery_boost,
                       control.async_set_ac_boost_cutoff):
            self.assertNotIn("remote_ems", change.__code__.co_varnames)


if __name__ == "__main__":
    unittest.main()
