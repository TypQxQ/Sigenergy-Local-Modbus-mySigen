# Feature plan

This add-on supplements [Sigenergy Local Modbus](https://github.com/TypQxQ/Sigenergy-Local-Modbus). It only adds what Modbus does not provide. Where both could supply a value or a control, Modbus keeps it and the add-on leaves it out.

This file is updated in the same pull request as each implementation, so the **Status** column always shows where things stand.

Last updated: 2026-11-16, at version 0.1.0b4. Step A is built.

## How to read the tables

| Column | Meaning |
| --- | --- |
| **Step** | When it is built. See [Steps](#steps). |
| **Status** | **Done**: released or merged. **Planned**: agreed, not built. **Later**: wanted, but needs data or a decision first. **Removed**: existed earlier and has been taken out. |
| **Default** | **Enabled**: appears as soon as the add-on is set up. **Disabled**: created but switched off until the user enables it. **If present**: only created when the equipment exists. Generator sensors are **Disabled** instead, because mySigen reports generator values on every Gateway, also without a generator. |
| **Data** | **Have**: already read. **Free**: already inside an answer the add-on fetches on every poll, so it costs no extra request. **New read**: needs a request the add-on does not make yet. **Local**: stored in Home Assistant, nothing is sent. |

## Steps

| Step | Contents | Sends extra requests | Changes the plant |
| --- | --- | --- | --- |
| **A** | Free Gateway sensors, dashboard controls for the battery override, removal of two duplicates, clearer names, all eleven languages | No | Only through the override that already exists |
| **B** | More override modes: charge, discharge, self-consumption | No | Yes, timed and self-ending |
| **C** | Settings that stay until changed back: operating mode, battery export, peak shaving, smart loads | Yes | Yes, permanently |
| **D** | Going off-grid, tariff windows | Yes | Yes, highest risk |

Each mode or setting in steps B to D is tested on real hardware before it is switched on for users.

## Sensors

| Sensor | What it tells you | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- |
| Battery override active | On while a timed battery override is running, whoever started it. | – | Done | Enabled | Have |
| Battery override mode | Which override is running: charging, discharging, hold battery or self-consumption. | – | Done | Enabled | Have |
| Battery override ends | The time the running override ends by itself. | – | Done | Enabled | Have |
| Last battery override result | How the last start or cancel sent from Home Assistant ended: confirmed, refused, outcome unknown, not confirmed, or nothing to cancel. | – | Done | Enabled | Local |
| mySigen last update | When mySigen last answered. Shows how old the cloud values are. | – | Done | Enabled | Local |
| Gateway grid current phase A / B / C | How many amperes each phase draws from or sends to the grid. Use it to watch the load on each main fuse. | – | Done | Enabled | Have |
| Gateway grid voltage phase A / B / C | The grid voltage at the connection point. During a power cut it shows whether the grid is back, which the inverter's own voltage cannot. | – | Done | Enabled | Have |
| Gateway grid highest voltage in the past minute, phase A / B / C | The highest voltage each phase reached in the last minute. Catches short peaks between two readings. | A | Done | Disabled | Free |
| Gateway grid lowest voltage in the past minute, phase A / B / C | The lowest voltage each phase reached in the last minute. Catches short dips between two readings. | A | Done | Disabled | Free |
| Gateway grid phase sequence | Whether the three phases are connected in the expected order. A wiring check. | A | Done | Disabled | Free |
| Gateway firmware version | The software version of the Gateway. Modbus only reports the inverter's. | A | Done | Enabled | Free |
| Gateway model | The Gateway's model name. | A | Done | Enabled | Free |
| Generator voltage phase A / B / C | The voltage the generator supplies on each phase. | A | Done | Disabled | Free |
| Generator current phase A / B / C | The current the generator supplies on each phase. | A | Done | Disabled | Free |
| Generator power | How much power the generator delivers right now. | A | Done | Disabled | Free |
| Generator frequency | The frequency of the generator's output. | A | Done | Disabled | Free |
| Generator contactor | Whether the generator is connected to the home. | A | Done | Disabled | Free |
| Sigen AI forecast | The plan Sigen AI has made for the coming hours. | – | Later | Disabled | New read |
| ~~Gateway grid frequency~~ | Removed. Duplicate of the inverter's **Grid frequency** from Modbus. | A | Removed | – | – |
| ~~Gateway grid contactor~~ | Removed. Duplicate of **Grid connection status** from Modbus. | A | Removed | – | – |

### Renames made in step A

Only pre-releases exist, so names can still change. Entities that were already created keep their entity ID; a renamed entity gets its new ID on a fresh install.

| Name today | New name | Why |
| --- | --- | --- |
| Battery override | Battery override active | Says that it is an on/off state. |
| Battery override end | Battery override ends | Reads as a time. |
| Battery override command | Last battery override result | It shows a result, not a command. |
| Last mySigen update | mySigen last update | Sorts next to other mySigen entries. |

## Controls

Controls only exist when **Allow battery control** is switched on for the connection (under Reconfigure). "Default" below applies once it is on.

Every control sends its request once, checks afterwards that the plant shows the new state, and reports the result in **Last battery override result**. Names and options are written so they can be understood without this document.

### Battery override

A battery override makes the battery do one thing for a set time. When the time is up the plant goes back to its normal strategy by itself, also when Home Assistant is not running. Sigen AI stays the operating mode throughout.

| Control | Type | What it does | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- |
| Start battery override | Action | Starts a timed override from an automation or script. | – | Done | Enabled | Have |
| Cancel battery override | Action | Ends the running override, including one started in the mySigen app. | – | Done | Enabled | Have |
| Battery override duration | Number, 30 to 120 minutes | How long an override started with the button below should last. Changing it sends nothing. | A | Done | Enabled | Local |
| Battery override to start | Dropdown | What the battery should do when the button below is pressed. Changing it sends nothing. | A | Done | Enabled | Local |
| Start battery override now | Button | Starts the chosen override for the chosen duration. | A | Done | Enabled | Have |
| Stop battery override now | Button | Ends the running override straight away and returns the plant to its normal strategy. | A | Done | Enabled | Have |

Options of **Battery override to start**, as the user sees them:

| Option | What happens | Step | Status |
| --- | --- | --- | --- |
| Hold battery (no charging, no discharging) | The battery keeps its charge. The home runs on solar and grid. | – | Done |
| Charge battery | The battery charges, from the grid if solar is not enough. | B | Planned |
| Discharge battery | The battery supplies the home and can export. | B | Planned |
| Use my own solar first (self-consumption) | The plant uses solar and battery before the grid. | B | Planned |

| Control | Type | What it does | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- |
| Battery override power limit | Number, kW | The most power a charge or discharge override may use. Leave empty for no limit. | B | Planned | Disabled | Local |

### Settings that stay until changed back

Unlike an override, these do not end by themselves.

| Control | Type | What it does | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- |
| Operating mode | Dropdown | Chooses how the plant runs day to day: Sigen AI, self-consumption or time-of-use. Modbus shows the mode but cannot choose it. | C | Later | Disabled | New read |
| Allow battery to export to the grid | Switch | On: the battery may send energy to the grid. Off: only solar is exported. | C | Later | Disabled | New read |
| Peak shaving | Switch | On: the battery helps keep grid power under the limit below. | C | Later | Disabled | New read |
| Peak shaving grid limit | Number, kW | The grid power the plant tries to stay under while peak shaving is on. | C | Later | Disabled | New read |
| Smart load: *name from mySigen* | Switch, one per configured load | Turns that smart load on or off. Modbus shows its power and energy but cannot switch it. | C | Later | If present, disabled | New read |

### Highest risk

| Control | Type | What it does | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- |
| Disconnect from the grid (run on battery and solar) | Switch | On: the home is separated from the grid and runs as an island. Off: reconnects. Needs its own setting in addition to Allow battery control. | D | Later | Disabled | New read |
| Set price windows | Action | Replaces the list of buy and sell price windows used by the plant. | D | Later | Disabled | New read |

## Languages

The add-on follows the Sigenergy Local Modbus integration, so entities from both read the same on one device.

| What | How | Step | Status |
| --- | --- | --- | --- |
| Languages | The same eleven as Modbus: Danish, German, English, Spanish, Finnish, French, Italian, Norwegian Bokmål, Dutch, Polish, Swedish. | A | Done |
| Entity names | Translated in every language. | A | Done |
| Dropdown options and sensor states | Translated labels over fixed keys. The user sees "Håll batteriet"; an automation uses `hold_battery` in every language. | A | Done |
| Dialogs, errors, repair notices, actions | Translated. | A | Done |
| Entity name language | Uses the Modbus plant's **Entity name language** setting, so the add-on's entities follow the same choice: Automatic (Home Assistant's language) or a fixed language. | A | Done |
| Entity IDs | Built by Home Assistant as for Modbus. A fixed entity name language changes what is displayed, not the entity ID. | A | Done |

The nine languages other than English and Swedish are written without a native speaker and need review before a stable release. All strings live in one table per language in `scripts/build_translations.py`, which writes the translation files.

## Left to Modbus on purpose

These exist in mySigen too but are not added, because the Modbus integration already has them:

- power, energy and battery readings, including per-phase power at the grid connection;
- inverter voltage, current and frequency;
- grid import and export limits, battery charge and discharge power limits, backup and cut-off charge levels;
- EV charger readings and controls;
- plant status, alarms and smart load readings.

## Open questions

- Which durations mySigen accepts outside 30 to 120 minutes.
- Whether a system without a Gateway answers the Gateway read with an empty answer or an error.
- Whether Sigenergy permits a published integration to use this interface.
