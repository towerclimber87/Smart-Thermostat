# Raspberry Pi Wall Panel Notes

This build is set up for a Raspberry Pi 4 wall-mounted thermostat panel running the local Python server plus Chromium in kiosk mode.

## Hardware target

- Raspberry Pi 4
- 10.1 inch DSI capacitive touch display, 800×1280 portrait-native panel
- PoE HAT with active cooling
- Wall case with limited airflow

The UI still works in a normal desktop browser, but the production target is the local kiosk URL:

```text
http://127.0.0.1:8080
```

## Recommended OS approach

Use Raspberry Pi OS with a desktop session for the wall panel. The kiosk service expects a graphical session on `DISPLAY=:0` and launches Chromium as the normal Pi user. Avoid installing extra office/games/media packages. The code does not require Node, Electron, or a heavy app wrapper on the Pi.

## Install/update on the Pi

From the project folder:

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/install-pi.sh scripts/kiosk-launch.sh scripts/configure-pi-display.sh scripts/network_watchdog.py
./scripts/install-pi.sh
sudo reboot
```

The installer enables these services:

```bash
smart-thermostat-web.service
smart-thermostat-kiosk.service
smart-thermostat-network-watchdog.service
```

If the Pi is currently configured to boot to console, switch it to desktop boot:

```bash
sudo systemctl set-default graphical.target
sudo reboot
```

## Kiosk mode

`systemd/smart-thermostat-kiosk.service` runs `scripts/kiosk-launch.sh`, which:

- waits for `/api/health` before opening Chromium
- launches Chromium full-screen at `http://127.0.0.1:8080`
- disables first-run prompts, default apps, translate, sync, background networking, and browser crash bubbles
- uses `/tmp` for the Chromium profile/cache to reduce SD-card writes
- hides the mouse pointer when `unclutter` is installed
- disables display blanking through `xset` when available

Useful commands:

```bash
sudo systemctl status smart-thermostat-web.service --no-pager -l
sudo systemctl status smart-thermostat-kiosk.service --no-pager -l
sudo systemctl restart smart-thermostat-kiosk.service
journalctl -u smart-thermostat-kiosk.service -f
```

Kiosk settings live here:

```bash
sudo nano /etc/smart-thermostat/kiosk.env
```

Defaults:

```bash
SMART_THERMOSTAT_URL=http://127.0.0.1:8080
SMART_KIOSK_PROFILE_DIR=/tmp/smart-thermostat-chromium-profile
SMART_KIOSK_CACHE_DIR=/tmp/smart-thermostat-chromium-cache
SMART_KIOSK_HEALTH_TIMEOUT_SECONDS=45
SMART_KIOSK_EXTRA_FLAGS=
```

For a display scale test, add something like this to `SMART_KIOSK_EXTRA_FLAGS`:

```bash
SMART_KIOSK_EXTRA_FLAGS=--force-device-scale-factor=1
```

Then restart kiosk:

```bash
sudo systemctl restart smart-thermostat-kiosk.service
```

## Touch/display orientation

The Waveshare 10.1-DSI-TOUCH-A is an 800×1280 portrait-native DSI panel. The intended thermostat wall layout is landscape, so configure the Pi display and touch matrix together:

```bash
cd ~/Smart-Thermostat-Development
sudo ./scripts/configure-pi-display.sh 90
sudo reboot
```

If the picture is upside-down for the way the panel is mounted, use 270 instead:

```bash
sudo ./scripts/configure-pi-display.sh 270
sudo reboot
```

The helper updates the Raspberry Pi boot config with the Waveshare DSI overlay, adds the DSI rotation command to `cmdline.txt`, and writes a libinput touch calibration rule so taps line up with the rotated picture. It also saves timestamped backups of the boot files before editing them.

The settings written by the helper are also recorded here for troubleshooting:

```bash
cat /etc/smart-thermostat/display.env
```

## Home Assistant traffic optimization

The panel keeps thermostat sync local and lightweight. Home Assistant-backed pages poll only when useful:

- thermostat local status: every 1 second, local Pi only
- active HA-backed page: every 5 seconds
- inactive HA-backed pages: at most every 5 minutes
- after a button/slider command: quick follow-up polls so the UI confirms the change

The Python backend batches Home Assistant entity state reads when multiple entities are needed, and uses a short shared `/api/states` cache so the Pi does not ask Home Assistant for the same state table repeatedly when several widgets refresh at the same time.

## Network watchdog

The network watchdog keeps Ethernet preferred and retries Wi-Fi recovery every 60 seconds when needed. Settings:

```bash
sudo nano /etc/smart-thermostat/network-watchdog.env
```

## Thermal checks

Check temperature and throttling after the screen, HAT, and case are installed:

```bash
vcgencmd measure_temp
vcgencmd get_throttled
```

`get_throttled=0x0` is ideal. Any non-zero value means the Pi has seen undervoltage or thermal throttling at some point since boot.

## Notes on power/heat

The kiosk intentionally avoids Electron and keeps Chromium stripped down. Do not disable GPU acceleration unless you see display artifacts, because software rendering usually increases CPU load and heat. If the wall case gets warm, verify the HAT fan is running and confirm the screen brightness/backlight setting on the display hardware.
