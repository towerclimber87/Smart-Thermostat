# Hybrid HTML WebView Display

This mode shows the same HTML/CSS/JavaScript screen that you see in a browser, but it does **not** launch the full Chromium kiosk stack.  The Pi runs the normal local web/API service, then a small GTK/WebKit native host opens `http://127.0.0.1:8080` full-screen on the DSI panel.

## What runs

```text
smart-thermostat-web.service       local FastAPI server, Home Assistant bridge, API, static HTML UI
smart-thermostat-hybrid.service    native GTK/WebKit full-screen host for the HTML UI
```

The older services remain available as fallbacks:

```text
smart-thermostat-native.service    Tk/canvas fallback display
smart-thermostat-kiosk.service     Chromium kiosk fallback display
```

## Enable the HTML hybrid display

Run this after updating the files:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh native/html_panel.py
./scripts/install-pi.sh
./scripts/display-mode.sh hybrid
sudo reboot
```

For a quick switch without reinstalling packages:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh native/html_panel.py
./scripts/appliance-mode.sh
```

## Manual service commands

```bash
sudo systemctl restart smart-thermostat-web.service smart-thermostat-hybrid.service
sudo systemctl status smart-thermostat-web.service smart-thermostat-hybrid.service --no-pager
journalctl -u smart-thermostat-hybrid.service -f
```

## Display-mode helper

```bash
./scripts/display-mode.sh hybrid   # HTML UI in native WebKit host, recommended
./scripts/display-mode.sh native   # old Tk/canvas native fallback
./scripts/display-mode.sh kiosk    # Chromium fallback
./scripts/display-mode.sh status   # service status
```

## Runtime environment

The installer writes `/etc/smart-thermostat/hybrid.env`:

```bash
SMART_THERMOSTAT_URL=http://127.0.0.1:8080
SMART_THERMOSTAT_API=http://127.0.0.1:8080
SMART_HYBRID_ROTATION=left
SMART_HYBRID_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
SMART_HYBRID_FULLSCREEN=1
SMART_HYBRID_CACHE_DIR=/tmp/smart-thermostat-webkit-cache
SMART_HYBRID_DATA_DIR=/tmp/smart-thermostat-webkit-data
```

The WebKit host uses an ephemeral or `/tmp` profile/cache path where supported, so it avoids a persistent browser profile on the SD card.  It still renders the real web UI, so the screen will look like the browser version rather than the simplified Tk/canvas display.

## Required packages

`install-pi.sh` installs the needed GTK/WebKit packages when available:

```text
python3-gi
gir1.2-gtk-3.0
gir1.2-webkit2-4.1 or gir1.2-webkit2-4.0
xinit
openbox
unclutter
```

If the service fails with a WebKit import error, install the WebKit GIR package manually for your Raspberry Pi OS release:

```bash
sudo apt-get update
sudo apt-get install -y python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1 || \
sudo apt-get install -y python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.0
```
