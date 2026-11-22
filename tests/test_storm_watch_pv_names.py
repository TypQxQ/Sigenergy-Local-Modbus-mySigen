"""Storm Watch, Sigen AI settings and PV string names (0.1.1).

The answers without alerts and names are the ones the development plant
gave, and the vacation mode request is the one captured from the mySigen
web app, with the station id replaced. Alert fields come from the mySigen web
app's code: no plant with an alert has been read yet.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from test_api_login import _Response, api
from test_control import _Http, control
from test_settings import _Coordinator, _Session, _no_sleep
from test_smart_loads import _ParamsHttp

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/sigen_mysigen"
SPEC = importlib.util.spec_from_file_location("sigen_mysigen_pv_names", COMPONENT / "pv_names.py")
pv_names = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pv_names
SPEC.loader.exec_module(pv_names)

# As the development plant answered (Sweden: Storm Watch not shown, off).
AI_SETTINGS = {
    "aiPeakShaving": {"enabled": False, "preferenceCode": "BALANCED",
                      "preferenceSettingSupported": False, "preferenceType": 1},
    "aiPeakShavingEnable": False, "holidayModeEnable": False,
    "showHolidayMode": True, "strategyMode": 2,
}
STORM_OFF = {
    "alertEvents": [], "chargeFull": False, "extremeWeatherEnumTypeVOList": [],
    "weatherAlertSwitch": 0, "weatherForecastAgency": "",
}
PV_NAMES_EMPTY = {
    "mpptNumber": 3, "pv1Name": "", "pv2Name": "", "pv3Name": "", "pv4Name": "",
    "stationId": None,
}


def ts(year, month, day, hour=0):
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp())


STORM_ALERT = {
    "weatherAlertSwitch": 1, "chargeFull": True, "weatherForecastAgency": "BoM",
    "extremeWeatherEnumTypeVOList": [
        {"code": 3, "weatherName": "Extreme heat"}, {"code": "4", "weatherName": " Storm "},
        {"code": 0, "weatherName": "Nothing"}, {"code": 5}, None,
    ],
    "alertEvents": [
        {"eventId": "e1", "chargingStartTime": ts(2026, 1, 10), "onsetTime": ts(2026, 1, 10, 6),
         "endTime": ts(2026, 1, 12), "eventType": 3},
        {"eventId": "e2", "chargingStartTime": 0, "onsetTime": 0, "endTime": 0, "eventType": 9},
        "not an event",
    ],
}


class TestAiSettings(unittest.TestCase):
    def test_real_answer(self):
        self.assertEqual(api.parse_ai_settings(AI_SETTINGS), api.AiSettings(False, True, 2))
        self.assertEqual(
            api.parse_ai_settings({**AI_SETTINGS, "holidayModeEnable": True, "strategyMode": 0}),
            api.AiSettings(True, True, 0),
        )

    def test_the_app_names_the_strategies(self):
        self.assertEqual(api.AI_STRATEGIES, {
            "profit_max": 2, "balanced_energy": 1, "self_use_first": 0,
        })

    def test_missing_and_odd_fields(self):
        # Shown unless mySigen says it is not; on or off only when it says so;
        # a strategy only when it is one of the three.
        self.assertEqual(api.parse_ai_settings({}), api.AiSettings(None, True, None))
        self.assertEqual(
            api.parse_ai_settings(
                {"holidayModeEnable": 1, "showHolidayMode": False, "strategyMode": 3}),
            api.AiSettings(None, False, None),
        )
        for strategy, expected in (("1", 1), (None, None), (True, None), (-1, None), ("x", None)):
            self.assertEqual(
                api.parse_ai_settings({"strategyMode": strategy}).strategy, expected, strategy)
        for bad in (None, [], "x", 2):
            self.assertIsNone(api.parse_ai_settings(bad))


class TestStormWatch(unittest.TestCase):
    def test_real_answer_without_alerts(self):
        watch = api.parse_storm_watch(STORM_OFF)
        self.assertEqual(watch, api.StormWatch(False, (), False))
        self.assertEqual(watch.current(datetime.now(timezone.utc)), ())

    def test_alerts(self):
        watch = api.parse_storm_watch(STORM_ALERT)
        self.assertTrue(watch.enabled)
        self.assertTrue(watch.charged_full)
        first, second = watch.alerts
        self.assertEqual(first.weather, "Extreme heat")
        self.assertEqual(first.charging_start, datetime(2026, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(first.onset, datetime(2026, 1, 10, 6, tzinfo=timezone.utc))
        self.assertEqual(first.end, datetime(2026, 1, 12, tzinfo=timezone.utc))
        # Zero times mean none; an unknown type has no name.
        self.assertEqual(second, api.StormAlert(9, None, None, None, None))

    def test_an_alert_counts_until_it_ends(self):
        watch = api.parse_storm_watch(STORM_ALERT)
        during = datetime(2026, 1, 11, tzinfo=timezone.utc)
        after = datetime(2026, 1, 13, tzinfo=timezone.utc)
        self.assertEqual(len(watch.current(during)), 2)
        # The first has ended; the one without an end still counts.
        self.assertEqual([a.event_type for a in watch.current(after)], [9])
        only_first = api.parse_storm_watch({**STORM_ALERT, "alertEvents": STORM_ALERT["alertEvents"][:1]})
        self.assertEqual(only_first.current(after), ())

    def test_missing_and_empty_fields(self):
        self.assertEqual(api.parse_storm_watch({}), api.StormWatch(None, (), None))
        odd = api.parse_storm_watch({
            "weatherAlertSwitch": 7, "chargeFull": "yes", "alertEvents": {"x": 1},
            "extremeWeatherEnumTypeVOList": "none",
        })
        self.assertEqual(odd, api.StormWatch(None, (), None))
        alert = api.parse_storm_watch({"alertEvents": [{"endTime": "x", "eventType": None}]})
        self.assertEqual(alert.alerts, (api.StormAlert(None, None, None, None, None),))
        for bad in (None, [], "x"):
            self.assertIsNone(api.parse_storm_watch(bad))


class TestPvNames(unittest.TestCase):
    def test_real_answer_without_names(self):
        self.assertEqual(api.parse_pv_names(PV_NAMES_EMPTY), {})

    def test_names(self):
        names = api.parse_pv_names({
            **PV_NAMES_EMPTY, "pv1Name": " Rear  roof ", "pv3Name": "Front",
            # Past the inverter's string count, too long, not text.
            "pv4Name": "Ghost", "pv2Name": 5,
        })
        self.assertEqual(names, {1: "Rear roof", 3: "Front"})
        self.assertEqual(
            api.parse_pv_names({"pv1Name": "x" * 30, "pv6Name": "Six", "pv0Name": "Zero"}),
            {1: "x" * 20, 6: "Six"},
        )
        for bad in (None, [], "x"):
            self.assertIsNone(api.parse_pv_names(bad))


class TestRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_reads_go_with_the_station(self):
        station = _Response(200, {"code": 0, "data": {"stationId": "42", "displayStormWatch": True}})
        http = _ParamsHttp([
            station,
            _Response(200, {"code": 0, "data": AI_SETTINGS}),
            _Response(200, {"code": 0, "data": STORM_OFF}),
            _Response(200, {"code": 0, "data": PV_NAMES_EMPTY}),
        ])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p")
        self.assertFalse(client.storm_watch_shown)
        await client.async_station_id()
        self.assertTrue(client.storm_watch_shown)
        self.assertFalse((await client.async_ai_settings()).vacation)
        self.assertFalse((await client.async_storm_watch()).enabled)
        self.assertEqual(await client.async_pv_names("AIO-1"), {})
        self.assertEqual([entry[:3] for entry in http.sent[1:]], [
            ("GET", "aiSettingsInfo", {"stationId": "42"}),
            ("GET", "query", {"stationId": "42"}),
            ("GET", "42", {"snCode": "AIO-1"}),
        ])

    async def test_storm_watch_is_not_shown_unless_mySigen_says_so(self):
        for value in (False, None, "true", 1):
            station = _Response(200, {"code": 0, "data": {"stationId": "42", "displayStormWatch": value}})
            client = api.MySigenSession(_ParamsHttp([station]), "eu", "u@example.invalid", "p")
            await client.async_station_id()
            self.assertFalse(client.storm_watch_shown)


OFF, ON = api.AiSettings(False, True, 2), api.AiSettings(True, True, 2)


class _VacationSession(_Session):
    async def async_operating_mode(self):
        self.mode_reads = getattr(self, "mode_reads", 0) + 1
        return self._next("mode")

    async def async_ai_settings(self):
        return self._next("vacation")

    async def async_set_vacation_mode(self, enabled):
        await self._write("vacation", enabled)

    async def async_set_ai_strategy(self, code):
        await self._write("strategy", code)


class _VacationCoordinator(_Coordinator):
    def __init__(self, answers, mode=1, **kwargs):
        super().__init__({}, **kwargs)
        self.session = _VacationSession(
            {"vacation": answers, "mode": [mode]}, kwargs.get("write_error"))
        self.modes = []

    def publish_ai_settings(self, value):
        self.published.append(value)

    def publish_operating_mode(self, value):
        self.modes.append(value)


class TestVacationRules(unittest.IsolatedAsyncioTestCase):
    async def _set(self, coordinator, enabled=True):
        return await control.async_set_vacation_mode(coordinator, enabled=enabled, sleep=_no_sleep)

    async def test_sent_once_and_confirmed(self):
        coordinator = _VacationCoordinator([OFF, ON])
        self.assertEqual(await self._set(coordinator), "confirmed")
        self.assertEqual(coordinator.session.writes, [("vacation", True)])
        self.assertEqual(coordinator.settings, [("vacation_mode", "confirmed")])
        self.assertEqual(coordinator.published, [ON])
        # The mode is read once, first, and shown; read-backs skip it.
        self.assertEqual((coordinator.session.mode_reads, coordinator.modes), (1, [1]))
        coordinator = _VacationCoordinator([ON, OFF])
        await self._set(coordinator, enabled=False)
        self.assertEqual(coordinator.session.writes, [("vacation", False)])

    async def test_nothing_is_sent_when_already_set(self):
        coordinator = _VacationCoordinator([ON])
        self.assertEqual(await self._set(coordinator), "confirmed")
        self.assertEqual(coordinator.session.writes, [])

    async def test_refused_when_not_offered_or_not_known(self):
        for answer, text in (
            (api.AiSettings(False, False, 2), "does not offer"),
            # Also when it would already look set.
            (api.AiSettings(True, False, 2), "does not offer"),
            (api.AiSettings(None, True, 2), "did not report"),
            (None, "did not report the current setting"),
        ):
            coordinator = _VacationCoordinator([answer])
            with self.assertRaisesRegex(control.CommandRefused, text):
                await self._set(coordinator)
            self.assertEqual(coordinator.session.writes, [])
            self.assertEqual(coordinator.settings, [("vacation_mode", "refused")])

    async def test_a_lost_answer_is_unknown_and_not_sent_again(self):
        coordinator = _VacationCoordinator(
            [OFF], write_error=api.MySigenUnknownOutcome("set vacation mode: TimeoutError"))
        with self.assertRaises(control.CommandUnknown):
            await self._set(coordinator)
        self.assertEqual(coordinator.session.writes, [("vacation", True)])
        self.assertEqual(coordinator.settings, [("vacation_mode", "unknown")])

    async def test_only_counts_when_mysigen_shows_it(self):
        coordinator = _VacationCoordinator([OFF, OFF, OFF])
        with self.assertRaises(control.CommandNotConfirmed):
            await self._set(coordinator)
        self.assertEqual(coordinator.session.writes, [("vacation", True)])

    async def test_refused_without_control(self):
        coordinator = _VacationCoordinator([OFF], allow=False)
        with self.assertRaisesRegex(control.CommandRefused, "Control is switched off"):
            await self._set(coordinator)
        self.assertEqual(coordinator.session.writes, [])

    async def test_only_in_sigen_ai_mode(self):
        # Self-consumption, time of use, Remote EMS, unknown. Refused also
        # when it already shows the requested state; vacation is not read.
        for mode in (0, 2, 7, None):
            for answer, enabled in ((OFF, True), (ON, True), (ON, False)):
                coordinator = _VacationCoordinator([answer], mode=mode)
                with self.assertRaisesRegex(control.CommandRefused, "only available in Sigen AI"):
                    await self._set(coordinator, enabled=enabled)
                self.assertEqual(coordinator.session.writes, [])
                self.assertEqual(coordinator.session.reads["vacation"], [answer])
                self.assertEqual(coordinator.settings, [("vacation_mode", "refused")])
                self.assertEqual(coordinator.modes, [] if mode is None else [mode])

    async def test_a_failed_mode_read_sends_nothing(self):
        for error in (api.MySigenConnectionError("x"), api.MySigenRateLimitError(None)):
            coordinator = _VacationCoordinator([OFF], mode=error)
            with self.assertRaisesRegex(control.CommandRefused, "Nothing was sent"):
                await self._set(coordinator)
            self.assertEqual(coordinator.session.writes, [])


def strategy(code, vacation=False):
    return api.AiSettings(vacation, True, code)


class TestStrategyRules(unittest.IsolatedAsyncioTestCase):
    async def _set(self, coordinator, option="balanced_energy"):
        return await control.async_set_ai_strategy(coordinator, strategy=option, sleep=_no_sleep)

    async def test_sent_once_and_confirmed(self):
        for option, code in api.AI_STRATEGIES.items():
            before = 0 if code else 2
            coordinator = _VacationCoordinator([strategy(before), strategy(code)])
            self.assertEqual(await self._set(coordinator, option), "confirmed")
            self.assertEqual(coordinator.session.writes, [("strategy", code)])
            self.assertEqual(coordinator.settings, [("ai_strategy", "confirmed")])
            self.assertEqual(coordinator.published, [strategy(code)])
            self.assertEqual(coordinator.session.mode_reads, 1)

    async def test_nothing_is_sent_when_already_set(self):
        coordinator = _VacationCoordinator([strategy(1)])
        self.assertEqual(await self._set(coordinator), "confirmed")
        self.assertEqual(coordinator.session.writes, [])

    async def test_vacation_mode_does_not_matter(self):
        coordinator = _VacationCoordinator([strategy(2, True), strategy(1, True)])
        self.assertEqual(await self._set(coordinator), "confirmed")

    async def test_refused_when_unknown(self):
        for answer, text in (
            (strategy(None), "did not report the current strategy"),
            (None, "did not report the current setting"),
        ):
            coordinator = _VacationCoordinator([answer])
            with self.assertRaisesRegex(control.CommandRefused, text):
                await self._set(coordinator)
            self.assertEqual(coordinator.session.writes, [])
        coordinator = _VacationCoordinator([strategy(2)])
        with self.assertRaisesRegex(control.CommandRefused, "not available"):
            await self._set(coordinator, "max_self_consumption")
        self.assertEqual((coordinator.session.writes, coordinator.settings), ([], []))

    async def test_only_in_sigen_ai_mode(self):
        for mode in (0, 2, 7, None):
            for answer in (strategy(2), strategy(1)):
                coordinator = _VacationCoordinator([answer], mode=mode)
                with self.assertRaisesRegex(control.CommandRefused, "only available in Sigen AI"):
                    await self._set(coordinator)
                self.assertEqual(coordinator.session.writes, [])
                self.assertEqual(coordinator.settings, [("ai_strategy", "refused")])

    async def test_a_lost_answer_is_unknown_and_not_sent_again(self):
        coordinator = _VacationCoordinator(
            [strategy(2)], write_error=api.MySigenUnknownOutcome("set AI strategy: TimeoutError"))
        with self.assertRaises(control.CommandUnknown):
            await self._set(coordinator)
        self.assertEqual(coordinator.session.writes, [("strategy", 1)])
        self.assertEqual(coordinator.settings, [("ai_strategy", "unknown")])

    async def test_only_counts_when_mysigen_shows_it(self):
        coordinator = _VacationCoordinator([strategy(2), strategy(2), strategy(2)])
        with self.assertRaises(control.CommandNotConfirmed):
            await self._set(coordinator)
        self.assertEqual(coordinator.session.writes, [("strategy", 1)])

    async def test_refused_without_control(self):
        coordinator = _VacationCoordinator([strategy(2)], allow=False)
        with self.assertRaisesRegex(control.CommandRefused, "Control is switched off"):
            await self._set(coordinator)
        self.assertEqual(coordinator.session.writes, [])


class TestVacationRequest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_the_app_request(self):
        ok = _Response(200, {"code": 0, "msg": "success", "data": True})
        http = _Http([_Response(200, {"code": 0, "data": {"stationId": "42"}}), ok, ok])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        await client.async_set_vacation_mode(True)
        await client.async_set_vacation_mode(False)
        self.assertEqual(http.requests[1:], [
            ("POST", "strategyMode", {"stationId": 42, "strategyMode": None,
                                      "enableAiPeakShaving": None, "enableHolidayMode": True}),
            ("POST", "strategyMode", {"stationId": 42, "strategyMode": None,
                                      "enableAiPeakShaving": None, "enableHolidayMode": False}),
        ])

    async def test_the_app_strategy_request(self):
        # As captured: 1, 0, then back to 2.
        ok = _Response(200, {"code": 0, "msg": "success", "data": True})
        http = _Http([_Response(200, {"code": 0, "data": {"stationId": "42"}}), ok, ok, ok])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
        for code in (1, 0, 2):
            await client.async_set_ai_strategy(code)
        self.assertEqual(http.requests[1:], [
            ("POST", "strategyMode", {"stationId": 42, "strategyMode": code,
                                      "enableAiPeakShaving": None, "enableHolidayMode": None})
            for code in (1, 0, 2)
        ])
        with self.assertRaises(ValueError):
            await client.async_set_ai_strategy(3)
        self.assertEqual(len(http.requests), 4)

    async def test_a_refusal_and_a_lost_answer_are_not_sent_again(self):
        station = _Response(200, {"code": 0, "data": {"stationId": "42"}})
        for reply, error in (
            (_Response(200, {"code": 1001, "msg": "fail", "data": None}), api.MySigenRejectedError),
            (TimeoutError(), api.MySigenUnknownOutcome),
        ):
            http = _Http([station, reply])
            client = api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)
            with self.assertRaises(error):
                await client.async_set_vacation_mode(True)
            self.assertEqual(len(http.requests), 2)


def device(device_id, number=1, name=None, serial="AIO-1"):
    return pv_names.PvStringDevice(device_id, serial, number, name)


class TestPvNameRules(unittest.TestCase):
    def plan(self, devices, names, applied=None):
        return pv_names.plan_pv_names(devices, names, applied or {})

    def test_a_default_name_takes_the_app_name(self):
        updates, applied = self.plan([device("d1"), device("d2", 2)], {"AIO-1": {1: "Rear roof"}})
        self.assertEqual(updates, {"d1": "Rear roof"})
        self.assertEqual(applied, {"d1": "Rear roof"})

    def test_a_name_the_user_gave_is_never_changed(self):
        updates, applied = self.plan([device("d1", name="Garage")], {"AIO-1": {1: "Rear roof"}})
        self.assertEqual(updates, {})
        # Remembered, so it is not offered again until the app name changes.
        self.assertEqual(applied, {"d1": "Rear roof"})
        updates, _ = self.plan(
            [device("d1", name="Garage")], {"AIO-1": {1: "Back"}}, {"d1": "Rear roof"})
        self.assertEqual(updates, {})

    def test_the_add_on_follows_a_changed_app_name(self):
        updates, applied = self.plan(
            [device("d1", name="Rear roof")], {"AIO-1": {1: "Back roof"}}, {"d1": "Rear roof"})
        self.assertEqual(updates, {"d1": "Back roof"})
        self.assertEqual(applied, {"d1": "Back roof"})

    def test_a_cleared_name_stays_default_until_the_app_name_changes(self):
        devices = [device("d1", name=None)]
        updates, applied = self.plan(devices, {"AIO-1": {1: "Rear roof"}}, {"d1": "Rear roof"})
        self.assertEqual((updates, applied), ({}, {"d1": "Rear roof"}))
        updates, _ = self.plan(devices, {"AIO-1": {1: "Back roof"}}, {"d1": "Rear roof"})
        self.assertEqual(updates, {"d1": "Back roof"})

    def test_a_name_removed_in_the_app_restores_the_default(self):
        updates, applied = self.plan(
            [device("d1", name="Rear roof")], {"AIO-1": {}}, {"d1": "Rear roof"})
        self.assertEqual((updates, applied), ({"d1": None}, {}))
        # Not when the user has renamed it since.
        updates, applied = self.plan(
            [device("d1", name="Garage")], {"AIO-1": {}}, {"d1": "Rear roof"})
        self.assertEqual((updates, applied), ({}, {}))

    def test_an_inverter_that_was_not_read_is_left_alone(self):
        updates, applied = self.plan(
            [device("d1", name="Rear roof")], {}, {"d1": "Rear roof"})
        self.assertEqual((updates, applied), ({}, {"d1": "Rear roof"}))

    def test_an_already_matching_name_is_only_remembered(self):
        # For example after the store was lost.
        updates, applied = self.plan([device("d1", name="Rear roof")], {"AIO-1": {1: "Rear roof"}})
        self.assertEqual((updates, applied), ({}, {"d1": "Rear roof"}))

    def test_strings_are_matched_by_inverter_and_number(self):
        devices = [device("a1", 1, serial="A"), device("b1", 1, serial="B"), device("b2", 2, serial="B")]
        updates, _ = self.plan(devices, {"A": {1: "East"}, "B": {2: "West"}})
        self.assertEqual(updates, {"a1": "East", "b2": "West"})

    def test_devices_that_are_gone_are_forgotten(self):
        _, applied = self.plan([device("d1")], {"AIO-1": {1: "Rear roof"}}, {"old": "Front"})
        self.assertEqual(applied, {"d1": "Rear roof"})

    def test_string_number_from_the_identifier(self):
        number = pv_names.pv_string_number
        self.assertEqual(number("E1_sigen_inverter_pv2", "E1"), 2)
        self.assertEqual(number("E1_sigen_inverter_pv12", "E1"), 12)
        self.assertIsNone(number("E2_sigen_inverter_pv2", "E1"))
        self.assertIsNone(number("E1_sigen_inverter", "E1"))
        self.assertIsNone(number("E1_sigen_inverter_dc_charger", "E1"))


if __name__ == "__main__":
    unittest.main()
