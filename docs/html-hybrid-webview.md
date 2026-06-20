# HTML Hybrid WebView wall display

The wall display should show the same HTML/CSS interface that is available at `http://<panel-ip>:8080`.

This build does not use full Chromium kiosk for the normal wall display. It uses a lightweight native GTK/WebKit host:

- `native/html_panel.py`
- `scripts/hybrid-launch.sh`
- `scripts/hybrid-xinit.sh`
- `systemd/smart-thermostat-hybrid.service`

The old Tk/canvas native display is no longer the normal `native` display path. For compatibility, `scripts/native-launch.sh` now forwards old native launches to the HTML WebKit host so an older enabled `smart-thermostat-native.service` will not show the blocky canvas screen.

## Force the HTML display

After applying the update, run:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh native/*.py
./scripts/force-html-display.sh
```

This does the following:

1. Installs the WebKitGTK runtime packages.
2. Installs the updated systemd units.
3. Stops and disables Chromium kiosk.
4. Stops and disables the old native canvas service.
5. Enables and restarts `smart-thermostat-web.service` and `smart-thermostat-hybrid.service`.

## Display modes

```bash
./scripts/display-mode.sh hybrid   # same as html/native in this build
./scripts/display-mode.sh html
./scripts/display-mode.sh native   # alias to hybrid HTML, not the old canvas UI
./scripts/display-mode.sh canvas   # emergency old Tk/canvas fallback only
./scripts/display-mode.sh kiosk    # full Chromium fallback only
./scripts/display-mode.sh status
```

## Troubleshooting

If the wall screen does not show the HTML UI:

```bash
systemctl is-enabled smart-thermostat-web.service smart-thermostat-hybrid.service smart-thermostat-native.service smart-thermostat-kiosk.service
systemctl status smart-thermostat-web.service smart-thermostat-hybrid.service --no-pager
journalctl -u smart-thermostat-hybrid.service -n 120 --no-pager
```

If the old native canvas screen is still visible, force the mode again:

```bash
cd ~/Smart-Thermostat-Development
./scripts/force-html-display.sh
sudo reboot
```
