#!/usr/bin/env python3
"""Network watchdog for Raspberry Pi climate controllers.

Keeps Ethernet preferred and keeps saved Wi-Fi networks reconnecting forever.
The watchdog is intentionally conservative once the Pi has a local IP address:
a home LAN without internet is still a useful network for the local thermostat,
Home Assistant, SSH, and update agent recovery.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from pathlib import Path

CHECK_INTERVAL_SECONDS = int(os.getenv("NETWORK_WATCHDOG_INTERVAL_SECONDS", "30") or "30")
CONNECTIVITY_TARGET = os.getenv("NETWORK_WATCHDOG_TARGET", "1.1.1.1").strip() or "1.1.1.1"
ETHERNET_METRIC = os.getenv("NETWORK_WATCHDOG_ETHERNET_METRIC", "50").strip() or "50"
WIFI_METRIC = os.getenv("NETWORK_WATCHDOG_WIFI_METRIC", "600").strip() or "600"
WIFI_RESCAN_SECONDS = int(os.getenv("NETWORK_WATCHDOG_WIFI_RESCAN_SECONDS", "30") or "30")
SAVED_WIFI_AUTOCONNECT_PRIORITY = os.getenv("NETWORK_WATCHDOG_WIFI_PRIORITY", "20").strip() or "20"
METRIC_REFRESH_SECONDS = int(os.getenv("NETWORK_WATCHDOG_METRIC_REFRESH_SECONDS", "21600") or "21600")
LOG_REPEAT_SECONDS = int(os.getenv("NETWORK_WATCHDOG_LOG_REPEAT_SECONDS", "1800") or "1800")

SYS_NET = Path("/sys/class/net")
_LAST_WIFI_RESCAN_AT: dict[str, float] = {}
_LAST_METRIC_REFRESH_AT = 0.0
_LAST_LOG_AT: dict[str, float] = {}


def log(message: str, *, force: bool = False) -> None:
    now = time.time()
    if not force and LOG_REPEAT_SECONDS > 0:
        last = float(_LAST_LOG_AT.get(message, 0))
        if now - last < LOG_REPEAT_SECONDS:
            return
    _LAST_LOG_AT[message] = now
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def run(cmd: list[str], *, timeout: int = 15, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=check,
    )


def command_exists(name: str) -> bool:
    proc = run(["/bin/sh", "-lc", f"command -v {name} >/dev/null 2>&1"], timeout=5)
    return proc.returncode == 0


def interfaces(patterns: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    if not SYS_NET.exists():
        return found
    for item in SYS_NET.iterdir():
        name = item.name
        if name == "lo":
            continue
        if any(re.match(pattern, name) for pattern in patterns):
            found.append(name)
    return sorted(found)


def ethernet_interfaces() -> list[str]:
    return interfaces((r"^eth\d+$", r"^en[a-zA-Z0-9_.-]+$"))


def wifi_interfaces() -> list[str]:
    return interfaces((r"^wlan\d+$", r"^wl[a-zA-Z0-9_.-]+$"))


def carrier_up(iface: str) -> bool:
    carrier_file = SYS_NET / iface / "carrier"
    try:
        return carrier_file.read_text(encoding="utf-8").strip() == "1"
    except OSError:
        return False


def has_ipv4(iface: str) -> bool:
    proc = run(["ip", "-4", "addr", "show", "dev", iface], timeout=5)
    return proc.returncode == 0 and " inet " in proc.stdout


def ping_ok(target: str, iface: str | None = None, timeout_seconds: int = 2) -> bool:
    cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_seconds))]
    if iface:
        cmd.extend(["-I", iface])
    cmd.append(target)
    proc = run(cmd, timeout=max(4, timeout_seconds + 3))
    return proc.returncode == 0


def default_route_iface() -> str:
    proc = run(["ip", "route", "show", "default"], timeout=5)
    if proc.returncode != 0:
        return ""
    match = re.search(r"\bdev\s+(\S+)", proc.stdout)
    return match.group(1) if match else ""


def default_gateway_for_iface(iface: str) -> str:
    proc = run(["ip", "route", "show", "default", "dev", iface], timeout=5)
    if proc.returncode != 0:
        return ""
    match = re.search(r"\bvia\s+(\S+)", proc.stdout)
    return match.group(1) if match else ""


def nmcli(*args: str, timeout: int = 20) -> bool:
    if not command_exists("nmcli"):
        return False
    proc = run(["nmcli", *args], timeout=timeout)
    if proc.returncode != 0:
        log(f"nmcli {' '.join(args)} failed: {proc.stdout.strip()}")
    return proc.returncode == 0


def nmcli_output(*args: str, timeout: int = 20) -> str:
    if not command_exists("nmcli"):
        return ""
    proc = run(["nmcli", *args], timeout=timeout)
    return proc.stdout if proc.returncode == 0 else ""


def nmcli_get(*args: str, timeout: int = 10) -> str:
    output = nmcli_output("--terse", "--escape", "no", "-g", *args, timeout=timeout)
    return output.strip()


def active_connection_name(iface: str) -> str:
    name = nmcli_get("GENERAL.CONNECTION", "device", "show", iface) or ""
    return "" if name.strip() in {"", "--"} else name.strip()


def connection_field(name: str, field: str) -> str:
    return nmcli_get(field, "connection", "show", name) or ""


def connection_is_wifi_ap(name: str) -> bool:
    mode = connection_field(name, "802-11-wireless.mode").strip().lower()
    return mode in {"ap", "adhoc"} or "hotspot" in name.lower()


def connection_ssid(name: str) -> str:
    return connection_field(name, "802-11-wireless.ssid").strip() or name


def saved_wifi_connections() -> list[dict[str, str]]:
    proc = run(["nmcli", "--terse", "--escape", "no", "-f", "NAME,TYPE,AUTOCONNECT", "connection", "show"], timeout=10)
    if proc.returncode != 0:
        return []
    saved: list[dict[str, str]] = []
    for raw_line in proc.stdout.splitlines():
        parts = raw_line.split(":")
        if len(parts) < 3:
            continue
        name, conn_type, autoconnect = parts[0].strip(), parts[1].strip().lower(), parts[2].strip().lower()
        if conn_type not in {"wifi", "802-11-wireless", "wireless"}:
            continue
        if connection_is_wifi_ap(name):
            continue
        ssid = connection_ssid(name)
        saved.append({"name": name, "ssid": ssid, "autoconnect": autoconnect})
    return saved


def connection_needs_fields(name: str, expected: dict[str, str]) -> bool:
    for field, desired in expected.items():
        current = connection_field(name, field).strip().lower()
        if current != str(desired).strip().lower():
            return True
    return False


def configure_network_metrics(*, force: bool = False) -> None:
    """Best-effort persistent Ethernet-over-Wi-Fi route priority and infinite Wi-Fi retry.

    This used to call `nmcli connection modify` on every watchdog cycle. Even
    when the values were already correct, that can rewrite NetworkManager's
    connection profiles on the SD card. Now it is throttled and idempotent: we
    read the current values first and only modify profiles when something is
    actually wrong or a new saved Wi-Fi profile appears.
    """
    global _LAST_METRIC_REFRESH_AT
    now = time.time()
    if not force and now - _LAST_METRIC_REFRESH_AT < max(300, METRIC_REFRESH_SECONDS):
        return
    _LAST_METRIC_REFRESH_AT = now
    if not command_exists("nmcli"):
        return
    proc = run(["nmcli", "--terse", "--escape", "no", "-f", "NAME,TYPE", "connection", "show"], timeout=10)
    if proc.returncode != 0:
        return
    for raw_line in proc.stdout.splitlines():
        if not raw_line.strip() or ":" not in raw_line:
            continue
        name, conn_type = raw_line.rsplit(":", 1)
        conn_type = conn_type.strip().lower()
        if conn_type in {"ethernet", "802-3-ethernet"}:
            expected = {
                "connection.autoconnect": "yes",
                "ipv4.route-metric": ETHERNET_METRIC,
                "ipv6.route-metric": ETHERNET_METRIC,
            }
            if connection_needs_fields(name, expected):
                log(f"Refreshing Ethernet connection profile '{name}' for route priority.")
                nmcli("connection", "modify", name, "connection.autoconnect", "yes", "ipv4.route-metric", ETHERNET_METRIC, "ipv6.route-metric", ETHERNET_METRIC, timeout=10)
        elif conn_type in {"wifi", "802-11-wireless", "wireless"} and not connection_is_wifi_ap(name):
            expected = {
                "connection.autoconnect": "yes",
                "connection.autoconnect-retries": "0",
                "connection.autoconnect-priority": SAVED_WIFI_AUTOCONNECT_PRIORITY,
                "ipv4.route-metric": WIFI_METRIC,
                "ipv6.route-metric": WIFI_METRIC,
            }
            if connection_needs_fields(name, expected):
                log(f"Refreshing saved Wi-Fi connection profile '{name}' for auto-reconnect.")
                nmcli(
                    "connection", "modify", name,
                    "connection.autoconnect", "yes",
                    "connection.autoconnect-retries", "0",
                    "connection.autoconnect-priority", SAVED_WIFI_AUTOCONNECT_PRIORITY,
                    "ipv4.route-metric", WIFI_METRIC,
                    "ipv6.route-metric", WIFI_METRIC,
                    timeout=10,
                )


def iface_has_lan_connectivity(iface: str) -> bool:
    if not has_ipv4(iface):
        return False
    gateway = default_gateway_for_iface(iface)
    if gateway and ping_ok(gateway, iface, timeout_seconds=1):
        return True
    # Static IPs or isolated LANs may not answer ping. An IPv4 address is still
    # useful for the local web UI and Home Assistant on the same network, so do
    # not churn the interface just because the internet target is unreachable.
    return True


def reconnect_ethernet(iface: str) -> None:
    log(f"Ethernet {iface} is linked but not online; reconnecting Ethernet.")
    run(["ip", "link", "set", iface, "up"], timeout=10)
    if not nmcli("device", "reapply", iface, timeout=20):
        nmcli("device", "connect", iface, timeout=30)


def rescan_wifi(iface: str, *, force: bool = False) -> None:
    now = time.time()
    if not force and now - float(_LAST_WIFI_RESCAN_AT.get(iface, 0)) < WIFI_RESCAN_SECONDS:
        return
    _LAST_WIFI_RESCAN_AT[iface] = now
    nmcli("device", "wifi", "rescan", "ifname", iface, timeout=20)


def visible_wifi_ssids(iface: str) -> set[str]:
    rescan_wifi(iface)
    output = nmcli_output("--terse", "--escape", "no", "-f", "SSID", "device", "wifi", "list", "ifname", iface, timeout=20)
    return {line.strip() for line in output.splitlines() if line.strip()}


def connect_visible_saved_wifi(iface: str) -> bool:
    saved = saved_wifi_connections()
    if not saved:
        return False
    visible = visible_wifi_ssids(iface)
    if not visible:
        return False
    for connection in saved:
        name = connection["name"]
        ssid = connection["ssid"]
        if ssid not in visible and name not in visible:
            continue
        log(f"Saved Wi-Fi '{ssid}' is visible; bringing up connection '{name}' on {iface}.")
        if nmcli("connection", "up", "id", name, "ifname", iface, timeout=60):
            return True
    return False


def reconnect_wifi(iface: str) -> None:
    log(f"Wi-Fi {iface} is not online; reconnecting Wi-Fi.")
    if command_exists("rfkill"):
        run(["rfkill", "unblock", "wifi"], timeout=10)
    run(["ip", "link", "set", iface, "up"], timeout=10)
    if command_exists("nmcli"):
        nmcli("radio", "wifi", "on", timeout=10)
        nmcli("device", "set", iface, "managed", "yes", timeout=10)
        if connect_visible_saved_wifi(iface):
            return
        if not nmcli("device", "reapply", iface, timeout=20):
            nmcli("device", "connect", iface, timeout=45)
        if connect_visible_saved_wifi(iface):
            return
    if command_exists("wpa_cli"):
        run(["wpa_cli", "-i", iface, "reconfigure"], timeout=15)
        run(["wpa_cli", "-i", iface, "reconnect"], timeout=15)
        run(["wpa_cli", "-i", iface, "reassociate"], timeout=15)


def hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown"


def check_wifi_iface(iface: str) -> bool:
    active_name = active_connection_name(iface)
    active_is_ap = bool(active_name and connection_is_wifi_ap(active_name))

    if active_name and not active_is_ap and iface_has_lan_connectivity(iface):
        return True

    # If the unit is in setup/hotspot mode, do not tear it down unless one of
    # the user's saved infrastructure Wi-Fi networks has come back into range.
    if active_is_ap and not connect_visible_saved_wifi(iface):
        log(f"Wi-Fi {iface} is running setup/hotspot connection '{active_name}'. Waiting for a saved network to appear.")
        return True

    reconnect_wifi(iface)
    time.sleep(3)
    active_name = active_connection_name(iface)
    return bool(active_name and not connection_is_wifi_ap(active_name) and iface_has_lan_connectivity(iface))


def check_once() -> None:
    configure_network_metrics()

    eth_ifaces = ethernet_interfaces()
    wifi_ifaces = wifi_interfaces()
    linked_eth = [iface for iface in eth_ifaces if carrier_up(iface)]
    route_iface = default_route_iface()

    for iface in linked_eth:
        if iface_has_lan_connectivity(iface):
            if route_iface and route_iface not in linked_eth:
                log(f"Ethernet {iface} is online; default route is currently {route_iface}. Route metrics were refreshed to prefer Ethernet.")
            return
        reconnect_ethernet(iface)
        if iface_has_lan_connectivity(iface):
            return

    if linked_eth:
        log("Ethernet link exists but no Ethernet LAN connectivity was confirmed; allowing Wi-Fi fallback.")
    else:
        log("No Ethernet link detected; Wi-Fi fallback is allowed.")

    if not wifi_ifaces:
        log("No Wi-Fi interface found.")
        return

    for iface in wifi_ifaces:
        if check_wifi_iface(iface):
            if not ping_ok(CONNECTIVITY_TARGET, iface, timeout_seconds=1):
                log(f"Wi-Fi {iface} has local connectivity; internet target {CONNECTIVITY_TARGET} is not reachable right now.")
            return

    log(f"Network still offline after reconnect attempt; will retry on next {max(15, CHECK_INTERVAL_SECONDS)}-second cycle.")


def main() -> int:
    log(f"Network watchdog started on {hostname()}; interval={CHECK_INTERVAL_SECONDS}s target={CONNECTIVITY_TARGET}.", force=True)
    configure_network_metrics(force=True)
    while True:
        try:
            check_once()
        except Exception as exc:  # noqa: BLE001 - keep watchdog alive
            log(f"Network watchdog error: {exc}")
        time.sleep(max(15, CHECK_INTERVAL_SECONDS))


if __name__ == "__main__":
    raise SystemExit(main())
