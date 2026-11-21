"""Exercise the add-on's imports, forms and action schemas on Home Assistant.

Run with Home Assistant Core installed and the repository root on the path:

    PYTHONPATH=. python tests/ha_dependency_smoke.py

It imports every module, builds each config flow form, serializes it the way
Home Assistant's flow view does, and validates the action schemas. It does
not start Home Assistant or contact mySigen.
"""
from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import probatio
from homeassistant.helpers import config_validation as cv

PACKAGE = "custom_components.sigen_mysigen"
COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/sigen_mysigen"


def form_field_names(form):
    """Serialize fields with the same serializer as Home Assistant's flow view."""
    assert form["type"] == "form", form
    fields = probatio.to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)
    return {field["name"] for field in fields}


def rejects(schema, value):
    try:
        schema(value)
    except probatio.Invalid:
        return True
    return False


async def main():
    # Every module must import; a missing dependency shows up here.
    for path in sorted(COMPONENT.glob("*.py")):
        name = PACKAGE if path.stem == "__init__" else f"{PACKAGE}.{path.stem}"
        importlib.import_module(name)

    addon = importlib.import_module(PACKAGE)
    flow_module = importlib.import_module(f"{PACKAGE}.config_flow")
    const = importlib.import_module(f"{PACKAGE}.const")

    account = {
        const.CONF_PLANT_ENTRY_ID: "01PLANT", const.CONF_REGION: "eu",
        "username": "ha@example.invalid", "password": "secret",
    }
    entry = SimpleNamespace(data=account, options={})

    flow = flow_module.MySigenConfigFlow()
    flow.flow_id = "mysigen-smoke"
    flow.handler = const.DOMAIN
    flow.context = {}

    async def no_problem():
        return None

    flow._async_modbus_problem = no_problem
    flow._plants = lambda: {"01PLANT": "Sigen Plant"}
    flow._get_reconfigure_entry = lambda: entry
    flow._get_reauth_entry = lambda: entry

    assert form_field_names(await flow.async_step_user()) == {
        const.CONF_PLANT_ENTRY_ID, const.CONF_REGION, "username", "password",
    }
    assert form_field_names(await flow.async_step_settings()) == {
        const.CONF_SCAN_INTERVAL, const.CONF_ALLOW_CONTROL, const.CONF_ALLOW_GRID_DISCONNECT,
    }
    assert form_field_names(await flow.async_step_reauth_confirm()) == {"password"}
    assert form_field_names(await flow.async_step_reconfigure()) == {
        "username", "password", const.CONF_SCAN_INTERVAL, const.CONF_ALLOW_CONTROL,
        const.CONF_ALLOW_GRID_DISCONNECT, const.CONF_ENTITY_NAME_LANGUAGE,
    }

    # The actions validate and coerce as Home Assistant will call them.
    start = addon._START_SCHEMA
    assert start({"mode": "charging", "duration": "60"})["duration"] == 60
    assert start({"mode": "charging", "duration": 1440, "power_limit": "3.5"})["power_limit"] == 3.5
    assert rejects(start, {"mode": "charging", "duration": 0})
    assert rejects(start, {"mode": "charging", "duration": 1441})
    assert rejects(start, {"mode": "turbo", "duration": 60})
    assert rejects(start, {"mode": "charging", "duration": 60, "power_limit": 0})
    assert addon._CANCEL_SCHEMA({}) == {}
    assert addon._CANCEL_SCHEMA({"config_entry_id": "01ENTRY"}) == {"config_entry_id": "01ENTRY"}
    print("Add-on imports, forms and action schemas pass on Home Assistant")


if __name__ == "__main__":
    asyncio.run(main())
