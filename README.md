# UpSnap for Home Assistant

Control the computers you manage with [UpSnap](https://github.com/seriousm4x/UpSnap)
(a self-hosted Wake-on-LAN dashboard) from Home Assistant.

## Features

- Set up from the UI: URL, username and password. Every device the UpSnap user can
  read is added automatically, including devices you add to UpSnap later.
- For each device:
  - **Switch** (`switch.<device>`): turning it on sends Wake-on-LAN, turning it off runs
    the shutdown command configured in UpSnap.
  - **Power state sensor** (`sensor.<device>_power_state`): `on`, `off`, `turning_on`,
    `turning_off` or `restarting`.
  - **Restart button**, and a **Sleep button** for devices with Sleep-on-LAN enabled.
- Tracks actions in progress: after you press the switch, the device shows as
  *turning on* / *turning off* until it really answers (or stops answering) ping,
  or until the timeout passes. Further presses are ignored meanwhile. Actions started
  from the UpSnap web UI are shown too.
- While a computer is shutting down it keeps showing as *on*, so the state never flips
  to *off* too early.
- Only the fields it needs are requested from UpSnap. Wake-on-LAN and Sleep-on-LAN
  passwords and shutdown commands never reach Home Assistant.
- Re-authentication flow if the password changes, and options for the polling interval
  and the action timeout.

## Requirements

- UpSnap 4.x or 5.x (tested with 4.x).
- Home Assistant 2024.12 or newer.

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

1. In UpSnap, create a user for Home Assistant (Users → New user) and give it the
   **Read** and **Power** permissions on the devices it should control. Do not give it
   edit or delete permissions. The UpSnap admin account also works but is not needed.
2. In Home Assistant: **Settings → Devices & services → Add integration → UpSnap**.
3. Enter the UpSnap URL (for example `http://192.168.1.10:8090`), the username and the
   password.

### Options

| Option | Default | Description |
|---|---|---|
| Polling interval | 10 s | How often UpSnap is asked for the device states. |
| Maximum wait for a power action | 180 s | After this time a device that has not reached the requested state stops showing as turning on/off. UpSnap itself gives up after 2 minutes. |

## Entities and attributes

The switch has a `power_state` attribute with the same value as the power state sensor,
so one entity is enough for dashboards and automations.

| UpSnap status | Power state |
|---|---|
| `online` | `on` |
| `offline` | `off` |
| `pending` (action started outside Home Assistant) | `turning_on` or `turning_off`, depending on the previous state |
| any, while an action sent from Home Assistant is running | `turning_on`, `turning_off` or `restarting` |

## Dashboard example

A tile with a toggle:

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

## Notes

- Turning a device off needs a shutdown command configured for it in UpSnap.
- UpSnap marks a device as online/offline by ping. If a device does not answer ping,
  set a custom ping command in UpSnap.
- Do not expose UpSnap to the internet: its shutdown commands run on the UpSnap host.

## License

MIT

The UpSnap logo used as the integration icon belongs to the
[UpSnap](https://github.com/seriousm4x/UpSnap) project (MIT License, © seriousm4x).
This integration is not affiliated with the UpSnap project.
