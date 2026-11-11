"""Shared fixtures for the UpSnap tests."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upsnap.api import UpSnapDevice
from custom_components.upsnap.const import DOMAIN
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

URL = "http://upsnap.local:8090"
ENTRY_DATA = {
    CONF_URL: URL,
    CONF_USERNAME: "homeassistant",
    CONF_PASSWORD: "secret",
    CONF_VERIFY_SSL: True,
}

DESKTOP = UpSnapDevice(
    id="desktop00000001",
    name="Desktop",
    ip="192.168.1.20",
    mac="AA:BB:CC:DD:EE:01",
    status="online",
    sol_enabled=False,
)
LAPTOP = UpSnapDevice(
    id="laptop000000002",
    name="Laptop",
    ip="192.168.1.21",
    mac="aa:bb:cc:dd:ee:02",
    status="offline",
    sol_enabled=True,
    has_shutdown_cmd=False,
)
SERVER = UpSnapDevice(
    id="server000000003",
    name="Server",
    ip="192.168.1.22",
    mac="",
    status="online",
    sol_enabled=False,
    can_power=False,
)


def upsnap_device(
    hass: HomeAssistant, entry: MockConfigEntry, device_id: str
) -> dr.DeviceEntry | None:
    """Return the registry device of an UpSnap device (any Home Assistant version)."""
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, device_id) in device.identifiers:
            return device
    return None


def devices(*items: UpSnapDevice) -> dict[str, UpSnapDevice]:
    """Return devices keyed by id, as the client does."""
    return {item.id: item for item in items}


def with_status(device: UpSnapDevice, status: str) -> UpSnapDevice:
    """Return a copy of a device with another UpSnap status."""
    return replace(device, status=status)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading the integration from custom_components."""


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Mock the UpSnap client used by the integration and the config flow."""
    with (
        patch("custom_components.upsnap.UpSnapClient", autospec=True) as client_class,
        patch("custom_components.upsnap.config_flow.UpSnapClient", new=client_class),
    ):
        client = client_class.return_value
        client.url = URL
        client.get_devices.return_value = devices(DESKTOP, LAPTOP)
        yield client


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for UpSnap."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="UpSnap (upsnap.local)",
        unique_id=URL,
        data=dict(ENTRY_DATA),
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up the integration with the mocked client."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry
