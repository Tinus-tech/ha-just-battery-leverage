"""Select entity to switch between battery trading strategies."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STRATEGIES, STRATEGY_OFF
from .coordinator import MarstekBatteryTraderCoordinator

STRATEGY_LABELS = {
    "arbitrage": "Arbitrage (goedkoop laden / duur ontladen)",
    "ups": "UPS Stand-by (batterij bewaken, geen handel)",
    "off": "Uit (geen automatische sturing)",
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MarstekBatteryTraderCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([MarstekStrategySelect(coordinator, config_entry)])


class MarstekStrategySelect(CoordinatorEntity, SelectEntity):
    """Dropdown to select the active battery trading strategy."""

    def __init__(self, coordinator: MarstekBatteryTraderCoordinator, config_entry: ConfigEntry):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_strategy_select"
        self._attr_name = "Batterij Strategie Kiezer"
        self._attr_icon = "mdi:strategy"
        self._attr_options = list(STRATEGY_LABELS.values())

    @property
    def current_option(self) -> str:
        return STRATEGY_LABELS.get(self.coordinator.strategy, STRATEGY_LABELS[STRATEGY_OFF])

    async def async_select_option(self, option: str) -> None:
        """Called when user picks a strategy in the UI."""
        # Reverse lookup: label → key
        key = next((k for k, v in STRATEGY_LABELS.items() if v == option), STRATEGY_OFF)
        self.coordinator.strategy = key
        # Trigger an immediate update so the new strategy runs right away
        await self.coordinator.async_request_refresh()
