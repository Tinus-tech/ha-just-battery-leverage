"""Just Leverage Battery — HACS integration."""
from __future__ import annotations

import logging
import os

import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .coordinator import MarstekBatteryTraderCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "select", "number"]
DASHBOARD_URL_PATH = "just-leverage-battery"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Just Leverage Battery from a config entry."""
    coordinator = MarstekBatteryTraderCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    entry.async_on_unload(coordinator.async_cleanup)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    await _async_setup_dashboard(hass)

    _LOGGER.info("Just Leverage Battery gestart — strategie: %s", coordinator.strategy)
    return True


async def _async_setup_dashboard(hass: HomeAssistant) -> None:
    """Create the Lovelace dashboard on first setup."""
    yaml_path = os.path.join(os.path.dirname(__file__), "dashboard.yaml")
    if not os.path.exists(yaml_path):
        _LOGGER.warning("dashboard.yaml niet gevonden, dashboard wordt niet aangemaakt")
        return

    def _load_yaml():
        with open(yaml_path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    try:
        config = await hass.async_add_executor_job(_load_yaml)
    except Exception as exc:
        _LOGGER.warning("Kon dashboard.yaml niet laden: %s", exc)
        return

    # Save/update dashboard content
    content_store = Store(hass, 1, f"lovelace.{DASHBOARD_URL_PATH}")
    await content_store.async_save(config)

    # Register in the Lovelace dashboard list
    dashboards_store = Store(hass, 1, "lovelace_dashboards")
    data = await dashboards_store.async_load() or {"items": []}
    if not any(d.get("url_path") == DASHBOARD_URL_PATH for d in data.get("items", [])):
        data.setdefault("items", []).append({
            "id": DASHBOARD_URL_PATH,
            "url_path": DASHBOARD_URL_PATH,
            "title": "Energie",
            "icon": "mdi:lightning-bolt-outline",
            "show_in_sidebar": True,
            "require_admin": False,
        })
        await dashboards_store.async_save(data)
        _LOGGER.info("Just Leverage Battery dashboard aangemaakt — herstart HA om het te zien")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
