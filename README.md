# Smart Thermostat Native

This package removes Chromium from the touchscreen UI and replaces it with a native PyQt5 appliance client. The local Python backend is still used for thermostat safety logic, relay control, Home Assistant proxy calls, config import/export, hardware info, history, reboot, and update handling.

The package intentionally does not include the old `public/` web UI or the old Tkinter native file.

## Important security note

`data/panel-config.json` may contain your Home Assistant URL and long-lived access token. Do not push this repository to a public GitHub repo with real panel data inside `data/`.

## Install or update on the Pi

From the repo folder:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh
sudo ./scripts/install-native.sh
```

The installer:

- installs the native Qt/X dependencies,
- writes `/etc/X11/Xwrapper.config`,
- disables old Chromium/web kiosk services if present,
- disables `getty@tty1.service` so the native UI owns the touchscreen terminal,
- installs `smart-thermostat-backend.service`,
- installs `smart-thermostat-native.service`,
- starts the backend and native UI.

The native UI service launches X directly with `xinit` on `tty1`. This avoids the earlier issue where `startx` stayed running but the screen remained on the terminal.

## Services

- `smart-thermostat-backend.service` runs `server.py` on `127.0.0.1:8080`.
- `smart-thermostat-native.service` launches the Qt full-screen UI through X on `tty1`.

## Useful checks

```bash
systemctl status smart-thermostat-backend.service smart-thermostat-native.service --no-pager -l
pgrep -a -f 'Xorg|xinit|native_app/main.py'
tail -120 ~/Smart-Thermostat-Development/data/logs/native-ui.log
journalctl -u smart-thermostat-native.service -n 120 --no-pager
```

## Manual test without installing services

Backend:

```bash
cd ~/Smart-Thermostat-Development
python3 server.py --host 127.0.0.1 --port 8080
```

Native UI from an existing X session:

```bash
cd ~/Smart-Thermostat-Development
python3 native_app/main.py
```
