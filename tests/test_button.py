"""Tests for the UpSnap restart and sleep buttons."""

from __future__ import annotations

from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import DESKTOP, LAPTOP, devices, with_status


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_buttons_created(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Every device gets a restart button; only Sleep-on-LAN devices a sleep one."""
    for entity_id in (
        "button.desktop_restart",
        "button.laptop_restart",
        "button.laptop_sleep",
    ):
        entry = entity_registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.entity_category is EntityCategory.CONFIG
    assert entity_registry.async_get("button.desktop_sleep") is None


async def test_restart(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Restarting shows the device as restarting until UpSnap is done."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.return_value = devices(
        with_status(DESKTOP, "pending"), LAPTOP
    )

    await _press(hass, "button.desktop_restart")

    mock_client.power.assert_awaited_once_with(DESKTOP.id, "reboot")
    assert hass.states.get("sensor.desktop_power_state").state == "restarting"
    assert hass.states.get("switch.desktop").state == "on"

    mock_client.get_devices.return_value = devices(DESKTOP, LAPTOP)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.desktop_power_state").state == "on"


async def test_restart_ignores_data_from_before_upsnap_accepted_it(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A poll that ran while the request was in flight cannot finish the restart."""
    coordinator = init_integration.runtime_data

    async def power(device_id: str, action: str) -> None:
        # UpSnap has not marked the device as pending yet: it still looks online.
        await coordinator.async_refresh()
        assert coordinator.power_state(DESKTOP.id) == "restarting"
        mock_client.get_devices.return_value = devices(
            with_status(DESKTOP, "pending"), LAPTOP
        )

    mock_client.power.side_effect = power
    await _press(hass, "button.desktop_restart")
    assert hass.states.get("sensor.desktop_power_state").state == "restarting"


async def test_sleep(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """The sleep button sends the sleep action."""
    mock_client.get_devices.return_value = devices(
        DESKTOP, with_status(LAPTOP, "online")
    )
    await init_integration.runtime_data.async_refresh()

    await _press(hass, "button.laptop_sleep")

    mock_client.power.assert_awaited_once_with(LAPTOP.id, "sleep")
    assert hass.states.get("sensor.laptop_power_state").state == "turning_off"
