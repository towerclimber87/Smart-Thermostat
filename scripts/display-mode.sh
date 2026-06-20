#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-fast}"
WEB_SERVICE="smart-thermostat-web.service"
HYBRID_SERVICE="smart-thermostat-hybrid.service"
NATIVE_SERVICE="smart-thermostat-native.service"
KIOSK_SERVICE="smart-thermostat-kiosk.service"

usage() {
  cat <<'USAGE'
Usage: scripts/display-mode.sh fast|kiosk|chromium|hybrid|html|canvas|status

  fast/kiosk/chromium
          Recommended wall display. Shows the exact local HTML UI from
          http://127.0.0.1:8080 in optimized Chromium running under bare X11.
          This is faster on the Pi than GTK/WebKit for the current glassy UI.
          Profile/cache stay in /tmp so Chromium does not hammer the SD card.

  hybrid/html/webview
          GTK/WebKit host for the same HTML UI. Kept as a fallback, but it can
          be slower on Raspberry Pi with the current animation/glass effects.

  canvas  Emergency fallback only. Shows the old Tk/canvas native screen.
          Do not use this when you want the clean browser-looking UI.

  status  Show display service status.
USAGE
}

status() {
  systemctl is-enabled "${WEB_SERVICE}" "${KIOSK_SERVICE}" "${HYBRID_SERVICE}" "${NATIVE_SERVICE}" 2>/dev/null || true
  systemctl --no-pager --full status "${WEB_SERVICE}" "${KIOSK_SERVICE}" "${HYBRID_SERVICE}" "${NATIVE_SERVICE}" 2>/dev/null || true
}

prepare_headless_display_target() {
  sudo systemctl disable --now display-manager.service >/dev/null 2>&1 || true
  sudo systemctl disable --now lightdm.service >/dev/null 2>&1 || true
  sudo systemctl disable --now gdm.service >/dev/null 2>&1 || true
  sudo systemctl set-default multi-user.target >/dev/null 2>&1 || true
  sudo rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 2>/dev/null || true
  sudo systemctl daemon-reload
}

case "${MODE}" in
  fast|kiosk|chromium|chrome)
    prepare_headless_display_target
    sudo systemctl disable --now "${NATIVE_SERVICE}" "${HYBRID_SERVICE}" >/dev/null 2>&1 || true
    sudo systemctl enable --now "${WEB_SERVICE}" "${KIOSK_SERVICE}"
    echo "Fast HTML display enabled. The wall panel now shows the same web UI from port 8080 in optimized Chromium under bare X11; GTK/WebKit and the old blocky canvas are disabled."
    ;;
  hybrid|webview|html)
    prepare_headless_display_target
    sudo systemctl disable --now "${KIOSK_SERVICE}" "${NATIVE_SERVICE}" >/dev/null 2>&1 || true
    sudo systemctl enable --now "${WEB_SERVICE}" "${HYBRID_SERVICE}"
    echo "GTK/WebKit HTML display enabled. This still shows the same web UI, but may be slower than fast Chromium on this Pi."
    ;;
  canvas|tk|fallback)
    prepare_headless_display_target
    sudo systemctl disable --now "${KIOSK_SERVICE}" "${HYBRID_SERVICE}" >/dev/null 2>&1 || true
    sudo systemctl enable --now "${WEB_SERVICE}" "${NATIVE_SERVICE}"
    echo "Canvas fallback display enabled. This is the old blocky native screen, not the clean HTML web UI."
    ;;
  status)
    status
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
