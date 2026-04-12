"""Number entity for manual Marstek power control."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    slider = MarstekManualPowerNumber(hass, config_entry, coordinator)
    coordinator._power_slider = slider
    async_add_entities([slider])


class MarstekManualPowerNumber(NumberEntity):
    """Schuifregelaar voor handmatig laden/ontladen van de Marstek batterij."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, coordinator) -> None:
        self._hass = hass
        self._config_entry = config_entry
        self._coordinator = coordinator
        self._attr_unique_id = f"{config_entry.entry_id}_manual_power"
        self._attr_translation_key = "manual_power"
        self._attr_icon = "mdi:lightning-bolt"
        self._attr_native_min_value = -5000
        self._attr_native_max_value = 5000
        self._attr_native_step = 100
        self._attr_native_unit_of_measurement = "W"
        self._attr_mode = NumberMode.SLIDER
        self._attr_native_value = 0
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name="Just Leverage Battery",
            manufacturer="Just Leverage",
            model="Battery Trader",
            entry_type="service",
        )
        self._manual_override = False  # True = gebruiker heeft slider handmatig gewijzigd

    @property
    def manual_override(self) -> bool:
        return self._manual_override

    async def async_set_native_value(self, value: float) -> None:
        """Aangeroepen als de gebruiker de slider handmatig wijzigt."""
        power = int(value)

        # Markeer als handmatige override — coordinator pauzeert
        self._manual_override = True
        _LOGGER.info("Handmatige override geactiveerd — coordinator gepauzeerd")

        # Stuur direct naar de Marstek via de coordinator (Modbus)
        await self._coordinator._set_battery_power(power)

        self._attr_native_value = value
        self.async_write_ha_state()

    def set_coordinator_value(self, value: int) -> None:
        """Aangeroepen door de coordinator om de slider bij te werken (geen service call)."""
        if self._manual_override:
            return  # niet overschrijven als gebruiker handmatig stuurt
        self._attr_native_value = float(value)
        self.async_write_ha_state()

    def clear_manual_override(self) -> None:
        """Coordinator mag weer sturen."""
        self._manual_override = False
        _LOGGER.info("Handmatige override opgeheven — coordinator hervat")
