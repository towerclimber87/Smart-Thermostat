#!/usr/bin/env bash
set -euo pipefail

# Configure the Waveshare 10.1-DSI-TOUCH-A for the Raspberry Pi kiosk.
# Default is landscape clockwise rotation for the 800x1280 portrait panel.
# Usage:
#   sudo ./scripts/configure-pi-display.sh 90
#   sudo ./scripts/configure-pi-display.sh 270

ROTATION="${1:-${SMART_DISPLAY_ROTATION:-90}}"
DISPLAY_OUTPUT="${SMART_DISPLAY_OUTPUT:-DSI-1}"
DISPLAY_MODE="${SMART_DISPLAY_MODE:-800x1280e}"
DISPLAY_OVERLAY="${SMART_DISPLAY_OVERLAY:-vc4-kms-dsi-waveshare-panel-v2,10_1_inch_a}"
BOOT_CONFIG_FILE="${SMART_BOOT_CONFIG_FILE:-}"
BOOT_CMDLINE_FILE="${SMART_BOOT_CMDLINE_FILE:-}"
TOUCH_RULE_FILE="${SMART_TOUCH_RULE_FILE:-/etc/udev/rules.d/99-smart-thermostat-touch-rotation.rules}"
DISPLAY_ENV_FILE="${SMART_DISPLAY_ENV_FILE:-/etc/smart-thermostat/display.env}"

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo "${BASH_SOURCE[0]}" "$@"
fi

case "${ROTATION}" in
  0|90|180|270) ;;
  *)
    echo "Rotation must be one of: 0, 90, 180, 270" >&2
    exit 2
    ;;
esac

find_boot_config_file() {
  if [[ -n "${BOOT_CONFIG_FILE}" ]]; then
    echo "${BOOT_CONFIG_FILE}"
    return 0
  fi
  local candidate
  for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

find_boot_cmdline_file() {
  if [[ -n "${BOOT_CMDLINE_FILE}" ]]; then
    echo "${BOOT_CMDLINE_FILE}"
    return 0
  fi
  local candidate
  for candidate in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    if [[ -f "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

backup_file() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    cp -a "${path}" "${path}.smart-thermostat.$(date +%Y%m%d%H%M%S).bak"
  fi
}

ensure_config_txt() {
  local config_file="$1"
  touch "${config_file}"
  backup_file "${config_file}"

  if grep -Eq '^\s*#?\s*dtoverlay=vc4-kms-v3d\b' "${config_file}"; then
    sed -i -E 's|^\s*#?\s*dtoverlay=vc4-kms-v3d.*|dtoverlay=vc4-kms-v3d|' "${config_file}"
  else
    printf '\n# Smart Thermostat Waveshare DSI display\ndtoverlay=vc4-kms-v3d\n' >> "${config_file}"
  fi

  # Keep only one managed 10.1-DSI-TOUCH-A overlay line.
  sed -i -E '/^\s*#?\s*dtoverlay=vc4-kms-dsi-waveshare-panel-v2.*10_1_inch_a/d' "${config_file}"
  printf 'dtoverlay=%s\n' "${DISPLAY_OVERLAY}" >> "${config_file}"
}

ensure_cmdline_txt() {
  local cmdline_file="$1"
  touch "${cmdline_file}"
  backup_file "${cmdline_file}"

  python3 - "${cmdline_file}" "${DISPLAY_OUTPUT}" "${DISPLAY_MODE}" "${ROTATION}" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
output = sys.argv[2]
mode = sys.argv[3]
rotation = sys.argv[4]
text = path.read_text(encoding="utf-8", errors="ignore").strip()
tokens = text.split() if text else []
pattern = re.compile(rf"^video={re.escape(output)}:")
tokens = [token for token in tokens if not pattern.match(token)]
if rotation != "0":
    tokens.append(f"video={output}:{mode},rotate={rotation}")
path.write_text(" ".join(tokens).strip() + "\n", encoding="utf-8")
PY
}

write_touch_rule() {
  local matrix
  case "${ROTATION}" in
    0) matrix="1 0 0 0 1 0" ;;
    90) matrix="0 -1 1 1 0 0" ;;
    180) matrix="-1 0 1 0 -1 1" ;;
    270) matrix="0 1 0 -1 0 1" ;;
  esac

  install -d -m 755 "$(dirname "${TOUCH_RULE_FILE}")"
  cat > "${TOUCH_RULE_FILE}" <<EOF_RULE
# Managed by Smart Thermostat. Keeps the Waveshare capacitive touch panel aligned with display rotation.
ENV{ID_INPUT_TOUCHSCREEN}=="1", ENV{LIBINPUT_CALIBRATION_MATRIX}="${matrix}"
EOF_RULE
  chmod 644 "${TOUCH_RULE_FILE}"
}

write_display_env() {
  install -d -m 755 "$(dirname "${DISPLAY_ENV_FILE}")"
  cat > "${DISPLAY_ENV_FILE}" <<EOF_ENV
SMART_DISPLAY_OUTPUT=${DISPLAY_OUTPUT}
SMART_DISPLAY_MODE=${DISPLAY_MODE}
SMART_DISPLAY_ROTATION=${ROTATION}
SMART_TOUCH_ROTATION=${ROTATION}
SMART_DISPLAY_OVERLAY=${DISPLAY_OVERLAY}
EOF_ENV
  chmod 644 "${DISPLAY_ENV_FILE}"
}

main() {
  local config_file cmdline_file
  config_file="$(find_boot_config_file)" || {
    echo "Could not find /boot/firmware/config.txt or /boot/config.txt" >&2
    exit 1
  }
  cmdline_file="$(find_boot_cmdline_file)" || {
    echo "Could not find /boot/firmware/cmdline.txt or /boot/cmdline.txt" >&2
    exit 1
  }

  ensure_config_txt "${config_file}"
  ensure_cmdline_txt "${cmdline_file}"
  write_touch_rule
  write_display_env

  udevadm control --reload-rules >/dev/null 2>&1 || true
  udevadm trigger >/dev/null 2>&1 || true

  echo "Configured Waveshare DSI display: ${DISPLAY_OUTPUT} ${DISPLAY_MODE}, rotate=${ROTATION}"
  echo "Updated: ${config_file}"
  echo "Updated: ${cmdline_file}"
  echo "Updated: ${TOUCH_RULE_FILE}"
  echo "Reboot is required for display rotation to take effect."
}

main "$@"
