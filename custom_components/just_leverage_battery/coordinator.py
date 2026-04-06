"""DataUpdateCoordinator for Just Leverage Battery."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import entity_registry as er

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
    MARSTEK_DOMAIN,
    MARSTEK_SERVICE_SET_PASSIVE,
    MARSTEK_SOC_SUFFIX,
    PASSIVE_DURATION,
    STRATEGY_ARBITRAGE,
    STRATEGY_UPS,
    STRATEGY_OFF,
    UPDATE_INTERVAL,
)
from .strategy import parse_zonneplan_forecast, should_charge, should_discharge, TradeDecision

_LOGGER = logging.getLogger(__name__)


class MarstekBatteryTraderCoordinator(DataUpdateCoordinator):
    """Coordinates the battery trading logic."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        self.config_entry = config_entry
        self._strategy = config_entry.data.get(CONF_STRATEGY, DEFAULT_STRATEGY)
        self._last_action: str = "unknown"
        self._last_reason: str = ""
        self._last_decision: TradeDecision | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    @property
    def strategy(self) -> str:
        return self._strategy

    @strategy.setter
    def strategy(self, value: str) -> None:
        self._strategy = value
        _LOGGER.info("Strategie gewijzigd naar: %s", value)

    def _get_conf(self, key, default):
        """Get from options first, then data."""
        return self.config_entry.options.get(key, self.config_entry.data.get(key, default))

    async def _async_update_data(self) -> dict:
        """Main update loop — called every UPDATE_INTERVAL seconds."""
        strategy = self._strategy

        if strategy == STRATEGY_OFF:
            self._last_action = "off"
            self._last_reason = "Strategie staat op Uit"
            return self._state_dict()

        if strategy == STRATEGY_UPS:
            self._last_action = "ups"
            self._last_reason = "UPS stand-by — batterij behoudt lading, geen export"
            await self._set_auto_mode()
            return self._state_dict()

        if strategy == STRATEGY_ARBITRAGE:
            await self._run_arbitrage()
            return self._state_dict()

        return self._state_dict()

    async def _run_arbitrage(self) -> None:
        """Execute arbitrage strategy: cheap = charge, expensive = discharge."""
        price_sensor = self._get_conf(CONF_PRICE_SENSOR, None)
        forecast_attr = self._get_conf(CONF_PRICE_FORECAST_ATTR, DEFAULT_PRICE_FORECAST_ATTR)
        n_cheap = int(self._get_conf(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS))
        n_expensive = int(self._get_conf(CONF_EXPENSIVE_HOURS, DEFAULT_EXPENSIVE_HOURS))
        min_soc = float(self._get_conf(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        max_soc = float(self._get_conf(CONF_MAX_SOC, DEFAULT_MAX_SOC))
        charge_power = int(self._get_conf(CONF_CHARGE_POWER, DEFAULT_CHARGE_POWER))
        discharge_power = int(self._get_conf(CONF_DISCHARGE_POWER, DEFAULT_DISCHARGE_POWER))
        device_id = self._get_conf(CONF_MARSTEK_DEVICE_ID, None)

        if not price_sensor or not device_id:
            _LOGGER.warning("Geen prijs-sensor of device_id geconfigureerd")
            self._last_action = "config_error"
            self._last_reason = "Geen prijs-sensor of device_id geconfigureerd"
            return

        # Get price forecast
        price_state = self.hass.states.get(price_sensor)
        if price_state is None:
            _LOGGER.warning("Prijssensor %s niet gevonden", price_sensor)
            self._last_action = "error"
            self._last_reason = f"Prijssensor {price_sensor} niet gevonden"
            return

        forecast_raw = price_state.attributes.get(forecast_attr, [])
        decision = parse_zonneplan_forecast(forecast_raw, n_cheap, n_expensive)
        self._last_decision = decision

        if decision is None:
            self._last_action = "error"
            self._last_reason = "Geen geldige prijsdata beschikbaar"
            return

        # Get current SOC
        current_soc = await self._get_soc()
        if current_soc is None:
            self._last_action = "error"
            self._last_reason = "Kan SOC niet uitlezen van Marstek"
            return

        _LOGGER.debug("SOC: %s%% | Beslissing: %s | Reden: %s", current_soc, decision.action, decision.reason)

        if should_charge(decision, current_soc, max_soc):
            await self._set_passive_mode(device_id, power=-charge_power, duration=PASSIVE_DURATION)
            self._last_action = "charging"
            self._last_reason = decision.reason

        elif should_discharge(decision, current_soc, min_soc):
            await self._set_passive_mode(device_id, power=discharge_power, duration=PASSIVE_DURATION)
            self._last_action = "discharging"
            self._last_reason = decision.reason

        else:
            # Idle — switch to auto so Marstek manages itself
            await self._set_auto_mode()
            self._last_action = decision.action  # "idle" or overridden by SOC guard
            self._last_reason = decision.reason

    async def _get_soc(self) -> float | None:
        """Try to find the Marstek SOC sensor from entity registry."""
        device_id = self._get_conf(CONF_MARSTEK_DEVICE_ID, None)
        if not device_id:
            return None

        ent_reg = er.async_get(self.hass)
        for entry in ent_reg.entities.values():
            if entry.device_id == device_id and MARSTEK_SOC_SUFFIX in entry.entity_id:
                state = self.hass.states.get(entry.entity_id)
                if state and state.state not in ("unknown", "unavailable"):
                    try:
                        return float(state.state)
                    except ValueError:
                        pass
        _LOGGER.warning("Marstek SOC sensor niet gevonden voor device %s", device_id)
        return None

    async def _set_passive_mode(self, device_id: str, power: int, duration: int) -> None:
        """Call marstek_local_api.set_passive_mode service."""
        try:
            await self.hass.services.async_call(
                MARSTEK_DOMAIN,
                MARSTEK_SERVICE_SET_PASSIVE,
                {
                    "device_id": device_id,
                    "power": power,
                    "duration": duration,
                },
                blocking=False,
            )
            direction = "laden" if power < 0 else "ontladen"
            _LOGGER.info("Marstek passive mode: %s @ %sW voor %ss", direction, abs(power), duration)
        except Exception as exc:
            _LOGGER.error("Fout bij aanroepen set_passive_mode: %s", exc)

    async def _set_auto_mode(self) -> None:
        """Press the Marstek 'Auto mode' button entity."""
        device_id = self._get_conf(CONF_MARSTEK_DEVICE_ID, None)
        if not device_id:
            return

        ent_reg = er.async_get(self.hass)
        for entry in ent_reg.entities.values():
            if entry.device_id == device_id and "auto_mode" in entry.entity_id:
                try:
                    await self.hass.services.async_call(
                        "button",
                        "press",
                        {"entity_id": entry.entity_id},
                        blocking=False,
                    )
                    _LOGGER.info("Marstek Auto mode geactiveerd")
                except Exception as exc:
                    _LOGGER.error("Fout bij activeren auto mode: %s", exc)
                return

        _LOGGER.warning("Marstek auto_mode button niet gevonden voor device %s", device_id)

    def _state_dict(self) -> dict:
        return {
            "strategy": self._strategy,
            "last_action": self._last_action,
            "last_reason": self._last_reason,
        }
