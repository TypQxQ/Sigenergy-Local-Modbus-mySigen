"""Signed-in reads against a fake HTTP session (no network, no HA)."""
from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from test_api_login import _Response, api


class _Reply(_Response):
    def __init__(self, status, payload, headers=None):
        super().__init__(status, payload)
        self.headers = headers or {}


TOKEN = _Reply(200, {"data": {"access_token": "a", "refresh_token": "r", "expires_in": 3600}})
STATION = _Reply(200, {"code": 0, "data": {"stationId": 42}})


class _Session:
    def __init__(self, gets):
        self.gets, self.posts, self.urls = list(gets), [], []

    def post(self, url, **kwargs):
        self.posts.append(kwargs["data"]["grant_type"])
        return TOKEN

    def request(self, method, url, **kwargs):
        self.urls.append(url)
        return self.gets.pop(0)


class TestSession(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self, *gets):
        http = _Session(gets)
        return http, api.MySigenSession(http, "eu", "u@example.invalid", "p")

    async def test_reads_override_and_keeps_station_and_token(self):
        end = 1794760000000
        reply = _Reply(200, {"code": 0, "data": {"enable": True, "mode": 2, "endTime": end}})
        http, client = self._client(STATION, reply, reply)
        status = await client.async_override_status()
        await client.async_override_status()
        self.assertEqual((status.enabled, status.mode), (True, "hold_battery"))
        self.assertEqual(status.ends_at, datetime.fromtimestamp(end / 1000, tz=timezone.utc))
        self.assertEqual(http.posts, ["password"])
        self.assertEqual(len(http.urls), 3)
        self.assertTrue(http.urls[1].endswith("instant/manunal/42"))

    async def test_off_clears_mode_and_end(self):
        status = api.parse_override({"enable": False, "mode": "2", "endTime": "1794760000"})
        self.assertEqual((status.enabled, status.mode, status.ends_at), (False, None, None))

    async def test_seconds_and_unusable_end_times(self):
        self.assertIsNotNone(api.parse_override({"enable": 1, "endTime": 1794760000}).ends_at)
        for value in (None, "", 0, 3600, "x"):
            self.assertIsNone(api.parse_override({"enable": 1, "endTime": value}).ends_at)

    async def test_dropped_session_signs_in_again_once(self):
        ok = _Reply(200, {"code": 0, "data": {"enable": False}})
        http, client = self._client(STATION, _Reply(401, {}), ok)
        self.assertFalse((await client.async_override_status()).enabled)
        self.assertEqual(http.posts, ["password", "refresh_token"])

    async def test_second_refusal_is_an_auth_error(self):
        http, client = self._client(_Reply(200, {"code": 401}), _Reply(401, {}))
        with self.assertRaises(api.MySigenAuthError):
            await client.async_override_status()

    async def test_rate_limit_carries_retry_after(self):
        http, client = self._client(STATION, _Reply(429, None, {"Retry-After": "120"}))
        with self.assertRaises(api.MySigenRateLimitError) as caught:
            await client.async_override_status()
        self.assertEqual(caught.exception.retry_after, 120)

    async def test_bad_answers_are_connection_errors(self):
        for reply in (_Reply(500, None), _Reply(200, {"code": 1234}), _Reply(200, {"code": 0, "data": []})):
            http, client = self._client(STATION, reply)
            with self.assertRaises(api.MySigenConnectionError):
                await client.async_override_status()


if __name__ == "__main__":
    unittest.main()
