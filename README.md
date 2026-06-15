# IHA

A Raspberry Pi wall-panel interface starter project for the IHA thermostat, Sonos-style audio controller, and room/blind controls.

This first version is intentionally frontend-only. The buttons, sliders, page swipes, and placeholder states work locally so the interface can be tested before wiring GPIO, I2C sensors, relays, Home Assistant, or Sonos integrations.

## Current screens

- **Thermostat**
  - Cool / Heat mode only
  - No auto heat/cool mode
  - Away mode with safety setpoints
  - Interactive setpoint buttons
  - Placeholder humidity, runtime, and current temperature

- **Audio**
  - Sonos placeholder page
  - Play/pause, next, previous
  - Volume, gain, bass, and treble sliders
  - Now-playing placeholder card

- **Blinds**
  - Living Room: 4 blinds
  - Kitchen: 3 blinds
  - Room-level open/close/stop
  - Independent blind sliders and actions

## Navigation

- Tap the left/right side of the screen to change pages.
- Use the bottom nav buttons.
- Swipe right/left on a touchscreen.
- On desktop, use left/right arrow keys.
- Use +/- keys to change the thermostat setpoint.

## Run locally

From the project root:

```bash
python3 server.py --host 0.0.0.0 --port 8080
```

Then open:

```text
http://localhost:8080
```

On another computer on the same network, use the Pi/computer IP address:

```text
http://<device-ip>:8080
```


## Home Assistant auto-discovery

This build includes a no-MQTT Home Assistant path. The Raspberry Pi server exposes a local thermostat API and advertises `_iha-thermostat._tcp.local.` over mDNS/Zeroconf. The included custom integration in `custom_components/iha` turns the panel into a Home Assistant `climate` entity after the discovered device is accepted.

See `docs/home-assistant.md` for install steps and the manual-add fallback.

## Project structure

```text
Smart-Thermostat/
├── public/
│   ├── index.html
│   ├── css/styles.css
│   └── js/app.js
├── docs/
│   ├── integration-plan.md
│   └── raspberry-pi-notes.md
├── scripts/
│   └── install-pi.sh
├── systemd/
│   └── smart-thermostat-web.service
├── .gitignore
├── LICENSE
├── package.json
└── README.md
```

## Future direction

The intended production architecture is:

```text
Touch UI
  ↓
Local control service
  ↓
GPIO/I2C/relay interface hardware
  ↓
HVAC / sensors / blinds / audio bridge
```

Home Assistant should eventually be an integration layer, not the required path for local thermostat changes.
