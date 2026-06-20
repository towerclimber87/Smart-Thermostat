#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-native}"
WEB_SERVICE="smart-thermostat-web.service"
NATIVE_SERVICE="smart-thermostat-native.service"
KIOSK_SERVICE="smart-thermostat-kiosk.service"

usage() {
  cat <<'EOF'
Usage: scripts/display-mode.sh native|kiosk|status

  native  Keep the web/API server running, disable Chromium kiosk, and run the
          native touchscreen app as the wall display. This is the recommended
          Raspberry Pi appliance mode.

  kiosk   Fallback mode. Keep the web/API server running and use Chromium kiosk
          as the wall display. Only use this for troubleshooting.

  status  Show which display services are enabled and active.
EOF
}

status() {
  systemctl is-enabled "${WEB_SERVICE}" "${NATIVE_SERVICE}" "${KIOSK_SERVICE}" 2>/dev/null || true
  systemctl --no-pager --full status "${WEB_SERVICE}" "${NATIVE_SERVICE}" "${KIOSK_SERVICE}" 2>/dev/null || true
}

case "${MODE}" in
  native)
    sudo systemctl disable --now display-manager.service >/dev/null 2>&1 || true
    sudo systemctl disable --now lightdm.service >/dev/null 2>&1 || true
    sudo systemctl disable --now gdm.service >/dev/null 2>&1 || true
    sudo systemctl disable --now "${KIOSK_SERVICE}" >/dev/null 2>&1 || true
    sudo systemctl set-default multi-user.target >/dev/null 2>&1 || true
    sudo rm -f /tmp/.X0-lock /tmp/.X11-unix/X0 2>/dev/null || true
    sudo systemctl daemon-reload
    sudo systemctl enable --now "${WEB_SERVICE}" "${NATIVE_SERVICE}"
    echo "Native display mode enabled. Web/API stays available; Chromium kiosk is disabled."
    ;;
  kiosk|chromium)
    sudo systemctl disable --now "${NATIVE_SERVICE}" >/dev/null 2>&1 || true
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
