#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${SMART_THERMOSTAT_CONFIG_FILE:-$APP_DIR/data/panel-config.json}"
DISPLAY_OUTPUT="${SMART_THERMOSTAT_DISPLAY_OUTPUT:-DSI-1}"

read_saved_orientation() {
  python3 - "$CONFIG_FILE" <<'PY' 2>/dev/null || true
import json, sys
path = sys.argv[1]
try:
    with open(path, 'r', encoding='utf-8') as fh:
        record = json.load(fh)
    cfg = record.get('config') if isinstance(record, dict) and isinstance(record.get('config'), dict) else record
    display = cfg.get('display') if isinstance(cfg, dict) and isinstance(cfg.get('display'), dict) else {}
    print(display.get('screenOrientation') or display.get('screen_rotation') or display.get('orientation') or '')
except Exception:
    pass
PY
}

normalize_orientation() {
  local value
  value="$(printf '%s' "${1:-upright}" | tr '[:upper:]' '[:lower:]' | tr ' -' '__')"
  case "$value" in
    upside_down|upsidedown|flipped|inverted|left) printf '%s\n' "upside_down" ;;
    *) printf '%s\n' "upright" ;;
  esac
}

orientation_arg="${1:-${SMART_THERMOSTAT_SCREEN_ORIENTATION:-}}"
if [ -z "${orientation_arg:-}" ]; then
  orientation_arg="$(read_saved_orientation)"
fi
SCREEN_ORIENTATION_SETTING="$(normalize_orientation "${orientation_arg:-upright}")"

# The physical DSI panel is portrait.  The native UI is landscape, so the two
# safe choices are the two landscape xrandr rotations.
case "$SCREEN_ORIENTATION_SETTING" in
  upside_down) XRANDR_ROTATION="left" ;;
  *) XRANDR_ROTATION="right" ;;
esac

if command -v xrandr >/dev/null 2>&1; then
  echo "Applying screen orientation: $SCREEN_ORIENTATION_SETTING ($DISPLAY_OUTPUT rotate $XRANDR_ROTATION)"
  xrandr --output "$DISPLAY_OUTPUT" --rotate "$XRANDR_ROTATION" || true
else
  echo "xrandr not installed; cannot rotate display live."
fi

if command -v xinput >/dev/null 2>&1; then
  touch_ids="$(xinput list | awk -F'id=' '
    /slave[[:space:]]+pointer/ && /Goodix|[Tt]ouch|TouchScreen|touchscreen|FT5406|ADS7846/ {
      split($2,a,"[ \t]");
      if (a[1] != "") print a[1]
    }
  ' | sort -u)"
  if [ -z "${touch_ids:-}" ]; then
    echo "No touchscreen pointer devices found to remap."
  else
    for touch_id in $touch_ids; do
      echo "Remapping touch pointer id $touch_id to $DISPLAY_OUTPUT"
      xinput set-prop "$touch_id" "libinput Calibration Matrix" 1 0 0 0 1 0 0 0 1 || true
      xinput set-prop "$touch_id" "Coordinate Transformation Matrix" 1 0 0 0 1 0 0 0 1 || true
      xinput map-to-output "$touch_id" "$DISPLAY_OUTPUT" || true
      xinput list-props "$touch_id" | grep -Ei "Coordinate Transformation Matrix|libinput Calibration Matrix|Device Node" || true
    done
  fi
else
  echo "xinput not installed; cannot remap touchscreen live."
fi
