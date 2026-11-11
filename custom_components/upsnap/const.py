"""Constants for the UpSnap integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "upsnap"

CONF_PENDING_TIMEOUT: Final = "pending_timeout"

# Polling is not configurable (Home Assistant guidelines): UpSnap pings the devices
# every few seconds and one request returns all of them, so 10 s is cheap.
SCAN_INTERVAL: Final = 10  # seconds
# Poll faster while a power action is in progress, to show the result sooner.
FAST_SCAN_INTERVAL: Final = 3  # seconds

# How long to keep waiting for a device after UpSnap has finished an action, for
# example a computer still booting after UpSnap gave up waiting for it.
DEFAULT_PENDING_TIMEOUT: Final = 180  # seconds
MIN_PENDING_TIMEOUT: Final = 30
MAX_PENDING_TIMEOUT: Final = 900
# Safety limit in case UpSnap never finishes. UpSnap gives up on each step after
# 2 minutes, and a restart is a shutdown, a 15 s pause and a wake.
MIN_HARD_TIMEOUT: Final = 600  # seconds
UPSNAP_MAX_ACTION_TIME: Final = 300  # seconds

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
