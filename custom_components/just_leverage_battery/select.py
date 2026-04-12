"""Select entity to switch between battery trading strategies."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STRATEGIES, STRATEGY_OFF
from .coordinator import MarstekBatteryTraderCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MarstekBatteryTraderCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([MarstekStrategySelect(coordinator, config_entry)])


class MarstekStrategySelect(CoordinatorEntity, SelectEntity, RestoreEntity):
    """Dropdown to select the active battery trading strategy."""

    _attr_has_entity_name = True
    _attr_translation_key = "strategy_select"

    def __init__(self, coordinator: MarstekBatteryTraderCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_strategy_select"
        self._attr_icon = "mdi:strategy"
        self._attr_options = STRATEGIES
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name="Just Leverage Battery",
            manufacturer="Just Leverage",
            model="Battery Trader",
            entry_type="service",
        )

    @property
    def current_option(self) -> str:
        return self.coordinator.strategy

    async def async_added_to_hass(self) -> None:
        """Restore last strategy after HA restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in STRATEGIES:
            self.coordinator.strategy = last_state.state

    async def async_select_option(self, option: str) -> None:
        """Called when user picks a strategy in the UI."""
        self.coordinator.strategy = option
        # Trigger an immediate update so the new strategy runs right away
        await self.coordinator.async_request_refresh()
