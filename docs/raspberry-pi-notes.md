# Raspberry Pi Notes

This starter project is designed to run as a lightweight static web UI on a Raspberry Pi with a touchscreen.

## Display target

Initial assumed display:

- 10.1 inch Waveshare DSI capacitive touch display
- 800×1280 portrait-native or software-rotated landscape
- Raspberry Pi 3B/4B/5 compatible

The UI is responsive and should work in either landscape or portrait, but the intended wall-panel layout is landscape.

## Run the UI manually

```bash
cd ~/Smart-Thermostat
python3 -m http.server 8080 --directory public
```

## Install as a system service

The included `scripts/install-pi.sh` copies the systemd service and enables it.

```bash
chmod +x scripts/install-pi.sh
./scripts/install-pi.sh
```

Then browse to:

```text
http://localhost:8080
```

## Future kiosk note

For first testing, use a normal browser on your development computer.

For the wall panel, you can later choose between:

1. Chromium kiosk mode.
2. A native wrapper app such as Electron/Tauri-style packaging.
3. A Qt/QML native app if you decide to move away from browser UI.

The backend control service should remain separate from the UI either way.

## GPIO caution

Use proper interface hardware. Raspberry Pi GPIO is control logic only, not relay coil power.
