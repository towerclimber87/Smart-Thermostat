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
import signal
import socket
import shutil
import shlex
import subprocess
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request, error
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA_DIR = ROOT / "data"
VERSION_FILE = ROOT / "VERSION"
THERMOSTAT_STATE_FILE = DATA_DIR / "thermostat-state.json"
THERMOSTAT_SCHEDULES_FILE = DATA_DIR / "thermostat-schedules.json"
THERMOSTAT_SCHEDULES_BACKUP_FILE = DATA_DIR / "thermostat-schedules.backup.json"
PANEL_CONFIG_FILE = DATA_DIR / "panel-config.json"
HVAC_HISTORY_FILE = DATA_DIR / "hvac-history.json"
APP_STARTED_AT = time.time()
HA_STATES_CACHE_TTL_SECONDS = float(os.environ.get("SMART_THERMOSTAT_HA_STATES_CACHE_TTL_SECONDS", "8.0"))
HA_ENTITY_STATE_CACHE_TTL_SECONDS = float(os.environ.get("SMART_THERMOSTAT_HA_ENTITY_STATE_CACHE_TTL_SECONDS", "8.0"))
HA_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("SMART_THERMOSTAT_HA_REQUEST_TIMEOUT_SECONDS", "3.0"))
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
CONFIG_WEB_PORTAL_TIMEOUT_SECONDS = max(60.0, float(os.environ.get("SMART_THERMOSTAT_CONFIG_PORTAL_TIMEOUT_SECONDS", "900") or "900"))
_CONFIG_WEB_PORTAL_LOCK = threading.RLock()
_CONFIG_WEB_PORTAL_ENABLED_UNTIL = 0.0
_CONFIG_WEB_PORTAL_STARTED_AT = 0.0
_CONFIG_WEB_PORTAL_LAST_ACTIVITY_AT = 0.0
_HA_STATES_CACHE_LOCK = threading.Lock()
_HA_STATES_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_HA_ENTITY_STATE_CACHE_LOCK = threading.Lock()
_HA_ENTITY_STATE_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
_THERMOSTAT_RECORD_LOCK = threading.RLock()
_THERMOSTAT_RECORD_CACHE: dict | None = None
_THERMOSTAT_RECORD_LAST_PERSIST_SIGNATURE = ""
_THERMOSTAT_RECORD_DIRTY = False
_HVAC_HISTORY_LOCK = threading.RLock()
_HVAC_HISTORY_ARCHIVE: dict | None = None
_HVAC_HISTORY_CURRENT: dict | None = None


HARDWARE_RELAY_PINS = {
    "fan": {"gpio": 7, "physical": 26, "label": "Fan Relay", "resistor": "R98 470Ω"},
    "cool": {"gpio": 8, "physical": 24, "label": "Cool Relay", "resistor": "R97 470Ω"},
    "heat": {"gpio": 22, "physical": 15, "label": "Heat Relay", "resistor": "R95 470Ω"},
}
HARDWARE_I2C_PINS = {
    "sda": {"gpio": 2, "physical": 3, "label": "SDA"},
    "scl": {"gpio": 3, "physical": 5, "label": "SCL"},
}
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
LOCAL_TEMP_SENSOR_MODE = os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_MODE", "fallback").strip().lower() or "fallback"
LOCAL_TEMP_SENSOR_POLL_SECONDS = max(2.0, float(os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_POLL_SECONDS", "5") or "5"))
LOCAL_TEMP_SENSOR_STALE_SECONDS = max(15.0, float(os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_FALLBACK_AFTER_SECONDS", "60") or "60"))
LOCAL_TEMP_SENSOR_ADDRESS = os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_I2C_ADDRESS", "").strip()
LOCAL_TEMP_SENSOR_TYPE = os.environ.get("SMART_THERMOSTAT_LOCAL_TEMP_TYPE", "auto").strip().lower() or "auto"
LOCAL_TEMP_SOURCE_NAMES = {"onboard", "local", "i2c", "hardware", "onboard-fallback"}

_HARDWARE_LOCK = threading.RLock()
_HARDWARE_RELAY_BACKEND = None
_HARDWARE_RGB_BACKEND = None
_HARDWARE_LAST_RELAYS = {"fan": False, "heat": False, "cool": False}
_HARDWARE_LAST_RELAY_SOURCE = "thermostat"
_HARDWARE_MANUAL = {"active": False, "relays": {"fan": False, "heat": False, "cool": False}}
_HARDWARE_RGB = {"on": False, "color": "#35eaff"}
_HVAC_HISTORY_LAST_RELAYS = {"fan": False, "heat": False, "cool": False}
_HVAC_HISTORY_LAST_SOURCE = "thermostat"
_EXTERNAL_HA_AIR_LOCK = threading.RLock()
_EXTERNAL_HA_AIR_LAST_STATES = {"heat": None, "cool": None}
_EXTERNAL_HA_AIR_LAST_ENTITIES = {"heat": "", "cool": ""}
_EXTERNAL_HA_AIR_RETRY_AFTER = {"heat": 0.0, "cool": 0.0}
_EXTERNAL_HA_AIR_NEXT_VERIFY_AT = {"heat": 0.0, "cool": 0.0}
_EXTERNAL_HA_AIR_LAST_ERROR_AT = {"heat": 0.0, "cool": 0.0}
_THERMOSTAT_ASYNC_OUTPUT_LOCK = threading.Lock()
_THERMOSTAT_ASYNC_OUTPUT_SEQ = 0
_HARDWARE_LAST_I2C_SCAN = {"at": 0.0, "payload": None}
_LOCAL_TEMP_SENSOR_LOCK = threading.RLock()
_LOCAL_TEMP_SENSOR_CACHE = {"at": 0.0, "payload": None}
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
    "airControlMode": "internal",
    "externalHeatEntity": None,
    "externalCoolEntity": None,
    "heatLocked": False,
    "coolLocked": False,
    "people": [],
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
    "alarmEntity": None,
    "alarmAvailableEntities": [],
    "doorEntity": None,
    "doorAvailableEntities": [],
    "lightAvailableEntities": [],
    "roomAvailableEntities": [],
    "personAvailableEntities": [],
    "pauseFunctionAvailableEntities": [],
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
    if mode in {"heat_cool", "auto"}:
        return "auto"
    if mode in {"off", "heat", "cool"}:
        return mode
    if fallback == "":
        return ""
    return fallback if fallback in {"off", "heat", "cool", "auto"} else "cool"


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
    started_at = _number(value.get("startedAt"), int(time.time() * 1000), 0, None)
    entity_ids = _normalize_presence_entity_list(value.get("entityIds", value.get("blockedEntityIds", [])))
    return {
        "active": True,
        "startedAt": started_at,
        "entityIds": entity_ids,
        "reason": str(value.get("reason") or "manual-return-home").strip()[:80] or "manual-return-home",
    }


def _presence_home_override_payload(entity_ids: list[str] | None = None, *, reason: str = "manual-return-home") -> dict:
    return {
        "active": True,
        "startedAt": int(time.time() * 1000),
        "entityIds": _normalize_presence_entity_list(entity_ids or []),
        "reason": reason,
    }


def _allowed_mode_for_locks(mode: str, thermostat: dict, fallback: str = "cool") -> str:
    # Lockout is an output interlock, not a mode selector. A user may still
    # select Heat/Cool/Auto/Off from the wall panel or Home Assistant, but the
    # locked relay side must never energize, including during safety calls.
    return _normalize_mode(mode, fallback)


def _available_hvac_modes(thermostat: dict) -> list[str]:
    return ["off", "cool", "heat", "heat_cool"]

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
    for source in (existing or {}, incoming or {}):
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

        if "heatLocked" in source:
            base["heatLocked"] = _boolish(source.get("heatLocked"))
        if "coolLocked" in source:
            base["coolLocked"] = _boolish(source.get("coolLocked"))
        air_mode_value = source.get("airControlMode", source.get("airSwitchMode", source.get("airSourceMode", None)))
        if air_mode_value is not None:
            base["airControlMode"] = _normalize_air_control_mode(air_mode_value, base.get("airControlMode", "internal"))
        for dst_key, aliases in (
            ("externalHeatEntity", ("externalHeatEntity", "externalHeatControlEntity", "externalHeatEntry", "heatControlEntity")),
            ("externalCoolEntity", ("externalCoolEntity", "externalCoolControlEntity", "externalCoolEntry", "coolControlEntity")),
        ):
            for alias in aliases:
                if alias in source:
                    base[dst_key] = _normalize_external_air_entity(source.get(alias))
                    break
        if "people" in source:
            base["people"] = _normalize_person_entries(source.get("people"))
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
            ("heatMinimumRuntimeMinutes", base.get("heatMinimumRuntimeMinutes", 2), 1, 30),
            ("coolMinimumRuntimeMinutes", base.get("coolMinimumRuntimeMinutes", 2), 1, 30),
            ("coolFanRemainOnMinutes", base["coolFanRemainOnMinutes"], 0, 15),
            ("autoLockoutUntil", base["autoLockoutUntil"], 0, None),
            ("manualLockoutUntil", base.get("manualLockoutUntil", 0), 0, None),
            ("lastHeatRunAt", base["lastHeatRunAt"], 0, None),
            ("lastCoolRunAt", base["lastCoolRunAt"], 0, None),
            ("equipmentLastHeatRunAt", base.get("equipmentLastHeatRunAt", 0), 0, None),
            ("equipmentLastCoolRunAt", base.get("equipmentLastCoolRunAt", 0), 0, None),
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
        if "autoSwitchNotice" in source:
            base["autoSwitchNotice"] = _normalize_auto_switch_notice(source.get("autoSwitchNotice"))
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
    if base["away"] or not base.get("people"):
        # A manual Home override only makes sense while the thermostat is in
        # Home mode and person tracking is configured.
        base["presenceHomeOverride"] = None
    mode_limits = base["limits"].get(base["mode"], {"min": 45, "max": 95})
    if not base["away"] and not base.get("pauseFunction", {}).get("active"):
        base["targetTemp"] = _number(base["targetTemp"], 70, mode_limits.get("min"), mode_limits.get("max"))
        base["lastComfortTarget"] = _number(base["lastComfortTarget"], base["targetTemp"], mode_limits.get("min"), mode_limits.get("max"))
    return base


THERMOSTAT_PERSIST_KEYS = (
    "name",
    "currentTempSource",
    "currentTempSourceName",
    "targetTemp",
    "lastComfortTarget",
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
    "heatMinimumRuntimeMinutes",
    "coolMinimumRuntimeMinutes",
    "coolFanRemainOnMinutes",
    "airControlMode",
    "externalHeatEntity",
    "externalCoolEntity",
    "heatLocked",
    "coolLocked",
    "people",
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
    "lastHeatRunAt",
    "lastCoolRunAt",
    "equipmentLastHeatRunAt",
    "equipmentLastCoolRunAt",
    "heatRelayWasOn",
    "coolRelayWasOn",
    "heatCycleStartedAt",
    "coolCycleStartedAt",
    "heatCycleStoppedAt",
    "coolCycleStoppedAt",
    "coolFanHoldUntil",
    "autoSwitchNotice",
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
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


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
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{name} Config Backup</title>
  <style>
    :root {{ color-scheme: dark; font-family: Arial, Helvetica, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; color: #f7fbff; background: radial-gradient(circle at 15% 0%, rgba(70,232,255,.24), transparent 28%), linear-gradient(135deg, #071222, #101d35 52%, #050913); }}
    .wrap {{ width: min(980px, calc(100% - 36px)); margin: 0 auto; padding: 34px 0 46px; }}
    .hero {{ display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; margin-bottom: 18px; }}
    .eyebrow {{ color: #46e8ff; font-size: 12px; font-weight: 900; letter-spacing: 4px; text-transform: uppercase; }}
    h1 {{ margin: 7px 0 8px; font-size: clamp(32px, 5vw, 58px); line-height: .95; }}
    .muted {{ color: #a8b6cf; line-height: 1.45; }}
    .pill {{ border: 1px solid rgba(255,255,255,.16); border-radius: 999px; padding: 10px 14px; color: #dce8ff; background: rgba(255,255,255,.07); white-space: nowrap; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid rgba(255,255,255,.13); border-radius: 24px; padding: 18px; background: rgba(255,255,255,.075); box-shadow: 0 18px 55px rgba(0,0,0,.28); backdrop-filter: blur(14px); }}
    .label {{ color: #8fa1be; font-size: 11px; font-weight: 900; letter-spacing: 2.5px; text-transform: uppercase; margin-bottom: 8px; }}
    .value {{ font-size: 18px; font-weight: 900; overflow-wrap: anywhere; }}
    .actions {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 18px; }}
    .action-card {{ min-height: 240px; display: flex; flex-direction: column; gap: 12px; }}
    button, .button {{ border: 0; border-radius: 18px; min-height: 56px; padding: 0 22px; display: inline-flex; align-items: center; justify-content: center; text-decoration: none; font-weight: 1000; font-size: 16px; color: white; cursor: pointer; background: linear-gradient(135deg, #14b8ff, #7c3aed); box-shadow: 0 12px 36px rgba(20,184,255,.22); }}
    button.secondary {{ background: rgba(255,255,255,.10); box-shadow: none; border: 1px solid rgba(255,255,255,.15); }}
    input[type=file] {{ width: 100%; padding: 14px; border-radius: 16px; color: #dce8ff; border: 1px dashed rgba(255,255,255,.28); background: rgba(0,0,0,.18); }}
    .status {{ min-height: 48px; padding: 12px 14px; border-radius: 16px; color: #e9f4ff; background: rgba(0,0,0,.20); border: 1px solid rgba(255,255,255,.10); }}
    .ok {{ color: #76ffc4; }} .bad {{ color: #ff8a8a; }}
    @media (max-width: 760px) {{ .hero, .actions {{ grid-template-columns: 1fr; display: grid; }} .grid {{ grid-template-columns: 1fr 1fr; }} .pill {{ white-space: normal; }} }}
    @media (max-width: 480px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div>
        <div class="eyebrow">Smart Thermostat Backup</div>
        <h1>{name}</h1>
        <div class="muted">Download a backup into this browser or upload a saved backup to restore this panel. No USB drive is required.</div>
      </div>
      <div class="pill">{address}</div>
    </section>

    <section class="grid">
      <div class="card"><div class="label">Version</div><div class="value">{version}</div></div>
      <div class="card"><div class="label">Host</div><div class="value">{host}</div></div>
      <div class="card"><div class="label">Thermal</div><div class="value">{thermal}</div></div>
      <div class="card"><div class="label">Uptime</div><div class="value">{uptime}</div></div>
      <div class="card"><div class="label">Config Version</div><div class="value">{cfg_version}</div></div>
      <div class="card"><div class="label">Config Updated</div><div class="value">{updated}</div></div>
      <div class="card"><div class="label">Backup Name</div><div class="value">Device + date</div></div>
      <div class="card"><div class="label">Backup Portal</div><div class="value">Open</div></div>
    </section>

    <section class="actions">
      <div class="card action-card">
        <div class="label">Download</div>
        <div class="value">Save this panel's config</div>
        <p class="muted">The filename includes the thermostat name and current date/time.</p>
        <a class="button" href="/api/system/config-export">Download Config</a>
      </div>
      <div class="card action-card">
        <div class="label">Upload</div>
        <div class="value">Restore from a config file</div>
        <input id="file" type="file" accept="application/json,.json" />
        <button id="upload" type="button">Upload Config</button>
        <div id="status" class="status muted">Choose a Smart Thermostat config backup, then press Upload Config.</div>
      </div>
    </section>
  </main>
<script>
const fileInput = document.getElementById('file');
const uploadButton = document.getElementById('upload');
const statusBox = document.getElementById('status');
function setStatus(text, kind) {{
  statusBox.textContent = text;
  statusBox.className = 'status ' + (kind || 'muted');
}}
uploadButton.addEventListener('click', async () => {{
  const file = fileInput.files && fileInput.files[0];
  if (!file) {{ setStatus('Select a config JSON file first.', 'bad'); return; }}
  try {{
    setStatus('Reading file...', 'muted');
    const text = await file.text();
    const payload = JSON.parse(text);
    setStatus('Uploading config...', 'muted');
    const response = await fetch('/api/system/config-import', {{
      method: 'POST',
      headers: {{ 'Accept': 'application/json', 'Content-Type': 'application/json' }},
      body: JSON.stringify(payload)
    }});
    const data = await response.json().catch(() => ({{}}));
    if (!response.ok || !data.ok) {{
      throw new Error(data.error || data.message || 'Upload failed.');
    }}
    setStatus(data.message || 'Config uploaded. Restart or refresh the panel if needed.', 'ok');
  }} catch (err) {{
    setStatus(err && err.message ? err.message : String(err), 'bad');
  }}
}});
</script>
</body>
</html>"""


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
        "message": "Config uploaded. Settings were restored on this panel.",
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


def _empty_auto_switch_hold() -> dict:
    return _deepcopy_json(DEFAULT_THERMOSTAT["autoSwitchHold"])


def _record_auto_switch_notice(thermostat: dict, source: str, from_mode: str, to_mode: str) -> None:
    from_mode = str(from_mode or "").strip().lower()
    to_mode = str(to_mode or "").strip().lower()
    if from_mode not in {"heat", "cool"} or to_mode not in {"heat", "cool"} or from_mode == to_mode:
        return
    cool_target, heat_target = _auto_switch_targets(thermostat)
    current = _number(thermostat.get("currentTemp"), 70, -40, 130)
    thermostat["autoSwitchNotice"] = {
        "active": True,
        "source": str(source or "manual").strip().lower() if str(source or "").strip().lower() in {"auto", "manual"} else "manual",
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
    if signal and current_mode and signal != current_mode and _mode_available_for_auto_switch(t, signal):
        previous = current_mode
        t["mode"] = signal
        t["manualPendingMode"] = ""
        t["manualLockoutUntil"] = 0
        _clamp_comfort_target_to_mode(t)
        if notify:
            _record_auto_switch_notice(t, "manual", previous, signal)
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
    for person in thermostat.get("people") or []:
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


def _apply_away_setpoint_logic(thermostat: dict, *, was_away: bool | None = None) -> dict:
    """Keep Away mode tied to Heat Away / Cool Away without losing the Home target."""
    t = dict(thermostat or {})
    away = bool(t.get("away"))
    previous_away = bool(was_away) if was_away is not None else away

    if away:
        away_target = _away_target_for_current_mode(t)
        if not previous_away:
            current_target = _number(t.get("targetTemp"), t.get("lastComfortTarget", 70), 45, 95)
            if abs(float(current_target) - float(away_target)) > 0.01:
                t["lastComfortTarget"] = current_target
        t["targetTemp"] = away_target
    elif was_away is True:
        t["targetTemp"] = _number(t.get("lastComfortTarget"), t.get("targetTemp", 70), 45, 95)

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
    now = datetime.now()
    time_key = now.strftime("%H:%M")
    date_key = now.strftime("%Y-%m-%d")
    changed = False
    updated_schedules: list[dict] = []
    updated = dict(thermostat)
    for schedule in schedules:
        sched = dict(schedule)
        should_run = (
            bool(sched.get("enabled", True))
            and str(sched.get("time") or "") == time_key
            and str(sched.get("lastTriggeredDate") or "") != date_key
            and _schedule_people_are_home(sched, updated)
        )
        if should_run:
            target = _schedule_target_for_current_mode(updated, sched)
            updated["targetTemp"] = target
            updated["lastComfortTarget"] = target
            sched["lastTriggeredDate"] = date_key
            changed = True
        updated_schedules.append(sched)
    if changed:
        updated["schedules"] = updated_schedules
        return _merge_thermostat_state(updated)
    return thermostat



def _thermostat_person_entity_ids(thermostat: dict) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for person in thermostat.get("people") or []:
        if not isinstance(person, dict):
            continue
        entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
        if entity_id and entity_id not in seen:
            seen.add(entity_id)
            ids.append(entity_id)
    return ids


def _apply_presence_away_logic(thermostat: dict) -> dict:
    entity_ids = _thermostat_person_entity_ids(thermostat)
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

    if any_home:
        # A real Home report proves the phone/person tracker is back online, so
        # release a manual Return Home override and allow future all-away
        # transitions to work normally again.
        if home_override:
            updated["presenceHomeOverride"] = None
        if bool(updated.get("away")) and away_source in {"presence", "auto", ""}:
            updated["away"] = False
            updated["awaySource"] = "presence"
            updated["manualAwayPresenceLatch"] = None
            if updated.get("lastComfortTarget"):
                updated["targetTemp"] = updated.get("lastComfortTarget")
    else:
        if home_override:
            # The user deliberately tapped Return Home while the assigned people
            # still report Away/unknown. Keep Home until at least one assigned
            # person reports Home again; this covers dead phones and stale
            # presence data without disabling Auto Away forever.
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


def _apply_door_pause_snooze_request(existing: dict, minutes: object = 5) -> dict:
    t = _merge_thermostat_state(existing)
    pause = _normalize_pause_function(t.get("pauseFunction"))
    snooze_minutes = _normalize_pause_function_duration(minutes, 5)
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
        "countdownReason": "snoozed",
    })
    t["pauseFunction"] = pause
    return _merge_thermostat_state(t)


def _apply_runtime_thermostat_logic(record: dict, *, notify: bool = True) -> dict:
    thermostat = record.get("thermostat") or {}
    was_away = bool(thermostat.get("away"))
    thermostat = _clear_expired_virtual_temp_override(thermostat)
    thermostat = _apply_selected_ha_temperature_sensor_if_needed(thermostat)
    thermostat = _apply_selected_ha_outdoor_temperature_sensor_if_needed(thermostat)
    thermostat = _apply_local_temperature_sensor_if_needed({"thermostat": thermostat})
    updated = _apply_presence_away_logic(thermostat)
    updated = _apply_door_pause_logic(updated)
    updated = _apply_away_setpoint_logic(updated, was_away=was_away)
    updated = _apply_comfort_auto_switch_logic(updated, notify=notify)
    scheduled = _apply_thermostat_schedules(updated)
    scheduled = _apply_away_setpoint_logic(scheduled, was_away=was_away)
    if scheduled != updated:
        _write_thermostat_record(scheduled, persist=True)
        updated = scheduled
    elif updated != thermostat:
        _write_thermostat_record(updated, persist=False)
    return updated


def _mark_thermostat_equipment_run(outputs: dict) -> None:
    now_ms = int(time.time() * 1000)
    try:
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
    was_on = bool(thermostat.get(relay_key))
    min_ms = _minimum_cycle_runtime_ms(thermostat, kind)

    if locked or active_mode == "off":
        return False, 0, ""

    # Do not keep running a side just because it had been running if the user
    # changed modes, a changeover lockout is active, or safety needs the other
    # side. Minimum runtime only extends normal operation for the current side.
    may_extend_current_run = active_mode == kind and (not safety_mode or safety_mode == kind)
    if was_on and not requested_on and may_extend_current_run:
        started_at = _number(thermostat.get(started_key), 0, 0)
        if started_at > 0:
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
    heat = (not thermostat.get("heatLocked")) and active_mode == "heat" and current < target
    cool = (not thermostat.get("coolLocked")) and active_mode == "cool" and current > target
    pending_mode = ""
    manual_lockout_until = 0
    now_ms = int(time.time() * 1000)
    if not safety_mode and mode != "auto" and (heat or cool):
        pending_mode = "heat" if heat else "cool"
        last_key = "equipmentLastCoolRunAt" if pending_mode == "heat" else "equipmentLastHeatRunAt"
        legacy_key = "lastCoolRunAt" if pending_mode == "heat" else "lastHeatRunAt"
        last_opposite_run_at = max(_number(thermostat.get(last_key), 0, 0), _number(thermostat.get(legacy_key), 0, 0))
        lockout_minutes = _number(thermostat.get("manualChangeoverLockoutMinutes"), MANUAL_CHANGEOVER_LOCKOUT_MINUTES, 0, 60)
        until = last_opposite_run_at + lockout_minutes * 60000
        if lockout_minutes > 0 and last_opposite_run_at and until > now_ms:
            heat = False
            cool = False
            active_mode = "lockout"
            manual_lockout_until = until
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
    )
    cool, cool_cycle_until, cool_cycle_reason = _apply_minimum_cycle_protection(
        thermostat,
        kind="cool",
        requested_on=cool,
        active_mode=active_mode,
        safety_mode=safety_mode,
        now_ms=now_ms,
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
    starting_cool_fan_hold = bool(thermostat.get("coolRelayWasOn")) and (not cool) and remain_minutes > 0
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
    outputs = _thermostat_outputs(thermostat)
    if apply_hardware:
        _apply_thermostat_outputs_to_hardware(outputs, thermostat)
    hvac_mode = "heat_cool" if thermostat["mode"] == "auto" else thermostat["mode"]
    preset_mode = "away" if thermostat.get("away") else "home"
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
        "externalHeatEntity": _normalize_external_air_entity(thermostat.get("externalHeatEntity")),
        "externalCoolEntity": _normalize_external_air_entity(thermostat.get("externalCoolEntity")),
        "relays": {"fan": outputs["fan"], "heat": outputs["heat"], "cool": outputs["cool"]},
        "serial": serial,
        "unique_id": serial,
        "manufacturer": "IHA",
        "model": "Smart Thermostat Wall Panel",
        "sw_version": sw_version,
        "swVersion": sw_version,
    }
    payload = {
        "ok": True,
        "version": record["version"],
        "updatedAt": record["updatedAt"],
        "name": thermostat.get("name") or "IHA Thermostat",
        "serial": serial,
        "unique_id": serial,
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
        "externalHeatEntity": _normalize_external_air_entity(thermostat.get("externalHeatEntity")),
        "externalCoolEntity": _normalize_external_air_entity(thermostat.get("externalCoolEntity")),
        "relays": {"fan": outputs["fan"], "heat": outputs["heat"], "cool": outputs["cool"]},
        "relayFan": outputs["fan"],
        "relayHeat": outputs["heat"],
        "relayCool": outputs["cool"],
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
            outputs = _thermostat_outputs(snapshot)
            _apply_thermostat_outputs_to_hardware(outputs, snapshot)
        except Exception as exc:  # noqa: BLE001 - never let async apply kill the server
            print(f"Thermostat async output apply failed ({reason}): {exc}", flush=True)

    threading.Thread(target=_worker, name=f"thermostat-output-{reason}", daemon=True).start()


def _handle_thermostat_update(payload: dict) -> dict:
    existing = _read_thermostat_record()["thermostat"]
    incoming = payload.get("thermostat", payload) if isinstance(payload, dict) else {}
    if not isinstance(incoming, dict):
        incoming = {}
    incoming_has_schedules = "schedules" in incoming

    pause_incoming = incoming.get("pauseFunction") if isinstance(incoming.get("pauseFunction"), dict) else {}
    if pause_incoming and str(pause_incoming.get("action") or "").strip().lower() == "snooze":
        existing = _apply_door_pause_snooze_request(existing, pause_incoming.get("snoozeMinutes", 5))
        incoming = {k: v for k, v in incoming.items() if k != "pauseFunction"}
    elif pause_incoming and pause_incoming.get("snoozeMinutes") is not None:
        existing = _apply_door_pause_snooze_request(existing, pause_incoming.get("snoozeMinutes", 5))
        incoming = {k: v for k, v in incoming.items() if k != "pauseFunction"}

    bypass_mode = str(incoming.get("bypassChangeoverLockout") or "").strip().lower()
    if bypass_mode not in {"heat", "cool"} and incoming.get("bypassChangeoverLockout"):
        bypass_mode = str(existing.get("manualPendingMode") or existing.get("autoPendingMode") or "").strip().lower()
    existing_away_source = str(existing.get("awaySource") or "").strip().lower()
    incoming_requests_home = (
        "away" in incoming
        and bool(existing.get("away"))
        and existing_away_source in {"presence", "auto"}
        and not bool(incoming.get("away"))
    )
    if incoming_requests_home and "presenceHomeOverride" not in incoming:
        incoming = dict(incoming)
        incoming["presenceHomeOverride"] = _presence_home_override_payload(
            _thermostat_person_entity_ids(existing),
            reason="manual-return-home",
        )

    requested_mode_raw = incoming.get("mode", incoming.get("hvac_mode", incoming.get("hvacMode")))
    requested_mode = _normalize_mode(requested_mode_raw, "") if requested_mode_raw is not None else ""
    if requested_mode in {"off", "heat", "cool", "auto"} and "away" not in incoming and "preset_mode" not in incoming and "presetMode" not in incoming:
        # A Home Assistant HVAC-mode change should behave like tapping the mode
        # button on the thermostat page: leave Away and make this a manual mode
        # command. Without this, HA could send heat/cool while the saved Away
        # preset stayed active, making the HA card look like the command only
        # partly applied or reverted.
        incoming = dict(incoming)
        incoming["away"] = False
        incoming["awaySource"] = ""
        if bool(existing.get("away")) and existing_away_source in {"presence", "auto"} and "presenceHomeOverride" not in incoming:
            incoming["presenceHomeOverride"] = _presence_home_override_payload(
                _thermostat_person_entity_ids(existing),
                reason="manual-ha-mode-change",
            )

    if bypass_mode in {"heat", "cool"}:
        existing = dict(existing)
        if bypass_mode == "heat":
            existing["equipmentLastCoolRunAt"] = 0
            existing["lastCoolRunAt"] = 0
        else:
            existing["equipmentLastHeatRunAt"] = 0
            existing["lastHeatRunAt"] = 0
        existing["manualPendingMode"] = ""
        existing["manualLockoutUntil"] = 0
        existing["autoPendingMode"] = ""
        existing["autoLockoutUntil"] = 0
        incoming = {k: v for k, v in incoming.items() if k != "bypassChangeoverLockout"}

    was_away = bool(existing.get("away"))
    merged = _merge_thermostat_state(existing, incoming)
    merged = _apply_away_setpoint_logic(merged, was_away=was_away)

    if requested_mode in {"heat", "cool"}:
        # Manual / physical mode changes always win, whether they came from the
        # touchscreen buttons or Home Assistant. If the comfort auto-switch rule
        # would have chosen the other side, hold the requested manual mode and
        # let the UI show the same notice it shows for a front-panel tap.
        merged["autoSwitchNotice"] = _empty_auto_switch_notice()
        merged["autoPendingMode"] = ""
        merged["autoLockoutUntil"] = 0
        signal = _auto_switch_signal(merged)
        if signal and signal != requested_mode and _mode_available_for_auto_switch(merged, signal):
            merged["autoSwitchHold"] = {
                "active": True,
                "source": "manual",
                "mode": requested_mode,
                "suggestedMode": signal,
                "reason": "manual-override",
                "dismissed": False,
                "createdAt": int(time.time() * 1000),
            }
        else:
            merged["autoSwitchHold"] = _empty_auto_switch_hold()
    elif requested_mode in {"auto", "off"}:
        merged["autoSwitchNotice"] = _empty_auto_switch_notice()
        merged["autoSwitchHold"] = _empty_auto_switch_hold()
        merged["autoPendingMode"] = ""
        merged["autoLockoutUntil"] = 0
        merged["manualPendingMode"] = ""
        merged["manualLockoutUntil"] = 0

    merged = _apply_comfort_auto_switch_logic(merged, notify=True)
    merged = _apply_away_setpoint_logic(merged, was_away=was_away)
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
    for relay, on in normalized.items():
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
    key = "externalHeatEntity" if kind == "heat" else "externalCoolEntity"
    return _normalize_external_air_entity(thermostat.get(key))


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
    if _normalize_air_control_mode(thermostat.get("airControlMode")) != "external":
        return
    ha_url, token = _ha_credentials_from_panel_config()
    if not ha_url or not token:
        return

    desired = {
        "heat": bool(outputs.get("heat")),
        "cool": bool(outputs.get("cool")),
    }
    if desired["heat"] and desired["cool"]:
        desired["cool"] = False

    with _EXTERNAL_HA_AIR_LOCK:
        now = time.monotonic()
        # Turn inactive side off first, then energize the active side. That keeps
        # external HA helpers from seeing heat and cool on at the same moment.
        ordered = [kind for kind in ("heat", "cool") if not desired[kind]] + [kind for kind in ("heat", "cool") if desired[kind]]
        for kind in ordered:
            entry = _external_air_entity_for_kind(thermostat, kind)
            entity_id = str((entry or {}).get("entityId") or "").strip()
            if not entity_id:
                _EXTERNAL_HA_AIR_LAST_STATES[kind] = None
                _EXTERNAL_HA_AIR_LAST_ENTITIES[kind] = ""
                _EXTERNAL_HA_AIR_RETRY_AFTER[kind] = 0.0
                continue
            want_on = desired[kind]
            last_entity = str(_EXTERNAL_HA_AIR_LAST_ENTITIES.get(kind) or "")
            last_state = _EXTERNAL_HA_AIR_LAST_STATES.get(kind)
            retry_after = float(_EXTERNAL_HA_AIR_RETRY_AFTER.get(kind) or 0.0)
            next_verify_at = float(_EXTERNAL_HA_AIR_NEXT_VERIFY_AT.get(kind) or 0.0)
            needs_call = last_entity != entity_id or last_state is None or bool(last_state) != want_on
            can_try_now = not (retry_after and now < retry_after and last_entity == entity_id)

            # In external mode, do not only trust the command we sent earlier.
            # Wireless HA commands can be missed by remote heat/cool helpers, so
            # every 30 seconds verify the actual HA state and re-apply the desired
            # on/off state when it drifted.
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
                        print(f"External {kind} air state verify failed for {entity_id}: {exc}", flush=True)
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
                    print(f"External {kind} air control failed for {entity_id}: {exc}", flush=True)


def _release_external_ha_air_outputs(thermostat: dict) -> None:
    ha_url, token = _ha_credentials_from_panel_config()
    if not ha_url or not token:
        return
    with _EXTERNAL_HA_AIR_LOCK:
        now = time.monotonic()
        for kind in ("heat", "cool"):
            if _EXTERNAL_HA_AIR_LAST_STATES.get(kind) is not True:
                continue
            entry = _external_air_entity_for_kind(thermostat, kind)
            entity_id = str((entry or {}).get("entityId") or _EXTERNAL_HA_AIR_LAST_ENTITIES.get(kind) or "").strip()
            if not entity_id:
                _EXTERNAL_HA_AIR_LAST_STATES[kind] = None
                _EXTERNAL_HA_AIR_LAST_ENTITIES[kind] = ""
                continue
            retry_after = float(_EXTERNAL_HA_AIR_RETRY_AFTER.get(kind) or 0.0)
            if retry_after and now < retry_after:
                continue
            try:
                _call_room_control_service(ha_url, token, entity_id, "off")
                _EXTERNAL_HA_AIR_LAST_STATES[kind] = False
                _EXTERNAL_HA_AIR_LAST_ENTITIES[kind] = entity_id
                _EXTERNAL_HA_AIR_RETRY_AFTER[kind] = 0.0
                _EXTERNAL_HA_AIR_NEXT_VERIFY_AT[kind] = time.monotonic() + EXTERNAL_HA_AIR_VERIFY_INTERVAL_SECONDS
            except Exception as exc:
                _EXTERNAL_HA_AIR_RETRY_AFTER[kind] = time.monotonic() + 30.0
                print(f"External {kind} air release failed for {entity_id}: {exc}", flush=True)


def _apply_thermostat_outputs_to_hardware(outputs: dict, thermostat: dict | None = None) -> None:
    thermostat = _merge_thermostat_state(thermostat or _read_thermostat_record().get("thermostat") or {})
    external_mode = _normalize_air_control_mode(thermostat.get("airControlMode")) == "external"
    hardware_relays = {
        "fan": bool(outputs.get("fan")),
        "heat": False if external_mode else bool(outputs.get("heat")),
        "cool": False if external_mode else bool(outputs.get("cool")),
    }
    with _HARDWARE_LOCK:
        if _HARDWARE_MANUAL.get("active"):
            return
        _write_relay_outputs_locked(
            hardware_relays,
            "thermostat-external" if external_mode else "thermostat",
            record_history=not external_mode,
        )
    if external_mode:
        _apply_external_ha_air_outputs(thermostat, outputs)
        _remember_hvac_history_source(
            {"fan": outputs.get("fan"), "heat": outputs.get("heat"), "cool": outputs.get("cool")},
            "thermostat-external",
        )
    else:
        _release_external_ha_air_outputs(thermostat)
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
        _HARDWARE_MANUAL["active"] = True
        _HARDWARE_MANUAL["relays"] = _normalize_relay_outputs(relays)
        _write_relay_outputs_locked(_HARDWARE_MANUAL["relays"], "manual")
    return _hardware_status_payload()


def _release_manual_hardware() -> dict:
    with _HARDWARE_LOCK:
        _HARDWARE_MANUAL["active"] = False
        _HARDWARE_MANUAL["relays"] = {"fan": False, "heat": False, "cool": False}
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


def _read_direct_i2c_temperature_sensor() -> dict | None:
    if not HARDWARE_I2C_DEVICE.exists():
        return None
    address_override = _parse_i2c_address(LOCAL_TEMP_SENSOR_ADDRESS)
    sensor_type = LOCAL_TEMP_SENSOR_TYPE
    probes: list[tuple[str, int]] = []
    if address_override is not None:
        if sensor_type in {"sht30", "sht31", "sht3x"}:
            probes.append(("sht3x", address_override))
        elif sensor_type in {"tmp102", "tmp112", "tmp10x"}:
            probes.append(("tmp102", address_override))
        else:
            probes.extend((kind, address_override) for kind in ("sht3x", "tmp102"))
    else:
        probes.extend(("sht3x", address) for address in (0x44, 0x45))
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
            payload = _read_hwmon_temperature_sensor() or _read_direct_i2c_temperature_sensor()
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
    mode = LOCAL_TEMP_SENSOR_MODE
    if mode in {"always", "on", "force"}:
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
    raw = item.get("state")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, ""
    unit = str(attrs.get("unit_of_measurement") or "").strip()
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
        # Return to the configured source. The runtime pass immediately below
        # will read the selected Home Assistant sensor if one is configured.
        if _selected_ha_temperature_entity():
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


def _apply_local_temperature_sensor_if_needed(record: dict) -> dict:
    thermostat = record.get("thermostat") or {}
    if _virtual_temp_override_active(thermostat):
        return thermostat
    now = time.time()
    if not _thermostat_should_use_local_temp_sensor(thermostat, now):
        return thermostat
    sensor = _read_local_temperature_sensor()
    temp_f = sensor.get("temperatureF")
    if not sensor.get("available") or temp_f is None:
        return thermostat
    try:
        next_temp = round(float(temp_f), 1)
    except (TypeError, ValueError):
        return thermostat
    if not (-40.0 <= next_temp <= 130.0):
        return thermostat
    label = str(sensor.get("label") or "Onboard Temp Sensor").strip() or "Onboard Temp Sensor"
    updated = dict(thermostat)
    updated["currentTemp"] = next_temp
    updated["currentTempUpdatedAt"] = int(now)
    updated["runtimeTempSource"] = "onboard"
    updated["runtimeTempSourceName"] = label
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

def _hardware_status_payload(force_i2c: bool = False) -> dict:
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
        manual = {
            "active": bool(_HARDWARE_MANUAL.get("active")),
            "relays": dict(_HARDWARE_MANUAL.get("relays") or {}),
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


def _fetch_update_payload() -> dict:
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
    runtime = Path(os.environ.get("SMART_THERMOSTAT_UPDATE_RUNTIME_DIR", "/tmp/smart-thermostat-self-update")).expanduser()
    runtime.mkdir(parents=True, exist_ok=True)
    log_path = runtime / "fetch-update.log"
    script_path = runtime / "self-update.sh"

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
run_as_app_user git fetch origin Development
run_as_app_user git reset --hard origin/Development
cleanup_legacy_usb_mounts
run_as_app_user git clean -fd

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
        "--property", "Type=oneshot",
        "--property", f"WorkingDirectory={str(ROOT)}",
        "/bin/bash",
        str(script_path),
    ]

    try:
        started = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=12)
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


def _stable_panel_serial() -> str:
    """Return a stable Home Assistant unique id for this wall panel."""
    configured = os.environ.get("SMART_THERMOSTAT_SERIAL", "").strip()
    if configured:
        return configured

    for machine_id_path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            machine_id = machine_id_path.read_text(encoding="utf-8").strip()
        except OSError:
            machine_id = ""
        if machine_id:
            return f"iha-smart-thermostat-{machine_id[:12]}"

    hostname = _local_host_name().strip() or socket.gethostname().strip()
    if hostname:
        safe_hostname = "".join(ch.lower() if ch.isalnum() else "-" for ch in hostname).strip("-")
        if safe_hostname:
            return f"iha-smart-thermostat-{safe_hostname}"

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
    try:
        with request.urlopen(req, timeout=HA_REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc

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


def _ha_json_request(ha_url: str, token: str, method: str, path: str, payload: dict | None = None) -> object:
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
    try:
        with request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw": raw.decode("utf-8", errors="replace")}


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
    try:
        with request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc

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


def _call_alarm_service(ha_url: str, token: str, entity_id: str, action: str, code: str | None = None) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("alarm_control_panel."):
        raise ValueError("Entity must be an alarm_control_panel.* entity")

    service_by_action = {
        "disarm": "alarm_disarm",
        "arm_home": "alarm_arm_home",
        "arm_away": "alarm_arm_away",
        "arm_night": "alarm_arm_night",
    }
    action = (action or "disarm").strip().lower()
    service = service_by_action.get(action)
    if not service:
        raise ValueError("Unsupported alarm action")

    payload = {"entity_id": entity_id}
    code_value = str(code or "").strip()
    if code_value:
        payload["code"] = code_value

    _ha_json_request(ha_url, token, "POST", f"/api/services/alarm_control_panel/{service}", payload)
    _invalidate_ha_state_cache(ha_url, token)
    try:
        return _fetch_ha_alarm_state(ha_url, token, entity_id)
    except Exception:
        optimistic_state = "disarmed" if action == "disarm" else action.replace("arm_", "armed_")
        return {"entityId": entity_id, "name": entity_id, "domain": "alarm_control_panel", "state": optimistic_state}


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
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    friendly = str(attrs.get("friendly_name") or "")
    haystack = f"{entity_id} {friendly}".lower()
    compact = _compact_key(haystack)

    kind_terms = {
        "bass": ["bass"],
        "treble": ["treble"],
        "gain": ["gain", "subwoofergain", "subgain"],
        "music_surround": ["musicsurround", "surroundmusic", "music", "surroundlevel", "surroundvolume", "musicvolume"],
    }
    if not any(term in compact for term in kind_terms.get(kind, [kind])):
        return -1

    player_slug = player_entity_id.split(".", 1)[-1]
    player_compact = _compact_key(player_slug)
    name_words = [word for word in str(player_name or "").lower().replace("-", " ").split() if len(word) > 2]

    score = 5
    if player_compact and player_compact in compact:
        score += 20
    for word in name_words:
        if _compact_key(word) in compact:
            score += 4
    if "sonos" in compact:
        score += 1
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
        if best is not None and best_score >= 8:
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
        if path == "/api/history":
            requested_date = (query.get("date") or [None])[0]
            return _json(self, 200, _hvac_history_payload(requested_date))
        if path == "/api/history/dates":
            return _json(self, 200, _hvac_history_dates_payload())
        if path == "/api/config":
            return _json(self, 200, _panel_config_payload())
        if path == "/api/thermostat/status":
            return _json(self, 200, _thermostat_status_payload(refresh_runtime=False, apply_hardware=False))
        if path == "/api/discovery":
            return _json(self, 200, _discovery_payload())

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
        if path not in {"/api/config", "/api/thermostat/status", "/api/thermostat/control", "/api/system/fetch-update", "/api/system/reboot", "/api/system/config-web-portal", "/api/system/config-web-portal/close", "/api/system/config-export-usb", "/api/system/config-import", "/api/system/config-import-usb", "/api/hardware/relay", "/api/hardware/rgb", "/api/hardware/release", "/api/ha/covers", "/api/ha/cover/action", "/api/ha/cover/states", "/api/ha/entities", "/api/ha/weather/state", "/api/ha/media_players", "/api/ha/media/action", "/api/ha/media/states", "/api/ha/audio/controls", "/api/ha/audio/control_states", "/api/ha/audio/control/action", "/api/ha/audio/switch_states", "/api/ha/audio/switch/action", "/api/ha/alarm/states", "/api/ha/alarm/action", "/api/ha/binary_sensor/states", "/api/ha/light/states", "/api/ha/light/action", "/api/ha/room/states", "/api/ha/room/action"}:
            self.send_error(404, "Not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")

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

            if path == "/api/thermostat/status":
                return _json(self, 200, _thermostat_status_payload(refresh_runtime=False, apply_hardware=False))

            if path == "/api/thermostat/control":
                return _json(self, 200, _handle_thermostat_update(payload))

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
                    payload.get("action", "disarm"),
                    payload.get("code", ""),
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
