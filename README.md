# UpSnap for Home Assistant

[![Validate](https://github.com/pmelero/ha-upsnap/actions/workflows/validate.yml/badge.svg)](https://github.com/pmelero/ha-upsnap/actions/workflows/validate.yml)
[![Tests](https://github.com/pmelero/ha-upsnap/actions/workflows/tests.yml/badge.svg)](https://github.com/pmelero/ha-upsnap/actions/workflows/tests.yml)
[![GitHub release](https://img.shields.io/github/v/release/pmelero/ha-upsnap)](https://github.com/pmelero/ha-upsnap/releases)
[![License: MIT](https://img.shields.io/github/license/pmelero/ha-upsnap)](LICENSE)

Control the computers you manage with [UpSnap](https://github.com/seriousm4x/UpSnap)
(a self-hosted Wake-on-LAN dashboard) from Home Assistant: turn them on and off, restart
them and see whether they are on, off, or still booting or shutting down.

## Features

- Set up from the UI: URL, username and password. Every device the UpSnap user can
  read is added automatically, including devices you add to UpSnap later. Devices
  removed from UpSnap are removed from Home Assistant.
- For each device:
  - **Switch**: on sends Wake-on-LAN, off runs the shutdown command configured in
    UpSnap.
  - **Power state** sensor: `on`, `off`, `turning_on`, `turning_off` or `restarting`.
  - **Restart** button, and a **Sleep** button for devices with Sleep-on-LAN enabled.
- Tracks every action until it is really done: after you turn a computer on, it shows
  as *turning on* until it answers ping, not just until the magic packet is sent.
  Further presses are ignored meanwhile. Actions started from the UpSnap web UI are
  shown too.
- A computer that is shutting down keeps showing as *on* until it is really off.
- Only the device fields the integration needs are requested from UpSnap:
  Wake-on-LAN and Sleep-on-LAN passwords and shutdown commands never reach Home
  Assistant.
- Re-authentication, reconfiguration (change URL or credentials without removing the
  integration) and diagnostics with credentials and addresses removed.
- English and Spanish translations.

## Supported devices

Any computer or device UpSnap can wake (Wake-on-LAN) and check (ping or a custom ping
command). Turning off needs a shutdown command in UpSnap; sleeping needs
[Sleep-on-LAN](https://github.com/SR-G/sleep-on-lan) on the device.

## Requirements

- UpSnap 4.x or 5.x (tested with 4.x).
- Home Assistant 2024.12 or newer (the integration icon needs 2026.3 or newer).

## Installation

### Home Assistant 2026.11 or newer (Marketplace)

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=pmelero&repository=ha-upsnap&category=integration)

Or manually:

1. **Settings → Marketplace**, then **Add from link** in the upper-right corner.
2. Enter `https://github.com/pmelero/ha-upsnap` and select **Add**.
3. Install **UpSnap** and restart Home Assistant.

### Home Assistant 2026.10 or older (HACS)

1. HACS → ⋮ → **Custom repositories**.
2. Add `https://github.com/pmelero/ha-upsnap` with type **Integration**.
3. Install **UpSnap** and restart Home Assistant.

### Manual

Copy `custom_components/upsnap` into the `custom_components` folder of your Home
Assistant configuration and restart.

## Setup

1. In UpSnap, create a user for Home Assistant (**Users → New user**) and give it the
   **Read** and **Power** permissions on the devices it should control. Do not give it
   edit or delete permissions. The UpSnap admin account also works but is not needed.
2. In Home Assistant: **Settings → Devices & services → Add integration → UpSnap**.

| Parameter | Description |
|---|---|
| URL | Address of the UpSnap web interface, for example `http://192.168.1.10:8090`. `http://` is added if you leave it out. |
| Username | The UpSnap user (username or email), or the UpSnap admin email. |
| Password | Its password. |
| Verify SSL certificate | Turn off only if UpSnap uses HTTPS with a self-signed certificate. |

To change any of them later: **Settings → Devices & services → UpSnap → ⋮ →
Reconfigure**.

### Options

**Settings → Devices & services → UpSnap → Configure**:

| Option | Default | Description |
|---|---|---|
| Maximum wait for a power action | 180 s | After this time a device that has not reached the requested state stops showing as turning on/off. UpSnap itself gives up after 2 minutes. |

## Entities

| Entity | Example | Description |
|---|---|---|
| Switch | `switch.desktop` | Turn the device on or off. While an action is in progress it shows the requested state. Attribute `power_state` has the detailed state. |
| Power state | `sensor.desktop_power_state` | `on`, `off`, `turning_on`, `turning_off` or `restarting`. |
| Restart | `button.desktop_restart` | Shut down, wait 15 s and wake again (done by UpSnap). |
| Sleep | `button.desktop_sleep` | Only for devices with Sleep-on-LAN enabled in UpSnap. |

How the UpSnap status becomes the power state:

| UpSnap status | Power state |
|---|---|
| `online` | `on` |
| `offline` | `off` |
| `pending` (action started outside Home Assistant) | `turning_on` or `turning_off`, depending on the previous state |
| any, while an action sent from Home Assistant is running | `turning_on`, `turning_off` or `restarting` |

## How data is updated

The integration polls UpSnap's REST API every 10 seconds, and every 3 seconds while an
action is in progress. One request returns all the devices. UpSnap checks the devices
itself by ping (every 3 s by default), so Home Assistant is at most 10 seconds behind
UpSnap.

As Home Assistant recommends, the interval is not an option. To poll at another rate,
turn off **Enable polling for changes** in the integration's system options and call
`homeassistant.update_entity` on one of its entities from an automation.

## Examples

Turn the desktop on when you arrive home:

```yaml
automation:
  - alias: Wake the desktop when I get home
    triggers:
      - trigger: state
        entity_id: person.me
        to: home
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.desktop
```

Get a notification when a restart is complete:

```yaml
automation:
  - alias: Desktop restarted
    triggers:
      - trigger: state
        entity_id: sensor.desktop_power_state
        from: restarting
        to: "on"
    actions:
      - action: notify.notify
        data:
          message: The desktop is back on.
```

A dashboard tile with a toggle:

```yaml
type: tile
entity: switch.desktop
features:
  - type: toggle
```

Green when on, blinking orange while changing state (needs
[button-card](https://github.com/custom-cards/button-card)):

```yaml
type: custom:button-card
entity: switch.desktop
show_state: true
tap_action:
  action: toggle
state_display: |
  [[[ return {on: 'On', off: 'Off', turning_on: 'Turning on…', turning_off: 'Turning off…',
      restarting: 'Restarting…'}[entity.attributes.power_state] ?? entity.state; ]]]
styles:
  icon:
    - color: |
        [[[ const s = entity.attributes.power_state;
            return s === 'on' ? 'var(--green-color)'
              : s === 'off' ? 'var(--disabled-color)' : 'var(--orange-color)'; ]]]
    - animation: |
        [[[ return ['turning_on', 'turning_off', 'restarting']
              .includes(entity.attributes.power_state) ? 'blink 1.2s ease infinite' : 'none'; ]]]
```

## Known limitations

- Whether a device is on is decided by UpSnap's ping. A device that does not answer
  ping shows as off; set a custom ping command for it in UpSnap.
- Turning a device off needs a shutdown command configured for it in UpSnap.
- Updates come by polling, not push: changes appear after up to one polling interval.
- If Home Assistant restarts while UpSnap is busy with a device, the direction
  (turning on or off) is not known until the device settles.
- UpSnap 4.x logs `no such process` after many successful wake/shutdown commands. It
  is harmless and the integration does not depend on it.

## Troubleshooting

- **"This user cannot see any device"** (while setting up) or a **repair issue
  "UpSnap user cannot see any device"** (later, in Settings → Repairs): give the
  UpSnap user the Read permission on the devices. The devices are kept meanwhile.
- **"Not allowed to power" errors**: give it the Power permission.
- **A device stays *turning on***: check that Wake-on-LAN works from the UpSnap web UI
  and that the device answers ping.
- **A device does not turn off**: check its shutdown command in UpSnap; UpSnap runs it
  on the UpSnap host.
- **Debug logging**:

  ```yaml
  logger:
    logs:
      custom_components.upsnap: debug
  ```

- **Diagnostics**: Settings → Devices & services → UpSnap → ⋮ → Download diagnostics.
  Credentials, URL, IP and MAC addresses are removed; attach the file to bug reports.

## Removal

1. **Settings → Devices & services → UpSnap → ⋮ → Delete**.
2. Uninstall it from the Marketplace/HACS (or delete `custom_components/upsnap`) and
   restart Home Assistant.
3. Optionally delete the Home Assistant user in UpSnap.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt -r requirements_lint.txt pre-commit
pre-commit install
pytest --cov
```

The CI runs the tests against the latest Home Assistant and against the oldest
supported version (`.github/requirements-oldest-ha.txt`, Python 3.13), plus Ruff,
hassfest and the HACS validation.

## License

MIT

The UpSnap logo used as the integration icon belongs to the
[UpSnap](https://github.com/seriousm4x/UpSnap) project (MIT License, © seriousm4x).
This integration is not affiliated with the UpSnap project.
