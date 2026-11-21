"""Settings that stay until changed: operating mode, battery export, peak shaving."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from test_api_login import _Response, api
from test_control import STATION, _Http, control

# Shapes as the dev plant returned them (values not personal).
MODES = {"defaultWorkingModes": [
    {"label": "Maximum Self-Powered", "value": "0"}, {"label": "Sigen AI Mode", "value": "1"},
    {"label": "TOU", "value": "2"}, {"label": "Fully Fed to Grid", "value": "5"},
    {"label": "Remote EMS Mode", "value": "7"}, {"label": "Custom Operation Mode", "value": "9"},
], "energyProfileItems": []}
EXPORT = {"currentEnable": False, "ownerSetEnable": False, "installerSetEnable": None,
          "nearModify": False}
# A plant with an installer limit and no owner limit (shape as in sigenergy2mqtt).
GRID_CURRENT = {"enable": True, "currentLimitation": "25.0", "ownerSetLimitation": "",
                "installerSetLimitation": "25.0"}
# As the development plant answered: no limit set at all.
NO_GRID_CURRENT = {"enable": False, "currentLimitation": "", "ownerSetLimitation": "",
                   "installerSetLimitation": ""}
PEAK = {"controlMode": 1, "shavingSOC": 15.0, "settingList": [
    {"whichDay": "1,2,3,4,5,6,7", "startTime": "00:00", "endTime": "24:00", "peakPower": 13.0}]}


class TestParsing(unittest.TestCase):
    def test_modes_and_current_mode(self):
        self.assertEqual(api.parse_available_modes(MODES), frozenset({0, 1, 2, 5, 7, 9}))
        self.assertEqual(api.parse_operating_mode({"currentMode": 1, "currentProfileId": -1}), 1)
        for bad in (None, {}, {"currentMode": None}, {"currentMode": True}, {"currentMode": "x"}):
            self.assertIsNone(api.parse_operating_mode(bad))
        self.assertEqual(api.parse_available_modes({"defaultWorkingModes": "x"}), frozenset())

    def test_battery_export(self):
        # mySigen stores a limitation: false means export is allowed.
        export = api.parse_battery_export(EXPORT)
        self.assertEqual((export.allowed, export.allowed_in_effect, export.installer),
                         (True, True, None))
        blocked = api.parse_battery_export({**EXPORT, "ownerSetEnable": True, "currentEnable": True})
        self.assertEqual((blocked.allowed, blocked.allowed_in_effect), (False, False))
        unknown = api.parse_battery_export({**EXPORT, "currentEnable": None})
        self.assertIsNone(unknown.allowed_in_effect)
        for bad in (None, {}, {"ownerSetEnable": None}, {"ownerSetEnable": "true"}):
            self.assertIsNone(api.parse_battery_export(bad))

    def test_grid_current_limit(self):
        limit = api.parse_grid_current_limit(GRID_CURRENT)
        self.assertEqual(
            (limit.enabled, limit.effective, limit.owner, limit.installer),
            (True, 25.0, None, 25.0),
        )
        owner = api.parse_grid_current_limit(
            {**GRID_CURRENT, "currentLimitation": 16, "ownerSetLimitation": "16.0"})
        self.assertEqual((owner.effective, owner.owner), (16.0, 16.0))
        # The app's "no owner limit" value is no limit, not 2 million amperes.
        none = api.parse_grid_current_limit({**GRID_CURRENT, "ownerSetLimitation": "2147483.647"})
        self.assertIsNone(none.owner)
        # No limit set is an answer too, not a missing one.
        unset = api.parse_grid_current_limit(NO_GRID_CURRENT)
        self.assertEqual(
            (unset.enabled, unset.effective, unset.owner, unset.installer),
            (False, None, None, None),
        )
        # Only a complete, readable answer counts; a missing or odd field
        # must not pass for "no installer limit".
        incomplete = {key: value for key, value in GRID_CURRENT.items()
                      if key != "installerSetLimitation"}
        for bad in (
            None, {}, [], {"other": 1}, {"enable": True}, incomplete,
            {**GRID_CURRENT, "enable": None},
            {**GRID_CURRENT, "installerSetLimitation": "x"},
            {**GRID_CURRENT, "installerSetLimitation": True},
            {**GRID_CURRENT, "currentLimitation": -1},
            {**GRID_CURRENT, "ownerSetLimitation": "0"},
        ):
            self.assertIsNone(api.parse_grid_current_limit(bad), bad)

    def test_peak_shaving(self):
        peak = api.parse_peak_shaving(PEAK)
        self.assertEqual((peak.enabled, len(peak.slots), peak.single_limit), (True, 1, 13.0))
        self.assertEqual(peak.with_enabled(False)["controlMode"], 0)
        changed = peak.with_limit(9.25)
        self.assertEqual(changed["settingList"][0]["peakPower"], 9.2)
        # Everything else is written back as it was read.
        self.assertEqual(changed["shavingSOC"], 15.0)
        self.assertEqual(changed["settingList"][0]["whichDay"], "1,2,3,4,5,6,7")
        self.assertEqual(PEAK["settingList"][0]["peakPower"], 13.0)
        two = api.parse_peak_shaving({**PEAK, "settingList": PEAK["settingList"] * 2})
        self.assertIsNone(two.single_limit)
        for bad in (None, {}, {"controlMode": 3}):
            self.assertIsNone(api.parse_peak_shaving(bad))


class TestWrites(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self, *replies):
        http = _Http([STATION, *replies])
        return http, api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)

    async def test_bodies(self):
        ok = _Response(200, {"code": 0, "data": True})
        http, client = self._client(ok, ok, ok, ok)
        await client.async_set_operating_mode(0)
        await client.async_set_battery_export(api.parse_battery_export(
            {**EXPORT, "installerSetEnable": True}), False)
        await client.async_set_peak_shaving(api.parse_peak_shaving(PEAK).with_enabled(False))
        self.assertEqual(http.requests[1], ("PUT", "mode", {
            "stationId": 42, "operationMode": 0, "workingMode": 0, "profileId": -1}))
        # The installer's setting is sent back as it was read.
        self.assertEqual(http.requests[2], ("PUT", "limitation", {
            "stationId": 42, "installerSetEnable": True, "ownerSetEnable": True}))
        method, last, body = http.requests[3]
        self.assertEqual((method, last, body["controlMode"], body["stationId"]),
                         ("POST", "shaving", 0, 42))
        self.assertEqual(body["settingList"], PEAK["settingList"])
        # The installer's ceiling is never sent; the owner's value is, with
        # one decimal, as the app does.
        await client.async_set_grid_current_limit(16)
        self.assertEqual(http.requests[4], ("PUT", "grid", {
            "stationId": 42, "enable": True, "ownerSetLimitation": "16.0",
            "installerSetLimitation": None}))

    async def test_remote_ems_and_custom_modes_cannot_be_sent(self):
        http, client = self._client()
        for code in (7, 9, 3):
            with self.assertRaises(ValueError):
                await client.async_set_operating_mode(code)
        self.assertEqual(http.requests, [])


class _Session:
    def __init__(self, reads, write_error=None):
        self.reads, self.write_error, self.writes = dict(reads), write_error, []

    def _next(self, name):
        answer = self.reads[name].pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def async_operating_mode(self):
        return self._next("mode")

    async def async_battery_export(self):
        return self._next("export")

    async def async_peak_shaving(self):
        return self._next("peak")

    async def async_grid_current_limit(self):
        return self._next("current")

    async def async_set_grid_current_limit(self, amps):
        await self._write("current", amps)

    async def _write(self, *entry):
        self.writes.append(entry)
        if self.write_error:
            raise self.write_error

    async def async_set_operating_mode(self, code):
        await self._write("mode", code)

    async def async_set_battery_export(self, current, allowed):
        await self._write("export", current.installer, allowed)

    async def async_set_peak_shaving(self, schedule):
        await self._write("peak", schedule["controlMode"], schedule["settingList"][0]["peakPower"])


class _Coordinator:
    def __init__(self, reads, write_error=None, allow=True, verified=True, modes=None):
        self.session = _Session(reads, write_error)
        self.allow_control, self.link_verified, self.rate_limit_wait = allow, verified, 0.0
        self.available_modes = frozenset({0, 1, 2, 5, 7, 9} if modes is None else modes)
        self.command_lock = asyncio.Lock()
        self.settings, self.published = [], []

    def record_setting(self, action, result):
        self.settings.append((action, result))

    def publish_operating_mode(self, value):
        self.published.append(value)

    publish_battery_export = publish_peak_shaving = publish_operating_mode
    publish_grid_current_limit = publish_operating_mode

    def note_rate_limit(self, err):
        return 30.0


async def _no_sleep(_seconds):
    return None


# Named for what the switch shows: export allowed or blocked.
ALLOWED_EXPORT = api.parse_battery_export(EXPORT)
BLOCKED_EXPORT = api.parse_battery_export({**EXPORT, "ownerSetEnable": True})
PEAK_ON = api.parse_peak_shaving(PEAK)
PEAK_OFF = api.parse_peak_shaving({**PEAK, "controlMode": 0})


class TestRules(unittest.IsolatedAsyncioTestCase):
    async def _mode(self, coordinator, mode="max_self_consumption", remote_ems="off"):
        return await control.async_set_operating_mode(
            coordinator, mode=mode, remote_ems=lambda: remote_ems, sleep=_no_sleep)

    async def test_mode_change_is_sent_once_and_confirmed(self):
        coordinator = _Coordinator({"mode": [1, 0]})
        self.assertEqual(await self._mode(coordinator), "confirmed")
        self.assertEqual(coordinator.session.writes, [("mode", 0)])
        self.assertEqual(coordinator.settings, [("operating_mode", "confirmed")])

    async def test_nothing_is_sent_when_already_set(self):
        coordinator = _Coordinator({"mode": [0]})
        self.assertEqual(await self._mode(coordinator), "confirmed")
        self.assertEqual(coordinator.session.writes, [])

    async def test_mode_change_is_refused_around_remote_ems(self):
        for state, text in (("on", "Remote EMS is active"), (None, "Could not confirm")):
            coordinator = _Coordinator({"mode": [1]})
            with self.assertRaisesRegex(control.CommandRefused, text):
                await self._mode(coordinator, remote_ems=state)
            self.assertEqual(coordinator.session.writes, [])

    async def test_mode_not_offered_or_not_allowed_sends_nothing(self):
        cases = [
            (dict(), dict(mode="remote_ems"), "not available"),
            (dict(modes={0, 1}), dict(mode="time_of_use"), "not available"),
            (dict(modes=set()), dict(), "not available"),
            (dict(allow=False), dict(), "switched off"),
            (dict(verified=False), dict(), "could not be checked"),
        ]
        for coordinator_args, call_args, text in cases:
            coordinator = _Coordinator({"mode": [1, 0]}, **coordinator_args)
            with self.assertRaisesRegex(control.CommandRefused, text):
                await self._mode(coordinator, **call_args)
            self.assertEqual(coordinator.session.writes, [])

    async def test_unreadable_current_setting_sends_nothing(self):
        coordinator = _Coordinator({"mode": [None]})
        with self.assertRaisesRegex(control.CommandRefused, "did not report"):
            await self._mode(coordinator)
        self.assertEqual(coordinator.session.writes, [])

    async def test_battery_export_and_readback_outcomes(self):
        # Blocking export sets mySigen's limitation; the switch turns off.
        coordinator = _Coordinator({"export": [ALLOWED_EXPORT, BLOCKED_EXPORT]})
        result = await control.async_set_battery_export(coordinator, allowed=False, sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.writes, [("export", None, False)])
        coordinator = _Coordinator({"export": [ALLOWED_EXPORT, ALLOWED_EXPORT, ALLOWED_EXPORT]})
        with self.assertRaises(control.CommandNotConfirmed):
            await control.async_set_battery_export(coordinator, allowed=False, sleep=_no_sleep)
        error = api.MySigenConnectionError("x")
        coordinator = _Coordinator({"export": [ALLOWED_EXPORT, error, error]})
        with self.assertRaises(control.CommandUnknown):
            await control.async_set_battery_export(coordinator, allowed=False, sleep=_no_sleep)
        self.assertEqual(len(coordinator.session.writes), 1)
        # Already allowed: nothing is sent.
        coordinator = _Coordinator({"export": [ALLOWED_EXPORT]})
        await control.async_set_battery_export(coordinator, allowed=True, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_lost_answer_is_unknown_and_not_sent_again(self):
        coordinator = _Coordinator({"export": [ALLOWED_EXPORT]}, api.MySigenUnknownOutcome("x"))
        with self.assertRaises(control.CommandUnknown):
            await control.async_set_battery_export(coordinator, allowed=False, sleep=_no_sleep)
        self.assertEqual(len(coordinator.session.writes), 1)

    async def test_peak_shaving_switch_and_limit(self):
        coordinator = _Coordinator({"peak": [PEAK_ON, PEAK_OFF]})
        await control.async_set_peak_shaving(coordinator, enabled=False, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("peak", 0, 13.0)])
        limited = api.parse_peak_shaving(PEAK_ON.with_limit(9.5))
        coordinator = _Coordinator({"peak": [PEAK_ON, limited]})
        await control.async_set_peak_shaving(coordinator, limit_kw=9.5, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("peak", 1, 9.5)])

    async def test_grid_current_limit(self):
        at_25 = api.parse_grid_current_limit(GRID_CURRENT)
        at_16 = api.parse_grid_current_limit(
            {**GRID_CURRENT, "currentLimitation": "16.0", "ownerSetLimitation": "16.0"})
        coordinator = _Coordinator({"current": [at_25, at_16]})
        result = await control.async_set_grid_current_limit(coordinator, amps=16, sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.writes, [("current", 16.0)])
        self.assertEqual(coordinator.settings, [("grid_current_limit", "confirmed")])
        # Already set: nothing is sent.
        coordinator = _Coordinator({"current": [at_16]})
        await control.async_set_grid_current_limit(coordinator, amps=16.0, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])
        # A limit that is switched off is not "already set".
        off = api.parse_grid_current_limit(
            {**GRID_CURRENT, "enable": False, "currentLimitation": "16.0"})
        coordinator = _Coordinator({"current": [off, at_16]})
        await control.async_set_grid_current_limit(coordinator, amps=16, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [("current", 16.0)])

    async def test_grid_current_limit_on_a_plant_without_one(self):
        # No installer limit: any value above 0 may be set, which switches it on.
        unset = api.parse_grid_current_limit(NO_GRID_CURRENT)
        at_20 = api.parse_grid_current_limit(
            {"enable": True, "currentLimitation": "20.0", "ownerSetLimitation": "20.0",
             "installerSetLimitation": ""})
        coordinator = _Coordinator({"current": [unset, at_20]})
        result = await control.async_set_grid_current_limit(coordinator, amps=20, sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.writes, [("current", 20.0)])

    async def test_an_incomplete_answer_sends_nothing(self):
        incomplete = api.parse_grid_current_limit({"enable": True, "currentLimitation": "25.0"})
        coordinator = _Coordinator({"current": [incomplete]})
        with self.assertRaisesRegex(control.CommandRefused, "did not report"):
            await control.async_set_grid_current_limit(coordinator, amps=32, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_grid_current_limit_refusals(self):
        at_25 = api.parse_grid_current_limit(GRID_CURRENT)
        for amps, text in ((32, "at most 25 A"), (0, "above 0"), (-5, "above 0")):
            coordinator = _Coordinator({"current": [at_25]})
            with self.assertRaisesRegex(control.CommandRefused, text):
                await control.async_set_grid_current_limit(
                    coordinator, amps=amps, sleep=_no_sleep)
            self.assertEqual(coordinator.session.writes, [])
        coordinator = _Coordinator({"current": [at_25]}, allow=False)
        with self.assertRaisesRegex(control.CommandRefused, "switched off"):
            await control.async_set_grid_current_limit(coordinator, amps=16, sleep=_no_sleep)

    async def test_peak_shaving_refusals(self):
        two = api.parse_peak_shaving({**PEAK, "settingList": PEAK["settingList"] * 2})
        empty = api.parse_peak_shaving({**PEAK, "controlMode": 0, "settingList": []})
        cases = [
            ({"peak": [two]}, dict(limit_kw=5.0), "more than one time slot"),
            ({"peak": [empty]}, dict(enabled=True), "no time slot"),
            ({"peak": [PEAK_ON]}, dict(limit_kw=0.0), "above 0"),
            ({"peak": [PEAK_ON]}, dict(limit_kw=150.0), "at most"),
        ]
        for reads, call_args, text in cases:
            coordinator = _Coordinator(reads)
            with self.assertRaisesRegex(control.CommandRefused, text):
                await control.async_set_peak_shaving(coordinator, sleep=_no_sleep, **call_args)
            self.assertEqual(coordinator.session.writes, [])


if __name__ == "__main__":
    unittest.main()
