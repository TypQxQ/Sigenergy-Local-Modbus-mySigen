# Roadmap

What the add-on does today, what still needs testing, and what may come. Everything Sigenergy ESS (Modbus) already does stays there and is not repeated here.

*Updated 2026-11-17, version 0.1.0b9.*

## ✅ Working and tested

Tested on one system: a European account, one inverter, a Gateway, no charger.

- **Battery overrides:** hold, charge, discharge or self-consumption for 30 to 120 minutes, with an optional power limit, from a dashboard or an automation.
- **Operating mode:** Sigen AI, self-consumption, time of use, or full feed-in.
- **Battery export to the grid** on or off.
- **Peak shaving** on or off, and its limit.
- **Gateway readings:** grid voltage and current per phase, recent peaks and dips, phase order, model and firmware.
- **Eleven languages,** following the plant's entity name language.

## 🧪 Working, needs testers

[Help test →](https://github.com/TypQxQ/Sigenergy-Local-Modbus-mySigen/discussions/20)

- **Disconnect from the grid.** The status is read correctly, but no disconnect has been sent yet.
- **DC charger:** charging mode, stop level, the level to keep in the car, and letting the car power the home (V2X). It was built from other mySigen clients and has never met a real charger.
- **Other systems:** other regions, models, firmware, and systems without a Gateway.
- **Translations:** nine of the eleven languages need a native speaker's check.

## 📋 Planned

These need someone with the equipment to share diagnostics first.

- **Smart loads:** switch each one on or off.
- **AC charger:** charging mode, session energy and duration, and current and voltage per phase.

## 💭 Not planned yet

Ideas that need more information about how mySigen handles them:

- charging schedules, and the next scheduled charging start;
- a timed V2X session (the car supplies power for a set time);
- the Sigen AI forecast;
- charger firmware version;
- override durations outside 30 to 120 minutes.

## 🚫 Won't be added

- **Choosing solar or grid first for an override:** mySigen's override has no such option. Use the Remote EMS modes in Sigenergy ESS instead.
- **Price windows:** dropped.
- **Reactive power, power factor and per-phase setpoints:** mySigen has no way to set them.
- **Anything Modbus already has:**
  - power, energy and battery readings;
  - grid frequency and connection state;
  - import, export and battery power limits;
  - backup levels;
  - charger start and stop;
  - alarms.
