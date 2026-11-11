"""Base entity for UpSnap."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
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
            connections.add((CONNECTION_NETWORK_MAC, dr.format_mac(device.mac)))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            connections=connections,
            name=device.name,
            manufacturer="UpSnap",
            model="Wake-on-LAN device",
            configuration_url=coordinator.client.url,
        )

    @property
    def available(self) -> bool:
        """Return False when the device is no longer in UpSnap."""
        return super().available and self._device_id in self.coordinator.data


def async_add_device_entities(
    entry: UpSnapConfigEntry,
    async_add_entities: AddEntitiesCallback,
    factory: Callable[[UpSnapCoordinator, UpSnapDevice], Iterable[Entity]],
) -> None:
    """Add the entities of every device, also those that appear later.

    Devices added to UpSnap get their entities, and so do entities that start to
    apply to a device later (for example a restart button once a shutdown
    command is set in UpSnap).
    """
    coordinator = entry.runtime_data
    added: dict[str, set[str | None]] = {}  # device id -> unique ids added

    @callback
    def _add_new_entities() -> None:
        # Forget devices the coordinator removed from the registry (their entities
        # are gone too), so they get new entities if they come back.
        registered = {
            identifier
            for device_entry in dr.async_entries_for_config_entry(
                dr.async_get(coordinator.hass), entry.entry_id
            )
            for domain, identifier in device_entry.identifiers
            if domain == DOMAIN
        }
        for device_id in added.keys() - coordinator.data.keys() - registered:
            del added[device_id]

        new_entities: list[Entity] = []
        for device in coordinator.data.values():
            done = added.setdefault(device.id, set())
            for entity in factory(coordinator, device):
                if entity.unique_id not in done:
                    done.add(entity.unique_id)
                    new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))
