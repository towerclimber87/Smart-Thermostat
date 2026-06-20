# Native / Hybrid Thermostat Appliance Mode

## Current recommended mode: Hybrid HTML WebView

The wall display now defaults to the hybrid HTML runtime.  That means the screen shows the same polished HTML/CSS interface you see from a browser, but it is hosted by `native/html_panel.py` in a small GTK/WebKit full-screen shell instead of the full Chromium kiosk stack.

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh native/html_panel.py
./scripts/install-pi.sh
./scripts/display-mode.sh hybrid
```

Use the older Tk/canvas native UI only as a fallback:

```bash
./scripts/display-mode.sh native
```

Use Chromium only as a troubleshooting fallback:

```bash
./scripts/display-mode.sh kiosk
```

More detail is in `docs/html-hybrid-webview.md`.

---

# Native Thermostat Appliance Mode

This build adds a native Raspberry Pi touchscreen client so the wall controller no longer needs Chromium for the on-device display.

## Hybrid native/web runtime

The wall screen now uses a hybrid runtime:

- `smart-thermostat-web.service` keeps running as the local FastAPI control engine, Home Assistant bridge, settings/config store, update endpoint, and optional browser/admin UI.
- `smart-thermostat-native.service` is the on-device touchscreen display. It talks to the local API over `127.0.0.1` and draws the web-inspired UI directly with Tk/canvas.
- `smart-thermostat-kiosk.service` is only a fallback. It is disabled in normal appliance mode so Chromium does not sit on the Pi consuming CPU, memory, browser cache, and compositor resources.

Use the native display mode command after updating:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh
./scripts/display-mode.sh native
```

To temporarily go back to the Chromium fallback for troubleshooting:

```bash
./scripts/display-mode.sh kiosk
```

To check what is running:

```bash
./scripts/display-mode.sh status
```

Chromium is not installed by default anymore. If you specifically want the fallback browser installed on the Pi, run:

```bash
SMART_INSTALL_CHROMIUM=1 ./scripts/install-pi.sh
```

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


## 10.1 Visual-Parity Native UI

Version 10.1 keeps the native no-Chromium appliance path, but redraws the thermostat screen to match the cleaner web Climate Control layout much more closely: large title, top status chips, center dial, mode/fan segmented controls, virtual outputs, Alarmo, Inside Doors, info/update, settings, and hardware modal access. The native app remains a lightweight Tk/canvas client talking to the existing local API so GitHub Fetch Update, Home Assistant proxying, state/config storage, GPIO/control loop, and remote updater behavior stay intact.

The display service still applies the known Waveshare/Goodix setup before launching the app:

```text
SMART_NATIVE_ROTATION=left
SMART_NATIVE_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
SMART_NATIVE_THERMOSTAT_ONLY=1
SMART_NATIVE_VISUAL_MODE=web_parity
```
