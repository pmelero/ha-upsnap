"""Config flow for UpSnap."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import UpSnapAuthError, UpSnapClient, UpSnapConnectionError, UpSnapError
from .const import (
    CONF_PENDING_TIMEOUT,
    DEFAULT_PENDING_TIMEOUT,
    DOMAIN,
    MAX_PENDING_TIMEOUT,
    MIN_PENDING_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
        vol.Optional(CONF_VERIFY_SSL, default=True): bool,
    }
)


def entry_title(url: str) -> str:
    """Return the title of the config entry for an UpSnap URL."""
    return f"UpSnap ({urlparse(url).hostname or url})"


def without_password(data: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return form values to suggest again, never including the password."""
    if data is None:
        return None
    return {key: value for key, value in data.items() if key != CONF_PASSWORD}


def normalize_url(url: str) -> str:
    """Add a scheme if missing and drop the trailing slash."""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = f"http://{url}"
    return url


async def validate_input(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> dict[str, str]:
    """Log in and list devices. Return form errors (empty when everything works)."""
    if urlparse(data[CONF_URL]).scheme not in ("http", "https"):
        return {CONF_URL: "invalid_url"}
    session = async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, True))
    client = UpSnapClient(
        session, data[CONF_URL], data[CONF_USERNAME], data[CONF_PASSWORD]
    )
    try:
        await client.login()
        devices = await client.get_devices()
    except UpSnapAuthError:
        return {"base": "invalid_auth"}
    except UpSnapConnectionError:
        return {"base": "cannot_connect"}
    except UpSnapError:
        _LOGGER.exception("Unexpected error while checking UpSnap")
        return {"base": "unknown"}
    if not devices:
        return {"base": "no_devices"}
    return {}


class UpSnapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for UpSnap."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the server and the credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_URL] = normalize_url(user_input[CONF_URL])
            await self.async_set_unique_id(user_input[CONF_URL].lower())
            self._abort_if_unique_id_configured()
            errors = await validate_input(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title=entry_title(user_input[CONF_URL]), data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, without_password(user_input)
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication when UpSnap rejects the credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            errors = await validate_input(self.hass, data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                    vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
            description_placeholders={"url": entry.data[CONF_URL]},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the URL or the credentials of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_URL] = normalize_url(user_input[CONF_URL])
            unique_id = user_input[CONF_URL].lower()
            if unique_id != entry.unique_id:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
            errors = await validate_input(self.hass, user_input)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    title=entry_title(user_input[CONF_URL]),
                    data_updates=user_input,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, without_password(user_input or dict(entry.data))
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> UpSnapOptionsFlow:
        """Return the options flow."""
        return UpSnapOptionsFlow()


class UpSnapOptionsFlow(OptionsFlow):
    """How long to wait for power actions."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={key: int(value) for key, value in user_input.items()}
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PENDING_TIMEOUT,
                    default=options.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_PENDING_TIMEOUT,
                        max=MAX_PENDING_TIMEOUT,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
