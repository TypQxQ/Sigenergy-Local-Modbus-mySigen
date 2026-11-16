"""The rules for starting and cancelling the battery override (no network, no HA)."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import aiohttp

from test_api_login import _Response, api

# control.py imports ".api" relatively; give it a package to live in.
_PACKAGE = types.ModuleType("sigen_mysigen_pkg")
_PACKAGE.__path__ = []
sys.modules["sigen_mysigen_pkg"] = _PACKAGE
sys.modules["sigen_mysigen_pkg.api"] = api
_SPEC = importlib.util.spec_from_file_location(
    "sigen_mysigen_pkg.control",
    Path(__file__).resolve().parents[1] / "custom_components/sigen_mysigen/control.py",
)
control = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = control
_SPEC.loader.exec_module(control)

OFF = api.OverrideStatus(False, None, None)
HOLD = api.OverrideStatus(True, "hold_battery", None)
CHARGE = api.OverrideStatus(True, "charging", None)
UNSTATED = api.OverrideStatus(False, None, None, explicit=False)


class _FakeSession:
    """Answers reads from a script and records every change request."""

    def __init__(self, reads, write_error=None):
        self.reads, self.write_error, self.writes = list(reads), write_error, []

    async def async_override_status(self):
        answer = self.reads.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def async_start_override(self, mode, minutes):
        self.writes.append(("start", mode, minutes))
        if self.write_error:
            raise self.write_error

    async def async_cancel_override(self):
        self.writes.append(("cancel",))
        if self.write_error:
            raise self.write_error


class _FakeCoordinator:
    def __init__(self, reads, write_error=None, allow=True, wait=0.0, verified=True):
        self.link_verified = verified
        self.session = _FakeSession(reads, write_error)
        self.allow_control, self.rate_limit_wait = allow, wait
        self.command_lock = asyncio.Lock()
        self.commands, self.published, self.rate_limits = [], [], 0

    def record_command(self, action, result):
        self.commands.append((action, result))

    def publish_override(self, status):
        self.published.append(status)

    def note_rate_limit(self, err):
        self.rate_limits += 1
        return 30.0


async def _no_sleep(_seconds):
    return None


class TestStart(unittest.IsolatedAsyncioTestCase):
    async def _start(self, coordinator, mode="hold_battery", minutes=60, remote_ems="off"):
        if not callable(remote_ems):
            state = remote_ems
            remote_ems = lambda: state  # noqa: E731
        return await control.async_start_override(
            coordinator, mode=mode, minutes=minutes, remote_ems=remote_ems, sleep=_no_sleep
        )

    async def test_confirmed_start_sends_once_and_publishes(self):
        coordinator = _FakeCoordinator([OFF, HOLD])
        self.assertEqual(await self._start(coordinator), "confirmed")
        self.assertEqual(coordinator.session.writes, [("start", "hold_battery", 60)])
        self.assertEqual(coordinator.published, [HOLD])
        self.assertEqual(coordinator.commands, [("start", "confirmed")])

    async def test_nothing_is_sent_when_a_precondition_fails(self):
        cases = [
            (dict(allow=False), {}, "switched off"),
            (dict(wait=40.0), {}, "pause"),
            (dict(verified=False), {}, "could not be checked"),
            ({}, dict(remote_ems="on"), "Remote EMS is active"),
            ({}, dict(remote_ems=None), "Could not confirm"),
            ({}, dict(mode="charging"), "not available"),
            ({}, dict(minutes=29), "Duration"),
            ({}, dict(minutes=121), "Duration"),
        ]
        for coordinator_args, call_args, text in cases:
            coordinator = _FakeCoordinator([OFF, HOLD], **coordinator_args)
            with self.assertRaisesRegex(control.CommandRefused, text):
                await self._start(coordinator, **call_args)
            self.assertEqual(coordinator.session.writes, [])
            self.assertEqual(coordinator.commands, [("start", "refused")])

    async def test_remote_ems_is_read_last_and_inside_the_lock(self):
        # Remote EMS turns on while the second start waits for the first.
        states = iter(["off", "on"])
        coordinator = _FakeCoordinator([OFF, HOLD, OFF])
        results = await asyncio.gather(
            self._start(coordinator, remote_ems=lambda: next(states)),
            self._start(coordinator, remote_ems=lambda: next(states)),
            return_exceptions=True,
        )
        self.assertEqual(results[0], "confirmed")
        self.assertIsInstance(results[1], control.CommandRefused)
        self.assertIn("Remote EMS is active", str(results[1]))
        self.assertEqual(len(coordinator.session.writes), 1)

    async def test_unstated_off_is_not_proof_of_off(self):
        coordinator = _FakeCoordinator([UNSTATED, HOLD])
        with self.assertRaisesRegex(control.CommandRefused, "did not state"):
            await self._start(coordinator)
        self.assertEqual(coordinator.session.writes, [])

    async def test_running_override_is_left_alone(self):
        for running in (HOLD, CHARGE):
            coordinator = _FakeCoordinator([running])
            with self.assertRaisesRegex(control.CommandRefused, "already running"):
                await self._start(coordinator)
            self.assertEqual(coordinator.session.writes, [])
            self.assertEqual(coordinator.published, [running])

    async def test_failed_first_read_sends_nothing(self):
        for error in (api.MySigenConnectionError("x"), api.MySigenRateLimitError(5)):
            coordinator = _FakeCoordinator([error])
            with self.assertRaises(control.CommandRefused):
                await self._start(coordinator)
            self.assertEqual(coordinator.session.writes, [])

    async def test_lost_answer_is_unknown_and_not_sent_again(self):
        coordinator = _FakeCoordinator([OFF, HOLD], api.MySigenUnknownOutcome("x"))
        with self.assertRaises(control.CommandUnknown):
            await self._start(coordinator)
        self.assertEqual(len(coordinator.session.writes), 1)
        self.assertEqual(coordinator.commands, [("start", "unknown")])

    async def test_answered_errors_are_refusals(self):
        for error in (api.MySigenRejectedError("x"), api.MySigenPermissionError("x"),
                      api.MySigenAuthError("x"), api.MySigenRateLimitError(None)):
            coordinator = _FakeCoordinator([OFF], error)
            with self.assertRaises(control.CommandRefused):
                await self._start(coordinator)
            self.assertEqual(len(coordinator.session.writes), 1)

    async def test_readback_gets_a_second_chance_but_no_second_write(self):
        coordinator = _FakeCoordinator([OFF, OFF, HOLD])
        self.assertEqual(await self._start(coordinator), "confirmed")
        self.assertEqual(len(coordinator.session.writes), 1)

    async def test_accepted_but_not_shown_is_not_confirmed(self):
        for readback in ([OFF, OFF], [CHARGE, CHARGE]):
            coordinator = _FakeCoordinator([OFF, *readback])
            with self.assertRaises(control.CommandNotConfirmed):
                await self._start(coordinator)
            self.assertEqual(coordinator.commands, [("start", "not_confirmed")])
            self.assertEqual(len(coordinator.session.writes), 1)

    async def test_accepted_but_unreadable_is_unknown(self):
        error = api.MySigenConnectionError("x")
        coordinator = _FakeCoordinator([OFF, error, error])
        with self.assertRaises(control.CommandUnknown):
            await self._start(coordinator)

    async def test_two_starts_at_once_send_one_request(self):
        coordinator = _FakeCoordinator([OFF, HOLD, HOLD])
        results = await asyncio.gather(
            self._start(coordinator), self._start(coordinator), return_exceptions=True
        )
        self.assertEqual(results[0], "confirmed")
        self.assertIsInstance(results[1], control.CommandRefused)
        self.assertEqual(len(coordinator.session.writes), 1)


class TestCancel(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_ends_any_running_override(self):
        coordinator = _FakeCoordinator([CHARGE, OFF])
        result = await control.async_cancel_override(coordinator, sleep=_no_sleep)
        self.assertEqual(result, "confirmed")
        self.assertEqual(coordinator.session.writes, [("cancel",)])

    async def test_nothing_running_sends_nothing(self):
        coordinator = _FakeCoordinator([OFF])
        result = await control.async_cancel_override(coordinator, sleep=_no_sleep)
        self.assertEqual(result, "no_override")
        self.assertEqual(coordinator.session.writes, [])

    async def test_cancel_needs_a_verified_station(self):
        coordinator = _FakeCoordinator([HOLD], verified=False)
        with self.assertRaisesRegex(control.CommandRefused, "could not be checked"):
            await control.async_cancel_override(coordinator, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_cancel_needs_control_switched_on(self):
        coordinator = _FakeCoordinator([HOLD], allow=False)
        with self.assertRaises(control.CommandRefused):
            await control.async_cancel_override(coordinator, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])

    async def test_unstated_answers_neither_allow_nor_confirm_a_cancel(self):
        coordinator = _FakeCoordinator([UNSTATED])
        with self.assertRaisesRegex(control.CommandRefused, "did not state"):
            await control.async_cancel_override(coordinator, sleep=_no_sleep)
        self.assertEqual(coordinator.session.writes, [])
        coordinator = _FakeCoordinator([HOLD, UNSTATED, UNSTATED])
        with self.assertRaises(control.CommandNotConfirmed):
            await control.async_cancel_override(coordinator, sleep=_no_sleep)

    async def test_still_running_after_cancel_is_not_confirmed(self):
        coordinator = _FakeCoordinator([HOLD, HOLD, HOLD])
        with self.assertRaises(control.CommandNotConfirmed):
            await control.async_cancel_override(coordinator, sleep=_no_sleep)


class _Http:
    closed = False

    """Fake aiohttp session that records the method and body of each request."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def post(self, url, **kwargs):
        reply = _Response(200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600})
        return reply

    def request(self, method, url, **kwargs):
        self.requests.append((method, url.rsplit("/", 1)[-1], kwargs.get("json")))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


STATION = _Response(200, {"code": 0, "data": {"stationId": "42"}})


class TestWriteRequests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self, *replies):
        http = _Http([STATION, *replies])
        return http, api.MySigenSession(
            http, "eu", "u@example.invalid", "p", write_session=http
        )

    async def test_start_and_cancel_bodies(self):
        ok = _Response(200, {"code": 0, "data": True})
        http, client = self._client(ok, ok)
        await client.async_start_override("hold_battery", 60)
        await client.async_cancel_override()
        self.assertEqual(http.requests[1], ("PUT", "manunal", {
            "enable": True, "stationId": 42, "mode": "2",
            "duration": "60", "powerLimitation": ""}))
        self.assertEqual(http.requests[2], ("PUT", "manunal", {
            "enable": False, "stationId": 42, "mode": "",
            "duration": "", "powerLimitation": ""}))

    async def test_only_verified_modes_and_durations_can_be_sent(self):
        http, client = self._client()
        for mode, minutes in (("charging", 60), ("hold_battery", 29), ("hold_battery", 121)):
            with self.assertRaises(ValueError):
                await client.async_start_override(mode, minutes)
        self.assertEqual(len(http.requests), 0)

    async def test_write_answers_are_sorted_by_meaning(self):
        import aiohttp
        cases = [
            (aiohttp.ClientError("x"), api.MySigenUnknownOutcome),
            (TimeoutError(), api.MySigenUnknownOutcome),
            (_Response(200, ValueError("no json")), api.MySigenUnknownOutcome),
            (_Response(500, None), api.MySigenUnknownOutcome),
            (_Response(502, {"code": 0}), api.MySigenUnknownOutcome),
            (_Response(400, {}), api.MySigenRejectedError),
            (_Response(200, {"code": 1234}), api.MySigenRejectedError),
            (_Response(403, {}), api.MySigenPermissionError),
            (_Response(401, {}), api.MySigenAuthError),
            (_Response(429, None), api.MySigenRateLimitError),
        ]
        for reply, expected in cases:
            http, client = self._client(reply)
            with self.assertRaises(expected):
                await client.async_start_override("hold_battery", 30)
            # One change request, never repeated, not even after a sign-out.
            self.assertEqual([r[0] for r in http.requests].count("PUT"), 1)


class TestSingleSend(unittest.IsolatedAsyncioTestCase):
    """Against a real aiohttp session and a server that drops the connection."""

    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _serve(self):
        received = []

        async def handle(reader, writer):
            # Read the whole request, then hang up without answering.
            head = await reader.readuntil(b"\r\n\r\n")
            length = int(next(
                (line.split(b":")[1] for line in head.split(b"\r\n")
                 if line.lower().startswith(b"content-length")), b"0"))
            await reader.readexactly(length)
            received.append(head.split(b" ")[0])
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.addAsyncCleanup(self._close, server)
        return server.sockets[0].getsockname()[1], received

    @staticmethod
    async def _close(server):
        server.close()
        await server.wait_closed()

    async def _send(self, write_session, port):
        reads = _Http([STATION])
        client = api.MySigenSession(
            reads, "eu", "u@example.invalid", "p", write_session=write_session
        )
        with patch.dict(api.REGIONS, {"eu": f"http://127.0.0.1:{port}/"}):
            await client.async_start_override("hold_battery", 30)

    @unittest.skipUnless(hasattr(aiohttp, "TraceConfig"), "needs the real aiohttp")
    async def test_dropped_connection_is_not_sent_again(self):
        port, received = await self._serve()
        session = aiohttp.ClientSession(trace_configs=[api.single_send_trace_config()])
        self.addAsyncCleanup(session.close)
        with self.assertRaises(api.MySigenUnknownOutcome):
            await self._send(session, port)
        self.assertEqual(received, [b"PUT"])

    @unittest.skipUnless(hasattr(aiohttp, "TraceConfig"), "needs the real aiohttp")
    async def test_without_the_guard_aiohttp_would_send_twice(self):
        # Documents why the guard exists; fails if aiohttp stops repeating.
        port, received = await self._serve()
        session = aiohttp.ClientSession()
        self.addAsyncCleanup(session.close)
        with self.assertRaises(api.MySigenUnknownOutcome):
            await self._send(session, port)
        self.assertEqual(received, [b"PUT", b"PUT"])

    async def test_no_write_session_sends_nothing(self):
        http = _Http([STATION])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p")
        with self.assertRaises(api.MySigenRejectedError):
            await client.async_start_override("hold_battery", 30)
        self.assertEqual([r[0] for r in http.requests].count("PUT"), 0)

    async def test_closed_write_session_sends_nothing(self):
        http, closed = _Http([STATION]), _Http([])
        closed.closed = True
        client = api.MySigenSession(
            http, "eu", "u@example.invalid", "p", write_session=closed
        )
        with self.assertRaises(api.MySigenRejectedError):
            await client.async_start_override("hold_battery", 30)
        self.assertEqual(closed.requests, [])

    async def test_unexpected_failure_while_sending_is_unknown(self):
        http = _Http([STATION, RuntimeError("boom")])
        client = api.MySigenSession(
            http, "eu", "u@example.invalid", "p", write_session=http
        )
        with self.assertRaises(api.MySigenUnknownOutcome):
            await client.async_start_override("hold_battery", 30)


if __name__ == "__main__":
    unittest.main()
