"""Tests for the UpSnap power switch, power state sensor and power tracking."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upsnap.api import (
    UpSnapAuthError,
    UpSnapError,
    UpSnapNotFoundError,
    UpSnapPermissionError,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import DESKTOP, LAPTOP, devices, with_status


async def _switch(hass: HomeAssistant, service: str, entity_id: str) -> None:
    await hass.services.async_call(
        SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


def _power_state(hass: HomeAssistant, name: str) -> str:
    state = hass.states.get(f"sensor.{name}_power_state")
    assert state is not None
    return state.state


async def test_initial_states(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Online devices are on, offline devices are off."""
    desktop = hass.states.get("switch.desktop")
    assert desktop is not None
    assert desktop.state == STATE_ON
    assert desktop.attributes["power_state"] == "on"
    laptop = hass.states.get("switch.laptop")
    assert laptop is not None
    assert laptop.state == STATE_OFF
    assert _power_state(hass, "desktop") == "on"
    assert _power_state(hass, "laptop") == "off"
    assert init_integration.runtime_data.is_on("unknown00000000") is None


async def test_turn_on_tracks_progress(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Waking a device shows it as turning on until UpSnap reports it online."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.return_value = devices(
        DESKTOP, with_status(LAPTOP, "pending")
    )

    await _switch(hass, SERVICE_TURN_ON, "switch.laptop")

    mock_client.power.assert_awaited_once_with(LAPTOP.id, "wake")
    laptop = hass.states.get("switch.laptop")
    assert laptop is not None
    assert laptop.state == STATE_ON
    assert laptop.attributes["power_state"] == "turning_on"
    assert _power_state(hass, "laptop") == "turning_on"
    assert coordinator.update_interval == timedelta(seconds=3)

    # UpSnap gave up waiting for ping, but the computer is still booting.
    mock_client.get_devices.return_value = devices(DESKTOP, LAPTOP)
    await coordinator.async_refresh()
    assert _power_state(hass, "laptop") == "turning_on"

    mock_client.get_devices.return_value = devices(
        DESKTOP, with_status(LAPTOP, "online")
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _power_state(hass, "laptop") == "on"
    assert coordinator.update_interval == timedelta(seconds=10)


async def test_turn_off_keeps_on_until_offline(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A device shutting down is shown as turning off, not off, until it is off."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.return_value = devices(
        with_status(DESKTOP, "pending"), LAPTOP
    )

    await _switch(hass, SERVICE_TURN_OFF, "switch.desktop")

    mock_client.power.assert_awaited_once_with(DESKTOP.id, "shutdown")
    desktop = hass.states.get("switch.desktop")
    assert desktop is not None
    assert desktop.state == STATE_OFF
    assert desktop.attributes["power_state"] == "turning_off"

    mock_client.get_devices.return_value = devices(
        with_status(DESKTOP, "offline"), LAPTOP
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _power_state(hass, "desktop") == "off"


async def test_already_in_requested_state(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Turning on a device that is on (or off one that is off) does nothing."""
    await _switch(hass, SERVICE_TURN_ON, "switch.desktop")
    await _switch(hass, SERVICE_TURN_OFF, "switch.laptop")
    mock_client.power.assert_not_awaited()


async def test_action_in_progress(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """A second action while one is in progress is refused."""
    mock_client.get_devices.return_value = devices(
        DESKTOP, with_status(LAPTOP, "pending")
    )
    await _switch(hass, SERVICE_TURN_ON, "switch.laptop")

    with pytest.raises(ServiceValidationError) as err:
        await _switch(hass, SERVICE_TURN_OFF, "switch.laptop")
    assert err.value.translation_key == "action_in_progress"
    assert mock_client.power.await_count == 1


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (UpSnapPermissionError("HTTP 403"), "no_power_permission"),
        (UpSnapNotFoundError("HTTP 404"), "device_not_found"),
        (UpSnapError("HTTP 500"), "action_failed"),
    ],
)
async def test_action_errors(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    error: Exception,
    translation_key: str,
) -> None:
    """A failed action raises a translated error and leaves the state as it was."""
    mock_client.power.side_effect = error

    with pytest.raises(HomeAssistantError) as err:
        await _switch(hass, SERVICE_TURN_ON, "switch.laptop")

    assert err.value.translation_key == translation_key
    laptop = hass.states.get("switch.laptop")
    assert laptop is not None
    assert laptop.state == STATE_OFF
    assert laptop.attributes["power_state"] == "off"
    assert init_integration.runtime_data.pending_actions() == {}


async def test_action_auth_error_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Rejected credentials during an action start the reauth flow."""
    mock_client.power.side_effect = UpSnapAuthError("HTTP 401")

    with pytest.raises(HomeAssistantError) as err:
        await _switch(hass, SERVICE_TURN_ON, "switch.laptop")
    await hass.async_block_till_done()

    assert err.value.translation_key == "invalid_auth"
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_pending_timeout(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A device that never reaches the requested state stops showing as pending."""
    coordinator = init_integration.runtime_data
    await _switch(hass, SERVICE_TURN_ON, "switch.laptop")
    assert _power_state(hass, "laptop") == "turning_on"

    freezer.tick(timedelta(seconds=179))
    await coordinator.async_refresh()
    assert _power_state(hass, "laptop") == "turning_on"

    freezer.tick(timedelta(seconds=2))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _power_state(hass, "laptop") == "off"
    switch = hass.states.get("switch.laptop")
    assert switch is not None
    assert switch.state == STATE_OFF
    assert (
        "Laptop did not turn on within 180 seconds after UpSnap finished the wake"
        in caplog.text
    )


async def test_wait_counts_from_when_upsnap_finishes(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_client: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The extra wait starts when UpSnap is done, not when the action was sent."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.return_value = devices(
        DESKTOP, with_status(LAPTOP, "pending")
    )
    await _switch(hass, SERVICE_TURN_ON, "switch.laptop")

    # UpSnap waits its 2 minutes for the computer to answer ping...
    freezer.tick(timedelta(seconds=120))
    await coordinator.async_refresh()
    # ...then gives up and marks it offline, but the computer is still booting.
    mock_client.get_devices.return_value = devices(DESKTOP, LAPTOP)
    await coordinator.async_refresh()
    freezer.tick(timedelta(seconds=170))
    await coordinator.async_refresh()
    assert _power_state(hass, "laptop") == "turning_on"
    pending = coordinator.pending_actions()[LAPTOP.id]
    assert pending["upsnap_finished"] is True
    assert pending["seconds_left"] == 10

    mock_client.get_devices.return_value = devices(
        DESKTOP, with_status(LAPTOP, "online")
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _power_state(hass, "laptop") == "on"


async def test_action_started_outside_home_assistant(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """UpSnap's pending status (web UI, cron) shows as turning on/off."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.return_value = devices(
        with_status(DESKTOP, "pending"), with_status(LAPTOP, "pending")
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert _power_state(hass, "desktop") == "turning_off"
    assert _power_state(hass, "laptop") == "turning_on"
    desktop = hass.states.get("switch.desktop")
    assert desktop is not None
    assert desktop.state == STATE_ON
    assert coordinator.update_interval == timedelta(seconds=3)


async def test_switch_unavailable_when_update_fails(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Entities become unavailable while UpSnap cannot be reached."""
    coordinator = init_integration.runtime_data
    mock_client.get_devices.side_effect = UpSnapError("unreachable")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    desktop = hass.states.get("switch.desktop")
    assert desktop is not None
    assert desktop.state == STATE_UNAVAILABLE

    mock_client.get_devices.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    desktop = hass.states.get("switch.desktop")
    assert desktop is not None
    assert desktop.state == STATE_ON
