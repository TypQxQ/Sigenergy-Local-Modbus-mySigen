<div align="center">

<img src="https://raw.githubusercontent.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/main/custom_components/sigen_mysigen/brand/icon.png" alt="" width="96" />

# The full guide

**Every entity and control, and how each change is checked.**<br>
New here? Start with the [README](../README.md).

📊 [Entities](#-entities) · 🔋 [Modules](#-battery-modules) · 🔋 [Overrides](#-battery-overrides) · ⚙️ [Settings](#️-settings-that-stay) · 🔌 [Off-grid](#-disconnecting-from-the-grid) · 🚗 [DC charger](#-dc-charger) · 🛡️ [Safety](#️-how-changes-are-checked) · 🩺 [Help](#-troubleshooting)

</div>

---

## 📊 Entities

Everything appears on your existing **Sigen Plant** device. mySigen is read every 15 to 60 seconds (chosen at setup, changed under **Reconfigure**); for anything fast, keep using the Modbus entities.

Every name ends with **(mySigen)**, in every language, so you can tell these apart from the Modbus entities. With default Home Assistant naming settings, a new entity ID typically ends in `_mysigen`, for example `sensor.sigen_plant_battery_override_mode_mysigen`. Your entity ID settings can change the rest, and Home Assistant adds `_2` when an ID is already taken. Entities you already have keep their IDs. The tables below leave the suffix out.

Names follow the **Entity name language** of Sigenergy ESS, so both read the same on the plant device. To use another language for these, pick **Automatic** (the Home Assistant server language) or a language under **Reconfigure**. Changing it reloads the connection; entity IDs and names you set yourself are kept.

<sub>✅ on from the start · ⬜ disabled until you enable it</sub>

| | Entity | What it shows |
| :-: | --- | --- |
| | **Battery override** | |
| ✅ | Battery override active | On while an override runs, whoever started it |
| ✅ | Battery override mode | Charging, discharging, hold battery or self-consumption |
| ✅ | Battery override end time | When the running override stops by itself |
| ✅ | Last override command result | How the last start or stop from Home Assistant ended |
| ✅ | Last update | When mySigen last answered |
| | **Gateway** | <sub>only with a Sigenergy Gateway</sub> |
| ✅ | Gateway grid current phase A, B, C | Load on each main fuse |
| ✅ | Gateway grid voltage phase A, B, C | Whether the grid is back during a power cut |
| ✅ | Gateway firmware, model | What the Gateway reports about itself |
| ⬜ | Gateway grid voltage phase A, B, C, 1-min max / min | Highest and lowest voltage in the past minute: short peaks and dips between two readings |
| ⬜ | Gateway grid phase sequence | Whether the phases are wired in the expected order |
| ⬜ | Generator voltage, current and power per phase, total power, frequency, contactor | A generator on the Gateway; shown on every Gateway, so off by default |
| | **DC charger** | <sub>only when mySigen lists one</sub> |
| ✅ | Current DC charging mode | The charging mode in use: how the charger decides when to charge |
| ✅ | Current DC charge limit | The car's charge level where charging stops |
| ✅ | Power from EV allowed (V2X) | Whether the car may power the home |

> [!NOTE]
> Grid frequency, grid connection state, power, energy and the charger's own readings are left out on purpose: Sigenergy ESS already has them.

## 🔋 Battery modules

Sigenergy ESS reads each inverter's battery as a whole. mySigen also knows each **battery module**, so every module gets a device of its own, **Sigen battery module 1**, 2 and so on, under its inverter and listed with the other Sigenergy ESS devices. They appear by themselves when mySigen lists modules, and go away when you remove the add-on.

| | Entity | What it shows |
| :-: | --- | --- |
| ✅ | State of charge | The module's own charge level |
| ✅ | Total energy discharged | Lifetime discharge of the module, in kWh; for warranty throughput |
| ✅ | Average cell temperature | Average over the module's cells |
| ✅ | Average cell voltage | Average over the module's cells |
| ✅ | Rated capacity | The module's capacity on paper, e.g. 8.06 kWh |
| ⬜ | Power | The module's charging or discharging power; negative while discharging |
| ⬜ | Pack voltage | The module's voltage as mySigen reports it |
| ⬜ | Heating status | Whether the module is heating itself |

The device also shows the model and firmware. To keep the request count low, one module is read per poll, and each module is read again about every five minutes; with many modules or a long polling interval it takes longer. Model, firmware and rated capacity are read once a day.

> [!NOTE]
> Total energy discharged moves in steps of 10 kWh, as mySigen reports it in MWh with two decimals.

## 🔋 Battery overrides

An override makes the battery do **one thing for a set time**, then hands back to Sigen AI by itself, even if Home Assistant is down. Needs **Allow battery control**.

### On a dashboard

| | Control | Type | What it does |
| :-: | --- | --- | --- |
| 🎯 | Next override mode | Dropdown | What the battery should do; [options below](#what-the-battery-can-do). Sends nothing. |
| ⏱️ | Next override duration | Number, min | 1 to 1440 minutes (24 hours), as in the mySigen app. Sends nothing. |
| ⚡ | Next override power limit | Number, kW | Cap for charging or discharging; **0 = no limit**. Not used for hold battery or self-consumption. Disabled by default. Sends nothing. |
| ▶️ | Start battery override | Button | Starts the chosen override right away. |
| ⏹️ | Stop battery override | Button | Ends the running override right away, even one started in the app. |

#### What the battery can do

The options of the **Next override mode** dropdown. Automations use the key as `mode`.

| Option | Key | What happens |
| --- | --- | --- |
| 🛑 Hold battery | `hold_battery` | The battery keeps its charge; the home runs on solar and grid |
| ⬆️ Charge battery | `charging` | The battery charges, from the grid if solar isn't enough |
| ⬇️ Discharge battery | `discharging` | The battery supplies the home and can export |
| ☀️ Use my own solar first | `self_consumption` | Solar and battery are used before the grid |

### In an automation

```yaml
action: sigen_mysigen.start_battery_override
data:
  mode: charging      # a key from the table above
  duration: 60        # 1 to 1440 minutes
  power_limit: 3.5    # optional, kW
```

Stop it with the action `sigen_mysigen.cancel_battery_override`. The keys are the same in every language, and a failed action raises an error your automation can catch.

### What you'll see

A start is refused, with nothing sent, while another override runs or while Remote EMS is on (or can't be read). **Last override command result** then shows:

| | Result | Meaning |
| :-: | --- | --- |
| ✅ | Confirmed | The plant shows the requested state |
| ⛔ | Refused | Nothing was changed |
| ❓ | Outcome unknown | The answer was lost; the request was **not** sent again |
| ⚠️ | Not confirmed | mySigen accepted it, but the plant doesn't show it |
| ➖ | Nothing to cancel | No override was running |

> [!TIP]
> Charging from or discharging to the grid can cost or earn money, depending on your tariff. An override can't choose solar or grid first; use the Remote EMS modes in Sigenergy ESS for that. And you can always stop an override in the mySigen app.

## ⚙️ Settings that stay

Unlike overrides, these **stay until you change them back**. Need **Allow battery control**; disabled until you enable them.

| | Control | Type | What it does |
| :-: | --- | --- | --- |
| 🧠 | Operating mode | Dropdown | Sigen AI, self-consumption, time of use or full feed-in, as far as your plant offers them. Refused while Remote EMS is on. |
| ↗️ | Allow battery export to grid | Switch | On: the battery may export to the grid. Off: only solar is exported. The `in_effect` attribute shows what applies, as an installer setting can override it. |
| 📉 | Peak shaving | Switch | The battery helps keep grid power under the limit. Needs a time slot in the schedule. |
| 🎚️ | Peak shaving grid limit | Number, kW | The limit, when the schedule has one time slot. Several slots? Change them in the app. |
| 🔌 | Max grid connection current | Number, A | The most current per phase the plant draws from the grid, as in the app's *Grid Settings*. Unknown while no limit is set. Setting it also switches the limit on; switch it off in the app. It can't go above the installer's limit, shown in the `installer_limit` attribute. |

<sub>Read every ten minutes, so a change made in the app can take that long to appear here.</sub>

## 🔌 Disconnecting from the grid

**Disconnect from grid** is a switch that takes the home off the grid, so it runs on battery and solar alone; switching it off connects it again. To get it, you need a Sigenergy Gateway, and both **Allow battery control** and **Allow disconnecting from the grid**, chosen at setup or under **Reconfigure**. The switch then starts disabled: enable it in the entity settings.

> [!WARNING]
> **Off the grid, the battery drains.** When it's empty, the home loses power until you reconnect.

Before anything is sent, all of these must agree:

- ☑️ mySigen offers the switch right now, as it does in the app.
- ☑️ The plant is settled on the grid (to disconnect), or off the grid after a manual disconnect (to reconnect).
- ☑️ The Sigenergy ESS **Grid connection status** sensor says the same.

During a grid outage the plant reconnects by itself, so reconnecting is refused then. The `grid_connection` attribute shows `on_grid`, `going_off_grid`, `off_grid` or `returning_to_grid`.

## 🚗 DC charger

When mySigen lists a DC charger, its sensors appear by themselves. With **Allow battery control** on, you also get these, disabled until you enable them:

| | Control | Type | What it does |
| :-: | --- | --- | --- |
| 🔀 | DC charging mode | Dropdown | Charge now at full power, charge on solar surplus only, or charge and let the car supply power (V2X) |
| 🔝 | DC charge limit | Number, % | Charging stops when the car reaches this charge level |
| 🔒 | Minimum EV charge for V2X | Number, % | The car stops supplying power at this level, so at least this much stays in the car |
| 🏠 | Allow power from EV (V2X) | Switch | Allow or block the car powering the home, no Remote EMS needed |

Start and stop charging and the power limits stay in Sigenergy ESS. These settings exist only in mySigen, so they also work while Remote EMS is on. A charge level outside what the charger allows is refused; where mySigen gives no limits, 0 to 100 % applies to the charge limit and 20 to 100 % to the V2X minimum, as in the mySigen app.

> [!NOTE]
> **Not yet tried on a real charger.** These were built from other mySigen clients. Have a DC charger? [Help test it →](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/discussions/20)

## 🛡️ How changes are checked

Every change, from every control, follows the same four rules:

| | Rule | Meaning |
| :-: | --- | --- |
| 1️⃣ | **Allowed** | Control is switched on, and the mySigen system was matched to your plant. |
| 2️⃣ | **Fresh** | The current value is read first. Already set? Nothing is sent. |
| 3️⃣ | **Once** | Sent exactly once, never repeated, not even after a lost answer. |
| 4️⃣ | **Confirmed** | Read back; it only counts when the plant shows it. |

When mySigen asks for fewer requests, the add-on pauses and refuses changes until the pause is over.

## ⚠️ Limitations

- **Unofficial interface.** Sigenergy hasn't approved it, and a mySigen update could stop sign-in.
- **Tested on one system:** a European account, one inverter with four battery modules, a Gateway, no charger.
- **One system per account.** If the account starts opening a different system, the connection reloads and checks it against your plant again.
- **Nine of eleven languages** still need a native speaker's review.
- **Your password is stored** in Home Assistant so it can sign in again.

## 🩺 Troubleshooting

| Symptom | What to do |
| --- | --- |
| Entities unavailable, Modbus fine | mySigen is unreachable or limiting requests. Wait; Modbus is unaffected. |
| Repair: *belongs to a different system* | The inverter, sharing or account changed. Reconfigure with an account that has access. |
| Repair: *not allowed to read* | Share the system with the account with **View and Edit** in the mySigen app. |
| A change was refused | The message says why. Nothing was sent. |

Reporting a problem? Download the diagnostics from the connection's menu first; they contain no password, email, token, station ID or serial numbers. To remove the add-on, delete the connection; your Modbus plant is untouched.

---

<div align="center">

🏠 [README](../README.md) · 🗺️ [Roadmap](PLAN.md) · 🧪 [Join the test](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/discussions/20) · 🐛 [Report a problem](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/issues)

</div>
