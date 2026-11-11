"""The UpSnap integration."""

from __future__ import annotations

from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UpSnapClient
from .const import DOMAIN
from .coordinator import UpSnapConfigEntry, UpSnapCoordinator, no_devices_issue_id

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: UpSnapConfigEntry) -> bool:
    """Set up UpSnap from a config entry."""
    session = async_get_clientsession(
        hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True)
    )
    client = UpSnapClient(
        session,
        entry.data[CONF_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = UpSnapCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UpSnapConfigEntry) -> bool:
    """Unload a config entry."""
    ir.async_delete_issue(hass, DOMAIN, no_devices_issue_id(entry))
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: UpSnapConfigEntry) -> None:
    """Reload after the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: UpSnapConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing devices that no longer exist in UpSnap."""
    return not any(
        domain == DOMAIN and device_id in entry.runtime_data.data
        for domain, device_id in device_entry.identifiers
    )
