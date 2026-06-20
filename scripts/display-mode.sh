#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-hybrid}"
WEB_SERVICE="smart-thermostat-web.service"
HYBRID_SERVICE="smart-thermostat-hybrid.service"
NATIVE_SERVICE="smart-thermostat-native.service"
KIOSK_SERVICE="smart-thermostat-kiosk.service"

usage() {
  cat <<'EOF'
Usage: scripts/display-mode.sh hybrid|native|kiosk|status

  hybrid  Recommended wall display. Keep the web/API server running and show
          the exact local HTML UI in a lightweight native WebKit host. Chromium
          kiosk and the old Tk/canvas native screen are disabled.

  native  Fallback no-browser mode. Keep the web/API server running and use the
          Tk/canvas native touchscreen app as the wall display.

  kiosk   Fallback troubleshooting mode. Keep the web/API server running and use
          Chromium kiosk as the wall display.

  status  Show which display services are enabled and active.
EOF
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
  hybrid|webview|html)
    prepare_headless_display_target
    sudo systemctl disable --now "${KIOSK_SERVICE}" "${NATIVE_SERVICE}" >/dev/null 2>&1 || true
    sudo systemctl enable --now "${WEB_SERVICE}" "${HYBRID_SERVICE}"
    echo "Hybrid HTML display mode enabled. The Pi shows the local web UI in a native WebKit host; Chromium kiosk is disabled."
    ;;
  native)
    prepare_headless_display_target
    sudo systemctl disable --now "${KIOSK_SERVICE}" "${HYBRID_SERVICE}" >/dev/null 2>&1 || true
    sudo systemctl enable --now "${WEB_SERVICE}" "${NATIVE_SERVICE}"
    echo "Native fallback display mode enabled. Web/API stays available; Chromium kiosk and hybrid WebKit display are disabled."
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
