"""Tests for setting up UpSnap and keeping its devices in sync."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upsnap import async_remove_config_entry_device
from custom_components.upsnap.api import UpSnapAuthError, UpSnapConnectionError
from custom_components.upsnap.const import CONF_PENDING_TIMEOUT, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, issue_registry as ir

from .conftest import DESKTOP, LAPTOP, SERVER, devices, upsnap_device


async def test_setup_and_unload(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The entry loads and unloads."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_setup_retry_when_unreachable(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Setup is retried later when UpSnap cannot be reached."""
    mock_client.get_devices.side_effect = UpSnapConnectionError("refused")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failed_starts_reauth(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Rejected credentials at setup start the reauth flow."""
    mock_client.get_devices.side_effect = UpSnapAuthError("rejected")
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_devices_created(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Each UpSnap device becomes a device with its MAC address."""
    device = upsnap_device(hass, init_integration, DESKTOP.id)
    assert device is not None
    assert device.name == "Desktop"
    assert device.manufacturer == "UpSnap"
    assert device.configuration_url == "http://upsnap.local:8090"
    assert (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:01") in device.connections


async def test_new_removed_and_returning_devices(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Devices added to UpSnap appear, removed ones are cleaned up and can return."""
    coordinator = init_integration.runtime_data

    mock_client.get_devices.return_value = devices(DESKTOP, LAPTOP, SERVER)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("switch.server") is not None

    mock_client.get_devices.return_value = devices(DESKTOP, SERVER)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert upsnap_device(hass, init_integration, LAPTOP.id) is None
    assert hass.states.get("switch.laptop") is None

    mock_client.get_devices.return_value = devices(DESKTOP, LAPTOP, SERVER)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    laptop = hass.states.get("switch.laptop")
    assert laptop is not None
    assert laptop.state == "off"


async def test_empty_device_list_keeps_devices(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    device_registry: dr.DeviceRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty list (likely a permissions problem) does not delete the devices."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.return_value = {}
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert upsnap_device(hass, init_integration, DESKTOP.id)
    assert hass.states.get("switch.desktop").state == "unavailable"
    assert caplog.text.count("UpSnap returned no devices") == 1
    issue_id = f"no_devices_{init_integration.entry_id}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "no_devices"

    mock_client.get_devices.return_value = devices(DESKTOP, LAPTOP)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("switch.desktop").state == "on"
    assert "does not generate unique IDs" not in caplog.text
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_unload_removes_repair_issue(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Unloading the entry removes its repair issue."""
    mock_client.get_devices.return_value = {}
    await init_integration.runtime_data.async_refresh()
    issue_id = f"no_devices_{init_integration.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_remove_config_entry_device(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Only devices that are no longer in UpSnap can be removed by the user."""
    desktop = upsnap_device(hass, init_integration, DESKTOP.id)
    assert desktop is not None
    assert not await async_remove_config_entry_device(hass, init_integration, desktop)

    stale = device_registry.async_get_or_create(
        config_entry_id=init_integration.entry_id,
        identifiers={(DOMAIN, "gone00000000009")},
    )
    assert await async_remove_config_entry_device(hass, init_integration, stale)


async def test_options_reload_entry(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Changing the options reloads the entry with the new action timeout."""
    old_coordinator = init_integration.runtime_data
    hass.config_entries.async_update_entry(
        init_integration, options={CONF_PENDING_TIMEOUT: 60}
    )
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.LOADED
    assert init_integration.runtime_data is not old_coordinator
    assert init_integration.runtime_data._pending_timeout == 60
    assert init_integration.runtime_data.update_interval == timedelta(seconds=10)
