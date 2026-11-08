#!/usr/bin/env python3
"""Ethernet-first networking for a thermostat with NetworkManager.

Keep Wi-Fi available until wired carrier, an IPv4 address and a wired default
route are established. Re-enable Wi-Fi promptly if any condition disappears.
Never change Wi-Fi profiles or credentials.
"""
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time

INTERVAL = int(os.environ.get('THERMOSTAT_FAILOVER_INTERVAL', '5'))
STABLE_CYCLES = int(os.environ.get('THERMOSTAT_ETHERNET_STABLE_CYCLES', '3'))
LOG = logging.getLogger('thermostat-network-failover')


def command(*args):
    return subprocess.run(args, text=True, capture_output=True, timeout=8, check=False)


def ethernet_interfaces():
    result = []
    for item in Path('/sys/class/net').iterdir():
        if item.name.startswith(('eth', 'en')) and (item / 'type').read_text().strip() == '1':
            result.append(item.name)
    return result


def wired_ready():
    for interface in ethernet_interfaces():
        try:
            if (Path('/sys/class/net') / interface / 'carrier').read_text().strip() != '1':
                continue
        except OSError:
            continue
        addr = command('ip', '-4', '-o', 'addr', 'show', 'dev', interface, 'scope', 'global')
        route = command('ip', '-4', 'route', 'show', 'default', 'dev', interface)
        if addr.returncode == 0 and addr.stdout.strip() and route.returncode == 0 and route.stdout.strip():
            return True
    return False


def wifi_state():
    result = command('nmcli', '-t', '-f', 'WIFI', 'radio')
    value = result.stdout.strip().lower()
    return value if result.returncode == 0 and value in ('enabled', 'disabled') else None


def set_wifi(enable):
    state = wifi_state()
    if state is None:
        LOG.warning('NetworkManager radio status unavailable; Wi-Fi not changed')
        return False
    desired = 'enabled' if enable else 'disabled'
    if state == desired:
        return True
    result = command('nmcli', 'radio', 'wifi', 'on' if enable else 'off')
    if result.returncode != 0:
        LOG.error('Unable to change Wi-Fi radio: %s', result.stderr.strip())
        return False
    LOG.warning('Wi-Fi radio %s (%s)', 'enabled' if enable else 'disabled',
                'Ethernet fallback' if enable else 'wired connection ready')
    return True


def run():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if not shutil.which('nmcli') or not shutil.which('ip'):
        LOG.warning('NetworkManager or iproute2 unavailable; leaving Wi-Fi unchanged')
        return
    stable = 0
    while True:
        try:
            if wired_ready():
                stable = min(stable + 1, STABLE_CYCLES)
                if stable >= STABLE_CYCLES:
                    set_wifi(False)
            else:
                stable = 0
                set_wifi(True)
        except (OSError, subprocess.TimeoutExpired) as exc:
            # Fail open: if monitoring has an error, prioritize Wi-Fi availability.
            stable = 0
            LOG.warning('Wired status unknown: %s', exc)
            try:
                set_wifi(True)
            except (OSError, subprocess.TimeoutExpired):
                pass
        time.sleep(max(INTERVAL, 2))


if __name__ == '__main__':
    run()
