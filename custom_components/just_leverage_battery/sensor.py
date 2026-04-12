"""Sensor entities for Just Leverage Battery."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
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
        MarstekSocSensor(coordinator, config_entry),
        MarstekPriceDeltaSensor(coordinator, config_entry),
        MarstekNextChargeSensor(coordinator, config_entry),
        MarstekNextDischargeSensor(coordinator, config_entry),
        MarstekPlanSensor(coordinator, config_entry),
        MarstekGridPowerSensor(coordinator, config_entry),
        MarstekPidPowerSensor(coordinator, config_entry),
        MarstekTimedPeriodSensor(coordinator, config_entry),
    ])


def _device_info(config_entry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, config_entry.entry_id)},
        name="Just Leverage Battery",
        manufacturer="Just Leverage",
        model="Battery Trader",
        entry_type="service",
    )


class _Base(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, unique_suffix, translation_key, icon):
        super().__init__(coordinator)
        self._attr_unique_id = f"{config_entry.entry_id}_{unique_suffix}"
        self._attr_translation_key = translation_key
        self._attr_icon = icon
        self._attr_device_info = _device_info(config_entry)

    def _get(self, key, default=None):
        return (self.coordinator.data or {}).get(key, default)


# ---------------------------------------------------------------------------
# Strategy & action sensors
# ---------------------------------------------------------------------------

class MarstekStrategyStatusSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "strategy", "strategy", "mdi:flash-auto")

    @property
    def native_value(self):
        return self._get("strategy", "unknown")


class MarstekLastActionSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "last_action", "last_action", "mdi:battery-charging")

    @property
    def native_value(self):
        return self._get("last_action", "unknown")


class MarstekLastReasonSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "last_reason", "last_reason", "mdi:information-outline")

    @property
    def native_value(self):
        reason = self._get("last_reason", "—")
        return reason[:255] if reason else "—"


# ---------------------------------------------------------------------------
# Battery SOC
# ---------------------------------------------------------------------------

class MarstekSocSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "soc", "soc", "mdi:battery")
        self._attr_native_unit_of_measurement = "%"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._get("soc")


# ---------------------------------------------------------------------------
# Arbitrage planning sensors
# ---------------------------------------------------------------------------

class MarstekPriceDeltaSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "price_delta", "price_delta", "mdi:cash-multiple")
        self._attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self):
        return self._get("price_delta")

    @property
    def extra_state_attributes(self):
        return {"profitable": self._get("plan_profitable")}


class MarstekNextChargeSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "next_charge", "next_charge", "mdi:battery-arrow-up")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        val = self._get("next_charge_start")
        if val:
            try:
                return datetime.fromisoformat(val)
            except ValueError:
                return None
        return None

    @property
    def extra_state_attributes(self):
        return {"end": self._get("next_charge_end")}


class MarstekNextDischargeSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "next_discharge", "next_discharge", "mdi:battery-arrow-down")
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        val = self._get("next_discharge_start")
        if val:
            try:
                return datetime.fromisoformat(val)
            except ValueError:
                return None
        return None

    @property
    def extra_state_attributes(self):
        return {"end": self._get("next_discharge_end")}


class MarstekPlanSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "plan", "plan", "mdi:calendar-clock")

    @property
    def native_value(self):
        hours = self._get("plan_hours", [])
        n_charge = sum(1 for h in hours if h["action"] == "charge")
        n_discharge = sum(1 for h in hours if h["action"] == "discharge")
        return f"{n_charge}x laden, {n_discharge}x ontladen"

    @property
    def extra_state_attributes(self):
        return {"hours": self._get("plan_hours", [])}


# ---------------------------------------------------------------------------
# PID / self-consumption sensors
# ---------------------------------------------------------------------------

class MarstekGridPowerSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "grid_power", "grid_power", "mdi:transmission-tower")
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._get("pid_grid_power")


class MarstekPidPowerSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "pid_power", "pid_power", "mdi:sine-wave")
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self._get("pid_power_w")

    @property
    def extra_state_attributes(self):
        return {
            "in_deadband": self._get("pid_in_deadband"),
            "error": self._get("pid_error"),
        }


# ---------------------------------------------------------------------------
# Timed strategy sensor
# ---------------------------------------------------------------------------

class MarstekTimedPeriodSensor(_Base):
    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry, "timed_period", "timed_period", "mdi:clock-outline")

    @property
    def native_value(self):
        return self._get("timed_active_period", "geen")
