# Just Leverage Battery

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![GitHub Release](https://img.shields.io/github/v/release/Tinus-tech/ha-just-battery-leverage)](https://github.com/Tinus-tech/ha-just-battery-leverage/releases)

A Home Assistant (HACS) integration for smart battery trading with the **Marstek Venus V3** using **dynamic electricity prices** (Zonneplan, Nordpool, or any sensor with hourly forecast data).

Controls the Marstek battery via **Modbus TCP** through the [ViperRNMC marstek_modbus](https://github.com/ViperRNMC/marstek_venus_modbus) integration. No cloud dependency, no UDP flickering — direct register writes that persist until changed.

---

## Features

| Strategy | Description |
|---|---|
| **Arbitrage** | Automatically charge during cheapest hours, discharge during most expensive hours |
| **Self-consumption** | PID controller that targets zero grid import/export |
| **Solar charging + peak shaving** | Self-consumption with solar priority and peak power limiting |
| **Charge to target** | Charge the battery to a configurable SOC target |
| **Discharge to minimum** | Discharge/sell until minimum SOC is reached |
| **Schedule (A/B/C)** | Time-based scheduler — run different strategies per time block |
| **UPS standby** | Battery stays charged as backup power — no trading |
| **Off** | Integration does nothing — Marstek controls itself |

**Safety rules (always active):**
- Configurable minimum SOC (UPS reserve — e.g. 20% always kept)
- Configurable maximum SOC (prevent overcharging)
- Manual override slider pauses all automatic control

**Multi-language support:** English, Dutch (Nederlands), Spanish (Espanol)

---

## Requirements

Install these integrations first via HACS — **Just Leverage Battery requires both**:

### 1. Marstek Venus Modbus (required)
**[ViperRNMC/marstek_venus_modbus](https://github.com/ViperRNMC/marstek_venus_modbus)**

Provides low-level Modbus TCP communication with the Marstek Venus V3 over ethernet (port 502). Just Leverage Battery uses this to:
- Read **battery SOC** (State of Charge)
- Set **force mode** (standby / charge / discharge)
- Set **charge/discharge power** levels

After installing, configure ViperRNMC and set:
- **RS485 Bedrijfsmodus** (RS485 control mode) → **ON**
- **Gebruikersmodus** (User work mode) → **Handmatig** (Manual)

### 2. Zonneplan ONE (required for arbitrage)
**[fsaris/home-assistant-zonneplan-one](https://github.com/fsaris/home-assistant-zonneplan-one)**

Provides dynamic hourly electricity prices and forecast data used by the arbitrage planning algorithm.

> **Note:** Any price sensor with a `forecast` attribute containing hourly price data can be used — Zonneplan is not strictly required. Nordpool and other dynamic price providers work too.

---

## Installation

### Via HACS (recommended)

1. Go to **HACS > Integrations > ... > Custom repositories**
2. Add: `https://github.com/Tinus-tech/ha-just-battery-leverage`
3. Category: **Integration**
4. Search **Just Leverage Battery** and install
5. Restart Home Assistant

### Manual

Copy the `custom_components/just_leverage_battery` folder to your HA `custom_components` directory and restart.

---

## Configuration

1. Go to **Settings > Devices & Services > + Add Integration**
2. Search **Just Leverage Battery**
3. Fill in the base settings:

| Field | Description |
|---|---|
| **Marstek Device ID** | Copy from Settings > Devices > your Marstek device |
| **Strategy** | Select your preferred trading strategy |
| **Min SOC %** | Minimum battery level — kept as UPS reserve |
| **Max SOC %** | Maximum charge limit |
| **Charge power (W)** | Maximum charge power (e.g. 2000W) |
| **Discharge power (W)** | Maximum discharge power (e.g. 2000W) |

Depending on the strategy, additional settings are shown:

**Arbitrage:** Price sensor, forecast attribute, cheap/expensive hours, minimum price delta

**Self-consumption / Solar charging:** Grid power sensor (P1 meter), PID tuning parameters, peak shave limit

**Charge / Sell:** Target SOC percentage

**Schedule:** Up to 3 time periods (A/B/C) with individual sub-strategies

All settings can be changed later via **Settings > Devices & Services > Just Leverage Battery > Configure**.

---

## Entities

After installation, these entities are created:

| Entity | Type | Description |
|---|---|---|
| Strategy selector | Select | Switch between trading strategies |
| Manual power | Number | Slider for manual charge/discharge control (-5000W to +5000W) |
| Trading strategy | Sensor | Currently active strategy |
| Last action | Sensor | What the integration is currently doing |
| Decision reason | Sensor | Why that decision was made |
| Battery SOC | Sensor | Current state of charge (%) |
| Price delta | Sensor | Price difference cheapest vs most expensive hour |
| Next charge window | Sensor | Timestamp of next planned charge block |
| Next discharge window | Sensor | Timestamp of next planned discharge block |
| Arbitrage plan | Sensor | Summary + full hourly plan as attribute |
| Grid power (measured) | Sensor | Current grid power for PID strategies |
| PID battery command | Sensor | Current PID output power |
| Schedule active block | Sensor | Currently active time period (A/B/C/none) |

---

## How does Arbitrage work?

Every minute, a full plan is calculated for all upcoming hours in the price forecast:

1. Fetch all future hourly prices from the price sensor's forecast attribute
2. Rank from cheapest to most expensive
3. Check if the price delta exceeds the configured threshold — if not, no trading
4. Mark the N cheapest hours as **charge** hours, the N most expensive as **discharge** hours
5. Simulate SOC hour-by-hour based on charge/discharge power and battery limits
6. Execute the planned action for the current hour:
   - **Charge** — Modbus: force_mode=charge, set_charge_power=configured power
   - **Discharge** — Modbus: force_mode=discharge, set_discharge_power=configured power
   - **Idle** — Modbus: force_mode=standby

The full plan (per hour: action, price, simulated SOC) is available as an attribute on the arbitrage plan sensor.

---

## Manual override

Use the **Manual power** slider to directly control the battery:
- Negative values = charge (e.g. -2000W)
- Positive values = discharge (e.g. 2000W)
- 0 = standby

When you move the slider, automatic strategy control is paused. The coordinator resumes when you select a strategy again.

---

## Architecture

```
Just Leverage Battery (strategy & planning)
        |
        | HA service calls (select.select_option, number.set_value)
        v
ViperRNMC marstek_modbus (Modbus TCP driver)
        |
        | Modbus TCP (port 502, ethernet)
        v
Marstek Venus V3 battery
```

---

## Finding your Marstek Device ID

1. Go to **Settings > Devices & Services > Marstek Venus Modbus**
2. Click on your battery device
3. Scroll down and copy the **Device ID**

---

## License

MIT — use at your own risk. Not affiliated with Marstek, Zonneplan, or ViperRNMC.

---

## Keywords

`home-assistant` `hacs` `marstek` `marstek-venus` `marstek-venus-v3` `marstek-modbus` `battery` `battery-trading` `battery-arbitrage` `energy-arbitrage` `dynamic-pricing` `zonneplan` `nordpool` `modbus-tcp` `solar-battery` `peak-shaving` `self-consumption` `energy-management` `smart-home` `home-battery` `thuisbatterij` `bateria-inteligente`
