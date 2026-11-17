"""Disconnecting from the grid and connecting again (step D)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from test_api_login import _Response, api
from test_control import STATION, _Http, control
from test_settings import _Coordinator, _Session, _no_sleep

# Shapes as the reference client documents them.
ON = {"onOffGridStatus": 0, "manualOffGridStatus": 0, "showButton": True}
GOING_OFF = {**ON, "manualOffGridStatus": 1}
OFF_MANUAL = {"onOffGridStatus": 2, "manualOffGridStatus": 2, "showButton": True}
OFF_OUTAGE = {"onOffGridStatus": 2, "manualOffGridStatus": 0, "showButton": True}
RETURNING = {"onOffGridStatus": 1, "manualOffGridStatus": 2, "showButton": True}


def grid(data):
    return api.parse_grid_connection(data)


class TestParsing(unittest.TestCase):
    def test_states(self):
        self.assertEqual(grid(ON).state, "on_grid")
        self.assertEqual(grid(GOING_OFF).state, "going_off_grid")
        self.assertEqual(grid(OFF_MANUAL).state, "off_grid")
        self.assertEqual(grid(RETURNING).state, "returning_to_grid")
        self.assertEqual(
            [grid(d).off_grid for d in (ON, GOING_OFF, OFF_MANUAL, RETURNING)],
            [False, True, True, False],
        )
        self.assertTrue(grid(OFF_MANUAL).manual_off_grid)
        self.assertFalse(grid(OFF_OUTAGE).manual_off_grid)
        self.assertFalse(grid({**ON, "showButton": "true"}).offered)
        for bad in (None, {}, {"onOffGridStatus": 3}, {"onOffGridStatus": True}):
            self.assertIsNone(grid(bad))


class TestRequests(unittest.IsolatedAsyncioTestCase):
    async def test_body(self):
        with patch.object(api, "encrypt_password", return_value="ENC"):
            http = _Http([STATION, _Response(200, {"code": 0, "data": True})] * 2)
            client = api.MySigenSession(
                http, "eu", "u@example.invalid", "p", write_session=http
            )
            await client.async_set_off_grid(True)
            await client.async_set_off_grid(False)
        writes = [r for r in http.requests if r[0] == "POST"]
        self.assertEqual(writes, [
            ("POST", "update", {"onGridState": 1, "stationId": 42}),
            ("POST", "update", {"onGridState": 0, "stationId": 42}),
        ])


class _GridSession(_Session):
    async def async_grid_connection(self):
        return self._next("grid")

    async def async_set_off_grid(self, off_grid):
        await self._write("grid", off_grid)


class _GridCoordinator(_Coordinator):
    def __init__(self, reads, allow_grid=True, **kwargs):
        super().__init__(reads, **kwargs)
        self.session = _GridSession(reads, kwargs.get("write_error"))
        self.allow_grid_disconnect = allow_grid

    publish_grid_connection = _Coordinator.publish_operating_mode


class TestRules(unittest.IsolatedAsyncioTestCase):
    async def _set(self, coordinator, off_grid=True, plant="on_grid"):
        return await control.async_set_off_grid(
            coordinator, off_grid=off_grid, plant_grid=lambda: plant, sleep=_no_sleep
        )

    async def test_disconnect_is_sent_once_and_confirmed_while_going_off(self):
        coordinator = _GridCoordinator({"grid": [grid(ON), grid(GOING_OFF)]})
        self.assertEqual(await self._set(coordinator), "confirmed")
        self.assertEqual(coordinator.session.writes, [("grid", True)])
        self.assertEqual(coordinator.settings, [("disconnect_grid", "confirmed")])

    async def test_reconnect_after_manual_disconnect(self):
        coordinator = _GridCoordinator({"grid": [grid(OFF_MANUAL), grid(RETURNING)]})
        result = await self._set(coordinator, off_grid=False, plant="off_grid_manual")
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.writes, [("grid", False)])

    async def test_nothing_sent_when_already_there(self):
        for data, off_grid in ((GOING_OFF, True), (OFF_MANUAL, True), (ON, False)):
            coordinator = _GridCoordinator({"grid": [grid(data)]})
            self.assertEqual(await self._set(coordinator, off_grid=off_grid), "confirmed")
            self.assertEqual(coordinator.session.writes, [])

    async def test_refusals_send_nothing(self):
        cases = [
            (dict(allow_grid=False), [ON], dict(), "switched off"),
            (dict(allow=False), [ON], dict(), "switched off"),
            (dict(verified=False), [ON], dict(), "could not be checked"),
            (dict(), [{**ON, "showButton": False}], dict(), "does not offer"),
            (dict(), [RETURNING], dict(), "not settled"),
            (dict(), [ON], dict(plant="off_grid_auto"), "Modbus"),
            (dict(), [ON], dict(plant=None), "Modbus"),
            # An outage reconnects by itself; it is never answered with a reconnect.
            (dict(), [OFF_OUTAGE], dict(off_grid=False, plant="off_grid_auto"), "outage"),
            (dict(), [OFF_MANUAL], dict(off_grid=False, plant="on_grid"), "Modbus"),
            (dict(), [None], dict(), "did not report"),
        ]
        for coordinator_args, reads, call_args, text in cases:
            coordinator = _GridCoordinator(
                {"grid": [grid(d) if d else None for d in reads]}, **coordinator_args
            )
            with self.assertRaisesRegex(control.CommandRefused, text):
                await self._set(coordinator, **call_args)
            self.assertEqual(coordinator.session.writes, [])

    async def test_lost_answer_is_unknown_and_not_sent_again(self):
        coordinator = _GridCoordinator(
            {"grid": [grid(ON)]}, write_error=api.MySigenUnknownOutcome("x")
        )
        with self.assertRaises(control.CommandUnknown):
            await self._set(coordinator)
        self.assertEqual(len(coordinator.session.writes), 1)


if __name__ == "__main__":
    unittest.main()
