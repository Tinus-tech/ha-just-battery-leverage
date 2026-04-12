"""Config flow for Just Leverage Battery."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_MARSTEK_DEVICE_ID,
    CONF_PRICE_SENSOR,
    CONF_PRICE_FORECAST_ATTR,
    CONF_CHEAP_HOURS,
    CONF_EXPENSIVE_HOURS,
    CONF_MIN_SOC,
    CONF_MAX_SOC,
    CONF_CHARGE_POWER,
    CONF_DISCHARGE_POWER,
    CONF_STRATEGY,
    CONF_MIN_PRICE_DELTA,
    CONF_GRID_POWER_SENSOR,
    CONF_TARGET_GRID_POWER,
    CONF_PID_KP,
    CONF_PID_KI,
    CONF_PID_KD,
    CONF_PID_DEADBAND,
    CONF_PEAK_SHAVE_LIMIT,
    CONF_TARGET_SOC,
    CONF_TIMED_PERIOD_A_START,
    CONF_TIMED_PERIOD_A_END,
    CONF_TIMED_PERIOD_A_STRATEGY,
    CONF_TIMED_PERIOD_B_ENABLED,
    CONF_TIMED_PERIOD_B_START,
    CONF_TIMED_PERIOD_B_END,
    CONF_TIMED_PERIOD_B_STRATEGY,
    CONF_TIMED_PERIOD_C_ENABLED,
    CONF_TIMED_PERIOD_C_START,
    CONF_TIMED_PERIOD_C_END,
    CONF_TIMED_PERIOD_C_STRATEGY,
    DEFAULT_CHEAP_HOURS,
    DEFAULT_EXPENSIVE_HOURS,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_CHARGE_POWER,
    DEFAULT_DISCHARGE_POWER,
    DEFAULT_PRICE_FORECAST_ATTR,
    DEFAULT_STRATEGY,
    DEFAULT_MIN_PRICE_DELTA,
    DEFAULT_TARGET_GRID_POWER,
    DEFAULT_PID_KP,
    DEFAULT_PID_KI,
    DEFAULT_PID_KD,
    DEFAULT_PID_DEADBAND,
    DEFAULT_PEAK_SHAVE_LIMIT,
    DEFAULT_TARGET_SOC,
    DEFAULT_TIMED_PERIOD_A_START,
    DEFAULT_TIMED_PERIOD_A_END,
    DEFAULT_TIMED_PERIOD_A_STRATEGY,
    STRATEGIES,
    TIMED_SUB_STRATEGIES,
)

# Human-readable labels for strategies (English fallback; translated via strings.json)
STRATEGY_LABELS = {
    "arbitrage": "Arbitrage (buy low / sell high)",
    "self_consumption": "Self-consumption (PID controller)",
    "charge_pv": "Solar charging + peak shaving",
    "charge": "Charge to target SOC",
    "sell": "Discharge to minimum SOC",
    "timed": "Schedule (A/B/C periods)",
    "ups": "UPS standby",
    "off": "Off",
}

SUB_STRATEGY_LABELS = {k: v for k, v in STRATEGY_LABELS.items() if k in TIMED_SUB_STRATEGIES}


def _number(min_val, max_val, step, unit=None, mode="box"):
    cfg = selector.NumberSelectorConfig(min=min_val, max=max_val, step=step, mode=mode)
    if unit:
        cfg = selector.NumberSelectorConfig(min=min_val, max=max_val, step=step, mode=mode, unit_of_measurement=unit)
    return selector.NumberSelector(cfg)


def _base_schema(current: dict) -> vol.Schema:
    """Fields common to all strategies."""
    return vol.Schema({
        vol.Required(CONF_MARSTEK_DEVICE_ID, default=current.get(CONF_MARSTEK_DEVICE_ID, "")): str,
        vol.Required(CONF_STRATEGY, default=current.get(CONF_STRATEGY, DEFAULT_STRATEGY)):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=[{"value": k, "label": v} for k, v in STRATEGY_LABELS.items()],
                mode="dropdown",
            )),
        vol.Optional(CONF_MIN_SOC, default=current.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)):
            _number(5, 50, 5, "%", "slider"),
        vol.Optional(CONF_MAX_SOC, default=current.get(CONF_MAX_SOC, DEFAULT_MAX_SOC)):
            _number(50, 100, 5, "%", "slider"),
        vol.Optional(CONF_CHARGE_POWER, default=current.get(CONF_CHARGE_POWER, DEFAULT_CHARGE_POWER)):
            _number(100, 5000, 100, "W"),
        vol.Optional(CONF_DISCHARGE_POWER, default=current.get(CONF_DISCHARGE_POWER, DEFAULT_DISCHARGE_POWER)):
            _number(100, 5000, 100, "W"),
    })


def _arbitrage_schema(current: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_PRICE_SENSOR, default=current.get(CONF_PRICE_SENSOR, "sensor.zonneplan_current_electricity_tariff")):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_PRICE_FORECAST_ATTR, default=current.get(CONF_PRICE_FORECAST_ATTR, DEFAULT_PRICE_FORECAST_ATTR)): str,
        vol.Optional(CONF_CHEAP_HOURS, default=current.get(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS)):
            _number(1, 12, 1),
        vol.Optional(CONF_EXPENSIVE_HOURS, default=current.get(CONF_EXPENSIVE_HOURS, DEFAULT_EXPENSIVE_HOURS)):
            _number(1, 12, 1),
        vol.Optional(CONF_MIN_PRICE_DELTA, default=current.get(CONF_MIN_PRICE_DELTA, DEFAULT_MIN_PRICE_DELTA)):
            _number(0.0, 0.5, 0.01, "€/kWh"),
    })


def _pid_schema(current: dict, include_peak_shave: bool = False) -> vol.Schema:
    fields = {
        vol.Required(CONF_GRID_POWER_SENSOR, default=current.get(CONF_GRID_POWER_SENSOR, "")):
            selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Optional(CONF_TARGET_GRID_POWER, default=current.get(CONF_TARGET_GRID_POWER, DEFAULT_TARGET_GRID_POWER)):
            _number(-500, 500, 10, "W"),
        vol.Optional(CONF_PID_KP, default=current.get(CONF_PID_KP, DEFAULT_PID_KP)):
            _number(0.0, 5.0, 0.1),
        vol.Optional(CONF_PID_KI, default=current.get(CONF_PID_KI, DEFAULT_PID_KI)):
            _number(0.0, 1.0, 0.01),
        vol.Optional(CONF_PID_KD, default=current.get(CONF_PID_KD, DEFAULT_PID_KD)):
            _number(0.0, 1.0, 0.01),
        vol.Optional(CONF_PID_DEADBAND, default=current.get(CONF_PID_DEADBAND, DEFAULT_PID_DEADBAND)):
            _number(0, 200, 5, "W"),
    }
    if include_peak_shave:
        fields[vol.Optional(CONF_PEAK_SHAVE_LIMIT, default=current.get(CONF_PEAK_SHAVE_LIMIT, DEFAULT_PEAK_SHAVE_LIMIT))] = \
            _number(500, 10000, 100, "W")
    return vol.Schema(fields)


def _target_soc_schema(current: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_TARGET_SOC, default=current.get(CONF_TARGET_SOC, DEFAULT_TARGET_SOC)):
            _number(5, 100, 5, "%", "slider"),
    })


def _timed_schema(current: dict) -> vol.Schema:
    sub_options = [{"value": k, "label": v} for k, v in SUB_STRATEGY_LABELS.items()]
    return vol.Schema({
        # Period A
        vol.Optional(CONF_TIMED_PERIOD_A_START, default=current.get(CONF_TIMED_PERIOD_A_START, DEFAULT_TIMED_PERIOD_A_START)):
            selector.TimeSelector(),
        vol.Optional(CONF_TIMED_PERIOD_A_END, default=current.get(CONF_TIMED_PERIOD_A_END, DEFAULT_TIMED_PERIOD_A_END)):
            selector.TimeSelector(),
        vol.Optional(CONF_TIMED_PERIOD_A_STRATEGY, default=current.get(CONF_TIMED_PERIOD_A_STRATEGY, DEFAULT_TIMED_PERIOD_A_STRATEGY)):
            selector.SelectSelector(selector.SelectSelectorConfig(options=sub_options, mode="dropdown")),
        # Period B
        vol.Optional(CONF_TIMED_PERIOD_B_ENABLED, default=current.get(CONF_TIMED_PERIOD_B_ENABLED, False)):
            selector.BooleanSelector(),
        vol.Optional(CONF_TIMED_PERIOD_B_START, default=current.get(CONF_TIMED_PERIOD_B_START, "06:00:00")):
            selector.TimeSelector(),
        vol.Optional(CONF_TIMED_PERIOD_B_END, default=current.get(CONF_TIMED_PERIOD_B_END, "09:00:00")):
            selector.TimeSelector(),
        vol.Optional(CONF_TIMED_PERIOD_B_STRATEGY, default=current.get(CONF_TIMED_PERIOD_B_STRATEGY, "off")):
            selector.SelectSelector(selector.SelectSelectorConfig(options=sub_options, mode="dropdown")),
        # Period C
        vol.Optional(CONF_TIMED_PERIOD_C_ENABLED, default=current.get(CONF_TIMED_PERIOD_C_ENABLED, False)):
            selector.BooleanSelector(),
        vol.Optional(CONF_TIMED_PERIOD_C_START, default=current.get(CONF_TIMED_PERIOD_C_START, "14:00:00")):
            selector.TimeSelector(),
        vol.Optional(CONF_TIMED_PERIOD_C_END, default=current.get(CONF_TIMED_PERIOD_C_END, "17:00:00")):
            selector.TimeSelector(),
        vol.Optional(CONF_TIMED_PERIOD_C_STRATEGY, default=current.get(CONF_TIMED_PERIOD_C_STRATEGY, "off")):
            selector.SelectSelector(selector.SelectSelectorConfig(options=sub_options, mode="dropdown")),
    })


def _strategy_schema(strategy: str, current: dict) -> vol.Schema | None:
    if strategy == "arbitrage":
        return _arbitrage_schema(current)
    if strategy == "self_consumption":
        return _pid_schema(current, include_peak_shave=False)
    if strategy == "charge_pv":
        return _pid_schema(current, include_peak_shave=True)
    if strategy in ("charge", "sell"):
        return _target_soc_schema(current)
    if strategy == "timed":
        return _timed_schema(current)
    return None  # ups / off: no extra fields


class MarstekBatteryTraderConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Just Leverage Battery."""

    VERSION = 1

    def __init__(self):
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._data.update(user_input)
            strategy = user_input.get(CONF_STRATEGY, DEFAULT_STRATEGY)
            if _strategy_schema(strategy, {}) is not None:
                return await self.async_step_strategy_config()
            return self.async_create_entry(title="Just Leverage Battery", data=self._data)

        return self.async_show_form(
            step_id="user",
            data_schema=_base_schema({}),
        )

    async def async_step_strategy_config(self, user_input=None):
        strategy = self._data.get(CONF_STRATEGY, DEFAULT_STRATEGY)
        schema = _strategy_schema(strategy, {})

        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="Just Leverage Battery", data=self._data)

        return self.async_show_form(
            step_id="strategy_config",
            data_schema=schema,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MarstekBatteryTraderOptionsFlow()


class MarstekBatteryTraderOptionsFlow(config_entries.OptionsFlow):
    """Handle options (change settings after setup)."""

    def __init__(self):
        self._data: dict = {}

    async def async_step_init(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Toon alle relevante velden op één pagina
        strategy = current.get(CONF_STRATEGY, DEFAULT_STRATEGY)
        base = _base_schema(current)
        extra = _strategy_schema(strategy, current)

        if extra:
            combined = vol.Schema({**base.schema, **extra.schema})
        else:
            combined = base

        return self.async_show_form(
            step_id="init",
            data_schema=combined,
        )
