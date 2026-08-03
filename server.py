#!/usr/bin/env python3
"""Smart Thermostat local development / Raspberry Pi server.

Serves the web UI from ./public and provides a same-origin Home Assistant proxy
so the browser does not get blocked by CORS when loading entities.
"""

from __future__ import annotations

import argparse
import atexit
import html
import json
import mimetypes
import os
import re
import signal
import socket
import shutil
import shlex
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request, error
from urllib.parse import parse_qs, urlparse, urlunparse

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA_DIR = ROOT / "data"
VERSION_FILE = ROOT / "VERSION"
UPDATE_RUNTIME_DIR = Path(os.environ.get("SMART_THERMOSTAT_UPDATE_RUNTIME_DIR", "/tmp/smart-thermostat-self-update")).expanduser()
UPDATE_STATUS_FILE = UPDATE_RUNTIME_DIR / "update-status.json"
UPDATE_CHECK_FILE = UPDATE_RUNTIME_DIR / "update-check.json"
UPDATE_BRANCH = os.environ.get("SMART_THERMOSTAT_UPDATE_BRANCH", "Development").strip() or "Development"
UPDATE_CHECK_TTL_SECONDS = max(60.0, float(os.environ.get("SMART_THERMOSTAT_UPDATE_CHECK_TTL_SECONDS", "900") or "900"))
_UPDATE_LAUNCH_LOCK = threading.Lock()
_UPDATE_CHECK_LOCK = threading.Lock()
THERMOSTAT_STATE_FILE = DATA_DIR / "thermostat-state.json"
THERMOSTAT_SCHEDULES_FILE = DATA_DIR / "thermostat-schedules.json"
THERMOSTAT_SCHEDULES_BACKUP_FILE = DATA_DIR / "thermostat-schedules.backup.json"
PANEL_CONFIG_FILE = DATA_DIR / "panel-config.json"
HVAC_HISTORY_FILE = DATA_DIR / "hvac-history.json"
ALARM_ACTION_AUDIT_FILE = DATA_DIR / "alarm-actions.log"
APP_STARTED_AT = time.time()
HA_STATES_CACHE_TTL_SECONDS = float(os.environ.get("SMART_THERMOSTAT_HA_STATES_CACHE_TTL_SECONDS", "8.0"))
HA_ENTITY_STATE_CACHE_TTL_SECONDS = float(os.environ.get("SMART_THERMOSTAT_HA_ENTITY_STATE_CACHE_TTL_SECONDS", "8.0"))
HA_REQUEST_TIMEOUT_SECONDS = max(0.5, float(os.environ.get("SMART_THERMOSTAT_HA_REQUEST_TIMEOUT_SECONDS", "3.0") or "3.0"))
ASSISTANT_HA_TIMEOUT_SECONDS = max(5.0, float(os.environ.get("SMART_THERMOSTAT_ASSISTANT_HA_TIMEOUT_SECONDS", "45") or "45"))
HA_REQUEST_CONCURRENCY = max(1, int(float(os.environ.get("SMART_THERMOSTAT_HA_REQUEST_CONCURRENCY", "4") or "4")))
EXTERNAL_HA_AIR_VERIFY_INTERVAL_SECONDS = max(5.0, float(os.environ.get("SMART_THERMOSTAT_EXTERNAL_AIR_VERIFY_SECONDS", "30") or "30"))
ACCESS_LOGS_ENABLED = os.environ.get("SMART_THERMOSTAT_ACCESS_LOGS", "0").strip().lower() in {"1", "true", "yes", "on"}
HISTORY_PERSISTENCE_MODE = os.environ.get("SMART_THERMOSTAT_HISTORY_PERSISTENCE", "daily").strip().lower() or "daily"
HISTORY_SAVE_ON_SHUTDOWN = os.environ.get("SMART_THERMOSTAT_HISTORY_SAVE_ON_SHUTDOWN", "0").strip().lower() in {"1", "true", "yes", "on"}
USB_CONFIG_FILENAME = os.environ.get("SMART_THERMOSTAT_USB_CONFIG_FILENAME", "smart-thermostat-config-backup.json").strip() or "smart-thermostat-config-backup.json"
# Never mount USB drives inside the Git checkout. A previous version used
# data/usb-mounts, which makes git clean fail with "Device or resource busy"
# when a thumb drive is still mounted there. Use a runtime folder outside the
# repo instead.
RUNTIME_DIR = Path(os.environ.get("SMART_THERMOSTAT_RUNTIME_DIR", "/tmp/smart-thermostat-runtime")).expanduser()
USB_RUNTIME_MOUNT_ROOT = Path(os.environ.get("SMART_THERMOSTAT_USB_RUNTIME_MOUNT_ROOT", "/tmp/smart-thermostat-usb")).expanduser()
USB_LEGACY_MOUNT_ROOT = DATA_DIR / "usb-mounts"
USB_MOUNT_ROOTS = tuple(
    x.strip()
    for x in os.environ.get(
        "SMART_THERMOSTAT_USB_MOUNT_ROOTS",
        f"/media:/run/media:/mnt:{USB_RUNTIME_MOUNT_ROOT}",
    ).split(":")
    if x.strip()
)
MANUAL_CHANGEOVER_LOCKOUT_MINUTES = 10
PANEL_COMMAND_GRACE_MS = int(float(os.environ.get("SMART_THERMOSTAT_PANEL_COMMAND_GRACE_SECONDS", "18") or "18") * 1000)
# Target/setpoint echoes from Home Assistant can arrive much later than mode
# echoes after a reconnect/unavailable cycle. Keep a longer panel-owned window
# for setpoint changes so the wall thermostat does not snap back to an old HA
# value 30-60 seconds after the user tapped the touchscreen.
PANEL_TARGET_COMMAND_GRACE_MS = int(float(os.environ.get("SMART_THERMOSTAT_PANEL_TARGET_GRACE_SECONDS", "300") or "300") * 1000)
ARRIVING_AWAY_BYPASS_MS = int(float(os.environ.get("SMART_THERMOSTAT_ARRIVING_BYPASS_MINUTES", "120") or "120") * 60000)
SYNC_ARM_DURATION_SECONDS = max(5.0, float(os.environ.get("SMART_THERMOSTAT_SYNC_ARM_SECONDS", "30") or "30"))
CONFIG_WEB_PORTAL_TIMEOUT_SECONDS = max(60.0, float(os.environ.get("SMART_THERMOSTAT_CONFIG_PORTAL_TIMEOUT_SECONDS", "900") or "900"))
MANUAL_HARDWARE_TIMEOUT_SECONDS = max(0.0, float(os.environ.get("SMART_THERMOSTAT_MANUAL_HARDWARE_TIMEOUT_SECONDS", "300") or "300"))
_CONFIG_WEB_PORTAL_LOCK = threading.RLock()
_CONFIG_WEB_PORTAL_ENABLED_UNTIL = 0.0
_CONFIG_WEB_PORTAL_STARTED_AT = 0.0
_CONFIG_WEB_PORTAL_LAST_ACTIVITY_AT = 0.0
_ASSISTANT_LOCK = threading.RLock()
_ASSISTANT_PROCESS_LOCK = threading.Lock()
_ASSISTANT_REQUEST_SEQ = 0
_ASSISTANT_STATE = {
    "active": False,
    "stage": "idle",
    "requestId": 0,
    "command": "",
    "response": "",
    "spokenResponse": "",
    "statusText": "Standing by.",
    "error": "",
    "startedAt": 0,
    "updatedAt": 0,
    "clearAtMonotonic": 0.0,
    "conversationId": "",
    "mediaPlayerId": "",
    "ttsEntityId": "",
    "speechPlayed": False,
}
_HA_STATES_CACHE_LOCK = threading.Lock()
_HA_STATES_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_HA_ENTITY_STATE_CACHE_LOCK = threading.Lock()
_HA_ENTITY_STATE_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
_HA_REQUEST_SEMAPHORE = threading.BoundedSemaphore(HA_REQUEST_CONCURRENCY)
_PANEL_CONFIG_LOCK = threading.RLock()
_ALARM_ACTION_AUDIT_LOCK = threading.RLock()
_THERMOSTAT_RECORD_LOCK = threading.RLock()
_THERMOSTAT_RECORD_CACHE: dict | None = None
_THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE = ""
_THERMOSTAT_RECORD_DIRTY = False
_HVAC_HISTORY_LOCK = threading.RLock()
_HVAC_HISTORY_ARCHIVE: dict | None = None
_HVAC_HISTORY_CURRENT: dict | None = None
_SYNC_ARM_LOCK = threading.RLock()
_SYNC_ARMED_UNTIL = 0.0
_SYNC_ARM_SOURCE = ""
_SYNC_LAST_RESULT: dict = {}
_SYNC_LAST_REQUEST_SIGNATURE = ""
_SYNC_LAST_REQUEST_AT = 0.0


# Onboard HVAC relay outputs use BCM GPIO numbering. These pins are used when
# airControlMode is Internal; External mode sends heat/cool calls to the
# configured Home Assistant entities instead.
HARDWARE_RELAY_PINS = {
    "fan": {"gpio": 24, "physical": 18, "label": "Fan Relay", "resistor": "R98 470Ω"},
    "cool": {"gpio": 23, "physical": 16, "label": "Cool Relay", "resistor": "R97 470Ω"},
    "heat": {"gpio": 22, "physical": 15, "label": "Heat Relay", "resistor": "R95 470Ω"},
}
HARDWARE_I2C_PINS = {
    "sda": {"gpio": 2, "physical": 3, "label": "SDA"},
    "scl": {"gpio": 3, "physical": 5, "label": "SCL"},
}
# S18-L262B-2 motion sensor wiring from the thermostat schematic. REL is the
# active-high motion output. SENS and ONTIME each pass through a 15k series /
# 10k pull-down divider, so a GPIO HIGH presents about 0.4 x VDD to the sensor.
# That exposes two stable board-level settings without pretending the GPIO is a
# true DAC: 0V and approximately 0.4 x VDD.
HARDWARE_MOTION_PINS = {
    "rel": {"gpio": 27, "physical": 13, "label": "Motion REL"},
    "sens": {"gpio": 25, "physical": 22, "label": "Motion SENS", "seriesOhms": 15000, "pulldownOhms": 10000},
    "ontime": {"gpio": 16, "physical": 36, "label": "Motion ONTIME", "seriesOhms": 15000, "pulldownOhms": 10000},
}
HARDWARE_MOTION_POLL_OPTIONS = (1, 3, 6)
HARDWARE_MOTION_DEFAULT_CONFIG = {"pollSeconds": 3, "sensitivityLevel": 0, "onTimeSeconds": 2}
HARDWARE_RGB_PIN = {"gpio": 26, "physical": 37, "label": "RGB Data / LED"}
HARDWARE_POWER_PINS = {
    "3v3": {"physical": 17, "label": "3.3V"},
    "5v": {"physical": 4, "label": "5V"},
    "gnd": {"physical": 6, "label": "GND"},
}
HARDWARE_RELAY_ACTIVE_LOW = os.environ.get("SMART_THERMOSTAT_RELAY_ACTIVE_LOW", "0").strip().lower() in {"1", "true", "yes", "on"}
HARDWARE_I2C_BUS = int(os.environ.get("SMART_THERMOSTAT_I2C_BUS", "1"))
HARDWARE_I2C_DEVICE = Path(f"/dev/i2c-{HARDWARE_I2C_BUS}")
CONTROL_LOOP_ENABLED = os.environ.get("SMART_THERMOSTAT_CONTROL_LOOP", "1").strip().lower() not in {"0", "false", "no", "off"}
CONTROL_LOOP_INTERVAL_SECONDS = max(1.0, float(os.environ.get("SMART_THERMOSTAT_CONTROL_LOOP_SECONDS", "2") or "2"))
LOCAL_TEMP_SENSOR_ENABLED = os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_SENSOR", "auto").strip().lower() not in {"0", "false", "no", "off", "disabled"}
# The two onboard HDC2080 sensors are the primary room-temperature source.
# Home Assistant remains available only when neither onboard sensor can provide
# a trustworthy reading. Set SMART_THERMOSTAT_LOCAL_TEMP_MODE=fallback to
# restore the older Home-Assistant-first behavior.
LOCAL_TEMP_SENSOR_MODE = os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_MODE", "primary").strip().lower() or "primary"
LOCAL_TEMP_SENSOR_POLL_SECONDS = max(2.0, float(os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_POLL_SECONDS", "5") or "5"))
LOCAL_TEMP_SENSOR_STALE_SECONDS = max(15.0, float(os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_FALLBACK_AFTER_SECONDS", "60") or "60"))
LOCAL_TEMP_SENSOR_ADDRESS = os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_I2C_ADDRESS", "").strip()
LOCAL_TEMP_SENSOR_ADDRESSES = os.environ.get(
    "SMART_THERMOSTAT_LOCAL_TEMP_I2C_ADDRESSES",
    LOCAL_TEMP_SENSOR_ADDRESS or "0x40,0x41",
).strip()
LOCAL_TEMP_SENSOR_TYPE = os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_TYPE", "auto").strip().lower() or "auto"
LOCAL_TEMP_SENSOR_MAX_PAIR_DELTA_F = max(0.5, float(os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_MAX_PAIR_DELTA_F", "5") or "5"))
LOCAL_TEMP_SENSOR_MAX_STEP_F = max(1.0, float(os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_MAX_STEP_F", "6") or "6"))
LOCAL_TEMP_SENSOR_HOLD_LAST_SECONDS = max(15.0, float(os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_HOLD_LAST_SECONDS", "120") or "120"))
LOCAL_TEMP_SOURCE_NAMES = {"onboard", "local", "i2c", "hardware", "onboard-fallback", "hdc2080", "hdc2080-pair"}

_HARDWARE_LOCK = threading.RLock()
_HARDWARE_RELAY_BACKEND = None
_HARDWARE_RGB_BACKEND = None
_HARDWARE_LAST_RELAYS = {"fan": False, "heat": False, "cool": False}
_HARDWARE_LAST_RELAY_SOURCE = "thermostat"
_HARDWARE_MANUAL = {
    "active": False,
    "relays": {"fan": False, "heat": False, "cool": False},
    "activatedAt": 0.0,
    "expiresAt": 0.0,
}
_HARDWARE_RGB = {"on": False, "color": "#35eaff"}
_HVAC_HISTORY_LAST_RELAYS = {"fan": False, "heat": False, "cool": False}
_HVAC_HISTORY_LAST_SOURCE = "thermostat"
_EXTERNAL_HA_AIR_LOCK = threading.RLock()
_EXTERNAL_HA_AIR_LAST_STATES = {"heat": None, "cool": None, "fan": None}
_EXTERNAL_HA_AIR_LAST_ENTITIES = {"heat": "", "cool": "", "fan": ""}
_EXTERNAL_HA_AIR_RETRY_AFTER = {"heat": 0.0, "cool": 0.0, "fan": 0.0}
_EXTERNAL_HA_AIR_NEXT_VERIFY_AT = {"heat": 0.0, "cool": 0.0, "fan": 0.0}
_EXTERNAL_HA_AIR_LAST_ERROR_AT = {"heat": 0.0, "cool": 0.0, "fan": 0.0}
_THERMOSTAT_ASYNC_OUTPUT_LOCK = threading.Lock()
_THERMOSTAT_ASYNC_OUTPUT_SEQ = 0
_HVAC_OUTPUT_LAST_LOG_SIGNATURE = None
_HARDWARE_LAST_I2C_SCAN = {"at": 0.0, "payload": None}
_HARDWARE_MOTION_LOCK = threading.RLock()
_HARDWARE_MOTION_BACKEND = None
_HARDWARE_MOTION_RUNTIME = {
    "motion": False,
    "lastChangedAt": 0.0,
    "lastReadAt": 0.0,
}
_LOCAL_TEMP_SENSOR_LOCK = threading.RLock()
_LOCAL_TEMP_SENSOR_CACHE = {"at": 0.0, "payload": None}
_LOCAL_TEMP_SENSOR_HEALTH = {
    "lastAcceptedF": None,
    "lastAcceptedAt": 0.0,
    "selectedAddress": "",
    "sensorLastF": {},
}
_CONTROL_LOOP_THREAD_STARTED = False
_CONTROL_LOOP_STOP = threading.Event()


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    handler.wfile.write(body)


DEFAULT_THERMOSTAT = {
    "name": "IHA Thermostat",
    "currentTemp": 70,
    "currentTempUpdatedAt": 0,
    "currentTempSource": "virtual",
    "currentTempSourceName": "Virtual Temp",
    "virtualTempOverrideUntil": 0,
    "targetTemp": 70,
    "lastComfortTarget": 70,
    # Exact local setpoint captured when this unit enters Away. Unlike
    # lastComfortTarget, this is intentionally insulated from schedules and
    # other activity while Away so Arriving can restore the real pre-Away value.
    "preAwayTargetTemp": None,
    "temperatureDifferential": 0,
    "lastPanelTargetTemp": 0,
    "lastPanelTargetRequestAt": 0,
    "lastPanelModeRequestMode": "",
    "lastPanelModeRequestAt": 0,
    "mode": "cool",
    "fan": "auto",
    "away": False,
    "awaySource": "",
    "manualAwayPresenceLatch": None,
    "presenceHomeOverride": None,
    "awayHeat": 55,
    "awayCool": 85,
    "safetyLow": 55,
    "safetyHigh": 85,
    "humidity": 45,
    "outdoorTemp": 78,
    "outdoorWindSpeed": 0,
    "outdoorWindUnit": "mph",
    "autoCoolOutdoorTarget": 70,
    "autoHeatOutdoorTarget": 65,
    "autoChangeoverLockoutMinutes": 120,
    "manualChangeoverLockoutMinutes": MANUAL_CHANGEOVER_LOCKOUT_MINUTES,
    "heatMinimumRuntimeMinutes": 2,
    "coolMinimumRuntimeMinutes": 2,
    "coolFanRemainOnMinutes": 2,
    # Independent source routing. airControlMode remains as a legacy
    # compatibility value for older panel builds.
    "airControlMode": "internal",
    "roomTempControlMode": "internal",
    "heatControlMode": "internal",
    "coolControlMode": "internal",
    "fanControlMode": "internal",
    "externalHeatEntity": None,
    "externalCoolEntity": None,
    "externalFanEntity": None,
    "heatLocked": False,
    "coolLocked": False,
    "people": [],
    "autoAwayPeople": [],
    "schedules": [],
    "autoActiveMode": "cool",
    "autoPendingMode": "",
    "autoLockoutUntil": 0,
    "manualPendingMode": "",
    "manualLockoutUntil": 0,
    "lastHeatRunAt": 0,
    "lastCoolRunAt": 0,
    "equipmentLastHeatRunAt": 0,
    "equipmentLastCoolRunAt": 0,
    "heatRelayWasOn": False,
    "coolRelayWasOn": False,
    "lastManualChangeoverBypassMode": "",
    "lastManualChangeoverBypassAt": 0,
    "heatCycleStartedAt": 0,
    "coolCycleStartedAt": 0,
    "heatCycleStoppedAt": 0,
    "coolCycleStoppedAt": 0,
    "coolFanHoldUntil": 0,
    "pauseFunction": {
        "durationMinutes": 5,
        "entries": [],
        "active": False,
        "pausedAt": 0,
        "previousTargetTemp": None,
        "previousLastComfortTarget": None,
        "activeEntityIds": [],
        "snoozeUntil": 0,
        "countdownAllowed": False,
        "countdownReason": "",
    },
    "autoSwitchNotice": {"active": False, "source": "", "fromMode": "", "toMode": "", "switchTemp": 0, "outdoorTemp": 0, "coolTarget": 0, "heatTarget": 0, "createdAt": 0},
    "autoSwitchNoticeDismissed": {"active": False, "source": "", "fromMode": "", "toMode": "", "coolTarget": 0, "heatTarget": 0, "dismissedAt": 0},
    "autoSwitchHold": {"active": False, "source": "", "mode": "", "suggestedMode": "", "reason": "", "dismissed": False, "createdAt": 0},
    "limits": {
        "cool": {"min": 65, "max": 80},
        "heat": {"min": 60, "max": 78},
        "auto": {"min": 60, "max": 80},
    },
}


DEFAULT_HOME_ASSISTANT_CONFIG = {
    "url": "",
    "token": "",
    "coverEntities": [],
    "mediaPlayerEntities": [],
    "selectedMediaPlayerId": "",
    "audioControlEntities": {
        "gain": None,
        "bass": None,
        "treble": None,
        "music_surround": None,
        "subwoofer": None,
        "surround": None,
        "projector": None,
    },
    "audioAvailableEntities": {"mediaPlayers": [], "numbers": [], "switches": []},
    "weatherEntity": {"entityId": "weather.home", "name": "Home"},
    "weatherAvailableEntities": [],
    "currentTempEntity": None,
    "currentTempAvailableEntities": [],
    "externalHeatControlEntity": None,
    "externalCoolControlEntity": None,
    "externalFanControlEntity": None,
    "externalAirControlAvailableEntities": [],
    "alarmEntity": None,
    "alarmAvailableEntities": [],
    "doorEntity": None,
    "doorAvailableEntities": [],
    "lightAvailableEntities": [],
    "roomAvailableEntities": [],
    "personAvailableEntities": [],
    "pauseFunctionAvailableEntities": [],
    "syncThermostatEntities": [],
    "syncAvailableThermostatEntities": [],
    "voiceAssistant": {
        "enabled": True,
        "agentId": "",
        "ttsEntityId": "",
        "mediaPlayerId": "",
        "language": "en",
        "speak": True,
        "funMode": True,
        "playfulReplies": True,
        "continueConversation": True,
        "showResponseText": True,
        "responseHoldSeconds": 2.0,
        "announcementVolumePercent": 45,
    },
}


def _merge_missing_defaults(defaults: object, saved: object) -> object:
    """Return saved config with newly introduced default keys filled in.

    Saved values always win. This is used as a small migration layer so a
    program update can introduce a new setting without wiping the values that
    are already on the Raspberry Pi. Lists and non-dict values are treated as
    complete values because entity lists and room maps are user controlled.
    """
    if isinstance(defaults, dict) and isinstance(saved, dict):
        merged = _deepcopy_json(defaults)
        for key, value in saved.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = _merge_missing_defaults(merged[key], value)
            else:
                merged[key] = _deepcopy_json(value)
        return merged
    if saved is not None:
        return _deepcopy_json(saved)
    return _deepcopy_json(defaults)


def _migrate_panel_config(config: object) -> dict | None:
    """Normalize the saved panel configuration without replacing user choices."""
    if not isinstance(config, dict):
        return None

    migrated = _deepcopy_json(config)

    if isinstance(migrated.get("thermostat"), dict):
        thermostat = _merge_thermostat_state(migrated["thermostat"])
        # The panel config is long-term settings, not live thermostat state.
        # Runtime readings, relay states, lockout timers, weather values and
        # auto-switch notices stay in RAM so normal operation and Wi-Fi outages
        # do not rewrite panel-config.json over and over.
        thermostat = _thermostat_persist_payload(thermostat)
        migrated["thermostat"] = thermostat

    if isinstance(migrated.get("alarm"), dict):
        migrated["alarm"] = {
            **migrated["alarm"],
            "disarmCode": str(migrated["alarm"].get("disarmCode") or "")[:8],
        }

    integrations = migrated.get("integrations")
    if isinstance(integrations, dict) and isinstance(integrations.get("homeAssistant"), dict):
        integrations["homeAssistant"] = _merge_missing_defaults(
            DEFAULT_HOME_ASSISTANT_CONFIG,
            integrations["homeAssistant"],
        )
        migrated["integrations"] = integrations

    return migrated


def _snapshot_settings_files() -> dict[str, str | None]:
    """Capture settings files before a git reset/update can replace them."""
    snapshots: dict[str, str | None] = {}
    for key, path in (("thermostat", THERMOSTAT_STATE_FILE), ("schedules", THERMOSTAT_SCHEDULES_FILE), ("schedulesBackup", THERMOSTAT_SCHEDULES_BACKUP_FILE), ("panel", PANEL_CONFIG_FILE)):
        try:
            snapshots[key] = path.read_text(encoding="utf-8") if path.exists() else None
        except OSError:
            snapshots[key] = None
    return snapshots


def _restore_settings_files(snapshots: dict[str, str | None]) -> None:
    """Restore settings captured before the updater reset the code folder."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for key, path in (("thermostat", THERMOSTAT_STATE_FILE), ("schedules", THERMOSTAT_SCHEDULES_FILE), ("schedulesBackup", THERMOSTAT_SCHEDULES_BACKUP_FILE), ("panel", PANEL_CONFIG_FILE)):
        content = snapshots.get(key)
        if content is None:
            continue
        temp_path = path.with_suffix(path.suffix + ".restore.tmp")
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)


def _number(value, fallback: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        next_value = float(value)
    except (TypeError, ValueError):
        next_value = float(fallback)
    if minimum is not None:
        next_value = max(float(minimum), next_value)
    if maximum is not None:
        next_value = min(float(maximum), next_value)
    return next_value


def _intish(value, fallback: float, minimum: float | None = None, maximum: float | None = None) -> int:
    return int(round(_number(value, fallback, minimum, maximum)))


def _normalize_mode(value: object, fallback: str = "cool") -> str:
    mode = str(value or fallback).strip().lower()
    if mode.startswith("hvacmode."):
        mode = mode.split(".", 1)[1]
    # Auto / heat_cool is intentionally no longer exposed as an HVAC mode.
    # The controller still performs safety and comfort-switch suggestions while
    # in Heat or Cool, so an incoming legacy Auto value is treated as the
    # provided fallback instead of preserving a separate Auto state.
    if mode in {"heat_cool", "auto"}:
        return "" if fallback == "" else (fallback if fallback in {"off", "heat", "cool"} else "cool")
    if mode in {"off", "heat", "cool"}:
        return mode
    if fallback == "":
        return ""
    return fallback if fallback in {"off", "heat", "cool"} else "cool"


def _normalize_fan(value: object, fallback: str = "auto") -> str:
    fan = str(value or fallback).strip().lower()
    return fan if fan in {"off", "on", "auto"} else fallback




def _boolish(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "enabled"}


def _normalize_air_control_mode(value: object, fallback: str = "internal") -> str:
    mode = str(value or fallback).strip().lower().replace("_", "-")
    if mode in {"external", "home-assistant", "ha", "remote"}:
        return "external"
    if mode in {"internal", "local", "onboard", "gpio", "hardware"}:
        return "internal"
    return fallback if fallback in {"internal", "external"} else "internal"


def _equipment_control_mode(thermostat: dict, kind: str) -> str:
    """Return the independent Internal/External mode for one HVAC output."""
    kind = str(kind or "").strip().lower()
    key = {
        "heat": "heatControlMode",
        "cool": "coolControlMode",
        "fan": "fanControlMode",
    }.get(kind)
    if not key:
        return "internal"
    explicit = thermostat.get(key)
    if explicit is not None:
        return _normalize_air_control_mode(explicit, "internal")
    # Older state only had one switch for heat/cool and always kept fan local.
    if kind in {"heat", "cool"}:
        return _normalize_air_control_mode(thermostat.get("airControlMode"), "internal")
    return "internal"


def _known_equipment_output_on(thermostat: dict, kind: str) -> bool:
    """Return the best known real/commanded state for one HVAC output.

    Runtime relay markers are normally authoritative, but they are written at
    the end of the output-application pass.  A Home Assistant call or another
    output worker can therefore leave a brief window where the physical/local
    relay (or the last confirmed external command) is on while relayWasOn is
    still false.  Fan overrun and minimum-cycle protection must not drop out in
    that window.
    """
    kind = str(kind or "").strip().lower()
    if kind not in {"heat", "cool", "fan"}:
        return False
    relay_key = {
        "heat": "heatRelayWasOn",
        "cool": "coolRelayWasOn",
    }.get(kind)
    if relay_key and bool(thermostat.get(relay_key)):
        return True

    if _equipment_control_mode(thermostat, kind) == "internal":
        return bool(_HARDWARE_LAST_RELAYS.get(kind))

    entry = _external_air_entity_for_kind(thermostat, kind)
    entity_id = str((entry or {}).get("entityId") or "").strip()
    last_entity = str(_EXTERNAL_HA_AIR_LAST_ENTITIES.get(kind) or "").strip()
    return bool(entity_id and entity_id == last_entity and _EXTERNAL_HA_AIR_LAST_STATES.get(kind) is True)


def _room_temp_control_mode(thermostat: dict) -> str:
    return _normalize_air_control_mode(thermostat.get("roomTempControlMode"), "internal")


def _normalize_external_air_entity(value: object) -> dict | None:
    if isinstance(value, str):
        value = {"entityId": value}
    if not isinstance(value, dict):
        return None
    entity_id = str(value.get("entityId") or value.get("entity_id") or "").strip()
    if not entity_id or "." not in entity_id:
        return None
    domain = str(value.get("domain") or entity_id.split(".", 1)[0]).strip().lower()
    if domain not in {"switch", "input_boolean"}:
        return None
    name = str(value.get("name") or value.get("friendlyName") or value.get("friendly_name") or value.get("haName") or entity_id).strip() or entity_id
    return {
        "entityId": entity_id[:160],
        "name": name[:120],
        "domain": domain[:40],
        "state": str(value.get("state") or "unknown").strip()[:80] or "unknown",
        "lastChanged": str(value.get("lastChanged") or value.get("last_changed") or "").strip()[:80],
        "lastUpdated": str(value.get("lastUpdated") or value.get("last_updated") or "").strip()[:80],
    }


def _normalize_person_entries(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    people: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        domain = entity_id.split(".", 1)[0] if "." in entity_id else "person"
        people.append({
            "entityId": entity_id,
            "domain": domain,
            "name": str(item.get("name") or item.get("friendly_name") or entity_id).strip()[:120] or entity_id,
            "state": str(item.get("state") or "unknown").strip() or "unknown",
            "lastChanged": item.get("lastChanged") or item.get("last_changed") or "",
            "lastUpdated": item.get("lastUpdated") or item.get("last_updated") or "",
        })
    return people

AUTO_AWAY_PEOPLE_KEYS = ("autoAwayPeople", "awayPeople", "autoAwayPersonEntities", "presenceAwayPeople")


def _auto_away_people_from_source(source: dict) -> object:
    for key in AUTO_AWAY_PEOPLE_KEYS:
        if key in source:
            return source.get(key)
    return None


def _source_has_auto_away_people(source: dict) -> bool:
    return any(key in source for key in AUTO_AWAY_PEOPLE_KEYS)


def _normalize_schedule_time(value: object, fallback: str = "20:00") -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        return fallback
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return fallback
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return f"{hour:02d}:{minute:02d}"


def _normalize_schedule_person_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for item in value:
        entity_id = str(item or "").strip()
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        ids.append(entity_id)
    return ids


SCHEDULE_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SCHEDULE_DAY_ALIASES = {
    "0": "mon", "1": "mon", "m": "mon", "mon": "mon", "monday": "mon",
    "2": "tue", "tu": "tue", "tue": "tue", "tues": "tue", "tuesday": "tue",
    "3": "wed", "w": "wed", "wed": "wed", "weds": "wed", "wednesday": "wed",
    "4": "thu", "th": "thu", "thur": "thu", "thurs": "thu", "thu": "thu", "thursday": "thu",
    "5": "fri", "f": "fri", "fri": "fri", "friday": "fri",
    "6": "sat", "sa": "sat", "sat": "sat", "saturday": "sat",
    "7": "sun", "su": "sun", "sun": "sun", "sunday": "sun",
}


def _normalize_schedule_days(value: object) -> list[str]:
    """Return selected schedule weekdays, Monday first.

    Older schedules did not have a day selector, so missing/invalid day data
    intentionally means every day. Numeric values accept both Python weekday
    indexes (0=Monday) and common calendar values (1=Monday ... 7=Sunday).
    """
    if value is None:
        return list(SCHEDULE_DAY_KEYS)
    if isinstance(value, str):
        raw_items: list[object] = [x.strip() for x in value.replace(";", ",").split(",")]
    elif isinstance(value, list):
        raw_items = value
    elif isinstance(value, tuple):
        raw_items = list(value)
    else:
        return list(SCHEDULE_DAY_KEYS)

    selected: set[str] = set()
    for raw in raw_items:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            try:
                n = int(raw)
            except Exception:
                continue
            if n == 0:
                key = "mon"
            elif 1 <= n <= 7:
                key = SCHEDULE_DAY_KEYS[n - 1]
            else:
                continue
        else:
            text = str(raw or "").strip().lower()
            key = SCHEDULE_DAY_ALIASES.get(text)
        if key in SCHEDULE_DAY_KEYS:
            selected.add(key)

    if not selected:
        return list(SCHEDULE_DAY_KEYS)
    return [key for key in SCHEDULE_DAY_KEYS if key in selected]


def _normalize_schedule_entries(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    schedules: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(value[:18]):
        if not isinstance(item, dict):
            continue
        schedule_id = str(item.get("id") or f"schedule-{index + 1}").strip()[:80]
        if not schedule_id or schedule_id in seen:
            continue
        seen.add(schedule_id)
        name = str(item.get("name") or f"Schedule {index + 1}").strip()[:40] or f"Schedule {index + 1}"
        schedules.append({
            "id": schedule_id,
            "name": name,
            "enabled": not (item.get("enabled") is False),
            "time": _normalize_schedule_time(item.get("time"), "20:00"),
            "days": _normalize_schedule_days(item.get("days", item.get("weekdays", item.get("daysOfWeek")))),
            "coolSetpoint": _intish(item.get("coolSetpoint", item.get("coolTarget", 68)), 68, 45, 95),
            "heatSetpoint": _intish(item.get("heatSetpoint", item.get("heatTarget", 71)), 71, 45, 95),
            "personEntityIds": _normalize_schedule_person_ids(item.get("personEntityIds", item.get("people", item.get("persons", [])))),
            "lastTriggeredDate": str(item.get("lastTriggeredDate") or item.get("lastRunDate") or "").strip()[:16],
        })
    return schedules




def _pause_function_domain_from_entity_id(entity_id: object) -> str:
    text = str(entity_id or "").strip()
    return text.split(".", 1)[0].lower() if "." in text else ""


def _normalize_pause_function_duration(value: object, fallback: int = 5) -> int:
    try:
        raw = int(round(float(value)))
    except (TypeError, ValueError):
        raw = fallback
    return max(1, min(60, raw))


def _preferred_ha_display_name(entity_id: object, *candidates: object) -> str:
    """Pick the best user-facing Home Assistant label for an entity.

    Door/comfort-pause entries can be saved from older configs with the raw
    entity id as their name.  When a live HA state has attributes.friendly_name,
    prefer that over the saved label so the popup shows the same friendly name
    the user sees in Home Assistant.
    """
    eid = str(entity_id or "").strip()
    fallback = eid or "Entity"
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value and value != eid:
            return value
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return fallback


def _normalize_pause_function_entry(entry: object) -> dict | None:
    if isinstance(entry, str):
        entry = {"entityId": entry}
    if not isinstance(entry, dict):
        return None
    entity_id = str(entry.get("entityId") or entry.get("entity_id") or "").strip()
    if not entity_id or "." not in entity_id:
        return None
    domain = str(entry.get("domain") or _pause_function_domain_from_entity_id(entity_id)).strip().lower()
    name = _preferred_ha_display_name(
        entity_id,
        entry.get("friendlyName"),
        entry.get("friendly_name"),
        entry.get("haName"),
        entry.get("name"),
    )
    state = str(entry.get("state") or "unknown").strip().lower()
    device_class = str(entry.get("deviceClass") or entry.get("device_class") or "").strip().lower()
    try:
        opened_at = max(0, int(float(entry.get("openedAt") or 0)))
    except (TypeError, ValueError):
        opened_at = 0
    raw_position = entry.get("currentPosition", entry.get("current_position"))
    current_position = None
    if raw_position not in (None, ""):
        try:
            current_position = max(0, min(100, float(raw_position)))
        except (TypeError, ValueError):
            current_position = None
    raw_is_closed = entry.get("isClosed", entry.get("is_closed"))
    is_closed = raw_is_closed if isinstance(raw_is_closed, bool) else None
    return {
        "entityId": entity_id[:160],
        "name": name[:120],
        "friendlyName": name[:120],
        "domain": domain[:40],
        "deviceClass": device_class[:60],
        "state": state[:80],
        "openedAt": opened_at,
        "lastChanged": str(entry.get("lastChanged") or entry.get("last_changed") or "").strip()[:80],
        "lastUpdated": str(entry.get("lastUpdated") or entry.get("last_updated") or "").strip()[:80],
        "currentPosition": current_position,
        "isClosed": is_closed,
    }


def _normalize_pause_function_entries(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for raw in value:
        entry = _normalize_pause_function_entry(raw)
        if not entry or entry["entityId"] in seen:
            continue
        seen.add(entry["entityId"])
        result.append(entry)
    return result


def _normalize_pause_function(value: object) -> dict:
    source = value if isinstance(value, dict) else {}
    previous_target = None
    try:
        if source.get("previousTargetTemp") is not None:
            previous_target = max(45, min(95, float(source.get("previousTargetTemp"))))
    except (TypeError, ValueError):
        previous_target = None
    previous_last = None
    try:
        if source.get("previousLastComfortTarget") is not None:
            previous_last = max(45, min(95, float(source.get("previousLastComfortTarget"))))
    except (TypeError, ValueError):
        previous_last = None
    return {
        "durationMinutes": _normalize_pause_function_duration(source.get("durationMinutes", source.get("minutes", source.get("delayMinutes", 5)))),
        "entries": _normalize_pause_function_entries(source.get("entries", source.get("entities", []))),
        "active": bool(source.get("active")),
        "pausedAt": _number(source.get("pausedAt"), 0, 0, None),
        "previousTargetTemp": previous_target,
        "previousLastComfortTarget": previous_last,
        "activeEntityIds": _normalize_presence_entity_list(source.get("activeEntityIds", [])),
        "snoozeUntil": _number(source.get("snoozeUntil", source.get("snoozedUntil", 0)), 0, 0, None),
        "countdownAllowed": bool(source.get("countdownAllowed")),
        "countdownReason": str(source.get("countdownReason") or "").strip()[:80],
    }


def _normalize_away_source(value: object) -> str:
    source = str(value or "").strip().lower()
    return source if source in {"manual", "presence"} else ""


def _normalize_presence_entity_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        entity_id = str(item or "").strip()
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        result.append(entity_id)
    return result


def _normalize_manual_away_presence_latch(value: object) -> dict | None:
    if not isinstance(value, dict) or value.get("active") is False:
        return None
    return {
        "active": True,
        "startedAt": _number(value.get("startedAt"), int(time.time() * 1000), 0, None),
        "baselineHome": _normalize_presence_entity_list(value.get("baselineHome")),
        "seenAway": _normalize_presence_entity_list(value.get("seenAway")),
    }


def _normalize_presence_home_override(value: object) -> dict | None:
    if not isinstance(value, dict) or value.get("active") is False:
        return None
    now_ms = int(time.time() * 1000)
    started_at = _number(value.get("startedAt"), now_ms, 0, None)
    entity_ids = _normalize_presence_entity_list(value.get("entityIds", value.get("blockedEntityIds", [])))
    reason = str(value.get("reason") or "manual-return-home").strip()[:80] or "manual-return-home"
    duration_ms = int(_number(value.get("durationMs", value.get("durationMilliseconds", 0)), 0, 0, None) or 0)
    expires_at = int(_number(value.get("expiresAt", value.get("until", value.get("expires_at", 0))), 0, 0, None) or 0)
    if reason == "arriving" and expires_at <= 0:
        expires_at = int(started_at + (duration_ms if duration_ms > 0 else ARRIVING_AWAY_BYPASS_MS))
    if expires_at > 0 and now_ms >= expires_at:
        return None
    payload = {
        "active": True,
        "startedAt": started_at,
        "entityIds": entity_ids,
        "reason": reason,
    }
    if duration_ms > 0:
        payload["durationMs"] = duration_ms
    if expires_at > 0:
        payload["expiresAt"] = expires_at
    return payload


def _presence_home_override_payload(
    entity_ids: list[str] | None = None,
    *,
    reason: str = "manual-return-home",
    duration_ms: int | None = None,
    expires_at: int | None = None,
) -> dict:
    started_at = int(time.time() * 1000)
    payload = {
        "active": True,
        "startedAt": started_at,
        "entityIds": _normalize_presence_entity_list(entity_ids or []),
        "reason": reason,
    }
    if duration_ms is not None and duration_ms > 0:
        payload["durationMs"] = int(duration_ms)
    if expires_at is not None and expires_at > 0:
        payload["expiresAt"] = int(expires_at)
    elif reason == "arriving":
        duration = int(duration_ms) if duration_ms is not None and duration_ms > 0 else ARRIVING_AWAY_BYPASS_MS
        payload["durationMs"] = duration
        payload["expiresAt"] = started_at + duration
    return payload


def _allowed_mode_for_locks(mode: str, thermostat: dict, fallback: str = "cool") -> str:
    # Lockout is an output interlock, not a mode selector. A user may still
    # select Heat/Cool/Auto/Off from the wall panel or Home Assistant, but the
    # locked relay side must never energize, including during safety calls.
    return _normalize_mode(mode, fallback)


def _available_hvac_modes(thermostat: dict) -> list[str]:
    return ["off", "cool", "heat"]

def _deepcopy_json(value: object) -> object:
    return json.loads(json.dumps(value))



def _normalize_pending_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"heat", "cool"} else ""


def _normalize_auto_switch_notice(value: object) -> dict:
    if not isinstance(value, dict):
        return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchNotice"])
    source = str(value.get("source") or "").strip().lower()
    from_mode = _normalize_pending_mode(value.get("fromMode"))
    to_mode = _normalize_pending_mode(value.get("toMode"))
    active = bool(value.get("active")) and source in {"auto", "manual"} and from_mode and to_mode and from_mode != to_mode
    if not active:
        return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchNotice"])
    return {
        "active": True,
        "source": source,
        "fromMode": from_mode,
        "toMode": to_mode,
        "switchTemp": _number(value.get("switchTemp", value.get("indoorTemp", value.get("currentTemp", value.get("outdoorTemp")))), 0, -40, 130),
        "outdoorTemp": _number(value.get("switchTemp", value.get("indoorTemp", value.get("currentTemp", value.get("outdoorTemp")))), 0, -40, 130),
        "coolTarget": _number(value.get("coolTarget"), 0, 0, 130),
        "heatTarget": _number(value.get("heatTarget"), 0, 0, 130),
        "createdAt": _number(value.get("createdAt"), 0, 0, None),
    }


def _normalize_auto_switch_notice_dismissed(value: object) -> dict:
    if not isinstance(value, dict):
        return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchNoticeDismissed"])
    source = str(value.get("source") or "").strip().lower()
    from_mode = _normalize_pending_mode(value.get("fromMode"))
    to_mode = _normalize_pending_mode(value.get("toMode"))
    active = bool(value.get("active")) and source in {"auto", "manual"} and from_mode and to_mode and from_mode != to_mode
    if not active:
        return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchNoticeDismissed"])
    return {
        "active": True,
        "source": source,
        "fromMode": from_mode,
        "toMode": to_mode,
        "coolTarget": _number(value.get("coolTarget"), 0, 0, 130),
        "heatTarget": _number(value.get("heatTarget"), 0, 0, 130),
        "dismissedAt": _number(value.get("dismissedAt"), int(time.time() * 1000), 0, None),
    }


def _normalize_auto_switch_hold(value: object) -> dict:
    if not isinstance(value, dict):
        return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchHold"])
    source = str(value.get("source") or "").strip().lower()
    mode = _normalize_pending_mode(value.get("mode"))
    active = bool(value.get("active")) and source in {"auto", "manual"} and mode
    if not active:
        return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchHold"])
    suggested = _normalize_pending_mode(value.get("suggestedMode"))
    reason = str(value.get("reason") or "").strip().lower()[:40]
    return {
        "active": True,
        "source": source,
        "mode": mode,
        "suggestedMode": suggested,
        "reason": reason,
        "dismissed": bool(value.get("dismissed")),
        "createdAt": _number(value.get("createdAt"), int(time.time() * 1000), 0, None),
    }


def _merge_thermostat_state(existing: dict | None = None, incoming: dict | None = None) -> dict:
    base = _deepcopy_json(DEFAULT_THERMOSTAT)
    auto_away_people_explicit = False
    for source_index, source in enumerate((existing or {}, incoming or {})):
        if not isinstance(source, dict):
            continue
        if isinstance(source.get("thermostat"), dict):
            source = source["thermostat"]

        if "name" in source:
            name = str(source.get("name") or "").strip()
            if name:
                base["name"] = name[:80]
        if "currentTempSource" in source:
            base["currentTempSource"] = str(source.get("currentTempSource") or "virtual").strip()[:80] or "virtual"
        if "currentTempSourceName" in source:
            base["currentTempSourceName"] = str(source.get("currentTempSourceName") or "Virtual Temp").strip()[:120] or "Virtual Temp"
        if "outdoorTempSource" in source:
            base["outdoorTempSource"] = str(source.get("outdoorTempSource") or "").strip()[:80]
        if "outdoorTempSourceName" in source:
            base["outdoorTempSourceName"] = str(source.get("outdoorTempSourceName") or "").strip()[:120]

        hvac_mode = source.get("hvac_mode", source.get("hvacMode", source.get("mode")))
        if hvac_mode is not None:
            base["mode"] = _normalize_mode(hvac_mode, base["mode"])

        fan_mode = source.get("fan_mode", source.get("fanMode", source.get("fan")))
        if fan_mode is not None:
            base["fan"] = _normalize_fan(fan_mode, base["fan"])

        preset = source.get("preset_mode", source.get("presetMode"))
        if "away" in source:
            base["away"] = bool(source.get("away"))
        elif preset is not None:
            base["away"] = str(preset).strip().lower() == "away"
        if "awaySource" in source:
            base["awaySource"] = _normalize_away_source(source.get("awaySource"))
        if "manualAwayPresenceLatch" in source:
            base["manualAwayPresenceLatch"] = _normalize_manual_away_presence_latch(source.get("manualAwayPresenceLatch"))
        if "presenceHomeOverride" in source:
            base["presenceHomeOverride"] = _normalize_presence_home_override(source.get("presenceHomeOverride"))
        if "preAwayTargetTemp" in source:
            raw_pre_away_target = source.get("preAwayTargetTemp")
            if raw_pre_away_target is None or raw_pre_away_target == "":
                base["preAwayTargetTemp"] = None
            else:
                base["preAwayTargetTemp"] = _number(raw_pre_away_target, base.get("lastComfortTarget", 70), 40, 100)

        if "heatLocked" in source:
            base["heatLocked"] = _boolish(source.get("heatLocked"))
        if "coolLocked" in source:
            base["coolLocked"] = _boolish(source.get("coolLocked"))
        air_mode_value = source.get("airControlMode", source.get("airSwitchMode", source.get("airSourceMode", None)))
        if air_mode_value is not None:
            legacy_mode = _normalize_air_control_mode(air_mode_value, base.get("airControlMode", "internal"))
            base["airControlMode"] = legacy_mode
            # Migration for state written before independent routing existed.
            # Once any independent mode is present, never let the lossy legacy
            # flag overwrite the unchanged side during a partial update.
            has_independent_mode = any(key in source for key in (
                "roomTempControlMode", "roomTempSourceMode", "temperatureSourceMode",
                "heatControlMode", "heatSourceMode",
                "coolControlMode", "coolSourceMode",
                "fanControlMode", "fanSourceMode",
            ))
            if not has_independent_mode:
                base["heatControlMode"] = legacy_mode
                base["coolControlMode"] = legacy_mode
        for dst_key, aliases in (
            ("roomTempControlMode", ("roomTempControlMode", "roomTempSourceMode", "temperatureSourceMode")),
            ("heatControlMode", ("heatControlMode", "heatSourceMode")),
            ("coolControlMode", ("coolControlMode", "coolSourceMode")),
            ("fanControlMode", ("fanControlMode", "fanSourceMode")),
        ):
            for alias in aliases:
                if alias in source:
                    base[dst_key] = _normalize_air_control_mode(source.get(alias), base.get(dst_key, "internal"))
                    break
        for dst_key, aliases in (
            ("externalHeatEntity", ("externalHeatEntity", "externalHeatControlEntity", "externalHeatEntry", "heatControlEntity")),
            ("externalCoolEntity", ("externalCoolEntity", "externalCoolControlEntity", "externalCoolEntry", "coolControlEntity")),
            ("externalFanEntity", ("externalFanEntity", "externalFanControlEntity", "externalFanEntry", "fanControlEntity")),
        ):
            for alias in aliases:
                if alias in source:
                    base[dst_key] = _normalize_external_air_entity(source.get(alias))
                    break
        source_has_auto_away_people = _source_has_auto_away_people(source)
        if "people" in source:
            base["people"] = _normalize_person_entries(source.get("people"))
            # Migration only: older builds used `people` for both the home-screen
            # presence strip and Auto Away/Home. Copy the saved list into the new
            # Auto Away list when reading old state, but do not let future person
            # tracking edits keep changing the Auto Away users.
            if source_index == 0 and not auto_away_people_explicit and not base.get("autoAwayPeople"):
                base["autoAwayPeople"] = _normalize_person_entries(source.get("people"))
        if source_has_auto_away_people:
            auto_away_people_explicit = True
            base["autoAwayPeople"] = _normalize_person_entries(_auto_away_people_from_source(source))
        if "schedules" in source:
            base["schedules"] = _normalize_schedule_entries(source.get("schedules"))
        if "pauseFunction" in source:
            base["pauseFunction"] = _normalize_pause_function(source.get("pauseFunction"))

        for key, fallback, minimum, maximum in (
            ("currentTemp", base["currentTemp"], -40, 130),
            ("currentTempUpdatedAt", base.get("currentTempUpdatedAt", 0), 0, None),
            ("virtualTempOverrideUntil", base.get("virtualTempOverrideUntil", 0), 0, None),
            ("targetTemp", base["targetTemp"], 40, 100),
            ("lastComfortTarget", base["lastComfortTarget"], 40, 100),
            ("lastPanelTargetTemp", base.get("lastPanelTargetTemp", 0), 0, 130),
            ("lastPanelTargetRequestAt", base.get("lastPanelTargetRequestAt", 0), 0, None),
            ("lastPanelModeRequestAt", base.get("lastPanelModeRequestAt", 0), 0, None),
            # Match the values the native settings screen actually offers.
            # Otherwise the UI can show “saved” while the backend silently
            # clamps values like Heat Away 40 or Cool Away 100 back to older
            # limits.
            ("awayHeat", base["awayHeat"], 40, 75),
            ("awayCool", base["awayCool"], 75, 100),
            ("safetyLow", base.get("safetyLow", base.get("awayHeat", 55)), 40, 75),
            ("safetyHigh", base.get("safetyHigh", base.get("awayCool", 85)), 75, 100),
            ("humidity", base["humidity"], 0, 100),
            ("outdoorTemp", base["outdoorTemp"], -40, 130),
            ("outdoorWindSpeed", base.get("outdoorWindSpeed", 0), 0, 250),
            ("autoCoolOutdoorTarget", base["autoCoolOutdoorTarget"], 40, 100),
            ("autoHeatOutdoorTarget", base["autoHeatOutdoorTarget"], 40, 100),
            ("autoChangeoverLockoutMinutes", base["autoChangeoverLockoutMinutes"], 0, 720),
            ("manualChangeoverLockoutMinutes", base.get("manualChangeoverLockoutMinutes", MANUAL_CHANGEOVER_LOCKOUT_MINUTES), 0, 60),
            ("temperatureDifferential", base.get("temperatureDifferential", 0), 0, 5),
            ("heatMinimumRuntimeMinutes", base.get("heatMinimumRuntimeMinutes", 2), 1, 30),
            ("coolMinimumRuntimeMinutes", base.get("coolMinimumRuntimeMinutes", 2), 1, 30),
            ("coolFanRemainOnMinutes", base["coolFanRemainOnMinutes"], 0, 15),
            ("autoLockoutUntil", base["autoLockoutUntil"], 0, None),
            ("manualLockoutUntil", base.get("manualLockoutUntil", 0), 0, None),
            ("lastHeatRunAt", base["lastHeatRunAt"], 0, None),
            ("lastCoolRunAt", base["lastCoolRunAt"], 0, None),
            ("equipmentLastHeatRunAt", base.get("equipmentLastHeatRunAt", 0), 0, None),
            ("equipmentLastCoolRunAt", base.get("equipmentLastCoolRunAt", 0), 0, None),
            ("lastManualChangeoverBypassAt", base.get("lastManualChangeoverBypassAt", 0), 0, None),
            ("heatCycleStartedAt", base.get("heatCycleStartedAt", 0), 0, None),
            ("coolCycleStartedAt", base.get("coolCycleStartedAt", 0), 0, None),
            ("heatCycleStoppedAt", base.get("heatCycleStoppedAt", 0), 0, None),
            ("coolCycleStoppedAt", base.get("coolCycleStoppedAt", 0), 0, None),
            ("coolFanHoldUntil", base["coolFanHoldUntil"], 0, None),
        ):
            if key in source:
                base[key] = _number(source.get(key), fallback, minimum, maximum)

        if "outdoorWindUnit" in source:
            wind_unit = str(source.get("outdoorWindUnit") or "").strip()
            base["outdoorWindUnit"] = (wind_unit or "mph")[:16]

        if "temperature" in source:
            base["targetTemp"] = _number(source.get("temperature"), base["targetTemp"], 45, 95)
        if "target_temperature" in source:
            base["targetTemp"] = _number(source.get("target_temperature"), base["targetTemp"], 45, 95)
        if "current_temperature" in source:
            base["currentTemp"] = _number(source.get("current_temperature"), base["currentTemp"], -40, 130)
        if "currentTempUpdatedAt" in source or "current_temp_updated_at" in source:
            base["currentTempUpdatedAt"] = _number(source.get("currentTempUpdatedAt", source.get("current_temp_updated_at")), base.get("currentTempUpdatedAt", 0), 0, None)

        if "autoActiveMode" in source:
            active = str(source.get("autoActiveMode") or base.get("autoActiveMode") or "cool").strip().lower()
            if active in {"heat", "cool"}:
                base["autoActiveMode"] = active
        if "autoPendingMode" in source:
            pending = str(source.get("autoPendingMode") or "").strip().lower()
            base["autoPendingMode"] = pending if pending in {"", "heat", "cool"} else ""
        if "manualPendingMode" in source:
            pending = str(source.get("manualPendingMode") or "").strip().lower()
            base["manualPendingMode"] = pending if pending in {"", "heat", "cool"} else ""
        if "heatRelayWasOn" in source:
            base["heatRelayWasOn"] = bool(source.get("heatRelayWasOn"))
        if "coolRelayWasOn" in source:
            base["coolRelayWasOn"] = bool(source.get("coolRelayWasOn"))
        if "lastManualChangeoverBypassMode" in source:
            bypass_mode = str(source.get("lastManualChangeoverBypassMode") or "").strip().lower()
            base["lastManualChangeoverBypassMode"] = bypass_mode if bypass_mode in {"", "heat", "cool"} else ""
        if "lastPanelModeRequestMode" in source:
            panel_mode = str(source.get("lastPanelModeRequestMode") or "").strip().lower()
            base["lastPanelModeRequestMode"] = panel_mode if panel_mode in {"", "off", "heat", "cool"} else ""
        if "autoSwitchNotice" in source:
            base["autoSwitchNotice"] = _normalize_auto_switch_notice(source.get("autoSwitchNotice"))
        if "autoSwitchNoticeDismissed" in source:
            base["autoSwitchNoticeDismissed"] = _normalize_auto_switch_notice_dismissed(source.get("autoSwitchNoticeDismissed"))
        if "autoSwitchHold" in source:
            base["autoSwitchHold"] = _normalize_auto_switch_hold(source.get("autoSwitchHold"))

        incoming_limits = source.get("limits") if isinstance(source.get("limits"), dict) else {}
        for mode in ("cool", "heat", "auto"):
            current = base["limits"].get(mode) or DEFAULT_THERMOSTAT["limits"][mode]
            update = incoming_limits.get(mode) if isinstance(incoming_limits.get(mode), dict) else {}
            low = _intish(update.get("min", current.get("min")), current.get("min"), 40, 100)
            high = _intish(update.get("max", current.get("max")), current.get("max"), low + 2, 100)
            base["limits"][mode] = {"min": min(low, high - 2), "max": high}

    base["safetyLow"] = int(max(40, min(75, round(_number(base.get("safetyLow"), base.get("awayHeat", 55), 40, 75)))))
    base["safetyHigh"] = int(max(base["safetyLow"] + 2, min(100, round(_number(base.get("safetyHigh"), base.get("awayCool", 85), 75, 100)))))
    base["autoHeatOutdoorTarget"] = min(base["autoHeatOutdoorTarget"], base["autoCoolOutdoorTarget"] - 1)
    base["mode"] = _allowed_mode_for_locks(base["mode"], base, base["mode"])
    if base.get("autoActiveMode") == "heat" and base.get("heatLocked"):
        base["autoActiveMode"] = "cool" if not base.get("coolLocked") else ""
    if base.get("autoActiveMode") == "cool" and base.get("coolLocked"):
        base["autoActiveMode"] = "heat" if not base.get("heatLocked") else ""
    if base.get("autoPendingMode") == "heat" and base.get("heatLocked"):
        base["autoPendingMode"] = ""
    if base.get("autoPendingMode") == "cool" and base.get("coolLocked"):
        base["autoPendingMode"] = ""
    if base.get("manualPendingMode") == "heat" and base.get("heatLocked"):
        base["manualPendingMode"] = ""
        base["manualLockoutUntil"] = 0
    if base.get("manualPendingMode") == "cool" and base.get("coolLocked"):
        base["manualPendingMode"] = ""
        base["manualLockoutUntil"] = 0
    hold_mode = base.get("autoSwitchHold", {}).get("mode") if isinstance(base.get("autoSwitchHold"), dict) else ""
    if (hold_mode == "heat" and base.get("heatLocked")) or (hold_mode == "cool" and base.get("coolLocked")):
        base["autoSwitchHold"] = _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchHold"])
    if base.get("heatLocked") and base.get("coolLocked"):
        base["autoActiveMode"] = ""
        base["autoPendingMode"] = ""
        base["autoLockoutUntil"] = 0
        base["manualPendingMode"] = ""
        base["manualLockoutUntil"] = 0
        base["autoSwitchNotice"] = _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchNotice"])
        base["autoSwitchNoticeDismissed"] = _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchNoticeDismissed"])
        base["autoSwitchHold"] = _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchHold"])
        base["coolFanHoldUntil"] = 0
        base["coolRelayWasOn"] = False
    base["awaySource"] = _normalize_away_source(base.get("awaySource"))
    base["manualAwayPresenceLatch"] = _normalize_manual_away_presence_latch(base.get("manualAwayPresenceLatch"))
    base["presenceHomeOverride"] = _normalize_presence_home_override(base.get("presenceHomeOverride"))
    base["pauseFunction"] = _normalize_pause_function(base.get("pauseFunction"))
    if not base["pauseFunction"].get("entries"):
        base["pauseFunction"]["active"] = False
        base["pauseFunction"]["activeEntityIds"] = []
        base["pauseFunction"]["snoozeUntil"] = 0
        base["pauseFunction"]["countdownAllowed"] = False
        base["pauseFunction"]["countdownReason"] = ""
    if not base["pauseFunction"].get("active"):
        base["pauseFunction"]["pausedAt"] = 0
        base["pauseFunction"]["previousTargetTemp"] = None
        base["pauseFunction"]["previousLastComfortTarget"] = None
        base["pauseFunction"]["activeEntityIds"] = []
    if not base["away"]:
        base["awaySource"] = ""
        base["manualAwayPresenceLatch"] = None
    elif base["awaySource"] != "manual":
        base["manualAwayPresenceLatch"] = None
    override_reason = str((base.get("presenceHomeOverride") or {}).get("reason") or "").strip().lower()
    if base["away"] or (not base.get("autoAwayPeople") and override_reason != "arriving"):
        # Indefinite Return Home overrides only matter when Auto Away has people
        # to evaluate. Arriving is an explicit timed panel state and must remain
        # visible/toggleable even on a thermostat that has no Auto Away users.
        base["presenceHomeOverride"] = None
    mode_limits = base["limits"].get(base["mode"], {"min": 45, "max": 95})
    if not base["away"] and not base.get("pauseFunction", {}).get("active"):
        base["targetTemp"] = _number(base["targetTemp"], 70, mode_limits.get("min"), mode_limits.get("max"))
        base["lastComfortTarget"] = _number(base["lastComfortTarget"], base["targetTemp"], mode_limits.get("min"), mode_limits.get("max"))
    # Preserve a meaningful legacy value for older clients. Mixed routing cannot
    # be represented by the old flag, so only report External when both sides are.
    base["airControlMode"] = "external" if (
        _normalize_air_control_mode(base.get("heatControlMode"), "internal") == "external"
        and _normalize_air_control_mode(base.get("coolControlMode"), "internal") == "external"
    ) else "internal"
    return base


THERMOSTAT_PERSIST_KEYS = (
    "name",
    "currentTempSource",
    "currentTempSourceName",
    "targetTemp",
    "lastComfortTarget",
    "preAwayTargetTemp",
    "mode",
    "fan",
    "away",
    "awaySource",
    "manualAwayPresenceLatch",
    "presenceHomeOverride",
    "awayHeat",
    "awayCool",
    "safetyLow",
    "safetyHigh",
    "autoCoolOutdoorTarget",
    "autoHeatOutdoorTarget",
    "autoChangeoverLockoutMinutes",
    "manualChangeoverLockoutMinutes",
    "temperatureDifferential",
    "heatMinimumRuntimeMinutes",
    "coolMinimumRuntimeMinutes",
    "coolFanRemainOnMinutes",
    "airControlMode",
    "roomTempControlMode",
    "heatControlMode",
    "coolControlMode",
    "fanControlMode",
    "externalHeatEntity",
    "externalCoolEntity",
    "externalFanEntity",
    "heatLocked",
    "coolLocked",
    "people",
    "autoAwayPeople",
    "schedules",
    "autoActiveMode",
    "limits",
)

THERMOSTAT_RUNTIME_KEYS = (
    "currentTemp",
    "currentTempUpdatedAt",
    "virtualTempOverrideUntil",
    "humidity",
    "outdoorTemp",
    "outdoorWindSpeed",
    "outdoorWindUnit",
    "outdoorTempSource",
    "outdoorTempSourceName",
    "autoPendingMode",
    "autoLockoutUntil",
    "manualPendingMode",
    "manualLockoutUntil",
    "lastPanelTargetTemp",
    "lastPanelTargetRequestAt",
    "lastPanelModeRequestMode",
    "lastPanelModeRequestAt",
    "lastHeatRunAt",
    "lastCoolRunAt",
    "equipmentLastHeatRunAt",
    "equipmentLastCoolRunAt",
    "lastManualChangeoverBypassMode",
    "lastManualChangeoverBypassAt",
    "heatRelayWasOn",
    "coolRelayWasOn",
    "heatCycleStartedAt",
    "coolCycleStartedAt",
    "heatCycleStoppedAt",
    "coolCycleStoppedAt",
    "coolFanHoldUntil",
    "autoSwitchNotice",
    "autoSwitchNoticeDismissed",
    "autoSwitchHold",
    "relays",
    "relayFan",
    "relayHeat",
    "relayCool",
    "hvacAction",
    "hvac_action",
    "safetyMode",
    "presetMode",
    "preset_mode",
)


def _thermostat_persist_payload(thermostat: dict) -> dict:
    """Return only long-term thermostat settings that are worth SD-card persistence.

    Live runtime fields such as current readings, auto-switch notices, relay
    flags, lockout timers, and the active door/comfort pause state stay in RAM.
    That keeps the controller working like a normal local thermostat during a
    Wi-Fi outage without turning every sensor/update loop into an SD-card write.
    """
    safe = _merge_thermostat_state(thermostat)
    persistent = {key: _deepcopy_json(safe.get(key)) for key in THERMOSTAT_PERSIST_KEYS}
    pause = safe.get("pauseFunction") if isinstance(safe.get("pauseFunction"), dict) else {}
    # Door/entry pause is a runtime state. If a save occurs while comfort is
    # paused, keep the long-term saved target at the original comfort target
    # instead of permanently saving the temporary away setpoint.
    if bool(pause.get("active")) and pause.get("previousTargetTemp") is not None:
        persistent["targetTemp"] = pause.get("previousTargetTemp")
    if bool(pause.get("active")) and pause.get("previousLastComfortTarget") is not None:
        persistent["lastComfortTarget"] = pause.get("previousLastComfortTarget")
    persistent["pauseFunction"] = {
        "durationMinutes": _number(pause.get("durationMinutes"), 5, 1, 60),
        "entries": _deepcopy_json(pause.get("entries") if isinstance(pause.get("entries"), list) else []),
        "active": False,
        "pausedAt": 0,
        "previousTargetTemp": None,
        "previousLastComfortTarget": None,
        "activeEntityIds": [],
        "snoozeUntil": 0,
        "countdownAllowed": False,
        "countdownReason": "",
    }
    return persistent


def _thermostat_record_for_disk(record: dict) -> dict:
    return {
        "version": int(record.get("version", 1) or 1),
        "updatedAt": int(record.get("updatedAt", 0) or 0),
        "thermostat": _thermostat_persist_payload(record.get("thermostat") or {}),
    }


def _atomic_write_json(path: Path, record: dict) -> None:
    """Durably write JSON without leaving a half-written file on SD-card power loss."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    body = json.dumps(record, indent=2, sort_keys=True).encode("utf-8")
    with temp_path.open("wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)
    try:
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        pass


def _schedule_entries_from_object(value: object) -> list[dict] | None:
    """Extract an explicit schedule list from known old/new storage shapes.

    Returns None when no schedule field is present. Returns [] when a schedule
    field is explicitly present but empty, which lets an intentional clear stay
    cleared instead of being restored from older backups.
    """
    candidates: list[object] = []

    def add_candidates(obj: object) -> None:
        if not isinstance(obj, dict):
            return
        candidates.append(obj.get("schedules"))
        for key in ("schedule", "scheduleConfig", "thermostatSchedule", "thermostatSchedules"):
            candidates.append(obj.get(key))

    add_candidates(value)
    if isinstance(value, dict):
        thermostat = value.get("thermostat")
        add_candidates(thermostat)
        config = value.get("config")
        add_candidates(config)
        if isinstance(config, dict):
            add_candidates(config.get("thermostat"))

    for candidate in candidates:
        if isinstance(candidate, list):
            return _normalize_schedule_entries(candidate)
        if isinstance(candidate, dict):
            inner = candidate.get("schedules") or candidate.get("items") or candidate.get("entries")
            if isinstance(inner, list):
                return _normalize_schedule_entries(inner)
    return None


def _read_schedule_entries_from_file(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _schedule_entries_from_object(raw)


def _schedule_backup_roots() -> list[Path]:
    roots: list[Path] = []
    for candidate in (Path.home(), Path("/home/david"), Path("/home/pi")):
        try:
            if candidate.exists():
                root = candidate / "thermostat-pi-data-backups"
                if root not in roots:
                    roots.append(root)
        except Exception:
            continue
    return roots


def _schedules_from_recent_update_backups() -> list[dict]:
    backup_dirs: list[Path] = []
    for root in _schedule_backup_roots():
        try:
            backup_dirs.extend([p for p in root.iterdir() if p.is_dir()])
        except Exception:
            continue
    backup_dirs.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for folder in backup_dirs[:30]:
        for filename in (
            "thermostat-schedules.json",
            "thermostat-schedules.backup.json",
            "thermostat-state.json",
            "panel-config.json",
        ):
            schedules = _read_schedule_entries_from_file(folder / filename)
            if schedules:
                return schedules
    return []


def _fallback_saved_schedules() -> list[dict]:
    # A dedicated schedule file is authoritative when present. If it explicitly
    # contains an empty list, the user intentionally deleted all schedules.
    for path in (THERMOSTAT_SCHEDULES_FILE, THERMOSTAT_SCHEDULES_BACKUP_FILE):
        schedules = _read_schedule_entries_from_file(path)
        if schedules is not None:
            return schedules

    legacy_schedules = _legacy_panel_config_schedules()
    if legacy_schedules:
        return legacy_schedules

    return _schedules_from_recent_update_backups()


def _write_schedule_backup(schedules: object) -> None:
    safe = _normalize_schedule_entries(schedules)
    record = {"version": 1, "updatedAt": int(time.time()), "schedules": safe}
    _atomic_write_json(THERMOSTAT_SCHEDULES_FILE, record)
    # Keep the last known non-empty copy too. If a full code copy accidentally
    # replaces thermostat-state.json with the repo default, this gives the next
    # boot a small independent recovery point.
    if safe:
        _atomic_write_json(THERMOSTAT_SCHEDULES_BACKUP_FILE, record)


def _mirror_schedules_to_panel_config(schedules: object) -> None:
    """Mirror schedules into panel config for migration/update safety.

    Schedule edits are low-frequency user actions, so this does not add steady
    SD-card wear. It gives older and newer builds a second persistent location
    to recover from if thermostat-state.json is replaced during an update.
    """
    safe = _normalize_schedule_entries(schedules)
    try:
        existing = _read_panel_config_record().get("config")
        config = _deepcopy_json(existing) if isinstance(existing, dict) else {}
        thermostat = config.setdefault("thermostat", {})
        if not isinstance(thermostat, dict):
            thermostat = {}
            config["thermostat"] = thermostat
        thermostat["schedules"] = safe
        # Also keep the old top-level shape populated for legacy fallbacks.
        config["schedules"] = safe
        _write_panel_config_record(config)
    except Exception:
        pass


def _legacy_panel_config_schedules() -> list[dict]:
    """Return schedules saved in old panel config locations, if any."""
    try:
        record = _read_panel_config_record()
        config = record.get("config") if isinstance(record, dict) else {}
        if not isinstance(config, dict):
            return []
        candidates: list[object] = [config.get("schedules")]
        thermo = config.get("thermostat") if isinstance(config.get("thermostat"), dict) else {}
        if isinstance(thermo, dict):
            candidates.append(thermo.get("schedules"))
        for key in ("schedule", "scheduleConfig", "thermostatSchedule", "thermostatSchedules"):
            candidates.append(config.get(key))
            if isinstance(thermo, dict):
                candidates.append(thermo.get(key))
        for candidate in candidates:
            if isinstance(candidate, list):
                schedules = _normalize_schedule_entries(candidate)
                if schedules:
                    return schedules
            if isinstance(candidate, dict):
                inner = candidate.get("schedules") or candidate.get("items") or candidate.get("entries")
                if isinstance(inner, list):
                    schedules = _normalize_schedule_entries(inner)
                    if schedules:
                        return schedules
    except Exception:
        return []
    return []

def _read_thermostat_record_from_disk() -> dict:
    if not THERMOSTAT_STATE_FILE.exists():
        thermostat = _merge_thermostat_state()
        fallback_schedules = _fallback_saved_schedules()
        if fallback_schedules:
            thermostat["schedules"] = fallback_schedules
        return {"version": 1, "updatedAt": int(time.time()), "thermostat": thermostat}
    try:
        raw = json.loads(THERMOSTAT_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    thermostat = _merge_thermostat_state(raw.get("thermostat", raw if isinstance(raw, dict) else {}))
    if not thermostat.get("schedules"):
        fallback_schedules = _fallback_saved_schedules()
        if fallback_schedules:
            thermostat["schedules"] = fallback_schedules
            try:
                _write_schedule_backup(fallback_schedules)
            except Exception:
                pass
    return {
        "version": int(raw.get("version", 1)) if isinstance(raw, dict) else 1,
        "updatedAt": int(raw.get("updatedAt", 0) or 0) if isinstance(raw, dict) else 0,
        "thermostat": thermostat,
    }


def _thermostat_persist_signature(thermostat: dict) -> str:
    return json.dumps(_thermostat_persist_payload(thermostat), sort_keys=True, separators=(",", ":"))


def _read_thermostat_record() -> dict:
    global _THERMOSTAT_RECORD_CACHE, _THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE
    with _THERMOSTAT_RECORD_LOCK:
        if _THERMOSTAT_RECORD_CACHE is None:
            _THERMOSTAT_RECORD_CACHE = _read_thermostat_record_from_disk()
            _THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE = _thermostat_persist_signature(_THERMOSTAT_RECORD_CACHE["thermostat"])
        return _deepcopy_json(_THERMOSTAT_RECORD_CACHE)


def _write_thermostat_record(thermostat: dict, *, force: bool = False, persist: bool = True) -> dict:
    """Update thermostat state in RAM and avoid SD writes for live runtime changes.

    Live readings, relay flags and lockout timers can change frequently. Those
    are kept in RAM for the local API and Home Assistant, while long-term
    thermostat choices still persist to the SD card when they actually change.
    Pass persist=False for sensor/control-loop updates that must never touch
    the SD card.
    """
    global _THERMOSTAT_RECORD_CACHE, _THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE, _THERMOSTAT_RECORD_DIRTY
    with _THERMOSTAT_RECORD_LOCK:
        if _THERMOSTAT_RECORD_CACHE is None:
            _THERMOSTAT_RECORD_CACHE = _read_thermostat_record_from_disk()
            _THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE = _thermostat_persist_signature(_THERMOSTAT_RECORD_CACHE["thermostat"])
        merged = _merge_thermostat_state(thermostat)
        now = int(time.time())
        record = {
            "version": int(_THERMOSTAT_RECORD_CACHE.get("version", 1) or 1),
            "updatedAt": now,
            "thermostat": merged,
        }
        _THERMOSTAT_RECORD_CACHE = record
        if not persist:
            return _deepcopy_json(record)
        signature = _thermostat_persist_signature(merged)
        should_write = force or signature != _THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE or not THERMOSTAT_STATE_FILE.exists()
        _THERMOSTAT_RECORD_DIRTY = should_write
        if should_write:
            _atomic_write_json(THERMOSTAT_STATE_FILE, _thermostat_record_for_disk(record))
            _THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE = signature
            _THERMOSTAT_RECORD_DIRTY = False
        return _deepcopy_json(record)


def _flush_thermostat_state_to_disk() -> None:
    global _THERMOSTAT_RECORD_DIRTY, _THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE
    with _THERMOSTAT_RECORD_LOCK:
        if not _THERMOSTAT_RECORD_DIRTY or _THERMOSTAT_RECORD_CACHE is None:
            return
        _atomic_write_json(THERMOSTAT_STATE_FILE, _thermostat_record_for_disk(_THERMOSTAT_RECORD_CACHE))
        _THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE = _thermostat_persist_signature(_THERMOSTAT_RECORD_CACHE["thermostat"])
        _THERMOSTAT_RECORD_DIRTY = False


def _normalize_panel_config(config: object) -> dict | None:
    return _migrate_panel_config(config)


def _read_panel_config_record() -> dict:
    with _PANEL_CONFIG_LOCK:
        if not PANEL_CONFIG_FILE.exists():
            return {"version": 1, "updatedAt": 0, "config": None}

        try:
            raw = json.loads(PANEL_CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}

        version = 1
        updated_at = 0
        config = None
        if isinstance(raw, dict):
            version = int(raw.get("version", 1) or 1)
            updated_at = int(raw.get("updatedAt", 0) or 0)
            config = _normalize_panel_config(raw.get("config"))
            if config is None and any(key in raw for key in ("thermostat", "alarm", "blinds", "lights", "integrations")):
                config = _normalize_panel_config(raw)

        return {"version": version, "updatedAt": updated_at, "config": config}


def _write_panel_config_record(config: dict) -> dict:
    safe_config = _normalize_panel_config(config) or {}
    with _PANEL_CONFIG_LOCK:
        existing = _read_panel_config_record()
        if existing.get("config") == safe_config and PANEL_CONFIG_FILE.exists():
            return {
                "version": int(existing.get("version", 1) or 1),
                "updatedAt": int(existing.get("updatedAt", 0) or 0),
                "config": safe_config,
            }
        next_version = int(existing.get("version", 0) or 0) + 1
        record = {"version": next_version, "updatedAt": int(time.time()), "config": safe_config}
        _atomic_write_json(PANEL_CONFIG_FILE, record)
        return record



HVAC_HISTORY_RELAYS = ("fan", "heat", "cool")
HVAC_HISTORY_MAX_DAYS = int(os.environ.get("SMART_THERMOSTAT_HISTORY_MAX_DAYS", "370"))


def _date_key_from_ms(ms: int | float | None = None) -> str:
    seconds = (float(ms) / 1000.0) if ms is not None else time.time()
    return time.strftime("%Y-%m-%d", time.localtime(seconds))


def _midnight_after_date_key(date_key: str) -> int:
    try:
        date_value = datetime.strptime(date_key, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        date_value = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return int(time.mktime(date_value.timetuple()) * 1000)


def _new_hvac_history_day(date_key: str, now_ms: int, relays: dict | None = None, source: str = "thermostat") -> dict:
    relay_state = _normalize_relay_outputs(relays or {})
    day = {
        "date": date_key,
        "startedAtMs": now_ms,
        "updatedAtMs": now_ms,
        "totalsMs": {relay: 0 for relay in HVAC_HISTORY_RELAYS},
        "cycles": {relay: 0 for relay in HVAC_HISTORY_RELAYS},
        "relays": relay_state,
        "relayStartedAtMs": {relay: now_ms if relay_state.get(relay) else None for relay in HVAC_HISTORY_RELAYS},
        "events": [],
    }
    for relay, on in relay_state.items():
        if on:
            day["cycles"][relay] = 1
            day["events"].append({"relay": relay, "action": "on", "atMs": now_ms, "source": source})
    return day


def _normalize_hvac_history_day(value: object, date_key: str, now_ms: int) -> dict:
    if not isinstance(value, dict):
        return _new_hvac_history_day(date_key, now_ms)
    day = _new_hvac_history_day(str(value.get("date") or date_key), now_ms)
    totals = value.get("totalsMs") if isinstance(value.get("totalsMs"), dict) else {}
    cycles = value.get("cycles") if isinstance(value.get("cycles"), dict) else {}
    relays = value.get("relays") if isinstance(value.get("relays"), dict) else {}
    starts = value.get("relayStartedAtMs") if isinstance(value.get("relayStartedAtMs"), dict) else {}
    events = value.get("events") if isinstance(value.get("events"), list) else []
    day["startedAtMs"] = int(value.get("startedAtMs") or now_ms)
    day["updatedAtMs"] = int(value.get("updatedAtMs") or now_ms)
    day["totalsMs"] = {relay: max(0, int(float(totals.get(relay) or 0))) for relay in HVAC_HISTORY_RELAYS}
    day["cycles"] = {relay: max(0, int(float(cycles.get(relay) or 0))) for relay in HVAC_HISTORY_RELAYS}
    day["relays"] = {relay: bool(relays.get(relay)) for relay in HVAC_HISTORY_RELAYS}
    day["relayStartedAtMs"] = {
        relay: int(starts.get(relay)) if starts.get(relay) not in (None, "") else None
        for relay in HVAC_HISTORY_RELAYS
    }
    normalized_events = []
    for event in events[-2000:]:
        if not isinstance(event, dict):
            continue
        relay = str(event.get("relay") or "").lower()
        action = str(event.get("action") or "").lower()
        if relay not in HVAC_HISTORY_RELAYS or action not in {"on", "off"}:
            continue
        try:
            at_ms = int(float(event.get("atMs") or 0))
        except (TypeError, ValueError):
            continue
        if at_ms <= 0:
            continue
        normalized_events.append({"relay": relay, "action": action, "atMs": at_ms, "source": str(event.get("source") or "thermostat")[:40]})
    day["events"] = normalized_events
    return day


def _load_hvac_history_archive_locked() -> dict:
    global _HVAC_HISTORY_ARCHIVE
    if _HVAC_HISTORY_ARCHIVE is not None:
        return _HVAC_HISTORY_ARCHIVE
    raw = {}
    if HVAC_HISTORY_FILE.exists():
        try:
            raw = json.loads(HVAC_HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    days_raw = raw.get("days") if isinstance(raw, dict) and isinstance(raw.get("days"), dict) else {}
    now_ms = int(time.time() * 1000)
    days = {}
    for date_key, value in days_raw.items():
        key = str(date_key)
        if len(key) == 10:
            days[key] = _normalize_hvac_history_day(value, key, now_ms)
    _HVAC_HISTORY_ARCHIVE = {
        "version": int(raw.get("version", 1) or 1) if isinstance(raw, dict) else 1,
        "updatedAt": int(raw.get("updatedAt", 0) or 0) if isinstance(raw, dict) else 0,
        "days": days,
    }
    return _HVAC_HISTORY_ARCHIVE


def _hvac_history_persistence_enabled() -> bool:
    return HISTORY_PERSISTENCE_MODE not in {"0", "false", "no", "off", "ram", "memory", "none", "disabled"}


def _save_hvac_history_archive_locked(include_current: bool = False) -> None:
    archive = _load_hvac_history_archive_locked()
    days = dict(archive.get("days") or {})
    if include_current and _HVAC_HISTORY_CURRENT is not None:
        days[str(_HVAC_HISTORY_CURRENT.get("date"))] = _deepcopy_json(_HVAC_HISTORY_CURRENT)
    sorted_keys = sorted(days.keys())[-HVAC_HISTORY_MAX_DAYS:]
    record = {
        "version": 1,
        "updatedAt": int(time.time()),
        "days": {key: days[key] for key in sorted_keys},
    }
    archive["version"] = record["version"]
    archive["updatedAt"] = record["updatedAt"]
    archive["days"] = record["days"]
    if _hvac_history_persistence_enabled():
        _atomic_write_json(HVAC_HISTORY_FILE, record)


def _ensure_hvac_history_current_locked(now_ms: int) -> dict:
    global _HVAC_HISTORY_CURRENT
    archive = _load_hvac_history_archive_locked()
    today = _date_key_from_ms(now_ms)
    if _HVAC_HISTORY_CURRENT is None:
        saved_today = archive.get("days", {}).get(today)
        if saved_today:
            _HVAC_HISTORY_CURRENT = _normalize_hvac_history_day(saved_today, today, now_ms)
            # Do not count downtime between process stop and restart. Close any
            # previously open period at the last saved time, then resume with
            # all relays considered off until the next hardware write.
            saved_stop_ms = int(_HVAC_HISTORY_CURRENT.get("updatedAtMs") or now_ms)
            active_from_events = set()
            for event in _HVAC_HISTORY_CURRENT.get("events") or []:
                if not isinstance(event, dict):
                    continue
                relay = str(event.get("relay") or "").lower()
                action = str(event.get("action") or "").lower()
                if relay not in HVAC_HISTORY_RELAYS:
                    continue
                if action == "on":
                    active_from_events.add(relay)
                elif action == "off":
                    active_from_events.discard(relay)
            old_relays = dict(_HVAC_HISTORY_CURRENT.get("relays") or {})
            for relay in HVAC_HISTORY_RELAYS:
                if old_relays.get(relay) or relay in active_from_events:
                    _HVAC_HISTORY_CURRENT.setdefault("events", []).append({"relay": relay, "action": "off", "atMs": saved_stop_ms, "source": "service-restart"})
            _HVAC_HISTORY_CURRENT["updatedAtMs"] = now_ms
            _HVAC_HISTORY_CURRENT["relays"] = {relay: False for relay in HVAC_HISTORY_RELAYS}
            _HVAC_HISTORY_CURRENT["relayStartedAtMs"] = {relay: None for relay in HVAC_HISTORY_RELAYS}
        else:
            _HVAC_HISTORY_CURRENT = _new_hvac_history_day(today, now_ms)
    return _HVAC_HISTORY_CURRENT


def _account_hvac_history_duration_locked(day: dict, until_ms: int) -> None:
    last_ms = int(day.get("updatedAtMs") or until_ms)
    delta = max(0, int(until_ms) - last_ms)
    if delta:
        totals = day.setdefault("totalsMs", {relay: 0 for relay in HVAC_HISTORY_RELAYS})
        relays = day.setdefault("relays", {relay: False for relay in HVAC_HISTORY_RELAYS})
        for relay in HVAC_HISTORY_RELAYS:
            if relays.get(relay):
                totals[relay] = max(0, int(totals.get(relay) or 0)) + delta
    day["updatedAtMs"] = int(until_ms)


def _rollover_hvac_history_locked(now_ms: int) -> None:
    global _HVAC_HISTORY_CURRENT
    current = _ensure_hvac_history_current_locked(now_ms)
    today = _date_key_from_ms(now_ms)
    wrote_archive = False
    while str(current.get("date")) != today:
        midnight_ms = _midnight_after_date_key(str(current.get("date")))
        relay_state = {relay: bool((current.get("relays") or {}).get(relay)) for relay in HVAC_HISTORY_RELAYS}
        _account_hvac_history_duration_locked(current, midnight_ms)
        for relay, on in relay_state.items():
            if on:
                current.setdefault("events", []).append({"relay": relay, "action": "off", "atMs": midnight_ms, "source": "day-rollover"})
        archive = _load_hvac_history_archive_locked()
        archive.setdefault("days", {})[str(current.get("date"))] = _deepcopy_json(current)
        next_day = _date_key_from_ms(midnight_ms + 1000)
        current = _new_hvac_history_day(next_day, midnight_ms, relay_state, "day-rollover")
        _HVAC_HISTORY_CURRENT = current
        wrote_archive = True
    if wrote_archive:
        _save_hvac_history_archive_locked(include_current=False)


def _record_hvac_history(relays: dict, source: str = "thermostat") -> None:
    now_ms = int(time.time() * 1000)
    normalized = _normalize_relay_outputs(relays or {})
    with _HVAC_HISTORY_LOCK:
        _rollover_hvac_history_locked(now_ms)
        day = _ensure_hvac_history_current_locked(now_ms)
        _account_hvac_history_duration_locked(day, now_ms)
        old_relays = day.setdefault("relays", {relay: False for relay in HVAC_HISTORY_RELAYS})
        starts = day.setdefault("relayStartedAtMs", {relay: None for relay in HVAC_HISTORY_RELAYS})
        cycles = day.setdefault("cycles", {relay: 0 for relay in HVAC_HISTORY_RELAYS})
        events = day.setdefault("events", [])
        for relay in HVAC_HISTORY_RELAYS:
            old_on = bool(old_relays.get(relay))
            new_on = bool(normalized.get(relay))
            if old_on == new_on:
                continue
            events.append({"relay": relay, "action": "on" if new_on else "off", "atMs": now_ms, "source": str(source or "thermostat")[:40]})
            if new_on:
                starts[relay] = now_ms
                cycles[relay] = max(0, int(cycles.get(relay) or 0)) + 1
            else:
                starts[relay] = None
            old_relays[relay] = new_on
        day["updatedAtMs"] = now_ms


def _refresh_hvac_history_now() -> None:
    relays = dict(_HVAC_HISTORY_LAST_RELAYS)
    source = str(_HVAC_HISTORY_LAST_SOURCE or "thermostat")
    _record_hvac_history(relays, source)


def _hvac_history_periods(day: dict, now_ms: int) -> list[dict]:
    active: dict[str, dict] = {}
    periods: list[dict] = []
    events = day.get("events") if isinstance(day.get("events"), list) else []
    for event in events:
        if not isinstance(event, dict):
            continue
        relay = str(event.get("relay") or "").lower()
        action = str(event.get("action") or "").lower()
        if relay not in HVAC_HISTORY_RELAYS or action not in {"on", "off"}:
            continue
        at_ms = int(event.get("atMs") or 0)
        if action == "on":
            active[relay] = {"relay": relay, "startMs": at_ms, "source": event.get("source") or "thermostat"}
        elif relay in active:
            start = active.pop(relay)
            periods.append({
                "relay": relay,
                "startMs": int(start.get("startMs") or at_ms),
                "endMs": at_ms,
                "durationMs": max(0, at_ms - int(start.get("startMs") or at_ms)),
                "source": start.get("source") or event.get("source") or "thermostat",
                "ongoing": False,
            })
    for relay, start in active.items():
        start_ms = int(start.get("startMs") or now_ms)
        periods.append({
            "relay": relay,
            "startMs": start_ms,
            "endMs": now_ms,
            "durationMs": max(0, now_ms - start_ms),
            "source": start.get("source") or "thermostat",
            "ongoing": True,
        })
    periods.sort(key=lambda item: (int(item.get("startMs") or 0), item.get("relay") or ""), reverse=True)
    return periods[:200]


def _hvac_history_payload(date_key: str | None = None) -> dict:
    _refresh_hvac_history_now()
    now_ms = int(time.time() * 1000)
    today = _date_key_from_ms(now_ms)
    requested = str(date_key or today).strip()[:10]
    if len(requested) != 10:
        requested = today
    with _HVAC_HISTORY_LOCK:
        archive = _load_hvac_history_archive_locked()
        current = _ensure_hvac_history_current_locked(now_ms)
        if requested == str(current.get("date")):
            day = _deepcopy_json(current)
        else:
            day = _deepcopy_json((archive.get("days") or {}).get(requested) or _new_hvac_history_day(requested, now_ms))
        dates = sorted(set((archive.get("days") or {}).keys()) | {str(current.get("date"))})
    totals = day.get("totalsMs") if isinstance(day.get("totalsMs"), dict) else {}
    cycles = day.get("cycles") if isinstance(day.get("cycles"), dict) else {}
    relays = day.get("relays") if isinstance(day.get("relays"), dict) else {}
    return {
        "ok": True,
        "date": requested,
        "today": today,
        "availableDates": dates,
        "summary": {
            relay: {
                "totalMs": max(0, int(totals.get(relay) or 0)),
                "cycles": max(0, int(cycles.get(relay) or 0)),
                "active": requested == today and bool(relays.get(relay)),
            }
            for relay in HVAC_HISTORY_RELAYS
        },
        "periods": _hvac_history_periods(day, now_ms),
        "updatedAtMs": int(day.get("updatedAtMs") or now_ms),
        "writePolicy": "Active-day history is held in RAM. By default, the SD card is written only when the day rolls over; clean shutdown snapshots are disabled. Set SMART_THERMOSTAT_HISTORY_SAVE_ON_SHUTDOWN=1 to save a final shutdown snapshot, or SMART_THERMOSTAT_HISTORY_PERSISTENCE=ram to keep history RAM-only.",
    }


def _hvac_history_dates_payload() -> dict:
    _refresh_hvac_history_now()
    with _HVAC_HISTORY_LOCK:
        archive = _load_hvac_history_archive_locked()
        current = _ensure_hvac_history_current_locked(int(time.time() * 1000))
        dates = sorted(set((archive.get("days") or {}).keys()) | {str(current.get("date"))})
    return {"ok": True, "dates": dates, "today": _date_key_from_ms()}


def _flush_hvac_history_to_disk() -> None:
    if not HISTORY_SAVE_ON_SHUTDOWN:
        return
    try:
        _refresh_hvac_history_now()
        with _HVAC_HISTORY_LOCK:
            _save_hvac_history_archive_locked(include_current=True)
    except Exception as exc:
        print(f"Unable to flush HVAC history: {exc}")


def _panel_config_payload() -> dict:
    record = _read_panel_config_record()
    return {
        "ok": True,
        "version": record["version"],
        "updatedAt": record["updatedAt"],
        "exists": isinstance(record.get("config"), dict),
        "config": record.get("config"),
    }


def _config_backup_filename() -> str:
    thermostat = _read_thermostat_record()["thermostat"]
    raw_name = str(thermostat.get("name") or "smart-thermostat").strip().lower()
    safe_name = "".join(ch if ch.isalnum() else "-" for ch in raw_name).strip("-") or "smart-thermostat"
    while "--" in safe_name:
        safe_name = safe_name.replace("--", "-")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{safe_name}-config-{timestamp}.json"


def _config_export_payload(server_port: int | str | None = None) -> dict:
    _flush_thermostat_state_to_disk()
    panel_record = _read_panel_config_record()
    thermostat_record = _thermostat_record_for_disk(_read_thermostat_record())
    exported_at = int(time.time())
    return {
        "ok": True,
        "exportType": "smart-thermostat-config-backup",
        "exportVersion": 1,
        "exportedAt": exported_at,
        "exportedAtIso": datetime.now().astimezone().isoformat(timespec="seconds"),
        "appVersion": _read_version_value(),
        "system": _system_info_payload(server_port),
        "records": {
            "panelConfig": panel_record,
            "thermostatState": thermostat_record,
        },
        # Convenience copies make the file easy to inspect or import by hand.
        "config": panel_record.get("config") or {},
        "thermostat": thermostat_record.get("thermostat") or {},
    }


def _send_json_download(handler: BaseHTTPRequestHandler, filename: str, payload: dict) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    safe_filename = filename.replace('"', "").replace("\r", "").replace("\n", "") or "smart-thermostat-config.json"
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Disposition", f'attachment; filename="{safe_filename}"')
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    handler.wfile.write(body)


def _send_html(handler: BaseHTTPRequestHandler, body: str) -> None:
    raw = body.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    handler.wfile.write(raw)


def _config_portal_url(server_port: int | str | None = None) -> str:
    info = _system_info_payload(server_port)
    ip = str(info.get("ipAddress") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(info.get("port") or server_port or 8080)
    except (TypeError, ValueError):
        port = 8080
    return f"http://{ip}:{port}"


def _config_web_portal_activate() -> dict:
    now = time.monotonic()
    with _CONFIG_WEB_PORTAL_LOCK:
        global _CONFIG_WEB_PORTAL_ENABLED_UNTIL, _CONFIG_WEB_PORTAL_STARTED_AT, _CONFIG_WEB_PORTAL_LAST_ACTIVITY_AT
        _CONFIG_WEB_PORTAL_STARTED_AT = now
        _CONFIG_WEB_PORTAL_LAST_ACTIVITY_AT = now
        _CONFIG_WEB_PORTAL_ENABLED_UNTIL = now + CONFIG_WEB_PORTAL_TIMEOUT_SECONDS
        return {
            "active": True,
            "timeoutSeconds": int(CONFIG_WEB_PORTAL_TIMEOUT_SECONDS),
        }


def _config_web_portal_active(touch: bool = False) -> bool:
    now = time.monotonic()
    with _CONFIG_WEB_PORTAL_LOCK:
        global _CONFIG_WEB_PORTAL_ENABLED_UNTIL, _CONFIG_WEB_PORTAL_LAST_ACTIVITY_AT
        if _CONFIG_WEB_PORTAL_ENABLED_UNTIL <= now:
            _CONFIG_WEB_PORTAL_ENABLED_UNTIL = 0.0
            return False
        if touch:
            _CONFIG_WEB_PORTAL_LAST_ACTIVITY_AT = now
            _CONFIG_WEB_PORTAL_ENABLED_UNTIL = now + CONFIG_WEB_PORTAL_TIMEOUT_SECONDS
        return True


def _config_web_portal_stop_payload() -> dict:
    with _CONFIG_WEB_PORTAL_LOCK:
        global _CONFIG_WEB_PORTAL_ENABLED_UNTIL, _CONFIG_WEB_PORTAL_STARTED_AT, _CONFIG_WEB_PORTAL_LAST_ACTIVITY_AT
        was_active = _CONFIG_WEB_PORTAL_ENABLED_UNTIL > time.monotonic()
        _CONFIG_WEB_PORTAL_ENABLED_UNTIL = 0.0
        _CONFIG_WEB_PORTAL_STARTED_AT = 0.0
        _CONFIG_WEB_PORTAL_LAST_ACTIVITY_AT = 0.0
    return {
        "ok": True,
        "active": False,
        "wasActive": was_active,
        "message": "Config backup portal stopped.",
    }


def _config_transfer_closed_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Config Backup Closed</title>
  <style>
    :root { color-scheme: dark; font-family: Arial, Helvetica, sans-serif; }
    body { margin:0; min-height:100vh; display:grid; place-items:center; color:#f7fbff; background:linear-gradient(135deg,#071222,#101d35,#050913); }
    .card { width:min(680px, calc(100% - 36px)); border:1px solid rgba(255,255,255,.14); border-radius:28px; padding:28px; background:rgba(255,255,255,.075); box-shadow:0 18px 55px rgba(0,0,0,.28); }
    .eyebrow { color:#46e8ff; font-size:12px; font-weight:900; letter-spacing:4px; text-transform:uppercase; }
    h1 { margin:8px 0 10px; font-size:clamp(32px,5vw,54px); line-height:.95; }
    p { color:#a8b6cf; line-height:1.5; font-size:17px; }
  </style>
</head>
<body>
  <main class="card">
    <div class="eyebrow">Smart Thermostat</div>
    <h1>Config backup is closed</h1>
    <p>Open Backup Config on the thermostat panel to temporarily enable this page.</p>
  </main>
</body>
</html>"""


def _config_web_portal_payload(server_port: int | str | None = None) -> dict:
    info = _system_info_payload(server_port)
    url = _config_portal_url(server_port)
    portal = _config_web_portal_activate()
    return {
        "ok": True,
        "message": "Config backup portal is ready.",
        "url": url,
        "ipAddress": info.get("ipAddress"),
        "port": info.get("port"),
        "address": info.get("address"),
        "thermostatName": info.get("thermostatName") or info.get("name"),
        "note": "Open this address from a computer on the same network to download or upload the thermostat config. Keep the panel popup open while transferring; closing it stops the backup portal.",
        "resourceMode": "temporary-root-backup-route",
        "active": portal.get("active"),
        "timeoutSeconds": portal.get("timeoutSeconds"),
    }



def _settings_entity_id(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("entityId") or value.get("entity_id")
    return str(value or "").strip()


def _settings_entity_from_item(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    entity_id = _settings_entity_id(item)
    if not entity_id or "." not in entity_id:
        return None
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    domain = str(item.get("domain") or entity_id.split(".", 1)[0]).strip().lower()
    name = str(
        item.get("name")
        or item.get("friendlyName")
        or item.get("friendly_name")
        or attributes.get("friendly_name")
        or entity_id
    ).strip() or entity_id
    unit = str(
        item.get("unitOfMeasurement")
        or item.get("unit_of_measurement")
        or attributes.get("unit_of_measurement")
        or ""
    ).strip()
    payload = {
        "entityId": entity_id,
        "name": name[:160],
        "domain": domain,
        "state": item.get("state"),
        "unitOfMeasurement": unit[:32],
    }
    # Preserve the IHA peer metadata used by the existing Sync path. Generic
    # sensor/door/person fields stay small, while selected thermostat peers keep
    # their direct panel route and capability markers.
    for key in ("serial", "panelUrl", "panel_url", "syncCapable", "ihaPanel", "available", "away", "doorPauseActive"):
        if key in item:
            payload[key] = item.get(key)
    return payload


def _settings_entity_catalog() -> tuple[dict[str, list[dict]], dict[str, dict], list[str]]:
    """Build a token-free entity catalog for the temporary settings portal."""
    record = _read_panel_config_record()
    config = record.get("config") if isinstance(record, dict) else {}
    integrations = config.get("integrations") if isinstance(config, dict) else {}
    ha = integrations.get("homeAssistant") if isinstance(integrations, dict) else {}
    ha = ha if isinstance(ha, dict) else {}
    thermostat = _read_thermostat_record().get("thermostat") or {}
    warnings: list[str] = []
    by_id: dict[str, dict] = {}
    sync_allowed: set[str] = set()

    def add(item: object) -> None:
        clean = _settings_entity_from_item(item)
        if clean:
            by_id[clean["entityId"]] = clean

    # Keep every currently selected or previously discovered option available,
    # even if Home Assistant is temporarily offline while the portal is open.
    for key in (
        "currentTempEntity", "outdoorTempEntity", "weatherEntity",
        "externalHeatControlEntity", "externalCoolControlEntity", "externalFanControlEntity",
        "doorEntity",
    ):
        add(ha.get(key))
    for key in (
        "currentTempAvailableEntities", "weatherAvailableEntities",
        "externalAirControlAvailableEntities", "doorAvailableEntities",
        "pauseFunctionAvailableEntities", "personAvailableEntities",
    ):
        values = ha.get(key)
        if isinstance(values, list):
            for item in values:
                add(item)
    for key in ("syncThermostatEntities", "syncAvailableThermostatEntities"):
        values = ha.get(key)
        if isinstance(values, list):
            for item in values:
                add(item)
                entity_id = _settings_entity_id(item)
                if entity_id:
                    sync_allowed.add(entity_id)
    for key in ("autoAwayPeople", "people"):
        values = thermostat.get(key) if isinstance(thermostat, dict) else []
        if isinstance(values, list):
            for item in values:
                add(item)
    for key in ("externalHeatEntity", "externalCoolEntity", "externalFanEntity"):
        add(thermostat.get(key) if isinstance(thermostat, dict) else None)
    pause = thermostat.get("pauseFunction") if isinstance(thermostat, dict) else {}
    if isinstance(pause, dict) and isinstance(pause.get("entries"), list):
        for item in pause.get("entries") or []:
            add(item)

    ha_url, token = _ha_credentials_from_panel_config()
    if ha_url and token:
        try:
            for item in _ha_all_states_cached(ha_url, token):
                add(item)
        except Exception as exc:
            warnings.append(f"Home Assistant entities could not be refreshed: {exc}")
        try:
            saved_sync = ha.get("syncThermostatEntities") if isinstance(ha.get("syncThermostatEntities"), list) else []
            local_name = str(thermostat.get("name") or "") if isinstance(thermostat, dict) else ""
            for item in _fetch_ha_sync_thermostats(ha_url, token, saved_sync, local_name):
                add(item)
                entity_id = _settings_entity_id(item)
                if entity_id:
                    sync_allowed.add(entity_id)
        except Exception as exc:
            warnings.append(f"IHA sync thermostats could not be refreshed: {exc}")
    else:
        warnings.append("Home Assistant is not configured; only saved entity selections are available.")

    groups: dict[str, list[dict]] = {
        "temperature": [],
        "outdoor": [],
        "airControls": [],
        "doors": [],
        "people": [],
        "syncThermostats": [],
    }
    for item in by_id.values():
        domain = item.get("domain")
        if domain in {"sensor", "climate"}:
            groups["temperature"].append(item)
        if domain in {"sensor", "weather"}:
            groups["outdoor"].append(item)
        if domain in {"switch", "input_boolean"}:
            groups["airControls"].append(item)
        if domain in {"binary_sensor", "cover"}:
            groups["doors"].append(item)
        if domain == "person":
            groups["people"].append(item)
        if domain == "climate" and item.get("entityId") in sync_allowed:
            groups["syncThermostats"].append(item)
    for values in groups.values():
        values.sort(key=lambda item: (str(item.get("name") or "").lower(), item.get("entityId") or ""))
    return groups, by_id, warnings


def _settings_source_mode(value: object) -> str:
    return "external" if str(value or "").strip().lower() in {"external", "home-assistant", "ha", "remote"} else "internal"


def _config_settings_current() -> dict:
    thermostat = _read_thermostat_record().get("thermostat") or {}
    thermostat = thermostat if isinstance(thermostat, dict) else {}
    record = _read_panel_config_record()
    config = record.get("config") if isinstance(record, dict) else {}
    config = config if isinstance(config, dict) else {}
    integrations = config.get("integrations") if isinstance(config.get("integrations"), dict) else {}
    ha = integrations.get("homeAssistant") if isinstance(integrations.get("homeAssistant"), dict) else {}
    display = config.get("display") if isinstance(config.get("display"), dict) else {}
    alarm = config.get("alarm") if isinstance(config.get("alarm"), dict) else {}
    security = config.get("security") if isinstance(config.get("security"), dict) else {}
    limits = thermostat.get("limits") if isinstance(thermostat.get("limits"), dict) else {}
    cool_limits = limits.get("cool") if isinstance(limits.get("cool"), dict) else {}
    heat_limits = limits.get("heat") if isinstance(limits.get("heat"), dict) else {}
    pause = thermostat.get("pauseFunction") if isinstance(thermostat.get("pauseFunction"), dict) else {}
    pause_entries = pause.get("entries") if isinstance(pause.get("entries"), list) else []

    def first_entity(value: object) -> str:
        return _settings_entity_id(value)

    return {
        "name": str(thermostat.get("name") or "IHA Thermostat"),
        "fan": str(thermostat.get("fan") or "auto"),
        "screenOrientation": str(display.get("screenOrientation") or "upright"),
        "awayHeat": thermostat.get("awayHeat", 55),
        "awayCool": thermostat.get("awayCool", 85),
        "autoAwayPersonIds": [first_entity(x) for x in (thermostat.get("autoAwayPeople") or []) if first_entity(x)],
        "coolMin": cool_limits.get("min", 65),
        "coolMax": cool_limits.get("max", 80),
        "heatMin": heat_limits.get("min", 60),
        "heatMax": heat_limits.get("max", 78),
        "safetyLow": thermostat.get("safetyLow", 55),
        "safetyHigh": thermostat.get("safetyHigh", 85),
        "autoCoolOutdoorTarget": thermostat.get("autoCoolOutdoorTarget", 70),
        "autoHeatOutdoorTarget": thermostat.get("autoHeatOutdoorTarget", 65),
        "heatLocked": bool(thermostat.get("heatLocked")),
        "coolLocked": bool(thermostat.get("coolLocked")),
        "autoChangeoverHours": round(float(thermostat.get("autoChangeoverLockoutMinutes", 120) or 0) / 60.0, 2),
        "manualChangeoverMinutes": thermostat.get("manualChangeoverLockoutMinutes", 10),
        "coolFanRemainOnMinutes": thermostat.get("coolFanRemainOnMinutes", 2),
        "temperatureDifferential": thermostat.get("temperatureDifferential", 0),
        "heatMinimumRuntimeMinutes": thermostat.get("heatMinimumRuntimeMinutes", 2),
        "coolMinimumRuntimeMinutes": thermostat.get("coolMinimumRuntimeMinutes", 2),
        "roomTempControlMode": _settings_source_mode(thermostat.get("roomTempControlMode")),
        "heatControlMode": _settings_source_mode(thermostat.get("heatControlMode", thermostat.get("airControlMode"))),
        "coolControlMode": _settings_source_mode(thermostat.get("coolControlMode", thermostat.get("airControlMode"))),
        "fanControlMode": _settings_source_mode(thermostat.get("fanControlMode")),
        "currentTempEntityId": first_entity(ha.get("currentTempEntity")),
        "externalHeatEntityId": first_entity(thermostat.get("externalHeatEntity") or ha.get("externalHeatControlEntity")),
        "externalCoolEntityId": first_entity(thermostat.get("externalCoolEntity") or ha.get("externalCoolControlEntity")),
        "externalFanEntityId": first_entity(thermostat.get("externalFanEntity") or ha.get("externalFanControlEntity")),
        "outdoorTempEntityId": first_entity(ha.get("outdoorTempEntity") or ha.get("weatherEntity")),
        "syncThermostatIds": [first_entity(x) for x in (ha.get("syncThermostatEntities") or []) if first_entity(x)],
        "trackedPersonIds": [first_entity(x) for x in (thermostat.get("people") or []) if first_entity(x)],
        "doorEntityId": first_entity(pause_entries[0] if pause_entries else ha.get("doorEntity")),
        "doorPauseDurationMinutes": pause.get("durationMinutes", 5),
        "disarmCode": str(alarm.get("disarmCode") or ""),
        "settingsCode": str(security.get("settingsCode") or ""),
    }


def _config_settings_payload() -> dict:
    groups, _lookup, warnings = _settings_entity_catalog()
    return {
        "ok": True,
        "settings": _config_settings_current(),
        "entities": groups,
        "warnings": warnings,
    }


def _settings_number(payload: dict, key: str, default: float, low: float, high: float, *, integer: bool = True) -> int | float:
    raw = payload.get(key, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    value = max(low, min(high, value))
    return int(round(value)) if integer else round(value, 2)


def _settings_code(payload: dict, key: str, current: str) -> str:
    value = str(payload.get(key, current) or "").strip()
    if value and not re.fullmatch(r"\d{4}", value):
        raise ValueError(f"{key} must be blank or exactly four digits")
    return value


def _settings_selected_ids(payload: dict, key: str, domain: str) -> list[str]:
    values = payload.get(key, [])
    if not isinstance(values, list):
        raise ValueError(f"{key} must be a list")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        entity_id = str(value or "").strip()
        if not entity_id:
            continue
        if not entity_id.startswith(domain + "."):
            raise ValueError(f"{key} contains an invalid {domain} entity")
        if entity_id not in seen:
            seen.add(entity_id)
            result.append(entity_id)
    return result


def _settings_entity_for_id(entity_id: str, allowed_domains: set[str], lookup: dict[str, dict]) -> dict | None:
    entity_id = str(entity_id or "").strip()
    if not entity_id:
        return None
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    if domain not in allowed_domains:
        raise ValueError(f"{entity_id} is not an allowed {'/'.join(sorted(allowed_domains))} entity")
    existing = lookup.get(entity_id)
    if existing:
        return _deepcopy_json(existing)
    return {"entityId": entity_id, "name": entity_id, "domain": domain, "state": "unknown", "unitOfMeasurement": ""}


def _settings_prepend_entity(existing: object, selected: dict | None) -> list[dict]:
    clean: list[dict] = []
    seen: set[str] = set()
    for item in ([selected] if selected else []) + (existing if isinstance(existing, list) else []):
        normalized = _settings_entity_from_item(item)
        if not normalized:
            continue
        entity_id = normalized["entityId"]
        if entity_id in seen:
            continue
        seen.add(entity_id)
        clean.append(normalized)
    return clean


def _apply_saved_screen_orientation(orientation: str) -> tuple[bool, str]:
    orientation = "upside_down" if str(orientation or "").strip().lower() == "upside_down" else "upright"
    script = ROOT / "scripts" / "apply-screen-orientation.sh"
    if not script.exists():
        return False, "Screen orientation helper is missing; the saved orientation will apply after restart."
    env = os.environ.copy()
    env.setdefault("SMART_THERMOSTAT_DISPLAY_OUTPUT", "DSI-1")
    try:
        result = subprocess.run(
            [str(script), orientation], cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=8, check=False,
        )
    except Exception as exc:
        return False, str(exc)
    detail = (result.stdout or "").strip()
    return result.returncode == 0, detail


def _config_settings_save(payload: dict) -> dict:
    if not _config_web_portal_active(touch=True):
        return {"ok": False, "error": "The temporary config portal is closed."}
    values = payload.get("settings", payload) if isinstance(payload, dict) else {}
    if not isinstance(values, dict):
        raise ValueError("settings must be an object")

    current_thermostat = _read_thermostat_record().get("thermostat") or {}
    current_settings = _config_settings_current()
    old_record = _read_panel_config_record()
    old_config = _deepcopy_json(old_record.get("config") or {})
    config = _deepcopy_json(old_config)
    if not isinstance(config, dict):
        config = {}
    groups, lookup, warnings = _settings_entity_catalog()
    del groups

    name = str(values.get("name", current_settings.get("name") or "IHA Thermostat") or "").strip()[:80]
    if not name:
        raise ValueError("Thermostat name cannot be blank")
    fan = str(values.get("fan", current_settings.get("fan") or "auto") or "auto").strip().lower()
    if fan not in {"off", "on", "auto"}:
        raise ValueError("fan must be off, on, or auto")
    orientation = str(values.get("screenOrientation", current_settings.get("screenOrientation") or "upright") or "upright").strip().lower()
    if orientation not in {"upright", "upside_down"}:
        raise ValueError("screenOrientation must be upright or upside_down")

    cool_min = _settings_number(values, "coolMin", current_settings["coolMin"], 50, 90)
    cool_max = _settings_number(values, "coolMax", current_settings["coolMax"], 50, 90)
    heat_min = _settings_number(values, "heatMin", current_settings["heatMin"], 40, 80)
    heat_max = _settings_number(values, "heatMax", current_settings["heatMax"], 40, 85)
    if cool_min > cool_max:
        raise ValueError("Cool Low cannot be higher than Cool High")
    if heat_min > heat_max:
        raise ValueError("Heat Low cannot be higher than Heat High")

    room_mode = _settings_source_mode(values.get("roomTempControlMode", current_settings["roomTempControlMode"]))
    heat_mode = _settings_source_mode(values.get("heatControlMode", current_settings["heatControlMode"]))
    cool_mode = _settings_source_mode(values.get("coolControlMode", current_settings["coolControlMode"]))
    fan_mode = _settings_source_mode(values.get("fanControlMode", current_settings["fanControlMode"]))
    current_temp_entity = _settings_entity_for_id(values.get("currentTempEntityId", ""), {"sensor", "climate"}, lookup)
    external_heat = _settings_entity_for_id(values.get("externalHeatEntityId", ""), {"switch", "input_boolean"}, lookup)
    external_cool = _settings_entity_for_id(values.get("externalCoolEntityId", ""), {"switch", "input_boolean"}, lookup)
    external_fan = _settings_entity_for_id(values.get("externalFanEntityId", ""), {"switch", "input_boolean"}, lookup)
    if room_mode == "external" and not current_temp_entity:
        raise ValueError("Select a room temperature entity before using External room temperature")
    if heat_mode == "external" and not external_heat:
        raise ValueError("Select an external Heat entity before using External heat control")
    if cool_mode == "external" and not external_cool:
        raise ValueError("Select an external Cool entity before using External cool control")
    if fan_mode == "external" and not external_fan:
        raise ValueError("Select an external Fan entity before using External fan control")

    outdoor = _settings_entity_for_id(values.get("outdoorTempEntityId", ""), {"sensor", "weather"}, lookup)
    door = _settings_entity_for_id(values.get("doorEntityId", ""), {"binary_sensor", "cover"}, lookup)
    auto_away_ids = _settings_selected_ids(values, "autoAwayPersonIds", "person")
    tracked_ids = _settings_selected_ids(values, "trackedPersonIds", "person")
    sync_ids = _settings_selected_ids(values, "syncThermostatIds", "climate")
    auto_away_people = [_settings_entity_for_id(x, {"person"}, lookup) for x in auto_away_ids]
    tracked_people = [_settings_entity_for_id(x, {"person"}, lookup) for x in tracked_ids]
    sync_peers = [_settings_entity_for_id(x, {"climate"}, lookup) for x in sync_ids]

    integrations = config.setdefault("integrations", {})
    if not isinstance(integrations, dict):
        integrations = {}
        config["integrations"] = integrations
    ha = integrations.setdefault("homeAssistant", {})
    if not isinstance(ha, dict):
        ha = {}
        integrations["homeAssistant"] = ha
    alarm = config.setdefault("alarm", {})
    if not isinstance(alarm, dict):
        alarm = {}
        config["alarm"] = alarm
    security = config.setdefault("security", {})
    if not isinstance(security, dict):
        security = {}
        config["security"] = security
    display = config.setdefault("display", {})
    if not isinstance(display, dict):
        display = {}
        config["display"] = display

    old_orientation = str(display.get("screenOrientation") or "upright")
    display["screenOrientation"] = orientation
    display["xrandrRotation"] = "inverted" if orientation == "upside_down" else "normal"
    alarm["disarmCode"] = _settings_code(values, "disarmCode", str(alarm.get("disarmCode") or ""))
    security["settingsCode"] = _settings_code(values, "settingsCode", str(security.get("settingsCode") or ""))

    if current_temp_entity:
        ha["currentTempEntity"] = current_temp_entity
        ha["currentTempAvailableEntities"] = _settings_prepend_entity(ha.get("currentTempAvailableEntities"), current_temp_entity)
    elif room_mode == "internal":
        ha["currentTempEntity"] = None
    for selected_key, available_key, entity in (
        ("externalHeatControlEntity", "externalAirControlAvailableEntities", external_heat),
        ("externalCoolControlEntity", "externalAirControlAvailableEntities", external_cool),
        ("externalFanControlEntity", "externalAirControlAvailableEntities", external_fan),
    ):
        ha[selected_key] = entity
        ha[available_key] = _settings_prepend_entity(ha.get(available_key), entity)
    ha["outdoorTempEntity"] = outdoor
    if outdoor and outdoor.get("domain") == "weather":
        ha["weatherEntity"] = outdoor
    elif outdoor is None:
        ha["weatherEntity"] = None
    ha["weatherAvailableEntities"] = _settings_prepend_entity(ha.get("weatherAvailableEntities"), outdoor)
    ha["doorEntity"] = door
    ha["doorAvailableEntities"] = _settings_prepend_entity(ha.get("doorAvailableEntities"), door)
    ha["pauseFunctionAvailableEntities"] = _settings_prepend_entity(ha.get("pauseFunctionAvailableEntities"), door)
    ha["syncThermostatEntities"] = sync_peers
    ha["syncAvailableThermostatEntities"] = sync_peers
    person_available = ha.get("personAvailableEntities")
    for person in auto_away_people + tracked_people:
        person_available = _settings_prepend_entity(person_available, person)
    ha["personAvailableEntities"] = person_available if isinstance(person_available, list) else []

    room_source_name = (current_temp_entity or {}).get("name") or "Onboard HDC2080 Sensors"
    pause_existing = current_thermostat.get("pauseFunction") if isinstance(current_thermostat.get("pauseFunction"), dict) else {}
    thermostat_changes = {
        "name": name,
        "fan": fan,
        "awayHeat": _settings_number(values, "awayHeat", current_settings["awayHeat"], 40, 75),
        "awayCool": _settings_number(values, "awayCool", current_settings["awayCool"], 75, 100),
        "autoAwayPeople": auto_away_people,
        "people": tracked_people,
        "limits": {
            "cool": {"min": cool_min, "max": cool_max},
            "heat": {"min": heat_min, "max": heat_max},
            "auto": {"min": min(cool_min, heat_min), "max": max(cool_max, heat_max)},
        },
        "safetyLow": _settings_number(values, "safetyLow", current_settings["safetyLow"], 40, 75),
        "safetyHigh": _settings_number(values, "safetyHigh", current_settings["safetyHigh"], 75, 100),
        "autoCoolOutdoorTarget": _settings_number(values, "autoCoolOutdoorTarget", current_settings["autoCoolOutdoorTarget"], 40, 100),
        "autoHeatOutdoorTarget": _settings_number(values, "autoHeatOutdoorTarget", current_settings["autoHeatOutdoorTarget"], 40, 100),
        "heatLocked": _assistant_bool(values.get("heatLocked"), current_settings["heatLocked"]),
        "coolLocked": _assistant_bool(values.get("coolLocked"), current_settings["coolLocked"]),
        "autoChangeoverLockoutMinutes": _settings_number(values, "autoChangeoverHours", current_settings["autoChangeoverHours"], 0, 8, integer=False) * 60,
        "manualChangeoverLockoutMinutes": _settings_number(values, "manualChangeoverMinutes", current_settings["manualChangeoverMinutes"], 0, 60),
        "coolFanRemainOnMinutes": _settings_number(values, "coolFanRemainOnMinutes", current_settings["coolFanRemainOnMinutes"], 0, 15),
        "temperatureDifferential": _settings_number(values, "temperatureDifferential", current_settings["temperatureDifferential"], 0, 5),
        "heatMinimumRuntimeMinutes": _settings_number(values, "heatMinimumRuntimeMinutes", current_settings["heatMinimumRuntimeMinutes"], 1, 30),
        "coolMinimumRuntimeMinutes": _settings_number(values, "coolMinimumRuntimeMinutes", current_settings["coolMinimumRuntimeMinutes"], 1, 30),
        "roomTempControlMode": room_mode,
        "heatControlMode": heat_mode,
        "coolControlMode": cool_mode,
        "fanControlMode": fan_mode,
        "airControlMode": "external" if heat_mode == "external" and cool_mode == "external" else "internal",
        "currentTempSource": "home-assistant" if room_mode == "external" else "onboard",
        "currentTempSourceName": room_source_name,
        "runtimeTempSource": "home-assistant" if room_mode == "external" else "onboard",
        "runtimeTempSourceName": room_source_name,
        "externalHeatEntity": external_heat,
        "externalCoolEntity": external_cool,
        "externalFanEntity": external_fan,
        "outdoorTempSource": "home-assistant" if outdoor else "",
        "outdoorTempSourceName": (outdoor or {}).get("name") or "",
        "pauseFunction": {
            "durationMinutes": _settings_number(values, "doorPauseDurationMinutes", current_settings["doorPauseDurationMinutes"], 1, 60),
            "entries": [door] if door else [],
            "active": False,
            "pausedAt": 0,
            "previousTargetTemp": None,
            "previousLastComfortTarget": None,
            "activeEntityIds": [],
            "snoozeUntil": 0,
        },
    }

    # Validate and commit only after every field has been normalized. If the
    # thermostat write unexpectedly fails, restore the prior config record.
    saved_config = _write_panel_config_record(config)
    try:
        thermostat_result = _handle_thermostat_update(thermostat_changes)
    except Exception:
        _write_panel_config_record(old_config)
        raise

    if orientation != old_orientation:
        ok, detail = _apply_saved_screen_orientation(orientation)
        if not ok:
            warnings.append(detail or "Screen orientation was saved and will apply after restart.")

    return {
        "ok": True,
        "message": "Thermostat settings saved. Close Backup Config when finished so the panel reloads every config-backed selection.",
        "version": saved_config.get("version"),
        "updatedAt": saved_config.get("updatedAt"),
        "settings": _config_settings_current(),
        "thermostat": thermostat_result.get("thermostat") if isinstance(thermostat_result, dict) else thermostat_result,
        "warnings": warnings,
    }


def _config_transfer_html(server_port: int | str | None = None) -> str:
    info = _system_info_payload(server_port)
    config_record = _panel_config_payload()
    name = html.escape(str(info.get("thermostatName") or info.get("name") or "Smart Thermostat"))
    version = html.escape(str(info.get("version") or "--"))
    address = html.escape(str(info.get("address") or "--"))
    uptime = html.escape(str(info.get("uptime") or "--"))
    thermal = html.escape(str(info.get("thermal") or "--"))
    host = html.escape(str(info.get("host") or "--"))
    updated = html.escape(str(config_record.get("updatedAt") or "--"))
    cfg_version = html.escape(str(config_record.get("version") or "--"))
    assistant = _assistant_config_payload()
    assistant_agent = html.escape(str(assistant.get("agentId") or ""), quote=True)
    assistant_tts = html.escape(str(assistant.get("ttsEntityId") or ""), quote=True)
    assistant_media = html.escape(str(assistant.get("mediaPlayerId") or ""), quote=True)
    assistant_effective_media = html.escape(str(assistant.get("effectiveMediaPlayerId") or "not selected"), quote=True)
    assistant_language = html.escape(str(assistant.get("language") or "en"), quote=True)
    assistant_hold = html.escape(str(assistant.get("responseHoldSeconds") or 2.0), quote=True)
    assistant_volume = int(assistant.get("announcementVolumePercent") or 45)
    assistant_enabled = "checked" if assistant.get("enabled") else ""
    assistant_speak = "checked" if assistant.get("speak") else ""
    assistant_fun = "checked" if assistant.get("funMode") else ""
    assistant_playful = "checked" if assistant.get("playfulReplies") else ""
    assistant_continue = "checked" if assistant.get("continueConversation") else ""
    assistant_show_text = "checked" if assistant.get("showResponseText") else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{name} Configuration</title>
  <style>
    :root {{ color-scheme:dark; font-family:Arial,Helvetica,sans-serif; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; color:#f7fbff; background:radial-gradient(circle at 15% 0%,rgba(70,232,255,.24),transparent 28%),linear-gradient(135deg,#071222,#101d35 52%,#050913); }}
    .wrap {{ width:min(1180px,calc(100% - 30px)); margin:0 auto; padding:28px 0 48px; }}
    .hero {{ display:flex; justify-content:space-between; gap:18px; align-items:flex-start; margin-bottom:16px; }}
    .eyebrow {{ color:#46e8ff; font-size:12px; font-weight:900; letter-spacing:4px; text-transform:uppercase; }}
    h1 {{ margin:7px 0 8px; font-size:clamp(32px,5vw,58px); line-height:.95; }}
    h2 {{ margin:6px 0 8px; font-size:28px; }}
    h3 {{ margin:0 0 14px; font-size:19px; }}
    .muted {{ color:#a8b6cf; line-height:1.45; }}
    .pill {{ border:1px solid rgba(255,255,255,.16); border-radius:999px; padding:10px 14px; color:#dce8ff; background:rgba(255,255,255,.07); white-space:nowrap; }}
    .summary-grid {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; margin:16px 0; }}
    .card {{ border:1px solid rgba(255,255,255,.13); border-radius:22px; padding:18px; background:rgba(255,255,255,.075); box-shadow:0 18px 55px rgba(0,0,0,.25); backdrop-filter:blur(14px); }}
    .label {{ color:#8fa1be; font-size:11px; font-weight:900; letter-spacing:2px; text-transform:uppercase; margin-bottom:7px; }}
    .value {{ font-size:17px; font-weight:900; overflow-wrap:anywhere; }}
    .tabs {{ display:flex; gap:8px; flex-wrap:wrap; margin:18px 0; padding:6px; border:1px solid rgba(255,255,255,.12); background:rgba(0,0,0,.18); border-radius:18px; }}
    .tab-button {{ min-height:46px; border-radius:13px; padding:0 20px; background:transparent; border:1px solid transparent; box-shadow:none; color:#b9c8de; }}
    .tab-button.active {{ color:white; background:linear-gradient(135deg,#14b8ff,#7c3aed); box-shadow:0 10px 28px rgba(20,184,255,.20); }}
    .tab-panel {{ display:none; }} .tab-panel.active {{ display:block; }}
    .actions {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .action-card {{ min-height:230px; display:flex; flex-direction:column; gap:12px; }}
    button,.button {{ border:0; border-radius:16px; min-height:52px; padding:0 20px; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; font-weight:1000; font-size:15px; color:white; cursor:pointer; background:linear-gradient(135deg,#14b8ff,#7c3aed); box-shadow:0 12px 36px rgba(20,184,255,.22); }}
    button.secondary {{ background:rgba(255,255,255,.10); box-shadow:none; border:1px solid rgba(255,255,255,.15); }}
    button:disabled {{ opacity:.55; cursor:wait; }}
    input[type=file] {{ width:100%; padding:14px; border-radius:15px; color:#dce8ff; border:1px dashed rgba(255,255,255,.28); background:rgba(0,0,0,.18); }}
    input[type=text],input[type=number],input[type=password],select {{ width:100%; min-height:46px; border-radius:13px; border:1px solid rgba(255,255,255,.16); background:#0a1425; color:#f7fbff; padding:0 12px; font-size:14px; }}
    select[multiple] {{ min-height:170px; padding:8px; }}
    input:focus,select:focus {{ outline:2px solid rgba(70,232,255,.45); border-color:#46e8ff; }}
    .form-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:13px; }}
    .field label {{ display:block; color:#b8c6da; font-size:12px; font-weight:900; letter-spacing:.5px; margin:0 0 7px; }}
    .hint {{ display:block; margin-top:7px; color:#8294ae; font-size:12px; line-height:1.35; }}
    .section-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin-top:14px; }}
    .settings-section {{ border:1px solid rgba(255,255,255,.11); border-radius:18px; padding:16px; background:rgba(0,0,0,.16); }}
    .full {{ grid-column:1/-1; }}
    .checks {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:14px 0; }}
    .check {{ display:flex; gap:9px; align-items:center; min-height:44px; padding:10px 12px; border-radius:13px; border:1px solid rgba(255,255,255,.12); background:rgba(0,0,0,.18); color:#dce8ff; font-weight:800; }}
    .check input {{ width:18px; height:18px; accent-color:#46e8ff; }}
    .slider-row {{ display:grid; grid-template-columns:1fr 76px; gap:12px; align-items:center; }}
    input[type=range] {{ width:100%; accent-color:#46e8ff; }}
    .test-row {{ display:grid; grid-template-columns:1fr auto; gap:10px; margin-top:14px; }}
    .button-row {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
    code {{ color:#8ff4ff; }}
    .status {{ min-height:48px; padding:12px 14px; border-radius:15px; color:#e9f4ff; background:rgba(0,0,0,.20); border:1px solid rgba(255,255,255,.10); white-space:pre-wrap; margin-top:12px; }}
    .ok {{ color:#76ffc4; }} .bad {{ color:#ff8a8a; }}
    @media(max-width:900px) {{ .summary-grid {{ grid-template-columns:repeat(3,1fr); }} .section-grid {{ grid-template-columns:1fr; }} .full {{ grid-column:auto; }} }}
    @media(max-width:720px) {{ .hero,.actions {{ display:grid; grid-template-columns:1fr; }} .form-grid {{ grid-template-columns:1fr; }} .checks {{ grid-template-columns:1fr 1fr; }} .pill {{ white-space:normal; }} }}
    @media(max-width:480px) {{ .summary-grid {{ grid-template-columns:1fr 1fr; }} .checks {{ grid-template-columns:1fr; }} .test-row {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
<main class="wrap">
  <section class="hero">
    <div><div class="eyebrow">Smart Thermostat Configuration</div><h1>{name}</h1><div class="muted">The portal is temporary and closes when Backup Config is closed on the thermostat.</div></div>
    <div class="pill">{address}</div>
  </section>
  <section class="summary-grid">
    <div class="card"><div class="label">Version</div><div class="value">{version}</div></div>
    <div class="card"><div class="label">Host</div><div class="value">{host}</div></div>
    <div class="card"><div class="label">Thermal</div><div class="value">{thermal}</div></div>
    <div class="card"><div class="label">Uptime</div><div class="value">{uptime}</div></div>
    <div class="card"><div class="label">Config Version</div><div class="value">{cfg_version}</div></div>
    <div class="card"><div class="label">Updated</div><div class="value">{updated}</div></div>
  </section>
  <nav class="tabs" aria-label="Configuration tabs">
    <button class="tab-button active" data-tab="backup" type="button">Backup &amp; Restore</button>
    <button class="tab-button" data-tab="thermostat" type="button">Thermostat Settings</button>
    <button class="tab-button" data-tab="jarvis" type="button">JARVIS Voice</button>
  </nav>

  <section id="tab-backup" class="tab-panel active">
    <div class="actions">
      <div class="card action-card"><div class="label">Download</div><div class="value">Save this panel's full config</div><p class="muted">The backup remains compatible with the existing restore process.</p><a class="button" href="/api/system/config-export">Download Config</a></div>
      <div class="card action-card"><div class="label">Upload</div><div class="value">Restore from a config file</div><input id="file" type="file" accept="application/json,.json" /><button id="upload" type="button">Upload Config</button><div id="status" class="status muted">Choose a Smart Thermostat config backup, then press Upload Config.</div></div>
    </div>
  </section>

  <section id="tab-thermostat" class="tab-panel">
    <div class="card">
      <div class="eyebrow">Comfort Setup</div><h2>Thermostat Settings</h2>
      <p class="muted">These are the same thermostat settings available on the wall panel. Saving uses the existing thermostat control path and does not replace unrelated configuration.</p>
      <div id="thermostat-sections" class="section-grid">
        <div class="settings-section"><h3>Thermostat Unit</h3><div class="form-grid"><div class="field"><label>Thermostat name</label><input id="ts-name" type="text" maxlength="80"></div><div class="field"><label>Fan mode</label><select id="ts-fan"><option value="auto">Auto</option><option value="on">On</option><option value="off">Off</option></select></div><div class="field full"><label>Screen rotation</label><select id="ts-orientation"><option value="upright">Upright</option><option value="upside_down">Upside Down</option></select></div></div></div>
        <div class="settings-section"><h3>Auto Away / Home</h3><div class="form-grid"><div class="field"><label>Heat Away</label><input id="ts-away-heat" type="number" min="40" max="75" step="1"></div><div class="field"><label>Cool Away</label><input id="ts-away-cool" type="number" min="75" max="100" step="1"></div><div class="field full"><label>Auto Away users</label><select id="ts-auto-away-people" multiple></select><span class="hint">Hold Ctrl while clicking to select more than one person.</span></div></div></div>
        <div class="settings-section"><h3>Range</h3><div class="form-grid"><div class="field"><label>Cool Low</label><input id="ts-cool-min" type="number" min="50" max="90"></div><div class="field"><label>Cool High</label><input id="ts-cool-max" type="number" min="50" max="90"></div><div class="field"><label>Heat Low</label><input id="ts-heat-min" type="number" min="40" max="80"></div><div class="field"><label>Heat High</label><input id="ts-heat-max" type="number" min="40" max="85"></div></div></div>
        <div class="settings-section"><h3>Safety / Mode Switches</h3><div class="form-grid"><div class="field"><label>Low Safety</label><input id="ts-safety-low" type="number" min="40" max="75"></div><div class="field"><label>High Safety</label><input id="ts-safety-high" type="number" min="75" max="100"></div><div class="field"><label>Cool Mode Switch</label><input id="ts-cool-switch" type="number" min="40" max="100"></div><div class="field"><label>Heat Mode Switch</label><input id="ts-heat-switch" type="number" min="40" max="100"></div></div><div class="checks"><label class="check"><input id="ts-heat-lock" type="checkbox">Heat lockout</label><label class="check"><input id="ts-cool-lock" type="checkbox">Cool lockout</label></div></div>
        <div class="settings-section"><h3>Changeover / Fan</h3><div class="form-grid"><div class="field"><label>Auto delay (hours)</label><input id="ts-auto-delay" type="number" min="0" max="8" step="1"></div><div class="field"><label>Manual delay (minutes)</label><input id="ts-manual-delay" type="number" min="0" max="60"></div><div class="field full"><label>Cool fan remain on (minutes)</label><input id="ts-cool-fan" type="number" min="0" max="15"></div></div></div>
        <div class="settings-section"><h3>Differential / Minimum Runtime</h3><div class="form-grid"><div class="field"><label>Temperature differential</label><input id="ts-differential" type="number" min="0" max="5"></div><div class="field"><label>Heat minimum runtime (minutes)</label><input id="ts-heat-runtime" type="number" min="1" max="30"></div><div class="field"><label>Cool minimum runtime (minutes)</label><input id="ts-cool-runtime" type="number" min="1" max="30"></div></div></div>
        <div class="settings-section full"><h3>Internal / External Sources</h3><p class="muted">Each source remains independent, matching the wall-panel settings.</p><div class="form-grid">
          <div class="field"><label>Room temperature source</label><select id="ts-room-mode"><option value="internal">Internal</option><option value="external">External</option></select></div><div class="field"><label>Room temperature HA entity</label><input id="ts-room-entity" type="text" list="temperature-entities" placeholder="sensor... or climate..."></div>
          <div class="field"><label>Heat control source</label><select id="ts-heat-mode"><option value="internal">Internal</option><option value="external">External</option></select></div><div class="field"><label>External Heat entity</label><input id="ts-heat-entity" type="text" list="air-control-entities" placeholder="switch... or input_boolean..."></div>
          <div class="field"><label>Cool control source</label><select id="ts-cool-mode"><option value="internal">Internal</option><option value="external">External</option></select></div><div class="field"><label>External Cool entity</label><input id="ts-cool-entity" type="text" list="air-control-entities"></div>
          <div class="field"><label>Fan control source</label><select id="ts-fan-mode"><option value="internal">Internal</option><option value="external">External</option></select></div><div class="field"><label>External Fan entity</label><input id="ts-fan-entity" type="text" list="air-control-entities"></div>
        </div></div>
        <div class="settings-section"><h3>Outside Temperature</h3><div class="field"><label>Home Assistant sensor/weather entity</label><input id="ts-outdoor-entity" type="text" list="outdoor-entities" placeholder="sensor... or weather..."></div></div>
        <div class="settings-section"><h3>Sync</h3><div class="field"><label>Thermostats to sync</label><select id="ts-sync" multiple></select><span class="hint">Sync remains inactive until the main thermostat Sync button is armed.</span></div></div>
        <div class="settings-section"><h3>Person Tracking</h3><div class="field"><label>People shown on the main screen</label><select id="ts-tracked-people" multiple></select></div></div>
        <div class="settings-section"><h3>Doors / Comfort Pause</h3><div class="form-grid"><div class="field"><label>Door/contact/cover entity</label><input id="ts-door-entity" type="text" list="door-entities"></div><div class="field"><label>Door delay (minutes)</label><input id="ts-door-delay" type="number" min="1" max="60"></div></div></div>
        <div class="settings-section"><h3>Security Codes</h3><div class="form-grid"><div class="field"><label>Alarm disarm code</label><input id="ts-disarm-code" type="password" inputmode="numeric" maxlength="4"></div><div class="field"><label>Settings access code</label><input id="ts-settings-code" type="password" inputmode="numeric" maxlength="4"></div></div></div>
      </div>
      <datalist id="temperature-entities"></datalist><datalist id="outdoor-entities"></datalist><datalist id="air-control-entities"></datalist><datalist id="door-entities"></datalist>
      <div class="button-row"><button id="ts-save" type="button">Save Thermostat Settings</button><button id="ts-reload" type="button" class="secondary">Reload Values</button></div>
      <div id="ts-status" class="status muted">Open this tab to load current thermostat values and Home Assistant choices.</div>
    </div>
  </section>

  <section id="tab-jarvis" class="tab-panel">
    <div class="card">
      <div class="eyebrow">JARVIS Voice</div><h2>Assistant &amp; Sonos</h2>
      <p class="muted">The selected announcement volume is remembered. On Sonos, the response is sent as an announcement so the previous music and volume return automatically when speech finishes.</p>
      <div class="checks"><label class="check"><input id="va-enabled" type="checkbox" {assistant_enabled}>Assistant enabled</label><label class="check"><input id="va-speak" type="checkbox" {assistant_speak}>Speak on Sonos</label><label class="check"><input id="va-fun" type="checkbox" {assistant_fun}>Goofy screen quips</label><label class="check"><input id="va-playful" type="checkbox" {assistant_playful}>Playful spoken prefix</label><label class="check"><input id="va-continue" type="checkbox" {assistant_continue}>Continue conversation</label><label class="check"><input id="va-show-text" type="checkbox" {assistant_show_text}>Show response text</label></div>
      <div class="form-grid"><div class="field"><label>Conversation agent entity</label><input id="va-agent" type="text" value="{assistant_agent}" placeholder="conversation.openai_conversation or blank"></div><div class="field"><label>Text-to-speech entity</label><input id="va-tts" type="text" value="{assistant_tts}" placeholder="tts.openai_tts or blank"></div><div class="field"><label>Sonos / media player entity</label><input id="va-media" type="text" value="{assistant_media}" placeholder="Blank follows Audio page selection"><span class="hint">Current effective output: <code>{assistant_effective_media}</code></span></div><div class="field"><label>Language</label><input id="va-language" type="text" value="{assistant_language}" placeholder="en"></div><div class="field"><label>Extra screen hold after response (seconds)</label><input id="va-hold" type="number" min="0" max="15" step="0.5" value="{assistant_hold}"></div><div class="field"><label>Speech volume</label><div class="slider-row"><input id="va-volume" type="range" min="1" max="100" step="1" value="{assistant_volume}"><output id="va-volume-value">{assistant_volume}%</output></div><span class="hint">This volume applies only to the JARVIS announcement. Sonos restores its previous volume afterward.</span></div></div>
      <div class="button-row"><button id="va-save" type="button">Save JARVIS Settings</button></div>
      <div class="test-row"><input id="va-test-text" type="text" value="Tell me the current thermostat temperature in one short sentence."><button id="va-test" type="button" class="secondary">Run Test</button></div>
      <div id="va-status" class="status muted">Save the settings, then run a typed test.</div>
    </div>
  </section>
</main>
<script>
const qs = (id) => document.getElementById(id);
function setBox(box,text,kind) {{ box.textContent=text; box.className='status '+(kind||'muted'); }}
for (const button of document.querySelectorAll('.tab-button')) {{ button.addEventListener('click',()=>{{ document.querySelectorAll('.tab-button').forEach(x=>x.classList.toggle('active',x===button)); document.querySelectorAll('.tab-panel').forEach(x=>x.classList.toggle('active',x.id==='tab-'+button.dataset.tab)); if(button.dataset.tab==='thermostat'&&!window.thermostatSettingsLoaded) loadThermostatSettings(); }}); }}

qs('upload').addEventListener('click',async()=>{{ const file=qs('file').files&&qs('file').files[0]; if(!file){{setBox(qs('status'),'Select a config JSON file first.','bad');return;}} try{{setBox(qs('status'),'Uploading config...','muted'); const payload=JSON.parse(await file.text()); const response=await fetch('/api/system/config-import',{{method:'POST',headers:{{'Accept':'application/json','Content-Type':'application/json'}},body:JSON.stringify(payload)}}); const data=await response.json().catch(()=>({{}})); if(!response.ok||!data.ok)throw new Error(data.error||data.message||'Upload failed.'); setBox(qs('status'),data.message||'Config uploaded.','ok');}}catch(err){{setBox(qs('status'),err.message||String(err),'bad');}} }});

function value(id) {{ return qs(id).value; }} function numberValue(id) {{ return Number(qs(id).value); }}
function selectedValues(id) {{ return Array.from(qs(id).selectedOptions).map(x=>x.value); }}
function fillDatalist(id,items) {{ qs(id).replaceChildren(...items.map(item=>{{const option=document.createElement('option');option.value=item.entityId;option.label=(item.name||item.entityId)+' — '+item.entityId;return option;}})); }}
function fillMulti(id,items,selected) {{ const chosen=new Set(selected||[]); qs(id).replaceChildren(...items.map(item=>{{const option=document.createElement('option');option.value=item.entityId;option.textContent=(item.name||item.entityId)+' — '+item.entityId;option.selected=chosen.has(item.entityId);return option;}})); }}
function setThermostatForm(s,e) {{
  qs('ts-name').value=s.name||''; qs('ts-fan').value=s.fan||'auto'; qs('ts-orientation').value=s.screenOrientation||'upright'; qs('ts-away-heat').value=s.awayHeat; qs('ts-away-cool').value=s.awayCool;
  qs('ts-cool-min').value=s.coolMin; qs('ts-cool-max').value=s.coolMax; qs('ts-heat-min').value=s.heatMin; qs('ts-heat-max').value=s.heatMax; qs('ts-safety-low').value=s.safetyLow; qs('ts-safety-high').value=s.safetyHigh; qs('ts-cool-switch').value=s.autoCoolOutdoorTarget; qs('ts-heat-switch').value=s.autoHeatOutdoorTarget; qs('ts-heat-lock').checked=!!s.heatLocked; qs('ts-cool-lock').checked=!!s.coolLocked;
  qs('ts-auto-delay').value=s.autoChangeoverHours; qs('ts-manual-delay').value=s.manualChangeoverMinutes; qs('ts-cool-fan').value=s.coolFanRemainOnMinutes; qs('ts-differential').value=s.temperatureDifferential; qs('ts-heat-runtime').value=s.heatMinimumRuntimeMinutes; qs('ts-cool-runtime').value=s.coolMinimumRuntimeMinutes;
  qs('ts-room-mode').value=s.roomTempControlMode||'internal'; qs('ts-heat-mode').value=s.heatControlMode||'internal'; qs('ts-cool-mode').value=s.coolControlMode||'internal'; qs('ts-fan-mode').value=s.fanControlMode||'internal'; qs('ts-room-entity').value=s.currentTempEntityId||''; qs('ts-heat-entity').value=s.externalHeatEntityId||''; qs('ts-cool-entity').value=s.externalCoolEntityId||''; qs('ts-fan-entity').value=s.externalFanEntityId||''; qs('ts-outdoor-entity').value=s.outdoorTempEntityId||''; qs('ts-door-entity').value=s.doorEntityId||''; qs('ts-door-delay').value=s.doorPauseDurationMinutes; qs('ts-disarm-code').value=s.disarmCode||''; qs('ts-settings-code').value=s.settingsCode||'';
  fillDatalist('temperature-entities',e.temperature||[]); fillDatalist('outdoor-entities',e.outdoor||[]); fillDatalist('air-control-entities',e.airControls||[]); fillDatalist('door-entities',e.doors||[]); fillMulti('ts-auto-away-people',e.people||[],s.autoAwayPersonIds); fillMulti('ts-tracked-people',e.people||[],s.trackedPersonIds); fillMulti('ts-sync',e.syncThermostats||[],s.syncThermostatIds);
}}
async function loadThermostatSettings() {{ try{{setBox(qs('ts-status'),'Loading current thermostat settings...','muted'); const response=await fetch('/api/settings/web'); const data=await response.json().catch(()=>({{}})); if(!response.ok||!data.ok)throw new Error(data.error||'Could not load settings.'); window.lastThermostatEntities=data.entities||{{}}; setThermostatForm(data.settings||{{}},window.lastThermostatEntities); window.thermostatSettingsLoaded=true; const warning=(data.warnings||[]).join('\\n'); setBox(qs('ts-status'),warning||'Current values loaded. Changes are not applied until Save Thermostat Settings is pressed.',warning?'muted':'ok');}}catch(err){{setBox(qs('ts-status'),err.message||String(err),'bad');}} }}
function thermostatPayload() {{ return {{settings:{{ name:value('ts-name').trim(),fan:value('ts-fan'),screenOrientation:value('ts-orientation'),awayHeat:numberValue('ts-away-heat'),awayCool:numberValue('ts-away-cool'),autoAwayPersonIds:selectedValues('ts-auto-away-people'),coolMin:numberValue('ts-cool-min'),coolMax:numberValue('ts-cool-max'),heatMin:numberValue('ts-heat-min'),heatMax:numberValue('ts-heat-max'),safetyLow:numberValue('ts-safety-low'),safetyHigh:numberValue('ts-safety-high'),autoCoolOutdoorTarget:numberValue('ts-cool-switch'),autoHeatOutdoorTarget:numberValue('ts-heat-switch'),heatLocked:qs('ts-heat-lock').checked,coolLocked:qs('ts-cool-lock').checked,autoChangeoverHours:numberValue('ts-auto-delay'),manualChangeoverMinutes:numberValue('ts-manual-delay'),coolFanRemainOnMinutes:numberValue('ts-cool-fan'),temperatureDifferential:numberValue('ts-differential'),heatMinimumRuntimeMinutes:numberValue('ts-heat-runtime'),coolMinimumRuntimeMinutes:numberValue('ts-cool-runtime'),roomTempControlMode:value('ts-room-mode'),heatControlMode:value('ts-heat-mode'),coolControlMode:value('ts-cool-mode'),fanControlMode:value('ts-fan-mode'),currentTempEntityId:value('ts-room-entity').trim(),externalHeatEntityId:value('ts-heat-entity').trim(),externalCoolEntityId:value('ts-cool-entity').trim(),externalFanEntityId:value('ts-fan-entity').trim(),outdoorTempEntityId:value('ts-outdoor-entity').trim(),syncThermostatIds:selectedValues('ts-sync'),trackedPersonIds:selectedValues('ts-tracked-people'),doorEntityId:value('ts-door-entity').trim(),doorPauseDurationMinutes:numberValue('ts-door-delay'),disarmCode:value('ts-disarm-code').trim(),settingsCode:value('ts-settings-code').trim()}} }}; }}
qs('ts-save').addEventListener('click',async()=>{{ try{{qs('ts-save').disabled=true;setBox(qs('ts-status'),'Validating and saving thermostat settings...','muted'); const response=await fetch('/api/settings/web',{{method:'POST',headers:{{'Accept':'application/json','Content-Type':'application/json'}},body:JSON.stringify(thermostatPayload())}}); const data=await response.json().catch(()=>({{}})); if(!response.ok||!data.ok)throw new Error(data.error||'Could not save thermostat settings.'); if(data.settings)setThermostatForm(data.settings,window.lastThermostatEntities||{{}}); const warning=(data.warnings||[]).join('\\n'); setBox(qs('ts-status'),(data.message||'Settings saved.')+(warning?'\\n'+warning:''),warning?'muted':'ok');}}catch(err){{setBox(qs('ts-status'),err.message||String(err),'bad');}}finally{{qs('ts-save').disabled=false;}} }});
qs('ts-reload').addEventListener('click',()=>{{window.thermostatSettingsLoaded=false;loadThermostatSettings();}});

qs('va-volume').addEventListener('input',()=>{{qs('va-volume-value').textContent=qs('va-volume').value+'%';}});
function assistantPayload() {{ return {{enabled:qs('va-enabled').checked,speak:qs('va-speak').checked,funMode:qs('va-fun').checked,playfulReplies:qs('va-playful').checked,continueConversation:qs('va-continue').checked,showResponseText:qs('va-show-text').checked,agentId:value('va-agent').trim(),ttsEntityId:value('va-tts').trim(),mediaPlayerId:value('va-media').trim(),language:value('va-language').trim()||'en',responseHoldSeconds:Number(value('va-hold')||2),announcementVolumePercent:Number(value('va-volume')||45)}}; }}
qs('va-save').addEventListener('click',async()=>{{try{{qs('va-save').disabled=true;setBox(qs('va-status'),'Saving JARVIS settings...','muted');const response=await fetch('/api/assistant/config',{{method:'POST',headers:{{'Accept':'application/json','Content-Type':'application/json'}},body:JSON.stringify(assistantPayload())}});const data=await response.json().catch(()=>({{}}));if(!response.ok||!data.ok)throw new Error(data.error||'Could not save JARVIS settings.');setBox(qs('va-status'),data.message||'JARVIS settings saved.','ok');}}catch(err){{setBox(qs('va-status'),err.message||String(err),'bad');}}finally{{qs('va-save').disabled=false;}}}});
qs('va-test').addEventListener('click',async()=>{{const text=value('va-test-text').trim();if(!text){{setBox(qs('va-status'),'Enter a test command first.','bad');return;}}try{{qs('va-test').disabled=true;setBox(qs('va-status'),'Running command. Watch the thermostat screen...','muted');const response=await fetch('/api/assistant/process',{{method:'POST',headers:{{'Accept':'application/json','Content-Type':'application/json'}},body:JSON.stringify({{text}})}});const data=await response.json().catch(()=>({{}}));if(!response.ok||!data.ok)throw new Error(data.error||'Assistant test failed.');const suffix=data.speechPlayed?'\\nSpoken on '+data.mediaPlayerId+' at '+(data.announcementVolumePercent||value('va-volume'))+'%.':(data.speechError?'\\nVoice was not played: '+data.speechError:'');setBox(qs('va-status'),'JARVIS: '+data.response+suffix,data.speechPlayed?'ok':'muted');}}catch(err){{setBox(qs('va-status'),err.message||String(err),'bad');}}finally{{qs('va-test').disabled=false;}}}});
</script>
</body></html>"""


def _decode_proc_mount_field(value: str) -> str:
    # /proc/mounts escapes spaces and a few control characters as octal values.
    # Keep this local and dependency-free so USB backup works on a minimal Pi OS.
    return (
        str(value or "")
        .replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def _mounted_paths() -> list[dict]:
    mounts: list[dict] = []
    try:
        lines = Path("/proc/mounts").read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        lines = []
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mounts.append({
            "device": _decode_proc_mount_field(parts[0]),
            "path": _decode_proc_mount_field(parts[1]),
            "fsType": _decode_proc_mount_field(parts[2]).lower(),
        })
    return mounts


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _safe_usb_mount_name(device_path: str) -> str:
    name = Path(str(device_path or "usb")).name.strip() or "usb"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name)
    return safe or "usb"


def _run_command_quiet(cmd: list[str], timeout: float = 10.0) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        detail = "\n".join(x for x in (result.stdout.strip(), result.stderr.strip()) if x).strip()
        return result.returncode == 0, detail
    except Exception as exc:
        return False, str(exc)


def _cleanup_legacy_repo_usb_mounts() -> None:
    """Unmount and remove the old repo-local USB mount folder if it exists.

    This keeps future git reset/clean operations from getting stuck on
    data/usb-mounts/sdX entries that are really mounted thumb drives.
    """
    legacy_root = USB_LEGACY_MOUNT_ROOT
    if not legacy_root.exists():
        return
    legacy_mounts: list[Path] = []
    for mount in _mounted_paths():
        mount_path = Path(str(mount.get("path") or ""))
        if mount_path == legacy_root or _path_is_relative_to(mount_path, legacy_root):
            legacy_mounts.append(mount_path)
    legacy_mounts.sort(key=lambda x: len(str(x)), reverse=True)
    for mount_path in legacy_mounts:
        ok, detail = _run_command_quiet(["sudo", "umount", str(mount_path)], timeout=12)
        if not ok:
            print(f"Legacy USB unmount skipped for {mount_path}: {detail}", flush=True)
    try:
        shutil.rmtree(legacy_root, ignore_errors=True)
    except Exception:
        pass


def _is_usb_mount_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    for root in USB_MOUNT_ROOTS:
        try:
            root_path = Path(root).resolve()
        except Exception:
            root_path = Path(root)
        if resolved == root_path:
            # Never write directly to /mnt or /media itself. We only want a
            # mounted USB volume below one of those roots.
            continue
        try:
            resolved.relative_to(root_path)
            return True
        except ValueError:
            continue
    return False


def _mounted_usb_drives(require_writable: bool = False) -> list[dict]:
    ignored_fs = {
        "autofs", "binfmt_misc", "bpf", "cgroup", "cgroup2", "configfs", "debugfs",
        "devpts", "devtmpfs", "efivarfs", "fusectl", "hugetlbfs", "mqueue", "overlay",
        "proc", "pstore", "ramfs", "securityfs", "sysfs", "tmpfs", "tracefs",
    }
    candidates: list[dict] = []
    seen: set[str] = set()

    for mount in _mounted_paths():
        device = str(mount.get("device") or "")
        fs_type = str(mount.get("fsType") or "").lower()
        mount_path = Path(str(mount.get("path") or ""))

        if fs_type in ignored_fs:
            continue
        if not device.startswith("/dev/"):
            continue
        if not _is_usb_mount_path(mount_path):
            continue
        if not mount_path.exists() or not mount_path.is_dir():
            continue
        try:
            resolved = str(mount_path.resolve())
        except Exception:
            resolved = str(mount_path)
        if resolved in seen:
            continue
        if require_writable and not os.access(str(mount_path), os.W_OK):
            continue
        seen.add(resolved)
        candidates.append({
            "path": str(mount_path),
            "device": device,
            "fsType": fs_type,
            "label": mount_path.name or str(mount_path),
        })
    candidates.sort(key=lambda item: (str(item.get("label") or "").lower(), str(item.get("path") or "")))
    return candidates


def _flatten_lsblk_nodes(nodes: list[dict], parent_usb: bool = False) -> list[dict]:
    flattened: list[dict] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        node_is_usb = parent_usb or str(node.get("tran") or "").lower() == "usb" or bool(node.get("rm"))
        item = dict(node)
        item["_smartThermostatUsb"] = node_is_usb
        flattened.append(item)
        children = node.get("children") if isinstance(node.get("children"), list) else []
        flattened.extend(_flatten_lsblk_nodes(children, node_is_usb))
    return flattened


def _removable_block_devices() -> list[dict]:
    cmd = ["lsblk", "-J", "-o", "NAME,KNAME,PATH,TYPE,TRAN,MOUNTPOINTS,MOUNTPOINT,FSTYPE,LABEL,RM,RO,SIZE"]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8, check=False)
        if result.returncode != 0:
            print(f"USB lsblk failed: {result.stderr.strip()}", flush=True)
            return []
        raw = json.loads(result.stdout or "{}")
    except Exception as exc:
        print(f"USB lsblk parse failed: {exc}", flush=True)
        return []

    nodes = _flatten_lsblk_nodes(raw.get("blockdevices") if isinstance(raw, dict) else [])
    devices: list[dict] = []
    seen: set[str] = set()
    for node in nodes:
        device_path = str(node.get("path") or "").strip()
        if not device_path.startswith("/dev/"):
            continue
        if not bool(node.get("_smartThermostatUsb")):
            continue
        if str(node.get("ro") or "0") in {"1", "true", "True"}:
            continue
        node_type = str(node.get("type") or "").lower()
        fs_type = str(node.get("fstype") or "").strip().lower()
        # Prefer partitions. Allow a whole disk only if it directly contains a filesystem.
        if node_type not in {"part", "disk"}:
            continue
        if node_type == "disk" and not fs_type:
            continue
        mountpoints = node.get("mountpoints") if isinstance(node.get("mountpoints"), list) else []
        mountpoint = node.get("mountpoint")
        if mountpoint:
            mountpoints.append(mountpoint)
        mounted = [str(x) for x in mountpoints if x]
        if device_path in seen:
            continue
        seen.add(device_path)
        devices.append({
            "path": device_path,
            "fsType": fs_type,
            "label": str(node.get("label") or Path(device_path).name),
            "mounted": mounted,
        })
    return devices


def _mount_usb_device(device: dict) -> tuple[bool, str]:
    device_path = str(device.get("path") or "")
    if not device_path.startswith("/dev/"):
        return False, "Invalid USB device path."
    mount_root = USB_RUNTIME_MOUNT_ROOT
    try:
        mount_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return False, f"Could not create USB runtime mount folder {mount_root}: {exc}"
    target = mount_root / _safe_usb_mount_name(device_path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return False, f"Could not create USB mount folder {target}: {exc}"

    fs_type = str(device.get("fsType") or "").lower()
    opts: list[str] = []
    if fs_type in {"vfat", "exfat", "msdos", "ntfs", "ntfs3"}:
        opts.append(f"uid={os.getuid()}")
        opts.append(f"gid={os.getgid()}")
        opts.append("umask=0002")
    cmd = ["sudo", "mount"]
    if opts:
        cmd += ["-o", ",".join(opts)]
    cmd += [device_path, str(target)]
    ok, detail = _run_command_quiet(cmd, timeout=15)
    if not ok:
        try:
            target.rmdir()
        except Exception:
            pass
        return False, detail or f"Could not mount {device_path}."
    return True, str(target)


def _ensure_usb_drives(require_writable: bool = False) -> list[dict]:
    _cleanup_legacy_repo_usb_mounts()
    drives = _mounted_usb_drives(require_writable=require_writable)
    if drives:
        return drives

    errors: list[str] = []
    for device in _removable_block_devices():
        if device.get("mounted"):
            # It is mounted somewhere outside our allowed USB roots. Do not touch
            # it; a later _mounted_usb_drives pass will pick it up if it is under
            # /media, /run/media, /mnt, or SMART_THERMOSTAT_USB_RUNTIME_MOUNT_ROOT.
            continue
        ok, detail = _mount_usb_device(device)
        if not ok:
            errors.append(f"{device.get('label') or device.get('path')}: {detail}")
    drives = _mounted_usb_drives(require_writable=require_writable)
    if not drives and errors:
        print("USB auto-mount did not produce a usable drive: " + "; ".join(errors[:3]), flush=True)
    return drives

def _write_json_atomic(path: Path, payload: dict) -> int:
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)
    try:
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        pass
    return len(body)


def _config_export_usb_payload(server_port: int | str | None = None) -> dict:
    drives = _ensure_usb_drives(require_writable=True)
    if not drives:
        return {
            "ok": False,
            "error": "No writable USB drive was found. Insert a USB drive directly into the Pi and try Download Config again.",
            "filename": USB_CONFIG_FILENAME,
            "drives": [],
        }

    payload = _config_export_payload(server_port)
    errors: list[str] = []
    for drive in drives:
        target = Path(str(drive.get("path") or "")) / USB_CONFIG_FILENAME
        try:
            size = _write_json_atomic(target, payload)
            return {
                "ok": True,
                "message": f"Config downloaded to USB as {USB_CONFIG_FILENAME}.",
                "filename": USB_CONFIG_FILENAME,
                "path": str(target),
                "drive": drive,
                "bytes": size,
            }
        except Exception as exc:
            errors.append(f"{drive.get('label') or drive.get('path')}: {exc}")

    return {
        "ok": False,
        "error": "A USB drive was found, but the config could not be written. " + "; ".join(errors[:3]),
        "filename": USB_CONFIG_FILENAME,
        "drives": drives,
    }


def _find_usb_config_files() -> list[dict]:
    found: list[dict] = []
    for drive in _ensure_usb_drives(require_writable=False):
        path = Path(str(drive.get("path") or "")) / USB_CONFIG_FILENAME
        try:
            if path.exists() and path.is_file():
                found.append({
                    "path": str(path),
                    "drive": drive,
                    "mtime": path.stat().st_mtime,
                    "bytes": path.stat().st_size,
                })
        except Exception:
            continue
    found.sort(key=lambda item: float(item.get("mtime") or 0), reverse=True)
    return found


def _config_import_usb_payload() -> dict:
    matches = _find_usb_config_files()
    if not matches:
        return {
            "ok": False,
            "error": f"No {USB_CONFIG_FILENAME} file was found on any USB drive.",
            "filename": USB_CONFIG_FILENAME,
            "drives": _ensure_usb_drives(require_writable=False),
        }

    selected = matches[0]
    path = Path(str(selected.get("path") or ""))
    try:
        backup = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "error": f"The USB config file could not be read: {exc}",
            "filename": USB_CONFIG_FILENAME,
            "path": str(path),
        }

    result = _config_import_payload(backup)
    if not result.get("ok"):
        result.setdefault("filename", USB_CONFIG_FILENAME)
        result.setdefault("path", str(path))
        return result
    result["message"] = f"Config uploaded from USB file {USB_CONFIG_FILENAME}."
    result["filename"] = USB_CONFIG_FILENAME
    result["path"] = str(path)
    result["drive"] = selected.get("drive")
    return result


def _extract_import_record(payload: object) -> tuple[dict | None, dict | None]:
    if not isinstance(payload, dict):
        return None, None
    source = payload.get("backup", payload)
    if not isinstance(source, dict):
        return None, None

    records = source.get("records") if isinstance(source.get("records"), dict) else {}
    panel_record_source = records.get("panelConfig") or source.get("panelConfig")
    thermostat_record_source = records.get("thermostatState") or source.get("thermostatState")

    config = None
    if isinstance(panel_record_source, dict):
        if isinstance(panel_record_source.get("config"), dict):
            config = panel_record_source.get("config")
        elif any(key in panel_record_source for key in ("thermostat", "alarm", "blinds", "lights", "roomControl", "integrations")):
            config = panel_record_source

    if config is None and isinstance(source.get("config"), dict):
        config = source.get("config")

    # Allow importing a raw data/panel-config.json file or a copied config object.
    if config is None and any(key in source for key in ("thermostat", "alarm", "blinds", "lights", "roomControl", "integrations")):
        config = source

    thermostat = None
    if isinstance(thermostat_record_source, dict):
        if isinstance(thermostat_record_source.get("thermostat"), dict):
            thermostat = thermostat_record_source.get("thermostat")
        elif any(key in thermostat_record_source for key in ("name", "targetTemp", "mode", "fan", "limits")):
            thermostat = thermostat_record_source

    if thermostat is None and isinstance(source.get("thermostat"), dict):
        thermostat = source.get("thermostat")
    if thermostat is None and isinstance(config, dict) and isinstance(config.get("thermostat"), dict):
        thermostat = config.get("thermostat")

    return config if isinstance(config, dict) else None, thermostat if isinstance(thermostat, dict) else None


def _config_import_payload(payload: object) -> dict:
    config, thermostat = _extract_import_record(payload)
    if config is None:
        return {"ok": False, "error": "The uploaded file does not contain a valid Smart Thermostat config."}

    panel_record = _write_panel_config_record(config)
    thermostat_record = None
    if thermostat is not None:
        thermostat_record = _write_thermostat_record(thermostat, force=True)
    elif isinstance(panel_record.get("config"), dict) and isinstance(panel_record["config"].get("thermostat"), dict):
        thermostat_record = _write_thermostat_record(panel_record["config"].get("thermostat"), force=True)

    return {
        "ok": True,
        "message": "Config uploaded. Close the Backup Config popup on the thermostat to apply the restored settings immediately.",
        "version": panel_record["version"],
        "updatedAt": panel_record["updatedAt"],
        "config": panel_record["config"],
        "thermostat": thermostat_record.get("thermostat") if isinstance(thermostat_record, dict) else None,
    }



def _mode_available_for_auto_switch(thermostat: dict, mode: str) -> bool:
    mode = str(mode or "").strip().lower()
    if mode == "heat":
        return not bool(thermostat.get("heatLocked"))
    if mode == "cool":
        return not bool(thermostat.get("coolLocked"))
    return False


def _auto_switch_targets(thermostat: dict) -> tuple[float, float]:
    cool_target = _number(thermostat.get("autoCoolOutdoorTarget"), 70, 41, 100)
    heat_target = _number(thermostat.get("autoHeatOutdoorTarget"), 65, 40, 99)
    heat_target = min(heat_target, cool_target - 1)
    return cool_target, heat_target


def _auto_switch_signal(thermostat: dict) -> str:
    current = _number(thermostat.get("currentTemp"), 70, -40, 130)
    cool_target, heat_target = _auto_switch_targets(thermostat)
    cool_available = _mode_available_for_auto_switch(thermostat, "cool")
    heat_available = _mode_available_for_auto_switch(thermostat, "heat")
    if not cool_available and not heat_available:
        return ""
    if current > cool_target:
        return "cool" if cool_available else ("heat" if heat_available else "")
    if current <= heat_target:
        return "heat" if heat_available else ("cool" if cool_available else "")
    return ""


def _empty_auto_switch_notice() -> dict:
    return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchNotice"])


def _empty_auto_switch_notice_dismissed() -> dict:
    return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchNoticeDismissed"])


def _empty_auto_switch_hold() -> dict:
    return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchHold"])


def _auto_switch_notice_was_dismissed(thermostat: dict, source: str, from_mode: str, to_mode: str, cool_target: float, heat_target: float) -> bool:
    dismissed = _normalize_auto_switch_notice_dismissed(thermostat.get("autoSwitchNoticeDismissed"))
    if not dismissed.get("active"):
        return False
    return (
        str(dismissed.get("source") or "") == source
        and str(dismissed.get("fromMode") or "") == from_mode
        and str(dismissed.get("toMode") or "") == to_mode
        and abs(float(dismissed.get("coolTarget") or 0) - float(cool_target or 0)) < 0.01
        and abs(float(dismissed.get("heatTarget") or 0) - float(heat_target or 0)) < 0.01
    )


def _opposite_hvac_mode(mode: str) -> str:
    mode = str(mode or "").strip().lower()
    if mode == "heat":
        return "cool"
    if mode == "cool":
        return "heat"
    return ""


def _last_opposite_equipment_run_at(thermostat: dict, requested_mode: str, *, now_ms: int | None = None) -> float:
    requested_mode = str(requested_mode or "").strip().lower()
    opposite = _opposite_hvac_mode(requested_mode)
    if opposite not in {"heat", "cool"}:
        return 0
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    relay_key = "heatRelayWasOn" if opposite == "heat" else "coolRelayWasOn"
    equipment_key = "equipmentLastHeatRunAt" if opposite == "heat" else "equipmentLastCoolRunAt"
    legacy_key = "lastHeatRunAt" if opposite == "heat" else "lastCoolRunAt"
    if bool(thermostat.get(relay_key)):
        return now
    return max(
        _number(thermostat.get(equipment_key), 0, 0),
        _number(thermostat.get(legacy_key), 0, 0),
        _last_manual_bypass_run_at(thermostat, requested_mode, now_ms=now),
    )


def _last_manual_bypass_run_at(thermostat: dict, requested_mode: str, *, now_ms: int | None = None) -> float:
    """Return a temporary runtime marker after a user bypasses into a mode.

    The relay/runtime marker is written by the output worker after the API
    response. If the user immediately switches back before that worker or an
    external Home Assistant switch confirms, the next transition used to miss
    the manual delay about one poll out of a few. A bypass means the selected
    side is allowed to energize now, so keep a short-lived software marker for
    that side and use it only while it is still inside the configured manual
    lockout window.
    """
    requested_mode = str(requested_mode or "").strip().lower()
    opposite = _opposite_hvac_mode(requested_mode)
    if opposite not in {"heat", "cool"}:
        return 0
    bypass_mode = str(thermostat.get("lastManualChangeoverBypassMode") or "").strip().lower()
    if bypass_mode != opposite:
        return 0
    bypass_at = _number(thermostat.get("lastManualChangeoverBypassAt"), 0, 0, None)
    if bypass_at <= 0:
        return 0
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    lockout_minutes = _number(thermostat.get("manualChangeoverLockoutMinutes"), MANUAL_CHANGEOVER_LOCKOUT_MINUTES, 0, 60)
    if lockout_minutes <= 0 or bypass_at + lockout_minutes * 60000 <= now:
        return 0
    return bypass_at


def _manual_changeover_lockout_until(thermostat: dict, requested_mode: str, *, now_ms: int | None = None) -> int:
    requested_mode = str(requested_mode or "").strip().lower()
    if requested_mode not in {"heat", "cool"}:
        return 0
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    lockout_minutes = _number(thermostat.get("manualChangeoverLockoutMinutes"), MANUAL_CHANGEOVER_LOCKOUT_MINUTES, 0, 60)
    if lockout_minutes <= 0:
        return 0
    last_opposite_run_at = _last_opposite_equipment_run_at(thermostat, requested_mode, now_ms=now)
    if not last_opposite_run_at:
        return 0
    until = int(last_opposite_run_at + lockout_minutes * 60000)
    return until if until > now else 0


def _manual_changeover_lockout_until_for_transition(existing: dict, requested_mode: str, *, now_ms: int | None = None) -> int:
    """Return the manual Heat/Cool delay for a user-requested transition.

    The normal timestamp path only works after the equipment runtime marker has
    already written `coolRelayWasOn`/`equipmentLastCoolRunAt` or the heat
    equivalents. On the touchscreen, a mode tap can arrive between control-loop
    passes, especially with external Home Assistant outputs. In that race the
    room is visibly/actively cooling or heating, but the persisted relay marker
    can still be false, so the old logic returned no 10-minute countdown and
    comfort auto-switch immediately grabbed the mode back.

    For a manual tap, use the actual current state we are transitioning from:
    if the opposite side is currently requested/running from the existing
    thermostat state, start a full manual lockout right now.
    """
    requested_mode = str(requested_mode or "").strip().lower()
    if requested_mode not in {"heat", "cool"}:
        return 0
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    lockout_minutes = _number(existing.get("manualChangeoverLockoutMinutes"), MANUAL_CHANGEOVER_LOCKOUT_MINUTES, 0, 60)
    if lockout_minutes <= 0:
        return 0

    # First honor the recorded runtime timestamps/relay flags.
    until = _manual_changeover_lockout_until(existing, requested_mode, now_ms=now)
    if until > now:
        return until

    opposite = _opposite_hvac_mode(requested_mode)
    if opposite not in {"heat", "cool"}:
        return 0

    relay_key = "heatRelayWasOn" if opposite == "heat" else "coolRelayWasOn"
    if bool(existing.get(relay_key)):
        return int(now + lockout_minutes * 60000)

    try:
        outputs = _thermostat_outputs(existing)
    except Exception:
        outputs = {}
    if bool(outputs.get(opposite)):
        return int(now + lockout_minutes * 60000)

    mode = _normalize_mode(existing.get("mode"), "")
    active_mode = str(existing.get("autoActiveMode") or "").strip().lower() if mode == "auto" else mode
    if active_mode == opposite:
        current = _number(existing.get("currentTemp"), 70, -40, 130)
        target = _number(existing.get("targetTemp"), 70, 45, 95)
        if (opposite == "cool" and current > target) or (opposite == "heat" and current < target):
            return int(now + lockout_minutes * 60000)

    return 0


def _active_manual_changeover_pending(thermostat: dict, *, now_ms: int | None = None) -> tuple[str, int]:
    pending = _normalize_pending_mode(thermostat.get("manualPendingMode"))
    until = int(_number(thermostat.get("manualLockoutUntil"), 0, 0, None))
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if pending in {"heat", "cool"} and until > now:
        return pending, until
    return "", 0


def _record_auto_switch_notice(thermostat: dict, source: str, from_mode: str, to_mode: str) -> None:
    from_mode = str(from_mode or "").strip().lower()
    to_mode = str(to_mode or "").strip().lower()
    source = str(source or "manual").strip().lower() if str(source or "").strip().lower() in {"auto", "manual"} else "manual"
    if from_mode not in {"heat", "cool"} or to_mode not in {"heat", "cool"} or from_mode == to_mode:
        return
    cool_target, heat_target = _auto_switch_targets(thermostat)
    current = _number(thermostat.get("currentTemp"), 70, -40, 130)
    if _auto_switch_notice_was_dismissed(thermostat, source, from_mode, to_mode, cool_target, heat_target):
        thermostat["autoSwitchNotice"] = _empty_auto_switch_notice()
        thermostat["autoSwitchHold"] = _empty_auto_switch_hold()
        return
    thermostat["autoSwitchNoticeDismissed"] = _empty_auto_switch_notice_dismissed()
    thermostat["autoSwitchNotice"] = {
        "active": True,
        "source": source,
        "fromMode": from_mode,
        "toMode": to_mode,
        "switchTemp": current,
        "outdoorTemp": current,
        "coolTarget": cool_target,
        "heatTarget": heat_target,
        "createdAt": int(time.time() * 1000),
    }
    thermostat["autoSwitchHold"] = _empty_auto_switch_hold()


def _clamp_comfort_target_to_mode(thermostat: dict) -> None:
    mode = str(thermostat.get("mode") or "auto").strip().lower()
    mode_for_limits = mode if mode in {"heat", "cool"} else "auto"
    limits = thermostat.get("limits") if isinstance(thermostat.get("limits"), dict) else {}
    lim = limits.get(mode_for_limits) or limits.get("auto") or {"min": 45, "max": 95}
    low = _number(lim.get("min"), 45, 45, 95)
    high = _number(lim.get("max"), 95, low + 1, 95)
    thermostat["targetTemp"] = _number(thermostat.get("targetTemp"), thermostat.get("lastComfortTarget", 70), low, high)
    thermostat["lastComfortTarget"] = _number(thermostat.get("lastComfortTarget"), thermostat["targetTemp"], low, high)


def _apply_comfort_auto_switch_logic(thermostat: dict, *, notify: bool = True) -> dict:
    """Apply room-temp mode switching and changeover lockout state.

    The old web UI used autoCoolOutdoorTarget/autoHeatOutdoorTarget as the room
    temperature thresholds for comfort auto-switching. Keep the field names for
    compatibility, but treat them as Cool Mode Switch / Heat Mode Switch here.
    """
    t = _merge_thermostat_state(thermostat)
    now_ms = int(time.time() * 1000)
    mode = _normalize_mode(t.get("mode"), "cool")
    signal = _auto_switch_signal(t)

    manual_pending, manual_until = _active_manual_changeover_pending(t, now_ms=now_ms)
    if manual_pending:
        # A direct Heat/Cool tap during compressor changeover protection must
        # show the manual cooldown/bypass state instead of being immediately
        # undone by comfort auto-switch logic. Keep the user's chosen mode
        # selected while the relay side waits out the lockout.
        if mode != "auto" and manual_pending == mode and _mode_available_for_auto_switch(t, manual_pending):
            t["manualPendingMode"] = manual_pending
            t["manualLockoutUntil"] = manual_until
            return t
        t["manualPendingMode"] = ""
        t["manualLockoutUntil"] = 0
    elif t.get("manualPendingMode") or t.get("manualLockoutUntil"):
        t["manualPendingMode"] = ""
        t["manualLockoutUntil"] = 0

    hold = t.get("autoSwitchHold") if isinstance(t.get("autoSwitchHold"), dict) else {}
    hold_active = bool(hold.get("active"))
    hold_source = str(hold.get("source") or "").lower()
    hold_mode = str(hold.get("mode") or "").lower()
    hold_until = _number(hold.get("until"), 0, 0, None)
    if hold_active and hold_source == "manual" and mode != "auto":
        # A physical mode tap wins over comfort auto-switch. Keep the selected
        # manual mode while the room-temp rule would have recommended the
        # opposite mode. Once the rule no longer conflicts, clear the notice.
        if (
            hold_mode not in {"heat", "cool"}
            or not _mode_available_for_auto_switch(t, hold_mode)
            or not signal
            or signal == hold_mode
        ):
            t["autoSwitchHold"] = _empty_auto_switch_hold()
        else:
            if t.get("mode") != hold_mode:
                t["mode"] = hold_mode
                _clamp_comfort_target_to_mode(t)
            t["manualPendingMode"] = ""
            t["manualLockoutUntil"] = 0
            return t

    if mode == "auto":
        active = str(t.get("autoActiveMode") or "").lower()
        if active not in {"heat", "cool"} or not _mode_available_for_auto_switch(t, active):
            active = "cool" if _mode_available_for_auto_switch(t, "cool") else "heat" if _mode_available_for_auto_switch(t, "heat") else ""
        desired = signal or active
        if desired not in {"heat", "cool"}:
            t["autoActiveMode"] = ""
            t["autoPendingMode"] = ""
            t["autoLockoutUntil"] = 0
            return t

        pending = str(t.get("autoPendingMode") or "").lower()
        until = _number(t.get("autoLockoutUntil"), 0, 0, None)
        if pending in {"heat", "cool"} and until and now_ms >= until:
            previous = active
            t["autoActiveMode"] = pending
            t["autoPendingMode"] = ""
            t["autoLockoutUntil"] = 0
            if notify and previous and pending != previous:
                _record_auto_switch_notice(t, "auto", previous, pending)
            return t

        if desired != active:
            last_key = "equipmentLastCoolRunAt" if desired == "heat" else "equipmentLastHeatRunAt"
            legacy_key = "lastCoolRunAt" if desired == "heat" else "lastHeatRunAt"
            last_opposite = max(_number(t.get(last_key), 0, 0), _number(t.get(legacy_key), 0, 0))
            lockout_minutes = _number(t.get("autoChangeoverLockoutMinutes"), 120, 0, 720)
            lockout_ms = lockout_minutes * 60000
            if lockout_ms > 0 and last_opposite and now_ms - last_opposite < lockout_ms:
                t["autoPendingMode"] = desired
                t["autoLockoutUntil"] = last_opposite + lockout_ms
                return t
            previous = active
            t["autoActiveMode"] = desired
            t["autoPendingMode"] = ""
            t["autoLockoutUntil"] = 0
            if notify and previous and desired != previous:
                _record_auto_switch_notice(t, "auto", previous, desired)
        elif t.get("autoLockoutUntil") and now_ms >= _number(t.get("autoLockoutUntil"), 0, 0):
            t["autoPendingMode"] = ""
            t["autoLockoutUntil"] = 0
        return t

    current_mode = mode if mode in {"heat", "cool"} else ""
    if current_mode:
        if signal and signal != current_mode and _mode_available_for_auto_switch(t, signal):
            hold = t.get("autoSwitchHold") if isinstance(t.get("autoSwitchHold"), dict) else {}
            hold_active = (
                bool(hold.get("active"))
                and str(hold.get("source") or "").lower() == "manual"
                and str(hold.get("mode") or "").lower() == current_mode
                and str(hold.get("suggestedMode") or "").lower() == signal
            )
            if hold_active:
                # Revert/manual override wins.  Keep the selected mode and show
                # the small left-side override notice until the user dismisses
                # it or the room no longer calls for the opposite mode.
                t["mode"] = current_mode
                t["autoSwitchNotice"] = _empty_auto_switch_notice()
                return t

            # With the Auto button removed, Heat and Cool still have the same
            # comfort auto-switch safety behavior.  Once any manual changeover
            # delay is no longer active, flip to the recommended side and leave
            # a persistent notice for the front end.
            previous = current_mode
            t["mode"] = signal
            t["autoActiveMode"] = signal
            t["manualPendingMode"] = ""
            t["manualLockoutUntil"] = 0
            t["autoPendingMode"] = ""
            t["autoLockoutUntil"] = 0
            _clamp_comfort_target_to_mode(t)
            if notify and previous != signal:
                _record_auto_switch_notice(t, "manual", previous, signal)
            return t
        if isinstance(t.get("autoSwitchHold"), dict) and str(t["autoSwitchHold"].get("source") or "").lower() == "manual":
            t["autoSwitchHold"] = _empty_auto_switch_hold()
        if isinstance(t.get("autoSwitchNotice"), dict) and str(t["autoSwitchNotice"].get("source") or "").lower() == "manual":
            t["autoSwitchNotice"] = _empty_auto_switch_notice()
    return t



def _ha_credentials_from_panel_config() -> tuple[str, str]:
    try:
        record = _read_panel_config_record()
        config = record.get("config") if isinstance(record, dict) else {}
        ha = (((config or {}).get("integrations") or {}).get("homeAssistant") or {})
        return str(ha.get("url") or "").strip(), str(ha.get("token") or "").strip()
    except Exception:
        return "", ""


def _configured_home_assistant_door_entry() -> dict | None:
    """Return the saved Doors/comfort-pause HA entity from panel config.

    This keeps older configs working when the entity was saved under
    integrations.homeAssistant.doorEntity but pauseFunction.entries is empty.
    """
    try:
        record = _read_panel_config_record()
        config = record.get("config") if isinstance(record, dict) else {}
        ha = (((config or {}).get("integrations") or {}).get("homeAssistant") or {})
        door = ha.get("doorEntity") if isinstance(ha, dict) else None
        return _normalize_pause_function_entry(door) if door else None
    except Exception:
        return None


def _person_states_for_schedule(entity_ids: list[str], thermostat: dict) -> dict[str, str]:
    wanted = {str(entity_id or "").strip() for entity_id in entity_ids if str(entity_id or "").strip()}
    if not wanted:
        return {}
    states: dict[str, str] = {}
    for group_key in ("people", "autoAwayPeople"):
        for person in thermostat.get(group_key) or []:
            if not isinstance(person, dict):
                continue
            entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
            if entity_id in wanted:
                states[entity_id] = str(person.get("state") or "unknown").strip().lower()

    ha_url, token = _ha_credentials_from_panel_config()
    if ha_url and token:
        try:
            for item in _ha_all_states_cached(ha_url, token):
                entity_id = str(item.get("entity_id") or "").strip()
                if entity_id in wanted:
                    states[entity_id] = str(item.get("state") or "unknown").strip().lower()
        except Exception as exc:
            print(f"Schedule person-state lookup failed: {exc}", flush=True)
    return states



def _refresh_people_entry_states(people: list[dict], states: dict[str, str]) -> list[dict]:
    refreshed: list[dict] = []
    for person in people:
        if not isinstance(person, dict):
            continue
        item = dict(person)
        entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
        if entity_id and entity_id in states:
            item["state"] = states.get(entity_id) or item.get("state") or "unknown"
            item["lastUpdated"] = datetime.now(timezone.utc).isoformat()
        refreshed.append(item)
    return _normalize_person_entries(refreshed)


def _refresh_person_tracking_states(thermostat: dict) -> dict:
    """Refresh configured person states from Home Assistant for UI/status payloads.

    `people` controls the main-screen person tracking strip. `autoAwayPeople`
    controls Auto Away/Home. They intentionally share only HA state refreshes;
    edits to either list remain independent.
    """
    tracking_people = thermostat.get("people") if isinstance(thermostat.get("people"), list) else []
    away_people = thermostat.get("autoAwayPeople") if isinstance(thermostat.get("autoAwayPeople"), list) else []
    wanted: list[str] = []
    seen: set[str] = set()
    for group in (tracking_people, away_people):
        for person in group or []:
            if not isinstance(person, dict):
                continue
            entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
            if entity_id and entity_id not in seen:
                seen.add(entity_id)
                wanted.append(entity_id)
    if not wanted:
        return thermostat
    states = _person_states_for_schedule(wanted, thermostat)
    if not states:
        return thermostat
    updated = dict(thermostat)
    updated["people"] = _refresh_people_entry_states(tracking_people, states)
    updated["autoAwayPeople"] = _refresh_people_entry_states(away_people, states)
    try:
        _write_thermostat_record(updated, persist=False)
    except Exception as exc:
        print(f"Person tracking refresh could not update runtime state: {exc}", flush=True)
    return updated


def _schedule_people_are_home(schedule: dict, thermostat: dict) -> bool:
    entity_ids = _normalize_schedule_person_ids(schedule.get("personEntityIds") or [])
    if not entity_ids:
        return True
    states = _person_states_for_schedule(entity_ids, thermostat)
    return all(states.get(entity_id) == "home" for entity_id in entity_ids)


def _away_target_for_current_mode(thermostat: dict) -> int:
    """Return the correct Away setpoint for the active heat/cool side."""
    mode = str(thermostat.get("mode") or "cool").strip().lower()
    active = str(thermostat.get("autoActiveMode") or "").strip().lower()
    effective = active if mode == "auto" and active in {"heat", "cool"} else mode
    if effective == "heat":
        return _intish(thermostat.get("awayHeat"), DEFAULT_THERMOSTAT.get("awayHeat", 55), 40, 75)
    return _intish(thermostat.get("awayCool"), DEFAULT_THERMOSTAT.get("awayCool", 85), 75, 100)


def _apply_away_setpoint_logic(
    thermostat: dict,
    *,
    was_away: bool | None = None,
    finalize_restore: bool = False,
) -> dict:
    """Use Away setpoints temporarily and restore this unit's exact pre-Away target."""
    t = dict(thermostat or {})
    away = bool(t.get("away"))
    previous_away = bool(was_away) if was_away is not None else away

    if away:
        away_target = _away_target_for_current_mode(t)
        if not previous_away and t.get("preAwayTargetTemp") is None:
            current_target = _number(t.get("targetTemp"), t.get("lastComfortTarget", 70), 45, 95)
            # Preserve a dedicated snapshot. lastComfortTarget can legitimately
            # change while Away (for example, when a schedule becomes due), but
            # Arriving must return to the temperature that was active at the
            # exact moment this individual thermostat entered Away. The null
            # check makes this safe when one control transaction reapplies Away
            # protection more than once.
            t["preAwayTargetTemp"] = current_target
            t["lastComfortTarget"] = current_target
        t["targetTemp"] = away_target
    elif was_away is True:
        restore_target = t.get("preAwayTargetTemp")
        if restore_target is None:
            restore_target = t.get("lastComfortTarget")
        restore_target = _number(restore_target, t.get("targetTemp", 70), 45, 95)
        t["targetTemp"] = restore_target
        t["lastComfortTarget"] = restore_target
        if finalize_restore:
            t["preAwayTargetTemp"] = None
    elif finalize_restore and t.get("preAwayTargetTemp") is not None:
        # Clean up a snapshot left by an interrupted prior Home/Arriving
        # transition without changing the already-restored Home setpoint.
        t["preAwayTargetTemp"] = None

    return _merge_thermostat_state(t)


def _schedule_target_for_current_mode(thermostat: dict, schedule: dict) -> int:
    mode = str(thermostat.get("mode") or "cool").strip().lower()
    active = str(thermostat.get("autoActiveMode") or "").strip().lower()
    effective = active if mode == "auto" and active in {"heat", "cool"} else mode
    if effective == "heat":
        return _intish(schedule.get("heatSetpoint"), thermostat.get("targetTemp", 70), 45, 95)
    return _intish(schedule.get("coolSetpoint"), thermostat.get("targetTemp", 70), 45, 95)


def _apply_thermostat_schedules(thermostat: dict) -> dict:
    schedules = _normalize_schedule_entries(thermostat.get("schedules") or [])
    if not schedules:
        return thermostat
    if _panel_target_guard_active(thermostat):
        # A schedule due in the same minute as a wall-panel setpoint tap should
        # not immediately undo the user. The schedule will naturally be skipped
        # once the minute rolls over, leaving the manual panel target in charge.
        return thermostat
    now = datetime.now()
    time_key = now.strftime("%H:%M")
    date_key = now.strftime("%Y-%m-%d")
    day_key = SCHEDULE_DAY_KEYS[now.weekday()]
    changed = False
    updated_schedules: list[dict] = []
    updated = dict(thermostat)
    for schedule in schedules:
        sched = dict(schedule)
        should_run = (
            bool(sched.get("enabled", True))
            and str(sched.get("time") or "") == time_key
            and day_key in _normalize_schedule_days(sched.get("days"))
            and str(sched.get("lastTriggeredDate") or "") != date_key
            and _schedule_people_are_home(sched, updated)
        )
        if should_run:
            target = _schedule_target_for_current_mode(updated, sched)
            pause = _normalize_pause_function(updated.get("pauseFunction"))
            if pause.get("active"):
                # A schedule may become due while an open door has temporarily
                # moved the thermostat to its away setpoint. Keep that temporary
                # setpoint in force, but replace the comfort target saved by the
                # pause so closing the door resumes at the newly scheduled
                # temperature instead of the stale pre-pause temperature.
                pause["previousTargetTemp"] = target
                pause["previousLastComfortTarget"] = target
                updated["lastComfortTarget"] = target
                updated["pauseFunction"] = pause
            else:
                updated["targetTemp"] = target
                updated["lastComfortTarget"] = target
            sched["lastTriggeredDate"] = date_key
            changed = True
        updated_schedules.append(sched)
    if changed:
        updated["schedules"] = updated_schedules
        return _merge_thermostat_state(updated)
    return thermostat



def _person_entity_ids_from_entries(entries: object) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for person in entries or []:
        if not isinstance(person, dict):
            continue
        entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
        if entity_id and entity_id not in seen:
            seen.add(entity_id)
            ids.append(entity_id)
    return ids


def _thermostat_person_entity_ids(thermostat: dict) -> list[str]:
    return _person_entity_ids_from_entries(thermostat.get("people") or [])


def _thermostat_auto_away_entity_ids(thermostat: dict) -> list[str]:
    return _person_entity_ids_from_entries(thermostat.get("autoAwayPeople") or [])


def _apply_presence_away_logic(thermostat: dict) -> dict:
    entity_ids = _thermostat_auto_away_entity_ids(thermostat)
    if not entity_ids:
        return thermostat
    states = _person_states_for_schedule(entity_ids, thermostat)
    if not states:
        return thermostat
    was_away = bool(thermostat.get("away"))
    home_entity_ids = [entity_id for entity_id in entity_ids if states.get(entity_id) == "home"]
    any_home = bool(home_entity_ids)
    updated = dict(thermostat)
    away_source = str(updated.get("awaySource") or "").strip().lower()
    home_override = _normalize_presence_home_override(updated.get("presenceHomeOverride"))
    if home_override is None and updated.get("presenceHomeOverride"):
        # Timed Arriving holds expire here. Once expired, normal Auto Away/Home
        # logic below is allowed to put the thermostat back in Away if nobody is home.
        updated["presenceHomeOverride"] = None

    if any_home:
        # A real Home report releases the indefinite manual Return Home hold,
        # but it must not cancel the timed Arriving hold. Arrival is also used
        # while already home to suppress Auto Away during a short trip, so that
        # two-hour bypass must remain active even after a Home state is observed.
        home_override_reason = str((home_override or {}).get("reason") or "").strip().lower()
        if home_override and home_override_reason != "arriving":
            updated["presenceHomeOverride"] = None
        if bool(updated.get("away")) and away_source in {"presence", "auto", ""}:
            updated["away"] = False
            updated["awaySource"] = "presence"
            updated["manualAwayPresenceLatch"] = None
            if updated.get("lastComfortTarget"):
                updated["targetTemp"] = updated.get("lastComfortTarget")
    else:
        if home_override:
            # A Home override blocks Auto Away while all assigned people still
            # report Away/unknown. Manual Return Home lasts until a real Home
            # report; the Arriving override lasts only until its timed expiry.
            updated["away"] = False
            updated["awaySource"] = ""
            updated["manualAwayPresenceLatch"] = None
            updated["presenceHomeOverride"] = home_override
        elif not bool(updated.get("away")) and away_source in {"presence", "auto", ""}:
            updated["away"] = True
            updated["awaySource"] = "presence"
            updated["manualAwayPresenceLatch"] = None
    return _apply_away_setpoint_logic(updated, was_away=was_away)

def _pause_entry_open_state(entry: dict) -> bool:
    """Return True when a configured inside-door entry should pause comfort."""
    if not isinstance(entry, dict):
        return False
    state = str(entry.get("state") or "").strip().lower()
    domain = str(entry.get("domain") or _pause_function_domain_from_entity_id(entry.get("entityId"))).strip().lower()
    if state in {"", "unknown", "unavailable", "none", "null"}:
        return False
    if entry.get("isClosed") is True:
        return False
    if entry.get("isClosed") is False:
        return True
    if domain == "cover":
        if state in {"open", "opening"}:
            return True
        if state in {"closed", "closing"}:
            return False
        pos = entry.get("currentPosition")
        try:
            if pos is not None:
                return float(pos) > 0
        except (TypeError, ValueError):
            return False
    if state in {"on", "open", "opened", "opening", "true", "1", "detected", "triggered"}:
        return True
    return False


def _refresh_pause_function_entry_states(entries: list[dict]) -> list[dict]:
    if not entries:
        return []
    ha_url, token = _ha_credentials_from_panel_config()
    if not ha_url or not token:
        return entries
    refreshed: list[dict] = []
    for entry in entries:
        current = dict(entry)
        entity_id = str(current.get("entityId") or "").strip()
        if not entity_id:
            refreshed.append(current)
            continue
        try:
            item = _ha_state_cached(ha_url, token, entity_id, ttl=1.0)
            if isinstance(item, dict) and item.get("entity_id"):
                fresh = _normalize_generic_entity(item)
                # Refresh the live state/device class from HA and use HA's
                # friendly_name for display. Older saved configs sometimes have
                # the raw entity id or picker label in name; the popup should
                # show the actual Home Assistant friendly name when available.
                attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
                saved_name = current.get("name") or current.get("friendlyName") or current.get("friendly_name")
                ha_name = attrs.get("friendly_name") or fresh.get("name")
                current.update(fresh)
                display_name = _preferred_ha_display_name(entity_id, ha_name, saved_name)
                current["name"] = display_name
                current["friendlyName"] = display_name
        except Exception as exc:
            print(f"Door pause state update failed for {entity_id}: {exc}", flush=True)
        refreshed.append(_normalize_pause_function_entry(current) or current)
    return refreshed


def _door_pause_away_target(thermostat: dict) -> int:
    return _away_target_for_current_mode(thermostat)


def _restore_from_door_pause(thermostat: dict, pause: dict) -> dict:
    restored = dict(thermostat)
    if pause.get("previousTargetTemp") is not None:
        restored["targetTemp"] = _number(pause.get("previousTargetTemp"), restored.get("targetTemp", 70), 45, 95)
    elif restored.get("lastComfortTarget") is not None:
        restored["targetTemp"] = _number(restored.get("lastComfortTarget"), restored.get("targetTemp", 70), 45, 95)
    if pause.get("previousLastComfortTarget") is not None:
        restored["lastComfortTarget"] = _number(pause.get("previousLastComfortTarget"), restored.get("targetTemp", 70), 45, 95)
    return restored


def _door_pause_countdown_allowed(thermostat: dict) -> bool:
    """Only let the comfort-pause countdown run while equipment is active.

    A door can be open all day while the thermostat is idle; that should show
    the open entry, but it should not burn down the timer or switch to away
    setpoints until cooling is actually cooling or heating is actually heating.
    Auto mode follows the currently active heat/cool side.
    """
    try:
        outputs = _thermostat_outputs(thermostat)
    except Exception:
        return False
    mode = _normalize_mode(thermostat.get("mode"), "")
    active_mode = _normalize_mode(thermostat.get("autoActiveMode"), "") if mode == "auto" else mode
    if active_mode == "cool" and bool(outputs.get("cool")):
        return True
    if active_mode == "heat" and bool(outputs.get("heat")):
        return True
    return False


def _apply_door_pause_logic(thermostat: dict) -> dict:
    """Apply the configured door countdown and temporary away setpoint.

    The selected entry and delay are persisted, but the active countdown,
    previous target and snooze state are runtime-only so normal sensor polling
    does not create extra SD-card writes.
    """
    t = _merge_thermostat_state(thermostat)
    pause = _normalize_pause_function(t.get("pauseFunction"))
    entries = pause.get("entries") or []
    if not entries:
        configured_entry = _configured_home_assistant_door_entry()
        if configured_entry:
            entries = [configured_entry]
    entries = _refresh_pause_function_entry_states(entries)
    pause["entries"] = entries
    if not entries:
        pause.update({
            "active": False,
            "pausedAt": 0,
            "previousTargetTemp": None,
            "previousLastComfortTarget": None,
            "activeEntityIds": [],
            "snoozeUntil": 0,
            "countdownAllowed": False,
            "countdownReason": "",
        })
        t["pauseFunction"] = pause
        return _merge_thermostat_state(t)

    now_ms = int(time.time() * 1000)
    duration_ms = _normalize_pause_function_duration(pause.get("durationMinutes"), 5) * 60000
    countdown_allowed = _door_pause_countdown_allowed(t)
    pause["countdownAllowed"] = bool(countdown_allowed)
    pause["countdownReason"] = "" if countdown_allowed else "waiting-for-active-hvac"

    open_entries: list[dict] = []
    for entry in entries:
        if _pause_entry_open_state(entry):
            if countdown_allowed or pause.get("active"):
                if not _number(entry.get("openedAt"), 0, 0):
                    entry["openedAt"] = now_ms
            else:
                # The entry is open, but the thermostat is idle or not actively
                # running the selected heat/cool side. Do not let the countdown
                # start or continue until equipment is actually running.
                entry["openedAt"] = 0
            open_entries.append(entry)
        else:
            entry["openedAt"] = 0

    if not open_entries:
        if pause.get("active"):
            t = _restore_from_door_pause(t, pause)
        pause.update({
            "active": False,
            "pausedAt": 0,
            "previousTargetTemp": None,
            "previousLastComfortTarget": None,
            "activeEntityIds": [],
            "snoozeUntil": 0,
            "countdownAllowed": False,
            "countdownReason": "",
        })
        t["pauseFunction"] = pause
        return _merge_thermostat_state(t)

    open_ids = [str(entry.get("entityId") or "") for entry in open_entries if str(entry.get("entityId") or "")]
    first_opened_at = min(int(_number(entry.get("openedAt"), now_ms, 0)) for entry in open_entries) if countdown_allowed else now_ms
    snooze_until = int(_number(pause.get("snoozeUntil"), 0, 0))

    if snooze_until and now_ms < snooze_until:
        if pause.get("active"):
            t = _restore_from_door_pause(t, pause)
        pause.update({
            "active": False,
            "pausedAt": 0,
            "previousTargetTemp": None,
            "previousLastComfortTarget": None,
            "activeEntityIds": [],
            "countdownAllowed": False,
            "countdownReason": "",
        })
    elif not bool(t.get("away")):
        if pause.get("active"):
            t["targetTemp"] = _door_pause_away_target(t)
            pause["activeEntityIds"] = open_ids
        elif not countdown_allowed:
            pause.update({
                "active": False,
                "pausedAt": 0,
                "previousTargetTemp": None,
                "previousLastComfortTarget": None,
                "activeEntityIds": [],
            })
        elif now_ms - first_opened_at >= duration_ms:
            pause["active"] = True
            pause["pausedAt"] = now_ms
            pause["previousTargetTemp"] = _number(t.get("targetTemp"), 70, 45, 95)
            pause["previousLastComfortTarget"] = _number(t.get("lastComfortTarget"), t.get("targetTemp", 70), 45, 95)
            pause["activeEntityIds"] = open_ids
            pause["snoozeUntil"] = 0
            t["targetTemp"] = _door_pause_away_target(t)
    else:
        # Already away: show the door state/countdown, but do not create a
        # second pause state over away mode.
        if pause.get("active"):
            t = _restore_from_door_pause(t, pause)
        pause.update({
            "active": False,
            "pausedAt": 0,
            "previousTargetTemp": None,
            "previousLastComfortTarget": None,
            "activeEntityIds": [],
            "countdownAllowed": False,
            "countdownReason": "",
        })

    if snooze_until and now_ms >= snooze_until:
        pause["snoozeUntil"] = 0
    t["pauseFunction"] = pause
    return _merge_thermostat_state(t)


def _apply_door_pause_snooze_request(existing: dict, minutes: object = None) -> dict:
    t = _merge_thermostat_state(existing)
    pause = _normalize_pause_function(t.get("pauseFunction"))

    # Some older/current configs keep the selected door only under
    # integrations.homeAssistant.doorEntity.  If pauseFunction.entries is empty,
    # _merge_thermostat_state intentionally clears runtime pause fields, including
    # snoozeUntil.  Hydrate the configured door before saving the snooze so a
    # snooze tap does not appear to work locally and then immediately re-open the
    # comfort-pause alert on the next backend status/control-loop pass.
    if not pause.get("entries"):
        configured_entry = _configured_home_assistant_door_entry()
        if configured_entry:
            pause["entries"] = [configured_entry]

    configured_minutes = _normalize_pause_function_duration(pause.get("durationMinutes"), 5)
    snooze_minutes = (
        configured_minutes
        if minutes in (None, "")
        else _normalize_pause_function_duration(minutes, configured_minutes)
    )
    now_ms = int(time.time() * 1000)
    if pause.get("active"):
        t = _restore_from_door_pause(t, pause)
    for entry in pause.get("entries") or []:
        if _pause_entry_open_state(entry):
            entry["openedAt"] = now_ms
    pause.update({
        "active": False,
        "pausedAt": 0,
        "previousTargetTemp": None,
        "previousLastComfortTarget": None,
        "activeEntityIds": [],
        "snoozeUntil": now_ms + snooze_minutes * 60000,
        "countdownAllowed": False,
        "countdownReason": "resumed",
    })
    t["pauseFunction"] = pause
    return _merge_thermostat_state(t)


def _apply_runtime_thermostat_logic(record: dict, *, notify: bool = True) -> dict:
    thermostat = record.get("thermostat") or {}
    was_away = bool(thermostat.get("away"))
    thermostat = _clear_expired_virtual_temp_override(thermostat)

    if not _virtual_temp_override_active(thermostat):
        room_mode = _room_temp_control_mode(thermostat)
        if room_mode == "external":
            # External means the selected Home Assistant sensor/climate entity is
            # primary. Onboard sensors remain a safety fallback if HA is missing.
            before_ha = thermostat
            thermostat = _apply_selected_ha_temperature_sensor_if_needed(thermostat)
            if thermostat is before_ha and LOCAL_TEMP_SENSOR_ENABLED:
                thermostat = _apply_local_temperature_sensor_if_needed(
                    {"thermostat": thermostat}, force_use=True
                )
        else:
            # Internal means the onboard HDC2080 pair is primary. Keep HA only as
            # a fallback when neither onboard sensor can provide a trusted value.
            if LOCAL_TEMP_SENSOR_ENABLED:
                sensor = _read_local_temperature_sensor()
                if sensor.get("available") and sensor.get("temperatureF") is not None:
                    thermostat = _apply_local_temperature_sensor_if_needed(
                        {"thermostat": thermostat}, sensor=sensor, force_use=True
                    )
                else:
                    thermostat = _apply_selected_ha_temperature_sensor_if_needed(thermostat)
            else:
                thermostat = _apply_selected_ha_temperature_sensor_if_needed(thermostat)

    thermostat = _apply_selected_ha_outdoor_temperature_sensor_if_needed(thermostat)
    updated = _apply_presence_away_logic(thermostat)
    updated = _apply_door_pause_logic(updated)
    updated = _apply_away_setpoint_logic(updated, was_away=was_away)
    updated = _apply_comfort_auto_switch_logic(updated, notify=notify)
    scheduled = _apply_thermostat_schedules(updated)
    scheduled = _apply_away_setpoint_logic(scheduled, was_away=was_away, finalize_restore=True)
    if scheduled != updated:
        _write_thermostat_record(scheduled, persist=True)
        updated = scheduled
    elif updated != thermostat:
        _write_thermostat_record(updated, persist=False)
    return updated


def _mark_thermostat_equipment_run(outputs: dict) -> None:
    now_ms = int(time.time() * 1000)
    try:
        # Keep the read/modify/write transaction atomic. Multiple output workers
        # can otherwise write stale relay markers over a newer thermostat state.
        with _THERMOSTAT_RECORD_LOCK:
            current = _read_thermostat_record()["thermostat"]
            changes: dict[str, float | bool] = {}
            was_heating = bool(current.get("heatRelayWasOn"))
            is_heating = bool(outputs.get("heat"))
            was_cooling = bool(current.get("coolRelayWasOn"))
            is_cooling = bool(outputs.get("cool"))

            if is_heating:
                changes["equipmentLastHeatRunAt"] = now_ms
                changes["lastHeatRunAt"] = now_ms
                changes["heatRelayWasOn"] = True
                if (not was_heating) or not _number(current.get("heatCycleStartedAt"), 0, 0):
                    changes["heatCycleStartedAt"] = now_ms
                    changes["heatCycleStoppedAt"] = 0
            elif was_heating:
                changes["heatRelayWasOn"] = False
                changes["heatCycleStartedAt"] = 0
                changes["heatCycleStoppedAt"] = now_ms

            if is_cooling:
                changes["equipmentLastCoolRunAt"] = now_ms
                changes["lastCoolRunAt"] = now_ms
                changes["coolRelayWasOn"] = True
                changes["coolFanHoldUntil"] = 0
                if (not was_cooling) or not _number(current.get("coolCycleStartedAt"), 0, 0):
                    changes["coolCycleStartedAt"] = now_ms
                    changes["coolCycleStoppedAt"] = 0
            elif was_cooling:
                remain_minutes = _number(current.get("coolFanRemainOnMinutes"), 2, 0, 15)
                changes["coolRelayWasOn"] = False
                changes["coolCycleStartedAt"] = 0
                changes["coolCycleStoppedAt"] = now_ms
                changes["coolFanHoldUntil"] = int(now_ms + remain_minutes * 60000) if remain_minutes > 0 else 0
            elif _number(current.get("coolFanHoldUntil"), 0, 0) and _number(current.get("coolFanHoldUntil"), 0, 0) <= now_ms:
                changes["coolFanHoldUntil"] = 0

            if not changes:
                return
            updated = {**current, **changes}
            _write_thermostat_record(updated, persist=False)
    except Exception as exc:
        print(f"Unable to mark HVAC equipment runtime: {exc}", flush=True)


def _minimum_cycle_runtime_ms(thermostat: dict, kind: str) -> int:
    key = "heatMinimumRuntimeMinutes" if kind == "heat" else "coolMinimumRuntimeMinutes"
    minutes = _number(thermostat.get(key), 2, 1, 30)
    return int(minutes * 60000)


def _apply_minimum_cycle_protection(
    thermostat: dict,
    *,
    kind: str,
    requested_on: bool,
    active_mode: str,
    safety_mode: str,
    now_ms: int,
    was_on: bool | None = None,
) -> tuple[bool, int, str]:
    """Apply minimum-on and minimum-off protection for one HVAC side.

    The configured value is intentionally used for both directions: once the
    side starts, it stays on for that many minutes during normal thermostat
    control; once it stops, it must remain off that many minutes before a
    normal restart. Manual Off, lockout buttons, opposite-mode changeover
    lockouts, and safety calls are allowed to shut equipment down.
    """
    kind = "heat" if kind == "heat" else "cool"
    locked = bool(thermostat.get("heatLocked" if kind == "heat" else "coolLocked"))
    relay_key = "heatRelayWasOn" if kind == "heat" else "coolRelayWasOn"
    started_key = "heatCycleStartedAt" if kind == "heat" else "coolCycleStartedAt"
    stopped_key = "heatCycleStoppedAt" if kind == "heat" else "coolCycleStoppedAt"
    was_on = bool(thermostat.get(relay_key)) if was_on is None else bool(was_on)
    min_ms = _minimum_cycle_runtime_ms(thermostat, kind)

    if locked or active_mode == "off":
        return False, 0, ""

    # Do not keep running a side just because it had been running if the user
    # changed modes, a changeover lockout is active, or safety needs the other
    # side. Minimum runtime only extends normal operation for the current side.
    may_extend_current_run = active_mode == kind and (not safety_mode or safety_mode == kind)
    if was_on and not requested_on and may_extend_current_run:
        # Older runtime records, external-output races, or a service restart can
        # leave relayWasOn=true without a cycle start marker. Still protect the
        # compressor and show the countdown immediately instead of silently
        # dropping the minimum-runtime hold. The async/control-loop marker will
        # write the synthesized start on its next pass.
        started_at = _number(thermostat.get(started_key), 0, 0)
        if started_at <= 0:
            started_at = now_ms
        until = int(started_at + min_ms)
        if until > now_ms:
            return True, until, "minimum-runtime"

    # Safety heat/cool must still be able to start immediately. Normal comfort
    # calls wait until the side has been off for the same configured duration.
    if (not was_on) and requested_on and not safety_mode:
        stopped_at = _number(thermostat.get(stopped_key), 0, 0)
        if stopped_at > 0:
            until = int(stopped_at + min_ms)
            if until > now_ms:
                return False, until, "minimum-off"

    return requested_on, 0, ""




def _normalize_fan_for_active_cooling(thermostat: dict) -> dict:
    """Cooling must always keep fan mode at Auto or On; Off is not valid."""
    if not isinstance(thermostat, dict):
        return thermostat
    if str(thermostat.get("fan") or "auto").strip().lower() != "off":
        return thermostat
    probe = dict(thermostat)
    probe["fan"] = "auto"
    try:
        outputs = _thermostat_outputs(probe)
    except Exception:
        outputs = {}
    if bool(outputs.get("cool")) or str(outputs.get("hvacAction") or "").lower() == "cooling":
        thermostat = dict(thermostat)
        thermostat["fan"] = "auto"
    return thermostat

def _thermostat_outputs(thermostat: dict) -> dict:
    mode = _allowed_mode_for_locks(_normalize_mode(thermostat.get("mode"), "cool"), thermostat, "cool")
    active_mode = thermostat.get("autoActiveMode") if mode == "auto" else mode
    active_mode = _normalize_mode(active_mode, "cool")
    current = _number(thermostat.get("currentTemp"), 70)
    target = _number(thermostat.get("targetTemp"), 70)
    safety_low = _number(thermostat.get("safetyLow"), thermostat.get("awayHeat", 55), 45, 93)
    safety_high = _number(thermostat.get("safetyHigh"), thermostat.get("awayCool", 85), safety_low + 2, 95)
    safety_mode = ""
    if current < safety_low and not thermostat.get("heatLocked"):
        safety_mode = "heat"
    elif current > safety_high and not thermostat.get("coolLocked"):
        safety_mode = "cool"
    if safety_mode:
        active_mode = safety_mode
        target = safety_low if safety_mode == "heat" else safety_high
    differential = 0 if safety_mode else _number(thermostat.get("temperatureDifferential"), 0, 0, 5)
    heat_was_on = _known_equipment_output_on(thermostat, "heat")
    cool_was_on = _known_equipment_output_on(thermostat, "cool")

    # Temperature differential is a start threshold, not an early shutoff.
    # Example with target 70 and differential 1:
    # - Cooling that is already running keeps cooling until the room reaches 70,
    #   then waits until the room rises to 71 before starting again.
    # - Heating that is already running keeps heating until the room reaches 70,
    #   then waits until the room falls to 69 before starting again.
    # A value of 0 preserves the original exact-setpoint behavior.
    heat_start_threshold = target - differential
    cool_start_threshold = target + differential
    starting_heat_call = current <= heat_start_threshold if differential > 0 else current < target
    starting_cool_call = current >= cool_start_threshold if differential > 0 else current > target
    heat = (
        (not thermostat.get("heatLocked"))
        and active_mode == "heat"
        and (current < target if heat_was_on else starting_heat_call)
    )
    cool = (
        (not thermostat.get("coolLocked"))
        and active_mode == "cool"
        and (current > target if cool_was_on else starting_cool_call)
    )
    pending_mode = ""
    manual_lockout_until = 0
    now_ms = int(time.time() * 1000)
    state_pending, state_until = _active_manual_changeover_pending(thermostat, now_ms=now_ms)
    if not safety_mode and mode != "auto" and state_pending in {"heat", "cool"}:
        pending_mode = state_pending
        manual_lockout_until = state_until
        heat = False
        cool = False
        active_mode = "lockout"
    # Do not infer a new manual changeover delay from old opposite-run timestamps
    # during every normal output calculation. The control handler arms
    # manualPendingMode/manualLockoutUntil only when the selected mode actually
    # changes. Recomputing it here made an already-selected Cool mode suddenly
    # enter "Cool Delay" after a repeated HA/status command or a late runtime
    # marker, even though no Heat -> Cool transition had occurred.
    heat_cycle_until = 0
    cool_cycle_until = 0
    heat_cycle_reason = ""
    cool_cycle_reason = ""
    heat, heat_cycle_until, heat_cycle_reason = _apply_minimum_cycle_protection(
        thermostat,
        kind="heat",
        requested_on=heat,
        active_mode=active_mode,
        safety_mode=safety_mode,
        now_ms=now_ms,
        was_on=heat_was_on,
    )
    cool, cool_cycle_until, cool_cycle_reason = _apply_minimum_cycle_protection(
        thermostat,
        kind="cool",
        requested_on=cool,
        active_mode=active_mode,
        safety_mode=safety_mode,
        now_ms=now_ms,
        was_on=cool_was_on,
    )
    if heat and cool:
        # Heat and cool must never be energized together. Favor the current
        # active/safety mode, otherwise fail safe by dropping cooling.
        if active_mode == "cool" or safety_mode == "cool":
            heat = False
        else:
            cool = False
    cycle_until = max(heat_cycle_until, cool_cycle_until)
    cycle_mode = "heat" if heat_cycle_until >= cool_cycle_until and heat_cycle_until else "cool" if cool_cycle_until else ""
    cycle_reason = heat_cycle_reason if cycle_mode == "heat" else cool_cycle_reason if cycle_mode == "cool" else ""

    active_hold_until = _number(thermostat.get("coolFanHoldUntil"), 0, 0)
    remain_minutes = _number(thermostat.get("coolFanRemainOnMinutes"), 2, 0, 15)
    # If cooling was on in the last control pass and this pass turns cooling
    # off, keep the fan output high immediately. _mark_thermostat_equipment_run
    # will then write coolFanHoldUntil, but the relay never sees a false/true
    # blip in between.
    starting_cool_fan_hold = cool_was_on and (not cool) and remain_minutes > 0
    cooling_fan_hold = (not cool) and (active_hold_until > now_ms or starting_cool_fan_hold)
    fan = bool(cool or cooling_fan_hold or thermostat.get("fan") == "on")
    action = "heating" if heat else "cooling" if cool else "fan" if fan else "idle"
    return {
        "fan": fan,
        "heat": heat,
        "cool": cool,
        "coolingFanHold": cooling_fan_hold,
        "hvacAction": action,
        "controlMode": active_mode,
        "safetyMode": safety_mode,
        "pendingMode": pending_mode,
        "manualLockoutUntil": manual_lockout_until,
        "minimumCycleUntil": cycle_until,
        "minimumCycleMode": cycle_mode,
        "minimumCycleReason": cycle_reason,
    }

def _thermostat_status_payload(*, refresh_runtime: bool = False, apply_hardware: bool = False) -> dict:
    serial = _stable_panel_serial()
    sw_version = _read_version_value()
    record = _read_thermostat_record()
    if refresh_runtime:
        thermostat = _apply_runtime_thermostat_logic(record)
        record = _read_thermostat_record()
        record["thermostat"] = thermostat
    else:
        thermostat = record["thermostat"]
    try:
        thermostat = _refresh_person_tracking_states(thermostat)
    except Exception as exc:
        # Person state refresh is helpful for the UI, but it must never make the
        # thermostat status endpoint fail or make the native app boot with blank
        # information. Keep the last saved thermostat record if HA/person refresh
        # has a bad value or an unexpected schema issue.
        print(f"Person tracking refresh skipped: {exc}", flush=True)
    outputs = _thermostat_outputs(thermostat)
    if (bool(outputs.get("cool")) or str(outputs.get("hvacAction") or "").lower() == "cooling") and str(thermostat.get("fan") or "auto").lower() == "off":
        thermostat = dict(thermostat)
        thermostat["fan"] = "auto"
    if apply_hardware:
        _apply_thermostat_outputs_to_hardware(outputs, thermostat)
    hvac_mode = thermostat["mode"] if thermostat["mode"] in {"off", "heat", "cool"} else str(thermostat.get("autoActiveMode") or "cool")
    presence_override = _normalize_presence_home_override(thermostat.get("presenceHomeOverride"))
    preset_mode = (
        "away"
        if thermostat.get("away")
        else "arriving"
        if presence_override and str(presence_override.get("reason") or "").strip().lower() == "arriving"
        else "home"
    )
    sync_status = _sync_status_payload()
    thermostat_detail = {
        **thermostat,
        "current_temperature": thermostat["currentTemp"],
        "currentTempUpdatedAt": thermostat.get("currentTempUpdatedAt", 0),
        "current_temp_updated_at": thermostat.get("currentTempUpdatedAt", 0),
        "target_temperature": thermostat["targetTemp"],
        "temperature": thermostat["targetTemp"],
        "hvac_mode": hvac_mode,
        "hvacMode": hvac_mode,
        "hvac_action": outputs["hvacAction"],
        "hvacAction": outputs["hvacAction"],
        "fan_mode": thermostat["fan"],
        "fanMode": thermostat["fan"],
        "preset_mode": preset_mode,
        "presetMode": preset_mode,
        "heatLocked": bool(thermostat.get("heatLocked")),
        "coolLocked": bool(thermostat.get("coolLocked")),
        "people": thermostat.get("people") or [],
        "autoAwayPeople": thermostat.get("autoAwayPeople") or [],
        "hvac_modes": _available_hvac_modes(thermostat),
        "hvacModes": _available_hvac_modes(thermostat),
        "outdoor_temperature": thermostat["outdoorTemp"],
        "outdoorWindSpeed": thermostat.get("outdoorWindSpeed", 0),
        "outdoor_wind_speed": thermostat.get("outdoorWindSpeed", 0),
        "outdoorWindUnit": thermostat.get("outdoorWindUnit", "mph"),
        "outdoor_wind_unit": thermostat.get("outdoorWindUnit", "mph"),
        "safetyMode": outputs.get("safetyMode", ""),
        "minimumCycleUntil": outputs.get("minimumCycleUntil", 0),
        "minimumCycleMode": outputs.get("minimumCycleMode", ""),
        "minimumCycleReason": outputs.get("minimumCycleReason", ""),
        "coolingFanHold": outputs.get("coolingFanHold", False),
        "coolFanHoldUntil": thermostat.get("coolFanHoldUntil", 0),
        "schedules": thermostat.get("schedules") or [],
        "airControlMode": _normalize_air_control_mode(thermostat.get("airControlMode")),
        "roomTempControlMode": _room_temp_control_mode(thermostat),
        "heatControlMode": _equipment_control_mode(thermostat, "heat"),
        "coolControlMode": _equipment_control_mode(thermostat, "cool"),
        "fanControlMode": _equipment_control_mode(thermostat, "fan"),
        "externalHeatEntity": _normalize_external_air_entity(thermostat.get("externalHeatEntity")),
        "externalCoolEntity": _normalize_external_air_entity(thermostat.get("externalCoolEntity")),
        "externalFanEntity": _normalize_external_air_entity(thermostat.get("externalFanEntity")),
        "relays": {"fan": outputs["fan"], "heat": outputs["heat"], "cool": outputs["cool"]},
        "outputs": outputs,
        "serial": serial,
        "unique_id": serial,
        "id": serial,
        "hardware_id": serial,
        "mac_address": _primary_mac_address(),
        "manufacturer": "IHA",
        "model": "Smart Thermostat Wall Panel",
        "sw_version": sw_version,
        "swVersion": sw_version,
        "sync": sync_status,
        "syncArmed": bool(sync_status.get("active")),
        "syncRemainingSeconds": int(sync_status.get("remainingSeconds") or 0),
    }
    payload = {
        "ok": True,
        "version": record["version"],
        "updatedAt": record["updatedAt"],
        "name": thermostat.get("name") or "IHA Thermostat",
        "serial": serial,
        "unique_id": serial,
        "id": serial,
        "hardware_id": serial,
        "mac_address": _primary_mac_address(),
        "manufacturer": "IHA",
        "model": "Smart Thermostat Wall Panel",
        "sw_version": sw_version,
        "swVersion": sw_version,
        "thermostat": thermostat_detail,
        # Home Assistant integration payload. Older panel builds returned the
        # thermostat details under `thermostat`; the HA custom integration reads
        # `climate`. Keep both so the wall UI and HA stay in sync.
        "climate": thermostat_detail,
        "outputs": outputs,
        "currentTemp": thermostat["currentTemp"],
        "targetTemp": thermostat["targetTemp"],
        "current_temperature": thermostat["currentTemp"],
        "currentTempUpdatedAt": thermostat.get("currentTempUpdatedAt", 0),
        "current_temp_updated_at": thermostat.get("currentTempUpdatedAt", 0),
        "target_temperature": thermostat["targetTemp"],
        "temperature": thermostat["targetTemp"],
        "mode": thermostat["mode"],
        "hvac_mode": hvac_mode,
        "hvacMode": hvac_mode,
        "hvac_action": outputs["hvacAction"],
        "hvacAction": outputs["hvacAction"],
        "fan": thermostat["fan"],
        "fan_mode": thermostat["fan"],
        "fanMode": thermostat["fan"],
        "preset_mode": preset_mode,
        "presetMode": preset_mode,
        "away": thermostat.get("away"),
        "heatLocked": bool(thermostat.get("heatLocked")),
        "coolLocked": bool(thermostat.get("coolLocked")),
        "people": thermostat.get("people") or [],
        "autoAwayPeople": thermostat.get("autoAwayPeople") or [],
        "hvac_modes": _available_hvac_modes(thermostat),
        "hvacModes": _available_hvac_modes(thermostat),
        "humidity": thermostat["humidity"],
        "outdoorTemp": thermostat["outdoorTemp"],
        "outdoor_temperature": thermostat["outdoorTemp"],
        "outdoorWindSpeed": thermostat.get("outdoorWindSpeed", 0),
        "outdoor_wind_speed": thermostat.get("outdoorWindSpeed", 0),
        "outdoorWindUnit": thermostat.get("outdoorWindUnit", "mph"),
        "outdoor_wind_unit": thermostat.get("outdoorWindUnit", "mph"),
        "safetyMode": outputs.get("safetyMode", ""),
        "minimumCycleUntil": outputs.get("minimumCycleUntil", 0),
        "minimumCycleMode": outputs.get("minimumCycleMode", ""),
        "minimumCycleReason": outputs.get("minimumCycleReason", ""),
        "coolingFanHold": outputs.get("coolingFanHold", False),
        "coolFanHoldUntil": thermostat.get("coolFanHoldUntil", 0),
        "schedules": thermostat.get("schedules") or [],
        "airControlMode": _normalize_air_control_mode(thermostat.get("airControlMode")),
        "roomTempControlMode": _room_temp_control_mode(thermostat),
        "heatControlMode": _equipment_control_mode(thermostat, "heat"),
        "coolControlMode": _equipment_control_mode(thermostat, "cool"),
        "fanControlMode": _equipment_control_mode(thermostat, "fan"),
        "externalHeatEntity": _normalize_external_air_entity(thermostat.get("externalHeatEntity")),
        "externalCoolEntity": _normalize_external_air_entity(thermostat.get("externalCoolEntity")),
        "externalFanEntity": _normalize_external_air_entity(thermostat.get("externalFanEntity")),
        "relays": {"fan": outputs["fan"], "heat": outputs["heat"], "cool": outputs["cool"]},
        "relayFan": outputs["fan"],
        "relayHeat": outputs["heat"],
        "relayCool": outputs["cool"],
        "sync": sync_status,
        "syncArmed": bool(sync_status.get("active")),
        "syncRemainingSeconds": int(sync_status.get("remainingSeconds") or 0),
    }
    return payload


def _schedule_thermostat_outputs_apply(thermostat: dict, *, reason: str = "control") -> None:
    """Apply relay/external HA outputs after the API response path returns.

    A touchscreen setpoint change should update and acknowledge quickly. External
    Home Assistant switch/input_boolean calls can take several seconds or time
    out on Wi-Fi, so do that work in a coalesced background pass instead of
    making /api/thermostat/control wait for it. The autonomous 2-second control
    loop still provides the normal safety net; this is just an immediate kick.
    """
    global _THERMOSTAT_ASYNC_OUTPUT_SEQ
    snapshot = _merge_thermostat_state(thermostat or {})
    with _THERMOSTAT_ASYNC_OUTPUT_LOCK:
        _THERMOSTAT_ASYNC_OUTPUT_SEQ += 1
        seq = _THERMOSTAT_ASYNC_OUTPUT_SEQ

    def _worker() -> None:
        try:
            # Coalesce rapid repeated taps/slider changes so only the newest
            # desired setpoint/mode drives the external HA equipment call.
            time.sleep(0.05)
            with _THERMOSTAT_ASYNC_OUTPUT_LOCK:
                if seq != _THERMOSTAT_ASYNC_OUTPUT_SEQ:
                    return
            latest = _read_thermostat_record().get("thermostat") or snapshot
            latest = _merge_thermostat_state(latest)
            outputs = _thermostat_outputs(latest)
            _apply_thermostat_outputs_to_hardware(outputs, latest)
        except Exception as exc:  # noqa: BLE001 - never let async apply kill the server
            print(f"Thermostat async output apply failed ({reason}): {exc}", flush=True)

    threading.Thread(target=_worker, name=f"thermostat-output-{reason}", daemon=True).start()


THERMOSTAT_COMFORT_TARGET_KEYS = ("targetTemp", "target_temperature", "temperature", "lastComfortTarget")


def _incoming_source(incoming: dict | None, *keys: str) -> str:
    if not isinstance(incoming, dict):
        return ""
    candidates = list(keys) or ["source"]
    candidates.extend(["source", "commandSource", "command_source", "clientSource", "client_source"])
    for key in candidates:
        value = str(incoming.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def _source_is_panel(source: str) -> bool:
    return str(source or "").strip().lower() in {"panel", "wall-panel", "wall_panel", "touchscreen", "native", "local"}


def _source_is_explicit_home_assistant_command(source: str) -> bool:
    """Return True when a /control write is a real HA user/service command.

    Recent touchscreen commands are protected from stale Home Assistant echoes,
    but HA climate-card, automation, and voice-assistant service calls still need
    to be accepted immediately. The custom HA integration now tags writes with
    these source values so the panel can tell an intentional command apart from
    an untagged stale status echo.
    """
    normalized = str(source or "").strip().lower().replace("_", "-")
    return normalized in {
        "home-assistant",
        "home-assistant-command",
        "ha-command",
        "hass-command",
        "voice-assistant",
        "voice-command",
        "automation",
        "external-command",
        # Direct peer-to-peer Sync commands are intentional writes, not stale HA
        # echoes. They must bypass the recent-touch protection on the destination.
        "peer-sync",
        "sync-receiver",
        "thermostat-sync",
    }


def _source_is_panel_guard_exempt(source: str) -> bool:
    return _source_is_panel(source) or _source_is_explicit_home_assistant_command(source)


def _target_value_from_incoming(incoming: dict) -> float | None:
    for key in THERMOSTAT_COMFORT_TARGET_KEYS:
        if key in incoming:
            return _number(incoming.get(key), 0, 0, 130)
    return None


def _strip_mode_changes(incoming: dict) -> dict:
    return {k: v for k, v in incoming.items() if k not in {"mode", "hvac_mode", "hvacMode"}}


def _panel_command_guard_active(existing: dict, at_key: str, *, now_ms: int | None = None, grace_ms: int | None = None) -> bool:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    at = _number(existing.get(at_key), 0, 0, None)
    window = PANEL_COMMAND_GRACE_MS if grace_ms is None else max(0, int(grace_ms))
    return at > 0 and now - at <= window


def _panel_target_guard_active(thermostat: dict, *, now_ms: int | None = None) -> bool:
    return _panel_command_guard_active(
        thermostat,
        "lastPanelTargetRequestAt",
        now_ms=now_ms,
        grace_ms=PANEL_TARGET_COMMAND_GRACE_MS,
    )


def _incoming_explicitly_leaves_away(incoming: dict | None) -> bool:
    if not isinstance(incoming, dict):
        return False
    if "away" in incoming and not _boolish(incoming.get("away")):
        return True
    preset = incoming.get("preset_mode", incoming.get("presetMode"))
    return str(preset or "").strip().lower() in {"home", "arriving"}


def _incoming_has_comfort_target_change(incoming: dict | None) -> bool:
    return isinstance(incoming, dict) and any(key in incoming for key in THERMOSTAT_COMFORT_TARGET_KEYS)


def _strip_comfort_target_changes(incoming: dict) -> dict:
    return {k: v for k, v in incoming.items() if k not in THERMOSTAT_COMFORT_TARGET_KEYS}


def _handle_thermostat_update_locked(payload: dict) -> dict:
    existing = _read_thermostat_record()["thermostat"]
    incoming = payload.get("thermostat", payload) if isinstance(payload, dict) else {}
    if not isinstance(incoming, dict):
        incoming = {}
    incoming_has_schedules = "schedules" in incoming

    requested_preset = str(incoming.get("preset_mode", incoming.get("presetMode")) or "").strip().lower()
    if requested_preset in {"home", "away", "arriving"}:
        # Treat Home Assistant preset commands exactly like the matching wall-panel
        # actions. Arriving may come from temporary peer Sync; Home and Away may
        # still be commanded directly in Home Assistant. The receiving thermostat
        # remains responsible for its own Away/Arrival and safety logic.
        incoming = dict(incoming)
        incoming["away"] = requested_preset == "away"
        incoming["awaySource"] = "manual" if requested_preset == "away" else ""
        incoming["manualAwayPresenceLatch"] = None
        if requested_preset == "away":
            incoming["presenceHomeOverride"] = None
        elif requested_preset == "arriving":
            if not _normalize_presence_home_override(incoming.get("presenceHomeOverride")):
                incoming["presenceHomeOverride"] = _presence_home_override_payload(
                    _thermostat_auto_away_entity_ids(existing),
                    reason="arriving",
                    duration_ms=ARRIVING_AWAY_BYPASS_MS,
                )
        else:
            # Explicit Home clears a prior timed Arrival hold. If the thermostat
            # was Away, the existing return-home guard below replaces this with
            # the normal presence hold so Auto Away cannot immediately reassert.
            incoming.setdefault("presenceHomeOverride", None)

    if (
        bool(existing.get("away"))
        and _incoming_has_comfort_target_change(incoming)
        and not _incoming_explicitly_leaves_away(incoming)
    ):
        # Away mode owns the active target. HA cards/automations may still
        # report the thermostat and may change fan/mode, but they must not
        # drag the active Away setpoint back to the last Home comfort value.
        incoming = _strip_comfort_target_changes(dict(incoming))

    pause_incoming = incoming.get("pauseFunction") if isinstance(incoming.get("pauseFunction"), dict) else {}
    pause_action = str(pause_incoming.get("action") or "").strip().lower()
    if pause_incoming and pause_action in {"resume", "snooze"}:
        # Resume always restarts the saved door-delay window. Keep the older
        # snooze action compatible with clients that intentionally provide a
        # specific snoozeMinutes value.
        requested_minutes = None if pause_action == "resume" else pause_incoming.get("snoozeMinutes")
        existing = _apply_door_pause_snooze_request(existing, requested_minutes)
        incoming = {k: v for k, v in incoming.items() if k != "pauseFunction"}
    elif pause_incoming and pause_incoming.get("snoozeMinutes") is not None:
        existing = _apply_door_pause_snooze_request(existing, pause_incoming.get("snoozeMinutes"))
        incoming = {k: v for k, v in incoming.items() if k != "pauseFunction"}

    bypass_mode = str(incoming.get("bypassChangeoverLockout") or "").strip().lower()
    if bypass_mode not in {"heat", "cool"} and incoming.get("bypassChangeoverLockout"):
        bypass_mode = str(existing.get("manualPendingMode") or existing.get("autoPendingMode") or "").strip().lower()
    existing_away_source = str(existing.get("awaySource") or "").strip().lower()
    incoming_requests_home = (
        "away" in incoming
        and bool(existing.get("away"))
        and existing_away_source in {"", "manual", "presence", "auto"}
        and not bool(incoming.get("away"))
    )
    if incoming_requests_home and not _normalize_presence_home_override(incoming.get("presenceHomeOverride")):
        incoming = dict(incoming)
        incoming["presenceHomeOverride"] = _presence_home_override_payload(
            _thermostat_auto_away_entity_ids(existing),
            reason="manual-return-home",
        )

    requested_mode_raw = incoming.get("mode", incoming.get("hvac_mode", incoming.get("hvacMode")))
    requested_mode = _normalize_mode(requested_mode_raw, "") if requested_mode_raw is not None else ""
    mode_change_source = _incoming_source(incoming, "modeChangeSource", "mode_change_source", "hvacModeChangeSource", "hvac_mode_change_source")
    target_change_source = _incoming_source(incoming, "targetChangeSource", "target_change_source", "temperatureChangeSource", "temperature_change_source")
    now_ms = int(time.time() * 1000)

    # The wall panel is the authority for very recent touches. Home Assistant can
    # echo old mode/setpoint values for a few seconds when it reconnects or when
    # its climate entity briefly goes unavailable. Those stale echoes used to
    # clear bypass countdowns or snap the set temperature backward, then forward.
    if requested_mode in {"off", "heat", "cool"} and not _source_is_panel_guard_exempt(mode_change_source):
        panel_mode = str(existing.get("lastPanelModeRequestMode") or "").strip().lower()
        if panel_mode and requested_mode != panel_mode and _panel_command_guard_active(existing, "lastPanelModeRequestAt", now_ms=now_ms):
            incoming = _strip_mode_changes(dict(incoming))
            requested_mode = ""

    incoming_target = _target_value_from_incoming(incoming)
    if incoming_target is not None and not _source_is_panel_guard_exempt(target_change_source):
        panel_target_at = _number(existing.get("lastPanelTargetRequestAt"), 0, 0, None)
        panel_target = _number(existing.get("lastPanelTargetTemp"), existing.get("targetTemp", 70), 0, 130)
        if panel_target_at > 0 and now_ms - panel_target_at <= PANEL_TARGET_COMMAND_GRACE_MS and abs(incoming_target - panel_target) >= 0.5:
            incoming = _strip_comfort_target_changes(dict(incoming))

    existing_manual_pending, existing_manual_until = _active_manual_changeover_pending(existing, now_ms=now_ms)
    if (
        existing_manual_pending in {"heat", "cool"}
        and requested_mode in {"heat", "cool"}
        and requested_mode != existing_manual_pending
        and not _source_is_panel_guard_exempt(mode_change_source)
        and bypass_mode not in {"heat", "cool"}
    ):
        # A just-tapped panel mode owns the compressor changeover window.
        # Home Assistant can briefly echo the previous HVAC mode while its
        # entity refresh catches up; accepting that stale opposite mode clears
        # the countdown and makes the bypass banner flash for only one cycle.
        incoming = dict(incoming)
        for key in ("mode", "hvac_mode", "hvacMode"):
            incoming.pop(key, None)
        requested_mode = ""

    if (
        requested_mode in {"off", "heat", "cool"}
        and not bool(existing.get("away"))
        and "away" not in incoming
        and "preset_mode" not in incoming
        and "presetMode" not in incoming
    ):
        # Keep normal Home-mode HVAC commands explicit, but do not let a HA
        # HVAC-mode command implicitly clear Away. The panel's Away preset must
        # win until the user or an automation sends preset/home or away=false.
        incoming = dict(incoming)
        incoming["away"] = False
        incoming["awaySource"] = ""

    if bypass_mode in {"heat", "cool"}:
        existing = dict(existing)
        now_ms = int(time.time() * 1000)
        if bypass_mode == "heat":
            existing["equipmentLastCoolRunAt"] = 0
            existing["lastCoolRunAt"] = 0
            existing["coolCycleStartedAt"] = 0
            existing["coolCycleStoppedAt"] = 0
        else:
            existing["equipmentLastHeatRunAt"] = 0
            existing["lastHeatRunAt"] = 0
            existing["heatCycleStartedAt"] = 0
            existing["heatCycleStoppedAt"] = 0
        existing["manualPendingMode"] = ""
        existing["manualLockoutUntil"] = 0
        existing["autoPendingMode"] = ""
        existing["autoLockoutUntil"] = 0

        # Remember the bypassed-into side long enough for a fast reverse tap to
        # still get compressor protection, even if the external relay marker has
        # not been written yet. Only arm this marker when the bypassed side is
        # actually calling based on the current selected mode/temperature.
        try:
            probe = _merge_thermostat_state(existing, {"mode": bypass_mode})
            out = _thermostat_outputs({**probe, "manualPendingMode": "", "manualLockoutUntil": 0})
            if bool(out.get(bypass_mode)):
                existing["lastManualChangeoverBypassMode"] = bypass_mode
                existing["lastManualChangeoverBypassAt"] = now_ms
        except Exception:
            existing["lastManualChangeoverBypassMode"] = bypass_mode
            existing["lastManualChangeoverBypassAt"] = now_ms
        incoming = {k: v for k, v in incoming.items() if k != "bypassChangeoverLockout"}

    was_away = bool(existing.get("away"))
    merged = _merge_thermostat_state(existing, incoming)
    merged = _apply_away_setpoint_logic(merged, was_away=was_away)

    if _source_is_panel(mode_change_source) and requested_mode in {"off", "heat", "cool"}:
        merged["lastPanelModeRequestMode"] = requested_mode
        merged["lastPanelModeRequestAt"] = now_ms
    if _source_is_panel(target_change_source):
        panel_target = _target_value_from_incoming(incoming)
        if panel_target is not None:
            merged["lastPanelTargetTemp"] = panel_target
            merged["lastPanelTargetRequestAt"] = now_ms

    existing_selected_mode = _normalize_mode(existing.get("mode"), "")
    is_real_manual_mode_transition = (
        requested_mode in {"heat", "cool"}
        and requested_mode != existing_selected_mode
    )

    if is_real_manual_mode_transition:
        # Manual / physical mode changes always win, whether they came from the
        # touchscreen buttons or Home Assistant. If opposite equipment has just
        # run, create the manual changeover cooldown immediately, even when the
        # newly selected side is not calling yet. This is what makes Cool -> Heat
        # and Heat -> Cool taps show the bypassable cooldown popup every time.
        merged["autoSwitchNotice"] = _empty_auto_switch_notice()
        merged["autoSwitchNoticeDismissed"] = _empty_auto_switch_notice_dismissed()
        merged["autoPendingMode"] = ""
        merged["autoLockoutUntil"] = 0
        now_ms = int(time.time() * 1000)
        manual_until = _manual_changeover_lockout_until_for_transition(existing, requested_mode, now_ms=now_ms)
        if manual_until <= now_ms:
            manual_until = _manual_changeover_lockout_until(merged, requested_mode, now_ms=now_ms)
        if manual_until > now_ms:
            merged["manualPendingMode"] = requested_mode
            merged["manualLockoutUntil"] = manual_until
        else:
            merged["manualPendingMode"] = ""
            merged["manualLockoutUntil"] = 0
        signal = _auto_switch_signal(merged)
        if signal and signal != requested_mode and _mode_available_for_auto_switch(merged, signal):
            merged["autoSwitchHold"] = {
                "active": True,
                "source": "manual",
                "mode": requested_mode,
                "suggestedMode": signal,
                "reason": "manual-override",
                "dismissed": False,
                "createdAt": now_ms,
            }
        else:
            merged["autoSwitchHold"] = _empty_auto_switch_hold()
    elif requested_mode == "off" and requested_mode != existing_selected_mode:
        merged["autoSwitchNotice"] = _empty_auto_switch_notice()
        merged["autoSwitchNoticeDismissed"] = _empty_auto_switch_notice_dismissed()
        merged["autoSwitchHold"] = _empty_auto_switch_hold()
        merged["autoPendingMode"] = ""
        merged["autoLockoutUntil"] = 0
        merged["manualPendingMode"] = ""
        merged["manualLockoutUntil"] = 0

    merged = _apply_comfort_auto_switch_logic(merged, notify=True)
    merged = _apply_away_setpoint_logic(merged, was_away=was_away, finalize_restore=True)
    merged = _normalize_fan_for_active_cooling(merged)
    _write_thermostat_record(merged)
    if incoming_has_schedules:
        saved_schedules = _normalize_schedule_entries(incoming.get("schedules"))
        _write_schedule_backup(saved_schedules)
        _mirror_schedules_to_panel_config(saved_schedules)

    # Keep the control endpoint fast. The native UI has already made the local
    # setpoint change visible, and it only needs confirmation that the setting
    # persisted. Hardware/external HA output application is kicked to a
    # background worker so a slow HA switch command cannot surface as
    # "Set temp failed: timed out" on the touchscreen.
    _schedule_thermostat_outputs_apply(merged, reason="control")
    return merged


def _handle_thermostat_update(payload: dict) -> dict:
    """Apply one thermostat command without racing the autonomous control loop.

    The control endpoint previously read a full thermostat snapshot, calculated
    the requested change, and then wrote that entire snapshot back. During that
    window the two-second runtime loop could record that cooling/heating was on.
    The older control snapshot would then overwrite those relay/cycle markers,
    making the next output pass briefly believe the equipment was off. On an
    external HA-controlled system that could produce a visible off/on command;
    on local GPIO it could unnecessarily rewrite the active relay.

    The thermostat record lock is re-entrant, so the existing read/write helpers
    remain safe while this command owns one consistent state transaction.
    """
    with _THERMOSTAT_RECORD_LOCK:
        accepted = _handle_thermostat_update_locked(payload)
    sync_changes = _sync_changes_from_control_payload(payload, accepted)
    if sync_changes:
        _schedule_armed_thermostat_sync(sync_changes)
    # Build the response after releasing the state transaction. Status assembly
    # may refresh Home Assistant person tracking and should never delay the
    # autonomous HVAC loop while holding the thermostat record lock.
    return _thermostat_status_payload(refresh_runtime=False, apply_hardware=False)


def _local_host_name() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "iha-thermostat"


def _local_ip_address() -> str:
    candidates: list[str] = []

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            candidates.append(probe.getsockname()[0])
    except Exception:
        pass

    try:
        for info in socket.getaddrinfo(_local_host_name(), None, socket.AF_INET, socket.SOCK_STREAM):
            candidates.append(info[4][0])
    except Exception:
        pass

    for ip_address in candidates:
        if ip_address and not ip_address.startswith("127."):
            return ip_address
    return candidates[0] if candidates else "127.0.0.1"


def _read_version_value() -> str:
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        version = "0.3.0"
    return version or "0.3.0"


def _format_duration(seconds: float | int | None) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        total = 0
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _system_uptime_seconds() -> float:
    try:
        raw = Path("/proc/uptime").read_text(encoding="utf-8").split()[0]
        return float(raw)
    except (OSError, IndexError, ValueError):
        return 0.0


def _cpu_temperature_c() -> float | None:
    for path in (Path("/sys/class/thermal/thermal_zone0/temp"),):
        try:
            raw = path.read_text(encoding="utf-8").strip()
            value = float(raw)
            return value / 1000.0 if value > 200 else value
        except (OSError, ValueError):
            continue
    return None


def _throttled_status() -> str:
    vcgencmd = "/usr/bin/vcgencmd" if Path("/usr/bin/vcgencmd").exists() else "vcgencmd"
    try:
        result = subprocess.run(
            [vcgencmd, "get_throttled"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "Unavailable"
    raw = result.stdout.strip()
    if not raw:
        return "Unavailable"
    if raw.endswith("0x0"):
        return "OK"
    return raw.replace("throttled=", "")


def _system_info_payload(server_port: int | str | None = None) -> dict:
    thermostat = _read_thermostat_record()["thermostat"]
    thermostat_name = str(thermostat.get("name") or "IHA Thermostat").strip() or "IHA Thermostat"
    system_uptime_seconds = _system_uptime_seconds()
    app_uptime_seconds = max(0, time.time() - APP_STARTED_AT)
    system_uptime = _format_duration(system_uptime_seconds)
    app_uptime = _format_duration(app_uptime_seconds)
    cpu_temp_c = _cpu_temperature_c()
    cpu_temp_f = (cpu_temp_c * 9 / 5 + 32) if cpu_temp_c is not None else None
    throttled = _throttled_status()
    thermal_summary = "Unavailable"
    if cpu_temp_c is not None:
        thermal_summary = f"{cpu_temp_c:.1f}°C / {cpu_temp_f:.1f}°F • {throttled}"
    ip_address = _local_ip_address()
    try:
        port_value = int(server_port) if server_port not in (None, "") else None
    except (TypeError, ValueError):
        port_value = None
    address = f"{ip_address}:{port_value}" if port_value else ip_address
    return {
        "ok": True,
        "ipAddress": ip_address,
        "port": port_value,
        "address": address,
        "host": _local_host_name(),
        "version": _read_version_value(),
        "name": thermostat_name,
        "thermostatName": thermostat_name,
        "uptime": f"System {system_uptime} • App {app_uptime}",
        "systemUptime": system_uptime,
        "appUptime": app_uptime,
        "systemUptimeSeconds": int(system_uptime_seconds),
        "appUptimeSeconds": int(app_uptime_seconds),
        "cpuTempC": round(cpu_temp_c, 1) if cpu_temp_c is not None else None,
        "cpuTempF": round(cpu_temp_f, 1) if cpu_temp_f is not None else None,
        "throttled": throttled,
        "thermal": thermal_summary,
    }



class _SimulatedRelayBackend:
    name = "simulated"
    available = False
    error = "GPIO library is unavailable or this is not running on Raspberry Pi hardware."

    def write(self, relay: str, on: bool) -> None:
        return None


class _GpioZeroRelayBackend:
    name = "gpiozero"
    available = True
    error = ""

    def __init__(self) -> None:
        from gpiozero import OutputDevice  # type: ignore
        active_high = not HARDWARE_RELAY_ACTIVE_LOW
        self.devices = {
            relay: OutputDevice(meta["gpio"], active_high=active_high, initial_value=False)
            for relay, meta in HARDWARE_RELAY_PINS.items()
        }

    def write(self, relay: str, on: bool) -> None:
        device = self.devices[relay]
        device.on() if on else device.off()


class _RpiGpioRelayBackend:
    name = "RPi.GPIO"
    available = True
    error = ""

    def __init__(self) -> None:
        import RPi.GPIO as GPIO  # type: ignore
        self.GPIO = GPIO
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        off_level = GPIO.HIGH if HARDWARE_RELAY_ACTIVE_LOW else GPIO.LOW
        for meta in HARDWARE_RELAY_PINS.values():
            GPIO.setup(meta["gpio"], GPIO.OUT, initial=off_level)

    def write(self, relay: str, on: bool) -> None:
        level_on = self.GPIO.LOW if HARDWARE_RELAY_ACTIVE_LOW else self.GPIO.HIGH
        level_off = self.GPIO.HIGH if HARDWARE_RELAY_ACTIVE_LOW else self.GPIO.LOW
        self.GPIO.output(HARDWARE_RELAY_PINS[relay]["gpio"], level_on if on else level_off)


class _NeoPixelRgbBackend:
    name = "neopixel"
    available = True
    error = ""

    def __init__(self) -> None:
        import board  # type: ignore
        import neopixel  # type: ignore
        pin_name = f"D{HARDWARE_RGB_PIN['gpio']}"
        pin = getattr(board, pin_name)
        self.pixels = neopixel.NeoPixel(pin, 1, auto_write=False)

    def write(self, on: bool, color: str) -> None:
        self.pixels[0] = _hex_to_rgb(color) if on else (0, 0, 0)
        self.pixels.show()


class _RpiWs281xRgbBackend:
    name = "rpi_ws281x"
    available = True
    error = ""

    def __init__(self) -> None:
        from rpi_ws281x import PixelStrip, Color  # type: ignore
        self.Color = Color
        self.strip = PixelStrip(1, HARDWARE_RGB_PIN["gpio"], 800000, 10, False, 255, 0)
        self.strip.begin()

    def write(self, on: bool, color: str) -> None:
        red, green, blue = _hex_to_rgb(color) if on else (0, 0, 0)
        self.strip.setPixelColor(0, self.Color(red, green, blue))
        self.strip.show()


class _SimulatedRgbBackend:
    name = "simulated"
    available = False
    error = "RGB library is unavailable. Install a supported addressable LED library if this pin drives a NeoPixel-style RGB LED."

    def write(self, on: bool, color: str) -> None:
        return None


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = str(value or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        raw = "35eaff"
    try:
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    except ValueError:
        return (53, 234, 255)


def _normalize_hex_color(value: object, fallback: str = "#35eaff") -> str:
    red, green, blue = _hex_to_rgb(str(value or fallback))
    return f"#{red:02x}{green:02x}{blue:02x}"


def _relay_backend():
    global _HARDWARE_RELAY_BACKEND
    if _HARDWARE_RELAY_BACKEND is not None:
        return _HARDWARE_RELAY_BACKEND

    errors: list[str] = []
    for backend_cls in (_GpioZeroRelayBackend, _RpiGpioRelayBackend):
        try:
            _HARDWARE_RELAY_BACKEND = backend_cls()
            return _HARDWARE_RELAY_BACKEND
        except Exception as exc:
            errors.append(f"{backend_cls.name}: {exc}")

    backend = _SimulatedRelayBackend()
    backend.error = "; ".join(errors) or backend.error
    _HARDWARE_RELAY_BACKEND = backend
    return backend


def _rgb_backend():
    global _HARDWARE_RGB_BACKEND
    if _HARDWARE_RGB_BACKEND is not None:
        return _HARDWARE_RGB_BACKEND

    errors: list[str] = []
    for backend_cls in (_NeoPixelRgbBackend, _RpiWs281xRgbBackend):
        try:
            _HARDWARE_RGB_BACKEND = backend_cls()
            return _HARDWARE_RGB_BACKEND
        except Exception as exc:
            errors.append(f"{backend_cls.name}: {exc}")

    backend = _SimulatedRgbBackend()
    backend.error = "; ".join(errors) or backend.error
    _HARDWARE_RGB_BACKEND = backend
    return backend


def _normalize_relay_outputs(relays: dict) -> dict[str, bool]:
    normalized = {name: bool(relays.get(name)) for name in ("fan", "heat", "cool")}
    # Never energize heating and cooling together from the hardware test page or thermostat output layer.
    if normalized["heat"] and normalized["cool"]:
        normalized["cool"] = False
    return normalized


def _remember_hvac_history_source(relays: dict, source: str) -> None:
    global _HVAC_HISTORY_LAST_RELAYS, _HVAC_HISTORY_LAST_SOURCE
    normalized = _normalize_relay_outputs(relays or {})
    _HVAC_HISTORY_LAST_RELAYS = normalized
    _HVAC_HISTORY_LAST_SOURCE = str(source or "thermostat")[:40]
    try:
        _record_hvac_history(normalized, _HVAC_HISTORY_LAST_SOURCE)
    except Exception as exc:
        print(f"Unable to record HVAC history: {exc}")


def _write_relay_outputs_locked(relays: dict, source: str, *, record_history: bool = True) -> None:
    global _HARDWARE_LAST_RELAYS, _HARDWARE_LAST_RELAY_SOURCE
    normalized = _normalize_relay_outputs(relays)
    backend = _relay_backend()
    previous = dict(_HARDWARE_LAST_RELAYS)
    for relay, on in normalized.items():
        # Reasserting an already-active relay on every control/status pass is
        # unnecessary and can create a short dropout on some relay/interface
        # boards. Only touch GPIO when the requested state actually changes.
        if bool(previous.get(relay)) == bool(on):
            continue
        try:
            backend.write(relay, on)
        except Exception as exc:
            backend.available = False
            backend.error = str(exc)
    _HARDWARE_LAST_RELAYS = normalized
    _HARDWARE_LAST_RELAY_SOURCE = source
    if record_history:
        _remember_hvac_history_source(normalized, source)


def _external_air_entity_for_kind(thermostat: dict, kind: str) -> dict | None:
    key = {
        "heat": "externalHeatEntity",
        "cool": "externalCoolEntity",
        "fan": "externalFanEntity",
    }.get(str(kind or "").strip().lower())
    return _normalize_external_air_entity(thermostat.get(key)) if key else None


def _external_ha_state_is_on(item: dict | None) -> bool | None:
    if not isinstance(item, dict):
        return None
    state = str(item.get("state") or "").strip().lower()
    if state in {"on", "true", "1"}:
        return True
    if state in {"off", "false", "0"}:
        return False
    return None


def _apply_external_ha_air_outputs(thermostat: dict, outputs: dict) -> None:
    """Apply each independently configured external HVAC output in Home Assistant."""
    ha_url, token = _ha_credentials_from_panel_config()
    if not ha_url or not token:
        return

    desired = {
        kind: bool(outputs.get(kind)) if _equipment_control_mode(thermostat, kind) == "external" else False
        for kind in ("heat", "cool", "fan")
    }
    if desired["heat"] and desired["cool"]:
        desired["cool"] = False

    with _EXTERNAL_HA_AIR_LOCK:
        now = time.monotonic()
        # Turn inactive outputs off first, then energize active outputs.
        ordered = [kind for kind in ("heat", "cool", "fan") if not desired[kind]] + [
            kind for kind in ("heat", "cool", "fan") if desired[kind]
        ]
        for kind in ordered:
            entry = _external_air_entity_for_kind(thermostat, kind)
            entity_id = str((entry or {}).get("entityId") or _EXTERNAL_HA_AIR_LAST_ENTITIES.get(kind) or "").strip()
            if not entity_id:
                _EXTERNAL_HA_AIR_LAST_STATES[kind] = None
                _EXTERNAL_HA_AIR_LAST_ENTITIES[kind] = ""
                _EXTERNAL_HA_AIR_RETRY_AFTER[kind] = 0.0
                continue
            want_on = desired[kind]
            last_entity = str(_EXTERNAL_HA_AIR_LAST_ENTITIES.get(kind) or "")
            last_state = _EXTERNAL_HA_AIR_LAST_STATES.get(kind)
            if last_entity and last_entity != entity_id and last_state is True:
                try:
                    _call_room_control_service(ha_url, token, last_entity, "off")
                except Exception as exc:
                    print(f"External {kind} previous entry release failed for {last_entity}: {exc}", flush=True)
            retry_after = float(_EXTERNAL_HA_AIR_RETRY_AFTER.get(kind) or 0.0)
            next_verify_at = float(_EXTERNAL_HA_AIR_NEXT_VERIFY_AT.get(kind) or 0.0)
            needs_call = last_entity != entity_id or last_state is None or bool(last_state) != want_on
            can_try_now = not (retry_after and now < retry_after and last_entity == entity_id)

            if (not needs_call) and can_try_now and now >= next_verify_at:
                try:
                    item = _ha_state_cached(ha_url, token, entity_id, ttl=0.0)
                    actual_on = _external_ha_state_is_on(item)
                    if actual_on is not None:
                        _EXTERNAL_HA_AIR_LAST_ENTITIES[kind] = entity_id
                        _EXTERNAL_HA_AIR_LAST_STATES[kind] = actual_on
                        needs_call = actual_on != want_on
                    _EXTERNAL_HA_AIR_NEXT_VERIFY_AT[kind] = now + EXTERNAL_HA_AIR_VERIFY_INTERVAL_SECONDS
                except Exception as exc:
                    _EXTERNAL_HA_AIR_RETRY_AFTER[kind] = time.monotonic() + 30.0
                    _EXTERNAL_HA_AIR_NEXT_VERIFY_AT[kind] = now + EXTERNAL_HA_AIR_VERIFY_INTERVAL_SECONDS
                    last_error_at = float(_EXTERNAL_HA_AIR_LAST_ERROR_AT.get(kind) or 0.0)
                    if now - last_error_at > 60.0:
                        _EXTERNAL_HA_AIR_LAST_ERROR_AT[kind] = now
                        print(f"External {kind} state verify failed for {entity_id}: {exc}", flush=True)
                    continue

            if not needs_call or not can_try_now:
                continue
            try:
                _call_room_control_service(ha_url, token, entity_id, "on" if want_on else "off")
                _EXTERNAL_HA_AIR_LAST_ENTITIES[kind] = entity_id
                _EXTERNAL_HA_AIR_LAST_STATES[kind] = want_on
                _EXTERNAL_HA_AIR_RETRY_AFTER[kind] = 0.0
                _EXTERNAL_HA_AIR_NEXT_VERIFY_AT[kind] = time.monotonic() + EXTERNAL_HA_AIR_VERIFY_INTERVAL_SECONDS
            except Exception as exc:
                _EXTERNAL_HA_AIR_RETRY_AFTER[kind] = time.monotonic() + 30.0
                last_error_at = float(_EXTERNAL_HA_AIR_LAST_ERROR_AT.get(kind) or 0.0)
                if now - last_error_at > 60.0:
                    _EXTERNAL_HA_AIR_LAST_ERROR_AT[kind] = now
                    print(f"External {kind} control failed for {entity_id}: {exc}", flush=True)


def _release_external_ha_air_outputs(thermostat: dict) -> None:
    # Retained for callers outside this module. Feeding all-false outputs turns
    # off every previously energized HA helper, including the external fan.
    _apply_external_ha_air_outputs(thermostat, {"heat": False, "cool": False, "fan": False})


def _expire_manual_hardware_locked(now: float | None = None) -> bool:
    """Release hardware-test relay mode if it was left on too long."""
    if not bool(_HARDWARE_MANUAL.get("active")):
        return False
    expires_at = float(_HARDWARE_MANUAL.get("expiresAt") or 0.0)
    if expires_at <= 0:
        return False
    now = time.time() if now is None else float(now)
    if now < expires_at:
        return False
    _HARDWARE_MANUAL["active"] = False
    _HARDWARE_MANUAL["relays"] = {"fan": False, "heat": False, "cool": False}
    _HARDWARE_MANUAL["activatedAt"] = 0.0
    _HARDWARE_MANUAL["expiresAt"] = 0.0
    print("Manual hardware relay override expired; returning to thermostat control.", flush=True)
    return True


def _expire_manual_hardware_if_needed() -> bool:
    with _HARDWARE_LOCK:
        return _expire_manual_hardware_locked()


def _apply_thermostat_outputs_to_hardware(outputs: dict, thermostat: dict | None = None) -> None:
    """Route room outputs independently to onboard GPIO or Home Assistant."""
    global _HVAC_OUTPUT_LAST_LOG_SIGNATURE
    thermostat = _merge_thermostat_state(thermostat or _read_thermostat_record().get("thermostat") or {})
    modes = {kind: _equipment_control_mode(thermostat, kind) for kind in ("fan", "heat", "cool")}
    hardware_relays = {
        kind: bool(outputs.get(kind)) if modes[kind] == "internal" else False
        for kind in ("fan", "heat", "cool")
    }
    any_external = any(mode == "external" for mode in modes.values())
    any_internal = any(mode == "internal" for mode in modes.values())
    history_source = "thermostat-mixed" if any_external and any_internal else ("thermostat-external" if any_external else "thermostat")
    log_signature = (
        bool(outputs.get("fan")),
        bool(outputs.get("heat")),
        bool(outputs.get("cool")),
        str(outputs.get("pendingMode") or ""),
        str(outputs.get("minimumCycleMode") or ""),
        str(outputs.get("minimumCycleReason") or ""),
        bool(outputs.get("coolingFanHold")),
        tuple((kind, modes[kind]) for kind in ("fan", "heat", "cool")),
    )
    if log_signature != _HVAC_OUTPUT_LAST_LOG_SIGNATURE:
        _HVAC_OUTPUT_LAST_LOG_SIGNATURE = log_signature
        print(
            "HVAC output transition: "
            f"mode={thermostat.get('mode')} current={_number(thermostat.get('currentTemp'), 70):g} "
            f"target={_number(thermostat.get('targetTemp'), 70):g} "
            f"fan={int(bool(outputs.get('fan')))} heat={int(bool(outputs.get('heat')))} cool={int(bool(outputs.get('cool')))} "
            f"pending={outputs.get('pendingMode') or '-'} minimum={outputs.get('minimumCycleMode') or '-'}:{outputs.get('minimumCycleReason') or '-'} "
            f"fan_hold={int(bool(outputs.get('coolingFanHold')))} "
            f"sources=fan:{modes['fan']},heat:{modes['heat']},cool:{modes['cool']}",
            flush=True,
        )
    with _HARDWARE_LOCK:
        _expire_manual_hardware_locked()
        if _HARDWARE_MANUAL.get("active"):
            return
        _write_relay_outputs_locked(
            hardware_relays,
            history_source,
            record_history=not any_external,
        )
    _apply_external_ha_air_outputs(thermostat, outputs)
    if any_external:
        _remember_hvac_history_source(
            {"fan": outputs.get("fan"), "heat": outputs.get("heat"), "cool": outputs.get("cool")},
            history_source,
        )
    _mark_thermostat_equipment_run(outputs)


def _set_manual_relay(relay: str, on: bool) -> dict:
    relay = str(relay or "").strip().lower()
    if relay not in HARDWARE_RELAY_PINS:
        raise ValueError("Unknown relay")
    with _HARDWARE_LOCK:
        relays = dict(_HARDWARE_MANUAL.get("relays") or {"fan": False, "heat": False, "cool": False})
        relays[relay] = bool(on)
        if relay == "heat" and on:
            relays["cool"] = False
        if relay == "cool" and on:
            relays["heat"] = False
        now = time.time()
        _HARDWARE_MANUAL["active"] = True
        _HARDWARE_MANUAL["relays"] = _normalize_relay_outputs(relays)
        _HARDWARE_MANUAL["activatedAt"] = now
        _HARDWARE_MANUAL["expiresAt"] = now + MANUAL_HARDWARE_TIMEOUT_SECONDS if MANUAL_HARDWARE_TIMEOUT_SECONDS > 0 else 0.0
        _write_relay_outputs_locked(_HARDWARE_MANUAL["relays"], "manual")
    return _hardware_status_payload()


def _release_manual_hardware() -> dict:
    with _HARDWARE_LOCK:
        _HARDWARE_MANUAL["active"] = False
        _HARDWARE_MANUAL["relays"] = {"fan": False, "heat": False, "cool": False}
        _HARDWARE_MANUAL["activatedAt"] = 0.0
        _HARDWARE_MANUAL["expiresAt"] = 0.0
    thermostat = _read_thermostat_record()["thermostat"]
    _apply_thermostat_outputs_to_hardware(_thermostat_outputs(thermostat))
    return _hardware_status_payload()


def _set_rgb_hardware(on: bool, color: object) -> dict:
    with _HARDWARE_LOCK:
        _HARDWARE_RGB["on"] = bool(on)
        _HARDWARE_RGB["color"] = _normalize_hex_color(color, _HARDWARE_RGB.get("color") or "#35eaff")
        backend = _rgb_backend()
        try:
            backend.write(bool(_HARDWARE_RGB["on"]), str(_HARDWARE_RGB["color"]))
        except Exception as exc:
            backend.available = False
            backend.error = str(exc)
    return _hardware_status_payload()




def _normalize_motion_config(value: object) -> dict:
    raw = value if isinstance(value, dict) else {}
    try:
        requested_poll = int(raw.get("pollSeconds", HARDWARE_MOTION_DEFAULT_CONFIG["pollSeconds"]))
    except (TypeError, ValueError):
        requested_poll = HARDWARE_MOTION_DEFAULT_CONFIG["pollSeconds"]
    poll_seconds = min(HARDWARE_MOTION_POLL_OPTIONS, key=lambda item: abs(item - requested_poll))

    try:
        requested_sensitivity = int(raw.get("sensitivityLevel", HARDWARE_MOTION_DEFAULT_CONFIG["sensitivityLevel"]))
    except (TypeError, ValueError):
        requested_sensitivity = HARDWARE_MOTION_DEFAULT_CONFIG["sensitivityLevel"]
    # The current resistor-divider hardware provides only two stable DC levels:
    # 0V (datasheet level 0, maximum sensitivity) and about 0.4 x VDD
    # (datasheet level 25, substantially reduced sensitivity).
    sensitivity_level = 25 if requested_sensitivity >= 13 else 0

    try:
        requested_on_time = int(raw.get("onTimeSeconds", HARDWARE_MOTION_DEFAULT_CONFIG["onTimeSeconds"]))
    except (TypeError, ValueError):
        requested_on_time = HARDWARE_MOTION_DEFAULT_CONFIG["onTimeSeconds"]
    # At approximately 0.4 x VDD, the S18-L262B-2 digital ONTIME table selects
    # the 600-second range. LOW selects the 2-second range.
    on_time_seconds = 600 if requested_on_time >= 301 else 2
    return {
        "pollSeconds": poll_seconds,
        "sensitivityLevel": sensitivity_level,
        "onTimeSeconds": on_time_seconds,
    }


def _motion_config_from_panel() -> dict:
    record = _read_panel_config_record()
    config = record.get("config") if isinstance(record, dict) else {}
    hardware = config.get("hardware") if isinstance(config, dict) else {}
    motion = hardware.get("motion") if isinstance(hardware, dict) else {}
    return _normalize_motion_config(motion)


def _write_motion_config(updates: object) -> dict:
    current = _motion_config_from_panel()
    if isinstance(updates, dict):
        current.update({key: updates[key] for key in ("pollSeconds", "sensitivityLevel", "onTimeSeconds") if key in updates})
    normalized = _normalize_motion_config(current)

    record = _read_panel_config_record()
    config = _deepcopy_json(record.get("config") or {})
    hardware = config.get("hardware") if isinstance(config.get("hardware"), dict) else {}
    hardware = _deepcopy_json(hardware)
    hardware["motion"] = normalized
    config["hardware"] = hardware
    _write_panel_config_record(config)
    return normalized


class _UnavailableMotionBackend:
    backend = "unavailable"
    available = False

    def __init__(self, error: str):
        self.error = str(error or "Motion GPIO is unavailable")

    def apply(self, config: dict) -> None:
        return

    def read(self) -> bool:
        return False


class _GpioZeroMotionBackend:
    backend = "gpiozero"
    available = True
    error = ""

    def __init__(self):
        from gpiozero import DigitalInputDevice, OutputDevice  # type: ignore

        self.rel = DigitalInputDevice(
            HARDWARE_MOTION_PINS["rel"]["gpio"],
            pull_up=None,
            active_state=True,
            bounce_time=0.05,
        )
        self.sens = OutputDevice(
            HARDWARE_MOTION_PINS["sens"]["gpio"],
            active_high=True,
            initial_value=False,
        )
        self.ontime = OutputDevice(
            HARDWARE_MOTION_PINS["ontime"]["gpio"],
            active_high=True,
            initial_value=False,
        )

    def apply(self, config: dict) -> None:
        if int(config.get("sensitivityLevel") or 0) >= 25:
            self.sens.on()
        else:
            self.sens.off()
        if int(config.get("onTimeSeconds") or 2) >= 600:
            self.ontime.on()
        else:
            self.ontime.off()

    def read(self) -> bool:
        return bool(self.rel.is_active)


def _motion_backend():
    global _HARDWARE_MOTION_BACKEND
    if _HARDWARE_MOTION_BACKEND is not None:
        return _HARDWARE_MOTION_BACKEND
    try:
        _HARDWARE_MOTION_BACKEND = _GpioZeroMotionBackend()
    except Exception as exc:
        _HARDWARE_MOTION_BACKEND = _UnavailableMotionBackend(str(exc))
    return _HARDWARE_MOTION_BACKEND


def _motion_status_payload(*, apply_config: bool = True) -> dict:
    now = time.time()
    with _HARDWARE_MOTION_LOCK:
        config = _motion_config_from_panel()
        backend = _motion_backend()
        if apply_config:
            try:
                backend.apply(config)
            except Exception as exc:
                backend.available = False
                backend.error = str(exc)
        motion = False
        if backend.available:
            try:
                motion = bool(backend.read())
            except Exception as exc:
                backend.available = False
                backend.error = str(exc)
        previous = bool(_HARDWARE_MOTION_RUNTIME.get("motion"))
        if motion != previous or not float(_HARDWARE_MOTION_RUNTIME.get("lastChangedAt") or 0.0):
            _HARDWARE_MOTION_RUNTIME["lastChangedAt"] = now
        _HARDWARE_MOTION_RUNTIME["motion"] = motion
        _HARDWARE_MOTION_RUNTIME["lastReadAt"] = now

        sensitivity_level = int(config["sensitivityLevel"])
        on_time_seconds = int(config["onTimeSeconds"])
        return {
            "ok": True,
            "available": bool(backend.available),
            "backend": str(getattr(backend, "backend", "unavailable")),
            "error": str(getattr(backend, "error", "") or ""),
            "motion": motion,
            "state": "motion" if motion else "clear",
            "lastChangedAt": int(_HARDWARE_MOTION_RUNTIME.get("lastChangedAt") or now),
            "readAt": int(now),
            "pins": _deepcopy_json(HARDWARE_MOTION_PINS),
            "config": config,
            "choices": {
                "pollSeconds": list(HARDWARE_MOTION_POLL_OPTIONS),
                "sensitivity": [
                    {"value": 0, "label": "Maximum", "detail": "Datasheet level 0 of 31; SENS = 0V"},
                    {"value": 25, "label": "Reduced", "detail": "Approx. datasheet level 25 of 31; SENS ~= 0.4 x VDD"},
                ],
                "onTime": [
                    {"value": 2, "label": "2 seconds", "detail": "ONTIME = 0V"},
                    {"value": 600, "label": "10 minutes", "detail": "ONTIME ~= 0.4 x VDD"},
                ],
            },
            "display": {
                "sensitivity": "Maximum (level 0)" if sensitivity_level == 0 else "Reduced (level 25)",
                "onTime": "2 seconds" if on_time_seconds == 2 else "10 minutes",
            },
            "electrical": {
                "stableBoardLevels": 2,
                "lowRatioVdd": 0.0,
                "highRatioVdd": 0.4,
                "note": "The installed 15k/10k dividers expose two stable DC settings. Continuous analog adjustment requires a DAC or filtered PWM hardware revision.",
            },
        }


def _set_motion_hardware(payload: object) -> dict:
    updates = payload if isinstance(payload, dict) else {}
    _write_motion_config(updates)
    # pollSeconds is a panel refresh preference, not an electrical setting.
    # Re-driving SENS and ONTIME for a poll-only change adds unnecessary GPIO
    # work and previously made the innocent 3s -> 6s selection share the same
    # failure path as a hardware output change.
    changes_hardware_output = any(key in updates for key in ("sensitivityLevel", "onTimeSeconds"))
    return _motion_status_payload(apply_config=changes_hardware_output)


def _initialize_motion_hardware() -> None:
    try:
        _motion_status_payload(apply_config=True)
    except Exception as exc:
        print(f"Motion sensor GPIO initialization failed: {exc}", flush=True)


def _parse_i2cdetect_output(output: str) -> list[dict]:
    devices: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        row_label, _, remainder = line.partition(":")
        try:
            row_base = int(row_label, 16)
        except ValueError:
            continue
        for column, token in enumerate(remainder.split()):
            token = token.strip()
            if token == "--":
                continue
            address = row_base + column
            if token == "UU":
                devices.append({"address": f"0x{address:02X}", "decimal": address, "status": "in-use"})
                continue
            try:
                parsed = int(token, 16)
            except ValueError:
                continue
            devices.append({"address": f"0x{parsed:02X}", "decimal": parsed, "status": "found"})
    devices.sort(key=lambda item: item["decimal"])
    return devices

def _i2c_disabled_payload(now: float, detail: str = "") -> dict:
    # A missing /dev/i2c-1 is a setup/not-installed state, not an application
    # failure. Keep it out of the red error path so the hardware page stays
    # calm until real I2C sensors are added.
    notice = (
        f"I2C bus {HARDWARE_I2C_BUS} is not active yet. "
        "This is OK until I2C sensors are installed."
    )
    action = "Run scripts/install-pi.sh, then reboot once before I2C sensors will appear."
    if detail:
        action = f"{action} ({detail})"
    return {
        "backend": "disabled",
        "bus": HARDWARE_I2C_BUS,
        "devicePath": str(HARDWARE_I2C_DEVICE),
        "enabled": False,
        "available": False,
        "addresses": [],
        "devices": [],
        "scannedAt": int(now),
        "notice": notice,
        "action": action,
        "error": "",
    }


def _friendly_i2c_error(exc: Exception | str) -> str:
    raw = str(exc or "").strip()
    lower = raw.lower()
    if "no such file" in lower or str(HARDWARE_I2C_DEVICE) in raw and not HARDWARE_I2C_DEVICE.exists():
        return _i2c_disabled_payload(time.time(), raw).get("error", raw)
    if "permission denied" in lower or "could not open file" in lower and "permission" in lower:
        return (
            f"I2C permission denied for {HARDWARE_I2C_DEVICE}. "
            "The service user must be in the i2c group; run scripts/install-pi.sh, then log out/in or reboot."
        )
    if isinstance(exc, FileNotFoundError) or "no such file or directory: 'i2cdetect'" in lower or "i2cdetect" in lower and "not found" in lower:
        return "i2cdetect is not installed. Run scripts/install-pi.sh to install i2c-tools."
    return raw or "I2C scan failed"


def _scan_i2c_with_i2cdetect() -> dict:
    result = subprocess.run(
        ["i2cdetect", "-y", str(HARDWARE_I2C_BUS)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=4,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "i2cdetect failed")
    devices = _parse_i2cdetect_output(result.stdout)
    return {
        "backend": "i2cdetect",
        "bus": HARDWARE_I2C_BUS,
        "devicePath": str(HARDWARE_I2C_DEVICE),
        "enabled": True,
        "available": True,
        "addresses": [item["address"] for item in devices],
        "devices": devices,
        "error": "",
    }


def _scan_i2c_with_smbus() -> dict:
    try:
        from smbus2 import SMBus  # type: ignore
    except Exception:
        from smbus import SMBus  # type: ignore
    devices: list[dict] = []
    with SMBus(HARDWARE_I2C_BUS) as bus:
        for address in range(0x03, 0x78):
            try:
                bus.read_byte(address)
            except Exception:
                continue
            devices.append({"address": f"0x{address:02X}", "decimal": address, "status": "found"})
    devices.sort(key=lambda item: item["decimal"])
    return {
        "backend": "smbus",
        "bus": HARDWARE_I2C_BUS,
        "devicePath": str(HARDWARE_I2C_DEVICE),
        "enabled": True,
        "available": True,
        "addresses": [item["address"] for item in devices],
        "devices": devices,
        "error": "",
    }


def _scan_i2c_devices(force: bool = False) -> dict:
    now = time.time()
    cached = _HARDWARE_LAST_I2C_SCAN.get("payload")
    if not force and cached and now - float(_HARDWARE_LAST_I2C_SCAN.get("at") or 0) < 3:
        return cached
    if not force and not cached:
        return {
            "backend": "not-scanned",
            "bus": HARDWARE_I2C_BUS,
            "devicePath": str(HARDWARE_I2C_DEVICE),
            "enabled": HARDWARE_I2C_DEVICE.exists(),
            "available": False,
            "addresses": [],
            "devices": [],
            "scannedAt": 0,
            "notice": "Open Hardware Information or press Refresh Hardware to scan I2C.",
            "error": "",
        }

    if not HARDWARE_I2C_DEVICE.exists():
        payload = _i2c_disabled_payload(now, f"missing {HARDWARE_I2C_DEVICE}")
        _HARDWARE_LAST_I2C_SCAN["at"] = now
        _HARDWARE_LAST_I2C_SCAN["payload"] = payload
        return payload

    errors: list[str] = []
    for scanner in (_scan_i2c_with_i2cdetect, _scan_i2c_with_smbus):
        try:
            payload = scanner()
            payload["bus"] = HARDWARE_I2C_BUS
            payload["scannedAt"] = int(now)
            _HARDWARE_LAST_I2C_SCAN["at"] = now
            _HARDWARE_LAST_I2C_SCAN["payload"] = payload
            return payload
        except Exception as exc:
            friendly = _friendly_i2c_error(exc)
            if friendly and friendly not in errors:
                errors.append(friendly)
    payload = {
        "backend": "unavailable",
        "bus": HARDWARE_I2C_BUS,
        "devicePath": str(HARDWARE_I2C_DEVICE),
        "enabled": HARDWARE_I2C_DEVICE.exists(),
        "available": False,
        "addresses": [],
        "devices": [],
        "scannedAt": int(now),
        "error": "; ".join(error for error in errors if error) or "No I2C scanner available",
    }
    _HARDWARE_LAST_I2C_SCAN["at"] = now
    _HARDWARE_LAST_I2C_SCAN["payload"] = payload
    return payload



def _fahrenheit_from_celsius(value_c: float) -> float:
    return value_c * 9.0 / 5.0 + 32.0


def _parse_i2c_address(value: str) -> int | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    try:
        return int(raw, 16 if raw.startswith("0x") else 10)
    except ValueError:
        return None


def _read_hwmon_temperature_sensor() -> dict | None:
    """Read kernel-exposed I2C/1-Wire ambient temperature sensors.

    This intentionally avoids the Pi CPU thermal zone. It only looks under
    /sys/bus/i2c and /sys/bus/w1 so it can be used as a room sensor fallback
    when Home Assistant or Wi-Fi is down.
    """
    candidates: list[Path] = []
    candidates.extend(Path("/sys/bus/i2c/devices").glob("*/hwmon/hwmon*/temp*_input"))
    candidates.extend(Path("/sys/bus/w1/devices").glob("28-*/w1_slave"))

    for path in candidates:
        try:
            if path.name == "w1_slave":
                raw = path.read_text(encoding="utf-8")
                if "YES" not in raw:
                    continue
                marker = "t="
                if marker not in raw:
                    continue
                temp_c = float(raw.rsplit(marker, 1)[1].strip()) / 1000.0
            else:
                raw = path.read_text(encoding="utf-8").strip()
                temp_c = float(raw) / 1000.0
            if not (-40.0 <= temp_c <= 60.0):
                continue
            name_path = path.parent / "name"
            try:
                label = name_path.read_text(encoding="utf-8").strip() or path.parent.name
            except OSError:
                label = path.parent.parent.name if path.parent.parent.name else path.parent.name
            return {
                "ok": True,
                "available": True,
                "temperatureF": round(_fahrenheit_from_celsius(temp_c), 1),
                "temperatureC": round(temp_c, 2),
                "source": "hwmon",
                "label": label,
                "path": str(path),
                "error": "",
            }
        except (OSError, ValueError):
            continue
    return None


def _smbus_class():
    try:
        from smbus2 import SMBus  # type: ignore
        return SMBus
    except Exception:
        from smbus import SMBus  # type: ignore
        return SMBus


def _read_tmp102_temperature(bus, address: int) -> float:
    data = bus.read_i2c_block_data(address, 0x00, 2)
    raw = ((int(data[0]) << 8) | int(data[1])) >> 4
    if raw & 0x800:
        raw -= 1 << 12
    return raw * 0.0625


def _read_sht3x_temperature(bus, address: int) -> float:
    bus.write_i2c_block_data(address, 0x24, [0x00])
    time.sleep(0.02)
    data = bus.read_i2c_block_data(address, 0x00, 6)
    raw = (int(data[0]) << 8) | int(data[1])
    return -45.0 + 175.0 * (raw / 65535.0)


def _parse_i2c_addresses(value: str, default: tuple[int, ...] = ()) -> list[int]:
    addresses: list[int] = []
    for part in str(value or "").replace(";", ",").split(","):
        address = _parse_i2c_address(part)
        if address is None or not (0x03 <= address <= 0x77) or address in addresses:
            continue
        addresses.append(address)
    return addresses or list(default)


def _read_hdc2080_measurement(bus, address: int) -> dict:
    """Trigger and read one HDC2080 temperature/humidity conversion."""
    bus.write_byte_data(address, 0x0F, 0x01)
    # Default 14-bit temperature + humidity conversion is about 1.27 ms. Ten
    # milliseconds leaves comfortable margin without slowing the 5-second poll.
    time.sleep(0.010)
    data = bus.read_i2c_block_data(address, 0x00, 4)
    if len(data) < 4:
        raise OSError(f"short HDC2080 read ({len(data)} bytes)")

    raw_temp = int(data[0]) | (int(data[1]) << 8)
    raw_humidity = int(data[2]) | (int(data[3]) << 8)
    temp_c = (raw_temp / 65536.0) * 165.0 - 40.0
    humidity = (raw_humidity / 65536.0) * 100.0
    # Thermostat control accepts the same practical range used for Home
    # Assistant temperature inputs: -40F through 130F.
    if not (-40.0 <= _fahrenheit_from_celsius(temp_c) <= 130.0):
        raise ValueError(f"unreasonable room temperature {temp_c:.2f}C")
    if not (0.0 <= humidity <= 100.0):
        raise ValueError(f"unreasonable humidity {humidity:.2f}%")
    return {
        "ok": True,
        "available": True,
        "address": f"0x{address:02X}",
        "temperatureF": round(_fahrenheit_from_celsius(temp_c), 2),
        "temperatureC": round(temp_c, 2),
        "humidity": round(humidity, 2),
        "error": "",
    }


def _select_hdc2080_pair_reading(readings: list[dict], errors: list[dict], now: float) -> dict:
    valid = [item for item in readings if item.get("available") and item.get("temperatureF") is not None]
    sensor_last = _LOCAL_TEMP_SENSOR_HEALTH.setdefault("sensorLastF", {})
    previous_accepted = _LOCAL_TEMP_SENSOR_HEALTH.get("lastAcceptedF")
    previous_accepted_at = float(_LOCAL_TEMP_SENSOR_HEALTH.get("lastAcceptedAt") or 0.0)
    previous_selected = str(_LOCAL_TEMP_SENSOR_HEALTH.get("selectedAddress") or "")

    def finalize(selected: list[dict], strategy: str, ignored: list[dict] | None = None, *, degraded: bool = False) -> dict:
        ignored = ignored or []
        temp_f = sum(float(item["temperatureF"]) for item in selected) / len(selected)
        temp_c = sum(float(item["temperatureC"]) for item in selected) / len(selected)
        humidities = [float(item["humidity"]) for item in selected if item.get("humidity") is not None]
        humidity = sum(humidities) / len(humidities) if humidities else None
        used_addresses = [str(item.get("address") or "") for item in selected]
        for item in valid:
            address = str(item.get("address") or "")
            sensor_last[address] = float(item["temperatureF"])
            item["used"] = address in used_addresses
            if not item["used"] and not item.get("ignoredReason"):
                item["ignoredReason"] = "disagreed with trusted temperature"

        accepted_f = round(temp_f, 2)
        _LOCAL_TEMP_SENSOR_HEALTH["lastAcceptedF"] = accepted_f
        _LOCAL_TEMP_SENSOR_HEALTH["lastAcceptedAt"] = now
        _LOCAL_TEMP_SENSOR_HEALTH["selectedAddress"] = used_addresses[0] if len(used_addresses) == 1 else ",".join(used_addresses)
        delta = None
        if len(valid) >= 2:
            delta = round(max(float(item["temperatureF"]) for item in valid) - min(float(item["temperatureF"]) for item in valid), 2)
        label = "HDC2080 pair " + "/".join(used_addresses) if len(used_addresses) > 1 else f"HDC2080 {used_addresses[0]}"
        if ignored:
            label += " (other sensor ignored)"
        return {
            "ok": True,
            "available": True,
            "temperatureF": round(temp_f, 1),
            "temperatureC": round(temp_c, 2),
            "humidity": round(humidity, 1) if humidity is not None else None,
            "source": "hdc2080-pair",
            "label": label,
            "addresses": used_addresses,
            "strategy": strategy,
            "degraded": bool(degraded or ignored or errors),
            "temperatureDeltaF": delta,
            "maxAllowedDeltaF": LOCAL_TEMP_SENSOR_MAX_PAIR_DELTA_F,
            "ignoredAddresses": [str(item.get("address") or "") for item in ignored],
            "sensors": valid + errors,
            "error": "; ".join(str(item.get("error") or "") for item in errors if item.get("error")),
        }

    if not valid:
        if previous_accepted is not None and now - previous_accepted_at <= LOCAL_TEMP_SENSOR_HOLD_LAST_SECONDS:
            return {
                "ok": True,
                "available": True,
                "temperatureF": round(float(previous_accepted), 1),
                "temperatureC": round((float(previous_accepted) - 32.0) * 5.0 / 9.0, 2),
                "humidity": None,
                "source": "hdc2080-pair",
                "label": "HDC2080 pair (holding last trusted reading)",
                "addresses": [],
                "strategy": "hold-last-good",
                "degraded": True,
                "temperatureDeltaF": None,
                "maxAllowedDeltaF": LOCAL_TEMP_SENSOR_MAX_PAIR_DELTA_F,
                "ignoredAddresses": [],
                "sensors": errors,
                "error": "; ".join(str(item.get("error") or "") for item in errors if item.get("error")),
            }
        return {
            "ok": False,
            "available": False,
            "temperatureF": None,
            "temperatureC": None,
            "humidity": None,
            "source": "hdc2080-pair",
            "label": "HDC2080 pair unavailable",
            "addresses": [],
            "strategy": "unavailable",
            "degraded": True,
            "temperatureDeltaF": None,
            "maxAllowedDeltaF": LOCAL_TEMP_SENSOR_MAX_PAIR_DELTA_F,
            "ignoredAddresses": [],
            "sensors": errors,
            "error": "; ".join(str(item.get("error") or "") for item in errors if item.get("error")) or "No HDC2080 readings",
        }

    if len(valid) == 1:
        return finalize(valid, "single-sensor", degraded=True)

    ordered = sorted(valid, key=lambda item: str(item.get("address") or ""))
    pair_delta = abs(float(ordered[0]["temperatureF"]) - float(ordered[1]["temperatureF"]))
    if pair_delta <= LOCAL_TEMP_SENSOR_MAX_PAIR_DELTA_F:
        return finalize(ordered, "average-pair")

    # With only two sensors, disagreement alone cannot prove which unit is bad.
    # Use continuity from the last trusted pair value and the previously selected
    # sensor to reject a sudden outlier. If there is no defensible winner, hold
    # the last trusted temperature briefly instead of averaging a bad reading.
    winner: dict | None = None
    if previous_accepted is not None:
        ranked = sorted(ordered, key=lambda item: abs(float(item["temperatureF"]) - float(previous_accepted)))
        near_distance = abs(float(ranked[0]["temperatureF"]) - float(previous_accepted))
        far_distance = abs(float(ranked[1]["temperatureF"]) - float(previous_accepted))
        if near_distance <= LOCAL_TEMP_SENSOR_MAX_STEP_F and far_distance - near_distance >= 1.0:
            winner = ranked[0]

    if winner is None and previous_selected and "," not in previous_selected:
        previous_addresses = {previous_selected}
        prior = next((item for item in ordered if str(item.get("address") or "") in previous_addresses), None)
        if prior is not None:
            prior_address = str(prior.get("address") or "")
            prior_raw = sensor_last.get(prior_address)
            if prior_raw is None or abs(float(prior["temperatureF"]) - float(prior_raw)) <= LOCAL_TEMP_SENSOR_MAX_STEP_F:
                winner = prior

    if winner is not None:
        ignored = [item for item in ordered if item is not winner]
        for item in ignored:
            item["ignoredReason"] = f"pair disagreement exceeded {LOCAL_TEMP_SENSOR_MAX_PAIR_DELTA_F:.1f}F"
        return finalize([winner], "outlier-rejected", ignored, degraded=True)

    if previous_accepted is not None and now - previous_accepted_at <= LOCAL_TEMP_SENSOR_HOLD_LAST_SECONDS:
        for item in ordered:
            item["used"] = False
            item["ignoredReason"] = "pair disagreement; no trustworthy winner"
        return {
            "ok": True,
            "available": True,
            "temperatureF": round(float(previous_accepted), 1),
            "temperatureC": round((float(previous_accepted) - 32.0) * 5.0 / 9.0, 2),
            "humidity": None,
            "source": "hdc2080-pair",
            "label": "HDC2080 pair (holding last trusted reading)",
            "addresses": [],
            "strategy": "hold-last-good",
            "degraded": True,
            "temperatureDeltaF": round(pair_delta, 2),
            "maxAllowedDeltaF": LOCAL_TEMP_SENSOR_MAX_PAIR_DELTA_F,
            "ignoredAddresses": [str(item.get("address") or "") for item in ordered],
            "sensors": ordered + errors,
            "error": f"HDC2080 readings disagree by {pair_delta:.2f}F",
        }

    for item in ordered:
        item["used"] = False
        item["ignoredReason"] = "startup disagreement; no trusted history"
    return {
        "ok": False,
        "available": False,
        "temperatureF": None,
        "temperatureC": None,
        "humidity": None,
        "source": "hdc2080-pair",
        "label": "HDC2080 pair disagreement",
        "addresses": [],
        "strategy": "unresolved-disagreement",
        "degraded": True,
        "temperatureDeltaF": round(pair_delta, 2),
        "maxAllowedDeltaF": LOCAL_TEMP_SENSOR_MAX_PAIR_DELTA_F,
        "ignoredAddresses": [str(item.get("address") or "") for item in ordered],
        "sensors": ordered + errors,
        "error": f"HDC2080 readings disagree by {pair_delta:.2f}F and no trusted history exists",
    }


def _read_hdc2080_pair_sensor() -> dict | None:
    addresses = _parse_i2c_addresses(LOCAL_TEMP_SENSOR_ADDRESSES, (0x40, 0x41))
    if not addresses:
        return None
    try:
        SMBus = _smbus_class()
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "temperatureF": None,
            "temperatureC": None,
            "humidity": None,
            "source": "hdc2080-pair",
            "label": "HDC2080 pair unavailable",
            "error": str(exc),
            "sensors": [],
        }

    readings: list[dict] = []
    errors: list[dict] = []
    try:
        with SMBus(HARDWARE_I2C_BUS) as bus:
            for address in addresses:
                try:
                    readings.append(_read_hdc2080_measurement(bus, address))
                except Exception as exc:
                    errors.append({
                        "ok": False,
                        "available": False,
                        "address": f"0x{address:02X}",
                        "temperatureF": None,
                        "temperatureC": None,
                        "humidity": None,
                        "used": False,
                        "error": str(exc),
                    })
    except Exception as exc:
        errors.append({
            "ok": False,
            "available": False,
            "address": "bus",
            "temperatureF": None,
            "temperatureC": None,
            "humidity": None,
            "used": False,
            "error": str(exc),
        })
    return _select_hdc2080_pair_reading(readings, errors, time.time())


def _read_direct_i2c_temperature_sensor() -> dict | None:
    if not HARDWARE_I2C_DEVICE.exists():
        return None
    sensor_type = LOCAL_TEMP_SENSOR_TYPE

    if sensor_type in {"auto", "hdc2080", "hdc20x0", "hdc20xx"}:
        hdc_payload = _read_hdc2080_pair_sensor()
        if hdc_payload and (hdc_payload.get("available") or sensor_type != "auto"):
            return hdc_payload

    address_override = _parse_i2c_address(LOCAL_TEMP_SENSOR_ADDRESS)
    probes: list[tuple[str, int]] = []
    if address_override is not None:
        if sensor_type in {"sht30", "sht31", "sht3x"}:
            probes.append(("sht3x", address_override))
        elif sensor_type in {"tmp102", "tmp112", "tmp10x"}:
            probes.append(("tmp102", address_override))
        elif sensor_type == "auto":
            probes.extend((kind, address_override) for kind in ("sht3x", "tmp102"))
    elif sensor_type in {"auto", "sht30", "sht31", "sht3x"}:
        probes.extend(("sht3x", address) for address in (0x44, 0x45))
    if address_override is None and sensor_type in {"auto", "tmp102", "tmp112", "tmp10x"}:
        probes.extend(("tmp102", address) for address in (0x48, 0x49, 0x4A, 0x4B))

    if not probes:
        return None

    try:
        SMBus = _smbus_class()
    except Exception:
        return None

    errors: list[str] = []
    try:
        with SMBus(HARDWARE_I2C_BUS) as bus:
            for kind, address in probes:
                try:
                    if kind == "sht3x":
                        temp_c = _read_sht3x_temperature(bus, address)
                        label = f"SHT3x 0x{address:02X}"
                    else:
                        temp_c = _read_tmp102_temperature(bus, address)
                        label = f"TMP102 0x{address:02X}"
                    if not (-40.0 <= temp_c <= 60.0):
                        raise ValueError(f"unreasonable reading {temp_c:.2f}C")
                    return {
                        "ok": True,
                        "available": True,
                        "temperatureF": round(_fahrenheit_from_celsius(temp_c), 1),
                        "temperatureC": round(temp_c, 2),
                        "source": "i2c",
                        "label": label,
                        "address": f"0x{address:02X}",
                        "error": "",
                    }
                except Exception as exc:
                    errors.append(f"{kind} 0x{address:02X}: {exc}")
                    continue
    except Exception as exc:
        errors.append(str(exc))
    if address_override is not None and errors:
        return {"ok": False, "available": False, "temperatureF": None, "temperatureC": None, "source": "i2c", "label": "", "error": "; ".join(errors[-3:])}
    return None

def _read_local_temperature_sensor(force: bool = False) -> dict:
    now = time.time()
    with _LOCAL_TEMP_SENSOR_LOCK:
        cached = _LOCAL_TEMP_SENSOR_CACHE.get("payload")
        if not force and cached and now - float(_LOCAL_TEMP_SENSOR_CACHE.get("at") or 0) < LOCAL_TEMP_SENSOR_POLL_SECONDS:
            return _deepcopy_json(cached)

        if not LOCAL_TEMP_SENSOR_ENABLED:
            payload = {"ok": True, "available": False, "temperatureF": None, "temperatureC": None, "source": "disabled", "label": "", "error": "Local temperature sensor disabled"}
        else:
            # Prefer the dedicated room sensors over any unrelated kernel hwmon
            # device that may be present on the Raspberry Pi.
            payload = _read_direct_i2c_temperature_sensor() or _read_hwmon_temperature_sensor()
            if not payload:
                payload = {
                    "ok": True,
                    "available": False,
                    "temperatureF": None,
                    "temperatureC": None,
                    "source": "none",
                    "label": "No onboard room temperature sensor found",
                    "error": "",
                }
        payload["readAt"] = int(now)
        _LOCAL_TEMP_SENSOR_CACHE["at"] = now
        _LOCAL_TEMP_SENSOR_CACHE["payload"] = _deepcopy_json(payload)
        return _deepcopy_json(payload)


def _thermostat_should_use_local_temp_sensor(thermostat: dict, now: float) -> bool:
    if _room_temp_control_mode(thermostat) == "external":
        return False
    mode = LOCAL_TEMP_SENSOR_MODE
    if mode in {"primary", "onboard", "local", "always", "on", "force"}:
        return True
    source = str(thermostat.get("currentTempSource") or "").strip().lower()
    if source in LOCAL_TEMP_SOURCE_NAMES:
        return True
    if mode in {"fallback", "auto", "ha-fallback", "home-assistant-fallback"} and source == "home-assistant":
        last_update = _number(thermostat.get("currentTempUpdatedAt"), 0, 0)
        return not last_update or now - last_update >= LOCAL_TEMP_SENSOR_STALE_SECONDS
    return False



def _selected_ha_temperature_entity() -> dict | None:
    try:
        record = _read_panel_config_record()
        config = record.get("config") if isinstance(record, dict) else {}
        ha = (((config or {}).get("integrations") or {}).get("homeAssistant") or {})
        selected = ha.get("currentTempEntity")
        if isinstance(selected, dict):
            eid = str(selected.get("entityId") or selected.get("entity_id") or "").strip()
            if eid:
                return {"entityId": eid, "name": str(selected.get("name") or selected.get("friendly_name") or eid)}
        if isinstance(selected, str) and selected.strip():
            eid = selected.strip()
            return {"entityId": eid, "name": eid}
    except Exception:
        return None
    return None


def _temperature_from_ha_state_item(item: dict) -> tuple[float | None, str]:
    attrs = item.get("attributes") or {}
    # A sensor stores the temperature in state; a climate entity stores the
    # measured room temperature in current_temperature.
    raw = attrs.get("current_temperature")
    if raw is None:
        raw = item.get("state")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, ""
    unit = str(attrs.get("temperature_unit") or attrs.get("unit_of_measurement") or "").strip()
    if unit.lower() in {"°c", "c", "celsius"}:
        value = value * 9.0 / 5.0 + 32.0
    if not (-40.0 <= value <= 130.0):
        return None, unit
    return round(value, 1), unit



def _virtual_temp_override_active(thermostat: dict) -> bool:
    source = str(thermostat.get("currentTempSource") or "").strip().lower()
    until = _number(thermostat.get("virtualTempOverrideUntil"), 0, 0, None)
    return source == "virtual" and until > int(time.time() * 1000)


def _clear_expired_virtual_temp_override(thermostat: dict) -> dict:
    source = str(thermostat.get("currentTempSource") or "").strip().lower()
    until = _number(thermostat.get("virtualTempOverrideUntil"), 0, 0, None)
    if source == "virtual" and until and until <= int(time.time() * 1000):
        updated = dict(thermostat)
        updated["virtualTempOverrideUntil"] = 0
        # Return to the normal runtime source. The onboard HDC2080 pair is
        # primary; Home Assistant is used only if the pair is unavailable.
        if LOCAL_TEMP_SENSOR_ENABLED and LOCAL_TEMP_SENSOR_MODE in {"primary", "onboard", "local", "always", "on", "force"}:
            updated["currentTempSource"] = "onboard"
            updated["currentTempSourceName"] = "Onboard HDC2080 Sensors"
        elif _selected_ha_temperature_entity():
            updated["currentTempSource"] = "home-assistant"
            updated["currentTempSourceName"] = "Home Assistant Sensor"
        return _merge_thermostat_state(updated)
    return thermostat


def _apply_selected_ha_temperature_sensor_if_needed(thermostat: dict) -> dict:
    if _virtual_temp_override_active(thermostat):
        return thermostat
    source = _selected_ha_temperature_entity()
    if not source:
        return thermostat
    ha_url, token = _ha_credentials_from_panel_config()
    if not ha_url or not token:
        return thermostat
    entity_id = str(source.get("entityId") or "").strip()
    if not entity_id:
        return thermostat
    try:
        item = _ha_state_cached(ha_url, token, entity_id)
        if not isinstance(item, dict):
            return thermostat
        temp_f, _unit = _temperature_from_ha_state_item(item)
        if temp_f is None:
            return thermostat
        attrs = item.get("attributes") or {}
        label = str(source.get("name") or attrs.get("friendly_name") or entity_id).strip() or entity_id
        updated = dict(thermostat)
        updated["currentTemp"] = temp_f
        updated["currentTempUpdatedAt"] = int(time.time())
        updated["currentTempSource"] = "home-assistant"
        updated["currentTempSourceName"] = label
        updated["runtimeTempSource"] = "home-assistant"
        updated["runtimeTempSourceName"] = label
        if updated != thermostat:
            _write_thermostat_record(updated, persist=False)
        return updated
    except Exception as exc:
        print(f"Home Assistant temperature sensor update failed for {entity_id}: {exc}", flush=True)
        return thermostat


def _selected_ha_outdoor_temperature_entity() -> dict | None:
    try:
        record = _read_panel_config_record()
        config = record.get("config") if isinstance(record, dict) else {}
        ha = (((config or {}).get("integrations") or {}).get("homeAssistant") or {})
        selected = ha.get("outdoorTempEntity") or ha.get("outsideTempEntity") or ha.get("weatherEntity")
        if isinstance(selected, dict):
            eid = str(selected.get("entityId") or selected.get("entity_id") or "").strip()
            if eid:
                return {"entityId": eid, "name": str(selected.get("name") or selected.get("friendly_name") or eid)}
        if isinstance(selected, str) and selected.strip():
            eid = selected.strip()
            return {"entityId": eid, "name": eid}
    except Exception:
        return None
    return None


def _apply_selected_ha_outdoor_temperature_sensor_if_needed(thermostat: dict) -> dict:
    source = _selected_ha_outdoor_temperature_entity()
    if not source:
        return thermostat
    ha_url, token = _ha_credentials_from_panel_config()
    if not ha_url or not token:
        return thermostat
    entity_id = str(source.get("entityId") or "").strip()
    if not entity_id:
        return thermostat
    try:
        item = _ha_state_cached(ha_url, token, entity_id)
        if not isinstance(item, dict):
            return thermostat
        attrs = item.get("attributes") or {}
        label = str(source.get("name") or attrs.get("friendly_name") or entity_id).strip() or entity_id
        updated = dict(thermostat)

        if entity_id.startswith("weather."):
            weather = _normalize_weather_item(item)
            if weather.get("temperature") is not None:
                updated["outdoorTemp"] = round(float(weather.get("temperature")), 1)
            if weather.get("windSpeed") is not None:
                updated["outdoorWindSpeed"] = round(float(weather.get("windSpeed")), 1)
            if weather.get("windSpeedUnit"):
                updated["outdoorWindUnit"] = str(weather.get("windSpeedUnit") or "mph")
        else:
            temp_f, _unit = _temperature_from_ha_state_item(item)
            if temp_f is None:
                return thermostat
            updated["outdoorTemp"] = temp_f

        updated["outdoorTempSource"] = "home-assistant"
        updated["outdoorTempSourceName"] = label
        if updated != thermostat:
            _write_thermostat_record(updated, persist=False)
        return updated
    except Exception as exc:
        print(f"Home Assistant outdoor temperature update failed for {entity_id}: {exc}", flush=True)
        return thermostat


def _apply_local_temperature_sensor_if_needed(record: dict, *, sensor: dict | None = None, force_use: bool = False) -> dict:
    thermostat = record.get("thermostat") or {}
    if _virtual_temp_override_active(thermostat):
        return thermostat
    now = time.time()
    if not force_use and not _thermostat_should_use_local_temp_sensor(thermostat, now):
        return thermostat
    sensor = sensor if isinstance(sensor, dict) else _read_local_temperature_sensor()
    temp_f = sensor.get("temperatureF")
    if not sensor.get("available") or temp_f is None:
        return thermostat
    try:
        next_temp = round(float(temp_f), 1)
    except (TypeError, ValueError):
        return thermostat
    if not (-40.0 <= next_temp <= 130.0):
        return thermostat
    label = str(sensor.get("label") or "Onboard HDC2080 Sensors").strip() or "Onboard HDC2080 Sensors"
    updated = dict(thermostat)
    updated["currentTemp"] = next_temp
    updated["currentTempUpdatedAt"] = int(now)
    updated["currentTempSource"] = "onboard"
    updated["currentTempSourceName"] = label
    updated["runtimeTempSource"] = "onboard"
    updated["runtimeTempSourceName"] = label
    humidity = sensor.get("humidity")
    if humidity is not None:
        try:
            next_humidity = round(float(humidity), 1)
            if 0.0 <= next_humidity <= 100.0:
                updated["humidity"] = next_humidity
        except (TypeError, ValueError):
            pass
    updated["onboardTempSensorStatus"] = {
        "strategy": str(sensor.get("strategy") or "single"),
        "degraded": bool(sensor.get("degraded")),
        "temperatureDeltaF": sensor.get("temperatureDeltaF"),
        "ignoredAddresses": list(sensor.get("ignoredAddresses") or []),
    }
    if updated != thermostat:
        _write_thermostat_record(updated, persist=False)
    return updated


def _thermostat_control_loop() -> None:
    print(
        f"Smart Thermostat autonomous control loop started; interval={CONTROL_LOOP_INTERVAL_SECONDS}s "
        f"local_temp_mode={LOCAL_TEMP_SENSOR_MODE}.",
        flush=True,
    )
    while not _CONTROL_LOOP_STOP.wait(CONTROL_LOOP_INTERVAL_SECONDS):
        try:
            record = _read_thermostat_record()
            thermostat = _apply_runtime_thermostat_logic(record)
            outputs = _thermostat_outputs(thermostat)
            _apply_thermostat_outputs_to_hardware(outputs, thermostat)
        except Exception as exc:  # noqa: BLE001 - keep local HVAC control alive
            print(f"Thermostat autonomous control loop error: {exc}", flush=True)


def _start_thermostat_control_loop() -> None:
    global _CONTROL_LOOP_THREAD_STARTED
    if not CONTROL_LOOP_ENABLED or _CONTROL_LOOP_THREAD_STARTED:
        return
    _CONTROL_LOOP_THREAD_STARTED = True
    thread = threading.Thread(target=_thermostat_control_loop, name="thermostat-control-loop", daemon=True)
    thread.start()

def _hardware_telemetry_payload() -> dict:
    """Return read-only onboard sensor telemetry for Home Assistant.

    This endpoint intentionally avoids the full thermostat status path and any
    Home Assistant proxy lookups. It reads the motion input without reapplying
    configuration and uses the existing HDC2080 cache, so frequent motion polls
    cannot create climate-command echoes or hammer the I2C bus.
    """
    temperature = _read_local_temperature_sensor(force=False)
    motion = _motion_status_payload(apply_config=False)
    return {
        "ok": True,
        "readAt": int(time.time()),
        "motion": motion,
        "temperature": temperature,
        # Keep the established hardware-status key as an alias for clients that
        # already understand the local sensor payload.
        "localTempSensor": temperature,
    }


def _hardware_status_payload(force_i2c: bool = False) -> dict:
    if _expire_manual_hardware_if_needed():
        try:
            thermostat = _read_thermostat_record()["thermostat"]
            _apply_thermostat_outputs_to_hardware(_thermostat_outputs(thermostat), thermostat)
        except Exception as exc:
            print(f"Manual hardware expiry reapply failed: {exc}", flush=True)
    with _HARDWARE_LOCK:
        relay_backend = _relay_backend()
        rgb_backend = _rgb_backend()
        relays = {
            name: {
                **meta,
                "on": bool(_HARDWARE_LAST_RELAYS.get(name)),
                "activeLow": HARDWARE_RELAY_ACTIVE_LOW,
            }
            for name, meta in HARDWARE_RELAY_PINS.items()
        }
        rgb = {
            **HARDWARE_RGB_PIN,
            "on": bool(_HARDWARE_RGB.get("on")),
            "color": _normalize_hex_color(_HARDWARE_RGB.get("color")),
            "backend": getattr(rgb_backend, "name", "simulated"),
            "available": bool(getattr(rgb_backend, "available", False)),
            "error": str(getattr(rgb_backend, "error", "") or ""),
        }
        gpio = {
            "backend": getattr(relay_backend, "name", "simulated"),
            "available": bool(getattr(relay_backend, "available", False)),
            "error": str(getattr(relay_backend, "error", "") or ""),
            "source": _HARDWARE_LAST_RELAY_SOURCE,
            "activeLow": HARDWARE_RELAY_ACTIVE_LOW,
        }
        expires_at = float(_HARDWARE_MANUAL.get("expiresAt") or 0.0)
        manual = {
            "active": bool(_HARDWARE_MANUAL.get("active")),
            "relays": dict(_HARDWARE_MANUAL.get("relays") or {}),
            "activatedAt": float(_HARDWARE_MANUAL.get("activatedAt") or 0.0),
            "expiresAt": expires_at,
            "remainingSeconds": max(0, int(round(expires_at - time.time()))) if expires_at else 0,
        }

    return {
        "ok": True,
        "gpio": gpio,
        "manual": manual,
        "relays": relays,
        "rgb": rgb,
        "i2c": _scan_i2c_devices(force=force_i2c),
        "localTempSensor": _read_local_temperature_sensor(force=force_i2c),
        "pinout": {
            "relays": HARDWARE_RELAY_PINS,
            "rgb": HARDWARE_RGB_PIN,
            "i2c": HARDWARE_I2C_PINS,
            "power": HARDWARE_POWER_PINS,
        },
    }


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _git_ssh_command() -> str:
    env_file = Path(os.environ.get("SMART_THERMOSTAT_UPDATE_ENV", "/etc/smart-thermostat/update-agent.env"))
    values = _read_env_file(env_file)
    key_path = (
        os.environ.get("CLIMATE_CONTROLLER_DEPLOY_KEY_PATH")
        or values.get("CLIMATE_CONTROLLER_DEPLOY_KEY_PATH")
        or str(Path.home() / ".ssh" / "climate_controller_deploy_key")
    )
    return (
        f"ssh -i {key_path} -o IdentitiesOnly=yes "
        "-o StrictHostKeyChecking=accept-new -o HostName=ssh.github.com -p 443"
    )


def _update_branch_name() -> str:
    env_file = Path(os.environ.get("SMART_THERMOSTAT_UPDATE_ENV", "/etc/smart-thermostat/update-agent.env"))
    values = _read_env_file(env_file)
    branch = os.environ.get("CLIMATE_CONTROLLER_BRANCH") or values.get("CLIMATE_CONTROLLER_BRANCH") or "Development"
    branch = branch.strip() or "Development"
    # This panel intentionally does not expose branch selection. Keep the endpoint
    # locked to the configured/default Development branch.
    return "Development" if branch.lower() != "development" else branch


def _run_git_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = _git_ssh_command()
    return subprocess.run(
        args,
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )


def _schedule_service_restart() -> None:
    service_name = os.environ.get("SMART_THERMOSTAT_SERVICE", "smart-thermostat-web.service")

    def _restart() -> None:
        time.sleep(1.5)
        subprocess.run(["sudo", "systemctl", "restart", service_name], check=False)

    threading.Thread(target=_restart, daemon=True).start()


def _schedule_server_reboot() -> None:
    def _run_reboot_command(command: list[str]) -> bool:
        try:
            result = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=8,
                check=False,
            )
        except Exception as exc:
            print(f"Reboot command failed to start: {' '.join(command)}: {exc}", flush=True)
            return False
        if result.returncode == 0:
            return True
        message = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        print(f"Reboot command failed: {' '.join(command)}: {message}", flush=True)
        return False

    def _reboot() -> None:
        time.sleep(1.5)
        systemctl_candidates = []
        for candidate in (shutil.which("systemctl"), "/usr/bin/systemctl", "/bin/systemctl"):
            if candidate and candidate not in systemctl_candidates:
                systemctl_candidates.append(candidate)
        commands = [["sudo", "-n", "/usr/local/sbin/smart-thermostat-reboot"]]
        commands.extend(["sudo", "-n", candidate, "reboot"] for candidate in systemctl_candidates)
        commands.extend([
            ["sudo", "-n", "/usr/sbin/reboot"],
            ["sudo", "-n", "/sbin/reboot"],
            ["sudo", "-n", "reboot"],
        ])
        seen: set[tuple[str, ...]] = set()
        for command in commands:
            key = tuple(command)
            if key in seen:
                continue
            seen.add(key)
            if _run_reboot_command(command):
                return
        print(
            "Unable to reboot without a sudo password. Run scripts/install-pi.sh once to install the smart-thermostat sudoers rule.",
            flush=True,
        )

    threading.Thread(target=_reboot, daemon=True).start()


def _reboot_payload() -> dict:
    _schedule_server_reboot()
    return {"ok": True, "message": "Restart command sent. The server will reboot now."}



def _read_json_record(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def _version_key(value: object) -> tuple[int, ...]:
    numbers = [int(item) for item in re.findall(r"\d+", str(value or ""))]
    return tuple(numbers) if numbers else (0,)


def _update_available(installed: object, latest: object) -> bool:
    installed_text = str(installed or "").strip()
    latest_text = str(latest or "").strip()
    return bool(latest_text and latest_text != installed_text and _version_key(latest_text) > _version_key(installed_text))


def _normalized_update_branch() -> str:
    branch = UPDATE_BRANCH
    if re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        return branch
    return "Development"


def _read_update_check_record() -> dict:
    return _read_json_record(UPDATE_CHECK_FILE)


def _refresh_remote_update_info(force: bool = False) -> dict:
    """Refresh the remote VERSION without changing the checked-out files."""
    installed = _read_version_value()
    cached = _read_update_check_record()
    now = time.time()
    checked_at = float(cached.get("checkedAtEpoch") or 0)
    if not force and checked_at and (now - checked_at) < UPDATE_CHECK_TTL_SECONDS:
        return cached

    with _UPDATE_CHECK_LOCK:
        cached = _read_update_check_record()
        checked_at = float(cached.get("checkedAtEpoch") or 0)
        now = time.time()
        if not force and checked_at and (now - checked_at) < UPDATE_CHECK_TTL_SECONDS:
            return cached

        branch = _normalized_update_branch()
        latest = str(cached.get("latestVersion") or installed).strip() or installed
        error_message = ""
        if not (ROOT / ".git").exists():
            error_message = "This thermostat folder is not connected to Git."
        else:
            fetch_cmd = [
                "git",
                "fetch",
                "--quiet",
                "origin",
                f"+{branch}:refs/remotes/origin/{branch}",
            ]
            try:
                fetched = subprocess.run(
                    fetch_cmd,
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                if fetched.returncode != 0:
                    error_message = (fetched.stderr or fetched.stdout or "Git fetch failed").strip()
            except Exception as exc:
                error_message = f"Git fetch failed: {exc}"

            try:
                shown = subprocess.run(
                    ["git", "show", f"refs/remotes/origin/{branch}:VERSION"],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if shown.returncode == 0 and shown.stdout.strip():
                    latest = shown.stdout.strip().splitlines()[0].strip()
                elif not error_message:
                    error_message = (shown.stderr or shown.stdout or "Could not read the remote VERSION file").strip()
            except Exception as exc:
                if not error_message:
                    error_message = f"Could not read the remote VERSION file: {exc}"

        record = {
            "ok": True,
            "branch": branch,
            "installedVersion": installed,
            "latestVersion": latest,
            "updateAvailable": _update_available(installed, latest),
            "checkedAt": int(now * 1000),
            "checkedAtEpoch": now,
            "checkError": error_message or None,
        }
        try:
            _write_json_record(UPDATE_CHECK_FILE, record)
        except OSError:
            pass
        return record


def _update_unit_state(unit_name: str) -> dict:
    unit = str(unit_name or "").strip()
    if not unit:
        return {"activeState": "unknown", "subState": "unknown", "result": "unknown"}
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    try:
        result = subprocess.run(
            [
                systemctl,
                "show",
                unit,
                "--property=ActiveState",
                "--property=SubState",
                "--property=Result",
                "--value",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return {"activeState": "unknown", "subState": "unknown", "result": "unknown"}
    values = [line.strip() for line in (result.stdout or "").splitlines()]
    while len(values) < 3:
        values.append("")
    return {
        "activeState": values[0] or "unknown",
        "subState": values[1] or "unknown",
        "result": values[2] or "unknown",
    }


def _read_update_log(log_path: object) -> str:
    path = Path(str(log_path or UPDATE_RUNTIME_DIR / "fetch-update.log"))
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-262144:].decode("utf-8", errors="replace")


def _current_update_job_status() -> dict:
    installed = _read_version_value()
    record = _read_json_record(UPDATE_STATUS_FILE)
    if not record:
        return {
            "inProgress": False,
            "phase": "idle",
            "message": "No update is currently running.",
            "installedVersion": installed,
            "unit": None,
            "log": str(UPDATE_RUNTIME_DIR / "fetch-update.log"),
        }

    record["installedVersion"] = installed
    if not bool(record.get("inProgress")):
        return record

    unit_state = _update_unit_state(str(record.get("unit") or ""))
    record["unitState"] = unit_state
    active_state = unit_state.get("activeState")
    started_at = float(record.get("startedAtEpoch") or 0)
    age = max(0.0, time.time() - started_at) if started_at else 0.0
    log_text = _read_update_log(record.get("log"))

    if "===== Smart Thermostat self-update finished:" in log_text:
        record.update(
            {
                "inProgress": False,
                "phase": "complete",
                "message": "The thermostat update completed successfully.",
                "error": None,
                "finishedAt": int(time.time() * 1000),
                "installedVersion": installed,
            }
        )
    elif active_state in {"active", "activating", "reloading"} or age < 15:
        record["phase"] = "installing"
        record["message"] = "The thermostat is fetching and installing the update."
    else:
        record.update(
            {
                "inProgress": False,
                "phase": "failed",
                "message": "The thermostat update did not finish successfully.",
                "error": f"Update unit state: {active_state or 'unknown'}; result: {unit_state.get('result') or 'unknown'}",
                "finishedAt": int(time.time() * 1000),
                "installedVersion": installed,
            }
        )

    try:
        _write_json_record(UPDATE_STATUS_FILE, record)
    except OSError:
        pass
    return record


def _cached_update_info(installed: str | None = None) -> dict:
    """Return update metadata without performing network or Git operations."""
    installed = str(installed or _read_version_value()).strip()
    cached = _read_update_check_record()
    if cached:
        result = dict(cached)
        result.setdefault("ok", True)
        result.setdefault("branch", _normalized_update_branch())
        result.setdefault("installedVersion", installed)
        result.setdefault("latestVersion", installed)
        result.setdefault("updateAvailable", _update_available(installed, result.get("latestVersion")))
        return result
    return {
        "ok": True,
        "branch": _normalized_update_branch(),
        "installedVersion": installed,
        "latestVersion": installed,
        "updateAvailable": False,
        "checkedAt": None,
        "checkedAtEpoch": 0,
        "checkError": None,
    }


def _update_status_payload(refresh_remote: bool = False) -> dict:
    job = _current_update_job_status()
    installed = _read_version_value()
    if bool(job.get("inProgress")):
        # Never run a second Git operation alongside the transient installer.
        # During installation, report the last completed version check instead.
        check = _cached_update_info(installed)
    elif refresh_remote:
        check = _refresh_remote_update_info(force=True)
    else:
        # Home Assistant first loads this local snapshot so the Software entity
        # is immediately available. Its next update-coordinator pass performs
        # the slower GitHub refresh without holding initial entity setup open.
        check = _cached_update_info(installed)

    latest = str(check.get("latestVersion") or job.get("targetVersion") or installed).strip() or installed
    return {
        "ok": True,
        "title": "Smart Thermostat Software",
        "installedVersion": installed,
        "latestVersion": latest,
        "updateAvailable": _update_available(installed, latest),
        "inProgress": bool(job.get("inProgress")),
        "phase": job.get("phase") or "idle",
        "message": job.get("message") or "",
        "error": job.get("error"),
        "unit": job.get("unit"),
        "unitState": job.get("unitState"),
        "log": job.get("log") or str(UPDATE_RUNTIME_DIR / "fetch-update.log"),
        "startedAt": job.get("startedAt"),
        "finishedAt": job.get("finishedAt"),
        "branch": check.get("branch") or _normalized_update_branch(),
        "checkedAt": check.get("checkedAt"),
        "checkError": check.get("checkError"),
    }


def _fetch_update_payload() -> dict:
    """Start the existing safe updater once and expose its state to Home Assistant."""
    with _UPDATE_LAUNCH_LOCK:
        current = _current_update_job_status()
        if bool(current.get("inProgress")):
            return {
                "ok": True,
                "started": False,
                "alreadyRunning": True,
                "inProgress": True,
                "unit": current.get("unit"),
                "log": current.get("log"),
                "message": "A thermostat update is already in progress.",
            }

        result = _start_update_payload_unlocked()
        now = time.time()
        if result.get("ok"):
            check = _read_update_check_record()
            record = {
                "ok": True,
                "inProgress": True,
                "phase": "queued",
                "message": result.get("message") or "Thermostat update started.",
                "error": None,
                "unit": result.get("unit"),
                "log": result.get("log"),
                "targetVersion": check.get("latestVersion"),
                "startedAt": int(now * 1000),
                "startedAtEpoch": now,
                "finishedAt": None,
            }
        else:
            record = {
                "ok": False,
                "inProgress": False,
                "phase": "failed",
                "message": "The thermostat update could not be started.",
                "error": result.get("error") or "Unknown update startup error",
                "unit": result.get("unit"),
                "log": result.get("log") or str(UPDATE_RUNTIME_DIR / "fetch-update.log"),
                "startedAt": int(now * 1000),
                "startedAtEpoch": now,
                "finishedAt": int(now * 1000),
            }
        try:
            _write_json_record(UPDATE_STATUS_FILE, record)
        except OSError:
            pass
        return {**result, "inProgress": bool(record.get("inProgress")), "phase": record.get("phase")}


def _start_update_payload_unlocked() -> dict:
    """Start the full self-update/deploy command from the panel Info dialog.

    Important: do NOT run the update as a child of the backend service. When the
    command restarts smart-thermostat-backend.service, systemd kills every child
    in that service cgroup. That is what can leave the panel at a black console
    with a blinking cursor. We launch a separate transient systemd unit instead.
    """
    if not (ROOT / ".git").exists():
        return {"ok": False, "error": "This thermostat folder is not connected to Git."}

    # Keep transient self-update scripts/logs out of the Git checkout and out
    # of the backend service RuntimeDirectory. The update restarts this service,
    # so the script must live somewhere that survives that restart.
    runtime = UPDATE_RUNTIME_DIR
    runtime.mkdir(parents=True, exist_ok=True)
    log_path = runtime / "fetch-update.log"
    script_path = runtime / "self-update.sh"
    try:
        log_path.write_text("", encoding="utf-8")
    except OSError:
        pass

    script = f"""#!/usr/bin/env bash
set -Eeuo pipefail

exec >>{shlex.quote(str(log_path))} 2>&1

echo "===== Smart Thermostat self-update started: $(date) ====="
cd {shlex.quote(str(ROOT))}

APP_USER="$(stat -c '%U' . 2>/dev/null || true)"
if [[ -z "$APP_USER" || "$APP_USER" == "UNKNOWN" || "$APP_USER" == "root" ]]; then
  APP_USER="$(logname 2>/dev/null || true)"
fi
if [[ -z "$APP_USER" || "$APP_USER" == "root" ]]; then
  if id david >/dev/null 2>&1; then
    APP_USER="david"
  elif id pi >/dev/null 2>&1; then
    APP_USER="pi"
  else
    APP_USER="root"
  fi
fi
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
if [[ -z "$APP_HOME" || ! -d "$APP_HOME" ]]; then
  APP_HOME="$HOME"
fi
BACKUP_ROOT="$APP_HOME/thermostat-pi-data-backups"

run_as_app_user() {{
  if [[ "$(id -u)" -eq 0 && "$APP_USER" != "root" ]]; then
    if command -v runuser >/dev/null 2>&1; then
      runuser -u "$APP_USER" -- "$@"
    else
      su -s /bin/bash "$APP_USER" -c "$(printf '%q ' "$@")"
    fi
  else
    "$@"
  fi
}}

cleanup_legacy_usb_mounts() {{
  local root="$PWD/data/usb-mounts"
  [[ -d "$root" ]] || return 0
  echo "Checking for old repo-local USB mounts under $root"
  python3 - <<'PY_CLEANUP'
from pathlib import Path
import shutil, subprocess
root = Path.cwd() / 'data' / 'usb-mounts'
mounts = []
try:
    for line in Path('/proc/mounts').read_text(errors='ignore').splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        mount = Path(parts[1].replace('\\040', ' '))
        try:
            mount.resolve().relative_to(root.resolve())
            mounts.append(mount)
        except Exception:
            continue
except Exception as exc:
    print(f'Could not inspect mounts: {{exc}}')
for mount in sorted(mounts, key=lambda p: len(str(p)), reverse=True):
    print(f'Unmounting legacy USB mount: {{mount}}')
    subprocess.run(['umount', str(mount)], check=False)
shutil.rmtree(root, ignore_errors=True)
PY_CLEANUP
}}

configure_git_deploy_exclusions() {{
  # Keep repo-only supporting documents out of the wall-panel checkout. The
  # project can track a top-level Supporting/ folder in GitHub, but the tablet
  # update path should never deploy it to the unit.
  [[ -d ".git" ]] || return 0
  mkdir -p .git/info
  cat >.git/info/sparse-checkout <<'SPARSE_CHECKOUT'
/*
!/Supporting/
!/Supporting/**
SPARSE_CHECKOUT
  chown "$APP_USER:$APP_USER" .git/info/sparse-checkout 2>/dev/null || true
  run_as_app_user git config core.sparseCheckout true || true
  run_as_app_user git config core.sparseCheckoutCone false || true
  rm -rf Supporting
}}

backup_data_files() {{
  mkdir -p "$BACKUP_ROOT/$ts"
  for file in panel-config.json thermostat-state.json thermostat-schedules.json thermostat-schedules.backup.json hvac-history.json; do
    if [[ -f "data/$file" ]]; then
      cp -av "data/$file" "$BACKUP_ROOT/$ts/$file"
    fi
  done
}}

restore_data_files() {{
  mkdir -p data
  if compgen -G "$BACKUP_ROOT/$ts/*" >/dev/null; then
    cp -av "$BACKUP_ROOT/$ts/." data/.
  fi
}}

ts=$(date +%F-%H%M%S)
cleanup_legacy_usb_mounts
backup_data_files

# Match the terminal update path: git operations run as the project owner, not
# root. This avoids Git safe-directory failures and root-owned checkout files.
configure_git_deploy_exclusions
run_as_app_user git fetch origin Development
run_as_app_user git reset --hard origin/Development
cleanup_legacy_usb_mounts
run_as_app_user git clean -fd
rm -rf Supporting

restore_data_files
chmod +x scripts/*.sh

# This transient unit already runs as root. install-native.sh now resolves the
# correct app user from the project folder owner before writing the services.
./scripts/install-native.sh

systemctl daemon-reload
systemctl restart smart-thermostat-backend.service smart-thermostat-native.service

sleep 8
systemctl status smart-thermostat-backend.service smart-thermostat-native.service --no-pager -l || true
echo "===== Smart Thermostat self-update finished: $(date) ====="
"""
    try:
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o755)
    except Exception as exc:
        return {"ok": False, "error": f"Could not write update script: {exc}"}

    unit_name = f"smart-thermostat-self-update-{int(time.time())}"
    cmd = [
        "sudo",
        "systemd-run",
        "--unit", unit_name,
        "--collect",
        "--no-block",
        "--property", "Type=oneshot",
        "--property", f"WorkingDirectory={str(ROOT)}",
        "/bin/bash",
        str(script_path),
    ]

    try:
        started = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=3)
    except Exception as exc:
        return {"ok": False, "error": f"Could not start update unit: {exc}"}

    if started.returncode != 0:
        detail = (started.stderr or started.stdout or "").strip()
        return {
            "ok": False,
            "error": detail or "systemd-run could not start the update unit. Run sudo ./scripts/install-native.sh once to update sudoers.",
        }

    return {
        "ok": True,
        "started": True,
        "unit": unit_name,
        "log": str(log_path),
        "message": "Fetch update started safely. The panel will restart after the transient update service finishes.",
    }


def _safe_identifier_slug(value: object) -> str:
    """Return a lowercase id-safe slug that is stable for HA unique ids."""
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "")).strip("-")


def _raspberry_pi_serial() -> str:
    """Read the physical Raspberry Pi board serial when the OS exposes it."""
    candidates = (
        Path("/sys/firmware/devicetree/base/serial-number"),
        Path("/proc/device-tree/serial-number"),
    )
    for candidate in candidates:
        try:
            value = candidate.read_bytes().decode("utf-8", errors="ignore").replace("\x00", "").strip()
        except OSError:
            value = ""
        if value:
            slug = _safe_identifier_slug(value)
            if slug:
                return slug

    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.lower().startswith("serial") and ":" in line:
                slug = _safe_identifier_slug(line.split(":", 1)[1].strip())
                if slug:
                    return slug
    except OSError:
        pass
    return ""


def _primary_mac_address() -> str:
    """Return a stable non-loopback MAC address, preferring onboard adapters.

    SD-card clones can share /etc/machine-id. The NIC MAC and Pi board serial
    are hardware-specific, so they keep Home Assistant from merging multiple
    wall panels into one discovered device.
    """
    sysfs = Path("/sys/class/net")
    candidates: list[tuple[int, str, str]] = []
    try:
        interfaces = list(sysfs.iterdir())
    except OSError:
        interfaces = []

    for interface in interfaces:
        name = interface.name
        if name == "lo":
            continue
        try:
            raw_mac = (interface / "address").read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        compact = "".join(ch for ch in raw_mac if ch in "0123456789abcdef")
        if len(compact) != 12 or compact in {"000000000000", "ffffffffffff"}:
            continue
        # Prefer predictable physical adapters, but keep all usable adapters as
        # fallbacks so Wi-Fi-only panels still get unique discovery ids.
        priority = 50
        if name.startswith("eth"):
            priority = 0
        elif name.startswith("en"):
            priority = 5
        elif name.startswith("wlan"):
            priority = 10
        elif name.startswith("wl"):
            priority = 15
        candidates.append((priority, name, compact))

    if candidates:
        candidates.sort()
        return candidates[0][2]
    return ""


def _stable_panel_serial() -> str:
    """Return a stable Home Assistant unique id for this wall panel."""
    configured = os.environ.get("SMART_THERMOSTAT_SERIAL", "").strip()
    if configured:
        return configured

    board_serial = _raspberry_pi_serial()
    if board_serial:
        return f"iha-smart-thermostat-pi-{board_serial}"

    mac_address = _primary_mac_address()
    if mac_address:
        return f"iha-smart-thermostat-mac-{mac_address}"

    for machine_id_path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            machine_id = machine_id_path.read_text(encoding="utf-8").strip()
        except OSError:
            machine_id = ""
        if machine_id:
            return f"iha-smart-thermostat-machine-{machine_id[:12]}"

    hostname = _local_host_name().strip() or socket.gethostname().strip()
    safe_hostname = _safe_identifier_slug(hostname)
    if safe_hostname:
        return f"iha-smart-thermostat-host-{safe_hostname}"

    return "iha-smart-thermostat-local"


def _discovery_payload() -> dict:
    status = _thermostat_status_payload()
    thermostat_name = str(status.get("name") or "IHA Thermostat").strip() or "IHA Thermostat"
    serial = _stable_panel_serial()
    return {
        "ok": True,
        "name": thermostat_name,
        "thermostatName": thermostat_name,
        "friendly_name": thermostat_name,
        "serial": serial,
        "unique_id": serial,
        "id": serial,
        "hardware_id": serial,
        "mac_address": _primary_mac_address(),
        "manufacturer": "IHA",
        "model": "Smart Thermostat Wall Panel",
        "sw_version": _read_version_value(),
        "swVersion": _read_version_value(),
        "host": _local_host_name(),
        "hostname": _local_host_name(),
        "ip": _local_ip_address(),
        "port": 8080,
        "api_path": "/api",
        "mdns_service": "_iha-thermostat._tcp.local.",
        "endpoints": {
            "status": "/api/thermostat/status",
            "control": "/api/thermostat/control",
            "hardwareTelemetry": "/api/hardware/telemetry",
            "motionControl": "/api/hardware/motion",
            "discovery": "/api/discovery",
        },
        "thermostat": status["thermostat"],
        "climate": status["thermostat"],
    }


def _safe_join_public(path: str) -> Path | None:
    if path == "/":
        path = "/index.html"
    candidate = (PUBLIC / path.lstrip("/")).resolve()
    try:
        candidate.relative_to(PUBLIC.resolve())
    except ValueError:
        return None
    return candidate


def _normalize_ha_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Home Assistant URL must include http:// or https:// and a host")
    return value


def _fetch_ha_covers(ha_url: str, token: str) -> list[dict]:
    ha_url = _normalize_ha_url(ha_url)
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing Home Assistant token")

    req = request.Request(
        f"{ha_url}/api/states",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "SmartThermostatPanel/0.1",
            "Connection": "close",
        },
        method="GET",
    )
    acquired = _HA_REQUEST_SEMAPHORE.acquire(timeout=HA_REQUEST_TIMEOUT_SECONDS)
    if not acquired:
        raise RuntimeError("Home Assistant requests are backed up; try again in a moment")
    try:
        with request.urlopen(req, timeout=HA_REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc
    finally:
        _HA_REQUEST_SEMAPHORE.release()

    states = json.loads(raw.decode("utf-8"))
    covers = []
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        if not entity_id.startswith("cover."):
            continue
        attrs = item.get("attributes") or {}
        covers.append({
            "entityId": entity_id,
            "name": attrs.get("friendly_name") or entity_id,
            "state": item.get("state") or "unknown",
            "currentPosition": attrs.get("current_position"),
            "currentTiltPosition": attrs.get("current_tilt_position"),
            "supportedFeatures": attrs.get("supported_features"),
        })
    covers.sort(key=lambda item: item["name"].lower())
    return covers


def _fetch_ha_cover_states_for_entities(ha_url: str, token: str, entity_ids: list[str]) -> list[dict]:
    """Fetch current state for selected cover entities with ordered batching."""
    wanted = _ordered_unique_entity_ids(entity_ids, {"cover"})
    items = _fetch_ha_state_items_for_entities(ha_url, token, wanted, {"cover"}, all_states_threshold=2)
    covers = []
    for item in items:
        attrs = item.get("attributes") or {}
        entity_id = str(item.get("entity_id", ""))
        covers.append({
            "entityId": entity_id,
            "name": attrs.get("friendly_name") or entity_id,
            "state": item.get("state") or "unknown",
            "currentPosition": attrs.get("current_position"),
            "currentTiltPosition": attrs.get("current_tilt_position"),
            "supportedFeatures": attrs.get("supported_features"),
        })
    by_id = {item["entityId"]: item for item in covers}
    return [by_id[entity_id] for entity_id in wanted if entity_id in by_id]


def _ha_json_request(ha_url: str, token: str, method: str, path: str, payload: dict | None = None, *, timeout: float | None = None) -> object:
    ha_url = _normalize_ha_url(ha_url)
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing Home Assistant token")

    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "SmartThermostatPanel/0.1",
        "Connection": "close",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(f"{ha_url}{path}", data=data, headers=headers, method=method)
    request_timeout = max(0.5, float(timeout if timeout is not None else HA_REQUEST_TIMEOUT_SECONDS))
    queue_timeout = max(0.5, min(request_timeout, HA_REQUEST_TIMEOUT_SECONDS))
    acquired = _HA_REQUEST_SEMAPHORE.acquire(timeout=queue_timeout)
    if not acquired:
        raise RuntimeError("Home Assistant requests are backed up; try again in a moment")
    try:
        with request.urlopen(req, timeout=request_timeout) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc
    finally:
        _HA_REQUEST_SEMAPHORE.release()

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw": raw.decode("utf-8", errors="replace")}




def _normalized_network_ip(value: object) -> str:
    """Normalize IPv4, IPv6, and IPv4-mapped IPv6 addresses for comparison."""
    ip = str(value or "").strip().lower()
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    if "%" in ip:
        ip = ip.split("%", 1)[0]
    return ip


def _configured_home_assistant_server_ips() -> set[str]:
    """Resolve the configured Home Assistant URL to trusted source addresses.

    The thermostat already stores the Home Assistant server URL. Allowing only
    that host to invoke the assistant endpoint lets Home Assistant automations
    trigger JARVIS while the temporary backup/config portal remains closed.
    """
    record = _read_panel_config_record()
    config = record.get("config") if isinstance(record, dict) else {}
    integrations = config.get("integrations") if isinstance(config, dict) else {}
    ha = integrations.get("homeAssistant") if isinstance(integrations, dict) else {}
    ha = ha if isinstance(ha, dict) else {}
    raw_url = str(ha.get("url") or "").strip()
    if not raw_url:
        return set()

    parsed = urlparse(raw_url if "://" in raw_url else f"http://{raw_url}")
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return set()

    trusted: set[str] = {_normalized_network_ip(host)}
    try:
        for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
            address = info[4][0] if info and len(info) > 4 and info[4] else ""
            normalized = _normalized_network_ip(address)
            if normalized:
                trusted.add(normalized)
    except OSError:
        # A literal IP or temporarily unavailable local DNS should not prevent
        # the direct host comparison above from working.
        pass
    return {item for item in trusted if item}


def _assistant_client_access_allowed(client_ip: object) -> bool:
    """Allow local calls, the configured HA server, or the temporary portal."""
    ip = _normalized_network_ip(client_ip)
    if ip in {"127.0.0.1", "::1", "localhost"}:
        return True
    if ip and ip in _configured_home_assistant_server_ips():
        return True
    return _config_web_portal_active(touch=False)


def _assistant_bool(value: object, fallback: bool = False) -> bool:
    if value is None:
        return bool(fallback)
    return _boolish(value)


def _assistant_config_payload() -> dict:
    """Return normalized, token-free voice-assistant settings."""
    record = _read_panel_config_record()
    config = record.get("config") if isinstance(record, dict) else {}
    integrations = config.get("integrations") if isinstance(config, dict) else {}
    ha = integrations.get("homeAssistant") if isinstance(integrations, dict) else {}
    ha = ha if isinstance(ha, dict) else {}
    defaults = DEFAULT_HOME_ASSISTANT_CONFIG.get("voiceAssistant") or {}
    raw = ha.get("voiceAssistant") if isinstance(ha.get("voiceAssistant"), dict) else {}
    voice = _merge_missing_defaults(defaults, raw)

    def clean_entity(value: object, domain: str = "") -> str:
        entity_id = str(value or "").strip()
        if not entity_id:
            return ""
        if domain and not entity_id.startswith(domain + "."):
            return ""
        return entity_id[:255]

    media_player_id = clean_entity(voice.get("mediaPlayerId"), "media_player")
    legacy_media = ha.get("mediaPlayerEntity") if isinstance(ha.get("mediaPlayerEntity"), dict) else {}
    fallback_media_player_id = clean_entity(
        ha.get("selectedMediaPlayerId") or legacy_media.get("entityId") or legacy_media.get("entity_id"),
        "media_player",
    )
    language = str(voice.get("language") or "en").strip().replace("_", "-")[:16] or "en"
    try:
        response_hold = max(0.0, min(15.0, float(voice.get("responseHoldSeconds", 2.0) or 0.0)))
    except (TypeError, ValueError):
        response_hold = 2.0
    try:
        announcement_volume = int(max(1, min(100, round(float(voice.get("announcementVolumePercent", 45) or 45)))))
    except (TypeError, ValueError):
        announcement_volume = 45
    return {
        "enabled": _assistant_bool(voice.get("enabled"), True),
        "agentId": clean_entity(voice.get("agentId"), "conversation"),
        "ttsEntityId": clean_entity(voice.get("ttsEntityId"), "tts"),
        "mediaPlayerId": media_player_id,
        "effectiveMediaPlayerId": media_player_id or fallback_media_player_id,
        "language": language,
        "speak": _assistant_bool(voice.get("speak"), True),
        "funMode": _assistant_bool(voice.get("funMode"), True),
        "playfulReplies": _assistant_bool(voice.get("playfulReplies"), True),
        "continueConversation": _assistant_bool(voice.get("continueConversation"), True),
        "showResponseText": _assistant_bool(voice.get("showResponseText"), True),
        "responseHoldSeconds": response_hold,
        "announcementVolumePercent": announcement_volume,
        "homeAssistantConfigured": bool(str(ha.get("url") or "").strip() and str(ha.get("token") or "").strip()),
    }


def _assistant_update_config(payload: dict) -> dict:
    if not _config_web_portal_active(touch=True):
        return {"ok": False, "error": "The temporary config portal is closed."}
    payload = payload if isinstance(payload, dict) else {}
    record = _read_panel_config_record()
    config = _deepcopy_json(record.get("config") if isinstance(record, dict) else {})
    if not isinstance(config, dict):
        config = {}
    integrations = config.setdefault("integrations", {})
    if not isinstance(integrations, dict):
        integrations = {}
        config["integrations"] = integrations
    ha = integrations.setdefault("homeAssistant", {})
    if not isinstance(ha, dict):
        ha = {}
        integrations["homeAssistant"] = ha
    current = ha.get("voiceAssistant") if isinstance(ha.get("voiceAssistant"), dict) else {}
    defaults = DEFAULT_HOME_ASSISTANT_CONFIG.get("voiceAssistant") or {}
    voice = _merge_missing_defaults(defaults, current)

    def entity_value(key: str, domain: str) -> str:
        text = str(payload.get(key, voice.get(key, "")) or "").strip()
        if text and not text.startswith(domain + "."):
            raise ValueError(f"{key} must be a {domain}. entity ID or blank")
        return text[:255]

    voice.update({
        "enabled": _assistant_bool(payload.get("enabled"), voice.get("enabled", True)),
        "agentId": entity_value("agentId", "conversation"),
        "ttsEntityId": entity_value("ttsEntityId", "tts"),
        "mediaPlayerId": entity_value("mediaPlayerId", "media_player"),
        "language": str(payload.get("language", voice.get("language", "en")) or "en").strip().replace("_", "-")[:16] or "en",
        "speak": _assistant_bool(payload.get("speak"), voice.get("speak", True)),
        "funMode": _assistant_bool(payload.get("funMode"), voice.get("funMode", True)),
        "playfulReplies": _assistant_bool(payload.get("playfulReplies"), voice.get("playfulReplies", True)),
        "continueConversation": _assistant_bool(payload.get("continueConversation"), voice.get("continueConversation", True)),
        "showResponseText": _assistant_bool(payload.get("showResponseText"), voice.get("showResponseText", True)),
    })
    try:
        voice["responseHoldSeconds"] = max(0.0, min(15.0, float(payload.get("responseHoldSeconds", voice.get("responseHoldSeconds", 2.0)) or 0.0)))
    except (TypeError, ValueError):
        voice["responseHoldSeconds"] = 2.0
    try:
        voice["announcementVolumePercent"] = int(max(1, min(100, round(float(payload.get("announcementVolumePercent", voice.get("announcementVolumePercent", 45)) or 45)))))
    except (TypeError, ValueError):
        voice["announcementVolumePercent"] = 45
    ha["voiceAssistant"] = voice
    saved = _write_panel_config_record(config)
    return {
        "ok": True,
        "message": "Voice assistant settings saved.",
        "version": saved.get("version"),
        "updatedAt": saved.get("updatedAt"),
        "assistant": _assistant_config_payload(),
    }


def _assistant_public_state_locked() -> dict:
    state = _deepcopy_json(_ASSISTANT_STATE)
    state.pop("clearAtMonotonic", None)
    return state


def _assistant_status_payload(include_config: bool = False) -> dict:
    now = time.monotonic()
    with _ASSISTANT_LOCK:
        clear_at = float(_ASSISTANT_STATE.get("clearAtMonotonic") or 0.0)
        if clear_at > 0 and now >= clear_at and str(_ASSISTANT_STATE.get("stage") or "") in {"speaking", "complete", "error"}:
            _ASSISTANT_STATE.update({
                "active": False,
                "stage": "idle",
                "statusText": "Standing by.",
                "error": "",
                "clearAtMonotonic": 0.0,
                "updatedAt": int(time.time() * 1000),
                "speechPlayed": False,
            })
        payload = {"ok": True, "assistant": _assistant_public_state_locked()}
    if include_config:
        payload["config"] = _assistant_config_payload()
    return payload


def _assistant_set_state(stage: str, **changes) -> dict:
    stage = str(stage or "idle").strip().lower() or "idle"
    with _ASSISTANT_LOCK:
        _ASSISTANT_STATE.update(changes)
        _ASSISTANT_STATE["stage"] = stage
        _ASSISTANT_STATE["active"] = stage != "idle"
        _ASSISTANT_STATE["updatedAt"] = int(time.time() * 1000)
        return _assistant_public_state_locked()


def _assistant_stage_quip(stage: str, request_id: int) -> str:
    options = {
        "listening": (
            "Terminal link established. Pretending this keyboard is a microphone.",
            "Command channel open. No dramatic cape required.",
            "I heard the keyboard. Close enough for government automation.",
            "JARVIS-ish mode engaged. Legal says the '-ish' is important.",
        ),
        "processing": (
            "Consulting the silicon committee...",
            "Thinking very hard in several billion tiny yes-or-no decisions...",
            "Asking the house nicely. It responds better to flattery.",
            "Routing this through the unnecessarily dramatic glowing orb...",
            "Crunching electrons. Please do not feed the Raspberry Pi after midnight.",
        ),
        "speaking": (
            "Sending the answer to the selected speaker.",
            "Response ready for playback.",
            "The house has answered.",
        ),
        "error": (
            "Well, that went about as smoothly as a shopping cart with one bad wheel.",
            "A small digital gremlin has filed an objection.",
            "The electrons have unionized. Reviewing their demands now.",
        ),
    }
    choices = options.get(stage, ("Standing by.",))
    return choices[max(0, int(request_id)) % len(choices)]


def _assistant_extract_response(data: object) -> tuple[str, str, str, bool]:
    obj = data if isinstance(data, dict) else {}
    response = obj.get("response") if isinstance(obj.get("response"), dict) else {}
    speech = response.get("speech") if isinstance(response.get("speech"), dict) else {}
    plain = speech.get("plain") if isinstance(speech.get("plain"), dict) else {}
    ssml = speech.get("ssml") if isinstance(speech.get("ssml"), dict) else {}
    text = str(plain.get("speech") or "").strip()
    if not text and ssml.get("speech"):
        # Home Assistant may return SSML. The configured TTS provider receives
        # normal text here, so remove markup while preserving the spoken words.
        text = html.unescape(re.sub(r"<[^>]+>", " ", str(ssml.get("speech") or "")))
        text = " ".join(text.split())
    if not text:
        text = str(response.get("text") or obj.get("speech") or obj.get("response_text") or "").strip()
    response_type = str(response.get("response_type") or obj.get("response_type") or "").strip()
    conversation_id = str(obj.get("conversation_id") or "").strip()
    continue_conversation = _assistant_bool(obj.get("continue_conversation"), False)
    return text, response_type, conversation_id, continue_conversation


def _assistant_resolve_tts_entity(ha_url: str, token: str, configured: str) -> str:
    configured = str(configured or "").strip()
    if configured:
        return configured
    states = _ha_all_states_cached(ha_url, token)
    available = sorted(
        str(item.get("entity_id") or "").strip()
        for item in states
        if isinstance(item, dict) and str(item.get("entity_id") or "").startswith("tts.")
    )
    for preferred in ("tts.home_assistant_cloud", "tts.openai", "tts.piper"):
        if preferred in available:
            return preferred
    return available[0] if available else ""


def _assistant_playful_spoken_text(text: str, request_id: int, enabled: bool, response_type: str = "") -> str:
    clean = str(text or "").strip()
    if not clean or not enabled:
        return clean
    # Keep the useful response intact and keep the joke short. Shorter prefixes
    # reduce TTS synthesis/playback time without making the assistant sterile.
    action_prefixes = (
        "Naturally. ",
        "Done. Try to look surprised. ",
        "Handled. No cape required. ",
        "The house bureaucracy approves. ",
    )
    info_prefixes = (
        "The house says: ",
        "Tiny electronic drumroll: ",
        "Officially unnecessary drama: ",
        "The electrons agree: ",
    )
    pool = action_prefixes if response_type == "action_done" else info_prefixes
    return pool[max(0, int(request_id)) % len(pool)] + clean


def _assistant_reachable_media_url(ha_url: str, media_url: str) -> str:
    media_url = str(media_url or "").strip()
    if not media_url:
        return ""
    base = urlparse(str(ha_url or "").strip())
    parsed = urlparse(media_url)
    if not parsed.scheme:
        path = media_url if media_url.startswith("/") else "/" + media_url
        return urlunparse((base.scheme or "http", base.netloc, path, "", "", ""))
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"} and base.netloc:
        return urlunparse((base.scheme or parsed.scheme, base.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return media_url


def _assistant_tts_speak(
    ha_url: str,
    token: str,
    tts_entity_id: str,
    media_player_id: str,
    message: str,
    language: str,
    announcement_volume_percent: int = 45,
) -> object:
    if not tts_entity_id:
        raise ValueError("No Home Assistant TTS entity is configured or available")
    if not media_player_id:
        raise ValueError("No Sonos/media player entity is configured")
    volume = int(max(1, min(100, round(float(announcement_volume_percent or 45)))))

    # Sonos supports announcement overlays with a dedicated volume in
    # media_player.play_media. The speaker restores its prior playback and
    # volume after the announcement, so we never have to guess when speech ends
    # or leave the user's music at the JARVIS volume.
    try:
        tts_payload = {
            "engine_id": tts_entity_id,
            "message": message,
            "cache": True,
        }
        generated = _ha_json_request(
            ha_url, token, "POST", "/api/tts_get_url", tts_payload,
            timeout=ASSISTANT_HA_TIMEOUT_SECONDS,
        )
        media_url = _assistant_reachable_media_url(ha_url, str((generated or {}).get("url") or (generated or {}).get("path") or ""))
        if not media_url:
            raise ValueError("Home Assistant did not return a TTS media URL")
        result = _ha_json_request(
            ha_url,
            token,
            "POST",
            "/api/services/media_player/play_media",
            {
                "entity_id": media_player_id,
                "media_content_id": media_url,
                "media_content_type": "music",
                "announce": True,
                "extra": {"volume": volume},
            },
            timeout=ASSISTANT_HA_TIMEOUT_SECONDS,
        )
        return {"method": "sonos_announcement", "volumeManaged": True, "volumePercent": volume, "result": result}
    except Exception as announcement_error:
        # Preserve compatibility with non-Sonos players or older Sonos
        # firmware. This fallback speaks normally but cannot promise a separate
        # announcement volume.
        fallback = _ha_json_request(
            ha_url,
            token,
            "POST",
            "/api/services/tts/speak",
            {
                "entity_id": tts_entity_id,
                "media_player_entity_id": media_player_id,
                "message": message,
                "cache": True,
            },
            timeout=ASSISTANT_HA_TIMEOUT_SECONDS,
        )
        return {
            "method": "tts_speak_fallback",
            "volumeManaged": False,
            "volumePercent": volume,
            "announcementError": str(announcement_error),
            "result": fallback,
        }


def _assistant_estimated_hold_seconds(message: str, extra_seconds: float = 0.0) -> float:
    words = max(1, len(str(message or "").split()))
    # Natural TTS commonly lands around 145-175 WPM. Add startup/announcement time.
    return max(4.0, min(28.0, (words / 2.45) + 2.8 + max(0.0, float(extra_seconds or 0.0))))



def _assistant_format_temperature(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if not (-40.0 <= number <= 130.0):
        return ""
    rounded = round(number, 1)
    return str(int(rounded)) if float(rounded).is_integer() else f"{rounded:.1f}"


def _assistant_local_fast_response(text: str) -> tuple[str, str, str] | None:
    """Answer a narrow set of read-only thermostat questions without an LLM.

    This deliberately does not interpret control commands. It only handles short,
    unambiguous status questions already available in the thermostat's RAM, so it
    cannot interfere with Home Assistant automations or equipment safety logic.
    """
    normalized = " ".join(re.sub(r"[^a-z0-9.]+", " ", str(text or "").lower()).split())
    words = normalized.split()
    if not normalized or len(words) > 18:
        return None
    blocked = (
        " and ", " then ", " also ", "introduce", "explain", "why ", "forecast",
        "outside", "bedroom", "office", "weather", "change ", "raise ",
        "lower ", "turn ", "switch ", "make ",
    )
    padded = f" {normalized} "
    if any(marker in padded for marker in blocked):
        return None

    thermostat = (_read_thermostat_record().get("thermostat") or {})
    if not isinstance(thermostat, dict):
        return None
    location = str(thermostat.get("name") or "living room").strip() or "living room"
    if location.lower().endswith(" test"):
        location = location[:-5].strip() or "living room"
    if location.lower().replace(" ", "") in {"livingroom", "livingroomclimate"}:
        location = "living room"

    asks_temperature = any(term in normalized for term in ("temperature", "temp", "degrees"))
    asks_current = any(term in normalized for term in ("current", "currently", "right now", "what is", "whats", "how warm", "how cold"))
    if asks_temperature and asks_current:
        current = _assistant_format_temperature(thermostat.get("currentTemp"))
        if current:
            return f"The {location} is currently {current} degrees.", "query_answer", "local_thermostat_temperature"

    asks_target = any(term in normalized for term in ("setpoint", "target", "set to", "thermostat setting"))
    if asks_target and any(term in normalized for term in ("what", "current", "currently")):
        target = _assistant_format_temperature(thermostat.get("targetTemp"))
        if target:
            return f"The thermostat is set to {target} degrees.", "query_answer", "local_thermostat_target"

    asks_mode = "mode" in words or "running" in words
    if asks_mode and any(term in normalized for term in ("what", "current", "currently")):
        mode = str(thermostat.get("mode") or "off").strip().lower() or "off"
        return f"The thermostat is currently in {mode} mode.", "query_answer", "local_thermostat_mode"
    return None

def _assistant_process_payload(payload: dict) -> dict:
    request_started = time.monotonic()
    payload = payload if isinstance(payload, dict) else {}
    text = str(payload.get("text") or payload.get("command") or "").strip()
    if not text:
        return {"ok": False, "error": "Enter a command in the text field."}
    if len(text) > 1200:
        return {"ok": False, "error": "Command is too long (maximum 1200 characters)."}
    config = _assistant_config_payload()
    if not config.get("enabled") and not _assistant_bool(payload.get("force"), False):
        return {"ok": False, "error": "The thermostat voice assistant is disabled in the config portal."}
    ha_url, token = _ha_credentials_from_panel_config()
    if not ha_url or not token:
        return {"ok": False, "error": "Home Assistant URL/token are not configured on this thermostat."}
    if not _ASSISTANT_PROCESS_LOCK.acquire(blocking=False):
        return {"ok": False, "busy": True, "error": "JARVIS is already handling another command.", "assistant": _assistant_status_payload().get("assistant")}

    global _ASSISTANT_REQUEST_SEQ
    try:
        with _ASSISTANT_LOCK:
            _ASSISTANT_REQUEST_SEQ += 1
            request_id = _ASSISTANT_REQUEST_SEQ
            previous_conversation_id = str(_ASSISTANT_STATE.get("conversationId") or "").strip()
        speak = _assistant_bool(payload.get("speak"), config.get("speak", True))
        fun_mode = _assistant_bool(payload.get("funMode"), config.get("funMode", True))
        playful_replies = _assistant_bool(payload.get("playfulReplies"), config.get("playfulReplies", True)) and fun_mode
        language = str(payload.get("language") or config.get("language") or "en").strip()[:16] or "en"
        agent_id = str(payload.get("agentId") or config.get("agentId") or "").strip()
        media_player_id = str(payload.get("mediaPlayerId") or config.get("effectiveMediaPlayerId") or "").strip()
        configured_tts = str(payload.get("ttsEntityId") or config.get("ttsEntityId") or "").strip()
        try:
            announcement_volume = int(max(1, min(100, round(float(payload.get("announcementVolumePercent", config.get("announcementVolumePercent", 45)) or 45)))))
        except (TypeError, ValueError):
            announcement_volume = int(config.get("announcementVolumePercent") or 45)
        new_conversation = _assistant_bool(payload.get("newConversation"), False)
        conversation_id = str(payload.get("conversationId") or "").strip()
        if not conversation_id and config.get("continueConversation") and not new_conversation:
            conversation_id = previous_conversation_id

        _assistant_set_state(
            "processing",
            requestId=request_id,
            command=text,
            response="",
            spokenResponse="",
            statusText=_assistant_stage_quip("processing", request_id) if fun_mode else "Processing...",
            error="",
            startedAt=int(time.time() * 1000),
            clearAtMonotonic=0.0,
            mediaPlayerId=media_player_id,
            ttsEntityId=configured_tts,
            speechPlayed=False,
        )

        conversation_started = time.monotonic()
        quick = _assistant_local_fast_response(text)
        route = "home_assistant_conversation"
        returned_conversation_id = ""
        continue_conversation = False
        if quick:
            response_text, response_type, route = quick
        else:
            conversation_payload = {"text": text, "language": language}
            if agent_id:
                conversation_payload["agent_id"] = agent_id
            if conversation_id:
                conversation_payload["conversation_id"] = conversation_id
            raw = _ha_json_request(
                ha_url,
                token,
                "POST",
                "/api/conversation/process",
                conversation_payload,
                timeout=ASSISTANT_HA_TIMEOUT_SECONDS,
            )
            response_text, response_type, returned_conversation_id, continue_conversation = _assistant_extract_response(raw)
            if not response_text:
                response_text = "Home Assistant completed the request but did not return a spoken response."
        conversation_ms = int((time.monotonic() - conversation_started) * 1000)

        saved_conversation_id = returned_conversation_id if config.get("continueConversation") else ""
        spoken_response = _assistant_playful_spoken_text(response_text, request_id, playful_replies, response_type)
        tts_entity_id = ""
        speech_played = False
        speech_error = ""

        _assistant_set_state(
            "speaking",
            response=response_text if config.get("showResponseText") else "",
            spokenResponse=spoken_response,
            statusText="Response ready.",
            conversationId=saved_conversation_id,
            mediaPlayerId=media_player_id,
        )
        tts_started = time.monotonic()
        if speak:
            try:
                tts_entity_id = _assistant_resolve_tts_entity(ha_url, token, configured_tts)
                _assistant_set_state("speaking", ttsEntityId=tts_entity_id)
                _assistant_tts_speak(ha_url, token, tts_entity_id, media_player_id, spoken_response, language, announcement_volume)
                speech_played = True
            except Exception as exc:
                speech_error = str(exc)
        tts_ms = int((time.monotonic() - tts_started) * 1000) if speak else 0
        total_ms = int((time.monotonic() - request_started) * 1000)
        timings = {
            "conversationMs": conversation_ms,
            "ttsRequestMs": tts_ms,
            "totalMs": total_ms,
        }

        hold_seconds = _assistant_estimated_hold_seconds(spoken_response if speech_played else response_text, config.get("responseHoldSeconds", 2.0))
        status_text = "Response ready."
        if speak and speech_error:
            status_text = "The answer is ready, but Sonos/TTS needs configuration."
        state = _assistant_set_state(
            "speaking",
            response=response_text if config.get("showResponseText") else "",
            spokenResponse=spoken_response,
            statusText=status_text,
            error=speech_error,
            ttsEntityId=tts_entity_id or configured_tts,
            speechPlayed=speech_played,
            clearAtMonotonic=time.monotonic() + hold_seconds,
        )
        return {
            "ok": True,
            "requestId": request_id,
            "command": text,
            "response": response_text,
            "spokenResponse": spoken_response,
            "responseType": response_type,
            "route": route,
            "conversationId": returned_conversation_id,
            "continueConversation": continue_conversation,
            "agentId": agent_id or "home_assistant/default",
            "mediaPlayerId": media_player_id,
            "ttsEntityId": tts_entity_id or configured_tts,
            "announcementVolumePercent": announcement_volume,
            "speechPlayed": speech_played,
            "speechError": speech_error,
            "timings": timings,
            "assistant": state,
        }
    except Exception as exc:
        with _ASSISTANT_LOCK:
            request_id = int(_ASSISTANT_STATE.get("requestId") or 0)
        state = _assistant_set_state(
            "error",
            statusText="Assistant request failed.",
            error=str(exc),
            response="",
            spokenResponse="",
            speechPlayed=False,
            clearAtMonotonic=time.monotonic() + 10.0,
        )
        return {
            "ok": False,
            "error": str(exc),
            "timings": {"totalMs": int((time.monotonic() - request_started) * 1000)},
            "assistant": state,
        }
    finally:
        _ASSISTANT_PROCESS_LOCK.release()


def _ha_state_cached(ha_url: str, token: str, entity_id: str, ttl: float | None = None) -> dict:
    """Cached direct /api/states/<entity_id> lookup.

    This is used by the local thermostat status/runtime path so a slow Home
    Assistant request cannot make the touchscreen feel sticky every few seconds.
    """
    entity_id = str(entity_id or "").strip()
    if not entity_id:
        return {}
    ha_url = _normalize_ha_url(ha_url)
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing Home Assistant token")
    ttl = HA_ENTITY_STATE_CACHE_TTL_SECONDS if ttl is None else max(0.0, float(ttl))
    cache_key = (ha_url, token, entity_id)
    now = time.monotonic()
    with _HA_ENTITY_STATE_CACHE_LOCK:
        cached = _HA_ENTITY_STATE_CACHE.get(cache_key)
        if cached and now - cached[0] <= ttl:
            return _deepcopy_json(cached[1])
    item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
    if not isinstance(item, dict):
        item = {}
    with _HA_ENTITY_STATE_CACHE_LOCK:
        _HA_ENTITY_STATE_CACHE[cache_key] = (time.monotonic(), _deepcopy_json(item))
    return item


def _invalidate_ha_entity_state_cache(ha_url: str, token: str, entity_ids: list[str] | None = None) -> None:
    try:
        norm_url = _normalize_ha_url(ha_url)
        norm_token = (token or "").strip()
    except ValueError:
        return
    wanted = {str(x or "").strip() for x in (entity_ids or []) if str(x or "").strip()}
    with _HA_ENTITY_STATE_CACHE_LOCK:
        for key in list(_HA_ENTITY_STATE_CACHE.keys()):
            if key[0] == norm_url and key[1] == norm_token and (not wanted or key[2] in wanted):
                _HA_ENTITY_STATE_CACHE.pop(key, None)


def _ha_all_states_cached(ha_url: str, token: str) -> list[dict]:
    """Return HA /api/states with a tiny cache shared by all panel pollers.

    The touchscreen polls several HA-backed widgets. When two widgets poll at
    nearly the same time, this keeps the Pi from asking Home Assistant for the
    full state table repeatedly. Service commands still bypass this cache.
    """
    ha_url = _normalize_ha_url(ha_url)
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing Home Assistant token")

    cache_key = (ha_url, token)
    now = time.monotonic()
    with _HA_STATES_CACHE_LOCK:
        cached = _HA_STATES_CACHE.get(cache_key)
        if cached and now - cached[0] <= HA_STATES_CACHE_TTL_SECONDS:
            return cached[1]

    states = _ha_json_request(ha_url, token, "GET", "/api/states")
    if not isinstance(states, list):
        states = []

    with _HA_STATES_CACHE_LOCK:
        _HA_STATES_CACHE[cache_key] = (time.monotonic(), states)
    return states


def _invalidate_ha_state_cache(ha_url: str, token: str) -> None:
    try:
        cache_key = (_normalize_ha_url(ha_url), (token or "").strip())
    except ValueError:
        return
    with _HA_STATES_CACHE_LOCK:
        _HA_STATES_CACHE.pop(cache_key, None)
    _invalidate_ha_entity_state_cache(ha_url, token)


def _ordered_unique_entity_ids(entity_ids: list[str] | None, domains: set[str] | None = None) -> list[str]:
    wanted: list[str] = []
    seen: set[str] = set()
    for raw in entity_ids or []:
        entity_id = str(raw or "").strip()
        if not entity_id or entity_id in seen or "." not in entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        if domains and domain not in domains:
            continue
        seen.add(entity_id)
        wanted.append(entity_id)
    return wanted


def _fetch_ha_state_items_for_entities(
    ha_url: str,
    token: str,
    entity_ids: list[str] | None,
    domains: set[str] | None = None,
    all_states_threshold: int = 3,
    use_cache: bool = True,
) -> list[dict]:
    """Fetch selected HA entity states in order with fewer REST calls.

    One or two entities are cheaper as direct /api/states/<entity_id> calls.
    Larger batches use one cached /api/states call, which is much lighter than
    making a dozen separate calls from a wall-mounted Pi.
    """
    wanted = _ordered_unique_entity_ids(entity_ids, domains)
    if not wanted:
        return []

    if len(wanted) < max(2, int(all_states_threshold or 3)):
        items: list[dict] = []
        for entity_id in wanted:
            item = _ha_state_cached(ha_url, token, entity_id) if use_cache else _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
            if isinstance(item, dict) and item:
                items.append(item)
                if not use_cache:
                    try:
                        cache_key = (_normalize_ha_url(ha_url), (token or "").strip(), entity_id)
                        with _HA_ENTITY_STATE_CACHE_LOCK:
                            _HA_ENTITY_STATE_CACHE[cache_key] = (time.monotonic(), _deepcopy_json(item))
                    except Exception:
                        pass
        return items

    if use_cache:
        states = _ha_all_states_cached(ha_url, token)
    else:
        states = _ha_json_request(ha_url, token, "GET", "/api/states")
        if not isinstance(states, list):
            states = []
        try:
            cache_key = (_normalize_ha_url(ha_url), (token or "").strip())
            with _HA_STATES_CACHE_LOCK:
                _HA_STATES_CACHE[cache_key] = (time.monotonic(), states)
        except Exception:
            pass
    by_id = {str(item.get("entity_id", "")): item for item in states if isinstance(item, dict)}
    return [by_id[entity_id] for entity_id in wanted if entity_id in by_id]


def _normalize_media_player_item(ha_url: str, item: dict) -> dict:
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    picture = attrs.get("entity_picture") or ""
    if picture and picture.startswith("/"):
        picture = f"{_normalize_ha_url(ha_url)}{picture}"

    source_list = attrs.get("source_list") or []
    if isinstance(source_list, str):
        source_list = [source_list] if source_list.strip() else []
    elif not isinstance(source_list, list):
        source_list = []

    return {
        "entityId": entity_id,
        "name": attrs.get("friendly_name") or entity_id,
        "state": item.get("state") or "unknown",
        "volumeLevel": attrs.get("volume_level"),
        "isVolumeMuted": attrs.get("is_volume_muted"),
        "mediaContentId": attrs.get("media_content_id") or "",
        "mediaContentType": attrs.get("media_content_type") or "",
        "mediaTitle": attrs.get("media_title") or "",
        "mediaArtist": attrs.get("media_artist") or "",
        "mediaAlbum": attrs.get("media_album_name") or attrs.get("media_album") or "",
        "mediaPosition": attrs.get("media_position"),
        "mediaDuration": attrs.get("media_duration"),
        "source": attrs.get("source") or "",
        "sourceList": source_list,
        "pictureUrl": picture,
        "supportedFeatures": attrs.get("supported_features"),
    }


def _fetch_ha_media_players(ha_url: str, token: str) -> list[dict]:
    ha_url = _normalize_ha_url(ha_url)
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing Home Assistant token")

    req = request.Request(
        f"{ha_url}/api/states",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "SmartThermostatPanel/0.1",
            "Connection": "close",
        },
        method="GET",
    )
    acquired = _HA_REQUEST_SEMAPHORE.acquire(timeout=HA_REQUEST_TIMEOUT_SECONDS)
    if not acquired:
        raise RuntimeError("Home Assistant requests are backed up; try again in a moment")
    try:
        with request.urlopen(req, timeout=HA_REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc
    finally:
        _HA_REQUEST_SEMAPHORE.release()

    states = json.loads(raw.decode("utf-8"))
    players = []
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        if not entity_id.startswith("media_player."):
            continue
        players.append(_normalize_media_player_item(ha_url, item))
    players.sort(key=lambda item: item["name"].lower())
    return players


def _fetch_ha_media_states_for_entities(ha_url: str, token: str, entity_ids: list[str]) -> list[dict]:
    """Fetch selected media player states without pulling every HA entity for one player."""
    wanted = _ordered_unique_entity_ids(entity_ids, {"media_player"})
    if len(wanted) == 1:
        return [_fetch_ha_media_state(ha_url, token, wanted[0])]
    items = _fetch_ha_state_items_for_entities(ha_url, token, wanted, {"media_player"}, all_states_threshold=2)
    players = [_normalize_media_player_item(ha_url, item) for item in items]
    by_id = {item["entityId"]: item for item in players}
    return [by_id[entity_id] for entity_id in wanted if entity_id in by_id]


def _fetch_ha_media_state(ha_url: str, token: str, entity_id: str) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("media_player."):
        raise ValueError("Entity must be a media_player.* entity")
    item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
    return _normalize_media_player_item(ha_url, item)


def _normalize_generic_entity(item: dict) -> dict:
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    payload = {
        "entityId": entity_id,
        "domain": domain,
        "name": attrs.get("friendly_name") or entity_id,
        "state": item.get("state") or "unknown",
        "deviceClass": attrs.get("device_class") or "",
        "icon": attrs.get("icon") or "",
        "supportedFeatures": attrs.get("supported_features"),
        "unitOfMeasurement": attrs.get("unit_of_measurement") or "",
        "currentPosition": attrs.get("current_position"),
        "isClosed": attrs.get("is_closed"),
        "lastChanged": item.get("last_changed"),
        "lastUpdated": item.get("last_updated"),
    }
    if domain in {"number", "input_number"}:
        try:
            payload.update(_normalize_number_control(item))
            payload["domain"] = domain
        except Exception:
            pass
    if domain == "alarm_control_panel":
        payload.update(_normalize_alarm_control_item(item))
    if domain == "light":
        payload.update(_normalize_light_item(item))
    if domain == "media_player":
        payload.update({
            "volumeLevel": attrs.get("volume_level"),
            "source": attrs.get("source") or "",
            "sourceList": attrs.get("source_list") or [],
            "mediaTitle": attrs.get("media_title") or "",
            "mediaArtist": attrs.get("media_artist") or "",
            "mediaAlbum": attrs.get("media_album_name") or attrs.get("media_album") or "",
        })
        payload["domain"] = domain
    return payload



def _normalize_alarm_control_item(item: dict) -> dict:
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    return {
        "entityId": entity_id,
        "domain": "alarm_control_panel",
        "name": attrs.get("friendly_name") or entity_id,
        "state": item.get("state") or "unknown",
        "codeFormat": attrs.get("code_format"),
        "changedBy": attrs.get("changed_by"),
        "codeArmRequired": attrs.get("code_arm_required"),
        "armMode": attrs.get("arm_mode"),
        "nextState": attrs.get("next_state"),
        "openSensors": attrs.get("open_sensors"),
        "bypassedSensors": attrs.get("bypassed_sensors"),
        "delay": attrs.get("delay"),
        "lastTriggered": attrs.get("last_triggered"),
        "supportedFeatures": attrs.get("supported_features"),
    }


def _normalize_binary_sensor_item(item: dict) -> dict:
    payload = _normalize_generic_entity(item)
    payload["domain"] = "binary_sensor"
    return payload


def _fetch_ha_binary_sensor_state(ha_url: str, token: str, entity_id: str) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("binary_sensor."):
        raise ValueError("Entity must be a binary_sensor.* entity")
    item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
    return _normalize_binary_sensor_item(item)


def _fetch_ha_binary_sensor_states_for_entities(ha_url: str, token: str, entity_ids: list[str]) -> list[dict]:
    wanted = _ordered_unique_entity_ids(entity_ids, {"binary_sensor"})
    if len(wanted) == 1:
        return [_fetch_ha_binary_sensor_state(ha_url, token, wanted[0])]
    items = _fetch_ha_state_items_for_entities(ha_url, token, wanted, {"binary_sensor"}, all_states_threshold=2)
    sensors = [_normalize_binary_sensor_item(item) for item in items]
    by_id = {item["entityId"]: item for item in sensors}
    return [by_id[entity_id] for entity_id in wanted if entity_id in by_id]


def _fetch_ha_alarm_state(ha_url: str, token: str, entity_id: str) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("alarm_control_panel."):
        raise ValueError("Entity must be an alarm_control_panel.* entity")
    item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
    return _normalize_alarm_control_item(item)


def _fetch_ha_alarm_states_for_entities(ha_url: str, token: str, entity_ids: list[str]) -> list[dict]:
    wanted = _ordered_unique_entity_ids(entity_ids, {"alarm_control_panel"})
    if len(wanted) == 1:
        return [_fetch_ha_alarm_state(ha_url, token, wanted[0])]
    items = _fetch_ha_state_items_for_entities(ha_url, token, wanted, {"alarm_control_panel"}, all_states_threshold=2)
    alarms = [_normalize_alarm_control_item(item) for item in items]
    by_id = {item["entityId"]: item for item in alarms}
    return [by_id[entity_id] for entity_id in wanted if entity_id in by_id]


def _audit_alarm_action(event: str, **fields) -> None:
    """Append a small local audit trail without ever recording the alarm code."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": str(event or "alarm_action"),
    }
    for key, value in fields.items():
        if key.lower() in {"code", "token", "password"}:
            continue
        record[str(key)] = value
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with _ALARM_ACTION_AUDIT_LOCK:
            # Keep the appliance log bounded. One megabyte is several thousand
            # alarm events and is enough to distinguish a state refresh from an
            # actual service call without allowing indefinite growth.
            try:
                if ALARM_ACTION_AUDIT_FILE.exists() and ALARM_ACTION_AUDIT_FILE.stat().st_size > 1_000_000:
                    rotated = ALARM_ACTION_AUDIT_FILE.with_suffix(".log.1")
                    try:
                        rotated.unlink(missing_ok=True)
                    except TypeError:
                        if rotated.exists():
                            rotated.unlink()
                    ALARM_ACTION_AUDIT_FILE.replace(rotated)
            except OSError:
                pass
            with ALARM_ACTION_AUDIT_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:
        print(f"Alarm audit write failed: {exc}", flush=True)


def _call_alarm_service(
    ha_url: str,
    token: str,
    entity_id: str,
    action: str,
    code: str | None = None,
    confirmation: dict | None = None,
    source: str = "",
) -> dict:
    entity_id = (entity_id or "").strip()
    source = str(source or "unknown").strip() or "unknown"
    if not entity_id.startswith("alarm_control_panel."):
        _audit_alarm_action("rejected", entityId=entity_id, action=action, source=source, reason="invalid_entity")
        raise ValueError("Entity must be an alarm_control_panel.* entity")

    service_by_action = {
        "disarm": "alarm_disarm",
        "arm_home": "alarm_arm_home",
        "arm_away": "alarm_arm_away",
        "arm_night": "alarm_arm_night",
    }
    # Never default an absent/malformed request to Disarm. An explicit action is
    # required for every alarm service call.
    action = str(action or "").strip().lower()
    service = service_by_action.get(action)
    if not service:
        _audit_alarm_action("rejected", entityId=entity_id, action=action, source=source, reason="unsupported_action")
        raise ValueError("Unsupported alarm action")

    payload = {"entity_id": entity_id}
    code_value = str(code or "").strip()
    if action == "disarm":
        panel_record = _read_panel_config_record()
        panel_config = panel_record.get("config") if isinstance(panel_record, dict) else {}
        alarm_config = (panel_config or {}).get("alarm") if isinstance(panel_config, dict) else {}
        configured_code = str((alarm_config or {}).get("disarmCode") or "").strip()
        if not configured_code:
            _audit_alarm_action("rejected", entityId=entity_id, action=action, source=source, reason="code_not_configured")
            raise ValueError("Alarm disarm code is not configured")
        if not code_value:
            _audit_alarm_action("rejected", entityId=entity_id, action=action, source=source, reason="code_missing")
            raise ValueError("Alarm disarm code is required")
        if code_value != configured_code:
            _audit_alarm_action("rejected", entityId=entity_id, action=action, source=source, reason="invalid_code")
            raise ValueError("Invalid alarm disarm code")

        confirm = confirmation if isinstance(confirmation, dict) else {}
        method = str(confirm.get("method") or "").strip().lower()
        try:
            confirmed_at = int(confirm.get("confirmedAt") or 0)
        except (TypeError, ValueError):
            confirmed_at = 0
        try:
            digits = int(confirm.get("digits") or 0)
        except (TypeError, ValueError):
            digits = 0
        age_ms = abs(int(time.time() * 1000) - confirmed_at) if confirmed_at else 999_999_999
        if method != "keypad" or digits != len(code_value) or age_ms > 15_000:
            _audit_alarm_action(
                "rejected",
                entityId=entity_id,
                action=action,
                source=source,
                reason="fresh_keypad_confirmation_required",
                confirmationMethod=method,
                confirmationAgeMs=age_ms,
                digits=digits,
            )
            raise ValueError("Fresh keypad confirmation is required to disarm")

    if code_value:
        payload["code"] = code_value

    _audit_alarm_action(
        "service_call",
        entityId=entity_id,
        action=action,
        source=source,
        hasCode=bool(code_value),
    )
    _ha_json_request(ha_url, token, "POST", f"/api/services/alarm_control_panel/{service}", payload)
    _invalidate_ha_state_cache(ha_url, token)
    try:
        result = _fetch_ha_alarm_state(ha_url, token, entity_id)
    except Exception:
        optimistic_state = "disarmed" if action == "disarm" else action.replace("arm_", "armed_")
        result = {"entityId": entity_id, "name": entity_id, "domain": "alarm_control_panel", "state": optimistic_state}
    _audit_alarm_action(
        "service_result",
        entityId=entity_id,
        action=action,
        source=source,
        state=str((result or {}).get("state") or "unknown"),
    )
    return result


def _fetch_ha_entities(ha_url: str, token: str, domains: list[str] | None = None) -> list[dict]:
    wanted = {str(domain).strip().lower() for domain in (domains or []) if str(domain).strip()}
    states = _ha_json_request(ha_url, token, "GET", "/api/states")
    if not isinstance(states, list):
        return []
    entities = []
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        if wanted and domain not in wanted:
            continue
        entities.append(_normalize_generic_entity(item))
    entities.sort(key=lambda item: (item.get("domain", ""), str(item.get("name") or item.get("entityId") or "").lower()))
    return entities




IHA_CLIMATE_DISCOVERY_ATTRS = {
    "iha_panel",
    "iha_sync_capable",
    "iha_api_poll_seconds",
    "iha_api_status_source",
    "iha_api_stale_status_seconds",
    "iha_entity_available_from_cache",
    "iha_panel_url",
}


def _sync_peer_entity_id(entry: object) -> str:
    if not isinstance(entry, dict):
        return ""
    entity_id = str(entry.get("entityId") or entry.get("entity_id") or "").strip()
    return entity_id if entity_id.startswith("climate.") else ""


def _normalize_sync_peer_entry(entry: object) -> dict | None:
    if not isinstance(entry, dict):
        return None
    entity_id = _sync_peer_entity_id(entry)
    if not entity_id:
        return None
    name = _preferred_ha_display_name(
        entity_id,
        entry.get("friendlyName"),
        entry.get("friendly_name"),
        entry.get("name"),
    )
    state = str(entry.get("state") or "unknown").strip().lower() or "unknown"
    result = {
        "entityId": entity_id,
        "name": name[:120],
        "friendlyName": name[:120],
        "domain": "climate",
        "state": state[:80],
    }
    for key in (
        "currentTemp",
        "targetTemp",
        "hvacAction",
        "away",
        "doorPauseActive",
        "ihaPanel",
        "syncCapable",
        "serial",
        "panelUrl",
        "arrivingButtonEntityId",
    ):
        if key in entry:
            result[key] = entry.get(key)
    return result


def _normalize_sync_peer_entries(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    peers: list[dict] = []
    seen: set[str] = set()
    for raw in value:
        peer = _normalize_sync_peer_entry(raw)
        if not peer:
            continue
        entity_id = peer["entityId"]
        if entity_id in seen:
            continue
        seen.add(entity_id)
        peers.append(peer)
    return peers


def _ha_climate_item_is_iha(item: dict) -> bool:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    if any(key in attrs for key in IHA_CLIMATE_DISCOVERY_ATTRS):
        return True
    manufacturer = str(attrs.get("manufacturer") or "").strip().lower()
    model = str(attrs.get("model") or "").strip().lower()
    friendly = str(attrs.get("friendly_name") or "").strip().lower()
    return manufacturer == "iha" or "iha" in model or friendly.startswith("iha ")


def _normalize_sync_climate_item(item: dict, arriving_buttons_by_serial: dict[str, str] | None = None) -> dict | None:
    if not isinstance(item, dict):
        return None
    entity_id = str(item.get("entity_id") or "").strip()
    if not entity_id.startswith("climate."):
        return None
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    name = _preferred_ha_display_name(entity_id, attrs.get("friendly_name"))
    state = str(item.get("state") or "unknown").strip().lower() or "unknown"
    preset = str(attrs.get("preset_mode") or "").strip().lower()
    away = preset == "away" or bool(attrs.get("target_temperature_locked_by_preset"))
    door_pause_active = bool(attrs.get("door_pause_active"))
    serial = str(attrs.get("iha_serial") or attrs.get("serial") or "").strip()
    result = {
        "entityId": entity_id,
        "name": name[:120],
        "friendlyName": name[:120],
        "domain": "climate",
        "state": state[:80],
        "currentTemp": attrs.get("current_temperature"),
        "targetTemp": attrs.get("temperature"),
        "hvacAction": attrs.get("hvac_action"),
        "away": away,
        "doorPauseActive": door_pause_active,
        "ihaPanel": _ha_climate_item_is_iha(item),
        "syncCapable": _ha_climate_item_is_iha(item),
        "serial": serial,
        "panelUrl": str(
            attrs.get("iha_panel_url")
            or attrs.get("iha_api_base_url")
            or attrs.get("panel_url")
            or ""
        ).strip(),
    }
    if serial and isinstance(arriving_buttons_by_serial, dict):
        button_id = str(arriving_buttons_by_serial.get(serial) or "").strip()
        if button_id.startswith("button."):
            result["arrivingButtonEntityId"] = button_id
    return result


def _ha_iha_action_buttons_by_serial(states: list[dict], action: str) -> dict[str, str]:
    wanted_action = str(action or "").strip().lower()
    result: dict[str, str] = {}
    for item in states if isinstance(states, list) else []:
        if not isinstance(item, dict):
            continue
        entity_id = str(item.get("entity_id") or "").strip()
        if not entity_id.startswith("button."):
            continue
        attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        item_action = str(attrs.get("iha_action") or "").strip().lower()
        serial = str(attrs.get("iha_serial") or attrs.get("serial") or "").strip()
        if item_action == wanted_action and serial:
            result[serial] = entity_id
    return result


def _fetch_ha_sync_thermostats(ha_url: str, token: str, saved: list[dict] | None = None, local_name: str = "") -> list[dict]:
    """Return IHA thermostat climate entities visible to Home Assistant.

    Discovery intentionally uses Home Assistant as the bus.  That avoids opening
    LAN scanning/mDNS behavior on the Raspberry Pi and lets Home Assistant keep
    owning IP changes, authentication and stale/unavailable handling.
    """
    saved_by_id = {peer["entityId"]: peer for peer in _normalize_sync_peer_entries(saved or [])}
    states = _ha_json_request(ha_url, token, "GET", "/api/states")
    if not isinstance(states, list):
        states = []
    arriving_buttons_by_serial = _ha_iha_action_buttons_by_serial(states, "arriving-sync-receiver")

    local_key = str(local_name or "").strip().casefold()
    by_id: dict[str, dict] = {}
    for item in states:
        if not isinstance(item, dict):
            continue
        entity_id = str(item.get("entity_id") or "")
        if not entity_id.startswith("climate."):
            continue
        is_saved = entity_id in saved_by_id
        if not is_saved and not _ha_climate_item_is_iha(item):
            continue
        peer = _normalize_sync_climate_item(item, arriving_buttons_by_serial)
        if not peer:
            continue
        # Hide the current wall panel when HA exposes it with the same friendly
        # name.  If names do not match, keep it visible rather than accidentally
        # hiding a different thermostat.
        if local_key and str(peer.get("name") or "").strip().casefold() == local_key and entity_id not in saved_by_id:
            continue
        peer["selected"] = entity_id in saved_by_id
        by_id[entity_id] = peer

    # Keep previously selected peers visible even if HA is briefly unavailable or
    # the peer has not yet been updated to expose the newer IHA sync markers.
    for entity_id, saved_peer in saved_by_id.items():
        by_id.setdefault(entity_id, saved_peer | {"selected": True})

    peers = list(by_id.values())
    peers.sort(key=lambda item: (not bool(item.get("selected")), str(item.get("name") or item.get("entityId") or "").lower()))
    return peers


def _sync_hvac_mode_for_ha(mode: object) -> str:
    value = str(mode or "").strip().lower()
    if value.startswith("hvacmode."):
        value = value.split(".", 1)[1]
    if value in {"auto", "heat_cool"}:
        return "heat_cool"
    return value if value in {"off", "heat", "cool"} else ""


def _sync_target_temperature(value: object) -> int | None:
    try:
        return int(max(45, min(95, round(float(value)))))
    except (TypeError, ValueError):
        return None


def _sync_preset_mode_for_ha(value: object) -> str:
    # Away and Return Home are deliberately local to each thermostat. Arriving
    # is the only preset that the temporary Sync button may propagate.
    preset = str(value or "").strip().lower()
    return "arriving" if preset == "arriving" else ""


def _normalize_sync_panel_url(value: object) -> str:
    """Return a safe panel API base URL discovered through Home Assistant."""
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"http://{raw}")
        scheme = str(parsed.scheme or "http").lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = parsed.port
        if port is None:
            port = 443 if scheme == "https" else 8080
        return f"{scheme}://{host}:{int(port)}"
    except (TypeError, ValueError):
        return ""


def _call_direct_iha_peer(
    panel_url: str,
    *,
    mode: object = None,
    target_temp: object = None,
    preset_mode: object = None,
) -> dict:
    """Deliver one Sync command directly to the destination IHA panel.

    Home Assistant supplies the destination's current address through the IHA
    climate attributes, but the actual command goes to the panel API. This avoids
    entity-id rename problems and HA service calls that return successfully while
    the destination never receives an Arriving or temperature command.
    """
    base_url = _normalize_sync_panel_url(panel_url)
    if not base_url:
        raise ValueError("Destination thermostat does not expose a valid panel URL")

    hvac_mode = _sync_hvac_mode_for_ha(mode)
    target = _sync_target_temperature(target_temp)
    preset = _sync_preset_mode_for_ha(preset_mode)
    command: dict[str, object] = {
        "commandSource": "peer-sync",
        "source": "peer-sync",
    }
    if preset == "arriving":
        command.update({
            "preset_mode": "arriving",
            "away": False,
            "presetChangeSource": "peer-sync",
        })
    if hvac_mode:
        command.update({"mode": hvac_mode, "modeChangeSource": "peer-sync"})
    if target is not None:
        command.update({
            "targetTemp": target,
            "lastComfortTarget": target,
            "targetChangeSource": "peer-sync",
        })
    if len(command) <= 2:
        raise ValueError("Nothing to sync")

    data = json.dumps(command).encode("utf-8")
    req = request.Request(
        f"{base_url}/api/thermostat/control",
        data=data,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SmartThermostatPeerSync/1.0",
            "Connection": "close",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=max(2.0, HA_REQUEST_TIMEOUT_SECONDS)) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Destination panel returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach destination panel {base_url}: {exc.reason}") from exc

    if not raw:
        return {"ok": True}
    try:
        result = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Destination panel returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Destination panel returned a non-object response")
    if result.get("ok", True) is False:
        raise RuntimeError(str(result.get("error") or "Destination panel rejected the Sync command"))
    return result


def _call_ha_sync_thermostat_services(
    ha_url: str,
    token: str,
    entity_ids: list[str],
    *,
    mode: object = None,
    target_temp: object = None,
    preset_mode: object = None,
    peer_metadata: list[dict] | None = None,
) -> dict:
    """Synchronize selected thermostats, preferring direct IHA panel delivery.

    HA remains the discovery directory and compatibility fallback. Updated IHA
    climate entities expose ``iha_panel_url`` so the source panel can address the
    destination directly and receive a real HTTP success/failure response.
    """
    wanted = _ordered_unique_entity_ids(entity_ids, {"climate"})
    if not wanted:
        return {"ok": True, "synced": [], "skipped": [], "errors": [], "count": 0}

    hvac_mode = _sync_hvac_mode_for_ha(mode)
    target = _sync_target_temperature(target_temp)
    preset = _sync_preset_mode_for_ha(preset_mode)
    if not hvac_mode and target is None and not preset:
        return {"ok": False, "error": "Nothing to sync", "synced": [], "skipped": [], "errors": []}

    saved_by_id = {
        peer["entityId"]: peer
        for peer in _normalize_sync_peer_entries(peer_metadata or [])
        if peer.get("entityId") in wanted
    }

    all_states: list[dict] = []
    ha_state_error = ""
    try:
        state_result = _ha_json_request(ha_url, token, "GET", "/api/states")
        if isinstance(state_result, list):
            all_states = [item for item in state_result if isinstance(item, dict)]
        else:
            ha_state_error = "Home Assistant returned an invalid state list"
    except Exception as exc:  # Direct delivery can still work from saved panel URLs.
        ha_state_error = str(exc)

    state_by_id = {
        str(item.get("entity_id") or ""): item
        for item in all_states
        if str(item.get("entity_id") or "") in wanted
    }
    arriving_buttons_by_serial = _ha_iha_action_buttons_by_serial(all_states, "arriving-sync-receiver")

    errors: list[dict] = []
    skipped: list[dict] = []
    successful: set[str] = set()
    direct_transports: list[str] = []
    fallback_transports: list[str] = []
    direct_failures: dict[str, str] = {}

    arriving_button_by_climate: dict[str, str] = {}
    arriving_preset_fallback_targets: list[str] = []
    mode_targets: list[str] = []
    temp_targets: list[str] = []

    for entity_id in wanted:
        item = state_by_id.get(entity_id)
        saved = saved_by_id.get(entity_id, {})
        attrs = item.get("attributes") if isinstance(item, dict) and isinstance(item.get("attributes"), dict) else {}
        friendly = attrs.get("friendly_name") or saved.get("name") or saved.get("friendlyName") or entity_id
        serial = str(attrs.get("iha_serial") or attrs.get("serial") or saved.get("serial") or "").strip()
        panel_url = _normalize_sync_panel_url(
            attrs.get("iha_panel_url")
            or attrs.get("iha_api_base_url")
            or attrs.get("panel_url")
            or saved.get("panelUrl")
            or saved.get("panel_url")
        )
        is_iha = bool(panel_url) or bool(saved.get("syncCapable") or saved.get("ihaPanel"))
        if isinstance(item, dict):
            is_iha = is_iha or _ha_climate_item_is_iha(item)

        current_preset = str(attrs.get("preset_mode") or "").strip().lower()
        is_away = current_preset == "away" or bool(attrs.get("target_temperature_locked_by_preset")) or bool(saved.get("away"))
        door_pause_active = bool(attrs.get("door_pause_active")) or bool(saved.get("doorPauseActive"))

        entity_target = target
        if target is not None:
            if is_away and preset != "arriving":
                skipped.append({"entityId": entity_id, "name": friendly, "reason": "away-target"})
                entity_target = None
            elif door_pause_active:
                skipped.append({"entityId": entity_id, "name": friendly, "reason": "door-pause-target"})
                entity_target = None

        has_entity_action = bool(preset or hvac_mode or entity_target is not None)
        if not has_entity_action:
            continue

        # New IHA entities use a real panel-to-panel request. A saved URL also lets
        # Sync continue during a transient HA outage after discovery has succeeded.
        if is_iha and panel_url:
            try:
                _call_direct_iha_peer(
                    panel_url,
                    mode=hvac_mode,
                    target_temp=entity_target,
                    preset_mode=preset,
                )
                successful.add(entity_id)
                direct_transports.append(entity_id)
                continue
            except Exception as exc:
                direct_failures[entity_id] = str(exc)

        # HA fallback requires the selected climate entity to exist in the current
        # state table. Do not report a stale/missing entity as successfully synced.
        if not isinstance(item, dict):
            detail = direct_failures.get(entity_id)
            if not detail:
                detail = "Selected climate entity was not found in Home Assistant"
                if ha_state_error:
                    detail += f" ({ha_state_error})"
            errors.append({"entityId": entity_id, "name": friendly, "action": "resolve", "error": detail})
            continue

        if preset:
            receiver_button = str(arriving_buttons_by_serial.get(serial) or "").strip() if serial else ""
            if preset == "arriving" and receiver_button.startswith("button."):
                arriving_button_by_climate[entity_id] = receiver_button
            else:
                arriving_preset_fallback_targets.append(entity_id)
        if hvac_mode:
            mode_targets.append(entity_id)
        if entity_target is not None:
            temp_targets.append(entity_id)

    def service_error(entity_list: list[str], action: str, exc: Exception) -> None:
        for entity_id in entity_list:
            direct_detail = direct_failures.get(entity_id)
            detail = str(exc)
            if direct_detail:
                detail = f"Direct delivery failed: {direct_detail}; Home Assistant fallback failed: {detail}"
            errors.append({"entityId": entity_id, "action": action, "error": detail})

    if preset == "arriving" and arriving_button_by_climate:
        climate_ids = sorted(arriving_button_by_climate)
        try:
            _ha_json_request(
                ha_url,
                token,
                "POST",
                "/api/services/button/press",
                {"entity_id": sorted(set(arriving_button_by_climate.values()))},
            )
            successful.update(climate_ids)
            fallback_transports.extend(climate_ids)
        except Exception as exc:
            service_error(climate_ids, "arriving", exc)

    if preset and arriving_preset_fallback_targets:
        try:
            _ha_json_request(
                ha_url,
                token,
                "POST",
                "/api/services/climate/set_preset_mode",
                {"entity_id": arriving_preset_fallback_targets, "preset_mode": preset},
            )
            successful.update(arriving_preset_fallback_targets)
            fallback_transports.extend(arriving_preset_fallback_targets)
        except Exception as exc:
            service_error(arriving_preset_fallback_targets, "preset", exc)

    if hvac_mode and mode_targets:
        try:
            _ha_json_request(
                ha_url,
                token,
                "POST",
                "/api/services/climate/set_hvac_mode",
                {"entity_id": mode_targets, "hvac_mode": hvac_mode},
            )
            successful.update(mode_targets)
            fallback_transports.extend(mode_targets)
        except Exception as exc:
            service_error(mode_targets, "mode", exc)

    if target is not None and temp_targets:
        try:
            _ha_json_request(
                ha_url,
                token,
                "POST",
                "/api/services/climate/set_temperature",
                {"entity_id": temp_targets, "temperature": target},
            )
            successful.update(temp_targets)
            fallback_transports.extend(temp_targets)
        except Exception as exc:
            service_error(temp_targets, "temperature", exc)

    if arriving_button_by_climate or arriving_preset_fallback_targets or mode_targets or temp_targets:
        _invalidate_ha_state_cache(ha_url, token)

    synced_ids = sorted(successful)
    return {
        "ok": not bool(errors),
        "synced": synced_ids,
        "skipped": skipped,
        "errors": errors,
        "count": len(synced_ids),
        "mode": hvac_mode,
        "targetTemp": target,
        "presetMode": preset,
        "direct": sorted(set(direct_transports)),
        "homeAssistantFallback": sorted(set(fallback_transports)),
    }


def _sync_panel_context() -> dict:
    record = _read_panel_config_record()
    config = record.get("config") if isinstance(record, dict) else {}
    config = config if isinstance(config, dict) else {}
    integrations = config.get("integrations") if isinstance(config.get("integrations"), dict) else {}
    ha = integrations.get("homeAssistant") if isinstance(integrations.get("homeAssistant"), dict) else {}
    peers = _normalize_sync_peer_entries(ha.get("syncThermostatEntities") or [])
    return {
        "url": str(ha.get("url") or "").strip(),
        "token": str(ha.get("token") or "").strip(),
        "peers": peers,
        "entityIds": [peer.get("entityId") for peer in peers if peer.get("entityId")],
    }


def _sync_status_payload() -> dict:
    global _SYNC_ARMED_UNTIL, _SYNC_ARM_SOURCE
    context = _sync_panel_context()
    now = time.monotonic()
    with _SYNC_ARM_LOCK:
        active = bool(context.get("entityIds")) and now < float(_SYNC_ARMED_UNTIL or 0.0)
        if not active:
            _SYNC_ARMED_UNTIL = 0.0
            _SYNC_ARM_SOURCE = ""
        remaining = max(0, int(round(_SYNC_ARMED_UNTIL - now))) if active else 0
        source = _SYNC_ARM_SOURCE if active else ""
        last_result = _deepcopy_json(_SYNC_LAST_RESULT) if isinstance(_SYNC_LAST_RESULT, dict) else {}
    return {
        "ok": True,
        "active": active,
        "armed": active,
        "remainingSeconds": remaining,
        "durationSeconds": int(round(SYNC_ARM_DURATION_SECONDS)),
        "source": source,
        "configuredCount": len(context.get("entityIds") or []),
        "lastResult": last_result,
    }


def _set_sync_arm(payload: dict | None = None) -> dict:
    global _SYNC_ARMED_UNTIL, _SYNC_ARM_SOURCE, _SYNC_LAST_REQUEST_SIGNATURE, _SYNC_LAST_REQUEST_AT
    request_payload = payload if isinstance(payload, dict) else {}
    context = _sync_panel_context()
    if not context.get("entityIds"):
        result = _sync_status_payload()
        result.update({"ok": False, "error": "No Sync thermostats are configured on this panel."})
        return result

    action = str(request_payload.get("action") or "arm").strip().lower()
    source = str(request_payload.get("source") or "home-assistant").strip().lower()[:40] or "home-assistant"
    try:
        duration = float(request_payload.get("durationSeconds", SYNC_ARM_DURATION_SECONDS) or SYNC_ARM_DURATION_SECONDS)
    except (TypeError, ValueError):
        duration = SYNC_ARM_DURATION_SECONDS
    duration = max(5.0, min(120.0, duration))

    with _SYNC_ARM_LOCK:
        currently_active = time.monotonic() < float(_SYNC_ARMED_UNTIL or 0.0)
        if action in {"off", "disable", "disarm", "cancel"} or (action == "toggle" and currently_active):
            _SYNC_ARMED_UNTIL = 0.0
            _SYNC_ARM_SOURCE = ""
            _SYNC_LAST_REQUEST_SIGNATURE = ""
            _SYNC_LAST_REQUEST_AT = 0.0
        else:
            if not currently_active:
                _SYNC_LAST_REQUEST_SIGNATURE = ""
                _SYNC_LAST_REQUEST_AT = 0.0
            _SYNC_ARMED_UNTIL = time.monotonic() + duration
            _SYNC_ARM_SOURCE = source
    return _sync_status_payload()


def _sync_changes_from_control_payload(payload: dict, accepted: dict | None = None) -> dict:
    incoming = payload.get("thermostat", payload) if isinstance(payload, dict) else {}
    if not isinstance(incoming, dict):
        return {}
    sources = {
        str(incoming.get(key) or "").strip().lower()
        for key in (
            "commandSource",
            "source",
            "modeChangeSource",
            "targetChangeSource",
            "presetChangeSource",
        )
    }
    if any(source in {"peer-sync", "sync-receiver", "thermostat-sync"} for source in sources):
        return {}

    changes: dict[str, object] = {}
    mode_raw = incoming.get("mode", incoming.get("hvac_mode", incoming.get("hvacMode")))
    mode = _sync_hvac_mode_for_ha(mode_raw)
    if mode in {"off", "heat", "cool"}:
        changes["mode"] = mode

    preset = _sync_preset_mode_for_ha(incoming.get("preset_mode", incoming.get("presetMode", incoming.get("preset"))))
    if not preset:
        override = incoming.get("presenceHomeOverride", incoming.get("presence_home_override"))
        if isinstance(override, dict) and str(override.get("reason") or "").strip().lower() == "arriving":
            preset = "arriving"
    if preset == "arriving":
        changes["presetMode"] = "arriving"

    target = None
    for key in ("targetTemp", "target_temperature", "temperature", "targetTemperature"):
        if key in incoming:
            target = _sync_target_temperature(incoming.get(key))
            break
    accepted_state = accepted if isinstance(accepted, dict) else {}
    if target is not None and not bool(accepted_state.get("away")):
        changes["targetTemp"] = target
    return changes


def _schedule_armed_thermostat_sync(changes: dict) -> dict:
    """Queue one peer Sync operation while the shared window is armed.

    Both the thermostat control endpoint and explicit UI/HA dispatch path observe
    the same command. Suppress the duplicate copy for a brief interval so every
    user action reaches each destination exactly once.
    """
    global _SYNC_LAST_RESULT, _SYNC_LAST_REQUEST_SIGNATURE, _SYNC_LAST_REQUEST_AT
    status = _sync_status_payload()
    if not isinstance(changes, dict) or not changes:
        return {"ok": False, "queued": False, "active": bool(status.get("active")), "error": "Nothing to sync"}
    if not status.get("active"):
        return {"ok": True, "queued": False, "active": False, "reason": "sync-not-armed"}

    payload: dict[str, object] = {}
    mode = _sync_hvac_mode_for_ha(changes.get("mode", changes.get("hvacMode", changes.get("hvac_mode"))))
    if mode:
        payload["mode"] = mode
    preset = _sync_preset_mode_for_ha(changes.get("presetMode", changes.get("preset_mode", changes.get("preset"))))
    if preset:
        payload["presetMode"] = preset
    target = None
    for key in ("targetTemp", "target_temperature", "temperature", "targetTemperature"):
        if key in changes:
            target = _sync_target_temperature(changes.get(key))
            break
    if target is not None:
        payload["targetTemp"] = target
    if not payload:
        return {"ok": False, "queued": False, "active": True, "error": "Nothing to sync"}

    context = _sync_panel_context()
    if not context.get("entityIds"):
        result = {
            "ok": False,
            "queued": False,
            "active": True,
            "error": "Sync is armed, but no peer thermostats are configured.",
            "at": int(time.time() * 1000),
        }
        with _SYNC_ARM_LOCK:
            _SYNC_LAST_RESULT = result
        return result

    # A direct panel URL can carry Sync through a temporary HA outage. If none of
    # the selected peers has one yet, HA credentials remain required for discovery
    # and the compatibility service fallback.
    peers = list(context.get("peers") or [])
    has_saved_direct_peer = any(_normalize_sync_panel_url(peer.get("panelUrl")) for peer in peers if isinstance(peer, dict))
    if (not context.get("url") or not context.get("token")) and not has_saved_direct_peer:
        result = {
            "ok": False,
            "queued": False,
            "active": True,
            "error": "Sync is armed, but Home Assistant connection details are missing.",
            "at": int(time.time() * 1000),
        }
        with _SYNC_ARM_LOCK:
            _SYNC_LAST_RESULT = result
        return result

    signature = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    now = time.monotonic()
    with _SYNC_ARM_LOCK:
        if signature == _SYNC_LAST_REQUEST_SIGNATURE and now - float(_SYNC_LAST_REQUEST_AT or 0.0) <= 1.5:
            return {
                "ok": True,
                "queued": False,
                "active": True,
                "reason": "duplicate-suppressed",
                "configuredCount": len(context.get("entityIds") or []),
                "requested": payload,
            }
        _SYNC_LAST_REQUEST_SIGNATURE = signature
        _SYNC_LAST_REQUEST_AT = now

    context_snapshot = {
        "url": str(context.get("url") or ""),
        "token": str(context.get("token") or ""),
        "entityIds": list(context.get("entityIds") or []),
        "peers": _deepcopy_json(peers),
    }

    def _worker() -> None:
        global _SYNC_LAST_RESULT
        try:
            result = _call_ha_sync_thermostat_services(
                context_snapshot["url"],
                context_snapshot["token"],
                context_snapshot["entityIds"],
                mode=payload.get("mode"),
                target_temp=payload.get("targetTemp"),
                preset_mode=payload.get("presetMode"),
                peer_metadata=context_snapshot["peers"],
            )
        except Exception as exc:  # noqa: BLE001 - sync must never block local HVAC control
            result = {"ok": False, "error": str(exc), "synced": [], "errors": [{"error": str(exc)}]}
        result = dict(result or {})
        result["at"] = int(time.time() * 1000)
        result["requested"] = dict(payload)
        with _SYNC_ARM_LOCK:
            _SYNC_LAST_RESULT = result

    threading.Thread(target=_worker, name="thermostat-peer-sync", daemon=True).start()
    return {
        "ok": True,
        "queued": True,
        "active": True,
        "configuredCount": len(context_snapshot["entityIds"]),
        "requested": payload,
    }


def _normalize_weather_item(item: dict) -> dict:
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))

    def optional_number(raw: object, minimum: float, maximum: float) -> float | None:
        if raw is None or raw == "":
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return min(max(value, minimum), maximum)

    temperature = optional_number(attrs.get("temperature"), -100, 180)
    wind_speed = optional_number(attrs.get("wind_speed"), 0, 250)
    temp_unit = str(attrs.get("temperature_unit") or attrs.get("unit_of_measurement") or "°F").strip() or "°F"
    wind_unit = str(attrs.get("wind_speed_unit") or "mph").strip() or "mph"

    # The wall panel and thermostat logic are Fahrenheit based. HA weather.home
    # normally follows the HA unit system, but convert if a Celsius weather entity
    # is ever selected so auto-switch targets stay consistent.
    if temperature is not None and temp_unit.lower() in {"°c", "c", "celsius"}:
        temperature = (temperature * 9 / 5) + 32
        temp_unit = "°F"

    return {
        "entityId": entity_id,
        "name": attrs.get("friendly_name") or entity_id,
        "state": item.get("state") or "unknown",
        "temperature": temperature,
        "temperatureUnit": temp_unit,
        "windSpeed": wind_speed,
        "windSpeedUnit": wind_unit,
        "humidity": attrs.get("humidity"),
        "pressure": attrs.get("pressure"),
        "pressureUnit": attrs.get("pressure_unit"),
        "windBearing": attrs.get("wind_bearing"),
        "lastChanged": item.get("last_changed") or "",
        "lastUpdated": item.get("last_updated") or "",
    }


def _fetch_ha_weather_state(ha_url: str, token: str, entity_id: str = "weather.home") -> dict:
    entity_id = (entity_id or "weather.home").strip() or "weather.home"
    if not entity_id.startswith("weather."):
        raise ValueError("Entity must be a weather.* entity")
    item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
    if not isinstance(item, dict):
        raise RuntimeError("Home Assistant did not return a weather state")
    return _normalize_weather_item(item)


def _audio_control_entity_id(controls: dict, kind: str) -> str:
    value = (controls or {}).get(kind)
    if kind == "music_surround" and not value:
        value = (controls or {}).get("musicSurround")
    if isinstance(value, dict):
        return str(value.get("entityId") or value.get("entity_id") or "").strip()
    return str(value or "").strip()


def _fetch_ha_switch_control_states(ha_url: str, token: str, controls: dict) -> dict:
    kinds = ("subwoofer", "surround", "projector")
    entity_by_kind = {kind: _audio_control_entity_id(controls, kind) for kind in kinds}
    wanted = [
        entity_id for entity_id in entity_by_kind.values()
        if entity_id.startswith("switch.") or entity_id.startswith("input_boolean.")
    ]
    items = _fetch_ha_state_items_for_entities(ha_url, token, wanted, {"switch", "input_boolean"}, all_states_threshold=2)
    by_id = {str(item.get("entity_id", "")): item for item in items}
    refreshed: dict[str, dict | None] = {}
    for kind in kinds:
        entity_id = entity_by_kind[kind]
        item = by_id.get(entity_id)
        refreshed[kind] = _normalize_generic_entity(item) if item else None
    return refreshed


def _call_switch_service(ha_url: str, token: str, entity_id: str, action: str) -> dict:
    entity_id = (entity_id or "").strip()
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    if domain not in {"switch", "input_boolean"}:
        raise ValueError("Entity must be a switch.* or input_boolean.* entity")
    action = (action or "toggle").strip().lower()
    service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}.get(action)
    if not service:
        raise ValueError("Unsupported switch action")
    _ha_json_request(ha_url, token, "POST", f"/api/services/{domain}/{service}", {"entity_id": entity_id})
    _invalidate_ha_state_cache(ha_url, token)
    try:
        item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
        return _normalize_generic_entity(item)
    except Exception:
        return {"entityId": entity_id, "name": entity_id, "domain": domain, "state": action}


def _room_control_domain(entity_id: str) -> str:
    entity_id = (entity_id or "").strip()
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    if not domain or not entity_id or "." not in entity_id:
        raise ValueError("Entity ID must include a Home Assistant domain, for example switch.kitchen")
    return domain


def _fetch_ha_room_control_states_for_entities(ha_url: str, token: str, entity_ids: list[str]) -> list[dict]:
    wanted: list[str] = []
    seen: set[str] = set()
    for raw in entity_ids or []:
        entity_id = str(raw or "").strip()
        try:
            _room_control_domain(entity_id)
        except ValueError:
            continue
        if entity_id in seen:
            continue
        seen.add(entity_id)
        wanted.append(entity_id)

    items = _fetch_ha_state_items_for_entities(ha_url, token, wanted, None, all_states_threshold=3)
    controls = [_normalize_generic_entity(item) for item in items]
    by_id = {item["entityId"]: item for item in controls}
    return [by_id[entity_id] for entity_id in wanted if entity_id in by_id]


def _room_control_service_for_action(domain: str, action: str) -> tuple[str, str] | None:
    domain = (domain or "").strip().lower()
    action = (action or "toggle").strip().lower()

    turn_domains = {
        "switch", "input_boolean", "light", "fan", "automation", "humidifier",
        "remote", "siren", "water_heater", "climate",
    }
    read_only_domains = {
        "binary_sensor", "sensor", "number", "input_number", "select", "input_select",
        "person", "device_tracker", "sun", "weather", "calendar", "alarm_control_panel",
    }

    if domain in read_only_domains:
        return None

    if domain in turn_domains:
        service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}.get(action)
        if service:
            return domain, service
        return None

    if domain == "cover":
        service = {
            "open": "open_cover",
            "on": "open_cover",
            "close": "close_cover",
            "off": "close_cover",
            "stop": "stop_cover",
            "toggle": "toggle",
            "position": "set_cover_position",
            "set_position": "set_cover_position",
        }.get(action)
        return (domain, service) if service else None

    if domain == "lock":
        service = {
            "lock": "lock",
            "off": "lock",
            "unlock": "unlock",
            "on": "unlock",
            "open": "open",
        }.get(action)
        return (domain, service) if service else None

    if domain in {"button", "input_button"}:
        return domain, "press"

    if domain in {"scene", "script"}:
        return domain, "turn_on"

    if domain == "media_player":
        service = {
            "play_pause": "media_play_pause",
            "toggle": "media_play_pause",
            "on": "turn_on",
            "off": "turn_off",
        }.get(action)
        return (domain, service) if service else None

    if domain == "vacuum":
        service = {
            "start": "start",
            "on": "start",
            "return_to_base": "return_to_base",
            "off": "return_to_base",
            "stop": "stop",
        }.get(action)
        return (domain, service) if service else None

    # Best effort for HA domains that expose the common turn_on/turn_off pair.
    service = {"on": "turn_on", "off": "turn_off", "toggle": "toggle", "run": "turn_on", "press": "press"}.get(action)
    return (domain, service) if service else None


def _call_room_control_service(ha_url: str, token: str, entity_id: str, action: str, code: str | None = None, position: int | float | str | None = None) -> dict:
    entity_id = (entity_id or "").strip()
    domain = _room_control_domain(entity_id)
    action = (action or "toggle").strip().lower()
    spec = _room_control_service_for_action(domain, action)
    if spec is None:
        item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
        if isinstance(item, dict):
            control = _normalize_generic_entity(item)
            control["actionApplied"] = False
            return control
        return {"entityId": entity_id, "name": entity_id, "domain": domain, "state": "unknown", "actionApplied": False}

    service_domain, service = spec
    service_payload = {"entity_id": entity_id}
    code_value = str(code or "").strip()
    if code_value and service_domain == "lock":
        service_payload["code"] = code_value
    position_value: int | None = None
    if service_domain == "cover" and service == "set_cover_position":
        try:
            position_value = max(0, min(100, int(round(float(position)))))
        except (TypeError, ValueError):
            raise ValueError("Missing cover position")
        service_payload["position"] = position_value
    _ha_json_request(ha_url, token, "POST", f"/api/services/{service_domain}/{service}", service_payload)
    _invalidate_ha_state_cache(ha_url, token)
    try:
        item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
        if isinstance(item, dict):
            control = _normalize_generic_entity(item)
            if position_value is not None:
                control["currentPosition"] = position_value
                control["state"] = "open" if position_value > 0 else "closed"
            control["actionApplied"] = True
            return control
    except Exception:
        pass

    optimistic = {
        "on": "on",
        "open": "open",
        "unlock": "unlocked",
        "lock": "locked",
        "off": "off",
        "close": "closed",
        "press": "on",
        "run": "on",
        "start": "cleaning",
        "return_to_base": "returning",
        "position": "open" if (position_value or 0) > 0 else "closed",
        "set_position": "open" if (position_value or 0) > 0 else "closed",
    }.get(action, "on")
    payload = {"entityId": entity_id, "name": entity_id, "domain": domain, "state": optimistic, "actionApplied": True}
    if position_value is not None:
        payload["currentPosition"] = position_value
    return payload



def _rgb_to_hex(rgb_value) -> str:
    if not isinstance(rgb_value, (list, tuple)) or len(rgb_value) < 3:
        return "#ffd76f"
    try:
        parts = [max(0, min(255, int(round(float(part))))) for part in rgb_value[:3]]
    except (TypeError, ValueError):
        return "#ffd76f"
    return "#" + "".join(f"{part:02x}" for part in parts)


def _hex_to_rgb(color_value: str | None) -> list[int] | None:
    raw = str(color_value or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(part * 2 for part in raw)
    if len(raw) != 6:
        return None
    try:
        return [int(raw[idx:idx + 2], 16) for idx in (0, 2, 4)]
    except ValueError:
        return None


def _light_supports_color(color_modes) -> bool:
    color_mode_set = {str(mode or "").lower() for mode in (color_modes or [])}
    return bool(color_mode_set.intersection({"hs", "rgb", "rgbw", "rgbww", "xy"}))


def _normalize_light_item(item: dict) -> dict:
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    raw_brightness = attrs.get("brightness")
    try:
        brightness_pct = int(round((float(raw_brightness) / 255.0) * 100)) if raw_brightness is not None else 0
    except (TypeError, ValueError):
        brightness_pct = 0
    brightness_pct = max(0, min(100, brightness_pct))
    state = item.get("state") or "unknown"
    if str(state).lower() != "on":
        brightness_pct = 0

    supported_color_modes = attrs.get("supported_color_modes") or []
    rgb_color = attrs.get("rgb_color")
    hs_color = attrs.get("hs_color")
    color_hex = _rgb_to_hex(rgb_color)
    if color_hex == "#ffd76f" and isinstance(hs_color, (list, tuple)) and len(hs_color) >= 2:
        try:
            import colorsys
            hue = max(0.0, min(360.0, float(hs_color[0]))) / 360.0
            saturation = max(0.0, min(100.0, float(hs_color[1]))) / 100.0
            red, green, blue = colorsys.hsv_to_rgb(hue, saturation, 1.0)
            color_hex = _rgb_to_hex([red * 255, green * 255, blue * 255])
        except Exception:
            color_hex = "#ffd76f"

    return {
        "entityId": entity_id,
        "domain": "light",
        "name": attrs.get("friendly_name") or entity_id,
        "state": state,
        "brightnessPct": brightness_pct,
        "supportedColorModes": supported_color_modes,
        "colorMode": attrs.get("color_mode") or "",
        "colorSupported": _light_supports_color(supported_color_modes) or bool(rgb_color or hs_color),
        "colorHex": color_hex,
    }


def _fetch_ha_light_state(ha_url: str, token: str, entity_id: str) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("light."):
        raise ValueError("Entity must be a light.* entity")
    item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
    return _normalize_light_item(item)


def _fetch_ha_light_states_for_entities(ha_url: str, token: str, entity_ids: list[str], fresh: bool = False) -> list[dict]:
    wanted = _ordered_unique_entity_ids(entity_ids, {"light"})
    if len(wanted) == 1 and not fresh:
        return [_fetch_ha_light_state(ha_url, token, wanted[0])]
    items = _fetch_ha_state_items_for_entities(ha_url, token, wanted, {"light"}, all_states_threshold=2, use_cache=not fresh)
    lights = [_normalize_light_item(item) for item in items]
    by_id = {item["entityId"]: item for item in lights}
    return [by_id[entity_id] for entity_id in wanted if entity_id in by_id]


def _call_light_service(ha_url: str, token: str, entity_id: str, action: str, brightness: int | float | None = None, color: str | None = None, transition: int | float | None = None, refresh: bool = True) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("light."):
        raise ValueError("Entity must be a light.* entity")
    action = (action or "on").strip().lower()
    if action not in {"on", "off", "toggle", "brightness", "color"}:
        raise ValueError("Unsupported light action")

    if action == "off":
        payload = {"entity_id": entity_id}
        try:
            if transition is not None:
                payload["transition"] = max(0.0, min(5.0, float(transition)))
        except (TypeError, ValueError):
            pass
        _ha_json_request(ha_url, token, "POST", "/api/services/light/turn_off", payload)
    elif action == "toggle":
        _ha_json_request(ha_url, token, "POST", "/api/services/light/toggle", {"entity_id": entity_id})
    else:
        payload = {"entity_id": entity_id}
        if brightness is not None:
            try:
                payload["brightness_pct"] = max(0, min(100, int(round(float(brightness)))))
            except (TypeError, ValueError):
                pass
        try:
            if transition is not None:
                payload["transition"] = max(0.0, min(5.0, float(transition)))
        except (TypeError, ValueError):
            pass
        rgb_color = _hex_to_rgb(color)
        if action == "color" and rgb_color:
            payload["rgb_color"] = rgb_color
        _ha_json_request(ha_url, token, "POST", "/api/services/light/turn_on", payload)

    _invalidate_ha_state_cache(ha_url, token)

    def fallback_state() -> dict:
        fallback_brightness = 0
        if action != "off":
            try:
                fallback_brightness = max(0, min(100, int(round(float(brightness if brightness is not None else 100)))))
            except (TypeError, ValueError):
                fallback_brightness = 100
        return {
            "entityId": entity_id,
            "domain": "light",
            "name": entity_id,
            "state": "off" if action == "off" else "on",
            "brightnessPct": fallback_brightness,
            "colorSupported": bool(_hex_to_rgb(color)),
            "colorHex": f"#{str(color or '').strip().lstrip('#').lower()}" if _hex_to_rgb(color) else "#ffd76f",
        }

    if not refresh:
        return fallback_state()

    try:
        return _fetch_ha_light_state(ha_url, token, entity_id)
    except Exception:
        return fallback_state()


def _normalize_number_control(item: dict, kind: str | None = None) -> dict:
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    raw_state = item.get("state")
    try:
        value = float(raw_state)
    except (TypeError, ValueError):
        value = None

    def num_attr(name: str, fallback: float | None = None) -> float | None:
        raw = attrs.get(name, fallback)
        try:
            return float(raw) if raw is not None and raw != "" else fallback
        except (TypeError, ValueError):
            return fallback

    return {
        "kind": kind or "",
        "entityId": entity_id,
        "name": attrs.get("friendly_name") or entity_id,
        "state": raw_state,
        "value": value,
        "min": num_attr("min", -10),
        "max": num_attr("max", 10),
        "step": num_attr("step", 1),
        "unit": attrs.get("unit_of_measurement") or "",
        "mode": attrs.get("mode") or "",
    }


def _compact_key(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _score_audio_control(item: dict, kind: str, player_entity_id: str, player_name: str) -> int:
    """Score a number.* tone control against the selected media player.

    Generic words such as ``sonos`` or ``connect`` are deliberately excluded
    from the identity match. A house can contain several Sonos products, and
    treating the brand as a strong match can assign Living Room Bass to a
    Bedroom player. Room/device-specific tokens are what make a candidate safe.
    """
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    friendly = str(attrs.get("friendly_name") or "")
    haystack = f"{entity_id} {friendly}".lower()
    compact = _compact_key(haystack)

    kind_terms = {
        "bass": ["bass"],
        "treble": ["treble"],
        "gain": ["gain", "subwoofergain", "subgain"],
        "music_surround": ["musicsurround", "surroundmusic", "surroundlevel", "surroundvolume", "musicvolume"],
    }
    if not any(term in compact for term in kind_terms.get(kind, [kind])):
        return -1

    generic_tokens = {
        "media", "player", "audio", "speaker", "sound", "sonos", "connect",
        "amp", "port", "zone", "room", "home", "number", "level", "control",
        "bass", "treble", "gain", "sub", "subwoofer", "surround", "music",
    }

    def identity_tokens(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
            if len(token) > 2 and token not in generic_tokens
        }

    player_slug = player_entity_id.split(".", 1)[-1]
    player_compact = _compact_key(player_slug)
    player_tokens = identity_tokens(player_slug) | identity_tokens(player_name)
    candidate_tokens = identity_tokens(entity_id) | identity_tokens(friendly)
    shared_tokens = player_tokens & candidate_tokens

    score = 5
    if player_compact and player_compact in compact:
        score += 30
    score += 12 * len(shared_tokens)
    if kind == "gain" and "subwoofer" in compact:
        score += 10
    return score


def _fetch_ha_audio_controls(ha_url: str, token: str, media_player_id: str, media_player_name: str = "") -> dict:
    """Find HA number.* entities that represent tone controls for a media player.

    Sonos exposes bass/treble/subwoofer-gain as number entities in Home Assistant,
    not as generic media_player services. This lookup is done when selecting or
    loading an audio device, not on every UI tick.
    """
    media_player_id = (media_player_id or "").strip()
    if not media_player_id.startswith("media_player."):
        raise ValueError("Entity must be a media_player.* entity")

    states = _ha_json_request(ha_url, token, "GET", "/api/states")
    if not isinstance(states, list):
        return {"gain": None, "bass": None, "treble": None, "music_surround": None}

    controls: dict[str, dict | None] = {"gain": None, "bass": None, "treble": None, "music_surround": None}
    for kind in list(controls.keys()):
        best = None
        best_score = -1
        for item in states:
            entity_id = str(item.get("entity_id", ""))
            if not entity_id.startswith("number."):
                continue
            score = _score_audio_control(item, kind, media_player_id, media_player_name)
            if score > best_score:
                best = item
                best_score = score
        # Require at least one device/room identity match. The base kind score
        # is only 5, so unrelated tone controls from another Sonos zone cannot
        # be selected merely because they share the same brand.
        if best is not None and best_score >= 12:
            controls[kind] = _normalize_number_control(best, kind)
    return controls


def _fetch_ha_audio_control_states(ha_url: str, token: str, controls: dict) -> dict:
    kinds = ("gain", "bass", "treble", "music_surround")
    entity_by_kind = {kind: _audio_control_entity_id(controls, kind) for kind in kinds}
    wanted = [entity_id for entity_id in entity_by_kind.values() if entity_id.startswith("number.")]
    items = _fetch_ha_state_items_for_entities(ha_url, token, wanted, {"number"}, all_states_threshold=2)
    by_id = {str(item.get("entity_id", "")): item for item in items}
    refreshed: dict[str, dict | None] = {}
    for kind in kinds:
        entity_id = entity_by_kind[kind]
        item = by_id.get(entity_id)
        refreshed[kind] = _normalize_number_control(item, kind) if item else None
    return refreshed


def _call_number_service(ha_url: str, token: str, entity_id: str, value: int | float) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("number."):
        raise ValueError("Entity must be a number.* entity")
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Missing or invalid numeric value") from exc

    _ha_json_request(ha_url, token, "POST", "/api/services/number/set_value", {
        "entity_id": entity_id,
        "value": numeric_value,
    })
    _invalidate_ha_state_cache(ha_url, token)
    try:
        item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
        return _normalize_number_control(item)
    except Exception:
        return {"entityId": entity_id, "name": entity_id, "value": numeric_value, "state": str(numeric_value)}

def _call_media_service(ha_url: str, token: str, entity_id: str, action: str, value: int | float | None = None) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("media_player."):
        raise ValueError("Entity must be a media_player.* entity")

    service_by_action = {
        "play_pause": "media_play_pause",
        "play": "media_play",
        "pause": "media_pause",
        "previous": "media_previous_track",
        "next": "media_next_track",
        "volume": "volume_set",
        "volume_up": "volume_up",
        "volume_down": "volume_down",
        "source": "select_source",
    }
    service = service_by_action.get(action)
    if not service:
        raise ValueError("Unsupported media action")

    payload = {"entity_id": entity_id}
    if action == "volume":
        if value is None:
            raise ValueError("Missing volume value")
        raw_volume = float(value)
        # Accept either the native panel's percent values (0-100) or older
        # fractional values (0.0-1.0) so volume actions stay backward compatible.
        payload["volume_level"] = max(0.0, min(1.0, raw_volume if raw_volume <= 1 else raw_volume / 100.0))

    if action == "source":
        source = str(value or "").strip()
        if not source:
            raise ValueError("Missing source value")
        payload["source"] = source

    _ha_json_request(ha_url, token, "POST", f"/api/services/media_player/{service}", payload)
    _invalidate_ha_state_cache(ha_url, token)
    try:
        return _fetch_ha_media_state(ha_url, token, entity_id)
    except Exception:
        return {"entityId": entity_id, "name": entity_id, "state": action}


def _fetch_ha_state(ha_url: str, token: str, entity_id: str) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("cover."):
        raise ValueError("Entity must be a cover.* entity")
    item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
    attrs = item.get("attributes") or {}
    return {
        "entityId": item.get("entity_id") or entity_id,
        "name": attrs.get("friendly_name") or item.get("entity_id") or entity_id,
        "state": item.get("state") or "unknown",
        "currentPosition": attrs.get("current_position"),
        "currentTiltPosition": attrs.get("current_tilt_position"),
        "supportedFeatures": attrs.get("supported_features"),
    }


def _call_cover_service(ha_url: str, token: str, entity_id: str, action: str, position: int | None = None) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("cover."):
        raise ValueError("Entity must be a cover.* entity")

    action = (action or "").strip().lower()
    service_by_action = {
        "open": "open_cover",
        "close": "close_cover",
        "stop": "stop_cover",
        "position": "set_cover_position",
        "open_tilt": "open_cover_tilt",
        "close_tilt": "close_cover_tilt",
        "stop_tilt": "stop_cover_tilt",
        "tilt": "set_cover_tilt_position",
        "set_tilt_position": "set_cover_tilt_position",
    }
    service = service_by_action.get(action)
    if not service:
        raise ValueError("Unsupported cover action")

    payload = {"entity_id": entity_id}
    position_value = None
    if action in {"position", "tilt", "set_tilt_position"}:
        if position is None:
            raise ValueError("Missing position")
        position_value = max(0, min(100, int(position)))
        if action == "position":
            payload["position"] = position_value
        else:
            payload["tilt_position"] = position_value

    _ha_json_request(ha_url, token, "POST", f"/api/services/cover/{service}", payload)
    _invalidate_ha_state_cache(ha_url, token)
    try:
        state = _fetch_ha_state(ha_url, token, entity_id)
    except Exception:
        # Some cover integrations update state slowly. Return an optimistic value so the UI changes immediately.
        optimistic = 100 if action in {"open", "open_tilt"} else 0 if action in {"close", "close_tilt"} else position_value
        state = {"entityId": entity_id, "name": entity_id, "state": action, "currentPosition": optimistic}
        if action in {"open_tilt", "close_tilt", "tilt", "set_tilt_position"}:
            state["currentTiltPosition"] = optimistic
    return state


class SmartThermostatHandler(BaseHTTPRequestHandler):
    server_version = "SmartThermostatServer/0.1"

    def log_message(self, fmt: str, *args) -> None:  # token-safe basic logs
        if ACCESS_LOGS_ENABLED:
            print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/health":
            return _json(self, 200, {"ok": True})
        if path == "/api/system/info":
            server_port = getattr(self.server, "server_address", (None, None))[1]
            return _json(self, 200, _system_info_payload(server_port))
        if path == "/api/system/update-status":
            refresh = str((query.get("refresh") or [""])[0]).strip().lower() in {"1", "true", "yes", "on"}
            return _json(self, 200, _update_status_payload(refresh_remote=refresh))
        if path == "/":
            if not _config_web_portal_active(touch=True):
                self.send_error(404, "Not found")
                return
            server_port = getattr(self.server, "server_address", (None, None))[1]
            return _send_html(self, _config_transfer_html(server_port))
        if path in {"/config-transfer", "/config", "/config-backup"}:
            if not _config_web_portal_active(touch=True):
                self.send_error(404, "Not found")
                return
            server_port = getattr(self.server, "server_address", (None, None))[1]
            return _send_html(self, _config_transfer_html(server_port))
        if path == "/api/system/config-export":
            if not _config_web_portal_active(touch=True):
                self.send_error(403, "Config backup portal is closed")
                return
            server_port = getattr(self.server, "server_address", (None, None))[1]
            return _send_json_download(self, _config_backup_filename(), _config_export_payload(server_port))
        if path == "/api/hardware/status":
            return _json(self, 200, _hardware_status_payload(force_i2c=True))
        if path == "/api/hardware/telemetry":
            return _json(self, 200, _hardware_telemetry_payload())
        if path == "/api/hardware/motion":
            # GET is strictly read-only. Configuration is applied at startup and
            # only changed by an explicit POST from the panel or Home Assistant.
            return _json(self, 200, _motion_status_payload(apply_config=False))
        if path == "/api/history":
            requested_date = (query.get("date") or [None])[0]
            return _json(self, 200, _hvac_history_payload(requested_date))
        if path == "/api/history/dates":
            return _json(self, 200, _hvac_history_dates_payload())
        if path == "/api/config":
            return _json(self, 200, _panel_config_payload())
        if path == "/api/thermostat/status":
            return _json(self, 200, _thermostat_status_payload(refresh_runtime=False, apply_hardware=False))
        if path == "/api/sync/status":
            return _json(self, 200, _sync_status_payload())
        if path == "/api/discovery":
            return _json(self, 200, _discovery_payload())
        if path == "/api/assistant/status":
            if not _assistant_client_access_allowed(self.client_address[0] if self.client_address else ""):
                return _json(self, 403, {"ok": False, "error": "Assistant status is local-only unless the temporary config portal is open."})
            include_config = str((query.get("include_config") or [""])[0]).strip().lower() in {"1", "true", "yes", "on"}
            return _json(self, 200, _assistant_status_payload(include_config=include_config))
        if path == "/api/settings/web":
            if not _config_web_portal_active(touch=True):
                return _json(self, 403, {"ok": False, "error": "The temporary config portal is closed."})
            return _json(self, 200, _config_settings_payload())
        if path == "/api/assistant/config":
            if not _config_web_portal_active(touch=True):
                return _json(self, 403, {"ok": False, "error": "The temporary config portal is closed."})
            return _json(self, 200, {"ok": True, "assistant": _assistant_config_payload()})

        file_path = _safe_join_public(path)
        if not file_path or not file_path.exists() or not file_path.is_file():
            self.send_error(404, "Not found")
            return

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(data)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/config", "/api/thermostat/status", "/api/thermostat/control", "/api/system/fetch-update", "/api/system/reboot", "/api/system/config-web-portal", "/api/system/config-web-portal/close", "/api/system/config-export-usb", "/api/system/config-import", "/api/system/config-import-usb", "/api/hardware/relay", "/api/hardware/rgb", "/api/hardware/release", "/api/hardware/motion", "/api/ha/covers", "/api/ha/cover/action", "/api/ha/cover/states", "/api/ha/entities", "/api/ha/weather/state", "/api/ha/media_players", "/api/ha/media/action", "/api/ha/media/states", "/api/ha/audio/controls", "/api/ha/audio/control_states", "/api/ha/audio/control/action", "/api/ha/audio/switch_states", "/api/ha/audio/switch/action", "/api/ha/alarm/states", "/api/ha/alarm/action", "/api/ha/binary_sensor/states", "/api/ha/light/states", "/api/ha/light/action", "/api/ha/room/states", "/api/ha/room/action", "/api/sync/thermostats", "/api/sync/apply", "/api/sync/dispatch", "/api/sync/arm", "/api/assistant/process", "/api/assistant/config", "/api/settings/web"}:
            self.send_error(404, "Not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")

            if path == "/api/assistant/process":
                if not _assistant_client_access_allowed(self.client_address[0] if self.client_address else ""):
                    return _json(self, 403, {"ok": False, "error": "Assistant commands are local-only unless the temporary config portal is open."})
                result = _assistant_process_payload(payload)
                status = 200 if result.get("ok") else (409 if result.get("busy") else 400)
                return _json(self, status, result)

            if path == "/api/settings/web":
                try:
                    result = _config_settings_save(payload)
                except ValueError as exc:
                    result = {"ok": False, "error": str(exc)}
                return _json(self, 200 if result.get("ok") else 400, result)

            if path == "/api/assistant/config":
                try:
                    result = _assistant_update_config(payload)
                except ValueError as exc:
                    result = {"ok": False, "error": str(exc)}
                return _json(self, 200 if result.get("ok") else 400, result)

            if path == "/api/config":
                config_payload = payload.get("config", payload) if isinstance(payload, dict) else {}
                if not isinstance(config_payload, dict):
                    return _json(self, 400, {"ok": False, "error": "config must be an object"})
                record = _write_panel_config_record(config_payload)
                return _json(self, 200, {"ok": True, "version": record["version"], "updatedAt": record["updatedAt"], "config": record["config"]})

            if path == "/api/system/fetch-update":
                result = _fetch_update_payload()
                return _json(self, 200 if result.get("ok") else 500, result)

            if path == "/api/system/reboot":
                result = _reboot_payload()
                return _json(self, 200 if result.get("ok") else 500, result)

            if path == "/api/system/config-web-portal":
                server_port = getattr(self.server, "server_address", (None, None))[1]
                result = _config_web_portal_payload(server_port)
                return _json(self, 200 if result.get("ok") else 500, result)

            if path == "/api/system/config-web-portal/close":
                result = _config_web_portal_stop_payload()
                return _json(self, 200 if result.get("ok") else 500, result)

            if path == "/api/system/config-export-usb":
                server_port = getattr(self.server, "server_address", (None, None))[1]
                result = _config_export_usb_payload(server_port)
                return _json(self, 200 if result.get("ok") else 400, result)

            if path == "/api/system/config-import":
                if not _config_web_portal_active(touch=True):
                    return _json(self, 403, {"ok": False, "error": "Config backup portal is closed. Open Backup Config again on the thermostat panel."})
                result = _config_import_payload(payload)
                return _json(self, 200 if result.get("ok") else 400, result)

            if path == "/api/system/config-import-usb":
                result = _config_import_usb_payload()
                return _json(self, 200 if result.get("ok") else 400, result)

            if path == "/api/hardware/relay":
                return _json(self, 200, _set_manual_relay(payload.get("relay", ""), bool(payload.get("on"))))

            if path == "/api/hardware/rgb":
                return _json(self, 200, _set_rgb_hardware(bool(payload.get("on")), payload.get("color")))

            if path == "/api/hardware/release":
                return _json(self, 200, _release_manual_hardware())

            if path == "/api/hardware/motion":
                return _json(self, 200, _set_motion_hardware(payload))

            if path == "/api/thermostat/status":
                return _json(self, 200, _thermostat_status_payload(refresh_runtime=False, apply_hardware=False))

            if path == "/api/thermostat/control":
                return _json(self, 200, _handle_thermostat_update(payload))

            if path == "/api/sync/arm":
                result = _set_sync_arm(payload)
                return _json(self, 200 if result.get("ok") else 400, result)

            if path == "/api/ha/covers":
                covers = _fetch_ha_covers(payload.get("url", ""), payload.get("token", ""))
                return _json(self, 200, {"ok": True, "covers": covers, "count": len(covers)})

            if path == "/api/ha/entities":
                entities = _fetch_ha_entities(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("domains", []),
                )
                return _json(self, 200, {"ok": True, "entities": entities, "count": len(entities)})

            if path == "/api/sync/thermostats":
                peers = _fetch_ha_sync_thermostats(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("selected", payload.get("selectedEntities", [])),
                    str((_read_thermostat_record().get("thermostat") or {}).get("name") or ""),
                )
                return _json(self, 200, {"ok": True, "thermostats": peers, "count": len(peers)})

            if path == "/api/sync/dispatch":
                # The wall screen can be tapped again immediately after Sync. If
                # its arm request and command arrive on separate server threads,
                # make the command deterministic by arming here only when needed.
                if bool(payload.get("ensureArmed")) and not _sync_status_payload().get("active"):
                    arm_result = _set_sync_arm({
                        "action": "arm",
                        "durationSeconds": payload.get("durationSeconds", SYNC_ARM_DURATION_SECONDS),
                        "source": payload.get("source", "touchscreen"),
                    })
                    if not arm_result.get("ok"):
                        return _json(self, 400, arm_result)
                raw_changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else payload
                changes = _sync_changes_from_control_payload(raw_changes, {})
                result = _schedule_armed_thermostat_sync(changes)
                return _json(self, 200 if result.get("ok") else 400, result)

            if path == "/api/sync/apply":
                result = _call_ha_sync_thermostat_services(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityIds", []),
                    mode=payload.get("mode", payload.get("hvacMode", payload.get("hvac_mode"))),
                    target_temp=payload.get("targetTemp", payload.get("target_temperature", payload.get("temperature"))),
                    preset_mode=payload.get("presetMode", payload.get("preset_mode", payload.get("preset"))),
                    peer_metadata=payload.get("peers") if isinstance(payload.get("peers"), list) else None,
                )
                return _json(self, 200 if result.get("ok") else 500, result)

            if path == "/api/ha/weather/state":
                weather = _fetch_ha_weather_state(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityId", "weather.home"),
                )
                return _json(self, 200, {"ok": True, "weather": weather})

            if path == "/api/ha/alarm/states":
                alarms = _fetch_ha_alarm_states_for_entities(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityIds", []),
                )
                return _json(self, 200, {"ok": True, "alarms": alarms, "count": len(alarms)})

            if path == "/api/ha/alarm/action":
                alarm = _call_alarm_service(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityId", ""),
                    payload.get("action", ""),
                    payload.get("code", ""),
                    payload.get("confirmation"),
                    payload.get("source", ""),
                )
                return _json(self, 200, {"ok": True, "alarm": alarm})

            if path == "/api/ha/binary_sensor/states":
                sensors = _fetch_ha_binary_sensor_states_for_entities(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityIds", []),
                )
                return _json(self, 200, {"ok": True, "sensors": sensors, "count": len(sensors)})

            if path == "/api/ha/light/states":
                lights = _fetch_ha_light_states_for_entities(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityIds", []),
                    bool(payload.get("fresh", False)),
                )
                return _json(self, 200, {"ok": True, "lights": lights, "count": len(lights)})

            if path == "/api/ha/light/action":
                light = _call_light_service(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityId", ""),
                    payload.get("action", "on"),
                    payload.get("brightness"),
                    payload.get("color"),
                    payload.get("transition"),
                    bool(payload.get("refresh", True)),
                )
                return _json(self, 200, {"ok": True, "light": light})

            if path == "/api/ha/room/states":
                controls = _fetch_ha_room_control_states_for_entities(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityIds", []),
                )
                return _json(self, 200, {"ok": True, "controls": controls, "count": len(controls)})

            if path == "/api/ha/room/action":
                control = _call_room_control_service(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityId", ""),
                    payload.get("action", "toggle"),
                    payload.get("code", ""),
                    payload.get("position"),
                )
                return _json(self, 200, {"ok": True, "control": control})

            if path == "/api/ha/cover/states":
                covers = _fetch_ha_cover_states_for_entities(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityIds", []),
                )
                return _json(self, 200, {"ok": True, "covers": covers, "count": len(covers)})

            if path == "/api/ha/media_players":
                players = _fetch_ha_media_players(payload.get("url", ""), payload.get("token", ""))
                return _json(self, 200, {"ok": True, "players": players, "count": len(players)})

            if path == "/api/ha/media/states":
                players = _fetch_ha_media_states_for_entities(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityIds", []),
                )
                return _json(self, 200, {"ok": True, "players": players, "count": len(players)})

            if path == "/api/ha/audio/controls":
                controls = _fetch_ha_audio_controls(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("mediaPlayerId", ""),
                    payload.get("mediaPlayerName", ""),
                )
                return _json(self, 200, {"ok": True, "controls": controls})

            if path == "/api/ha/audio/control_states":
                controls = _fetch_ha_audio_control_states(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("controls", {}),
                )
                return _json(self, 200, {"ok": True, "controls": controls})

            if path == "/api/ha/audio/switch_states":
                controls = _fetch_ha_switch_control_states(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("controls", {}),
                )
                return _json(self, 200, {"ok": True, "controls": controls})

            if path == "/api/ha/audio/control/action":
                control = _call_number_service(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityId", ""),
                    payload.get("value"),
                )
                return _json(self, 200, {"ok": True, "control": control})

            if path == "/api/ha/audio/switch/action":
                control = _call_switch_service(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityId", ""),
                    payload.get("action", "toggle"),
                )
                return _json(self, 200, {"ok": True, "control": control})

            if path == "/api/ha/media/action":
                state = _call_media_service(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityId", ""),
                    payload.get("action", ""),
                    payload.get("value"),
                )
                return _json(self, 200, {"ok": True, "state": state})

            state = _call_cover_service(
                payload.get("url", ""),
                payload.get("token", ""),
                payload.get("entityId", ""),
                payload.get("action", ""),
                payload.get("position"),
            )
            return _json(self, 200, {"ok": True, "state": state})
        except Exception as exc:
            _json(self, 502, {"ok": False, "error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Smart Thermostat local server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args()

    class SmartThermostatHTTPServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = 32

    def _clean_shutdown(signum=None, frame=None):
        _CONTROL_LOOP_STOP.set()
        _flush_thermostat_state_to_disk()
        _flush_hvac_history_to_disk()
        if signum is not None:
            raise SystemExit(0)

    atexit.register(_flush_thermostat_state_to_disk)
    atexit.register(_flush_hvac_history_to_disk)
    try:
        signal.signal(signal.SIGTERM, _clean_shutdown)
        signal.signal(signal.SIGINT, _clean_shutdown)
    except Exception:
        pass

    _initialize_motion_hardware()
    _start_thermostat_control_loop()

    httpd = SmartThermostatHTTPServer((args.host, args.port), SmartThermostatHandler)
    print(f"Smart Thermostat server running at http://{args.host}:{args.port}")
    print("Open http://localhost:%s" % args.port)
    try:
        httpd.serve_forever()
    finally:
        _CONTROL_LOOP_STOP.set()
        _flush_thermostat_state_to_disk()
        _flush_hvac_history_to_disk()


if __name__ == "__main__":
    main()
