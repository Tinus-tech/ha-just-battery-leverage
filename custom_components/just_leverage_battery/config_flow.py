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
    DEFAULT_CHEAP_HOURS,
    DEFAULT_EXPENSIVE_HOURS,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_CHARGE_POWER,
    DEFAULT_DISCHARGE_POWER,
    DEFAULT_PRICE_FORECAST_ATTR,
    DEFAULT_STRATEGY,
    STRATEGIES,
)


class MarstekBatteryTraderConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Just Leverage Battery."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle initial setup step."""
        errors = {}

        if user_input is not None:
            return self.async_create_entry(
                title="Just Leverage Battery",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_MARSTEK_DEVICE_ID): str,
                vol.Required(CONF_PRICE_SENSOR, default="sensor.zonneplan_current_electricity_tariff"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_PRICE_FORECAST_ATTR, default=DEFAULT_PRICE_FORECAST_ATTR): str,
                vol.Optional(CONF_CHEAP_HOURS, default=DEFAULT_CHEAP_HOURS): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=12, step=1, mode="box")
                ),
                vol.Optional(CONF_EXPENSIVE_HOURS, default=DEFAULT_EXPENSIVE_HOURS): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=12, step=1, mode="box")
                ),
                vol.Optional(CONF_MIN_SOC, default=DEFAULT_MIN_SOC): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=50, step=5, mode="slider", unit_of_measurement="%")
                ),
                vol.Optional(CONF_MAX_SOC, default=DEFAULT_MAX_SOC): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=50, max=100, step=5, mode="slider", unit_of_measurement="%")
                ),
                vol.Optional(CONF_CHARGE_POWER, default=DEFAULT_CHARGE_POWER): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=100, max=5000, step=100, mode="box", unit_of_measurement="W")
                ),
                vol.Optional(CONF_DISCHARGE_POWER, default=DEFAULT_DISCHARGE_POWER): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=100, max=5000, step=100, mode="box", unit_of_measurement="W")
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MarstekBatteryTraderOptionsFlow(config_entry)


class MarstekBatteryTraderOptionsFlow(config_entries.OptionsFlow):
    """Handle options (change settings after setup)."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.data

        schema = vol.Schema(
            {
                vol.Optional(CONF_CHEAP_HOURS, default=current.get(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=12, step=1, mode="box")
                ),
                vol.Optional(CONF_EXPENSIVE_HOURS, default=current.get(CONF_EXPENSIVE_HOURS, DEFAULT_EXPENSIVE_HOURS)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=12, step=1, mode="box")
                ),
                vol.Optional(CONF_MIN_SOC, default=current.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=50, step=5, mode="slider", unit_of_measurement="%")
                ),
                vol.Optional(CONF_MAX_SOC, default=current.get(CONF_MAX_SOC, DEFAULT_MAX_SOC)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=50, max=100, step=5, mode="slider", unit_of_measurement="%")
                ),
                vol.Optional(CONF_CHARGE_POWER, default=current.get(CONF_CHARGE_POWER, DEFAULT_CHARGE_POWER)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=100, max=5000, step=100, mode="box", unit_of_measurement="W")
                ),
                vol.Optional(CONF_DISCHARGE_POWER, default=current.get(CONF_DISCHARGE_POWER, DEFAULT_DISCHARGE_POWER)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=100, max=5000, step=100, mode="box", unit_of_measurement="W")
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
