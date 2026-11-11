"""Base entity for UpSnap."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
    format_mac,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import UpSnapDevice
from .const import DOMAIN
from .coordinator import UpSnapConfigEntry, UpSnapCoordinator


class UpSnapEntity(CoordinatorEntity[UpSnapCoordinator]):
    """An entity that belongs to one UpSnap device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: UpSnapCoordinator, device_id: str, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{key}"
        device = coordinator.data[device_id]
        connections: set[tuple[str, str]] = set()
        if device.mac:
            connections.add((CONNECTION_NETWORK_MAC, format_mac(device.mac)))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            connections=connections,
            name=device.name,
            manufacturer="UpSnap",
            model="Wake-on-LAN device",
            configuration_url=coordinator.client.url,
        )

    @property
    def device(self) -> UpSnapDevice | None:
        """Return the latest data of this device."""
        return self.coordinator.data.get(self._device_id)

    @property
    def available(self) -> bool:
        """Return False when the device is no longer in UpSnap."""
        return super().available and self._device_id in self.coordinator.data


def async_add_device_entities(
    entry: UpSnapConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[UpSnapCoordinator, UpSnapDevice], Iterable[Entity]],
) -> None:
    """Add entities for every device, including devices added to UpSnap later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        new = [
            device
            for device_id, device in coordinator.data.items()
            if device_id not in known
        ]
        if not new:
            return
        known.update(device.id for device in new)
        async_add_entities(
            entity for device in new for entity in factory(coordinator, device)
        )

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))
