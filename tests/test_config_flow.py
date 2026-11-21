"""Setup asks for the settings after sign-in; Reconfigure keeps the same ones.

config_flow.py is loaded with small stand-ins for Home Assistant and
voluptuous, so these run without either installed.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components/sigen_mysigen"
PACKAGE = "sigen_mysigen_flow_pkg"


class _Abort(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _Marker:
    """vol.Required / vol.Optional: a key that remembers its name."""

    def __init__(self, key, default=None):
        self.schema = key
        self.default = default

    def __hash__(self):
        return hash(self.schema)

    def __eq__(self, other):
        return isinstance(other, _Marker) and other.schema == self.schema


class _Schema:
    def __init__(self, schema):
        self.schema = dict(schema)

    def extend(self, schema):
        extra = schema.schema if isinstance(schema, _Schema) else schema
        return _Schema({**self.schema, **extra})

    def keys(self):
        return [marker.schema for marker in self.schema]


class _ConfigFlow:
    """The parts of Home Assistant's ConfigFlow that the flow uses."""

    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self):
        self.unique_id = None
        self.configured: set[str] = set()
        self.suggested: dict = {}

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        if self.unique_id in self.configured:
            raise _Abort("already_configured")

    def async_abort(self, reason):
        return {"type": "abort", "reason": reason}

    def async_show_form(self, step_id, data_schema=None, errors=None, **_kwargs):
        return {
            "type": "form", "step_id": step_id,
            "data_schema": data_schema, "errors": errors or {},
        }

    def async_create_entry(self, title, data, options=None):
        return {"type": "create_entry", "title": title, "data": data, "options": options}

    def add_suggested_values_to_schema(self, schema, suggested):
        self.suggested = dict(suggested)
        return schema

    def _get_reconfigure_entry(self):
        return self.reconfigure_entry

    def async_update_reload_and_abort(self, entry, data_updates=None, options=None):
        return {
            "type": "abort", "reason": "reconfigure_successful",
            "data_updates": data_updates, "options": options,
        }


def _module(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


def _anything(*_args, **_kwargs):
    return types.SimpleNamespace(args=_args, kwargs=_kwargs)


def _load_flow():
    _module("awesomeversion", AwesomeVersion=str)
    _module(
        "voluptuous", Schema=_Schema,
        Required=_Marker, Optional=_Marker,
    )
    _module("homeassistant")
    _module("homeassistant.helpers")
    _module("homeassistant.config_entries", ConfigFlow=_ConfigFlow, ConfigFlowResult=dict)
    _module(
        "homeassistant.const",
        CONF_NAME="name", CONF_PASSWORD="password", CONF_USERNAME="username",
    )
    _module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
    _module(
        "homeassistant.helpers.selector",
        **{
            name: _anything
            for name in (
                "BooleanSelector", "NumberSelector", "NumberSelectorConfig",
                "SelectOptionDict", "SelectSelector", "SelectSelectorConfig",
                "TextSelector", "TextSelectorConfig",
            )
        },
        NumberSelectorMode=types.SimpleNamespace(BOX="box"),
        SelectSelectorMode=types.SimpleNamespace(DROPDOWN="dropdown"),
        TextSelectorType=types.SimpleNamespace(PASSWORD="password", EMAIL="email"),
    )
    _module(
        "homeassistant.loader",
        IntegrationNotFound=Exception, async_get_integration=None,
    )
    package = _module(PACKAGE)
    package.__path__ = []
    for name in ("const", "api"):
        spec = importlib.util.spec_from_file_location(
            f"{PACKAGE}.{name}", COMPONENT / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    _module(f"{PACKAGE}.link", async_link_problem=None)
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.config_flow", COMPONENT / "config_flow.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


config_flow = _load_flow()
const = sys.modules[f"{PACKAGE}.const"]

ACCOUNT = {
    const.CONF_PLANT_ENTRY_ID: "01PLANT",
    const.CONF_REGION: "eu",
    "username": "ha@example.com",
    "password": "secret",
}


def _flow(login_error=None):
    flow = config_flow.MySigenConfigFlow()
    flow.logins = []

    async def no_problem():
        return None

    async def login(*args):
        flow.logins.append(args)
        return login_error

    flow._async_modbus_problem = no_problem
    flow._plants = lambda: {"01PLANT": "Home"}
    flow._async_login_error = login
    return flow


def _run(coro):
    return asyncio.run(coro)


class TestSetupSettings(unittest.TestCase):
    def test_sign_in_leads_to_the_settings_step_not_to_an_entry(self):
        flow = _flow()
        result = _run(flow.async_step_user(dict(ACCOUNT)))
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "settings")
        self.assertEqual(
            result["data_schema"].keys(),
            [const.CONF_SCAN_INTERVAL, const.CONF_ALLOW_CONTROL, const.CONF_ALLOW_GRID_DISCONNECT],
        )
        self.assertEqual(len(flow.logins), 1)

    def test_settings_start_with_safe_defaults(self):
        flow = _flow()
        _run(flow.async_step_user(dict(ACCOUNT)))
        self.assertEqual(
            flow.suggested,
            {
                const.CONF_SCAN_INTERVAL: const.DEFAULT_SCAN_INTERVAL,
                const.CONF_ALLOW_CONTROL: False,
                const.CONF_ALLOW_GRID_DISCONNECT: False,
            },
        )

    def test_the_chosen_settings_are_stored_as_options(self):
        flow = _flow()
        _run(flow.async_step_user(dict(ACCOUNT)))
        result = _run(flow.async_step_settings({
            const.CONF_SCAN_INTERVAL: 45.0,
            const.CONF_ALLOW_CONTROL: True,
            const.CONF_ALLOW_GRID_DISCONNECT: False,
        }))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Home mySigen")
        self.assertEqual(result["data"], ACCOUNT)
        self.assertEqual(
            result["options"],
            {
                const.CONF_SCAN_INTERVAL: 45,
                const.CONF_ALLOW_CONTROL: True,
                const.CONF_ALLOW_GRID_DISCONNECT: False,
            },
        )
        self.assertIsInstance(result["options"][const.CONF_SCAN_INTERVAL], int)
        # The settings step signs in no further.
        self.assertEqual(len(flow.logins), 1)

    def test_a_failed_sign_in_stays_on_the_first_step(self):
        flow = _flow(login_error="invalid_auth")
        result = _run(flow.async_step_user(dict(ACCOUNT)))
        self.assertEqual(result["step_id"], "user")
        self.assertEqual(result["errors"], {"base": "invalid_auth"})

    def test_a_plant_linked_meanwhile_is_not_linked_twice(self):
        flow = _flow()
        _run(flow.async_step_user(dict(ACCOUNT)))
        flow.configured.add("01PLANT")
        with self.assertRaises(_Abort) as caught:
            _run(flow.async_step_settings({
                const.CONF_SCAN_INTERVAL: 30,
                const.CONF_ALLOW_CONTROL: False,
                const.CONF_ALLOW_GRID_DISCONNECT: False,
            }))
        self.assertEqual(caught.exception.reason, "already_configured")


class TestReconfigureSettings(unittest.TestCase):
    def _entry(self, **options):
        return types.SimpleNamespace(data=dict(ACCOUNT), options=options)

    def test_reconfigure_offers_the_same_settings(self):
        flow = _flow()
        flow.reconfigure_entry = self._entry(scan_interval=20, allow_control=True)
        result = _run(flow.async_step_reconfigure())
        self.assertEqual(
            result["data_schema"].keys(),
            [
                "username", "password", const.CONF_SCAN_INTERVAL,
                const.CONF_ALLOW_CONTROL, const.CONF_ALLOW_GRID_DISCONNECT,
            ],
        )
        self.assertEqual(flow.suggested[const.CONF_SCAN_INTERVAL], 20)
        self.assertIs(flow.suggested[const.CONF_ALLOW_CONTROL], True)
        self.assertIs(flow.suggested[const.CONF_ALLOW_GRID_DISCONNECT], False)

    def test_reconfigure_stores_settings_the_same_way(self):
        flow = _flow()
        flow.reconfigure_entry = self._entry(scan_interval=20, other="kept")
        result = _run(flow.async_step_reconfigure({
            "username": ACCOUNT["username"],
            const.CONF_SCAN_INTERVAL: 60.0,
            const.CONF_ALLOW_CONTROL: True,
            const.CONF_ALLOW_GRID_DISCONNECT: True,
        }))
        self.assertEqual(result["reason"], "reconfigure_successful")
        self.assertEqual(
            result["options"],
            {
                "other": "kept",
                const.CONF_SCAN_INTERVAL: 60,
                const.CONF_ALLOW_CONTROL: True,
                const.CONF_ALLOW_GRID_DISCONNECT: True,
            },
        )
        # Same account and password: no new sign-in.
        self.assertEqual(flow.logins, [])


if __name__ == "__main__":
    unittest.main()
