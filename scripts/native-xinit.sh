#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NATIVE_SCRIPT="${PROJECT_DIR}/scripts/native-launch.sh"
DISPLAY_NUMBER="${SMART_NATIVE_DISPLAY_NUMBER:-0}"
DISPLAY=":${DISPLAY_NUMBER}"
export DISPLAY

ensure_runtime_dir() {
  local runtime_dir="${XDG_RUNTIME_DIR:-}"
  if [[ -z "${runtime_dir}" || ! -d "${runtime_dir}" || ! -w "${runtime_dir}" ]]; then
    runtime_dir="/tmp/smart-thermostat-runtime-$(id -u)"
    mkdir -p "${runtime_dir}"
    chmod 700 "${runtime_dir}"
    export XDG_RUNTIME_DIR="${runtime_dir}"
  fi
}

run_existing_display() {
  if [[ -S "/tmp/.X11-unix/X${DISPLAY_NUMBER}" ]]; then
    exec "${NATIVE_SCRIPT}"
  fi
}

find_openbox() {
  if command -v openbox-session >/dev/null 2>&1; then
    command -v openbox-session
    return 0
  fi
  if command -v openbox >/dev/null 2>&1; then
    command -v openbox
    return 0
  fi
  return 1
}

ensure_runtime_dir
run_existing_display

if ! command -v xinit >/dev/null 2>&1; then
  echo "xinit is not installed. Run scripts/install-pi.sh to install the native display packages." >&2
  exit 1
fi

OPENBOX_BIN="$(find_openbox || true)"
if [[ -z "${OPENBOX_BIN}" ]]; then
  echo "openbox is not installed. Run scripts/install-pi.sh to install the native display packages." >&2
  exit 1
fi

STARTUP_SCRIPT="/tmp/smart-thermostat-native-xinitrc-$(id -u).sh"
cat > "${STARTUP_SCRIPT}" <<EOF_STARTUP
#!/usr/bin/env bash
set -euo pipefail
export DISPLAY=${DISPLAY}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR}
${OPENBOX_BIN} >/tmp/smart-thermostat-native-openbox.log 2>&1 &
exec ${NATIVE_SCRIPT}
EOF_STARTUP
chmod +x "${STARTUP_SCRIPT}"

X_ARGS=("/usr/bin/X" "${DISPLAY}" "-nolisten" "tcp" "-nocursor")
if [[ -e /dev/tty7 ]]; then
  X_ARGS+=("vt7")
fi

exec xinit "${STARTUP_SCRIPT}" -- "${X_ARGS[@]}"
