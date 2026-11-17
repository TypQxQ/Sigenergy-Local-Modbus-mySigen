# Feature plan

This add-on supplements [Sigenergy Local Modbus](https://github.com/TypQxQ/Sigenergy-Local-Modbus). It only adds what Modbus does not provide. Where both could supply a value or a control, Modbus keeps it and the add-on leaves it out.

This file is updated in the same pull request as each implementation, so the **Status** column always shows where things stand.

Last updated: 2026-11-17, at version 0.1.0b8. Steps A, B and C are built and tested, except smart loads. Step D is merged; its grid switch has not been tested on real hardware yet.

## How to read the tables

| Column | Meaning |
| --- | --- |
| **Step** | When it is built. See [Steps](#steps). |
| **Status** | **Done**: released or merged. **Testing**: built, waiting for a test on real hardware. **Planned**: agreed, not built. **Later**: wanted, but needs data or a decision first. **Removed**: existed earlier and has been taken out. **Not planned**: will not be built, because no way through mySigen is known or it was decided against. |
| **Default** | **Enabled**: appears as soon as the add-on is set up. **Disabled**: created but switched off until the user enables it. **If present**: only created when the equipment exists. Generator sensors are **Disabled** instead, because mySigen reports generator values on every Gateway, also without a generator. |
| **Data** | **Have**: already read. **Free**: already inside an answer the add-on fetches on every poll, so it costs no extra request. **New read**: needs a request the add-on does not make yet. **Local**: stored in Home Assistant, nothing is sent. |

## Steps

| Step | Contents | Sends extra requests | Changes the plant |
| --- | --- | --- | --- |
| **A** | Free Gateway sensors, dashboard controls for the battery override, removal of two duplicates, clearer names, all eleven languages | No | Only through the override that already exists |
| **B** | More override modes: charge, discharge, self-consumption | No | Yes, timed and self-ending |
| **C** | Settings that stay until changed back: operating mode, battery export, peak shaving, smart loads | Yes | Yes, permanently |
| **D** | Going off-grid | Yes | Yes, highest risk |
| **E** | AC and DC charger sensors and controls that Modbus does not have, or that Modbus only offers with Remote EMS on | Yes | Yes, charging and vehicle discharging |
| **F** | What Modbus can only do with Remote EMS on, offered through mySigen so it also works in Sigen AI mode | Mostly no | Yes, timed and self-ending |

Each mode or setting in steps B to F is tested on real hardware before it is switched on for users.

The steps are groups, not a fixed order. Step F is largely done already through step B.

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
| Charge battery | The battery charges, from the grid if solar is not enough. | B | Done |
| Discharge battery | The battery supplies the home and can export. | B | Done |
| Use my own solar first (self-consumption) | The plant uses solar and battery before the grid. | B | Done |

| Control | Type | What it does | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- |
| Battery override power limit (0 = no limit) | Number, 0 to 100 kW | The most power a charge or discharge override may use. 0 means no limit. The start action takes the same value as `power_limit`. | B | Done | Disabled | Local |

### Settings that stay until changed back

These are read when the connection starts and every ten minutes after that, and only while **Allow battery control** is on. A change reads the setting back at once. Each change follows the same rules as the battery override: nothing is sent when the fresh read already shows the requested value, the request is sent once, and it only succeeds when the plant shows the new value. Peak shaving changes write the whole schedule back, with everything else unchanged.

Unlike an override, these do not end by themselves.

| Control | Type | What it does | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- |
| Operating mode | Dropdown | Chooses how the plant runs day to day: **Sigen AI**, **Use my own power first (maximum self-consumption)**, **Time of use (TOU)** or **Send all solar to the grid (fully fed to grid)**, as far as the plant offers them. Shows no option while Remote EMS or a custom profile is in use. Refused while Remote EMS is on. Modbus shows the mode but cannot choose it. | C | Done | Disabled | New read |
| Allow battery to export to the grid | Switch | On: the battery may send energy to the grid. Off: only solar is exported. The `in_effect` attribute shows what applies, since an installer setting can override it. | C | Done | Disabled | New read |
| Peak shaving | Switch | On: the battery helps keep grid power under the limit below. Refused when the schedule has no time slot. | C | Done | Disabled | New read |
| Peak shaving grid limit | Number, kW | The grid power the plant tries to stay under while peak shaving is on. Only available when the schedule has a single time slot; a schedule with several slots is changed in the mySigen app. | C | Done | Disabled | New read |
| Smart load: *name from mySigen* | Switch, one per configured load | Turns that smart load on or off. Modbus shows its power and energy but cannot switch it. Waits for a system with smart loads to build against; the dev plant has none. | C | Later | If present, disabled | New read |

### Highest risk

The grid connection is read with the other settings every ten minutes, and on every poll while the plant is on its way off or back onto the grid. A change is never delayed: during a rate-limit pause it is refused instead.

| Control | Type | What it does | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- |
| Disconnect from the grid (run on battery and solar) | Switch | On: the home is separated from the grid and runs as an island. Off: reconnects. Only exists while **Allow disconnecting from the grid** is on as well as Allow battery control. Sent only when mySigen offers the switch, its fresh read shows the plant settled in the other state, and the Modbus **Grid connection status** agrees. Reconnecting is only done after a manual disconnect, never during a grid outage. The `grid_connection` attribute shows on grid, going off grid, off grid or returning to grid. Modbus shows the grid connection but cannot change it. Merged before a hardware test; the plant's answers were read, but no disconnect has been sent yet. | D | Done | Disabled | New read |
| Set price windows | Action | Replaces the list of buy and sell price windows used by the plant. Taken out of step D. | – | Not planned | – | New read |

## Step E: EV chargers

The Modbus integration already covers the basics, and those stay there:

| Charger | Modbus already has |
| --- | --- |
| AC charger | Start charging, Stop charging, Charger output current. System state, charging power, total energy, ratings, alarms. |
| DC charger | Start charging, Stop charging, Maximum charging power limit, Maximum discharging power limit. Running state, vehicle charge level and voltage, current, power, energy and duration per session, totals, ratings. |

None of those Modbus charger controls needs Remote EMS. The one charger function that does is letting the car supply power (V2G), which Modbus only offers as a Remote EMS control mode.

**Everything below is a candidate.** No charger answer from mySigen has been read yet. As with the Gateway, the first piece of work is to read the real answer with its values masked, then keep the rows that exist and drop the rest. The development plant has a DC charger and no AC charger, so AC charger rows need a second tester.

### Charger sensors

| Sensor | What it tells you | Charger | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- |
| Charging mode in use | How the charger decides when to charge: straight away, on solar surplus only, or on a schedule. | AC, DC | E | Later | Enabled | New read |
| Next scheduled charging start | When the next scheduled charging session begins. | AC, DC | E | Later | Enabled | New read |
| Energy this session | How much energy has gone into the car in this session. Modbus has it for the DC charger only. | AC | E | Later | Enabled | New read |
| Session duration | How long the current session has lasted. Modbus has it for the DC charger only. | AC | E | Later | Enabled | New read |
| Charging current and voltage per phase | What each phase delivers to the car. Modbus has charging power only. | AC | E | Later | Disabled | New read |
| Vehicle charge level to stop at | The charge level at which charging stops. | DC | E | Later | Enabled | New read |
| Vehicle discharging allowed | Whether the car may supply power to the home or grid. | DC | E | Later | Enabled | New read |
| Charger firmware version | The charger's software version. | AC, DC | E | Later | Enabled | New read |

### Charger controls

These follow the same rules as the battery override: only created while control is allowed, sent once, checked afterwards.

| Control | Type | What it does | Charger | In Modbus | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Charging mode | Dropdown | Chooses when the charger charges: **Charge now**, **Charge on solar surplus only**, or **Charge on schedule**. | AC, DC | No | E | Later | Disabled | New read |
| Set charging schedule | Action | Sets the times at which scheduled charging runs. | AC, DC | No | E | Later | Disabled | New read |
| Stop charging at vehicle charge level | Number, % | Charging stops when the car reaches this level. | DC | No | E | Later | Disabled | New read |
| Let the car power the home | Switch or timed start | The car supplies the home, and the grid if allowed. Works without switching the plant to Remote EMS. | DC | Only with Remote EMS (V2G mode) | E | Later | Disabled | New read |
| Keep at least this much in the car | Number, % | The car stops supplying power at this charge level. | DC | No | E | Later | Disabled | New read |

## Step F: what Modbus only does with Remote EMS on

Switching on Remote EMS takes the plant out of Sigen AI mode, and it stays that way until something switches it back. The controls below reach the same result through mySigen while Sigen AI stays the operating mode, and they end by themselves.

| Modbus control (needs Remote EMS) | What it does | Through mySigen | Step | Status |
| --- | --- | --- | --- | --- |
| Remote EMS control mode: **Standby** | The battery neither charges nor discharges. | Battery override: Hold battery | B | Done |
| Remote EMS control mode: **Maximum Self Consumption** | Solar and battery are used before the grid. | Battery override: Use my own solar first | B | Done |
| Remote EMS control mode: **Command Charging (Grid First)** | The battery charges, taking grid power first. | Battery override: Charge battery | B | Done |
| Remote EMS control mode: **Command Charging (PV First)** | The battery charges, taking solar power first. | Battery override: Charge battery, with a choice of **solar first** or **grid first** | F | Later |
| Remote EMS control mode: **Command Discharging (ESS First)** | The plant sends power out, taking it from the battery first. | Battery override: Discharge battery | B | Done |
| Remote EMS control mode: **Command Discharging (PV First)** | The plant sends power out, taking it from solar first. | Battery override: Discharge battery, with a choice of **battery first** or **solar first** | F | Later |
| Remote EMS control mode: **V2G** | The car supplies power through the DC charger. | Let the car power the home | E | Later |
| Active power fixed adjustment | Asks for an exact power in kW. | Battery override power limit. It caps the power; it does not set an exact value. | B | Done |
| Active power percentage adjustment | Asks for power as a share of the rating. | Battery override power limit, in kW | B | Done |
| Reactive power, Q/S and power factor adjustments | Shape the reactive power the inverter delivers. | None known | – | Not planned |
| Per-phase active and reactive power setpoints | Set power separately for each phase. | None known | – | Not planned |

New in step F:

| Control | Type | What it does | Step | Status | Default | Data |
| --- | --- | --- | --- | --- | --- | --- |
| Charge the battery from | Dropdown | **Solar first, then grid** or **Grid first**. Used by a Charge battery override. | F | Later | Disabled | New read |
| Discharge from | Dropdown | **Battery first** or **Solar first**. Used by a Discharge battery override. | F | Later | Disabled | New read |

Whether mySigen lets an override choose its source is not known. If it does not, these two rows become Not planned, and the Remote EMS route in Modbus remains the way to do it.

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
- EV charger start and stop, current and power limits, and the readings listed under [Step E](#step-e-ev-chargers);
- plant status, alarms and smart load readings.

## Open questions

- Which durations mySigen accepts outside 30 to 120 minutes.
- Whether a system without a Gateway answers the Gateway read with an empty answer or an error. Either way it is asked again once an hour, not on every poll.
- What mySigen offers for AC and DC chargers. Every row in step E is a candidate until a real answer has been read.
- Whether an override can choose between solar and grid, or battery and solar, as its source (step F).
- Whether Sigenergy permits a published integration to use this interface.
