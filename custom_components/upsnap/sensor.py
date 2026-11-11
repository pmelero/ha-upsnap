"""Power state sensor for UpSnap devices."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import POWER_STATES
from .coordinator import UpSnapConfigEntry
from .entity import UpSnapEntity, async_add_device_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UpSnapConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the power state sensors."""
    async_add_device_entities(
        entry,
        async_add_entities,
        lambda coordinator, device: [UpSnapPowerStateSensor(coordinator, device.id)],
    )


class UpSnapPowerStateSensor(UpSnapEntity, SensorEntity):
    """On, off, turning on, turning off or restarting."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = POWER_STATES
    _attr_translation_key = "power_state"

    def __init__(self, coordinator, device_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, "power_state")

    @property
    def native_value(self) -> str | None:
        """Return the power state."""
        return self.coordinator.power_state(self._device_id)
