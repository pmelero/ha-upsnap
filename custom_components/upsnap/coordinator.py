"""Data update coordinator for UpSnap."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    UpSnapAuthError,
    UpSnapClient,
    UpSnapDevice,
    UpSnapError,
    UpSnapNotFoundError,
    UpSnapPermissionError,
)
from .const import (
    ACTION_REBOOT,
    ACTION_SHUTDOWN,
    ACTION_WAKE,
    CONF_PENDING_TIMEOUT,
    CONF_SCAN_INTERVAL,
    DEFAULT_PENDING_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    POWER_OFF,
    POWER_ON,
    POWER_RESTARTING,
    POWER_TURNING_OFF,
    POWER_TURNING_ON,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    STATUS_PENDING,
)

_LOGGER = logging.getLogger(__name__)

type UpSnapConfigEntry = ConfigEntry[UpSnapCoordinator]


@dataclass(slots=True)
class PendingAction:
    """A power action sent from Home Assistant that has not finished yet."""

    action: str
    target_on: bool
    issued_at: float
    deadline: float


class UpSnapCoordinator(DataUpdateCoordinator[dict[str, UpSnapDevice]]):
    """Poll UpSnap and track power actions in progress."""

    config_entry: UpSnapConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: UpSnapConfigEntry, client: UpSnapClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=int(
                    entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                )
            ),
        )
        self.client = client
        self._pending_timeout = int(
            entry.options.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT)
        )
        self._pending: dict[str, PendingAction] = {}
        self._real_on: dict[str, bool] = {}
        self._power_state: dict[str, str] = {}

    def is_on(self, device_id: str) -> bool | None:
        """Return the switch state, showing the requested state while pending."""
        if device_id not in self._real_on:
            return None
        pending = self._pending.get(device_id)
        if pending is not None and pending.action != ACTION_REBOOT:
            return pending.target_on
        return self._real_on[device_id]

    def power_state(self, device_id: str) -> str | None:
        """Return on, off, turning_on, turning_off or restarting."""
        return self._power_state.get(device_id)

    async def _async_update_data(self) -> dict[str, UpSnapDevice]:
        started = time.monotonic()
        try:
            devices = await self.client.get_devices()
        except UpSnapAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except UpSnapError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self._compute_states(devices, started)
        return devices

    def _compute_states(
        self, devices: dict[str, UpSnapDevice], poll_started: float
    ) -> None:
        """Derive the power state of every device.

        ``poll_started`` is when the data was requested: a pending action is only
        considered finished by data fetched after the action was sent.
        """
        now = time.monotonic()
        for device_id in list(self._real_on):
            if device_id not in devices:
                self._real_on.pop(device_id, None)
                self._power_state.pop(device_id, None)
                self._pending.pop(device_id, None)

        for device_id, device in devices.items():
            if device.status == STATUS_ONLINE:
                real_on = True
            elif device.status == STATUS_OFFLINE:
                real_on = False
            else:
                # UpSnap is busy with this device: keep the last known state, so a
                # computer that is shutting down still shows as on.
                real_on = self._real_on.get(device_id, False)
            self._real_on[device_id] = real_on

            pending = self._pending.get(device_id)
            if pending is not None:
                if (
                    poll_started > pending.issued_at
                    and device.status != STATUS_PENDING
                    and real_on == pending.target_on
                ):
                    self._pending.pop(device_id)
                    pending = None
                elif now > pending.deadline:
                    _LOGGER.warning(
                        "%s did not turn %s within %s seconds after the %s request",
                        device.name,
                        "on" if pending.target_on else "off",
                        self._pending_timeout,
                        pending.action,
                    )
                    self._pending.pop(device_id)
                    pending = None

            if pending is not None:
                if pending.action == ACTION_REBOOT:
                    state = POWER_RESTARTING
                else:
                    state = POWER_TURNING_ON if pending.target_on else POWER_TURNING_OFF
            elif device.status == STATUS_PENDING:
                # An action started from the UpSnap web UI or one of its cron jobs.
                state = POWER_TURNING_OFF if real_on else POWER_TURNING_ON
            else:
                state = POWER_ON if real_on else POWER_OFF
            self._power_state[device_id] = state

    async def async_power(self, device_id: str, action: str) -> None:
        """Send a power action and track it until the device reaches the new state."""
        device = (self.data or {}).get(device_id)
        name = device.name if device else device_id

        if device_id in self._pending:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="action_in_progress",
                translation_placeholders={"device": name},
            )
        real_on = self._real_on.get(device_id)
        busy = device is not None and device.status == STATUS_PENDING
        if not busy and (
            (action == ACTION_WAKE and real_on)
            or (action == ACTION_SHUTDOWN and real_on is False)
        ):
            return  # Already in the requested state.

        now = time.monotonic()
        self._pending[device_id] = PendingAction(
            action=action,
            target_on=action in (ACTION_WAKE, ACTION_REBOOT),
            issued_at=now,
            deadline=now + self._pending_timeout,
        )
        self._refresh_listeners()

        try:
            await self.client.power(device_id, action)
        except UpSnapError as err:
            self._pending.pop(device_id, None)
            self._refresh_listeners()
            if isinstance(err, UpSnapAuthError):
                self.config_entry.async_start_reauth(self.hass)
                key = "invalid_auth"
            elif isinstance(err, UpSnapPermissionError):
                key = "no_power_permission"
            elif isinstance(err, UpSnapNotFoundError):
                key = "device_not_found"
            else:
                key = "action_failed"
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=key,
                translation_placeholders={"device": name, "error": str(err)},
            ) from err

        await self.async_request_refresh()

    def _refresh_listeners(self) -> None:
        """Recompute states from the last data and notify entities right away."""
        if self.data is not None:
            # -inf: data from before the action can never finish it.
            self._compute_states(self.data, float("-inf"))
        self.async_update_listeners()
