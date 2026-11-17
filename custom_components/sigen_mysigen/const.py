"""Constants for the Sigenergy ESS mySigen cloud add-on."""
from __future__ import annotations

DOMAIN = "sigen_mysigen"

# The local Modbus integration this add-on extends.
MODBUS_DOMAIN = "sigen"
MIN_MODBUS_VERSION = "2.0.3"
# Keys the Modbus integration stores in its plant config entries.
MODBUS_CONF_DEVICE_TYPE = "device_type"
MODBUS_DEVICE_TYPE_PLANT = "plant"

CONF_PLANT_ENTRY_ID = "plant_entry_id"
CONF_REGION = "region"
DEFAULT_REGION = "eu"

# Seconds between mySigen reads. Stored in the entry options.
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 60

# Whether Home Assistant may start and cancel the battery override.
# Stored in the entry options; off unless the user switches it on.
CONF_ALLOW_CONTROL = "allow_control"
# Whether Home Assistant may also disconnect the plant from the grid. Needs
# allow_control as well; off unless the user switches it on.
CONF_ALLOW_GRID_DISCONNECT = "allow_grid_disconnect"

# What the Start battery override button uses. Stored in the entry options;
# changing them sends nothing to the plant.
CONF_OVERRIDE_MINUTES = "override_minutes"
CONF_OVERRIDE_MODE = "override_mode"
DEFAULT_OVERRIDE_MINUTES = 60
DEFAULT_OVERRIDE_MODE = "hold_battery"
# 0 means no limit.
CONF_OVERRIDE_POWER = "override_power_kw"

# The Modbus integration's per-plant display language for entity names.
MODBUS_CONF_ENTITY_NAME_LANGUAGE = "entity_name_language"
MODBUS_ENTITY_NAME_LANGUAGE_AUTO = "auto"
ENTITY_NAME_LANGUAGES = ("da", "de", "en", "es", "fi", "fr", "it", "nb", "nl", "pl", "sv")
