# Native Thermostat Appliance Mode

This build adds a native Raspberry Pi touchscreen client so the wall controller no longer needs Chromium for the on-device display.

## Services

- `smart-thermostat-web.service` remains the local API, Home Assistant proxy, config store, relay control loop, HA discovery endpoint, and update endpoint.
- `smart-thermostat-native.service` owns the DSI display on tty7 and launches `native/thermostat_native.py` through Xorg/Openbox.
- `smart-thermostat-kiosk.service` remains included as a fallback, but appliance mode disables it.

## Display calibration

The native launcher applies the same working Waveshare/Goodix values found during field testing:

```bash
xrandr --fb 1280x800 --output DSI-1 --mode 800x1280 --rotate left
xinput set-prop <Goodix pointer id> "Coordinate Transformation Matrix" -1 0 1 0 -1 1 0 0 1
```

These are configured in `/etc/smart-thermostat/native.env`:

```bash
SMART_NATIVE_ROTATION=left
SMART_NATIVE_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
```

## Updating

The Fetch Update button in the native info panel calls the existing local endpoint:

```text
POST /api/system/fetch-update
```

The server fetches GitHub `Development`, resets to the new code, restores local settings files, and restarts:

```text
smart-thermostat-web.service
smart-thermostat-native.service
```

Run `scripts/install-pi.sh` once after installing this version so the systemd service and sudoers restart permissions are installed.

## Manual install/update command

```bash
cd ~/Smart-Thermostat-Development
git fetch origin Development
git reset --hard origin/Development
chmod +x scripts/*.sh
./scripts/install-pi.sh
```

## Manual appliance-mode repair command

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh
./scripts/appliance-mode.sh
```
