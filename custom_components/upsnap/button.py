"""Restart and sleep buttons for UpSnap devices.

They follow UpSnap's own device menu: Reboot is offered when the user has the
Power permission, the device is on and it has a shutdown command; Sleep when the
user has the Power permission, the device is on and Sleep-on-LAN is enabled.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import UpSnapDevice
from .const import ACTION_REBOOT, ACTION_SLEEP, DOMAIN, POWER_ON
from .coordinator import UpSnapConfigEntry, UpSnapCoordinator
from .entity import UpSnapEntity, async_add_device_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class UpSnapButtonDescription(ButtonEntityDescription):
    """Describe an UpSnap button."""

    action: str
    # Whether UpSnap would offer the action for the device at all.
    exists_fn: Callable[[UpSnapDevice], bool]


BUTTONS: tuple[UpSnapButtonDescription, ...] = (
    UpSnapButtonDescription(
        key="reboot",
        action=ACTION_REBOOT,
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        exists_fn=lambda device: device.can_power and device.has_shutdown_cmd,
    ),
    UpSnapButtonDescription(
        key="sleep",
        translation_key="sleep",
        action=ACTION_SLEEP,
        entity_category=EntityCategory.CONFIG,
        exists_fn=lambda device: device.can_power and device.sol_enabled,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UpSnapConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the buttons."""
    # Remove buttons left by older versions where UpSnap does not offer the action.
    registry = er.async_get(hass)
    for device in entry.runtime_data.data.values():
        for description in BUTTONS:
            if description.exists_fn(device):
                continue
            entity_id = registry.async_get_entity_id(
                BUTTON_DOMAIN, DOMAIN, f"{device.id}_{description.key}"
            )
            if entity_id is not None:
                registry.async_remove(entity_id)

    async_add_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, device: [
            UpSnapButton(coordinator, device.id, description)
            for description in BUTTONS
            if description.exists_fn(device)
        ],
    )


class UpSnapButton(UpSnapEntity, ButtonEntity):
    """Send a one-off power action to a device."""

    entity_description: UpSnapButtonDescription

    def __init__(
        self,
        coordinator: UpSnapCoordinator,
        device_id: str,
        description: UpSnapButtonDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Like UpSnap: only while the device is on and nothing else is running."""
        device = self.coordinator.data.get(self._device_id)
        return (
            super().available
            and device is not None
            and self.entity_description.exists_fn(device)
            and self.coordinator.power_state(self._device_id) == POWER_ON
        )

    async def async_press(self) -> None:
        """Send the action."""
        await self.coordinator.async_power(
            self._device_id, self.entity_description.action
        )
