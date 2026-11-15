"""Serial collection and the plant/station match rule (no network, no HA)."""
from __future__ import annotations

import ast
from pathlib import Path
import unittest
from unittest.mock import patch

from test_api_login import api
from test_api_session import STATION, _Reply, _Session

# link.py imports Home Assistant; take only its pure rule.
_SOURCE = (
    Path(__file__).resolve().parents[1] / "custom_components/sigen_mysigen/link.py"
).read_text(encoding="utf-8")
_NAMESPACE: dict = {}
exec(  # noqa: S102 - our own source file
    compile(
        ast.Module(
            [
                node
                for node in ast.parse(_SOURCE).body
                if isinstance(node, ast.Assign)
                or (isinstance(node, ast.FunctionDef) and node.name == "match_station")
            ],
            type_ignores=[],
        ),
        "link",
        "exec",
    ),
    _NAMESPACE,
)
match_station = _NAMESPACE["match_station"]

TREE = {"nodeList": [
    {"deviceType": 8, "snCode": "gw001", "nodeList": [
        {"deviceType": 3, "snCode": " inv123 ", "nodeList": [{"deviceType": 4, "snCode": ""}]},
    ]},
    {"deviceType": 5},
]}


class TestSerials(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(api, "encrypt_password", return_value="ENC")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_topology_walks_nested_nodes_and_normalizes(self):
        self.assertEqual(api.topology_serials(TREE), {"GW001", "INV123"})
        for bad in (None, "x", 5, {"nodeList": "x"}):
            self.assertEqual(api.topology_serials(bad), set())

    def test_inverter_list_shapes(self):
        self.assertEqual(api.aio_serials(["a1", "", None, 7]), {"A1", "7"})
        self.assertEqual(api.aio_serials({"snList": ["b2"]}), {"B2"})
        self.assertEqual(api.aio_serials({"other": 1}), set())
        self.assertEqual(api.aio_serials([{"sn": "x"}]), set())

    async def test_sources_are_combined_and_one_may_fail(self):
        http = _Session([STATION, _Reply(200, {"code": 0, "data": TREE}), _Reply(500, None)])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p")
        self.assertEqual(await client.async_device_serials(), {"GW001", "INV123"})
        self.assertTrue(http.urls[1].endswith("devicetreepanel/topology"))

    async def test_rate_limit_is_not_swallowed(self):
        http = _Session([STATION, _Reply(429, None)])
        client = api.MySigenSession(http, "eu", "u@example.invalid", "p")
        with self.assertRaises(api.MySigenRateLimitError):
            await client.async_device_serials()

    def test_match_rule(self):
        self.assertIsNone(match_station({"INV123"}, {"GW001", "INV123"}))
        self.assertEqual(match_station(set(), {"INV123"}), "no_local_serial")
        self.assertEqual(match_station({"INV123"}, set()), "no_cloud_serial")
        self.assertEqual(match_station({"INV123"}, {"OTHER"}), "station_mismatch")


if __name__ == "__main__":
    unittest.main()
