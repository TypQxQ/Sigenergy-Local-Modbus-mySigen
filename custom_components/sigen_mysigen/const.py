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
