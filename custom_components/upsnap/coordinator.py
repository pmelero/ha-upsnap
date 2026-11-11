"""Data update coordinator for UpSnap."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr, issue_registry as ir
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
    DEFAULT_PENDING_TIMEOUT,
    DOMAIN,
    FAST_SCAN_INTERVAL,
    POWER_OFF,
    POWER_ON,
    POWER_RESTARTING,
    POWER_TURNING_OFF,
    POWER_TURNING_ON,
    SCAN_INTERVAL,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    STATUS_PENDING,
)

_LOGGER = logging.getLogger(__name__)

type UpSnapConfigEntry = ConfigEntry[UpSnapCoordinator]


def no_devices_issue_id(entry: ConfigEntry) -> str:
    """Return the id of the repair issue shown when no device is visible."""
    return f"no_devices_{entry.entry_id}"


@dataclass(slots=True)
class PendingAction:
    """A power action sent from Home Assistant that has not finished yet."""

    action: str
    target_on: bool
    deadline: float
    # Number of the last poll started before UpSnap accepted the action. None while
    # the request is still in flight: no data can finish the action until then.
    accepted_after_poll: int | None = None


class UpSnapCoordinator(DataUpdateCoordinator[dict[str, UpSnapDevice]]):
    """Poll UpSnap and track power actions in progress."""

    config_entry: UpSnapConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: UpSnapConfigEntry, client: UpSnapClient
    ) -> None:
        """Initialize the coordinator."""
        self._scan_interval = timedelta(seconds=SCAN_INTERVAL)
        self._fast_scan_interval = timedelta(seconds=FAST_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self._scan_interval,
        )
        self.client = client
        self._pending_timeout = int(
            entry.options.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT)
        )
        self._pending: dict[str, PendingAction] = {}
        self._real_on: dict[str, bool] = {}
        self._power_state: dict[str, str] = {}
        self._polls_started = 0
        self._warned_no_devices = False

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

    def pending_actions(self) -> dict[str, dict[str, Any]]:
        """Describe the actions in progress (for diagnostics)."""
        now = time.monotonic()
        return {
            device_id: {
                "action": pending.action,
                "target_on": pending.target_on,
                "accepted": pending.accepted_after_poll is not None,
                "seconds_left": round(max(pending.deadline - now, 0)),
            }
            for device_id, pending in self._pending.items()
        }

    async def _async_update_data(self) -> dict[str, UpSnapDevice]:
        self._polls_started += 1
        poll = self._polls_started
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
        self._remove_stale_devices(devices)
        self._compute_states(devices, poll)
        return devices

    def _remove_stale_devices(self, devices: dict[str, UpSnapDevice]) -> None:
        """Remove devices that no longer exist in UpSnap (or the user cannot read)."""
        if not devices:
            # More likely a permissions problem than every device being deleted:
            # keep the devices (and their entity settings) until it is sorted out.
            if not self._warned_no_devices:
                _LOGGER.warning(
                    "UpSnap returned no devices: check that the user still has the "
                    "Read permission on them"
                )
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    no_devices_issue_id(self.config_entry),
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="no_devices",
                    translation_placeholders={"title": self.config_entry.title},
                )
                self._warned_no_devices = True
            return
        if self._warned_no_devices:
            ir.async_delete_issue(
                self.hass, DOMAIN, no_devices_issue_id(self.config_entry)
            )
            self._warned_no_devices = False
        registry = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        for device_entry in dr.async_entries_for_config_entry(registry, entry_id):
            upsnap_ids = {
                identifier
                for domain, identifier in device_entry.identifiers
                if domain == DOMAIN
            }
            if upsnap_ids and not upsnap_ids & devices.keys():
                _LOGGER.info(
                    "Removing %s: it is no longer in UpSnap", device_entry.name
                )
                registry.async_update_device(
                    device_entry.id, remove_config_entry_id=entry_id
                )

    def _compute_states(self, devices: dict[str, UpSnapDevice], poll: int) -> None:
        """Derive the power state of every device from the data of poll number ``poll``.

        A pending action is only considered finished by data from a poll started
        after UpSnap accepted the action.
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
                accepted = pending.accepted_after_poll
                if (
                    accepted is not None
                    and poll > accepted
                    and device.status != STATUS_PENDING
                    and real_on == pending.target_on
                ):
                    _LOGGER.debug(
                        "%s finished the %s action", device.name, pending.action
                    )
                    del self._pending[device_id]
                    pending = None
                elif now > pending.deadline:
                    _LOGGER.warning(
                        "%s did not turn %s within %s seconds after the %s request",
                        device.name,
                        "on" if pending.target_on else "off",
                        self._pending_timeout,
                        pending.action,
                    )
                    del self._pending[device_id]
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

        busy = bool(self._pending) or any(
            device.status == STATUS_PENDING for device in devices.values()
        )
        self.update_interval = self._fast_scan_interval if busy else self._scan_interval

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

        pending = PendingAction(
            action=action,
            target_on=action in (ACTION_WAKE, ACTION_REBOOT),
            deadline=time.monotonic() + self._pending_timeout,
        )
        self._pending[device_id] = pending
        self._refresh_listeners()

        try:
            await self.client.power(device_id, action)
        except UpSnapError as err:
            if self._pending.get(device_id) is pending:
                del self._pending[device_id]
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

        # UpSnap marks the device as pending before it answers, so from now on
        # any new poll reflects the action.
        pending.accepted_after_poll = self._polls_started
        _LOGGER.debug("UpSnap accepted %s for %s", action, name)
        await self.async_request_refresh()

    def _refresh_listeners(self) -> None:
        """Recompute states from the last data and notify entities right away."""
        if self.data is not None:
            # Poll number -1: data from before the action can never finish it.
            self._compute_states(self.data, -1)
        self.async_update_listeners()
