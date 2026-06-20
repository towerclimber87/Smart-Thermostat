
# Smart Thermostat 12.2 - Fast HTML Appliance Runtime

This build changes the recommended wall-display runtime back to an optimized Chromium kiosk under bare X11.

Why: the GTK/WebKit host can display the same HTML UI, but on the Pi it can peg the CPU on the glass/animation-heavy interface. Optimized Chromium is the smoother runtime for this UI and still avoids the old SD-card write problem by storing its profile/cache in `/tmp`.

## Recommended startup

```bash
cd ~/Smart-Thermostat-Development
chmod +x scripts/*.sh native/*.py
./scripts/install-pi.sh
./scripts/appliance-mode.sh
sudo reboot
```

## Switch display modes

```bash
./scripts/display-mode.sh fast      # recommended: clean HTML UI in optimized Chromium under bare X11
./scripts/display-mode.sh hybrid    # fallback: clean HTML UI in GTK/WebKit
./scripts/display-mode.sh canvas    # emergency fallback: old Tk/canvas screen
./scripts/display-mode.sh status
```

## Optional service cleanup

```bash
./scripts/trim-appliance-services.sh
```

This disables Bluetooth and disables NFS/RPC only when no NFS mounts are active.

## Verify the display runtime

```bash
ps -ef | grep -E "chromium|html_panel.py|thermostat_native.py|WebKit|xinit|Xorg" | grep -v grep
```

Recommended runtime should show Chromium and should not show `thermostat_native.py`.
