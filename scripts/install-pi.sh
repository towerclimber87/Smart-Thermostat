#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_SERVICE_NAME="smart-thermostat-web.service"
KIOSK_SERVICE_NAME="smart-thermostat-kiosk.service"
NATIVE_SERVICE_NAME="smart-thermostat-native.service"
UPDATE_AGENT_SERVICE_NAME="smart-thermostat-update-agent.service"
NETWORK_WATCHDOG_SERVICE_NAME="smart-thermostat-network-watchdog.service"

if [[ $EUID -eq 0 ]]; then
  echo "Run this script as the pi/user account, not root. It will use sudo when needed."
  exit 1
fi

INSTALL_USER="$(id -un)"

install_packages() {
  if ! command -v apt-get >/dev/null 2>&1; then
    return 0
  fi

  sudo apt-get update

  local packages=(
    avahi-daemon
    git
    python3
    python3-zeroconf
    x11-xserver-utils
    xserver-xorg
    xinit
    openbox
    dbus-x11
    unclutter
    python3-tk
  )

  if apt-cache show wlr-randr >/dev/null 2>&1; then
    packages+=(wlr-randr)
  fi

  local optional_hardware_packages=(
    python3-gpiozero
    python3-rpi.gpio
    python3-smbus
    i2c-tools
  )
  local optional_package
  for optional_package in "${optional_hardware_packages[@]}"; do
    if apt-cache show "${optional_package}" >/dev/null 2>&1; then
      packages+=("${optional_package}")
    else
      echo "WARNING: ${optional_package} is not available in apt; hardware page will use fallbacks if needed." >&2
    fi
  done

  # Chromium is no longer required for the wall display.  Keep it optional so
  # existing browser-kiosk installs can still be serviced, but do not make the
  # appliance depend on it.
  if apt-cache show chromium-browser >/dev/null 2>&1; then
    packages+=(chromium-browser)
  elif apt-cache show chromium >/dev/null 2>&1; then
    packages+=(chromium)
  fi

  sudo apt-get install -y "${packages[@]}"
}

install_service() {
  local service_name="$1"
  local source_path="${PROJECT_DIR}/systemd/${service_name}"
  local service_path="/etc/systemd/system/${service_name}"

  if [[ ! -f "${source_path}" ]]; then
    echo "Skipping optional service ${service_name}; ${source_path} is not included in this build."
    return 0
  fi

  local user_id
  user_id="$(id -u)"
  sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" -e "s|__USER__|${INSTALL_USER}|g" -e "s|__UID__|${user_id}|g" "${source_path}" > "/tmp/${service_name}"
  sudo cp "/tmp/${service_name}" "${service_path}"
}

enable_i2c() {
  echo "Configuring Raspberry Pi I2C support..."

  local config_file=""
  local candidate
  for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "${candidate}" ]]; then
      config_file="${candidate}"
      break
    fi
  done

  if command -v raspi-config >/dev/null 2>&1; then
    sudo raspi-config nonint do_i2c 0 || true
  fi

  # Also edit config.txt directly. On some Debian/Raspberry Pi OS builds,
  # raspi-config may be unavailable or may not update the same boot config file.
  if [[ -n "${config_file}" ]]; then
    if grep -Eq '^\s*dtparam=i2c_arm=on' "${config_file}"; then
      true
    elif grep -Eq '^\s*#?\s*dtparam=i2c_arm=' "${config_file}"; then
      sudo sed -i -E 's|^\s*#?\s*dtparam=i2c_arm=.*|dtparam=i2c_arm=on|' "${config_file}"
    else
      echo 'dtparam=i2c_arm=on' | sudo tee -a "${config_file}" >/dev/null
    fi
  else
    echo "WARNING: Could not find /boot/firmware/config.txt or /boot/config.txt to enable I2C." >&2
  fi

  echo i2c-dev | sudo tee /etc/modules-load.d/smart-thermostat-i2c.conf >/dev/null
  sudo modprobe i2c-dev >/dev/null 2>&1 || true

  if [[ ! -e /dev/i2c-1 ]]; then
    echo "NOTE: /dev/i2c-1 is not available yet. Reboot the Pi after this install before testing I2C sensors."
  fi
}

install_xorg_kiosk_permissions() {
  if [[ -d /etc/X11 ]]; then
    sudo tee /etc/X11/Xwrapper.config >/dev/null <<'EOF_XWRAPPER'
allowed_users=anybody
needs_root_rights=yes
EOF_XWRAPPER
  fi
}

install_appliance_boot_mode() {
  # This controller is a thermostat appliance: no LightDM/labwc desktop should
  # compete with the kiosk for the display.  Keep the packages available for X,
  # but disable graphical login and let smart-thermostat-kiosk own tty7.
  sudo systemctl disable --now display-manager.service >/dev/null 2>&1 || true
  sudo systemctl disable --now lightdm.service >/dev/null 2>&1 || true
  sudo systemctl disable --now gdm.service >/dev/null 2>&1 || true
  sudo systemctl set-default multi-user.target >/dev/null 2>&1 || true
}

install_sudoers() {
  local systemctl_bin
  systemctl_bin="$(command -v systemctl || echo /usr/bin/systemctl)"

  sudo install -d -m 755 /usr/local/sbin
  sudo tee /usr/local/sbin/smart-thermostat-reboot >/dev/null <<'EOF_REBOOT_HELPER'
#!/usr/bin/env bash
set -euo pipefail
if command -v systemctl >/dev/null 2>&1; then
  exec systemctl reboot
fi
exec /sbin/reboot
EOF_REBOOT_HELPER
  sudo chmod 755 /usr/local/sbin/smart-thermostat-reboot

  sudo tee /etc/sudoers.d/smart-thermostat-panel >/dev/null <<EOF_SUDOERS
Cmnd_Alias SMART_THERMOSTAT_REBOOT = /usr/local/sbin/smart-thermostat-reboot, /usr/bin/systemctl reboot, /bin/systemctl reboot, ${systemctl_bin} reboot, /usr/sbin/reboot, /sbin/reboot, /usr/bin/reboot
Cmnd_Alias SMART_THERMOSTAT_WEB_RESTART = /usr/bin/systemctl restart ${WEB_SERVICE_NAME}, /bin/systemctl restart ${WEB_SERVICE_NAME}, ${systemctl_bin} restart ${WEB_SERVICE_NAME}
Cmnd_Alias SMART_THERMOSTAT_NATIVE_RESTART = /usr/bin/systemctl restart ${NATIVE_SERVICE_NAME}, /bin/systemctl restart ${NATIVE_SERVICE_NAME}, ${systemctl_bin} restart ${NATIVE_SERVICE_NAME}
Cmnd_Alias SMART_THERMOSTAT_DISPLAY_RESTART = /usr/bin/systemctl restart ${WEB_SERVICE_NAME} ${NATIVE_SERVICE_NAME}, /bin/systemctl restart ${WEB_SERVICE_NAME} ${NATIVE_SERVICE_NAME}, ${systemctl_bin} restart ${WEB_SERVICE_NAME} ${NATIVE_SERVICE_NAME}
${INSTALL_USER} ALL=(root) NOPASSWD: SMART_THERMOSTAT_REBOOT, SMART_THERMOSTAT_WEB_RESTART, SMART_THERMOSTAT_NATIVE_RESTART, SMART_THERMOSTAT_DISPLAY_RESTART
EOF_SUDOERS
  sudo chmod 440 /etc/sudoers.d/smart-thermostat-panel
  sudo visudo -cf /etc/sudoers.d/smart-thermostat-panel >/dev/null
}

install_packages
if getent group gpio >/dev/null 2>&1; then
  sudo usermod -aG gpio "${INSTALL_USER}" || true
fi
if getent group i2c >/dev/null 2>&1; then
  sudo usermod -aG i2c "${INSTALL_USER}" || true
fi
enable_i2c
chmod +x "${PROJECT_DIR}/scripts/install-pi.sh" "${PROJECT_DIR}/scripts/kiosk-launch.sh" "${PROJECT_DIR}/scripts/kiosk-xinit.sh" "${PROJECT_DIR}/scripts/native-launch.sh" "${PROJECT_DIR}/scripts/native-xinit.sh" "${PROJECT_DIR}/scripts/appliance-mode.sh" "${PROJECT_DIR}/scripts/configure-pi-display.sh" "${PROJECT_DIR}/scripts/network_watchdog.py" 2>/dev/null || true
install_xorg_kiosk_permissions
install_appliance_boot_mode

install_service "${WEB_SERVICE_NAME}"
install_service "${KIOSK_SERVICE_NAME}"
install_service "${NATIVE_SERVICE_NAME}"
install_service "${UPDATE_AGENT_SERVICE_NAME}"
install_service "${NETWORK_WATCHDOG_SERVICE_NAME}"

sudo install -d -m 700 /etc/smart-thermostat
if [[ ! -f /etc/smart-thermostat/network-watchdog.env ]]; then
  sudo tee /etc/smart-thermostat/network-watchdog.env >/dev/null <<'EOF_NETWORK'
NETWORK_WATCHDOG_INTERVAL_SECONDS=60
NETWORK_WATCHDOG_TARGET=1.1.1.1
NETWORK_WATCHDOG_ETHERNET_METRIC=50
NETWORK_WATCHDOG_WIFI_METRIC=600
EOF_NETWORK
  sudo chmod 600 /etc/smart-thermostat/network-watchdog.env
fi

if [[ ! -f /etc/smart-thermostat/kiosk.env ]]; then
  sudo tee /etc/smart-thermostat/kiosk.env >/dev/null <<'EOF_KIOSK'
SMART_THERMOSTAT_URL=http://127.0.0.1:8080
SMART_KIOSK_PROFILE_DIR=/tmp/smart-thermostat-chromium-profile
SMART_KIOSK_CACHE_DIR=/tmp/smart-thermostat-chromium-cache
SMART_KIOSK_HEALTH_TIMEOUT_SECONDS=75
# auto, wayland, or x11. Auto lets the launcher use Wayland when the desktop exposes it.
SMART_KIOSK_OZONE_PLATFORM=x11
SMART_KIOSK_LOW_POWER_MODE=1
SMART_KIOSK_ROTATION=left
SMART_KIOSK_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
# Optional extra Chromium flags. Example:
# SMART_KIOSK_EXTRA_FLAGS=--force-device-scale-factor=1
SMART_KIOSK_EXTRA_FLAGS=
EOF_KIOSK
  sudo chmod 600 /etc/smart-thermostat/kiosk.env
fi

if [[ ! -f /etc/smart-thermostat/native.env ]]; then
  sudo tee /etc/smart-thermostat/native.env >/dev/null <<'EOF_NATIVE'
SMART_THERMOSTAT_API=http://127.0.0.1:8080
SMART_NATIVE_HEALTH_TIMEOUT_SECONDS=75
SMART_NATIVE_ROTATION=left
SMART_NATIVE_TOUCH_MATRIX=-1 0 1 0 -1 1 0 0 1
SMART_NATIVE_POLL_MS=1500
SMART_NATIVE_SLOW_POLL_MS=8000
SMART_NATIVE_THERMOSTAT_ONLY=0
SMART_NATIVE_VISUAL_MODE=web_parity
EOF_NATIVE
  sudo chmod 600 /etc/smart-thermostat/native.env
fi

install_sudoers

sudo systemctl daemon-reload
sudo systemctl enable --now "${WEB_SERVICE_NAME}"
sudo systemctl enable --now "${NETWORK_WATCHDOG_SERVICE_NAME}"
sudo systemctl disable --now "${KIOSK_SERVICE_NAME}" >/dev/null 2>&1 || true
sudo systemctl enable --now "${NATIVE_SERVICE_NAME}" || true
if [[ -f "/etc/systemd/system/${UPDATE_AGENT_SERVICE_NAME}" ]]; then
  sudo systemctl enable --now "${UPDATE_AGENT_SERVICE_NAME}" || true
fi

echo "IHA web service, native thermostat display service, and network watchdog installed."
echo "Web UI: http://localhost:8080"
echo "Native display service: sudo systemctl status ${NATIVE_SERVICE_NAME}"
echo "Waveshare 10.1 DSI rotation helper: sudo ${PROJECT_DIR}/scripts/configure-pi-display.sh 90"
echo "Home Assistant discovery uses mDNS service _iha-thermostat._tcp.local."
