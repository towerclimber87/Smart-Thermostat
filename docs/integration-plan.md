# Smart Thermostat Integration Plan

This project starts as a local UI. The next phases should add control layers without making the UI itself responsible for critical hardware decisions.

## Recommended layers

```text
public UI
  ↓ local HTTP/WebSocket/Unix socket
control service
  ↓ GPIO / I2C / serial / Home Assistant API
hardware and integrations
```

## Why not put everything in browser JavaScript?

The browser should not be the thermostat brain. If the UI crashes or reloads, relay logic should continue.

Recommended later services:

- `thermostatd`
  - Reads I2C temperature/humidity sensors.
  - Stores mode, target temperature, away state, fan state.
  - Applies hysteresis and compressor short-cycle protection.
  - Controls GPIO outputs through proper relay/interface hardware.
  - Exposes local status and command API to the UI.

- `ha-sync`
  - Mirrors current state to Home Assistant.
  - Accepts HA setpoint changes when network is available.
  - Does not block local control if Home Assistant or Wi-Fi is unavailable.

- `audio-bridge`
  - Eventually maps the audio screen to Sonos/Home Assistant/media APIs.

- `blind-bridge`
  - Eventually maps room/blind commands to Home Assistant cover entities or a direct local blind controller.

## Initial placeholder API shape

Future UI calls can be normalized around this style:

```http
GET /api/status
POST /api/thermostat/setpoint
POST /api/thermostat/mode
POST /api/thermostat/away
POST /api/audio/volume
POST /api/audio/transport
POST /api/blinds/room
POST /api/blinds/blind
```

Example thermostat response:

```json
{
  "current_temp": 72.1,
  "target_temp": 70,
  "mode": "cool",
  "fan": "auto",
  "away": false,
  "runtime_state": "idle",
  "humidity": 45
}
```

## Local-first rule

The production rule should be:

```text
A thermostat setpoint changed on the wall panel must apply locally even when Wi-Fi and Home Assistant are down.
```

That means GPIO and sensor logic should be local to the Raspberry Pi or delegated to a directly wired controller.
