"""Diagnostics support for UpSnap."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import UpSnapConfigEntry

# Diagnostics end up attached to public bug reports.
TO_REDACT = {CONF_PASSWORD, CONF_URL, CONF_USERNAME, "ip", "mac", "title"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: UpSnapConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(
            {
                "title": entry.title,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
            "pending_actions": coordinator.pending_actions(),
        },
        "devices": [
            async_redact_data(
                {
                    **asdict(device),
                    "is_on": coordinator.is_on(device.id),
                    "power_state": coordinator.power_state(device.id),
                },
                TO_REDACT,
            )
            for device in coordinator.data.values()
        ],
    }
