# Smart Thermostat Native

This package removes Chromium from the touchscreen UI and replaces it with a native PyQt5 appliance client. The local Python backend is still used for thermostat safety logic, relay control, Home Assistant proxy calls, config import/export, hardware info, history, reboot, and update handling.

The package intentionally does not include the old `public/` web UI or the old Tkinter native file.

## Included from the current panel

- `data/panel-config.json` copied from the uploaded panel, including the panel name, Home Assistant URL/token, assigned room controls, blinds, lights, audio controls, alarm entity, and thermostat settings.
- `data/thermostat-state.json` copied from the uploaded panel.

Do not share this folder publicly because the config contains a Home Assistant long-lived access token.

## Install on the Pi

From the folder you extracted:

```bash
cd ~/SmartThermostatNative
chmod +x scripts/install-native.sh
sudo ./scripts/install-native.sh
```

Then reboot:

```bash
sudo reboot
```

The installer disables the old Chromium kiosk services if they exist, installs Qt dependencies, enables the local backend on `127.0.0.1:8080`, and starts the full-screen native UI.

## Manual test without installing services

```bash
cd ~/SmartThermostatNative
python3 server.py --host 127.0.0.1 --port 8080
```

In a second terminal/X session:

```bash
cd ~/SmartThermostatNative
python3 native_app/main.py
```

## Services

- `smart-thermostat-backend.service` runs `server.py`.
- `smart-thermostat-native.service` launches the Qt full-screen UI through X.
