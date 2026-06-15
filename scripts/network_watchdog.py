#!/usr/bin/env python3
"""Network watchdog for Raspberry Pi climate controllers.

Keeps Ethernet preferred and aggressively nudges Wi-Fi back online when
Ethernet is unavailable or Wi-Fi connectivity is lost.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from pathlib import Path

CHECK_INTERVAL_SECONDS = int(os.getenv("NETWORK_WATCHDOG_INTERVAL_SECONDS", "60") or "60")
CONNECTIVITY_TARGET = os.getenv("NETWORK_WATCHDOG_TARGET", "1.1.1.1").strip() or "1.1.1.1"
ETHERNET_METRIC = os.getenv("NETWORK_WATCHDOG_ETHERNET_METRIC", "50").strip() or "50"
WIFI_METRIC = os.getenv("NETWORK_WATCHDOG_WIFI_METRIC", "600").strip() or "600"

SYS_NET = Path("/sys/class/net")


def log(message: str) -> None:
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


def ping_ok(iface: str | None = None) -> bool:
    cmd = ["ping", "-c", "1", "-W", "3"]
    if iface:
        cmd.extend(["-I", iface])
    cmd.append(CONNECTIVITY_TARGET)
    proc = run(cmd, timeout=6)
    return proc.returncode == 0


def default_route_iface() -> str:
    proc = run(["ip", "route", "show", "default"], timeout=5)
    if proc.returncode != 0:
        return ""
    match = re.search(r"\bdev\s+(\S+)", proc.stdout)
    return match.group(1) if match else ""


def nmcli(*args: str, timeout: int = 20) -> bool:
    if not command_exists("nmcli"):
        return False
    proc = run(["nmcli", *args], timeout=timeout)
    if proc.returncode != 0:
        log(f"nmcli {' '.join(args)} failed: {proc.stdout.strip()}")
    return proc.returncode == 0


def configure_network_metrics() -> None:
    """Best-effort persistent Ethernet-over-Wi-Fi route priority."""
    if not command_exists("nmcli"):
        return
    proc = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], timeout=10)
    if proc.returncode != 0:
        return
    for raw_line in proc.stdout.splitlines():
        if not raw_line.strip() or ":" not in raw_line:
            continue
        name, conn_type = raw_line.rsplit(":", 1)
        conn_type = conn_type.strip().lower()
        if conn_type in {"ethernet", "802-3-ethernet"}:
            nmcli("connection", "modify", name, "connection.autoconnect", "yes", "ipv4.route-metric", ETHERNET_METRIC, "ipv6.route-metric", ETHERNET_METRIC, timeout=10)
        elif conn_type in {"wifi", "802-11-wireless", "wireless"}:
            nmcli("connection", "modify", name, "connection.autoconnect", "yes", "ipv4.route-metric", WIFI_METRIC, "ipv6.route-metric", WIFI_METRIC, timeout=10)


def reconnect_ethernet(iface: str) -> None:
    log(f"Ethernet {iface} is linked but not online; reconnecting Ethernet.")
    run(["ip", "link", "set", iface, "up"], timeout=10)
    if not nmcli("device", "reapply", iface, timeout=20):
        nmcli("device", "connect", iface, timeout=30)


def reconnect_wifi(iface: str) -> None:
    log(f"Wi-Fi {iface} is not online; reconnecting Wi-Fi.")
    if command_exists("rfkill"):
        run(["rfkill", "unblock", "wifi"], timeout=10)
    run(["ip", "link", "set", iface, "up"], timeout=10)
    if command_exists("nmcli"):
        nmcli("radio", "wifi", "on", timeout=10)
        nmcli("device", "set", iface, "managed", "yes", timeout=10)
        if not nmcli("device", "reapply", iface, timeout=20):
            nmcli("device", "connect", iface, timeout=45)
    if command_exists("wpa_cli"):
        run(["wpa_cli", "-i", iface, "reconfigure"], timeout=15)
        run(["wpa_cli", "-i", iface, "reconnect"], timeout=15)
        run(["wpa_cli", "-i", iface, "reassociate"], timeout=15)


def hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown"


def check_once() -> None:
    configure_network_metrics()

    eth_ifaces = ethernet_interfaces()
    wifi_ifaces = wifi_interfaces()
    linked_eth = [iface for iface in eth_ifaces if carrier_up(iface)]
    route_iface = default_route_iface()

    for iface in linked_eth:
        if has_ipv4(iface) and ping_ok(iface):
            if route_iface and route_iface not in linked_eth:
                log(f"Ethernet {iface} is online; default route is currently {route_iface}. Route metrics were refreshed to prefer Ethernet.")
            return
        reconnect_ethernet(iface)
        if has_ipv4(iface) and ping_ok(iface):
            return

    if linked_eth:
        log("Ethernet link exists but no Ethernet connectivity was confirmed; allowing Wi-Fi fallback.")
    else:
        log("No Ethernet link detected; Wi-Fi fallback is allowed.")

    if not wifi_ifaces:
        log("No Wi-Fi interface found.")
        return

    for iface in wifi_ifaces:
        if has_ipv4(iface) and ping_ok(iface):
            return

    for iface in wifi_ifaces:
        reconnect_wifi(iface)
        time.sleep(3)
        if has_ipv4(iface) and ping_ok(iface):
            log(f"Wi-Fi {iface} is back online.")
            return

    log("Network still offline after reconnect attempt; will retry on next 60-second cycle.")


def main() -> int:
    log(f"Network watchdog started on {hostname()}; interval={CHECK_INTERVAL_SECONDS}s target={CONNECTIVITY_TARGET}.")
    while True:
        try:
            check_once()
        except Exception as exc:  # noqa: BLE001 - keep watchdog alive
            log(f"Network watchdog error: {exc}")
        time.sleep(max(15, CHECK_INTERVAL_SECONDS))


if __name__ == "__main__":
    raise SystemExit(main())
