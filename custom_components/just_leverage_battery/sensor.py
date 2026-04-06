"""Sensor entities for Just Leverage Battery."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MarstekBatteryTraderCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MarstekBatteryTraderCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([
        MarstekStrategyStatusSensor(coordinator, config_entry),
        MarstekLastActionSensor(coordinator, config_entry),
        MarstekLastReasonSensor(coordinator, config_entry),
    ])


class MarstekStrategyStatusSensor(CoordinatorEntity, SensorEntity):
    """Shows the active strategy."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_strategy"
        self._attr_name = "Batterij Handelsstrategie"
        self._attr_icon = "mdi:flash-auto"

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("strategy", "unknown")
        return "unknown"


class MarstekLastActionSensor(CoordinatorEntity, SensorEntity):
    """Shows what the coordinator last did."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_last_action"
        self._attr_name = "Batterij Laatste Actie"
        self._attr_icon = "mdi:battery-charging"

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("last_action", "unknown")
        return "unknown"


class MarstekLastReasonSensor(CoordinatorEntity, SensorEntity):
    """Shows why the coordinator made its last decision."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_last_reason"
        self._attr_name = "Batterij Beslissingsreden"
        self._attr_icon = "mdi:information-outline"

    @property
    def native_value(self):
        if self.coordinator.data:
            reason = self.coordinator.data.get("last_reason", "")
            # HA sensor states max 255 chars
            return reason[:255] if reason else "—"
        return "—"
