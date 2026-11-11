"""Restart and sleep buttons for UpSnap devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import UpSnapDevice
from .const import ACTION_REBOOT, ACTION_SLEEP
from .coordinator import UpSnapConfigEntry, UpSnapCoordinator
from .entity import UpSnapEntity, async_add_device_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class UpSnapButtonDescription(ButtonEntityDescription):
    """Describe an UpSnap button."""

    action: str
    exists_fn: Callable[[UpSnapDevice], bool] = lambda device: True


BUTTONS: tuple[UpSnapButtonDescription, ...] = (
    UpSnapButtonDescription(
        key="reboot",
        action=ACTION_REBOOT,
        device_class=ButtonDeviceClass.RESTART,
    ),
    UpSnapButtonDescription(
        key="sleep",
        translation_key="sleep",
        action=ACTION_SLEEP,
        # Sleep needs Sleep-on-LAN to be set up for the device in UpSnap.
        exists_fn=lambda device: device.sol_enabled,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UpSnapConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the buttons."""
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

    async def async_press(self) -> None:
        """Send the action."""
        await self.coordinator.async_power(
            self._device_id, self.entity_description.action
        )
