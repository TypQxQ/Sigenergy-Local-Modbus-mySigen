# Sigenergy ESS – mySigen cloud

Optional add-on for [Sigenergy Local Modbus](https://github.com/TypQxQ/Sigenergy-Local-Modbus) in Home Assistant. It signs in to mySigen and adds a few things Modbus cannot provide to the plant device that the Modbus integration already created:

- the **temporary battery override** (Instant Manual Control): whether one is running, its mode and when it ends;
- **Gateway grid-side readings**: voltage and current per phase, frequency and the contactor state, on systems with a Gateway;
- optional **actions to start and cancel a timed Hold Battery**, which the plant ends by itself when the time is up.

**Status: beta.** This project is unofficial and not affiliated with Sigenergy. It uses the same private interface as the mySigen app, which Sigenergy can change or close at any time. Read [Limitations](#limitations) before relying on it.

## Requirements

- Home Assistant 2026.11 or later.
- Sigenergy Local Modbus 2.0.3 or later, with a plant already set up.
- A mySigen account that has access to the same system.

The add-on does nothing by itself. It refuses to set up without the Modbus integration and a plant.

## Installation

1. In Home Assistant, open **Settings → Marketplace**, add this repository as a custom repository of type integration, and install **Sigenergy ESS – mySigen cloud**.
2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration** and choose **Sigenergy ESS – mySigen cloud**.

## Setup

Use a separate mySigen account for Home Assistant. Signing in with the account you use in the app can sign the app out.

1. In the mySigen app, sign in as the system owner and share the system with a new email address. Choose **View and Edit** if you want Home Assistant to control the battery; a view-only account can only read.
2. Activate the new account from the email mySigen sends.
3. In the setup dialog, pick the plant, the region, and enter that email and password.

The add-on checks that the account's system contains the inverter of the plant you picked, and refuses the account otherwise.

To change the account, the polling interval (15 to 60 seconds) or the control setting later, open the connection's menu and choose **Reconfigure**.

## Entities

All entities appear on the existing plant device. The add-on only adds what the Modbus integration does not provide; see [docs/PLAN.md](docs/PLAN.md) for what is planned.

| Entity | Meaning | Default |
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
| Generator voltage and current per phase, power, frequency, contactor | Readings of a generator connected to the Gateway. | Disabled |

The Gateway entities are only created when mySigen returns Gateway readings. Cloud values are read every 15 to 60 seconds; keep using the Modbus entities for anything fast.

Grid frequency and the grid connection state are not included, because the Modbus integration already has them.

## Controlling the battery

Control is off until you switch on **Allow battery control** under **Reconfigure**. The controls below only exist while it is on.

A battery override makes the battery do one thing for a set time. When the time is up the plant returns to its normal strategy by itself, also when Home Assistant is stopped. Sigen AI stays the operating mode throughout.

### From a dashboard

| Control | Type | What it does |
| --- | --- | --- |
| Battery override to start | Dropdown | What the battery should do. Today: **Hold battery (no charging, no discharging)**. Changing it sends nothing. |
| Battery override duration | Number, 30 to 120 minutes | How long the override should last. Changing it sends nothing. |
| Start battery override now | Button | Starts the chosen override for the chosen duration. |
| Stop battery override now | Button | Ends the running override, including one started in the mySigen app. |

### From an automation

| Action | What it does |
| --- | --- |
| `sigen_mysigen.start_battery_override` | Starts a timed override. `mode` is `hold_battery`, `duration` is 30 to 120 minutes. |
| `sigen_mysigen.cancel_battery_override` | Ends the running override. |

```yaml
action: sigen_mysigen.start_battery_override
data:
  mode: hold_battery
  duration: 60
```

In the dropdown and in automations the option is `hold_battery` in every language; only the text shown to the user is translated.

### What to expect

A start is refused, and nothing is sent, when:

- another override is already running (stop it first);
- Remote EMS is on in the Modbus integration, or its state cannot be read;
- mySigen has asked for fewer requests.

Each request is sent once and never repeated. After mySigen accepts it, the add-on reads the state back, and the command only succeeds when the plant shows the expected state. **Last battery override result** shows the outcome:

| State | Meaning |
| --- | --- |
| Confirmed | The plant shows the requested state. |
| Refused | Nothing was changed. |
| Outcome unknown | The answer was lost. Check the override entities; the request was not sent again. |
| Not confirmed | mySigen accepted the request but the plant does not show it. |
| Nothing to cancel | No override was running. |

A failed action or button press raises an error, so an automation can react to it.

If Home Assistant is unavailable, an override can always be stopped in the mySigen app.

## Limitations

- **Unofficial interface.** Sigenergy has not confirmed that integrations may use it. Sign-in has reportedly stopped working for new setups in some regions after a mySigen update. If that happens here, the add-on stops working and there is no supported workaround.
- **Verified on one system:** a European account, one inverter, one Gateway. Other regions, models and firmware are untested.
- **One system per account.** The add-on uses the system the account opens by default.
- **Only Hold Battery can be started.** Charging, discharging and self-consumption are shown when started elsewhere, but cannot be started from Home Assistant yet.
- **Eleven languages, nine of them unreviewed.** Danish, German, English, Spanish, Finnish, French, Italian, Norwegian, Dutch, Polish and Swedish, the same as Sigenergy Local Modbus. Only English and Swedish have been checked by a speaker. Entity names follow the plant's **Entity name language** setting in the Modbus integration.
- **The password is stored** in Home Assistant so the add-on can sign in again.

## Troubleshooting

Download diagnostics from the connection's menu and attach the file to an issue. It contains no password, email, token, station ID or serial number.

- **A repair notice says the account belongs to a different system:** the inverter was replaced, sharing was removed, or the account changed. Reconfigure with an account that has access.
- **A repair notice says the account is not allowed to read:** check in the mySigen app that the system is shared with the account.
- **Entities are unavailable but the Modbus entities work:** mySigen is unreachable or limiting requests. The Modbus integration is not affected.

## Removing

Delete the connection under **Settings → Devices & services**. Its entities are removed and the Modbus plant is untouched.

## License

MIT. Request formats follow [solidfox/sigenergy-cloud](https://github.com/solidfox/sigenergy-cloud) (MIT).
