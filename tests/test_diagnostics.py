"""Tests for the UpSnap diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upsnap.diagnostics import async_get_config_entry_diagnostics
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant

from .conftest import DESKTOP, LAPTOP, devices, with_status

REDACTED = "**REDACTED**"


async def test_diagnostics(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_client: MagicMock
) -> None:
    """Diagnostics include the device states but no credentials or addresses."""
    mock_client.get_devices.return_value = devices(
        DESKTOP, with_status(LAPTOP, "pending")
    )
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.laptop"}, blocking=True
    )

    result = await async_get_config_entry_diagnostics(hass, init_integration)

    assert result["entry"]["title"] == REDACTED
    assert result["entry"]["data"] == {
        "url": REDACTED,
        "username": REDACTED,
        "password": REDACTED,
        "verify_ssl": True,
    }
    assert result["coordinator"]["last_update_success"] is True
    assert result["coordinator"]["pending_actions"][LAPTOP.id]["action"] == "wake"
    laptop = next(device for device in result["devices"] if device["id"] == LAPTOP.id)
    assert laptop["ip"] == REDACTED
    assert laptop["mac"] == REDACTED
    assert laptop["status"] == "pending"
    assert laptop["power_state"] == "turning_on"
    assert "secret" not in str(result)
    assert "upsnap.local" not in str(result)
