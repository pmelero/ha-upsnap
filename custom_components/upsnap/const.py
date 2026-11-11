"""Constants for the UpSnap integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "upsnap"

CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_PENDING_TIMEOUT: Final = "pending_timeout"

DEFAULT_SCAN_INTERVAL: Final = 10  # seconds
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 300

# UpSnap itself gives up after 2 minutes; leave some margin for slow boots.
DEFAULT_PENDING_TIMEOUT: Final = 180  # seconds
MIN_PENDING_TIMEOUT: Final = 30
MAX_PENDING_TIMEOUT: Final = 900

# Values of the "status" field of an UpSnap device.
STATUS_ONLINE: Final = "online"
STATUS_OFFLINE: Final = "offline"
STATUS_PENDING: Final = "pending"

# Power actions exposed by UpSnap at /api/upsnap/<action>/<device id>.
ACTION_WAKE: Final = "wake"
ACTION_SHUTDOWN: Final = "shutdown"
ACTION_REBOOT: Final = "reboot"
ACTION_SLEEP: Final = "sleep"
ACTIONS: Final = (ACTION_WAKE, ACTION_SHUTDOWN, ACTION_REBOOT, ACTION_SLEEP)

# Power state reported by the integration (sensor options).
POWER_ON: Final = "on"
POWER_OFF: Final = "off"
POWER_TURNING_ON: Final = "turning_on"
POWER_TURNING_OFF: Final = "turning_off"
POWER_RESTARTING: Final = "restarting"
POWER_STATES: Final = [
    POWER_ON,
    POWER_OFF,
    POWER_TURNING_ON,
    POWER_TURNING_OFF,
    POWER_RESTARTING,
]
