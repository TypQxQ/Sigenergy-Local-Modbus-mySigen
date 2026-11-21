<div align="center">

<img src="https://raw.githubusercontent.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/main/custom_components/sigen_mysigen/brand/icon.png" alt="" width="96" />

# Roadmap

**What works today, what needs your help, and what comes next.**

![Working and tested](https://img.shields.io/badge/working_%26_tested-7-2ea44f?style=for-the-badge)
![Needs testers](https://img.shields.io/badge/needs_testers-4-f0a500?style=for-the-badge)
![Planned](https://img.shields.io/badge/planned-2-0969da?style=for-the-badge)

<sub>Updated 21 November 2026 · version 0.1.1b1</sub>

</div>

---

## ✅ Working and tested

| | Feature | What you get |
| :-: | --- | --- |
| 🔋 | **Battery overrides** | Hold, charge, discharge or self-consumption for 30–120 min, with an optional power limit |
| ⚙️ | **Operating mode** | Sigen AI, self-consumption, time of use, or full feed-in |
| ↗️ | **Battery export** | Allow or block the battery sending power to the grid |
| 📉 | **Peak shaving** | On or off, and the grid limit to stay under |
| 📈 | **Gateway readings** | Grid voltage and current per phase, peaks and dips, phase order |
| 🔋 | **Battery modules** | Charge level, power, cell temperature and voltage, lifetime discharge, model and rated capacity per module |
| 🌍 | **Eleven languages** | Following the plant's entity name language |

<sub>Tested on one system: a European account, one inverter with four battery modules, a Gateway, no charger.</sub>

## 🧪 Working, needs testers

| | Feature | What is missing |
| :-: | --- | --- |
| 🔌 | **Disconnect from the grid** | The status reads correctly, but no disconnect has been sent yet |
| 🚗 | **DC charger** | Charging mode, stop level, reserve and V2X have never met a real charger |
| 🗺️ | **Other systems** | Other regions, models, firmware, and systems without a Gateway |
| 🗣️ | **Translations** | Nine of the eleven languages need a native speaker |

> [!TIP]
> **Have one of these?** [Join the test →](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/discussions/20) Even a diagnostics file helps, before you change anything.

## 📋 Planned

Waiting for someone with the equipment to share diagnostics.

| | Feature | What it will do |
| :-: | --- | --- |
| 💡 | **Smart loads** | Switch each one on or off |
| 🔋 | **AC charger** | Charging mode, session energy and duration, current and voltage per phase |

## 💭 Not planned yet

Ideas that need more information about how mySigen handles them:

`charging schedules` · `timed V2X sessions` · `Sigen AI forecast` · `charger firmware` · `overrides longer than 2 hours`

## 🚫 Won't be added

| Feature | Why |
| --- | --- |
| Choosing solar or grid first for an override | mySigen's override has no such option. Use the Remote EMS modes in Sigenergy ESS. |
| Price windows | Dropped |
| Reactive power, power factor, per-phase setpoints | mySigen has no way to set them |
| Anything Sigenergy ESS already does | No duplicates: Modbus keeps power and energy readings, grid frequency and state, power limits, backup levels, charger start and stop, and alarms |

---

<div align="center">

[Install](../README.md#install) · [Full guide](USAGE.md) · [Join the test](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/discussions/20) · [Report a problem](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/issues)

</div>
