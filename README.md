# Smart Thermostat

Version: `12.0-html-display-force-webview-6/19/2026`

This build keeps the local FastAPI/web UI on port `8080`, and makes the wall display show that same HTML/CSS UI through a lightweight native GTK/WebKit host instead of full Chromium kiosk.

## Important wall-display change

The normal display path is now:

```text
smart-thermostat-web.service      # API + HTML UI on port 8080
smart-thermostat-hybrid.service   # native WebKit host showing that HTML UI
```

The old blocky Tk/canvas native screen is no longer the normal native path. For compatibility, `scripts/native-launch.sh` forwards old native launches to the HTML WebKit host.

## Apply and force the HTML display

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh native/*.py
./scripts/force-html-display.sh
```

That script installs the WebKit runtime packages, writes the updated systemd units, disables Chromium kiosk and the old native canvas path, and starts the HTML hybrid display.

## Display controls

```bash
./scripts/display-mode.sh hybrid
./scripts/display-mode.sh html
./scripts/display-mode.sh native   # alias to HTML hybrid in this build
./scripts/display-mode.sh canvas   # emergency old canvas fallback only
./scripts/display-mode.sh kiosk    # full Chromium fallback only
./scripts/display-mode.sh status
```

## If the screen still looks blocky

Run:

```bash
cd ~/Smart-Thermostat-Development
./scripts/force-html-display.sh
sudo reboot
```

Then check:

```bash
journalctl -u smart-thermostat-hybrid.service -n 120 --no-pager
```

## 12.1 hard switch to HTML wall display

If the wall screen still shows the old blocky Tk/canvas display, the installed `smart-thermostat-native.service` is still launching `native/thermostat_native.py`.

Run:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh native/*.py
./scripts/hard-switch-html-display.sh
sudo reboot
```

After the fix, this command should show `native/html_panel.py`, not `native/thermostat_native.py`:

```bash
ps -ef | grep -E 'thermostat_native.py|html_panel.py|hybrid-xinit|xinit' | grep -v grep
```
