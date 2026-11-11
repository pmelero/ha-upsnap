"""Tests for the UpSnap restart and sleep buttons."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import DESKTOP, LAPTOP, SERVER, devices, with_status


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def test_buttons_follow_upsnap(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Buttons exist where UpSnap's device menu would offer the action."""
    # Desktop: shutdown command, no Sleep-on-LAN. Laptop: the other way round.
    for entity_id in ("button.desktop_restart", "button.laptop_sleep"):
        entry = entity_registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert entry.entity_category is EntityCategory.CONFIG
    assert entity_registry.async_get("button.desktop_sleep") is None
    assert entity_registry.async_get("button.laptop_restart") is None

    # Like in UpSnap, they can only be used while the device is on.
    assert hass.states.get("button.desktop_restart").state != "unavailable"
    assert hass.states.get("button.laptop_sleep").state == "unavailable"


async def test_old_buttons_removed(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Restart buttons created by older versions where UpSnap offers none go away."""
    mock_config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        "button",
        "upsnap",
        f"{LAPTOP.id}_reboot",
        config_entry=mock_config_entry,
        suggested_object_id="laptop_restart",
    )
    assert entity_registry.async_get("button.laptop_restart") is not None

    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert entity_registry.async_get("button.laptop_restart") is None
    assert entity_registry.async_get("button.desktop_restart") is not None


async def test_buttons_appear_and_need_permission(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    entity_registry: er.EntityRegistry,
) -> None:
    """Buttons appear when UpSnap starts to offer the action, if allowed."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.return_value = devices(
        DESKTOP, replace(LAPTOP, status="online", has_shutdown_cmd=True), SERVER
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # A shutdown command was added to the laptop in UpSnap.
    assert hass.states.get("button.laptop_restart").state != "unavailable"
    # No Power permission on the server: no restart button, like in UpSnap.
    assert hass.states.get("switch.server") is not None
    assert entity_registry.async_get("button.server_restart") is None


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
    # Not available while the restart is in progress.
    assert hass.states.get("button.desktop_restart").state == "unavailable"

    mock_client.get_devices.return_value = devices(DESKTOP, LAPTOP)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.desktop_power_state").state == "on"
    assert hass.states.get("button.desktop_restart").state != "unavailable"


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


async def test_long_restart(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A restart keeps showing as restarting for as long as UpSnap works on it."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.return_value = devices(
        with_status(DESKTOP, "pending"), LAPTOP
    )
    await _press(hass, "button.desktop_restart")

    # Shutdown, the 15 s pause and the wake can take over 4 minutes in UpSnap.
    freezer.tick(timedelta(seconds=250))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.desktop_power_state").state == "restarting"

    # Pressing the switch meanwhile is refused instead of interrupting the restart.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: "switch.desktop"},
            blocking=True,
        )

    mock_client.get_devices.return_value = devices(DESKTOP, LAPTOP)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.desktop_power_state").state == "on"


async def test_upsnap_stuck(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If UpSnap never finishes, the action stops being tracked after 10 minutes."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.return_value = devices(
        with_status(DESKTOP, "pending"), LAPTOP
    )
    await _press(hass, "button.desktop_restart")

    freezer.tick(timedelta(seconds=599))
    await coordinator.async_refresh()
    assert coordinator.power_state(DESKTOP.id) == "restarting"

    freezer.tick(timedelta(seconds=2))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    # UpSnap still says pending: shown as an action started outside Home Assistant.
    assert coordinator.pending_actions() == {}
    assert "Desktop did not turn on within 600 seconds after the reboot" in caplog.text


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
