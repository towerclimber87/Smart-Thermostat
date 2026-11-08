#!/usr/bin/env python3
"""Keep verified boot-file recovery copies; repair obviously invalid cmdline.

This guard runs only after Linux starts. It deliberately does not attempt to
repair every checksum mismatch, since legitimate boot updates change files.
"""
import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time

LOG = logging.getLogger('thermostat-boot-guard')
STATE = Path(os.environ.get('THERMOSTAT_BOOT_GUARD_STATE', '/var/lib/smart-thermostat-boot-guard'))
INTERVAL = int(os.environ.get('THERMOSTAT_BOOT_GUARD_INTERVAL', '60'))


def boot_path():
    for root in (Path('/boot/firmware'), Path('/boot')):
        if (root / 'cmdline.txt').is_file() and (root / 'config.txt').is_file():
            return root
    return None


def valid_cmdline(content):
    if not content or len(content) > 4096 or b'\x00' in content or b'\r' in content:
        return False
    try:
        line = content.decode('ascii', errors='strict').strip()
    except UnicodeDecodeError:
        return False
    if '\n' in line or not re.fullmatch(r'[\x20-\x7e]+', line):
        return False
    roots = re.findall(r'(?:^| )root=(\S+)', line)
    return len(roots) == 1 and roots[0].startswith(('PARTUUID=', 'UUID=', '/dev/')) and 'rootwait' in line.split()


def valid_config(content):
    return content and b'\x00' not in content and len(content) < 65536


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_atomic(path, data, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.thermostat-', dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def checkpoint(boot):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, validate in [('cmdline.txt', valid_cmdline), ('config.txt', valid_config)]:
        source = boot / name
        try:
            data = source.read_bytes()
        except OSError as exc:
            LOG.error('Cannot read %s: %s', source, exc)
            continue
        trusted = STATE / (name + '.known-good')
        checksum = STATE / (name + '.sha256')
        if not validate(data):
            LOG.error('%s has invalid contents', source)
            if name == 'cmdline.txt' and trusted.exists() and checksum.exists():
                previous = trusted.read_bytes()
                if valid_cmdline(previous) and digest(previous) == checksum.read_text().strip():
                    try:
                        # Repair only proven-invalid cmdline, never a valid changed one.
                        # FAT boot partition may be read-only; leave untouched on failure.
                        save_atomic(source, previous, 0o644)
                        LOG.warning('Restored corrupted cmdline.txt from verified known-good copy')
                    except OSError as exc:
                        LOG.error('Cannot restore cmdline.txt: %s', exc)
            continue
        if trusted.exists() and checksum.exists():
            previous = trusted.read_bytes()
            if digest(previous) == checksum.read_text().strip():
                if data != previous:
                    LOG.warning('%s differs from verified snapshot; not overwriting legitimate changes', name)
                continue
            LOG.error('Backup checksum mismatch for %s; will not trust or replace it', name)
            continue
        if not trusted.exists() and not checksum.exists():
            save_atomic(trusted, data)
            save_atomic(checksum, (digest(data) + '\n').encode('ascii'))
            LOG.info('Created known-good %s backup', name)
        else:
            LOG.error('Incomplete trusted backup for %s; manual inspection required', name)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    while True:
        boot = boot_path()
        if boot is None:
            LOG.error('Boot partition files not found; no recovery attempted')
        else:
            try:
                checkpoint(boot)
            except OSError as exc:
                LOG.error('Boot integrity check failed: %s', exc)
        if '--once' in sys.argv:
            return
        time.sleep(max(INTERVAL, 10))


if __name__ == '__main__':
    main()
