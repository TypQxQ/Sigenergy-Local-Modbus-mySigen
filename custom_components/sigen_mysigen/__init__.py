"""Sigenergy ESS mySigen cloud add-on.

Placeholder only: no mySigen requests, entities or config flow exist yet.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN  # noqa: F401


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the add-on. Nothing is started until a config flow exists."""
    return True
