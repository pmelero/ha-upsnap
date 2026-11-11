"""Tests for the UpSnap config, reauth, reconfigure and options flows."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upsnap.api import (
    UpSnapAuthError,
    UpSnapConnectionError,
    UpSnapError,
)
from custom_components.upsnap.const import CONF_PENDING_TIMEOUT, DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import ENTRY_DATA, URL


@pytest.fixture
def no_setup_on_reload() -> Generator[None]:
    """Do not set the entry up when a flow reloads it."""
    with patch("custom_components.upsnap.async_setup_entry", return_value=True):
        yield


USER_INPUT = {
    CONF_URL: "upsnap.local:8090/",
    CONF_USERNAME: "homeassistant",
    CONF_PASSWORD: "secret",
    CONF_VERIFY_SSL: True,
}


async def test_user_flow(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """The URL is normalized and the entry is created."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(USER_INPUT)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "UpSnap (upsnap.local)"
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == URL
    mock_client.login.assert_awaited_once()


@pytest.mark.parametrize(
    ("login_error", "found", "error"),
    [
        (UpSnapAuthError("rejected"), None, "invalid_auth"),
        (UpSnapConnectionError("refused"), None, "cannot_connect"),
        (UpSnapError("HTTP 500"), None, "unknown"),
        (None, {}, "no_devices"),
    ],
)
async def test_user_flow_errors_and_recovery(
    hass: HomeAssistant,
    mock_client: MagicMock,
    login_error: Exception | None,
    found: dict | None,
    error: str,
) -> None:
    """Errors are shown on the form and the user can fix them."""
    mock_client.login.side_effect = login_error
    if found is not None:
        devices_found = mock_client.get_devices.return_value
        mock_client.get_devices.return_value = found
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(USER_INPUT)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_client.login.side_effect = None
    if found is not None:
        mock_client.get_devices.return_value = devices_found
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(USER_INPUT)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_invalid_url(
    hass: HomeAssistant, mock_client: MagicMock
) -> None:
    """Only http and https URLs are accepted."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_URL: "ftp://upsnap.local"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}
    mock_client.login.assert_not_awaited()


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The same UpSnap server cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], dict(USER_INPUT)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("no_setup_on_reload")
async def test_reauth_flow(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """New credentials are validated and saved."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_client.login.side_effect = UpSnapAuthError("rejected")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "homeassistant", CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "homeassistant", CONF_PASSWORD: "new"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_PASSWORD] == "new"


@pytest.mark.usefixtures("no_setup_on_reload")
async def test_reconfigure_flow(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The URL and credentials can be changed without removing the entry."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    mock_client.login.side_effect = UpSnapConnectionError("refused")
    new_input = {
        **ENTRY_DATA,
        CONF_URL: "https://upsnap.example.com",
        CONF_PASSWORD: "new",
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], new_input
    )
    assert result["errors"] == {"base": "cannot_connect"}

    mock_client.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], new_input
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.unique_id == "https://upsnap.example.com"
    assert mock_config_entry.title == "UpSnap (upsnap.example.com)"
    assert mock_config_entry.data[CONF_PASSWORD] == "new"


@pytest.mark.usefixtures("no_setup_on_reload")
async def test_reconfigure_same_url(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Keeping the URL and changing only the password works."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_PASSWORD: "new"}
    )
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.unique_id == URL
    assert mock_config_entry.data[CONF_PASSWORD] == "new"


async def test_reconfigure_to_url_of_other_entry(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An entry cannot be pointed at a server that is already configured."""
    mock_config_entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="http://other.local:8090",
        data={**ENTRY_DATA, CONF_URL: "http://other.local:8090"},
    )
    other.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_URL: "http://other.local:8090"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """The action timeout is stored as whole seconds."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PENDING_TIMEOUT: 240.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options == {CONF_PENDING_TIMEOUT: 240}
