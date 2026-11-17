# Using Sigenergy ESS – mySigen cloud

The full reference. Installation and setup are in the [README](../README.md).

- [Entities](#entities)
- [Battery override](#battery-override)
- [Settings that stay until changed back](#settings-that-stay-until-changed-back)
- [Disconnecting from the grid](#disconnecting-from-the-grid)
- [DC charger](#dc-charger)
- [How changes are checked](#how-changes-are-checked)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)

## Entities

All entities appear on the existing plant device. Cloud values are read every 15 to 60 seconds (set under **Reconfigure**); keep using the Modbus entities for anything fast.

| Entity | What it shows | Default |
| --- | --- | --- |
| Battery override active | On while a timed battery override is running, whoever started it. | Enabled |
| Battery override mode | Which override is running: charging, discharging, hold battery or self-consumption. | Enabled |
| Battery override ends | When the running override ends by itself. | Enabled |
| Last battery override result | How the last start or stop sent from Home Assistant ended. | Enabled |
| mySigen last update | When mySigen last answered. Stays readable during an outage. | Enabled |
| Gateway grid current phase A, B, C | Amperes each phase draws from or sends to the grid. Use it to watch the load on each main fuse. | Enabled |
| Gateway grid voltage phase A, B, C | Grid voltage at the connection point. During a power cut it shows whether the grid is back. | Enabled |
| Gateway firmware version, Gateway model | What the Gateway reports about itself. | Enabled |
| Gateway grid highest / lowest voltage in the past minute, phase A, B, C | Short peaks and dips between two readings. | Disabled |
| Gateway grid phase sequence | Whether the phases are connected in the expected order. | Disabled |
| Generator voltage and current per phase, power, frequency, contactor | Readings of a generator connected to the Gateway. mySigen reports these on every Gateway, also without a generator. | Disabled |
| DC charger charging mode in use | How the DC charger decides when to charge. | Enabled |
| DC charger stops charging at vehicle level | The charge level at which charging stops. | Enabled |
| DC charger car may supply power | Whether the car may supply power to the home. | Enabled |

The Gateway entities are only created when mySigen returns Gateway readings; a system without a Gateway is asked again once an hour. The DC charger entities are only created when mySigen lists a DC charger.

Grid frequency, the grid connection state, power and energy readings, and the charger's own readings are not included, because Sigenergy ESS already has them.

## Battery override

A battery override makes the battery do one thing for a set time. When the time is up the plant returns to its normal strategy by itself, also when Home Assistant is stopped. Sigen AI stays the operating mode throughout. These controls exist while **Allow battery control** is on.

### From a dashboard

| Control | Type | What it does |
| --- | --- | --- |
| Battery override to start | Dropdown | What the battery should do. See the options below. Changing it sends nothing. |
| Battery override duration | Number, 30 to 120 minutes | How long the override should last. Changing it sends nothing. |
| Battery override power limit (0 = no limit) | Number, kW | The most power a charge or discharge override may use. Disabled by default. Changing it sends nothing. |
| Start battery override now | Button | Starts the chosen override for the chosen duration. |
| Stop battery override now | Button | Ends the running override, including one started in the mySigen app. |

| Option | Key in automations | What happens |
| --- | --- | --- |
| Hold battery (no charging, no discharging) | `hold_battery` | The battery keeps its charge. The home runs on solar and grid. |
| Charge battery | `charging` | The battery charges, from the grid if solar is not enough. |
| Discharge battery | `discharging` | The battery supplies the home and can export to the grid. |
| Use my own solar first (self-consumption) | `self_consumption` | Solar and battery are used before the grid. |

Charging from the grid and discharging to the grid can cost or earn money depending on your tariff. An override cannot choose whether it takes solar or grid power first; the Remote EMS modes in Sigenergy ESS remain the way to do that.

### From an automation

| Action | What it does |
| --- | --- |
| `sigen_mysigen.start_battery_override` | Starts a timed override. `mode` is one of the keys above, `duration` is 30 to 120 minutes, and the optional `power_limit` (kW) caps charging or discharging. |
| `sigen_mysigen.cancel_battery_override` | Ends the running override. |

```yaml
action: sigen_mysigen.start_battery_override
data:
  mode: charging
  duration: 60
  power_limit: 3.5
```

The keys are the same in every language; only the text shown to the user is translated. A failed action raises an error, so an automation can react to it.

A start is refused, and nothing is sent, while another override is running (stop it first) or while Remote EMS is on or its state cannot be read. **Last battery override result** shows how each command ended:

| State | Meaning |
| --- | --- |
| Confirmed | The plant shows the requested state. |
| Refused | Nothing was changed. |
| Outcome unknown | The answer was lost. Check the override entities; the request was not sent again. |
| Not confirmed | mySigen accepted the request but the plant does not show it. |
| Nothing to cancel | No override was running. |

If Home Assistant is unavailable, an override can always be stopped in the mySigen app.

## Settings that stay until changed back

Unlike an override, these do not end by themselves. They exist while **Allow battery control** is on and start disabled.

| Control | Type | What it does |
| --- | --- | --- |
| Operating mode | Dropdown | How the plant runs day to day: Sigen AI, Use my own power first (maximum self-consumption), Time of use (TOU) or Send all solar to the grid (fully fed to grid), as far as the plant offers them. Refused while Remote EMS is on. Shows no option while the plant runs in Remote EMS or a custom profile. |
| Allow battery to export to the grid | Switch | On: the battery may send energy to the grid. Off: only solar is exported. The `in_effect` attribute shows what applies right now, since an installer setting can override it. |
| Peak shaving | Switch | On: the battery helps keep grid power under the peak shaving limit. Refused when the schedule has no time slot. |
| Peak shaving grid limit | Number, kW | The limit, when the schedule has a single time slot. A schedule with several slots is changed in the mySigen app. |

These are read every ten minutes, so a change made in the mySigen app can take that long to show. Peak shaving changes write the whole schedule back with only that one value changed.

## Disconnecting from the grid

**Disconnect from the grid (run on battery and solar)** is a switch that takes the plant off the grid, so the home runs on battery and solar alone, and connects it again when switched off. It needs a Sigenergy Gateway and its own setting: **Allow disconnecting from the grid** under **Reconfigure**, in addition to **Allow battery control**. The switch then starts disabled.

Before sending, the add-on checks that:

- mySigen offers the switch for the plant right now, as it does in the app;
- the plant is settled on the grid (to disconnect), or off the grid after a manual disconnect (to reconnect);
- the Sigenergy ESS **Grid connection status** sensor shows the same.

During a grid outage the plant reconnects by itself, so reconnecting is refused then. A change is never delayed: during a mySigen rate-limit pause it is refused instead. The `grid_connection` attribute shows `on_grid`, `going_off_grid`, `off_grid` or `returning_to_grid`.

**Running off the grid drains the battery.** When it is empty, the home loses power until you reconnect.

## DC charger

When mySigen lists a DC charger for the station, its sensors appear (see [Entities](#entities)). While **Allow battery control** is on, these controls are added, disabled to start with:

| Control | Type | What it does |
| --- | --- | --- |
| DC charger charging mode | Dropdown | Charge now at full power, Charge on solar surplus only, or Charge and let the car supply power (V2X). |
| DC charger stop charging at vehicle level | Number, % | Charging stops when the car reaches this level. |
| DC charger keep at least this much in the car | Number, % | The car stops supplying power at this level. |
| DC charger let the car power the home (V2X) | Switch | Allows or blocks the car supplying power, without Remote EMS. |

Sigenergy ESS keeps start and stop charging and the power limits. Changes here are refused while Remote EMS is on. The charger is read every ten minutes, and the list of chargers once an hour; only the first charger mySigen lists is used.

These controls are built from other mySigen clients and have not been checked against a real charger yet. Please [report](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/discussions/20) how they behave.

## How changes are checked

Every change follows the same rules:

1. **Allowed:** control is switched on for the connection, and the mySigen system was matched to the plant when the connection started.
2. **Fresh:** the current value is read first. Nothing is sent when it already is what you asked for.
3. **Once:** the request is sent exactly once and never repeated, not even after a lost answer.
4. **Confirmed:** the value is read back, and the change only succeeds when the plant shows it. Otherwise the error says whether nothing changed, the outcome is unknown, or mySigen accepted it without showing it.

When mySigen asks for fewer requests, the add-on pauses, and changes are refused until the pause is over.

## Limitations

- **Unofficial interface.** Sigenergy has not confirmed that integrations may use it. Sign-in has reportedly stopped working for new setups in some regions after a mySigen update. If that happens here, the add-on stops working and there is no supported workaround.
- **Tested on one system:** a European account, one inverter, one Gateway, no charger. Other regions, models and firmware are untested.
- **One system per account.** The add-on uses the system the account opens by default. If that changes, the connection reloads and checks the new system against the plant again.
- **Eleven languages, nine of them unreviewed.** Danish, German, English, Spanish, Finnish, French, Italian, Norwegian, Dutch, Polish and Swedish. Only English and Swedish have been checked by a speaker.
- **The password is stored** in Home Assistant so the add-on can sign in again.

## Troubleshooting

- **Entities are unavailable while the Modbus entities work:** mySigen is unreachable or limiting requests. The Modbus integration is not affected.
- **A repair says the account belongs to a different system:** the inverter was replaced, sharing was removed, or the account changed. Reconfigure with an account that has access.
- **A repair says the account may not read:** check in the mySigen app that the system is shared with the account with **View and Edit**.
- **A change was refused:** the error message says why; nothing was sent.
- Download diagnostics from the connection's menu before reporting a problem.

To remove the add-on, delete the connection under **Settings → Devices & services**. The Modbus plant is untouched.
