"""Login-check outcomes against a fake HTTP session (no network, no HA)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

try:
    import aiohttp  # noqa: F401
except ImportError:  # minimal stand-in so the module under test imports
    stub = types.ModuleType("aiohttp")
    stub.ClientError = type("ClientError", (Exception,), {})
    stub.ClientSession = object
    stub.BasicAuth = lambda *args: args
    stub.ClientTimeout = lambda **kwargs: kwargs
    sys.modules["aiohttp"] = stub
import aiohttp  # noqa: E402

PATH = Path(__file__).resolve().parents[1] / "custom_components/sigen_mysigen/api.py"
SPEC = importlib.util.spec_from_file_location("sigen_mysigen_api", PATH)
api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = api  # dataclasses look the module up by name
SPEC.loader.exec_module(api)


class _Response:
    def __init__(self, status, payload):
        self.status, self._payload, self.headers = status, payload, {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Session:
    def __init__(self, result):
        self.result, self.calls = result, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class TestLogin(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _login(self, result, region="eu"):
        session = _Session(result)
        await api.async_validate_login(session, region, "user@example.invalid", "secret")
        return session

    async def test_token_in_data_or_top_level_is_success(self):
        for payload in ({"data": {"access_token": "t"}}, {"access_token": "t"}):
            session = await self._login(_Response(200, payload))
        url, kwargs = session.calls[0]
        self.assertEqual(url, "https://api-eu.sigencloud.com/auth/oauth/token")
        self.assertEqual(kwargs["data"], {
            "username": "user@example.invalid", "password": "ENC", "grant_type": "password",
        })
        # Issue 36: the header is built directly, not with aiohttp.BasicAuth.
        self.assertNotIn("auth", kwargs)
        self.assertEqual(kwargs["headers"], {"Authorization": "Basic c2lnZW46c2lnZW4="})

    async def test_refusals_are_auth_errors(self):
        for response in (
            _Response(200, {"code": 11003, "msg": "authentication failed", "data": None}),
            _Response(401, {}),
            _Response(400, None),
        ):
            with self.assertRaises(api.MySigenAuthError):
                await self._login(response)

    async def test_server_error_keeps_its_status(self):
        with self.assertRaises(api.MySigenConnectionError) as caught:
            await self._login(_Response(500, ValueError("no json")))
        self.assertEqual(caught.exception.status, 500)

    async def test_transport_failures_and_bad_bodies(self):
        for result in (aiohttp.ClientError("x"), TimeoutError(), _Response(200, ["x"])):
            with self.assertRaises(api.MySigenConnectionError):
                await self._login(result)

    async def test_rate_limited_login_is_its_own_error(self):
        response = _Response(429, None)
        response.headers = {"Retry-After": "60"}
        with self.assertRaises(api.MySigenRateLimitError) as caught:
            await self._login(response)
        self.assertEqual(caught.exception.retry_after, 60)

    async def test_unknown_region_makes_no_request(self):
        session = _Session(_Response(200, {"access_token": "t"}))
        with self.assertRaises(api.MySigenConnectionError):
            await api.async_validate_login(session, "mars", "u", "p")
        self.assertEqual(session.calls, [])

    async def test_secrets_stay_out_of_error_text(self):
        with self.assertRaises(api.MySigenAuthError) as caught:
            await self._login(_Response(200, {"code": 11003, "msg": "secret"}))
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
