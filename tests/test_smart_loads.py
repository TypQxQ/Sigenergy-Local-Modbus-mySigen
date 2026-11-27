"""Smart loads on SigenStor and Gateway ports.

The answers and the switch request are the ones captured from the mySigen
web app on the development plant, with serials and the station id replaced.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from test_api_login import _Response, api
from test_control import _Http, control
from test_settings import _Coordinator, _Session, _no_sleep

STATION = _Response(200, {"code": 0, "data": {"stationId": "42"}})
KINDS = {"AIO-1": 2, "GW-1": 8, "INV-1": 3}


def _row(load_id, path, name, host, **extra):
    return {
        "hasSwitch": None, "loadPath": path, "id": load_id, "stationId": 42, "type": 11,
        "title": "Smart Load", "name": name, "loadKey": 10, "path": path,
        "deviceCommunicationStatus": 1, "isControllable": True, "deviceBelongType": 1,
        "connectDeviceSnCode": host, "deviceSnCode": "LOAD-SN", "offlineGuideType": "",
        **extra,
    }


# As the development plant lists them, plus loads that must be left out.
LIST = [
    _row(134389, 2, "Other", "AIO-1"),
    _row(134388, 1, "Hot Water", "GW-1"),
    # The generator card the plant also lists.
    {"id": -1, "type": 1, "title": "GENERATOR", "name": "Generator", "loadPath": None},
    # Not controllable, on a plain inverter, unknown device, odd values.
    _row(5, 3, "Pump", "AIO-1", isControllable=False),
    _row(6, 4, "Heater", "INV-1"),
    _row(7, 5, "Shelly", "SOMEWHERE-ELSE"),
    _row(8, None, "No port", "AIO-1"),
    _row("x", 6, "No id", "AIO-1"),
]
STATES = [
    {"id": 134388, "stationId": 42, "valueWithUnit": "0.00 kW", "operationMode": 1,
     "manualSwitch": 1, "startControlType": None, "connectDeviceSnCode": "GW-1"},
    {"id": 134389, "stationId": 42, "valueWithUnit": "", "operationMode": 1,
     "manualSwitch": 0, "startControlType": None, "connectDeviceSnCode": "AIO-1"},
]


class TestParsing(unittest.TestCase):
    def test_only_switchable_loads_on_sigen_ports(self):
        loads = api.parse_smart_loads(LIST, KINDS)
        self.assertEqual(loads, (
            api.SmartLoad(134388, 1, "Hot Water", "gateway"),
            api.SmartLoad(134389, 2, "Other", "sigenstor"),
        ))
        for bad in (None, {}, [None, 3]):
            self.assertEqual(api.parse_smart_loads(bad, KINDS), ())
        # Without the device tree no load can be placed.
        self.assertEqual(api.parse_smart_loads(LIST, {}), ())

    def test_a_load_without_a_name_is_named_by_its_port(self):
        self.assertEqual(api.parse_smart_loads([_row(9, 2, " ", "AIO-1")], KINDS)[0].name, "2")

    def test_states(self):
        states = api.parse_smart_load_states(STATES)
        self.assertEqual(states[134388], api.SmartLoadState(True, 1))
        self.assertEqual(states[134389], api.SmartLoadState(False, 1))
        self.assertTrue(states[134389].manual)
        odd = api.parse_smart_load_states([
            {"id": 1, "manualSwitch": 2, "operationMode": 0},
            {"id": 2, "manualSwitch": True, "operationMode": None},
            {"id": -1, "manualSwitch": 0}, {"manualSwitch": 1}, None,
        ])
        self.assertEqual(odd, {
            1: api.SmartLoadState(None, 0), 2: api.SmartLoadState(None, None),
        })
        self.assertFalse(odd[1].manual)
        self.assertEqual(api.parse_smart_load_states(None), {})

    def test_device_kinds_from_the_tree(self):
        tree = {"snCode": "aio-1", "deviceType": 2, "nodeList": [
            {"snCode": "BAT-1", "deviceType": 4},
            {"showSnCode": "GW-1", "deviceType": "8", "nodeList": []},
            {"snCode": "", "deviceType": 9},
        ]}
        self.assertEqual(api.parse_device_kinds(tree), {"AIO-1": 2, "BAT-1": 4, "GW-1": 8})


class TestMode(unittest.TestCase):
    """The control mode, shown by a sensor whether or not control is on."""

    def test_mode_names(self):
        self.assertEqual(api.smart_load_mode_name(0), "auto")
        self.assertEqual(api.smart_load_mode_name(1), "manual")
        self.assertEqual(sorted(api.SMART_LOAD_MODE_NAMES.values()), ["auto", "manual"])

    def test_unknown_mode_code_has_no_name(self):
        # For example a load Sigen AI runs: shown as unknown, never guessed.
        for code in (2, -1, None):
            self.assertIsNone(api.smart_load_mode_name(code))

    def test_mode_sensor_state_and_attributes(self):
        load = api.parse_smart_loads(LIST, KINDS)[0]
        state = api.parse_smart_load_states(STATES)[134388]
        self.assertEqual(api.smart_load_mode_name(state.operation_mode), "manual")
        self.assertEqual(api.smart_load_mode_attributes(load, state), {
            "port": 1, "attached_to": "gateway", "switched_on": True, "mode_code": 1,
        })
        # The raw code stays visible when it has no name.
        odd = api.SmartLoadState(None, 2)
        self.assertEqual(api.smart_load_mode_attributes(load, odd)["mode_code"], 2)
        self.assertIsNone(api.smart_load_mode_attributes(load, odd)["switched_on"])

    def test_listed_loads_keep_every_entity_kind(self):
        # Setup removes smart load entities mySigen no longer lists by their
        # unique ID prefix, so the mode sensor must count as listed too.
        loads = api.parse_smart_loads(LIST, KINDS)
        keys = api.smart_load_unique_keys(loads)
        for load_id in (134388, 134389):
            self.assertIn(f"smart_load_{load_id}", keys)
            self.assertIn(f"smart_load_mode_{load_id}", keys)
            self.assertIn(f"smart_load_mode_select_{load_id}", keys)
        self.assertEqual(api.smart_load_unique_keys(()), set())


class TestReadSchedule(unittest.TestCase):
    """When the coordinator reads the smart loads' states."""

    LOADS = api.parse_smart_loads(LIST, KINDS)

    def _due(self, *, allow=True, loads=LOADS, watched=(), shown=("entry_smart_load_mode_134388",),
             now=1000.0, read_at=0.0):
        return api.smart_load_read_due(
            allow_control=allow, loads=loads, watched=set(watched), shown=set(shown),
            now=now, read_at=read_at, interval=600.0,
        )

    def test_state_read_every_settings_interval_with_control_off(self):
        self.assertTrue(self._due(allow=False))
        self.assertFalse(self._due(allow=False, now=1000.0, read_at=500.0))
        self.assertTrue(self._due(allow=False, now=1100.0, read_at=500.0))
        # A watched switch does not count while control is off.
        self.assertFalse(self._due(allow=False, watched=[134388], now=1000.0, read_at=500.0))

    def test_every_poll_while_a_switch_is_watched(self):
        self.assertTrue(self._due(watched=[134388], now=1000.0, read_at=999.0))
        self.assertTrue(self._due(watched=[134388], shown=(), now=1000.0, read_at=999.0))
        # Control on but no switch enabled: the sensor's ten minutes.
        self.assertFalse(self._due(now=1000.0, read_at=999.0))

    def test_no_read_while_nothing_is_enabled(self):
        # Both entities start disabled; a plant with loads nobody looks at
        # makes no extra request.
        for allow in (True, False):
            self.assertFalse(self._due(allow=allow, shown=()))

    def test_first_read_is_due_right_after_boot(self):
        # time.monotonic() counts from host boot, so it can be below the
        # interval when Home Assistant starts soon after a reboot. The
        # coordinator starts the timer at -inf, never at 0.
        self.assertTrue(self._due(allow=False, now=5.0, read_at=float("-inf")))
        self.assertFalse(self._due(allow=False, now=5.0, read_at=0.0))

    def test_read_soon_makes_the_next_poll_read(self):
        # After an unconfirmed change the coordinator sets the timer to -inf
        # (read_settings_soon), so a read is due at once, also with only a
        # mode sensor or select enabled.
        self.assertTrue(self._due(now=1000.0, read_at=float("-inf")))

    def test_no_loads_no_read(self):
        for allow in (True, False):
            self.assertFalse(self._due(allow=allow, loads=(), watched=[1]))


class _ParamsHttp(_Http):
    """Also records the query string and whether a body was sent."""

    def request(self, method, url, **kwargs):
        self.sent = getattr(self, "sent", []) + [
            (method, url.rsplit("/", 1)[-1], kwargs.get("params"), "json" in kwargs)
        ]
        return super().request(method, url, **kwargs)


class TestRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self, *replies):
        http = _ParamsHttp([STATION, *replies])
        return http, api.MySigenSession(http, "eu", "u@example.invalid", "p", write_session=http)

    async def test_switch_is_a_patch_with_the_values_in_the_query(self):
        ok = _Response(200, {"code": 0, "msg": "success", "data": True})
        http, client = self._client(ok, ok)
        await client.async_set_smart_load(2, False)
        await client.async_set_smart_load(2, True)
        self.assertEqual(http.sent[1:], [
            ("PATCH", "switch", {"stationId": "42", "loadPath": "2", "manualSwitch": "0"}, False),
            ("PATCH", "switch", {"stationId": "42", "loadPath": "2", "manualSwitch": "1"}, False),
        ])

    async def test_mode_write_request(self):
        # As the web app sent it: the "device/" prefix, not "third-party-device/".
        self.assertEqual(api._SMART_LOAD_MODE_PATH, "device/tp-device/smart-loads/control-mode")
        ok = _Response(200, {"code": 0, "msg": "success", "data": True})
        http, client = self._client(ok, ok)
        await client.async_set_smart_load_mode(1, True)
        await client.async_set_smart_load_mode(1, False)
        self.assertEqual(http.sent[1:], [
            ("PATCH", "control-mode", {"stationId": "42", "loadPath": "1", "controlMode": "1"}, False),
            ("PATCH", "control-mode", {"stationId": "42", "loadPath": "1", "controlMode": "0"}, False),
        ])

    async def test_list_and_states_are_read_with_the_station(self):
        tree = _Response(200, {"code": 0, "data": {"snCode": "AIO-1", "deviceType": 2,
                                                   "nodeList": [{"snCode": "GW-1", "deviceType": 8}]}})
        listed = _Response(200, {"code": 0, "data": LIST})
        states = _Response(200, {"code": 0, "data": STATES})
        http, client = self._client(tree, listed, states)
        await client.async_station_id()
        loads = await client.async_smart_loads()
        self.assertEqual([load.load_id for load in loads], [134388, 134389])
        self.assertEqual((await client.async_smart_load_states())[134389].on, False)
        self.assertEqual([entry[:3] for entry in http.sent[1:]], [
            ("GET", "topology", {"stationId": "42"}),
            ("GET", "static-info", {"stationId": "42", "showNewGenerator": "true"}),
            ("GET", "dynamic-info", {"stationId": "42"}),
        ])

    async def test_a_bad_list_answer_is_a_failed_read_not_an_empty_list(self):
        # An empty list would remove every smart load switch with its settings.
        tree = _Response(200, {"code": 0, "data": {"snCode": "AIO-1", "deviceType": 2}})
        for data in (None, {}, "x"):
            http, client = self._client(tree, _Response(200, {"code": 0, "data": data}))
            await client.async_station_id()
            with self.assertRaises(api.MySigenConnectionError):
                await client.async_smart_loads()
        # A real empty list is an answer: no loads.
        http, client = self._client(tree, _Response(200, {"code": 0, "data": []}))
        await client.async_station_id()
        self.assertEqual(await client.async_smart_loads(), ())

    async def test_without_a_device_tree_the_list_is_not_read(self):
        # Without it no load can be placed, which would also look like none.
        for data in (None, [], {"deviceType": 2}):
            http, client = self._client(_Response(200, {"code": 0, "data": data}))
            await client.async_station_id()
            with self.assertRaises(api.MySigenConnectionError):
                await client.async_smart_loads()
            self.assertEqual([entry[1] for entry in http.sent[1:]], ["topology"])

    async def test_a_refusal_and_a_lost_answer_are_not_sent_again(self):
        refused = _Response(200, {"code": 1001, "msg": "fail", "data": None})
        http, client = self._client(refused)
        with self.assertRaises(api.MySigenRejectedError):
            await client.async_set_smart_load(1, True)
        self.assertEqual(len(http.sent), 2)

        http, client = self._client(TimeoutError())
        with self.assertRaises(api.MySigenUnknownOutcome):
            await client.async_set_smart_load(1, True)
        self.assertEqual(len(http.sent), 2)


class _LoadSession(_Session):
    async def async_smart_load_states(self):
        return self._next("states")

    async def async_set_smart_load(self, path, on):
        await self._write("load", path, on)

    async def async_set_smart_load_mode(self, path, manual):
        await self._write("mode", path, manual)


class _LoadCoordinator(_Coordinator):
    def __init__(self, states, loads=None, **kwargs):
        super().__init__({"states": states}, **kwargs)
        self.session = _LoadSession({"states": states}, kwargs.get("write_error"))
        self.smart_loads = api.parse_smart_loads(LIST, KINDS) if loads is None else loads

    def publish_smart_load(self, load_id, state):
        self.published.append((load_id, state))


def states(on, mode=1, load_id=134389):
    """The state answer with the SigenStor load ("Other") set as given."""
    return {load_id: api.SmartLoadState(on, mode)}


OTHER = 134389


class TestRules(unittest.IsolatedAsyncioTestCase):
    async def _switch(self, coordinator, on=True, sleep=_no_sleep):
        return await control.async_set_smart_load(coordinator, load_id=OTHER, on=on, sleep=sleep)

    async def test_switch_is_sent_once_and_confirmed(self):
        waits = []

        async def sleep(seconds):
            waits.append(seconds)

        coordinator = _LoadCoordinator([states(False), states(True)])
        self.assertEqual(await self._switch(coordinator, sleep=sleep), "confirmed")
        self.assertEqual(coordinator.session.writes, [("load", 2, True)])
        self.assertEqual(coordinator.settings, [("smart_load", "confirmed")])
        self.assertEqual(coordinator.published, [(OTHER, api.SmartLoadState(True, 1))])
        # The state follows a few seconds later, so the first read-back waits.
        self.assertEqual(waits, [3.0])

        coordinator = _LoadCoordinator([states(True), states(False)])
        await self._switch(coordinator, on=False)
        self.assertEqual(coordinator.session.writes, [("load", 2, False)])

    async def test_nothing_is_sent_when_already_set(self):
        coordinator = _LoadCoordinator([states(True)])
        self.assertEqual(await self._switch(coordinator), "confirmed")
        self.assertEqual(coordinator.session.writes, [])
        self.assertEqual(coordinator.settings, [("smart_load", "confirmed")])

    async def test_automatic_control_is_refused_even_when_already_set(self):
        # The app shows no switch then, so "already on" is not a confirmation.
        for mode in (0, 2, None):
            coordinator = _LoadCoordinator([states(True, mode=mode)])
            with self.assertRaisesRegex(control.CommandRefused, "manual"):
                await self._switch(coordinator)
            self.assertEqual(coordinator.session.writes, [])
            self.assertEqual(coordinator.settings, [("smart_load", "refused")])

    async def test_a_read_back_under_automatic_control_does_not_count(self):
        coordinator = _LoadCoordinator(
            [states(False), states(True, mode=0), states(True, mode=0)])
        with self.assertRaises(control.CommandNotConfirmed):
            await self._switch(coordinator)
        self.assertEqual(coordinator.session.writes, [("load", 2, True)])

    async def test_a_lost_answer_is_unknown_and_not_sent_again(self):
        coordinator = _LoadCoordinator(
            [states(False)], write_error=api.MySigenUnknownOutcome("switch smart load: TimeoutError"))
        with self.assertRaises(control.CommandUnknown):
            await self._switch(coordinator)
        self.assertEqual(coordinator.session.writes, [("load", 2, True)])
        self.assertEqual(coordinator.settings, [("smart_load", "unknown")])

    async def test_a_refusal_from_mysigen_is_reported(self):
        coordinator = _LoadCoordinator(
            [states(False)], write_error=api.MySigenRejectedError("switch smart load: code 1001"))
        with self.assertRaisesRegex(control.CommandRefused, "refused"):
            await self._switch(coordinator)
        self.assertEqual(coordinator.session.writes, [("load", 2, True)])
        self.assertEqual(coordinator.settings, [("smart_load", "refused")])

    async def test_it_only_counts_when_mysigen_shows_it(self):
        coordinator = _LoadCoordinator([states(False), states(False), states(False)])
        with self.assertRaises(control.CommandNotConfirmed):
            await self._switch(coordinator)
        self.assertEqual(coordinator.session.writes, [("load", 2, True)])
        self.assertEqual(coordinator.settings, [("smart_load", "not_confirmed")])

        coordinator = _LoadCoordinator(
            [states(False), api.MySigenConnectionError("x"), api.MySigenConnectionError("x")])
        with self.assertRaises(control.CommandUnknown):
            await self._switch(coordinator)

    async def test_refusals_send_nothing(self):
        cases = [
            (dict(allow=False), [states(False)], "switched off"),
            (dict(verified=False), [states(False)], "could not be checked"),
            (dict(loads=()), [states(False)], "no longer lists"),
            # Under automatic control the app shows no switch.
            (dict(), [states(False, mode=0)], "manual"),
            (dict(), [states(None)], "did not report whether"),
            (dict(), [{}], "did not report the current setting"),
            (dict(), [api.MySigenConnectionError("x")], "Could not read"),
        ]
        for coordinator_args, answers, text in cases:
            coordinator = _LoadCoordinator(answers, **coordinator_args)
            with self.assertRaisesRegex(control.CommandRefused, text):
                await self._switch(coordinator)
            self.assertEqual(coordinator.session.writes, [], text)


class TestModeRules(unittest.IsolatedAsyncioTestCase):
    """Choosing a smart load's control mode from Home Assistant."""

    async def _choose(self, coordinator, manual=True, sleep=_no_sleep):
        return await control.async_set_smart_load_mode(
            coordinator, load_id=OTHER, manual=manual, sleep=sleep)

    async def test_select_sets_manual_and_confirms(self):
        waits = []

        async def sleep(seconds):
            waits.append(seconds)

        coordinator = _LoadCoordinator([states(False, mode=0), states(False, mode=1)])
        self.assertEqual(await self._choose(coordinator, sleep=sleep), "confirmed")
        self.assertEqual(coordinator.session.writes, [("mode", 2, True)])
        self.assertEqual(coordinator.settings, [("smart_load_mode", "confirmed")])
        self.assertEqual(coordinator.published, [(OTHER, api.SmartLoadState(False, 1))])
        self.assertEqual(waits, [3.0])

        coordinator = _LoadCoordinator([states(True, mode=1), states(True, mode=0)])
        self.assertEqual(await self._choose(coordinator, manual=False), "confirmed")
        self.assertEqual(coordinator.session.writes, [("mode", 2, False)])

    async def test_select_already_in_mode_sends_nothing(self):
        coordinator = _LoadCoordinator([states(True, mode=1)])
        self.assertEqual(await self._choose(coordinator), "confirmed")
        self.assertEqual(coordinator.session.writes, [])

    async def test_select_refuses_unknown_mode(self):
        # A mode this add-on has no name for (for example Sigen AI's) is
        # left alone rather than overwritten.
        for mode in (2, None):
            coordinator = _LoadCoordinator([states(True, mode=mode)])
            with self.assertRaisesRegex(control.CommandRefused, "mode"):
                await self._choose(coordinator)
            self.assertEqual(coordinator.session.writes, [])
            self.assertEqual(coordinator.settings, [("smart_load_mode", "refused")])

    async def test_select_not_confirmed_or_unknown(self):
        coordinator = _LoadCoordinator(
            [states(False, mode=0), states(False, mode=0), states(False, mode=0)])
        with self.assertRaises(control.CommandNotConfirmed):
            await self._choose(coordinator)
        self.assertEqual(coordinator.session.writes, [("mode", 2, True)])

        coordinator = _LoadCoordinator(
            [states(False, mode=0)],
            write_error=api.MySigenUnknownOutcome("smart load mode: TimeoutError"))
        with self.assertRaises(control.CommandUnknown):
            await self._choose(coordinator)
        self.assertEqual(coordinator.session.writes, [("mode", 2, True)])

    async def test_select_refusals_send_nothing(self):
        cases = [
            (dict(allow=False), [states(False, mode=0)], "switched off"),
            (dict(loads=()), [states(False, mode=0)], "no longer lists"),
            (dict(), [{}], "did not report the current setting"),
        ]
        for coordinator_args, answers, text in cases:
            coordinator = _LoadCoordinator(answers, **coordinator_args)
            with self.assertRaisesRegex(control.CommandRefused, text):
                await self._choose(coordinator)
            self.assertEqual(coordinator.session.writes, [], text)

    async def test_switch_refusal_names_mode_select(self):
        coordinator = _LoadCoordinator([states(True, mode=0)])
        with self.assertRaisesRegex(control.CommandRefused, "control mode select"):
            await control.async_set_smart_load(
                coordinator, load_id=OTHER, on=True, sleep=_no_sleep)

    def test_the_mode_select_takes_no_remote_ems_check(self):
        self.assertNotIn("remote_ems", control.async_set_smart_load_mode.__code__.co_varnames)


class TestNoRemoteEmsRule(unittest.IsolatedAsyncioTestCase):
    """Remote EMS does not switch smart loads, so a switch is not refused for it."""

    def test_the_switch_takes_no_remote_ems_check(self):
        self.assertNotIn("remote_ems", control.async_set_smart_load.__code__.co_varnames)

    async def test_mysigen_refusing_it_in_remote_ems_is_reported_as_refused(self):
        # What a refusal from mySigen would look like; not seen on a real plant.
        coordinator = _LoadCoordinator(
            [states(False)], write_error=api.MySigenRejectedError("switch smart load: code 1"))
        with self.assertRaises(control.CommandRefused):
            await control.async_set_smart_load(
                coordinator, load_id=OTHER, on=True, sleep=_no_sleep)
        self.assertEqual(len(coordinator.session.writes), 1)


if __name__ == "__main__":
    unittest.main()
