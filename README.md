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

All entities appear on the existing plant device.

| Entity | Meaning |
| --- | --- |
| Battery override | On while a temporary override is running, whoever started it. |
| Battery override mode | Charging, Discharging, Hold battery or Self-consumption. Unknown when none is running. |
| Battery override end | When the running override ends. |
| Battery override command | How the last start or cancel sent from Home Assistant ended. |
| Gateway grid voltage / current phase A, B, C | Grid-side readings of the Gateway. |
| Gateway grid frequency | Grid frequency at the Gateway. |
| Gateway grid contactor | The contactor state as mySigen words it, for example `close`. |
| Last mySigen update | When mySigen last answered. Stays readable during an outage. |

The Gateway entities are only created when mySigen returns Gateway readings. Cloud values are read every 15 to 60 seconds; keep using the Modbus entities for anything fast.

## Controlling the battery

Control is off until you switch on **Allow battery control** under **Reconfigure**.

| Action | What it does |
| --- | --- |
| `sigen_mysigen.start_battery_override` | Starts a timed Hold Battery for 30 to 120 minutes. |
| `sigen_mysigen.cancel_battery_override` | Ends the running override, including one started in the app. |

```yaml
action: sigen_mysigen.start_battery_override
data:
  mode: hold_battery
  duration: 60
```

The plant runs the timer itself. When the time is up it returns to its normal strategy, also when Home Assistant is stopped. Sigen AI stays the active operating mode during the override.

A start is refused, and nothing is sent, when:

- another override is already running (cancel it first);
- Remote EMS is on in the Modbus integration, or its state cannot be read;
- mySigen has asked for fewer requests.

Each request is sent once and never repeated. After mySigen accepts it, the add-on reads the state back, and the action only succeeds when the plant shows the expected state. The **Battery override command** sensor shows the result:

| State | Meaning |
| --- | --- |
| Confirmed | The plant shows the requested state. |
| Refused | Nothing was changed. |
| Outcome unknown | The answer was lost. Check the override entities; the request was not sent again. |
| Not confirmed | mySigen accepted the request but the plant does not show it. |
| Nothing to cancel | No override was running. |

A failed action raises an error, so an automation can react to it.

If Home Assistant is unavailable, an override can always be cancelled in the mySigen app.

## Limitations

- **Unofficial interface.** Sigenergy has not confirmed that integrations may use it. Sign-in has reportedly stopped working for new setups in some regions after a mySigen update. If that happens here, the add-on stops working and there is no supported workaround.
- **Verified on one system:** a European account, one inverter, one Gateway. Other regions, models and firmware are untested.
- **One system per account.** The add-on uses the system the account opens by default.
- **Only Hold Battery can be started.** Charging, discharging and self-consumption are shown when started elsewhere, but cannot be started from Home Assistant yet.
- **English only.**
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
