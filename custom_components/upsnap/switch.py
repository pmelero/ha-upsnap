"""Power switch for UpSnap devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ACTION_SHUTDOWN, ACTION_WAKE
from .coordinator import UpSnapConfigEntry, UpSnapCoordinator
from .entity import UpSnapEntity, async_add_device_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UpSnapConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the power switches."""
    async_add_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, device: [UpSnapPowerSwitch(coordinator, device.id)],
    )


class UpSnapPowerSwitch(UpSnapEntity, SwitchEntity):
    """Turn a device on (Wake-on-LAN) or off (UpSnap shutdown command).

    While an action is in progress the switch shows the requested state and the
    ``power_state`` attribute says ``turning_on`` or ``turning_off``.
    """

    _attr_name = None
    _attr_translation_key = "power"

    def __init__(self, coordinator: UpSnapCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id, "power")

    @property
    def is_on(self) -> bool | None:
        """Return true if the device is on (or being turned on)."""
        return self.coordinator.is_on(self._device_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the detailed power state for dashboards and automations."""
        return {"power_state": self.coordinator.power_state(self._device_id)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Wake the device."""
        await self.coordinator.async_power(self._device_id, ACTION_WAKE)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Shut the device down."""
        await self.coordinator.async_power(self._device_id, ACTION_SHUTDOWN)
