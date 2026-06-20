#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-hybrid}"
WEB_SERVICE="smart-thermostat-web.service"
HYBRID_SERVICE="smart-thermostat-hybrid.service"
NATIVE_SERVICE="smart-thermostat-native.service"
KIOSK_SERVICE="smart-thermostat-kiosk.service"

usage() {
  cat <<'USAGE'
Usage: scripts/display-mode.sh hybrid|native|html|canvas|kiosk|status

  hybrid/html/native
          Recommended wall display. Shows the exact local HTML UI from
          http://127.0.0.1:8080 inside a lightweight native WebKit host.
          Chromium kiosk and the old blocky Tk/canvas screen are disabled.

  canvas  Emergency fallback only. Shows the old Tk/canvas native screen.
          Do not use this when you want the clean browser-looking UI.

  kiosk   Troubleshooting fallback. Uses full Chromium kiosk.

  status  Show display service status.

For a stubborn unit that still boots the old blocky native screen, run:
  ./scripts/force-html-display.sh
USAGE
}

status() {
  systemctl is-enabled "${WEB_SERVICE}" "${HYBRID_SERVICE}" "${NATIVE_SERVICE}" "${KIOSK_SERVICE}" 2>/dev/null || true
  systemctl --no-pager --full status "${WEB_SERVICE}" "${HYBRID_SERVICE}" "${NATIVE_SERVICE}" "${KIOSK_SERVICE}" 2>/dev/null || true
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
  hybrid|webview|html|native)
    prepare_headless_display_target
    sudo systemctl disable --now "${KIOSK_SERVICE}" "${NATIVE_SERVICE}" >/dev/null 2>&1 || true
    sudo systemctl enable --now "${WEB_SERVICE}" "${HYBRID_SERVICE}"
    echo "HTML hybrid display enabled. The wall panel now shows the same web UI from port 8080 in native WebKit; Chromium and the old blocky native canvas are disabled."
    ;;
  canvas|tk|fallback)
    prepare_headless_display_target
    sudo systemctl disable --now "${KIOSK_SERVICE}" "${HYBRID_SERVICE}" >/dev/null 2>&1 || true
    sudo systemctl enable --now "${WEB_SERVICE}" "${NATIVE_SERVICE}"
    echo "Canvas fallback display enabled. This is the old blocky native screen, not the clean HTML web UI."
    ;;
  kiosk|chromium)
    sudo systemctl disable --now "${NATIVE_SERVICE}" "${HYBRID_SERVICE}" >/dev/null 2>&1 || true
    sudo systemctl set-default graphical.target >/dev/null 2>&1 || true
    sudo systemctl daemon-reload
    sudo systemctl enable --now "${WEB_SERVICE}" "${KIOSK_SERVICE}"
    echo "Chromium kiosk fallback enabled. Reboot if the desktop target was just changed."
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
