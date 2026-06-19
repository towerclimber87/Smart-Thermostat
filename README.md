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



## Raspberry Pi wall-panel kiosk

This build includes a Raspberry Pi kiosk setup for the wall-mounted thermostat panel. The Pi runs the local Python server and starts Chromium automatically in full-screen kiosk mode at `http://127.0.0.1:8080`.

Install on the Pi:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/install-pi.sh scripts/kiosk-launch.sh scripts/network_watchdog.py
./scripts/install-pi.sh
sudo reboot
```

Services installed:

- `smart-thermostat-web.service` — local API/static web server
- `smart-thermostat-kiosk.service` — Chromium kiosk launcher
- `smart-thermostat-network-watchdog.service` — Ethernet priority and Wi-Fi reconnect watchdog

Kiosk configuration is stored in `/etc/smart-thermostat/kiosk.env`. More details are in `docs/raspberry-pi-notes.md`.

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


## Native appliance display

Version 10 adds a native Raspberry Pi touchscreen client for the wall thermostat. The web server remains the local API/control engine and Home Assistant proxy, but the wall display is now `smart-thermostat-native.service` instead of the Chromium kiosk. This keeps GitHub Fetch Update, config storage, Home Assistant discovery, GPIO control, and the browser-accessible web UI while removing Chromium from the on-device display path.

Install or repair native appliance mode with:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh
./scripts/install-pi.sh
```

For a quick repair without package install:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh
./scripts/appliance-mode.sh
```

See `docs/native-appliance.md` for details.
