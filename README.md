<div align="center">

<img src="https://raw.githubusercontent.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/main/custom_components/sigen_mysigen/brand/icon.png" alt="" width="120" />

# Sigenergy ESS – mySigen cloud

**Control your Sigenergy battery from Home Assistant, without Remote EMS.**<br>
Sigen AI stays in charge. Modbus can stay read-only.

[![Release](https://img.shields.io/github/v/release/TypQxQ/Sigenergy-Local-Modbus-mySigen?style=for-the-badge&label=release)](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/releases)
[![Testers wanted](https://img.shields.io/badge/testers-wanted-f0a500?style=for-the-badge)](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/discussions/38)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-2026.11+-18bcf2?style=for-the-badge&logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)

</div>

---

An add-on for [Sigenergy ESS](https://github.com/TypQxQ/Sigenergy-Local-Modbus) that talks to mySigen the way the app does, and adds what Modbus can't do. Everything appears on the plant device you already have.

| | Feature | What you get |
| :-: | --- | --- |
| 🔋 | **Battery overrides** | Hold, charge or discharge for 1 minute to 24 hours. They end by themselves, even if Home Assistant is down. |
| ⚙️ | **Plant settings** | Operating mode, Sigen AI strategy and vacation mode, battery export, peak shaving and max grid connection current. |
| 🔌 | **Off-grid** | Run the home on battery and solar alone. |
| 🚗 | **DC charger** | Charging mode, stop level, and letting the car power the home (V2X). |
| 🔋 | **AC charger** | Charging mode, battery boost and its cut-off level, and the energy of each charging session. |
| 💡 | **Smart loads** | Switch each smart load on the SigenStor or Gateway on or off. |
| 🌪️ | **Storm Watch** | Know when Storm Watch is charging the battery full for a weather alert. |
| ☀️ | **PV string names** | Your PV strings get the names you gave them in the app. |
| 📈 | **Gateway** | Grid voltage and current on every phase. |
| 🔋 | **Battery modules** | Charge level, cell temperature and voltage, lifetime discharge, model and rated capacity for each module. |

> [!TIP]
> **Tested on a few systems so far.** [Tell us how it runs on yours →](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/discussions/38)

<a name="install"></a>

## 🚀 Install

<a href="https://my.home-assistant.io/redirect/marketplace_repository/?owner=TypQxQ&repository=Sigenergy-Local-Modbus-mySigen&category=integration"><img src="https://my.home-assistant.io/badges/marketplace_repository.svg" alt="Open in Marketplace" /></a>

1. Click the button to add the add-on to the Marketplace. Until it is listed in the catalog, it is added as a custom repository, which needs a GitHub connection.
2. Install **Sigenergy ESS – mySigen cloud**. To try a beta, enable prereleases for this repository and pick the beta version. Turning prereleases off again offers the latest stable version as an update, which can be older than your beta.
3. Restart if Home Assistant asks.

You need Home Assistant 2026.11+ and [Sigenergy ESS](https://github.com/TypQxQ/Sigenergy-Local-Modbus) 2.0.3+ with a plant set up.

<details>
<summary>Install by hand instead</summary>

Download `sigen_mysigen.zip` from a [release](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/releases), extract it into `config/custom_components/sigen_mysigen/`, and restart. A manual install gets no update notices.

</details>

## 🔧 Set up

1. **Share your system** in the mySigen app with a *new* email address, with **View and Edit** access.
2. **Add the integration:** **Settings → Devices & services → Add integration → Sigenergy ESS – mySigen cloud**. Pick your plant and sign in with the new account.
3. **Choose the settings:** how often mySigen is read and, if you want control, **Allow battery control**. Then enable the controls you want. You can change these later under **Reconfigure**.

> [!IMPORTANT]
> Use a separate account. Signing in with the account you use in the app can sign the app out.

Every change is checked first, sent once, and only counts when the plant shows it.

---

<div align="center">

📖 [Full guide](docs/USAGE.md) · 🗺️ [Roadmap](docs/PLAN.md) · 🧪 [Join the test](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/discussions/38) · 🐛 [Report a problem](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/issues)

<sub>Unofficial and not affiliated with Sigenergy. It uses mySigen's private interface, which can change at any time.<br>
MIT licensed · request formats follow <a href="https://github.com/solidfox/sigenergy-cloud">solidfox/sigenergy-cloud</a></sub>

</div>
