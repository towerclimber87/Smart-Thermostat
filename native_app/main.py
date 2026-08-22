#!/usr/bin/env python3
from __future__ import annotations

import copy
import faulthandler
from concurrent.futures import ThreadPoolExecutor
import math
import html
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from urllib import request as urlrequest
from pathlib import Path
from typing import Any, Callable

# Allow running from this folder without installing a package.
APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

_RUNTIME_CRASH_LOG_HANDLE = None


def runtime_log_dir() -> Path:
    configured = str(os.environ.get("SMART_THERMOSTAT_LOG_DIR") or "").strip()
    if configured:
        return Path(configured)
    # Diagnostics are intentionally volatile on the appliance. A restart loop
    # or repeated UI-stall trace must not turn into continuous SD-card writes.
    # The installed X launcher explicitly uses /dev/shm; keep the same safe
    # behavior when main.py is started by hand.
    candidates = [
        Path("/dev/shm/smart-thermostat-native/logs"),
        Path(os.environ.get("SMART_THERMOSTAT_RUNTIME_DIR", "/tmp/smart-thermostat-native")) / "logs",
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if os.access(str(candidate), os.W_OK):
                return candidate
        except Exception:
            continue
    return Path("/tmp/smart-thermostat-native/logs")


def runtime_log_path(name: str) -> Path:
    directory = runtime_log_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except Exception:
        directory = Path("/tmp")
    return directory / name


def trace_runtime(message: str):
    try:
        stamp = datetime.now().isoformat(timespec="seconds")
        print(f"[{stamp}] {message}", flush=True)
    except Exception:
        pass


def install_crash_logging():
    """Keep a small persistent crash log so forced restarts do not erase clues."""
    global _RUNTIME_CRASH_LOG_HANDLE
    if _RUNTIME_CRASH_LOG_HANDLE is not None:
        return
    try:
        path = runtime_log_path("native-crash.log")
        _RUNTIME_CRASH_LOG_HANDLE = open(path, "a", buffering=1)
        _RUNTIME_CRASH_LOG_HANDLE.write(f"\n===== native ui process start {datetime.now().isoformat(timespec='seconds')} =====\n")
        faulthandler.enable(file=_RUNTIME_CRASH_LOG_HANDLE, all_threads=True)

        original_excepthook = sys.excepthook

        def excepthook(exc_type, exc_value, exc_tb):
            try:
                _RUNTIME_CRASH_LOG_HANDLE.write(f"\n===== unhandled exception {datetime.now().isoformat(timespec='seconds')} =====\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=_RUNTIME_CRASH_LOG_HANDLE)
                _RUNTIME_CRASH_LOG_HANDLE.flush()
            except Exception:
                pass
            original_excepthook(exc_type, exc_value, exc_tb)

        sys.excepthook = excepthook

        if hasattr(threading, "excepthook"):
            original_threading_excepthook = threading.excepthook

            def thread_excepthook(args):
                try:
                    _RUNTIME_CRASH_LOG_HANDLE.write(f"\n===== unhandled thread exception {datetime.now().isoformat(timespec='seconds')} thread={getattr(args, 'thread', None)} =====\n")
                    traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback, file=_RUNTIME_CRASH_LOG_HANDLE)
                    _RUNTIME_CRASH_LOG_HANDLE.flush()
                except Exception:
                    pass
                original_threading_excepthook(args)

            threading.excepthook = thread_excepthook
        trace_runtime(f"Crash logging enabled at {path}")
    except Exception as exc:
        trace_runtime(f"Crash logging unavailable: {exc}")


from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QFont, QIcon, QImage, QPainter, QPen, QBrush, QLinearGradient, QPainterPath, QRadialGradient, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from api import ApiClient, ApiError
from theme import T, SLIDER_H, button_style, font
from widgets import (
    Background,
    BlindPreview,
    EntityPickerDialog,
    GlassPanel,
    HoldCard,
    IconCircle,
    LightCard,
    KeypadButton,
    MiniTextKeyboardDialog,
    NavBar,
    RoomControlCard,
    RoundButton,
    SectionTitle,
    ThermostatDial,
    TopPill,
    compact_name,
    fmt_temp,
)


def clamp(n, low, high):
    return max(low, min(high, n))


def intimacy_hold_target_f(thermostat: dict | None, default: int = 66) -> int:
    t = thermostat if isinstance(thermostat, dict) else {}
    raw = t.get("intimacyHoldTargetTemp")
    if raw is None:
        hold = t.get("intimacyHold") if isinstance(t.get("intimacyHold"), dict) else {}
        raw = hold.get("targetTemp", default)
    try:
        return int(clamp(round(float(raw)), 40, 100))
    except (TypeError, ValueError):
        return int(default)


def intimacy_hold_target_text(thermostat: dict | None, default: int = 66) -> str:
    return f"{intimacy_hold_target_f(thermostat, default)}°F"


def parse_schedule_time_24h(value: Any, default_hour: int = 7, default_minute: int = 0) -> tuple[int, int]:
    """Parse a schedule time into internal 24-hour HH:MM form.

    The scheduler stores time as HH:MM for the backend, but the touchscreen UI
    presents 12-hour time. Accept both forms so older/newer saved schedules and
    hand-edited config remain safe.
    """
    hour = int(clamp(default_hour, 0, 23))
    minute = int(clamp(default_minute, 0, 59))
    text = str(value or "").strip().upper()
    if not text:
        return hour, minute

    meridiem = None
    if text.endswith("AM") or text.endswith("PM"):
        meridiem = text[-2:]
        text = text[:-2].strip()

    try:
        if ":" not in text:
            return hour, minute
        h_text, m_text = text.split(":", 1)
        parsed_hour = int("".join(ch for ch in h_text if ch.isdigit()) or hour)
        parsed_minute = int("".join(ch for ch in m_text if ch.isdigit())[:2] or minute)
        if meridiem:
            parsed_hour = parsed_hour % 12
            if meridiem == "PM":
                parsed_hour += 12
        parsed_hour = int(clamp(parsed_hour, 0, 23))
        parsed_minute = int(clamp(parsed_minute, 0, 59))
        return parsed_hour, parsed_minute
    except Exception:
        return hour, minute


def format_schedule_time_12h(value: Any) -> str:
    hour, minute = parse_schedule_time_24h(value)
    suffix = "PM" if hour >= 12 else "AM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


SCHEDULE_DAY_OPTIONS = (
    ("mon", "Mon", "Monday"),
    ("tue", "Tue", "Tuesday"),
    ("wed", "Wed", "Wednesday"),
    ("thu", "Thu", "Thursday"),
    ("fri", "Fri", "Friday"),
    ("sat", "Sat", "Saturday"),
    ("sun", "Sun", "Sunday"),
)
SCHEDULE_DAY_KEYS = tuple(key for key, _short, _long in SCHEDULE_DAY_OPTIONS)
SCHEDULE_DAY_LABELS = {key: short for key, short, _long in SCHEDULE_DAY_OPTIONS}
SCHEDULE_DAY_ALIASES = {
    "0": "mon", "1": "mon", "m": "mon", "mon": "mon", "monday": "mon",
    "2": "tue", "tu": "tue", "tue": "tue", "tues": "tue", "tuesday": "tue",
    "3": "wed", "w": "wed", "wed": "wed", "weds": "wed", "wednesday": "wed",
    "4": "thu", "th": "thu", "thu": "thu", "thur": "thu", "thurs": "thu", "thursday": "thu",
    "5": "fri", "f": "fri", "fri": "fri", "friday": "fri",
    "6": "sat", "sa": "sat", "sat": "sat", "saturday": "sat",
    "7": "sun", "su": "sun", "sun": "sun", "sunday": "sun",
}


def normalize_schedule_days(value: Any) -> list[str]:
    if value is None:
        return list(SCHEDULE_DAY_KEYS)
    if isinstance(value, str):
        raw_items: list[Any] = [x.strip() for x in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple)):
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
            key = SCHEDULE_DAY_ALIASES.get(str(raw or "").strip().lower())
        if key in SCHEDULE_DAY_KEYS:
            selected.add(key)
    if not selected:
        return list(SCHEDULE_DAY_KEYS)
    return [key for key in SCHEDULE_DAY_KEYS if key in selected]


def schedule_days_text(value: Any, compact: bool = False) -> str:
    days = normalize_schedule_days(value)
    if len(days) >= 7:
        return "Every day"
    if days == list(SCHEDULE_DAY_KEYS[:5]):
        return "Weekdays"
    if days == list(SCHEDULE_DAY_KEYS[5:]):
        return "Weekends"
    labels = [SCHEDULE_DAY_LABELS.get(day, day.title()) for day in days]
    if compact:
        return ", ".join(labels)
    return "Runs " + ", ".join(labels)


def normalize_schedule_person_names(value: Any, entity_ids: Any = None) -> dict[str, str]:
    """Keep stable display names for schedule people alongside their entity IDs."""
    allowed = {str(x or "").strip() for x in (entity_ids or []) if str(x or "").strip()}
    names: dict[str, str] = {}
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
                name = str(item.get("name") or item.get("friendly_name") or "").strip()
                items.append((entity_id, name))
    else:
        items = []
    for raw_entity_id, raw_name in items:
        entity_id = str(raw_entity_id or "").strip()
        name = str(raw_name or "").strip()
        if not entity_id or not name or entity_id not in allowed:
            continue
        names[entity_id] = name[:80]
    return names


def schedule_person_names(schedule: dict, thermostat: dict | None = None) -> list[str]:
    entity_ids = [str(x or "").strip() for x in (schedule.get("personEntityIds") or []) if str(x or "").strip()]
    if not entity_ids:
        return []
    names = normalize_schedule_person_names(schedule.get("personNames"), entity_ids)
    if isinstance(thermostat, dict):
        for group_key in ("people", "autoAwayPeople"):
            for person in thermostat.get(group_key) or []:
                if not isinstance(person, dict):
                    continue
                entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
                if entity_id not in entity_ids:
                    continue
                friendly = str(person.get("name") or person.get("friendly_name") or "").strip()
                if friendly:
                    names[entity_id] = friendly
    return [names.get(entity_id) or entity_id for entity_id in entity_ids]


def as_bool_state(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").lower()
    return text in {"on", "open", "opening", "playing", "heat", "cool", "true", "1"}


def room_control_active(state: Any, domain: str | None = None) -> bool:
    text = str(state or "").strip().lower()
    domain = str(domain or "").strip().lower()
    if domain == "lock":
        return text in {"unlocked", "open", "opening"}
    if domain == "cover":
        return text in {"open", "opening"}
    return as_bool_state(text)


def room_control_domain_for(ctl: dict | None) -> str:
    entity_id = str((ctl or {}).get("haEntityId") or "")
    return str((ctl or {}).get("domain") or (entity_id.split(".", 1)[0] if "." in entity_id else "switch")).strip().lower()


def room_control_code_state_choices(ctl: dict | None) -> list[tuple[str, str]]:
    domain = room_control_domain_for(ctl)
    if domain == "lock":
        return [("lock", "Lock"), ("unlock", "Unlock")]
    if domain == "cover":
        return [("open", "Open"), ("close", "Close")]
    if domain in {"button", "input_button"}:
        return [("press", "Press")]
    if domain in {"scene", "script"}:
        return [("run", "Run")]
    if domain == "media_player":
        return [("on", "On"), ("off", "Off"), ("play_pause", "Play/Pause")]
    if domain == "vacuum":
        return [("start", "Start"), ("return_to_base", "Return Home"), ("stop", "Stop")]
    return [("on", "On"), ("off", "Off")]


def room_control_legacy_required_actions(ctl: dict | None) -> set[str]:
    domain = room_control_domain_for(ctl)
    if domain == "lock":
        # Preserve the old behavior: locking was quick, unlocking required the entry code.
        return {"unlock"}
    return {action for action, _label in room_control_code_state_choices(ctl)}


def room_control_required_state_map(ctl: dict | None) -> dict[str, bool]:
    choices = {action for action, _label in room_control_code_state_choices(ctl)}
    raw = (ctl or {}).get("codeRequiredStates")
    if isinstance(raw, dict):
        return {action: bool(raw.get(action, False)) for action in choices}
    if isinstance(raw, (list, tuple, set)):
        selected = {str(action).strip().lower() for action in raw}
        return {action: action in selected for action in choices}
    legacy = room_control_legacy_required_actions(ctl)
    return {action: action in legacy for action in choices}


def room_control_action_requires_code(ctl: dict | None, action: str) -> bool:
    if not str((ctl or {}).get("accessCode") or "").strip():
        return False
    action_key = str(action or "").strip().lower()
    if action_key == "toggle":
        action_key = "off" if room_control_active((ctl or {}).get("state"), room_control_domain_for(ctl)) else "on"
    return bool(room_control_required_state_map(ctl).get(action_key, False))


def room_control_has_code_protection(ctl: dict | None) -> bool:
    return bool(str((ctl or {}).get("accessCode") or "").strip()) and any(room_control_required_state_map(ctl).values())


def room_control_next_action(ctl: dict) -> str:
    domain = room_control_domain_for(ctl)
    state = str((ctl or {}).get("state") or "").strip().lower()
    active = room_control_active(state, domain) if state else bool((ctl or {}).get("on"))
    if domain == "lock":
        return "unlock" if state in {"locked", "locking"} or not active else "lock"
    if domain == "cover":
        return "open" if state in {"closed", "closing"} or not active else "close"
    if domain in {"button", "input_button"}:
        return "press"
    if domain in {"scene", "script"}:
        return "run"
    if domain == "media_player":
        return "off" if active else "on"
    if domain == "vacuum":
        return "return_to_base" if active else "start"
    return "off" if active else "on"



def normalize_screen_orientation(value: Any) -> str:
    text = str(value or "upright").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"upside_down", "upsidedown", "flipped", "inverted", "left"}:
        return "upside_down"
    return "upright"


def screen_orientation_label(value: Any) -> str:
    return "Upside Down" if normalize_screen_orientation(value) == "upside_down" else "Upright"


def screen_orientation_to_xrandr(value: Any) -> str:
    # The DSI panel is portrait at the hardware level.  The native UI is
    # landscape, so the two safe 180-degree choices are xrandr right and left.
    return "left" if normalize_screen_orientation(value) == "upside_down" else "right"

def nested_get(data: dict, *keys, default=None):
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur




AUDIO_NUMBER_CONTROL_ORDER: list[tuple[str, str]] = [
    ("gain", "Gain"),
    ("bass", "Bass"),
    ("treble", "Treble"),
    ("music_surround", "Music Surround"),
]
AUDIO_SWITCH_CONTROL_ORDER: list[tuple[str, str]] = [
    ("tv_power", "TV Power"),
    ("projector", "Projector"),
    ("subwoofer", "Sub"),
    ("surround", "Surround Sound"),
]
AUDIO_CONTROL_ORDER: list[tuple[str, str]] = AUDIO_NUMBER_CONTROL_ORDER + AUDIO_SWITCH_CONTROL_ORDER
AUDIO_CONTROL_LABELS = {key: label for key, label in AUDIO_CONTROL_ORDER}
AUDIO_CONTROL_DEFAULTS = {key: True for key, _label in AUDIO_CONTROL_ORDER}
AUDIO_PRESET_ORDER: list[tuple[str, str, str, str]] = [
    ("movie", "Movie", "movie", "warm"),
    ("show", "Show", "show", "purple"),
    ("volume40", "40%", "volume", "normal"),
    ("max", "Max", "max", "danger"),
]
AUDIO_IDLE_SECONDS = 120.0
AUDIO_FAST_NAV_POLL_SECONDS = 2.0
# These Home Assistant media-player states mean the Audio page is no longer
# actively playing. Track their duration directly so a paused player cannot
# remain on-screen forever because of unrelated panel activity.
AUDIO_RETURN_TO_THERMOSTAT_STATES = frozenset({
    "paused",
    "idle",
    "off",
    "standby",
    "unavailable",
    "unknown",
})

# Display sleep is handled only by the native UI layer. Backend polling, HVAC
# runtime protection, Home Assistant sync, and alarm logic continue normally.
SCREEN_SLEEP_IDLE_SECONDS = float(os.environ.get("SMART_THERMOSTAT_SCREEN_SLEEP_SECONDS", "10800"))
DEFAULT_SCREEN_INACTIVITY_MINUTES = max(1, int(round(SCREEN_SLEEP_IDLE_SECONDS / 60.0))) if SCREEN_SLEEP_IDLE_SECONDS > 0 else 180
SCREEN_WAKE_INPUT_BLOCK_SECONDS = float(os.environ.get("SMART_THERMOSTAT_SCREEN_WAKE_BLOCK_SECONDS", "1.0"))
SCREEN_SLEEP_OFF_COMMAND = os.environ.get("SMART_THERMOSTAT_SCREEN_OFF_COMMAND", "xset dpms force off")
SCREEN_SLEEP_ON_COMMAND = os.environ.get("SMART_THERMOSTAT_SCREEN_ON_COMMAND", "xset dpms force on")
SCREEN_BRIGHTNESS_EDGE_ENABLED = str(os.environ.get("SMART_THERMOSTAT_SCREEN_BRIGHTNESS_EDGE", "1")).strip().lower() not in {"0", "false", "no", "off"}
SCREEN_BRIGHTNESS_EDGE_WIDTH_PX = int(os.environ.get("SMART_THERMOSTAT_SCREEN_BRIGHTNESS_EDGE_WIDTH_PX", "72"))
SCREEN_BRIGHTNESS_PIXELS_PER_PERCENT = float(os.environ.get("SMART_THERMOSTAT_SCREEN_BRIGHTNESS_PIXELS_PER_PERCENT", "7.0"))
SCREEN_BRIGHTNESS_MIN_PERCENT = int(os.environ.get("SMART_THERMOSTAT_SCREEN_BRIGHTNESS_MIN_PERCENT", "8"))
SCREEN_BRIGHTNESS_MAX_PERCENT = int(os.environ.get("SMART_THERMOSTAT_SCREEN_BRIGHTNESS_MAX_PERCENT", "100"))
SCREEN_BRIGHTNESS_DEFAULT_PERCENT = int(os.environ.get("SMART_THERMOSTAT_SCREEN_BRIGHTNESS_DEFAULT_PERCENT", "100"))
SCREEN_BRIGHTNESS_APPLY_DELAY_MS = int(os.environ.get("SMART_THERMOSTAT_SCREEN_BRIGHTNESS_APPLY_DELAY_MS", "80"))
SCREEN_BRIGHTNESS_DISPLAY_OUTPUT = os.environ.get("SMART_THERMOSTAT_DISPLAY_OUTPUT", "DSI-1")
SCREEN_BRIGHTNESS_BACKLIGHT_PATH = os.environ.get("SMART_THERMOSTAT_SCREEN_BACKLIGHT_PATH", "").strip()
SCREEN_BRIGHTNESS_COMMAND = os.environ.get("SMART_THERMOSTAT_SCREEN_BRIGHTNESS_COMMAND", "").strip()
ALARM_STATE_POLL_SECONDS = float(os.environ.get("SMART_THERMOSTAT_ALARM_POLL_SECONDS", "30"))

# Local backend polling is adaptive. Fast refresh is reserved for an active
# assistant, a sleeping panel waiting for motion, an armed Sync window, or a Pi
# approaching its thermal safety threshold. Keeping the idle cadence relaxed
# avoids hundreds of short-lived HTTP/request threads per minute.
ASSISTANT_STATUS_ACTIVE_INTERVAL_MS = 300
ASSISTANT_STATUS_IDLE_INTERVAL_MS = 2000
ASSISTANT_STATUS_ERROR_INTERVAL_MS = 5000
SCREEN_MOTION_SLEEP_INTERVAL_MS = 1000
SCREEN_MOTION_AWAKE_INTERVAL_MS = 3000
SCREEN_MOTION_MANUAL_SLEEP_INTERVAL_MS = 5000
SCREEN_MOTION_DISABLED_INTERVAL_MS = 10000
THERMAL_STATUS_NORMAL_INTERVAL_MS = 5000
THERMAL_STATUS_NEAR_INTERVAL_MS = 2000
THERMAL_STATUS_NEAR_MARGIN_C = 5.0


def screen_display_settings(config: dict | None) -> dict:
    """Return normalized display sleep/brightness settings without mutating panel config."""
    cfg = config if isinstance(config, dict) else {}
    display = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}

    def as_minutes(key: str, default: int, low: int = 1, high: int = 720) -> int:
        try:
            value = int(round(float(display.get(key, default))))
        except (TypeError, ValueError):
            value = int(default)
        return int(clamp(value, low, high))

    def as_percent(key: str, default: int) -> int:
        try:
            value = int(round(float(display.get(key, default))))
        except (TypeError, ValueError):
            value = int(default)
        return int(clamp(value, SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))

    def as_bool(key: str, default: bool) -> bool:
        value = display.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value or "").strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled"}:
            return False
        return bool(default)

    def as_time(key: str, default: str) -> str:
        hour, minute = parse_schedule_time_24h(display.get(key, default), *parse_schedule_time_24h(default))
        return f"{hour:02d}:{minute:02d}"

    try:
        step = int(round(float(display.get("timeoutAdjustmentStepMinutes", 1))))
    except (TypeError, ValueError):
        step = 1
    if step not in {1, 5}:
        step = 1

    brightness_rules: list[dict] = []
    raw_rules = display.get("brightnessEntityRules") if isinstance(display.get("brightnessEntityRules"), list) else []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        entity_id = str(raw.get("entityId") or raw.get("entity_id") or "").strip()
        if not entity_id or "." not in entity_id:
            continue
        state = str(raw.get("state") or "on").strip().lower()
        if state not in {"on", "off"}:
            state = "on"
        try:
            brightness = int(round(float(raw.get("brightnessPercent", raw.get("brightness", 40)))))
        except (TypeError, ValueError):
            brightness = 40
        brightness_rules.append({
            "entityId": entity_id,
            "name": str(raw.get("name") or raw.get("friendly_name") or entity_id),
            "domain": str(raw.get("domain") or entity_id.split(".", 1)[0]),
            "state": state,
            "brightnessPercent": int(clamp(brightness, SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT)),
        })

    return {
        "inactivityAutoOffEnabled": as_bool("inactivityAutoOffEnabled", SCREEN_SLEEP_IDLE_SECONDS > 0),
        "inactivityAutoOffMinutes": as_minutes("inactivityAutoOffMinutes", DEFAULT_SCREEN_INACTIVITY_MINUTES),
        "motionAutoSleepEnabled": as_bool("motionAutoSleepEnabled", False),
        "motionAutoSleepMinutes": as_minutes("motionAutoSleepMinutes", 5),
        "motionAutoWakeEnabled": as_bool("motionAutoWakeEnabled", False),
        "timeoutAdjustmentStepMinutes": step,
        "brightnessNormalPercent": as_percent("brightnessNormalPercent", SCREEN_BRIGHTNESS_DEFAULT_PERCENT),
        "brightnessTimeEnabled": as_bool("brightnessTimeEnabled", False),
        "brightnessTimeStart": as_time("brightnessTimeStart", "22:00"),
        "brightnessTimeEnd": as_time("brightnessTimeEnd", "07:00"),
        "brightnessTimePercent": as_percent("brightnessTimePercent", 40),
        "brightnessEntityRules": brightness_rules,
    }


def audio_ui_config(config: dict | None) -> dict:
    cfg = config if isinstance(config, dict) else {}
    audio = cfg.get("audio") if isinstance(cfg.get("audio"), dict) else {}
    enabled = audio.get("enabledControls") if isinstance(audio.get("enabledControls"), dict) else {}
    merged = dict(AUDIO_CONTROL_DEFAULTS)
    for key in merged:
        if key in enabled:
            merged[key] = bool(enabled.get(key))
    return {
        "enabledControls": merged,
        "autoNavigate": bool(audio.get("autoNavigate", False)),
    }


def audio_group_definitions(config: dict | None) -> list[dict]:
    """Return normalized saved Audio-page speaker groups."""
    cfg = config if isinstance(config, dict) else {}
    audio = cfg.get("audio") if isinstance(cfg.get("audio"), dict) else {}
    raw_groups = audio.get("groups") if isinstance(audio.get("groups"), list) else []
    groups: list[dict] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_groups):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        members_raw = raw.get("members") if isinstance(raw.get("members"), list) else []
        members: list[str] = []
        for value in members_raw:
            entity_id = str(value or "").strip()
            if entity_id.startswith("media_player.") and entity_id not in members:
                members.append(entity_id)
        if not name or len(members) < 2:
            continue
        coordinator = str(raw.get("coordinatorId") or "").strip()
        if coordinator not in members:
            coordinator = members[0]
        group_id = str(raw.get("id") or "").strip() or f"audio-group-{index + 1}"
        base_id = group_id
        suffix = 2
        while group_id in seen_ids:
            group_id = f"{base_id}-{suffix}"
            suffix += 1
        seen_ids.add(group_id)
        groups.append({
            "id": group_id,
            "name": name,
            "members": members,
            "coordinatorId": coordinator,
        })
    return groups


def audio_control_enabled(config: dict | None, key: str) -> bool:
    return bool(audio_ui_config(config).get("enabledControls", {}).get(key, True))


def audio_auto_navigate_enabled(config: dict | None) -> bool:
    return bool(audio_ui_config(config).get("autoNavigate", False))


def default_audio_presets() -> dict:
    return {
        "show": {
            "label": "Show Mode",
            "volume": None,
            "numbers": {"gain": 0, "bass": 8, "treble": 8},
            "switches": {"subwoofer": "off", "surround": "on"},
        },
        "volume40": {
            "label": "40% Volume",
            "volume": 40,
            "numbers": {"gain": "max", "bass": "max", "treble": 8},
            "switches": {"subwoofer": "on", "surround": "on"},
        },
        "max": {
            "label": "Max",
            "volume": 100,
            "numbers": {"gain": "max", "bass": "max", "treble": 8},
            "switches": {"subwoofer": "on", "surround": "on"},
        },
        "movie": {
            "label": "Movie Mode",
            "volume": None,
            "numbers": {},
            "switches": {},
        },
    }


def audio_preset_definition(config: dict | None, preset: str) -> dict:
    preset = str(preset or "").strip()
    defaults = default_audio_presets()
    if preset not in defaults:
        return {}
    result = copy.deepcopy(defaults[preset])
    cfg = config if isinstance(config, dict) else {}
    custom = nested_get(cfg, "audio", "presets", preset, default=None)
    if isinstance(custom, dict):
        if "label" in custom and str(custom.get("label") or "").strip():
            result["label"] = str(custom.get("label") or result.get("label"))
        if "volume" in custom:
            result["volume"] = custom.get("volume")
        if isinstance(custom.get("numbers"), dict):
            result["numbers"] = copy.deepcopy(custom.get("numbers") or {})
        if isinstance(custom.get("switches"), dict):
            result["switches"] = copy.deepcopy(custom.get("switches") or {})
    return result


def thermostat_detail_payload(payload: dict | None) -> dict:
    """Return the actual thermostat state from the local API response.

    /api/thermostat/status and /api/thermostat/control return a wrapper with
    metadata plus a nested `thermostat` object. The UI should edit/sync against
    that nested object because it contains persistent settings such as limits,
    away setpoints, pause settings, and the unit name. Keep wrapper-only fields
    like outputs available as extras so older call sites do not lose context.
    """
    if not isinstance(payload, dict):
        return {}
    detail = payload.get("thermostat") if isinstance(payload.get("thermostat"), dict) else None
    if not detail:
        return copy.deepcopy(payload)
    merged = copy.deepcopy(detail)
    for key, value in payload.items():
        if key in {"thermostat", "climate"}:
            continue
        if key not in merged:
            merged[key] = copy.deepcopy(value)
    return merged


def slug_room_key(label: str, rooms: dict) -> str:
    base = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(label or "room")).strip("-")
    base = "-".join(part for part in base.split("-") if part) or "room"
    key = base
    idx = 2
    while key in rooms:
        key = f"{base}-{idx}"
        idx += 1
    return key


def fit_dialog_to_available_screen(dialog: QDialog, margin: int = 0):
    screen = dialog.screen() or QApplication.primaryScreen()
    if not screen:
        return
    geo = screen.availableGeometry()
    margin = max(0, int(margin))
    dialog.resize(max(320, geo.width() - (margin * 2)), max(260, geo.height() - (margin * 2)))
    dialog.move(geo.x() + margin, geo.y() + margin)



class ScheduleClockButton(QPushButton):
    """Compact modern schedule button used in the thermostat header.

    A drawn icon is more reliable than Unicode clock glyphs on the Pi because
    installed fonts vary between images. This keeps the schedule control crisp
    and consistent on the native 10-inch touchscreen.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Schedules")
        self.setAccessibleName("Schedules")
        self.setFixedSize(58, 52)
        self.setFocusPolicy(Qt.NoFocus)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        outer = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        pressed = self.isDown()

        bg = QLinearGradient(outer.topLeft(), outer.bottomRight())
        if pressed:
            bg.setColorAt(0.0, QColor(64, 201, 242, 218))
            bg.setColorAt(1.0, QColor(76, 129, 232, 222))
            border = QColor(171, 245, 255, 236)
            icon = QColor(2, 18, 34, 236)
            accent = QColor(255, 255, 255, 242)
        else:
            bg.setColorAt(0.0, QColor(74, 87, 109, 188))
            bg.setColorAt(1.0, QColor(24, 33, 51, 224))
            border = QColor(130, 229, 255, 120)
            icon = QColor(235, 246, 255, 232)
            accent = QColor(71, 224, 255, 238)

        p.setBrush(QBrush(bg))
        p.setPen(QPen(border, 1.5))
        p.drawRoundedRect(outer, 17, 17)

        halo = QRadialGradient(outer.center(), max(outer.width(), outer.height()) * 0.55)
        halo.setColorAt(0.0, QColor(71, 224, 255, 34 if not pressed else 58))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(outer, halo)

        center = QPointF(outer.center().x() - 2.0, outer.center().y() - 1.0)
        radius = min(outer.width(), outer.height()) * 0.27
        clock_rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(icon, 2.35, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawEllipse(clock_rect)

        tick_pen = QPen(icon, 1.45, Qt.SolidLine, Qt.RoundCap)
        tick_pen.setColor(QColor(icon.red(), icon.green(), icon.blue(), 176))
        p.setPen(tick_pen)
        for angle_deg in (0, 90, 180, 270):
            angle = math.radians(angle_deg - 90)
            outer_pt = QPointF(center.x() + math.cos(angle) * (radius - 3.1), center.y() + math.sin(angle) * (radius - 3.1))
            inner_pt = QPointF(center.x() + math.cos(angle) * (radius - 6.0), center.y() + math.sin(angle) * (radius - 6.0))
            p.drawLine(inner_pt, outer_pt)

        p.setPen(QPen(icon, 2.25, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(center, QPointF(center.x(), center.y() - radius * 0.52))
        p.setPen(QPen(accent, 2.55, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(center, QPointF(center.x() + radius * 0.50, center.y() + radius * 0.18))
        p.setBrush(QBrush(accent))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(center.x() - 2.2, center.y() - 2.2, 4.4, 4.4))

        badge_radius = 8.2
        badge_center = QPointF(outer.right() - 13.2, outer.bottom() - 12.8)
        p.setBrush(QBrush(accent))
        p.setPen(QPen(QColor(255, 255, 255, 216), 1.1))
        p.drawEllipse(QRectF(badge_center.x() - badge_radius, badge_center.y() - badge_radius, badge_radius * 2, badge_radius * 2))
        p.setPen(QPen(QColor(2, 18, 34, 235), 2.0, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(badge_center.x() - 3.5, badge_center.y()), QPointF(badge_center.x() + 3.5, badge_center.y()))
        p.drawLine(QPointF(badge_center.x(), badge_center.y() - 3.5), QPointF(badge_center.x(), badge_center.y() + 3.5))



class HoldLabel(QLabel):
    held = pyqtSignal()

    def __init__(self, text: str = "", hold_ms: int = 700, parent=None):
        super().__init__(text, parent)
        self._hold_fired = False
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(hold_ms)
        self._hold_timer.timeout.connect(self._fire_hold)
        self.setCursor(Qt.PointingHandCursor)

    def _fire_hold(self):
        self._hold_fired = True
        self.held.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._hold_fired = False
            self._hold_timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._hold_timer.isActive():
            self._hold_timer.stop()
        if self._hold_fired:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TouchFriendlySlider(QSlider):
    """Slider that treats the whole widget as the touch target.

    QSlider is precise with a mouse, but on the Raspberry Pi touchscreen the
    effective grab area is too skinny. This subclass lets the user press or
    drag anywhere inside the larger slider widget and maps that point directly
    to the slider value.
    """

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._touch_drag_active = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)

    def _value_from_pos(self, pos) -> int:
        try:
            minimum = int(self.minimum())
            maximum = int(self.maximum())
            span = max(1, maximum - minimum)
            if self.orientation() == Qt.Horizontal:
                width = max(1, self.width() - 1)
                ratio = clamp(float(pos.x()) / float(width), 0.0, 1.0)
            else:
                height = max(1, self.height() - 1)
                ratio = 1.0 - clamp(float(pos.y()) / float(height), 0.0, 1.0)
            if self.invertedAppearance():
                ratio = 1.0 - ratio
            return int(clamp(round(minimum + ratio * span), minimum, maximum))
        except Exception:
            return int(self.value())

    def _set_from_pos(self, pos):
        self.setValue(self._value_from_pos(pos))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._touch_drag_active = True
            self.setSliderDown(True)
            self.sliderPressed.emit()
            self._set_from_pos(event.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._touch_drag_active and (event.buttons() & Qt.LeftButton):
            self._set_from_pos(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._touch_drag_active and event.button() == Qt.LeftButton:
            self._set_from_pos(event.pos())
            self._touch_drag_active = False
            self.setSliderDown(False)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)



class MotionStatusIndicator(QWidget):
    """Vector person/motion indicator for the protected panel information view."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._motion = False
        self._available = True
        self.setFixedSize(104, 104)

    def setMotion(self, motion: bool, available: bool = True):
        next_motion = bool(motion)
        next_available = bool(available)
        if next_motion == self._motion and next_available == self._available:
            return
        self._motion = next_motion
        self._available = next_available
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(5, 5, -5, -5)

        if not self._available:
            accent = QColor(255, 103, 114)
            glow_alpha = 45
        elif self._motion:
            accent = QColor(80, 241, 174)
            glow_alpha = 135
        else:
            accent = QColor(126, 145, 174)
            glow_alpha = 34

        glow = QRadialGradient(r.center(), r.width() * 0.58)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), glow_alpha))
        glow.setColorAt(0.70, QColor(accent.red(), accent.green(), accent.blue(), max(0, glow_alpha // 4)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)

        p.setBrush(QColor(7, 18, 32, 220))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 165 if self._motion else 75), 2))
        p.drawRoundedRect(r, 28, 28)

        p.setPen(QPen(accent, 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx = r.center().x()
        p.drawEllipse(QRectF(cx - 8, r.top() + 17, 16, 16))
        p.drawLine(QPointF(cx, r.top() + 38), QPointF(cx, r.top() + 63))
        p.drawLine(QPointF(cx, r.top() + 45), QPointF(cx - 18, r.top() + 56))
        p.drawLine(QPointF(cx, r.top() + 45), QPointF(cx + 18, r.top() + 56))
        p.drawLine(QPointF(cx, r.top() + 63), QPointF(cx - 15, r.top() + 80))
        p.drawLine(QPointF(cx, r.top() + 63), QPointF(cx + 15, r.top() + 80))

        if self._motion and self._available:
            p.setPen(QPen(QColor(80, 241, 174, 210), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(r.left() + 8, r.top() + 27, 28, 42), 75 * 16, 160 * 16)
            p.drawArc(QRectF(r.right() - 36, r.top() + 27, 28, 42), -55 * 16, 160 * 16)
        p.end()


class CoverArtLabel(QLabel):
    """Rounded album-art card with a clean fallback when no artwork is available."""

    def __init__(self, size: int = 250, parent=None):
        super().__init__(parent)
        self._card_size = int(size)
        self.setFixedSize(self._card_size, self._card_size)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(font(max(28, self._card_size // 4), QFont.Black))
        self.show_fallback("NP")

    def show_fallback(self, text: str = "NP"):
        self.clear()
        self.setText(text or "NP")
        self.setStyleSheet(f"""
            QLabel {{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(73,229,255,0.96),
                    stop:1 rgba(109,77,208,0.96));
                color:rgba(8,26,45,0.92);
                border:1px solid rgba(255,255,255,0.24);
                border-radius:{max(24, self._card_size // 8)}px;
            }}
        """)

    def set_cover_bytes(self, payload: bytes) -> bool:
        image = QImage()
        if not payload or not image.loadFromData(payload):
            return False
        self.set_cover_pixmap(QPixmap.fromImage(image))
        return True

    def set_cover_pixmap(self, pixmap: QPixmap):
        if pixmap.isNull():
            self.show_fallback("NP")
            return
        size = self.size()
        scaled = pixmap.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (scaled.width() - size.width()) // 2)
        y = max(0, (scaled.height() - size.height()) // 2)
        cropped = scaled.copy(x, y, size.width(), size.height())
        rounded = QPixmap(size)
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        radius = max(24, self._card_size // 8)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size.width(), size.height()), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, cropped)
        painter.setClipping(False)
        painter.setPen(QPen(QColor(255, 255, 255, 64), 2))
        painter.drawRoundedRect(QRectF(1, 1, size.width() - 2, size.height() - 2), radius, radius)
        painter.end()
        self.setText("")
        self.setStyleSheet("background:transparent; border:0;")
        self.setPixmap(rounded)


class HoldRoundButton(RoundButton):
    held = pyqtSignal()

    def __init__(self, *args, hold_ms: int = 700, **kwargs):
        super().__init__(*args, **kwargs)
        self._hold_fired = False
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(hold_ms)
        self._hold_timer.timeout.connect(self._fire_hold)

    def _fire_hold(self):
        self._hold_fired = True
        self.held.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._hold_fired = False
            self._hold_timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._hold_timer.isActive():
            self._hold_timer.stop()
        if self._hold_fired:
            self.setDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ModernAudioButton(QAbstractButton):
    """Smooth vector-icon audio tile used for presets and assignable controls."""
    held = pyqtSignal()

    def __init__(self, icon_key: str, label: str, *, active: bool = False, kind: str = "normal", min_h: int = 76, hold_ms: int = 700, holdable: bool = False, parent=None):
        super().__init__(parent)
        self.icon_key = str(icon_key or "")
        self.label_text = str(label or "")
        self.status_text = ""
        self.active = bool(active)
        self.kind = str(kind or "normal")
        self.holdable = bool(holdable)
        self._hold_fired = False
        self._press_feedback_active = False
        self._press_feedback_timer = QTimer(self)
        self._press_feedback_timer.setSingleShot(True)
        self._press_feedback_timer.setInterval(140)
        self._press_feedback_timer.timeout.connect(self._clear_press_feedback)
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(max(250, int(hold_ms)))
        self._hold_timer.timeout.connect(self._fire_hold)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(int(min_h))
        self.setMinimumWidth(108)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFont(font(10, QFont.Black))

    def setActive(self, active: bool):
        self.active = bool(active)
        self.update()

    def setStatus(self, status: str):
        self.status_text = str(status or "")
        self.update()

    def setLabel(self, label: str):
        self.label_text = str(label or "")
        self.update()

    def _fire_hold(self):
        if not self.holdable:
            return
        self._hold_fired = True
        self.held.emit()

    def _clear_press_feedback(self):
        self._press_feedback_active = False
        self.update()

    def _show_press_feedback(self):
        self._press_feedback_active = True
        self._press_feedback_timer.start()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.holdable:
            self._hold_fired = False
            self._hold_timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._hold_timer.isActive():
            self._hold_timer.stop()
        if self._hold_fired:
            self.setDown(False)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.setDown(False)
            self._show_press_feedback()
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _palette(self) -> tuple[QColor, QColor, QColor, QColor, QColor]:
        if self.kind == "danger":
            icon = QColor(255, 116, 120)
            bg1 = QColor(72, 55, 65, 188)
            bg2 = QColor(32, 32, 44, 218)
            border = QColor(255, 116, 120, 82)
        elif self.kind == "purple":
            icon = QColor(211, 154, 255)
            bg1 = QColor(68, 63, 93, 190)
            bg2 = QColor(31, 34, 51, 218)
            border = QColor(211, 154, 255, 82)
        elif self.kind == "warm":
            icon = QColor(255, 218, 86)
            bg1 = QColor(69, 73, 73, 190)
            bg2 = QColor(31, 36, 49, 218)
            border = QColor(255, 218, 86, 82)
        else:
            icon = QColor(80, 232, 255)
            bg1 = QColor(67, 83, 101, 186)
            bg2 = QColor(26, 34, 51, 220)
            border = QColor(80, 232, 255, 70)
        if self.active:
            bg1 = QColor(42, 126, 151, 220)
            bg2 = QColor(39, 48, 86, 230)
            border = QColor(icon.red(), icon.green(), icon.blue(), 160)
        text = QColor(245, 248, 255)
        return icon, bg1, bg2, border, text

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        pressed = self.isDown() or self._press_feedback_active
        if pressed:
            rect = rect.adjusted(4, 5, -4, -3)
        icon_col, bg1, bg2, border, text_col = self._palette()
        if pressed:
            bg1 = QColor(max(0, bg1.red() - 34), max(0, bg1.green() - 34), max(0, bg1.blue() - 34), 220)
            bg2 = QColor(max(0, bg2.red() - 22), max(0, bg2.green() - 22), max(0, bg2.blue() - 22), 232)
            border = QColor(icon_col.red(), icon_col.green(), icon_col.blue(), 58)
            icon_col = QColor(icon_col.red(), icon_col.green(), icon_col.blue(), 175)
            text_col = QColor(text_col.red(), text_col.green(), text_col.blue(), 180)

        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        g.setColorAt(0.0, bg1)
        g.setColorAt(1.0, bg2)
        p.setBrush(QBrush(g))
        p.setPen(QPen(border, 1.35))
        p.drawRoundedRect(rect, 20, 20)

        if self.active:
            glow = QRadialGradient(rect.center(), max(rect.width(), rect.height()) * 0.72)
            glow.setColorAt(0.0, QColor(icon_col.red(), icon_col.green(), icon_col.blue(), 44))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(rect, glow)

        icon_size = min(36.0, max(24.0, rect.height() * 0.34))
        icon_rect = QRectF(rect.center().x() - icon_size / 2, rect.top() + 12, icon_size, icon_size)
        self._draw_icon(p, icon_rect, icon_col)

        p.setPen(text_col)
        p.setFont(font(9, QFont.Black))
        label_y = icon_rect.bottom() + 8
        label_rect = QRectF(rect.left() + 8, label_y, rect.width() - 16, 18)
        p.drawText(label_rect, Qt.AlignCenter, self.label_text)
        if self.status_text:
            status_col = QColor(icon_col.red(), icon_col.green(), icon_col.blue(), 226) if self.active else QColor(210, 220, 238, 190)
            p.setPen(status_col)
            p.setFont(font(7, QFont.Black, 18))
            status_rect = QRectF(rect.left() + 8, label_y + 18, rect.width() - 16, 15)
            p.drawText(status_rect, Qt.AlignCenter, self.status_text.upper())

    def _draw_icon(self, p: QPainter, r: QRectF, c: QColor):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(c, max(2.0, r.width() / 14.0), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        key = self.icon_key
        x, y, w, h = r.x(), r.y(), r.width(), r.height()
        cx, cy = r.center().x(), r.center().y()

        if key == "movie":
            body = QRectF(x + w * 0.16, y + h * 0.34, w * 0.52, h * 0.36)
            p.drawRoundedRect(body, w * 0.08, w * 0.08)
            p.drawEllipse(QRectF(x + w * 0.22, y + h * 0.16, w * 0.18, h * 0.18))
            p.drawEllipse(QRectF(x + w * 0.44, y + h * 0.15, w * 0.2, h * 0.2))
            path = QPainterPath()
            path.moveTo(x + w * 0.70, y + h * 0.43)
            path.lineTo(x + w * 0.90, y + h * 0.32)
            path.lineTo(x + w * 0.90, y + h * 0.73)
            path.lineTo(x + w * 0.70, y + h * 0.62)
            path.closeSubpath()
            p.drawPath(path)
        elif key == "show":
            mic = QRectF(cx - w * 0.14, y + h * 0.12, w * 0.28, h * 0.42)
            p.drawRoundedRect(mic, w * 0.13, w * 0.13)
            p.drawArc(QRectF(cx - w * 0.28, y + h * 0.34, w * 0.56, h * 0.42), 200 * 16, 140 * 16)
            p.drawLine(QPointF(cx, y + h * 0.72), QPointF(cx, y + h * 0.88))
            p.drawLine(QPointF(cx - w * 0.19, y + h * 0.88), QPointF(cx + w * 0.19, y + h * 0.88))
        elif key in {"volume", "max"}:
            path = QPainterPath()
            path.moveTo(x + w * 0.14, y + h * 0.43)
            path.lineTo(x + w * 0.32, y + h * 0.43)
            path.lineTo(x + w * 0.50, y + h * 0.25)
            path.lineTo(x + w * 0.50, y + h * 0.75)
            path.lineTo(x + w * 0.32, y + h * 0.57)
            path.lineTo(x + w * 0.14, y + h * 0.57)
            path.closeSubpath()
            p.drawPath(path)
            p.drawArc(QRectF(x + w * 0.42, y + h * 0.28, w * 0.32, h * 0.44), -45 * 16, 90 * 16)
            p.drawArc(QRectF(x + w * 0.49, y + h * 0.16, w * 0.40, h * 0.68), -45 * 16, 90 * 16)
            if key == "max":
                bolt = QPainterPath()
                bolt.moveTo(x + w * 0.68, y + h * 0.08)
                bolt.lineTo(x + w * 0.58, y + h * 0.43)
                bolt.lineTo(x + w * 0.73, y + h * 0.43)
                bolt.lineTo(x + w * 0.62, y + h * 0.90)
                p.drawPath(bolt)
        elif key == "sub":
            box = QRectF(x + w * 0.23, y + h * 0.12, w * 0.54, h * 0.76)
            p.drawRoundedRect(box, w * 0.12, w * 0.12)
            p.drawEllipse(QRectF(cx - w * 0.18, cy - w * 0.18, w * 0.36, w * 0.36))
            p.drawEllipse(QRectF(cx - w * 0.055, cy - w * 0.055, w * 0.11, w * 0.11))
            p.drawLine(QPointF(x + w * 0.37, y + h * 0.22), QPointF(x + w * 0.63, y + h * 0.22))
        elif key == "surround":
            pts = [
                QPointF(cx, y + h * 0.18),
                QPointF(x + w * 0.24, y + h * 0.68),
                QPointF(x + w * 0.76, y + h * 0.68),
            ]
            p.drawLine(pts[0], pts[1])
            p.drawLine(pts[0], pts[2])
            p.drawLine(pts[1], pts[2])
            p.setBrush(QBrush(QColor(c.red(), c.green(), c.blue(), 38)))
            for pt in pts:
                p.drawEllipse(QRectF(pt.x() - w * 0.085, pt.y() - w * 0.085, w * 0.17, w * 0.17))
            p.setBrush(Qt.NoBrush)
        elif key == "tv":
            screen = QRectF(x + w * 0.12, y + h * 0.20, w * 0.76, h * 0.52)
            p.drawRoundedRect(screen, w * 0.08, w * 0.08)
            p.drawLine(QPointF(cx, y + h * 0.72), QPointF(cx, y + h * 0.84))
            p.drawLine(QPointF(x + w * 0.34, y + h * 0.86), QPointF(x + w * 0.66, y + h * 0.86))
            power = QRectF(cx - w * 0.13, y + h * 0.34, w * 0.26, h * 0.26)
            p.drawArc(power, 35 * 16, 290 * 16)
            p.drawLine(QPointF(cx, y + h * 0.30), QPointF(cx, y + h * 0.45))
        elif key == "projector":
            body = QRectF(x + w * 0.12, y + h * 0.34, w * 0.66, h * 0.34)
            p.drawRoundedRect(body, w * 0.08, w * 0.08)
            p.drawEllipse(QRectF(x + w * 0.56, y + h * 0.39, w * 0.17, h * 0.17))
            p.drawLine(QPointF(x + w * 0.24, y + h * 0.72), QPointF(x + w * 0.18, y + h * 0.88))
            p.drawLine(QPointF(x + w * 0.58, y + h * 0.72), QPointF(x + w * 0.68, y + h * 0.88))
            beam = QPainterPath()
            beam.moveTo(x + w * 0.81, y + h * 0.42)
            beam.lineTo(x + w * 0.96, y + h * 0.30)
            beam.moveTo(x + w * 0.81, y + h * 0.60)
            beam.lineTo(x + w * 0.96, y + h * 0.72)
            p.drawPath(beam)
        else:
            p.drawEllipse(r.adjusted(w * 0.18, h * 0.18, -w * 0.18, -h * 0.18))
        p.restore()



class StatusToast(GlassPanel):
    def __init__(self, parent=None):
        super().__init__(parent, radius=18, strong=True)
        self.label = QLabel("", self)
        self.label.setStyleSheet("color:#f6f8ff; font-weight:900; padding:10px 16px;")
        self.label.setFont(font(12))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.addWidget(self.label)
        self.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def show_message(self, text: str, ms: int = 2800):
        parent = self.parent()
        if parent is not None and getattr(parent, "_thermal_protection_active", False):
            self.hide()
            return
        self.label.setText(text)
        self.adjustSize()
        if self.parent():
            self.move((self.parent().width() - self.width()) // 2, self.parent().height() - self.height() - 28)
        self.show()
        self.raise_()
        self.timer.start(ms)


class Page(QWidget):
    requestToast = pyqtSignal(str)
    requestAssign = pyqtSignal(str, object, str)  # domain group, object, target kind
    configChanged = pyqtSignal()
    asyncCompleted = pyqtSignal(object)

    def __init__(self, app_state: "AppState", parent=None):
        super().__init__(parent)
        self.s = app_state
        self.config: dict = {}
        self.thermostat: dict = {}
        self._async_jobs: dict[str, tuple[Callable | None, Callable | None]] = {}
        self.asyncCompleted.connect(self._handle_async_completed)

    def sync(self, config: dict, thermostat: dict):
        self.config = config or {}
        self.thermostat = thermostat or {}

    def run_async(self, name: str, worker: Callable[[], Any], on_success: Callable[[Any], None] | None = None, on_error: Callable[[str], None] | None = None):
        """Run slow API/Home Assistant work off the Qt UI thread.

        Touch feedback and local state updates should happen before this is
        called. The callback is delivered back through a Qt signal so UI changes
        still run on the main thread.
        """
        job_id = f"{name}-{time.monotonic_ns()}"
        self._async_jobs[job_id] = (on_success, on_error)

        def target():
            try:
                result = worker()
                self.asyncCompleted.emit({"id": job_id, "result": result, "error": None})
            except Exception as exc:
                self.asyncCompleted.emit({"id": job_id, "result": None, "error": str(exc)})

        threading.Thread(target=target, name=f"ui-{name}", daemon=True).start()

    def _handle_async_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        callbacks = self._async_jobs.pop(str(data.get("id") or ""), None)
        if not callbacks:
            return
        window = self.window()
        if window is not None and getattr(window, "_thermal_protection_active", False):
            return
        on_success, on_error = callbacks
        if data.get("error"):
            if on_error:
                on_error(str(data.get("error")))
            return
        if on_success:
            on_success(data.get("result"))

    def poll(self):
        pass


class ScreenLockButton(QAbstractButton):
    """Top-left control lock pill for the wall-panel guest-safe mode."""
    longPressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.locked = False
        self.secure_locked = False
        self._press_inside = False
        self._long_press_triggered = False
        self._flash_phase: str | None = None
        self._flash_step = 0
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(700)
        self._hold_timer.timeout.connect(self._trigger_long_press)
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(500)
        self._flash_timer.timeout.connect(self._advance_confirmation_flash)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(180, 48)
        self.setFont(font(9, QFont.Black, 18))

    def setLocked(self, locked: bool, secure: bool = False):
        self.locked = bool(locked)
        self.secure_locked = bool(self.locked and secure)
        self.setToolTip(
            "Security locked: alarm control only; tap to unlock with the Settings code"
            if self.secure_locked
            else (
                "Locked: temperature and alarm controls only; tap to unlock with the Alarm code"
                if self.locked
                else "Tap for guest lock, or hold 700 ms for the security lock"
            )
        )
        self.update()

    def begin_press(self):
        self._press_inside = True
        self._long_press_triggered = False
        self.setDown(True)
        self.update()
        self._hold_timer.start()

    def update_press(self, inside: bool):
        self._press_inside = bool(inside)
        self.setDown(self._press_inside)
        if not self._press_inside:
            self._hold_timer.stop()
        self.update()

    def end_press(self, inside: bool, *, emit_click: bool = True) -> bool:
        self._hold_timer.stop()
        self._press_inside = False
        self.setDown(False)
        self.update()
        should_click = bool(inside and not self._long_press_triggered)
        if should_click and emit_click:
            self.clicked.emit()
        self._long_press_triggered = False
        return should_click

    def cancel_press(self):
        self._hold_timer.stop()
        self._press_inside = False
        self._long_press_triggered = False
        self.setDown(False)
        self.update()

    def _trigger_long_press(self):
        if not self._press_inside or not self.isEnabled():
            return
        self._long_press_triggered = True
        self.longPressed.emit()

    def start_security_confirmation_flash(self):
        """Show three red/green confirmation cycles at 500 ms per color."""
        self._flash_timer.stop()
        self._flash_step = 0
        self._flash_phase = "red"
        self.update()
        self._flash_timer.start()

    def _advance_confirmation_flash(self):
        self._flash_step += 1
        if self._flash_step >= 6:
            self._flash_timer.stop()
            self._flash_phase = None
        else:
            self._flash_phase = "green" if self._flash_step % 2 else "red"
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.begin_press()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._hold_timer.isActive() or self._press_inside or self._long_press_triggered:
            self.update_press(self.rect().contains(event.pos()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and (self._press_inside or self._long_press_triggered or self._hold_timer.isActive()):
            self.end_press(self.rect().contains(event.pos()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        g = QLinearGradient(r.topLeft(), r.bottomRight())
        flash_red = self._flash_phase == "red"
        flash_green = self._flash_phase == "green"
        if flash_red:
            g.setColorAt(0.0, QColor(180, 28, 53, 248))
            g.setColorAt(0.58, QColor(105, 14, 34, 242))
            g.setColorAt(1.0, QColor(39, 8, 19, 246))
            border = QColor(255, 112, 139, 230)
            icon_bg = QColor(255, 82, 118, 95)
            icon_fg = QColor(255, 235, 241, 245)
            txt = QColor(255, 242, 246)
            label = "SECURE LOCK"
        elif flash_green:
            g.setColorAt(0.0, QColor(23, 145, 103, 248))
            g.setColorAt(0.58, QColor(12, 91, 77, 242))
            g.setColorAt(1.0, QColor(7, 41, 48, 246))
            border = QColor(108, 255, 205, 230)
            icon_bg = QColor(84, 255, 196, 92)
            icon_fg = QColor(224, 255, 246, 245)
            txt = QColor(230, 255, 249)
            label = "SECURE LOCK"
        elif self.locked:
            g.setColorAt(0.0, QColor(92, 22, 39, 238))
            g.setColorAt(0.58, QColor(51, 16, 29, 230))
            g.setColorAt(1.0, QColor(18, 10, 18, 238))
            border = QColor(255, 82, 118, 175)
            icon_bg = QColor(255, 74, 111, 62)
            icon_fg = QColor(255, 210, 221, 230)
            txt = QColor(255, 230, 236)
            label = "SECURE LOCK" if self.secure_locked else "LOCKED"
        else:
            g.setColorAt(0.0, QColor(19, 82, 74, 232))
            g.setColorAt(0.55, QColor(14, 57, 64, 226))
            g.setColorAt(1.0, QColor(13, 28, 43, 236))
            border = QColor(88, 255, 206, 150)
            icon_bg = QColor(84, 255, 196, 50)
            icon_fg = QColor(195, 255, 240, 228)
            txt = QColor(216, 255, 247)
            label = "UNLOCKED"

        p.setBrush(QBrush(g))
        p.setPen(QPen(border, 1.45))
        p.drawRoundedRect(r, 22, 22)

        glow = QRadialGradient(QPointF(27, r.center().y()), 36)
        glow.setColorAt(0.0, QColor(icon_fg.red(), icon_fg.green(), icon_fg.blue(), 70))
        glow.setColorAt(0.72, QColor(icon_fg.red(), icon_fg.green(), icon_fg.blue(), 15))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, 62, self.height()), glow)

        icon = QRectF(11, 9, 30, 30)
        p.setBrush(icon_bg)
        p.setPen(QPen(QColor(icon_fg.red(), icon_fg.green(), icon_fg.blue(), 105), 1.1))
        p.drawEllipse(icon)

        cx = icon.center().x()
        cy = icon.center().y()
        p.setPen(QPen(icon_fg, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        if self.locked or flash_red or flash_green:
            p.drawArc(QRectF(cx - 7, cy - 11, 14, 15), 0, 180 * 16)
        else:
            p.drawArc(QRectF(cx - 10, cy - 11, 14, 15), 18 * 16, 155 * 16)
        body = QRectF(cx - 9, cy - 2, 18, 13)
        p.setBrush(QColor(icon_fg.red(), icon_fg.green(), icon_fg.blue(), 35))
        p.drawRoundedRect(body, 4, 4)
        p.setBrush(icon_fg)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(cx - 1.8, cy + 3, 3.6, 3.6))

        p.setFont(self.font())
        p.setPen(txt)
        p.drawText(QRectF(48, 0, r.width() - 54, r.height()), Qt.AlignVCenter | Qt.AlignLeft, label)


class SleepButton(QAbstractButton):
    """Floating bottom-right display sleep control."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(58, 58)
        self.setToolTip("Sleep display")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)

        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor(38, 58, 91, 235))
        g.setColorAt(0.54, QColor(20, 31, 55, 230))
        g.setColorAt(1.0, QColor(9, 14, 27, 238))
        p.setBrush(QBrush(g))
        p.setPen(QPen(QColor(126, 171, 255, 118), 1.35))
        p.drawRoundedRect(r, 20, 20)

        glow = QRadialGradient(r.center(), 34)
        glow.setColorAt(0.0, QColor(120, 170, 255, 72))
        glow.setColorAt(0.68, QColor(80, 140, 255, 18))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)

        # Sleek crescent-moon icon. Draw a light circle, then cut it with the
        # button background so it reads as sleep without using a plain letter.
        cx = r.center().x() - 1
        cy = r.center().y() - 1
        moon_color = QColor(235, 244, 255, 232)
        cut_color = QColor(18, 29, 52, 255)
        p.setPen(Qt.NoPen)
        p.setBrush(moon_color)
        p.drawEllipse(QRectF(cx - 12, cy - 14, 28, 28))
        p.setBrush(cut_color)
        p.drawEllipse(QRectF(cx - 3, cy - 17, 28, 31))

        p.setBrush(QColor(114, 170, 255, 190))
        p.drawEllipse(QRectF(cx + 13, cy - 16, 3.6, 3.6))
        p.drawEllipse(QRectF(cx + 18, cy + 1, 2.8, 2.8))


class SyncButton(QAbstractButton):
    """Floating thermostat sync control shown above the sleep button."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(58, 58)
        self.setToolTip("Sync thermostat changes")
        self._active = False
        self._remaining = 0

    def setActive(self, active: bool, remaining: int = 0):
        self._active = bool(active)
        self._remaining = max(0, int(remaining or 0))
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        active = bool(self._active)

        g = QLinearGradient(r.topLeft(), r.bottomRight())
        if active:
            g.setColorAt(0.0, QColor(68, 235, 255, 238))
            g.setColorAt(0.58, QColor(62, 145, 255, 235))
            g.setColorAt(1.0, QColor(41, 70, 173, 238))
            border = QColor(222, 253, 255, 230)
            icon = QColor(4, 17, 34, 238)
            text = QColor(3, 17, 31, 230)
        else:
            g.setColorAt(0.0, QColor(42, 63, 95, 230))
            g.setColorAt(0.56, QColor(19, 31, 55, 228))
            g.setColorAt(1.0, QColor(9, 14, 27, 238))
            border = QColor(108, 221, 255, 118)
            icon = QColor(232, 246, 255, 232)
            text = QColor(162, 220, 238, 218)

        p.setBrush(QBrush(g))
        p.setPen(QPen(border, 1.35))
        p.drawRoundedRect(r, 20, 20)

        glow = QRadialGradient(r.center(), 35)
        glow.setColorAt(0.0, QColor(70, 230, 255, 84 if active else 44))
        glow.setColorAt(0.70, QColor(64, 150, 255, 28 if active else 12))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)

        cx = r.center().x()
        cy = r.center().y() - 5
        p.setPen(QPen(icon, 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        # Two clean circular arrows; drawn manually so no icon font is required.
        p.drawArc(QRectF(cx - 17, cy - 14, 26, 26), 35 * 16, 245 * 16)
        p.drawArc(QRectF(cx - 9, cy - 12, 26, 26), 215 * 16, 245 * 16)
        p.setBrush(icon)
        p.setPen(Qt.NoPen)
        path1 = QPainterPath()
        path1.moveTo(cx + 9, cy - 14)
        path1.lineTo(cx + 16, cy - 13)
        path1.lineTo(cx + 12, cy - 7)
        path1.closeSubpath()
        p.drawPath(path1)
        path2 = QPainterPath()
        path2.moveTo(cx - 9, cy + 14)
        path2.lineTo(cx - 16, cy + 13)
        path2.lineTo(cx - 12, cy + 7)
        path2.closeSubpath()
        p.drawPath(path2)

        p.setFont(font(6, QFont.Black, 10))
        p.setPen(text)
        label = f"{int(self._remaining)}" if active and self._remaining > 0 else "SYNC"
        p.drawText(QRectF(3, r.bottom() - 16, r.width() - 6, 14), Qt.AlignCenter, label)


class DeviceInternetButton(QAbstractButton):
    """Main-screen device internet toggle drawn as a small tablet/iPad."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(58, 58)
        self._blocked = False
        self._mixed = False
        self._busy = False
        self.setToolTip("Disable internet for selected devices")

    def setState(self, blocked: bool, *, mixed: bool = False, busy: bool = False):
        self._blocked = bool(blocked)
        self._mixed = bool(mixed)
        self._busy = bool(busy)
        if self._busy:
            tip = "Changing internet access for selected devices…"
        elif self._mixed:
            tip = "Selected devices have mixed internet states; tap to disable internet for all"
        elif self._blocked:
            tip = "Internet disabled for selected devices; tap to restore"
        else:
            tip = "Internet enabled for selected devices; tap to disable"
        self.setToolTip(tip)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)

        g = QLinearGradient(r.topLeft(), r.bottomRight())
        if self._busy:
            g.setColorAt(0.0, QColor(84, 98, 125, 238))
            g.setColorAt(0.58, QColor(45, 58, 83, 235))
            g.setColorAt(1.0, QColor(18, 27, 45, 240))
            border = QColor(196, 216, 255, 165)
            screen = QColor(181, 204, 236, 185)
        elif self._mixed:
            g.setColorAt(0.0, QColor(157, 116, 35, 240))
            g.setColorAt(0.58, QColor(92, 65, 24, 236))
            g.setColorAt(1.0, QColor(36, 28, 20, 242))
            border = QColor(255, 215, 124, 215)
            screen = QColor(255, 225, 159, 235)
        elif self._blocked:
            g.setColorAt(0.0, QColor(151, 39, 59, 242))
            g.setColorAt(0.58, QColor(87, 24, 42, 238))
            g.setColorAt(1.0, QColor(34, 14, 27, 242))
            border = QColor(255, 137, 156, 220)
            screen = QColor(255, 217, 225, 235)
        else:
            g.setColorAt(0.0, QColor(31, 91, 100, 236))
            g.setColorAt(0.58, QColor(18, 54, 68, 232))
            g.setColorAt(1.0, QColor(10, 22, 38, 240))
            border = QColor(91, 241, 225, 165)
            screen = QColor(211, 255, 248, 235)

        p.setBrush(QBrush(g))
        p.setPen(QPen(border, 1.35))
        p.drawRoundedRect(r, 20, 20)

        glow = QRadialGradient(r.center(), 34)
        glow.setColorAt(0.0, QColor(border.red(), border.green(), border.blue(), 58))
        glow.setColorAt(0.72, QColor(border.red(), border.green(), border.blue(), 12))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)

        # Tablet/iPad silhouette. Keep the icon slightly high so a live network
        # status can sit directly beneath it without changing the floating control size.
        tablet = QRectF(r.center().x() - 11.5, r.top() + 5.0, 23.0, 31.0)
        p.setPen(QPen(screen, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor(screen.red(), screen.green(), screen.blue(), 24))
        p.drawRoundedRect(tablet, 4.0, 4.0)
        display = tablet.adjusted(3.6, 3.8, -3.6, -5.4)
        p.setPen(QPen(QColor(screen.red(), screen.green(), screen.blue(), 145), 1.0))
        p.setBrush(QColor(6, 14, 26, 135))
        p.drawRoundedRect(display, 2.2, 2.2)
        p.setPen(Qt.NoPen)
        p.setBrush(screen)
        p.drawEllipse(QRectF(tablet.center().x() - 1.3, tablet.bottom() - 4.0, 2.6, 2.6))

        # Small Wi-Fi mark on the tablet screen makes the function readable.
        cx = display.center().x()
        cy = display.center().y() + 1.5
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(screen, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawArc(QRectF(cx - 6.2, cy - 6.3, 12.4, 8.8), 35 * 16, 110 * 16)
        p.drawArc(QRectF(cx - 4.0, cy - 3.4, 8.0, 5.7), 35 * 16, 110 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(screen)
        p.drawEllipse(QRectF(cx - 1.15, cy + 1.8, 2.3, 2.3))

        if self._blocked:
            p.setPen(QPen(QColor(255, 235, 239, 245), 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(tablet.left() + 2, tablet.bottom() - 2), QPointF(tablet.right() - 2, tablet.top() + 2))
        elif self._mixed:
            p.setFont(font(8, QFont.Black))
            p.setPen(screen)
            p.drawText(QRectF(display.left(), display.top() - 1, display.width(), display.height()), Qt.AlignCenter, "?")

        # The button's active state means the selected tablets are intentionally
        # blocked. Home Assistant switch OFF = blocked/offline; switch ON = online.
        if self._busy:
            status_text = "..."
            status_color = QColor(210, 225, 247, 230)
        elif self._mixed:
            status_text = "MIXED"
            status_color = QColor(255, 215, 124, 245)
        elif self._blocked:
            status_text = "OFFLINE"
            status_color = QColor(255, 105, 119, 255)
        else:
            status_text = "ONLINE"
            status_color = QColor(100, 255, 157, 255)
        p.setFont(font(6, QFont.Black, 8))
        p.setPen(status_color)
        p.drawText(QRectF(2, r.bottom() - 16, r.width() - 4, 13), Qt.AlignCenter, status_text)


class IntimacyHoldButton(QAbstractButton):
    """Small six-hour configurable comfort override shown above Thermostat Sync."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(54, 54)
        self._target_temp = 66
        self.setToolTip("Intimacy hold: 66°F for 6 hours")
        self._active = False
        self._remaining = 0

    def setTargetTemp(self, target_temp: float | int):
        try:
            self._target_temp = int(clamp(round(float(target_temp)), 40, 100))
        except (TypeError, ValueError):
            self._target_temp = 66
        self.setToolTip(f"Intimacy hold: {self._target_temp}°F for 6 hours")

    def setActive(self, active: bool, remaining: int = 0):
        self._active = bool(active)
        self._remaining = max(0, int(remaining or 0))
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        active = bool(self._active)

        g = QLinearGradient(r.topLeft(), r.bottomRight())
        if active:
            g.setColorAt(0.0, QColor(255, 129, 177, 242))
            g.setColorAt(0.55, QColor(178, 78, 157, 238))
            g.setColorAt(1.0, QColor(83, 45, 130, 242))
            border = QColor(255, 227, 241, 225)
            icon = QColor(255, 249, 252, 242)
            text = QColor(255, 250, 253, 238)
        else:
            g.setColorAt(0.0, QColor(57, 52, 78, 232))
            g.setColorAt(0.56, QColor(27, 27, 47, 232))
            g.setColorAt(1.0, QColor(10, 14, 28, 240))
            border = QColor(229, 151, 201, 120)
            icon = QColor(246, 226, 240, 230)
            text = QColor(219, 174, 207, 220)

        p.setBrush(QBrush(g))
        p.setPen(QPen(border, 1.3))
        p.drawRoundedRect(r, 18, 18)

        glow = QRadialGradient(r.center(), 31)
        glow.setColorAt(0.0, QColor(255, 126, 188, 80 if active else 28))
        glow.setColorAt(0.72, QColor(180, 83, 190, 25 if active else 8))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)

        # Two abstract profiles leaning toward one another with a small heart.
        # It reads as intimacy/connection without using explicit imagery.
        cx = r.center().x()
        cy = r.center().y() - 6
        p.setPen(QPen(icon, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(cx - 15, cy - 12, 11, 11))
        p.drawEllipse(QRectF(cx + 4, cy - 12, 11, 11))
        p.drawArc(QRectF(cx - 18, cy - 3, 19, 16), 25 * 16, 130 * 16)
        p.drawArc(QRectF(cx - 1, cy - 3, 19, 16), 25 * 16, 130 * 16)

        p.setPen(Qt.NoPen)
        p.setBrush(icon)
        heart = QPainterPath()
        heart.moveTo(cx, cy + 6)
        heart.cubicTo(cx - 7, cy + 1, cx - 8, cy - 5, cx - 3, cy - 6)
        heart.cubicTo(cx, cy - 7, cx + 1, cy - 4, cx, cy - 2)
        heart.cubicTo(cx - 1, cy - 4, cx, cy - 7, cx + 3, cy - 6)
        heart.cubicTo(cx + 8, cy - 5, cx + 7, cy + 1, cx, cy + 6)
        p.drawPath(heart)

        if active and self._remaining > 0:
            hours = self._remaining // 3600
            minutes = (self._remaining % 3600) // 60
            label = f"{hours}h{minutes:02d}" if hours else f"{max(1, minutes)}m"
        else:
            label = "OFF"
        p.setFont(font(6, QFont.Black, 10))
        p.setPen(text)
        p.drawText(QRectF(2, r.bottom() - 14, r.width() - 4, 12), Qt.AlignCenter, label)


class ScreenSleepOverlay(QWidget):
    """Black touch shield shown while the appliance display is asleep/waking."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setMouseTracking(True)
        self.setStyleSheet("background:#000000;")
        self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))


class ThermalProtectionOverlay(QWidget):
    """Static black emergency screen with one high-contrast warning."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setMouseTracking(True)
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        painter.setPen(QColor(255, 35, 35))
        painter.setFont(font(44, QFont.Black, 8))
        painter.drawText(self.rect(), Qt.AlignCenter, "UNIT OVERHEATING")




class AssistantOverlay(QWidget):
    """Full-screen cinematic JARVIS hologram driven by backend assistant state.

    The display is rendered entirely with lightweight QPainter geometry.  That
    keeps the Raspberry Pi free of video/shader dependencies while still giving
    the speaking state a dense, movie-style holographic AI core that visibly
    reacts to speech.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setMouseTracking(True)
        self.stage = "idle"
        self.command = ""
        self.response = ""
        self.status_text = "Standing by."
        self.error_text = ""
        self.request_id = 0
        self.phase = 0.0
        self._active = False
        self._status_signature: tuple[object, ...] | None = None

        # Deterministic particle field.  Keeping all pseudo-random geometry
        # precomputed prevents per-frame allocations and RNG churn on the Pi.
        self._particle_specs: list[tuple[float, float, float, float, float, float, int]] = []
        seed = 0x4A415256  # "JARV"
        for i in range(156):
            values: list[float] = []
            for _ in range(6):
                seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
                values.append(((seed >> 8) & 0xFFFF) / 65535.0)
            self._particle_specs.append((
                values[0] * math.tau,
                0.72 + values[1] * 0.58,
                0.46 + values[2] * 0.48,
                (0.10 + values[3] * 0.34) * (-1.0 if i % 2 else 1.0),
                0.65 + values[4] * 1.35,
                values[5] * math.tau,
                i % 11,
            ))

        # A second deterministic set is used for the wireframe shell nodes.
        self._mesh_specs: list[tuple[float, float, float, float]] = []
        for i in range(30):
            seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
            a = ((seed >> 8) & 0xFFFF) / 65535.0
            seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
            b = ((seed >> 8) & 0xFFFF) / 65535.0
            seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
            c = ((seed >> 8) & 0xFFFF) / 65535.0
            self._mesh_specs.append((
                a * math.tau,
                0.70 + b * 0.24,
                0.60 + c * 0.34,
                0.035 + (i % 7) * 0.009,
            ))

        self.animation = QTimer(self)
        # ~16 FPS looks smooth on the wall display without stealing meaningful
        # CPU from the thermostat control loop.
        self.animation.setInterval(60)
        self.animation.timeout.connect(self.advance_animation)
        self.hide()

    def is_active(self) -> bool:
        return bool(self._active and self.stage != "idle")

    def set_status(self, payload: object) -> bool:
        data = payload if isinstance(payload, dict) else {}
        if isinstance(data.get("assistant"), dict):
            data = data.get("assistant") or {}
        stage = str(data.get("stage") or "idle").strip().lower()
        active = bool(data.get("active", stage != "idle")) and stage != "idle"
        command = str(data.get("command") or "")
        response = str(data.get("response") or "")
        status_text = str(data.get("statusText") or "")
        error_text = str(data.get("error") or "")
        try:
            request_id = int(data.get("requestId") or 0)
        except Exception:
            request_id = 0

        signature = (stage, active, command, response, status_text, error_text, request_id)
        changed = signature != self._status_signature
        if not changed:
            if active and not self.isVisible():
                self.show()
                self.raise_()
                return True
            if not active and self.isVisible():
                self.animation.stop()
                self.hide()
                return True
            return False

        self._status_signature = signature
        self.stage = stage
        self.command = command
        self.response = response
        self.status_text = status_text
        self.error_text = error_text
        self.request_id = request_id
        self._active = active
        if active:
            if not self.animation.isActive():
                self.animation.start()
            self.show()
            self.raise_()
            self.update()
        else:
            self.animation.stop()
            self.hide()
        return True

    def advance_animation(self):
        # Speaking is deliberately a little more animated than listening, but
        # avoids a frantic 30/60 FPS update rate.
        speed = 0.092 if self.stage == "processing" else (0.084 if self.stage == "speaking" else 0.055)
        self.phase = (self.phase + speed) % (math.pi * 400.0)
        self.update()

    def stage_color(self) -> QColor:
        if self.stage == "error":
            return QColor(255, 74, 58)
        if self.stage == "processing":
            return QColor(46, 210, 255)
        if self.stage == "listening":
            return QColor(38, 241, 238)
        if self.stage == "speaking":
            return QColor(63, 247, 255)
        return QColor(39, 213, 230)

    def mousePressEvent(self, event):
        # The animation intentionally owns the screen while a command is active.
        event.accept()

    def touchEvent(self, event):
        event.accept()

    @staticmethod
    def _fit_text(text: str, max_chars: int) -> str:
        text = " ".join(str(text or "").split())
        if len(text) <= max_chars:
            return text
        return text[: max(1, max_chars - 1)].rstrip() + "…"

    @staticmethod
    def _draw_arc_plane(p: QPainter, cx: float, cy: float, rx: float, ry: float, rotation: float,
                        start: float, span: float, color: QColor, width: float = 1.0):
        p.save()
        p.translate(cx, cy)
        p.rotate(rotation)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(-rx, -ry, rx * 2.0, ry * 2.0), int(start * 16.0), int(span * 16.0))
        p.restore()

    @staticmethod
    def _hud_box(p: QPainter, rect: QRectF, title: str, rows: list[str], accent: QColor):
        """Small chamfered HUD panel used only for live JARVIS metadata."""
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        cut = min(14.0, h * 0.18)
        path = QPainterPath()
        path.moveTo(x + cut, y)
        path.lineTo(x + w, y)
        path.lineTo(x + w, y + h - cut)
        path.lineTo(x + w - cut, y + h)
        path.lineTo(x, y + h)
        path.lineTo(x, y + cut)
        path.closeSubpath()
        p.setBrush(QColor(1, 16, 20, 176))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 86), 1.0))
        p.drawPath(path)
        p.setPen(QColor(accent.red(), accent.green(), accent.blue(), 225))
        p.setFont(font(8, QFont.Black, 1))
        p.drawText(QRectF(x + 14, y + 8, w - 28, 18), Qt.AlignLeft | Qt.AlignVCenter, title)
        p.setPen(QColor(125, 224, 229, 176))
        p.setFont(font(7, QFont.Bold))
        yy = y + 32
        for row in rows[:4]:
            p.drawText(QRectF(x + 14, yy, w - 28, 17), Qt.AlignLeft | Qt.AlignVCenter, row)
            yy += 18

    def _speech_level(self) -> float:
        """Generate a speech-like amplitude envelope without capturing Sonos audio.

        The response text seeds irregular syllable-sized peaks and punctuation
        creates small pauses.  The result avoids the obviously repetitive sine
        wave that the previous speaking animation used.
        """
        if self.stage != "speaking":
            return 0.0
        text = " ".join((self.response or self.status_text or "JARVIS").split()) or "JARVIS"
        position = int(self.phase * 4.8) % len(text)
        ch = text[position]
        punctuation = ch in ".,!?;:"
        space = ch.isspace()
        character_energy = ((ord(ch) * 17 + position * 23 + self.request_id * 7) % 97) / 96.0
        carrier = abs(math.sin(self.phase * 4.1 + character_energy * 5.8))
        secondary = abs(math.sin(self.phase * 7.7 + position * 0.61))
        level = 0.18 + 0.52 * carrier + 0.30 * secondary
        if punctuation:
            level *= 0.20
        elif space:
            level *= 0.38
        return clamp(level, 0.05, 1.0)

    def _draw_ticks(self, p: QPainter, cx: float, cy: float, radius: float, count: int,
                    phase_deg: float, accent: QColor, speech: float):
        for i in range(count):
            angle = math.radians((i * 360.0 / count + phase_deg) % 360.0)
            major = i % 6 == 0
            mid = i % 3 == 0
            inner = radius * (0.955 if major else (0.968 if mid else 0.978))
            outer = radius * (1.022 if major else 1.008)
            alpha = 164 if major else (88 if mid else 48)
            if self.stage == "speaking" and (i + int(self.phase * 9)) % 11 == 0:
                outer += radius * (0.018 + speech * 0.025)
                alpha = 220
            p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), alpha), 1.5 if major else 0.8))
            p.drawLine(
                QPointF(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner),
                QPointF(cx + math.cos(angle) * outer, cy + math.sin(angle) * outer),
            )

    def _draw_wire_shell(self, p: QPainter, cx: float, cy: float, radius: float,
                         accent: QColor, speech: float):
        points: list[QPointF] = []
        depths: list[float] = []
        for i, (base, shell, vertical, drift) in enumerate(self._mesh_specs):
            angle = base + self.phase * drift * (-1.0 if i % 3 == 0 else 1.0)
            wobble = 1.0 + math.sin(self.phase * 0.43 + i * 1.37) * 0.025
            if self.stage == "speaking":
                wobble += speech * (0.016 if i % 2 else 0.034)
            rr = radius * shell * wobble
            x = cx + math.cos(angle) * rr
            y = cy + math.sin(angle * (0.92 + (i % 5) * 0.017) + i * 0.14) * rr * vertical
            points.append(QPointF(x, y))
            depths.append(0.45 + 0.55 * abs(math.cos(angle)))

        # Connect nearby pre-indexed neighbors.  The offset pattern forms a
        # geodesic-looking shell without an expensive all-pairs distance pass.
        for i, a in enumerate(points):
            for offset in (1, 4, 9):
                j = (i + offset) % len(points)
                if offset == 9 and i % 2:
                    continue
                b = points[j]
                alpha = int(28 + 70 * min(depths[i], depths[j]) + speech * 32)
                p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), min(148, alpha)), 0.72))
                p.drawLine(a, b)

        p.setPen(Qt.NoPen)
        for i, point in enumerate(points):
            alpha = int(80 + 120 * depths[i] + speech * 35)
            size = 1.15 + (i % 4) * 0.32
            p.setBrush(QColor(92, 248, 255, min(235, alpha)))
            p.drawEllipse(point, size, size)

    def _draw_particles(self, p: QPainter, cx: float, cy: float, radius: float,
                        accent: QColor, speech: float):
        for base, shell, vertical, speed, size, twinkle, group in self._particle_specs:
            angle = base + self.phase * speed
            breathing = 1.0 + math.sin(self.phase * 0.39 + twinkle) * 0.022
            outward = speech * (0.035 + (group % 5) * 0.008) if self.stage == "speaking" else 0.0
            rr = radius * shell * (breathing + outward)
            x = cx + math.cos(angle) * rr
            y = cy + math.sin(angle * (0.96 + group * 0.004) + twinkle * 0.11) * rr * vertical
            flicker = 0.55 + 0.45 * abs(math.sin(self.phase * (0.65 + group * 0.04) + twinkle))
            alpha = int(54 + 145 * flicker + speech * 34)
            p.setPen(Qt.NoPen)
            if group in {0, 6}:
                p.setBrush(QColor(188, 255, 255, min(242, alpha)))
            else:
                p.setBrush(QColor(accent.red(), accent.green(), accent.blue(), min(224, alpha)))
            dot = size * (0.72 + flicker * 0.46)
            p.drawEllipse(QPointF(x, y), dot, dot)
            if self.stage == "speaking" and group in {2, 7} and speech > 0.45:
                tangent = angle + math.pi / 2.0
                length = radius * (0.010 + speech * 0.020)
                p.setPen(QPen(QColor(73, 246, 255, int(45 + speech * 90)), 0.75))
                p.drawLine(
                    QPointF(x - math.cos(tangent) * length, y - math.sin(tangent) * length),
                    QPointF(x + math.cos(tangent) * length, y + math.sin(tangent) * length),
                )

    def _draw_waveform(self, p: QPainter, rect: QRectF, accent: QColor, speech: float):
        x0, y0, width, height = rect.x(), rect.y(), rect.width(), rect.height()
        center_y = y0 + height * 0.50
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 74), 1.0))
        p.drawLine(QPointF(x0, center_y), QPointF(x0 + width, center_y))
        bars = 61
        p.setPen(QPen(QColor(80, 247, 255, 220 if self.stage == "speaking" else 118), 1.6, Qt.SolidLine, Qt.RoundCap))
        for i in range(bars):
            normalized = i / max(1, bars - 1)
            distance = abs(normalized - 0.5) * 2.0
            envelope = 0.44 + (1.0 - distance) * 0.56
            if self.stage == "speaking":
                local = abs(math.sin(self.phase * 5.0 + i * 0.71 + math.sin(i * 0.31) * 1.3))
                local2 = abs(math.sin(self.phase * 8.4 + i * 0.37))
                amp = 3.0 + height * 0.40 * envelope * clamp(0.15 + speech * (0.50 * local + 0.50 * local2), 0.0, 1.0)
            elif self.stage == "listening":
                amp = 2.0 + height * 0.12 * abs(math.sin(self.phase * 2.0 + i * 0.33))
            else:
                amp = 2.0 + height * 0.07 * abs(math.sin(self.phase + i * 0.26))
            xx = x0 + width * normalized
            p.drawLine(QPointF(xx, center_y - amp), QPointF(xx, center_y + amp))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = max(1, self.width())
        h = max(1, self.height())
        p.fillRect(self.rect(), QColor(0, 0, 0))

        phase = self.phase
        accent = self.stage_color()
        speech = self._speech_level()
        cyan = QColor(45, 232, 241)
        bright = QColor(138, 255, 255)

        # Subtle blue-black central atmosphere.
        bg = QRadialGradient(QPointF(w * 0.5, h * 0.43), min(w, h) * 0.74)
        bg.setColorAt(0.0, QColor(0, 31, 37, 116))
        bg.setColorAt(0.45, QColor(0, 12, 17, 78))
        bg.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRect(self.rect())

        # Fine crosshair/grid and edge instrumentation.
        p.setPen(QPen(QColor(24, 176, 188, 25), 1.0))
        grid_step = max(64, int(min(w, h) * 0.105))
        for xx in range(grid_step, w, grid_step):
            p.drawLine(QPointF(xx, 0), QPointF(xx, h))
        for yy in range(grid_step, h, grid_step):
            p.drawLine(QPointF(0, yy), QPointF(w, yy))

        margin = max(24.0, min(w, h) * 0.032)
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 112), 1.0))
        p.drawLine(QPointF(margin, 24), QPointF(w * 0.31, 24))
        p.drawLine(QPointF(w - margin, 24), QPointF(w * 0.69, 24))
        p.drawLine(QPointF(margin, h - 24), QPointF(w * 0.29, h - 24))
        p.drawLine(QPointF(w - margin, h - 24), QPointF(w * 0.71, h - 24))
        for x_sign in (1, -1):
            x = margin if x_sign > 0 else w - margin
            p.drawLine(QPointF(x, 24), QPointF(x, 74))
            p.drawLine(QPointF(x, h - 24), QPointF(x, h - 74))

        # Header labels stay compact and use only real assistant state.
        p.setFont(font(9, QFont.Black, 2))
        p.setPen(QColor(accent.red(), accent.green(), accent.blue(), 225))
        p.drawText(QRectF(margin + 14, 32, 300, 26), Qt.AlignLeft | Qt.AlignVCenter, "JARVIS  //  AI ASSISTANT")
        stage_label = self.stage.upper() if self.stage else "IDLE"
        p.setFont(font(8, QFont.Black, 2))
        p.drawText(QRectF(w * 0.5 - 120, 32, 240, 24), Qt.AlignCenter, stage_label)
        right_label = f"VOICE LINK  •  REQ {self.request_id:04d}" if self.request_id else "VOICE LINK  •  ACTIVE"
        p.drawText(QRectF(w - margin - 310, 32, 296, 24), Qt.AlignRight | Qt.AlignVCenter, right_label)

        cx = w * 0.50
        cy = h * 0.435
        radius = min(w, h) * 0.285
        if self.stage == "speaking":
            radius *= 1.0 + speech * 0.020
        else:
            radius *= 1.0 + math.sin(phase * 1.2) * 0.006

        # Side metadata panes add the dense movie-HUD feel without inventing
        # fake thermostat/system values.
        side_w = min(238.0, max(170.0, w * 0.185))
        side_y = max(105.0, h * 0.15)
        side_h = min(128.0, h * 0.18)
        status_line = self._fit_text(self.status_text or stage_label.title(), 30).upper()
        command_line = self._fit_text(self.command or "VOICE CHANNEL READY", 30).upper()
        left_rows = [
            f"STATE     {stage_label}",
            f"REQUEST   {self.request_id:04d}" if self.request_id else "REQUEST   --",
            "CHANNEL   PRIMARY",
            "LINK      SYNCHRONIZED",
        ]
        right_rows = [
            "CORE      ONLINE",
            "OUTPUT    SONOS / TTS",
            f"ACTIVITY  {int(max(0.0, speech) * 100):02d}%" if self.stage == "speaking" else "ACTIVITY  NOMINAL",
            status_line,
        ]
        self._hud_box(p, QRectF(margin + 10, side_y, side_w, side_h), "VOICE INPUT", left_rows, accent)
        self._hud_box(p, QRectF(w - margin - side_w - 10, side_y, side_w, side_h), "NEURAL OUTPUT", right_rows, accent)

        # Secondary little callout strips.
        mini_y = side_y + side_h + 18
        mini_h = 66
        self._hud_box(p, QRectF(margin + 10, mini_y, side_w, mini_h), "COMMAND BUFFER", [command_line], accent)
        synthesis = "SPEECH SYNTHESIS ACTIVE" if self.stage == "speaking" else ("ANALYZING REQUEST" if self.stage == "processing" else "MICROPHONE LINK ACTIVE")
        self._hud_box(p, QRectF(w - margin - side_w - 10, mini_y, side_w, mini_h), "SYNTHESIS", [synthesis], accent)

        # Large layered HUD rings.
        outer = radius * 1.38
        p.setBrush(Qt.NoBrush)
        for scale, alpha, width in ((1.38, 74, 1.2), (1.29, 42, 0.8), (1.17, 110, 1.3), (1.08, 55, 0.9)):
            p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), alpha), width))
            p.drawEllipse(QPointF(cx, cy), radius * scale, radius * scale)
        self._draw_ticks(p, cx, cy, outer, 96, phase * 13.0, accent, speech)
        self._draw_ticks(p, cx, cy, radius * 1.19, 72, -phase * 8.0, accent, speech)

        # Segmented rotating HUD arcs.
        for i in range(16):
            ring = radius * (1.09 + (i % 4) * 0.075)
            start = (i * 29.0 + phase * (22.0 + (i % 3) * 9.0) * (-1 if i % 2 else 1)) % 360.0
            span = 8.0 + (i % 5) * 5.0
            alpha = 70 + (i % 4) * 30
            if self.stage == "speaking" and i % 3 == 0:
                span += speech * 14.0
                alpha = min(230, int(alpha + speech * 72))
            self._draw_arc_plane(p, cx, cy, ring, ring, 0, start, span,
                                 QColor(accent.red(), accent.green(), accent.blue(), alpha),
                                 1.2 + (i % 3) * 0.55)

        # Moving scanner flare.
        sweep = (phase * (58.0 if self.stage == "processing" else 34.0)) % 360.0
        if self.stage in {"listening", "processing", "speaking"}:
            sweep_span = 34 + (speech * 44 if self.stage == "speaking" else 0)
            self._draw_arc_plane(p, cx, cy, radius * 1.31, radius * 1.31, 0,
                                 sweep, sweep_span, QColor(125, 255, 255, 185), 3.0)

        # Particle cloud and geodesic network surrounding the central nucleus.
        self._draw_particles(p, cx, cy, radius, accent, speech)
        self._draw_wire_shell(p, cx, cy, radius, accent, speech)

        # Multiple atom/orbit planes.  These speed up and brighten while JARVIS
        # speaks, which gives the center the impression of reacting to syllables.
        orbit_specs = (
            (0.64, 0.29, 18.0, 0.38),
            (0.70, 0.24, -37.0, -0.31),
            (0.73, 0.34, 73.0, 0.27),
            (0.66, 0.43, 126.0, -0.22),
            (0.57, 0.22, -82.0, 0.46),
        )
        for i, (scale, squash, rotation, speed) in enumerate(orbit_specs):
            local_scale = scale * (1.0 + speech * (0.016 + i * 0.004))
            rot = rotation + math.sin(phase * 0.41 + i) * (3.0 + speech * 5.5)
            alpha = int(138 + i * 16 + speech * 66)
            self._draw_arc_plane(
                p, cx, cy, radius * local_scale, radius * local_scale * squash, rot,
                (phase * speed * 110.0 + i * 41.0) % 360.0, 340.0,
                QColor(111, 255, 255, min(250, alpha)), 1.5 + speech * 1.35,
            )

        # Bright wireframe nucleus with latitude/longitude lines.
        core_r = radius * (0.245 + speech * 0.028)
        core_halo = QRadialGradient(QPointF(cx, cy), core_r * 1.85)
        core_halo.setColorAt(0.0, QColor(223, 255, 255, 255))
        core_halo.setColorAt(0.13, QColor(76, 247, 255, 238))
        core_halo.setColorAt(0.46, QColor(0, 196, 222, int(135 + speech * 70)))
        core_halo.setColorAt(0.78, QColor(0, 89, 116, 45))
        core_halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(core_halo)
        p.drawEllipse(QPointF(cx, cy), core_r * 1.85, core_r * 1.85)
        p.setBrush(QColor(0, 196, 220, 28))
        p.setPen(QPen(QColor(125, 255, 255, 218), 1.15))
        p.drawEllipse(QPointF(cx, cy), core_r, core_r)
        for i in range(1, 6):
            yy = core_r * (i / 6.0)
            half = math.sqrt(max(0.0, core_r * core_r - yy * yy))
            alpha = 65 + i * 10
            p.setPen(QPen(QColor(100, 250, 255, alpha), 0.75))
            p.drawEllipse(QRectF(cx - half, cy - yy * 0.42, half * 2, yy * 0.84))
            p.drawEllipse(QRectF(cx - yy * 0.42, cy - half, yy * 0.84, half * 2))

        # Speaking-specific radial energy spikes.  Their length follows the
        # text-seeded speech envelope so the orb appears to "talk".
        if self.stage == "speaking":
            burst_count = 64
            for i in range(burst_count):
                angle = math.radians(i * 360.0 / burst_count + phase * 8.0)
                local = abs(math.sin(phase * 5.7 + i * 0.83 + (i % 7) * 0.19))
                energy = speech * (0.30 + 0.70 * local)
                inner = radius * (0.82 + (i % 3) * 0.012)
                outer_r = inner + radius * (0.025 + energy * 0.095)
                alpha = int(32 + energy * 158)
                p.setPen(QPen(QColor(94, 251, 255, alpha), 0.8 + energy * 1.1))
                p.drawLine(
                    QPointF(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner),
                    QPointF(cx + math.cos(angle) * outer_r, cy + math.sin(angle) * outer_r),
                )

        # Listening uses a quiet crosshair sweep; processing uses orbiting nodes.
        elif self.stage == "listening":
            p.setPen(QPen(QColor(77, 244, 246, 88), 1.0))
            p.drawLine(QPointF(cx - radius * 1.06, cy), QPointF(cx + radius * 1.06, cy))
            p.drawLine(QPointF(cx, cy - radius * 1.06), QPointF(cx, cy + radius * 1.06))
        elif self.stage == "processing":
            p.setPen(Qt.NoPen)
            for i in range(7):
                angle = phase * 1.3 + i * math.tau / 7.0
                rr = radius * 1.03
                p.setBrush(QColor(120, 255, 255, 190))
                p.drawEllipse(QPointF(cx + math.cos(angle) * rr, cy + math.sin(angle) * rr), 2.6, 2.6)
        elif self.stage == "error":
            p.setPen(QPen(QColor(255, 80, 62, 210), 2.0))
            for scale in (1.08, 1.19, 1.30):
                p.drawArc(QRectF(cx - radius * scale, cy - radius * scale,
                                 radius * scale * 2, radius * scale * 2),
                          int((phase * 125.0 + scale * 800) % 5760), int(58 * 16))

        # Central point flare.
        flare = QRadialGradient(QPointF(cx, cy), core_r * 0.52)
        flare.setColorAt(0.0, QColor(255, 255, 255, 255))
        flare.setColorAt(0.15, QColor(169, 255, 255, 255))
        flare.setColorAt(0.52, QColor(44, 238, 255, int(155 + speech * 80)))
        flare.setColorAt(1.0, QColor(0, 205, 236, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(flare)
        p.drawEllipse(QPointF(cx, cy), core_r * 0.52, core_r * 0.52)

        # Bottom voice-link bar and speech waveform.
        voice_rect = QRectF(w * 0.27, h - max(112.0, h * 0.135), w * 0.46, max(54.0, h * 0.066))
        p.setBrush(QColor(0, 18, 23, 208))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 102), 1.0))
        p.drawRoundedRect(voice_rect, 9, 9)
        p.setFont(font(7, QFont.Black, 2))
        p.setPen(QColor(accent.red(), accent.green(), accent.blue(), 215))
        p.drawText(QRectF(voice_rect.x() + 14, voice_rect.y() + 4, 132, 18), Qt.AlignLeft | Qt.AlignVCenter, "VOICE LINK")
        voice_state = "ACTIVE / SPEAKING" if self.stage == "speaking" else stage_label
        p.drawText(QRectF(voice_rect.right() - 160, voice_rect.y() + 4, 146, 18), Qt.AlignRight | Qt.AlignVCenter, voice_state)
        wave_rect = QRectF(voice_rect.x() + 24, voice_rect.y() + 20, voice_rect.width() - 48, voice_rect.height() - 24)
        self._draw_waveform(p, wave_rect, accent, speech)

        # Keep useful response/error text readable while leaving the hologram as
        # the visual focus.  During speaking this is the same text driving the
        # pseudo speech envelope above.
        display_text = self.response or (self.error_text if self.stage == "error" else "")
        if display_text:
            panel_h = max(54.0, min(82.0, h * 0.10))
            panel_y = voice_rect.y() - panel_h - 10
            panel = QRectF(w * 0.19, panel_y, w * 0.62, panel_h)
            p.setBrush(QColor(0, 12, 16, 212))
            p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 78), 1.0))
            p.drawRoundedRect(panel, 10, 10)
            p.setPen(QColor(215, 255, 255) if self.stage != "error" else QColor(255, 180, 168))
            p.setFont(font(max(9, int(min(w, h) * 0.016)), QFont.Bold))
            p.drawText(
                QRectF(panel.x() + 22, panel.y() + 9, panel.width() - 44, panel.height() - 18),
                Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap,
                self._fit_text(display_text, 300),
            )

class Header(QWidget):
    navChanged = pyqtSignal(str)
    infoClicked = pyqtSignal()
    settingsClicked = pyqtSignal()
    lockClicked = pyqtSignal()
    lockLongPressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(102)
        self.lock_button = ScreenLockButton()
        self.current_pill = TopPill("--", "Current")
        self.set_pill = TopPill("--", "Set", active=True)
        self.nav = NavBar(["Blinds", "Audio", "Thermostat", "Lights", "Room"])
        self.time_pill = QLabel("--:--")
        self.time_pill.setAlignment(Qt.AlignCenter)
        self.time_pill.setFont(font(10, QFont.Black))
        self.time_pill.setMinimumWidth(78)
        self.time_pill.setFixedHeight(34)
        self.time_pill.setStyleSheet("background:rgba(65,73,92,0.52); color:#f6f8ff; border:1px solid rgba(155,174,208,0.22); border-radius:16px; padding:4px 10px;")
        self.info = IconCircle("i", "info", 44, active=False)
        self.info.setFont(font(20, QFont.Black))
        self.gear = IconCircle("⚙", "settings", 44, active=False)
        self.gear.setFont(font(16, QFont.Black))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(42, 24, 42, 20)
        lay.setSpacing(14)
        lay.addWidget(self.lock_button)
        lay.addWidget(self.current_pill)
        lay.addWidget(self.set_pill)
        lay.addStretch(1)
        lay.addWidget(self.nav, 0, Qt.AlignCenter)
        lay.addStretch(1)
        lay.addWidget(self.time_pill)
        lay.addWidget(self.info)
        lay.addWidget(self.gear)

        self.nav.changed.connect(self.navChanged.emit)
        self.info.clicked.connect(self.infoClicked.emit)
        self.gear.clicked.connect(self.settingsClicked.emit)
        self.lock_button.clicked.connect(self.lockClicked.emit)
        self.lock_button.longPressed.connect(self.lockLongPressed.emit)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.update_time)
        self.clock.start(1000)
        self.update_time()



    def set_locked(self, locked: bool, secure: bool = False):
        self.lock_button.setLocked(locked, secure)
        # The lock pill itself remains available so the owner can unlock. Every
        # other header action is disabled while the panel is in guest-safe mode.
        for btn in self.nav.buttons.values():
            btn.setEnabled(not locked)
        self.info.setEnabled(not locked)
        self.gear.setEnabled(not locked)

    def flash_security_lock_confirmation(self):
        self.lock_button.start_security_confirmation_flash()

    def update_time(self):
        try:
            text = datetime.now().strftime("%-I:%M %p")
        except Exception:
            text = datetime.now().strftime("%I:%M %p").lstrip("0")
        self.time_pill.setText(text)

    def update_values(self, current, target):
        self.current_pill.setValues(fmt_temp(current), "Current")
        self.set_pill.setValues(fmt_temp(target), "Set")

    def set_page(self, name: str):
        self.nav.setActive(name)
        # The thermostat page already has the large center dial, so the small
        # Current/Set pills are redundant there. Keep them visible on Blinds,
        # Audio, Lights, and Room where they provide useful context.
        show_temp_pills = name != "Thermostat"
        self.lock_button.setVisible(name == "Thermostat")
        self.current_pill.setVisible(show_temp_pills)
        self.set_pill.setVisible(show_temp_pills)


class AppState:
    def __init__(self, api: ApiClient):
        self.api = api
        self.config: dict = {}
        self.thermostat: dict = {}
        self.system_info: dict = {}
        self.last_error = ""
        self.status_refresh_paused_until = 0.0
        # Every background thermostat-status request captures this epoch. Local
        # commands bump it before changing the optimistic UI so a slow response
        # that started earlier cannot repaint an old mode/setpoint/door-pause
        # state after the command has already been accepted on screen.
        self.status_refresh_epoch = 0
        # Local setpoint hold used by the native dial/buttons. Home Assistant and
        # the local backend can briefly report the old target while a just-touched
        # setpoint is still round-tripping. Keep the newly selected target on
        # screen for a short window so the dial does not snap backward and then
        # jump forward again.
        self._target_override_value: int | None = None
        self._target_override_until = 0.0
        self._target_override_base_revision = -1
        self._last_target_revision = -1
        self._last_mode_revision = -1
        self._mode_override: dict | None = None
        self._mode_override_until = 0.0
        # Monotonic timestamp of the last alarm state confirmed directly with
        # Home Assistant. Saved panel config is display cache only and must never
        # be treated as authoritative when opening alarm controls.
        self.alarm_state_refreshed_at = 0.0

    def pause_status_refresh(self, seconds: float = 2.5):
        self.status_refresh_epoch += 1
        self.status_refresh_paused_until = max(
            float(getattr(self, "status_refresh_paused_until", 0.0) or 0.0),
            time.monotonic() + max(0.0, float(seconds)),
        )

    def resume_status_refresh(self):
        # Invalidate requests that may have started during the optimistic hold,
        # then allow a new authoritative poll immediately.
        self.status_refresh_epoch += 1
        self.status_refresh_paused_until = 0.0

    def ha(self) -> dict:
        return nested_get(self.config, "integrations", "homeAssistant", default={}) or {}

    def ha_payload(self, extra: dict | None = None) -> dict:
        return ApiClient.ha_payload(self.config, extra)

    def legacy_config_schedules(self) -> list[dict]:
        """Return schedules saved by older builds in panel config, if present."""
        candidates: list[object] = []
        cfg = self.config if isinstance(self.config, dict) else {}
        candidates.append(cfg.get("schedules"))
        thermo = cfg.get("thermostat") if isinstance(cfg.get("thermostat"), dict) else {}
        candidates.append(thermo.get("schedules"))
        for key in ("schedule", "scheduleConfig", "thermostatSchedule", "thermostatSchedules"):
            candidates.append(cfg.get(key))
            if isinstance(thermo, dict):
                candidates.append(thermo.get(key))
        for candidate in candidates:
            if isinstance(candidate, list):
                schedules = [copy.deepcopy(x) for x in candidate if isinstance(x, dict)]
                if schedules:
                    return schedules
            if isinstance(candidate, dict):
                inner = candidate.get("schedules") or candidate.get("items") or candidate.get("entries")
                if isinstance(inner, list):
                    schedules = [copy.deepcopy(x) for x in inner if isinstance(x, dict)]
                    if schedules:
                        return schedules
        return []

    def thermostat_schedules(self) -> list[dict]:
        """Canonical schedule list for the native UI.

        New builds persist schedules in data/thermostat-state.json. Some earlier
        builds kept them in the panel config instead. Use both locations so the
        homepage shortcut buttons do not disappear after an update/migration.
        """
        t = self.thermostat if isinstance(self.thermostat, dict) else {}
        schedules = t.get("schedules") if isinstance(t.get("schedules"), list) else []
        if schedules:
            return [copy.deepcopy(x) for x in schedules if isinstance(x, dict)]
        schedules = self.legacy_config_schedules()
        if schedules:
            self.thermostat["schedules"] = copy.deepcopy(schedules)
        return schedules

    def set_thermostat_schedules_local(self, schedules: list[dict]):
        schedules = [copy.deepcopy(x) for x in schedules if isinstance(x, dict)]
        if not isinstance(self.thermostat, dict):
            self.thermostat = {}
        self.thermostat["schedules"] = copy.deepcopy(schedules)
        cfg = self.config if isinstance(self.config, dict) else {}
        thermo = cfg.setdefault("thermostat", {}) if isinstance(cfg, dict) else {}
        if isinstance(thermo, dict):
            thermo["schedules"] = copy.deepcopy(schedules)

    def fetch_alarm_state(self):
        """Fetch the current Alarmo/HA alarm state without touching widgets."""
        ha = self.ha()
        entity = ha.get("alarmEntity") or {}
        if not isinstance(entity, dict):
            return None
        eid = str(entity.get("entityId") or entity.get("entity_id") or "").strip()
        if not eid:
            return None
        data = self.api.post("/api/ha/alarm/states", self.ha_payload({"entityIds": [eid]}))
        alarms = data.get("alarms") or []
        if not alarms:
            return None
        return alarms[0]

    def apply_alarm_state(self, fresh: dict | None):
        """Store a freshly fetched alarm state back into panel config."""
        if not isinstance(fresh, dict) or not fresh:
            return None
        ha = self.ha()
        entity = ha.get("alarmEntity") or {}
        if not isinstance(entity, dict):
            entity = {}
        entity.update(fresh)
        ha["alarmEntity"] = entity
        self.alarm_state_refreshed_at = time.monotonic()
        return entity

    def refresh_alarm_state(self):
        """Read the current Alarmo/HA alarm state instead of trusting saved config."""
        fresh = self.fetch_alarm_state()
        self.apply_alarm_state(fresh)
        return fresh

    def save_config(self):
        record = self.api.save_config(self.config)
        self.config = record.get("config") or self.config
        return record

    def set_target_override(self, value: float, hold_seconds: float = 20.0):
        """Hold a local target only until the backend confirms a newer revision."""
        try:
            val = int(round(float(value)))
        except Exception:
            return
        current = self.thermostat if isinstance(self.thermostat, dict) else {}
        try:
            self._target_override_base_revision = int(float(current.get("targetRevision", -1)))
        except (TypeError, ValueError):
            self._target_override_base_revision = -1
        self._target_override_value = val
        self._target_override_until = time.monotonic() + max(1.0, float(hold_seconds))
        if not isinstance(self.thermostat, dict):
            self.thermostat = {}
        self.thermostat["targetTemp"] = val
        self.thermostat["lastComfortTarget"] = val

    def clear_target_override(self):
        self._target_override_value = None
        self._target_override_until = 0.0
        self._target_override_base_revision = -1

    def set_mode_override(
        self,
        mode: str,
        *,
        away: bool = False,
        hold_seconds: float = 14.0,
        presence_home_override: dict | None = None,
        clear_presence_home_override: bool = False,
    ):
        """Hold a just-requested local mode against stale status refreshes.

        ``None`` historically meant "do not alter the presence override".  The
        Arriving toggle also needs an explicit clear operation, so keep that
        compatibility and expose a separate flag for the clear case.
        """
        mode = str(mode or "").strip().lower()
        if mode not in {"off", "heat", "cool", "away"}:
            return
        override = {
            "mode": "cool" if mode == "away" else mode,
            "away": bool(away or mode == "away"),
            "awaySource": "manual" if (away or mode == "away") else "",
        }
        if clear_presence_home_override:
            override["presenceHomeOverride"] = None
        elif presence_home_override is not None:
            override["presenceHomeOverride"] = copy.deepcopy(presence_home_override)
        self._mode_override = override
        self._mode_override_until = time.monotonic() + max(1.0, float(hold_seconds))

    def clear_mode_override(self):
        self._mode_override = None
        self._mode_override_until = 0.0

    def apply_mode_override(self, thermostat: dict) -> dict:
        if not isinstance(thermostat, dict):
            return thermostat
        now = time.monotonic()
        if not self._mode_override or now >= self._mode_override_until:
            self.clear_mode_override()
            return thermostat
        thermostat.update(self._mode_override)
        return thermostat

    def apply_target_override(self, thermostat: dict) -> dict:
        if not isinstance(thermostat, dict):
            return thermostat
        now = time.monotonic()
        if self._target_override_value is None or now >= self._target_override_until:
            self.clear_target_override()
            return thermostat

        try:
            incoming_target = int(round(float(thermostat.get("targetTemp", thermostat.get("target_temperature")))))
        except (TypeError, ValueError):
            incoming_target = None
        try:
            incoming_revision = int(float(thermostat.get("targetRevision", -1)))
        except (TypeError, ValueError):
            incoming_revision = -1

        # Matching data is the command acknowledgement; a higher revision with a
        # different target is a later panel/HA command and must win immediately.
        if incoming_target == self._target_override_value:
            self.clear_target_override()
            return thermostat
        if incoming_revision > self._target_override_base_revision >= 0:
            self.clear_target_override()
            return thermostat

        thermostat["targetTemp"] = self._target_override_value
        thermostat["lastComfortTarget"] = self._target_override_value
        return thermostat

    def ingest_thermostat(self, payload: dict | None) -> dict:
        incoming = thermostat_detail_payload(payload)
        current = self.thermostat if isinstance(self.thermostat, dict) else {}

        # Status requests can finish out of order. Preserve only target/mode fields
        # from the newer revision while still accepting fresh temperatures, relay
        # state, timers, and diagnostics from the response.
        try:
            incoming_target_revision = int(float(incoming.get("targetRevision", -1)))
        except (TypeError, ValueError):
            incoming_target_revision = -1
        if incoming_target_revision >= 0 and self._last_target_revision >= 0 and incoming_target_revision < self._last_target_revision:
            for key in ("targetTemp", "target_temperature", "temperature", "lastComfortTarget", "targetRevision", "lastTargetChangeSource", "lastTargetChangeAt"):
                if key in current:
                    incoming[key] = copy.deepcopy(current.get(key))
        elif incoming_target_revision >= 0:
            self._last_target_revision = max(self._last_target_revision, incoming_target_revision)

        try:
            incoming_mode_revision = int(float(incoming.get("modeRevision", -1)))
        except (TypeError, ValueError):
            incoming_mode_revision = -1
        if incoming_mode_revision >= 0 and self._last_mode_revision >= 0 and incoming_mode_revision < self._last_mode_revision:
            for key in ("mode", "hvac_mode", "hvacMode", "modeRevision", "lastModeChangeSource", "lastModeChangeAt"):
                if key in current:
                    incoming[key] = copy.deepcopy(current.get(key))
        elif incoming_mode_revision >= 0:
            self._last_mode_revision = max(self._last_mode_revision, incoming_mode_revision)

        self.thermostat = self.apply_target_override(self.apply_mode_override(incoming))
        return self.thermostat

    def load(self):
        rec = self.api.get_config_record()
        self.config = rec.get("config") or {}
        self.ingest_thermostat(self.api.thermostat_status())
        # Older builds stored thermostat schedules in panel-config.json. If the
        # thermostat-state file does not have them yet, mirror that legacy copy
        # into the live state so shortcut buttons and the schedule manager still
        # show them immediately after boot.
        self.thermostat_schedules()
        try:
            self.system_info = self.api.get("/api/system/info")
        except Exception:
            self.system_info = {}

    def refresh_status(self):
        return self.ingest_thermostat(self.api.thermostat_status())

    def update_thermostat(self, changes: dict):
        return self.ingest_thermostat(self.api.thermostat_update(changes))


class ThermostatNoticeCard(QWidget):
    """Small fixed-size thermostat notice card.

    The auto switch notice used to be a plain QLabel, which could grow much
    taller on the Raspberry Pi touchscreen when Qt recalculated its wrapped
    text. Keeping this as a painted, fixed-size card makes it match the compact
    left-side tile design and prevents it from covering the Doors tile.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind = "cool"
        self.heading = ""
        self.primary = ""
        self.badge = ""
        self.setFixedSize(226, 118)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_notice(self, kind: str, heading: str, primary: str = "", badge: str = "", *, height: int = 118):
        self.kind = str(kind or "cool").lower()
        self.heading = str(heading or "")
        self.primary = str(primary or "")
        self.badge = str(badge or "")
        self.setFixedSize(226, int(clamp(height, 96, 136)))
        self.update()

    # Compatibility for older call sites.  Keep it fixed-size even if someone
    # later sets multiline text directly.
    def setText(self, value: str):
        parts = [line.strip() for line in str(value or "").splitlines() if line.strip()]
        heading = parts[0] if parts else ""
        primary = parts[1] if len(parts) > 1 else ""
        badge = parts[2] if len(parts) > 2 else ""
        inferred = self.kind
        lower = " ".join(parts).lower()
        if "cooldown" in lower or "manual" in lower:
            inferred = "purple"
        elif "heat" in lower and "cool" not in lower:
            inferred = "heat"
        elif "cool" in lower:
            inferred = "cool"
        self.set_notice(inferred, heading, primary, badge)

    def text(self) -> str:
        return "\n".join([part for part in (self.heading, self.primary, self.badge) if part])

    def _palette(self) -> tuple[QColor, QColor, QColor, QColor, QColor]:
        if self.kind == "heat":
            return (
                QColor(124, 22, 42, 218),
                QColor(35, 14, 30, 232),
                QColor(255, 87, 121, 176),
                QColor(255, 82, 115, 76),
                QColor(255, 232, 238),
            )
        if self.kind in {"purple", "manual", "cooldown"}:
            return (
                QColor(74, 54, 130, 218),
                QColor(25, 17, 45, 232),
                QColor(194, 155, 255, 176),
                QColor(194, 155, 255, 58),
                QColor(248, 244, 255),
            )
        return (
            QColor(16, 84, 118, 216),
            QColor(9, 34, 58, 232),
            QColor(71, 224, 255, 164),
            QColor(71, 224, 255, 64),
            QColor(246, 252, 255),
        )

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg1, bg2, border, glow_color, text_col = self._palette()

        glow = QRadialGradient(QPointF(r.center().x(), r.top() + 30), max(r.width(), r.height()) * 0.78)
        glow.setColorAt(0.0, glow_color)
        glow.setColorAt(0.68, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), max(12, glow_color.alpha() // 4)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, glow)

        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0.0, bg1)
        grad.setColorAt(1.0, bg2)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(border, 1.55))
        p.drawRoundedRect(r, 22, 22)

        p.setPen(QColor(224, 240, 255, 222))
        p.setFont(font(8, QFont.Black, 18))
        p.drawText(QRectF(14, 14, r.width() - 28, 18), Qt.AlignCenter, self.heading.upper())

        p.setPen(text_col)
        p.setFont(font(18, QFont.Black))
        primary_rect = QRectF(12, 35, r.width() - 24, 34)
        p.drawText(primary_rect, Qt.AlignCenter, self.primary)

        if self.badge:
            p.setFont(font(8, QFont.Black, 18))
            fm = p.fontMetrics()
            badge_w = max(86, min(r.width() - 42, fm.horizontalAdvance(self.badge.upper()) + 26))
            badge_r = QRectF(r.center().x() - badge_w / 2, r.bottom() - 36, badge_w, 22)
            badge_base = QColor(border.red(), border.green(), border.blue(), 92)
            p.setBrush(badge_base)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(badge_r, 11, 11)
            p.setPen(QColor(232, 252, 255))
            p.drawText(badge_r, Qt.AlignCenter, self.badge.upper())


class ThermostatBypassBubble(QWidget):
    """Compact left-side bypass bubble for changeover delays.

    This intentionally matches the small thermostat notice-card language instead
    of using the generic pill button.  It stays parked under the Inside Doors
    card, so the user always knows where the bypass action will appear.
    """

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind = "cool"
        self.label = "BYPASS"
        self._pressed = False
        self.setFixedSize(226, 70)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_kind(self, kind: str):
        self.kind = str(kind or "cool").lower()
        self.update()

    def setText(self, value: str):
        self.label = str(value or "BYPASS").strip().upper() or "BYPASS"
        self.update()

    def text(self) -> str:
        return self.label

    def _palette(self) -> tuple[QColor, QColor, QColor, QColor, QColor]:
        if self.kind == "heat":
            return (
                QColor(124, 22, 42, 218),
                QColor(35, 14, 30, 232),
                QColor(255, 87, 121, 176),
                QColor(255, 82, 115, 64),
                QColor(255, 232, 238),
            )
        if self.kind in {"purple", "manual", "cooldown", "lockout"}:
            return (
                QColor(74, 54, 130, 218),
                QColor(25, 17, 45, 232),
                QColor(194, 155, 255, 176),
                QColor(194, 155, 255, 58),
                QColor(248, 244, 255),
            )
        return (
            QColor(16, 84, 118, 216),
            QColor(9, 34, 58, 232),
            QColor(71, 224, 255, 164),
            QColor(71, 224, 255, 62),
            QColor(246, 252, 255),
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        was_pressed = self._pressed
        self._pressed = False
        self.update()
        if was_pressed and event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg1, bg2, border, glow_color, text_col = self._palette()
        if self._pressed:
            bg1 = QColor(max(0, bg1.red() - 14), max(0, bg1.green() - 14), max(0, bg1.blue() - 14), bg1.alpha())
            bg2 = QColor(max(0, bg2.red() - 10), max(0, bg2.green() - 10), max(0, bg2.blue() - 10), bg2.alpha())

        glow = QRadialGradient(QPointF(r.center().x(), r.center().y()), max(r.width(), r.height()) * 0.76)
        glow.setColorAt(0.0, glow_color)
        glow.setColorAt(0.70, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), max(10, glow_color.alpha() // 4)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, glow)

        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0.0, bg1)
        grad.setColorAt(1.0, bg2)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(border, 1.55))
        p.drawRoundedRect(r, 22, 22)

        p.setFont(font(13, QFont.Black, 18))
        badge_w = min(r.width() - 46, max(102, p.fontMetrics().horizontalAdvance(self.label) + 38))
        badge_r = QRectF(r.center().x() - badge_w / 2, r.center().y() - 15, badge_w, 30)
        p.setBrush(QColor(border.red(), border.green(), border.blue(), 86))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(badge_r, 15, 15)

        p.setPen(text_col)
        p.drawText(r, Qt.AlignCenter, self.label)


class ThermostatActionBanner(GlassPanel):
    dismissClicked = pyqtSignal()
    revertClicked = pyqtSignal()
    bypassClicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, radius=24, strong=True)
        self.kind = "info"
        self.setFixedWidth(540)
        self.setMinimumHeight(134)
        self.setMaximumHeight(230)
        self.title = QLabel("", self)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        self.title.setFont(font(17, QFont.Black))
        self.title.setStyleSheet("color:#ffffff; background:transparent; border:0;")
        self.body = QLabel("", self)
        self.body.setAlignment(Qt.AlignCenter)
        self.body.setWordWrap(True)
        self.body.setFont(font(11, QFont.Black))
        self.body.setStyleSheet("color:#dfe8ff; background:transparent; border:0;")
        self.dismiss = RoundButton("Dismiss", active=False, min_h=38)
        self.revert = RoundButton("Revert", active=True, min_h=38)
        self.bypass = RoundButton("Bypass", active=True, kind="purple", min_h=38)
        self.dismiss.setFixedWidth(120)
        self.revert.setFixedWidth(120)
        self.bypass.setFixedWidth(180)
        buttons = QHBoxLayout()
        self.buttons_layout = buttons
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(10)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(7)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addLayout(buttons)
        self.dismiss.clicked.connect(self.dismissClicked.emit)
        self.revert.clicked.connect(self.revertClicked.emit)
        self.bypass.clicked.connect(self.bypassClicked.emit)
        self._layout_buttons(False)
        self.hide()

    def _layout_buttons(self, bypass_left: bool = False):
        while self.buttons_layout.count():
            self.buttons_layout.takeAt(0)

        visible_buttons = [btn for btn in (self.dismiss, self.revert, self.bypass) if btn.isVisible()]
        if len(visible_buttons) == 1:
            # Door-comfort pause only has the snooze action; keep it centered so
            # the alert reads like a clean modal instead of a left-weighted banner.
            self.buttons_layout.addStretch(1)
            self.buttons_layout.addWidget(visible_buttons[0])
            self.buttons_layout.addStretch(1)
            return

        if bypass_left:
            self.buttons_layout.addWidget(self.bypass)
            self.buttons_layout.addStretch(1)
            self.buttons_layout.addWidget(self.dismiss)
            self.buttons_layout.addWidget(self.revert)
        else:
            self.buttons_layout.addStretch(1)
            self.buttons_layout.addWidget(self.dismiss)
            self.buttons_layout.addWidget(self.revert)
            self.buttons_layout.addWidget(self.bypass)

    def set_alert(self, kind: str, title: str, body: str, *, dismiss=False, revert=False, bypass=False, dismiss_text="Dismiss", revert_text="Revert", bypass_text="Bypass"):
        self.kind = kind or "info"
        if self.kind == "door-pause":
            self.setMinimumHeight(182)
            self.setMaximumHeight(260)
            self.title.setFont(font(24, QFont.Black))
            self.body.setFont(font(14, QFont.Black))
            self.bypass.setFixedWidth(220)
        elif self.kind == "lockout":
            self.setMinimumHeight(154)
            self.setMaximumHeight(210)
            self.title.setFont(font(22, QFont.Black))
            self.body.setFont(font(14, QFont.Black))
            self.bypass.setFixedWidth(170)
        else:
            self.setMinimumHeight(134)
            self.setMaximumHeight(190)
            self.title.setFont(font(17, QFont.Black))
            self.body.setFont(font(11, QFont.Black))
            self.bypass.setFixedWidth(180)
        self.title.setText(title)
        self.body.setText(body)
        self.dismiss.setText(dismiss_text or "Dismiss")
        self.revert.setText(revert_text or "Revert")
        self.bypass.setText(bypass_text or "Bypass")
        self.dismiss.setVisible(bool(dismiss))
        self.revert.setVisible(bool(revert))
        self.bypass.setVisible(bool(bypass))
        self._layout_buttons(bypass_left=self.kind == "lockout" and bool(bypass))
        color = {
            "heat": "rgba(255,72,83,0.58)",
            "cool": "rgba(65,225,255,0.48)",
            "lockout": "rgba(188,132,255,0.50)",
            "door-pause": "rgba(255,154,36,0.82)",
            "auto": "rgba(72,214,255,0.42)",
            "safety": "rgba(255,72,83,0.56)" if "Heat" in title else "rgba(65,225,255,0.50)",
        }.get(self.kind, "rgba(72,214,255,0.38)")
        border = "rgba(255,231,164,0.62)" if self.kind == "door-pause" else "rgba(255,255,255,0.20)"
        radius = 30 if self.kind == "door-pause" else 24
        border_width = 2 if self.kind == "door-pause" else 1
        self.setStyleSheet(f"""
            ThermostatActionBanner {{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {color},
                    stop:1 rgba(11,18,35,0.92));
                border:{border_width}px solid {border};
                border-radius:{radius}px;
            }}
        """)
        self.adjustSize()
        self.show()
        self.raise_()


class ThermostatNoticeActionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice = None
        self.kind = "cool"
        self.setModal(True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowTitle("Notice")
        self.setFixedSize(760, 336)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(0)

        self.card = QFrame(self)
        self.card.setObjectName("noticeDialogCard")
        root.addWidget(self.card, 1)

        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(26, 22, 26, 22)
        card_lay.setSpacing(12)

        self.kicker = QLabel("AUTO NOTICE")
        self.kicker.setAlignment(Qt.AlignCenter)
        self.kicker.setFont(font(10, QFont.Black, 18))
        card_lay.addWidget(self.kicker)

        self.title_label = QLabel("Auto-Switched")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setFont(font(26, QFont.Black))
        card_lay.addWidget(self.title_label)

        self.body_label = QLabel("")
        self.body_label.setAlignment(Qt.AlignCenter)
        self.body_label.setWordWrap(True)
        self.body_label.setFont(font(12, QFont.Black))
        self.body_label.setStyleSheet("color:#dfe8ff; background:transparent; border:0;")
        card_lay.addWidget(self.body_label)

        self.status_pill = QLabel("")
        self.status_pill.setAlignment(Qt.AlignCenter)
        self.status_pill.setFont(font(10, QFont.Black, 18))
        self.status_pill.setMinimumHeight(34)
        self.status_pill.setMaximumHeight(34)
        card_lay.addWidget(self.status_pill, 0, Qt.AlignCenter)

        card_lay.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.setSpacing(12)
        btn_row.addStretch(1)
        self.secondary_btn = RoundButton("Dismiss", active=False, min_h=48)
        self.secondary_btn.setMinimumWidth(170)
        self.primary_btn = RoundButton("Revert", active=True, min_h=48)
        self.primary_btn.setMinimumWidth(240)
        btn_row.addWidget(self.secondary_btn)
        btn_row.addWidget(self.primary_btn)
        btn_row.addStretch(1)
        card_lay.addLayout(btn_row)

        self.secondary_btn.clicked.connect(lambda: self.finish("secondary"))
        self.primary_btn.clicked.connect(lambda: self.finish("primary"))

        self.apply_kind("cool")

    def apply_kind(self, kind: str):
        self.kind = str(kind or "cool").lower()
        if self.kind == "heat":
            accent = "#ff8468"
            soft = "rgba(255,96,80,0.30)"
            pill_bg = "rgba(255,110,88,0.18)"
            pill_border = "rgba(255,145,126,0.42)"
        elif self.kind == "purple":
            accent = "#bd98ff"
            soft = "rgba(151,101,255,0.28)"
            pill_bg = "rgba(162,112,255,0.18)"
            pill_border = "rgba(196,167,255,0.42)"
        else:
            accent = "#46e8ff"
            soft = "rgba(72,214,255,0.26)"
            pill_bg = "rgba(72,214,255,0.16)"
            pill_border = "rgba(104,222,255,0.42)"
        self.kicker.setStyleSheet(f"color:{accent}; background:transparent; border:0; letter-spacing:3px;")
        self.card.setStyleSheet(f"""
            QFrame#noticeDialogCard {{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {soft},
                    stop:1 rgba(9,17,31,0.97));
                border:1px solid rgba(255,255,255,0.18);
                border-radius:28px;
            }}
        """)
        self.status_pill.setStyleSheet(
            f"background:{pill_bg}; border:1px solid {pill_border}; border-radius:17px; color:#f5fbff; padding:0 16px;"
        )

    def configure(self, *, kind: str = "cool", kicker: str = "AUTO NOTICE", title: str = "", body: str = "", status_text: str = "", primary_text: str = "OK", secondary_text: str = "Dismiss"):
        self.apply_kind(kind)
        self.kicker.setText((kicker or "NOTICE").upper())
        self.title_label.setText(title or "Notice")
        self.body_label.setText(body or "")
        self.status_pill.setVisible(bool(status_text))
        self.status_pill.setText((status_text or "").upper())
        self.primary_btn.setText(primary_text or "OK")
        self.secondary_btn.setText(secondary_text or "Dismiss")
        self.primary_btn.setVisible(bool(primary_text))
        self.secondary_btn.setVisible(bool(secondary_text))
        self.adjustSize()

    def finish(self, choice: str):
        self.choice = choice
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            geo = parent.frameGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(x, y)
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - self.width()) // 2
            y = geo.y() + (geo.height() - self.height()) // 2
            self.move(x, y)

    @staticmethod
    def ask(parent, **kwargs) -> str | None:
        dlg = ThermostatNoticeActionDialog(parent)
        dlg.configure(**kwargs)
        if dlg.exec_() == QDialog.Accepted:
            return dlg.choice
        return None


class TextKeyboardDialog(QDialog):
    def __init__(self, title: str, value: str = "", parent=None):
        super().__init__(parent)
        self.result_text = str(value or "")
        self.setModal(True)
        self.setWindowTitle(title)
        self.setFixedSize(660, 430)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(font(24, QFont.Black))
        root.addWidget(title_label)
        self.display = QLabel("")
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setFont(font(24, QFont.Black))
        self.display.setStyleSheet("background:rgba(255,255,255,0.07); border:1px solid rgba(85,240,255,0.35); border-radius:18px; padding:12px;")
        root.addWidget(self.display)
        self.uppercase = False
        self.letter_buttons: list[tuple[RoundButton, str]] = []
        rows = [list("QWERTYUIOP"), list("ASDFGHJKL"), list("ZXCVBNM")]
        for letters in rows:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addStretch(1)
            for ch in letters:
                b = KeypadButton(ch.lower(), active=True, min_h=42)
                b.setFixedSize(52, 42)
                b.pressed.connect(lambda c=ch: self.add_letter(c))
                self.letter_buttons.append((b, ch))
                row.addWidget(b)
            row.addStretch(1)
            root.addLayout(row)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.shift = KeypadButton("⇧ UPPER", active=False, min_h=46)
        space = KeypadButton("Space", active=False, min_h=46)
        back = KeypadButton("⌫", active=False, min_h=46)
        clear = KeypadButton("Clear", active=False, min_h=46)
        cancel = KeypadButton("Cancel", active=False, kind="danger", min_h=46)
        done = KeypadButton("Done", active=True, min_h=46)
        self.shift.pressed.connect(self.toggle_uppercase)
        space.pressed.connect(lambda: self.add_char(" "))
        back.pressed.connect(self.backspace)
        clear.pressed.connect(self.clear_text)
        cancel.clicked.connect(self.reject)
        done.clicked.connect(self.accept)
        bottom.addWidget(self.shift)
        bottom.addWidget(space)
        bottom.addWidget(back)
        bottom.addWidget(clear)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(done)
        root.addLayout(bottom)
        self.refresh_keyboard()
        self.refresh()

    def refresh_keyboard(self):
        for button, ch in self.letter_buttons:
            button.setText(ch if self.uppercase else ch.lower())
            if hasattr(button, "setActive"):
                button.setActive(True)
        self.shift.setText("abc lower" if self.uppercase else "⇧ UPPER")
        if hasattr(self.shift, "setActive"):
            self.shift.setActive(self.uppercase)

    def toggle_uppercase(self):
        self.uppercase = not self.uppercase
        self.refresh_keyboard()

    def refresh(self):
        next_text = self.result_text or " "
        if self.display.text() != next_text:
            self.display.setText(next_text)
            self.display.repaint()

    def add_letter(self, ch: str):
        self.add_char(ch if self.uppercase else ch.lower())

    def add_char(self, ch: str):
        if len(self.result_text) < 28:
            self.result_text += ch
            self.refresh()

    def backspace(self):
        self.result_text = self.result_text[:-1]
        self.refresh()

    def clear_text(self):
        self.result_text = ""
        self.refresh()

    @staticmethod
    def get_text(parent, title: str, value: str = "") -> str | None:
        dlg = TextKeyboardDialog(title, value, parent)
        if dlg.exec_() == QDialog.Accepted:
            return dlg.result_text.strip()
        return None


class ScheduleEditDialog(QDialog):
    saved = pyqtSignal(dict)
    peopleLoaded = pyqtSignal(object)

    def __init__(self, state: AppState, schedule: dict | None = None, parent=None):
        super().__init__(parent)
        self.s = state
        self.schedule = copy.deepcopy(schedule or {})
        self.people: list[dict] = []
        self._people_loading = False
        self.peopleLoaded.connect(self.handle_people_loaded)
        self.setModal(True)
        self.setWindowTitle("Schedule")
        # Keep this dialog inside the 10.1" touchscreen. The previous fixed
        # 780x620 layout was taller than the kiosk display and the new weekday
        # controls could visually collide with the person/bottom sections.
        self.setMinimumSize(640, 420)
        self.resize(860, 560)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
            QScrollArea { background:transparent; border:0; }
            QScrollBar:vertical { background:rgba(255,255,255,0.06); width:9px; border-radius:4px; }
            QScrollBar::handle:vertical { background:rgba(85,240,255,0.45); border-radius:4px; min-height:28px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        """)
        self.name = str(self.schedule.get("name") or "Morning")
        self.hour, self.minute = parse_schedule_time_24h(self.schedule.get("time") or "07:00")
        self.cool = int(float(self.schedule.get("coolSetpoint") or 72))
        self.heat = int(float(self.schedule.get("heatSetpoint") or 68))
        self.enabled = bool(self.schedule.get("enabled", True))
        self.days = normalize_schedule_days(self.schedule.get("days", self.schedule.get("weekdays", self.schedule.get("daysOfWeek"))))
        self.day_buttons: dict[str, RoundButton] = {}
        self.person_ids = [str(x) for x in (self.schedule.get("personEntityIds") or []) if str(x)]
        self.person_names = normalize_schedule_person_names(self.schedule.get("personNames"), self.person_ids)
        # Seed saved schedule people before the HA refresh so Edit always shows
        # who is already selected, even when that person is not part of the
        # panel's separate presence/Auto Away lists.
        self.available_people: list[dict] = [
            {"entityId": entity_id, "name": self.person_names.get(entity_id) or entity_id}
            for entity_id in self.person_ids
        ]
        self.load_people()
        self.build()
        QTimer.singleShot(0, self.fit_to_screen)
        QTimer.singleShot(0, self.refresh_people_async)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        width = max(640, min(860, geo.width() - 48))
        height = max(420, min(560, geo.height() - 32))
        self.resize(width, height)
        self.move(geo.x() + (geo.width() - width) // 2, geo.y() + (geo.height() - height) // 2)

    def load_people(self):
        """Load the saved person cache without touching the network.

        This runs while the schedule dialog is being constructed on the Qt UI
        thread. Home Assistant discovery is started separately after the dialog
        paints so a slow or unreachable HA server cannot freeze touch input.
        """
        saved_groups = []
        if isinstance(self.s.thermostat, dict):
            for key in ("people", "autoAwayPeople"):
                value = self.s.thermostat.get(key)
                if isinstance(value, list):
                    saved_groups.append(value)
        for saved_people in saved_groups:
            for person in saved_people:
                if not isinstance(person, dict):
                    continue
                entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
                if not entity_id:
                    continue
                existing = next(
                    (x for x in self.available_people if str(x.get("entityId") or x.get("entity_id") or "") == entity_id),
                    None,
                )
                if existing is None:
                    item = dict(person)
                    item["entityId"] = entity_id
                    self.available_people.append(item)
                else:
                    existing.update(person)
                    existing["entityId"] = entity_id
                if entity_id in self.person_ids:
                    friendly = str(person.get("name") or person.get("friendly_name") or "").strip()
                    if friendly:
                        self.person_names[entity_id] = friendly

    def refresh_people_async(self):
        if self._people_loading:
            return
        ha = self.s.ha()
        if not str(ha.get("url") or "").strip() or not str(ha.get("token") or "").strip():
            return
        self._people_loading = True

        def worker():
            try:
                data = self.s.api.post("/api/ha/entities", self.s.ha_payload({"domains": ["person"]}))
                self.peopleLoaded.emit({"entities": data.get("entities") or [], "error": None})
            except Exception as exc:
                self.peopleLoaded.emit({"entities": [], "error": str(exc)})

        threading.Thread(target=worker, name="schedule-person-refresh", daemon=True).start()

    def handle_people_loaded(self, info: object):
        self._people_loading = False
        data = info if isinstance(info, dict) else {}
        changed = False
        for person in data.get("entities") or []:
            if not isinstance(person, dict):
                continue
            entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
            if not entity_id:
                continue
            existing = next((x for x in self.available_people if str(x.get("entityId") or x.get("entity_id") or "") == entity_id), None)
            if existing is None:
                item = dict(person)
                item["entityId"] = entity_id
                self.available_people.append(item)
                changed = True
            else:
                before = dict(existing)
                existing.update(person)
                existing["entityId"] = entity_id
                changed = changed or existing != before
            if entity_id in self.person_ids:
                friendly = str(person.get("name") or person.get("friendly_name") or "").strip()
                if friendly and self.person_names.get(entity_id) != friendly:
                    self.person_names[entity_id] = friendly
                    changed = True
        if changed:
            self.available_people.sort(key=lambda item: str(item.get("name") or item.get("friendly_name") or item.get("entityId") or "").lower())
            self.refresh()

    def small_label(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setFont(font(10, QFont.Black))
        lab.setStyleSheet("color:#d8e8ff; background:transparent; border:0;")
        return lab

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("SCHEDULE")
        title.setFont(font(22, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        self.summary_label = QLabel("")
        self.summary_label.setFont(font(11, QFont.Black))
        self.summary_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.summary_label.setStyleSheet("color:#cde6ff; background:transparent; border:0;")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.summary_label)
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        top_row.addWidget(self.time_panel(), 1)

        target_wrap = QWidget()
        target_lay = QHBoxLayout(target_wrap)
        target_lay.setContentsMargins(0, 0, 0, 0)
        target_lay.setSpacing(10)
        target_lay.addWidget(self.target_control("Cool Target", "cool", 45, 95))
        target_lay.addWidget(self.target_control("Heat Target", "heat", 45, 95))
        top_row.addWidget(target_wrap, 1)
        content_lay.addLayout(top_row)

        content_lay.addWidget(self.days_panel())
        content_lay.addWidget(self.people_panel(), 1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.enabled_btn = RoundButton("Enabled", active=self.enabled, min_h=42)
        self.enabled_btn.clicked.connect(self.toggle_enabled)
        cancel = RoundButton("Cancel", active=False, kind="danger", min_h=42)
        save = RoundButton("Save", active=True, min_h=42)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save)
        bottom.addWidget(self.enabled_btn)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)
        self.refresh()

    def time_panel(self) -> QWidget:
        panel = GlassPanel(radius=18)
        lay = QGridLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setHorizontalSpacing(8)
        lay.setVerticalSpacing(7)

        self.name_btn = RoundButton(self.name, active=True, min_h=40)
        self.name_btn.clicked.connect(self.edit_name)
        lay.addWidget(self.small_label("Name"), 0, 0)
        lay.addWidget(self.name_btn, 0, 1, 1, 3)

        self.time_label = QLabel("")
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setMinimumHeight(38)
        self.time_label.setFont(font(21, QFont.Black))
        self.time_label.setStyleSheet("background:rgba(255,255,255,0.07); border:1px solid rgba(85,240,255,0.25); border-radius:14px; padding:4px;")
        lay.addWidget(self.small_label("Time"), 1, 0)
        lay.addWidget(self.time_label, 1, 1)

        self.ampm_btn = RoundButton("AM", active=True, min_h=38)
        self.ampm_btn.clicked.connect(self.toggle_ampm)
        lay.addWidget(self.small_label("AM / PM"), 2, 0)
        lay.addWidget(self.ampm_btn, 2, 1)

        for text_value, delta_h, delta_m, row, col in [
            ("Hour −", -1, 0, 1, 2),
            ("Hour +", 1, 0, 1, 3),
            ("Min −", 0, -5, 2, 2),
            ("Min +", 0, 5, 2, 3),
        ]:
            btn = RoundButton(text_value, active=False, min_h=38)
            btn.clicked.connect(lambda checked=False, dh=delta_h, dm=delta_m: self.adjust_time(dh, dm))
            lay.addWidget(btn, row, col)
        return panel

    def days_panel(self) -> QWidget:
        day_panel = GlassPanel(radius=18)
        day_lay = QVBoxLayout(day_panel)
        day_lay.setContentsMargins(12, 8, 12, 9)
        day_lay.setSpacing(6)
        day_hdr = QHBoxLayout()
        title = self.small_label("Run on these days")
        day_hdr.addWidget(title)
        day_hdr.addStretch(1)
        self.every_day_btn = RoundButton("Every Day", active=False, min_h=32)
        self.weekdays_btn = RoundButton("Weekdays", active=False, min_h=32)
        self.every_day_btn.setFixedHeight(32)
        self.weekdays_btn.setFixedHeight(32)
        self.every_day_btn.clicked.connect(lambda checked=False: self.set_days(SCHEDULE_DAY_KEYS))
        self.weekdays_btn.clicked.connect(lambda checked=False: self.set_days(SCHEDULE_DAY_KEYS[:5]))
        day_hdr.addWidget(self.every_day_btn)
        day_hdr.addWidget(self.weekdays_btn)
        day_lay.addLayout(day_hdr)
        day_row = QHBoxLayout()
        day_row.setSpacing(6)
        for key, short, _long in SCHEDULE_DAY_OPTIONS:
            btn = RoundButton(short, active=key in self.days, min_h=34)
            btn.clicked.connect(lambda checked=False, d=key: self.toggle_day(d))
            btn.setFixedHeight(34)
            btn.setMinimumWidth(52)
            self.day_buttons[key] = btn
            day_row.addWidget(btn)
        day_lay.addLayout(day_row)
        return day_panel

    def people_panel(self) -> QWidget:
        people_panel = GlassPanel(radius=18)
        people_lay = QVBoxLayout(people_panel)
        people_lay.setContentsMargins(12, 8, 12, 8)
        people_lay.setSpacing(6)
        hdr = QHBoxLayout()
        hdr.addWidget(self.small_label("Person condition — run if ANY selected person is home"))
        hdr.addStretch(1)
        add = RoundButton("Choose People", active=True, min_h=34)
        add.setFixedHeight(34)
        add.clicked.connect(self.add_person)
        hdr.addWidget(add)
        people_lay.addLayout(hdr)

        self.people_scroll = QScrollArea()
        self.people_scroll.setWidgetResizable(True)
        self.people_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.people_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.people_scroll.setFrameShape(QFrame.NoFrame)
        self.people_scroll.setMinimumHeight(72)
        self.people_list_widget = QWidget()
        self.people_box = QVBoxLayout(self.people_list_widget)
        self.people_box.setContentsMargins(0, 0, 0, 0)
        self.people_box.setSpacing(5)
        self.people_scroll.setWidget(self.people_list_widget)
        people_lay.addWidget(self.people_scroll, 1)
        return people_panel

    def target_control(self, title: str, attr: str, low: int, high: int) -> QWidget:
        panel = GlassPanel(radius=18)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        lab = QLabel(title)
        lab.setFont(font(11, QFont.Black))
        lab.setStyleSheet("background:transparent; border:0;")
        lay.addWidget(lab, 0, Qt.AlignCenter)
        val = QLabel("")
        val.setObjectName(attr + "_value")
        val.setAlignment(Qt.AlignCenter)
        val.setFont(font(28, QFont.Black))
        val.setStyleSheet("background:transparent; border:0;")
        lay.addWidget(val)
        row = QHBoxLayout()
        row.setSpacing(8)
        minus = RoundButton("−", active=False, min_h=36)
        plus = RoundButton("+", active=True, min_h=36)
        minus.setFixedHeight(36)
        plus.setFixedHeight(36)
        minus.clicked.connect(lambda: self.adjust_target(attr, -1, low, high))
        plus.clicked.connect(lambda: self.adjust_target(attr, 1, low, high))
        row.addWidget(minus)
        row.addWidget(plus)
        lay.addLayout(row)
        return panel

    def refresh(self):
        self.name_btn.setText(self.name)
        self.time_label.setText(format_schedule_time_12h(f"{self.hour:02d}:{self.minute:02d}"))
        if hasattr(self, "summary_label"):
            self.summary_label.setText(f"{schedule_days_text(self.days)} • {format_schedule_time_12h(f'{self.hour:02d}:{self.minute:02d}')}")
        if hasattr(self, "ampm_btn"):
            self.ampm_btn.setText("PM" if self.hour >= 12 else "AM")
            self.ampm_btn.setActive(self.hour >= 12)
        for label in self.findChildren(QLabel):
            if label.objectName() == "cool_value":
                label.setText(f"{self.cool}°")
            elif label.objectName() == "heat_value":
                label.setText(f"{self.heat}°")
        self.enabled_btn.setText("Enabled" if self.enabled else "Disabled")
        self.enabled_btn.setActive(self.enabled)
        while self.people_box.count():
            item = self.people_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
        for key, btn in getattr(self, "day_buttons", {}).items():
            btn.setActive(key in self.days)
        if hasattr(self, "every_day_btn"):
            self.every_day_btn.setActive(set(self.days) == set(SCHEDULE_DAY_KEYS))
        if hasattr(self, "weekdays_btn"):
            self.weekdays_btn.setActive(self.days == SCHEDULE_DAY_KEYS[:5])
        if not self.person_ids:
            none = QLabel("No people selected. Runs when the selected days and time match.")
            none.setWordWrap(True)
            none.setFont(font(10, QFont.Black))
            none.setStyleSheet("color:#c4d0e5; background:rgba(255,255,255,0.05); border-radius:10px; padding:8px;")
            self.people_box.addWidget(none)
        for eid in self.person_ids:
            row = QHBoxLayout()
            row.setSpacing(8)
            name = self.person_name(eid)
            lab = QLabel(f"{name}\n{eid}")
            lab.setFont(font(10, QFont.Black))
            lab.setStyleSheet("color:#e8f1ff; background:transparent; border:0;")
            remove = RoundButton("Remove", active=False, kind="danger", min_h=32)
            remove.setFixedHeight(32)
            remove.clicked.connect(lambda checked=False, x=eid: self.remove_person(x))
            row.addWidget(lab, 1)
            row.addWidget(remove)
            self.people_box.addLayout(row)
        self.people_box.addStretch(1)

    def person_name(self, entity_id: str) -> str:
        for p in self.available_people:
            if str(p.get("entityId") or p.get("entity_id") or "") == entity_id:
                name = str(p.get("name") or p.get("friendly_name") or "").strip()
                if name:
                    self.person_names[entity_id] = name
                    return name
        return self.person_names.get(entity_id) or entity_id

    def edit_name(self):
        value = TextKeyboardDialog.get_text(self, "Schedule Name", self.name)
        if value:
            self.name = value[:28]
            self.refresh()

    def adjust_time(self, dh: int, dm: int):
        total = self.hour * 60 + self.minute + dh * 60 + dm
        total %= 24 * 60
        self.hour, self.minute = divmod(total, 60)
        self.refresh()

    def toggle_ampm(self):
        self.hour = (self.hour + 12) % 24
        self.refresh()

    def adjust_target(self, attr: str, delta: int, low: int, high: int):
        if attr == "cool":
            self.cool = int(clamp(self.cool + delta, low, high))
        else:
            self.heat = int(clamp(self.heat + delta, low, high))
        self.refresh()

    def toggle_enabled(self):
        self.enabled = not self.enabled
        self.refresh()

    def set_days(self, days: Any):
        self.days = normalize_schedule_days(days)
        self.refresh()

    def toggle_day(self, day: str):
        key = str(day or "").strip().lower()
        if key not in SCHEDULE_DAY_KEYS:
            return
        selected = set(self.days)
        if key in selected and len(selected) > 1:
            selected.remove(key)
        else:
            selected.add(key)
        self.days = [d for d in SCHEDULE_DAY_KEYS if d in selected]
        self.refresh()

    def add_person(self):
        entities = self.available_people
        if not entities:
            message = "Home Assistant people are still loading." if self._people_loading else "No Home Assistant person entities found."
            QMessageBox.warning(self, "People", message)
            return
        dlg = EntityPickerDialog(
            "Choose People",
            entities,
            self,
            multi_select=True,
            selected_entity_ids=self.person_ids,
        )

        def selected_many(selected_people):
            next_ids: list[str] = []
            next_names: dict[str, str] = {}
            for person in selected_people or []:
                if not isinstance(person, dict):
                    continue
                entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
                if not entity_id or entity_id in next_ids:
                    continue
                next_ids.append(entity_id)
                friendly = str(person.get("name") or person.get("friendly_name") or "").strip()
                if friendly:
                    next_names[entity_id] = friendly
            self.person_ids = next_ids
            self.person_names = next_names
            self.refresh()

        dlg.selectedMany.connect(selected_many)
        dlg.exec_()

    def remove_person(self, entity_id: str):
        self.person_ids = [x for x in self.person_ids if x != entity_id]
        self.refresh()

    def save(self):
        sid = str(self.schedule.get("id") or f"schedule-{int(time.time()*1000)}")
        payload = {
            "id": sid,
            "name": self.name or "Schedule",
            "enabled": self.enabled,
            "time": f"{self.hour:02d}:{self.minute:02d}",
            "days": normalize_schedule_days(self.days),
            "coolSetpoint": int(self.cool),
            "heatSetpoint": int(self.heat),
            "personEntityIds": list(self.person_ids),
            "personNames": {entity_id: self.person_name(entity_id) for entity_id in self.person_ids},
            "lastTriggeredDate": str(self.schedule.get("lastTriggeredDate") or ""),
        }
        self.saved.emit(payload)
        self.accept()


class ScheduleManagerDialog(QDialog):
    changed = pyqtSignal()
    scheduleSaveCompleted = pyqtSignal(object)
    scheduleRunCompleted = pyqtSignal(object)

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.s = state
        self._schedule_save_seq = 0
        self._schedule_save_running = False
        self._schedule_save_pending: list[dict] | None = None
        self._schedule_run_seq = 0
        self._close_when_saved = False
        self._schedule_save_failed = False
        self.scheduleSaveCompleted.connect(self.handle_schedule_save_completed)
        self.scheduleRunCompleted.connect(self.handle_schedule_run_completed)
        self.setModal(True)
        self.setWindowTitle("Schedules")
        self.setMinimumSize(640, 420)
        self.resize(820, 560)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
            QScrollArea { background:transparent; border:0; }
            QScrollBar:vertical { background:rgba(255,255,255,0.06); width:9px; border-radius:4px; }
            QScrollBar::handle:vertical { background:rgba(85,240,255,0.45); border-radius:4px; min-height:28px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("SCHEDULES")
        title.setFont(font(24, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        new_btn = RoundButton("+ New", active=True, min_h=46)
        self.close_button = RoundButton("Done", active=False, min_h=46)
        new_btn.clicked.connect(self.new_schedule)
        self.close_button.clicked.connect(self.request_close)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(new_btn)
        header.addWidget(self.close_button)
        root.addLayout(header)
        self.save_status = QLabel("")
        self.save_status.setFont(font(8, QFont.Bold))
        self.save_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.save_status.setStyleSheet("color:#9fb0c8; background:transparent; border:0; padding:0 4px;")
        self.save_status.hide()
        root.addWidget(self.save_status)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(10)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)
        self.refresh()
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        width = max(640, min(820, geo.width() - 48))
        height = max(420, min(560, geo.height() - 32))
        self.resize(width, height)
        self.move(geo.x() + (geo.width() - width) // 2, geo.y() + (geo.height() - height) // 2)

    def schedules(self) -> list[dict]:
        return self.s.thermostat_schedules()

    def refresh(self):
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
        schedules = self.schedules()
        if not schedules:
            empty = QLabel("No schedules yet. Tap + New to create one.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#c4d0e5; background:rgba(255,255,255,0.05); border-radius:18px; padding:24px;")
            self.body_lay.addWidget(empty)
            self.body_lay.addStretch(1)
            return
        for sched in schedules:
            self.body_lay.addWidget(self.schedule_row(sched))
        self.body_lay.addStretch(1)

    def schedule_row(self, sched: dict) -> QWidget:
        panel = GlassPanel(radius=18)
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        people = sched.get("personEntityIds") or []
        selected_names = schedule_person_names(sched, self.s.thermostat)
        if not people:
            people_text = "No person condition"
        elif len(selected_names) == 1:
            people_text = f"Person condition: {selected_names[0]} must be home"
        else:
            people_text = f"Person condition: ANY one home — {', '.join(selected_names)}"
        day_text = schedule_days_text(sched.get("days"))
        time_text = format_schedule_time_12h(sched.get('time') or '07:00')
        text = QLabel(f"<b>{sched.get('name') or 'Schedule'}</b><br>{day_text} at {time_text} • Cool {sched.get('coolSetpoint')}° • Heat {sched.get('heatSetpoint')}°<br>{people_text}")
        text.setTextFormat(Qt.RichText)
        text.setWordWrap(True)
        text.setFont(font(11, QFont.Black))
        text.setStyleSheet("background:transparent; border:0; color:#eef4ff;")
        run = RoundButton("Run", active=True, min_h=42)
        edit = RoundButton("Edit", active=False, min_h=42)
        delete = RoundButton("Delete", active=False, kind="danger", min_h=42)
        run.clicked.connect(lambda checked=False, s=sched: self.run_schedule(s))
        edit.clicked.connect(lambda checked=False, s=sched: self.edit_schedule(s))
        delete.clicked.connect(lambda checked=False, s=sched: self.delete_schedule(s))
        lay.addWidget(text, 1)
        lay.addWidget(run)
        lay.addWidget(edit)
        lay.addWidget(delete)
        return panel

    def request_close(self):
        if self._schedule_save_failed and not self._schedule_save_running:
            self._close_when_saved = True
            self.close_button.setEnabled(False)
            self.close_button.setText("Retrying…")
            self.save_status.setText("Retrying the latest schedule changes before closing…")
            self.save_status.show()
            trace_runtime("schedule dialog retrying failed save before close")
            self._start_schedule_save(self.schedules())
            return
        if self._schedule_save_running or self._schedule_save_pending is not None:
            self._close_when_saved = True
            self.close_button.setEnabled(False)
            self.close_button.setText("Saving…")
            self.save_status.setText("Finishing schedule changes before closing…")
            self.save_status.show()
            trace_runtime("schedule dialog close deferred until queued saves finish")
            return
        self.accept()

    def closeEvent(self, event):
        if (self._schedule_save_running or self._schedule_save_pending is not None) and not QApplication.closingDown():
            self.request_close()
            event.ignore()
            return
        super().closeEvent(event)

    def save_schedules(self, schedules: list[dict]):
        # Update the native UI first so the schedule editor returns instantly.
        # Persist on a worker thread: /api/thermostat/control may wait on a slow
        # Home Assistant person-state refresh and must never block Qt touch input.
        schedules = [copy.deepcopy(item) for item in schedules if isinstance(item, dict)]
        self.s.set_thermostat_schedules_local(schedules)
        self.changed.emit()
        self.refresh()
        self._schedule_save_failed = False
        self.save_status.setText("Saving schedule changes…")
        self.save_status.show()
        if self._schedule_save_running:
            self._schedule_save_pending = copy.deepcopy(schedules)
            return
        self._start_schedule_save(schedules)

    def _start_schedule_save(self, schedules: list[dict]):
        self._schedule_save_running = True
        self._schedule_save_seq += 1
        seq = self._schedule_save_seq
        payload = copy.deepcopy(schedules)
        trace_runtime(f"schedule save started seq={seq} count={len(payload)}")

        def worker():
            try:
                result = self.s.api.thermostat_update({"schedules": payload})
                self.scheduleSaveCompleted.emit({"seq": seq, "result": result, "error": None})
            except Exception as exc:
                self.scheduleSaveCompleted.emit({"seq": seq, "result": None, "error": str(exc)})

        threading.Thread(target=worker, name="schedule-save", daemon=True).start()

    def handle_schedule_save_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        try:
            seq = int(data.get("seq") or 0)
        except Exception:
            seq = 0
        if seq != self._schedule_save_seq:
            return
        self._schedule_save_running = False
        pending = self._schedule_save_pending
        self._schedule_save_pending = None
        error = str(data.get("error") or "")
        trace_runtime(f"schedule save completed seq={seq} error={bool(error)} pending={pending is not None}")
        if error:
            if pending is not None:
                self.save_status.setText("First save failed; retrying the latest schedule list…")
                self.save_status.show()
                trace_runtime("schedule save failed with a newer edit queued; retrying latest list")
                self._start_schedule_save(pending)
                return
            self._close_when_saved = False
            self._schedule_save_failed = True
            self.close_button.setEnabled(True)
            self.close_button.setText("Retry / Done")
            self.save_status.setText("Schedule save failed. Tap Retry / Done to try again.")
            self.save_status.show()
            if self.isVisible():
                QMessageBox.warning(self, "Schedules", f"Schedule save failed: {error}")
            return

        self._schedule_save_failed = False
        if pending is None:
            # Only accept the response when there is no newer local edit waiting.
            # Otherwise the older response would briefly repaint the superseded
            # schedule list before the queued save completes.
            result = data.get("result")
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                self.s.set_thermostat_schedules_local(self.s.thermostat_schedules())
                self.changed.emit()
                self.refresh()
            self.close_button.setEnabled(True)
            self.close_button.setText("Done")
            self.save_status.setText("Schedule changes saved.")
            self.save_status.show()
            if self._close_when_saved:
                self._close_when_saved = False
                QTimer.singleShot(0, self.accept)
            else:
                QTimer.singleShot(1200, self.save_status.hide)
            return

        self._start_schedule_save(pending)

    def new_schedule(self):
        dlg = ScheduleEditDialog(self.s, None, self)
        dlg.saved.connect(self.upsert_schedule)
        dlg.exec_()

    def edit_schedule(self, sched: dict):
        dlg = ScheduleEditDialog(self.s, sched, self)
        dlg.saved.connect(self.upsert_schedule)
        dlg.exec_()

    def upsert_schedule(self, sched: dict):
        schedules = self.schedules()
        found = False
        for i, existing in enumerate(schedules):
            if str(existing.get("id")) == str(sched.get("id")):
                schedules[i] = sched
                found = True
                break
        if not found:
            schedules.append(sched)
        self.save_schedules(schedules)

    def delete_schedule(self, sched: dict):
        schedules = [s for s in self.schedules() if str(s.get("id")) != str(sched.get("id"))]
        self.save_schedules(schedules)

    def run_schedule(self, sched: dict):
        thermostat = self.s.thermostat if isinstance(self.s.thermostat, dict) else {}
        hold = thermostat.get("intimacyHold") if isinstance(thermostat.get("intimacyHold"), dict) else {}
        try:
            hold_active = bool(hold.get("active")) and float(hold.get("expiresAt") or 0) > time.time() * 1000
        except (TypeError, ValueError):
            hold_active = False
        if hold_active:
            QMessageBox.information(self, "Schedule", f"{intimacy_hold_target_text(thermostat)} hold is active. Schedules are ignored until it is turned off.")
            return
        mode = str((self.s.thermostat or {}).get("mode") or "cool").lower()
        active = str((self.s.thermostat or {}).get("autoActiveMode") or "").lower()
        effective = active if mode == "auto" and active in {"heat", "cool"} else mode
        target = sched.get("heatSetpoint") if effective == "heat" else sched.get("coolSetpoint")
        try:
            val = int(float(target))
        except Exception as exc:
            QMessageBox.warning(self, "Schedule", str(exc))
            return
        self.s.set_target_override(val)
        self.changed.emit()
        self._schedule_run_seq += 1
        seq = self._schedule_run_seq

        def worker():
            try:
                result = self.s.api.thermostat_update({"targetTemp": val, "lastComfortTarget": val, "targetChangeSource": "panel"})
                self.scheduleRunCompleted.emit({"seq": seq, "result": result, "error": None})
            except Exception as exc:
                self.scheduleRunCompleted.emit({"seq": seq, "result": None, "error": str(exc)})

        threading.Thread(target=worker, name="schedule-run-dialog", daemon=True).start()

    def handle_schedule_run_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        try:
            seq = int(data.get("seq") or 0)
        except Exception:
            seq = 0
        if seq != self._schedule_run_seq:
            return
        error = str(data.get("error") or "")
        if error:
            self.s.clear_target_override()
            self.changed.emit()
            if self.isVisible():
                QMessageBox.warning(self, "Schedule", error)
            return
        result = data.get("result")
        if isinstance(result, dict):
            self.s.ingest_thermostat(result)
        self.changed.emit()



class ThermostatNoticeActionPopup(QWidget):
    revertClicked = pyqtSignal()
    dismissAutoClicked = pyqtSignal()
    dismissManualClicked = pyqtSignal()
    followClicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.action_kind = "auto"
        self.follow_mode = ""
        self.setObjectName("thermostatNoticeActionPopup")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("""
            QWidget#thermostatNoticeActionPopup {
                background:rgba(3, 8, 18, 166);
                border:0;
            }
        """)

        self.card = GlassPanel(self, radius=30, strong=True)
        self.card.setFixedWidth(540)
        self.card.setMinimumHeight(238)
        self.card.setMaximumHeight(318)

        self.kicker = QLabel("THERMOSTAT NOTICE")
        self.kicker.setAlignment(Qt.AlignCenter)
        self.kicker.setFont(font(8, QFont.Black, 22))
        self.kicker.setStyleSheet("color:rgba(208,224,255,0.76); background:transparent; border:0; letter-spacing:3px;")

        self.title = QLabel("Auto-Switched")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setFont(font(28, QFont.Black))
        self.title.setStyleSheet("color:#ffffff; background:transparent; border:0;")

        self.body = QLabel("")
        self.body.setAlignment(Qt.AlignCenter)
        self.body.setWordWrap(True)
        self.body.setFont(font(13, QFont.Black))
        self.body.setStyleSheet("color:#dfe9ff; background:transparent; border:0;")

        self.hint = QLabel("Tap outside to close")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setFont(font(8, QFont.Black, 18))
        self.hint.setStyleSheet("color:rgba(214,225,245,0.48); background:transparent; border:0; letter-spacing:1px;")

        self.dismiss_button = RoundButton("Dismiss", active=False, min_h=48)
        self.primary_button = RoundButton("Revert", active=True, min_h=48)
        self.dismiss_button.setMinimumWidth(148)
        self.primary_button.setMinimumWidth(196)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(14)
        buttons.addStretch(1)
        buttons.addWidget(self.dismiss_button)
        buttons.addWidget(self.primary_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(30, 24, 30, 22)
        layout.setSpacing(10)
        layout.addWidget(self.kicker)
        layout.addSpacing(2)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addSpacing(8)
        layout.addLayout(buttons)
        layout.addWidget(self.hint)

        self.dismiss_button.clicked.connect(self._dismiss)
        self.primary_button.clicked.connect(self._primary)
        self.hide()

    def show_auto_switch(self, from_mode: str, to_mode: str, switch_temp: float | int | str | None = None):
        self.action_kind = "auto"
        self.follow_mode = ""
        from_mode = str(from_mode or "").lower()
        to_mode = str(to_mode or "").lower()
        from_label = from_mode.capitalize() if from_mode in {"heat", "cool"} else "Previous Mode"
        to_label = to_mode.capitalize() if to_mode in {"heat", "cool"} else "Auto"
        self.kicker.setText("AUTO SWITCHED")
        self.title.setText(f"Switched to {to_label}")
        temp_text = f" at {fmt_temp(switch_temp)}" if switch_temp not in (None, "") else ""
        self.body.setText(f"The thermostat automatically changed to {to_label}{temp_text}.\nChoose whether to revert back or dismiss this notice.")
        self.dismiss_button.setText("Dismiss")
        self.primary_button.setText(f"Revert to {from_label}")
        self.primary_button.setVisible(from_mode in {"heat", "cool"})
        self._show_centered()

    def show_manual_override(self, manual_mode: str, suggested_mode: str):
        self.action_kind = "manual"
        self.follow_mode = str(suggested_mode or "").lower()
        manual_label = str(manual_mode or "").capitalize() if str(manual_mode or "").lower() in {"heat", "cool"} else "Manual"
        suggested_label = self.follow_mode.capitalize() if self.follow_mode in {"heat", "cool"} else "Auto"
        self.kicker.setText("MANUAL OVERRIDE")
        self.title.setText(f"{manual_label} is staying on")
        self.body.setText(f"Auto would switch to {suggested_label}, but the manual override is still active.\nChoose whether to follow Auto or dismiss this notice.")
        self.dismiss_button.setText("Dismiss Notice")
        self.primary_button.setText(f"Follow Auto {suggested_label}")
        self.primary_button.setVisible(self.follow_mode in {"heat", "cool"})
        self._show_centered()

    def _show_centered(self):
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.card.raise_()
        self.setFocus(Qt.PopupFocusReason)
        self.position_card()

    def position_card(self):
        card_w = min(540, max(430, self.width() - 80))
        self.card.setFixedWidth(card_w)
        self.card.adjustSize()
        self.card.move(max(12, (self.width() - self.card.width()) // 2), max(18, (self.height() - self.card.height()) // 2))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_card()

    def mousePressEvent(self, event):
        if not self.card.geometry().contains(event.pos()):
            self.hide()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def _dismiss(self):
        action_kind = self.action_kind
        self.hide()
        if action_kind == "manual":
            self.dismissManualClicked.emit()
        else:
            self.dismissAutoClicked.emit()

    def _primary(self):
        action_kind = self.action_kind
        follow_mode = self.follow_mode
        self.hide()
        if action_kind == "manual" and follow_mode in {"heat", "cool"}:
            self.followClicked.emit(follow_mode)
        else:
            self.revertClicked.emit()


class PersonPresenceBadge(QWidget):
    """Tiny no-box person presence indicator for the main thermostat page."""
    def __init__(self, name: str, home: bool, parent=None):
        super().__init__(parent)
        self.name = compact_name(name, 12)
        self.home = bool(home)
        self.setFixedSize(68, 58)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setToolTip(f"{name}: {'Home' if home else 'Away'}")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        icon_color = QColor(40, 235, 130) if self.home else QColor(235, 55, 68)
        glow_color = QColor(icon_color)
        glow_color.setAlpha(82)

        cx = self.width() / 2.0
        # Glow only, no card/pill box.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow_color))
        painter.drawEllipse(QPointF(cx, 19), 22, 22)

        # Person silhouette: head + shoulders/body.
        painter.setBrush(QBrush(icon_color))
        painter.drawEllipse(QPointF(cx, 13), 7.2, 7.2)

        body = QPainterPath()
        body.moveTo(cx - 17, 36)
        body.cubicTo(cx - 15, 26, cx - 8, 22, cx, 22)
        body.cubicTo(cx + 8, 22, cx + 15, 26, cx + 17, 36)
        body.cubicTo(cx + 13, 39, cx - 13, 39, cx - 17, 36)
        painter.drawPath(body)

        # Small base shadow keeps it readable on the animated background.
        shadow = QColor(0, 0, 0, 95)
        painter.setPen(QPen(shadow, 2))
        painter.drawLine(int(cx - 14), 40, int(cx + 14), 40)

        painter.setPen(QColor("#edf6ff"))
        painter.setFont(font(7, QFont.Black))
        name_rect = self.rect().adjusted(0, 41, 0, 0)
        painter.drawText(name_rect, Qt.AlignHCenter | Qt.AlignTop, self.name)


class PersonPresenceStrip(QWidget):
    """Compact main-screen Home Assistant person tracker.

    Shows each configured person as only a silhouette and name. Home is green;
    anything else (away/not_home/unknown/unavailable) is red so stale or missing
    presence is obvious without taking much screen space.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("personPresenceStrip")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setMaximumHeight(60)
        self.setStyleSheet("QWidget#personPresenceStrip { background:transparent; border:0; }")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._people_signature = ""

    def update_people(self, people: list[dict] | None):
        clean: list[dict] = []
        for person in people or []:
            if not isinstance(person, dict):
                continue
            entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
            name = str(person.get("name") or person.get("friendly_name") or entity_id or "Person").strip()
            state = str(person.get("state") or "unknown").strip().lower()
            if entity_id or name:
                clean.append({"entityId": entity_id, "name": name, "state": state})
        # Keep the strip visually stable on the 10-inch screen.
        clean = clean[:4]
        signature = "|".join(f"{p['entityId']}:{p['name']}:{p['state']}" for p in clean)
        if signature == self._people_signature:
            self.setVisible(bool(clean))
            return
        self._people_signature = signature
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not clean:
            self.hide()
            return
        for person in clean:
            home = person["state"] == "home"
            self._layout.addWidget(PersonPresenceBadge(person["name"], home, self))
        self._layout.addStretch(1)
        self.show()



class ThermostatScreen(Page):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(app_state, parent)
        self._screen_locked = False
        self._screen_security_locked = False
        # Setpoint taps must never become concurrent absolute-temperature writes.
        # The UI updates optimistically on every tap, while this tiny queue keeps
        # only one backend request in flight and coalesces any newer taps into the
        # next request. Otherwise 72 -> 71 -> 70 -> 69 can be processed as
        # 71 -> 69 -> 70 when worker threads/network replies arrive out of order.
        self._target_command_in_flight = False
        self._target_command_pending_value: int | None = None
        self._target_command_pending_suppress_peer_sync = False
        self._target_command_sequence = 0
        self.dial = ThermostatDial()
        self.dial.setMaximumSize(470, 470)
        self.mode_buttons: dict[str, RoundButton] = {}
        self.fan_buttons: dict[str, RoundButton] = {}
        self.fan_status_button: RoundButton | None = None
        self.status_badge = QLabel("●  Cool • Idle")
        self.status_badge.setAlignment(Qt.AlignCenter)
        self.status_badge.setFont(font(11, QFont.Black))
        self.status_badge.setStyleSheet("color:#f6f8ff; background:transparent; border:0; padding:0;")
        self.outdoor = QLabel("OUTDOOR --°   WIND --")
        self.outdoor.setFont(font(9, QFont.Black, 20))
        self.outdoor.setStyleSheet("""
            color:#dce7fb;
            background:rgba(65,73,92,0.55);
            border:1px solid rgba(155,174,208,0.24);
            border-radius:14px;
            padding:7px 14px;
        """)
        self.door_countdown = QLabel("")
        self.door_countdown.setAlignment(Qt.AlignCenter)
        self.door_countdown.setFont(font(12, QFont.Black, 18))
        self.door_countdown.setMinimumWidth(330)
        self.door_countdown.setStyleSheet("""
            color:#fff4dc;
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 rgba(196,102,26,0.92),
                stop:1 rgba(58,36,20,0.92));
            border:2px solid rgba(255,215,135,0.68);
            border-radius:16px;
            padding:9px 18px;
            letter-spacing:1px;
        """)
        self.door_countdown.hide()
        self.sensor_warning_badge = QLabel("TEMP SENSOR WARNING")
        self.sensor_warning_badge.setAlignment(Qt.AlignCenter)
        self.sensor_warning_badge.setFont(font(9, QFont.Black, 10))
        self.sensor_warning_badge.setStyleSheet("""
            color:#fff1d6;
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                stop:0 rgba(190,91,22,0.94),
                stop:1 rgba(80,33,18,0.94));
            border:1px solid rgba(255,198,116,0.72);
            border-radius:13px;
            padding:7px 13px;
            letter-spacing:1px;
        """)
        self.sensor_warning_badge.hide()
        self.person_presence_strip = PersonPresenceStrip(self)
        self.person_presence_strip.hide()
        self.notice = ThermostatNoticeCard(self)
        self.notice.mousePressEvent = lambda event: self.show_auto_switch_menu()
        self.bypass_pill = ThermostatBypassBubble(self)
        self.bypass_pill.clicked.connect(self.bypass_changeover_lockout)
        self.bypass_pill.hide()
        self.away_overlay = QWidget(self)
        self.away_overlay.setObjectName("awayOverlay")
        self.away_overlay.setStyleSheet("""
            QWidget#awayOverlay {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(45,21,82,238),
                    stop:0.55 rgba(96,55,180,232),
                    stop:1 rgba(22,13,40,242));
                border:1px solid rgba(221,188,255,0.32);
                border-radius:32px;
            }
        """)
        away_lay = QVBoxLayout(self.away_overlay)
        away_lay.setContentsMargins(36, 32, 36, 32)
        away_lay.setSpacing(14)
        self.away_title = QLabel("AWAY MODE")
        self.away_title.setAlignment(Qt.AlignCenter)
        self.away_title.setFont(font(36, QFont.Black, 4))
        self.away_title.setStyleSheet("color:#ffffff; letter-spacing:4px; background:transparent; border:0;")
        self.away_body = QLabel("Tap to return Home")
        self.away_body.setAlignment(Qt.AlignCenter)
        self.away_body.setWordWrap(True)
        self.away_body.setFont(font(18, QFont.Black))
        self.away_body.setStyleSheet("color:#e7d8ff; background:transparent; border:0;")
        self.away_home_button = RoundButton("Return Home", active=True, min_h=62)
        self.away_home_button.setMinimumWidth(260)
        self.away_home_button.clicked.connect(self.return_home_from_away)
        away_lay.addStretch(1)
        away_lay.addWidget(self.away_title)
        away_lay.addWidget(self.away_body)
        away_lay.addSpacing(10)
        away_lay.addWidget(self.away_home_button, 0, Qt.AlignCenter)
        away_lay.addStretch(1)
        self.away_overlay.mousePressEvent = lambda event: self.return_home_from_away()
        self.away_overlay.hide()
        # The Doors tile remains a live status card, but also acts as a secure
        # close/lock action button. A full one-second hold opens assignment;
        # a normal tap sends the domain-appropriate Home Assistant action.
        self.door_card = InfoTile(
            "Doors",
            "CLOSED",
            "▯",
            good=True,
            hold_ms=1000,
            press_feedback=True,
        )
        self.alarm_card = InfoTile("Alarmo", "DISARMED", "盾", good=True)
        self._door_action_pending = False
        self.door_card.clicked.connect(self.run_door_action)
        self.door_card.held.connect(self.choose_door_action_entity)
        self.virtual_panel = VirtualOutputsPanel()
        self.virtual_temp_pending: float | None = None
        self._alarm_dialog_open = False
        self._alarm_reopen_block_until = 0.0
        self._alarm_open_refresh_running = False
        self._alarm_open_generation = 0
        self.virtual_temp_push_timer = QTimer(self)
        self.virtual_temp_push_timer.setSingleShot(True)
        self.virtual_temp_push_timer.timeout.connect(self.push_virtual_temp)
        self.virtual_panel.tempChanged.connect(self.set_virtual_temp)
        self.minus = IconCircle("−", "minus", 82)
        self.plus = IconCircle("+", "plus", 82)
        self.schedule_button = ScheduleClockButton()
        # Open only after release/click. The main-window touch filter already
        # provides edge tolerance and synthesizes click() after TouchEnd, so
        # opening this modal from pressed() only risks carrying an active X11
        # pointer grab into the dialog.
        self.schedule_button.clicked.connect(self.open_schedule_manager)

        root = QVBoxLayout(self)
        root.setContentsMargins(42, 18, 42, 34)
        root.setSpacing(0)
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        left_title = QVBoxLayout()
        left_title.setSpacing(8)
        left_title.addWidget(self.outdoor, 0, Qt.AlignLeft)
        self.title_label = QLabel(self.thermostat_title_text())
        self.title_label.setFont(font(49, QFont.Black))
        self.title_label.setStyleSheet("color:#ffffff;")
        left_title.addWidget(self.title_label)
        title_row.addLayout(left_title)
        title_row.addStretch(1)
        right_status = QVBoxLayout()
        right_status.setContentsMargins(0, 0, 0, 0)
        right_status.setSpacing(8)
        right_status.addWidget(self.door_countdown, 0, Qt.AlignRight | Qt.AlignTop)
        right_status.addWidget(self.person_presence_strip, 0, Qt.AlignRight | Qt.AlignTop)
        title_row.addLayout(right_status)
        title_row.addWidget(self.schedule_button, 0, Qt.AlignRight | Qt.AlignTop)
        root.addLayout(title_row)

        # Fixed-size side cards keep the Doors tile from drifting when the
        # status changes between CLOSED/OPEN and keep it visually locked to
        # Alarmo.
        self.door_card.setFixedSize(226, 164)
        self.alarm_card.setFixedSize(226, 164)

        # Keep the title/weather row in the normal page layout, but place the
        # thermostat controls in their own floating band.  The older VBox/Grid
        # layout centered the dial only inside the space left below the title,
        # which made the whole thermostat cluster sit too low on the colored
        # background.  This band is positioned from resizeEvent so the main
        # control centerline stays in the vertical middle of the page.
        root.addStretch(1)

        self.controls_band = QWidget(self)
        self.controls_band.setObjectName("thermostatControlsBand")
        self.controls_band.setStyleSheet("QWidget#thermostatControlsBand { background: transparent; border: 0; }")
        self.controls_band.setAttribute(Qt.WA_StyledBackground, False)

        mid = QGridLayout(self.controls_band)
        mid.setContentsMargins(42, 0, 42, 0)
        mid.setHorizontalSpacing(22)
        mid.setVerticalSpacing(4)
        mid.setColumnStretch(0, 3)
        mid.setColumnStretch(1, 1)
        mid.setColumnStretch(2, 5)
        mid.setColumnStretch(3, 1)
        mid.setColumnStretch(4, 3)
        mid.setRowMinimumHeight(0, 48)
        mid.setRowStretch(1, 1)
        # Actual spacer row below the dial row. This is intentionally a real
        # grid row instead of padding inside the button wrappers; Qt can collapse
        # or clip wrapper padding on the Pi touchscreen layout, making the move
        # look unchanged.
        self.thermostat_action_row_offset_px = 54
        mid.setRowMinimumHeight(2, self.thermostat_action_row_offset_px)
        mid.setRowMinimumHeight(3, 48)

        # Row 1 is the shared horizontal centerline:
        # Doors | minus | dial | plus | Alarmo.
        status_wrap = QWidget()
        status_lay = QHBoxLayout(status_wrap)
        status_lay.setContentsMargins(0, 0, 0, 0)
        status_lay.addStretch(1)
        status_lay.addWidget(self.status_badge, 0, Qt.AlignCenter)
        status_lay.addStretch(1)
        mid.addWidget(status_wrap, 0, 2, 1, 1, Qt.AlignCenter | Qt.AlignBottom)

        mid.addWidget(self.door_card, 1, 0, 1, 1, Qt.AlignCenter)
        mid.addWidget(self.minus, 1, 1, 1, 1, Qt.AlignCenter)
        mid.addWidget(self.dial, 1, 2, 1, 1, Qt.AlignCenter)
        mid.addWidget(self.plus, 1, 3, 1, 1, Qt.AlignCenter)
        mid.addWidget(self.alarm_card, 1, 4, 1, 1, Qt.AlignCenter)

        self.schedule_shortcuts = QScrollArea()
        self.schedule_shortcuts.setObjectName("thermostatScheduleShortcuts")
        self.schedule_shortcuts.setFixedHeight(44)
        self.schedule_shortcuts.setFrameShape(QFrame.NoFrame)
        self.schedule_shortcuts.setWidgetResizable(True)
        self.schedule_shortcuts.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.schedule_shortcuts.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.schedule_shortcuts.setStyleSheet("""
            QScrollArea#thermostatScheduleShortcuts {
                background:transparent;
                border:0;
            }
        """)
        self.schedule_shortcuts_content = QWidget()
        self.schedule_shortcuts_content.setStyleSheet("background:transparent; border:0;")
        self.schedule_shortcuts_lay = QHBoxLayout(self.schedule_shortcuts_content)
        self.schedule_shortcuts_lay.setContentsMargins(0, 2, 0, 2)
        self.schedule_shortcuts_lay.setSpacing(9)
        self.schedule_shortcuts.setWidget(self.schedule_shortcuts_content)
        self.schedule_shortcuts.hide()
        # Schedule hotkeys live in the spacer band directly above Off/Cool/Heat/Away.
        # The scroll area spans the full row, while its internal layout starts
        # at the left edge so each saved schedule becomes the next bubble.
        mid.addWidget(self.schedule_shortcuts, 2, 0, 1, 5, Qt.AlignBottom)

        mode_wrap = QWidget()
        mode_lay = QHBoxLayout(mode_wrap)
        mode_lay.setContentsMargins(0, 0, 0, 0)
        mode_lay.addStretch(1)
        mode_lay.addLayout(self._mode_bar())
        mode_lay.addStretch(1)
        mid.addWidget(mode_wrap, 3, 2, 1, 1, Qt.AlignCenter | Qt.AlignTop)

        fan_wrap = QWidget()
        fan_lay = QHBoxLayout(fan_wrap)
        fan_lay.setContentsMargins(0, 0, 0, 0)
        fan_lay.addStretch(1)
        fan_lay.addLayout(self._fan_bar())
        fan_lay.addStretch(1)
        mid.addWidget(fan_wrap, 3, 4, 1, 1, Qt.AlignCenter | Qt.AlignTop)

        # Keep these controls available for alerts, but do not let hidden/visible
        # optional widgets change the vertical position of Doors.
        self.bypass_pill.setParent(self)
        self.notice.setParent(self)
        self.virtual_panel.hide()
        self.virtual_panel.setParent(self)

        self.position_main_controls()

        self.minus.clicked.connect(lambda: self.change_target(-1))
        self.plus.clicked.connect(lambda: self.change_target(1))
        self.dial.targetChanged.connect(self.set_target_from_dial)
        # Doors already show their live state on the tile; no extra source toast is needed.
        self.alarm_card.clicked.connect(self.show_alarm_dialog)

        self.fx_phase = 0
        # Cache the full-screen procedural environment layer. The cache is
        # rebuilt only when the screen size, temperature palette, safety state,
        # or optional animation phase changes, keeping the richer background
        # inexpensive on Raspberry Pi-class hardware.
        self._environment_background_cache = QPixmap()
        self._environment_background_cache_key = None
        self.alert_banner = ThermostatActionBanner(self)
        self.alert_banner.dismissClicked.connect(self.dismiss_auto_switch)
        self.alert_banner.revertClicked.connect(self.revert_auto_switch)
        self.alert_banner.bypassClicked.connect(self.bypass_changeover_lockout)
        self.notice_action_popup = ThermostatNoticeActionPopup(self)
        self.notice_action_popup.revertClicked.connect(self.revert_auto_switch)
        self.notice_action_popup.dismissAutoClicked.connect(self.dismiss_auto_switch)
        self.notice_action_popup.dismissManualClicked.connect(self.dismiss_manual_override_notice)
        self.notice_action_popup.followClicked.connect(self.follow_manual_override)
        self.fx_timer = QTimer(self)
        self.fx_timer.timeout.connect(self.animate_environment)
        # Adaptive: idle thermostat screen should not repaint the whole page
        # several times a second. We retune this based on whether heat/cold
        # effects are actually visible.
        self.fx_timer.start(1000)
        self.notice.hide()
        QTimer.singleShot(0, self.position_alert_banner)

    def screen_control_locked(self) -> bool:
        return bool(getattr(self, "_screen_locked", False))

    def screen_security_locked(self) -> bool:
        return bool(getattr(self, "_screen_security_locked", False))

    def reject_locked_control(self) -> bool:
        if not self.screen_control_locked():
            return False
        if self.screen_security_locked():
            self.requestToast.emit("Security lock active: alarm control only")
        else:
            self.requestToast.emit("Screen locked: temperature and alarm controls only")
        return True

    def set_screen_locked(self, locked: bool, secure: bool = False):
        self._screen_locked = bool(locked)
        self._screen_security_locked = bool(self._screen_locked and secure)
        self.apply_screen_lock_state()

    def apply_screen_lock_state(self):
        """Apply guest-safe or security lockout while preserving Alarmo.

        This is repeated after each thermostat sync because schedule shortcuts
        and Away overlays can be recreated or shown from fresh runtime state.
        """
        locked = self.screen_control_locked()
        secure_locked = self.screen_security_locked()

        # These controls can change operating mode, fan mode, schedules, pause
        # behavior, diagnostics, or test values and are therefore unavailable.
        blocked_widgets = [
            self.dial,
            self.schedule_button,
            self.schedule_shortcuts,
            self.door_card,
            self.bypass_pill,
            self.notice,
            self.away_home_button,
            self.away_overlay,
            self.virtual_panel,
        ]
        for widget in blocked_widgets:
            widget.setEnabled(not locked)

        for button in self.mode_buttons.values():
            button.setEnabled(not locked)
        for button in self.fan_buttons.values():
            button.setEnabled(not locked)
        if self.fan_status_button is not None:
            self.fan_status_button.setEnabled(not locked)

        if hasattr(self, "alert_banner"):
            self.alert_banner.setEnabled(not locked)
            self.alert_banner.setAttribute(Qt.WA_TransparentForMouseEvents, locked)
        if hasattr(self, "notice_action_popup"):
            self.notice_action_popup.setEnabled(not locked)
            if locked and self.notice_action_popup.isVisible():
                self.notice_action_popup.hide()

        # Away normally uses a full-page Return Home overlay. Hide that overlay
        # while locked so the two permitted temperature buttons and Alarmo card
        # remain reachable. The selected Away mode pill still shows the state.
        if locked and self.away_overlay.isVisible():
            self.away_overlay.hide()

        # Guest lock keeps temperature +/- available. Security lock disables
        # them as well, leaving Alarmo as the only interactive thermostat card.
        # Preserve the existing rule that +/- are unavailable when the
        # thermostat is Off and not in Away.
        t = self.thermostat_view()
        mode = str(t.get("mode") or "cool").lower()
        setpoint_available = mode != "off" or bool(t.get("away"))
        self.minus.setEnabled(setpoint_available and not secure_locked)
        self.plus.setEnabled(setpoint_available and not secure_locked)
        self.alarm_card.setEnabled(True)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "fx_timer") and not self.fx_timer.isActive():
            self.fx_timer.start(1000)
        if hasattr(self, "retune_fx_timer"):
            self.retune_fx_timer()

    def hideEvent(self, event):
        if hasattr(self, "fx_timer") and self.fx_timer.isActive():
            self.fx_timer.stop()
        super().hideEvent(event)

    def value(self, key: str, default=None):
        src = self.thermostat if isinstance(self.thermostat, dict) else {}
        if key in src:
            return src.get(key)
        inner = src.get("thermostat") if isinstance(src.get("thermostat"), dict) else {}
        if key in inner:
            return inner.get(key)
        outputs = src.get("outputs") if isinstance(src.get("outputs"), dict) else {}
        if key in outputs:
            return outputs.get(key)
        return default

    def thermostat_view(self) -> dict:
        src = self.thermostat if isinstance(self.thermostat, dict) else {}
        inner = src.get("thermostat") if isinstance(src.get("thermostat"), dict) else {}
        merged = dict(inner)
        merged.update(src)
        if isinstance(src.get("outputs"), dict):
            merged["outputs"] = src.get("outputs")
        return merged

    def intimacy_hold_active(self, snapshot: dict | None = None) -> bool:
        t = snapshot if isinstance(snapshot, dict) else self.thermostat_view()
        hold = t.get("intimacyHold") if isinstance(t.get("intimacyHold"), dict) else {}
        if not bool(hold.get("active")):
            return False
        try:
            return float(hold.get("expiresAt") or 0) > time.time() * 1000
        except (TypeError, ValueError):
            return False

    def environment_effect_intensity(self, t: dict | None = None) -> float:
        t = t or self.thermostat_view()
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        safety = str(t.get("safetyMode") or outputs.get("safetyMode") or "").lower()
        if str(t.get("mode") or "").lower() == "off" and safety not in {"heat", "cool"}:
            return 0.0
        current = self.safe_float(t.get("currentTemp"), 70.0)
        low = self.safe_float(t.get("safetyLow"), 55.0)
        high = self.safe_float(t.get("safetyHigh"), 85.0)
        cold_ratio = 0.0
        if current <= 67.0:
            cold_ratio = clamp((68.0 - current) / 2.4, 0.0, 1.0)
            if current <= 66.0:
                cold_ratio = max(cold_ratio, 0.84)
        hot_ratio = 0.0
        if current >= 72.0:
            hot_ratio = clamp((current - 71.4) / 4.2, 0.0, 1.0)
            if current >= 72.0:
                hot_ratio = max(hot_ratio, 0.30)
            if current >= 76.0:
                hot_ratio = max(hot_ratio, 0.94)
        if current < low:
            cold_ratio = max(cold_ratio, 0.90)
        if current > high:
            hot_ratio = max(hot_ratio, 0.90)
        if cold_ratio > 0 and hot_ratio > 0:
            midpoint = (low + high) / 2 if high > low else 69.5
            if current <= midpoint:
                hot_ratio = 0.0
            else:
                cold_ratio = 0.0
        heat_mode_visual = bool(self.active_visual_mode() == "heat" and cold_ratio <= 0 and hot_ratio <= 0)
        return max(cold_ratio, hot_ratio, 0.38 if heat_mode_visual else 0.0)

    def background_animation_enabled(self) -> bool:
        """Return True only when optional ambient background pulsing is enabled.

        The futuristic environment is intentionally static by default and is
        cached as a single pixmap. Temperature changes rebuild the cache, but the
        panel does not continuously repaint the full screen unless a developer
        explicitly opts in with SMART_THERMOSTAT_BACKGROUND_ANIMATION=1.
        """
        value = str(os.environ.get("SMART_THERMOSTAT_BACKGROUND_ANIMATION") or "").strip().lower()
        return value in {"1", "true", "yes", "on", "full"}

    def retune_fx_timer(self):
        if not hasattr(self, "fx_timer"):
            return
        # Keep the timer at one second for door-pause/manual-delay countdown
        # text refreshes. Do not use a fast frame rate for the optional ambient
        # pulse; the cached background only advances every 1.5 seconds.
        interval = 1000
        if self.background_animation_enabled() and self.environment_effect_intensity() > 0.01:
            interval = 1500
        if self.fx_timer.interval() != interval:
            self.fx_timer.setInterval(interval)

    def animate_environment(self):
        animated = self.background_animation_enabled() and self.environment_effect_intensity() > 0.01
        if animated:
            self.fx_phase = (self.fx_phase + 1) % 10000
        else:
            # Static phase keeps glow nodes stable on occasional status refresh
            # paints while the low-resource default is active.
            self.fx_phase = 0
        self.update_door_pause_ui()
        self.update_alert_banner()
        self.update_status_badge()
        if animated:
            self.update()
        self.retune_fx_timer()
        if self.alert_banner.isVisible():
            self.alert_banner.raise_()
        if hasattr(self, "notice_action_popup") and self.notice_action_popup.isVisible():
            self.notice_action_popup.raise_()

    def safe_float(self, value, default=0.0):
        try:
            return float(value)
        except Exception:
            return default

    def show_notice_card(self, kind: str, heading: str, primary: str = "", badge: str = "", *, height: int = 118):
        self.notice.set_notice(kind, heading, primary, badge, height=height)
        self.notice.show()
        self.position_main_controls()
        self.notice.raise_()
        if hasattr(self, "notice_action_popup") and self.notice_action_popup.isVisible():
            self.notice_action_popup.raise_()

    def active_visual_mode(self) -> str:
        t = self.thermostat_view()
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        safety = str(t.get("safetyMode") or outputs.get("safetyMode") or "").lower()
        if safety in {"heat", "cool"}:
            return safety
        mode = str(t.get("mode") or "cool").lower()
        if mode in {"heat", "cool"}:
            return mode
        if mode == "off":
            return ""
        active = str(t.get("autoActiveMode") or t.get("activeMode") or "cool").lower()
        return active if active in {"heat", "cool"} else "cool"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_main_controls()
        self.position_alert_banner()
        self.position_away_overlay()
        self.position_notice_action_popup()

    def position_main_controls(self):
        """Center the five primary thermostat controls on one horizontal line."""
        if not hasattr(self, "controls_band"):
            return
        w = max(1, self.width())
        h = max(1, self.height())

        # Use most of the colored background height, but keep clear of the
        # weather/title area and bottom edge.  The dial itself sits in the
        # center row, so its center lands at the vertical center of this band.
        action_offset = int(getattr(self, "thermostat_action_row_offset_px", 0) or 0)
        base_band_h = min(max(360, int(h * 0.78)), max(260, h - 112))
        band_h = min(max(260, base_band_h + action_offset), max(260, h - 70))
        center_y = int(h * 0.51 + action_offset / 2)
        y = int(center_y - band_h / 2)
        y = max(58, min(y, max(58, h - band_h - 8)))

        self.controls_band.setGeometry(0, y, w, band_h)
        self.controls_band.raise_()

        # Temporary alert controls stay outside the grid so they do not push
        # the Doors card around.  The compact notice is explicitly positioned
        # above the Doors card, matching the clean left-side tile layout.
        pill_x = 58
        if self.notice.isVisible() and hasattr(self, "door_card"):
            try:
                door_pos = self.door_card.mapTo(self, QPoint(0, 0))
                notice_x = door_pos.x() + (self.door_card.width() - self.notice.width()) // 2
                notice_y = door_pos.y() - self.notice.height() - 12
            except Exception:
                notice_x = pill_x
                notice_y = y + 66
            notice_x = max(18, min(notice_x, max(18, w - self.notice.width() - 18)))
            notice_y = max(78, min(notice_y, max(78, h - self.notice.height() - 24)))
            self.notice.move(notice_x, notice_y)
            self.notice.raise_()
        if self.bypass_pill.isVisible():
            if hasattr(self, "door_card"):
                try:
                    door_pos = self.door_card.mapTo(self, QPoint(0, 0))
                    bx = door_pos.x() + (self.door_card.width() - self.bypass_pill.width()) // 2
                    by = door_pos.y() + self.door_card.height() + 12
                except Exception:
                    bx = pill_x
                    by = y + 230
            else:
                bx = pill_x
                by = y + 230
            bx = max(18, min(bx, max(18, w - self.bypass_pill.width() - 18)))
            by = max(70, min(by, max(70, h - self.bypass_pill.height() - 18)))
            self.bypass_pill.move(bx, by)
            self.bypass_pill.raise_()
        if hasattr(self, "sensor_warning_badge") and self.sensor_warning_badge.isVisible():
            self.sensor_warning_badge.adjustSize()
            warning_x = max(18, w - self.sensor_warning_badge.width() - 24)
            warning_y = 18
            self.sensor_warning_badge.move(warning_x, warning_y)
            self.sensor_warning_badge.raise_()
        if hasattr(self, "notice_action_popup") and self.notice_action_popup.isVisible():
            self.notice_action_popup.raise_()

    def position_away_overlay(self):
        if not hasattr(self, "away_overlay"):
            return
        margin = 30
        self.away_overlay.setGeometry(margin, margin, max(10, self.width() - margin * 2), max(10, self.height() - margin * 2))
        if self.away_overlay.isVisible():
            self.away_overlay.raise_()

    def position_notice_action_popup(self):
        if not hasattr(self, "notice_action_popup"):
            return
        self.notice_action_popup.setGeometry(self.rect())
        if self.notice_action_popup.isVisible():
            self.notice_action_popup.raise_()
            self.notice_action_popup.position_card()

    def position_alert_banner(self):
        if not hasattr(self, "alert_banner"):
            return
        kind = getattr(self.alert_banner, "kind", "")
        if kind == "lockout":
            w = min(470, max(390, self.width() - 160))
            self.alert_banner.setFixedWidth(w)
            self.alert_banner.adjustSize()
            x = min(max(280, self.width() // 4), max(12, self.width() - self.alert_banner.width() - 26))
            y = 104
        elif kind == "door-pause":
            w = min(640, max(500, self.width() - 140))
            self.alert_banner.setFixedWidth(w)
            self.alert_banner.adjustSize()
            x = max(12, (self.width() - self.alert_banner.width()) // 2)
            y = max(18, (self.height() - self.alert_banner.height()) // 2)
        else:
            w = min(520, max(420, self.width() - 120))
            self.alert_banner.setFixedWidth(w)
            self.alert_banner.adjustSize()
            x = max(12, (self.width() - self.alert_banner.width()) // 2)
            y = 72
        self.alert_banner.move(x, y)
        if self.alert_banner.isVisible():
            self.alert_banner.raise_()

    def _temperature_environment_palette(self, current: float, neutral_off: bool) -> dict[str, QColor]:
        """Return the smooth cold-to-hot palette used by the thermostat backdrop."""
        anchors = [
            (58.0, {
                "base0": (1, 5, 19), "base1": (2, 14, 45), "base2": (5, 29, 78),
                "primary": (25, 210, 255), "secondary": (65, 83, 255), "highlight": (145, 245, 255),
            }),
            (66.0, {
                "base0": (2, 5, 22), "base1": (5, 18, 58), "base2": (16, 38, 92),
                "primary": (40, 176, 255), "secondary": (91, 66, 255), "highlight": (120, 225, 255),
            }),
            (71.0, {
                "base0": (4, 5, 22), "base1": (11, 14, 53), "base2": (36, 23, 80),
                "primary": (45, 154, 255), "secondary": (165, 64, 255), "highlight": (99, 218, 255),
            }),
            (75.0, {
                "base0": (10, 4, 22), "base1": (40, 8, 54), "base2": (85, 17, 74),
                "primary": (73, 133, 255), "secondary": (245, 59, 209), "highlight": (255, 120, 210),
            }),
            (79.0, {
                "base0": (18, 3, 19), "base1": (68, 8, 39), "base2": (123, 26, 49),
                "primary": (238, 49, 156), "secondary": (255, 86, 57), "highlight": (255, 172, 83),
            }),
            (85.0, {
                "base0": (24, 2, 14), "base1": (87, 7, 27), "base2": (143, 25, 29),
                "primary": (255, 50, 83), "secondary": (255, 124, 38), "highlight": (255, 209, 104),
            }),
        ]

        def rgb_lerp(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
            amount = clamp(amount, 0.0, 1.0)
            return tuple(int(round(a[i] + (b[i] - a[i]) * amount)) for i in range(3))

        def sample(name: str) -> QColor:
            if current <= anchors[0][0]:
                rgb = anchors[0][1][name]
            elif current >= anchors[-1][0]:
                rgb = anchors[-1][1][name]
            else:
                rgb = anchors[-1][1][name]
                for index in range(len(anchors) - 1):
                    temp_a, palette_a = anchors[index]
                    temp_b, palette_b = anchors[index + 1]
                    if temp_a <= current <= temp_b:
                        amount = (current - temp_a) / max(0.001, temp_b - temp_a)
                        rgb = rgb_lerp(palette_a[name], palette_b[name], amount)
                        break
            color = QColor(*rgb)
            if neutral_off:
                # Off mode remains temperature-reactive, but is intentionally
                # quieter so the panel still communicates that HVAC is disabled.
                neutral = QColor(5, 9, 20)
                mix = 0.48 if name.startswith("base") else 0.28
                color = QColor(
                    int(color.red() * (1.0 - mix) + neutral.red() * mix),
                    int(color.green() * (1.0 - mix) + neutral.green() * mix),
                    int(color.blue() * (1.0 - mix) + neutral.blue() * mix),
                )
            return color

        return {name: sample(name) for name in ("base0", "base1", "base2", "primary", "secondary", "highlight")}

    @staticmethod
    def _alpha_color(color: QColor, alpha: int) -> QColor:
        return QColor(color.red(), color.green(), color.blue(), int(clamp(alpha, 0, 255)))

    def _render_environment_background(
        self,
        width: int,
        height: int,
        current: float,
        neutral_off: bool,
        safety_alert: bool,
        phase: int,
    ) -> QPixmap:
        """Draw the cached lightweight neo-futurist environment layer."""
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.transparent)
        p = QPainter(pixmap)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(0, 0, width, height)
        w = float(width)
        h = float(height)
        palette = self._temperature_environment_palette(current, neutral_off)
        base0 = palette["base0"]
        base1 = palette["base1"]
        base2 = palette["base2"]
        primary = palette["primary"]
        secondary = palette["secondary"]
        highlight = palette["highlight"]
        visual_scale = 0.58 if neutral_off else 1.0
        if safety_alert:
            visual_scale = min(1.15, visual_scale + 0.15)

        # Deep graphite/navy base. The color stops themselves interpolate with
        # room temperature, so every status refresh can move smoothly from an
        # icy cyan/blue environment to magenta, orange, and red.
        base = QLinearGradient(0, 0, w, h)
        base.setColorAt(0.0, base0)
        base.setColorAt(0.42, base1)
        base.setColorAt(0.76, base2)
        base.setColorAt(1.0, QColor(2, 4, 12))
        p.fillRect(r, base)

        # A very faint micro-grid gives the background depth without the busy
        # circuit-board appearance. It is fewer than thirty static lines.
        grid = self._alpha_color(highlight, int(12 * visual_scale))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(grid, 1.0))
        for index in range(1, 18):
            x = w * index / 18.0
            p.drawLine(QPointF(x, h * 0.10), QPointF(x, h * 0.94))
        for index in range(2, 12):
            y = h * index / 12.0
            p.drawLine(QPointF(w * 0.02, y), QPointF(w * 0.98, y))

        # Broad aurora fields. These are simple radial gradients—not blurred
        # images or shaders—and are only redrawn when the cache key changes.
        left_field = QRadialGradient(QPointF(w * 0.38, h * 0.48), w * 0.58)
        left_field.setColorAt(0.0, self._alpha_color(primary, int(92 * visual_scale)))
        left_field.setColorAt(0.36, self._alpha_color(primary, int(48 * visual_scale)))
        left_field.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, left_field)

        right_field = QRadialGradient(QPointF(w * 0.68, h * 0.46), w * 0.56)
        right_field.setColorAt(0.0, self._alpha_color(secondary, int(86 * visual_scale)))
        right_field.setColorAt(0.42, self._alpha_color(secondary, int(42 * visual_scale)))
        right_field.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, right_field)

        lower_field = QRadialGradient(QPointF(w * 0.54, h * 0.93), w * 0.56)
        lower_field.setColorAt(0.0, self._alpha_color(highlight, int(34 * visual_scale)))
        lower_field.setColorAt(0.48, self._alpha_color(secondary, int(18 * visual_scale)))
        lower_field.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, lower_field)

        # Temperature-reactive halo behind the main thermostat. This gives the
        # dial the requested focal-point wow factor without changing its layout.
        center = QPointF(w * 0.50, h * 0.515)
        halo_radius = min(w, h) * 0.48
        halo = QRadialGradient(center, halo_radius)
        halo.setColorAt(0.0, QColor(0, 0, 0, 0))
        halo.setColorAt(0.28, QColor(0, 0, 0, 0))
        halo.setColorAt(0.43, self._alpha_color(primary, int(88 * visual_scale)))
        halo.setColorAt(0.55, self._alpha_color(secondary, int(46 * visual_scale)))
        halo.setColorAt(0.74, self._alpha_color(secondary, int(14 * visual_scale)))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, halo)

        # A few clean holographic arcs frame the dial. They are ordinary vector
        # arcs and remain static unless the optional low-rate pulse is enabled.
        pulse = 0.78 + 0.22 * math.sin(phase * 0.18) if phase else 0.88
        ring_base = min(w, h) * 0.405
        arc_specs = (
            (ring_base, primary, 204, 152, 1.7, 78),
            (ring_base + 18, secondary, 326, 154, 1.3, 58),
            (ring_base + 34, highlight, 24, 122, 1.0, 38),
        )
        p.setBrush(Qt.NoBrush)
        for radius, color, start_deg, span_deg, pen_width, alpha in arc_specs:
            arc_rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
            p.setPen(QPen(self._alpha_color(color, int(alpha * visual_scale * pulse)), pen_width, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc_rect, int(start_deg * 16), int(span_deg * 16))

        def draw_light_path(path: QPainterPath, color: QColor, alpha: int = 58, width_px: float = 1.25):
            outer_alpha = max(4, int(alpha * 0.18 * visual_scale))
            p.setPen(QPen(self._alpha_color(color, outer_alpha), width_px * 6.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
            p.setPen(QPen(self._alpha_color(color, int(alpha * visual_scale)), width_px, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)

        # Sparse, flowing contour routes replace the old dense PCB traces. The
        # curves are deterministic and use only six QPainterPaths.
        contours: list[tuple[QPainterPath, QColor, int, float]] = []

        path = QPainterPath(QPointF(-w * 0.06, h * 0.43))
        path.cubicTo(QPointF(w * 0.10, h * 0.22), QPointF(w * 0.20, h * 0.62), QPointF(w * 0.36, h * 0.39))
        path.cubicTo(QPointF(w * 0.46, h * 0.23), QPointF(w * 0.55, h * 0.18), QPointF(w * 0.67, h * 0.31))
        path.cubicTo(QPointF(w * 0.78, h * 0.44), QPointF(w * 0.86, h * 0.20), QPointF(w * 1.06, h * 0.34))
        contours.append((path, primary, 66, 1.35))

        path = QPainterPath(QPointF(-w * 0.05, h * 0.73))
        path.cubicTo(QPointF(w * 0.14, h * 0.58), QPointF(w * 0.22, h * 0.91), QPointF(w * 0.40, h * 0.76))
        path.cubicTo(QPointF(w * 0.52, h * 0.66), QPointF(w * 0.64, h * 0.61), QPointF(w * 0.74, h * 0.72))
        path.cubicTo(QPointF(w * 0.84, h * 0.82), QPointF(w * 0.92, h * 0.58), QPointF(w * 1.05, h * 0.70))
        contours.append((path, secondary, 62, 1.45))

        path = QPainterPath(QPointF(w * 0.12, -h * 0.05))
        path.cubicTo(QPointF(w * 0.22, h * 0.09), QPointF(w * 0.23, h * 0.35), QPointF(w * 0.31, h * 0.39))
        path.cubicTo(QPointF(w * 0.38, h * 0.43), QPointF(w * 0.39, h * 0.18), QPointF(w * 0.48, h * 0.14))
        path.cubicTo(QPointF(w * 0.58, h * 0.10), QPointF(w * 0.62, h * 0.29), QPointF(w * 0.72, h * 0.26))
        contours.append((path, highlight, 42, 1.05))

        path = QPainterPath(QPointF(w * 0.36, h * 1.06))
        path.cubicTo(QPointF(w * 0.39, h * 0.86), QPointF(w * 0.49, h * 0.93), QPointF(w * 0.55, h * 0.82))
        path.cubicTo(QPointF(w * 0.63, h * 0.69), QPointF(w * 0.69, h * 0.91), QPointF(w * 0.78, h * 0.81))
        path.cubicTo(QPointF(w * 0.86, h * 0.72), QPointF(w * 0.89, h * 0.90), QPointF(w * 1.03, h * 0.84))
        contours.append((path, primary, 48, 1.10))

        path = QPainterPath(QPointF(w * 0.55, h * 0.08))
        path.cubicTo(QPointF(w * 0.69, h * 0.02), QPointF(w * 0.68, h * 0.24), QPointF(w * 0.79, h * 0.28))
        path.cubicTo(QPointF(w * 0.88, h * 0.31), QPointF(w * 0.88, h * 0.49), QPointF(w * 1.04, h * 0.45))
        contours.append((path, secondary, 48, 1.15))

        path = QPainterPath(QPointF(-w * 0.04, h * 0.89))
        path.cubicTo(QPointF(w * 0.18, h * 0.83), QPointF(w * 0.24, h * 1.00), QPointF(w * 0.44, h * 0.93))
        path.cubicTo(QPointF(w * 0.61, h * 0.87), QPointF(w * 0.78, h * 0.98), QPointF(w * 1.04, h * 0.91))
        contours.append((path, highlight, 34, 0.95))

        for contour, color, alpha, line_width in contours:
            draw_light_path(contour, color, alpha, line_width)

        # Restrained glow nodes and tiny particles provide depth. All positions
        # are deterministic, so the static default never flickers or drifts.
        node_specs = (
            (0.115, 0.350, primary, True), (0.265, 0.675, secondary, False),
            (0.355, 0.390, highlight, True), (0.585, 0.214, secondary, False),
            (0.680, 0.312, primary, True), (0.792, 0.718, highlight, True),
            (0.865, 0.286, secondary, False), (0.925, 0.835, primary, True),
        )
        p.setPen(Qt.NoPen)
        for index, (x_ratio, y_ratio, color, strong) in enumerate(node_specs):
            point = QPointF(w * x_ratio, h * y_ratio)
            node_pulse = (0.80 + 0.20 * math.sin(phase * 0.21 + index)) if phase else 0.92
            if strong:
                radius = 18.0 + 4.0 * node_pulse
                node_halo = QRadialGradient(point, radius)
                node_halo.setColorAt(0.0, self._alpha_color(color, int(122 * visual_scale * node_pulse)))
                node_halo.setColorAt(0.34, self._alpha_color(color, int(44 * visual_scale * node_pulse)))
                node_halo.setColorAt(1.0, QColor(0, 0, 0, 0))
                p.setBrush(QBrush(node_halo))
                p.drawEllipse(point, radius, radius)
            p.setBrush(self._alpha_color(color, int((170 if strong else 112) * visual_scale)))
            dot_radius = 2.3 if strong else 1.6
            p.drawEllipse(point, dot_radius, dot_radius)

        for index in range(28):
            x = w * (((index * 73 + 19) % 997) / 997.0)
            y = h * (0.13 + 0.76 * (((index * 151 + 31) % 991) / 991.0))
            color = primary if index % 3 else secondary
            alpha = int((18 + (index % 4) * 5) * visual_scale)
            p.setBrush(self._alpha_color(color, alpha))
            radius = 0.7 + (index % 3) * 0.25
            p.drawEllipse(QPointF(x, y), radius, radius)

        # Glass sheen and vignette preserve contrast for the existing title,
        # cards, buttons, and dial without changing their placement.
        sheen = QLinearGradient(0, 0, 0, h)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 13 if not neutral_off else 8))
        sheen.setColorAt(0.20, QColor(255, 255, 255, 3))
        sheen.setColorAt(0.56, QColor(255, 255, 255, 0))
        sheen.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, sheen)

        vignette = QRadialGradient(QPointF(w * 0.50, h * 0.49), max(w, h) * 0.82)
        vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
        vignette.setColorAt(0.57, QColor(0, 0, 0, 0))
        vignette.setColorAt(1.0, QColor(0, 0, 0, 92 if not neutral_off else 104))
        p.fillRect(r, vignette)

        p.end()
        return pixmap

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        r = self.rect()
        w = max(1, r.width())
        h = max(1, r.height())
        t = self.thermostat_view()
        current = self.safe_float(t.get("currentTemp"), 70.0)
        low = self.safe_float(t.get("safetyLow"), 55.0)
        high = self.safe_float(t.get("safetyHigh"), 85.0)
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        safety = str(t.get("safetyMode") or outputs.get("safetyMode") or "").lower()
        neutral_off = str(t.get("mode") or "").lower() == "off" and safety not in {"heat", "cool"}
        safety_alert = current < low or current > high
        animated = self.background_animation_enabled()
        phase = int(getattr(self, "fx_phase", 0) or 0) if animated else 0

        # Quantize only the cache key—not the palette calculation—to prevent
        # needless redraws from tiny sensor jitter while preserving smooth 0.1°F
        # visible transitions whenever the reported temperature meaningfully changes.
        cache_key = (
            w,
            h,
            round(current, 1),
            round(low, 1),
            round(high, 1),
            neutral_off,
            safety_alert,
            phase % 10000,
        )
        if self._environment_background_cache_key != cache_key or self._environment_background_cache.isNull():
            self._environment_background_cache = self._render_environment_background(
                w,
                h,
                current,
                neutral_off,
                safety_alert,
                phase,
            )
            self._environment_background_cache_key = cache_key

        p.drawPixmap(0, 0, self._environment_background_cache)
        p.end()
        super().paintEvent(event)

    def format_remaining(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        minutes, sec = divmod(seconds, 60)
        if minutes >= 60:
            h, m = divmod(minutes, 60)
            return f"{h}h {m:02d}m"
        return f"{minutes}m {sec:02d}s"

    def format_status_remaining(self, seconds: float) -> str:
        """Compact clock text for the small center HVAC status badge."""
        seconds = max(0, int(seconds + 0.999))
        minutes, sec = divmod(seconds, 60)
        if minutes >= 60:
            hours, minutes = divmod(minutes, 60)
            return f"{hours}:{minutes:02d}:{sec:02d}"
        return f"{minutes}:{sec:02d}"

    def minimum_runtime_status_suffix(self, t: dict | None = None, relays: dict | None = None) -> str:
        """Show remaining minimum-on time only while it is holding heat/cool on.

        When the setpoint is changed so the active side would normally shut off,
        the backend keeps the relay running until the configured minimum runtime
        expires and reports that as minimumCycleReason=minimum-runtime.  The
        center badge should make that visible without adding another popup.
        """
        t = t or self.thermostat_view()
        relays = relays if isinstance(relays, dict) else (t.get("relays") if isinstance(t.get("relays"), dict) else {})
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        reason = str(t.get("minimumCycleReason") or outputs.get("minimumCycleReason") or "").strip().lower()
        mode = str(t.get("minimumCycleMode") or outputs.get("minimumCycleMode") or "").strip().lower()
        until = self.safe_float(t.get("minimumCycleUntil") or outputs.get("minimumCycleUntil"), 0.0)
        now_ms = time.time() * 1000
        if mode not in {"heat", "cool"} or until <= now_ms:
            return ""
        if reason and reason != "minimum-runtime":
            return ""
        if mode == "heat" and not (bool(relays.get("heat")) or bool(outputs.get("heat")) or bool(t.get("heatRelayWasOn"))):
            return ""
        if mode == "cool" and not (bool(relays.get("cool")) or bool(outputs.get("cool")) or bool(t.get("coolRelayWasOn"))):
            return ""
        return f" {self.format_status_remaining((until - now_ms) / 1000)}"

    def predict_minimum_runtime_hold_for_target(self, snapshot: dict | None, new_target: float) -> dict:
        """Predict the center-badge minimum-runtime countdown immediately after a tap."""
        t = copy.deepcopy(snapshot) if isinstance(snapshot, dict) else self.thermostat_view()
        now_ms = time.time() * 1000
        try:
            current = self.safe_float(t.get("currentTemp"), 70.0)
            target = self.safe_float(new_target, self.safe_float(t.get("targetTemp"), 70.0))
            mode = str(t.get("mode") or "cool").strip().lower()
            active = str(t.get("autoActiveMode") or t.get("activeMode") or "").strip().lower() if mode == "auto" else mode
            relays = t.get("relays") if isinstance(t.get("relays"), dict) else {}
            outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}

            hold_mode = ""
            if active == "cool" and current <= target and (bool(relays.get("cool")) or bool(outputs.get("cool")) or bool(t.get("coolRelayWasOn"))):
                hold_mode = "cool"
            elif active == "heat" and current >= target and (bool(relays.get("heat")) or bool(outputs.get("heat")) or bool(t.get("heatRelayWasOn"))):
                hold_mode = "heat"
            if hold_mode not in {"heat", "cool"}:
                return {"until": 0, "mode": "", "reason": ""}

            started_key = "coolCycleStartedAt" if hold_mode == "cool" else "heatCycleStartedAt"
            minutes_key = "coolMinimumRuntimeMinutes" if hold_mode == "cool" else "heatMinimumRuntimeMinutes"
            started_at = self.safe_float(t.get(started_key), 0.0)
            if started_at <= 0:
                started_at = now_ms
            minutes = max(1.0, self.safe_float(t.get(minutes_key), 2.0))
            until = started_at + minutes * 60000
            if until <= now_ms:
                return {"until": 0, "mode": "", "reason": ""}
            return {"until": until, "mode": hold_mode, "reason": "minimum-runtime"}
        except Exception:
            return {"until": 0, "mode": "", "reason": ""}

    def apply_local_minimum_runtime_prediction(self, snapshot: dict | None, new_target: float) -> None:
        prediction = self.predict_minimum_runtime_hold_for_target(snapshot, new_target)
        if not isinstance(self.s.thermostat, dict):
            return
        if prediction.get("until"):
            self.s.thermostat["minimumCycleUntil"] = prediction["until"]
            self.s.thermostat["minimumCycleMode"] = prediction["mode"]
            self.s.thermostat["minimumCycleReason"] = prediction["reason"]
            outputs = self.s.thermostat.setdefault("outputs", {})
            if isinstance(outputs, dict):
                outputs["minimumCycleUntil"] = prediction["until"]
                outputs["minimumCycleMode"] = prediction["mode"]
                outputs["minimumCycleReason"] = prediction["reason"]
        else:
            self.s.thermostat["minimumCycleUntil"] = 0
            self.s.thermostat["minimumCycleMode"] = ""
            self.s.thermostat["minimumCycleReason"] = ""
            outputs = self.s.thermostat.get("outputs") if isinstance(self.s.thermostat.get("outputs"), dict) else None
            if isinstance(outputs, dict):
                outputs["minimumCycleUntil"] = 0
                outputs["minimumCycleMode"] = ""
                outputs["minimumCycleReason"] = ""

    def pending_equipment_status_text(self, t: dict | None = None, relays: dict | None = None) -> str:
        """Return visible Heat/Cool wait text when equipment is intentionally held off."""
        t = t or self.thermostat_view()
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        now_ms = time.time() * 1000

        pending = str(t.get("manualPendingMode") or outputs.get("pendingMode") or "").strip().lower()
        until = self.safe_float(t.get("manualLockoutUntil") or outputs.get("manualLockoutUntil"), 0.0)
        if pending in {"heat", "cool"} and until > now_ms:
            return f"{pending.capitalize()} Delay ({self.format_status_remaining((until - now_ms) / 1000)})"

        reason = str(t.get("minimumCycleReason") or outputs.get("minimumCycleReason") or "").strip().lower()
        mode = str(t.get("minimumCycleMode") or outputs.get("minimumCycleMode") or "").strip().lower()
        until = self.safe_float(t.get("minimumCycleUntil") or outputs.get("minimumCycleUntil"), 0.0)
        if reason == "minimum-off" and mode in {"heat", "cool"} and until > now_ms:
            return f"{mode.capitalize()} Wait ({self.format_status_remaining((until - now_ms) / 1000)})"
        return ""

    def update_status_badge(self):
        if not hasattr(self, "status_badge"):
            return
        t = self.thermostat_view()
        mode = str(t.get("mode") or "cool").lower()
        away = bool(t.get("away"))
        if mode == "auto":
            active = str(t.get("autoActiveMode") or t.get("activeMode") or "cool").lower()
            mode = active if active in {"heat", "cool"} else "cool"
        relays = t.get("relays") if isinstance(t.get("relays"), dict) else {}
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        equipment = "Idle"
        if relays.get("cool") or outputs.get("cool"):
            equipment = "Cooling" + self.minimum_runtime_status_suffix(t, relays)
        elif relays.get("heat") or outputs.get("heat"):
            equipment = "Heating" + self.minimum_runtime_status_suffix(t, relays)
        else:
            pending_text = self.pending_equipment_status_text(t, relays)
            if pending_text:
                equipment = pending_text
            elif t.get("coolingFanHold") or t.get("coolFanHoldUntil"):
                equipment = "Idle • Fan Hold"
            elif relays.get("fan"):
                equipment = "Fan"
        mode_label = "Away" if away else "Arriving" if self.arriving_override_active(t) else mode.capitalize()
        text = f"• {mode_label} • {equipment}"
        if self.status_badge.text() != text:
            self.status_badge.setText(text)

    def pause_entry_name(self, entry: dict | None, fallback: str = "Door") -> str:
        if not isinstance(entry, dict):
            return fallback
        entity_id = str(entry.get("entityId") or entry.get("entity_id") or "").strip()

        # Prefer Home Assistant's friendly name over the stored picker/entity
        # label. Older configs can carry the raw entity id in name, which made
        # the comfort-pause popup say binary_sensor.whatever instead of the
        # HA friendly name.
        for key in ("friendlyName", "friendly_name", "haName", "name"):
            value = str(entry.get(key) or "").strip()
            if value and value != entity_id:
                return value
        for key in ("name", "friendlyName", "friendly_name", "haName", "entityId", "entity_id"):
            value = str(entry.get(key) or "").strip()
            if value:
                return value
        return fallback

    def comfort_pause_countdown_allowed(self, t: dict | None = None, pause: dict | None = None) -> bool:
        t = t or self.thermostat_view()
        pause = pause if isinstance(pause, dict) else (t.get("pauseFunction") if isinstance(t.get("pauseFunction"), dict) else {})
        if "countdownAllowed" in pause:
            return bool(pause.get("countdownAllowed"))
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        mode = str(t.get("mode") or "").lower()
        active = str(t.get("autoActiveMode") or "").lower() if mode == "auto" else mode
        return (active == "cool" and bool(outputs.get("cool"))) or (active == "heat" and bool(outputs.get("heat")))

    def update_alert_banner(self):
        if not hasattr(self, "alert_banner"):
            return
        t = self.thermostat_view()
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        current = self.safe_float(t.get("currentTemp"), 70.0)
        safety_mode = str(t.get("safetyMode") or outputs.get("safetyMode") or "").lower()
        low = self.safe_float(t.get("safetyLow"), 55.0)
        high = self.safe_float(t.get("safetyHigh"), 85.0)
        if not safety_mode:
            if current < low and not bool(t.get("heatLocked")):
                safety_mode = "heat"
            elif current > high and not bool(t.get("coolLocked")):
                safety_mode = "cool"
        if safety_mode in {"heat", "cool"}:
            self.hide_notice_action_popup()
            self.bypass_pill.hide()
            if safety_mode == "heat":
                title = "Safety Heat Engaged"
                body = f"Room is {fmt_temp(current)}. Heating will stay active until the room is back above {fmt_temp(low)}."
            else:
                title = "Safety Cool Engaged"
                body = f"Room is {fmt_temp(current)}. Cooling will stay active until the room is back below {fmt_temp(high)}."
            self.notice.hide()
            self.alert_banner.set_alert("safety", title, body, dismiss=False, revert=False, bypass=False)
            self.position_alert_banner()
            return

        pause = t.get("pauseFunction") if isinstance(t.get("pauseFunction"), dict) else {}
        snooze_until = self.safe_float(pause.get("snoozeUntil"), 0.0)
        door_pause_active = bool(pause.get("active")) and not (snooze_until > time.time() * 1000)
        if door_pause_active:
            self.hide_notice_action_popup()
            self.bypass_pill.hide()
            self.notice.hide()
            entries = pause.get("entries") if isinstance(pause.get("entries"), list) else []
            open_entries = [e for e in entries if isinstance(e, dict) and self.pause_entry_is_open(e)]
            door_name = self.pause_entry_name(open_entries[0], "Selected door") if open_entries else "Selected door"
            resume_minutes = int(max(1, min(60, round(self.safe_float(pause.get("durationMinutes"), 5.0)))))
            minute_label = "Minute" if resume_minutes == 1 else "Minutes"
            self.alert_banner.set_alert(
                "door-pause",
                "Comfort Paused",
                f"{door_name} is open.\nComfort is paused and the away setpoint is being used until it closes.",
                dismiss=False,
                revert=False,
                bypass=True,
                bypass_text=f"Resume {resume_minutes} {minute_label}",
            )
            self.position_alert_banner()
            return

        now_ms = time.time() * 1000
        pending = str(t.get("manualPendingMode") or outputs.get("pendingMode") or "").lower()
        until = self.safe_float(t.get("manualLockoutUntil") or outputs.get("manualLockoutUntil"), 0.0)
        auto_pending = str(t.get("autoPendingMode") or "").lower()
        auto_until = self.safe_float(t.get("autoLockoutUntil"), 0.0)
        if pending in {"heat", "cool"} and until > now_ms:
            self.hide_notice_action_popup()
            self.alert_banner.hide()
            remaining = self.format_remaining((until - now_ms) / 1000)
            selected = pending.capitalize()
            self.show_notice_card("purple", "CHANGEOVER DELAY", f"To {selected}", remaining, height=118)
            self.bypass_pill.set_kind("purple")
            self.bypass_pill.setText("Bypass")
            self.bypass_pill.show()
            self.position_main_controls()
            return
        if auto_pending in {"heat", "cool"} and auto_until > now_ms:
            self.hide_notice_action_popup()
            self.alert_banner.hide()
            remaining = self.format_remaining((auto_until - now_ms) / 1000)
            selected = auto_pending.capitalize()
            self.show_notice_card("heat" if auto_pending == "heat" else "cool", "AUTO DELAY", f"To {selected}", remaining, height=118)
            self.bypass_pill.set_kind("heat" if auto_pending == "heat" else "cool")
            self.bypass_pill.setText("Bypass")
            self.bypass_pill.show()
            self.position_main_controls()
            return

        hold = t.get("autoSwitchHold") if isinstance(t.get("autoSwitchHold"), dict) else {}
        if hold.get("active") and str(hold.get("source") or "").lower() == "manual" and not hold.get("dismissed"):
            manual_mode = str(hold.get("mode") or "").lower()
            suggested = str(hold.get("suggestedMode") or "").lower()
            if manual_mode in {"heat", "cool"} and suggested in {"heat", "cool"} and manual_mode != suggested:
                self.bypass_pill.hide()
                self.alert_banner.hide()
                self.show_notice_card("purple", "MANUAL OVERRIDE", f"{manual_mode.capitalize()} allowed", f"AUTO WOULD {suggested.upper()}", height=118)
                return

        notice = t.get("autoSwitchNotice") if isinstance(t.get("autoSwitchNotice"), dict) else {}
        if notice.get("active"):
            self.bypass_pill.hide()
            self.alert_banner.hide()
            to_mode = str(notice.get("toMode") or self.active_visual_mode()).lower()
            switch_temp = notice.get("switchTemp") or current
            mode_label = to_mode.capitalize() if to_mode in {"heat", "cool"} else "Auto"
            self.show_notice_card("heat" if to_mode == "heat" else "cool", "AUTO-SWITCHED", f"To {mode_label}", f"INSIDE {fmt_temp(switch_temp)}", height=118)
            return
        self.hide_notice_action_popup()
        self.bypass_pill.hide()
        self.notice.hide()
        self.alert_banner.hide()

    def hide_notice_action_popup(self):
        if hasattr(self, "notice_action_popup") and self.notice_action_popup.isVisible():
            self.notice_action_popup.hide()

    def show_auto_switch_menu(self):
        if self.reject_locked_control():
            return
        t = self.thermostat_view()
        notice = t.get("autoSwitchNotice") if isinstance(t.get("autoSwitchNotice"), dict) else {}
        hold = t.get("autoSwitchHold") if isinstance(t.get("autoSwitchHold"), dict) else {}
        if not notice.get("active") and not (hold.get("active") and str(hold.get("source") or "").lower() == "manual"):
            return
        if not hasattr(self, "notice_action_popup"):
            return
        if notice.get("active"):
            from_mode = str(notice.get("fromMode") or "").lower()
            to_mode = str(notice.get("toMode") or self.active_visual_mode()).lower()
            switch_temp = notice.get("switchTemp") or self.safe_float(t.get("currentTemp"), 70.0)
            self.notice_action_popup.show_auto_switch(from_mode, to_mode, switch_temp)
        else:
            manual_mode = str(hold.get("mode") or "").lower()
            suggested = str(hold.get("suggestedMode") or "").lower()
            self.notice_action_popup.show_manual_override(manual_mode, suggested)

    def follow_manual_override(self, mode: str):
        if mode in {"heat", "cool"}:
            self.set_mode(mode)

    def dismiss_auto_switch(self):
        if self.reject_locked_control():
            return
        self.hide_notice_action_popup()
        t = self.thermostat_view()
        notice = t.get("autoSwitchNotice") if isinstance(t.get("autoSwitchNotice"), dict) else {}
        dismissed = {}
        if notice.get("active"):
            dismissed = {
                "active": True,
                "source": str(notice.get("source") or "").lower(),
                "fromMode": str(notice.get("fromMode") or "").lower(),
                "toMode": str(notice.get("toMode") or "").lower(),
                "coolTarget": notice.get("coolTarget") or 0,
                "heatTarget": notice.get("heatTarget") or 0,
                "dismissedAt": int(time.time() * 1000),
            }
        cleared_notice = {"active": False, "source": "", "fromMode": "", "toMode": "", "switchTemp": 0, "outdoorTemp": 0, "coolTarget": 0, "heatTarget": 0, "createdAt": 0}
        try:
            # Update the local copy first so the card does not reappear during
            # the round trip to the backend or the next status poll.
            self.s.thermostat["autoSwitchNotice"] = copy.deepcopy(cleared_notice)
            if dismissed:
                self.s.thermostat["autoSwitchNoticeDismissed"] = copy.deepcopy(dismissed)
            self.s.pause_status_refresh(2.5)
            self.sync(self.s.config, self.s.thermostat)

            changes = {"autoSwitchNotice": cleared_notice}
            if dismissed:
                changes["autoSwitchNoticeDismissed"] = dismissed
            def done(result):
                if isinstance(result, dict):
                    self.s.ingest_thermostat(result)
                    self.sync(self.s.config, self.s.thermostat)

            self.run_async(
                "dismiss-auto-switch",
                lambda: self.s.api.thermostat_update(changes),
                done,
                lambda err: self.requestToast.emit(f"Dismiss failed: {err}"),
            )
        except Exception as exc:
            self.requestToast.emit(f"Dismiss failed: {exc}")

    def dismiss_manual_override_notice(self):
        if self.reject_locked_control():
            return
        self.hide_notice_action_popup()
        t = self.thermostat_view()
        hold = t.get("autoSwitchHold") if isinstance(t.get("autoSwitchHold"), dict) else {}
        if not hold.get("active"):
            return
        try:
            # Mark it dismissed locally before the API round trip so the small
            # card does not pop back up on the next refresh. The backend still
            # keeps the manual hold active; dismiss only hides the notice.
            next_hold = dict(hold)
            next_hold["dismissed"] = True
            self.s.thermostat["autoSwitchHold"] = copy.deepcopy(next_hold)
            self.s.pause_status_refresh(2.5)
            self.sync(self.s.config, self.s.thermostat)
            changes = {"autoSwitchHold": next_hold}

            def done(result):
                if isinstance(result, dict):
                    self.s.ingest_thermostat(result)
                    self.sync(self.s.config, self.s.thermostat)

            self.run_async(
                "dismiss-manual-override",
                lambda: self.s.api.thermostat_update(changes),
                done,
                lambda err: self.requestToast.emit(f"Dismiss failed: {err}"),
            )
        except Exception as exc:
            self.requestToast.emit(f"Dismiss failed: {exc}")

    def revert_auto_switch(self):
        if self.reject_locked_control():
            return
        self.hide_notice_action_popup()
        t = self.thermostat_view()
        notice = t.get("autoSwitchNotice") if isinstance(t.get("autoSwitchNotice"), dict) else {}
        from_mode = str(notice.get("fromMode") or "").lower()
        if from_mode not in {"heat", "cool"}:
            self.dismiss_auto_switch()
            return
        try:
            changes = {
                "mode": from_mode,
                "modeChangeSource": "panel",
                "away": False,
                "autoSwitchNotice": {"active": False, "source": "", "fromMode": "", "toMode": "", "switchTemp": 0, "outdoorTemp": 0, "coolTarget": 0, "heatTarget": 0, "createdAt": 0},
                "autoSwitchHold": {"active": True, "source": "manual", "mode": from_mode, "until": int(time.time() * 1000) + 600000, "reason": "revert"},
            }
            # Optimistic mode feedback keeps the button responsive while the
            # authoritative update runs off the Qt event loop.
            self.s.thermostat.update(copy.deepcopy(changes))
            self.sync(self.s.config, self.s.thermostat)

            def done(result):
                if isinstance(result, dict):
                    self.s.ingest_thermostat(result)
                    self.sync(self.s.config, self.s.thermostat)

            self.run_async(
                "revert-auto-switch",
                lambda: self.s.api.thermostat_update(changes),
                done,
                lambda err: self.requestToast.emit(f"Revert failed: {err}"),
            )
        except Exception as exc:
            self.requestToast.emit(f"Revert failed: {exc}")

    def bypass_changeover_lockout(self):
        if self.reject_locked_control():
            return
        if getattr(self.alert_banner, "kind", "") == "door-pause":
            self.snooze_door_pause()
            return
        t = self.thermostat_view()
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        pending = str(t.get("manualPendingMode") or outputs.get("pendingMode") or t.get("autoPendingMode") or "").lower()
        if pending not in {"heat", "cool"}:
            self.requestToast.emit("No changeover delay active")
            return
        try:
            changes = {
                "manualPendingMode": "",
                "manualLockoutUntil": 0,
                "autoPendingMode": "",
                "autoLockoutUntil": 0,
                "bypassChangeoverLockout": pending,
            }
            if str(t.get("mode") or "").lower() == "auto":
                changes["autoActiveMode"] = pending
            else:
                changes["mode"] = pending
                changes["modeChangeSource"] = "panel"
            self.s.thermostat.update(copy.deepcopy(changes))
            self.sync(self.s.config, self.s.thermostat)

            def done(result):
                if isinstance(result, dict):
                    self.s.ingest_thermostat(result)
                    self.sync(self.s.config, self.s.thermostat)

            self.run_async(
                "bypass-changeover",
                lambda: self.s.api.thermostat_update(changes),
                done,
                lambda err: self.requestToast.emit(f"Bypass failed: {err}"),
            )
        except Exception as exc:
            self.requestToast.emit(f"Bypass failed: {exc}")

    def pause_entry_is_open(self, entry: dict) -> bool:
        if not isinstance(entry, dict):
            return False
        state = str(entry.get("state") or "").strip().lower()
        domain = str(entry.get("domain") or "").strip().lower()
        if entry.get("isClosed") is True:
            return False
        if entry.get("isClosed") is False:
            return True
        if domain == "cover":
            if state in {"open", "opening"}:
                return True
            if state in {"closed", "closing"}:
                return False
            try:
                pos = entry.get("currentPosition")
                if pos is not None:
                    return float(pos) > 0
            except Exception:
                return False
        return state in {"on", "open", "opened", "opening", "true", "1", "detected", "triggered"}

    def selected_pause_entry(self) -> dict | None:
        pause = self.thermostat_view().get("pauseFunction")
        if isinstance(pause, dict):
            entries = pause.get("entries") if isinstance(pause.get("entries"), list) else []
            for entry in entries:
                if isinstance(entry, dict) and str(entry.get("entityId") or entry.get("entity_id") or "").strip():
                    return entry

        # Older/saved configs can have the chosen door entry saved under the
        # Home Assistant integration while the live thermostat pauseFunction has
        # not caught up yet. Fall back to that saved doorEntity so the homepage
        # tile does not incorrectly say NOT SET after the user already chose one.
        try:
            ha = self.s.ha()
            door = ha.get("doorEntity") if isinstance(ha, dict) else None
            if isinstance(door, dict) and str(door.get("entityId") or door.get("entity_id") or "").strip():
                return door
        except Exception:
            pass
        return None

    def selected_door_action_entity(self) -> dict | None:
        """Return the Home Assistant entity used when the Doors tile is tapped.

        This is intentionally separate from ``doorEntity``/``pauseFunction``.
        The status entry can remain a binary sensor while the action points to a
        button, switch, lock, or cover that actually secures the opening.
        """
        try:
            ha = self.s.ha()
            action = ha.get("doorActionEntity") if isinstance(ha, dict) else None
            if isinstance(action, dict) and str(action.get("entityId") or action.get("entity_id") or "").strip():
                return action
        except Exception:
            pass
        return None

    @staticmethod
    def door_action_for_domain(domain: str) -> tuple[str, str]:
        """Map an assigned entity to a one-way close/secure action.

        Avoid toggling wherever Home Assistant exposes a deterministic command:
        covers close, locks lock, and switches turn off. Buttons remain a press
        because Home Assistant defines them as momentary actions.
        """
        domain = str(domain or "").strip().lower()
        if domain == "cover":
            return "close", "Closing"
        if domain == "lock":
            return "lock", "Locking"
        if domain in {"button", "input_button"}:
            return "press", "Pressing"
        return "off", "Turning off"

    def choose_door_action_entity(self):
        if self.reject_locked_control():
            return
        if getattr(self, "_door_action_picker_loading", False):
            return

        allowed_domains = {"switch", "lock", "cover", "button", "input_button"}
        stored: list[dict] = []
        ha = self.s.ha()
        if isinstance(ha, dict):
            current = ha.get("doorActionEntity")
            if isinstance(current, dict):
                stored.append(copy.deepcopy(current))
            available = ha.get("doorActionAvailableEntities")
            if isinstance(available, list):
                stored.extend(copy.deepcopy(item) for item in available if isinstance(item, dict))

        def open_picker(fresh: list[dict] | None = None, load_error: str = ""):
            self._door_action_picker_loading = False
            by_id: dict[str, dict] = {}
            # Saved records provide an offline fallback; fresh HA records overwrite
            # them so the picker uses current friendly names and domains.
            for item in list(stored) + list(fresh or []):
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
                if not entity_id:
                    continue
                domain = str(item.get("domain") or (entity_id.split(".", 1)[0] if "." in entity_id else "")).strip().lower()
                if domain not in allowed_domains:
                    continue
                name = str(item.get("friendlyName") or item.get("friendly_name") or item.get("name") or entity_id).strip() or entity_id
                by_id[entity_id] = {
                    "entityId": entity_id,
                    "name": name,
                    "friendlyName": name,
                    "domain": domain,
                    "state": str(item.get("state") or "unknown"),
                }

            entities = list(by_id.values())
            if not entities:
                message = "No Home Assistant switch, lock, cover, or button entities were found."
                if load_error:
                    message += f"\n\nHome Assistant lookup failed: {load_error}"
                QMessageBox.warning(self, "Door Action", message)
                return

            dlg = EntityPickerDialog("Choose Door Action", entities, self)
            current_action = self.selected_door_action_entity() or {}
            current_id = str(current_action.get("entityId") or current_action.get("entity_id") or "").strip()
            if current_id:
                for row in range(dlg.list.count()):
                    item = dlg.list.item(row)
                    data = item.data(Qt.UserRole) if item is not None else None
                    if isinstance(data, dict) and str(data.get("entityId") or "") == current_id:
                        dlg.list.setCurrentItem(item)
                        dlg.list.scrollToItem(item)
                        break

            def apply(entity: dict):
                try:
                    entity_id = str(entity.get("entityId") or entity.get("entity_id") or "").strip()
                    if not entity_id:
                        return
                    domain = str(entity.get("domain") or (entity_id.split(".", 1)[0] if "." in entity_id else "")).strip().lower()
                    if domain not in allowed_domains:
                        raise ValueError("Choose a switch, lock, cover, or button entity")
                    name = str(entity.get("friendlyName") or entity.get("friendly_name") or entity.get("name") or entity_id).strip() or entity_id
                    selected = {
                        "entityId": entity_id,
                        "name": name,
                        "friendlyName": name,
                        "domain": domain,
                        "state": str(entity.get("state") or by_id.get(entity_id, {}).get("state") or "unknown"),
                    }
                    config_ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                    config_ha["doorActionEntity"] = selected
                    config_ha["doorActionAvailableEntities"] = [selected] + [
                        item for item in entities if str(item.get("entityId") or "") != entity_id
                    ]
                    snapshot = copy.deepcopy(self.s.config)
                    _action, verb = self.door_action_for_domain(domain)

                    def saved(result):
                        if isinstance(result, dict) and isinstance(result.get("config"), dict):
                            self.s.config = result.get("config")
                        self.requestToast.emit(f"Doors action: {verb.lower()} {name}")

                    self.run_async(
                        "door-action-save",
                        lambda: self.s.api.save_config(snapshot),
                        saved,
                        lambda err: self.requestToast.emit(f"Door action save failed: {err}"),
                    )
                except Exception as exc:
                    QMessageBox.warning(self, "Door Action", str(exc))

            dlg.selected.connect(apply)
            dlg.exec_()

        self._door_action_picker_loading = True
        self.requestToast.emit("Loading door actions…")
        payload = self.s.ha_payload({"domains": ["switch", "lock", "cover", "button", "input_button"]})

        def loaded(result):
            fresh = result.get("entities") or [] if isinstance(result, dict) else []
            open_picker(fresh, "")

        def failed(error):
            open_picker([], str(error or ""))

        self.run_async(
            "door-action-entities",
            lambda: self.s.api.post("/api/ha/entities", payload),
            loaded,
            failed,
        )

    def run_door_action(self):
        if self.reject_locked_control():
            return
        if self._door_action_pending:
            self.requestToast.emit("Door action is still being sent…")
            return

        entity = self.selected_door_action_entity()
        if not entity:
            self.requestToast.emit("Press and hold Doors for 1 second to assign an action")
            return

        entity_id = str(entity.get("entityId") or entity.get("entity_id") or "").strip()
        domain = str(entity.get("domain") or (entity_id.split(".", 1)[0] if "." in entity_id else "")).strip().lower()
        if not entity_id or domain not in {"switch", "lock", "cover", "button", "input_button"}:
            self.requestToast.emit("The assigned door action is no longer valid. Press and hold to reassign it.")
            return

        action, verb = self.door_action_for_domain(domain)
        name = str(entity.get("friendlyName") or entity.get("name") or entity_id).strip() or entity_id
        self._door_action_pending = True
        payload = self.s.ha_payload({"entityId": entity_id, "action": action})

        def done(result):
            self._door_action_pending = False
            control = result.get("control") or {} if isinstance(result, dict) else {}
            if isinstance(control, dict) and control:
                entity["state"] = str(control.get("state") or entity.get("state") or "unknown")
                entity["domain"] = str(control.get("domain") or domain)
            # The Doors card already provides immediate press/release feedback,
            # so a successful Home Assistant acknowledgement does not need a
            # second toast at the bottom of the screen.

        def failed(error):
            self._door_action_pending = False
            self.requestToast.emit(f"Door action failed: {error}")

        self.run_async(
            "door-action",
            lambda: self.s.api.post("/api/ha/room/action", payload),
            done,
            failed,
        )

    def update_door_pause_ui(self):
        t = self.thermostat_view()
        pause = t.get("pauseFunction") if isinstance(t.get("pauseFunction"), dict) else {}
        entry = self.selected_pause_entry()
        if not entry:
            self.door_card.title = "Doors"
            self.door_card.setGood(False)
            self.door_card.setValue("NOT SET")
            self.door_countdown.hide()
            return
        open_state = self.pause_entry_is_open(entry)
        self.door_card.title = "Doors"
        self.door_card.setGood(not open_state)
        self.door_card.setValue("OPEN" if open_state else "CLOSED")
        if not open_state:
            self.door_countdown.hide()
            return

        now_ms = int(time.time() * 1000)
        name = self.pause_entry_name(entry, "Door")
        short_name = compact_name(name, 30).upper()
        duration_ms = int(max(1, min(60, float(pause.get("durationMinutes") or 5))) * 60000)
        snooze_until = int(float(pause.get("snoozeUntil") or 0))
        if snooze_until > now_ms:
            remaining = self.format_remaining((snooze_until - now_ms) / 1000)
            self.door_countdown.setText(f"{short_name} OPEN\nRESUMED {remaining}")
            self.door_countdown.show()
            return
        if pause.get("active"):
            self.door_countdown.setText(f"{short_name} OPEN\nCOMFORT PAUSED")
            self.door_countdown.show()
            return
        if not self.comfort_pause_countdown_allowed(t, pause):
            self.door_countdown.setText(f"{short_name} OPEN\nWAITING FOR ACTIVE HEAT/COOL")
            self.door_countdown.show()
            return
        opened_at = int(float(entry.get("openedAt") or now_ms))
        remaining = self.format_remaining(max(0, (opened_at + duration_ms - now_ms) / 1000))
        self.door_countdown.setText(f"{short_name} OPEN\nPAUSE IN {remaining}")
        self.door_countdown.show()

    def snooze_door_pause(self):
        if self.reject_locked_control():
            return
        pause = self.s.thermostat.setdefault("pauseFunction", {})
        duration_minutes = int(max(1, min(60, round(self.safe_float(pause.get("durationMinutes") if isinstance(pause, dict) else 5, 5.0)))))
        changes = {"pauseFunction": {"action": "resume"}}
        original_pause = copy.deepcopy(pause) if isinstance(pause, dict) else {}
        original_target = self.s.thermostat.get("targetTemp")
        original_last_comfort = self.s.thermostat.get("lastComfortTarget")
        if isinstance(pause, dict):
            # Restore the comfort target immediately instead of leaving the
            # dial on the temporary Away target until the API round trip ends.
            # Keep the original values above so a failed command can put the
            # visible pause state back exactly as it was.
            previous_target = pause.get("previousTargetTemp")
            previous_last = pause.get("previousLastComfortTarget")
            if not bool(self.s.thermostat.get("away")) and previous_target is not None:
                self.s.thermostat["targetTemp"] = previous_target
                self.s.thermostat["lastComfortTarget"] = previous_last if previous_last is not None else previous_target
            pause["snoozeUntil"] = int(time.time() * 1000) + duration_minutes * 60000
            pause["active"] = False
            pause["pausedAt"] = 0
            pause["previousTargetTemp"] = None
            pause["previousLastComfortTarget"] = None
            pause["activeEntityIds"] = []
            pause["countdownAllowed"] = False
            pause["countdownReason"] = "resumed"
        control_hold = max(5.0, float(getattr(self.s.api, "control_timeout", 4.0) or 4.0) + 1.0)
        self.s.pause_status_refresh(control_hold)
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
            self.s.resume_status_refresh()
            self.sync(self.s.config, self.s.thermostat)

        def failed(error):
            self.s.thermostat["pauseFunction"] = copy.deepcopy(original_pause)
            self.s.thermostat["targetTemp"] = original_target
            self.s.thermostat["lastComfortTarget"] = original_last_comfort
            self.s.resume_status_refresh()
            self.sync(self.s.config, self.s.thermostat)
            self.requestToast.emit(f"Resume failed: {error}")

        self.run_async(
            "door-snooze",
            lambda: self.s.api.thermostat_update(changes),
            done,
            failed,
        )


    def refresh_schedule_shortcuts(self):
        if not hasattr(self, "schedule_shortcuts_lay"):
            return
        while self.schedule_shortcuts_lay.count():
            item = self.schedule_shortcuts_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        schedules = self.s.thermostat_schedules()
        self.schedule_shortcuts.setVisible(bool(schedules))
        if not schedules:
            return
        for sched in schedules:
            full_name = str(sched.get("name") or "Schedule").strip() or "Schedule"
            display_name = full_name[:20] + ("…" if len(full_name) > 20 else "")
            b = RoundButton(display_name, active=False, min_h=34)
            b.setToolTip(full_name)
            b.setFixedHeight(34)
            b.setFixedWidth(max(82, min(166, 38 + len(display_name) * 8)))
            b.setStyleSheet("""
                QPushButton {
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                        stop:0 rgba(255,255,255,0.12),
                        stop:1 rgba(49,63,88,0.76));
                    color:#eef8ff;
                    border:1px solid rgba(130,229,255,0.42);
                    border-radius:17px;
                    padding:0 10px;
                    font-family:Arial;
                    font-weight:900;
                    font-size:11px;
                }
                QPushButton:pressed {
                    background:rgba(71,224,255,0.34);
                    border-color:rgba(71,224,255,0.82);
                    color:#ffffff;
                }
            """)
            b.clicked.connect(lambda checked=False, s=copy.deepcopy(sched): self.apply_schedule_now(s))
            b.setEnabled(not self.screen_control_locked())
            self.schedule_shortcuts_lay.addWidget(b)
        self.schedule_shortcuts_lay.addStretch(1)

    def open_schedule_manager(self):
        if self.reject_locked_control():
            return
        if getattr(self, "_schedule_dialog_open", False):
            return
        self._schedule_dialog_open = True
        try:
            dlg = ScheduleManagerDialog(self.s, self.window() or self)
            dlg.setWindowModality(Qt.ApplicationModal)
            dlg.setWindowFlags(dlg.windowFlags() | Qt.Dialog | Qt.WindowStaysOnTopHint)
            dlg.changed.connect(lambda: (self.sync(self.s.config, self.s.thermostat), self.refresh_schedule_shortcuts()))
            QTimer.singleShot(0, dlg.raise_)
            QTimer.singleShot(0, dlg.activateWindow)
            dlg.exec_()
            self.sync(self.s.config, self.s.thermostat)
            self.refresh_schedule_shortcuts()
        except Exception as exc:
            self.requestToast.emit(f"Schedule popup failed: {exc}")
        finally:
            self._schedule_dialog_open = False

    def apply_schedule_now(self, sched: dict):
        if self.reject_locked_control():
            return
        if self.intimacy_hold_active():
            self.requestToast.emit(f"{intimacy_hold_target_text(self.thermostat_view())} hold active — schedules are ignored")
            return
        mode = str(self.thermostat_view().get("mode") or "cool").lower()
        active = str(self.thermostat_view().get("autoActiveMode") or "").lower()
        effective = active if mode == "auto" and active in {"heat", "cool"} else mode
        target = sched.get("heatSetpoint") if effective == "heat" else sched.get("coolSetpoint")
        try:
            val = int(float(target))
        except Exception:
            self.requestToast.emit("Schedule target is invalid")
            return
        before_tap = copy.deepcopy(self.thermostat_view())
        self.s.set_target_override(val)
        self.apply_local_minimum_runtime_prediction(before_tap, val)
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "schedule-run",
            lambda: self.s.api.thermostat_update({"targetTemp": val, "lastComfortTarget": val, "targetChangeSource": "panel"}),
            done,
            lambda err: (self.s.clear_target_override(), self.requestToast.emit(f"Schedule failed: {err}")),
        )


    def _mode_bar(self):
        # Floating mode buttons. No shared rail/border. These use a tighter,
        # fully rounded pill style so they do not look squared-off or overlap.
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addStretch(1)
        for mode in ["off", "cool", "heat", "away", "arriving"]:
            label = "Arriving" if mode == "arriving" else mode.capitalize()
            b = RoundButton(label, active=False, min_h=38)
            b.setFixedSize(106 if mode == "arriving" else 82, 38)
            b.clicked.connect(lambda checked=False, m=mode: self.set_mode(m))
            self.mode_buttons[mode] = b
            lay.addWidget(b)
        lay.addStretch(1)
        return lay

    def _fan_bar(self):
        # Compact floating fan status pill. Tap it for available fan modes.
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        label = QLabel("FAN")
        label.setFont(font(8, QFont.Black, 15))
        label.setStyleSheet("color:#9ca6bb; background:transparent; border:0;")
        self.fan_status_button = RoundButton("Auto", active=False, min_h=38)
        self.fan_status_button.setFixedSize(86, 38)
        self.fan_status_button.clicked.connect(self.show_fan_menu)
        lay.addStretch(1)
        lay.addWidget(label)
        lay.addWidget(self.fan_status_button)
        lay.addStretch(1)
        return lay

    def cooling_is_active(self, t: dict | None = None) -> bool:
        """True when cooling is actually running, including minimum-runtime holds."""
        t = t if isinstance(t, dict) else self.thermostat_view()
        relays = t.get("relays") if isinstance(t.get("relays"), dict) else {}
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        hvac_action = str(
            t.get("hvacAction")
            or t.get("hvac_action")
            or outputs.get("hvacAction")
            or outputs.get("hvac_action")
            or ""
        ).strip().lower()
        reason = str(t.get("minimumCycleReason") or outputs.get("minimumCycleReason") or "").strip().lower()
        cycle_mode = str(t.get("minimumCycleMode") or outputs.get("minimumCycleMode") or "").strip().lower()
        until = self.safe_float(t.get("minimumCycleUntil") or outputs.get("minimumCycleUntil"), 0.0)
        minimum_runtime_cooling = reason == "minimum-runtime" and cycle_mode == "cool" and until > time.time() * 1000
        return bool(relays.get("cool") or outputs.get("cool") or hvac_action == "cooling" or minimum_runtime_cooling)

    def show_fan_menu(self):
        if self.reject_locked_control():
            return
        if not self.fan_status_button:
            return
        t = self.thermostat_view()
        cooling_active = self.cooling_is_active(t)
        current = str(t.get("fan") or "auto").lower()
        if cooling_active and current == "off":
            current = "auto"
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background:rgba(18,27,45,245);
                color:#f6f8ff;
                border:1px solid rgba(104,222,255,0.45);
                border-radius:14px;
                padding:8px;
                font-weight:900;
                font-size:15px;
            }
            QMenu::item {
                padding:11px 48px 11px 18px;
                border-radius:10px;
            }
            QMenu::item:selected {
                background:rgba(72,214,255,210);
                color:#06101f;
            }
        """)
        fan_options = ["on", "auto"] if cooling_active else ["off", "on", "auto"]
        for fan in fan_options:
            action = menu.addAction(("✓  " if fan == current else "   ") + fan.capitalize())
            action.triggered.connect(lambda checked=False, f=fan: self.set_fan(f))
        menu.exec_(self.fan_status_button.mapToGlobal(self.fan_status_button.rect().topLeft()))

    def predicted_manual_lockout_until(self, mode: str, snapshot: dict | None = None) -> int:
        mode = str(mode or "").strip().lower()
        if mode not in {"heat", "cool"}:
            return 0
        # Use the pre-tap thermostat snapshot.  The button handler updates the
        # local selected mode immediately for responsiveness; if prediction reads
        # after that mutation, it can no longer see that the opposite side was
        # active/calling and the bypass countdown is skipped intermittently.
        t = copy.deepcopy(snapshot) if isinstance(snapshot, dict) else self.thermostat_view()
        opposite = "cool" if mode == "heat" else "heat"
        delay_minutes = self.safe_float(t.get("manualChangeoverLockoutMinutes"), 10.0)
        if delay_minutes <= 0:
            return 0
        now_ms = int(time.time() * 1000)
        relay_key = "coolRelayWasOn" if opposite == "cool" else "heatRelayWasOn"
        last_key = "equipmentLastCoolRunAt" if opposite == "cool" else "equipmentLastHeatRunAt"
        legacy_key = "lastCoolRunAt" if opposite == "cool" else "lastHeatRunAt"
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        last_run = max(self.safe_float(t.get(last_key), 0.0), self.safe_float(t.get(legacy_key), 0.0))
        if bool(t.get(relay_key)) or bool(outputs.get(opposite)):
            last_run = now_ms
        if not last_run:
            current_mode = str(t.get("mode") or "").lower()
            active_mode = str(t.get("autoActiveMode") or "").lower() if current_mode == "auto" else current_mode
            current = self.safe_float(t.get("currentTemp"), 70.0)
            target = self.safe_float(t.get("targetTemp"), 70.0)
            if active_mode == opposite and ((opposite == "cool" and current > target) or (opposite == "heat" and current < target)):
                last_run = now_ms
        if not last_run:
            return 0
        until = int(last_run + delay_minutes * 60000)
        return until if until > now_ms else 0

    def request_peer_sync(self, changes: dict) -> bool:
        if self.screen_control_locked():
            return False
        top = self.window()
        if hasattr(top, "request_peer_sync"):
            try:
                return bool(top.request_peer_sync(changes))
            except Exception:
                return False
        return False

    def auto_away_entity_ids(self, snapshot: dict | None = None) -> list[str]:
        t = snapshot if isinstance(snapshot, dict) else self.thermostat_view()
        result: list[str] = []
        for person in t.get("autoAwayPeople") or []:
            if not isinstance(person, dict):
                continue
            entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
            if entity_id and entity_id not in result:
                result.append(entity_id)
        return result

    def arriving_override_payload(self, snapshot: dict | None = None) -> dict:
        now_ms = int(time.time() * 1000)
        duration_ms = 120 * 60000
        return {
            "active": True,
            "startedAt": now_ms,
            "entityIds": self.auto_away_entity_ids(snapshot),
            "reason": "arriving",
            "durationMs": duration_ms,
            "expiresAt": now_ms + duration_ms,
        }

    def manual_return_home_override_payload(self, snapshot: dict | None = None) -> dict:
        now_ms = int(time.time() * 1000)
        duration_ms = 6 * 60 * 60 * 1000
        return {
            "active": True,
            "startedAt": now_ms,
            "entityIds": self.auto_away_entity_ids(snapshot),
            "reason": "manual-return-home",
            "durationMs": duration_ms,
            "expiresAt": now_ms + duration_ms,
        }

    def arriving_override_active(self, snapshot: dict | None = None) -> bool:
        t = snapshot if isinstance(snapshot, dict) else self.thermostat_view()
        override = t.get("presenceHomeOverride") if isinstance(t.get("presenceHomeOverride"), dict) else {}
        if not override or not bool(override.get("active")):
            return False
        if str(override.get("reason") or "").strip().lower() != "arriving":
            return False
        expires_at = self.safe_float(override.get("expiresAt") or override.get("until"), 0.0)
        return expires_at <= 0 or expires_at > time.time() * 1000

    def set_mode(self, mode: str):
        if self.reject_locked_control():
            return
        mode = str(mode or "").strip().lower()
        if mode not in {"off", "heat", "cool", "away", "arriving"}:
            return
        before_tap = copy.deepcopy(self.thermostat_view())
        if self.intimacy_hold_active(before_tap) and mode in {"away", "arriving"}:
            self.requestToast.emit(f"{intimacy_hold_target_text(before_tap)} hold active — Away and Arriving are ignored")
            return
        arriving_was_active = mode == "arriving" and self.arriving_override_active(before_tap)
        if mode == "arriving":
            resume_mode = str(before_tap.get("mode") or "cool").lower()
            if resume_mode == "auto":
                resume_mode = str(before_tap.get("autoActiveMode") or before_tap.get("activeMode") or "cool").lower()
            if resume_mode not in {"heat", "cool", "off"}:
                resume_mode = "cool"

            if arriving_was_active:
                # Arriving is a true screen toggle. A second tap clears only the
                # temporary two-hour presence bypass and immediately reveals the
                # underlying Heat/Cool/Off mode. This is intentionally local: it
                # must not turn into a synced Return Home command.
                changes = {
                    "preset_mode": "home",
                    "presetChangeSource": "panel",
                    "away": False,
                    "awaySource": "",
                    "manualAwayPresenceLatch": None,
                    "presenceHomeOverride": None,
                }
                self.s.set_mode_override(
                    resume_mode,
                    away=False,
                    clear_presence_home_override=True,
                )
                self.s.thermostat["away"] = False
                self.s.thermostat["awaySource"] = ""
                self.s.thermostat["manualAwayPresenceLatch"] = None
                self.s.thermostat["presenceHomeOverride"] = None
            else:
                arriving_override = self.arriving_override_payload(before_tap)
                changes = {
                    "preset_mode": "arriving",
                    "presetChangeSource": "panel",
                    "away": False,
                    "awaySource": "",
                    "manualAwayPresenceLatch": None,
                    "presenceHomeOverride": arriving_override,
                }
                self.s.set_mode_override(resume_mode, away=False, presence_home_override=arriving_override)
                self.s.thermostat["away"] = False
                self.s.thermostat["awaySource"] = ""
                self.s.thermostat["manualAwayPresenceLatch"] = None
                self.s.thermostat["presenceHomeOverride"] = copy.deepcopy(arriving_override)
                restore_target = before_tap.get("preAwayTargetTemp")
                if restore_target is None:
                    restore_target = before_tap.get("lastComfortTarget")
                if restore_target is not None:
                    # Optimistic display only. The backend performs the authoritative
                    # restore and clears preAwayTargetTemp after leaving Away.
                    self.s.thermostat["targetTemp"] = restore_target
                    self.s.thermostat["lastComfortTarget"] = restore_target
        elif mode == "away":
            going_away = not bool(before_tap.get("away"))
            changes = {"away": going_away, "awaySource": "manual" if going_away else ""}
            if going_away:
                changes["presenceHomeOverride"] = None
            resume_mode = str(before_tap.get("mode") or "cool").lower()
            if resume_mode == "auto":
                resume_mode = str(before_tap.get("autoActiveMode") or before_tap.get("activeMode") or "cool").lower()
            if resume_mode not in {"heat", "cool", "off"}:
                resume_mode = "cool"
            self.s.set_mode_override("away" if going_away else resume_mode, away=going_away)
            self.s.thermostat["away"] = going_away
            self.s.thermostat["awaySource"] = changes["awaySource"]
            if going_away:
                # Keep the immediate UI snapshot aligned with the backend in case
                # Arriving is tapped before the Away request finishes its round trip.
                self.s.thermostat["preAwayTargetTemp"] = before_tap.get("targetTemp", before_tap.get("lastComfortTarget"))
                self.s.thermostat["presenceHomeOverride"] = None
        else:
            # Physical/manual button taps should visibly win immediately. They
            # also end a timed Arriving state; HVAC mode and the highlighted
            # presence preset must never disagree after the same wall-panel tap.
            changes = {
                "mode": mode,
                "away": False,
                "awaySource": "",
                "manualAwayPresenceLatch": None,
                "presenceHomeOverride": None,
                "modeChangeSource": "panel",
            }
            self.s.set_mode_override(mode, away=False, clear_presence_home_override=True)
            self.s.thermostat["mode"] = mode
            self.s.thermostat["away"] = False
            self.s.thermostat["awaySource"] = ""
            self.s.thermostat["manualAwayPresenceLatch"] = None
            self.s.thermostat["presenceHomeOverride"] = None
            if mode in {"heat", "cool"}:
                # Show the bypassable delay immediately instead of waiting for
                # the API round trip. The backend repeats the same calculation
                # from the authoritative runtime state and corrects this local
                # prediction on the next response.
                until = self.predicted_manual_lockout_until(mode, before_tap)
                self.s.thermostat["autoSwitchNotice"] = {"active": False, "source": "", "fromMode": "", "toMode": "", "switchTemp": 0, "outdoorTemp": 0, "coolTarget": 0, "heatTarget": 0, "createdAt": 0}
                if until > int(time.time() * 1000):
                    self.s.thermostat["manualPendingMode"] = mode
                    self.s.thermostat["manualLockoutUntil"] = until
                    self.s.thermostat["autoPendingMode"] = ""
                    self.s.thermostat["autoLockoutUntil"] = 0
                else:
                    self.s.thermostat["manualPendingMode"] = ""
                    self.s.thermostat["manualLockoutUntil"] = 0
        self.sync(self.s.config, self.s.thermostat)
        if mode == "arriving" and not arriving_was_active:
            # Each peer leaves Away and restores its own pre-Away temperature.
            # Do not send this panel's setpoint with the Arriving command. A
            # second Arriving tap is local-only because Return Home must not sync.
            self.request_peer_sync({"presetMode": "arriving"})
        elif mode in {"off", "heat", "cool"}:
            # HVAC selection is shared, but Away/Home status remains independent
            # on every thermostat.
            self.request_peer_sync({"mode": mode})

        def done(result):
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "thermostat-mode",
            lambda: self.s.api.thermostat_update(changes),
            done,
            lambda err: self.requestToast.emit(f"Thermostat update failed: {err}"),
        )

    def return_home_from_away(self):
        if self.reject_locked_control():
            return
        now = time.monotonic()
        if now - getattr(self, "_return_home_requested_at", 0.0) < 0.75:
            return
        self._return_home_requested_at = now
        # A tap on Away creates a short local mode hold so stale status reads do
        # not make the button feel broken. When Return Home is tapped, that hold
        # must be cleared immediately or the local UI can repaint Away several
        # more times before the backend response arrives.
        self.s.clear_mode_override()
        self.s.pause_status_refresh(2.5)
        changes = {"away": False, "awaySource": "", "manualAwayPresenceLatch": None}
        people = self.thermostat.get("autoAwayPeople") or self.s.thermostat.get("autoAwayPeople") or []
        entity_ids = []
        for person in people:
            if not isinstance(person, dict):
                continue
            entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
            if entity_id and entity_id not in entity_ids:
                entity_ids.append(entity_id)
        if entity_ids:
            # Return Home should mean "stay Home now" even if the Auto Away
            # person trackers still say everyone is away. The hold ends as soon
            # as an assigned Auto Away user reports Home, or after six hours so
            # normal Auto Away logic can re-check the house and take over again.
            changes["presenceHomeOverride"] = self.manual_return_home_override_payload(self.thermostat)
            self.s.thermostat["presenceHomeOverride"] = copy.deepcopy(changes["presenceHomeOverride"])
        else:
            # Do not send an explicit null override. The backend has the saved
            # Auto Away people list and will create the Home hold when stale
            # HA/person states would otherwise put the panel straight back into Away.
            self.s.thermostat["presenceHomeOverride"] = None
        self.s.thermostat["away"] = False
        self.s.thermostat["awaySource"] = ""
        self.s.thermostat["manualAwayPresenceLatch"] = None
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "thermostat-home",
            lambda: self.s.api.thermostat_update(changes),
            done,
            lambda err: self.requestToast.emit(f"Home failed: {err}"),
        )

    def set_fan(self, fan: str):
        if self.reject_locked_control():
            return
        fan = str(fan or "auto").strip().lower()
        if fan not in {"off", "on", "auto"}:
            fan = "auto"
        if fan == "off" and self.cooling_is_active():
            fan = "auto"
            self.requestToast.emit("Cooling requires fan Auto or On")
        self.s.thermostat["fan"] = fan
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "thermostat-fan",
            lambda: self.s.api.thermostat_update({"fan": fan}),
            done,
            lambda err: self.requestToast.emit(f"Fan update failed: {err}"),
        )

    def thermostat_title_text(self) -> str:
        name = str((self.s.thermostat or {}).get("name") or "").strip()
        return name or "Climate Control"

    def change_target(self, delta: int):
        if self.screen_security_locked():
            self.requestToast.emit("Security lock active: alarm control only")
            return
        t = self.thermostat_view()
        # One physical tap should move the main setpoint by exactly 1°F.
        # IconCircle now emits clicked() once, but keep this explicit so future
        # repeat/gesture changes cannot accidentally double the step size.
        step = 1 if delta >= 0 else -1
        self.set_target(float(t.get("targetTemp", t.get("target_temp", 70))) + step)

    def set_target_from_dial(self, value: float):
        if self.reject_locked_control():
            return
        self.set_target(value)

    def set_target(self, value: float):
        if self.screen_security_locked():
            self.requestToast.emit("Security lock active: alarm control only")
            return
        try:
            t = self.thermostat_view()
            if self.intimacy_hold_active(t):
                self.requestToast.emit(f"{intimacy_hold_target_text(t)} hold active — turn it off to change temperature")
                return
            before_tap = copy.deepcopy(t)
            limits = t.get("limits") or {}
            mode = str(t.get("mode") or "cool").lower()
            if mode == "auto":
                mode = str(t.get("autoActiveMode") or t.get("activeMode") or "cool").lower()
            if mode == "off" and not bool(t.get("away")):
                self.requestToast.emit("Thermostat is Off")
                return
            active = str(t.get("autoActiveMode") or t.get("activeMode") or "").lower()
            range_key = active if mode == "auto" and active in {"cool", "heat"} else mode
            lim = limits.get(range_key) or limits.get(mode) or limits.get("auto") or {"min": 55, "max": 90}
            val = int(clamp(round(float(value)), float(lim.get("min", 55)), float(lim.get("max", 90))))
        except Exception as exc:
            self.requestToast.emit(f"Set temp failed: {exc}")
            return
        self.s.set_target_override(val)
        # Invalidate any status GET that started before this tap and keep normal
        # polling paused until the final queued write is acknowledged. This keeps
        # an acknowledgement for an earlier tap from clearing the newer local
        # target override while the final value is still waiting to be sent.
        self.s.pause_status_refresh(30.0)
        self.apply_local_minimum_runtime_prediction(before_tap, val)
        self.sync(self.s.config, self.s.thermostat)
        if not bool(t.get("away")):
            # The main-window Sync path already coalesces target changes on a
            # short timer, so repeated +/- taps also send only the latest peer
            # target instead of replaying every intermediate degree.
            self.request_peer_sync({"targetTemp": val})
        self.queue_target_update(val, self.screen_control_locked())

    def queue_target_update(self, value: int, suppress_peer_sync: bool = False):
        """Serialize native setpoint writes while preserving instant touch UI."""
        self._target_command_pending_value = int(value)
        self._target_command_pending_suppress_peer_sync = bool(suppress_peer_sync)
        self._target_command_sequence += 1
        if not self._target_command_in_flight:
            self._send_next_target_update()

    def _send_next_target_update(self):
        if self._target_command_in_flight:
            return
        pending = self._target_command_pending_value
        if pending is None:
            self.s.resume_status_refresh()
            return

        value = int(pending)
        suppress_peer_sync = bool(self._target_command_pending_suppress_peer_sync)
        sequence = int(self._target_command_sequence)
        self._target_command_pending_value = None
        self._target_command_in_flight = True

        def done(result):
            self._target_command_in_flight = False
            newer_pending = self._target_command_pending_value is not None
            if newer_pending:
                # This response confirms an intermediate tap, not the value now
                # shown on screen. Do not ingest it: ingesting a higher revision
                # with the older target would clear the latest optimistic hold.
                # The next queued request is sent immediately and becomes the
                # only authoritative acknowledgement the UI consumes.
                self._send_next_target_update()
                return
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                self.sync(self.s.config, self.s.thermostat)
            self.s.resume_status_refresh()

        def failed(err):
            self._target_command_in_flight = False
            if self._target_command_pending_value is not None:
                # A superseded intermediate write failed, but a newer user target
                # is already queued. Keep the newer target visible and continue.
                self._send_next_target_update()
                return
            self.s.clear_target_override()
            self.s.resume_status_refresh()
            self.requestToast.emit(f"Set temp failed: {err}")

        self.run_async(
            f"thermostat-target-{sequence}",
            lambda: self.s.api.thermostat_update({
                "targetTemp": value,
                "lastComfortTarget": value,
                "targetChangeSource": "panel",
                "suppressPeerSync": suppress_peer_sync,
                "clientCommandId": f"native-panel:{sequence}",
            }),
            done,
            failed,
        )

    def set_virtual_temp(self, value: float):
        if self.reject_locked_control():
            return
        self.virtual_temp_pending = float(value)
        self.s.thermostat["currentTemp"] = float(value)
        self.s.thermostat["currentTempSource"] = "virtual"
        self.s.thermostat["currentTempSourceName"] = "Virtual Temp Test"
        self.s.thermostat["virtualTempOverrideUntil"] = int(time.time() * 1000) + 120000
        self.sync(self.s.config, self.s.thermostat)
        self.virtual_temp_push_timer.start(220)

    def push_virtual_temp(self):
        if self.virtual_temp_pending is None:
            return
        value = float(self.virtual_temp_pending)
        changes = {
            "currentTemp": value,
            "currentTempSource": "virtual",
            "currentTempSourceName": "Virtual Temp Test",
            "currentTempUpdatedAt": time.time(),
            "virtualTempOverrideUntil": int(time.time() * 1000) + 120000,
        }

        def done(result):
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "virtual-temp",
            lambda: self.s.api.thermostat_update(changes),
            done,
            lambda err: self.requestToast.emit(f"Virtual temp failed: {err}"),
        )

    def cached_alarm_entity(self) -> dict:
        ha = self.s.ha()
        entity = ha.get("alarmEntity") or {}
        return entity if isinstance(entity, dict) else {}

    @staticmethod
    def alarm_state_is_armed(state: str) -> bool:
        state = str(state or "").strip().lower()
        return state.startswith("armed") or state in {"arming", "pending", "triggered"}

    @staticmethod
    def alarm_state_is_controllable(state: str) -> bool:
        state = str(state or "").strip().lower()
        return state == "disarmed" or ThermostatScreen.alarm_state_is_armed(state)

    def restore_alarm_card_from_cache(self):
        entity = self.cached_alarm_entity()
        state = str(entity.get("state") or "unknown").lower()
        self.alarm_card.setValue(state.upper())
        self.alarm_card.setAlarmState(state)

    def mark_alarm_state_checking(self):
        self.alarm_card.setValue("CHECKING")
        self.alarm_card.setAlarmState("checking")

    def show_alarm_dialog(self):
        """Confirm live HA state before exposing any alarm control.

        The former flow rendered from cached panel config and refreshed after the
        modal appeared. If the alarm had already changed while the screen slept,
        the keypad could appear first and then switch to Disarmed, making a state
        refresh look exactly like an unauthorized disarm. Controls now fail closed
        until Home Assistant has answered this specific tap.
        """
        now = time.monotonic()
        if getattr(self, "_alarm_dialog_open", False):
            return
        if getattr(self, "_alarm_open_refresh_running", False):
            return
        if now < getattr(self, "_alarm_reopen_block_until", 0.0):
            return

        entity = self.cached_alarm_entity()
        eid = str(entity.get("entityId") or entity.get("entity_id") or "").strip()
        if not eid:
            self.requestToast.emit("No alarm entity assigned")
            return

        cached_state = str(entity.get("state") or "unknown").lower()
        self._alarm_open_refresh_running = True
        self._alarm_open_generation += 1
        generation = self._alarm_open_generation
        self.mark_alarm_state_checking()
        trace_runtime(f"alarm control requested entity={eid} cached_state={cached_state}")

        def refresh_done(fresh):
            if generation != getattr(self, "_alarm_open_generation", 0):
                return
            self._alarm_open_refresh_running = False
            if not isinstance(fresh, dict) or not fresh:
                self.restore_alarm_card_from_cache()
                self.requestToast.emit("Unable to verify alarm status")
                trace_runtime("alarm control blocked because live state was empty")
                return
            confirmed = self.s.apply_alarm_state(fresh) or fresh
            self.apply_alarm_state_refresh(confirmed)
            live_state = str(confirmed.get("state") or "unknown").lower()
            trace_runtime(f"alarm state confirmed before controls cached={cached_state} live={live_state}")
            if not self.alarm_state_is_controllable(live_state):
                self.requestToast.emit(f"Alarm status is {live_state.replace('_', ' ')}")
                trace_runtime(f"alarm control blocked for non-controllable state={live_state}")
                return
            if self.alarm_state_is_armed(cached_state) and live_state == "disarmed":
                self.requestToast.emit("Alarm was already disarmed; display refreshed")
            self._open_verified_alarm_dialog(confirmed)

        def refresh_failed(err):
            if generation != getattr(self, "_alarm_open_generation", 0):
                return
            self._alarm_open_refresh_running = False
            self.restore_alarm_card_from_cache()
            self.requestToast.emit("Unable to verify alarm status")
            trace_runtime(f"alarm control blocked because live refresh failed: {err}")

        self.run_async("alarm-state-before-open", self.s.refresh_alarm_state, refresh_done, refresh_failed)

    def _open_verified_alarm_dialog(self, verified_entity: dict):
        now = time.monotonic()
        if getattr(self, "_alarm_dialog_open", False):
            return
        if now < getattr(self, "_alarm_reopen_block_until", 0.0):
            return
        entity = self.cached_alarm_entity()
        if isinstance(verified_entity, dict):
            entity.update(verified_entity)
        live_state = str(entity.get("state") or "unknown").lower()
        if not self.alarm_state_is_controllable(live_state):
            self.requestToast.emit("Alarm status could not be verified")
            return

        self._alarm_dialog_open = True
        dlg = AlarmControlDialog(self.s, entity, self)
        trace_runtime(f"verified alarm dialog opened entity={entity.get('entityId') or ''} state={dlg.current_state()}")

        def applied(alarm, action):
            if alarm:
                entity.update(alarm)
            self.sync(self.s.config, self.s.thermostat)

        dlg.actionDone.connect(applied)
        self._alarm_dialog = dlg
        try:
            dlg.exec_()
        finally:
            try:
                dlg.prepare_for_close("verified alarm dialog finally")
            except Exception:
                pass
            if getattr(self, "_alarm_dialog", None) is dlg:
                self._alarm_dialog = None
            self._alarm_dialog_open = False
            self._alarm_reopen_block_until = time.monotonic() + 1.2
            parent = self.window()
            if parent is not None and hasattr(parent, "_modal_touch_block_until"):
                parent._modal_touch_block_until = time.monotonic() + 0.55
            trace_runtime("verified alarm dialog closed")

    def apply_alarm_state_refresh(self, fresh: dict | None):
        """Apply a Home Assistant-initiated alarm state change to visible controls."""
        if not isinstance(fresh, dict) or not fresh:
            return
        ha = self.s.ha()
        entity = ha.get("alarmEntity") or {}
        if not isinstance(entity, dict):
            entity = {}
        entity.update(fresh)
        ha["alarmEntity"] = entity
        state = str(entity.get("state") or "disarmed")
        self.alarm_card.setValue(state.upper())
        self.alarm_card.setAlarmState(state)
        dlg = getattr(self, "_alarm_dialog", None)
        if dlg is not None:
            try:
                if dlg.can_update_ui():
                    dlg.apply_fresh_alarm_state(entity)
            except Exception as exc:
                trace_runtime(f"visible alarm refresh skipped: {exc}")

    def _floating_button_style(self, active: bool = False) -> str:
        if active:
            bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #45e3ff, stop:1 #4ba7ff)"
            color = "#031322"
            border = "rgba(255,255,255,0.24)"
        else:
            bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(73,83,101,0.70), stop:1 rgba(29,36,52,0.72))"
            color = "#dbe3f4"
            border = "rgba(157,176,207,0.22)"
        return f"""
            QPushButton {{
                background:{bg};
                color:{color};
                border:1px solid {border};
                border-radius:19px;
                padding:0;
                font-family:Arial;
                font-weight:900;
            }}
            QPushButton:pressed {{
                background:rgba(72,214,255,0.48);
            }}
        """

    def sync(self, config: dict, thermostat: dict):
        super().sync(config, thermostat)
        t = self.thermostat_view()
        mode = str(t.get("mode") or "cool").lower()
        away = bool(t.get("away"))
        if mode == "auto":
            active = str(t.get("autoActiveMode") or t.get("activeMode") or "cool").lower()
            # Auto mode is no longer exposed on the wall panel.  If an older
            # saved state or Home Assistant still reports auto, render the
            # active heat/cool side so the UI never shows an orphaned Auto mode.
            mode = active if active in {"heat", "cool"} else "cool"
        else:
            active = mode
        if hasattr(self, "title_label"):
            next_title = self.thermostat_title_text()
            if self.title_label.text() != next_title:
                self.title_label.setText(next_title)
                self.title_label.repaint()
        self.dial.setData(t.get("currentTemp"), t.get("targetTemp"), mode, active, t.get("limits"))
        setpoint_visible = mode != "off" or away
        self.minus.setVisible(setpoint_visible)
        self.plus.setVisible(setpoint_visible)
        self.minus.setEnabled(setpoint_visible)
        self.plus.setEnabled(setpoint_visible)
        arriving = self.arriving_override_active(t) and not away
        for m, b in self.mode_buttons.items():
            selected = (m == mode and not away and not arriving) or (m == "away" and away) or (m == "arriving" and arriving)
            b.setStyleSheet(self._floating_button_style(selected))
        fan = str(t.get("fan") or "auto").lower()
        if self.cooling_is_active(t) and fan == "off":
            fan = "auto"
        if self.fan_status_button:
            self.fan_status_button.setText(fan.capitalize())
            self.fan_status_button.setStyleSheet(self._floating_button_style(False))
        out = t.get("outdoorTemp") or t.get("outdoor_temperature") or "--"
        wind = t.get("outdoorWindSpeed") or t.get("outdoor_wind_speed") or 0
        unit = t.get("outdoorWindUnit") or t.get("outdoor_wind_unit") or "mph"
        self.outdoor.setText(f"OUTDOOR  {fmt_temp(out)}   WIND  {wind} {unit}".upper())
        self.update_status_badge()
        sensor_status = t.get("onboardTempSensorStatus") if isinstance(t.get("onboardTempSensorStatus"), dict) else {}
        sensor_warning = bool(sensor_status.get("healthFlag") or sensor_status.get("degraded"))
        if sensor_warning:
            active_sensor = sensor_status.get("activeSensor")
            status_text = "TEMP SENSOR WARNING"
            if active_sensor == 1:
                status_text += "  ·  USING SENSOR 1 FALLBACK"
            elif str(sensor_status.get("healthStatus") or "").lower() == "hold":
                status_text += "  ·  HOLDING LAST READING"
            self.sensor_warning_badge.setText(status_text)
            self.sensor_warning_badge.setToolTip(str(sensor_status.get("healthMessage") or ""))
            self.sensor_warning_badge.show()
        else:
            self.sensor_warning_badge.hide()
        self.position_main_controls()
        if hasattr(self, "person_presence_strip"):
            self.person_presence_strip.update_people(t.get("people") or [])
        self.notice.hide()
        self.refresh_schedule_shortcuts()
        if away and not self.screen_control_locked():
            source = str(t.get("awaySource") or "").lower()
            if source == "presence":
                self.away_body.setText("No assigned Auto Away users are home. Tap Return Home to hold Home for up to 6 hours; normal presence control resumes sooner if someone returns.")
            else:
                self.away_body.setText("Tap to return Home and resume normal comfort.")
            self.position_away_overlay()
            self.away_overlay.show()
            self.away_overlay.raise_()
        else:
            self.away_overlay.hide()
        self.update_door_pause_ui()
        self.update_alert_banner()
        self.retune_fx_timer()
        self.update()
        ha = nested_get(config, "integrations", "homeAssistant", default={}) or {}
        alarm = ha.get("alarmEntity") or {}
        self.alarm_card.setValue(str(alarm.get("state") or "disarmed").upper())
        self.alarm_card.setAlarmState(str(alarm.get("state") or "disarmed"))
        self.virtual_panel.updateData(t)
        self.apply_screen_lock_state()

    def poll(self):
        # Keep thermostat page light. Expensive HA polling is done only for cards with configured entities.
        pass


class InfoTile(HoldCard):
    def __init__(
        self,
        title: str,
        value: str,
        symbol: str,
        good: bool = False,
        parent=None,
        *,
        hold_ms: int = 650,
        press_feedback: bool = False,
    ):
        super().__init__(parent, hold_ms=hold_ms)
        self.title = title
        self.value = value
        self.symbol = symbol
        self.good = good
        self.press_feedback = bool(press_feedback)
        self._press_visual_down = False
        self._press_feedback_active = False
        self._press_feedback_timer = QTimer(self)
        self._press_feedback_timer.setSingleShot(True)
        self._press_feedback_timer.setInterval(180)
        self._press_feedback_timer.timeout.connect(self._clear_press_feedback)
        self.alarm_state = ""
        self.flash_on = False
        self.flash_timer = QTimer(self)
        self.flash_timer.timeout.connect(self._flash_tick)
        self.setMinimumSize(226, 164)
        self.setMaximumWidth(270)

    def _clear_press_feedback(self):
        self._press_feedback_active = False
        self.update()

    def _fire_hold(self):
        if self.press_feedback:
            # A modal picker can open before the physical touch-release event is
            # delivered back to this card. Clear the held-down visual first so
            # the Doors tile cannot remain painted as pressed behind the dialog.
            self._press_visual_down = False
            self._press_feedback_active = True
            self._press_feedback_timer.start()
            self.update()
        super()._fire_hold()

    def mousePressEvent(self, event):
        if self.press_feedback and event.button() == Qt.LeftButton:
            self._press_feedback_timer.stop()
            self._press_feedback_active = False
            self._press_visual_down = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        was_held = bool(getattr(self, "_held_fired", False))
        inside = event.button() == Qt.LeftButton and self.rect().contains(event.pos())
        super().mouseReleaseEvent(event)
        if self.press_feedback:
            self._press_visual_down = False
            if inside and not was_held:
                self._press_feedback_active = True
                self._press_feedback_timer.start()
            self.update()

    def setValue(self, value: str):
        value = str(value)
        if self.value == value:
            return
        self.value = value
        self.update()

    def setGood(self, good: bool):
        good = bool(good)
        if self.good == good:
            return
        self.good = good
        self.update()

    def setAlarmState(self, state: str):
        state = str(state or "").lower()
        changed = self.alarm_state != state
        self.alarm_state = state
        armed = self.alarm_state.startswith("armed") or self.alarm_state in {"arming", "pending"}
        if armed and not self.flash_timer.isActive():
            self.flash_timer.start(520)
        elif not armed and self.flash_timer.isActive():
            self.flash_timer.stop()
            self.flash_on = False
            changed = True
        if changed:
            self.update()

    def _flash_tick(self):
        self.flash_on = not self.flash_on
        self.update()

    def draw_icon(self, p: QPainter, cx: float, cy: float, size: float):
        title = self.title.lower()
        icon_rect = QRectF(cx - size / 2, cy - size / 2, size, size)
        open_state = str(self.value or "").strip().lower() in {"open", "opened", "opening", "on", "triggered"}
        alarmish = "alarm" in title
        armed_alarm = alarmish and (
            self.alarm_state.startswith("armed") or self.alarm_state in {"arming", "pending", "triggered"}
        )

        if armed_alarm:
            glow_color = QColor(255, 74, 111, 88)
            ring_color = QColor(255, 102, 130, 135)
            icon_color = QColor(255, 226, 235, 235)
            fill_color = QColor(255, 74, 111, 82)
        elif "door" in title and open_state:
            glow_color = QColor(255, 184, 86, 76)
            ring_color = QColor(255, 202, 120, 135)
            icon_color = QColor(255, 242, 214, 235)
            fill_color = QColor(255, 188, 94, 58)
        else:
            glow_color = QColor(84, 255, 196, 70)
            ring_color = QColor(113, 255, 219, 120)
            icon_color = QColor(210, 255, 244, 232)
            fill_color = QColor(84, 255, 196, 46)

        ring = QRadialGradient(QPointF(cx, cy), size * 0.86)
        ring.setColorAt(0.0, glow_color)
        ring.setColorAt(0.64, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), max(12, glow_color.alpha() // 4)))
        ring.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(ring)
        p.setPen(Qt.NoPen)
        p.drawEllipse(icon_rect.adjusted(-13, -13, 13, 13))

        bg = QLinearGradient(icon_rect.topLeft(), icon_rect.bottomRight())
        bg.setColorAt(0.0, QColor(fill_color.red(), fill_color.green(), fill_color.blue(), 115))
        bg.setColorAt(1.0, QColor(8, 24, 36, 142))
        p.setBrush(QBrush(bg))
        p.setPen(QPen(ring_color, 1.7))
        p.drawEllipse(icon_rect)

        p.setPen(QPen(icon_color, 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)

        if "door" in title:
            frame = QRectF(cx - size * 0.23, cy - size * 0.32, size * 0.46, size * 0.64)
            p.setPen(QPen(QColor(icon_color.red(), icon_color.green(), icon_color.blue(), 150), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawRoundedRect(frame, 3, 3)
            if open_state:
                door = QPainterPath()
                door.moveTo(frame.left() + size * 0.08, frame.top() + size * 0.06)
                door.lineTo(frame.right() + size * 0.18, frame.top() + size * 0.13)
                door.lineTo(frame.right() + size * 0.18, frame.bottom() - size * 0.10)
                door.lineTo(frame.left() + size * 0.08, frame.bottom() - size * 0.04)
                door.closeSubpath()
                dg = QLinearGradient(door.boundingRect().topLeft(), door.boundingRect().bottomRight())
                dg.setColorAt(0.0, QColor(icon_color.red(), icon_color.green(), icon_color.blue(), 70))
                dg.setColorAt(1.0, QColor(8, 28, 38, 122))
                p.setBrush(QBrush(dg))
                p.setPen(QPen(icon_color, 2.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawPath(door)
                p.setBrush(icon_color)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(frame.right() + size * 0.08, cy - 1.8, 3.8, 3.8))
            else:
                door = QRectF(frame.left() + size * 0.07, frame.top() + size * 0.07, frame.width() - size * 0.14, frame.height() - size * 0.14)
                dg = QLinearGradient(door.topLeft(), door.bottomRight())
                dg.setColorAt(0.0, QColor(icon_color.red(), icon_color.green(), icon_color.blue(), 58))
                dg.setColorAt(1.0, QColor(8, 28, 38, 112))
                p.setBrush(QBrush(dg))
                p.setPen(QPen(icon_color, 2.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawRoundedRect(door, 2.5, 2.5)
                p.setBrush(icon_color)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(door.right() - size * 0.13, cy - 2.0, 4.2, 4.2))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(icon_color.red(), icon_color.green(), icon_color.blue(), 115), 2.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(frame.left() - size * 0.07, frame.bottom() + 1), QPointF(frame.right() + size * 0.09, frame.bottom() + 1))
        elif alarmish:
            shield = QPainterPath()
            shield.moveTo(cx, cy - size * 0.34)
            shield.lineTo(cx + size * 0.29, cy - size * 0.21)
            shield.lineTo(cx + size * 0.25, cy + size * 0.11)
            shield.quadTo(cx + size * 0.17, cy + size * 0.31, cx, cy + size * 0.40)
            shield.quadTo(cx - size * 0.17, cy + size * 0.31, cx - size * 0.25, cy + size * 0.11)
            shield.lineTo(cx - size * 0.29, cy - size * 0.21)
            shield.closeSubpath()
            sg = QLinearGradient(shield.boundingRect().topLeft(), shield.boundingRect().bottomRight())
            sg.setColorAt(0.0, QColor(icon_color.red(), icon_color.green(), icon_color.blue(), 72))
            sg.setColorAt(1.0, QColor(7, 29, 38, 142))
            p.setBrush(QBrush(sg))
            p.setPen(QPen(icon_color, 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(shield)
            p.setBrush(Qt.NoBrush)
            if armed_alarm:
                p.setPen(QPen(icon_color, 3.3, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(cx, cy - size * 0.15), QPointF(cx, cy + size * 0.10))
                p.setBrush(icon_color)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(cx - 2.6, cy + size * 0.18, 5.2, 5.2))
            else:
                p.setPen(QPen(icon_color, 3.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawLine(QPointF(cx - size * 0.13, cy + size * 0.03), QPointF(cx - size * 0.03, cy + size * 0.14))
                p.drawLine(QPointF(cx - size * 0.03, cy + size * 0.14), QPointF(cx + size * 0.17, cy - size * 0.12))
        else:
            p.setFont(font(34, QFont.Black))
            p.drawText(icon_rect, Qt.AlignCenter, self.symbol)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        pressed = self.press_feedback and (self._press_visual_down or self._press_feedback_active)
        if pressed:
            # Match the touchscreen keypad feedback: the card physically sinks
            # while touched and briefly remains illuminated after release.
            r = r.adjusted(4, 5, -4, -3)

        armed_alarm = "alarm" in self.title.lower() and (
            self.alarm_state.startswith("armed") or self.alarm_state in {"arming", "pending", "triggered"}
        )
        open_door = "door" in self.title.lower() and str(self.value or "").lower() in {"open", "opened", "opening", "on"}
        pulse = 34 if (armed_alarm and self.flash_on) else 0
        glow = QRadialGradient(QPointF(r.center().x(), r.top() + 70), max(r.width(), r.height()) * 0.8)
        if armed_alarm:
            glow.setColorAt(0.0, QColor(255, 45, 92, 98 + pulse))
            glow.setColorAt(0.62, QColor(115, 17, 39, 64 + pulse))
        elif open_door:
            glow.setColorAt(0.0, QColor(255, 178, 73, 78))
            glow.setColorAt(0.62, QColor(122, 70, 22, 44))
        else:
            glow.setColorAt(0.0, QColor(42, 255, 187, 68 if self.good else 32))
            glow.setColorAt(0.62, QColor(27, 102, 86, 40))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, glow)

        g = QLinearGradient(r.topLeft(), r.bottomRight())
        if armed_alarm:
            g.setColorAt(0.0, QColor(122, 23, 49, 232))
            g.setColorAt(0.48, QColor(66, 16, 37, 224))
            g.setColorAt(1.0, QColor(23, 12, 29, 236))
            border_color = QColor(255, 67, 111, 178 + min(pulse, 40))
        elif open_door:
            g.setColorAt(0.0, QColor(126, 73, 23, 222))
            g.setColorAt(0.48, QColor(58, 41, 27, 218))
            g.setColorAt(1.0, QColor(18, 22, 35, 232))
            border_color = QColor(255, 190, 91, 148)
        else:
            g.setColorAt(0.0, QColor(24, 102, 89, 216 if self.good else 166))
            g.setColorAt(0.48, QColor(16, 53, 60, 214))
            g.setColorAt(1.0, QColor(12, 23, 39, 232))
            border_color = QColor(80, 245, 202, 136 if self.good else 78)
        if pressed:
            # Darken the face and strengthen the border so the response remains
            # obvious on the wall panel even without haptics.
            darker = QLinearGradient(r.topLeft(), r.bottomRight())
            if armed_alarm:
                darker.setColorAt(0.0, QColor(80, 13, 32, 238))
                darker.setColorAt(1.0, QColor(17, 8, 23, 244))
            elif open_door:
                darker.setColorAt(0.0, QColor(84, 45, 13, 236))
                darker.setColorAt(1.0, QColor(12, 15, 26, 242))
            else:
                darker.setColorAt(0.0, QColor(13, 67, 61, 232))
                darker.setColorAt(1.0, QColor(8, 17, 29, 242))
            g = darker
            border_color = QColor(
                min(255, border_color.red() + 36),
                min(255, border_color.green() + 36),
                min(255, border_color.blue() + 36),
                min(235, border_color.alpha() + 66),
            )
        p.setBrush(QBrush(g))
        p.setPen(QPen(border_color, 1.6))
        p.drawRoundedRect(r, 28, 28)

        self.draw_icon(p, r.center().x(), r.top() + 50, 68)

        p.setFont(font(15, QFont.Black))
        p.setPen(QColor(246, 251, 255))
        p.drawText(QRectF(r.left() + 16, r.top() + 99, r.width() - 32, 24), Qt.AlignCenter, self.title)

        badge_text = str(self.value or "").upper()
        fm = p.fontMetrics()
        badge_w = max(86, min(r.width() - 34, fm.horizontalAdvance(badge_text) + 28))
        badge = QRectF(r.center().x() - badge_w / 2, r.top() + 127, badge_w, 24)
        if armed_alarm:
            badge_color = QColor(202, 42, 76, 226)
        elif open_door:
            badge_color = QColor(210, 126, 40, 220)
        else:
            badge_color = QColor(38, 160, 126, 214 if self.good else 145)
        p.setBrush(badge_color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(badge, 12, 12)
        p.setFont(font(9, QFont.Black, 18))
        p.setPen(QColor(230, 255, 247))
        p.drawText(badge, Qt.AlignCenter, badge_text)

class ValueTile(GlassPanel):
    def __init__(self, label: str, value: str, parent=None):
        super().__init__(parent, radius=23)
        self.label = label
        self.value = value
        self.setMinimumSize(170, 54)
        self.setMaximumWidth(230)

    def setValue(self, value: str):
        value = str(value)
        if self.value == value:
            return
        self.value = value
        self.update()

    def setAlarmState(self, state: str):
        self.alarm_state = str(state or "").lower()
        armed = self.alarm_state.startswith("armed") or self.alarm_state in {"arming", "pending"}
        if armed and not self.flash_timer.isActive():
            self.flash_timer.start(520)
        elif not armed and self.flash_timer.isActive():
            self.flash_timer.stop()
            self.flash_on = False
        self.update()

    def _flash_tick(self):
        self.flash_on = not self.flash_on
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect())
        p.setFont(font(8, QFont.Black, 18))
        p.setPen(T.TEXT_MUTED)
        p.drawText(QRectF(18, 0, r.width()/2, r.height()), Qt.AlignVCenter | Qt.AlignLeft, self.label)
        p.setFont(font(20, QFont.Black))
        p.setPen(T.TEXT)
        p.drawText(QRectF(r.width()/2-16, 0, r.width()/2, r.height()), Qt.AlignVCenter | Qt.AlignRight, self.value)


class VirtualOutputsPanel(GlassPanel):
    tempChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent, radius=20)
        self.data = {}
        self.virtual_min = 50.0
        self.virtual_max = 90.0
        self.dragging = False
        self.setFixedSize(252, 174)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)

    def updateData(self, data: dict):
        self.data = data or {}
        self.update()

    def current_virtual_temp(self) -> float:
        value = self.data.get("currentTemp")
        try:
            return clamp(float(value), self.virtual_min, self.virtual_max)
        except Exception:
            return 70.0

    def slider_rect(self) -> QRectF:
        return QRectF(18, 116, 208, 18)

    def temp_from_pos(self, pos: QPoint) -> float:
        rail = self.slider_rect()
        pct = clamp((pos.x() - rail.left()) / max(1.0, rail.width()), 0.0, 1.0)
        value = self.virtual_min + pct * (self.virtual_max - self.virtual_min)
        return round(value)

    def set_temp_from_pos(self, pos: QPoint):
        value = self.temp_from_pos(pos)
        self.data["currentTemp"] = value
        self.update()
        self.tempChanged.emit(float(value))

    def mousePressEvent(self, event):
        if self.slider_rect().adjusted(-12, -20, 12, 20).contains(QPointF(event.pos())):
            self.dragging = True
            self.set_temp_from_pos(event.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.set_temp_from_pos(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragging:
            self.set_temp_from_pos(event.pos())
            self.dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def event(self, event):
        if event.type() in (QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd):
            pts = event.touchPoints()
            if pts:
                self.set_temp_from_pos(pts[0].pos().toPoint())
                self.dragging = event.type() != QEvent.TouchEnd
                event.accept()
                return True
        return super().event(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect())
        p.setFont(font(7, QFont.Black, 18))
        p.setPen(T.TEXT_MUTED)
        p.drawText(QRectF(0, 12, r.width(), 14), Qt.AlignCenter, "VIRTUAL OUTPUTS")

        rel = self.data.get("relays") or {}
        labels = [("Fan", rel.get("fan")), ("Heat", rel.get("heat")), ("Cool", rel.get("cool"))]
        x = 22
        for name, on in labels:
            pill = QRectF(x, 34, 58, 36)
            p.setBrush(QColor(79, 92, 113, 110))
            p.setPen(QPen(QColor(255,255,255,42), 1))
            p.drawRoundedRect(pill, 14, 14)
            p.setBrush(T.GREEN if on else T.TEXT)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(pill.x()+25, pill.y()+7, 8, 8))
            p.setFont(font(8, QFont.Black))
            p.setPen(T.TEXT)
            p.drawText(QRectF(pill.x(), pill.y()+18, pill.width(), 14), Qt.AlignCenter, name)
            x += 68

        temp = self.current_virtual_temp()
        pct = (temp - self.virtual_min) / max(1.0, (self.virtual_max - self.virtual_min))
        rail = self.slider_rect()
        knob_x = rail.left() + rail.width() * pct

        p.setFont(font(8, QFont.Black, 18))
        p.setPen(T.TEXT_MUTED)
        p.drawText(QRectF(18, 84, 120, 16), Qt.AlignLeft, "VIRTUAL TEMP")
        p.setFont(font(16, QFont.Black))
        p.setPen(T.TEXT)
        p.drawText(QRectF(136, 78, 96, 28), Qt.AlignRight, fmt_temp(temp))

        p.setPen(QPen(QColor(255,255,255,70), 5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(rail.left(), rail.center().y()), QPointF(rail.right(), rail.center().y()))
        p.setPen(QPen(T.CYAN, 7, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(rail.left(), rail.center().y()), QPointF(knob_x, rail.center().y()))

        glow = QRadialGradient(QPointF(knob_x, rail.center().y()), 25)
        glow.setColorAt(0, QColor(90, 235, 255, 155))
        glow.setColorAt(1, QColor(90, 235, 255, 0))
        p.setBrush(glow)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(knob_x - 25, rail.center().y() - 25, 50, 50))
        p.setBrush(QColor(246, 248, 255))
        p.setPen(QPen(QColor(103, 226, 255, 190), 2))
        p.drawEllipse(QRectF(knob_x - 9, rail.center().y() - 9, 18, 18))

        p.setFont(font(7, QFont.Black, 15))
        p.setPen(T.TEXT_MUTED)
        p.drawText(QRectF(18, 136, 34, 16), Qt.AlignLeft, f"{int(self.virtual_min)}°")
        p.drawText(QRectF(192, 136, 34, 16), Qt.AlignRight, f"{int(self.virtual_max)}°")
        p.setFont(font(8, QFont.Black, 15))
        p.drawText(QRectF(18, 150, 216, 16), Qt.AlignCenter, "SLIDE TO TEST TEMP")


class RoomScreen(Page):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(app_state, parent)
        self.cards: list[RoomControlCard] = []
        self.room_buttons: dict[str, RoundButton] = {}
        # Home Assistant often reports the old value for a second or two after a
        # switch/lock command. Hold the intended local state during that round
        # trip so Room cards do not flicker back and forth.
        self._room_pending: dict[str, dict[str, Any]] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 0, 30, 22)
        root.setSpacing(8)
        self.panel = GlassPanel(radius=30)
        root.addWidget(self.panel, 1)
        self.lay = QVBoxLayout(self.panel)
        self.lay.setContentsMargins(24, 2, 24, 10)
        self.lay.setSpacing(0)
        top = QHBoxLayout()
        self.title = SectionTitle("Room Control", "Living Room")
        top.addWidget(self.title)
        top.addStretch(1)
        self.room_tabs = QHBoxLayout()
        self.room_tabs.setSpacing(12)
        self.room_tabs.setContentsMargins(0, 0, 0, 0)
        top.addLayout(self.room_tabs)
        self.lay.addLayout(top)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        self.lay.addLayout(self.grid, 1)

    def rebuild(self):
        for b in self.room_buttons.values():
            b.setParent(None)
        self.room_buttons = {}
        while self.room_tabs.count():
            item = self.room_tabs.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        rooms = nested_get(self.config, "roomControl", "rooms", default={}) or {}
        active = nested_get(self.config, "roomControl", "room", default="living")
        for key, room in rooms.items():
            b = RoundButton(room.get("label") or key, active=key == active, min_h=38)
            b.setMinimumWidth(112)
            b.clicked.connect(lambda checked=False, k=key: self.set_room(k))
            self.room_buttons[key] = b
            self.room_tabs.addWidget(b)
        self.populate_cards()

    def set_room(self, key: str):
        self.config.setdefault("roomControl", {})["room"] = key
        self.rebuild()
        self.run_async("room-save", lambda: self.s.api.save_config(self.config), None, None)

    def populate_cards(self):
        for c in self.cards:
            c.setParent(None)
            c.deleteLater()
        self.cards = []
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        active = nested_get(self.config, "roomControl", "room", default="living")
        room = nested_get(self.config, "roomControl", "rooms", active, default={}) or {}
        self.title.setText(f"<span style='color:#46e8ff; letter-spacing:4px; font-size:11px; font-weight:900'>ROOM CONTROL</span><br><span style='font-size:38px; font-weight:1000; color:#ffffff'>{room.get('label') or active}</span>")
        controls = room.get("controls") or []
        for idx, control in enumerate(controls):
            card = RoomControlCard(control)
            card.clicked.connect(lambda checked=False, ctl=control: self.toggle_control(ctl))
            card.held.connect(lambda ctl=control: self.requestAssign.emit("room", ctl, "room"))
            self.cards.append(card)
            self.grid.addWidget(card, idx // 6, idx % 6)
        for col in range(6):
            self.grid.setColumnStretch(col, 1)
        for row in range(3):
            self.grid.setRowStretch(row, 1)
        self.grid.setRowStretch((len(controls) + 5) // 6, 1)

    def sync(self, config: dict, thermostat: dict):
        first = self.config is not config
        super().sync(config, thermostat)
        if first or not self.cards:
            self.rebuild()
        else:
            active = nested_get(config, "roomControl", "room", default="living")
            controls = nested_get(config, "roomControl", "rooms", active, "controls", default=[]) or []
            for card, ctl in zip(self.cards, controls):
                card.setControl(ctl)

    def _room_eid(self, ctl: dict) -> str:
        return str((ctl or {}).get("haEntityId") or "").strip()

    def _room_domain(self, ctl: dict) -> str:
        entity_id = self._room_eid(ctl)
        return str((ctl or {}).get("domain") or (entity_id.split(".", 1)[0] if "." in entity_id else "")).lower()

    def _room_state_text_for_on(self, ctl: dict, is_on: bool) -> str:
        domain = self._room_domain(ctl)
        if domain == "lock":
            return "unlocked" if is_on else "locked"
        if domain == "cover":
            return "open" if is_on else "closed"
        return "on" if is_on else "off"

    def _room_pending_for(self, ctl: dict) -> dict[str, Any] | None:
        eid = self._room_eid(ctl)
        pending = self._room_pending.get(eid) if eid else None
        if pending and time.monotonic() > float(pending.get("until") or 0):
            self._clear_room_pending(ctl)
            return None
        return pending

    def _set_room_pending(self, ctl: dict, target_on: bool, previous_state: Any, previous_on: bool):
        eid = self._room_eid(ctl)
        if not eid:
            return
        target_state = self._room_state_text_for_on(ctl, target_on)
        pending = {
            "on": bool(target_on),
            "state": target_state,
            "until": time.monotonic() + 8.0,
            "previous_state": previous_state,
            "previous_on": bool(previous_on),
        }
        self._room_pending[eid] = pending
        ctl["_pendingUntil"] = pending["until"]
        ctl["_pendingOn"] = bool(target_on)
        ctl["_pendingState"] = target_state

    def _clear_room_pending(self, ctl: dict):
        eid = self._room_eid(ctl)
        if eid:
            self._room_pending.pop(eid, None)
        for key in ("_pendingUntil", "_pendingOn", "_pendingState"):
            ctl.pop(key, None)

    def _room_pending_matches(self, pending: dict[str, Any], incoming_state: Any, domain: str) -> bool:
        incoming_text = str(incoming_state or "").strip().lower()
        target_state = str(pending.get("state") or "").strip().lower()
        if target_state and incoming_text == target_state:
            return True
        return room_control_active(incoming_text, domain) == bool(pending.get("on"))

    def _apply_room_state_to_control(self, ctl: dict, state: dict):
        if not isinstance(state, dict):
            return
        domain = state.get("domain") or ctl.get("domain") or ""
        ctl["haName"] = state.get("name") or ctl.get("haName")
        ctl["name"] = state.get("name") or ctl.get("name") or ctl.get("haName")
        ctl["domain"] = domain or ctl.get("domain")
        for meta_key in ("deviceClass", "icon", "supportedFeatures", "currentPosition"):
            if meta_key in state:
                ctl[meta_key] = state.get(meta_key)

        pending = self._room_pending_for(ctl)
        incoming_state = state.get("state") or "unknown"
        if pending:
            if self._room_pending_matches(pending, incoming_state, domain):
                self._clear_room_pending(ctl)
            else:
                # This is a stale HA refresh from before the command finished.
                # Keep the local target visible and wait for HA to catch up.
                ctl["state"] = pending.get("state") or ctl.get("state") or "unknown"
                ctl["on"] = bool(pending.get("on"))
                return

        ctl["state"] = incoming_state
        ctl["on"] = room_control_active(ctl.get("state"), domain)

    def _latest_room_control_config(self, ctl: dict) -> dict:
        """Return the freshest saved config record for a Room control.

        Room cards intentionally keep live HA state locally, while Settings saves
        replace ``AppState.config`` with the backend's newly returned config object.
        Resolve PIN policy from that newest object so On/Off protection changes take
        effect immediately instead of being read from an older card/config snapshot.
        """
        entity_id = self._room_eid(ctl)
        control_id = str((ctl or {}).get("id") or "").strip()
        rooms = nested_get(self.s.config, "roomControl", "rooms", default={}) or {}
        for room in rooms.values():
            for saved in (room or {}).get("controls") or []:
                if not isinstance(saved, dict):
                    continue
                saved_entity_id = str(saved.get("haEntityId") or "").strip()
                saved_control_id = str(saved.get("id") or "").strip()
                if entity_id and saved_entity_id == entity_id:
                    return saved
                if control_id and saved_control_id == control_id:
                    return saved
        return ctl or {}

    def _entry_code_for_action(self, ctl: dict, action: str) -> str | None:
        code = str((ctl or {}).get("accessCode") or "").strip()
        if not code or not room_control_action_requires_code(ctl, action):
            return None
        return code

    def toggle_control(self, ctl: dict):
        eid = ctl.get("haEntityId") or ""
        if not eid:
            self.requestAssign.emit("room", ctl, "room")
            return
        if self._room_pending_for(ctl):
            self.requestToast.emit("Still updating Home Assistant…")
            return
        action = room_control_next_action(ctl)
        # The live card owns current on/off state, but the saved config owns PIN
        # policy.  Keeping those sources separate prevents a stale Room page from
        # treating both On and Off as protected after Settings changed one of them.
        policy_ctl = self._latest_room_control_config(ctl)
        required_code = self._entry_code_for_action(policy_ctl, action)
        entered_code = None
        if required_code:
            label = ctl.get("haName") or ctl.get("name") or "Entry"
            entered_code = CodeKeypadDialog.get_code(self, "Entry Locked", f"Enter Code for {compact_name(label, 20)}", required_code)
            if entered_code is None:
                return
        previous_state = ctl.get("state")
        previous_on = bool(ctl.get("on"))
        if action in {"toggle", "on", "off", "lock", "unlock", "open", "close"}:
            if action == "toggle":
                target_on = not previous_on
            elif action in {"on", "unlock", "open"}:
                target_on = True
            else:
                target_on = False
            ctl["on"] = target_on
            ctl["state"] = self._room_state_text_for_on(ctl, target_on)
            self._set_room_pending(ctl, target_on, previous_state, previous_on)
            self.sync(self.s.config, self.s.thermostat)
        service_code = entered_code or (self.config.get("alarm") or {}).get("disarmCode", "")
        payload = self.s.ha_payload({"entityId": eid, "action": action, "code": service_code})

        def done(result):
            state = (result or {}).get("control") or {} if isinstance(result, dict) else {}
            if state:
                self._apply_room_state_to_control(ctl, state)
                self.sync(self.s.config, self.s.thermostat)

        def failed(err):
            self._clear_room_pending(ctl)
            ctl["state"] = previous_state
            ctl["on"] = previous_on
            self.sync(self.s.config, self.s.thermostat)
            self.requestToast.emit(f"Control failed: {err}")

        self.run_async(
            "room-action",
            lambda: self.s.api.post("/api/ha/room/action", payload),
            done,
            failed,
        )

    def poll(self):
        controls = []
        active = nested_get(self.config, "roomControl", "room", default="living")
        for ctl in nested_get(self.config, "roomControl", "rooms", active, "controls", default=[]) or []:
            if ctl.get("haEntityId"):
                controls.append(ctl)
        if not controls:
            return
        payload = self.s.ha_payload({"entityIds": [c["haEntityId"] for c in controls]})

        def done(result):
            by_id = {x.get("entityId"): x for x in (result or {}).get("controls") or []}
            for ctl in controls:
                st = by_id.get(ctl.get("haEntityId"))
                if st:
                    self._apply_room_state_to_control(ctl, st)
            self.sync(self.s.config, self.s.thermostat)

        self.run_async("room-poll", lambda: self.s.api.post("/api/ha/room/states", payload), done, None)


class LightsScreen(Page):
    _lightActionCompleted = pyqtSignal(object)
    _lightColorCompleted = pyqtSignal(object)
    _lightPollCompleted = pyqtSignal(object)

    def __init__(self, app_state: AppState, parent=None):
        super().__init__(app_state, parent)
        self.cards: list[LightCard] = []
        self.room_buttons = {}
        self._card_by_entity: dict[str, LightCard] = {}
        self._light_send_timers: dict[str, QTimer] = {}
        self._light_color_timers: dict[str, QTimer] = {}
        self._light_pending: dict[str, dict] = {}
        self._light_color_pending: dict[str, dict] = {}
        self._light_inflight: set[str] = set()
        self._light_color_inflight: set[str] = set()
        self._light_power_pending: dict[str, dict] = {}
        self._light_settle_until: dict[str, float] = {}
        self._light_poll_running = False
        self._last_light_error = ""
        self._lightActionCompleted.connect(self._handle_light_action_completed)
        self._lightColorCompleted.connect(self._handle_light_color_completed)
        self._lightPollCompleted.connect(self._handle_light_poll_completed)

        root = QVBoxLayout(self)
        # Tighten the light page shell so the controls can use the full glass panel
        # height instead of leaving a large unused strip at the bottom.
        root.setContentsMargins(30, 0, 30, 12)
        self.panel = GlassPanel(radius=30)
        root.addWidget(self.panel, 1)
        self.lay = QVBoxLayout(self.panel)
        self.lay.setContentsMargins(24, 12, 24, 10)
        self.lay.setSpacing(4)
        top = QHBoxLayout()
        self.title = SectionTitle("Light Control", "Living Room")
        top.addWidget(self.title)
        top.addStretch(1)
        self.room_tabs = QHBoxLayout()
        top.addLayout(self.room_tabs)
        self.lay.addLayout(top)
        center_buttons = QHBoxLayout()
        center_buttons.setContentsMargins(0, 0, 0, 0)
        center_buttons.setSpacing(8)
        center_buttons.addStretch(1)
        self.on_btn = HoldRoundButton("Room On", active=True, min_h=44)
        self.off_btn = HoldRoundButton("Room Off", kind="purple", min_h=44)
        self.on_btn.setMinimumWidth(148); self.off_btn.setMinimumWidth(148)
        center_buttons.addWidget(self.on_btn); center_buttons.addWidget(self.off_btn)
        center_buttons.addStretch(1)
        self.lay.addLayout(center_buttons)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 2, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(8)
        self.lay.addLayout(self.grid, 1)
        self.on_btn.clicked.connect(lambda: self.room_action(True))
        self.off_btn.clicked.connect(lambda: self.room_action(False))
        self.on_btn.held.connect(self.open_room_color_picker)
        self.off_btn.held.connect(self.open_room_color_picker)

    def rebuild(self):
        while self.room_tabs.count():
            item = self.room_tabs.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.room_buttons = {}
        rooms = nested_get(self.config, "lights", "rooms", default={}) or {}
        active = nested_get(self.config, "lights", "room", default="living")
        for key, room in rooms.items():
            b = RoundButton(room.get("label") or key, active=key == active, min_h=46)
            b.setMinimumWidth(128)
            b.clicked.connect(lambda checked=False, k=key: self.set_room(k))
            self.room_buttons[key] = b
            self.room_tabs.addWidget(b)
        self.populate_cards()

    def set_room(self, key):
        self.config.setdefault("lights", {})["room"] = key
        self.rebuild()
        self.run_async("lights-save-room", lambda: self.s.api.save_config(self.config), None, None)
        # Do not wait for the normal poll cadence when changing rooms.
        QTimer.singleShot(80, self.poll)

    def populate_cards(self):
        for c in self.cards:
            c.setParent(None); c.deleteLater()
        self.cards = []
        self._card_by_entity = {}
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        active = nested_get(self.config, "lights", "room", default="living")
        room = nested_get(self.config, "lights", "rooms", active, default={}) or {}
        self.title.setText(f"<span style='color:#46e8ff; letter-spacing:4px; font-size:11px; font-weight:900'>LIGHT CONTROL</span><br><span style='font-size:38px; font-weight:1000; color:#ffffff'>{room.get('label') or active}</span>")
        for idx, light in enumerate(room.get("lights") or []):
            card = LightCard(light)
            card.powerClicked.connect(self.toggle_light)
            card.held.connect(lambda l=light: self.requestAssign.emit("light", l, "light"))
            card.brightnessChanged.connect(self.set_brightness)
            card.colorRequested.connect(self.open_light_color_picker)
            self.cards.append(card)
            eid = str(light.get("haEntityId") or "")
            if eid:
                self._card_by_entity[eid] = card
            self.grid.addWidget(card, idx // 6, idx % 6)
        for col in range(6):
            self.grid.setColumnStretch(col, 1)

        # The old layout stretched a blank row below the light cards, which kept
        # the cards short and left a large empty area at the bottom of the page.
        # Stretch the actual card rows instead so the sliders grow downward and
        # use the available space. Reset a few rows first because QGridLayout
        # keeps stretch values when rooms are rebuilt with different counts.
        rows = max(1, (len(self.cards) + 5) // 6)
        for row in range(4):
            self.grid.setRowStretch(row, 0)
        for row in range(rows):
            self.grid.setRowStretch(row, 1)

    def sync(self, config, thermostat):
        first = self.config is not config
        super().sync(config, thermostat)
        if first or not self.cards:
            self.rebuild()
        else:
            active = nested_get(config, "lights", "room", default="living")
            lights = nested_get(config, "lights", "rooms", active, "lights", default=[]) or []
            for card, light in zip(self.cards, lights):
                eid = str(light.get("haEntityId") or "")
                card.setLight(light, preserve_slider=self._light_is_busy(eid))

    def _active_lights(self) -> list[dict]:
        active = nested_get(self.config, "lights", "room", default="living")
        return [x for x in nested_get(self.config, "lights", "rooms", active, "lights", default=[]) or [] if x.get("haEntityId")]

    def _light_is_busy(self, entity_id: str) -> bool:
        entity_id = str(entity_id or "")
        if not entity_id:
            return False
        card = self._card_by_entity.get(entity_id)
        if card is not None and card.isSliderActive():
            return True
        if entity_id in self._light_pending or entity_id in self._light_inflight:
            return True
        if entity_id in self._light_color_pending or entity_id in self._light_color_inflight:
            return True
        if entity_id in self._light_power_pending:
            return True
        return time.monotonic() < self._light_settle_until.get(entity_id, 0.0)

    def _set_light_optimistic(self, light: dict, value: int | None = None, action: str | None = None, color: str | None = None):
        if value is not None:
            value = int(clamp(value, 0, 100))
            light["brightness"] = value
            light["on"] = value > 0
            if value > 0:
                light["lastBrightness"] = value
        if action == "off":
            light["on"] = False
            light["brightness"] = 0
        elif action in {"on", "brightness", "color"}:
            light["on"] = True
            if value is None and int(light.get("brightness") or 0) <= 0:
                value = int(light.get("lastBrightness") or 100)
                light["brightness"] = value
                light["lastBrightness"] = value
        if color:
            light["color"] = LightColorDialog.clean_color(color)
            light["colorSupported"] = True
        self.sync(self.s.config, self.s.thermostat)

    def room_action(self, turn_on: bool):
        lights = self._active_lights()
        for light in lights:
            bright = int(light.get("lastBrightness") or light.get("brightness") or 100)
            self._send_light(light, "on" if turn_on else "off", bright if turn_on else 0)

    def _open_live_color_dialog(self, lights: list[dict], current: str, title: str):
        """Open a touch-friendly RGB picker and push color changes live.

        The dialog itself does not have Apply/Cancel anymore. It emits each
        color as soon as the user taps or drags. A short debounce keeps the Pi
        responsive while still making the bulb update feel immediate.
        """
        targets = [x for x in lights if x.get("haEntityId")]
        if not targets:
            return
        dlg = LightColorDialog(current, title, self)
        pending = {"color": None}
        live_timer = QTimer(dlg)
        live_timer.setSingleShot(True)

        def brightness_for(light: dict) -> int:
            value = int(light.get("brightness") or light.get("lastBrightness") or 100)
            return value if value > 0 else int(light.get("lastBrightness") or 100)

        def flush_color():
            color = pending.get("color")
            if not color:
                return
            for item in targets:
                self._send_light_color(item, brightness_for(item), color)

        def live_color(color: str):
            color = LightColorDialog.clean_color(color)
            pending["color"] = color
            for item in targets:
                self._set_light_optimistic(item, brightness_for(item), action="color", color=color)
            live_timer.start(90)

        live_timer.timeout.connect(flush_color)
        dlg.colorPreviewed.connect(live_color)
        dlg.exec_()
        if live_timer.isActive():
            live_timer.stop()
            flush_color()

    def open_room_color_picker(self):
        lights = self._active_lights()
        rgb_lights = [x for x in lights if bool(x.get("colorSupported"))]
        if not rgb_lights:
            self.requestToast.emit("No RGB lights in this room")
            return
        current = rgb_lights[0].get("color") or rgb_lights[0].get("colorHex") or "#ffd76f"
        self._open_live_color_dialog(rgb_lights, current, "Room RGB Color")

    def open_light_color_picker(self, light: dict):
        if not light.get("haEntityId"):
            self.requestAssign.emit("light", light, "light")
            return
        if not bool(light.get("colorSupported")):
            self.requestToast.emit("That light is not marked as RGB")
            return
        name = light.get("haName") or light.get("name") or "RGB Light"
        current = light.get("color") or light.get("colorHex") or "#ffd76f"
        self._open_live_color_dialog([light], current, f"{compact_name(name, 24)} RGB Color")

    def toggle_light(self, light: dict):
        if not light.get("haEntityId"):
            self.requestAssign.emit("light", light, "light")
            return
        action = "off" if bool(light.get("on")) else "on"
        bright = int(light.get("lastBrightness") or light.get("brightness") or 100) if action == "on" else 0
        self._send_light(light, action, bright)

    def _cancel_pending_light_adjustments(self, entity_id: str):
        """Prevent a stale dim/color command from running after a power command."""
        entity_id = str(entity_id or "")
        timer = self._light_send_timers.get(entity_id)
        if timer is not None and timer.isActive():
            timer.stop()
        color_timer = self._light_color_timers.get(entity_id)
        if color_timer is not None and color_timer.isActive():
            color_timer.stop()
        self._light_pending.pop(entity_id, None)
        self._light_color_pending.pop(entity_id, None)

    def _flush_pending_light_power(self, entity_id: str):
        pending = self._light_power_pending.get(str(entity_id or ""))
        if not isinstance(pending, dict):
            return
        if entity_id in self._light_inflight or entity_id in self._light_color_inflight:
            QTimer.singleShot(100, lambda eid=entity_id: self._flush_pending_light_power(eid))
            return
        self._light_power_pending.pop(entity_id, None)
        self._send_light(
            pending.get("light") or {},
            str(pending.get("action") or "off"),
            pending.get("brightness"),
            pending.get("color"),
        )

    def _timer_for_light(self, entity_id: str) -> QTimer:
        timer = self._light_send_timers.get(entity_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda eid=entity_id: self._flush_light_send(eid))
            self._light_send_timers[entity_id] = timer
        return timer

    def _timer_for_light_color(self, entity_id: str) -> QTimer:
        timer = self._light_color_timers.get(entity_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda eid=entity_id: self._flush_light_color_send(eid))
            self._light_color_timers[entity_id] = timer
        return timer

    def _send_light_color(self, light: dict, brightness: int, color: str):
        entity_id = str(light.get("haEntityId") or "")
        if not entity_id:
            return
        color = LightColorDialog.clean_color(color)
        brightness = int(clamp(brightness, 1, 100))
        self._set_light_optimistic(light, brightness, action="color", color=color)
        # Keep HA state refreshes from snapping RGB controls backward while the
        # user is dragging the color picker. Only the newest pending color is sent.
        self._light_settle_until[entity_id] = time.monotonic() + 1.35
        self._light_color_pending[entity_id] = {"light": light, "brightness": brightness, "color": color}
        timer = self._timer_for_light_color(entity_id)
        if entity_id in self._light_color_inflight:
            return
        if not timer.isActive():
            timer.start(120)

    def _flush_light_color_send(self, entity_id: str):
        pending = self._light_color_pending.get(entity_id)
        if not pending:
            return
        if entity_id in self._light_color_inflight:
            return
        light = pending.get("light") or {}
        brightness = int(clamp(pending.get("brightness", 100), 1, 100))
        color = LightColorDialog.clean_color(str(pending.get("color") or "#ffd76f"))
        self._light_color_inflight.add(entity_id)
        payload = self.s.ha_payload({
            "entityId": entity_id,
            "action": "color",
            "brightness": brightness,
            "color": color,
            "transition": 0.20,
            "refresh": False,
        })
        api = self.s.api

        def worker():
            try:
                result = api.post("/api/ha/light/action", payload)
                self._lightColorCompleted.emit({"entityId": entity_id, "light": light, "brightness": brightness, "color": color, "result": result, "error": None})
            except Exception as exc:
                self._lightColorCompleted.emit({"entityId": entity_id, "light": light, "brightness": brightness, "color": color, "result": None, "error": str(exc)})

        threading.Thread(target=worker, name=f"light-color-{entity_id}", daemon=True).start()

    def _handle_light_color_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        entity_id = str(data.get("entityId") or "")
        color = LightColorDialog.clean_color(str(data.get("color") or "#ffd76f"))
        brightness = int(clamp(data.get("brightness", 100), 1, 100))
        self._light_color_inflight.discard(entity_id)
        if data.get("error"):
            err = str(data.get("error"))
            if err != self._last_light_error:
                self._last_light_error = err
                self.requestToast.emit(f"Light failed: {err}")
        latest = self._light_color_pending.get(entity_id)
        if latest:
            latest_color = LightColorDialog.clean_color(str(latest.get("color") or color))
            latest_brightness = int(clamp(latest.get("brightness", brightness), 1, 100))
            if latest_color != color or latest_brightness != brightness:
                self._timer_for_light_color(entity_id).start(80)
                return
        self._light_color_pending.pop(entity_id, None)
        self._light_settle_until[entity_id] = time.monotonic() + 0.90
        QTimer.singleShot(950, self.poll)

    def set_brightness(self, light: dict, value: int):
        entity_id = str(light.get("haEntityId") or "")
        if not entity_id:
            return
        value = int(clamp(value, 0, 100))
        self._set_light_optimistic(light, value, action="brightness")
        # Protect the local slider while HA catches up so a half-second-old HA
        # state cannot snap the knob back under the user's finger.
        self._light_settle_until[entity_id] = time.monotonic() + 1.35
        self._light_pending[entity_id] = {"light": light, "value": value}
        timer = self._timer_for_light(entity_id)
        if entity_id in self._light_inflight:
            return
        if not timer.isActive():
            # Small debounce: quick enough to feel live, light enough for the Pi
            # and Home Assistant when the slider generates many touch events.
            timer.start(90)

    def _flush_light_send(self, entity_id: str):
        pending = self._light_pending.get(entity_id)
        if not pending:
            return
        if entity_id in self._light_inflight:
            return
        light = pending.get("light") or {}
        value = int(clamp(pending.get("value", 0), 0, 100))
        action = "off" if value <= 0 else "brightness"
        self._light_inflight.add(entity_id)
        payload = self.s.ha_payload({
            "entityId": entity_id,
            "action": action,
            "brightness": value,
            "transition": 0.25,
            "refresh": False,
        })
        api = self.s.api

        def worker():
            try:
                result = api.post("/api/ha/light/action", payload)
                self._lightActionCompleted.emit({"entityId": entity_id, "light": light, "value": value, "result": result, "error": None})
            except Exception as exc:
                self._lightActionCompleted.emit({"entityId": entity_id, "light": light, "value": value, "result": None, "error": str(exc)})

        threading.Thread(target=worker, name=f"light-brightness-{entity_id}", daemon=True).start()

    def _handle_light_action_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        entity_id = str(data.get("entityId") or "")
        value = int(clamp(data.get("value", 0), 0, 100))
        self._light_inflight.discard(entity_id)
        if data.get("error"):
            err = str(data.get("error"))
            if err != self._last_light_error:
                self._last_light_error = err
                self.requestToast.emit(f"Light failed: {err}")
        latest = self._light_pending.get(entity_id)
        if latest and int(clamp(latest.get("value", value), 0, 100)) != value:
            self._timer_for_light(entity_id).start(70)
            return
        self._light_pending.pop(entity_id, None)
        # Let the physical light finish transitioning before accepting HA state
        # back into the slider.
        self._light_settle_until[entity_id] = time.monotonic() + 0.80
        QTimer.singleShot(950, self.poll)

    def _send_light(self, light: dict, action: str, brightness: int | None = None, color: str | None = None):
        entity_id = str(light.get("haEntityId") or "")
        if not entity_id:
            return
        action = str(action or "").strip().lower()
        if action in {"on", "off", "toggle"}:
            self._cancel_pending_light_adjustments(entity_id)
            if entity_id in self._light_inflight or entity_id in self._light_color_inflight:
                # Let an already-sent dim/color request finish, then send the
                # newest power command last so an old request cannot turn the
                # light back on after Room Off or an individual Off tap.
                self._light_power_pending[entity_id] = {
                    "light": light,
                    "action": action,
                    "brightness": brightness,
                    "color": color,
                }
                self._set_light_optimistic(light, brightness, action=action, color=None if action == "off" else color)
                QTimer.singleShot(100, lambda eid=entity_id: self._flush_pending_light_power(eid))
                return
            self._light_power_pending.pop(entity_id, None)
        payload = {"entityId": entity_id, "action": action, "refresh": False}
        if brightness is not None:
            payload["brightness"] = int(clamp(brightness, 0, 100))
            # A transition on light.turn_off is unreliable on some Z-Wave
            # dimmers. Off must be a clean power service call, not a fade.
            if action != "off":
                payload["transition"] = 0.25
        if color and action != "off":
            payload["color"] = color
        elif action != "off" and light.get("colorSupported") and light.get("color"):
            payload["color"] = light.get("color")
        self._set_light_optimistic(light, payload.get("brightness"), action=action, color=payload.get("color"))
        self._light_settle_until[entity_id] = time.monotonic() + 0.80

        def worker():
            return self.s.api.post("/api/ha/light/action", self.s.ha_payload(payload))

        def done(result):
            returned = (result or {}).get("light") if isinstance(result, dict) else None
            if isinstance(returned, dict):
                self._apply_light_state(light, returned)
                self.sync(self.s.config, self.s.thermostat)
                if action == "off" and returned.get("commandVerified") is False:
                    self.requestToast.emit(f"{light.get('haName') or light.get('name') or 'Light'} did not confirm Off")
            QTimer.singleShot(950, self.poll)

        self.run_async(
            "light-action",
            worker,
            done,
            lambda err: self.requestToast.emit(f"Light failed: {err}"),
        )

    def _apply_light_state(self, light: dict, st: dict):
        if not st:
            return
        light["on"] = as_bool_state(st.get("state"))
        if st.get("brightnessPct") is not None:
            light["brightness"] = int(st.get("brightnessPct") or 0)
        elif st.get("brightness") is not None:
            light["brightness"] = int(st.get("brightness") or 0)
        if light.get("brightness"):
            light["lastBrightness"] = int(light.get("brightness") or 0)
        if st.get("colorHex"):
            light["color"] = st.get("colorHex")
        if st.get("colorSupported") is not None:
            light["colorSupported"] = bool(st.get("colorSupported"))
        light["haName"] = st.get("name") or light.get("haName")

    def poll(self):
        lights = self._active_lights()
        if not lights or self._light_poll_running:
            return
        active = nested_get(self.config, "lights", "room", default="living")
        entity_ids = [l["haEntityId"] for l in lights]
        payload = self.s.ha_payload({"entityIds": entity_ids, "fresh": True})
        api = self.s.api
        self._light_poll_running = True

        def worker():
            try:
                result = api.post("/api/ha/light/states", payload)
                self._lightPollCompleted.emit({"room": active, "result": result, "error": None})
            except Exception as exc:
                self._lightPollCompleted.emit({"room": active, "result": None, "error": str(exc)})

        threading.Thread(target=worker, name="light-state-poll", daemon=True).start()

    def _handle_light_poll_completed(self, info: object):
        self._light_poll_running = False
        data = info if isinstance(info, dict) else {}
        if data.get("error"):
            return
        if data.get("room") != nested_get(self.config, "lights", "room", default="living"):
            return
        result = data.get("result") or {}
        by_id = {x.get("entityId"): x for x in result.get("lights") or []}
        for light in self._active_lights():
            entity_id = str(light.get("haEntityId") or "")
            if self._light_is_busy(entity_id):
                continue
            st = by_id.get(entity_id)
            if st:
                self._apply_light_state(light, st)
        self.sync(self.s.config, self.s.thermostat)


class BlindCard(GlassPanel):
    openClicked = pyqtSignal(dict)
    closeClicked = pyqtSignal(dict)
    positionRequested = pyqtSignal(dict, int)
    assignRequested = pyqtSignal(dict)

    def __init__(self, blind: dict, parent=None):
        super().__init__(parent, radius=24)
        self.blind = blind
        self.setMinimumSize(244, 520)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.name = QLabel(self)
        self.pos = QLabel(self)
        self.open_btn = RoundButton("Open", active=False, min_h=38, parent=self)
        self.close_btn = RoundButton("Close", active=False, min_h=38, parent=self)
        self.preview = BlindPreview(parent=self)
        self.open_btn.setStyleSheet(button_style(False) + "QPushButton{background:#ffe8a6;color:#2b2116;border-radius:18px;}" )
        self.close_btn.setStyleSheet(button_style(False) + "QPushButton{background:#f2eee4;color:#2b2116;border-radius:18px;}" )
        self.open_btn.clicked.connect(lambda: self.openClicked.emit(self.blind))
        self.close_btn.clicked.connect(lambda: self.closeClicked.emit(self.blind))
        self.preview.positionPreviewed.connect(self.preview_position)
        self.preview.positionRequested.connect(lambda value: self.positionRequested.emit(self.blind, int(value)))
        self.updateData(blind)

    def resizeEvent(self, event):
        self.name.setGeometry(14, 14, self.width()-96, 28)
        self.pos.setGeometry(self.width()-80, 14, 66, 28)
        self.open_btn.setGeometry(14, 48, self.width()-28, 38)
        self.preview.setGeometry(14, 88, self.width()-28, self.height()-136)
        self.close_btn.setGeometry(14, self.height()-48, self.width()-28, 38)

    def updateData(self, blind: dict):
        self.blind = blind
        self.name.setText(blind.get("haName") or blind.get("name") or "Blind")
        self.name.setFont(font(11, QFont.Black))
        self.name.setStyleSheet("color:#f6f8ff;")
        try:
            position = int(blind.get("position") or 0)
        except Exception:
            position = 0
        self.pos.setText(f"{position}%")
        self.pos.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pos.setFont(font(10, QFont.Black))
        self.pos.setStyleSheet("color:#ffe8a6;")
        self.preview.setPosition(position)

    def preview_position(self, value: int):
        try:
            value = max(0, min(100, int(value)))
        except Exception:
            return
        self.blind["position"] = value
        self.blind["pendingPosition"] = value
        self.pos.setText(f"{value}%")

    def hold_pending_position(self, value: int, seconds: float = 18.0):
        try:
            value = max(0, min(100, int(value)))
        except Exception:
            return
        self.blind["position"] = value
        self.blind["pendingPosition"] = value
        self.blind["pendingPositionUntil"] = time.time() + seconds
        self.preview.setPosition(value)
        self.pos.setText(f"{value}%")

    def mousePressEvent(self, event):
        self._press_time = time.time()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if getattr(self, "_press_time", 0) and time.time() - self._press_time > 0.7:
            self.assignRequested.emit(self.blind)
        super().mouseReleaseEvent(event)


class BlindsScreen(Page):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(app_state, parent)
        self.cards: list[BlindCard] = []
        self.room_buttons = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 0, 30, 22)
        self.panel = GlassPanel(radius=30)
        root.addWidget(self.panel, 1)
        self.lay = QVBoxLayout(self.panel)
        self.lay.setContentsMargins(24, 14, 24, 18)
        self.lay.setSpacing(8)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(14)
        self.title = SectionTitle("Shade Control", "Living Room")
        self.title.setMinimumHeight(52)
        top.addWidget(self.title)
        top.addStretch(1)

        # These are the room selector buttons only. Keep them up in the header.
        self.room_tabs = QHBoxLayout()
        top.addLayout(self.room_tabs)
        self.lay.addLayout(top)

        # Open Room / Close Room are room-wide shade commands, not room tabs.
        # Keep them centered on their own row so they read as global controls.
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 2)
        buttons.setSpacing(14)
        buttons.addStretch(1)
        self.open_room = RoundButton("Open Room", active=True, min_h=44)
        self.close_room = RoundButton("Close Room", kind="purple", min_h=44)
        self.open_room.setMinimumWidth(164); self.close_room.setMinimumWidth(164)
        self.open_room.setFixedHeight(44); self.close_room.setFixedHeight(44)
        buttons.addWidget(self.open_room)
        buttons.addWidget(self.close_room)
        buttons.addStretch(1)
        self.lay.addLayout(buttons)

        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.lay.addLayout(self.grid, 1)
        self.open_room.clicked.connect(lambda: self.room_action("open"))
        self.close_room.clicked.connect(lambda: self.room_action("close"))

    def rebuild(self):
        while self.room_tabs.count():
            item = self.room_tabs.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        rooms = nested_get(self.config, "blinds", "rooms", default={}) or {}
        active = nested_get(self.config, "blinds", "room", default="living")
        for key, room in rooms.items():
            b = RoundButton(room.get("label") or key, active=key == active, min_h=38)
            b.setMinimumWidth(118)
            b.setFixedHeight(38)
            b.setStyleSheet(button_style(key == active) + "QPushButton{border-radius:19px; padding:0 14px;}")
            b.clicked.connect(lambda checked=False, k=key: self.set_room(k))
            self.room_tabs.addWidget(b)
        self.populate_cards()

    def set_room(self, key):
        self.config.setdefault("blinds", {})["room"] = key
        self.rebuild()
        self.run_async("blinds-save-room", lambda: self.s.api.save_config(self.config), None, None)

    def populate_cards(self):
        for c in self.cards:
            c.setParent(None); c.deleteLater()
        self.cards = []
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        active = nested_get(self.config, "blinds", "room", default="living")
        room = nested_get(self.config, "blinds", "rooms", active, default={}) or {}
        self.title.setText(f"<span style='color:#46e8ff; letter-spacing:3px; font-size:10px; font-weight:900'>SHADE CONTROL</span><br><span style='font-size:34px; font-weight:1000; color:#ffffff'>{room.get('label') or active}</span>")
        for idx, blind in enumerate(room.get("blinds") or []):
            card = BlindCard(blind)
            card.openClicked.connect(lambda b: self.blind_action(b, "open"))
            card.closeClicked.connect(lambda b: self.blind_action(b, "close"))
            card.positionRequested.connect(lambda b, value: self.blind_action(b, "position", position=value))
            card.assignRequested.connect(lambda b: self.requestAssign.emit("cover", b, "blind"))
            self.cards.append(card)
            self.grid.addWidget(card, 0, idx)
        self.grid.setColumnStretch(len(self.cards), 1)

    def sync(self, config, thermostat):
        first = self.config is not config
        super().sync(config, thermostat)
        if first or not self.cards:
            self.rebuild()
        else:
            active = nested_get(config, "blinds", "room", default="living")
            blinds = nested_get(config, "blinds", "rooms", active, "blinds", default=[]) or []
            for c, b in zip(self.cards, blinds): c.updateData(b)

    def room_action(self, action):
        active = nested_get(self.config, "blinds", "room", default="living")
        for blind in nested_get(self.config, "blinds", "rooms", active, "blinds", default=[]) or []:
            if blind.get("haEntityId"):
                self.blind_action(blind, action, quiet=True)

    def blind_action(self, blind: dict, action: str, quiet: bool = False, position: int | None = None):
        if not blind.get("haEntityId"):
            self.requestAssign.emit("cover", blind, "blind")
            return
        payload = {"entityId": blind.get("haEntityId"), "action": action}
        desired_position = None
        if position is not None:
            desired_position = max(0, min(100, int(position)))
            payload["position"] = desired_position

        # Optimistic update so drag feels instant. Keep this pending long
        # enough for the blind motor to physically reach the target. During
        # that period, status polling will not snap the icon back to the
        # old in-motion position.
        if action == "open":
            desired_position = 100
        elif action == "close":
            desired_position = 0

        if desired_position is not None:
            blind["position"] = desired_position
            blind["pendingPosition"] = desired_position
            blind["pendingPositionUntil"] = time.time() + 18.0
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            state = (result or {}).get("state") or {} if isinstance(result, dict) else {}
            if state.get("currentPosition") is not None:
                reported = int(state.get("currentPosition") or 0)
                pending = blind.get("pendingPosition")
                pending_until = float(blind.get("pendingPositionUntil") or 0)
                if pending is None or time.time() >= pending_until or abs(reported - int(pending)) <= 3:
                    blind["position"] = reported
                    blind.pop("pendingPosition", None)
                    blind.pop("pendingPositionUntil", None)
            blind["haName"] = state.get("name") or blind.get("haName")
            self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "blind-action",
            lambda: self.s.api.post("/api/ha/cover/action", self.s.ha_payload(payload)),
            done,
            lambda err: self.requestToast.emit(f"Blind failed: {err}"),
        )

    def poll(self):
        active = nested_get(self.config, "blinds", "room", default="living")
        blinds = [x for x in nested_get(self.config, "blinds", "rooms", active, "blinds", default=[]) or [] if x.get("haEntityId")]
        if not blinds:
            return
        payload = self.s.ha_payload({"entityIds": [b["haEntityId"] for b in blinds]})

        def done(result):
            by_id = {x.get("entityId"): x for x in (result or {}).get("covers") or []}
            now = time.time()
            for blind in blinds:
                st = by_id.get(blind.get("haEntityId"))
                if st:
                    if st.get("currentPosition") is not None:
                        reported = int(st.get("currentPosition") if st.get("currentPosition") is not None else blind.get("position") or 0)
                        pending = blind.get("pendingPosition")
                        pending_until = float(blind.get("pendingPositionUntil") or 0)
                        if pending is not None and now < pending_until and abs(reported - int(pending)) > 3:
                            # Keep the user-requested preview while the blind is travelling.
                            blind["position"] = int(pending)
                        else:
                            blind["position"] = reported
                            blind.pop("pendingPosition", None)
                            blind.pop("pendingPositionUntil", None)
                    blind["haName"] = st.get("name") or blind.get("haName")
            self.sync(self.s.config, self.s.thermostat)

        self.run_async("blind-poll", lambda: self.s.api.post("/api/ha/cover/states", payload), done, None)


class AudioScreen(Page):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(app_state, parent)
        self.player_state: dict = {}
        self._cover_url = ""
        self._cover_loading_url = ""
        self._cover_cache: dict[str, bytes] = {}
        # Local slider holds keep Home Assistant's delayed state refreshes from
        # snapping the native sliders back to the previous value while the new
        # value is still round-tripping. These are intentionally RAM-only.
        self._audio_number_local: dict[str, dict[str, Any]] = {}
        self._audio_volume_local: dict[str, Any] = {"value": None, "until": 0.0}
        self._audio_assign_short_ms = 700
        self._audio_assign_reassign_ms = 6000
        self._audio_detected_player_id = ""
        self._audio_detect_running_player_id = ""
        self._audio_detect_last_attempt: dict[str, float] = {}
        self.group_player_states: dict[str, dict] = {}
        self.group_buttons: dict[str, RoundButton] = {}
        self._group_signature: tuple = ()
        root = QHBoxLayout(self)
        root.setContentsMargins(28, 6, 28, 18)
        root.setSpacing(18)
        self.left = GlassPanel(radius=28, strong=True)
        self.right = GlassPanel(radius=28, strong=True)
        root.addWidget(self.left, 5)
        root.addWidget(self.right, 4)
        self.build_left()
        self.build_right()

    def build_left(self):
        lay = QVBoxLayout(self.left)
        lay.setContentsMargins(24, 18, 24, 20)
        lay.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.preset_buttons = {}
        for preset, text, icon_key, kind in AUDIO_PRESET_ORDER:
            b = ModernAudioButton(icon_key, text, kind=kind, min_h=78, holdable=False)
            b.setMinimumWidth(126)
            b.setToolTip(f"Apply {text} audio preset")
            b.clicked.connect(lambda checked=False, p=preset: self.apply_audio_preset(p))
            self.preset_buttons[preset] = b
            top.addWidget(b)
        top.addStretch(1)
        lay.addLayout(top)

        now = QLabel("NOW PLAYING")
        now.setAlignment(Qt.AlignCenter)
        now.setFont(font(9, QFont.Black, 28))
        now.setStyleSheet("color:#46e8ff; letter-spacing:5px;")
        lay.addWidget(now)

        self.now_card = GlassPanel(radius=30)
        now_lay = QHBoxLayout(self.now_card)
        now_lay.setContentsMargins(26, 22, 26, 22)
        now_lay.setSpacing(26)
        self.art = CoverArtLabel(250)
        now_lay.addWidget(self.art, 0, Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(8)
        self.track = QLabel("Nothing\nPlaying")
        self.track.setWordWrap(True)
        self.track.setFont(font(38, QFont.Black))
        self.track.setStyleSheet("color:#f6f8ff; line-height:0.96;")
        self.artist_label = QLabel("Livingroom Sonos")
        self.artist_label.setWordWrap(True)
        self.artist_label.setFont(font(18, QFont.Black))
        self.artist_label.setStyleSheet("color:#d9e0ee;")
        self.album_label = QLabel("")
        self.album_label.setWordWrap(True)
        self.album_label.setFont(font(12, QFont.Black))
        self.album_label.setStyleSheet("color:rgba(219,227,244,0.68);")
        self.source_label = QLabel("Livingroom Sonos")
        self.source_label.setFont(font(11, QFont.Black))
        self.source_label.setStyleSheet("color:rgba(70,232,255,0.92); letter-spacing:2px;")
        text_col.addStretch(1)
        text_col.addWidget(self.track)
        text_col.addWidget(self.artist_label)
        text_col.addWidget(self.album_label)
        text_col.addSpacing(4)
        text_col.addWidget(self.source_label)
        text_col.addStretch(1)
        now_lay.addLayout(text_col, 1)
        lay.addWidget(self.now_card, 1)

        self.groups_strip = GlassPanel(radius=18)
        self.groups_strip.setMaximumHeight(66)
        groups_strip_lay = QHBoxLayout(self.groups_strip)
        groups_strip_lay.setContentsMargins(14, 8, 12, 8)
        groups_strip_lay.setSpacing(8)
        groups_label = QLabel("GROUPS")
        groups_label.setFont(font(8, QFont.Black))
        groups_label.setStyleSheet("color:#49e6ff; letter-spacing:2px;")
        groups_strip_lay.addWidget(groups_label)
        self.group_buttons_layout = QHBoxLayout()
        self.group_buttons_layout.setSpacing(8)
        groups_strip_lay.addLayout(self.group_buttons_layout, 1)
        lay.addWidget(self.groups_strip, 0)
        self.rebuild_audio_group_buttons()

        self.progress = QFrame()
        self.progress.setFixedHeight(10)
        self.progress.setStyleSheet("background:rgba(160,170,190,0.22); border-radius:5px;")
        lay.addWidget(self.progress)

    def build_right(self):
        lay = QVBoxLayout(self.right)
        lay.setContentsMargins(22, 18, 22, 20)
        lay.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        self.room_title = QPushButton("Livingroom Sonos")
        self.room_title.setFont(font(24, QFont.Black))
        self.room_title.setCursor(Qt.PointingHandCursor)
        self.room_title.setToolTip("Press to choose the Audio page media player")
        self.room_title.setAccessibleName("Choose Audio media player")
        self.room_title.setStyleSheet("""
            QPushButton {
                color:#f6f8ff;
                background:transparent;
                border:0;
                padding:0;
                text-align:left;
            }
            QPushButton:pressed { color:#49e6ff; }
        """)
        self.room_title.clicked.connect(self.assign_primary_media_player)
        self.state_pill = QLabel("Idle")
        self.state_pill.setAlignment(Qt.AlignCenter)
        self.state_pill.setFont(font(9, QFont.Black))
        self.state_pill.setStyleSheet("background:rgba(70,80,96,0.65); color:#dbe3f4; border:1px solid rgba(255,255,255,0.16); border-radius:13px; padding:6px 11px;")
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self.room_title)
        title_row.addWidget(self.state_pill)
        title_row.addStretch(1)
        title_col.addLayout(title_row)
        header.addLayout(title_col, 1)

        source_col = QVBoxLayout()
        source_col.setSpacing(4)
        source_text = QLabel("Source")
        source_text.setFont(font(8, QFont.Black, 18))
        source_text.setStyleSheet("color:rgba(219,227,244,0.78); letter-spacing:2px;")
        self.source = QComboBox()
        self.source.setMinimumWidth(145)
        self.source.setMinimumHeight(48)
        self.source.setStyleSheet("QComboBox{background:rgba(22,36,52,0.88); color:#f6f8ff; border:1px solid rgba(71,224,255,0.20); border-radius:16px; padding:8px 10px; font-weight:900;} QAbstractItemView{background:#182235;color:#fff; selection-background-color:#45e5ff; selection-color:#071420;}")
        self.source.activated[str].connect(lambda text: self.media_action("source", text))
        source_col.addWidget(source_text)
        source_col.addWidget(self.source)
        header.addLayout(source_col)
        lay.addLayout(header)

        controls = QHBoxLayout()
        controls.setSpacing(14)
        self.sub = ModernAudioButton("sub", "Sub", active=False, min_h=74, holdable=True)
        self.sub.setMinimumWidth(104)
        transport = GlassPanel(radius=44)
        transport_lay = QHBoxLayout(transport)
        transport_lay.setContentsMargins(10, 8, 10, 8)
        transport_lay.setSpacing(12)
        self.prev = IconCircle("◀◀", "previous", 62)
        self.play = IconCircle("▶", "play_pause", 82, active=True)
        self.next = IconCircle("▶▶", "next", 62)
        transport_lay.addWidget(self.prev)
        transport_lay.addWidget(self.play)
        transport_lay.addWidget(self.next)
        self.sur = ModernAudioButton("surround", "Surround", active=False, min_h=74, holdable=True)
        self.sur.setMinimumWidth(124)
        self.switch_buttons = {"subwoofer": self.sub, "surround": self.sur}
        controls.addWidget(self.sub)
        controls.addWidget(transport, 1)
        controls.addWidget(self.sur)
        lay.addLayout(controls)

        vol_panel = GlassPanel(radius=22)
        vol_lay = QVBoxLayout(vol_panel)
        vol_lay.setContentsMargins(16, 12, 16, 12)
        vol_lay.setSpacing(10)
        vol_row = QHBoxLayout()
        vol_name = QLabel("Volume")
        vol_name.setFont(font(12, QFont.Black))
        vol_name.setStyleSheet("color:#dbe3f4;")
        self.vol_value = QLabel("--%")
        self.vol_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.vol_value.setFont(font(12, QFont.Black))
        self.vol_value.setStyleSheet("color:#dbe3f4;")
        vol_row.addWidget(vol_name)
        vol_row.addStretch(1)
        vol_row.addWidget(self.vol_value)
        self.volume = TouchFriendlySlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setMinimumHeight(58)
        self.volume.setStyleSheet("""
            QSlider::groove:horizontal { height:18px; border-radius:9px; background:rgba(160,170,190,0.23); }
            QSlider::sub-page:horizontal { height:18px; border-radius:9px; background:#49e6ff; }
            QSlider::add-page:horizontal { height:18px; border-radius:9px; background:rgba(110,92,180,0.42); }
            QSlider::handle:horizontal { width:46px; height:46px; margin:-14px 0; border-radius:23px; background:#f8f5ff; border:1px solid rgba(255,255,255,0.42); }
        """)
        self.tv_power = ModernAudioButton("tv", "TV Power", min_h=64, holdable=True)
        self.tv_power.setMinimumWidth(126)
        self.projector = ModernAudioButton("projector", "Projector", min_h=64, holdable=True)
        self.projector.setMinimumWidth(126)
        self.volume_down = IconCircle("−", "volume_down", 58)
        self.volume_down.setToolTip("Lower volume 2%")
        self.volume_down.setAccessibleName("Lower volume 2 percent")
        self.volume_up = IconCircle("+", "volume_up", 58)
        self.volume_up.setToolTip("Raise volume 2%")
        self.volume_up.setAccessibleName("Raise volume 2 percent")
        self.switch_buttons["tv_power"] = self.tv_power
        self.switch_buttons["projector"] = self.projector
        projector_row = QHBoxLayout()
        projector_row.setContentsMargins(0, 0, 0, 0)
        projector_row.setSpacing(12)
        projector_row.addWidget(self.volume_down)
        projector_row.addWidget(self.projector)
        projector_row.addStretch(1)
        projector_row.addWidget(self.tv_power)
        projector_row.addWidget(self.volume_up)
        vol_lay.addLayout(vol_row)
        vol_lay.addWidget(self.volume)
        vol_lay.addLayout(projector_row)
        lay.addWidget(vol_panel)

        eq = QHBoxLayout()
        eq.setSpacing(10)
        self.eq_sliders = {}
        self.eq_cards = {}
        for key, name in AUDIO_NUMBER_CONTROL_ORDER:
            card = HoldCard(hold_ms=700)
            card.setMinimumHeight(232)
            card.setMaximumHeight(246)
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            card.setToolTip(f"Hold to assign {name}")
            v = QVBoxLayout(card)
            v.setContentsMargins(12, 10, 12, 12)
            v.setSpacing(6)
            label = HoldLabel(self._audio_number_label_text(key, "--"))
            label.setAlignment(Qt.AlignCenter)
            label.setFont(font(9 if key == "music_surround" else 10, QFont.Black))
            label.setStyleSheet("color:#dbe3f4;")
            sl = TouchFriendlySlider(Qt.Vertical)
            sl.setRange(0, 100)
            sl.setValue(50)
            sl.setMinimumWidth(68)
            sl.setMinimumHeight(168)
            sl.setStyleSheet("""
                QSlider::groove:vertical { width:18px; border-radius:9px; background:rgba(160,170,190,0.23); }
                QSlider::add-page:vertical { width:18px; border-radius:9px; background:#49e6ff; }
                QSlider::sub-page:vertical { width:18px; border-radius:9px; background:rgba(110,92,180,0.42); }
                QSlider::handle:vertical { height:46px; width:46px; margin:0 -14px; border-radius:23px; background:#f8f5ff; border:1px solid rgba(255,255,255,0.42); }
            """)
            v.addWidget(label)
            v.addWidget(sl, 1, Qt.AlignHCenter)
            self.eq_sliders[key] = (sl, label)
            self.eq_cards[key] = card
            sl.valueChanged.connect(lambda value, n=key: self.on_audio_number_slider_changed(n, value))
            sl.sliderReleased.connect(lambda n=key, s=sl: self.set_number_control(n, s.value()))
            card.held.connect(lambda n=key: self.assign_audio_control(n))
            label.held.connect(lambda n=key: self.assign_audio_control(n))
            eq.addWidget(card)
        lay.addLayout(eq, 1)

        self.prev.clicked.connect(lambda: self.media_action("previous"))
        self.play.clicked.connect(lambda: self.media_action("play_pause"))
        self.next.clicked.connect(lambda: self.media_action("next"))
        self.volume_down.clicked.connect(lambda: self.adjust_volume(-2))
        self.volume_up.clicked.connect(lambda: self.adjust_volume(2))
        self.volume.valueChanged.connect(lambda value: self.on_volume_slider_changed(value))
        self.volume.sliderReleased.connect(lambda: self.media_action("volume", self.volume.value()))
        self.sub.clicked.connect(lambda: self.toggle_audio_switch("subwoofer"))
        self.sur.clicked.connect(lambda: self.toggle_audio_switch("surround"))
        self.tv_power.clicked.connect(lambda: self.toggle_audio_switch("tv_power"))
        self.projector.clicked.connect(lambda: self.toggle_audio_switch("projector"))
        self.sub.held.connect(lambda: self.assign_audio_control("subwoofer"))
        self.sur.held.connect(lambda: self.assign_audio_control("surround"))
        self.tv_power.held.connect(lambda: self.assign_audio_control("tv_power"))
        self.projector.held.connect(lambda: self.assign_audio_control("projector"))

    def configured_audio_groups(self) -> list[dict]:
        return audio_group_definitions(self.config)

    def _audio_group_signature(self) -> tuple:
        return tuple(
            (str(group.get("id") or ""), str(group.get("name") or ""), tuple(group.get("members") or []), str(group.get("coordinatorId") or ""))
            for group in self.configured_audio_groups()
        )

    def rebuild_audio_group_buttons(self):
        if not hasattr(self, "group_buttons_layout"):
            return
        signature = self._audio_group_signature()
        if signature == self._group_signature and self.group_buttons:
            self.update_audio_group_buttons()
            return
        self._group_signature = signature
        while self.group_buttons_layout.count():
            item = self.group_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.group_buttons = {}
        groups = self.configured_audio_groups()
        self.groups_strip.setVisible(bool(groups))
        for group in groups:
            group_id = str(group.get("id") or "")
            btn = RoundButton(str(group.get("name") or "Group"), min_h=42)
            btn.setMaximumHeight(46)
            btn.setMinimumWidth(110)
            btn.setToolTip("Press to group. Press again to ungroup.")
            btn.clicked.connect(lambda checked=False, g=copy.deepcopy(group): self.toggle_audio_group(g))
            self.group_buttons[group_id] = btn
            self.group_buttons_layout.addWidget(btn)
        self.group_buttons_layout.addStretch(1)
        self.update_audio_group_buttons()

    def audio_group_is_active(self, group: dict) -> bool:
        members = [str(value or "").strip() for value in group.get("members") or [] if str(value or "").strip().startswith("media_player.")]
        if len(members) < 2:
            return False
        coordinator = str(group.get("coordinatorId") or "").strip()
        candidates = []
        if coordinator:
            candidates.append(self.group_player_states.get(coordinator) or {})
        for member in members:
            state = self.group_player_states.get(member) or {}
            if state and state not in candidates:
                candidates.append(state)
        wanted = set(members)
        for state in candidates:
            group_members = state.get("groupMembers") or state.get("group_members") or []
            if isinstance(group_members, str):
                group_members = [group_members]
            if wanted.issubset({str(value) for value in group_members if value}):
                return True
        return False

    def update_audio_group_buttons(self):
        for group in self.configured_audio_groups():
            group_id = str(group.get("id") or "")
            button = self.group_buttons.get(group_id)
            if button is not None:
                button.setActive(self.audio_group_is_active(group))

    def toggle_audio_group(self, group: dict):
        members = list(group.get("members") or [])
        if len(members) < 2:
            self.requestToast.emit("Audio group needs at least two players")
            return
        group_id = str(group.get("id") or "")
        button = self.group_buttons.get(group_id)
        if button is not None:
            button.setEnabled(False)
        payload = self.s.ha_payload({
            "members": members,
            "coordinatorId": group.get("coordinatorId") or members[0],
            "action": "toggle",
        })

        def done(result):
            states = (result or {}).get("states") if isinstance(result, dict) else []
            for state in states or []:
                if isinstance(state, dict) and state.get("entityId"):
                    self.group_player_states[str(state.get("entityId"))] = state
            active = bool((result or {}).get("active")) if isinstance(result, dict) else False
            if button is not None:
                button.setEnabled(True)
                button.setActive(active)
            name = str(group.get("name") or "Audio group")
            self.requestToast.emit(f"{name}: {'grouped' if active else 'ungrouped'}")

        def failed(message):
            if button is not None:
                button.setEnabled(True)
            self.requestToast.emit(f"Group failed: {message}")

        self.run_async(
            f"audio-group-{group_id or 'toggle'}",
            lambda: self.s.api.post("/api/ha/media/group", payload),
            done,
            failed,
        )

    def player_id(self):
        ha = self.s.ha()
        return ha.get("selectedMediaPlayerId") or nested_get(ha, "mediaPlayerEntity", "entityId", default="") or ""

    def sync(self, config, thermostat):
        super().sync(config, thermostat)
        ha = self.s.ha()
        mp = self.player_id()
        selected = ha.get("mediaPlayerEntity") if isinstance(ha.get("mediaPlayerEntity"), dict) else {}
        title = str(selected.get("name") or "").strip() if selected.get("entityId") == mp else ""
        for p in ha.get("mediaPlayerEntities") or ha.get("audioAvailableEntities", {}).get("mediaPlayers", []) or []:
            if p.get("entityId") == mp:
                title = p.get("name") or title
                break
        title = title or (mp.split(".", 1)[-1].replace("_", " ").title() if mp else "Audio")
        self.room_title.setText(title)
        self.source_label.setText(title.upper())
        if not self.player_state:
            self.artist_label.setText(title)
        self.apply_audio_control_state()
        self.rebuild_audio_group_buttons()

        # Discover tone-control entities once for each selected player. The
        # returned number.* records include Home Assistant's live min/max/step,
        # so a Sonos Connect/Port exposing 0..10 automatically behaves
        # differently from a speaker exposing -10..10.
        if mp and mp != self._audio_detected_player_id:
            last_attempt = float(self._audio_detect_last_attempt.get(mp) or 0.0)
            if self._audio_detect_running_player_id != mp and time.monotonic() - last_attempt >= 30.0:
                QTimer.singleShot(0, lambda eid=mp, name=title: self.auto_detect_audio_number_controls(eid, name))

    def audio_controls(self) -> dict:
        return nested_get(self.config, "integrations", "homeAssistant", "audioControlEntities", default={}) or {}

    def audio_control_record(self, name: str) -> dict:
        controls = self.audio_controls()
        value = controls.get(name)
        if name == "music_surround" and not value:
            value = controls.get("musicSurround")
        if isinstance(value, dict):
            return value
        if value:
            entity_id = str(value)
            domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
            return {"entityId": entity_id, "name": entity_id, "domain": domain}
        return {}

    def assign_audio_control(self, name: str):
        if name in {key for key, _label in AUDIO_NUMBER_CONTROL_ORDER}:
            group = "audio-number"
        elif name == "tv_power":
            group = "audio-media-player"
        else:
            group = "audio-toggle"
        self.requestAssign.emit(group, {"audioControlKind": name}, name)

    def assign_primary_media_player(self):
        """Choose the media player used by transport, source, and volume."""
        self.requestAssign.emit(
            "audio-primary-media-player",
            {"audioControlKind": "primary_media_player"},
            "Audio Media Player",
        )

    def auto_detect_audio_number_controls(self, media_player_id: str | None = None, media_player_name: str = "", notify: bool = False):
        """Associate tone controls with the selected player and adopt HA ranges."""
        entity_id = str(media_player_id or self.player_id() or "").strip()
        if not entity_id.startswith("media_player."):
            return
        if self._audio_detect_running_player_id == entity_id:
            return

        self._audio_detect_running_player_id = entity_id
        self._audio_detect_last_attempt[entity_id] = time.monotonic()
        payload = self.s.ha_payload({
            "mediaPlayerId": entity_id,
            "mediaPlayerName": str(media_player_name or self.room_title.text() or "").strip(),
        })

        def done(result):
            self._audio_detect_running_player_id = ""
            # Ignore a result that arrived after the user selected another room.
            if self.player_id() != entity_id:
                return
            fresh = (result or {}).get("controls") if isinstance(result, dict) else None
            if not isinstance(fresh, dict):
                self._audio_detected_player_id = entity_id
                return

            target = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {}).setdefault("audioControlEntities", {})
            changed_names: list[str] = []
            cleared_names: list[str] = []
            for key, _label in AUDIO_NUMBER_CONTROL_ORDER:
                discovered = fresh.get(key)
                previous = target.get(key) if isinstance(target.get(key), dict) else {}
                if isinstance(discovered, dict) and str(discovered.get("entityId") or "").startswith("number."):
                    record = dict(discovered)
                    record["kind"] = key
                    record["autoDetected"] = True
                    record["forMediaPlayerId"] = entity_id
                    comparable_previous = {k: v for k, v in previous.items() if k not in {"state", "value"}}
                    comparable_record = {k: v for k, v in record.items() if k not in {"state", "value"}}
                    if comparable_previous != comparable_record or previous.get("value") != record.get("value"):
                        changed_names.append(key)
                    target[key] = record
                elif previous.get("autoDetected"):
                    target[key] = None
                    cleared_names.append(key)

            self._audio_detected_player_id = entity_id
            if changed_names or cleared_names:
                try:
                    self.s.save_config()
                    self.config = self.s.config
                except Exception as exc:
                    self.requestToast.emit(f"Audio control save failed: {exc}")
            self.apply_audio_control_state()
            QTimer.singleShot(100, self.poll)
            if notify:
                if changed_names:
                    labels = ", ".join(AUDIO_CONTROL_LABELS.get(k, k.replace("_", " ").title()) for k in changed_names)
                    self.requestToast.emit(f"Auto-configured {labels}")
                else:
                    self.requestToast.emit("Audio player updated")

        def failed(error):
            self._audio_detect_running_player_id = ""
            if notify:
                self.requestToast.emit(f"Tone-control detection failed: {error}")

        self.run_async(
            "audio-control-detect",
            lambda: self.s.api.post("/api/ha/audio/controls", payload),
            done,
            failed,
        )

    def _set_audio_hold_ms(self, widget, hold_ms: int):
        try:
            hold_ms = max(250, int(hold_ms))
        except Exception:
            hold_ms = self._audio_assign_short_ms
        for timer_name in ("_timer", "_hold_timer"):
            timer = getattr(widget, timer_name, None)
            if timer is not None:
                try:
                    timer.setInterval(hold_ms)
                except Exception:
                    pass

    def _audio_reassign_hold_ms(self, name: str) -> int:
        return self._audio_assign_reassign_ms if self.control_entity(name) else self._audio_assign_short_ms

    def _audio_number_local_active(self, name: str) -> dict | None:
        local = self._audio_number_local.get(name)
        if not isinstance(local, dict):
            return None
        if float(local.get("until") or 0.0) <= time.monotonic():
            self._audio_number_local.pop(name, None)
            return None
        return local

    def _hold_audio_number_local(self, name: str, slider_value: int, seconds: float = 8.0):
        try:
            slider_value = int(clamp(round(float(slider_value)), 0, 100))
        except Exception:
            slider_value = 50
        actual_value = self.slider_to_number_value(name, slider_value)
        self._audio_number_local[name] = {
            "slider": slider_value,
            "actual": actual_value,
            "until": time.monotonic() + max(0.5, float(seconds)),
        }
        return actual_value

    def _clear_audio_number_local(self, name: str):
        self._audio_number_local.pop(name, None)

    def on_audio_number_slider_changed(self, name: str, value: int):
        actual_value = self._hold_audio_number_local(name, value, 2.0 if self.eq_sliders.get(name, (None, None))[0] and self.eq_sliders[name][0].isSliderDown() else 0.75)
        sl_label = self.eq_sliders.get(name)
        if sl_label:
            _slider, label = sl_label
            label.setText(self._audio_number_label_text(name, self._number_label_value(actual_value)))

    def _audio_volume_local_active(self) -> dict | None:
        local = self._audio_volume_local if isinstance(self._audio_volume_local, dict) else {}
        if float(local.get("until") or 0.0) <= time.monotonic():
            self._audio_volume_local = {"value": None, "until": 0.0}
            return None
        return local

    def _hold_audio_volume_local(self, value: int, seconds: float = 8.0) -> int:
        try:
            pct = int(clamp(round(float(value)), 0, 100))
        except Exception:
            pct = 0
        self._audio_volume_local = {"value": pct, "until": time.monotonic() + max(0.5, float(seconds))}
        self.vol_value.setText(f"{pct}%")
        return pct

    def _clear_audio_volume_local(self):
        self._audio_volume_local = {"value": None, "until": 0.0}

    def on_volume_slider_changed(self, value: int):
        self._hold_audio_volume_local(value, 2.0 if self.volume.isSliderDown() else 0.75)

    def adjust_volume(self, delta: int):
        """Move the selected media player's volume in exact 2% increments."""
        local = self._audio_volume_local_active()
        try:
            current = int(round(float(local.get("value")))) if local else int(self.volume.value())
        except Exception:
            current = int(self.volume.value() or 0)
        target = int(clamp(current + int(delta), 0, 100))
        if target == current:
            self._hold_audio_volume_local(target, 1.0)
            return
        self.media_action("volume", target)

    def number_value_to_slider(self, record: dict, value) -> int:
        try:
            val = float(value)
        except Exception:
            return 50
        try:
            low = float(record.get("min", 0))
            high = float(record.get("max", 100))
        except Exception:
            low, high = 0.0, 100.0
        if high <= low:
            return int(clamp(round(val), 0, 100))
        return int(clamp(round(((val - low) / (high - low)) * 100), 0, 100))

    def slider_to_number_value(self, name: str, slider_value: int):
        record = self.audio_control_record(name)
        try:
            low = float(record.get("min", 0))
            high = float(record.get("max", 100))
            step = float(record.get("step", 1) or 1)
        except Exception:
            return int(slider_value)
        raw = low + (float(slider_value) / 100.0) * (high - low)
        if step > 0:
            raw = round((raw - low) / step) * step + low
        if abs(raw - round(raw)) < 0.001:
            return int(round(raw))
        return round(raw, 2)

    def _number_label_value(self, value) -> str:
        if value is None or value == "":
            return "--"
        try:
            number = float(value)
            if abs(number - round(number)) < 0.001:
                return str(int(round(number)))
            return f"{number:.1f}"
        except Exception:
            return str(value)[:12]

    def _audio_number_label_text(self, name: str, value_text: str) -> str:
        title = AUDIO_CONTROL_LABELS.get(name, str(name or "").replace("_", " ").title())
        if name == "music_surround":
            title = "Music\nSurround"
        return f"{title}\n{value_text}"

    def apply_audio_visibility(self):
        for key, card in getattr(self, "eq_cards", {}).items():
            card.setVisible(audio_control_enabled(self.config, key))
        for key, button in getattr(self, "switch_buttons", {}).items():
            button.setVisible(audio_control_enabled(self.config, key))

    def apply_audio_control_state(self):
        self.apply_audio_visibility()
        controls = self.audio_controls()
        for name, title in AUDIO_NUMBER_CONTROL_ORDER:
            sl_label = self.eq_sliders.get(name)
            if not sl_label:
                continue
            slider, label = sl_label
            record = controls.get(name) if isinstance(controls.get(name), dict) else self.audio_control_record(name)
            assigned = isinstance(record, dict) and bool(record.get("entityId"))
            hold_ms = self._audio_reassign_hold_ms(name)
            self._set_audio_hold_ms(getattr(self, "eq_cards", {}).get(name), hold_ms)
            self._set_audio_hold_ms(label, hold_ms)
            if assigned:
                local = self._audio_number_local_active(name)
                if local:
                    if not slider.isSliderDown():
                        slider.blockSignals(True)
                        slider.setValue(int(local.get("slider", slider.value())))
                        slider.blockSignals(False)
                    label.setText(self._audio_number_label_text(name, self._number_label_value(local.get("actual"))))
                    continue
                value = record.get("value", record.get("state"))
                if value is not None and not slider.isSliderDown():
                    slider.blockSignals(True)
                    slider.setValue(self.number_value_to_slider(record, value))
                    slider.blockSignals(False)
                label.setText(self._audio_number_label_text(name, self._number_label_value(value)))
                slider.setEnabled(True)
                label.setToolTip("Hold 6 seconds to reassign")
                if getattr(self, "eq_cards", {}).get(name):
                    self.eq_cards[name].setToolTip(f"Hold 6 seconds to reassign {title}")
            else:
                self._clear_audio_number_local(name)
                label.setText(self._audio_number_label_text(name, "Hold"))
                slider.setEnabled(True)
                label.setToolTip("Hold to assign")
                if getattr(self, "eq_cards", {}).get(name):
                    self.eq_cards[name].setToolTip(f"Hold to assign {title}")
        for name, button in getattr(self, "switch_buttons", {}).items():
            record = controls.get(name) if isinstance(controls.get(name), dict) else self.audio_control_record(name)
            assigned = isinstance(record, dict) and bool(record.get("entityId"))
            self._set_audio_hold_ms(button, self._audio_reassign_hold_ms(name))
            state = str(record.get("state") or "").lower() if isinstance(record, dict) else ""
            domain = str(record.get("domain") or "").lower() if isinstance(record, dict) else ""
            if name == "tv_power" and domain == "media_player":
                on = bool(state) and state not in {"off", "standby", "unavailable", "unknown"}
            else:
                on = state in {"on", "open", "true", "1"}
            button.setActive(on)
            button.setToolTip("Hold 6 seconds to reassign" if assigned else "Hold to assign")
            # The highlight is the on/off indicator. Keep these tiles clean with no
            # ON/OFF text under Sub, Surround, or Projector.
            if hasattr(button, "setStatus"):
                button.setStatus("")

    def media_action(self, action: str, value=None):
        eid = self.player_id()
        if not eid:
            self.requestToast.emit("No media player selected")
            return
        send_value = value
        if action == "volume" and value is not None:
            try:
                raw = float(value)
                pct = int(clamp(round(raw * 100 if raw <= 1 else raw), 0, 100))
                send_value = pct
                self._hold_audio_volume_local(pct, 8.0)
                self.volume.blockSignals(True)
                self.volume.setValue(pct)
                self.volume.blockSignals(False)
                self.vol_value.setText(f"{pct}%")
            except Exception:
                pass
        if action == "source" and not str(value or "").strip():
            return
        payload = self.s.ha_payload({"entityId": eid, "action": action, "value": send_value})

        def done(result):
            if isinstance(result, dict):
                self.player_state = result.get("state") or self.player_state
                self.apply_player_state()
                QTimer.singleShot(700, self.poll)

        def failed(err):
            if action == "volume":
                self._clear_audio_volume_local()
                self.apply_player_state()
            self.requestToast.emit(f"Media failed: {err}")

        self.run_async(
            "audio-media",
            lambda: self.s.api.post("/api/ha/media/action", payload),
            done,
            failed,
        )

    def control_entity(self, name: str):
        return self.audio_control_record(name).get("entityId") or ""

    def _audio_preset_definition(self, preset: str) -> dict:
        return audio_preset_definition(self.config, preset)

    def _round_audio_number_value(self, name: str, target):
        record = self.audio_control_record(name)
        try:
            low = float(record.get("min", 0))
            high = float(record.get("max", 100))
            step = float(record.get("step", 1) or 1)
        except Exception:
            low, high, step = 0.0, 100.0, 1.0
        if high <= low:
            high = low + 100.0

        if isinstance(target, str):
            key = target.strip().lower()
            if key == "max":
                raw = high
            elif key == "min":
                raw = low
            else:
                raw = float(key)
        else:
            raw = float(target)

        raw = float(clamp(raw, low, high))
        if step > 0:
            raw = round((raw - low) / step) * step + low
            raw = float(clamp(raw, low, high))
        if abs(raw - round(raw)) < 0.001:
            return int(round(raw))
        return round(raw, 2)

    def _remember_number_state(self, name: str, value):
        controls = self.config.setdefault("integrations", {}).setdefault("homeAssistant", {}).setdefault("audioControlEntities", {})
        previous = controls.get(name) if isinstance(controls.get(name), dict) else self.audio_control_record(name)
        updated = dict(previous or {})
        updated["kind"] = name
        updated["value"] = value
        updated["state"] = str(value)
        controls[name] = updated

    def _remember_switch_state(self, name: str, action: str):
        controls = self.config.setdefault("integrations", {}).setdefault("homeAssistant", {}).setdefault("audioControlEntities", {})
        previous = controls.get(name) if isinstance(controls.get(name), dict) else self.audio_control_record(name)
        updated = dict(previous or {})
        updated["state"] = "on" if str(action).lower() == "on" else "off"
        controls[name] = updated

    def apply_audio_preset(self, preset: str):
        definition = self._audio_preset_definition(preset)
        label = definition.get("label") or "Audio preset"
        if not definition:
            self.requestToast.emit("Unknown audio preset")
            return
        media_player_id = self.player_id()
        missing: list[str] = []
        number_actions: list[tuple[str, str, int | float]] = []
        switch_actions: list[tuple[str, str, str]] = []
        volume_value = definition.get("volume")

        if volume_value is not None:
            if not media_player_id:
                missing.append("media player")
            else:
                try:
                    pct = int(clamp(round(float(volume_value)), 0, 100))
                    self._hold_audio_volume_local(pct, 8.0)
                    self.volume.blockSignals(True)
                    self.volume.setValue(pct)
                    self.volume.blockSignals(False)
                    self.vol_value.setText(f"{pct}%")
                    volume_value = pct
                except Exception:
                    volume_value = None

        for name, target in (definition.get("numbers") or {}).items():
            if not audio_control_enabled(self.config, name):
                continue
            entity_id = self.control_entity(name)
            if not entity_id:
                missing.append(AUDIO_CONTROL_LABELS.get(name, name))
                continue
            try:
                value = self._round_audio_number_value(name, target)
            except Exception:
                missing.append(AUDIO_CONTROL_LABELS.get(name, name))
                continue
            self._remember_number_state(name, value)
            self._hold_audio_number_local(name, self.number_value_to_slider(self.audio_control_record(name), value), 8.0)
            number_actions.append((name, entity_id, value))

        for name, action in (definition.get("switches") or {}).items():
            if not audio_control_enabled(self.config, name):
                continue
            entity_id = self.control_entity(name)
            if not entity_id:
                missing.append(name.replace("subwoofer", "sub"))
                continue
            action = "on" if str(action).lower() == "on" else "off"
            self._remember_switch_state(name, action)
            switch_actions.append((name, entity_id, action))

        self.apply_audio_control_state()
        if not any([volume_value is not None and media_player_id, number_actions, switch_actions]):
            self.requestToast.emit(f"{label}: no assigned controls")
            return

        def worker():
            result: dict[str, Any] = {"numbers": {}, "switches": {}, "missing": missing}
            if volume_value is not None and media_player_id:
                media = self.s.api.post(
                    "/api/ha/media/action",
                    self.s.ha_payload({"entityId": media_player_id, "action": "volume", "value": volume_value}),
                )
                result["state"] = media.get("state") if isinstance(media, dict) else None
            for name, entity_id, value in number_actions:
                resp = self.s.api.post(
                    "/api/ha/audio/control/action",
                    self.s.ha_payload({"entityId": entity_id, "value": value}),
                )
                control = resp.get("control") if isinstance(resp, dict) else None
                if isinstance(control, dict):
                    result["numbers"][name] = control
            for name, entity_id, action in switch_actions:
                record = self.audio_control_record(name)
                domain = str(record.get("domain") or (entity_id.split(".", 1)[0] if "." in entity_id else "")).strip().lower()
                if name == "tv_power" and domain == "media_player":
                    resp = self.s.api.post(
                        "/api/ha/room/action",
                        self.s.ha_payload({"entityId": entity_id, "action": action}),
                    )
                else:
                    resp = self.s.api.post(
                        "/api/ha/audio/switch/action",
                        self.s.ha_payload({"entityId": entity_id, "action": action}),
                    )
                control = resp.get("control") if isinstance(resp, dict) else None
                if isinstance(control, dict):
                    result["switches"][name] = control
            return result

        def done(result):
            if isinstance(result, dict):
                if isinstance(result.get("state"), dict):
                    self.player_state = result.get("state") or self.player_state
                    self.apply_player_state()
                controls = self.config.setdefault("integrations", {}).setdefault("homeAssistant", {}).setdefault("audioControlEntities", {})
                for name, control in (result.get("numbers") or {}).items():
                    previous = controls.get(name) if isinstance(controls.get(name), dict) else {}
                    updated = dict(previous)
                    updated.update(control)
                    updated["kind"] = name
                    controls[name] = updated
                for name, control in (result.get("switches") or {}).items():
                    previous = controls.get(name) if isinstance(controls.get(name), dict) else {}
                    updated = dict(previous)
                    updated.update(control)
                    controls[name] = updated
                self.apply_audio_control_state()
            skipped = ", ".join(dict.fromkeys(missing))
            if skipped:
                self.requestToast.emit(f"{label} skipped unassigned controls: {skipped}")
            QTimer.singleShot(650, self.poll)

        self.run_async(
            "audio-preset",
            worker,
            done,
            lambda err, label=label: self.requestToast.emit(f"{label} failed: {err}"),
        )

    def set_number_control(self, name: str, value: int):
        eid = self.control_entity(name)
        if not eid:
            self._clear_audio_number_local(name)
            self.requestToast.emit(f"No {name} control assigned")
            return
        actual_value = self._hold_audio_number_local(name, value, 8.0)
        sl_label = self.eq_sliders.get(name)
        if sl_label:
            _, label = sl_label
            label.setText(self._audio_number_label_text(name, self._number_label_value(actual_value)))
        payload = self.s.ha_payload({"entityId": eid, "value": actual_value})
        def done(result):
            control = (result or {}).get("control") if isinstance(result, dict) else None
            if isinstance(control, dict):
                controls = self.config.setdefault("integrations", {}).setdefault("homeAssistant", {}).setdefault("audioControlEntities", {})
                previous = controls.get(name) if isinstance(controls.get(name), dict) else {}
                updated = dict(previous)
                updated.update(control)
                updated["kind"] = name
                controls[name] = updated
                self.apply_audio_control_state()
                QTimer.singleShot(850, self.poll)

        def failed(err, n=name):
            self._clear_audio_number_local(n)
            self.apply_audio_control_state()
            self.requestToast.emit(f"{n} failed: {err}")

        self.run_async(
            "audio-number",
            lambda: self.s.api.post("/api/ha/audio/control/action", payload),
            done,
            failed,
        )

    def toggle_audio_switch(self, name: str):
        eid = self.control_entity(name)
        title = AUDIO_CONTROL_LABELS.get(name, str(name or "").replace("_", " ").title())
        if not eid:
            self.requestToast.emit(f"No {title} entity assigned")
            return

        record = self.audio_control_record(name)
        domain = str(record.get("domain") or (eid.split(".", 1)[0] if "." in eid else "")).strip().lower()

        def done(result):
            control = (result or {}).get("control") if isinstance(result, dict) else None
            if isinstance(control, dict):
                controls = self.config.setdefault("integrations", {}).setdefault("homeAssistant", {}).setdefault("audioControlEntities", {})
                previous = controls.get(name) if isinstance(controls.get(name), dict) else {}
                updated = dict(previous)
                updated.update(control)
                updated.setdefault("domain", domain)
                controls[name] = updated
                self.apply_audio_control_state()
                QTimer.singleShot(600, self.poll)

        if name == "tv_power" and domain == "media_player":
            state = str(record.get("state") or "").strip().lower()
            action = "on" if state in {"", "off", "standby", "unavailable", "unknown"} else "off"
            self._remember_switch_state(name, action)
            self.apply_audio_control_state()
            payload = self.s.ha_payload({"entityId": eid, "action": action})
            self.run_async(
                "audio-tv-power",
                lambda: self.s.api.post("/api/ha/room/action", payload),
                done,
                lambda err, n=title: self.requestToast.emit(f"{n} failed: {err}"),
            )
            return

        payload = self.s.ha_payload({"entityId": eid, "action": "toggle"})
        self.run_async(
            "audio-switch",
            lambda: self.s.api.post("/api/ha/audio/switch/action", payload),
            done,
            lambda err, n=title: self.requestToast.emit(f"{n} failed: {err}"),
        )

    def _compact_track_title(self, text: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return "Nothing\nPlaying"
        clean = clean.replace(" - ", "\n")
        if len(clean) > 54:
            clean = clean[:53].rstrip() + "…"
        return clean

    def _set_source_choices(self, st: dict):
        current = str(st.get("source") or "")
        choices = st.get("sourceList") or st.get("source_list") or []
        if isinstance(choices, str):
            choices = [choices] if choices.strip() else []
        if current and current not in choices:
            choices = [current] + list(choices)
        self.source.blockSignals(True)
        self.source.clear()
        if choices:
            self.source.addItems([str(x) for x in choices])
            if current:
                idx = self.source.findText(current)
                if idx >= 0:
                    self.source.setCurrentIndex(idx)
        else:
            self.source.addItem(current or "TV")
        self.source.blockSignals(False)

    def _fetch_cover_bytes(self, url: str) -> bytes:
        if url in self._cover_cache:
            return self._cover_cache[url]
        req = urlrequest.Request(
            url,
            headers={
                "User-Agent": "SmartThermostatPanel/0.1",
                "Accept": "image/*,*/*;q=0.8",
                "Connection": "close",
            },
        )
        with urlrequest.urlopen(req, timeout=5) as resp:
            payload = resp.read(6 * 1024 * 1024)
        self._cover_cache[url] = payload
        # Keep the cache tiny. HA artwork URLs usually change with the track.
        if len(self._cover_cache) > 8:
            for key in list(self._cover_cache.keys())[:-8]:
                self._cover_cache.pop(key, None)
        return payload

    def update_cover_art(self, st: dict):
        url = str(st.get("pictureUrl") or st.get("picture_url") or st.get("entityPicture") or st.get("entity_picture") or "").strip()
        title = str(st.get("mediaTitle") or st.get("media_title") or "").strip()
        initials = "NP"
        if title:
            words = [w for w in title.replace("-", " ").split() if w]
            if words:
                initials = "".join(w[0].upper() for w in words[:2])[:2]
        if not url:
            self._cover_url = ""
            self._cover_loading_url = ""
            self.art.show_fallback(initials)
            return
        if url == self._cover_url:
            return
        self._cover_url = url
        if url in self._cover_cache:
            if not self.art.set_cover_bytes(self._cover_cache[url]):
                self.art.show_fallback(initials)
            return
        if url == self._cover_loading_url:
            return
        self._cover_loading_url = url

        def done(payload):
            if url != self._cover_url:
                return
            self._cover_loading_url = ""
            if not self.art.set_cover_bytes(payload or b""):
                self.art.show_fallback(initials)

        def failed(_err):
            if url == self._cover_url:
                self._cover_loading_url = ""
                self.art.show_fallback(initials)

        self.run_async("audio-art", lambda: self._fetch_cover_bytes(url), done, failed)

    def apply_player_state(self):
        st = self.player_state or {}
        state_raw = str(st.get("state") or "idle").lower()
        self.state_pill.setText(state_raw.capitalize())
        is_playing = state_raw == "playing"
        self.play.active = is_playing
        self.play.text = "Ⅱ" if is_playing else "▶"
        self.play.update()

        media_title = st.get("mediaTitle") or st.get("media_title") or ""
        title_text = self._compact_track_title(media_title or "Nothing Playing")
        self.track.setText(title_text)
        artist = str(st.get("mediaArtist") or st.get("media_artist") or "").strip()
        album = str(st.get("mediaAlbum") or st.get("media_album") or "").strip()
        source_name = str(st.get("name") or self.room_title.text() or "Livingroom Sonos")
        self.artist_label.setText(artist or source_name)
        self.album_label.setText(album)
        self.album_label.setVisible(bool(album))
        self.source_label.setText(source_name.upper())
        self._set_source_choices(st)
        self.update_cover_art(st)

        vol = st.get("volumeLevel")
        if vol is None:
            vol = st.get("volume_level")
        local_volume = self._audio_volume_local_active()
        if local_volume:
            try:
                pct = int(clamp(round(float(local_volume.get("value"))), 0, 100))
            except Exception:
                pct = self.volume.value() or 0
            if not self.volume.isSliderDown():
                self.volume.blockSignals(True)
                self.volume.setValue(pct)
                self.volume.blockSignals(False)
            self.vol_value.setText(f"{pct}%")
            return
        try:
            pct = int(clamp(round(float(vol) * 100), 0, 100))
        except Exception:
            pct = self.volume.value() or 0
        if not self.volume.isSliderDown():
            self.volume.blockSignals(True)
            self.volume.setValue(pct)
            self.volume.blockSignals(False)
        self.vol_value.setText(f"{pct}%")

    def current_audio_source(self) -> str:
        st = self.player_state or {}
        return str(st.get("source") or st.get("sourceName") or st.get("source_name") or "").strip()

    def state_is_tv_audio(self, st: dict | None = None) -> bool:
        st = st if isinstance(st, dict) else (self.player_state or {})
        source = str(st.get("source") or st.get("sourceName") or st.get("source_name") or "").strip().lower()
        title = str(st.get("mediaTitle") or st.get("media_title") or "").strip().lower()
        content_id = str(st.get("mediaContentId") or st.get("media_content_id") or "").strip().lower()
        content_type = str(st.get("mediaContentType") or st.get("media_content_type") or "").strip().lower()
        tv_markers = ("x-sonos-htastream", ":spdif", "spdif", "hdmi", "television")
        if source in {"tv", "television"} or source.startswith("tv ") or source.endswith(" tv"):
            return True
        if title == "tv":
            return True
        if any(marker in content_id for marker in tv_markers):
            return True
        if content_type in {"tv", "television"}:
            return True
        return False

    def state_is_non_tv_audio_playing(self, st: dict | None = None) -> bool:
        st = st if isinstance(st, dict) else (self.player_state or {})
        state_raw = str(st.get("state") or "").strip().lower()
        if state_raw != "playing":
            return False
        return not self.state_is_tv_audio(st)

    def is_non_tv_audio_playing(self) -> bool:
        return self.state_is_non_tv_audio_playing(self.player_state or {})

    def poll_player_only(self):
        eid = self.player_id()
        groups = self.configured_audio_groups()
        entity_ids: list[str] = []
        if eid:
            entity_ids.append(eid)
        for group in groups:
            coordinator = str(group.get("coordinatorId") or "").strip()
            if coordinator.startswith("media_player.") and coordinator not in entity_ids:
                entity_ids.append(coordinator)
        if not entity_ids:
            return
        payload = self.s.ha_payload({"entityIds": entity_ids})

        def done(result):
            players = (result or {}).get("players") or []
            by_id = {str(player.get("entityId")): player for player in players if isinstance(player, dict) and player.get("entityId")}
            self.group_player_states.update(by_id)
            if eid and eid in by_id:
                self.player_state = by_id[eid]
                self.apply_player_state()
            self.update_audio_group_buttons()

        self.run_async("audio-poll", lambda: self.s.api.post("/api/ha/media/states", payload), done, None)

    def poll(self):
        self.poll_player_only()
        controls = self.audio_controls()
        if any(self.audio_control_record(k).get("entityId") for k, _label in AUDIO_NUMBER_CONTROL_ORDER):
            def number_done(result):
                fresh = (result or {}).get("controls") if isinstance(result, dict) else None
                if isinstance(fresh, dict):
                    target = self.config.setdefault("integrations", {}).setdefault("homeAssistant", {}).setdefault("audioControlEntities", {})
                    for key, value in fresh.items():
                        if isinstance(value, dict):
                            previous = target.get(key) if isinstance(target.get(key), dict) else {}
                            merged = dict(previous)
                            merged.update(value)
                            target[key] = merged
                    self.apply_audio_control_state()
            self.run_async("audio-number-poll", lambda: self.s.api.post("/api/ha/audio/control_states", self.s.ha_payload({"controls": controls})), number_done, None)
        if any(self.audio_control_record(k).get("entityId") for k, _label in AUDIO_SWITCH_CONTROL_ORDER):
            def switch_done(result):
                fresh = (result or {}).get("controls") if isinstance(result, dict) else None
                if isinstance(fresh, dict):
                    target = self.config.setdefault("integrations", {}).setdefault("homeAssistant", {}).setdefault("audioControlEntities", {})
                    for key, value in fresh.items():
                        if isinstance(value, dict):
                            previous = target.get(key) if isinstance(target.get(key), dict) else {}
                            merged = dict(previous)
                            merged.update(value)
                            target[key] = merged
                    self.apply_audio_control_state()
            self.run_async("audio-switch-poll", lambda: self.s.api.post("/api/ha/audio/switch_states", self.s.ha_payload({"controls": controls})), switch_done, None)

        tv_power = self.audio_control_record("tv_power")
        tv_power_entity = str(tv_power.get("entityId") or "").strip()
        if tv_power_entity.startswith("media_player."):
            def tv_power_done(result):
                players = (result or {}).get("players") if isinstance(result, dict) else None
                if isinstance(players, list) and players:
                    player = players[0] if isinstance(players[0], dict) else {}
                    target = self.config.setdefault("integrations", {}).setdefault("homeAssistant", {}).setdefault("audioControlEntities", {})
                    previous = target.get("tv_power") if isinstance(target.get("tv_power"), dict) else {}
                    merged = dict(previous)
                    merged.update(player)
                    merged["domain"] = "media_player"
                    target["tv_power"] = merged
                    self.apply_audio_control_state()
            self.run_async("audio-tv-power-poll", lambda: self.s.api.post("/api/ha/media/states", self.s.ha_payload({"entityIds": [tv_power_entity]})), tv_power_done, None)


class CodeKeypadDialog(QDialog):
    def __init__(self, title: str, subtitle: str = "Enter Code", verify_code: str | None = None, parent=None):
        super().__init__(parent)
        self.verify_code = None if verify_code is None else str(verify_code)
        self.code_buffer = ""
        self.result_code = ""
        self.title_text = title
        self.subtitle_text = subtitle
        self.setModal(True)
        self.setWindowTitle(title)
        self.setFixedSize(430, 505)
        self.setStyleSheet("""
            QDialog {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #070d18,
                    stop:0.55 #101a32,
                    stop:1 #150b1f);
                color:#f7fbff;
            }
            QLabel {
                color:#f7fbff;
                font-family:Arial;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(12)

        self.title = QLabel("")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setTextFormat(Qt.RichText)
        root.addWidget(self.title)

        self.code_display = QLabel("")
        self.code_display.setAlignment(Qt.AlignCenter)
        self.code_display.setFont(font(30, QFont.Black))
        root.addWidget(self.code_display)

        keypad = QGridLayout()
        keypad.setHorizontalSpacing(10)
        keypad.setVerticalSpacing(10)
        keys = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
            ("⌫", 3, 0), ("0", 3, 1), ("Cancel", 3, 2),
        ]
        for label, row, col in keys:
            b = KeypadButton(label, active=(label not in {"⌫", "Cancel"}), min_h=64)
            if label == "Cancel":
                b.setKind("danger")
                b.clicked.connect(self.reject)
            elif label == "⌫":
                b.pressed.connect(self.backspace_code)
            else:
                b.pressed.connect(lambda d=label: self.add_code_digit(d))
            keypad.addWidget(b, row, col)
        root.addLayout(keypad, 1)
        self.render_normal()

    def render_normal(self):
        self.title.setText(
            f"<span style='color:#55f0ff; letter-spacing:3px; font-size:12px; font-weight:900'>{self.title_text.upper()}</span>"
            f"<br><span style='font-size:30px; font-weight:1000; color:#ffffff'>{self.subtitle_text}</span>"
        )
        self.code_display.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,255,255,0.07);
                border:1px solid rgba(85,240,255,0.45);
                border-radius:24px;
                padding:12px;
                letter-spacing:9px;
            }
        """)
        self.update_code_display()

    def update_code_display(self):
        entered = "•" * len(self.code_buffer)
        remaining = "·" * max(0, 4 - len(self.code_buffer))
        next_text = entered + remaining
        if self.code_display.text() != next_text:
            self.code_display.setText(next_text)
            self.code_display.update()

    def add_code_digit(self, digit: str):
        if len(self.code_buffer) >= 4:
            return
        self.code_buffer += digit
        self.update_code_display()
        if len(self.code_buffer) == 4:
            QTimer.singleShot(120, self.accept_or_validate)

    def backspace_code(self):
        self.code_buffer = self.code_buffer[:-1]
        self.update_code_display()

    def accept_or_validate(self):
        if self.verify_code is not None and self.code_buffer != self.verify_code:
            self.invalid_code()
            return
        self.result_code = self.code_buffer
        self.accept()

    def invalid_code(self):
        self.code_buffer = ""
        self.title.setText(
            "<span style='color:#ff4c78; letter-spacing:3px; font-size:12px; font-weight:900'>INVALID CODE</span>"
            "<br><span style='font-size:30px; font-weight:1000; color:#ffffff'>Try Again</span>"
        )
        self.code_display.setText("••••")
        self.code_display.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,54,91,0.18);
                border:1px solid rgba(255,74,111,0.78);
                border-radius:24px;
                padding:12px;
                letter-spacing:9px;
            }
        """)
        QTimer.singleShot(750, self.render_normal)

    @staticmethod
    def get_code(parent, title: str, subtitle: str = "Enter Code", verify_code: str | None = None) -> str | None:
        dlg = CodeKeypadDialog(title, subtitle, verify_code, parent)
        if dlg.exec_() == QDialog.Accepted:
            return dlg.result_code
        return None



class PeopleSelectionDialog(QDialog):
    saved = pyqtSignal(list)
    loadCompleted = pyqtSignal(object)


    def __init__(self, state: AppState, selected_people: list[dict] | None = None, parent=None, *, title: str = "Auto Away / Home", note: str | None = None):
        super().__init__(parent)
        self.s = state
        self.selected_people = copy.deepcopy(selected_people or [])
        self.dialog_title = str(title or "People")
        self.note_text = str(note or "Select the Home Assistant person entries to use for this feature.")
        self.available_people: list[dict] = []
        self.buttons: dict[str, RoundButton] = {}
        self._people_loading = False
        self.setModal(True)
        self.setWindowTitle(self.dialog_title)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        self.loadCompleted.connect(self._handle_people_loaded)
        self._seed_selected_people()
        self.build()
        QTimer.singleShot(0, self.load_people)
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    def _refresh_config_save_state(self):
        busy = bool(self._config_save_inflight or self._config_save_pending)
        if hasattr(self, "close_btn"):
            self.close_btn.setEnabled(not busy)
            self.close_btn.setText("Saving…" if busy else "Close")

    def queue_config_save(self):
        """Persist room settings without blocking Qt touch handling.

        Multiple quick edits are coalesced. If another edit lands while a save
        is in flight, the latest full config snapshot is written immediately
        after the first request completes, avoiding out-of-order saves.
        """
        self._config_save_pending = True
        self._refresh_config_save_state()
        if self._config_save_inflight:
            return
        self._start_config_save()

    def _start_config_save(self):
        if self._config_save_inflight or not self._config_save_pending:
            return
        self._config_save_pending = False
        self._config_save_inflight = True
        self._refresh_config_save_state()
        snapshot = copy.deepcopy(self.s.config)

        def worker():
            try:
                result = self.s.api.save_config(snapshot)
                payload = {"result": result, "error": None}
            except Exception as exc:
                payload = {"result": None, "error": str(exc)}
            try:
                self.configSaveCompleted.emit(payload)
            except RuntimeError:
                pass

        threading.Thread(target=worker, name="room-settings-save", daemon=True).start()

    def _handle_config_save_completed(self, info: object):
        self._config_save_inflight = False
        data = info if isinstance(info, dict) else {}
        error = str(data.get("error") or "")
        if error:
            self._config_save_pending = False
            self._refresh_config_save_state()
            QMessageBox.warning(self, "Save failed", error)
            return
        self.saved.emit()
        if self._config_save_pending:
            self._start_config_save()
        else:
            self._refresh_config_save_state()



    def _seed_selected_people(self):
        by_id: dict[str, dict] = {}
        for person in self.selected_people:
            if not isinstance(person, dict):
                continue
            entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
            if not entity_id:
                continue
            by_id[entity_id] = {
                "entityId": entity_id,
                "name": str(person.get("name") or person.get("friendly_name") or entity_id),
                "state": str(person.get("state") or ""),
            }
        self.available_people = sorted(
            by_id.values(),
            key=lambda item: str(item.get("name") or item.get("entityId") or "").lower(),
        )

    def _handle_people_loaded(self, info: object):
        self._people_loading = False
        data = info if isinstance(info, dict) else {}
        by_id: dict[str, dict] = {}
        for person in self.selected_people:
            if not isinstance(person, dict):
                continue
            entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
            if entity_id:
                by_id[entity_id] = {
                    "entityId": entity_id,
                    "name": str(person.get("name") or person.get("friendly_name") or entity_id),
                    "state": str(person.get("state") or ""),
                }
        for person in data.get("entities") or []:
            if not isinstance(person, dict):
                continue
            entity_id = str(person.get("entityId") or person.get("entity_id") or "").strip()
            if entity_id:
                by_id[entity_id] = {
                    "entityId": entity_id,
                    "name": str(person.get("name") or person.get("friendly_name") or entity_id),
                    "state": str(person.get("state") or ""),
                }
        self.available_people = sorted(
            by_id.values(),
            key=lambda item: str(item.get("name") or item.get("entityId") or "").lower(),
        )
        self.refresh()

    def load_people(self):
        if self._people_loading:
            return
        self._people_loading = True
        self.refresh()
        payload = self.s.ha_payload({"domains": ["person"]})

        def worker():
            try:
                data = self.s.api.post("/api/ha/entities", payload)
                result = {"entities": data.get("entities") or [], "error": None}
            except Exception as exc:
                result = {"entities": [], "error": str(exc)}
            try:
                self.loadCompleted.emit(result)
            except RuntimeError:
                pass

        threading.Thread(target=worker, name="people-picker-load", daemon=True).start()

    def selected_ids(self) -> set[str]:
        return {str(p.get("entityId") or p.get("entity_id") or "").strip() for p in self.selected_people if isinstance(p, dict)}

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel(self.dialog_title.upper())
        title.setFont(font(22, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        select_all = RoundButton("Select All", active=True, min_h=40)
        clear = RoundButton("Clear", active=False, kind="danger", min_h=40)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(select_all)
        header.addWidget(clear)
        root.addLayout(header)

        note = QLabel(self.note_text)
        note.setWordWrap(True)
        note.setFont(font(10, QFont.Black))
        note.setStyleSheet("color:#cdd8ee; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.10); border-radius:12px; padding:8px;")
        root.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        body = QWidget()
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(8)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        cancel = RoundButton("Cancel", active=False, min_h=42)
        save = RoundButton("Save Selected", active=True, min_h=42)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)

        select_all.clicked.connect(self.select_all)
        clear.clicked.connect(self.clear_all)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save)
        self.refresh()

    def refresh(self):
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.buttons = {}
        if not self.available_people:
            none = QLabel("Loading Home Assistant people…" if self._people_loading else "No Home Assistant person entities found.")
            none.setWordWrap(True)
            none.setStyleSheet("color:#c4d0e5; background:rgba(255,255,255,0.05); border-radius:12px; padding:12px;")
            self.body_lay.addWidget(none)
            return
        selected = self.selected_ids()
        for person in self.available_people:
            eid = str(person.get("entityId") or "")
            name = str(person.get("name") or eid)
            state = str(person.get("state") or "")
            b = RoundButton(("✓  " if eid in selected else "○  ") + name + (f"  ({state})" if state else ""), active=eid in selected, min_h=52)
            b.clicked.connect(lambda checked=False, p=person: self.toggle_person(p))
            self.buttons[eid] = b
            self.body_lay.addWidget(b)
        self.body_lay.addStretch(1)

    def toggle_person(self, person: dict):
        eid = str(person.get("entityId") or "").strip()
        if not eid:
            return
        if eid in self.selected_ids():
            self.selected_people = [p for p in self.selected_people if str(p.get("entityId") or "") != eid]
        else:
            self.selected_people.append({"entityId": eid, "name": str(person.get("name") or eid), "state": str(person.get("state") or "")})
        self.refresh()

    def select_all(self):
        self.selected_people = [{"entityId": str(p.get("entityId") or ""), "name": str(p.get("name") or p.get("entityId") or ""), "state": str(p.get("state") or "")} for p in self.available_people if str(p.get("entityId") or "")]
        self.refresh()

    def clear_all(self):
        self.selected_people = []
        self.refresh()

    def save(self):
        self.saved.emit(self.selected_people)
        self.accept()



class AlexaLockoutSelectionDialog(QDialog):
    """Multi-select Home Assistant switches whose name contains 'Alexa'."""

    saved = pyqtSignal(list)
    loadCompleted = pyqtSignal(object)

    def __init__(self, state: AppState, selected_entities: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.s = state
        self.selected_entities = copy.deepcopy(selected_entities or [])
        self.available_entities: list[dict] = []
        self.buttons: dict[str, RoundButton] = {}
        self._loading = False
        self.setModal(True)
        self.setWindowTitle("Alexa Lockout")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        self.loadCompleted.connect(self._handle_loaded)
        self.build()
        QTimer.singleShot(0, self.load_entities)
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    @staticmethod
    def _normalize_switch(item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
        if not entity_id.startswith("switch."):
            return None
        friendly_name = str(item.get("name") or item.get("friendly_name") or entity_id).strip() or entity_id
        # Be deliberately broad: the user asked for every switch whose entity ID
        # OR friendly name contains "Alexa" anywhere, case-insensitively.
        if "alexa" not in entity_id.lower() and "alexa" not in friendly_name.lower():
            return None
        # UniFi's client-access switch often has the generic friendly name
        # "Blocked". In that case the entity ID carries the useful room name,
        # e.g. switch.office_alexa_blocked -> "Office Alexa Blocked".
        display_name = friendly_name
        if "alexa" not in friendly_name.lower() and "alexa" in entity_id.lower():
            stem = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
            display_name = " ".join(part.capitalize() for part in stem.split("_") if part) or friendly_name
        return {
            "entityId": entity_id,
            "name": display_name,
            "controlName": friendly_name,
            "state": str(item.get("state") or ""),
        }

    @staticmethod
    def _normalize_selection(item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
        if not entity_id.startswith("switch."):
            return None
        name = str(item.get("name") or item.get("friendly_name") or item.get("controlName") or entity_id).strip() or entity_id
        return {
            "entityId": entity_id,
            "name": name,
            "controlName": str(item.get("controlName") or item.get("control_name") or name).strip() or name,
            "state": str(item.get("state") or ""),
        }

    def selected_ids(self) -> set[str]:
        return {
            str(item.get("entityId") or "").strip()
            for item in (self._normalize_selection(raw) for raw in self.selected_entities)
            if item and str(item.get("entityId") or "").strip()
        }

    def load_entities(self):
        if self._loading:
            return
        self._loading = True
        self.refresh()
        payload = self.s.ha_payload({"domains": ["switch"]})

        def worker():
            try:
                data = self.s.api.post("/api/ha/entities", payload)
                result = {"entities": data.get("entities") or [], "error": None}
            except Exception as exc:
                result = {"entities": [], "error": str(exc)}
            try:
                self.loadCompleted.emit(result)
            except RuntimeError:
                pass

        threading.Thread(target=worker, name="alexa-lockout-picker-load", daemon=True).start()

    def _handle_loaded(self, info: object):
        self._loading = False
        data = info if isinstance(info, dict) else {}
        by_id: dict[str, dict] = {}
        for raw in data.get("entities") or []:
            item = self._normalize_switch(raw)
            if not item:
                continue
            by_id[item["entityId"]] = item
        self.available_entities = sorted(
            by_id.values(),
            key=lambda item: (str(item.get("name") or "").lower(), str(item.get("entityId") or "").lower()),
        )

        # Keep any saved selection that is still a valid switch. If it is in the
        # freshly loaded list, refresh its friendly name/state from Home Assistant.
        available_by_id = {item["entityId"]: item for item in self.available_entities}
        migrated: list[dict] = []
        seen: set[str] = set()
        for raw in self.selected_entities:
            item = self._normalize_selection(raw)
            if not item:
                continue
            entity_id = item["entityId"]
            if entity_id in seen:
                continue
            seen.add(entity_id)
            if entity_id in available_by_id:
                item = copy.deepcopy(available_by_id[entity_id])
            migrated.append(item)
        self.selected_entities = migrated
        self.refresh()

        error = str(data.get("error") or "").strip()
        if error:
            QMessageBox.warning(self, "Alexa Lockout", f"Could not load Alexa switches from Home Assistant:\n{error}")

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("ALEXA LOCKOUT")
        title.setFont(font(22, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        select_all = RoundButton("Select All", active=True, min_h=40)
        clear = RoundButton("Clear", active=False, kind="danger", min_h=40)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(select_all)
        header.addWidget(clear)
        root.addLayout(header)

        note = QLabel(
            "Select one or more Home Assistant switch entities containing 'Alexa' in the name. "
            "Every selected switch is controlled together when this screen locks or unlocks."
        )
        note.setWordWrap(True)
        note.setFont(font(10, QFont.Black))
        note.setStyleSheet(
            "color:#cdd8ee; background:rgba(255,255,255,0.06); "
            "border:1px solid rgba(255,255,255,0.10); border-radius:12px; padding:8px;"
        )
        root.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        body = QWidget()
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(8)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        cancel = RoundButton("Cancel", active=False, min_h=42)
        save = RoundButton("Save Selected", active=True, min_h=42)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)

        select_all.clicked.connect(self.select_all)
        clear.clicked.connect(self.clear_all)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save)
        self.refresh()

    def refresh(self):
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.buttons = {}

        if not self.available_entities:
            text = (
                "Loading Alexa switches from Home Assistant…"
                if self._loading
                else "No switch entities containing 'Alexa' were found in Home Assistant."
            )
            none = QLabel(text)
            none.setWordWrap(True)
            none.setStyleSheet(
                "color:#c4d0e5; background:rgba(255,255,255,0.05); "
                "border-radius:12px; padding:12px;"
            )
            self.body_lay.addWidget(none)
            return

        selected = self.selected_ids()
        for entity in self.available_entities:
            entity_id = str(entity.get("entityId") or "")
            name = str(entity.get("name") or entity_id)
            is_selected = entity_id in selected
            prefix = "✓  " if is_selected else "○  "
            # Show the entity ID underneath/in-line when it differs from the
            # friendly name so similarly named Alexa controls stay unambiguous.
            detail = "" if name == entity_id else f"  ·  {entity_id}"
            button = RoundButton(prefix + name + detail, active=is_selected, min_h=52)
            button.clicked.connect(lambda checked=False, e=entity: self.toggle_entity(e))
            self.buttons[entity_id] = button
            self.body_lay.addWidget(button)
        self.body_lay.addStretch(1)

    def toggle_entity(self, entity: dict):
        normalized = self._normalize_switch(entity)
        if not normalized:
            return
        entity_id = normalized["entityId"]
        selected = self.selected_ids()
        if entity_id in selected:
            self.selected_entities = [
                item for item in self.selected_entities
                if str((self._normalize_selection(item) or {}).get("entityId") or "") != entity_id
            ]
        else:
            # Multi-select by design: add this switch without clearing the others.
            self.selected_entities.append(copy.deepcopy(normalized))
        self.refresh()

    def select_all(self):
        self.selected_entities = [copy.deepcopy(item) for item in self.available_entities]
        self.refresh()

    def clear_all(self):
        self.selected_entities = []
        self.refresh()

    def save(self):
        clean: list[dict] = []
        seen: set[str] = set()
        available_by_id = {item["entityId"]: item for item in self.available_entities}
        for raw in self.selected_entities:
            item = self._normalize_selection(raw)
            if not item:
                continue
            entity_id = item["entityId"]
            if entity_id in seen:
                continue
            seen.add(entity_id)
            if entity_id in available_by_id:
                item = copy.deepcopy(available_by_id[entity_id])
            clean.append(item)
        self.saved.emit(clean)
        self.accept()


class DeviceInternetSelectionDialog(QDialog):
    """Searchable multi-select picker for device network-access switches."""

    saved = pyqtSignal(list)
    loadCompleted = pyqtSignal(object)

    def __init__(self, state: AppState, selected_entities: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.s = state
        self.selected_entities = copy.deepcopy(selected_entities or [])
        self.available_entities: list[dict] = []
        self.buttons: dict[str, RoundButton] = {}
        self._loading = False
        self.setModal(True)
        self.setWindowTitle("Device Internet")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
            QLineEdit {
                background:rgba(7,13,25,0.96);
                color:#ffffff;
                border:1px solid rgba(100,229,255,0.42);
                border-radius:11px;
                padding:8px 12px;
                font-weight:900;
                font-size:14px;
                min-height:28px;
            }
        """)
        self.loadCompleted.connect(self._handle_loaded)
        self.build()
        QTimer.singleShot(0, self.load_entities)
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    @staticmethod
    def _normalize_switch(item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
        if not entity_id.startswith("switch."):
            return None
        friendly_name = str(item.get("name") or item.get("friendly_name") or entity_id).strip() or entity_id
        display_name = friendly_name
        # Generic UniFi network-access switches often have an unhelpful friendly
        # name such as "Blocked". In that case, make the entity ID human-readable.
        if friendly_name.lower() in {"blocked", "block", "network access", "client access"}:
            stem = entity_id.split(".", 1)[1] if "." in entity_id else entity_id
            display_name = " ".join(part.capitalize() for part in stem.split("_") if part) or friendly_name
        return {
            "entityId": entity_id,
            "name": display_name,
            "controlName": friendly_name,
            "state": str(item.get("state") or ""),
        }

    @staticmethod
    def _normalize_selection(item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
        if not entity_id.startswith("switch."):
            return None
        name = str(item.get("name") or item.get("friendly_name") or item.get("controlName") or entity_id).strip() or entity_id
        return {
            "entityId": entity_id,
            "name": name,
            "controlName": str(item.get("controlName") or item.get("control_name") or name).strip() or name,
            "state": str(item.get("state") or ""),
        }

    def selected_ids(self) -> set[str]:
        return {
            str(item.get("entityId") or "").strip()
            for item in (self._normalize_selection(raw) for raw in self.selected_entities)
            if item and str(item.get("entityId") or "").strip()
        }

    def load_entities(self):
        if self._loading:
            return
        self._loading = True
        self.refresh()
        payload = self.s.ha_payload({"domains": ["switch"]})

        def worker():
            try:
                data = self.s.api.post("/api/ha/entities", payload)
                result = {"entities": data.get("entities") or [], "error": None}
            except Exception as exc:
                result = {"entities": [], "error": str(exc)}
            try:
                self.loadCompleted.emit(result)
            except RuntimeError:
                pass

        threading.Thread(target=worker, name="device-internet-picker-load", daemon=True).start()

    def _handle_loaded(self, info: object):
        self._loading = False
        data = info if isinstance(info, dict) else {}
        by_id: dict[str, dict] = {}
        for raw in data.get("entities") or []:
            item = self._normalize_switch(raw)
            if item:
                by_id[item["entityId"]] = item
        self.available_entities = sorted(
            by_id.values(),
            key=lambda item: (str(item.get("name") or "").lower(), str(item.get("entityId") or "").lower()),
        )

        available_by_id = {item["entityId"]: item for item in self.available_entities}
        migrated: list[dict] = []
        seen: set[str] = set()
        for raw in self.selected_entities:
            item = self._normalize_selection(raw)
            if not item:
                continue
            entity_id = item["entityId"]
            if entity_id in seen:
                continue
            seen.add(entity_id)
            if entity_id in available_by_id:
                item = copy.deepcopy(available_by_id[entity_id])
            migrated.append(item)
        self.selected_entities = migrated
        self.refresh()

        error = str(data.get("error") or "").strip()
        if error:
            QMessageBox.warning(self, "Device Internet", f"Could not load switch entities from Home Assistant:\n{error}")

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("DEVICE INTERNET")
        title.setFont(font(22, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        clear = RoundButton("Clear Selected", active=False, kind="danger", min_h=40)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(clear)
        root.addLayout(header)

        note = QLabel(
            "Choose the Home Assistant switch entities that control internet/network access for the devices you want on the main-screen iPad button. "
            "The iPad button shows ONLINE when those switches are ON. Pressing it makes the devices OFFLINE by turning the selected switches OFF; pressing it again restores them ON."
        )
        note.setWordWrap(True)
        note.setFont(font(10, QFont.Black))
        note.setStyleSheet(
            "color:#cdd8ee; background:rgba(255,255,255,0.06); "
            "border:1px solid rgba(255,255,255,0.10); border-radius:12px; padding:8px;"
        )
        root.addWidget(note)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search device name or entity ID…")
        self.search.setClearButtonEnabled(True)
        # This panel runs on a touchscreen and does not rely on an OS keyboard.
        # Match the other searchable entity picker: tapping the search field opens
        # the built-in keyboard, then setText() drives the existing live filter.
        self.search.mousePressEvent = lambda event: self.open_search_keyboard()
        self.search.textChanged.connect(self.refresh)
        root.addWidget(self.search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        body = QWidget()
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(8)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        cancel = RoundButton("Cancel", active=False, min_h=42)
        save = RoundButton("Save Selected", active=True, min_h=42)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)

        clear.clicked.connect(self.clear_all)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save)
        self.refresh()

    def open_search_keyboard(self):
        value = MiniTextKeyboardDialog.get_text(self, "Search Devices", self.search.text())
        if value is not None:
            self.search.setText(value)
            self.refresh()

    def filtered_entities(self) -> list[dict]:
        query = str(self.search.text() if hasattr(self, "search") else "").strip().lower()
        if not query:
            return list(self.available_entities)
        words = [word for word in query.split() if word]
        matched: list[dict] = []
        for entity in self.available_entities:
            haystack = " ".join([
                str(entity.get("name") or ""),
                str(entity.get("controlName") or ""),
                str(entity.get("entityId") or ""),
            ]).lower()
            if all(word in haystack for word in words):
                matched.append(entity)
        return matched

    def refresh(self):
        if not hasattr(self, "body_lay"):
            return
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.buttons = {}

        if not self.available_entities:
            text = "Loading switch entities from Home Assistant…" if self._loading else "No Home Assistant switch entities were found."
            none = QLabel(text)
            none.setWordWrap(True)
            none.setStyleSheet("color:#c4d0e5; background:rgba(255,255,255,0.05); border-radius:12px; padding:12px;")
            self.body_lay.addWidget(none)
            return

        filtered = self.filtered_entities()
        if not filtered:
            none = QLabel("No switches match your search.")
            none.setWordWrap(True)
            none.setStyleSheet("color:#c4d0e5; background:rgba(255,255,255,0.05); border-radius:12px; padding:12px;")
            self.body_lay.addWidget(none)
            return

        selected = self.selected_ids()
        for entity in filtered:
            entity_id = str(entity.get("entityId") or "")
            name = str(entity.get("name") or entity_id)
            is_selected = entity_id in selected
            prefix = "✓  " if is_selected else "○  "
            detail = "" if name == entity_id else f"  ·  {entity_id}"
            button = RoundButton(prefix + name + detail, active=is_selected, min_h=52)
            button.clicked.connect(lambda checked=False, e=entity: self.toggle_entity(e))
            self.buttons[entity_id] = button
            self.body_lay.addWidget(button)
        self.body_lay.addStretch(1)

    def toggle_entity(self, entity: dict):
        normalized = self._normalize_switch(entity)
        if not normalized:
            return
        entity_id = normalized["entityId"]
        if entity_id in self.selected_ids():
            self.selected_entities = [
                item for item in self.selected_entities
                if str((self._normalize_selection(item) or {}).get("entityId") or "") != entity_id
            ]
        else:
            self.selected_entities.append(copy.deepcopy(normalized))
        self.refresh()

    def clear_all(self):
        self.selected_entities = []
        self.refresh()

    def save(self):
        clean: list[dict] = []
        seen: set[str] = set()
        available_by_id = {item["entityId"]: item for item in self.available_entities}
        for raw in self.selected_entities:
            item = self._normalize_selection(raw)
            if not item:
                continue
            entity_id = item["entityId"]
            if entity_id in seen:
                continue
            seen.add(entity_id)
            if entity_id in available_by_id:
                item = copy.deepcopy(available_by_id[entity_id])
            clean.append(item)
        self.saved.emit(clean)
        self.accept()


class ThermostatSyncSelectionDialog(QDialog):
    saved = pyqtSignal(list)
    loadCompleted = pyqtSignal(object)


    def __init__(self, state: AppState, selected_peers: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.s = state
        self.selected_peers = copy.deepcopy(selected_peers or [])
        self.available_peers: list[dict] = []
        self.buttons: dict[str, RoundButton] = {}
        self._peers_loading = False
        self._load_error = ""
        self.setModal(True)
        self.setWindowTitle("Thermostat Sync")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        self.loadCompleted.connect(self._handle_peers_loaded)
        self._seed_selected_peers()
        self.build()
        QTimer.singleShot(0, self.load_peers)
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    def selected_ids(self) -> set[str]:
        return {str(p.get("entityId") or p.get("entity_id") or "").strip() for p in self.selected_peers if isinstance(p, dict)}



    def _normalize_peer(self, peer: object) -> dict | None:
        if not isinstance(peer, dict):
            return None
        entity_id = str(peer.get("entityId") or peer.get("entity_id") or "").strip()
        if not entity_id.startswith("climate."):
            return None
        return {
            "entityId": entity_id,
            "name": str(peer.get("name") or peer.get("friendlyName") or peer.get("friendly_name") or entity_id),
            "state": str(peer.get("state") or "unknown"),
            "domain": "climate",
            "away": bool(peer.get("away", False)),
            "doorPauseActive": bool(peer.get("doorPauseActive", False)),
            "serial": str(peer.get("serial") or peer.get("ihaSerial") or ""),
            "panelUrl": str(peer.get("panelUrl") or peer.get("panel_url") or ""),
            "syncCapable": bool(peer.get("syncCapable", peer.get("ihaPanel", False))),
        }

    def _seed_selected_peers(self):
        by_id: dict[str, dict] = {}
        for peer in self.selected_peers:
            normalized = self._normalize_peer(peer)
            if normalized:
                by_id[normalized["entityId"]] = normalized
        self.available_peers = sorted(
            by_id.values(),
            key=lambda item: str(item.get("name") or item.get("entityId") or "").lower(),
        )

    def _handle_peers_loaded(self, info: object):
        self._peers_loading = False
        data = info if isinstance(info, dict) else {}
        self._load_error = str(data.get("error") or "")
        by_id: dict[str, dict] = {}
        for peer in self.selected_peers:
            normalized = self._normalize_peer(peer)
            if normalized:
                by_id[normalized["entityId"]] = normalized
        for peer in data.get("thermostats") or []:
            normalized = self._normalize_peer(peer)
            if normalized:
                previous = by_id.get(normalized["entityId"]) or {}
                if not normalized.get("panelUrl") and previous.get("panelUrl"):
                    normalized["panelUrl"] = previous.get("panelUrl")
                if not normalized.get("serial") and previous.get("serial"):
                    normalized["serial"] = previous.get("serial")
                by_id[normalized["entityId"]] = normalized
        self.available_peers = sorted(
            by_id.values(),
            key=lambda item: str(item.get("name") or item.get("entityId") or "").lower(),
        )
        if hasattr(self, "refresh_button"):
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("Refresh")
        self.refresh()

    def load_peers(self):
        if self._peers_loading:
            return
        self._peers_loading = True
        self._load_error = ""
        if hasattr(self, "refresh_button"):
            self.refresh_button.setEnabled(False)
            self.refresh_button.setText("Loading…")
        self.refresh()
        selected_snapshot = copy.deepcopy(self.selected_peers)
        payload = self.s.ha_payload({"selected": selected_snapshot})

        def worker():
            try:
                data = self.s.api.post("/api/sync/thermostats", payload, timeout=8.0)
                result = {"thermostats": data.get("thermostats") or [], "error": None}
            except Exception as exc:
                result = {"thermostats": [], "error": str(exc)}
            try:
                self.loadCompleted.emit(result)
            except RuntimeError:
                pass

        threading.Thread(target=worker, name="sync-peer-picker-load", daemon=True).start()

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("THERMOSTAT SYNC")
        title.setFont(font(22, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        self.refresh_button = RoundButton("Refresh", active=True, min_h=40)
        refresh = self.refresh_button
        clear = RoundButton("Clear", active=False, kind="danger", min_h=40)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh)
        header.addWidget(clear)
        root.addLayout(header)

        note = QLabel("Select the other IHA thermostats that should receive Heat, Cool, Off, Arriving, and setpoint changes while the main-screen Sync button is active. Away and Return Home always remain separate on each thermostat. Sync only stays armed for 30 seconds at a time.")
        note.setWordWrap(True)
        note.setFont(font(10, QFont.Black))
        note.setStyleSheet("color:#cdd8ee; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.10); border-radius:12px; padding:8px;")
        root.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        body = QWidget()
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(8)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        cancel = RoundButton("Cancel", active=False, min_h=42)
        save = RoundButton("Save Selected", active=True, min_h=42)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)

        refresh.clicked.connect(self.reload_peers)
        clear.clicked.connect(self.clear_all)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save)
        self.refresh()


    def reload_peers(self):
        self.load_peers()

    def refresh(self):
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.buttons = {}
        if not self.available_peers:
            detail = getattr(self, "_load_error", "")
            text = "Loading IHA thermostats…" if self._peers_loading else "No IHA thermostat climate entities found in Home Assistant."
            if detail and not self._peers_loading:
                text += f"\n\n{detail}"
            none = QLabel(text)
            none.setWordWrap(True)
            none.setStyleSheet("color:#c4d0e5; background:rgba(255,255,255,0.05); border-radius:12px; padding:12px;")
            self.body_lay.addWidget(none)
            return
        selected = self.selected_ids()
        for peer in self.available_peers:
            eid = str(peer.get("entityId") or "").strip()
            name = str(peer.get("name") or eid)
            state = str(peer.get("state") or "")
            badges = []
            if peer.get("away"):
                badges.append("Away")
            if peer.get("doorPauseActive"):
                badges.append("Door pause")
            if state:
                badges.append(state)
            suffix = f"  ({' • '.join(badges)})" if badges else ""
            active = eid in selected
            b = RoundButton(("✓  " if active else "○  ") + name + suffix, active=active, min_h=52)
            b.clicked.connect(lambda checked=False, p=peer: self.toggle_peer(p))
            self.buttons[eid] = b
            self.body_lay.addWidget(b)
        self.body_lay.addStretch(1)

    def toggle_peer(self, peer: dict):
        eid = str(peer.get("entityId") or "").strip()
        if not eid:
            return
        if eid in self.selected_ids():
            self.selected_peers = [p for p in self.selected_peers if str(p.get("entityId") or "") != eid]
        else:
            self.selected_peers.append({
                "entityId": eid,
                "name": str(peer.get("name") or eid),
                "state": str(peer.get("state") or "unknown"),
                "domain": "climate",
                "serial": str(peer.get("serial") or peer.get("ihaSerial") or ""),
                "panelUrl": str(peer.get("panelUrl") or peer.get("panel_url") or ""),
                "syncCapable": bool(peer.get("syncCapable", peer.get("ihaPanel", False))),
            })
        self.refresh()

    def clear_all(self):
        self.selected_peers = []
        self.refresh()

    def save(self):
        clean = []
        seen = set()
        for peer in self.selected_peers:
            if not isinstance(peer, dict):
                continue
            eid = str(peer.get("entityId") or peer.get("entity_id") or "").strip()
            if not eid.startswith("climate.") or eid in seen:
                continue
            seen.add(eid)
            clean.append({
                "entityId": eid,
                "name": str(peer.get("name") or peer.get("friendlyName") or peer.get("friendly_name") or eid),
                "domain": "climate",
                "state": str(peer.get("state") or "unknown"),
                "serial": str(peer.get("serial") or peer.get("ihaSerial") or ""),
                "panelUrl": str(peer.get("panelUrl") or peer.get("panel_url") or ""),
                "syncCapable": bool(peer.get("syncCapable", peer.get("ihaPanel", False))),
            })
        self.saved.emit(clean)
        self.accept()



class HouseSyncSelectionDialog(QDialog):
    selected = pyqtSignal(dict)
    loadCompleted = pyqtSignal(object)


    def __init__(self, state: AppState, current_peer: dict | None = None, parent=None):
        super().__init__(parent)
        self.s = state
        self.current_peer = copy.deepcopy(current_peer) if isinstance(current_peer, dict) else None
        self.available_peers: list[dict] = []
        self.selected_peer: dict | None = copy.deepcopy(self.current_peer)
        self.buttons: dict[str, RoundButton] = {}
        self._peers_loading = False
        self._load_error = ""
        self.setModal(True)
        self.setWindowTitle("House Sync Source")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        self.loadCompleted.connect(self._handle_peers_loaded)
        self._seed_saved_peers()
        self.build()
        QTimer.singleShot(0, self.load_peers)
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    def current_id(self) -> str:
        peer = self.selected_peer if isinstance(self.selected_peer, dict) else {}
        return str(peer.get("entityId") or peer.get("entity_id") or "").strip()

    def normalize_peer(self, peer: object) -> dict | None:
        if not isinstance(peer, dict):
            return None
        entity_id = str(peer.get("entityId") or peer.get("entity_id") or "").strip()
        if not entity_id.startswith("climate."):
            return None
        return {
            "entityId": entity_id,
            "name": str(peer.get("name") or peer.get("friendlyName") or peer.get("friendly_name") or entity_id),
            "state": str(peer.get("state") or "unknown"),
            "serial": str(peer.get("serial") or peer.get("ihaSerial") or ""),
            "panelUrl": str(peer.get("panelUrl") or peer.get("panel_url") or "").strip(),
            "syncCapable": bool(peer.get("syncCapable", peer.get("ihaPanel", False))),
        }



    def _seed_saved_peers(self):
        by_id: dict[str, dict] = {}
        saved = self.normalize_peer(self.current_peer)
        if saved:
            by_id[saved["entityId"]] = saved
        ha = self.s.ha()
        if isinstance(ha, dict):
            for key in ("syncThermostatEntities", "syncAvailableThermostatEntities"):
                for raw in ha.get(key) or []:
                    peer = self.normalize_peer(raw)
                    if peer:
                        by_id[peer["entityId"]] = peer
        self.available_peers = sorted(
            by_id.values(),
            key=lambda item: str(item.get("name") or item.get("entityId") or "").lower(),
        )

    def _handle_peers_loaded(self, info: object):
        self._peers_loading = False
        data = info if isinstance(info, dict) else {}
        self._load_error = str(data.get("error") or "")
        by_id: dict[str, dict] = {}
        saved = self.normalize_peer(self.current_peer)
        if saved:
            by_id[saved["entityId"]] = saved
        ha = self.s.ha()
        if isinstance(ha, dict):
            for key in ("syncThermostatEntities", "syncAvailableThermostatEntities"):
                for raw in ha.get(key) or []:
                    peer = self.normalize_peer(raw)
                    if peer:
                        by_id[peer["entityId"]] = peer
        for raw in data.get("thermostats") or []:
            peer = self.normalize_peer(raw)
            if peer:
                previous = by_id.get(peer["entityId"]) or {}
                if not peer.get("panelUrl") and previous.get("panelUrl"):
                    peer["panelUrl"] = previous.get("panelUrl")
                if not peer.get("serial") and previous.get("serial"):
                    peer["serial"] = previous.get("serial")
                by_id[peer["entityId"]] = peer
        self.available_peers = sorted(
            by_id.values(),
            key=lambda item: str(item.get("name") or item.get("entityId") or "").lower(),
        )
        if hasattr(self, "refresh_button"):
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("Refresh")
        self.refresh()

    def load_peers(self):
        if self._peers_loading:
            return
        self._peers_loading = True
        self._load_error = ""
        if hasattr(self, "refresh_button"):
            self.refresh_button.setEnabled(False)
            self.refresh_button.setText("Loading…")
        self.refresh()
        selected = [copy.deepcopy(self.selected_peer)] if isinstance(self.selected_peer, dict) else []
        payload = self.s.ha_payload({"selected": selected})

        def worker():
            try:
                data = self.s.api.post("/api/sync/thermostats", payload, timeout=8.0)
                result = {"thermostats": data.get("thermostats") or [], "error": None}
            except Exception as exc:
                result = {"thermostats": [], "error": str(exc)}
            try:
                self.loadCompleted.emit(result)
            except RuntimeError:
                pass

        threading.Thread(target=worker, name="house-sync-peer-picker-load", daemon=True).start()

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("HOUSE SYNC SOURCE")
        title.setFont(font(22, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        self.refresh_button = RoundButton("Refresh", active=True, min_h=40)
        refresh = self.refresh_button
        self.use_button = RoundButton("Use Selected Source", active=True, min_h=40)
        self.use_button.setEnabled(bool(self.current_id()))
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.use_button)
        header.addWidget(refresh)
        root.addLayout(header)

        note = QLabel("Choose one other IHA thermostat to copy from. House Sync reads only the Blinds, Lights, and Room page configuration. It never copies thermostat settings, panel or Room-entry security codes, Home Assistant credentials, schedules, or JARVIS settings.")
        note.setWordWrap(True)
        note.setFont(font(10, QFont.Black))
        note.setStyleSheet("color:#cdd8ee; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.10); border-radius:12px; padding:8px;")
        root.addWidget(note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:0;}")
        body = QWidget()
        self.body_lay = QVBoxLayout(body)
        self.body_lay.setContentsMargins(0, 0, 0, 0)
        self.body_lay.setSpacing(8)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        cancel = RoundButton("Cancel", active=False, min_h=42)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        root.addLayout(bottom)

        refresh.clicked.connect(self.reload_peers)
        cancel.clicked.connect(self.reject)
        self.use_button.clicked.connect(self.save)
        self.refresh()


    def reload_peers(self):
        self.load_peers()

    def refresh(self):
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.buttons = {}
        if not self.available_peers:
            text = "Loading IHA thermostats…" if self._peers_loading else "No other IHA thermostat screens were found in Home Assistant."
            detail = str(getattr(self, "_load_error", "") or "").strip()
            if detail and not self._peers_loading:
                text += f"\n\n{detail}"
            none = QLabel(text)
            none.setWordWrap(True)
            none.setStyleSheet("color:#c4d0e5; background:rgba(255,255,255,0.05); border-radius:12px; padding:12px;")
            self.body_lay.addWidget(none)
            self.use_button.setEnabled(False)
            return

        selected_id = self.current_id()
        for peer in self.available_peers:
            entity_id = str(peer.get("entityId") or "")
            name = str(peer.get("name") or entity_id)
            state = str(peer.get("state") or "")
            panel_url = str(peer.get("panelUrl") or "").strip()
            active = entity_id == selected_id
            suffix_parts = [state] if state else []
            if not panel_url:
                suffix_parts.append("House Sync unavailable")
            suffix = f"  ({' • '.join(suffix_parts)})" if suffix_parts else ""
            button = RoundButton(("✓  " if active else "○  ") + name + suffix, active=active, min_h=52)
            button.setEnabled(bool(panel_url))
            if panel_url:
                button.clicked.connect(lambda checked=False, p=peer: self.choose_peer(p))
            self.buttons[entity_id] = button
            self.body_lay.addWidget(button)
        self.body_lay.addStretch(1)
        self.use_button.setEnabled(bool(selected_id and str((self.selected_peer or {}).get("panelUrl") or "").strip()))

    def choose_peer(self, peer: dict):
        self.selected_peer = copy.deepcopy(peer)
        self.refresh()

    def save(self):
        peer = self.normalize_peer(self.selected_peer)
        if not peer or not str(peer.get("panelUrl") or "").strip():
            return
        self.selected.emit(peer)
        self.accept()


class ColorWheelWidget(QWidget):
    colorChanged = pyqtSignal(QColor)

    def __init__(self, color: str = "#ffd76f", parent=None):
        super().__init__(parent)
        self.selected = QColor(color if str(color or "").startswith("#") else f"#{color}")
        if not self.selected.isValid():
            self.selected = QColor("#ffd76f")
        self.setMinimumSize(330, 330)
        self.setCursor(Qt.PointingHandCursor)
        self._cached_side = 0
        self._cached_image = None

    def _wheel_image(self, side: int):
        side = max(32, int(side))
        if self._cached_image is not None and self._cached_side == side:
            return self._cached_image
        img = QImage(side, side, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        cx = cy = side / 2.0
        radius = (side / 2.0) - 2.0
        for y in range(side):
            dy = y - cy
            for x in range(side):
                dx = x - cx
                dist = math.sqrt(dx * dx + dy * dy)
                if dist <= radius:
                    hue = (math.atan2(dy, dx) / (2.0 * math.pi) + 1.0) % 1.0
                    sat = max(0.0, min(1.0, dist / radius))
                    img.setPixelColor(x, y, QColor.fromHsvF(hue, sat, 1.0, 1.0))
        self._cached_side = side
        self._cached_image = img
        return img

    def set_color(self, color: str | QColor, notify: bool = True):
        c = QColor(color)
        if not c.isValid():
            return
        self.selected = c
        self.update()
        if notify:
            self.colorChanged.emit(QColor(self.selected))

    def _select_from_pos(self, pos):
        side = min(self.width(), self.height())
        left = (self.width() - side) / 2.0
        top = (self.height() - side) / 2.0
        cx = left + side / 2.0
        cy = top + side / 2.0
        radius = (side / 2.0) - 2.0
        dx = float(pos.x()) - cx
        dy = float(pos.y()) - cy
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > radius:
            dx *= radius / max(1.0, dist)
            dy *= radius / max(1.0, dist)
            dist = radius
        hue = (math.atan2(dy, dx) / (2.0 * math.pi) + 1.0) % 1.0
        sat = max(0.0, min(1.0, dist / radius))
        self.set_color(QColor.fromHsvF(hue, sat, 1.0, 1.0))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._select_from_pos(event.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self._select_from_pos(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        side = min(self.width(), self.height())
        left = int((self.width() - side) / 2)
        top = int((self.height() - side) / 2)
        image = self._wheel_image(side)
        p.drawImage(left, top, image)
        wheel = QRectF(left + 2, top + 2, side - 4, side - 4)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 130), 3))
        p.drawEllipse(wheel)

        hue, sat, value, alpha = self.selected.getHsvF()
        if hue < 0:
            hue = 0.0
        radius = (side / 2.0) - 2.0
        angle = hue * 2.0 * math.pi
        x = left + side / 2.0 + math.cos(angle) * sat * radius
        y = top + side / 2.0 + math.sin(angle) * sat * radius
        p.setBrush(QColor(self.selected))
        p.setPen(QPen(QColor(255, 255, 255), 4))
        p.drawEllipse(QPointF(x, y), 12, 12)
        p.setPen(QPen(QColor(0, 0, 0, 160), 2))
        p.drawEllipse(QPointF(x, y), 8, 8)


class LightColorDialog(QDialog):
    colorPreviewed = pyqtSignal(str)

    COLORS = [
        ("Warm", "#ffd76f"),
        ("White", "#ffffff"),
        ("Red", "#ff3b30"),
        ("Orange", "#ff9500"),
        ("Yellow", "#ffe600"),
        ("Green", "#34c759"),
        ("Cyan", "#32d7ff"),
        ("Blue", "#007aff"),
        ("Purple", "#af52de"),
        ("Pink", "#ff2d8d"),
    ]

    def __init__(self, current: str = "#ffd76f", title: str = "RGB Color", parent=None):
        super().__init__(parent)
        self.selected_color = self.clean_color(current)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(900, 520)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)

        header = QLabel(title)
        header.setAlignment(Qt.AlignCenter)
        header.setFont(font(24, QFont.Black))
        root.addWidget(header)

        hint = QLabel("Tap or drag the wheel for gradients, or choose one of the primary colors.")
        hint.setAlignment(Qt.AlignCenter)
        hint.setFont(font(10, QFont.Black))
        hint.setStyleSheet("color:#c9d5ea;")
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(18)
        self.wheel = ColorWheelWidget(self.selected_color, self)
        self.wheel.colorChanged.connect(lambda c: self.pick(c.name()))
        body.addWidget(self.wheel, 1)

        right = QVBoxLayout()
        right.setSpacing(12)
        self.preview = QLabel("")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(74)
        right.addWidget(self.preview)
        self.refresh_preview()

        presets_title = QLabel("PRIMARY COLORS")
        presets_title.setAlignment(Qt.AlignCenter)
        presets_title.setFont(font(10, QFont.Black))
        presets_title.setStyleSheet("color:#46e8ff; letter-spacing:3px;")
        right.addWidget(presets_title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.preset_buttons = []
        for idx, (name, color) in enumerate(self.COLORS):
            btn = QPushButton("")
            btn.setAccessibleName(name)
            btn.setMinimumSize(104, 56)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, c=color: self.pick(c, sync_wheel=True))
            self.preset_buttons.append((btn, color))
            grid.addWidget(btn, idx // 2, idx % 2)
        right.addLayout(grid, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        bottom = QHBoxLayout()
        close = RoundButton("Close", active=True, min_h=48)
        close.setMinimumWidth(170)
        close.clicked.connect(self.accept)
        bottom.addStretch(1)
        bottom.addWidget(close)
        bottom.addStretch(1)
        root.addLayout(bottom)

        self.refresh_presets()

    @staticmethod
    def clean_color(value: str, fallback: str = "#ffd76f") -> str:
        raw = str(value or fallback).strip()
        if not raw.startswith("#"):
            raw = "#" + raw
        raw = raw[:7]
        c = QColor(raw)
        return raw.lower() if c.isValid() else fallback

    def color_button_style(self, color: str, selected: bool = False) -> str:
        border = "4px solid #ffffff" if selected else "1px solid rgba(255,255,255,0.28)"
        return f"background:{color}; color:transparent; border:{border}; border-radius:18px;"

    def pick(self, color: str, sync_wheel: bool = False):
        self.selected_color = self.clean_color(color)
        if sync_wheel:
            self.wheel.set_color(self.selected_color, notify=False)
        self.refresh_preview()
        self.refresh_presets()
        self.colorPreviewed.emit(self.selected_color)

    def refresh_presets(self):
        for btn, color in getattr(self, "preset_buttons", []):
            btn.setStyleSheet(self.color_button_style(color, selected=(self.selected_color.lower() == color.lower())))

    def refresh_preview(self):
        color = self.clean_color(self.selected_color)
        self.preview.setText("")
        self.preview.setStyleSheet(f"background:{color}; border:2px solid rgba(255,255,255,0.28); border-radius:22px; padding:10px;")

    @staticmethod
    def get_color(parent, current: str = "#ffd76f", title: str = "RGB Color") -> str | None:
        dlg = LightColorDialog(current, title, parent)
        if dlg.exec_() == QDialog.Accepted:
            return dlg.selected_color
        return None


class RoomManagerSettingsDialog(QDialog):
    saved = pyqtSignal()
    configSaveCompleted = pyqtSignal(object)

    PROFILES = {
        "Blinds": ("blinds", "Shade Rooms", "blinds", "Blind", 6),
        "Lights": ("lights", "Light Rooms", "lights", "Light", 12),
        "Room": ("roomControl", "Room Control Rooms", "controls", "Control", 12),
    }

    def __init__(self, state: AppState, page_name: str, parent=None):
        super().__init__(parent)
        self.s = state
        self._config_save_inflight = False
        self._config_save_pending = False
        self.configSaveCompleted.connect(self._handle_config_save_completed)
        self.page_name = page_name
        self.domain, title, self.item_key, self.item_label, self.max_entries = self.PROFILES.get(page_name, self.PROFILES["Room"])
        self.selected_key = str((self.s.config.get(self.domain) or {}).get("room") or "")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(760, 500)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        self.header = QLabel(title)
        self.header.setAlignment(Qt.AlignCenter)
        self.header.setFont(font(24, QFont.Black))
        self.header.setMaximumHeight(44)
        root.addWidget(self.header)

        self.hint = QLabel(
            "Add/delete rooms, then select a room and set how many entries it should show. "
            "New empty entries appear on that page so they can be assigned to Home Assistant devices."
        )
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setFont(font(11, QFont.Black))
        self.hint.setStyleSheet("color:#c9d5ea;")
        root.addWidget(self.hint)

        self.active_tab = "rooms"
        self.tab_panel = None
        self.rooms_tab_btn = None
        self.code_tab_btn = None
        if self.page_name == "Room":
            self.tab_panel = QHBoxLayout()
            self.tab_panel.setSpacing(10)
            self.rooms_tab_btn = RoundButton("Rooms", active=True, min_h=44)
            self.code_tab_btn = RoundButton("Code", min_h=44)
            self.rooms_tab_btn.setMinimumWidth(150)
            self.code_tab_btn.setMinimumWidth(150)
            self.rooms_tab_btn.clicked.connect(lambda checked=False: self.select_settings_tab("rooms"))
            self.code_tab_btn.clicked.connect(lambda checked=False: self.select_settings_tab("code"))
            self.tab_panel.addStretch(1)
            self.tab_panel.addWidget(self.rooms_tab_btn)
            self.tab_panel.addWidget(self.code_tab_btn)
            self.tab_panel.addStretch(1)
            root.addLayout(self.tab_panel)

        self.entry_panel = GlassPanel(radius=22, strong=True)
        self.entry_panel.setMinimumHeight(90)
        entry_lay = QHBoxLayout(self.entry_panel)
        entry_lay.setContentsMargins(12, 10, 12, 10)
        entry_lay.setSpacing(10)

        self.selected_room_label = QLabel("Selected Room")
        self.selected_room_label.setFont(font(16, QFont.Black))
        self.selected_room_label.setStyleSheet("color:#ffffff; background:transparent; border:0;")
        self.selected_room_label.setMinimumWidth(280)
        entry_lay.addWidget(self.selected_room_label, 1)

        self.entry_minus = RoundButton("−", min_h=58)
        self.entry_minus.setFixedSize(64, 58)
        self.entry_count_label = QLabel("0")
        self.entry_count_label.setAlignment(Qt.AlignCenter)
        self.entry_count_label.setMinimumWidth(120)
        self.entry_count_label.setFont(font(34, QFont.Black))
        self.entry_count_label.setStyleSheet("color:#46e8ff; background:transparent; border:0;")
        self.entry_plus = RoundButton("+", active=True, min_h=58)
        self.entry_plus.setFixedSize(64, 58)
        entry_lay.addWidget(self.entry_minus)
        entry_lay.addWidget(self.entry_count_label)
        entry_lay.addWidget(self.entry_plus)

        self.entry_caption = QLabel(f"{self.item_label.upper()} ENTRIES\nMAX {self.max_entries}")
        self.entry_caption.setAlignment(Qt.AlignCenter)
        self.entry_caption.setMinimumWidth(160)
        self.entry_caption.setFont(font(10, QFont.Black, 18))
        self.entry_caption.setStyleSheet("color:#c9d5ea; background:transparent; border:0; letter-spacing:2px;")
        entry_lay.addWidget(self.entry_caption)
        root.addWidget(self.entry_panel)

        self.room_panel = GlassPanel(radius=24, strong=True)
        root.addWidget(self.room_panel, 1)
        self.room_lay = QGridLayout(self.room_panel)
        self.room_lay.setContentsMargins(10, 10, 10, 10)
        self.room_lay.setHorizontalSpacing(8)
        self.room_lay.setVerticalSpacing(8)

        self.code_panel = GlassPanel(radius=24, strong=True)
        code_root = QVBoxLayout(self.code_panel)
        code_root.setContentsMargins(12, 12, 12, 12)
        code_root.setSpacing(8)
        self.code_hint = QLabel("Set a 4-digit local code for any Room entry, then choose exactly which target states require that code. For example, a switch can require a code for ON while OFF stays unlocked.")
        self.code_hint.setWordWrap(True)
        self.code_hint.setAlignment(Qt.AlignCenter)
        self.code_hint.setFont(font(10, QFont.Black))
        self.code_hint.setStyleSheet("color:#c9d5ea; background:transparent; border:0;")
        code_root.addWidget(self.code_hint)

        # Keep Code settings manageable as Room Control grows. Each room gets a
        # compact filter tab so only that room's entries are shown below.
        self.code_room_filter = str(self.selected_key or "")
        self.code_room_buttons: dict[str, RoundButton] = {}
        self.code_room_tabs = QScrollArea()
        self.code_room_tabs.setObjectName("roomCodeTabs")
        self.code_room_tabs.setFixedHeight(54)
        self.code_room_tabs.setFrameShape(QFrame.NoFrame)
        self.code_room_tabs.setWidgetResizable(True)
        self.code_room_tabs.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.code_room_tabs.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.code_room_tabs.setStyleSheet("QScrollArea#roomCodeTabs { background:transparent; border:0; }")
        self.code_room_tabs_content = QWidget()
        self.code_room_tabs_content.setStyleSheet("background:transparent; border:0;")
        self.code_room_tabs_lay = QHBoxLayout(self.code_room_tabs_content)
        self.code_room_tabs_lay.setContentsMargins(0, 2, 0, 2)
        self.code_room_tabs_lay.setSpacing(8)
        self.code_room_tabs.setWidget(self.code_room_tabs_content)
        code_root.addWidget(self.code_room_tabs)

        self.code_scroll = QScrollArea()
        self.code_scroll.setWidgetResizable(True)
        self.code_scroll.setFrameShape(QFrame.NoFrame)
        self.code_scroll.setStyleSheet("QScrollArea { background:transparent; border:0; } QScrollBar:vertical { width:18px; background:rgba(255,255,255,0.04); border-radius:9px; } QScrollBar::handle:vertical { background:rgba(85,240,255,0.38); border-radius:9px; min-height:44px; }")
        self.code_content = QWidget()
        self.code_content.setStyleSheet("background:transparent; border:0;")
        self.code_lay = QVBoxLayout(self.code_content)
        self.code_lay.setContentsMargins(0, 0, 0, 0)
        self.code_lay.setSpacing(8)
        self.code_scroll.setWidget(self.code_content)
        code_root.addWidget(self.code_scroll, 1)
        root.addWidget(self.code_panel, 1)
        self.code_panel.hide()

        bottom = QHBoxLayout()
        self.add_btn = RoundButton("Add Room", active=True, min_h=50)
        self.delete_btn = RoundButton("Delete Selected", kind="danger", min_h=50)
        close_btn = RoundButton("Close", min_h=50)
        self.close_btn = close_btn
        self.add_btn.setMinimumWidth(160)
        self.delete_btn.setMinimumWidth(190)
        close_btn.setMinimumWidth(150)
        self.add_btn.clicked.connect(self.add_room)
        self.delete_btn.clicked.connect(self.delete_selected_room)
        self.entry_minus.clicked.connect(lambda: self.adjust_entry_count(-1))
        self.entry_plus.clicked.connect(lambda: self.adjust_entry_count(1))
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(self.add_btn)
        bottom.addWidget(self.delete_btn)
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)
        self.rebuild()
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    def select_settings_tab(self, tab: str):
        if self.page_name != "Room":
            return
        self.active_tab = "code" if tab == "code" else "rooms"
        is_code = self.active_tab == "code"
        if self.rooms_tab_btn:
            self.rooms_tab_btn.setActive(not is_code)
        if self.code_tab_btn:
            self.code_tab_btn.setActive(is_code)
        self.entry_panel.setVisible(not is_code)
        self.room_panel.setVisible(not is_code)
        self.code_panel.setVisible(is_code)
        self.add_btn.setVisible(not is_code)
        self.delete_btn.setVisible(not is_code)
        self.hint.setText(
            "Assign per-entry codes to Room controls, then choose which target states are protected. Switches show On/Off, covers show Open/Close, and locks show Lock/Unlock."
            if is_code else
            "Add/delete rooms, then select a room and set how many entries it should show. New empty entries appear on that page so they can be assigned to Home Assistant devices."
        )
        if is_code:
            self.rebuild_code_room_tabs()
            self.rebuild_code_entries()

    def rebuild_code_room_tabs(self):
        if self.page_name != "Room" or not hasattr(self, "code_room_tabs_lay"):
            return
        while self.code_room_tabs_lay.count():
            item = self.code_room_tabs_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.code_room_buttons = {}
        _section, rooms = self.ensure_model()
        if self.code_room_filter not in rooms:
            self.code_room_filter = self.selected_key if self.selected_key in rooms else next(iter(rooms), "")
        for room_key, room in rooms.items():
            room_key = str(room_key)
            label = str(room.get("label") or room_key.replace("-", " ").title())
            count = len(room.get("controls") or [])
            btn = RoundButton(f"{label}  {count}", active=room_key == self.code_room_filter, min_h=40)
            btn.setMinimumWidth(130)
            btn.clicked.connect(lambda checked=False, k=room_key: self.select_code_room(k))
            self.code_room_buttons[room_key] = btn
            self.code_room_tabs_lay.addWidget(btn)
        self.code_room_tabs_lay.addStretch(1)

    def select_code_room(self, room_key: str):
        _section, rooms = self.ensure_model()
        room_key = str(room_key or "")
        if room_key not in rooms:
            return
        self.code_room_filter = room_key
        for key, btn in self.code_room_buttons.items():
            btn.setActive(key == room_key)
        self.rebuild_code_entries()

    def _all_room_control_entries(self) -> list[tuple[str, str, dict, int]]:
        section, rooms = self.ensure_model()
        entries: list[tuple[str, str, dict, int]] = []
        for room_key, room in rooms.items():
            room_label = room.get("label") or str(room_key).replace("-", " ").title()
            for idx, ctl in enumerate(room.get("controls") or []):
                if isinstance(ctl, dict):
                    entries.append((str(room_key), room_label, ctl, idx))
        return entries

    def rebuild_code_entries(self):
        if not hasattr(self, "code_lay"):
            return
        while self.code_lay.count():
            item = self.code_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        entries = self._all_room_control_entries()
        if self.code_room_filter:
            entries = [entry for entry in entries if entry[0] == self.code_room_filter]
        if not entries:
            _section, rooms = self.ensure_model()
            room = rooms.get(self.code_room_filter) or {}
            room_label = str(room.get("label") or self.code_room_filter or "this room")
            empty = QLabel(f"No Room entries in {room_label}. Go to Rooms, add entries, then assign Home Assistant devices on the Room page.")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setFont(font(14, QFont.Black))
            empty.setStyleSheet("color:#c9d5ea; background:transparent; border:0; padding:28px;")
            self.code_lay.addWidget(empty)
            self.code_lay.addStretch(1)
            return
        for room_key, room_label, ctl, idx in entries:
            row = GlassPanel(radius=18, strong=False)
            row.setMinimumHeight(126)
            row_lay = QVBoxLayout(row)
            row_lay.setContentsMargins(12, 8, 12, 8)
            row_lay.setSpacing(6)

            top_lay = QHBoxLayout()
            top_lay.setContentsMargins(0, 0, 0, 0)
            top_lay.setSpacing(10)
            name = ctl.get("haName") or ctl.get("name") or f"Control {idx + 1}"
            eid = ctl.get("haEntityId") or "Unassigned"
            domain = ctl.get("domain") or (str(eid).split(".", 1)[0] if "." in str(eid) else "switch")
            code_set = bool(str(ctl.get("accessCode") or "").strip())
            protected = room_control_has_code_protection(ctl)
            text = QLabel(
                f"<span style='font-size:17px; font-weight:1000; color:#ffffff'>{room_label} • {name}</span>"
                f"<br><span style='font-size:11px; font-weight:900; color:#9fb0c8'>{domain.upper()} • {eid}</span>"
            )
            text.setTextFormat(Qt.RichText)
            text.setMinimumWidth(390)
            top_lay.addWidget(text, 1)
            status = QLabel("PROTECTED" if protected else "CODE SET" if code_set else "NO CODE")
            status.setAlignment(Qt.AlignCenter)
            status.setFont(font(10, QFont.Black, 16))
            status.setMinimumWidth(120)
            status.setStyleSheet(
                "color:#7dffce; background:rgba(73,255,196,0.12); border:1px solid rgba(73,255,196,0.35); border-radius:16px; padding:8px;"
                if protected else
                "color:#ffe29a; background:rgba(255,210,96,0.10); border:1px solid rgba(255,210,96,0.32); border-radius:16px; padding:8px;"
                if code_set else
                "color:#9fb0c8; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:16px; padding:8px;"
            )
            top_lay.addWidget(status)
            set_btn = RoundButton("Change Code" if code_set else "Set Code", active=True, min_h=46)
            set_btn.setMinimumWidth(148)
            clear_btn = RoundButton("Clear", kind="danger", min_h=46)
            clear_btn.setMinimumWidth(104)
            clear_btn.setEnabled(code_set)
            set_btn.clicked.connect(lambda checked=False, c=ctl: self.set_entry_code(c))
            clear_btn.clicked.connect(lambda checked=False, c=ctl: self.clear_entry_code(c))
            top_lay.addWidget(set_btn)
            top_lay.addWidget(clear_btn)
            row_lay.addLayout(top_lay)

            state_lay = QHBoxLayout()
            state_lay.setContentsMargins(0, 0, 0, 0)
            state_lay.setSpacing(8)
            label = QLabel("Require code for:")
            label.setFont(font(10, QFont.Black, 16))
            label.setStyleSheet("color:#c9d5ea; background:transparent; border:0;")
            label.setMinimumWidth(138)
            state_lay.addWidget(label)
            required_states = room_control_required_state_map(ctl)
            choices = room_control_code_state_choices(ctl)
            for action, state_label in choices:
                cb = QCheckBox(state_label)
                cb.setCursor(Qt.PointingHandCursor)
                cb.setFont(font(11, QFont.Black))
                cb.setChecked(code_set and bool(required_states.get(action, False)))
                cb.setEnabled(code_set)
                cb.setStyleSheet("""
                    QCheckBox {
                        color:#eef6ff;
                        spacing:8px;
                        background:rgba(255,255,255,0.055);
                        border:1px solid rgba(255,255,255,0.12);
                        border-radius:16px;
                        padding:8px 12px;
                    }
                    QCheckBox:disabled { color:#75849b; background:rgba(255,255,255,0.03); }
                    QCheckBox::indicator { width:22px; height:22px; border-radius:7px; border:2px solid rgba(147,232,255,0.60); background:rgba(0,0,0,0.18); }
                    QCheckBox::indicator:checked { background:#55f0ff; border:2px solid #55f0ff; }
                    QCheckBox::indicator:disabled { border:2px solid rgba(150,165,185,0.28); background:rgba(255,255,255,0.035); }
                """)
                cb.stateChanged.connect(lambda state, c=ctl, a=action: self.set_code_required_state(c, a, state == Qt.Checked))
                state_lay.addWidget(cb)
            state_lay.addStretch(1)
            if not code_set:
                helper = QLabel("Set a code first")
                helper.setFont(font(9, QFont.Black, 14))
                helper.setStyleSheet("color:#7f90aa; background:transparent; border:0;")
                state_lay.addWidget(helper)
            row_lay.addLayout(state_lay)
            self.code_lay.addWidget(row)
        self.code_lay.addStretch(1)

    def _default_code_required_states(self, ctl: dict) -> dict[str, bool]:
        legacy = room_control_legacy_required_actions(ctl)
        return {action: action in legacy for action, _label in room_control_code_state_choices(ctl)}

    def set_entry_code(self, ctl: dict):
        code = CodeKeypadDialog.get_code(self, "Entry Code", "New 4-Digit Code")
        if code is None:
            return
        ctl["accessCode"] = str(code)
        if not isinstance(ctl.get("codeRequiredStates"), dict):
            ctl["codeRequiredStates"] = self._default_code_required_states(ctl)
        self.queue_config_save()
        # Refresh immediately after the keypad closes so the row changes from
        # NO CODE / Set Code to CODE SET / Change Code and enables the state
        # protection choices. Persistence now runs off the UI thread.
        self.rebuild_code_entries()

    def set_code_required_state(self, ctl: dict, action: str, required: bool):
        if not str((ctl or {}).get("accessCode") or "").strip():
            return
        choices = {state_action for state_action, _label in room_control_code_state_choices(ctl)}
        action = str(action or "").strip().lower()
        if action not in choices:
            return
        states = ctl.get("codeRequiredStates")
        if not isinstance(states, dict):
            states = self._default_code_required_states(ctl)
        ctl["codeRequiredStates"] = {state_action: bool(states.get(state_action, False)) for state_action in choices}
        ctl["codeRequiredStates"][action] = bool(required)
        self.queue_config_save()
        self.rebuild_code_entries()

    def clear_entry_code(self, ctl: dict):
        ctl.pop("accessCode", None)
        ctl.pop("codeRequiredStates", None)
        self.queue_config_save()
        self.rebuild_code_entries()

    def ensure_model(self) -> tuple[dict, dict]:
        section = self.s.config.setdefault(self.domain, {})
        rooms = section.setdefault("rooms", {})
        if not rooms:
            rooms["living"] = {"label": "Living Room", self.item_key: []}
            section["room"] = "living"
        for key, room in rooms.items():
            room.setdefault("label", str(key).replace("-", " ").title())
            items = room.setdefault(self.item_key, [])
            if not isinstance(items, list):
                room[self.item_key] = []
        if section.get("room") not in rooms:
            section["room"] = next(iter(rooms))
        if self.selected_key not in rooms:
            self.selected_key = section.get("room") or next(iter(rooms))
        return section, rooms

    def entry_count(self, room: dict | None = None) -> int:
        if room is None:
            _, rooms = self.ensure_model()
            room = rooms.get(self.selected_key) or {}
        return len(room.get(self.item_key) or [])

    def make_entry(self, room_key: str, idx: int) -> dict:
        n = int(idx) + 1
        safe_key = str(room_key or "room").replace(" ", "-").lower()
        if self.page_name == "Lights":
            return {"id": f"{safe_key}-light-{n}", "name": f"Light {n}", "brightness": 0, "lastBrightness": 100, "on": False}
        if self.page_name == "Blinds":
            return {"id": f"{safe_key}-blind-{n}", "name": f"Blind {n}", "position": 100}
        return {"id": f"{safe_key}-control-{n}", "name": f"Control {n}", "domain": "switch", "on": False}

    def save_and_refresh(self):
        self.queue_config_save()
        self.rebuild()

    def update_entry_controls(self):
        _, rooms = self.ensure_model()
        room = rooms.get(self.selected_key) or {}
        count = self.entry_count(room)
        label = room.get("label") or self.selected_key or "Room"
        self.selected_room_label.setText(f"{label}\nEntries shown on this page")
        self.entry_count_label.setText(str(count))
        self.entry_minus.setEnabled(count > 0)
        self.entry_plus.setEnabled(count < self.max_entries)

    def rebuild(self):
        while self.room_lay.count():
            item = self.room_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        section, rooms = self.ensure_model()
        for idx, (key, room) in enumerate(rooms.items()):
            count = len(room.get(self.item_key) or [])
            label = room.get("label") or key
            btn = RoundButton(f"{label}\n{count}/{self.max_entries} {self.item_label}{'' if count == 1 else 's'}", active=key == self.selected_key, min_h=86)
            btn.setMinimumWidth(190)
            btn.clicked.connect(lambda checked=False, k=key: self.select_room(k))
            self.room_lay.addWidget(btn, idx // 4, idx % 4)
        self.delete_btn.setEnabled(len(rooms) > 1)
        self.update_entry_controls()
        if self.page_name == "Room" and self.active_tab == "code":
            self.rebuild_code_room_tabs()
            self.rebuild_code_entries()

    def select_room(self, key: str):
        self.selected_key = key
        if self.page_name == "Room":
            self.code_room_filter = key
        self.s.config.setdefault(self.domain, {})["room"] = key
        self.queue_config_save()
        self.rebuild()

    def set_entry_count(self, target: int):
        section, rooms = self.ensure_model()
        key = self.selected_key or section.get("room")
        room = rooms.get(key)
        if not room:
            return
        items = room.setdefault(self.item_key, [])
        target = int(clamp(target, 0, self.max_entries))
        current = len(items)
        if target == current:
            return

        if target < current:
            removed = items[target:]
            assigned_removed = [x for x in removed if isinstance(x, dict) and x.get("haEntityId")]
            if assigned_removed:
                msg = f"Reducing to {target} removes {len(assigned_removed)} assigned {self.item_label.lower()} entr{'y' if len(assigned_removed) == 1 else 'ies'} from this room. Continue?"
                if QMessageBox.question(self, "Remove Assigned Entries", msg) != QMessageBox.Yes:
                    return
            del items[target:]
        else:
            for idx in range(current, target):
                items.append(self.make_entry(key, idx))
        self.save_and_refresh()

    def adjust_entry_count(self, delta: int):
        self.set_entry_count(self.entry_count() + int(delta))

    def add_room(self):
        section, rooms = self.ensure_model()
        label = MiniTextKeyboardDialog.get_text(self, "New Room Name", "")
        label = str(label or "").strip()
        if not label:
            return
        key = slug_room_key(label, rooms)
        rooms[key] = {"label": label, self.item_key: []}
        section["room"] = key
        self.selected_key = key
        if self.page_name == "Room":
            self.code_room_filter = key
        self.queue_config_save()
        self.rebuild()

    def delete_selected_room(self):
        section, rooms = self.ensure_model()
        key = self.selected_key or section.get("room")
        if key not in rooms:
            return
        if len(rooms) <= 1:
            QMessageBox.information(self, "Room Required", "At least one room must remain.")
            return
        label = rooms.get(key, {}).get("label") or key
        if QMessageBox.question(self, "Delete Room", f"Delete {label}? This removes that room and its {self.item_label.lower()} entries from this page.") != QMessageBox.Yes:
            return
        rooms.pop(key, None)
        section["room"] = next(iter(rooms))
        self.selected_key = section["room"]
        if self.page_name == "Room":
            self.code_room_filter = self.selected_key
        self.queue_config_save()
        self.rebuild()


class SimplePageSettingsDialog(QDialog):
    def __init__(self, page_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{page_name} Settings")
        self.setModal(True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(640, 420)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        title = QLabel(f"{page_name} Settings")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(font(27, QFont.Black))
        root.addWidget(title)
        msg = QLabel("This page does not use the thermostat setup panel. Page-specific controls can be added here later.")
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignCenter)
        msg.setFont(font(13, QFont.Black))
        msg.setStyleSheet("color:#c9d5ea; padding:18px;")
        root.addWidget(msg, 1)
        close = RoundButton("Close", active=True, min_h=50)
        close.setMinimumWidth(150)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        root.addLayout(row)
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)



class AudioGroupEditorDialog(QDialog):
    def __init__(self, players: list[dict], group: dict | None = None, preferred_coordinator: str = "", parent=None):
        super().__init__(parent)
        self.group = copy.deepcopy(group) if isinstance(group, dict) else {}
        self.preferred_coordinator = str(preferred_coordinator or "").strip()
        self.result_group: dict | None = None
        self.checks: dict[str, QCheckBox] = {}
        self.setWindowTitle("Audio Group")
        self.setModal(True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.resize(960, 700)
        self.setStyleSheet("""
            QDialog { background:#07101f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
            QLineEdit { background:rgba(7,13,25,0.96); color:#ffffff; border:1px solid rgba(100,229,255,0.30); border-radius:12px; padding:8px 11px; font-weight:900; font-size:15px; min-height:38px; }
            QCheckBox { color:#f7fbff; font-family:Arial; font-weight:900; font-size:15px; spacing:12px; padding:8px; }
            QCheckBox::indicator { width:28px; height:28px; border-radius:8px; border:2px solid rgba(107,226,255,0.42); background:rgba(7,13,25,0.86); }
            QCheckBox::indicator:checked { background:#49e6ff; border:2px solid rgba(255,255,255,0.55); }
            QScrollArea { background:transparent; border:0; }
            QScrollArea > QWidget > QWidget { background:transparent; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 18)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Edit Audio Group" if self.group else "Add Audio Group")
        title.setFont(font(27, QFont.Black))
        header.addWidget(title, 1)
        cancel = RoundButton("Cancel", min_h=44)
        save = RoundButton("Save Group", active=True, min_h=44)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save_group)
        header.addWidget(cancel)
        header.addWidget(save)
        root.addLayout(header)

        name_label = QLabel("GROUP NAME")
        name_label.setFont(font(9, QFont.Black))
        name_label.setStyleSheet("color:#49e6ff; letter-spacing:2px;")
        root.addWidget(name_label)
        self.name_edit = QLineEdit(str(self.group.get("name") or ""))
        self.name_edit.setPlaceholderText("Example: Whole House")
        self.name_edit.mousePressEvent = lambda event: self.open_name_keyboard()
        root.addWidget(self.name_edit)

        members_label = QLabel("MEDIA PLAYERS")
        members_label.setFont(font(9, QFont.Black))
        members_label.setStyleSheet("color:#49e6ff; letter-spacing:2px; margin-top:4px;")
        root.addWidget(members_label)
        hint = QLabel("Select at least two Home Assistant media players. Pressing the group tile on Audio will group them; pressing it again will ungroup them.")
        hint.setWordWrap(True)
        hint.setFont(font(10, QFont.Black))
        hint.setStyleSheet("color:rgba(219,227,244,0.72);")
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        member_body = QWidget()
        member_layout = QVBoxLayout(member_body)
        member_layout.setContentsMargins(0, 0, 0, 0)
        member_layout.setSpacing(7)
        selected = set(self.group.get("members") or [])
        normalized_players: list[dict] = []
        seen: set[str] = set()
        for player in players or []:
            if not isinstance(player, dict):
                continue
            entity_id = str(player.get("entityId") or player.get("entity_id") or "").strip()
            if not entity_id.startswith("media_player.") or entity_id in seen:
                continue
            seen.add(entity_id)
            normalized_players.append({"entityId": entity_id, "name": str(player.get("name") or entity_id)})
        for entity_id in selected:
            if entity_id.startswith("media_player.") and entity_id not in seen:
                normalized_players.append({"entityId": entity_id, "name": entity_id.split(".", 1)[-1].replace("_", " ").title()})
        normalized_players.sort(key=lambda item: str(item.get("name") or item.get("entityId") or "").lower())
        for player in normalized_players:
            entity_id = player["entityId"]
            row = GlassPanel(radius=16)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(12, 4, 12, 4)
            cb = QCheckBox(str(player.get("name") or entity_id))
            cb.setChecked(entity_id in selected)
            cb.setToolTip(entity_id)
            self.checks[entity_id] = cb
            row_lay.addWidget(cb, 1)
            entity = QLabel(entity_id)
            entity.setFont(font(8, QFont.Black))
            entity.setStyleSheet("color:rgba(219,227,244,0.56);")
            row_lay.addWidget(entity)
            member_layout.addWidget(row)
        member_layout.addStretch(1)
        scroll.setWidget(member_body)
        root.addWidget(scroll, 1)
        QTimer.singleShot(0, self.fit_to_screen)

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    def open_name_keyboard(self):
        value = MiniTextKeyboardDialog.get_text(self, "Audio Group Name", self.name_edit.text())
        if value is not None:
            self.name_edit.setText(value.strip())

    def save_group(self):
        name = self.name_edit.text().strip()
        members = [entity_id for entity_id, checkbox in self.checks.items() if checkbox.isChecked()]
        if not name:
            QMessageBox.warning(self, "Audio Group", "Enter a name for this group.")
            return
        if len(members) < 2:
            QMessageBox.warning(self, "Audio Group", "Select at least two media players.")
            return
        existing_coordinator = str(self.group.get("coordinatorId") or "").strip()
        if self.preferred_coordinator in members:
            coordinator = self.preferred_coordinator
        elif existing_coordinator in members:
            coordinator = existing_coordinator
        else:
            coordinator = members[0]
        group_id = str(self.group.get("id") or "").strip() or f"audio-group-{int(time.time() * 1000)}"
        self.result_group = {"id": group_id, "name": name, "members": members, "coordinatorId": coordinator}
        self.accept()


class AudioSettingsDialog(QDialog):
    mediaPlayersLoaded = pyqtSignal(object)
    configSaveCompleted = pyqtSignal(object)
    saved = pyqtSignal()

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.s = state
        self.checks: dict[str, QCheckBox] = {}
        self.selected_preset = "movie"
        self.scene_number_sliders: dict[str, QSlider] = {}
        self.scene_switch_checks: dict[str, QCheckBox] = {}
        self.scene_volume_slider: QSlider | None = None
        self.scene_dirty = False
        self.preset_edits = {
            preset: copy.deepcopy(audio_preset_definition(self.s.config, preset))
            for preset, _label, _icon, _kind in AUDIO_PRESET_ORDER
        }
        self.scene_mode = False
        self.settings_mode = "options"
        self.group_edits = audio_group_definitions(self.s.config)
        self.available_media_players = self._cached_media_players()
        self._media_players_loading = False
        self._config_saving = False
        self.mediaPlayersLoaded.connect(self.handle_media_players_loaded)
        self.configSaveCompleted.connect(self._handle_config_save_completed)
        self.setWindowTitle("Audio Settings")
        self.setModal(True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(760, 500)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#07101f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
            QCheckBox {
                color:#f7fbff;
                font-family:Arial;
                font-weight:900;
                font-size:16px;
                spacing:12px;
                padding:6px 4px;
            }
            QCheckBox::indicator {
                width:28px;
                height:28px;
                border-radius:8px;
                border:2px solid rgba(107,226,255,0.42);
                background:rgba(7,13,25,0.86);
            }
            QCheckBox::indicator:checked {
                background:#49e6ff;
                border:2px solid rgba(255,255,255,0.55);
            }
            QCheckBox::indicator:unchecked {
                background:rgba(7,13,25,0.86);
            }
            QSlider::groove:horizontal { height:12px; border-radius:6px; background:rgba(160,170,190,0.23); }
            QSlider::sub-page:horizontal { height:12px; border-radius:6px; background:#49e6ff; }
            QSlider::add-page:horizontal { height:12px; border-radius:6px; background:rgba(110,92,180,0.42); }
            QSlider::handle:horizontal { width:34px; height:34px; margin:-11px 0; border-radius:17px; background:#f8f5ff; border:1px solid rgba(255,255,255,0.42); }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 20)
        root.setSpacing(14)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)
        title = QLabel("Audio Settings")
        title.setFont(font(30, QFont.Black))
        title.setStyleSheet("color:#ffffff;")
        sub = QLabel("Choose controls per room and save what each audio scene button should set.")
        sub.setFont(font(12, QFont.Black))
        sub.setStyleSheet("color:rgba(219,227,244,0.72);")
        title_col.addWidget(title)
        title_col.addWidget(sub)
        header.addLayout(title_col, 1)
        self.options_btn = RoundButton("Audio Options", active=True, min_h=42)
        self.options_btn.setMinimumWidth(150)
        self.options_btn.clicked.connect(lambda checked=False: self.set_settings_mode("options"))
        self.set_scenes_btn = RoundButton("Set Scenes", min_h=42)
        self.set_scenes_btn.setMinimumWidth(140)
        self.set_scenes_btn.clicked.connect(lambda checked=False: self.set_settings_mode("scenes"))
        self.groups_btn = RoundButton("Groups", min_h=42)
        self.groups_btn.setMinimumWidth(120)
        self.groups_btn.clicked.connect(lambda checked=False: self.set_settings_mode("groups"))
        header.addWidget(self.options_btn)
        header.addWidget(self.set_scenes_btn)
        header.addWidget(self.groups_btn)
        cancel = RoundButton("Cancel", min_h=42)
        self.cancel_button = cancel
        cancel.setMinimumWidth(120)
        cancel.clicked.connect(self.reject)
        save = RoundButton("Save", active=True, min_h=42)
        self.save_button = save
        save.setMinimumWidth(140)
        save.clicked.connect(self.save)
        header.addWidget(cancel)
        header.addWidget(save)
        root.addLayout(header)

        self.options_widget = QWidget()
        body = QHBoxLayout(self.options_widget)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        self.controls_panel = GlassPanel(radius=24, strong=True)
        controls_panel = self.controls_panel
        controls_lay = QVBoxLayout(controls_panel)
        controls_lay.setContentsMargins(20, 16, 20, 18)
        controls_lay.setSpacing(7)
        controls_title = QLabel("Visible Controls")
        controls_title.setFont(font(19, QFont.Black))
        controls_lay.addWidget(controls_title)
        hint = QLabel("Turn off controls this room does not have. Disabled items are hidden from Audio and skipped by scene buttons.")
        hint.setWordWrap(True)
        hint.setFont(font(10, QFont.Black))
        hint.setStyleSheet("color:rgba(219,227,244,0.72);")
        controls_lay.addWidget(hint)

        current = audio_ui_config(self.s.config)
        enabled = current.get("enabledControls", {})
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        for idx, (key, label) in enumerate(AUDIO_CONTROL_ORDER):
            row = GlassPanel(radius=18)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(12, 5, 12, 5)
            cb = QCheckBox(label)
            cb.setChecked(bool(enabled.get(key, True)))
            cb.toggled.connect(self._scene_visibility_changed)
            self.checks[key] = cb
            row_lay.addWidget(cb, 1)
            grid.addWidget(row, idx // 2, idx % 2)
        controls_lay.addLayout(grid)
        controls_lay.addStretch(1)
        body.addWidget(controls_panel, 3)

        self.nav_panel = GlassPanel(radius=24, strong=True)
        nav_panel = self.nav_panel
        nav_lay = QVBoxLayout(nav_panel)
        nav_lay.setContentsMargins(20, 16, 20, 18)
        nav_lay.setSpacing(12)
        nav_title = QLabel("Auto-Navigate")
        nav_title.setFont(font(19, QFont.Black))
        nav_lay.addWidget(nav_title)
        self.auto_nav = QCheckBox("Enable auto-navigate")
        self.auto_nav.setChecked(bool(current.get("autoNavigate", False)))
        nav_lay.addWidget(self.auto_nav)
        desc = QLabel(
            "Real music jumps to Audio quickly. If you leave Audio while music keeps playing, the panel returns after 2 minutes without touch. Paused, idle, or stopped audio returns to Thermostat after 2 minutes based on the player state itself. TV audio is ignored."
        )
        desc.setWordWrap(True)
        desc.setFont(font(11, QFont.Black))
        desc.setStyleSheet("color:rgba(219,227,244,0.74); line-height:1.25;")
        nav_lay.addWidget(desc)
        nav_lay.addStretch(1)
        body.addWidget(nav_panel, 2)
        root.addWidget(self.options_widget, 1)

        self.scene_panel = GlassPanel(radius=24, strong=True)
        scene_panel = self.scene_panel
        scene_lay = QVBoxLayout(scene_panel)
        scene_lay.setContentsMargins(20, 14, 20, 14)
        scene_lay.setSpacing(10)
        scene_top = QHBoxLayout()
        scene_title = QLabel("Scene Save Points")
        scene_title.setFont(font(19, QFont.Black))
        self.scene_name = QLabel("")
        self.scene_name.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.scene_name.setFont(font(12, QFont.Black))
        self.scene_name.setStyleSheet("color:#49e6ff;")
        scene_top.addWidget(scene_title)
        scene_top.addStretch(1)
        scene_top.addWidget(self.scene_name)
        scene_lay.addLayout(scene_top)

        scene_buttons = QHBoxLayout()
        scene_buttons.setSpacing(10)
        self.scene_buttons: dict[str, ModernAudioButton] = {}
        for preset, text, icon_key, kind in AUDIO_PRESET_ORDER:
            btn = ModernAudioButton(icon_key, text, kind=kind, min_h=70, holdable=False)
            btn.setMinimumWidth(128)
            btn.clicked.connect(lambda checked=False, p=preset: self.select_scene(p))
            self.scene_buttons[preset] = btn
            scene_buttons.addWidget(btn)
        scene_buttons.addStretch(1)
        scene_lay.addLayout(scene_buttons)

        self.scene_hint = QLabel("Pick a scene tile above, adjust the targets below, then press Save.")
        self.scene_hint.setFont(font(11, QFont.Black))
        self.scene_hint.setStyleSheet("color:rgba(219,227,244,0.72);")
        self.scene_hint.setWordWrap(True)
        scene_lay.addWidget(self.scene_hint)

        self.scene_editor = QWidget()
        self.scene_editor_lay = QHBoxLayout(self.scene_editor)
        self.scene_editor_lay.setContentsMargins(0, 0, 0, 0)
        self.scene_editor_lay.setSpacing(10)
        scene_lay.addWidget(self.scene_editor, 1)
        root.addWidget(scene_panel, 2)

        self.groups_widget = QWidget()
        groups_root = QVBoxLayout(self.groups_widget)
        groups_root.setContentsMargins(0, 0, 0, 0)
        groups_root.setSpacing(10)
        groups_panel = GlassPanel(radius=24, strong=True)
        groups_lay = QVBoxLayout(groups_panel)
        groups_lay.setContentsMargins(20, 16, 20, 18)
        groups_lay.setSpacing(10)
        groups_top = QHBoxLayout()
        groups_title_col = QVBoxLayout()
        groups_title_col.setSpacing(3)
        groups_title = QLabel("Speaker Groups")
        groups_title.setFont(font(20, QFont.Black))
        groups_hint = QLabel("Create named Home Assistant media-player groups for one-touch group / ungroup control on the Audio page.")
        groups_hint.setWordWrap(True)
        groups_hint.setFont(font(10, QFont.Black))
        groups_hint.setStyleSheet("color:rgba(219,227,244,0.72);")
        groups_title_col.addWidget(groups_title)
        groups_title_col.addWidget(groups_hint)
        groups_top.addLayout(groups_title_col, 1)
        self.refresh_players_btn = RoundButton("Refresh Players", min_h=40)
        self.refresh_players_btn.setMinimumWidth(150)
        self.refresh_players_btn.clicked.connect(lambda checked=False: self.refresh_media_players(True))
        self.add_group_btn = RoundButton("+ Add Group", active=True, min_h=40)
        self.add_group_btn.setMinimumWidth(140)
        self.add_group_btn.clicked.connect(lambda checked=False: self.edit_audio_group(None))
        groups_top.addWidget(self.refresh_players_btn)
        groups_top.addWidget(self.add_group_btn)
        groups_lay.addLayout(groups_top)

        self.groups_status = QLabel("")
        self.groups_status.setWordWrap(True)
        self.groups_status.setFont(font(9, QFont.Black))
        self.groups_status.setStyleSheet("color:#9fb0c8;")
        groups_lay.addWidget(self.groups_status)

        group_scroll = QScrollArea()
        group_scroll.setWidgetResizable(True)
        group_scroll.setFrameShape(QFrame.NoFrame)
        self.groups_list_body = QWidget()
        self.groups_list_lay = QVBoxLayout(self.groups_list_body)
        self.groups_list_lay.setContentsMargins(0, 0, 0, 0)
        self.groups_list_lay.setSpacing(8)
        group_scroll.setWidget(self.groups_list_body)
        groups_lay.addWidget(group_scroll, 1)
        groups_root.addWidget(groups_panel, 1)
        root.addWidget(self.groups_widget, 2)

        self.build_scene_editor()
        self.render_audio_groups()
        self.set_settings_mode("options")
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    def set_scene_mode(self, enabled: bool):
        self.set_settings_mode("scenes" if enabled else "options")

    def set_settings_mode(self, mode: str):
        mode = str(mode or "options").strip().lower()
        if mode not in {"options", "scenes", "groups"}:
            mode = "options"
        self.settings_mode = mode
        self.scene_mode = mode == "scenes"
        if hasattr(self, "options_widget"):
            self.options_widget.setVisible(mode == "options")
        if hasattr(self, "scene_panel"):
            self.scene_panel.setVisible(mode == "scenes")
        if hasattr(self, "groups_widget"):
            self.groups_widget.setVisible(mode == "groups")
        if hasattr(self, "options_btn"):
            self.options_btn.setActive(mode == "options")
        if hasattr(self, "set_scenes_btn"):
            self.set_scenes_btn.setActive(mode == "scenes")
        if hasattr(self, "groups_btn"):
            self.groups_btn.setActive(mode == "groups")
        if mode == "groups":
            self.render_audio_groups()
            self.refresh_media_players(False)
        self.fit_to_screen()

    def _cached_media_players(self) -> list[dict]:
        ha = self.s.ha()
        values = ha.get("mediaPlayerEntities") or (ha.get("audioAvailableEntities") or {}).get("mediaPlayers") or []
        players: list[dict] = []
        seen: set[str] = set()
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if entity_id.startswith("media_player.") and entity_id not in seen:
                seen.add(entity_id)
                players.append({"entityId": entity_id, "name": item.get("name") or item.get("friendly_name") or entity_id})
        selected = str(ha.get("selectedMediaPlayerId") or "").strip()
        if selected.startswith("media_player.") and selected not in seen:
            selected_name = nested_get(ha, "mediaPlayerEntity", "name", default="") or selected
            players.append({"entityId": selected, "name": selected_name})
            seen.add(selected)
        for group in self.group_edits:
            for entity_id in group.get("members") or []:
                if entity_id not in seen:
                    players.append({"entityId": entity_id, "name": entity_id.split(".", 1)[-1].replace("_", " ").title()})
                    seen.add(entity_id)
        players.sort(key=lambda item: str(item.get("name") or item.get("entityId") or "").lower())
        return players

    def refresh_media_players(self, notify: bool = False):
        if self._media_players_loading:
            return
        self._media_players_loading = True
        if hasattr(self, "refresh_players_btn"):
            self.refresh_players_btn.setEnabled(False)
        if hasattr(self, "groups_status"):
            self.groups_status.setText("Refreshing Home Assistant media players…")

        def worker():
            try:
                data = self.s.api.post("/api/ha/media_players", self.s.ha_payload())
                self.mediaPlayersLoaded.emit({"players": data.get("players") or [], "error": "", "notify": notify})
            except Exception as exc:
                self.mediaPlayersLoaded.emit({"players": [], "error": str(exc), "notify": notify})

        threading.Thread(target=worker, name="audio-settings-media-players", daemon=True).start()

    def handle_media_players_loaded(self, result: object):
        self._media_players_loading = False
        if hasattr(self, "refresh_players_btn"):
            self.refresh_players_btn.setEnabled(True)
        info = result if isinstance(result, dict) else {}
        error = str(info.get("error") or "")
        players = info.get("players") if isinstance(info.get("players"), list) else []
        if players:
            self.available_media_players = players
            try:
                ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                ha["mediaPlayerEntities"] = copy.deepcopy(players)
            except Exception:
                pass
        elif not self.available_media_players:
            self.available_media_players = self._cached_media_players()
        if error:
            self.groups_status.setText(f"Could not refresh Home Assistant players: {error}")
        else:
            self.groups_status.setText(f"{len(self.available_media_players)} media player(s) available.")

    def render_audio_groups(self):
        if not hasattr(self, "groups_list_lay"):
            return
        self._clear_layout(self.groups_list_lay)
        if not self.group_edits:
            empty = QLabel("No groups created yet. Tap + Add Group to choose two or more media players.")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            empty.setFont(font(12, QFont.Black))
            empty.setStyleSheet("color:rgba(219,227,244,0.68); padding:28px;")
            self.groups_list_lay.addWidget(empty)
            self.groups_list_lay.addStretch(1)
            return
        for index, group in enumerate(self.group_edits):
            card = GlassPanel(radius=18)
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 9, 12, 9)
            row.setSpacing(10)
            detail = QVBoxLayout()
            detail.setSpacing(2)
            name = QLabel(str(group.get("name") or "Audio Group"))
            name.setFont(font(14, QFont.Black))
            members = list(group.get("members") or [])
            member_text = "  ·  ".join(entity_id.split(".", 1)[-1].replace("_", " ").title() for entity_id in members)
            member_label = QLabel(member_text)
            member_label.setWordWrap(True)
            member_label.setFont(font(9, QFont.Black))
            member_label.setStyleSheet("color:rgba(219,227,244,0.66);")
            detail.addWidget(name)
            detail.addWidget(member_label)
            row.addLayout(detail, 1)
            edit = RoundButton("Edit", active=True, min_h=38)
            delete = RoundButton("Delete", min_h=38)
            edit.setMinimumWidth(92)
            delete.setMinimumWidth(96)
            edit.clicked.connect(lambda checked=False, i=index: self.edit_audio_group(i))
            delete.clicked.connect(lambda checked=False, i=index: self.delete_audio_group(i))
            row.addWidget(edit)
            row.addWidget(delete)
            self.groups_list_lay.addWidget(card)
        self.groups_list_lay.addStretch(1)

    def edit_audio_group(self, index: int | None):
        players = self.available_media_players or self._cached_media_players()
        if not players:
            QMessageBox.warning(self, "Audio Groups", "No Home Assistant media players are available yet. Tap Refresh Players and try again.")
            return
        existing = self.group_edits[index] if isinstance(index, int) and 0 <= index < len(self.group_edits) else None
        preferred = str(self.s.ha().get("selectedMediaPlayerId") or "").strip()
        dlg = AudioGroupEditorDialog(players, existing, preferred, self)
        if dlg.exec_() != QDialog.Accepted or not isinstance(dlg.result_group, dict):
            return
        if existing is None:
            self.group_edits.append(copy.deepcopy(dlg.result_group))
        else:
            self.group_edits[index] = copy.deepcopy(dlg.result_group)
        self.render_audio_groups()
        self.groups_status.setText("Group changes are ready. Press Save to keep them.")

    def delete_audio_group(self, index: int):
        if not (0 <= index < len(self.group_edits)):
            return
        group = self.group_edits[index]
        name = str(group.get("name") or "this group")
        if QMessageBox.question(self, "Delete Audio Group", f"Delete {name}?") != QMessageBox.Yes:
            return
        self.group_edits.pop(index)
        self.render_audio_groups()
        self.groups_status.setText("Group removed. Press Save to keep the change.")

    def _clear_layout(self, layout: QHBoxLayout | QVBoxLayout | QGridLayout):
        while layout.count():
            item = layout.takeAt(0)
            child_layout = item.layout()
            widget = item.widget()
            if child_layout is not None:
                self._clear_layout(child_layout)
            if widget is not None:
                widget.deleteLater()

    def _control_record(self, name: str) -> dict:
        controls = nested_get(self.s.config, "integrations", "homeAssistant", "audioControlEntities", default={}) or {}
        value = controls.get(name) if isinstance(controls, dict) else None
        if name == "music_surround" and not value and isinstance(controls, dict):
            value = controls.get("musicSurround")
        if isinstance(value, dict):
            return value
        if value:
            entity_id = str(value)
            domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
            return {"entityId": entity_id, "name": entity_id, "domain": domain}
        return {}

    def _number_range(self, name: str) -> tuple[float, float, float]:
        record = self._control_record(name)
        try:
            low = float(record.get("min", 0))
            high = float(record.get("max", 100))
            step = float(record.get("step", 1) or 1)
        except Exception:
            low, high, step = 0.0, 100.0, 1.0
        if high <= low:
            high = low + 100.0
        return low, high, step

    def _number_text(self, value) -> str:
        if value is None or value == "":
            return "--"
        try:
            number = float(value)
            if abs(number - round(number)) < 0.001:
                return str(int(round(number)))
            return f"{number:.1f}"
        except Exception:
            return str(value)[:12]

    def _target_to_actual(self, name: str, target):
        low, high, step = self._number_range(name)
        if isinstance(target, str):
            key = target.strip().lower()
            if key == "max":
                raw = high
            elif key == "min":
                raw = low
            else:
                try:
                    raw = float(key)
                except Exception:
                    raw = float(self._control_record(name).get("value", self._control_record(name).get("state", low)))
        elif target is None:
            record = self._control_record(name)
            try:
                raw = float(record.get("value", record.get("state", (low + high) / 2)))
            except Exception:
                raw = (low + high) / 2
        else:
            raw = float(target)
        raw = float(clamp(raw, low, high))
        if step > 0:
            raw = round((raw - low) / step) * step + low
            raw = float(clamp(raw, low, high))
        if abs(raw - round(raw)) < 0.001:
            return int(round(raw))
        return round(raw, 2)

    def _actual_to_slider(self, name: str, value) -> int:
        low, high, _step = self._number_range(name)
        try:
            raw = float(self._target_to_actual(name, value))
        except Exception:
            raw = (low + high) / 2
        return int(clamp(round(((raw - low) / (high - low)) * 100), 0, 100))

    def _slider_to_actual(self, name: str, slider_value: int):
        low, high, step = self._number_range(name)
        raw = low + (float(slider_value) / 100.0) * (high - low)
        if step > 0:
            raw = round((raw - low) / step) * step + low
            raw = float(clamp(raw, low, high))
        if abs(raw - round(raw)) < 0.001:
            return int(round(raw))
        return round(raw, 2)

    def _scene_visibility_changed(self, _checked=False):
        self.store_scene_editor_values()
        self.build_scene_editor()

    def select_scene(self, preset: str):
        if preset == self.selected_preset:
            return
        self.store_scene_editor_values()
        self.selected_preset = preset
        self.build_scene_editor()

    def build_scene_editor(self):
        self._clear_layout(self.scene_editor_lay)
        self.scene_number_sliders = {}
        self.scene_switch_checks = {}
        self.scene_volume_slider = None
        definition = self.preset_edits.get(self.selected_preset) or audio_preset_definition(self.s.config, self.selected_preset)
        label = str(definition.get("label") or dict((p, l) for p, l, _i, _k in AUDIO_PRESET_ORDER).get(self.selected_preset, "Scene"))
        self.scene_name.setText(f"Editing {label}")
        for key, btn in self.scene_buttons.items():
            btn.setActive(key == self.selected_preset)

        volume_card = GlassPanel(radius=18)
        volume_lay = QVBoxLayout(volume_card)
        volume_lay.setContentsMargins(12, 8, 12, 10)
        volume_lay.setSpacing(6)
        volume_value = definition.get("volume")
        try:
            volume_pct = int(clamp(round(float(volume_value)), 0, 100)) if volume_value is not None else 40
        except Exception:
            volume_pct = 40
        volume_title = QLabel("Volume")
        volume_title.setAlignment(Qt.AlignCenter)
        volume_title.setFont(font(10, QFont.Black))
        self.scene_volume_label = QLabel(f"{volume_pct}%")
        self.scene_volume_label.setAlignment(Qt.AlignCenter)
        self.scene_volume_label.setFont(font(11, QFont.Black))
        self.scene_volume_label.setStyleSheet("color:#49e6ff;")
        vslider = QSlider(Qt.Horizontal)
        vslider.setRange(0, 100)
        vslider.setValue(volume_pct)
        vslider.setMinimumWidth(145)
        vslider.valueChanged.connect(lambda value: (self.scene_volume_label.setText(f"{int(value)}%"), setattr(self, "scene_dirty", True)))
        volume_lay.addWidget(volume_title)
        volume_lay.addWidget(self.scene_volume_label)
        volume_lay.addWidget(vslider)
        self.scene_volume_slider = vslider
        self.scene_editor_lay.addWidget(volume_card, 2)

        numbers = definition.get("numbers") if isinstance(definition.get("numbers"), dict) else {}
        for key, title in AUDIO_NUMBER_CONTROL_ORDER:
            if not self.checks.get(key, QCheckBox()).isChecked():
                continue
            actual = self._target_to_actual(key, numbers.get(key))
            card = GlassPanel(radius=18)
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 8, 12, 10)
            lay.setSpacing(5)
            title_label = QLabel(title)
            title_label.setAlignment(Qt.AlignCenter)
            title_label.setFont(font(9 if key == "music_surround" else 10, QFont.Black))
            value_label = QLabel(self._number_text(actual))
            value_label.setAlignment(Qt.AlignCenter)
            value_label.setFont(font(11, QFont.Black))
            value_label.setStyleSheet("color:#49e6ff;")
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(self._actual_to_slider(key, actual))
            slider.setMinimumWidth(118)
            slider.valueChanged.connect(lambda value, k=key, lbl=value_label: (lbl.setText(self._number_text(self._slider_to_actual(k, value))), setattr(self, "scene_dirty", True)))
            lay.addWidget(title_label)
            lay.addWidget(value_label)
            lay.addWidget(slider)
            self.scene_number_sliders[key] = slider
            self.scene_editor_lay.addWidget(card, 2)

        switches = definition.get("switches") if isinstance(definition.get("switches"), dict) else {}
        switch_card = GlassPanel(radius=18)
        switch_lay = QVBoxLayout(switch_card)
        switch_lay.setContentsMargins(12, 8, 12, 10)
        switch_lay.setSpacing(4)
        switch_title = QLabel("Scene Toggles")
        switch_title.setFont(font(10, QFont.Black))
        switch_lay.addWidget(switch_title)
        for key, title in AUDIO_SWITCH_CONTROL_ORDER:
            if not self.checks.get(key, QCheckBox()).isChecked():
                continue
            cb = QCheckBox(title)
            cb.setFont(font(10, QFont.Black))
            cb.setChecked(str(switches.get(key, "off")).lower() == "on")
            cb.toggled.connect(lambda _checked=False: setattr(self, "scene_dirty", True))
            self.scene_switch_checks[key] = cb
            switch_lay.addWidget(cb)
        switch_lay.addStretch(1)
        self.scene_editor_lay.addWidget(switch_card, 2)
        self.scene_dirty = False

    def store_scene_editor_values(self):
        if self.scene_volume_slider is None or not getattr(self, "scene_dirty", False):
            return
        previous = copy.deepcopy(self.preset_edits.get(self.selected_preset) or audio_preset_definition(self.s.config, self.selected_preset))
        previous["volume"] = int(self.scene_volume_slider.value())
        previous["numbers"] = {key: self._slider_to_actual(key, slider.value()) for key, slider in self.scene_number_sliders.items()}
        previous["switches"] = {key: ("on" if cb.isChecked() else "off") for key, cb in self.scene_switch_checks.items()}
        self.preset_edits[self.selected_preset] = previous
        self.scene_dirty = False

    def save(self):
        if self._config_saving:
            return
        try:
            self.store_scene_editor_values()
            audio = self.s.config.setdefault("audio", {})
            audio["enabledControls"] = {key: bool(cb.isChecked()) for key, cb in self.checks.items()}
            audio["autoNavigate"] = bool(self.auto_nav.isChecked())
            audio["presets"] = copy.deepcopy(self.preset_edits)
            audio["groups"] = copy.deepcopy(self.group_edits)
            snapshot = copy.deepcopy(self.s.config)
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return

        self._config_saving = True
        self.save_button.setEnabled(False)
        self.save_button.setText("Saving…")
        self.cancel_button.setEnabled(False)

        def worker():
            try:
                result = self.s.api.save_config(snapshot)
                payload = {"result": result, "error": None}
            except Exception as exc:
                payload = {"result": None, "error": str(exc)}
            try:
                self.configSaveCompleted.emit(payload)
            except RuntimeError:
                pass

        threading.Thread(target=worker, name="audio-settings-save", daemon=True).start()

    def _handle_config_save_completed(self, info: object):
        self._config_saving = False
        data = info if isinstance(info, dict) else {}
        error = str(data.get("error") or "")
        if error:
            self.save_button.setEnabled(True)
            self.save_button.setText("Save")
            self.cancel_button.setEnabled(True)
            QMessageBox.warning(self, "Save failed", error)
            return
        record = data.get("result")
        if isinstance(record, dict) and isinstance(record.get("config"), dict):
            self.s.config = record.get("config")
        self.saved.emit()
        self.accept()




class SettingsDialog(QDialog):
    saved = pyqtSignal()
    settingsAsyncCompleted = pyqtSignal(object)
    thermostatUpdateCompleted = pyqtSignal(object)
    settingsSaveCompleted = pyqtSignal(object)
    tempSensorTelemetryLoaded = pyqtSignal(object)
    tempSensorSaveCompleted = pyqtSignal(object)
    houseSyncCompleted = pyqtSignal(object)
    brightnessEntitiesLoaded = pyqtSignal(object)

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.s = state
        self.setWindowTitle("Comfort Setup")
        self.setModal(True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(760, 500)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog {
                background:#07101f;
                color:#f7fbff;
            }
            QLabel {
                color:#f7fbff;
                font-family:Arial;
            }
            QLineEdit {
                background:rgba(7,13,25,0.96);
                color:#ffffff;
                border:1px solid rgba(100,229,255,0.30);
                border-radius:9px;
                padding:5px 8px;
                font-weight:900;
                font-size:12px;
                min-height:22px;
            }
            QScrollArea {
                background:transparent;
                border:0;
            }
            QScrollArea > QWidget > QWidget {
                background:transparent;
            }
            QScrollBar:vertical {
                background:rgba(255,255,255,0.05);
                width:12px;
                margin:0;
                border-radius:6px;
            }
            QScrollBar::handle:vertical {
                background:rgba(70,223,255,0.72);
                min-height:42px;
                border-radius:6px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height:0;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 4, 5, 5)
        root.setSpacing(3)

        header = QHBoxLayout()
        header.setSpacing(5)
        title = QLabel("<span style='color:#46e8ff; letter-spacing:2px; font-size:8px; font-weight:900'>PANEL SETTINGS</span><br><span style='font-size:20px; font-weight:1000; color:#ffffff'>Comfort Setup</span>")
        title.setTextFormat(Qt.RichText)
        title.setMinimumHeight(34)
        title.setMaximumHeight(38)
        header.addWidget(title)
        header.addStretch(1)
        self.hardware = RoundButton("Hardware", active=True, min_h=32)
        self.history = RoundButton("History", active=True, min_h=32)
        self.bottom_save = RoundButton("Save Settings", active=True, min_h=32)
        self.done = RoundButton("Done", active=True, min_h=32)
        self.hardware.setMinimumWidth(110)
        self.history.setMinimumWidth(96)
        self.bottom_save.setMinimumWidth(142)
        self.done.setMinimumWidth(92)
        header.addWidget(self.hardware)
        header.addWidget(self.history)
        header.addWidget(self.bottom_save)
        header.addWidget(self.done)
        root.addLayout(header)

        section_hint = QLabel("Tap a section to view and edit its settings.")
        section_hint.setFont(font(9, QFont.Bold))
        section_hint.setStyleSheet("color:#9fb0c8; background:transparent; border:0; padding:1px 3px 4px 3px;")
        root.addWidget(section_hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        scroll.viewport().setStyleSheet("background:transparent;")
        self.grid = QGridLayout(body)
        self.grid.setSpacing(8)
        self.grid.setContentsMargins(4, 2, 4, 6)
        for col in range(4):
            self.grid.setColumnStretch(col, 1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # The main Comfort Setup screen is intentionally only a section index.
        # Each section keeps its real controls alive in hidden storage and is
        # temporarily moved into a centered modal when its header is tapped.
        # This preserves every existing signal, state reference, and save path.
        self._section_entries: list[dict] = []
        self._section_storage = QWidget(self)
        self._section_storage.hide()
        self._active_section_dialog: QDialog | None = None

        self.controls: dict[str, QLabel] = {}
        self.value_control_widgets: dict[str, dict] = {}
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.timeout.connect(self.push_pending_settings)
        self._pending_settings_changes: dict | None = None
        self._pending_settings_quiet = True
        self._settings_saving = False
        self._settings_dirty = False
        self._jarvis_dirty = False
        self._display_settings_dirty = False
        self._settings_update_seq = 0
        self._settings_async_jobs: dict[str, tuple[Callable | None, Callable | None]] = {}
        self._settings_write_jobs = 0
        self.settingsAsyncCompleted.connect(self._handle_settings_async_completed)

        temp_cfg = nested_get(self.s.config, "hardware", "temperatureSensors", default={})
        temp_cfg = temp_cfg if isinstance(temp_cfg, dict) else {}

        def temp_number(key: str, default: float) -> float:
            try:
                return round(float(temp_cfg.get(key, default)), 1)
            except (TypeError, ValueError):
                return round(float(default), 1)

        self.source_temp_offsets = {
            1: temp_number("sensor1OffsetF", -5.2),
            2: temp_number("sensor2OffsetF", -7.3),
        }
        self.source_temp_thresholds = {
            "maxDisagreementF": temp_number("maxDisagreementF", 3.0),
            "maxJumpF": temp_number("maxJumpF", 15.0),
        }
        try:
            self.source_temp_primary_sensor = int(temp_cfg.get("primarySensor", 2))
        except (TypeError, ValueError):
            self.source_temp_primary_sensor = 2
        if self.source_temp_primary_sensor not in (1, 2):
            self.source_temp_primary_sensor = 2
        self.source_temp_primary_buttons: dict[int, RoundButton] = {}
        self.source_temp_sensor_labels: dict[int, dict[str, QLabel]] = {}
        self.source_temp_offset_labels: dict[int, QLabel] = {}
        self.source_temp_threshold_labels: dict[str, QLabel] = {}
        self._source_temp_loading = False
        self._source_temp_saving = False
        self._source_temp_config_loaded = False
        self._source_temp_dirty = False
        self.screen_setting_values = screen_display_settings(self.s.config)
        self._last_settings_error = ""
        self.thermostatUpdateCompleted.connect(self.handle_settings_update_completed)
        self.settingsSaveCompleted.connect(self.handle_save_all_completed)
        self.tempSensorTelemetryLoaded.connect(self.handle_source_temp_telemetry_loaded)
        self.tempSensorSaveCompleted.connect(self.handle_source_temp_save_completed)
        self.houseSyncCompleted.connect(self.handle_house_sync_completed)
        self.brightnessEntitiesLoaded.connect(self.handle_brightness_entities_loaded)
        self._brightness_entities_loading = False
        self.house_sync_source: dict | None = None
        self.house_sync_running = False
        self.build()
        self.finalize_section_index()
        self.done.clicked.connect(self.close_settings)
        self.hardware.clicked.connect(self.show_hardware)
        self.history.clicked.connect(self.show_history)
        self.bottom_save.clicked.connect(self.save_all)
        self.source_temp_refresh_timer = QTimer(self)
        self.source_temp_refresh_timer.setInterval(3000)
        self.source_temp_refresh_timer.timeout.connect(self.refresh_source_temp_sensors)
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)


    def run_settings_async(
        self,
        name: str,
        worker: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        job_id = f"{name}-{time.monotonic_ns()}"
        self._settings_async_jobs[job_id] = (on_success, on_error)

        def target():
            try:
                result = worker()
                payload = {"id": job_id, "result": result, "error": None}
            except Exception as exc:
                payload = {"id": job_id, "result": None, "error": str(exc)}
            try:
                self.settingsAsyncCompleted.emit(payload)
            except RuntimeError:
                pass

        threading.Thread(target=target, name=f"settings-{name}", daemon=True).start()
        return job_id

    def _handle_settings_async_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        callbacks = self._settings_async_jobs.pop(str(data.get("id") or ""), None)
        if not callbacks:
            return
        on_success, on_error = callbacks
        error = str(data.get("error") or "")
        if error:
            if on_error:
                on_error(error)
            return
        if on_success:
            on_success(data.get("result"))


    def _refresh_settings_write_controls(self):
        busy = bool(getattr(self, "_settings_write_jobs", 0) or getattr(self, "_settings_saving", False))
        if hasattr(self, "bottom_save"):
            self.bottom_save.setEnabled(not busy and not getattr(self, "house_sync_running", False))
            if not getattr(self, "_settings_saving", False):
                self.bottom_save.setText("Saving…" if busy else "Save Settings")
        if hasattr(self, "done"):
            self.done.setEnabled(not busy and not getattr(self, "house_sync_running", False))

    def run_settings_write(
        self,
        name: str,
        worker: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ):
        self._settings_write_jobs = int(getattr(self, "_settings_write_jobs", 0) or 0) + 1
        self._refresh_settings_write_controls()

        def finish_success(result):
            self._settings_write_jobs = max(0, int(getattr(self, "_settings_write_jobs", 0) or 0) - 1)
            self._refresh_settings_write_controls()
            if on_success:
                on_success(result)

        def finish_error(error):
            self._settings_write_jobs = max(0, int(getattr(self, "_settings_write_jobs", 0) or 0) - 1)
            self._refresh_settings_write_controls()
            if on_error:
                on_error(error)

        return self.run_settings_async(name, worker, finish_success, finish_error)


    def load_ha_entities_async(
        self,
        name: str,
        domains: list[str],
        on_success: Callable[[list[dict]], None],
        on_error: Callable[[str], None] | None = None,
    ):
        payload = self.s.ha_payload({"domains": list(domains)})

        def worker():
            data = self.s.api.post("/api/ha/entities", payload)
            return data.get("entities") or []

        self.run_settings_async(name, worker, on_success, on_error)

    def settings_panel(self, radius: int = 14) -> QFrame:
        p = QFrame()
        p.setStyleSheet(f"""
            QFrame {{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(27,39,59,0.96),
                    stop:1 rgba(8,15,29,0.96));
                border:1px solid rgba(111,139,166,0.38);
                border-radius:{radius}px;
            }}
        """)
        return p

    def value_control_style(self, popped: bool = False) -> str:
        if popped:
            return """
                QFrame {
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                        stop:0 rgba(46,71,102,0.98),
                        stop:1 rgba(11,24,47,0.98));
                    border:2px solid rgba(85,240,255,0.78);
                    border-radius:12px;
                }
            """
        return """
            QFrame {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(27,39,59,0.96),
                    stop:1 rgba(8,15,29,0.96));
                border:1px solid rgba(111,139,166,0.38);
                border-radius:9px;
            }
        """

    def pop_value_control(self, key: str):
        """Show tap feedback without changing the active touch target geometry.

        On the X11 touchscreen, resizing/restyling the pressed widget before the
        release event can leave a stale pointer grab. Keep the panel/button
        geometry fixed and apply this feedback only after clicked() returns.
        """
        meta = self.value_control_widgets.get(key) or {}
        panel = meta.get("panel")
        val = meta.get("value")
        if not panel or not val:
            return
        seq = int(meta.get("popSeq") or 0) + 1
        meta["popSeq"] = seq
        panel.setStyleSheet(self.value_control_style(True))
        val.setFont(font(13, QFont.Black))
        val.setStyleSheet("color:#ffffff; background:rgba(85,240,255,0.16); border:1px solid rgba(85,240,255,0.45); border-radius:10px; padding:2px 6px;")
        val.repaint()
        QTimer.singleShot(900, lambda k=key, s=seq: self.reset_value_control(k, s))

    def reset_value_control(self, key: str, seq: int | None = None):
        meta = self.value_control_widgets.get(key) or {}
        if seq is not None and int(meta.get("popSeq") or 0) != int(seq):
            return
        panel = meta.get("panel")
        val = meta.get("value")
        if panel:
            panel.setStyleSheet(self.value_control_style(False))
        if val:
            val.setFont(font(11, QFont.Black))
            val.setStyleSheet("color:#ffffff; background:transparent; border:0;")
            val.repaint()

    def mark_settings_dirty(self):
        self._settings_dirty = True
        self._last_settings_error = ""
        self.s.pause_status_refresh(8.0)

    def value_control(self, key: str, label: str, value, low=None, high=None, suffix="°") -> QFrame:
        panel = QFrame()
        panel.setMinimumHeight(44)
        panel.setMaximumHeight(50)
        panel.setStyleSheet(self.value_control_style(False))
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(7, 4, 7, 4)
        lay.setSpacing(6)
        lab = QLabel(label)
        lab.setFont(font(8, QFont.Black))
        lab.setStyleSheet("color:#e7efff; background:transparent; border:0;")
        lab.setWordWrap(False)
        minus = RoundButton("−", min_h=34)
        minus.setFixedSize(40, 34)
        minus.setFont(font(13, QFont.Black))
        val = QLabel(str(value) + suffix)
        val.setAlignment(Qt.AlignCenter)
        val.setMinimumWidth(56)
        val.setFont(font(11, QFont.Black))
        val.setStyleSheet("color:#ffffff; background:transparent; border:0;")
        plus = RoundButton("+", min_h=34)
        plus.setFixedSize(40, 34)
        plus.setFont(font(13, QFont.Black))
        lay.addWidget(lab, 1)
        lay.addWidget(minus)
        lay.addWidget(val)
        lay.addWidget(plus)
        self.controls[key] = val
        self.value_control_widgets[key] = {"panel": panel, "minus": minus, "plus": plus, "value": val, "popSeq": 0}
        minus.clicked.connect(lambda checked=False: self.adjust_value(key, -1, low, high, suffix))
        plus.clicked.connect(lambda checked=False: self.adjust_value(key, 1, low, high, suffix))
        return panel

    def build_value(self, key: str, label: str, value, row: int, col: int, low=None, high=None, suffix="°"):
        self.grid.addWidget(self.value_control(key, label, value, low, high, suffix), row, col)

    def section_grid(self, section: QFrame, columns: int = 2) -> QGridLayout:
        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(4)
        g.setVerticalSpacing(4)
        for col in range(max(1, int(columns))):
            g.setColumnStretch(col, 1)
        section.layout().addLayout(g)
        return g

    def add_section_value(self, layout: QGridLayout, key: str, label: str, value, row: int, col: int, low=None, high=None, suffix="°", colspan: int = 1):
        layout.addWidget(self.value_control(key, label, value, low, high, suffix), row, col, 1, colspan)

    def section_description(self, title: str) -> str:
        descriptions = {
            "Auto Away / Home": "Away temperatures and the people used for occupancy changes",
            "Range": "Minimum and maximum heating and cooling setpoints",
            "Safety / Mode Switches": "Safety limits, seasonal thresholds, and heat/cool lockouts",
            "Changeover / Fan": "Mode-change delays, fan run-on time, and fan operation",
            "Temperature Differential": "Degrees past the setpoint before heating or cooling restarts",
            "Minimum Runtime": "Minimum equipment on-time and off-time for each cycle",
            "Internal / External Sources": "Choose sources and configure both onboard temperature sensors",
            "Outside Temperature": "Home Assistant source for outdoor temperature and weather",
            "Sync": "Thermostat control syncing and one-time room setup copying",
            "Intimacy Hold": "Temperature used by the six-hour two-person hold button",
            "Person Tracking": "People displayed on the main thermostat screen",
            "Doors / Comfort Pause": "Door sensor and delay before heating or cooling pauses",
            "Security Codes": "Alarm disarm and settings-access PINs",
            "Thermostat Unit": "The name used to identify this thermostat",
            "Screen Settings": "Rotation, compact auto on/off, and automatic brightness rules",
            "Audio Settings": "Create media-player groups and configure Audio-page controls",
            "Jarvis": "Home Assistant speech volume, response, and screen-display controls",
            "Device Internet": "Choose device network-access switches for the main-screen iPad button",
            "Alexa Lockout": "Choose which Alexa devices are blocked while this screen is locked",
        }
        return descriptions.get(str(title or ""), "Open this section to view its settings")

    def jarvis_voice_defaults(self) -> dict:
        return {
            "enabled": True,
            "speak": True,
            "funMode": False,
            "playfulReplies": True,
            "continueConversation": True,
            "showResponseText": True,
            "responseHoldSeconds": 2,
            "announcementVolumePercent": 45,
        }

    def jarvis_voice_config(self, create: bool = False) -> dict:
        config = self.s.config if isinstance(self.s.config, dict) else {}
        if config is not self.s.config:
            self.s.config = config
        integrations = config.get("integrations")
        if not isinstance(integrations, dict):
            if not create:
                return {}
            integrations = {}
            config["integrations"] = integrations
        home_assistant = integrations.get("homeAssistant")
        if not isinstance(home_assistant, dict):
            if not create:
                return {}
            home_assistant = {}
            integrations["homeAssistant"] = home_assistant
        voice = home_assistant.get("voiceAssistant")
        if not isinstance(voice, dict):
            if not create:
                return {}
            voice = {}
            home_assistant["voiceAssistant"] = voice
        if create:
            for key, value in self.jarvis_voice_defaults().items():
                voice.setdefault(key, copy.deepcopy(value))
        return voice

    def jarvis_bool_value(self, key: str) -> bool:
        defaults = self.jarvis_voice_defaults()
        voice = self.jarvis_voice_config(False)
        return bool(voice.get(key, defaults.get(key, False)))

    def jarvis_toggle_text(self, key: str, label: str) -> str:
        return f"{label}: {'ON' if self.jarvis_bool_value(key) else 'OFF'}"

    def toggle_jarvis_setting(self, key: str, label: str):
        voice = self.jarvis_voice_config(True)
        voice[key] = not self.jarvis_bool_value(key)
        button = (getattr(self, "jarvis_toggle_buttons", {}) or {}).get(key)
        if button is not None:
            button.setText(self.jarvis_toggle_text(key, label))
            if hasattr(button, "setActive"):
                button.setActive(bool(voice.get(key)))
            button.repaint()
        self._jarvis_dirty = True
        self.mark_settings_dirty()

    def add_jarvis_toggle(self, layout: QGridLayout, key: str, label: str, row: int, col: int):
        button = RoundButton(
            self.jarvis_toggle_text(key, label),
            active=self.jarvis_bool_value(key),
            min_h=42,
        )
        button.clicked.connect(lambda checked=False, k=key, l=label: self.toggle_jarvis_setting(k, l))
        self.jarvis_toggle_buttons[key] = button
        layout.addWidget(button, row, col)

    def apply_jarvis_values_to_config(self):
        if not hasattr(self, "jarvis_toggle_buttons"):
            return
        voice = self.jarvis_voice_config(True)
        if "jarvisAnnouncementVolumePercent" in self.controls:
            voice["announcementVolumePercent"] = max(1, min(100, self.val_number("jarvisAnnouncementVolumePercent")))
        if "jarvisResponseHoldSeconds" in self.controls:
            voice["responseHoldSeconds"] = max(0, min(15, self.val_number("jarvisResponseHoldSeconds")))

    def section_header_button(self, title: str) -> QPushButton:
        description = self.section_description(title)
        button = QPushButton()
        button.setCursor(Qt.PointingHandCursor)
        button.setAccessibleName(title)
        button.setAccessibleDescription(description)
        button.setMinimumHeight(62)
        button.setMaximumHeight(68)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.setStyleSheet("""
            QPushButton {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 rgba(25,39,64,0.98),
                    stop:0.72 rgba(12,24,44,0.98),
                    stop:1 rgba(8,18,34,0.98));
                border:1px solid rgba(111,139,166,0.42);
                border-radius:14px;
                padding:0;
            }
            QPushButton:hover {
                border:1px solid rgba(85,240,255,0.72);
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 rgba(31,56,86,0.99),
                    stop:1 rgba(10,27,49,0.99));
            }
            QPushButton:pressed {
                border:2px solid rgba(85,240,255,0.92);
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 rgba(26,73,101,0.99),
                    stop:1 rgba(10,34,58,0.99));
            }
        """)
        row_layout = QHBoxLayout(button)
        row_layout.setContentsMargins(16, 7, 13, 7)
        row_layout.setSpacing(8)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        title_label = QLabel(title)
        title_label.setFont(font(12, QFont.Black))
        title_label.setStyleSheet("color:#f7fbff; background:transparent; border:0;")
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        subtitle_label = QLabel(f"({description})")
        subtitle_label.setWordWrap(False)
        subtitle_label.setFont(font(7, QFont.Bold))
        subtitle_label.setStyleSheet("color:#93a9c5; background:transparent; border:0;")
        subtitle_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text_layout.addWidget(title_label)
        text_layout.addWidget(subtitle_label)

        arrow = QLabel("›")
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setFixedWidth(24)
        arrow.setFont(font(23, QFont.Black))
        arrow.setStyleSheet("color:#55f0ff; background:transparent; border:0;")
        arrow.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row_layout.addLayout(text_layout, 1)
        row_layout.addWidget(arrow)
        return button

    def add_section(self, title: str, row: int, col: int, rowspan: int = 1, colspan: int = 1) -> QFrame:
        # Build the section exactly once so all existing widget references and
        # callbacks remain valid. The panel stays hidden until its header opens.
        panel = self.settings_panel(14)
        panel.setParent(self._section_storage)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        panel.hide()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(8)

        header = self.section_header_button(title)
        header.clicked.connect(lambda checked=False, t=title, p=panel: self.open_section_dialog(t, p))
        self._section_entries.append({
            "title": title,
            "row": int(row),
            "col": int(col),
            "rowspan": int(rowspan),
            "colspan": int(colspan),
            "header": header,
            "panel": panel,
        })
        return panel

    def add_section_link(self, title: str, row: int, col: int, callback, rowspan: int = 1, colspan: int = 1):
        header = self.section_header_button(title)
        header.clicked.connect(callback)
        self._section_entries.append({
            "title": title,
            "row": int(row),
            "col": int(col),
            "rowspan": int(rowspan),
            "colspan": int(colspan),
            "header": header,
            "panel": None,
        })
        return header

    def finalize_section_index(self):
        entries = sorted(self._section_entries, key=lambda item: (item["row"], item["col"], item["title"]))
        # Present the section index as two balanced columns. The source row/column
        # values still define the logical order, while the compact index avoids a
        # long single-column list and normally fits without vertical scrolling.
        for row in range(max(20, self.grid.rowCount() + len(entries) + 2)):
            self.grid.setRowStretch(row, 0)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(2, 1)
        self.grid.setColumnStretch(3, 1)
        for index, entry in enumerate(entries):
            grid_row = index // 2
            grid_col = 0 if index % 2 == 0 else 2
            self.grid.addWidget(entry["header"], grid_row, grid_col, 1, 2)
        last_row = (len(entries) + 1) // 2
        self.grid.setRowStretch(last_row, 1)

    def open_section_dialog(self, title: str, panel: QFrame):
        if self._active_section_dialog is not None:
            try:
                self._active_section_dialog.raise_()
                self._active_section_dialog.activateWindow()
            except Exception:
                pass
            return

        dlg = QDialog(self)
        self._active_section_dialog = dlg
        dlg.setModal(True)
        dlg.setWindowFlag(Qt.FramelessWindowHint, True)
        dlg.setStyleSheet("""
            QDialog {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #0b172b,
                    stop:0.58 #081321,
                    stop:1 #050d18);
                color:#f7fbff;
                border:2px solid rgba(85,240,255,0.58);
                border-radius:24px;
            }
            QLabel {
                color:#f7fbff;
                font-family:Arial;
            }
            QScrollArea {
                background:transparent;
                border:0;
            }
            QScrollArea > QWidget > QWidget {
                background:transparent;
            }
            QScrollBar:vertical {
                background:rgba(255,255,255,0.05);
                width:12px;
                margin:0;
                border-radius:6px;
            }
            QScrollBar::handle:vertical {
                background:rgba(70,223,255,0.72);
                min-height:42px;
                border-radius:6px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height:0;
            }
        """)

        available_width = max(640, int(self.width()) - 110)
        available_height = max(430, int(self.height()) - 90)
        dlg.resize(min(1120, available_width), min(680, available_height))

        root = QVBoxLayout(dlg)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        modal_header = QHBoxLayout()
        modal_header.setSpacing(10)
        heading = QLabel(title)
        heading.setFont(font(19, QFont.Black))
        heading.setStyleSheet("color:#ffffff; background:transparent; border:0;")
        close_button = RoundButton("Done", active=True, min_h=36)
        close_button.setMinimumWidth(104)
        close_button.clicked.connect(dlg.accept)
        modal_header.addWidget(heading, 1)
        modal_header.addWidget(close_button)
        root.addLayout(modal_header)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background:rgba(85,240,255,0.28); border:0;")
        root.addWidget(divider)

        content_scroll = QScrollArea()
        content_scroll.setWidgetResizable(True)
        content_scroll.setFrameShape(QFrame.NoFrame)
        content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content_scroll.setWidget(panel)
        panel.show()
        root.addWidget(content_scroll, 1)

        footer = QLabel("Changes made with plus/minus controls are applied when you tap Save Settings on the main page.")
        footer.setWordWrap(True)
        footer.setFont(font(8, QFont.Bold))
        footer.setStyleSheet("color:#8fa5c2; background:transparent; border:0; padding:2px 4px 0 4px;")
        root.addWidget(footer)

        def center_dialog():
            try:
                center = self.mapToGlobal(self.rect().center())
                frame = dlg.frameGeometry()
                frame.moveCenter(center)
                dlg.move(frame.topLeft())
            except Exception:
                pass

        if title == "Internal / External Sources":
            self.source_temp_refresh_timer.start()
            QTimer.singleShot(25, lambda: self.refresh_source_temp_sensors(force=True))

        QTimer.singleShot(0, center_dialog)
        try:
            dlg.exec_()
        finally:
            if title == "Internal / External Sources":
                self.source_temp_refresh_timer.stop()
            try:
                restored = content_scroll.takeWidget()
                if restored is not None:
                    restored.hide()
                    restored.setParent(self._section_storage)
            except Exception:
                panel.hide()
                panel.setParent(self._section_storage)
            self._active_section_dialog = None
            dlg.deleteLater()

    def masked_code(self, value: str) -> str:
        value = str(value or "")
        return "•" * len(value) if value else "Tap to set"

    def code_field(self, value: str, callback) -> QLineEdit:
        field = QLineEdit(self.masked_code(value))
        field.setReadOnly(True)
        field.setCursor(Qt.PointingHandCursor)
        field.setPlaceholderText("Tap to set")
        field.mousePressEvent = lambda event: callback()
        return field


    def edit_security_code(self):
        code = CodeKeypadDialog.get_code(self, "Security Code", "New 4-Digit Code")
        if code is None:
            return
        alarm = self.s.config.setdefault("alarm", {})
        previous = str(alarm.get("disarmCode") or "")
        alarm["disarmCode"] = code
        if hasattr(self, "security_code_field"):
            self.security_code_field.setText(self.masked_code(code))
        config_snapshot = copy.deepcopy(self.s.config)

        def done(record):
            if isinstance(record, dict):
                self.s.config = record.get("config") or self.s.config
            self.saved.emit()

        def failed(error):
            self.s.config.setdefault("alarm", {})["disarmCode"] = previous
            if hasattr(self, "security_code_field"):
                self.security_code_field.setText(self.masked_code(previous))
            QMessageBox.warning(self, "Save failed", error)

        self.run_settings_write(
            "security-code",
            lambda: self.s.api.save_config(config_snapshot),
            done,
            failed,
        )


    def edit_settings_code(self):
        code = CodeKeypadDialog.get_code(self, "Settings Code", "New 4-Digit Code")
        if code is None:
            return
        confirmed = CodeKeypadDialog.get_code(self, "Confirm Settings Code", "Re-enter New Code", verify_code=code)
        if confirmed is None:
            return
        security = self.s.config.setdefault("security", {})
        previous = str(security.get("settingsCode") or "")
        security["settingsCode"] = code
        if hasattr(self, "settings_code_field"):
            self.settings_code_field.setText(self.masked_code(code))
        config_snapshot = copy.deepcopy(self.s.config)

        def done(record):
            if isinstance(record, dict):
                self.s.config = record.get("config") or self.s.config
            self.saved.emit()

        def failed(error):
            self.s.config.setdefault("security", {})["settingsCode"] = previous
            if hasattr(self, "settings_code_field"):
                self.settings_code_field.setText(self.masked_code(previous))
            QMessageBox.warning(self, "Save failed", error)

        self.run_settings_write(
            "settings-code",
            lambda: self.s.api.save_config(config_snapshot),
            done,
            failed,
        )


    def person_names_summary(self, people: object, *, empty: str, prefix: str) -> str:
        names = []
        if isinstance(people, list):
            for person in people:
                if isinstance(person, dict):
                    names.append(str(person.get("name") or person.get("entityId") or "").strip())
        names = [x for x in names if x]
        if not names:
            return empty
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f" +{len(names)-3} more"
        return f"{prefix}: {shown}"

    def auto_away_people_summary_text(self) -> str:
        people = self.s.thermostat.get("autoAwayPeople") if isinstance(self.s.thermostat, dict) else []
        return self.person_names_summary(
            people,
            empty="No Auto Away people assigned. Tap Choose Away Users to add.",
            prefix="Auto Away uses",
        )

    def people_summary_text(self) -> str:
        people = self.s.thermostat.get("people") if isinstance(self.s.thermostat, dict) else []
        return self.person_names_summary(
            people,
            empty="No home-screen people assigned. Tap Choose Tracking to add.",
            prefix="Home screen shows",
        )

    def selected_device_internet_entities(self) -> list[dict]:
        ha = self.s.ha()
        raw = ha.get("deviceInternetSwitchEntitiesV1") if isinstance(ha, dict) else []
        clean: list[dict] = []
        seen: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not entity_id.startswith("switch.") or entity_id in seen:
                continue
            seen.add(entity_id)
            clean.append({
                "entityId": entity_id,
                "name": str(item.get("name") or item.get("friendly_name") or entity_id),
                "controlName": str(item.get("controlName") or item.get("control_name") or entity_id),
                "state": str(item.get("state") or ""),
            })
        return clean

    def device_internet_summary_text(self) -> str:
        entities = self.selected_device_internet_entities()
        if not entities:
            return "No devices selected. The main-screen iPad button will stay hidden."
        names = [str(item.get("name") or item.get("entityId") or "Device") for item in entities]
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f" +{len(names)-3} more"
        return f"iPad button controls: {shown}"

    def choose_device_internet_entities(self):
        current = self.selected_device_internet_entities()
        dlg = DeviceInternetSelectionDialog(self.s, current, self)

        def apply(entities):
            clean: list[dict] = []
            seen: set[str] = set()
            for item in entities if isinstance(entities, list) else []:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
                if not entity_id.startswith("switch.") or entity_id in seen:
                    continue
                seen.add(entity_id)
                clean.append({
                    "entityId": entity_id,
                    "name": str(item.get("name") or item.get("friendly_name") or entity_id),
                    "controlName": str(item.get("controlName") or item.get("control_name") or entity_id),
                    "state": str(item.get("state") or ""),
                })

            ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
            previous = copy.deepcopy(ha.get("deviceInternetSwitchEntitiesV1") or [])
            ha["deviceInternetSwitchEntitiesV1"] = copy.deepcopy(clean)
            ha["deviceInternetSchemaVersion"] = 1
            if hasattr(self, "device_internet_summary"):
                self.device_internet_summary.setText(self.device_internet_summary_text())
                self.device_internet_summary.repaint()
            config_snapshot = copy.deepcopy(self.s.config)

            def done(record):
                if isinstance(record, dict):
                    self.s.config = record.get("config") or self.s.config
                if hasattr(self, "device_internet_summary"):
                    self.device_internet_summary.setText(self.device_internet_summary_text())
                parent = self.parent()
                if parent is not None:
                    if hasattr(parent, "position_sleep_controls"):
                        QTimer.singleShot(0, parent.position_sleep_controls)
                    if hasattr(parent, "refresh_device_internet_state"):
                        QTimer.singleShot(0, parent.refresh_device_internet_state)
                self.saved.emit()

            def failed(error):
                ha_now = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                ha_now["deviceInternetSwitchEntitiesV1"] = previous
                if hasattr(self, "device_internet_summary"):
                    self.device_internet_summary.setText(self.device_internet_summary_text())
                QMessageBox.warning(self, "Device Internet", error)

            self.run_settings_write(
                "device-internet-save",
                lambda: self.s.api.save_config(config_snapshot),
                done,
                failed,
            )

        dlg.saved.connect(apply)
        dlg.exec_()


    def selected_alexa_lockout_entities(self) -> list[dict]:
        ha = self.s.ha()
        raw = ha.get("alexaLockoutSwitchEntitiesV2") if isinstance(ha, dict) else []
        clean: list[dict] = []
        seen: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not entity_id.startswith("switch.") or entity_id in seen:
                continue
            seen.add(entity_id)
            clean.append({
                "alexaDeviceId": str(item.get("alexaDeviceId") or item.get("alexa_device_id") or ""),
                "alexaEventEntity": str(item.get("alexaEventEntity") or item.get("alexa_event_entity") or ""),
                "serialNumber": str(item.get("serialNumber") or item.get("serial_number") or ""),
                "entityId": entity_id,
                "name": str(item.get("name") or item.get("friendly_name") or entity_id),
                "controlName": str(item.get("controlName") or item.get("control_name") or entity_id),
                "state": str(item.get("state") or ""),
            })
        return clean

    def alexa_lockout_summary_text(self) -> str:
        entities = self.selected_alexa_lockout_entities()
        if not entities:
            return "No Alexa devices selected. Screen lock will not change Alexa access."
        names = [str(item.get("name") or item.get("entityId") or "Alexa") for item in entities]
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f" +{len(names)-3} more"
        return f"Blocked while this screen is locked: {shown}"

    def choose_alexa_lockout_entities(self):
        current = self.selected_alexa_lockout_entities()
        dlg = AlexaLockoutSelectionDialog(self.s, current, self)

        def apply(entities):
            clean: list[dict] = []
            seen: set[str] = set()
            for item in entities if isinstance(entities, list) else []:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
                if not entity_id.startswith("switch.") or entity_id in seen:
                    continue
                seen.add(entity_id)
                clean.append({
                    "alexaDeviceId": str(item.get("alexaDeviceId") or item.get("alexa_device_id") or ""),
                    "alexaEventEntity": str(item.get("alexaEventEntity") or item.get("alexa_event_entity") or ""),
                    "serialNumber": str(item.get("serialNumber") or item.get("serial_number") or ""),
                    "entityId": entity_id,
                    "name": str(item.get("name") or item.get("friendly_name") or entity_id),
                    "controlName": str(item.get("controlName") or item.get("control_name") or entity_id),
                    "state": str(item.get("state") or ""),
                })

            ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
            previous = copy.deepcopy(ha.get("alexaLockoutSwitchEntitiesV2") or [])
            ha["alexaLockoutSwitchEntitiesV2"] = copy.deepcopy(clean)
            ha["alexaLockoutSchemaVersion"] = 2
            ha.pop("alexaLockoutEntities", None)
            if hasattr(self, "alexa_lockout_summary"):
                self.alexa_lockout_summary.setText(self.alexa_lockout_summary_text())
                self.alexa_lockout_summary.repaint()
            config_snapshot = copy.deepcopy(self.s.config)

            def done(record):
                if isinstance(record, dict):
                    self.s.config = record.get("config") or self.s.config
                if hasattr(self, "alexa_lockout_summary"):
                    self.alexa_lockout_summary.setText(self.alexa_lockout_summary_text())
                # Settings can only be opened from an unlocked panel. Reconcile
                # immediately so newly selected Alexa switches are definitely
                # unblocked before the user leaves Settings.
                parent = self.parent()
                if parent is not None and hasattr(parent, "apply_alexa_lockout_state"):
                    QTimer.singleShot(0, lambda p=parent: p.apply_alexa_lockout_state(False))
                self.saved.emit()

            def failed(error):
                ha_now = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                ha_now["alexaLockoutSwitchEntitiesV2"] = previous
                if hasattr(self, "alexa_lockout_summary"):
                    self.alexa_lockout_summary.setText(self.alexa_lockout_summary_text())
                QMessageBox.warning(self, "Alexa Lockout", error)

            self.run_settings_write(
                "alexa-lockout-save",
                lambda: self.s.api.save_config(config_snapshot),
                done,
                failed,
            )

        dlg.saved.connect(apply)
        dlg.exec_()


    def selected_sync_peers(self) -> list[dict]:
        ha = self.s.ha()
        peers = ha.get("syncThermostatEntities") if isinstance(ha, dict) else []
        clean: list[dict] = []
        if isinstance(peers, list):
            for peer in peers:
                if not isinstance(peer, dict):
                    continue
                entity_id = str(peer.get("entityId") or peer.get("entity_id") or "").strip()
                if not entity_id.startswith("climate."):
                    continue
                clean.append({
                    "entityId": entity_id,
                    "name": str(peer.get("name") or peer.get("friendly_name") or entity_id),
                    "state": str(peer.get("state") or "unknown"),
                    "available": bool(peer.get("available", True)),
                    "away": bool(peer.get("away", False)),
                    "doorPauseActive": bool(peer.get("doorPauseActive", False)),
                    "serial": str(peer.get("serial") or peer.get("ihaSerial") or ""),
                    "panelUrl": str(peer.get("panelUrl") or peer.get("panel_url") or ""),
                    "syncCapable": bool(peer.get("syncCapable", peer.get("ihaPanel", False))),
                })
        return clean

    def sync_peer_summary_text(self) -> str:
        peers = self.selected_sync_peers()
        if not peers:
            return "No sync thermostats selected. Tap Choose Thermostats to add."
        names = [str(peer.get("name") or peer.get("entityId") or "Thermostat") for peer in peers]
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f" +{len(names)-3} more"
        return f"Sync sends changes to: {shown}"


    def choose_sync_thermostats(self):
        current = self.selected_sync_peers()
        dlg = ThermostatSyncSelectionDialog(self.s, current, self)

        def apply(peers):
            clean = []
            seen = set()
            for peer in peers if isinstance(peers, list) else []:
                if not isinstance(peer, dict):
                    continue
                entity_id = str(peer.get("entityId") or peer.get("entity_id") or "").strip()
                if not entity_id.startswith("climate.") or entity_id in seen:
                    continue
                seen.add(entity_id)
                clean.append({
                    "entityId": entity_id,
                    "name": str(peer.get("name") or peer.get("friendly_name") or entity_id),
                    "state": str(peer.get("state") or "unknown"),
                    "available": bool(peer.get("available", True)),
                    "away": bool(peer.get("away", False)),
                    "doorPauseActive": bool(peer.get("doorPauseActive", False)),
                    "serial": str(peer.get("serial") or peer.get("ihaSerial") or ""),
                    "panelUrl": str(peer.get("panelUrl") or peer.get("panel_url") or ""),
                    "syncCapable": bool(peer.get("syncCapable", peer.get("ihaPanel", False))),
                })

            ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
            previous_selected = copy.deepcopy(ha.get("syncThermostatEntities") or [])
            previous_available = copy.deepcopy(ha.get("syncAvailableThermostatEntities") or [])
            ha["syncThermostatEntities"] = copy.deepcopy(clean)
            ha["syncAvailableThermostatEntities"] = copy.deepcopy(clean)
            if hasattr(self, "sync_peer_summary"):
                self.sync_peer_summary.setText(self.sync_peer_summary_text())
                self.sync_peer_summary.repaint()
            top = self.window()
            if hasattr(top, "update_sync_button_state"):
                top.update_sync_button_state()
            config_snapshot = copy.deepcopy(self.s.config)

            def done(record):
                if isinstance(record, dict):
                    self.s.config = record.get("config") or self.s.config
                if hasattr(self, "sync_peer_summary"):
                    self.sync_peer_summary.setText(self.sync_peer_summary_text())
                top = self.window()
                if hasattr(top, "update_sync_button_state"):
                    top.update_sync_button_state()
                self.saved.emit()

            def failed(error):
                ha_now = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                ha_now["syncThermostatEntities"] = previous_selected
                ha_now["syncAvailableThermostatEntities"] = previous_available
                if hasattr(self, "sync_peer_summary"):
                    self.sync_peer_summary.setText(self.sync_peer_summary_text())
                top = self.window()
                if hasattr(top, "update_sync_button_state"):
                    top.update_sync_button_state()
                QMessageBox.warning(self, "Sync", error)

            self.run_settings_write(
                "sync-peers-save",
                lambda: self.s.api.save_config(config_snapshot),
                done,
                failed,
            )

        dlg.saved.connect(apply)
        dlg.exec_()


    def house_sync_source_summary_text(self) -> str:
        source = self.house_sync_source if isinstance(self.house_sync_source, dict) else {}
        name = str(source.get("name") or source.get("entityId") or "").strip()
        panel_url = str(source.get("panelUrl") or source.get("panel_url") or "").strip()
        if not name or not panel_url:
            return "No source selected. Choose the configured thermostat you want to copy from."
        return f"Copy from: {name}\n{panel_url}"

    def update_house_sync_widgets(self):
        if hasattr(self, "house_sync_source_summary"):
            self.house_sync_source_summary.setText(self.house_sync_source_summary_text())
            self.house_sync_source_summary.repaint()
        source_ready = bool(
            isinstance(self.house_sync_source, dict)
            and str(self.house_sync_source.get("panelUrl") or self.house_sync_source.get("panel_url") or "").strip()
        )
        if hasattr(self, "house_sync_button"):
            self.house_sync_button.setVisible(source_ready)
            self.house_sync_button.setEnabled(source_ready and not self.house_sync_running)
            self.house_sync_button.setText("Sync House Setup" if not self.house_sync_running else "Syncing…")
        if hasattr(self, "choose_house_sync_button"):
            self.choose_house_sync_button.setEnabled(not self.house_sync_running)
        if hasattr(self, "bottom_save"):
            self.bottom_save.setEnabled(not self.house_sync_running and not self._settings_saving)
        if hasattr(self, "done"):
            self.done.setEnabled(not self.house_sync_running)

    def choose_house_sync_source(self):
        dlg = HouseSyncSelectionDialog(self.s, self.house_sync_source, self)

        def apply(peer: dict):
            self.house_sync_source = copy.deepcopy(peer) if isinstance(peer, dict) else None
            self.update_house_sync_widgets()

        dlg.selected.connect(apply)
        dlg.exec_()

    def start_house_sync(self):
        source = self.house_sync_source if isinstance(self.house_sync_source, dict) else {}
        panel_url = str(source.get("panelUrl") or source.get("panel_url") or "").strip()
        source_name = str(source.get("name") or source.get("entityId") or "source thermostat").strip() or "source thermostat"
        if not panel_url or self.house_sync_running:
            return
        if self._settings_saving:
            QMessageBox.information(self, "House Sync", "Settings are still being saved. Start House Sync after that save finishes.")
            return
        answer = QMessageBox.question(
            self,
            "House Sync",
            f"Replace this thermostat's Blinds, Lights, and Room page setup with the configuration from {source_name}?\n\n"
            "Only those three page sections will be replaced. The current complete panel config is backed up first. Thermostat operation, panel and Room-entry security codes, Home Assistant credentials, schedules, and JARVIS settings are not copied.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.house_sync_running = True
        self.update_house_sync_widgets()

        payload = {
            "sourcePanelUrl": panel_url,
            "sourceName": source_name,
            "sourceEntityId": str(source.get("entityId") or ""),
            "sourceSerial": str(source.get("serial") or ""),
        }

        def worker():
            try:
                result = self.s.api.post("/api/house-sync/apply", payload, timeout=18.0)
                self.houseSyncCompleted.emit({"result": result, "error": None})
            except Exception as exc:
                self.houseSyncCompleted.emit({"result": None, "error": str(exc)})

        threading.Thread(target=worker, name="settings-house-sync", daemon=True).start()

    def handle_house_sync_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        self.house_sync_running = False
        self.update_house_sync_widgets()
        error_text = str(data.get("error") or "").strip()
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        if error_text or not result.get("ok"):
            QMessageBox.warning(self, "House Sync", error_text or str(result.get("error") or "House Sync failed."))
            return

        returned_config = result.get("config") if isinstance(result.get("config"), dict) else {}
        for section_name in ("blinds", "lights", "roomControl"):
            if isinstance(returned_config.get(section_name), dict):
                self.s.config[section_name] = copy.deepcopy(returned_config[section_name])
        self.saved.emit()
        QMessageBox.information(
            self,
            "House Sync Complete",
            str(result.get("message") or "Blinds, Lights, and Room were copied successfully.")
            + "\n\nThe previous complete panel configuration is available in panel-config.backup.json.",
        )



    def choose_auto_away_people(self):
        current = self.s.thermostat.get("autoAwayPeople") if isinstance(self.s.thermostat, dict) else []
        dlg = PeopleSelectionDialog(
            self.s,
            current if isinstance(current, list) else [],
            self,
            title="Auto Away Users",
            note="Select the Home Assistant person entries that control Auto Away/Home. If none of these selected people are home, the thermostat can enter Away. When any selected Auto Away user comes home, it returns to Home automatically.",
        )

        def apply(people):
            previous = copy.deepcopy(self.s.thermostat.get("autoAwayPeople") or [])
            clean = copy.deepcopy(people if isinstance(people, list) else [])
            self.s.thermostat["autoAwayPeople"] = clean
            if hasattr(self, "people_summary"):
                self.people_summary.setText(self.auto_away_people_summary_text())
                self.people_summary.repaint()

            def done(result):
                if isinstance(result, dict):
                    self.s.ingest_thermostat(result)
                if hasattr(self, "people_summary"):
                    self.people_summary.setText(self.auto_away_people_summary_text())
                self.saved.emit()

            def failed(error):
                self.s.thermostat["autoAwayPeople"] = previous
                if hasattr(self, "people_summary"):
                    self.people_summary.setText(self.auto_away_people_summary_text())
                QMessageBox.warning(self, "Auto Away / Home", error)

            self.run_settings_write(
                "auto-away-people",
                lambda: self.s.api.thermostat_update({"autoAwayPeople": clean}),
                done,
                failed,
            )

        dlg.saved.connect(apply)
        dlg.exec_()


    def choose_person_tracking_people(self):
        current = self.s.thermostat.get("people") if isinstance(self.s.thermostat, dict) else []
        dlg = PeopleSelectionDialog(
            self.s,
            current if isinstance(current, list) else [],
            self,
            title="Person Tracking",
            note="Select the Home Assistant person entries that appear on the main thermostat screen. This list is display-only and does not control Auto Away/Home.",
        )

        def apply(people):
            previous = copy.deepcopy(self.s.thermostat.get("people") or [])
            clean = copy.deepcopy(people if isinstance(people, list) else [])
            self.s.thermostat["people"] = clean
            if hasattr(self, "person_tracking_summary"):
                self.person_tracking_summary.setText(self.people_summary_text())
                self.person_tracking_summary.repaint()

            def done(result):
                if isinstance(result, dict):
                    self.s.ingest_thermostat(result)
                if hasattr(self, "person_tracking_summary"):
                    self.person_tracking_summary.setText(self.people_summary_text())
                self.saved.emit()

            def failed(error):
                self.s.thermostat["people"] = previous
                if hasattr(self, "person_tracking_summary"):
                    self.person_tracking_summary.setText(self.people_summary_text())
                QMessageBox.warning(self, "Person Tracking", error)

            self.run_settings_write(
                "person-tracking",
                lambda: self.s.api.thermostat_update({"people": clean}),
                done,
                failed,
            )

        dlg.saved.connect(apply)
        dlg.exec_()

    def current_inside_door_entry(self) -> dict | None:
        pause = self.s.thermostat.get("pauseFunction") if isinstance(self.s.thermostat, dict) else {}
        if isinstance(pause, dict) and isinstance(pause.get("entries"), list) and pause.get("entries"):
            first = pause.get("entries")[0]
            if isinstance(first, dict):
                return first
        ha = self.s.ha()
        door = ha.get("doorEntity") if isinstance(ha, dict) else None
        return door if isinstance(door, dict) else None

    def inside_door_summary_text(self) -> str:
        entry = self.current_inside_door_entry()
        if not entry:
            return "No entry selected. Choose the HA door/contact/cover used by the Doors tile."
        name = str(entry.get("name") or entry.get("friendly_name") or entry.get("entityId") or "Selected Entry")
        entity_id = str(entry.get("entityId") or entry.get("entity_id") or "")
        return f"{name}\n{entity_id}"

    def current_door_pause_duration(self) -> int:
        pause = self.s.thermostat.get("pauseFunction") if isinstance(self.s.thermostat, dict) else {}
        if isinstance(pause, dict):
            try:
                return int(max(1, min(60, round(float(pause.get("durationMinutes") or 5)))))
            except Exception:
                pass
        return 5


    def choose_inside_door_entry(self):
        if getattr(self, "_door_entities_loading", False):
            return
        ha = self.s.ha()
        stored = []
        if isinstance(ha, dict):
            for key in ("doorAvailableEntities", "pauseFunctionAvailableEntities"):
                if isinstance(ha.get(key), list):
                    stored.extend(copy.deepcopy(ha.get(key) or []))
            current = ha.get("doorEntity")
            if isinstance(current, dict):
                stored.insert(0, copy.deepcopy(current))
        current_entry = self.current_inside_door_entry()
        if isinstance(current_entry, dict):
            stored.insert(0, copy.deepcopy(current_entry))

        self._door_entities_loading = True
        button = getattr(self, "inside_door_choose_button", None)
        if button is not None:
            button.setEnabled(False)
            button.setText("Loading…")

        def restore_button():
            self._door_entities_loading = False
            button = getattr(self, "inside_door_choose_button", None)
            if button is not None:
                button.setEnabled(True)
                button.setText("Choose Entry")

        def open_picker(live_entities: list[dict], load_error: str = ""):
            restore_button()
            by_id: dict[str, dict] = {}
            for item in list(stored) + list(live_entities or []):
                if not isinstance(item, dict):
                    continue
                eid = str(item.get("entityId") or item.get("entity_id") or "").strip()
                if not eid:
                    continue
                domain = str(item.get("domain") or (eid.split(".", 1)[0] if "." in eid else "")).strip()
                if domain not in {"binary_sensor", "cover"}:
                    continue
                name = str(item.get("friendlyName") or item.get("friendly_name") or item.get("name") or eid).strip() or eid
                record = copy.deepcopy(item)
                record.update({
                    "entityId": eid,
                    "name": name,
                    "friendlyName": name,
                    "domain": domain,
                })
                by_id[eid] = record
            entities = list(by_id.values())
            if not entities:
                detail = f"\n\n{load_error}" if load_error else ""
                QMessageBox.warning(self, "Doors", "No Home Assistant binary_sensor or cover entries found." + detail)
                return

            dlg = EntityPickerDialog("Choose Doors Entry", entities, self)

            def apply(e):
                eid = str(e.get("entityId") or e.get("entity_id") or "").strip()
                if not eid:
                    return
                source = next(
                    (item for item in entities if str(item.get("entityId") or item.get("entity_id") or "").strip() == eid),
                    {},
                )
                merged = copy.deepcopy(source)
                merged.update(e if isinstance(e, dict) else {})
                domain = str(merged.get("domain") or (eid.split(".", 1)[0] if "." in eid else "binary_sensor"))
                selected_name = str(
                    merged.get("friendlyName")
                    or merged.get("friendly_name")
                    or merged.get("name")
                    or eid
                ).strip() or eid
                selected = {
                    "entityId": eid,
                    "name": selected_name,
                    "friendlyName": selected_name,
                    "domain": domain,
                    "state": str(merged.get("state") or "unknown"),
                    "deviceClass": str(merged.get("deviceClass") or merged.get("device_class") or ""),
                    "currentPosition": merged.get("currentPosition")
                    if merged.get("currentPosition") is not None
                    else merged.get("current_position"),
                    "isClosed": merged.get("isClosed")
                    if isinstance(merged.get("isClosed"), bool)
                    else merged.get("is_closed"),
                }

                ha_config = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                previous_ha = {
                    key: copy.deepcopy(ha_config.get(key))
                    for key in ("doorEntity", "doorAvailableEntities", "pauseFunctionAvailableEntities")
                }
                previous_pause = copy.deepcopy(self.s.thermostat.get("pauseFunction"))
                ha_config["doorEntity"] = selected
                available = [selected] + [
                    copy.deepcopy(item)
                    for item in entities
                    if isinstance(item, dict) and str(item.get("entityId") or "") != eid
                ]
                ha_config["doorAvailableEntities"] = copy.deepcopy(available)
                ha_config["pauseFunctionAvailableEntities"] = copy.deepcopy(available)
                duration = (
                    self.val_number("doorPauseDurationMinutes")
                    if "doorPauseDurationMinutes" in self.controls
                    else self.current_door_pause_duration()
                )
                changes = {
                    "pauseFunction": {
                        "durationMinutes": duration,
                        "entries": [selected],
                        "active": False,
                        "pausedAt": 0,
                        "previousTargetTemp": None,
                        "previousLastComfortTarget": None,
                        "activeEntityIds": [],
                        "snoozeUntil": 0,
                    }
                }
                self.apply_thermostat_changes_locally(changes)
                if hasattr(self, "inside_door_label"):
                    self.inside_door_label.setText(self.inside_door_summary_text())
                config_snapshot = copy.deepcopy(self.s.config)

                def worker():
                    config_record = self.s.api.save_config(config_snapshot)
                    thermostat_result = self.s.api.thermostat_update(changes)
                    return {"config": config_record, "thermostat": thermostat_result}

                def done(result):
                    data = result if isinstance(result, dict) else {}
                    config_record = data.get("config")
                    if isinstance(config_record, dict):
                        self.s.config = config_record.get("config") or self.s.config
                    thermostat_result = data.get("thermostat")
                    if isinstance(thermostat_result, dict):
                        self.s.ingest_thermostat(thermostat_result)
                    if hasattr(self, "inside_door_label"):
                        self.inside_door_label.setText(self.inside_door_summary_text())
                    self.saved.emit()

                def failed(error):
                    ha_now = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                    for key, value in previous_ha.items():
                        if value is None:
                            ha_now.pop(key, None)
                        else:
                            ha_now[key] = value
                    if previous_pause is None:
                        self.s.thermostat.pop("pauseFunction", None)
                    else:
                        self.s.thermostat["pauseFunction"] = previous_pause
                    if hasattr(self, "inside_door_label"):
                        self.inside_door_label.setText(self.inside_door_summary_text())
                    QMessageBox.warning(self, "Doors", error)

                self.run_settings_write("door-entry-save", worker, done, failed)

            dlg.selected.connect(apply)
            dlg.exec_()

        self.load_ha_entities_async(
            "door-entities",
            ["binary_sensor", "cover"],
            lambda entities: open_picker(entities, ""),
            lambda error: open_picker([], error),
        )


    def source_mode_key(self, kind: str) -> str:
        return {
            "room": "roomTempControlMode",
            "heat": "heatControlMode",
            "cool": "coolControlMode",
            "fan": "fanControlMode",
        }.get(str(kind or "").strip().lower(), "roomTempControlMode")

    def source_control_mode(self, kind: str) -> str:
        kind = str(kind or "").strip().lower()
        t = self.s.thermostat if isinstance(self.s.thermostat, dict) else {}
        value = t.get(self.source_mode_key(kind))
        if value is None and kind in {"heat", "cool"}:
            value = t.get("airControlMode")
        mode = str(value or "internal").strip().lower()
        return "external" if mode in {"external", "home-assistant", "ha", "remote"} else "internal"

    def external_source_entity(self, kind: str) -> dict | None:
        kind = str(kind or "").strip().lower()
        if kind == "room":
            entry = nested_get(self.s.config, "integrations", "homeAssistant", "currentTempEntity", default=None)
            return entry if isinstance(entry, dict) and str(entry.get("entityId") or entry.get("entity_id") or "").strip() else None

        def allowed(entry: dict | None) -> dict | None:
            if not isinstance(entry, dict):
                return None
            eid = str(entry.get("entityId") or entry.get("entity_id") or "").strip()
            if not eid or "." not in eid:
                return None
            domain = str(entry.get("domain") or eid.split(".", 1)[0]).strip().lower()
            return entry if domain in {"switch", "input_boolean"} else None

        t = self.s.thermostat if isinstance(self.s.thermostat, dict) else {}
        thermostat_key = {
            "heat": "externalHeatEntity",
            "cool": "externalCoolEntity",
            "fan": "externalFanEntity",
        }.get(kind)
        entry = allowed(t.get(thermostat_key)) if thermostat_key else None
        if entry:
            return entry
        ha = self.s.ha()
        ha_key = {
            "heat": "externalHeatControlEntity",
            "cool": "externalCoolControlEntity",
            "fan": "externalFanControlEntity",
        }.get(kind)
        return allowed(ha.get(ha_key) if isinstance(ha, dict) and ha_key else None)

    def source_label(self, kind: str) -> str:
        return {"room": "Room Temp", "heat": "Heat", "cool": "Cool", "fan": "Fan"}.get(kind, "Source")

    def source_summary_text(self, kind: str) -> str:
        kind = str(kind or "").strip().lower()
        mode = self.source_control_mode(kind)
        if mode == "internal":
            return "Onboard HDC2080 sensors" if kind == "room" else "Onboard GPIO relay"
        entry = self.external_source_entity(kind)
        if not entry:
            return "No HA sensor/climate selected" if kind == "room" else "No HA entry selected"
        name = str(entry.get("name") or entry.get("friendly_name") or entry.get("entityId") or "Home Assistant")
        entity_id = str(entry.get("entityId") or entry.get("entity_id") or "")
        return f"{compact_name(name, 28)}\n{entity_id}"

    def update_source_control_widgets(self):
        for kind in ("room", "heat", "cool", "fan"):
            mode = self.source_control_mode(kind)
            external = mode == "external"
            mode_button = getattr(self, f"{kind}_source_mode_button", None)
            if mode_button is not None:
                mode_button.setText(mode.upper())
                if hasattr(mode_button, "setActive"):
                    mode_button.setActive(external)
            summary = getattr(self, f"{kind}_source_summary", None)
            if summary is not None:
                summary.setText(self.source_summary_text(kind))
                summary.repaint()
            choose_button = getattr(self, f"{kind}_source_choose_button", None)
            if choose_button is not None and hasattr(choose_button, "setActive"):
                choose_button.setActive(external)

    def legacy_air_mode_with(self, changed_kind: str, changed_mode: str) -> str:
        heat_mode = changed_mode if changed_kind == "heat" else self.source_control_mode("heat")
        cool_mode = changed_mode if changed_kind == "cool" else self.source_control_mode("cool")
        return "external" if heat_mode == "external" and cool_mode == "external" else "internal"


    def set_source_control_mode(self, kind: str, mode: str):
        kind = str(kind or "").strip().lower()
        if kind not in {"room", "heat", "cool", "fan"}:
            return
        mode = "external" if str(mode or "").strip().lower() == "external" else "internal"
        if mode == "external" and not self.external_source_entity(kind):
            if kind == "room":
                self.choose_temp_sensor()
            else:
                self.choose_external_air_entry(kind)
            return

        changes = {self.source_mode_key(kind): mode}
        if kind in {"heat", "cool"}:
            changes["airControlMode"] = self.legacy_air_mode_with(kind, mode)
        if kind == "room":
            if mode == "internal":
                changes.update({
                    "currentTempSource": "onboard",
                    "currentTempSourceName": "Onboard HDC2080 Sensors",
                    "runtimeTempSource": "onboard",
                    "runtimeTempSourceName": "Onboard HDC2080 Sensors",
                })
            else:
                entry = self.external_source_entity("room") or {}
                name = str(entry.get("name") or entry.get("friendly_name") or "Home Assistant Sensor")
                changes.update({
                    "currentTempSource": "home-assistant",
                    "currentTempSourceName": name,
                    "runtimeTempSource": "home-assistant",
                    "runtimeTempSourceName": name,
                })

        sentinel = object()
        previous = {key: copy.deepcopy(self.s.thermostat.get(key, sentinel)) for key in changes}
        self.apply_thermostat_changes_locally(changes)
        self.update_source_control_widgets()

        def done(result):
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
            self.update_source_control_widgets()
            self.saved.emit()

        def failed(error):
            for key, value in previous.items():
                if value is sentinel:
                    self.s.thermostat.pop(key, None)
                else:
                    self.s.thermostat[key] = value
            self.update_source_control_widgets()
            QMessageBox.warning(self, f"{self.source_label(kind)} Source", error)

        self.run_settings_write(
            f"{kind}-source-mode",
            lambda: self.s.api.thermostat_update(changes),
            done,
            failed,
        )

    def toggle_source_control_mode(self, kind: str):
        next_mode = "internal" if self.source_control_mode(kind) == "external" else "external"
        self.set_source_control_mode(kind, next_mode)


    def choose_external_air_entry(self, kind: str):
        kind = str(kind or "").strip().lower()
        if kind not in {"heat", "cool", "fan"}:
            return
        loading_key = f"_external_{kind}_entities_loading"
        if getattr(self, loading_key, False):
            return
        label = self.source_label(kind)
        ha = self.s.ha()
        stored = []
        selected_key = {
            "heat": "externalHeatControlEntity",
            "cool": "externalCoolControlEntity",
            "fan": "externalFanControlEntity",
        }[kind]
        if isinstance(ha, dict):
            current = ha.get(selected_key)
            if isinstance(current, dict):
                stored.append(copy.deepcopy(current))
            if isinstance(ha.get("externalAirControlAvailableEntities"), list):
                stored.extend(copy.deepcopy(ha.get("externalAirControlAvailableEntities") or []))
        current_entry = self.external_source_entity(kind)
        if isinstance(current_entry, dict):
            stored.insert(0, copy.deepcopy(current_entry))

        button = getattr(self, f"{kind}_source_choose_button", None)
        setattr(self, loading_key, True)
        if button is not None:
            button.setEnabled(False)
            button.setText("Loading…")

        def restore_button():
            setattr(self, loading_key, False)
            button = getattr(self, f"{kind}_source_choose_button", None)
            if button is not None:
                button.setEnabled(True)
                button.setText("Choose HA")

        def open_picker(live_entities: list[dict], load_error: str = ""):
            restore_button()
            domains = {"switch", "input_boolean"}
            by_id: dict[str, dict] = {}
            for item in list(stored) + list(live_entities or []):
                if not isinstance(item, dict):
                    continue
                eid = str(item.get("entityId") or item.get("entity_id") or "").strip()
                if not eid or "." not in eid:
                    continue
                domain = str(item.get("domain") or eid.split(".", 1)[0]).strip().lower()
                if domain not in domains:
                    continue
                record = copy.deepcopy(item)
                record.update({
                    "entityId": eid,
                    "name": str(item.get("name") or item.get("friendly_name") or eid),
                    "domain": domain,
                })
                by_id[eid] = record
            entities = list(by_id.values())
            if not entities:
                detail = f"\n\n{load_error}" if load_error else ""
                QMessageBox.warning(
                    self,
                    f"External {label} Entry",
                    "No Home Assistant switch or input_boolean entries found." + detail,
                )
                return

            dlg = EntityPickerDialog(f"Choose External {label} Entry", entities, self)

            def apply(e):
                eid = str(e.get("entityId") or e.get("entity_id") or "").strip()
                if not eid:
                    return
                source = next(
                    (item for item in entities if str(item.get("entityId") or item.get("entity_id") or "").strip() == eid),
                    {},
                )
                merged = copy.deepcopy(source)
                merged.update(e if isinstance(e, dict) else {})
                domain = str(merged.get("domain") or (eid.split(".", 1)[0] if "." in eid else "switch")).strip().lower()
                if domain not in domains:
                    QMessageBox.warning(
                        self,
                        f"External {label} Entry",
                        "External control can only use switch or input_boolean entries.",
                    )
                    return
                selected = {
                    "entityId": eid,
                    "name": str(merged.get("name") or merged.get("friendly_name") or eid),
                    "domain": domain,
                    "state": str(merged.get("state") or "unknown"),
                }

                ha_config = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                previous_selected = copy.deepcopy(ha_config.get(selected_key))
                previous_available = copy.deepcopy(ha_config.get("externalAirControlAvailableEntities"))
                thermostat_key = {
                    "heat": "externalHeatEntity",
                    "cool": "externalCoolEntity",
                    "fan": "externalFanEntity",
                }[kind]
                previous_thermostat = {
                    key: (key in self.s.thermostat, copy.deepcopy(self.s.thermostat.get(key)))
                    for key in (self.source_mode_key(kind), thermostat_key, "airControlMode")
                }

                ha_config[selected_key] = selected
                available = [selected] + [
                    copy.deepcopy(item)
                    for item in entities
                    if isinstance(item, dict) and str(item.get("entityId") or "") != eid
                ]
                ha_config["externalAirControlAvailableEntities"] = available
                changes = {
                    self.source_mode_key(kind): "external",
                    thermostat_key: selected,
                }
                if kind in {"heat", "cool"}:
                    changes["airControlMode"] = self.legacy_air_mode_with(kind, "external")
                self.apply_thermostat_changes_locally(changes)
                self.update_source_control_widgets()
                config_snapshot = copy.deepcopy(self.s.config)

                def worker():
                    config_record = self.s.api.save_config(config_snapshot)
                    thermostat_result = self.s.api.thermostat_update(changes)
                    return {"config": config_record, "thermostat": thermostat_result}

                def done(result):
                    data = result if isinstance(result, dict) else {}
                    config_record = data.get("config")
                    if isinstance(config_record, dict):
                        self.s.config = config_record.get("config") or self.s.config
                    thermostat_result = data.get("thermostat")
                    if isinstance(thermostat_result, dict):
                        self.s.ingest_thermostat(thermostat_result)
                    self.update_source_control_widgets()
                    self.saved.emit()

                def failed(error):
                    ha_now = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                    if previous_selected is None:
                        ha_now.pop(selected_key, None)
                    else:
                        ha_now[selected_key] = previous_selected
                    if previous_available is None:
                        ha_now.pop("externalAirControlAvailableEntities", None)
                    else:
                        ha_now["externalAirControlAvailableEntities"] = previous_available
                    for key, (existed, value) in previous_thermostat.items():
                        if existed:
                            self.s.thermostat[key] = value
                        else:
                            self.s.thermostat.pop(key, None)
                    self.update_source_control_widgets()
                    QMessageBox.warning(self, f"External {label} Entry", error)

                self.run_settings_write(f"external-{kind}-entry-save", worker, done, failed)

            dlg.selected.connect(apply)
            dlg.exec_()

        self.load_ha_entities_async(
            f"external-{kind}-entities",
            ["switch", "input_boolean"],
            lambda entities: open_picker(entities, ""),
            lambda error: open_picker([], error),
        )


    def source_temp_card_style(self) -> str:
        return """
            QFrame {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 rgba(27,39,59,0.96), stop:1 rgba(8,15,29,0.96));
                border:1px solid rgba(111,139,166,0.38);
                border-radius:12px;
            }
        """

    def build_source_temp_sensor_card(self, sensor_number: int, role: str, address: str) -> QFrame:
        card = QFrame()
        card.setStyleSheet(self.source_temp_card_style())
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 9, 12, 11)
        layout.setSpacing(5)
        title = QLabel(f"TEMP SENSOR {sensor_number}  ·  {address}")
        title.setFont(font(10, QFont.Black))
        title.setStyleSheet("color:#46e8ff; background:transparent; border:0;")
        role_label = QLabel(role)
        role_label.setFont(font(7, QFont.Black))
        role_label.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        layout.addWidget(title)
        layout.addWidget(role_label)

        adjusted = QLabel("Corrected temperature: --")
        raw = QLabel("Raw temperature: --")
        humidity = QLabel("Humidity: --")
        health = QLabel("Status: waiting for reading")
        for label in (adjusted, raw, humidity, health):
            label.setWordWrap(True)
            label.setFont(font(9 if label is adjusted else 8, QFont.Black))
            label.setStyleSheet("color:#f7fbff; background:rgba(5,10,20,0.32); border-radius:7px; padding:4px 7px;")
            layout.addWidget(label)
        self.source_temp_sensor_labels[sensor_number] = {
            "role": role_label,
            "adjusted": adjusted,
            "raw": raw,
            "humidity": humidity,
            "health": health,
        }

        offset_title = QLabel("Temperature offset")
        offset_title.setFont(font(7, QFont.Black))
        offset_title.setStyleSheet("color:#c4d0e5; background:transparent; border:0; margin-top:2px;")
        layout.addWidget(offset_title)
        row = QHBoxLayout()
        row.setSpacing(6)
        minus = RoundButton("− 0.1", active=True, min_h=30)
        plus = RoundButton("+ 0.1", active=True, min_h=30)
        value = QLabel("")
        value.setAlignment(Qt.AlignCenter)
        value.setMinimumHeight(30)
        value.setFont(font(10, QFont.Black))
        value.setStyleSheet("color:#ffffff; background:rgba(5,10,20,0.62); border:1px solid rgba(85,240,255,0.38); border-radius:8px;")
        minus.clicked.connect(lambda checked=False, n=sensor_number: self.adjust_source_temp_offset(n, -0.1))
        plus.clicked.connect(lambda checked=False, n=sensor_number: self.adjust_source_temp_offset(n, 0.1))
        row.addWidget(minus)
        row.addWidget(value, 1)
        row.addWidget(plus)
        layout.addLayout(row)
        self.source_temp_offset_labels[sensor_number] = value
        self.refresh_source_temp_offset_label(sensor_number)
        return card

    def build_source_temp_threshold_control(self, key: str, title: str, step: float, suffix: str) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("background:rgba(5,10,20,0.34); border:1px solid rgba(160,180,210,0.18); border-radius:9px;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 5, 8, 7)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setFont(font(7, QFont.Black))
        label.setStyleSheet("color:#c4d0e5; background:transparent; border:0;")
        layout.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(5)
        minus = RoundButton(f"− {step:g}", active=True, min_h=28)
        plus = RoundButton(f"+ {step:g}", active=True, min_h=28)
        value = QLabel("")
        value.setAlignment(Qt.AlignCenter)
        value.setFont(font(9, QFont.Black))
        value.setMinimumHeight(28)
        value.setProperty("suffix", suffix)
        value.setStyleSheet("color:#ffffff; background:rgba(5,10,20,0.62); border:1px solid rgba(85,240,255,0.30); border-radius:7px;")
        minus.clicked.connect(lambda checked=False, k=key, d=-step: self.adjust_source_temp_threshold(k, d))
        plus.clicked.connect(lambda checked=False, k=key, d=step: self.adjust_source_temp_threshold(k, d))
        row.addWidget(minus)
        row.addWidget(value, 1)
        row.addWidget(plus)
        layout.addLayout(row)
        self.source_temp_threshold_labels[key] = value
        self.refresh_source_temp_threshold_label(key)
        return panel

    def refresh_source_temp_offset_label(self, sensor_number: int):
        label = self.source_temp_offset_labels.get(sensor_number)
        if label is not None:
            label.setText(f"{self.source_temp_offsets.get(sensor_number, 0.0):+.1f}°F")

    def refresh_source_temp_threshold_label(self, key: str):
        label = self.source_temp_threshold_labels.get(key)
        if label is not None:
            suffix = str(label.property("suffix") or "")
            label.setText(f"{self.source_temp_thresholds.get(key, 0.0):.1f}{suffix}")

    def refresh_source_temp_primary_widgets(self):
        primary = self.source_temp_primary_sensor if self.source_temp_primary_sensor in (1, 2) else 2
        fallback = 1 if primary == 2 else 2
        for number, button in self.source_temp_primary_buttons.items():
            button.setActive(number == primary)
            button.setText(f"Sensor {number}" + ("  ·  PRIMARY" if number == primary else ""))
        for number, labels in self.source_temp_sensor_labels.items():
            role_label = labels.get("role")
            if role_label is not None:
                role_label.setText(
                    "Primary panel temperature"
                    if number == primary
                    else "Comparison / automatic fallback"
                )
        if hasattr(self, "source_temp_primary_summary"):
            self.source_temp_primary_summary.setText(
                f"Sensor {primary} controls panel temperature. Sensor {fallback} remains the automatic fallback."
            )
        if hasattr(self, "source_temp_health_note"):
            self.source_temp_health_note.setText(
                f"Corrected sensor {primary} is the primary panel temperature. Sensor {fallback} remains an independent comparison and automatic fallback. A failed sensor or sudden jump at or above the fault limit is ignored."
            )

    def set_source_temp_primary_sensor(self, sensor_number: int):
        if sensor_number not in (1, 2):
            return
        changed = self.source_temp_primary_sensor != sensor_number
        self.source_temp_primary_sensor = sensor_number
        self.refresh_source_temp_primary_widgets()
        if changed:
            self._source_temp_dirty = True
            self.source_temp_footer.setText(
                f"Sensor {sensor_number} selected as primary. Tap Save Sensors to apply."
            )

    def adjust_source_temp_offset(self, sensor_number: int, delta: float):
        current = float(self.source_temp_offsets.get(sensor_number, 0.0))
        self.source_temp_offsets[sensor_number] = round(clamp(current + delta, -20.0, 20.0), 1)
        self.refresh_source_temp_offset_label(sensor_number)
        self._source_temp_dirty = True
        self.source_temp_footer.setText("Unsaved onboard temperature-sensor changes. Tap Save Sensors to apply.")

    def adjust_source_temp_threshold(self, key: str, delta: float):
        low, high = (0.5, 20.0) if key == "maxDisagreementF" else (3.0, 40.0)
        current = float(self.source_temp_thresholds.get(key, low))
        self.source_temp_thresholds[key] = round(clamp(current + delta, low, high), 1)
        self.refresh_source_temp_threshold_label(key)
        self._source_temp_dirty = True
        self.source_temp_footer.setText("Unsaved onboard temperature-sensor changes. Tap Save Sensors to apply.")

    def restore_source_temp_defaults(self):
        self.source_temp_offsets = {1: -5.2, 2: -7.3}
        self.source_temp_thresholds = {"maxDisagreementF": 3.0, "maxJumpF": 15.0}
        self.source_temp_primary_sensor = 2
        for number in (1, 2):
            self.refresh_source_temp_offset_label(number)
        for key in self.source_temp_thresholds:
            self.refresh_source_temp_threshold_label(key)
        self.refresh_source_temp_primary_widgets()
        self._source_temp_dirty = True
        self.source_temp_footer.setText("Default primary sensor, offsets, and health limits loaded. Tap Save Sensors to apply.")

    def refresh_source_temp_sensors(self, force: bool = False):
        if self._source_temp_loading or self._source_temp_saving:
            return
        self._source_temp_loading = True
        if hasattr(self, "source_temp_refresh_button"):
            self.source_temp_refresh_button.setEnabled(False)
        path = "/api/hardware/temperature-sensors?refresh=1" if force else "/api/hardware/temperature-sensors"

        def worker():
            try:
                data = self.s.api.get(path)
                self.tempSensorTelemetryLoaded.emit({"data": data, "error": None})
            except Exception as exc:
                self.tempSensorTelemetryLoaded.emit({"data": None, "error": str(exc)})

        threading.Thread(target=worker, name="settings-temperature-sensor-refresh", daemon=True).start()

    def apply_source_temp_config(self, config: dict):
        try:
            self.source_temp_offsets[1] = round(float(config.get("sensor1OffsetF", self.source_temp_offsets.get(1, -5.2))), 1)
            self.source_temp_offsets[2] = round(float(config.get("sensor2OffsetF", self.source_temp_offsets.get(2, -7.3))), 1)
            self.source_temp_thresholds["maxDisagreementF"] = round(float(config.get("maxDisagreementF", self.source_temp_thresholds.get("maxDisagreementF", 3.0))), 1)
            self.source_temp_thresholds["maxJumpF"] = round(float(config.get("maxJumpF", self.source_temp_thresholds.get("maxJumpF", 15.0))), 1)
            primary_sensor = int(config.get("primarySensor", self.source_temp_primary_sensor))
            self.source_temp_primary_sensor = primary_sensor if primary_sensor in (1, 2) else 2
        except (TypeError, ValueError):
            return
        for number in (1, 2):
            self.refresh_source_temp_offset_label(number)
        for key in self.source_temp_thresholds:
            self.refresh_source_temp_threshold_label(key)
        self.refresh_source_temp_primary_widgets()

    def handle_source_temp_telemetry_loaded(self, result: object):
        self._source_temp_loading = False
        if hasattr(self, "source_temp_refresh_button"):
            self.source_temp_refresh_button.setEnabled(True)
        info = result if isinstance(result, dict) else {}
        error = str(info.get("error") or "")
        if error:
            if hasattr(self, "source_temp_footer"):
                self.source_temp_footer.setText(f"Sensor refresh failed: {error}")
            return
        data = info.get("data") if isinstance(info.get("data"), dict) else {}
        config = data.get("config") if isinstance(data.get("config"), dict) else {}
        if config and not self._source_temp_dirty:
            self.apply_source_temp_config(config)
            self._source_temp_config_loaded = True
        if config:
            hardware = self.s.config.setdefault("hardware", {})
            if isinstance(hardware, dict):
                hardware["temperatureSensors"] = copy.deepcopy(config)
        self.render_source_temp_telemetry(data)

    def render_source_temp_telemetry(self, data: dict):
        if not self.source_temp_sensor_labels:
            return
        temperature = data.get("temperature") if isinstance(data.get("temperature"), dict) else {}
        sensors = temperature.get("sensors") if isinstance(temperature.get("sensors"), list) else []
        sensor_map: dict[int, dict] = {}
        for item in sensors:
            if not isinstance(item, dict):
                continue
            try:
                number = int(item.get("sensorNumber") or 0)
            except Exception:
                number = 0
            if number in (1, 2):
                sensor_map[number] = item

        for number in (1, 2):
            item = sensor_map.get(number) or {}
            labels = self.source_temp_sensor_labels[number]
            if item.get("available") and item.get("temperatureF") is not None:
                labels["adjusted"].setText(f"Corrected temperature: {float(item.get('temperatureF')):.1f}°F")
                raw_f = item.get("rawTemperatureF")
                labels["raw"].setText(f"Raw temperature: {float(raw_f):.1f}°F" if raw_f is not None else "Raw temperature: --")
                humidity = item.get("humidity")
                labels["humidity"].setText(f"Humidity: {float(humidity):.1f}%" if humidity is not None else "Humidity: --")
                used = bool(item.get("used"))
                healthy = bool(item.get("healthy", True))
                status = "ACTIVE PANEL SOURCE" if used else "Healthy comparison sensor" if healthy else "FAULT / IGNORED"
                reason = str(item.get("ignoredReason") or item.get("error") or "").strip()
                labels["health"].setText(f"Status: {status}" + (f"\n{reason}" if reason else ""))
                labels["health"].setStyleSheet(
                    "color:#ffbe73; background:rgba(70,30,20,0.45); border-radius:7px; padding:4px 7px;"
                    if not healthy else
                    "color:#8fffd0; background:rgba(5,40,35,0.40); border-radius:7px; padding:4px 7px;"
                )
            else:
                error = str(item.get("error") or "No response")
                labels["adjusted"].setText("Corrected temperature: unavailable")
                labels["raw"].setText("Raw temperature: unavailable")
                labels["humidity"].setText("Humidity: unavailable")
                labels["health"].setText(f"Status: UNAVAILABLE\n{error}")
                labels["health"].setStyleSheet("color:#ff9b9b; background:rgba(60,12,20,0.48); border-radius:7px; padding:4px 7px;")

        panel_temp = temperature.get("temperatureF")
        active = temperature.get("activeSensor")
        delta = temperature.get("temperatureDeltaF")
        health_flag = bool(temperature.get("healthFlag"))
        health_message = str(temperature.get("healthMessage") or "").strip()
        panel_line = f"Panel: {float(panel_temp):.1f}°F" if panel_temp is not None else "Panel: unavailable"
        source_line = f"Source: sensor {active}" if active in (1, 2) else "Source: last trusted / unavailable"
        delta_line = f"Difference: {float(delta):.1f}°F" if delta is not None else "Difference: unavailable"
        self.source_temp_panel_status.setText(f"{panel_line}  ·  {source_line}  ·  {delta_line}" + (f"\n{health_message}" if health_message else ""))
        self.source_temp_panel_status.setStyleSheet(
            "color:#ffe0a8; background:rgba(65,35,10,0.58); border:1px solid rgba(255,183,91,0.65); border-radius:10px; padding:7px 10px;"
            if health_flag else
            "color:#dfffee; background:rgba(5,40,35,0.52); border:1px solid rgba(85,240,190,0.48); border-radius:10px; padding:7px 10px;"
        )
        if not self._source_temp_dirty:
            self.source_temp_footer.setText("Live readings refresh every 3 seconds while this page is open.")

    def save_source_temp_sensors(self):
        if self._source_temp_saving:
            return
        self._source_temp_saving = True
        self.source_temp_save_button.setEnabled(False)
        self.source_temp_footer.setText("Saving onboard temperature-sensor configuration...")
        payload = {
            "config": {
                "sensor1OffsetF": self.source_temp_offsets[1],
                "sensor2OffsetF": self.source_temp_offsets[2],
                "primarySensor": self.source_temp_primary_sensor,
                "fallbackSensor": 1 if self.source_temp_primary_sensor == 2 else 2,
                "maxDisagreementF": self.source_temp_thresholds["maxDisagreementF"],
                "maxJumpF": self.source_temp_thresholds["maxJumpF"],
                "holdLastSeconds": 120,
            }
        }

        def worker():
            try:
                data = self.s.api.post("/api/hardware/temperature-sensors", payload)
                self.tempSensorSaveCompleted.emit({"data": data, "error": None})
            except Exception as exc:
                self.tempSensorSaveCompleted.emit({"data": None, "error": str(exc)})

        threading.Thread(target=worker, name="settings-temperature-sensor-save", daemon=True).start()

    def handle_source_temp_save_completed(self, result: object):
        self._source_temp_saving = False
        if hasattr(self, "source_temp_save_button"):
            self.source_temp_save_button.setEnabled(True)
        info = result if isinstance(result, dict) else {}
        error = str(info.get("error") or "")
        if error:
            self.source_temp_footer.setText(f"Save failed: {error}")
            return
        data = info.get("data") if isinstance(info.get("data"), dict) else {}
        config = data.get("config") if isinstance(data.get("config"), dict) else {}
        if config:
            self.apply_source_temp_config(config)
            hardware = self.s.config.setdefault("hardware", {})
            if isinstance(hardware, dict):
                hardware["temperatureSensors"] = copy.deepcopy(config)
        self._source_temp_dirty = False
        self._source_temp_config_loaded = True
        self.render_source_temp_telemetry(data)
        self.source_temp_footer.setText(str(data.get("message") or "Onboard temperature-sensor configuration saved."))
        self.saved.emit()


    def open_audio_settings_from_comfort(self):
        dlg = AudioSettingsDialog(self.s, self)
        dlg.set_settings_mode("groups")
        dlg.saved.connect(self.saved.emit)
        dlg.exec_()

    def screen_checkbox_style(self) -> str:
        return """
            QCheckBox {
                color:#f7fbff;
                font-weight:900;
                font-size:11px;
                spacing:10px;
                padding:6px 8px;
                background:rgba(5,10,20,0.34);
                border:1px solid rgba(160,180,210,0.18);
                border-radius:9px;
            }
            QCheckBox::indicator {
                width:24px;
                height:24px;
                border:2px solid rgba(85,240,255,0.60);
                border-radius:6px;
                background:rgba(4,10,20,0.92);
            }
            QCheckBox::indicator:checked {
                background:#36d99c;
                border:2px solid #8fffd0;
            }
        """

    def screen_compact_checkbox_style(self) -> str:
        return """
            QCheckBox {
                color:#f7fbff;
                font-weight:900;
                font-size:10px;
                spacing:7px;
                padding:2px 4px;
                background:transparent;
                border:0;
            }
            QCheckBox::indicator {
                width:20px;
                height:20px;
                border:2px solid rgba(85,240,255,0.60);
                border-radius:5px;
                background:rgba(4,10,20,0.92);
            }
            QCheckBox::indicator:checked {
                background:#36d99c;
                border:2px solid #8fffd0;
            }
        """

    def screen_brightness_rules(self) -> list[dict]:
        rules = self.screen_setting_values.get("brightnessEntityRules")
        if not isinstance(rules, list):
            rules = []
            self.screen_setting_values["brightnessEntityRules"] = rules
        return rules

    def adjust_screen_brightness_setting(self, key: str, direction: int):
        try:
            current = int(round(float(self.screen_setting_values.get(key, 40))))
        except (TypeError, ValueError):
            current = 40
        self.screen_setting_values[key] = int(clamp(current + (5 * int(direction)), SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
        self.mark_display_settings_dirty()

    def adjust_brightness_rule_percent(self, index: int, direction: int):
        rules = self.screen_brightness_rules()
        if index < 0 or index >= len(rules):
            return
        try:
            current = int(round(float(rules[index].get("brightnessPercent", 40))))
        except (TypeError, ValueError):
            current = 40
        rules[index]["brightnessPercent"] = int(clamp(current + (5 * int(direction)), SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
        self.mark_display_settings_dirty()

    def toggle_brightness_rule_state(self, index: int):
        rules = self.screen_brightness_rules()
        if index < 0 or index >= len(rules):
            return
        rule = rules[index]
        next_state = "off" if str(rule.get("state") or "on").lower() == "on" else "on"
        entity_id = str(rule.get("entityId") or "")
        if any(i != index and str(item.get("entityId") or "") == entity_id and str(item.get("state") or "on").lower() == next_state for i, item in enumerate(rules)):
            QMessageBox.information(self, "Brightness Rule", f"A {next_state.upper()} rule already exists for this entry.")
            return
        rule["state"] = next_state
        self.mark_display_settings_dirty()

    def remove_brightness_rule(self, index: int):
        rules = self.screen_brightness_rules()
        if index < 0 or index >= len(rules):
            return
        rules.pop(index)
        self.mark_display_settings_dirty()

    def set_screen_brightness_time(self, key: str, combo: QComboBox):
        value = str(combo.currentData() or "").strip()
        hour, minute = parse_schedule_time_24h(value, 22 if key == "brightnessTimeStart" else 7, 0)
        self.screen_setting_values[key] = f"{hour:02d}:{minute:02d}"
        self.mark_display_settings_dirty()

    def populate_brightness_time_combo(self, combo: QComboBox, value: str):
        combo.blockSignals(True)
        try:
            combo.clear()
            values = [f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 15, 30, 45)]
            current_hour, current_minute = parse_schedule_time_24h(value)
            current = f"{current_hour:02d}:{current_minute:02d}"
            if current not in values:
                values.append(current)
                values.sort()
            for item in values:
                combo.addItem(format_schedule_time_12h(item), item)
            index = combo.findData(current)
            combo.setCurrentIndex(max(0, index))
        finally:
            combo.blockSignals(False)

    def add_brightness_entity_rule(self):
        if self._brightness_entities_loading:
            return
        self._brightness_entities_loading = True
        if hasattr(self, "screen_brightness_add_rule_button"):
            self.screen_brightness_add_rule_button.setEnabled(False)
            self.screen_brightness_add_rule_button.setText("Loading…")

        saved = [copy.deepcopy(rule) for rule in self.screen_brightness_rules() if isinstance(rule, dict)]

        def worker():
            try:
                data = self.s.api.post(
                    "/api/ha/entities",
                    self.s.ha_payload({"domains": ["binary_sensor", "input_boolean", "switch"]}),
                )
                self.brightnessEntitiesLoaded.emit({"entities": data.get("entities") or [], "saved": saved, "error": None})
            except Exception as exc:
                self.brightnessEntitiesLoaded.emit({"entities": [], "saved": saved, "error": str(exc)})

        threading.Thread(target=worker, name="screen-brightness-entities", daemon=True).start()

    def handle_brightness_entities_loaded(self, info: object):
        self._brightness_entities_loading = False
        if hasattr(self, "screen_brightness_add_rule_button"):
            self.screen_brightness_add_rule_button.setEnabled(True)
            self.screen_brightness_add_rule_button.setText("Add Entity Rule")
        data = info if isinstance(info, dict) else {}
        by_id: dict[str, dict] = {}
        for item in list(data.get("entities") or []) + list(data.get("saved") or []):
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not entity_id or "." not in entity_id:
                continue
            domain = str(item.get("domain") or entity_id.split(".", 1)[0]).strip().lower()
            if domain not in {"binary_sensor", "input_boolean", "switch"}:
                continue
            by_id[entity_id] = {
                "entityId": entity_id,
                "name": str(item.get("name") or item.get("friendly_name") or entity_id),
                "domain": domain,
                "state": str(item.get("state") or ""),
            }
        entities = sorted(by_id.values(), key=lambda item: str(item.get("name") or item.get("entityId") or "").lower())
        if not entities:
            error = str(data.get("error") or "").strip()
            QMessageBox.warning(self, "Brightness Rule", error or "No Home Assistant binary sensor, input boolean, or switch entries were found.")
            return
        dlg = EntityPickerDialog("Choose Brightness Trigger", entities, self)
        dlg.selected.connect(self.add_selected_brightness_rule)
        dlg.exec_()

    def add_selected_brightness_rule(self, entity: dict):
        entity_id = str(entity.get("entityId") or entity.get("entity_id") or "").strip()
        if not entity_id:
            return
        rules = self.screen_brightness_rules()
        existing_states = {
            str(rule.get("state") or "on").lower()
            for rule in rules
            if str(rule.get("entityId") or "") == entity_id
        }
        if "on" not in existing_states:
            state = "on"
        elif "off" not in existing_states:
            state = "off"
        else:
            QMessageBox.information(self, "Brightness Rule", "This entry already has both ON and OFF brightness rules.")
            return
        rules.append({
            "entityId": entity_id,
            "name": str(entity.get("name") or entity.get("friendly_name") or entity_id),
            "domain": str(entity.get("domain") or entity_id.split(".", 1)[0]),
            "state": state,
            "brightnessPercent": 40 if state == "on" else int(self.screen_setting_values.get("brightnessNormalPercent", SCREEN_BRIGHTNESS_DEFAULT_PERCENT)),
        })
        self.mark_display_settings_dirty()

    def refresh_brightness_rule_rows(self):
        layout = getattr(self, "screen_brightness_rules_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        rules = self.screen_brightness_rules()
        if not rules:
            empty = QLabel("No entity rules. Add one to change brightness when a Home Assistant entry turns ON or OFF.")
            empty.setWordWrap(True)
            empty.setFont(font(8, QFont.Bold))
            empty.setStyleSheet("color:#91a5c0; background:rgba(5,10,20,0.26); border:1px dashed rgba(160,180,210,0.20); border-radius:8px; padding:7px;")
            layout.addWidget(empty)
            return
        for index, rule in enumerate(rules):
            row = QFrame()
            row.setStyleSheet("background:rgba(5,10,20,0.36); border:1px solid rgba(160,180,210,0.18); border-radius:9px;")
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(8, 5, 8, 5)
            row_lay.setSpacing(6)
            name = str(rule.get("name") or rule.get("entityId") or "Brightness trigger")
            entity_id = str(rule.get("entityId") or "")
            label = QLabel(f"{name}\n{entity_id}")
            label.setFont(font(8, QFont.Black))
            label.setStyleSheet("color:#e8f1ff; background:transparent; border:0;")
            label.setWordWrap(True)
            state = str(rule.get("state") or "on").lower()
            state_button = RoundButton(f"When {state.upper()}", active=(state == "on"), min_h=32)
            state_button.setMinimumWidth(104)
            state_button.clicked.connect(lambda checked=False, i=index: self.toggle_brightness_rule_state(i))
            minus = RoundButton("−", active=False, min_h=32)
            plus = RoundButton("+", active=True, min_h=32)
            minus.setFixedWidth(42)
            plus.setFixedWidth(42)
            value = QLabel(f"{int(rule.get('brightnessPercent', 40))}%")
            value.setAlignment(Qt.AlignCenter)
            value.setMinimumWidth(58)
            value.setFont(font(10, QFont.Black))
            value.setStyleSheet("color:#ffffff; background:rgba(85,240,255,0.10); border:1px solid rgba(85,240,255,0.28); border-radius:7px; padding:4px;")
            minus.clicked.connect(lambda checked=False, i=index: self.adjust_brightness_rule_percent(i, -1))
            plus.clicked.connect(lambda checked=False, i=index: self.adjust_brightness_rule_percent(i, 1))
            remove = RoundButton("Remove", active=False, kind="danger", min_h=32)
            remove.setMinimumWidth(86)
            remove.clicked.connect(lambda checked=False, i=index: self.remove_brightness_rule(i))
            row_lay.addWidget(label, 1)
            row_lay.addWidget(state_button)
            row_lay.addWidget(minus)
            row_lay.addWidget(value)
            row_lay.addWidget(plus)
            row_lay.addWidget(remove)
            layout.addWidget(row)

    def build_compact_screen_timeout_row(self, checkbox: QCheckBox, key: str) -> QFrame:
        row = QFrame()
        row.setStyleSheet("background:rgba(5,10,20,0.30); border:1px solid rgba(160,180,210,0.16); border-radius:9px;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(7, 4, 7, 4)
        lay.setSpacing(6)
        checkbox.setStyleSheet(self.screen_compact_checkbox_style())
        minus = RoundButton("−", active=False, min_h=30)
        plus = RoundButton("+", active=True, min_h=30)
        minus.setFixedWidth(40)
        plus.setFixedWidth(40)
        value = QLabel("")
        value.setAlignment(Qt.AlignCenter)
        value.setMinimumWidth(76)
        value.setFont(font(9, QFont.Black))
        value.setStyleSheet("color:#ffffff; background:rgba(5,10,20,0.56); border:1px solid rgba(85,240,255,0.28); border-radius:7px; padding:3px;")
        minus.clicked.connect(lambda checked=False, k=key: self.adjust_screen_timeout(k, -1))
        plus.clicked.connect(lambda checked=False, k=key: self.adjust_screen_timeout(k, 1))
        lay.addWidget(checkbox, 1)
        lay.addWidget(minus)
        lay.addWidget(value)
        lay.addWidget(plus)
        if key == "inactivityAutoOffMinutes":
            self.screen_inactivity_value = value
        else:
            self.screen_motion_sleep_value = value
        return row

    def build_brightness_percent_row(self, label_text: str, key: str) -> QFrame:
        row = QFrame()
        row.setStyleSheet("background:rgba(5,10,20,0.30); border:1px solid rgba(160,180,210,0.16); border-radius:9px;")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(6)
        label = QLabel(label_text)
        label.setFont(font(9, QFont.Black))
        label.setStyleSheet("color:#e6efff; background:transparent; border:0;")
        minus = RoundButton("−", active=False, min_h=31)
        plus = RoundButton("+", active=True, min_h=31)
        minus.setFixedWidth(42)
        plus.setFixedWidth(42)
        value = QLabel("")
        value.setAlignment(Qt.AlignCenter)
        value.setMinimumWidth(64)
        value.setFont(font(10, QFont.Black))
        value.setStyleSheet("color:#ffffff; background:rgba(85,240,255,0.10); border:1px solid rgba(85,240,255,0.28); border-radius:7px; padding:4px;")
        minus.clicked.connect(lambda checked=False, k=key: self.adjust_screen_brightness_setting(k, -1))
        plus.clicked.connect(lambda checked=False, k=key: self.adjust_screen_brightness_setting(k, 1))
        lay.addWidget(label, 1)
        lay.addWidget(minus)
        lay.addWidget(value)
        lay.addWidget(plus)
        if key == "brightnessNormalPercent":
            self.screen_brightness_normal_value = value
        elif key == "brightnessTimePercent":
            self.screen_brightness_time_value = value
        return row

    def write_screen_setting_values_to_config(self):
        display = self.s.config.setdefault("display", {})
        if not isinstance(display, dict):
            display = {}
            self.s.config["display"] = display
        display.update({
            "inactivityAutoOffEnabled": bool(self.screen_setting_values.get("inactivityAutoOffEnabled", True)),
            "inactivityAutoOffMinutes": int(self.screen_setting_values.get("inactivityAutoOffMinutes", DEFAULT_SCREEN_INACTIVITY_MINUTES)),
            "motionAutoSleepEnabled": bool(self.screen_setting_values.get("motionAutoSleepEnabled", False)),
            "motionAutoSleepMinutes": int(self.screen_setting_values.get("motionAutoSleepMinutes", 5)),
            "motionAutoWakeEnabled": bool(self.screen_setting_values.get("motionAutoWakeEnabled", False)),
            "timeoutAdjustmentStepMinutes": int(self.screen_setting_values.get("timeoutAdjustmentStepMinutes", 1)),
            "brightnessNormalPercent": int(self.screen_setting_values.get("brightnessNormalPercent", SCREEN_BRIGHTNESS_DEFAULT_PERCENT)),
            "brightnessTimeEnabled": bool(self.screen_setting_values.get("brightnessTimeEnabled", False)),
            "brightnessTimeStart": str(self.screen_setting_values.get("brightnessTimeStart", "22:00")),
            "brightnessTimeEnd": str(self.screen_setting_values.get("brightnessTimeEnd", "07:00")),
            "brightnessTimePercent": int(self.screen_setting_values.get("brightnessTimePercent", 40)),
            "brightnessEntityRules": copy.deepcopy(self.screen_brightness_rules()),
        })

    def mark_display_settings_dirty(self):
        self.write_screen_setting_values_to_config()
        self._display_settings_dirty = True
        self._last_settings_error = ""
        self.refresh_screen_setting_controls()

    def toggle_screen_setting(self, key: str, checked: bool):
        self.screen_setting_values[key] = bool(checked)
        self.mark_display_settings_dirty()

    def set_screen_timeout_step(self, minutes: int):
        step = 5 if int(minutes) == 5 else 1
        self.screen_setting_values["timeoutAdjustmentStepMinutes"] = step
        self.mark_display_settings_dirty()

    def adjust_screen_timeout(self, key: str, direction: int):
        step = int(self.screen_setting_values.get("timeoutAdjustmentStepMinutes", 1))
        step = 5 if step == 5 else 1
        try:
            current = int(round(float(self.screen_setting_values.get(key, 5))))
        except (TypeError, ValueError):
            current = 5
        self.screen_setting_values[key] = int(clamp(current + (step * int(direction)), 1, 720))
        self.mark_display_settings_dirty()

    def refresh_screen_setting_controls(self):
        inactivity = int(self.screen_setting_values.get("inactivityAutoOffMinutes", DEFAULT_SCREEN_INACTIVITY_MINUTES))
        motion = int(self.screen_setting_values.get("motionAutoSleepMinutes", 5))
        step = int(self.screen_setting_values.get("timeoutAdjustmentStepMinutes", 1))
        if hasattr(self, "screen_inactivity_value"):
            self.screen_inactivity_value.setText(f"{inactivity} min")
        if hasattr(self, "screen_motion_sleep_value"):
            self.screen_motion_sleep_value.setText(f"{motion} min")
        if hasattr(self, "screen_step_one_button"):
            self.screen_step_one_button.setActive(step == 1)
        if hasattr(self, "screen_step_five_button"):
            self.screen_step_five_button.setActive(step == 5)
        if hasattr(self, "screen_inactivity_checkbox"):
            self.screen_inactivity_checkbox.blockSignals(True)
            self.screen_inactivity_checkbox.setChecked(bool(self.screen_setting_values.get("inactivityAutoOffEnabled", True)))
            self.screen_inactivity_checkbox.blockSignals(False)
        if hasattr(self, "screen_motion_sleep_checkbox"):
            self.screen_motion_sleep_checkbox.blockSignals(True)
            self.screen_motion_sleep_checkbox.setChecked(bool(self.screen_setting_values.get("motionAutoSleepEnabled", False)))
            self.screen_motion_sleep_checkbox.blockSignals(False)
        if hasattr(self, "screen_motion_wake_checkbox"):
            self.screen_motion_wake_checkbox.blockSignals(True)
            self.screen_motion_wake_checkbox.setChecked(bool(self.screen_setting_values.get("motionAutoWakeEnabled", False)))
            self.screen_motion_wake_checkbox.blockSignals(False)
        if hasattr(self, "screen_brightness_normal_value"):
            self.screen_brightness_normal_value.setText(f"{int(self.screen_setting_values.get('brightnessNormalPercent', SCREEN_BRIGHTNESS_DEFAULT_PERCENT))}%")
        if hasattr(self, "screen_brightness_time_value"):
            self.screen_brightness_time_value.setText(f"{int(self.screen_setting_values.get('brightnessTimePercent', 40))}%")
        if hasattr(self, "screen_brightness_time_checkbox"):
            self.screen_brightness_time_checkbox.blockSignals(True)
            self.screen_brightness_time_checkbox.setChecked(bool(self.screen_setting_values.get("brightnessTimeEnabled", False)))
            self.screen_brightness_time_checkbox.blockSignals(False)
        if hasattr(self, "screen_brightness_time_start_combo"):
            self.populate_brightness_time_combo(self.screen_brightness_time_start_combo, str(self.screen_setting_values.get("brightnessTimeStart", "22:00")))
        if hasattr(self, "screen_brightness_time_end_combo"):
            self.populate_brightness_time_combo(self.screen_brightness_time_end_combo, str(self.screen_setting_values.get("brightnessTimeEnd", "07:00")))
        self.refresh_brightness_rule_rows()

    def build_screen_timeout_control(self, title: str, key: str) -> QFrame:
        panel = QFrame()
        panel.setStyleSheet("background:rgba(5,10,20,0.34); border:1px solid rgba(160,180,210,0.18); border-radius:10px;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 7, 10, 9)
        layout.setSpacing(5)
        label = QLabel(title)
        label.setWordWrap(True)
        label.setFont(font(8, QFont.Black))
        label.setStyleSheet("color:#c4d0e5; background:transparent; border:0;")
        row = QHBoxLayout()
        row.setSpacing(7)
        minus = RoundButton("−", active=True, min_h=34)
        plus = RoundButton("+", active=True, min_h=34)
        value = QLabel("")
        value.setAlignment(Qt.AlignCenter)
        value.setMinimumHeight(34)
        value.setMinimumWidth(92)
        value.setFont(font(11, QFont.Black))
        value.setStyleSheet("color:#ffffff; background:rgba(5,10,20,0.62); border:1px solid rgba(85,240,255,0.38); border-radius:8px;")
        minus.clicked.connect(lambda checked=False, k=key: self.adjust_screen_timeout(k, -1))
        plus.clicked.connect(lambda checked=False, k=key: self.adjust_screen_timeout(k, 1))
        row.addWidget(minus)
        row.addWidget(value, 1)
        row.addWidget(plus)
        layout.addWidget(label)
        layout.addLayout(row)
        if key == "inactivityAutoOffMinutes":
            self.screen_inactivity_value = value
        else:
            self.screen_motion_sleep_value = value
        return panel


    def current_screen_orientation(self) -> str:
        display = self.s.config.setdefault("display", {}) if isinstance(self.s.config, dict) else {}
        if not isinstance(display, dict):
            display = {}
            self.s.config["display"] = display
        return normalize_screen_orientation(display.get("screenOrientation"))

    def screen_orientation_summary_text(self) -> str:
        return f"Current: {screen_orientation_label(self.current_screen_orientation())}"

    def refresh_screen_orientation_buttons(self):
        current = self.current_screen_orientation()
        label = getattr(self, "screen_orientation_label", None)
        if label is not None:
            label.setText(self.screen_orientation_summary_text())
            label.repaint()
        for orientation, attr in (("upright", "screen_upright_button"), ("upside_down", "screen_upside_button")):
            button = getattr(self, attr, None)
            if not button:
                continue
            active = current == orientation
            if hasattr(button, "setActive"):
                button.setActive(active)
            else:
                button.setStyleSheet(button_style(active))

    def apply_screen_orientation_now(self, orientation: str) -> tuple[bool, str]:
        orientation = normalize_screen_orientation(orientation)
        script = ROOT_DIR / "scripts" / "apply-screen-orientation.sh"
        display_output = os.environ.get("SMART_THERMOSTAT_DISPLAY_OUTPUT", "DSI-1")
        if script.exists():
            cmd = [str(script), orientation]
        else:
            cmd = ["xrandr", "--output", display_output, "--rotate", screen_orientation_to_xrandr(orientation)]
        env = os.environ.copy()
        env.setdefault("SMART_THERMOSTAT_DISPLAY_OUTPUT", display_output)
        try:
            result = subprocess.run(cmd, cwd=str(ROOT_DIR), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8)
        except Exception as exc:
            return False, str(exc)
        output = (result.stdout or "").strip()
        if result.returncode != 0:
            return False, output or f"rotation command exited {result.returncode}"
        return True, output


    def set_screen_orientation(self, orientation: str):
        if getattr(self, "_screen_orientation_saving", False):
            return
        orientation = normalize_screen_orientation(orientation)
        display = self.s.config.setdefault("display", {})
        if not isinstance(display, dict):
            display = {}
            self.s.config["display"] = display
        previous_orientation = normalize_screen_orientation(display.get("screenOrientation"))
        previous_rotation = str(display.get("xrandrRotation") or screen_orientation_to_xrandr(previous_orientation))
        display["screenOrientation"] = orientation
        display["xrandrRotation"] = screen_orientation_to_xrandr(orientation)
        self.refresh_screen_orientation_buttons()

        self._screen_orientation_saving = True
        for attr in ("screen_upright_button", "screen_upside_button"):
            button = getattr(self, attr, None)
            if button is not None:
                button.setEnabled(False)

        config_snapshot = copy.deepcopy(self.s.config)

        def worker():
            config_record = self.s.api.save_config(config_snapshot)
            ok, detail = self.apply_screen_orientation_now(orientation)
            return {"config": config_record, "ok": bool(ok), "detail": str(detail or "")}

        def finish_buttons():
            self._screen_orientation_saving = False
            for attr in ("screen_upright_button", "screen_upside_button"):
                button = getattr(self, attr, None)
                if button is not None:
                    button.setEnabled(True)

        def done(result):
            finish_buttons()
            data = result if isinstance(result, dict) else {}
            config_record = data.get("config")
            if isinstance(config_record, dict):
                self.s.config = config_record.get("config") or self.s.config
            top = self.window()
            if hasattr(top, "force_panel_geometry"):
                QTimer.singleShot(150, top.force_panel_geometry)
                QTimer.singleShot(550, top.force_panel_geometry)
                QTimer.singleShot(1100, top.force_panel_geometry)
            self.saved.emit()
            self.refresh_screen_orientation_buttons()
            if not bool(data.get("ok")):
                QMessageBox.warning(
                    self,
                    "Screen Settings",
                    "The setting was saved, but the live rotation command failed. Rebooting or restarting the native service should apply it.\n\n"
                    + str(data.get("detail") or ""),
                )

        def failed(error):
            finish_buttons()
            current = self.s.config.setdefault("display", {})
            if isinstance(current, dict):
                current["screenOrientation"] = previous_orientation
                current["xrandrRotation"] = previous_rotation
            self.refresh_screen_orientation_buttons()
            QMessageBox.warning(self, "Screen Settings", f"Could not save/apply rotation:\n{error}")

        self.run_settings_write("screen-orientation", worker, done, failed)

    def build(self):
        t = self.s.thermostat or {}

        jarvis_voice = self.jarvis_voice_config(False)
        jarvis_defaults = self.jarvis_voice_defaults()
        self.jarvis_toggle_buttons: dict[str, RoundButton] = {}
        jarvis = self.add_section("Jarvis", -1, 0, 1, 4)
        jarvis_note = QLabel("Home Assistant handles speech and Sonos playback. This screen controls announcement volume, response text, and how long the JARVIS overlay remains visible.")
        jarvis_note.setWordWrap(True)
        jarvis_note.setFont(font(8, QFont.Bold))
        jarvis_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        jarvis.layout().addWidget(jarvis_note)
        jarvis_grid = self.section_grid(jarvis, 2)
        try:
            jarvis_volume = int(round(float(jarvis_voice.get("announcementVolumePercent", jarvis_defaults["announcementVolumePercent"]))))
        except (TypeError, ValueError):
            jarvis_volume = int(jarvis_defaults["announcementVolumePercent"])
        try:
            jarvis_hold = int(round(float(jarvis_voice.get("responseHoldSeconds", jarvis_defaults["responseHoldSeconds"]))))
        except (TypeError, ValueError):
            jarvis_hold = int(jarvis_defaults["responseHoldSeconds"])
        self.add_section_value(jarvis_grid, "jarvisAnnouncementVolumePercent", "Announcement Volume", jarvis_volume, 0, 0, 1, 100, " %")
        self.add_section_value(jarvis_grid, "jarvisResponseHoldSeconds", "Screen Display Time", jarvis_hold, 0, 1, 0, 15, " sec")
        self.add_jarvis_toggle(jarvis_grid, "enabled", "JARVIS", 1, 0)
        self.add_jarvis_toggle(jarvis_grid, "speak", "Speak on Sonos", 1, 1)
        self.add_jarvis_toggle(jarvis_grid, "showResponseText", "Show Response Text", 2, 0)
        self.add_jarvis_toggle(jarvis_grid, "continueConversation", "Remember Conversation", 2, 1)
        self.add_jarvis_toggle(jarvis_grid, "playfulReplies", "Use Form of Address", 3, 0)
        self.add_jarvis_toggle(jarvis_grid, "funMode", "Processing Phrases", 3, 1)
        jarvis_save_note = QLabel("Changes are applied with Save Settings. Pressing Done on the main settings screen also saves pending JARVIS changes.")
        jarvis_save_note.setWordWrap(True)
        jarvis_save_note.setFont(font(7, QFont.Black))
        jarvis_save_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        jarvis.layout().addWidget(jarvis_save_note)

        device_internet = self.add_section("Device Internet", -2, 0, 1, 2)
        device_note = QLabel("Select the Home Assistant network-access switches controlled by the iPad button on the thermostat screen. The button only appears when at least one device is selected.")
        device_note.setWordWrap(True)
        device_note.setFont(font(8, QFont.Bold))
        device_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        device_internet.layout().addWidget(device_note)
        device_row = QHBoxLayout()
        device_row.setSpacing(8)
        self.device_internet_summary = QLabel(self.device_internet_summary_text())
        self.device_internet_summary.setWordWrap(True)
        self.device_internet_summary.setFont(font(8, QFont.Black))
        self.device_internet_summary.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:8px; padding:7px;")
        choose_devices = RoundButton("Choose Devices", active=True, min_h=34)
        choose_devices.setMinimumWidth(162)
        choose_devices.clicked.connect(self.choose_device_internet_entities)
        device_row.addWidget(self.device_internet_summary, 1)
        device_row.addWidget(choose_devices)
        device_internet.layout().addLayout(device_row)
        device_status = QLabel("MULTI-SELECT   ·   SEARCHABLE   ·   ONLINE = SWITCH ON   ·   OFFLINE/BLOCKED = SWITCH OFF")
        device_status.setWordWrap(True)
        device_status.setFont(font(7, QFont.Black))
        device_status.setStyleSheet("color:#8fffd0; background:transparent; border:0;")
        device_internet.layout().addWidget(device_status)

        alexa_lockout = self.add_section("Alexa Lockout", -2, 2, 1, 2)
        alexa_note = QLabel("Choose one or more Home Assistant Alexa switches this thermostat should control while its screen is locked. Each thermostat stores its own selection.")
        alexa_note.setWordWrap(True)
        alexa_note.setFont(font(8, QFont.Bold))
        alexa_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        alexa_lockout.layout().addWidget(alexa_note)
        alexa_row = QHBoxLayout()
        alexa_row.setSpacing(8)
        self.alexa_lockout_summary = QLabel(self.alexa_lockout_summary_text())
        self.alexa_lockout_summary.setWordWrap(True)
        self.alexa_lockout_summary.setFont(font(8, QFont.Black))
        self.alexa_lockout_summary.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:8px; padding:7px;")
        choose_alexa = RoundButton("Choose Alexa Devices", active=True, min_h=34)
        choose_alexa.setMinimumWidth(176)
        choose_alexa.clicked.connect(self.choose_alexa_lockout_entities)
        alexa_row.addWidget(self.alexa_lockout_summary, 1)
        alexa_row.addWidget(choose_alexa)
        alexa_lockout.layout().addLayout(alexa_row)
        alexa_status = QLabel("MULTI-SELECT   ·   LOCKED = BLOCKED   ·   UNLOCKED = UNBLOCKED")
        alexa_status.setWordWrap(True)
        alexa_status.setFont(font(7, QFont.Black))
        alexa_status.setStyleSheet("color:#8fffd0; background:transparent; border:0;")
        alexa_lockout.layout().addWidget(alexa_status)

        auto_home = self.add_section("Auto Away / Home", 0, 0, 1, 2)
        auto_grid = self.section_grid(auto_home, 2)
        self.add_section_value(auto_grid, "awayHeat", "Heat Away", t.get("awayHeat", 55), 0, 0, 40, 75)
        self.add_section_value(auto_grid, "awayCool", "Cool Away", t.get("awayCool", 85), 0, 1, 75, 100)
        people_head = QHBoxLayout()
        people_head.setSpacing(6)
        self.people_summary = QLabel(self.auto_away_people_summary_text())
        self.people_summary.setWordWrap(True)
        self.people_summary.setFont(font(7, QFont.Black))
        self.people_summary.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:8px; padding:4px;")
        add_people = RoundButton("Choose Away Users", active=True, min_h=28)
        add_people.setMinimumWidth(156)
        add_people.clicked.connect(self.choose_auto_away_people)
        people_head.addWidget(self.people_summary, 1)
        people_head.addWidget(add_people)
        auto_home.layout().addLayout(people_head)

        ranges = self.add_section("Range", 0, 2, 1, 2)
        range_grid = self.section_grid(ranges, 2)
        self.add_section_value(range_grid, "coolMin", "Cool Low", nested_get(t, "limits", "cool", "min", default=65), 0, 0, 50, 90)
        self.add_section_value(range_grid, "coolMax", "Cool High", nested_get(t, "limits", "cool", "max", default=80), 0, 1, 50, 90)
        self.add_section_value(range_grid, "heatMin", "Heat Low", nested_get(t, "limits", "heat", "min", default=60), 1, 0, 40, 80)
        self.add_section_value(range_grid, "heatMax", "Heat High", nested_get(t, "limits", "heat", "max", default=78), 1, 1, 40, 85)

        safety = self.add_section("Safety / Mode Switches", 1, 0, 1, 2)
        safety_grid = self.section_grid(safety, 2)
        self.add_section_value(safety_grid, "safetyLow", "Low Safety", t.get("safetyLow", 55), 0, 0, 40, 75)
        self.add_section_value(safety_grid, "safetyHigh", "High Safety", t.get("safetyHigh", 85), 0, 1, 75, 100)
        self.add_section_value(safety_grid, "autoCoolOutdoorTarget", "Cool Mode Switch", t.get("autoCoolOutdoorTarget", 70), 1, 0, 40, 100)
        self.add_section_value(safety_grid, "autoHeatOutdoorTarget", "Heat Mode Switch", t.get("autoHeatOutdoorTarget", 65), 1, 1, 40, 100)
        self.heat_lockout_button = RoundButton(self.lockout_button_text("heat"), active=bool(t.get("heatLocked")), kind="danger", min_h=30)
        self.heat_lockout_button.clicked.connect(lambda checked=False: self.toggle_lockout("heat"))
        self.cool_lockout_button = RoundButton(self.lockout_button_text("cool"), active=bool(t.get("coolLocked")), kind="danger", min_h=30)
        self.cool_lockout_button.clicked.connect(lambda checked=False: self.toggle_lockout("cool"))
        safety_grid.addWidget(self.heat_lockout_button, 2, 0)
        safety_grid.addWidget(self.cool_lockout_button, 2, 1)

        changeover = self.add_section("Changeover / Fan", 1, 2, 1, 2)
        change_grid = self.section_grid(changeover, 2)
        self.add_section_value(change_grid, "autoChangeoverLockoutMinutes", "Auto Delay", int(float(t.get("autoChangeoverLockoutMinutes", 120))/60), 0, 0, 0, 8, " hr")
        self.add_section_value(change_grid, "manualChangeoverLockoutMinutes", "Manual Delay", t.get("manualChangeoverLockoutMinutes", 10), 0, 1, 0, 60, " min")
        self.add_section_value(change_grid, "coolFanRemainOnMinutes", "Cool Fan", t.get("coolFanRemainOnMinutes", 2), 1, 0, 0, 15, " min", colspan=2)
        fan_row = QHBoxLayout()
        fan_row.setSpacing(6)
        changeover.layout().addLayout(fan_row)
        for f in ["off", "on", "auto"]:
            b = RoundButton(f.capitalize(), active=(t.get("fan") or "auto") == f, min_h=28)
            b.clicked.connect(lambda checked=False, x=f: self.set_thermostat({"fan": x}))
            fan_row.addWidget(b)

        differential = self.add_section("Temperature Differential", 4, 0, 1, 2)
        differential_grid = self.section_grid(differential, 1)
        self.add_section_value(differential_grid, "temperatureDifferential", "Differential", int(float(t.get("temperatureDifferential", 0) or 0)), 0, 0, 0, 5, "°")
        differential_note = QLabel("Cooling restarts at setpoint + differential. Heating restarts at setpoint - differential. 0 keeps exact setpoint behavior.")
        differential_note.setWordWrap(True)
        differential_note.setFont(font(7, QFont.Black))
        differential_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        differential.layout().addWidget(differential_note)

        minimum_runtime = self.add_section("Minimum Runtime", 5, 2, 1, 2)
        runtime_grid = self.section_grid(minimum_runtime, 2)
        self.add_section_value(runtime_grid, "heatMinimumRuntimeMinutes", "Heat", t.get("heatMinimumRuntimeMinutes", 2), 0, 0, 1, 30, " min")
        self.add_section_value(runtime_grid, "coolMinimumRuntimeMinutes", "Cool", t.get("coolMinimumRuntimeMinutes", 2), 0, 1, 1, 30, " min")
        runtime_note = QLabel("Same value is used for minimum ON time and minimum OFF time.")
        runtime_note.setWordWrap(True)
        runtime_note.setFont(font(7, QFont.Black))
        runtime_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        minimum_runtime.layout().addWidget(runtime_note)

        source_control = self.add_section("Internal / External Sources", 2, 0, 1, 4)
        source_note = QLabel("Choose each source independently. Internal uses onboard sensors/GPIO; External uses the selected Home Assistant entity.")
        source_note.setWordWrap(True)
        source_note.setFont(font(7, QFont.Black))
        source_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        source_control.layout().addWidget(source_note)
        source_grid = self.section_grid(source_control, 4)
        source_grid.setColumnStretch(0, 0)
        source_grid.setColumnStretch(1, 0)
        source_grid.setColumnStretch(2, 1)
        source_grid.setColumnStretch(3, 0)
        for row, kind in enumerate(("room", "heat", "cool", "fan")):
            title_label = QLabel(self.source_label(kind))
            title_label.setFont(font(8, QFont.Black))
            title_label.setMinimumWidth(86)
            title_label.setStyleSheet("color:#ffffff; background:transparent; border:0;")
            mode_button = RoundButton(self.source_control_mode(kind).upper(), active=(self.source_control_mode(kind) == "external"), min_h=28)
            mode_button.setMinimumWidth(112)
            mode_button.clicked.connect(lambda checked=False, k=kind: self.toggle_source_control_mode(k))
            summary = QLabel(self.source_summary_text(kind))
            summary.setWordWrap(True)
            summary.setFont(font(7, QFont.Black))
            summary.setMinimumHeight(30)
            summary.setStyleSheet("color:#dfe9ff; background:rgba(5,10,20,0.28); border:1px solid rgba(160,180,210,0.16); border-radius:8px; padding:3px 5px;")
            choose_button = RoundButton("Choose HA", active=(self.source_control_mode(kind) == "external"), min_h=28)
            choose_button.setMinimumWidth(112)
            if kind == "room":
                choose_button.clicked.connect(self.choose_temp_sensor)
            else:
                choose_button.clicked.connect(lambda checked=False, k=kind: self.choose_external_air_entry(k))
            setattr(self, f"{kind}_source_mode_button", mode_button)
            setattr(self, f"{kind}_source_summary", summary)
            setattr(self, f"{kind}_source_choose_button", choose_button)
            source_grid.addWidget(title_label, row, 0)
            source_grid.addWidget(mode_button, row, 1)
            source_grid.addWidget(summary, row, 2)
            source_grid.addWidget(choose_button, row, 3)
        self.update_source_control_widgets()

        source_divider = QFrame()
        source_divider.setFixedHeight(1)
        source_divider.setStyleSheet("background:rgba(85,240,255,0.26); border:0; margin-top:5px;")
        source_control.layout().addWidget(source_divider)
        sensor_header = QHBoxLayout()
        sensor_header.setSpacing(6)
        sensor_title = QLabel("ONBOARD TEMPERATURE SENSORS")
        sensor_title.setFont(font(9, QFont.Black))
        sensor_title.setStyleSheet("color:#46e8ff; letter-spacing:1px; background:transparent; border:0;")
        self.source_temp_defaults_button = RoundButton("Defaults", active=True, min_h=29)
        self.source_temp_refresh_button = RoundButton("Refresh", active=True, min_h=29)
        self.source_temp_save_button = RoundButton("Save Sensors", active=True, min_h=29)
        self.source_temp_defaults_button.clicked.connect(self.restore_source_temp_defaults)
        self.source_temp_refresh_button.clicked.connect(lambda checked=False: self.refresh_source_temp_sensors(force=True))
        self.source_temp_save_button.clicked.connect(self.save_source_temp_sensors)
        sensor_header.addWidget(sensor_title, 1)
        sensor_header.addWidget(self.source_temp_defaults_button)
        sensor_header.addWidget(self.source_temp_refresh_button)
        sensor_header.addWidget(self.source_temp_save_button)
        source_control.layout().addLayout(sensor_header)

        self.source_temp_panel_status = QLabel("Loading onboard temperature-sensor readings...")
        self.source_temp_panel_status.setWordWrap(True)
        self.source_temp_panel_status.setFont(font(8, QFont.Black))
        self.source_temp_panel_status.setStyleSheet("color:#dfe9ff; background:rgba(5,10,20,0.52); border:1px solid rgba(85,240,255,0.34); border-radius:10px; padding:7px 10px;")
        source_control.layout().addWidget(self.source_temp_panel_status)

        primary_panel = QFrame()
        primary_panel.setStyleSheet("background:rgba(5,10,20,0.34); border:1px solid rgba(85,240,255,0.26); border-radius:9px;")
        primary_layout = QHBoxLayout(primary_panel)
        primary_layout.setContentsMargins(9, 6, 9, 6)
        primary_layout.setSpacing(7)
        primary_text = QVBoxLayout()
        primary_text.setSpacing(1)
        primary_title = QLabel("PRIMARY ONBOARD SENSOR")
        primary_title.setFont(font(8, QFont.Black))
        primary_title.setStyleSheet("color:#46e8ff; background:transparent; border:0;")
        self.source_temp_primary_summary = QLabel("")
        self.source_temp_primary_summary.setFont(font(7, QFont.Bold))
        self.source_temp_primary_summary.setStyleSheet("color:#aebdd2; background:transparent; border:0;")
        self.source_temp_primary_summary.setWordWrap(True)
        primary_text.addWidget(primary_title)
        primary_text.addWidget(self.source_temp_primary_summary)
        primary_layout.addLayout(primary_text, 1)
        for number in (1, 2):
            button = RoundButton(f"Sensor {number}", active=(number == self.source_temp_primary_sensor), min_h=32)
            button.setMinimumWidth(128)
            button.clicked.connect(lambda checked=False, n=number: self.set_source_temp_primary_sensor(n))
            self.source_temp_primary_buttons[number] = button
            primary_layout.addWidget(button)
        source_control.layout().addWidget(primary_panel)

        onboard_grid = QGridLayout()
        onboard_grid.setSpacing(8)
        onboard_grid.addWidget(self.build_source_temp_sensor_card(1, "", "0x40"), 0, 0)
        onboard_grid.addWidget(self.build_source_temp_sensor_card(2, "", "0x41"), 0, 1)
        onboard_grid.setColumnStretch(0, 1)
        onboard_grid.setColumnStretch(1, 1)
        source_control.layout().addLayout(onboard_grid)
        self.refresh_source_temp_primary_widgets()

        sensor_health_row = QHBoxLayout()
        sensor_health_row.setSpacing(8)
        sensor_health_row.addWidget(self.build_source_temp_threshold_control("maxDisagreementF", "Maximum difference / warning", 0.5, "°F"), 1)
        sensor_health_row.addWidget(self.build_source_temp_threshold_control("maxJumpF", "Sudden jump / fault", 1.0, "°F"), 1)
        source_control.layout().addLayout(sensor_health_row)
        self.source_temp_health_note = QLabel("")
        self.source_temp_health_note.setWordWrap(True)
        self.source_temp_health_note.setFont(font(7, QFont.Bold))
        self.source_temp_health_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        source_control.layout().addWidget(self.source_temp_health_note)
        self.refresh_source_temp_primary_widgets()
        self.source_temp_footer = QLabel("Sensor settings are stored under hardware.temperatureSensors without replacing other panel configuration.")
        self.source_temp_footer.setWordWrap(True)
        self.source_temp_footer.setFont(font(7, QFont.Bold))
        self.source_temp_footer.setStyleSheet("color:#8fa5c2; background:transparent; border:0;")
        source_control.layout().addWidget(self.source_temp_footer)

        outdoor_source = self.add_section("Outside Temperature", 6, 0, 1, 2)
        selected_outdoor = nested_get(self.s.config, "integrations", "homeAssistant", "outdoorTempEntity", default=None) or nested_get(self.s.config, "integrations", "homeAssistant", "weatherEntity", default=None)
        if isinstance(selected_outdoor, dict):
            outdoor_name = selected_outdoor.get("name") or selected_outdoor.get("friendly_name") or selected_outdoor.get("entityId") or "Outside Sensor"
            outdoor_line = f"{selected_outdoor.get('entityId') or 'selected entry'}"
        else:
            outdoor_name = "Not selected"
            outdoor_line = "Choose outside temp/weather"
        self.outdoor_source_label = QLabel(f"Outside: {compact_name(outdoor_name, 26)}\n{outdoor_line}")
        self.outdoor_source_label.setWordWrap(True)
        self.outdoor_source_label.setFont(font(7, QFont.Black))
        self.outdoor_source_label.setStyleSheet("color:#dfe9ff; background:rgba(5,10,20,0.28); border:1px solid rgba(160,180,210,0.16); border-radius:8px; padding:4px;")
        choose_outdoor = RoundButton("Choose HA", active=True, min_h=28)
        self.outdoor_source_choose_button = choose_outdoor
        choose_outdoor.setMinimumWidth(112)
        choose_outdoor.clicked.connect(self.choose_outdoor_temp_sensor)
        outdoor_source.layout().addWidget(self.outdoor_source_label)
        outdoor_source.layout().addWidget(choose_outdoor)
        sync_section = self.add_section("Sync", 3, 2, 1, 2)
        sync_intro = QLabel("Keep temporary thermostat control syncing separate from the one-time House Sync setup copy.")
        sync_intro.setWordWrap(True)
        sync_intro.setFont(font(8, QFont.Bold))
        sync_intro.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        sync_section.layout().addWidget(sync_intro)

        thermostat_sync_card = QFrame()
        thermostat_sync_card.setStyleSheet("background:rgba(5,10,20,0.42); border:1px solid rgba(85,240,255,0.24); border-radius:10px;")
        thermostat_sync_layout = QVBoxLayout(thermostat_sync_card)
        thermostat_sync_layout.setContentsMargins(10, 8, 10, 9)
        thermostat_sync_layout.setSpacing(6)
        thermostat_sync_title = QLabel("THERMOSTAT SYNC")
        thermostat_sync_title.setFont(font(9, QFont.Black))
        thermostat_sync_title.setStyleSheet("color:#55f0ff; letter-spacing:1px; background:transparent; border:0;")
        thermostat_sync_layout.addWidget(thermostat_sync_title)
        self.sync_peer_summary = QLabel(self.sync_peer_summary_text())
        self.sync_peer_summary.setWordWrap(True)
        self.sync_peer_summary.setFont(font(7, QFont.Black))
        self.sync_peer_summary.setStyleSheet("color:#d6e2f5; background:rgba(255,255,255,0.025); border:1px dashed rgba(160,180,210,0.24); border-radius:8px; padding:5px;")
        choose_sync = RoundButton("Choose Thermostats", active=True, min_h=30)
        choose_sync.setMinimumWidth(156)
        choose_sync.clicked.connect(self.choose_sync_thermostats)
        sync_row = QHBoxLayout()
        sync_row.setSpacing(7)
        sync_row.addWidget(self.sync_peer_summary, 1)
        sync_row.addWidget(choose_sync)
        thermostat_sync_layout.addLayout(sync_row)
        sync_note = QLabel("The main-screen Sync button copies Heat, Cool, Off, Arriving, and setpoint changes for 30 seconds. Away and Return Home stay local.")
        sync_note.setWordWrap(True)
        sync_note.setFont(font(7, QFont.Black))
        sync_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        thermostat_sync_layout.addWidget(sync_note)
        sync_section.layout().addWidget(thermostat_sync_card)

        house_sync_card = QFrame()
        house_sync_card.setStyleSheet("background:rgba(5,10,20,0.42); border:1px solid rgba(160,180,210,0.22); border-radius:10px;")
        house_sync_layout = QVBoxLayout(house_sync_card)
        house_sync_layout.setContentsMargins(10, 8, 10, 9)
        house_sync_layout.setSpacing(6)
        house_title = QLabel("HOUSE SYNC")
        house_title.setFont(font(9, QFont.Black))
        house_title.setStyleSheet("color:#55f0ff; letter-spacing:1px; background:transparent; border:0;")
        house_sync_layout.addWidget(house_title)
        self.house_sync_source_summary = QLabel(self.house_sync_source_summary_text())
        self.house_sync_source_summary.setWordWrap(True)
        self.house_sync_source_summary.setFont(font(7, QFont.Black))
        self.house_sync_source_summary.setStyleSheet("color:#d6e2f5; background:rgba(255,255,255,0.025); border:1px dashed rgba(160,180,210,0.24); border-radius:8px; padding:5px;")
        self.choose_house_sync_button = RoundButton("Choose Source", active=True, min_h=30)
        self.choose_house_sync_button.setMinimumWidth(140)
        self.choose_house_sync_button.clicked.connect(self.choose_house_sync_source)
        house_source_row = QHBoxLayout()
        house_source_row.setSpacing(7)
        house_source_row.addWidget(self.house_sync_source_summary, 1)
        house_source_row.addWidget(self.choose_house_sync_button)
        house_sync_layout.addLayout(house_source_row)
        self.house_sync_button = RoundButton("Sync House Setup", active=True, min_h=34)
        self.house_sync_button.setMinimumWidth(190)
        self.house_sync_button.clicked.connect(self.start_house_sync)
        self.house_sync_button.hide()
        house_action_row = QHBoxLayout()
        house_action_row.addStretch(1)
        house_action_row.addWidget(self.house_sync_button)
        house_sync_layout.addLayout(house_action_row)
        house_note = QLabel("One-time copy of Blinds, Lights, and Room setup only. Thermostat, security, Home Assistant, schedules, and JARVIS settings are not copied.")
        house_note.setWordWrap(True)
        house_note.setFont(font(7, QFont.Black))
        house_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        house_sync_layout.addWidget(house_note)
        sync_section.layout().addWidget(house_sync_card)
        self.update_house_sync_widgets()

        intimacy = self.add_section("Intimacy Hold", 4, 1, 1, 2)
        intimacy_note = QLabel("Set the temperature used by the two-person button on the main thermostat screen. The hold remains active for six hours unless you turn it off early.")
        intimacy_note.setWordWrap(True)
        intimacy_note.setFont(font(8, QFont.Bold))
        intimacy_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        intimacy.layout().addWidget(intimacy_note)
        intimacy_grid = self.section_grid(intimacy, 1)
        intimacy_target = intimacy_hold_target_f(t)
        self.add_section_value(intimacy_grid, "intimacyHoldTargetTemp", "Hold Temperature", intimacy_target, 0, 0, 40, 100, "°F")
        intimacy_behavior = QLabel("While active, schedules, Away/Arriving, and manual setpoint changes are ignored. Changing this value while the hold is active moves the active hold to the new temperature after Save Settings.")
        intimacy_behavior.setWordWrap(True)
        intimacy_behavior.setFont(font(7, QFont.Black))
        intimacy_behavior.setStyleSheet("color:#c6d4e7; background:rgba(5,10,20,0.34); border:1px solid rgba(229,151,201,0.20); border-radius:8px; padding:6px 8px;")
        intimacy.layout().addWidget(intimacy_behavior)

        person_tracking = self.add_section("Person Tracking", 4, 2, 1, 2)
        self.person_tracking_summary = QLabel(self.people_summary_text())
        self.person_tracking_summary.setWordWrap(True)
        self.person_tracking_summary.setFont(font(7, QFont.Black))
        self.person_tracking_summary.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:8px; padding:4px;")
        pick_people = RoundButton("Choose Tracking", active=True, min_h=28)
        pick_people.setMinimumWidth(148)
        pick_people.clicked.connect(self.choose_person_tracking_people)
        person_row = QHBoxLayout()
        person_row.setSpacing(6)
        person_row.addWidget(self.person_tracking_summary, 1)
        person_row.addWidget(pick_people)
        person_tracking.layout().addLayout(person_row)
        person_note = QLabel("These entries only decide what appears on the main thermostat screen. They do not control Auto Away/Home.")
        person_note.setWordWrap(True)
        person_note.setFont(font(7, QFont.Black))
        person_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        person_tracking.layout().addWidget(person_note)

        door_source = self.add_section("Doors / Comfort Pause", 3, 0, 1, 2)
        self.inside_door_label = QLabel(self.inside_door_summary_text())
        self.inside_door_label.setWordWrap(True)
        self.inside_door_label.setFont(font(7, QFont.Black))
        self.inside_door_label.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:8px; padding:4px;")
        door_source.layout().addWidget(self.inside_door_label)
        pause = t.get("pauseFunction") if isinstance(t.get("pauseFunction"), dict) else {}
        door_grid = self.section_grid(door_source, 2)
        self.add_section_value(door_grid, "doorPauseDurationMinutes", "Door Delay", int(float(pause.get("durationMinutes") or 5)), 0, 0, 1, 60, " min")
        choose_door = RoundButton("Choose Entry", active=True, min_h=28)
        self.inside_door_choose_button = choose_door
        choose_door.clicked.connect(self.choose_inside_door_entry)
        door_grid.addWidget(choose_door, 0, 1)

        codes = self.add_section("Security Codes", 5, 0, 1, 2)
        code_grid = QGridLayout()
        code_grid.setContentsMargins(0, 0, 0, 0)
        code_grid.setHorizontalSpacing(6)
        code_grid.setVerticalSpacing(4)
        codes.layout().addLayout(code_grid)
        alarm_lab = QLabel("Alarm Disarm")
        alarm_lab.setFont(font(7, QFont.Black))
        alarm_lab.setStyleSheet("color:#c4d0e5; background:transparent; border:0;")
        settings_lab = QLabel("Settings Access")
        settings_lab.setFont(font(7, QFont.Black))
        settings_lab.setStyleSheet("color:#c4d0e5; background:transparent; border:0;")
        current_security = str((self.s.config.get("alarm") or {}).get("disarmCode") or "")
        self.security_code_field = self.code_field(current_security, self.edit_security_code)
        self.security_code_field.setPlaceholderText("Alarm disarm code")
        current_settings = str((self.s.config.get("security") or {}).get("settingsCode") or "")
        self.settings_code_field = self.code_field(current_settings, self.edit_settings_code)
        self.settings_code_field.setPlaceholderText("Settings access code")
        code_grid.addWidget(alarm_lab, 0, 0)
        code_grid.addWidget(settings_lab, 0, 1)
        code_grid.addWidget(self.security_code_field, 1, 0)
        code_grid.addWidget(self.settings_code_field, 1, 1)
        code_grid.setColumnStretch(0, 1)
        code_grid.setColumnStretch(1, 1)

        unit = self.add_section("Thermostat Unit", 6, 2, 1, 2)
        unit_row = QHBoxLayout()
        unit_row.setSpacing(6)
        self.thermostat_name_label = QLabel(self.thermostat_name_summary_text())
        self.thermostat_name_label.setWordWrap(True)
        self.thermostat_name_label.setFont(font(8, QFont.Black))
        self.thermostat_name_label.setStyleSheet("color:#dfe9ff; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:8px; padding:5px;")
        edit_name = RoundButton("Change Name", active=True, min_h=28)
        edit_name.setMinimumWidth(126)
        edit_name.clicked.connect(self.edit_thermostat_name)
        unit_row.addWidget(self.thermostat_name_label, 1)
        unit_row.addWidget(edit_name)
        unit.layout().addLayout(unit_row)

        display = self.add_section("Screen Settings", 7, 0, 1, 4)
        rotation_title = QLabel("SCREEN ROTATION")
        rotation_title.setFont(font(9, QFont.Black))
        rotation_title.setStyleSheet("color:#46e8ff; letter-spacing:1px; background:transparent; border:0;")
        display.layout().addWidget(rotation_title)
        display_row = QHBoxLayout()
        display_row.setSpacing(8)
        self.screen_orientation_label = QLabel(self.screen_orientation_summary_text())
        self.screen_orientation_label.setWordWrap(True)
        self.screen_orientation_label.setFont(font(8, QFont.Black))
        self.screen_orientation_label.setStyleSheet("color:#dfe9ff; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:8px; padding:5px;")
        self.screen_upright_button = RoundButton("Upright", active=(self.current_screen_orientation() == "upright"), min_h=30)
        self.screen_upside_button = RoundButton("Upside Down", active=(self.current_screen_orientation() == "upside_down"), min_h=30)
        self.screen_upright_button.setMinimumWidth(132)
        self.screen_upside_button.setMinimumWidth(156)
        self.screen_upright_button.clicked.connect(lambda checked=False: self.set_screen_orientation("upright"))
        self.screen_upside_button.clicked.connect(lambda checked=False: self.set_screen_orientation("upside_down"))
        display_row.addWidget(self.screen_orientation_label, 1)
        display_row.addWidget(self.screen_upright_button)
        display_row.addWidget(self.screen_upside_button)
        display.layout().addLayout(display_row)
        rotation_note = QLabel("Uses the two landscape orientations only. Touch is remapped after the screen rotates.")
        rotation_note.setWordWrap(True)
        rotation_note.setFont(font(7, QFont.Black))
        rotation_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        display.layout().addWidget(rotation_note)

        screen_divider = QFrame()
        screen_divider.setFixedHeight(1)
        screen_divider.setStyleSheet("background:rgba(85,240,255,0.26); border:0; margin-top:5px;")
        display.layout().addWidget(screen_divider)
        sleep_title = QLabel("SCREEN AUTO ON / OFF")
        sleep_title.setFont(font(9, QFont.Black))
        sleep_title.setStyleSheet("color:#46e8ff; letter-spacing:1px; background:transparent; border:0;")
        display.layout().addWidget(sleep_title)

        self.screen_inactivity_checkbox = QCheckBox("Inactivity auto off")
        self.screen_motion_sleep_checkbox = QCheckBox("No-motion auto off")
        self.screen_motion_wake_checkbox = QCheckBox("Motion auto on")
        self.screen_inactivity_checkbox.toggled.connect(lambda checked: self.toggle_screen_setting("inactivityAutoOffEnabled", checked))
        self.screen_motion_sleep_checkbox.toggled.connect(lambda checked: self.toggle_screen_setting("motionAutoSleepEnabled", checked))
        self.screen_motion_wake_checkbox.toggled.connect(lambda checked: self.toggle_screen_setting("motionAutoWakeEnabled", checked))

        auto_grid = QGridLayout()
        auto_grid.setHorizontalSpacing(7)
        auto_grid.setVerticalSpacing(5)
        auto_grid.addWidget(self.build_compact_screen_timeout_row(self.screen_inactivity_checkbox, "inactivityAutoOffMinutes"), 0, 0)
        auto_grid.addWidget(self.build_compact_screen_timeout_row(self.screen_motion_sleep_checkbox, "motionAutoSleepMinutes"), 0, 1)
        auto_grid.setColumnStretch(0, 1)
        auto_grid.setColumnStretch(1, 1)
        display.layout().addLayout(auto_grid)

        auto_bottom = QHBoxLayout()
        auto_bottom.setSpacing(7)
        self.screen_motion_wake_checkbox.setStyleSheet(self.screen_compact_checkbox_style())
        step_label = QLabel("TIME STEP")
        step_label.setFont(font(8, QFont.Black))
        step_label.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        self.screen_step_one_button = RoundButton("1 min", active=True, min_h=29)
        self.screen_step_five_button = RoundButton("5 min", active=False, min_h=29)
        self.screen_step_one_button.clicked.connect(lambda checked=False: self.set_screen_timeout_step(1))
        self.screen_step_five_button.clicked.connect(lambda checked=False: self.set_screen_timeout_step(5))
        auto_bottom.addWidget(self.screen_motion_wake_checkbox, 1)
        auto_bottom.addWidget(step_label)
        auto_bottom.addWidget(self.screen_step_one_button)
        auto_bottom.addWidget(self.screen_step_five_button)
        display.layout().addLayout(auto_bottom)

        brightness_divider = QFrame()
        brightness_divider.setFixedHeight(1)
        brightness_divider.setStyleSheet("background:rgba(85,240,255,0.26); border:0; margin-top:4px;")
        display.layout().addWidget(brightness_divider)
        brightness_title_row = QHBoxLayout()
        brightness_title = QLabel("BRIGHTNESS")
        brightness_title.setFont(font(9, QFont.Black))
        brightness_title.setStyleSheet("color:#46e8ff; letter-spacing:1px; background:transparent; border:0;")
        brightness_help = QLabel("Normal is the fallback; if multiple rules match, the lowest brightness wins.")
        brightness_help.setWordWrap(True)
        brightness_help.setFont(font(7, QFont.Bold))
        brightness_help.setStyleSheet("color:#8fa5c2; background:transparent; border:0;")
        brightness_title_row.addWidget(brightness_title)
        brightness_title_row.addWidget(brightness_help, 1)
        display.layout().addLayout(brightness_title_row)

        display.layout().addWidget(self.build_brightness_percent_row("Normal brightness", "brightnessNormalPercent"))

        time_panel = QFrame()
        time_panel.setStyleSheet("background:rgba(5,10,20,0.30); border:1px solid rgba(160,180,210,0.16); border-radius:9px;")
        time_lay = QGridLayout(time_panel)
        time_lay.setContentsMargins(8, 5, 8, 5)
        time_lay.setHorizontalSpacing(7)
        time_lay.setVerticalSpacing(4)
        self.screen_brightness_time_checkbox = QCheckBox("Time-based brightness")
        self.screen_brightness_time_checkbox.setStyleSheet(self.screen_compact_checkbox_style())
        self.screen_brightness_time_checkbox.toggled.connect(lambda checked: self.toggle_screen_setting("brightnessTimeEnabled", checked))
        time_lay.addWidget(self.screen_brightness_time_checkbox, 0, 0, 1, 2)
        start_label = QLabel("From")
        end_label = QLabel("Until")
        for label in (start_label, end_label):
            label.setFont(font(8, QFont.Black))
            label.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        combo_style = "QComboBox{background:rgba(7,13,25,0.96); color:#ffffff; border:1px solid rgba(85,240,255,0.28); border-radius:7px; padding:4px 7px; font-weight:900; min-height:24px;} QAbstractItemView{background:#182235; color:#fff; selection-background-color:#45e5ff; selection-color:#071420;}"
        self.screen_brightness_time_start_combo = QComboBox()
        self.screen_brightness_time_end_combo = QComboBox()
        self.screen_brightness_time_start_combo.setStyleSheet(combo_style)
        self.screen_brightness_time_end_combo.setStyleSheet(combo_style)
        self.populate_brightness_time_combo(self.screen_brightness_time_start_combo, str(self.screen_setting_values.get("brightnessTimeStart", "22:00")))
        self.populate_brightness_time_combo(self.screen_brightness_time_end_combo, str(self.screen_setting_values.get("brightnessTimeEnd", "07:00")))
        self.screen_brightness_time_start_combo.currentIndexChanged.connect(lambda _index: self.set_screen_brightness_time("brightnessTimeStart", self.screen_brightness_time_start_combo))
        self.screen_brightness_time_end_combo.currentIndexChanged.connect(lambda _index: self.set_screen_brightness_time("brightnessTimeEnd", self.screen_brightness_time_end_combo))
        time_lay.addWidget(start_label, 1, 0)
        time_lay.addWidget(self.screen_brightness_time_start_combo, 1, 1)
        time_lay.addWidget(end_label, 1, 2)
        time_lay.addWidget(self.screen_brightness_time_end_combo, 1, 3)
        time_brightness = self.build_brightness_percent_row("Brightness", "brightnessTimePercent")
        time_lay.addWidget(time_brightness, 2, 0, 1, 4)
        time_lay.setColumnStretch(1, 1)
        time_lay.setColumnStretch(3, 1)
        display.layout().addWidget(time_panel)

        rules_header = QHBoxLayout()
        rules_header.setSpacing(7)
        rules_label = QLabel("HOME ASSISTANT BRIGHTNESS RULES")
        rules_label.setFont(font(8, QFont.Black))
        rules_label.setStyleSheet("color:#c4d0e5; background:transparent; border:0;")
        self.screen_brightness_add_rule_button = RoundButton("Add Entity Rule", active=True, min_h=31)
        self.screen_brightness_add_rule_button.setMinimumWidth(148)
        self.screen_brightness_add_rule_button.clicked.connect(self.add_brightness_entity_rule)
        rules_header.addWidget(rules_label, 1)
        rules_header.addWidget(self.screen_brightness_add_rule_button)
        display.layout().addLayout(rules_header)

        rules_widget = QWidget()
        self.screen_brightness_rules_layout = QVBoxLayout(rules_widget)
        self.screen_brightness_rules_layout.setContentsMargins(0, 0, 0, 0)
        self.screen_brightness_rules_layout.setSpacing(5)
        display.layout().addWidget(rules_widget)

        brightness_note = QLabel("Entity rules are read-only triggers. Add the same entry twice to define separate ON and OFF brightness levels. Time ranges can cross midnight; outside the range the screen returns to Normal unless another rule matches.")
        brightness_note.setWordWrap(True)
        brightness_note.setFont(font(7, QFont.Black))
        brightness_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        display.layout().addWidget(brightness_note)
        self.refresh_screen_setting_controls()

        # Keep Audio Settings directly to the right of Screen Settings in the
        # two-column Comfort Setup index. It opens the existing Audio settings
        # dialog on the new speaker-groups page so there is one source of truth.
        self.add_section_link("Audio Settings", 7, 2, self.open_audio_settings_from_comfort, 1, 2)

        # Section headers are positioned after all panels have been built.

    def val_number(self, key):
        # Settings value labels may include compact suffixes such as "66°F",
        # "5 min", or "45%". Extract the numeric portion instead of
        # assuming the suffix is separated by whitespace. This keeps +/-
        # controls from falling back to 0 and then clamping to their minimum.
        text = str(self.controls[key].text() or "")
        match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text)
        if not match:
            return 0
        try:
            return int(float(match.group(0)))
        except (TypeError, ValueError):
            return 0

    def lockout_button_text(self, kind: str) -> str:
        key = "heatLocked" if kind == "heat" else "coolLocked"
        label = "Heat Lockout" if kind == "heat" else "Cool Lockout"
        return f"{label}: {'ON' if bool((self.s.thermostat or {}).get(key)) else 'OFF'}"

    def refresh_lockout_buttons(self):
        for kind, attr in (("heat", "heat_lockout_button"), ("cool", "cool_lockout_button")):
            button = getattr(self, attr, None)
            if not button:
                continue
            key = "heatLocked" if kind == "heat" else "coolLocked"
            active = bool((self.s.thermostat or {}).get(key))
            button.setText(self.lockout_button_text(kind))
            if hasattr(button, "setActive"):
                button.setActive(active)

    def toggle_lockout(self, kind: str):
        key = "heatLocked" if kind == "heat" else "coolLocked"
        current = bool((self.s.thermostat or {}).get(key))
        self.set_thermostat({key: not current}, quiet=True, debounce=False, push=False)
        self.refresh_lockout_buttons()

    def thermostat_name_summary_text(self) -> str:
        name = str((self.s.thermostat or {}).get("name") or "IHA Thermostat").strip() or "IHA Thermostat"
        return f"Name: {name}"

    def edit_thermostat_name(self):
        current = str((self.s.thermostat or {}).get("name") or "IHA Thermostat")
        value = TextKeyboardDialog.get_text(self, "Thermostat Name", current)
        if value is None:
            return
        name = str(value or "").strip()[:80]
        if not name:
            QMessageBox.warning(self, "Thermostat Name", "Please enter a name.")
            return
        if name == current:
            return
        self.s.thermostat["name"] = name
        if hasattr(self, "thermostat_name_label"):
            self.thermostat_name_label.setText(self.thermostat_name_summary_text())
            self.thermostat_name_label.repaint()
        top = self.window()
        thermo_page = getattr(top, "thermostat", None)
        title_label = getattr(thermo_page, "title_label", None)
        if title_label is not None:
            title_label.setText(name)
            title_label.repaint()
        self.set_thermostat({"name": name}, quiet=False, debounce=False)

    def adjust_value(self, key, delta, low, high, suffix):
        val = self.val_number(key) + delta
        if low is not None:
            val = max(low, val)
        if high is not None:
            val = min(high, val)
        self.controls[key].setText(str(val) + suffix)
        # Plus/minus settings are intentionally local until Save Settings.
        # Keeping the API out of the tap path makes repeated touchscreen taps
        # immediate and avoids waiting on a thermostat round-trip.
        self.controls[key].repaint()
        # Return from the current X11 touch release/click before changing visual
        # styles. This follows the same pointer-grab safety rule used by the
        # motion settings controls elsewhere in the native app.
        QTimer.singleShot(0, lambda k=key: self.pop_value_control(k))
        if str(key).startswith("jarvis"):
            self._jarvis_dirty = True
        self.mark_settings_dirty()

    def build_settings_changes(self) -> dict:
        limits = copy.deepcopy(self.s.thermostat.get("limits") or {})
        limits.setdefault("cool", {})["min"] = self.val_number("coolMin")
        limits.setdefault("cool", {})["max"] = self.val_number("coolMax")
        limits.setdefault("heat", {})["min"] = self.val_number("heatMin")
        limits.setdefault("heat", {})["max"] = self.val_number("heatMax")
        limits.setdefault("auto", {})["min"] = min(limits["cool"]["min"], limits["heat"]["min"])
        limits.setdefault("auto", {})["max"] = max(limits["cool"]["max"], limits["heat"]["max"])
        pause = self.s.thermostat.get("pauseFunction") if isinstance(self.s.thermostat.get("pauseFunction"), dict) else {}
        pause_entries = pause.get("entries") if isinstance(pause.get("entries"), list) else []
        return {
            "safetyLow": self.val_number("safetyLow"),
            "safetyHigh": self.val_number("safetyHigh"),
            "awayHeat": self.val_number("awayHeat"),
            "awayCool": self.val_number("awayCool"),
            "intimacyHoldTargetTemp": self.val_number("intimacyHoldTargetTemp"),
            "autoCoolOutdoorTarget": self.val_number("autoCoolOutdoorTarget"),
            "autoHeatOutdoorTarget": self.val_number("autoHeatOutdoorTarget"),
            "autoChangeoverLockoutMinutes": self.val_number("autoChangeoverLockoutMinutes") * 60,
            "manualChangeoverLockoutMinutes": self.val_number("manualChangeoverLockoutMinutes"),
            "temperatureDifferential": self.val_number("temperatureDifferential"),
            "heatMinimumRuntimeMinutes": self.val_number("heatMinimumRuntimeMinutes"),
            "coolMinimumRuntimeMinutes": self.val_number("coolMinimumRuntimeMinutes"),
            "coolFanRemainOnMinutes": self.val_number("coolFanRemainOnMinutes"),
            "heatLocked": bool((self.s.thermostat or {}).get("heatLocked")),
            "coolLocked": bool((self.s.thermostat or {}).get("coolLocked")),
            "pauseFunction": {
                "durationMinutes": self.val_number("doorPauseDurationMinutes") if "doorPauseDurationMinutes" in self.controls else int(float(pause.get("durationMinutes") or 5)),
                "entries": copy.deepcopy(pause_entries),
                "active": False,
                "pausedAt": 0,
                "previousTargetTemp": None,
                "previousLastComfortTarget": None,
                "activeEntityIds": [],
                "snoozeUntil": 0,
            },
            "limits": limits,
        }

    def apply_values(self, debounce: bool = True):
        self.set_thermostat(self.build_settings_changes(), quiet=True, debounce=debounce)

    def merge_dicts(self, base: dict | None, changes: dict | None) -> dict:
        merged = copy.deepcopy(base or {})
        for key, value in (changes or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self.merge_dicts(merged.get(key), value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    def apply_thermostat_changes_locally(self, changes: dict):
        self.s.thermostat = self.merge_dicts(self.s.thermostat, changes)

    def set_thermostat(self, changes, quiet=False, debounce=False, push=False):
        if not isinstance(changes, dict) or not changes:
            return
        self.apply_thermostat_changes_locally(changes)
        # Do not emit saved/reload on every plus/minus tap. That used to force a
        # synchronous full reload while the finger was still tapping, causing the
        # 1+ second lag and sometimes repainting old values back over the new
        # number. Keep the change local and let Save Settings perform the
        # authoritative thermostat write.
        self.s.pause_status_refresh(8.0)
        self._pending_settings_changes = self.merge_dicts(self._pending_settings_changes, changes)
        self._pending_settings_quiet = bool(getattr(self, "_pending_settings_quiet", True)) and bool(quiet)
        if not push:
            self._settings_dirty = True
            self._settings_save_timer.stop()
            return
        if debounce:
            self._settings_save_timer.start(350)
        else:
            self._settings_save_timer.stop()
            self.push_pending_settings()

    def push_pending_settings(self):
        if self._settings_saving:
            self._settings_dirty = True
            return
        if not self._pending_settings_changes:
            return
        changes = copy.deepcopy(self._pending_settings_changes)
        quiet = bool(getattr(self, "_pending_settings_quiet", True))
        self._pending_settings_changes = None
        self._pending_settings_quiet = True
        self._settings_saving = True
        self._settings_dirty = False
        self._settings_update_seq += 1
        seq = self._settings_update_seq

        def worker():
            try:
                result = self.s.api.thermostat_update(changes)
                self.thermostatUpdateCompleted.emit({"seq": seq, "result": result, "error": None, "quiet": quiet})
            except Exception as exc:
                self.thermostatUpdateCompleted.emit({"seq": seq, "result": None, "error": str(exc), "quiet": quiet})

        threading.Thread(target=worker, name="settings-thermostat-save", daemon=True).start()

    def handle_settings_update_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        try:
            seq = int(data.get("seq") or 0)
        except Exception:
            seq = 0
        if seq and seq < self._settings_update_seq:
            return
        self._settings_saving = False
        error = str(data.get("error") or "")
        quiet = bool(data.get("quiet", True))
        if error:
            self._last_settings_error = error
            if not quiet and self.isVisible():
                QMessageBox.warning(self, "Update failed", error)
        elif not self._settings_dirty and not self._pending_settings_changes:
            result = data.get("result")
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                if hasattr(self, "thermostat_name_label"):
                    self.thermostat_name_label.setText(self.thermostat_name_summary_text())
        if self._settings_dirty or self._pending_settings_changes:
            self._settings_dirty = False
            self.s.pause_status_refresh(2.5)
            QTimer.singleShot(0, self.push_pending_settings)
        else:
            self.s.resume_status_refresh()
            self.s.pause_status_refresh(0.2)

    def close_settings(self):
        if int(getattr(self, "_settings_write_jobs", 0) or 0) > 0:
            QMessageBox.information(self, "Settings", "A settings change is still saving. Keep Settings open until it finishes.")
            return
        if self.house_sync_running:
            QMessageBox.information(self, "House Sync", "House Sync is still running. Keep settings open until it finishes.")
            return
        self._settings_save_timer.stop()
        if self._jarvis_dirty or self._display_settings_dirty:
            self.save_all()
            return
        self.push_pending_settings()
        self.accept()

    def show_saved_then_close(self):
        dlg = QDialog(self)
        dlg.setModal(True)
        dlg.setWindowTitle("Saved")
        dlg.setFixedSize(360, 160)
        dlg.setStyleSheet("""
            QDialog {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #0a1b2d,
                    stop:1 #102d3e);
                color:#f7fbff;
                border:1px solid rgba(85,240,255,0.45);
                border-radius:24px;
            }
            QLabel {
                color:#f7fbff;
                font-family:Arial;
                background:transparent;
                border:0;
            }
        """)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(22, 18, 22, 18)
        title = QLabel("SETTINGS SAVED")
        title.setAlignment(Qt.AlignCenter)
        title.setFont(font(20, QFont.Black, 4))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        sub = QLabel("Applying changes…")
        sub.setAlignment(Qt.AlignCenter)
        sub.setFont(font(12, QFont.Black))
        sub.setStyleSheet("color:#dce8ff;")
        lay.addStretch(1)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addStretch(1)
        QTimer.singleShot(650, dlg.accept)
        dlg.exec_()
        self.accept()

    def save_display_settings_only(self):
        """Patch only display sleep keys into the newest saved panel config."""
        self.write_screen_setting_values_to_config()
        self._settings_saving = True
        self._settings_update_seq += 1
        seq = self._settings_update_seq
        self.bottom_save.setEnabled(False)
        self.bottom_save.setText("Saving…")
        display_patch = {
            key: copy.deepcopy(self.s.config.get("display", {}).get(key))
            for key in (
                "inactivityAutoOffEnabled",
                "inactivityAutoOffMinutes",
                "motionAutoSleepEnabled",
                "motionAutoSleepMinutes",
                "motionAutoWakeEnabled",
                "timeoutAdjustmentStepMinutes",
                "brightnessNormalPercent",
                "brightnessTimeEnabled",
                "brightnessTimeStart",
                "brightnessTimeEnd",
                "brightnessTimePercent",
                "brightnessEntityRules",
            )
        }

        def worker():
            try:
                latest_record = self.s.api.get_config_record()
                latest_config = latest_record.get("config") if isinstance(latest_record, dict) else {}
                latest_config = copy.deepcopy(latest_config) if isinstance(latest_config, dict) else {}
                display = latest_config.get("display") if isinstance(latest_config.get("display"), dict) else {}
                display.update(display_patch)
                latest_config["display"] = display
                config_record = self.s.api.save_config(latest_config)
                self.settingsSaveCompleted.emit({
                    "seq": seq,
                    "thermostat": None,
                    "config": config_record,
                    "error": None,
                })
            except Exception as exc:
                self.settingsSaveCompleted.emit({"seq": seq, "error": str(exc)})

        threading.Thread(target=worker, name="settings-display-save", daemon=True).start()

    def save_all(self):
        if int(getattr(self, "_settings_write_jobs", 0) or 0) > 0:
            QMessageBox.information(self, "Settings", "A settings change is still saving. Save Settings after it finishes.")
            return
        if self.house_sync_running:
            QMessageBox.information(self, "House Sync", "House Sync is still running. Save Settings after it finishes.")
            return
        self._settings_save_timer.stop()
        if (
            self._display_settings_dirty
            and not self._jarvis_dirty
            and not self._settings_dirty
            and not self._pending_settings_changes
        ):
            self.save_display_settings_only()
            return
        self.apply_jarvis_values_to_config()
        self.write_screen_setting_values_to_config()
        changes = self.merge_dicts(self._pending_settings_changes, self.build_settings_changes())
        self.apply_thermostat_changes_locally(changes)
        self._pending_settings_changes = None
        self._settings_dirty = False
        self._settings_saving = True
        self._settings_update_seq += 1
        seq = self._settings_update_seq
        self.s.pause_status_refresh(8.0)
        self.bottom_save.setEnabled(False)
        self.bottom_save.setText("Saving…")

        def worker():
            try:
                thermostat_result = self.s.api.thermostat_update(changes)
                config_record = self.s.api.save_config(self.s.config)
                self.settingsSaveCompleted.emit({
                    "seq": seq,
                    "thermostat": thermostat_result,
                    "config": config_record,
                    "error": None,
                })
            except Exception as exc:
                self.settingsSaveCompleted.emit({"seq": seq, "error": str(exc)})

        threading.Thread(target=worker, name="settings-save-all", daemon=True).start()

    def handle_save_all_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        try:
            seq = int(data.get("seq") or 0)
        except Exception:
            seq = 0
        if seq and seq < self._settings_update_seq:
            return
        self._settings_saving = False
        self.bottom_save.setEnabled(True)
        self.bottom_save.setText("Save Settings")
        error = str(data.get("error") or "")
        if error:
            self._last_settings_error = error
            QMessageBox.warning(self, "Save failed", error)
            return
        thermostat_result = data.get("thermostat")
        if isinstance(thermostat_result, dict):
            self.s.ingest_thermostat(thermostat_result)
        config_record = data.get("config")
        if isinstance(config_record, dict):
            self.s.config = config_record.get("config") or self.s.config
        self._jarvis_dirty = False
        self._display_settings_dirty = False
        if hasattr(self, "security_code_field"):
            self.security_code_field.setText(self.masked_code(str((self.s.config.get("alarm") or {}).get("disarmCode") or "")))
        if hasattr(self, "settings_code_field"):
            self.settings_code_field.setText(self.masked_code(str((self.s.config.get("security") or {}).get("settingsCode") or "")))
        self.saved.emit()
        self.show_saved_then_close()


    def choose_temp_sensor(self):
        if getattr(self, "_room_temp_entities_loading", False):
            return
        ha = self.s.ha()
        stored = copy.deepcopy(ha.get("currentTempAvailableEntities") or [])
        current = ha.get("currentTempEntity")
        if isinstance(current, dict):
            stored.insert(0, copy.deepcopy(current))
        button = getattr(self, "room_source_choose_button", None)
        self._room_temp_entities_loading = True
        if button is not None:
            button.setEnabled(False)
            button.setText("Loading…")

        def restore_button():
            self._room_temp_entities_loading = False
            button = getattr(self, "room_source_choose_button", None)
            if button is not None:
                button.setEnabled(True)
                button.setText("Choose HA")

        def open_picker(live_entities: list[dict], load_error: str = ""):
            restore_button()
            by_id: dict[str, dict] = {}
            for item in list(stored) + list(live_entities or []):
                if not isinstance(item, dict):
                    continue
                eid = str(item.get("entityId") or item.get("entity_id") or "").strip()
                if not eid:
                    continue
                record = copy.deepcopy(item)
                record.update({
                    "entityId": eid,
                    "name": str(item.get("name") or item.get("friendly_name") or eid),
                    "domain": str(item.get("domain") or (eid.split(".", 1)[0] if "." in eid else "sensor")),
                    "unitOfMeasurement": item.get("unitOfMeasurement") or item.get("unit_of_measurement") or "",
                })
                by_id[eid] = record
            entities = list(by_id.values())
            if not entities:
                detail = f"\n\n{load_error}" if load_error else ""
                QMessageBox.warning(self, "Choose Sensor", "No Home Assistant sensor or climate entities found." + detail)
                return

            dlg = EntityPickerDialog("Choose Room Temperature Entity", entities, self)

            def apply(e):
                eid = str(e.get("entityId") or e.get("entity_id") or "").strip()
                if not eid:
                    return
                source = next(
                    (item for item in entities if str(item.get("entityId") or item.get("entity_id") or "").strip() == eid),
                    {},
                )
                merged = copy.deepcopy(source)
                merged.update(e if isinstance(e, dict) else {})
                selected = {
                    "entityId": eid,
                    "name": str(merged.get("name") or merged.get("friendly_name") or eid),
                    "domain": str(merged.get("domain") or (eid.split(".", 1)[0] if "." in eid else "sensor")),
                    "unitOfMeasurement": str(merged.get("unitOfMeasurement") or merged.get("unit_of_measurement") or ""),
                }

                ha_config = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                previous_selected = copy.deepcopy(ha_config.get("currentTempEntity"))
                previous_available = copy.deepcopy(ha_config.get("currentTempAvailableEntities"))
                thermostat_keys = (
                    "roomTempControlMode",
                    "currentTempSource",
                    "currentTempSourceName",
                    "runtimeTempSource",
                    "runtimeTempSourceName",
                )
                previous_thermostat = {
                    key: (key in self.s.thermostat, copy.deepcopy(self.s.thermostat.get(key)))
                    for key in thermostat_keys
                }

                ha_config["currentTempEntity"] = selected
                ha_config["currentTempAvailableEntities"] = [selected] + [
                    copy.deepcopy(item)
                    for item in entities
                    if isinstance(item, dict) and str(item.get("entityId") or "") != eid
                ]
                changes = {
                    "roomTempControlMode": "external",
                    "currentTempSource": "home-assistant",
                    "currentTempSourceName": selected["name"],
                    "runtimeTempSource": "home-assistant",
                    "runtimeTempSourceName": selected["name"],
                }
                self.apply_thermostat_changes_locally(changes)
                self.update_source_control_widgets()
                config_snapshot = copy.deepcopy(self.s.config)

                def worker():
                    config_record = self.s.api.save_config(config_snapshot)
                    thermostat_result = self.s.api.thermostat_update(changes)
                    return {"config": config_record, "thermostat": thermostat_result}

                def done(result):
                    data = result if isinstance(result, dict) else {}
                    config_record = data.get("config")
                    if isinstance(config_record, dict):
                        self.s.config = config_record.get("config") or self.s.config
                    thermostat_result = data.get("thermostat")
                    if isinstance(thermostat_result, dict):
                        self.s.ingest_thermostat(thermostat_result)
                    self.update_source_control_widgets()
                    self.saved.emit()

                def failed(error):
                    ha_now = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                    if previous_selected is None:
                        ha_now.pop("currentTempEntity", None)
                    else:
                        ha_now["currentTempEntity"] = previous_selected
                    if previous_available is None:
                        ha_now.pop("currentTempAvailableEntities", None)
                    else:
                        ha_now["currentTempAvailableEntities"] = previous_available
                    for key, (existed, value) in previous_thermostat.items():
                        if existed:
                            self.s.thermostat[key] = value
                        else:
                            self.s.thermostat.pop(key, None)
                    self.update_source_control_widgets()
                    QMessageBox.warning(self, "Choose Sensor", error)

                self.run_settings_write("room-temp-sensor-save", worker, done, failed)

            dlg.selected.connect(apply)
            dlg.exec_()

        self.load_ha_entities_async(
            "room-temp-entities",
            ["sensor", "climate"],
            lambda entities: open_picker(entities, ""),
            lambda error: open_picker([], error),
        )


    def choose_outdoor_temp_sensor(self):
        if getattr(self, "_outdoor_temp_entities_loading", False):
            return
        ha = self.s.ha()
        stored = copy.deepcopy(ha.get("weatherAvailableEntities") or [])
        existing = ha.get("outdoorTempEntity") or ha.get("weatherEntity")
        if isinstance(existing, dict):
            stored.insert(0, copy.deepcopy(existing))
        button = getattr(self, "outdoor_source_choose_button", None)
        self._outdoor_temp_entities_loading = True
        if button is not None:
            button.setEnabled(False)
            button.setText("Loading…")

        def restore_button():
            self._outdoor_temp_entities_loading = False
            button = getattr(self, "outdoor_source_choose_button", None)
            if button is not None:
                button.setEnabled(True)
                button.setText("Choose HA")

        def open_picker(live_entities: list[dict], load_error: str = ""):
            restore_button()
            by_id: dict[str, dict] = {}
            for item in list(stored) + list(live_entities or []):
                if not isinstance(item, dict):
                    continue
                eid = str(item.get("entityId") or item.get("entity_id") or "").strip()
                if not eid:
                    continue
                domain = str(item.get("domain") or (eid.split(".", 1)[0] if "." in eid else "") or "sensor")
                record = copy.deepcopy(item)
                record.update({
                    "entityId": eid,
                    "name": str(item.get("name") or item.get("friendly_name") or eid),
                    "domain": domain,
                    "unitOfMeasurement": item.get("unitOfMeasurement") or item.get("unit_of_measurement") or "",
                })
                by_id[eid] = record
            entities = list(by_id.values())
            if not entities:
                detail = f"\n\n{load_error}" if load_error else ""
                QMessageBox.warning(self, "Outside Temperature", "No Home Assistant sensor/weather entries found." + detail)
                return

            dlg = EntityPickerDialog("Choose Outside Temperature", entities, self)

            def apply(e):
                eid = str(e.get("entityId") or e.get("entity_id") or "").strip()
                if not eid:
                    return
                source = next(
                    (item for item in entities if str(item.get("entityId") or item.get("entity_id") or "").strip() == eid),
                    {},
                )
                merged = copy.deepcopy(source)
                merged.update(e if isinstance(e, dict) else {})
                domain = str(merged.get("domain") or (eid.split(".", 1)[0] if "." in eid else "sensor"))
                selected = {
                    "entityId": eid,
                    "name": str(merged.get("name") or merged.get("friendly_name") or eid),
                    "domain": domain,
                    "unitOfMeasurement": str(merged.get("unitOfMeasurement") or merged.get("unit_of_measurement") or ""),
                }

                ha_config = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                previous = {
                    key: copy.deepcopy(ha_config.get(key))
                    for key in ("outdoorTempEntity", "weatherEntity", "weatherAvailableEntities")
                }
                previous_thermostat = {
                    key: (key in self.s.thermostat, copy.deepcopy(self.s.thermostat.get(key)))
                    for key in ("outdoorTempSource", "outdoorTempSourceName")
                }

                ha_config["outdoorTempEntity"] = selected
                if domain == "weather":
                    ha_config["weatherEntity"] = selected
                ha_config["weatherAvailableEntities"] = [selected] + [
                    copy.deepcopy(item)
                    for item in entities
                    if isinstance(item, dict) and str(item.get("entityId") or "") != eid
                ]
                changes = {
                    "outdoorTempSource": "home-assistant",
                    "outdoorTempSourceName": selected["name"],
                }
                self.apply_thermostat_changes_locally(changes)
                if hasattr(self, "outdoor_source_label"):
                    self.outdoor_source_label.setText(f"{selected['name']}\nUsing {selected['entityId']}")
                config_snapshot = copy.deepcopy(self.s.config)

                def worker():
                    config_record = self.s.api.save_config(config_snapshot)
                    thermostat_result = self.s.api.thermostat_update(changes)
                    return {"config": config_record, "thermostat": thermostat_result}

                def done(result):
                    data = result if isinstance(result, dict) else {}
                    config_record = data.get("config")
                    if isinstance(config_record, dict):
                        self.s.config = config_record.get("config") or self.s.config
                    thermostat_result = data.get("thermostat")
                    if isinstance(thermostat_result, dict):
                        self.s.ingest_thermostat(thermostat_result)
                    self.saved.emit()

                def failed(error):
                    ha_now = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                    for key, value in previous.items():
                        if value is None:
                            ha_now.pop(key, None)
                        else:
                            ha_now[key] = value
                    for key, (existed, value) in previous_thermostat.items():
                        if existed:
                            self.s.thermostat[key] = value
                        else:
                            self.s.thermostat.pop(key, None)
                    current = ha_now.get("outdoorTempEntity") or ha_now.get("weatherEntity")
                    if hasattr(self, "outdoor_source_label"):
                        if isinstance(current, dict):
                            name = current.get("name") or current.get("entityId") or "Outside Sensor"
                            self.outdoor_source_label.setText(f"Outside: {compact_name(name, 26)}\n{current.get('entityId') or 'selected entry'}")
                        else:
                            self.outdoor_source_label.setText("Outside: Not selected\nChoose outside temp/weather")
                    QMessageBox.warning(self, "Outside Temperature", error)

                self.run_settings_write("outdoor-temp-save", worker, done, failed)

            dlg.selected.connect(apply)
            dlg.exec_()

        self.load_ha_entities_async(
            "outdoor-temp-entities",
            ["sensor", "weather"],
            lambda entities: open_picker(entities, ""),
            lambda error: open_picker([], error),
        )



    def show_hardware(self):
        if getattr(self, "_hardware_info_loading", False):
            return
        self._hardware_info_loading = True
        self.hardware.setEnabled(False)
        self.hardware.setText("Loading…")

        def worker():
            info = self.s.api.get("/api/system/info")
            hw = self.s.api.get("/api/hardware/status")
            return {"info": info, "hardware": hw}

        def finish():
            self._hardware_info_loading = False
            self.hardware.setEnabled(True)
            self.hardware.setText("Hardware")

        def done(result):
            finish()
            data = result if isinstance(result, dict) else {}
            info = data.get("info") if isinstance(data.get("info"), dict) else {}
            hw = data.get("hardware") if isinstance(data.get("hardware"), dict) else {}
            msg = (
                f"{info.get('thermostatName') or info.get('name')}\n"
                f"{info.get('address')}\n"
                f"Version {info.get('version')}\n"
                f"{info.get('uptime')}\n"
                f"Thermal {info.get('thermal')}\n\n"
                f"Relays: {hw.get('relays') or hw}"
            )
            QMessageBox.information(self, "Hardware Information", msg)

        def failed(error):
            finish()
            QMessageBox.warning(self, "Hardware Information", error)

        self.run_settings_async("hardware-info", worker, done, failed)


    def show_history(self):
        if getattr(self, "_history_loading", False):
            return
        self._history_loading = True
        self.history.setEnabled(False)
        self.history.setText("Loading…")

        def worker():
            return self.s.api.get("/api/history")

        def finish():
            self._history_loading = False
            self.history.setEnabled(True)
            self.history.setText("History")

        def done(result):
            finish()
            hist = result if isinstance(result, dict) else {}
            items = hist.get("events") or hist.get("history") or []
            if not items:
                msg = "No HVAC history entries yet."
            else:
                msg = "\n".join(str(x)[:180] for x in items[-15:])
            QMessageBox.information(self, "History", msg)

        def failed(error):
            finish()
            QMessageBox.warning(self, "History", error)

        self.run_settings_async("history", worker, done, failed)



class AlarmCountdownRing(QWidget):
    def __init__(self, total_seconds: int = 60, parent=None):
        super().__init__(parent)
        self.total_seconds = max(1, int(total_seconds))
        self.remaining = self.total_seconds
        self.setMinimumSize(250, 250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def setRemaining(self, remaining: int):
        self.remaining = max(0, int(remaining))
        self.update()

    def sizeHint(self):
        return QSize(320, 320)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 18
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)

        glow = QRadialGradient(rect.center(), side * 0.56)
        glow.setColorAt(0, QColor(255, 183, 91, 55))
        glow.setColorAt(0.62, QColor(255, 91, 121, 26))
        glow.setColorAt(1, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(rect.adjusted(-8, -8, 8, 8))

        p.setBrush(QColor(255, 255, 255, 12))
        p.setPen(QPen(QColor(255, 255, 255, 28), 1.5))
        p.drawEllipse(rect.adjusted(18, 18, -18, -18))

        base_pen = QPen(QColor(255, 255, 255, 34), max(12, int(side * 0.045)))
        base_pen.setCapStyle(Qt.RoundCap)
        p.setPen(base_pen)
        p.drawArc(rect.adjusted(16, 16, -16, -16), 90 * 16, -360 * 16)

        ratio = max(0.0, min(1.0, self.remaining / self.total_seconds))
        accent_pen = QPen(QColor(255, 183, 91), max(12, int(side * 0.045)))
        accent_pen.setCapStyle(Qt.RoundCap)
        p.setPen(accent_pen)
        p.drawArc(rect.adjusted(16, 16, -16, -16), 90 * 16, int(-360 * 16 * ratio))

        p.setPen(QColor(255, 255, 255))
        p.setFont(font(max(44, int(side * 0.25)), QFont.Black))
        number_rect = rect.adjusted(0, -20, 0, 20)
        p.drawText(number_rect, Qt.AlignCenter, str(self.remaining))

        # Keep the unit label clearly below the countdown number instead of
        # crowding the center of the dial on the Arm Away exit timer screen.
        p.setFont(font(max(11, int(side * 0.045)), QFont.Black))
        p.setPen(QColor(255, 210, 151))
        label_top = rect.center().y() + side * 0.21
        label_rect = QRectF(rect.left(), label_top, rect.width(), side * 0.16)
        p.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop, "SECONDS")


class AlarmModeCard(QAbstractButton):
    def __init__(self, title: str, subtitle: str, icon: str, accent: str = "cyan", parent=None):
        super().__init__(parent)
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.accent = accent
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(178)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self):
        return QSize(300, 205)

    def accent_color(self):
        if self.accent == "orange":
            return QColor(255, 183, 91)
        if self.accent == "red":
            return QColor(255, 73, 121)
        return QColor(85, 240, 255)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        accent = self.accent_color()

        bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        bg.setColorAt(0, QColor(255, 255, 255, 35))
        bg.setColorAt(0.55, QColor(32, 44, 70, 210))
        bg.setColorAt(1, QColor(7, 13, 24, 236))
        p.setBrush(QBrush(bg))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 108), 1.6))
        p.drawRoundedRect(rect, 28, 28)

        glow = QRadialGradient(QPointF(rect.left() + 70, rect.top() + 66), 125)
        glow.setColorAt(0, QColor(accent.red(), accent.green(), accent.blue(), 58))
        glow.setColorAt(1, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, 28, 28)

        icon_rect = QRectF(rect.left() + 24, rect.top() + 24, 76, 76)
        p.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 34))
        p.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 136), 1.4))
        p.drawEllipse(icon_rect)
        p.setPen(QColor(255, 255, 255))
        p.setFont(font(28, QFont.Black))
        p.drawText(icon_rect, Qt.AlignCenter, self.icon)

        text_x = icon_rect.right() + 20
        title_rect = QRectF(text_x, rect.top() + 28, rect.width() - text_x + rect.left() - 22, 48)
        p.setPen(QColor(255, 255, 255))
        p.setFont(font(22, QFont.Black))
        p.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, self.title)

        sub_rect = QRectF(text_x, rect.top() + 82, rect.width() - text_x + rect.left() - 22, 74)
        p.setPen(QColor(196, 211, 232))
        p.setFont(font(11, QFont.Bold))
        p.drawText(sub_rect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.subtitle)

        p.setPen(QColor(accent.red(), accent.green(), accent.blue(), 220))
        p.setFont(font(18, QFont.Black))
        p.drawText(QRectF(rect.right() - 58, rect.bottom() - 56, 34, 34), Qt.AlignCenter, "›")


class AlarmControlDialog(QDialog):
    actionDone = pyqtSignal(dict, str)
    alarmActionCompleted = pyqtSignal(object)

    def __init__(self, state: AppState, alarm_entity: dict, parent=None):
        super().__init__(parent)
        self.s = state
        self.entity = alarm_entity or {}
        self.code_buffer = ""
        self.remaining = 0
        self.countdown_total = 60
        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self.countdown_tick)
        self._alarm_action_running = False
        self._closing = False
        self._destroyed = False
        self._keypad_accept_after = 0.0
        self.destroyed.connect(lambda *_args: setattr(self, "_destroyed", True))
        self.alarmActionCompleted.connect(self.handle_alarm_action_completed)

        self.setModal(True)
        self.setWindowTitle("Alarm Control")
        self.setStyleSheet("""
            QDialog {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #050a13,
                    stop:0.42 #0c1730,
                    stop:0.72 #121b36,
                    stop:1 #260b18);
                color:#f7fbff;
            }
            QLabel {
                color:#f7fbff;
                font-family:Arial;
                background:transparent;
                border:0;
            }
            QPushButton {
                font-family:Arial;
                font-weight:900;
            }
        """)

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(34, 28, 34, 28)
        self.root.setSpacing(18)

        self.title = QLabel("")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setTextFormat(Qt.RichText)
        self.root.addWidget(self.title)

        self.body = QVBoxLayout()
        self.body.setSpacing(16)
        self.root.addLayout(self.body, 1)

        self.fit_to_parent()
        self.render()

    def fit_to_parent(self):
        parent = self.parentWidget()
        if parent:
            base_w = max(620, parent.width())
            base_h = max(540, parent.height())
        else:
            screen = QApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRectF(0, 0, 1000, 700)
            base_w = int(geo.width())
            base_h = int(geo.height())
        width = int(base_w * 0.88)
        height = int(base_h * 0.86)
        if base_w > 720:
            width = min(width, base_w - 36)
        if base_h > 580:
            height = min(height, base_h - 36)
        width = max(620, min(width, 1180))
        height = max(520, min(height, 1040))
        self.resize(width, height)
        self.setMinimumSize(min(width, 620), min(height, 520))

    def fit_keypad_to_parent(self):
        """Use a compact, centered shape for the four-digit disarm keypad."""
        parent = self.parentWidget()
        if parent:
            base_w = max(620, parent.width())
            base_h = max(620, parent.height())
        else:
            screen = QApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else QRectF(0, 0, 1000, 700)
            base_w = int(geo.width())
            base_h = int(geo.height())
        width = max(560, min(int(base_w * 0.56), 720))
        height = max(620, min(int(base_h * 0.92), 820))
        self.setMinimumSize(min(width, 560), min(height, 620))
        self.resize(width, height)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                self._clear_layout(child_layout)

    def clear_body(self):
        self._clear_layout(self.body)

    def prepare_for_close(self, reason: str = ""):
        if getattr(self, "_closing", False):
            return
        self._closing = True
        try:
            if self.countdown_timer.isActive():
                self.countdown_timer.stop()
        except Exception:
            pass
        if reason:
            trace_runtime(f"alarm dialog preparing to close: {reason}")

    def can_update_ui(self) -> bool:
        if getattr(self, "_closing", False) or getattr(self, "_destroyed", False):
            return False
        try:
            return bool(self.isVisible()) and not getattr(self, "_alarm_action_running", False)
        except RuntimeError:
            return False

    def apply_fresh_alarm_state(self, fresh: dict | None):
        if not isinstance(fresh, dict) or not self.can_update_ui():
            return
        self.entity.update(fresh)
        self.render()

    def current_state(self) -> str:
        return str(self.entity.get("state") or "disarmed").lower()

    def state_text(self) -> str:
        return self.current_state().replace("_", " ").upper()

    def alarm_name(self) -> str:
        return str(self.entity.get("name") or self.entity.get("entityId") or "Alarm Panel")

    def is_armed(self) -> bool:
        state = self.current_state()
        return state.startswith("armed") or state in {"arming", "pending", "triggered"}

    def render(self):
        if getattr(self, "_closing", False):
            return
        self.clear_body()
        if self.is_armed():
            self.fit_keypad_to_parent()
            self.render_keypad()
        else:
            self.fit_to_parent()
            self.render_arm_options()

    def header_html(self, eyebrow: str, title: str, accent: str = "#55f0ff") -> str:
        safe_name = html.escape(self.alarm_name())
        return (
            f"<span style='color:{accent}; letter-spacing:4px; font-size:12px; font-weight:1000'>{eyebrow}</span>"
            f"<br><span style='font-size:36px; font-weight:1000; color:#ffffff'>{title}</span>"
            f"<br><span style='font-size:13px; font-weight:800; color:#aebbd0'>{safe_name}</span>"
        )

    def make_status_panel(self, state_label: str, message: str, accent: str = "#55f0ff"):
        panel = QFrame()
        panel.setStyleSheet(f"""
            QFrame {{
                background:rgba(255,255,255,0.055);
                border:1px solid rgba(255,255,255,0.105);
                border-radius:26px;
            }}
            QLabel {{ background:transparent; border:0; }}
        """)
        row = QHBoxLayout(panel)
        row.setContentsMargins(22, 16, 22, 16)
        row.setSpacing(16)

        badge = QLabel("●")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(54, 54)
        badge.setFont(font(26, QFont.Black))
        badge.setStyleSheet(f"""
            QLabel {{
                color:{accent};
                background:rgba(255,255,255,0.055);
                border:1px solid rgba(255,255,255,0.10);
                border-radius:27px;
            }}
        """)
        row.addWidget(badge)

        text = QLabel(f"<b>{state_label}</b><br><span style='color:#aebbd0'>{message}</span>")
        text.setTextFormat(Qt.RichText)
        text.setWordWrap(True)
        text.setFont(font(12, QFont.Bold))
        row.addWidget(text, 1)
        return panel

    def render_arm_options(self):
        self.title.setText(self.header_html("ALARM DISARMED", "Select Arm Mode", "#55f0ff"))

        self.body.addWidget(self.make_status_panel(
            "Ready to arm",
            "Choose a mode below. Arm Away starts a 60 second exit countdown before the alarm is armed.",
            "#55f0ff",
        ))

        cards = QHBoxLayout()
        cards.setSpacing(18)
        self.arm_home = AlarmModeCard(
            "Arm Home",
            "Stay mode for when people are inside. Exterior protection is armed while you remain home.",
            "⌂",
            "cyan",
        )
        self.arm_away = AlarmModeCard(
            "Arm Away",
            "Full protection with a 60 second exit timer. The panel arms after the countdown finishes.",
            "⏱",
            "orange",
        )
        self.arm_home.clicked.connect(lambda: self.send_action("arm_home"))
        self.arm_away.clicked.connect(self.begin_arm_away_countdown)
        cards.addWidget(self.arm_home, 1)
        cards.addWidget(self.arm_away, 1)
        self.body.addLayout(cards, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = RoundButton("Cancel", active=False, min_h=58)
        cancel.setMinimumWidth(190)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        actions.addStretch(1)
        self.body.addLayout(actions)

    def render_keypad(self):
        # Do not accept the touch/release that opened this modal as keypad
        # input. The Pi touchscreen can occasionally deliver that trailing
        # event to the newly displayed dialog.
        self._keypad_accept_after = time.monotonic() + 0.45
        state = self.current_state()
        accent = "#ff4979" if state != "triggered" else "#ff365b"
        title = "Alarm Triggered" if state == "triggered" else "Enter Code"
        self.title.setText(self.header_html(self.state_text(), title, accent))

        self.body.addWidget(self.make_status_panel(
            "Code required to disarm",
            "Enter your four-digit alarm code.",
            accent,
        ))

        code_area = QVBoxLayout()
        code_area.setSpacing(8)
        helper = QLabel("DISARM CODE")
        helper.setAlignment(Qt.AlignCenter)
        helper.setFont(font(10, QFont.Black))
        helper.setStyleSheet("color:#ff9cb5; letter-spacing:3px;")
        code_area.addWidget(helper)
        self.code_display = QLabel("····")
        self.code_display.setAlignment(Qt.AlignCenter)
        self.code_display.setFont(font(38, QFont.Black))
        self.code_display.setFixedHeight(78)
        self.code_display.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,255,255,0.07);
                border:1px solid rgba(255,73,121,0.55);
                border-radius:22px;
                padding:10px 16px;
                letter-spacing:16px;
            }
        """)
        code_area.addWidget(self.code_display)
        self.body.addLayout(code_area)

        keypad = QGridLayout()
        keypad.setHorizontalSpacing(10)
        keypad.setVerticalSpacing(10)
        keys = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
            ("⌫", 3, 0), ("0", 3, 1), ("Cancel", 3, 2),
        ]
        for label, row, col in keys:
            b = KeypadButton(label, active=(label not in {"⌫", "Cancel"}), min_h=64)
            b.setMinimumWidth(104)
            if label == "Cancel":
                b.setKind("danger")
                b.clicked.connect(self.reject)
            elif label == "⌫":
                b.pressed.connect(self.backspace_code)
            else:
                b.pressed.connect(lambda d=label: self.add_code_digit(d))
            keypad.addWidget(b, row, col)
        keypad_row = QHBoxLayout()
        keypad_row.addStretch(1)
        keypad_row.addLayout(keypad)
        keypad_row.addStretch(1)
        self.body.addLayout(keypad_row, 1)
        self.update_code_display()

    def update_code_display(self):
        entered = "•" * len(self.code_buffer)
        remaining = "·" * max(0, 4 - len(self.code_buffer))
        next_text = entered + remaining
        if self.code_display.text() != next_text:
            self.code_display.setText(next_text)
            self.code_display.update()

    def add_code_digit(self, digit: str):
        if time.monotonic() < getattr(self, "_keypad_accept_after", 0.0):
            return
        if len(self.code_buffer) >= 4:
            return
        self.code_buffer += digit
        self.update_code_display()
        if len(self.code_buffer) == 4:
            QTimer.singleShot(120, self.auto_disarm)

    def backspace_code(self):
        if time.monotonic() < getattr(self, "_keypad_accept_after", 0.0):
            return
        self.code_buffer = self.code_buffer[:-1]
        self.update_code_display()

    def auto_disarm(self):
        if len(self.code_buffer) != 4:
            return
        expected = str((self.s.config.get("alarm") or {}).get("disarmCode") or "").strip()
        if not expected or self.code_buffer != expected:
            self.invalid_disarm_code()
            return
        confirmation = {
            "method": "keypad",
            "confirmedAt": int(time.time() * 1000),
            "digits": len(self.code_buffer),
        }
        self.send_action("disarm", self.code_buffer, confirmation=confirmation)

    def invalid_disarm_code(self):
        self.code_buffer = ""
        self.title.setText(self.header_html("INVALID CODE", "Try Again", "#ff4979"))
        self.code_display.setText("••••")
        self.code_display.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,54,91,0.18);
                border:1px solid rgba(255,74,111,0.78);
                border-radius:22px;
                padding:10px 16px;
                letter-spacing:16px;
            }
        """)
        QTimer.singleShot(800, self.restore_keypad_after_invalid)

    def restore_keypad_after_invalid(self):
        self.title.setText(self.header_html(self.state_text(), "Enter Code", "#ff4979"))
        self.code_display.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,255,255,0.07);
                border:1px solid rgba(255,73,121,0.55);
                border-radius:22px;
                padding:10px 16px;
                letter-spacing:16px;
            }
        """)
        self.update_code_display()

    def begin_arm_away_countdown(self):
        if getattr(self, "_closing", False):
            return
        trace_runtime("alarm arm away countdown started")
        self.clear_body()
        self.countdown_total = 60
        self.remaining = self.countdown_total
        self.title.setText(self.header_html("ARMING AWAY", "Exit Timer", "#ffb65c"))

        self.body.addWidget(self.make_status_panel(
            "Leave now",
            "The alarm will arm away automatically when the countdown reaches zero.",
            "#ffb65c",
        ))

        mid = QHBoxLayout()
        mid.setSpacing(28)
        mid.addStretch(1)
        self.countdown_ring = AlarmCountdownRing(self.countdown_total)
        mid.addWidget(self.countdown_ring, 2)

        info = QVBoxLayout()
        info.setSpacing(14)
        info.addStretch(1)
        status = QLabel("ARM AWAY PENDING")
        status.setAlignment(Qt.AlignCenter)
        status.setFont(font(14, QFont.Black))
        status.setStyleSheet("color:#ffcf95; letter-spacing:3px;")
        info.addWidget(status)
        arm_now = RoundButton("Arm Now", active=True, kind="purple", min_h=62)
        arm_now.clicked.connect(lambda: self.send_action("arm_away"))
        info.addWidget(arm_now)
        cancel = RoundButton("Cancel Countdown", active=False, kind="danger", min_h=62)
        cancel.clicked.connect(self.reject)
        info.addWidget(cancel)
        info.addStretch(1)
        mid.addLayout(info, 1)
        mid.addStretch(1)
        self.body.addLayout(mid, 1)

        self.countdown_timer.start(1000)
        self.countdown_tick(first=True)

    def countdown_tick(self, first: bool = False):
        if getattr(self, "_closing", False) or not self.isVisible():
            try:
                self.countdown_timer.stop()
            except Exception:
                pass
            return
        if not first:
            self.remaining -= 1
        if self.remaining <= 0:
            self.countdown_timer.stop()
            self.send_action("arm_away")
            return
        if hasattr(self, "countdown_ring"):
            self.countdown_ring.setRemaining(self.remaining)

    def reject(self):
        trace_runtime(f"alarm dialog cancel pressed state={self.current_state()} remaining={getattr(self, 'remaining', 0)}")
        self.prepare_for_close("reject")
        super().reject()

    def done(self, result: int):
        self.prepare_for_close(f"done result={result}")
        super().done(result)

    def closeEvent(self, event):
        self.prepare_for_close("closeEvent")
        super().closeEvent(event)

    def set_busy(self, busy: bool):
        for btn in self.findChildren(QAbstractButton):
            btn.setEnabled(not busy)

    def send_action(self, action: str, code: str = "", confirmation: dict | None = None):
        if getattr(self, "_closing", False):
            trace_runtime(f"alarm action ignored after close action={action}")
            return
        if self._alarm_action_running:
            return
        if action == "disarm":
            expected = str((self.s.config.get("alarm") or {}).get("disarmCode") or "").strip()
            entered = str(code or "").strip()
            # Disarm must only be reached from a successfully completed keypad
            # entry. Never allow a missing, stale, or implicitly supplied code
            # to launch the Home Assistant service call.
            if not expected:
                QMessageBox.warning(self, "Alarm Code Required", "Configure an alarm disarm code before using Disarm.")
                return
            if not entered or entered != expected:
                if hasattr(self, "code_display"):
                    self.invalid_disarm_code()
                return
        if self.countdown_timer.isActive():
            self.countdown_timer.stop()
        trace_runtime(f"alarm action sending action={action}")
        self._alarm_action_running = True
        self.set_busy(True)
        payload = self.s.ha_payload({
            "entityId": self.entity.get("entityId") or "",
            "action": action,
            "code": code,
            "confirmation": confirmation if action == "disarm" else None,
            "source": "native-alarm-dialog",
        })

        def worker():
            try:
                result = self.s.api.post("/api/ha/alarm/action", payload)
                self.alarmActionCompleted.emit({"action": action, "result": result, "error": None})
            except Exception as exc:
                self.alarmActionCompleted.emit({"action": action, "result": None, "error": str(exc)})

        threading.Thread(target=worker, name="alarm-action", daemon=True).start()

    def handle_alarm_action_completed(self, info: object):
        self._alarm_action_running = False
        data = info if isinstance(info, dict) else {}
        action = str(data.get("action") or "")
        error = str(data.get("error") or "")
        if getattr(self, "_closing", False) and not self.isVisible():
            trace_runtime(f"alarm action completion ignored after close action={action} error={bool(error)}")
            return
        trace_runtime(f"alarm action completed action={action} error={bool(error)}")
        if error:
            self.set_busy(False)
            if action == "disarm" and hasattr(self, "code_display"):
                self.code_buffer = ""
                self.update_code_display()
            QMessageBox.warning(self, "Alarm Action Failed", error)
            return
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        alarm = result.get("alarm") or {}
        if alarm:
            self.entity.update(alarm)
        self.actionDone.emit(alarm or self.entity, action)
        self.accept()


class MainWindow(Background):
    statusRefreshCompleted = pyqtSignal(object)
    thermalStatusCompleted = pyqtSignal(object)
    alarmRefreshCompleted = pyqtSignal(object)
    assistantStatusCompleted = pyqtSignal(object)
    mainAsyncCompleted = pyqtSignal(object)
    screenBrightnessCompleted = pyqtSignal(object)
    screenBrightnessRuleStatesCompleted = pyqtSignal(object)
    screenMotionCompleted = pyqtSignal(object)
    reloadAllCompleted = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.api = ApiClient()
        self.s = AppState(self.api)
        self.setWindowTitle("Smart Thermostat Native")
        self.setMinimumSize(1000, 620)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.toast = StatusToast(self)
        self.navigation_locked = False
        self.security_lock_active = False
        self._thermal_protection_active = False
        self._thermal_suspended_timers: dict[str, tuple[QTimer, int, bool, bool]] = {}
        self._display_sleeping = False
        self._display_wake_block_until = 0.0
        self._motion_wake_block_until = 0.0
        self._manual_sleep_touch_only = False
        self._last_user_activity_at = time.monotonic()
        self._last_motion_activity_at = self._last_user_activity_at
        self._screen_motion_poll_running = False
        self._screen_motion_available = False
        self._screen_motion_active = False
        self.sleep_overlay = ScreenSleepOverlay(self)
        self.thermal_overlay = ThermalProtectionOverlay(self)
        self.assistant_overlay = AssistantOverlay(self)
        self._assistant_status_running = False
        self.sleep_button = SleepButton(self)
        self.sleep_button.clicked.connect(lambda: self.enter_display_sleep(manual=True))
        self.sync_button = SyncButton(self)
        self.sync_button.clicked.connect(self.toggle_sync_mode)
        self.device_internet_button = DeviceInternetButton(self)
        self.device_internet_button.clicked.connect(self.toggle_device_internet)
        self._device_internet_toggle_running = False
        self._device_internet_refresh_running = False
        self._device_internet_state_known = False
        self._device_internet_blocked = False
        self._device_internet_mixed = False
        self._device_internet_sequence = 0
        self.intimacy_button = IntimacyHoldButton(self)
        self.intimacy_button.clicked.connect(self.toggle_intimacy_hold)
        self._intimacy_toggle_running = False
        self._intimacy_optimistic_until = 0.0
        self._sync_active_until = 0.0
        self._sync_status_poll_running = False
        self._sync_pending_changes: dict[str, object] = {}
        self._sync_apply_running = False
        self._sync_toggle_generation = 0
        self._sync_toggle_pending_generation = 0
        self._sync_optimistic_active = False
        self._sync_status_initialized = False
        self._sync_status_retry_after = 0.0
        self._last_sync_button_render: tuple[bool, int, bool, bool] | None = None
        self._last_sync_notice_at = 0.0
        self._last_sync_result_at = 0
        self.peer_sync_timer = QTimer(self)
        self.peer_sync_timer.setSingleShot(True)
        self.peer_sync_timer.timeout.connect(self.flush_peer_sync)
        self.sync_button_timer = QTimer(self)
        self.sync_button_timer.timeout.connect(self.update_sync_button_state)
        self.sync_button_timer.start(1000)
        self.header = Header()
        self.stack = QStackedWidget()
        self.pages: dict[str, Page] = {
            "Blinds": BlindsScreen(self.s),
            "Audio": AudioScreen(self.s),
            "Thermostat": ThermostatScreen(self.s),
            "Lights": LightsScreen(self.s),
            "Room": RoomScreen(self.s),
        }
        for name, page in self.pages.items():
            self.stack.addWidget(page)
            page.requestToast.connect(self.toast.show_message)
            page.requestAssign.connect(self.assign_entity)
            page.configChanged.connect(self.reload_all)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addWidget(self.stack, 1)
        self.header.navChanged.connect(self.set_page)
        self.header.infoClicked.connect(self.show_info)
        self.header.settingsClicked.connect(self.show_settings)
        self.header.lockClicked.connect(self.toggle_navigation_lock)
        self.header.lockLongPressed.connect(self.activate_security_lock)
        self.current_name = "Thermostat"
        self.header.set_page(self.current_name)
        self.header.set_locked(self.navigation_locked, self.security_lock_active)
        self.stack.setCurrentWidget(self.pages[self.current_name])

        self._brightness_drag_active = False
        self._brightness_drag_start_y = 0.0
        self._brightness_drag_start_pct = int(clamp(SCREEN_BRIGHTNESS_DEFAULT_PERCENT, SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
        self._brightness_drag_moved = False
        self._last_brightness_toast_at = 0.0
        self._screen_brightness_pct = self.read_screen_brightness_percent()
        self._screen_brightness_pending_pct: int | None = None
        self._screen_brightness_inflight = False
        self.screen_brightness_timer = QTimer(self)
        self.screen_brightness_timer.setSingleShot(True)
        self.screen_brightness_timer.timeout.connect(self.flush_screen_brightness)
        self.screenBrightnessCompleted.connect(self.handle_screen_brightness_completed)
        self.screenBrightnessRuleStatesCompleted.connect(self.handle_screen_brightness_rule_states_completed)
        self._screen_brightness_rule_states: dict[str, str] = {}
        self._screen_brightness_rule_poll_running = False
        self._screen_brightness_automation_target: int | None = None
        self.screen_brightness_automation_timer = QTimer(self)
        self.screen_brightness_automation_timer.timeout.connect(self.refresh_screen_brightness_automation)
        self.screen_brightness_automation_timer.start(5000)

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(3000)
        self._last_poll_by_page: dict[str, float] = {}
        self._poll_busy = False
        self._last_page_change_at = time.monotonic()
        self._last_auto_nav_at = 0.0
        self._last_auto_nav_audio_poll_at = 0.0
        self._auto_nav_audio_poll_running = False
        self._auto_nav_audio_is_playing = False
        self._auto_nav_audio_previous_is_playing = False
        self._auto_nav_audio_state_at = 0.0
        self._audio_page_inactive_since = 0.0
        self._last_audio_manual_leave_at = -AUDIO_IDLE_SECONDS
        self._ignore_info_until = 0.0
        self._modal_touch_block_until = 0.0
        # Handle button touch sequences directly instead of waiting for Qt/X11
        # to synthesize mouse events.  This does not change any widget sizes.
        self._direct_touch_button: QAbstractButton | None = None
        self._direct_touch_last_global: QPoint | None = None
        self._direct_touch_mouse_suppress_until = 0.0
        self._direct_touch_mouse_suppress_point: QPoint | None = None
        self._touch_input_active = False
        self._alarm_dialog_open = False
        self._alarm_reopen_block_until = 0.0
        self._status_refresh_running = False
        self._thermal_status_running = False
        self._alarm_refresh_running = False
        # Reuse a small worker pool for recurring local status requests instead
        # of creating and destroying multiple Python threads every second.
        self._status_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="panel-status")
        self._main_async_jobs: dict[str, tuple[Callable | None, Callable | None]] = {}
        self._reload_all_running = False
        self._reload_all_pending = False
        self._interaction_runtime_sync_pending = False
        self._ui_heartbeat_at = time.monotonic()
        self._ui_stall_last_dump_at = 0.0
        self.statusRefreshCompleted.connect(self._handle_status_refresh_completed)
        self.thermalStatusCompleted.connect(self._handle_thermal_status_completed)
        self.alarmRefreshCompleted.connect(self._handle_alarm_refresh_completed)
        self.assistantStatusCompleted.connect(self._handle_assistant_status_completed)
        self.mainAsyncCompleted.connect(self._handle_main_async_completed)
        self.screenMotionCompleted.connect(self.handle_screen_motion_completed)
        self.screen_lock_report_timer = QTimer(self)
        self.screen_lock_report_timer.setInterval(30000)
        self.screen_lock_report_timer.timeout.connect(self.report_screen_lock_state)
        self.screen_lock_report_timer.start()
        self.device_internet_state_timer = QTimer(self)
        self.device_internet_state_timer.setInterval(30000)
        self.device_internet_state_timer.timeout.connect(self.refresh_device_internet_state)
        self.device_internet_state_timer.start()
        self.reloadAllCompleted.connect(self._handle_reload_all_completed)
        self.ui_heartbeat_timer = QTimer(self)
        self.ui_heartbeat_timer.setInterval(500)
        self.ui_heartbeat_timer.timeout.connect(self._mark_ui_heartbeat)
        self.ui_heartbeat_timer.start()
        threading.Thread(target=self._ui_stall_watchdog, name="ui-stall-watchdog", daemon=True).start()
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(4000)
        self.thermal_watch_timer = QTimer(self)
        self.thermal_watch_timer.timeout.connect(self.refresh_thermal_status)
        self.thermal_watch_timer.start(THERMAL_STATUS_NORMAL_INTERVAL_MS)
        self.assistant_status_timer = QTimer(self)
        self.assistant_status_timer.timeout.connect(self.refresh_assistant_status)
        self.assistant_status_timer.start(ASSISTANT_STATUS_IDLE_INTERVAL_MS)
        self.alarm_timer = QTimer(self)
        self.alarm_timer.timeout.connect(self.refresh_alarm_state)
        self.alarm_timer.start(int(max(5.0, ALARM_STATE_POLL_SECONDS) * 1000))
        self.auto_nav_timer = QTimer(self)
        self.auto_nav_timer.timeout.connect(self.check_auto_navigation)
        self.auto_nav_timer.start(1000)
        self.screen_sleep_timer = QTimer(self)
        self.screen_sleep_timer.timeout.connect(self.check_display_sleep_idle)
        self.screen_sleep_timer.start(5000)
        self.screen_motion_timer = QTimer(self)
        self.screen_motion_timer.timeout.connect(self.refresh_screen_motion_status)
        self.screen_motion_timer.start(SCREEN_MOTION_AWAKE_INTERVAL_MS)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        QTimer.singleShot(100, self.boot)

    @staticmethod
    def _set_repeating_timer_interval(timer: QTimer, interval_ms: int):
        interval_ms = max(50, int(interval_ms))
        if int(timer.interval() or 0) != interval_ms:
            timer.setInterval(interval_ms)
        if not timer.isActive():
            timer.start()

    def _submit_status_worker(self, worker: Callable[[], None]):
        try:
            self._status_executor.submit(worker)
        except RuntimeError:
            # The application is shutting down; no status result is needed.
            pass

    def _update_assistant_poll_interval(self, active: bool, *, error: bool = False):
        interval = (
            ASSISTANT_STATUS_ERROR_INTERVAL_MS
            if error
            else ASSISTANT_STATUS_ACTIVE_INTERVAL_MS
            if active
            else ASSISTANT_STATUS_IDLE_INTERVAL_MS
        )
        self._set_repeating_timer_interval(self.assistant_status_timer, interval)

    def _update_screen_motion_poll_interval(self):
        settings = self.current_screen_display_settings()
        enabled = bool(settings.get("motionAutoSleepEnabled") or settings.get("motionAutoWakeEnabled"))
        if not enabled:
            interval = SCREEN_MOTION_DISABLED_INTERVAL_MS
        elif getattr(self, "_display_sleeping", False):
            interval = (
                SCREEN_MOTION_MANUAL_SLEEP_INTERVAL_MS
                if getattr(self, "_manual_sleep_touch_only", False)
                else SCREEN_MOTION_SLEEP_INTERVAL_MS
            )
        else:
            interval = SCREEN_MOTION_AWAKE_INTERVAL_MS
        self._set_repeating_timer_interval(self.screen_motion_timer, interval)

    def _update_thermal_poll_interval(self, payload: object = None, *, error: bool = False):
        data = payload if isinstance(payload, dict) else {}
        thermal = self.thermal_protection_from_payload(data)
        active = bool(thermal.get("active"))
        try:
            cpu_temp = float(thermal.get("cpuTempC"))
            trigger = float(thermal.get("triggerC"))
            near_threshold = cpu_temp >= trigger - THERMAL_STATUS_NEAR_MARGIN_C
        except (TypeError, ValueError):
            near_threshold = False
        interval = THERMAL_STATUS_NEAR_INTERVAL_MS if active or near_threshold else THERMAL_STATUS_NORMAL_INTERVAL_MS
        if error:
            interval = THERMAL_STATUS_NORMAL_INTERVAL_MS
        self._set_repeating_timer_interval(self.thermal_watch_timer, interval)

    def boot(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if not self.api.wait_until_ready(5):
                self.toast.show_message("Backend is not responding on 127.0.0.1:8080")
            self.s.load()
            self.migrate_alexa_lockout_config()
            self.apply_thermal_protection_state(self.s.thermostat)
            if not getattr(self, "_thermal_protection_active", False):
                try:
                    self.s.refresh_alarm_state()
                except Exception:
                    pass
            self.sync_runtime_only()
            self.sync_visible_page(self.current_name)
            self.position_sleep_controls()
            self._last_motion_activity_at = time.monotonic()
            QTimer.singleShot(0, self.refresh_assistant_status)
            QTimer.singleShot(0, self.refresh_screen_motion_status)
            # Mirror the native lock state to the local backend for Home Assistant.
            # This is reporting only; the backend cannot lock or unlock the panel.
            QTimer.singleShot(0, self.report_screen_lock_state)
            QTimer.singleShot(250, self.refresh_device_internet_state)
            if not getattr(self, "_thermal_protection_active", False):
                self.toast.show_message("Native panel ready")
        except Exception as exc:
            self.toast.show_message(f"Startup problem: {exc}", 6000)
        finally:
            QApplication.restoreOverrideCursor()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_sleep_controls()
        if self.toast.isVisible():
            self.toast.move((self.width() - self.toast.width()) // 2, self.height() - self.toast.height() - 28)

    def position_sleep_controls(self):
        try:
            assistant_active = bool(
                hasattr(self, "assistant_overlay")
                and self.assistant_overlay.is_active()
            )
            if hasattr(self, "sleep_overlay"):
                self.sleep_overlay.setGeometry(self.rect())
            if hasattr(self, "thermal_overlay"):
                self.thermal_overlay.setGeometry(self.rect())
            if hasattr(self, "assistant_overlay"):
                self.assistant_overlay.setGeometry(self.rect())

            margin = 22
            sleep_x = max(0, self.width() - self.sleep_button.width() - margin) if hasattr(self, "sleep_button") else 0
            sleep_y = max(0, self.height() - self.sleep_button.height() - margin) if hasattr(self, "sleep_button") else 0
            if hasattr(self, "sleep_button"):
                self.sleep_button.move(sleep_x, sleep_y)
                self.sleep_button.setVisible(not assistant_active and not getattr(self, "_display_sleeping", False))
            show_sync = False
            sync_y = max(0, sleep_y - 58 - 10)
            if hasattr(self, "sync_button"):
                show_sync = (
                    not assistant_active
                    and not getattr(self, "_display_sleeping", False)
                    and self.current_name == "Thermostat"
                    and bool(self.sync_peer_entities())
                )
                self.sync_button.setVisible(show_sync)
                if show_sync:
                    sync_y = max(0, sleep_y - self.sync_button.height() - 10)
                    self.sync_button.move(sleep_x, sync_y)
                    self.sync_button.raise_()

            show_device_internet = False
            if hasattr(self, "device_internet_button"):
                show_device_internet = (
                    not assistant_active
                    and not getattr(self, "_display_sleeping", False)
                    and self.current_name == "Thermostat"
                    and bool(self.selected_device_internet_entities())
                )
                self.device_internet_button.setVisible(show_device_internet)
                if show_device_internet:
                    device_y = sync_y
                    device_x = max(0, sleep_x - self.device_internet_button.width() - 10) if show_sync else sleep_x
                    self.device_internet_button.move(device_x, device_y)
                    self.device_internet_button.raise_()

            if hasattr(self, "intimacy_button"):
                show_intimacy = (
                    not assistant_active
                    and not getattr(self, "_display_sleeping", False)
                    and self.current_name == "Thermostat"
                )
                self.intimacy_button.setVisible(show_intimacy)
                if show_intimacy:
                    intimacy_x = max(0, self.width() - self.intimacy_button.width() - margin - 2)
                    anchor_y = sync_y if (show_sync or show_device_internet) else sleep_y
                    self.intimacy_button.move(
                        intimacy_x,
                        max(0, anchor_y - self.intimacy_button.height() - 10),
                    )
                    self.intimacy_button.raise_()

            # Sleep shield is above the normal UI, but the active assistant screen
            # is deliberately top-most so a terminal command can wake the display
            # and immediately show the listening/processing animation.
            if hasattr(self, "sleep_overlay") and self.sleep_overlay.isVisible():
                self.sleep_overlay.raise_()
            if assistant_active and hasattr(self, "assistant_overlay"):
                self.assistant_overlay.show()
                self.assistant_overlay.raise_()
            elif hasattr(self, "sleep_button") and not getattr(self, "_display_sleeping", False):
                self.sleep_button.raise_()

            if getattr(self, "_thermal_protection_active", False) and hasattr(self, "thermal_overlay"):
                if hasattr(self, "sleep_button"):
                    self.sleep_button.hide()
                if hasattr(self, "sync_button"):
                    self.sync_button.hide()
                if hasattr(self, "device_internet_button"):
                    self.device_internet_button.hide()
                if hasattr(self, "intimacy_button"):
                    self.intimacy_button.hide()
                if hasattr(self, "assistant_overlay"):
                    self.assistant_overlay.hide()
                if hasattr(self, "sleep_overlay"):
                    self.sleep_overlay.hide()
                self.thermal_overlay.show()
                self.thermal_overlay.raise_()
        except Exception:
            pass

    def sync_peer_entities(self) -> list[dict]:
        try:
            ha = self.s.ha()
            peers = ha.get("syncThermostatEntities") if isinstance(ha, dict) else []
            clean: list[dict] = []
            seen = set()
            for peer in peers if isinstance(peers, list) else []:
                if not isinstance(peer, dict):
                    continue
                entity_id = str(peer.get("entityId") or peer.get("entity_id") or "").strip()
                if not entity_id.startswith("climate.") or entity_id in seen:
                    continue
                seen.add(entity_id)
                clean.append({
                    "entityId": entity_id,
                    "name": str(peer.get("name") or peer.get("friendly_name") or entity_id),
                    "serial": str(peer.get("serial") or peer.get("ihaSerial") or ""),
                    "panelUrl": str(peer.get("panelUrl") or peer.get("panel_url") or ""),
                    "syncCapable": bool(peer.get("syncCapable", peer.get("ihaPanel", False))),
                })
            return clean
        except Exception:
            return []

    def sync_is_active(self) -> bool:
        return bool(self.sync_peer_entities()) and time.monotonic() < float(getattr(self, "_sync_active_until", 0.0) or 0.0)

    def apply_sync_status(self, result: object, *, authoritative: bool = False):
        info = result if isinstance(result, dict) else {}
        active = bool(info.get("active", info.get("armed", False)))
        pending_generation = int(getattr(self, "_sync_toggle_pending_generation", 0) or 0)
        current_generation = int(getattr(self, "_sync_toggle_generation", 0) or 0)
        if (
            not authoritative
            and pending_generation > 0
            and pending_generation == current_generation
            and active != bool(getattr(self, "_sync_optimistic_active", False))
        ):
            # Ignore a status response that was requested before the latest
            # toggle reached the backend. The toggle callback supplies the first
            # authoritative state, preventing a stale poll from canceling the
            # user's immediate Arriving or setpoint tap.
            return
        try:
            remaining = max(0, int(info.get("remainingSeconds") or 0))
        except Exception:
            remaining = 0
        self._sync_active_until = time.monotonic() + remaining if active and remaining > 0 else 0.0
        if hasattr(self, "sync_button"):
            self.sync_button.setActive(active and remaining > 0, remaining)
        last_result = info.get("lastResult") if isinstance(info.get("lastResult"), dict) else {}
        try:
            result_at = int(last_result.get("at") or 0)
        except Exception:
            result_at = 0
        if result_at > int(getattr(self, "_last_sync_result_at", 0) or 0):
            self._last_sync_result_at = result_at
            errors = last_result.get("errors") if isinstance(last_result.get("errors"), list) else []
            skipped = last_result.get("skipped") if isinstance(last_result.get("skipped"), list) else []
            if errors:
                detail = errors[0].get("error") if isinstance(errors[0], dict) else errors[0]
                self.show_sync_notice("Sync issue: " + str(detail or "Unknown error")[:90], 2800)
            elif skipped:
                self.show_sync_notice("Sync skipped protected thermostat(s)", 2200)
        self.position_sleep_controls()

    def refresh_sync_status(self):
        if getattr(self, "_sync_status_poll_running", False):
            return
        self._sync_status_poll_running = True

        def done(result):
            self._sync_status_poll_running = False
            self._sync_status_initialized = True
            self._sync_status_retry_after = 0.0
            self.apply_sync_status(result)

        def failed(_err):
            self._sync_status_poll_running = False
            self._sync_status_initialized = False
            self._sync_status_retry_after = time.monotonic() + 10.0

        self.run_async("sync-status", lambda: self.s.api.get("/api/sync/status"), done, failed)

    def update_sync_button_state(self):
        self.update_intimacy_button_state()
        try:
            peers = self.sync_peer_entities()
            has_peers = bool(peers)
            active = has_peers and time.monotonic() < float(getattr(self, "_sync_active_until", 0.0) or 0.0)
            if not active:
                self._sync_active_until = 0.0
            remaining = max(0, int(math.ceil(float(getattr(self, "_sync_active_until", 0.0) or 0.0) - time.monotonic()))) if active else 0
            on_thermostat_page = self.current_name == "Thermostat"
            render = (active, remaining, has_peers, on_thermostat_page)
            previous = getattr(self, "_last_sync_button_render", None)
            if render != previous:
                self._last_sync_button_render = render
                if hasattr(self, "sync_button"):
                    self.sync_button.setActive(active, remaining)
                # Sync visibility depends on both peer availability and the
                # current page. Reconcile the floating controls whenever either
                # changes so a button hidden on another page cannot remain
                # stale-hidden after returning to Thermostat. Countdown-only
                # changes still avoid a full overlay reposition every second.
                if (
                    previous is None
                    or previous[0] != active
                    or previous[2] != has_peers
                    or len(previous) < 4
                    or previous[3] != on_thermostat_page
                ):
                    self.position_sleep_controls()

            initial_retry_due = bool(
                not getattr(self, "_sync_status_initialized", False)
                and time.monotonic() >= float(getattr(self, "_sync_status_retry_after", 0.0) or 0.0)
            )
            should_poll_backend = bool(
                active
                or initial_retry_due
                or getattr(self, "_sync_toggle_pending_generation", 0)
                or getattr(self, "_sync_apply_running", False)
            )
            if should_poll_backend:
                self.refresh_sync_status()
        except Exception:
            pass

    def update_intimacy_button_state(self):
        try:
            thermostat = self.s.thermostat if isinstance(self.s.thermostat, dict) else {}
            hold = thermostat.get("intimacyHold") if isinstance(thermostat.get("intimacyHold"), dict) else {}
            now_ms = int(time.time() * 1000)
            expires_at = int(float(hold.get("expiresAt") or 0))
            active = bool(hold.get("active")) and expires_at > now_ms
            remaining = max(0, int(math.ceil((expires_at - now_ms) / 1000.0))) if active else 0

            optimistic_until = float(getattr(self, "_intimacy_optimistic_until", 0.0) or 0.0)
            if not active and optimistic_until > time.monotonic():
                active = True
                remaining = max(1, int(math.ceil(optimistic_until - time.monotonic())))

            if hasattr(self, "intimacy_button"):
                self.intimacy_button.setTargetTemp(intimacy_hold_target_f(thermostat))
                self.intimacy_button.setActive(active, remaining)
        except Exception:
            pass

    def toggle_intimacy_hold(self):
        if getattr(self, "navigation_locked", False):
            self.toast.show_message(self.lock_restriction_message())
            return
        if getattr(self, "_intimacy_toggle_running", False):
            return

        thermostat = self.s.thermostat if isinstance(self.s.thermostat, dict) else {}
        hold = thermostat.get("intimacyHold") if isinstance(thermostat.get("intimacyHold"), dict) else {}
        now_ms = int(time.time() * 1000)
        try:
            expires_at = int(float(hold.get("expiresAt") or 0))
        except (TypeError, ValueError):
            expires_at = 0
        active = bool(hold.get("active")) and expires_at > now_ms
        if float(getattr(self, "_intimacy_optimistic_until", 0.0) or 0.0) > time.monotonic():
            active = True
        turning_on = not active
        self._intimacy_toggle_running = True
        hold_target = intimacy_hold_target_f(thermostat)

        if turning_on:
            self._intimacy_optimistic_until = time.monotonic() + (6 * 60 * 60)
            thermostat["targetTemp"] = hold_target
            thermostat["intimacyHold"] = {
                "active": True,
                "startedAt": now_ms,
                "expiresAt": now_ms + (6 * 60 * 60 * 1000),
                "targetTemp": hold_target,
            }
            self.intimacy_button.setTargetTemp(hold_target)
            self.intimacy_button.setActive(True, 6 * 60 * 60)
            self.header.update_values(thermostat.get("currentTemp"), hold_target)
            self.sync_visible_page("Thermostat")
        else:
            self._intimacy_optimistic_until = 0.0
            self.intimacy_button.setActive(False, 0)

        def done(result):
            self._intimacy_toggle_running = False
            self._intimacy_optimistic_until = 0.0
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
            self.sync_runtime_only()
            target_text = intimacy_hold_target_text(self.s.thermostat if isinstance(self.s.thermostat, dict) else thermostat)
            self.toast.show_message(
                f"{target_text} hold on for 6 hours" if turning_on else f"{target_text} hold off — normal schedule resumed",
                2600,
            )

        def failed(err):
            self._intimacy_toggle_running = False
            self._intimacy_optimistic_until = 0.0
            self.toast.show_message(f"{hold_target}°F hold failed: {err}", 3200)
            self.refresh_status()

        self.run_async(
            "intimacy-hold-on" if turning_on else "intimacy-hold-off",
            lambda: self.s.api.thermostat_update({
                "intimacyHoldAction": "on" if turning_on else "off",
                "intimacyHoldDurationSeconds": 6 * 60 * 60,
                "commandSource": "touchscreen",
            }),
            done,
            failed,
        )

    def toggle_sync_mode(self):
        if getattr(self, "navigation_locked", False):
            self.toast.show_message(self.lock_restriction_message())
            return
        peers = self.sync_peer_entities()
        if not peers:
            self.toast.show_message("Choose Sync thermostats in Settings")
            self.update_sync_button_state()
            return

        # Update the touchscreen immediately, then send the explicit desired
        # state. Using arm/off instead of a blind backend toggle prevents a stale
        # local status snapshot from accidentally turning Sync the wrong way.
        turning_on = not self.sync_is_active()
        self._sync_toggle_generation = int(getattr(self, "_sync_toggle_generation", 0) or 0) + 1
        generation = self._sync_toggle_generation
        self._sync_toggle_pending_generation = generation
        self._sync_optimistic_active = turning_on
        if turning_on:
            self._sync_active_until = time.monotonic() + 30.0
            self.sync_button.setActive(True, 30)
        else:
            self._sync_active_until = 0.0
            self._sync_pending_changes = {}
            self.peer_sync_timer.stop()
            self.sync_button.setActive(False, 0)
        self.position_sleep_controls()

        def done(result):
            # Ignore an older response after the user has tapped the button
            # again. A fresh status read then confirms the backend's final state.
            if generation == int(getattr(self, "_sync_toggle_generation", 0) or 0):
                self._sync_toggle_pending_generation = 0
                self.apply_sync_status(result, authoritative=True)
            self.refresh_sync_status()

        def failed(err):
            if generation == int(getattr(self, "_sync_toggle_generation", 0) or 0):
                self._sync_toggle_pending_generation = 0
                self.show_sync_notice(f"Sync failed: {err}", 3000)
            self.refresh_sync_status()

        self.run_async(
            f"sync-toggle-{generation}",
            lambda: self.s.api.post(
                "/api/sync/arm",
                {"action": "arm" if turning_on else "off", "durationSeconds": 30, "source": "touchscreen"},
                timeout=4.0,
            ),
            done,
            failed,
        )

    def request_peer_sync(self, changes: dict) -> bool:
        """Queue a direct touchscreen sync while the shared window is active.

        The backend also observes the subsequent thermostat control request.
        Keeping this explicit touchscreen path removes the arm/control race: the
        user can tap Sync and immediately tap Arriving or a temperature button,
        and the peer command is already queued from the optimistic local state.
        """
        if getattr(self, "navigation_locked", False):
            return False
        if not self.sync_is_active() or not isinstance(changes, dict):
            return False
        allowed: dict[str, object] = {}
        mode = str(changes.get("mode") or changes.get("hvacMode") or "").strip().lower()
        if mode in {"off", "heat", "cool"}:
            allowed["mode"] = mode
        preset = str(changes.get("presetMode") or changes.get("preset_mode") or changes.get("preset") or "").strip().lower()
        if preset == "arriving":
            allowed["presetMode"] = preset
        if "targetTemp" in changes:
            try:
                allowed["targetTemp"] = int(clamp(round(float(changes.get("targetTemp"))), 45, 95))
            except Exception:
                pass
        if not allowed:
            return False
        self._sync_pending_changes.update(allowed)
        self.peer_sync_timer.start(120)
        # Do not poll backend status here. This command may be the immediate tap
        # after Sync, while the arm request is still in flight; the optimistic
        # local window is intentionally authoritative until that request returns.
        if hasattr(self, "sync_button"):
            remaining = max(1, int(math.ceil(self._sync_active_until - time.monotonic())))
            self.sync_button.setActive(True, remaining)
        return True

    def show_sync_notice(self, message: str, duration: int = 1800):
        now = time.monotonic()
        if now - float(getattr(self, "_last_sync_notice_at", 0.0) or 0.0) < 4.0:
            return
        self._last_sync_notice_at = now
        try:
            self.toast.show_message(message, duration)
        except Exception:
            pass

    def flush_peer_sync(self):
        if getattr(self, "_sync_apply_running", False):
            self.peer_sync_timer.start(180)
            return
        # A queued command was authorized at the exact moment the user tapped
        # the thermostat control. Send it even if an older status poll briefly
        # reports the backend arm request as still pending. Pressing Sync again
        # cancels safely because toggle_sync_mode clears this queue and timer.
        peers = self.sync_peer_entities()
        changes = dict(getattr(self, "_sync_pending_changes", {}) or {})
        if not peers or not changes:
            return
        self._sync_pending_changes = {}
        payload = {
            "changes": changes,
            "ensureArmed": True,
            "durationSeconds": 30,
            "source": "touchscreen",
        }
        self._sync_apply_running = True

        def done(result):
            self._sync_apply_running = False
            info = result if isinstance(result, dict) else {}
            skipped = info.get("skipped") if isinstance(info.get("skipped"), list) else []
            errors = info.get("errors") if isinstance(info.get("errors"), list) else []
            if errors:
                detail = errors[0].get("error") if isinstance(errors[0], dict) else errors[0]
                self.show_sync_notice("Sync issue: " + str(detail or "Unknown error")[:90], 2800)
            elif skipped:
                self.show_sync_notice("Sync skipped protected thermostat(s)", 2200)
            if self._sync_pending_changes and self.sync_is_active():
                self.peer_sync_timer.start(120)
            self.update_sync_button_state()

        def failed(err):
            self._sync_apply_running = False
            self.show_sync_notice(f"Sync failed: {err}", 3000)
            if self._sync_pending_changes and self.sync_is_active():
                self.peer_sync_timer.start(180)
            self.update_sync_button_state()

        self.run_async(
            "peer-sync",
            lambda: self.s.api.post("/api/sync/dispatch", payload, timeout=10.0),
            done,
            failed,
        )

    @staticmethod
    def _touch_global_point(event) -> QPoint | None:
        points = []
        try:
            points = list(event.touchPoints())
        except Exception:
            points = []
        if not points:
            try:
                points = list(event.changedTouchPoints())
            except Exception:
                points = []
        if not points:
            return None
        point = points[0]
        try:
            screen_pos = point.screenPos()
            return QPoint(int(round(screen_pos.x())), int(round(screen_pos.y())))
        except Exception:
            return None

    @staticmethod
    def _button_global_rect(button: QAbstractButton) -> QRectF:
        top_left = button.mapToGlobal(button.rect().topLeft())
        return QRectF(float(top_left.x()), float(top_left.y()), float(button.width()), float(button.height()))

    @staticmethod
    def _button_ancestor(widget) -> QAbstractButton | None:
        current = widget
        while current is not None:
            if isinstance(current, QAbstractButton):
                return current
            try:
                current = current.parentWidget()
            except Exception:
                return None
        return None

    def _button_at_touch_point(self, global_point: QPoint) -> QAbstractButton | None:
        direct = self._button_ancestor(QApplication.widgetAt(global_point))
        if direct is not None and direct.isVisible() and direct.isEnabled():
            return direct

        # Keep the controls visually unchanged while allowing a very small
        # invisible edge tolerance for imprecise capacitive touch coordinates.
        root = QApplication.activeModalWidget() or QApplication.activeWindow() or self
        try:
            buttons = list(root.findChildren(QAbstractButton))
            if isinstance(root, QAbstractButton):
                buttons.append(root)
        except Exception:
            buttons = []
        candidates: list[tuple[float, QAbstractButton]] = []
        point_f = QPointF(global_point)
        for button in buttons:
            try:
                if not button.isVisible() or not button.isEnabled():
                    continue
                rect = self._button_global_rect(button)
                if not rect.adjusted(-6.0, -6.0, 6.0, 6.0).contains(point_f):
                    continue
                center = rect.center()
                distance = abs(center.x() - point_f.x()) + abs(center.y() - point_f.y())
                candidates.append((float(distance), button))
            except RuntimeError:
                continue
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _activate_direct_touch_button(self, button: QAbstractButton):
        try:
            if button is not None and button.isVisible() and button.isEnabled():
                button.click()
        except RuntimeError:
            pass

    def _handle_direct_button_touch(self, event_type, event) -> bool:
        touch_types = {QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd, QEvent.TouchCancel}
        if event_type not in touch_types:
            return False

        point = self._touch_global_point(event) or self._direct_touch_last_global
        if event_type == QEvent.TouchBegin:
            if point is None:
                return False
            button = self._button_at_touch_point(point)
            if button is None:
                return False
            self._direct_touch_button = button
            self._direct_touch_last_global = point
            self._touch_input_active = True
            try:
                if isinstance(button, ScreenLockButton):
                    button.begin_press()
                else:
                    button.setDown(True)
                    button.update()
            except RuntimeError:
                self._direct_touch_button = None
                self._touch_input_active = False
                return False
            event.accept()
            return True

        button = self._direct_touch_button
        if button is None:
            return False
        if point is not None:
            self._direct_touch_last_global = point
        try:
            hit_point = QPointF(self._direct_touch_last_global) if self._direct_touch_last_global is not None else QPointF()
            inside = self._button_global_rect(button).adjusted(-10.0, -10.0, 10.0, 10.0).contains(hit_point)
        except RuntimeError:
            inside = False

        if event_type == QEvent.TouchUpdate:
            try:
                if isinstance(button, ScreenLockButton):
                    button.update_press(bool(inside))
                else:
                    button.setDown(bool(inside))
                    button.update()
            except RuntimeError:
                pass
            event.accept()
            return True

        accepted = bool(event_type == QEvent.TouchEnd and inside)
        try:
            if isinstance(button, ScreenLockButton):
                if event_type == QEvent.TouchEnd:
                    accepted = button.end_press(bool(inside), emit_click=False)
                else:
                    button.cancel_press()
            else:
                button.setDown(False)
                button.update()
        except RuntimeError:
            accepted = False
        self._direct_touch_button = None
        self._touch_input_active = False
        self._direct_touch_mouse_suppress_until = time.monotonic() + 0.22
        self._direct_touch_mouse_suppress_point = self._direct_touch_last_global
        self._direct_touch_last_global = None
        event.accept()
        if accepted:
            # Run after the touch event returns.  Opening a modal from inside the
            # QTouchEvent filter can otherwise leave the release tied to the old
            # widget and make the first tap appear to do nothing.
            QTimer.singleShot(0, lambda b=button: self._activate_direct_touch_button(b))
        QTimer.singleShot(0, self.flush_deferred_interaction_sync)
        return True

    def _suppress_mouse_copy_of_touch(self, event_type, event, now: float) -> bool:
        if event_type not in {QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick}:
            return False
        point = None
        try:
            point = event.globalPos()
        except Exception:
            point = None
        reference = self._direct_touch_last_global if self._direct_touch_button is not None else self._direct_touch_mouse_suppress_point
        deadline = float("inf") if self._direct_touch_button is not None else float(self._direct_touch_mouse_suppress_until)
        if point is None or reference is None or now >= deadline:
            return False
        if (point - reference).manhattanLength() > 24:
            return False
        event.accept()
        return True

    def modal_interaction_active(self) -> bool:
        modal = QApplication.activeModalWidget()
        return modal is not None and modal is not self

    def background_interaction_busy(self) -> bool:
        return bool(getattr(self, "_touch_input_active", False) or self.modal_interaction_active())

    def flush_deferred_interaction_sync(self):
        if not getattr(self, "_interaction_runtime_sync_pending", False):
            return
        if self.background_interaction_busy():
            return
        self._interaction_runtime_sync_pending = False
        self.sync_runtime_only()

    def display_input_event_types(self) -> set:
        return {
            QEvent.MouseButtonPress,
            QEvent.MouseButtonRelease,
            QEvent.MouseButtonDblClick,
            QEvent.TouchBegin,
            QEvent.TouchUpdate,
            QEvent.TouchEnd,
            QEvent.KeyPress,
            QEvent.Wheel,
        }

    def display_activity_event_types(self) -> set:
        return {
            QEvent.MouseButtonPress,
            QEvent.MouseButtonDblClick,
            QEvent.TouchBegin,
            QEvent.KeyPress,
            QEvent.Wheel,
        }

    def edge_brightness_event_types(self) -> set:
        return {
            QEvent.MouseButtonPress,
            QEvent.MouseMove,
            QEvent.MouseButtonRelease,
            QEvent.TouchBegin,
            QEvent.TouchUpdate,
            QEvent.TouchEnd,
            QEvent.TouchCancel,
        }

    def _event_point_in_window(self, event) -> QPoint | None:
        try:
            if event.type() in {QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease}:
                if hasattr(event, "globalPos"):
                    return self.mapFromGlobal(event.globalPos())
                if hasattr(event, "pos"):
                    point = event.pos()
                    return QPoint(int(point.x()), int(point.y()))
            if event.type() in {QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd, QEvent.TouchCancel}:
                points = []
                try:
                    points = list(event.touchPoints())
                except Exception:
                    points = []
                if not points:
                    try:
                        points = list(event.changedTouchPoints())
                    except Exception:
                        points = []
                if not points:
                    return None
                point = points[0]
                screen_pos = None
                try:
                    screen_pos = point.screenPos()
                except Exception:
                    screen_pos = None
                if screen_pos is not None:
                    return self.mapFromGlobal(QPoint(int(screen_pos.x()), int(screen_pos.y())))
                try:
                    pos = point.pos()
                    return QPoint(int(pos.x()), int(pos.y()))
                except Exception:
                    return None
        except Exception:
            return None
        return None

    def _brightness_edge_width(self) -> int:
        return int(clamp(SCREEN_BRIGHTNESS_EDGE_WIDTH_PX, 24, max(24, min(160, self.width() // 5))))

    def handle_edge_brightness_event(self, event_type, event) -> bool:
        if not SCREEN_BRIGHTNESS_EDGE_ENABLED:
            return False
        if QApplication.activePopupWidget() is not None:
            return False
        point = self._event_point_in_window(event)
        if point is None:
            if event_type in {QEvent.TouchCancel, QEvent.TouchEnd, QEvent.MouseButtonRelease}:
                was_active = bool(getattr(self, "_brightness_drag_active", False))
                self._brightness_drag_active = False
                return was_active
            return False
        x = int(point.x())
        y = int(point.y())
        if event_type in {QEvent.MouseButtonPress, QEvent.TouchBegin}:
            if x < 0 or y < 0 or x > self.width() or y > self.height():
                return False
            if x > self._brightness_edge_width():
                self._brightness_drag_active = False
                return False
            self._brightness_drag_active = True
            self._brightness_drag_start_y = float(y)
            self._brightness_drag_start_pct = int(clamp(getattr(self, "_screen_brightness_pct", SCREEN_BRIGHTNESS_DEFAULT_PERCENT), SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
            self._brightness_drag_moved = False
            self._last_user_activity_at = time.monotonic()
            return True
        if event_type in {QEvent.MouseMove, QEvent.TouchUpdate}:
            if not getattr(self, "_brightness_drag_active", False):
                return False
            pixels_per_percent = max(1.0, float(SCREEN_BRIGHTNESS_PIXELS_PER_PERCENT or 7.0))
            delta_pct = int(round((float(getattr(self, "_brightness_drag_start_y", y)) - float(y)) / pixels_per_percent))
            target = int(clamp(int(getattr(self, "_brightness_drag_start_pct", self._screen_brightness_pct)) + delta_pct, SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
            if abs(float(y) - float(getattr(self, "_brightness_drag_start_y", y))) >= 3:
                self._brightness_drag_moved = True
            if target != getattr(self, "_screen_brightness_pct", None):
                self.set_screen_brightness_percent(target, show_feedback=True)
            self._last_user_activity_at = time.monotonic()
            return True
        if event_type in {QEvent.MouseButtonRelease, QEvent.TouchEnd, QEvent.TouchCancel}:
            if not getattr(self, "_brightness_drag_active", False):
                return False
            self._brightness_drag_active = False
            self._last_user_activity_at = time.monotonic()
            return True
        return False

    def eventFilter(self, obj, event):
        try:
            event_type = event.type()
            now = time.monotonic()
            if event_type == QEvent.Show and isinstance(obj, (QAbstractButton, QDialog)):
                # Ensure newly opened dialogs and their buttons expose the raw
                # touch sequence to the application filter instead of relying
                # only on delayed X11 mouse synthesis.
                obj.setAttribute(Qt.WA_AcceptTouchEvents, True)
            if getattr(self, "_thermal_protection_active", False) and event_type in self.display_input_event_types():
                return True
            if event_type in self.display_input_event_types():
                if getattr(self, "_display_sleeping", False):
                    # Touchscreens commonly arrive as either Qt touch events or
                    # synthesized mouse-button events. Only those physical panel
                    # inputs may clear a manual Sleep-button lock.
                    touch_wake_events = {
                        QEvent.MouseButtonPress,
                        QEvent.MouseButtonRelease,
                        QEvent.MouseButtonDblClick,
                        QEvent.TouchBegin,
                        QEvent.TouchUpdate,
                        QEvent.TouchEnd,
                    }
                    wake_source = "touch" if event_type in touch_wake_events else "input"
                    self.wake_display_screen(source=wake_source)
                    return True
                if now < getattr(self, "_display_wake_block_until", 0.0):
                    return True
                if now < getattr(self, "_modal_touch_block_until", 0.0):
                    return True
                if event_type in self.display_activity_event_types():
                    self._last_user_activity_at = now
                    self._last_motion_activity_at = now

            # Complete button taps from the original touch sequence.  This is
            # intentionally before edge-brightness handling so an actual button
            # at the left edge remains a button rather than becoming a drag.
            if self._handle_direct_button_touch(event_type, event):
                return True
            if self._suppress_mouse_copy_of_touch(event_type, event, now):
                return True

            if event_type in self.edge_brightness_event_types():
                if getattr(self, "_display_sleeping", False):
                    return False
                if now < getattr(self, "_display_wake_block_until", 0.0):
                    return True
                if getattr(self, "navigation_locked", False):
                    self._brightness_drag_active = False
                elif self.handle_edge_brightness_event(event_type, event):
                    return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def find_screen_backlight_path(self) -> Path | None:
        try:
            if SCREEN_BRIGHTNESS_BACKLIGHT_PATH:
                path = Path(SCREEN_BRIGHTNESS_BACKLIGHT_PATH)
                if path.exists():
                    return path
            base = Path("/sys/class/backlight")
            if not base.exists():
                return None
            candidates: list[tuple[int, Path]] = []
            for item in base.iterdir():
                brightness = item / "brightness"
                max_brightness = item / "max_brightness"
                if not brightness.exists() or not max_brightness.exists():
                    continue
                try:
                    max_value = int(max_brightness.read_text().strip() or "0")
                except Exception:
                    max_value = 0
                candidates.append((max_value, brightness))
            if not candidates:
                return None
            candidates.sort(reverse=True, key=lambda pair: pair[0])
            return candidates[0][1]
        except Exception:
            return None

    def read_screen_brightness_percent(self) -> int:
        default = int(clamp(SCREEN_BRIGHTNESS_DEFAULT_PERCENT, SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
        path = self.find_screen_backlight_path()
        if path is None:
            return default
        try:
            raw = int(path.read_text().strip() or "0")
            max_raw = int((path.parent / "max_brightness").read_text().strip() or "0")
            if max_raw <= 0:
                return default
            pct = int(round((raw / max_raw) * 100.0))
            return int(clamp(pct, SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
        except Exception:
            return default

    def set_screen_brightness_percent(self, percent: int, show_feedback: bool = False):
        pct = int(clamp(percent, SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
        self._screen_brightness_pct = pct
        self._screen_brightness_pending_pct = pct
        if show_feedback:
            self.maybe_show_brightness_feedback(pct)
        if getattr(self, "_screen_brightness_inflight", False):
            return
        self.screen_brightness_timer.start(max(10, SCREEN_BRIGHTNESS_APPLY_DELAY_MS))

    def maybe_show_brightness_feedback(self, pct: int):
        now = time.monotonic()
        if now - getattr(self, "_last_brightness_toast_at", 0.0) < 0.45:
            return
        self._last_brightness_toast_at = now
        try:
            self.toast.show_message(f"Screen brightness {pct}%", 900)
        except Exception:
            pass

    def flush_screen_brightness(self):
        if getattr(self, "_screen_brightness_inflight", False):
            return
        pct = self._screen_brightness_pending_pct
        if pct is None:
            return
        self._screen_brightness_pending_pct = None
        self._screen_brightness_inflight = True

        def worker():
            ok = False
            detail = ""
            try:
                ok, detail = self.apply_screen_brightness_now(int(pct))
            except Exception as exc:
                detail = str(exc)
            self.screenBrightnessCompleted.emit({"ok": ok, "detail": detail, "percent": int(pct)})

        threading.Thread(target=worker, name="screen-brightness", daemon=True).start()

    def handle_screen_brightness_completed(self, data: object):
        self._screen_brightness_inflight = False
        if self._screen_brightness_pending_pct is not None:
            self.screen_brightness_timer.start(20)
            return
        info = data if isinstance(data, dict) else {}
        if not info.get("ok"):
            detail = str(info.get("detail") or "not supported").strip()
            try:
                print(f"Screen brightness apply failed: {detail}", flush=True)
            except Exception:
                pass

    def apply_screen_brightness_now(self, percent: int) -> tuple[bool, str]:
        pct = int(clamp(percent, SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
        ratio = max(0.01, min(1.0, pct / 100.0))
        if SCREEN_BRIGHTNESS_COMMAND:
            command = SCREEN_BRIGHTNESS_COMMAND.format(percent=pct, ratio=f"{ratio:.3f}")
            result = subprocess.run(command, shell=True, check=False, timeout=1.5)
            if result.returncode == 0:
                return True, "custom command"
        path = self.find_screen_backlight_path()
        if path is not None:
            try:
                max_raw = int((path.parent / "max_brightness").read_text().strip() or "0")
                if max_raw > 0:
                    raw = int(clamp(round(max_raw * ratio), 1, max_raw))
                    path.write_text(f"{raw}\n")
                    return True, str(path)
            except Exception as exc:
                last_error = str(exc)
            else:
                last_error = ""
        else:
            last_error = "no backlight device"
        try:
            env = os.environ.copy()
            env.setdefault("DISPLAY", ":0")
            result = subprocess.run(
                ["xrandr", "--output", SCREEN_BRIGHTNESS_DISPLAY_OUTPUT, "--brightness", f"{ratio:.3f}"],
                check=False,
                timeout=1.5,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode == 0:
                return True, "xrandr"
            stderr = (result.stderr or "").strip()
            return False, stderr or last_error or f"xrandr exited {result.returncode}"
        except FileNotFoundError:
            return False, last_error or "xrandr not installed"
        except Exception as exc:
            return False, f"{last_error}; {exc}" if last_error else str(exc)

    def _run_display_power_command(self, command: str):
        command = str(command or "").strip()
        if not command:
            return

        def worker():
            try:
                subprocess.run(command, shell=True, check=False, timeout=2)
            except Exception as exc:
                try:
                    print(f"Display power command failed: {exc}", flush=True)
                except Exception:
                    pass

        threading.Thread(target=worker, name="screen-power-command", daemon=True).start()

    def enter_display_sleep(self, manual: bool = False):
        if manual and getattr(self, "navigation_locked", False):
            self.toast.show_message(self.lock_restriction_message())
            return
        if getattr(self, "_display_sleeping", False):
            return
        self._display_sleeping = True
        self._display_wake_block_until = 0.0
        self._motion_wake_block_until = time.monotonic() + (5.0 if manual else 0.0)
        # Pressing the on-screen Sleep button is an explicit request to keep the
        # display off. Preserve that state until a real touchscreen/mouse input
        # wakes it; motion and other programmatic wake paths must be ignored.
        self._manual_sleep_touch_only = bool(manual)
        self._update_screen_motion_poll_interval()
        self.position_sleep_controls()
        self.sleep_overlay.show()
        self.sleep_overlay.raise_()
        # Give Qt one paint cycle to place the black shield before DPMS turns the
        # panel off. If DPMS is unsupported, the black shield still prevents burn-in
        # and blocks accidental touches.
        QTimer.singleShot(120, lambda: self._run_display_power_command(SCREEN_SLEEP_OFF_COMMAND))

    def wake_display_screen(self, source: str = "programmatic"):
        if not getattr(self, "_display_sleeping", False):
            return
        if getattr(self, "_manual_sleep_touch_only", False) and source != "touch":
            return
        self._display_sleeping = False
        self._manual_sleep_touch_only = False
        self._display_wake_block_until = time.monotonic() + SCREEN_WAKE_INPUT_BLOCK_SECONDS
        self._last_user_activity_at = time.monotonic()
        self._last_motion_activity_at = self._last_user_activity_at
        self._update_screen_motion_poll_interval()
        self._run_display_power_command(SCREEN_SLEEP_ON_COMMAND)
        self.sleep_overlay.hide()
        self.position_sleep_controls()
        # Refresh Alarmo immediately on wake. The first wake touch remains blocked,
        # and tapping Alarmo performs a second per-tap verification before controls
        # are shown, so a sleeping display can never expose stale arm/disarm state.
        page = self.pages.get("Thermostat")
        if self.configured_alarm_entity_id() and isinstance(page, ThermostatScreen):
            page.mark_alarm_state_checking()
            QTimer.singleShot(0, self.refresh_alarm_state)
        # Some display stacks reset gamma/backlight state after DPMS wake. Reapply
        # the last edge-gesture brightness shortly after the panel comes back.
        QTimer.singleShot(250, lambda: self.set_screen_brightness_percent(getattr(self, "_screen_brightness_pct", SCREEN_BRIGHTNESS_DEFAULT_PERCENT), show_feedback=False))
        QTimer.singleShot(int(SCREEN_WAKE_INPUT_BLOCK_SECONDS * 1000), self.finish_display_wake)

    def finish_display_wake(self):
        self._display_wake_block_until = 0.0
        self._last_user_activity_at = time.monotonic()
        self.position_sleep_controls()

    def current_screen_display_settings(self) -> dict:
        return screen_display_settings(self.s.config if hasattr(self, "s") else {})

    def screen_brightness_time_is_active(self, settings: dict, now: datetime | None = None) -> bool:
        current = now or datetime.now()
        start_h, start_m = parse_schedule_time_24h(settings.get("brightnessTimeStart", "22:00"), 22, 0)
        end_h, end_m = parse_schedule_time_24h(settings.get("brightnessTimeEnd", "07:00"), 7, 0)
        current_minutes = current.hour * 60 + current.minute
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        if start_minutes == end_minutes:
            return True
        if start_minutes < end_minutes:
            return start_minutes <= current_minutes < end_minutes
        return current_minutes >= start_minutes or current_minutes < end_minutes

    def evaluate_screen_brightness_automation(self):
        settings = self.current_screen_display_settings()
        rules = settings.get("brightnessEntityRules") if isinstance(settings.get("brightnessEntityRules"), list) else []
        time_enabled = bool(settings.get("brightnessTimeEnabled", False))
        if not time_enabled and not rules:
            self._screen_brightness_automation_target = None
            return

        normal = int(clamp(settings.get("brightnessNormalPercent", SCREEN_BRIGHTNESS_DEFAULT_PERCENT), SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT))
        matching_targets: list[int] = []
        if time_enabled and self.screen_brightness_time_is_active(settings):
            matching_targets.append(int(clamp(settings.get("brightnessTimePercent", 40), SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT)))

        states = getattr(self, "_screen_brightness_rule_states", {}) or {}
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            entity_id = str(rule.get("entityId") or "").strip()
            if not entity_id:
                continue
            expected = str(rule.get("state") or "on").strip().lower()
            actual = str(states.get(entity_id) or "").strip().lower()
            if actual != expected:
                continue
            try:
                target = int(round(float(rule.get("brightnessPercent", 40))))
            except (TypeError, ValueError):
                target = 40
            matching_targets.append(int(clamp(target, SCREEN_BRIGHTNESS_MIN_PERCENT, SCREEN_BRIGHTNESS_MAX_PERCENT)))

        target = min(matching_targets) if matching_targets else normal
        self._screen_brightness_automation_target = target
        if int(getattr(self, "_screen_brightness_pct", target)) != target:
            self.set_screen_brightness_percent(target, show_feedback=False)

    def refresh_screen_brightness_automation(self):
        settings = self.current_screen_display_settings()
        rules = settings.get("brightnessEntityRules") if isinstance(settings.get("brightnessEntityRules"), list) else []
        time_enabled = bool(settings.get("brightnessTimeEnabled", False))
        if not time_enabled and not rules:
            self._screen_brightness_automation_target = None
            self._screen_brightness_rule_states = {}
            return

        # Time rules do not need Home Assistant and should continue to evaluate
        # even when HA is offline or an entity-state request is still in flight.
        self.evaluate_screen_brightness_automation()

        entity_ids: list[str] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            entity_id = str(rule.get("entityId") or "").strip()
            if entity_id and entity_id not in entity_ids:
                entity_ids.append(entity_id)
        if not entity_ids:
            self._screen_brightness_rule_states = {}
            return

        ha = self.s.ha()
        if not str(ha.get("url") or "").strip() or not str(ha.get("token") or "").strip():
            self._screen_brightness_rule_states = {}
            self.evaluate_screen_brightness_automation()
            return
        if getattr(self, "_screen_brightness_rule_poll_running", False):
            return
        self._screen_brightness_rule_poll_running = True

        def worker():
            try:
                result = self.s.api.post(
                    "/api/ha/room/states",
                    self.s.ha_payload({"entityIds": entity_ids}),
                )
                self.screenBrightnessRuleStatesCompleted.emit({"result": result, "error": None})
            except Exception as exc:
                self.screenBrightnessRuleStatesCompleted.emit({"result": None, "error": str(exc)})

        self._submit_status_worker(worker)

    def handle_screen_brightness_rule_states_completed(self, info: object):
        self._screen_brightness_rule_poll_running = False
        data = info if isinstance(info, dict) else {}
        error = str(data.get("error") or "").strip()
        if not error:
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            controls = result.get("controls") if isinstance(result.get("controls"), list) else []
            states: dict[str, str] = {}
            for item in controls:
                if not isinstance(item, dict):
                    continue
                entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
                if entity_id:
                    states[entity_id] = str(item.get("state") or "").strip().lower()
            self._screen_brightness_rule_states = states
        self.evaluate_screen_brightness_automation()

    def refresh_screen_motion_status(self):
        settings = self.current_screen_display_settings()
        if not (settings.get("motionAutoSleepEnabled") or settings.get("motionAutoWakeEnabled")):
            self._screen_motion_available = False
            self._screen_motion_active = False
            self._update_screen_motion_poll_interval()
            return
        if self._screen_motion_poll_running:
            return
        self._screen_motion_poll_running = True

        def worker():
            try:
                result = self.s.api.get("/api/hardware/motion")
                self.screenMotionCompleted.emit({"result": result, "error": None})
            except Exception as exc:
                self.screenMotionCompleted.emit({"result": None, "error": str(exc)})

        self._submit_status_worker(worker)

    def handle_screen_motion_completed(self, info: object):
        self._screen_motion_poll_running = False
        self._update_screen_motion_poll_interval()
        data = info if isinstance(info, dict) else {}
        if data.get("error"):
            self._screen_motion_available = False
            self._screen_motion_active = False
            return
        result = data.get("result") if isinstance(data.get("result"), dict) else {}
        available = bool(result.get("available"))
        motion = bool(result.get("motion")) if available else False
        self._screen_motion_available = available
        self._screen_motion_active = motion
        if not available:
            return

        now = time.monotonic()
        settings = self.current_screen_display_settings()
        if motion:
            self._last_motion_activity_at = now
            if (
                bool(settings.get("motionAutoWakeEnabled"))
                and getattr(self, "_display_sleeping", False)
                and now >= float(getattr(self, "_motion_wake_block_until", 0.0) or 0.0)
            ):
                self.wake_display_screen(source="motion")
            return

        if not bool(settings.get("motionAutoSleepEnabled")):
            return
        if getattr(self, "_display_sleeping", False):
            return
        if hasattr(self, "assistant_overlay") and self.assistant_overlay.is_active():
            return
        if QApplication.activeModalWidget() is not None:
            return
        timeout_seconds = max(60, int(settings.get("motionAutoSleepMinutes", 5)) * 60)
        if now - float(getattr(self, "_last_motion_activity_at", now)) >= timeout_seconds:
            self.enter_display_sleep(manual=False)

    def check_display_sleep_idle(self):
        try:
            if getattr(self, "_display_sleeping", False):
                return
            if hasattr(self, "assistant_overlay") and self.assistant_overlay.is_active():
                return
            settings = self.current_screen_display_settings()
            if not bool(settings.get("inactivityAutoOffEnabled")):
                return
            if QApplication.activeModalWidget() is not None:
                return
            timeout_seconds = max(60, int(settings.get("inactivityAutoOffMinutes", DEFAULT_SCREEN_INACTIVITY_MINUTES)) * 60)
            if time.monotonic() - getattr(self, "_last_user_activity_at", time.monotonic()) >= timeout_seconds:
                self.enter_display_sleep(manual=False)
        except Exception:
            pass

    def audio_page_is_music_playing(self) -> bool:
        page = self.pages.get("Audio")
        if isinstance(page, AudioScreen) and page.is_non_tv_audio_playing():
            self._auto_nav_audio_is_playing = True
            self._auto_nav_audio_state_at = time.monotonic()
            return True
        # Use the last background poll result when the Audio page is not visible.
        # The poll runs asynchronously, so checking only the page state can be stale
        # and was preventing the two-minute return-to-audio behavior from firing.
        if time.monotonic() - getattr(self, "_auto_nav_audio_state_at", 0.0) <= 20.0:
            return bool(getattr(self, "_auto_nav_audio_is_playing", False))
        return False

    def poll_audio_for_auto_navigation(self, now: float):
        if now - getattr(self, "_last_auto_nav_audio_poll_at", 0.0) < AUDIO_FAST_NAV_POLL_SECONDS:
            return
        if getattr(self, "_auto_nav_audio_poll_running", False):
            return
        page = self.pages.get("Audio")
        if not isinstance(page, AudioScreen):
            return
        eid = page.player_id()
        if not eid:
            self._auto_nav_audio_previous_is_playing = bool(getattr(self, "_auto_nav_audio_is_playing", False))
            self._auto_nav_audio_is_playing = False
            self._auto_nav_audio_state_at = now
            return
        payload = self.s.ha_payload({"entityIds": [eid]})
        self._last_auto_nav_audio_poll_at = now
        self._auto_nav_audio_poll_running = True

        def done(result):
            self._auto_nav_audio_poll_running = False
            players = (result or {}).get("players") or []
            was_playing = bool(getattr(self, "_auto_nav_audio_is_playing", False))
            is_playing = False
            if players:
                player = players[0]
                page.player_state = player
                if self.current_name == "Audio":
                    page.apply_player_state()
                is_playing = page.state_is_non_tv_audio_playing(player)
            self._auto_nav_audio_previous_is_playing = was_playing
            self._auto_nav_audio_is_playing = is_playing
            self._auto_nav_audio_state_at = time.monotonic()

            # A new real-music start should jump to Audio quickly. The two-minute
            # idle delay only applies after the user manually leaves Audio while
            # music is already playing. This prevents an immediate bounce-back.
            if (
                is_playing
                and not was_playing
                and self.current_name != "Audio"
                and audio_auto_navigate_enabled(self.s.config)
                and not getattr(self, "navigation_locked", False)
                and QApplication.activeModalWidget() is None
                and time.monotonic() - getattr(self, "_last_audio_manual_leave_at", 0.0) >= AUDIO_IDLE_SECONDS
            ):
                self._last_auto_nav_at = time.monotonic()
                self.set_page("Audio", force=True)

        def failed(_err):
            self._auto_nav_audio_poll_running = False
            self._auto_nav_audio_state_at = time.monotonic()

        page.run_async("audio-auto-nav-poll", lambda: self.s.api.post("/api/ha/media/states", payload), done, failed)

    def check_auto_navigation(self):
        try:
            if not audio_auto_navigate_enabled(self.s.config):
                return
            if getattr(self, "navigation_locked", False):
                return
            if QApplication.activeModalWidget() is not None:
                return
            now = time.monotonic()
            self.poll_audio_for_auto_navigation(now)

            # Returning from a visible Audio page must be driven by the media
            # player's state, not only by the panel-wide touch timer. Home
            # Assistant reports a paused Sonos player as ``paused``; measure how
            # long that state lasts so unrelated panel input cannot postpone the
            # return to Thermostat indefinitely.
            if self.current_name == "Audio":
                page = self.pages.get("Audio")
                state_raw = ""
                if isinstance(page, AudioScreen):
                    state_raw = str((page.player_state or {}).get("state") or "").strip().lower()
                if state_raw in AUDIO_RETURN_TO_THERMOSTAT_STATES:
                    inactive_since = float(getattr(self, "_audio_page_inactive_since", 0.0) or 0.0)
                    if inactive_since <= 0.0:
                        inactive_since = now
                        self._audio_page_inactive_since = inactive_since
                    if (
                        now - inactive_since >= AUDIO_IDLE_SECONDS
                        and now - getattr(self, "_last_auto_nav_at", 0.0) >= 15.0
                    ):
                        self._last_auto_nav_at = now
                        self._audio_page_inactive_since = 0.0
                        self.set_page("Thermostat", force=True)
                        return
                else:
                    self._audio_page_inactive_since = 0.0

            if now - getattr(self, "_last_user_activity_at", now) < AUDIO_IDLE_SECONDS:
                return
            if now - getattr(self, "_last_auto_nav_at", 0.0) < 15.0:
                return
            target = "Audio" if self.audio_page_is_music_playing() else "Thermostat"
            if self.current_name != target:
                self._last_auto_nav_at = now
                self.set_page(target, force=True)
        except Exception:
            pass

    def set_page(self, name: str, force: bool = False):
        if name not in self.pages:
            return
        if getattr(self, "navigation_locked", False) and name != "Thermostat":
            self.current_name = "Thermostat"
            self.stack.setCurrentWidget(self.pages["Thermostat"])
            self.header.set_page("Thermostat")
            self.toast.show_message(self.lock_restriction_message())
            QTimer.singleShot(60, lambda: self.sync_visible_page("Thermostat"))
            return
        previous_name = self.current_name
        now = time.monotonic()
        if previous_name == "Audio" and name != "Audio" and not force and self.audio_page_is_music_playing():
            self._last_audio_manual_leave_at = now
        if name == "Audio" and previous_name != "Audio":
            page = self.pages.get("Audio")
            state_raw = ""
            if isinstance(page, AudioScreen):
                state_raw = str((page.player_state or {}).get("state") or "").strip().lower()
            self._audio_page_inactive_since = now if state_raw in AUDIO_RETURN_TO_THERMOSTAT_STATES else 0.0
        elif name != "Audio":
            self._audio_page_inactive_since = 0.0
        self.current_name = name
        self._last_page_change_at = now
        # Touchscreens can emit a ghost release after a nav tap. Do not let
        # that release open the nearby info button while the page is changing.
        self._ignore_info_until = now + 1.25
        self.stack.setCurrentWidget(self.pages[name])
        self.header.set_page(name)
        # Show the page immediately, then kick a fresh active-page poll so it does not
        # sit on saved config for several seconds after navigation.
        QTimer.singleShot(60, lambda n=name: self.sync_visible_page(n))
        QTimer.singleShot(140, lambda n=name: self.poll_visible_page_now(n))
        # Sync is a floating control whose visibility depends on current_name.
        # Reconcile it on every page transition instead of relying solely on
        # peer/active-state changes in update_sync_button_state().
        QTimer.singleShot(0, self.position_sleep_controls)
        QTimer.singleShot(0, self.update_sync_button_state)



    def settings_code(self) -> str:
        security = self.s.config.get("security") or {}
        alarm = self.s.config.get("alarm") or {}
        # Settings access and alarm disarm are separate credentials. Falling
        # back to alarm.disarmCode made the Settings PIN appear to change after
        # a reboot whenever security.settingsCode was absent.
        code = str(security.get("settingsCode") or alarm.get("settingsCode") or "").strip()
        return code if len(code) == 4 and code.isdigit() else "3762"

    def alarm_disarm_code(self) -> str:
        alarm = self.s.config.get("alarm") or {}
        return str(alarm.get("disarmCode") or "").strip()

    def lock_restriction_message(self) -> str:
        if bool(getattr(self, "security_lock_active", False)):
            return "Security lock active: alarm control only"
        return "Screen locked: temperature and alarm controls only"

    def selected_device_internet_entities(self) -> list[dict]:
        """Return the explicitly configured network-access switches for the iPad button."""
        ha = self.s.ha()
        raw = ha.get("deviceInternetSwitchEntitiesV1") if isinstance(ha, dict) else []
        clean: list[dict] = []
        seen: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not entity_id.startswith("switch.") or entity_id in seen:
                continue
            seen.add(entity_id)
            clean.append({
                "entityId": entity_id,
                "name": str(item.get("name") or item.get("friendly_name") or entity_id),
                "controlName": str(item.get("controlName") or item.get("control_name") or entity_id),
                "state": str(item.get("state") or ""),
            })
        return clean

    def set_device_internet_button_state(self, *, blocked: bool, mixed: bool = False, busy: bool = False):
        self._device_internet_blocked = bool(blocked)
        self._device_internet_mixed = bool(mixed)
        if hasattr(self, "device_internet_button"):
            self.device_internet_button.setState(blocked, mixed=mixed, busy=busy)

    def refresh_device_internet_state(self):
        """Refresh the iPad button from the actual Home Assistant switch states."""
        entities = self.selected_device_internet_entities()
        if not entities:
            self._device_internet_state_known = False
            self.set_device_internet_button_state(blocked=False, mixed=False, busy=False)
            self.position_sleep_controls()
            return
        if getattr(self, "_device_internet_refresh_running", False) or getattr(self, "_device_internet_toggle_running", False):
            return
        self._device_internet_refresh_running = True
        wanted = {str(item.get("entityId") or "") for item in entities}
        payload = self.s.ha_payload({"domains": ["switch"]})

        def done(result):
            self._device_internet_refresh_running = False
            rows = result.get("entities") if isinstance(result, dict) else []
            by_id = {
                str(item.get("entityId") or item.get("entity_id") or ""): str(item.get("state") or "").strip().lower()
                for item in rows if isinstance(item, dict)
            }
            states = [by_id.get(entity_id, "") for entity_id in wanted]
            all_on = bool(states) and all(state == "on" for state in states)
            all_off = bool(states) and all(state == "off" for state in states)
            mixed = not (all_on or all_off)
            self._device_internet_state_known = bool(all_on or all_off)
            self.set_device_internet_button_state(blocked=all_off, mixed=mixed, busy=False)
            self.position_sleep_controls()

        def failed(error):
            self._device_internet_refresh_running = False
            trace_runtime(f"device internet state refresh failed error={error}")

        self.run_async(
            "device-internet-state",
            lambda: self.s.api.post("/api/ha/entities", payload, timeout=6.0),
            done,
            failed,
        )

    def _apply_device_internet_entities(self, entities: list[dict], *, blocked: bool, sequence: int) -> dict:
        """Set every selected network-access switch and verify each resulting state.

        The main-screen button is active when the selected tablets are OFFLINE.
        These Home Assistant network-access switches are inverse to that button:
        switch OFF = internet blocked, switch ON = internet allowed.
        """
        # Never use toggle here. The button state is deterministic: pressing it
        # from ONLINE requests switch.turn_off (blocked), and pressing it from
        # OFFLINE requests switch.turn_on (internet restored).
        action = "off" if bool(blocked) else "on"
        results: list[dict] = []
        for item in entities or []:
            if sequence != int(getattr(self, "_device_internet_sequence", 0) or 0):
                return {"stale": True, "results": results}
            entity_id = str((item or {}).get("entityId") or (item or {}).get("entity_id") or "").strip()
            if not entity_id.startswith("switch."):
                continue
            result = self._set_alexa_switch_state(entity_id, action, attempts=3)
            results.append(result)
            trace_runtime(
                f"device internet entity={entity_id} requested={action} ok={bool(result.get('ok'))} "
                f"state={result.get('state') or 'unknown'} attempts={result.get('attempts') or 0}"
            )
        return {"stale": False, "results": results}

    def toggle_device_internet(self):
        if getattr(self, "navigation_locked", False):
            self.toast.show_message(self.lock_restriction_message())
            return
        if getattr(self, "_device_internet_toggle_running", False):
            return
        entities = self.selected_device_internet_entities()
        if not entities:
            self.position_sleep_controls()
            return

        # The iPad button's ON/active state means OFFLINE/BLOCKED. Therefore a tap
        # while the selected switches are ON (internet allowed) must send them OFF.
        # If state is mixed/unknown, normalize everything to OFFLINE/BLOCKED first.
        currently_offline = bool(getattr(self, "_device_internet_blocked", False)) and not bool(getattr(self, "_device_internet_mixed", False))
        target_blocked = not currently_offline
        self._device_internet_toggle_running = True
        self._device_internet_sequence = int(getattr(self, "_device_internet_sequence", 0) or 0) + 1
        sequence = self._device_internet_sequence
        self.set_device_internet_button_state(
            blocked=bool(getattr(self, "_device_internet_blocked", False)),
            mixed=bool(getattr(self, "_device_internet_mixed", False)),
            busy=True,
        )
        if not hasattr(self, "_device_internet_apply_lock"):
            self._device_internet_apply_lock = threading.Lock()
        apply_lock = self._device_internet_apply_lock

        def worker():
            with apply_lock:
                if sequence != int(getattr(self, "_device_internet_sequence", 0) or 0):
                    return {"stale": True, "results": []}
                return self._apply_device_internet_entities(entities, blocked=target_blocked, sequence=sequence)

        def done(result):
            self._device_internet_toggle_running = False
            info = result if isinstance(result, dict) else {}
            if info.get("stale"):
                self.refresh_device_internet_state()
                return
            results = [item for item in (info.get("results") or []) if isinstance(item, dict)]
            failures = [item for item in results if not bool(item.get("ok"))]
            if failures or len(results) != len(entities):
                self.toast.show_message("Device internet change was incomplete; checking actual states", 3200)
                QTimer.singleShot(150, self.refresh_device_internet_state)
                return
            self._device_internet_state_known = True
            self.set_device_internet_button_state(blocked=target_blocked, mixed=False, busy=False)
            count = len(results)
            if target_blocked:
                self.toast.show_message(f"Internet disabled for {count} device{'s' if count != 1 else ''}")
            else:
                self.toast.show_message(f"Internet restored for {count} device{'s' if count != 1 else ''}")

        def failed(error):
            self._device_internet_toggle_running = False
            self.set_device_internet_button_state(
                blocked=bool(getattr(self, "_device_internet_blocked", False)),
                mixed=bool(getattr(self, "_device_internet_mixed", False)),
                busy=False,
            )
            self.toast.show_message(f"Device internet change failed: {error}", 3500)
            QTimer.singleShot(150, self.refresh_device_internet_state)

        self.run_async("device-internet-toggle", worker, done, failed)


    def selected_alexa_lockout_entities(self) -> list[dict]:
        """Return this panel's configured Alexa network-access controls."""
        ha = self.s.ha()
        raw = ha.get("alexaLockoutSwitchEntitiesV2") if isinstance(ha, dict) else []
        clean: list[dict] = []
        seen: set[str] = set()
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not entity_id.startswith("switch.") or entity_id in seen:
                continue
            seen.add(entity_id)
            clean.append({
                "alexaDeviceId": str(item.get("alexaDeviceId") or item.get("alexa_device_id") or ""),
                "entityId": entity_id,
                "name": str(item.get("name") or item.get("friendly_name") or entity_id),
                "controlName": str(item.get("controlName") or item.get("control_name") or entity_id),
            })
        return clean

    def _set_alexa_switch_state(self, entity_id: str, action: str, *, attempts: int = 3) -> dict:
        """Set one Alexa lockout switch and verify/retry the requested state."""
        entity_id = str(entity_id or "").strip()
        action = str(action or "").strip().lower()
        if not entity_id.startswith("switch.") or action not in {"on", "off"}:
            raise ValueError("Invalid Alexa lockout switch request")

        last_result: dict = {}
        last_state = ""
        last_error = ""
        for attempt in range(max(1, int(attempts))):
            payload = self.s.ha_payload({"entityId": entity_id, "action": action})
            try:
                result = self.s.api.post("/api/ha/audio/switch/action", payload, timeout=4.0)
                last_result = result if isinstance(result, dict) else {}
                control = last_result.get("control") if isinstance(last_result.get("control"), dict) else {}
                last_state = str(control.get("state") or "").strip().lower()
                if last_state == action:
                    return {"entityId": entity_id, "ok": True, "state": last_state, "attempts": attempt + 1}
                last_error = f"Home Assistant reported state {last_state or 'unknown'}"
            except Exception as exc:
                last_error = str(exc)
            if attempt + 1 < max(1, int(attempts)):
                time.sleep(0.75)

        return {
            "entityId": entity_id,
            "ok": False,
            "state": last_state,
            "attempts": max(1, int(attempts)),
            "error": last_error or "Could not verify requested switch state",
            "result": last_result,
        }

    def _apply_alexa_entities(self, entities: list[dict], *, locked: bool, sequence: int | None = None) -> dict:
        """Apply lock state to an explicit entity list. OFF blocks UniFi clients; ON unblocks."""
        action = "off" if bool(locked) else "on"
        results: list[dict] = []
        for item in entities or []:
            if sequence is not None and sequence != int(getattr(self, "_alexa_lockout_sequence", 0) or 0):
                return {"stale": True, "results": results}
            entity_id = str((item or {}).get("entityId") or (item or {}).get("entity_id") or "").strip()
            if not entity_id.startswith("switch."):
                continue
            result = self._set_alexa_switch_state(entity_id, action, attempts=3)
            results.append(result)
            trace_runtime(
                f"alexa lockout entity={entity_id} requested={action} ok={bool(result.get('ok'))} "
                f"state={result.get('state') or 'unknown'} attempts={result.get('attempts') or 0}"
            )
        return {"stale": False, "results": results}

    def migrate_alexa_lockout_config(self):
        """Reset legacy Alexa selections once so only explicit current-panel choices are controlled."""
        integrations = self.s.config.setdefault("integrations", {})
        ha = integrations.setdefault("homeAssistant", {})
        if not isinstance(ha, dict):
            return

        try:
            schema = int(ha.get("alexaLockoutSchemaVersion") or 0)
        except (TypeError, ValueError):
            schema = 0

        if schema >= 2:
            # The native UI always starts unlocked after a restart/update. Reconcile
            # the selected switches to that state so a previous crash cannot strand
            # an Echo in UniFi's blocked state.
            QTimer.singleShot(350, lambda: self.apply_alexa_lockout_state(False))
            return

        legacy_raw = ha.get("alexaLockoutEntities")
        legacy: list[dict] = []
        seen: set[str] = set()
        for item in legacy_raw if isinstance(legacy_raw, list) else []:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not entity_id.startswith("switch.") or entity_id in seen:
                continue
            seen.add(entity_id)
            legacy.append({"entityId": entity_id})

        # Start the corrected implementation with a clean, per-panel selection.
        # This prevents entities automatically carried forward from 16.45-16.48
        # from being controlled on screens where the user did not select them now.
        ha["alexaLockoutSwitchEntitiesV2"] = []
        ha["alexaLockoutSchemaVersion"] = 2
        ha.pop("alexaLockoutEntities", None)
        snapshot = copy.deepcopy(self.s.config)

        def worker():
            # Do not operate any legacy switch during migration. Older builds
            # could have saved arbitrary Alexa-named helper switches, so changing
            # them automatically would risk affecting devices this panel no longer
            # owns. The corrected selection starts empty and only explicit V2
            # selections are controlled from this point forward.
            saved = self.s.api.save_config(snapshot)
            return {"saved": saved}

        def done(result):
            record = result.get("saved") if isinstance(result, dict) else None
            if isinstance(record, dict):
                self.s.config = record.get("config") or self.s.config
            trace_runtime(f"alexa lockout migration complete legacy_cleared={len(legacy)}")

        self.run_async(
            "alexa-lockout-migrate",
            worker,
            done,
            lambda error: trace_runtime(f"alexa lockout migration failed error={error}"),
        )

    def apply_alexa_lockout_state(self, locked: bool):
        """Block/unblock only the Alexa switches explicitly selected on this panel.

        Home Assistant's UniFi client-access switch is ON when network access is
        allowed and OFF when the client is blocked. Therefore screen LOCKED sends
        turn_off, and screen UNLOCKED sends turn_on.
        """
        entities = self.selected_alexa_lockout_entities()
        if not entities:
            return

        self._alexa_lockout_sequence = int(getattr(self, "_alexa_lockout_sequence", 0) or 0) + 1
        sequence = self._alexa_lockout_sequence
        if not hasattr(self, "_alexa_lockout_apply_lock"):
            self._alexa_lockout_apply_lock = threading.Lock()
        apply_lock = self._alexa_lockout_apply_lock

        def worker():
            with apply_lock:
                if sequence != int(getattr(self, "_alexa_lockout_sequence", 0) or 0):
                    return {"stale": True, "results": []}
                return self._apply_alexa_entities(entities, locked=bool(locked), sequence=sequence)

        self.run_async(
            "alexa-lockout",
            worker,
            lambda _result: None,
            lambda error: trace_runtime(f"alexa lockout apply failed error={error}"),
        )

    def report_screen_lock_state(self):
        """Mirror the existing native lock state to the local backend."""
        locked = bool(getattr(self, "navigation_locked", False))
        security_locked = bool(locked and getattr(self, "security_lock_active", False))
        payload = {
            "locked": locked,
            "securityLocked": security_locked,
            # Requests run off the UI thread. The sequence lets the backend ignore
            # an older request if rapid lock/unlock taps complete out of order.
            "sequence": time.monotonic_ns(),
        }
        self.run_async(
            "screen-lock-state",
            lambda p=payload: self.s.api.post("/api/screen/lock-status", p, timeout=1.0),
            lambda _result: None,
            lambda _err: None,
        )

    def set_navigation_locked(self, locked: bool, *, secure: bool | None = None, show_toast: bool = False):
        was_locked = bool(getattr(self, "navigation_locked", False))
        self.navigation_locked = bool(locked)
        if self.navigation_locked:
            if secure is None:
                secure = bool(getattr(self, "security_lock_active", False)) if was_locked else False
            self.security_lock_active = bool(secure)
        else:
            self.security_lock_active = False
        self.header.set_locked(self.navigation_locked, self.security_lock_active)
        self.sleep_button.setEnabled(not self.navigation_locked)
        self.sync_button.setEnabled(not self.navigation_locked)
        self.device_internet_button.setEnabled(not self.navigation_locked)
        self.intimacy_button.setEnabled(not self.navigation_locked)

        thermostat_page = self.pages.get("Thermostat")
        if isinstance(thermostat_page, ThermostatScreen):
            thermostat_page.set_screen_locked(self.navigation_locked, self.security_lock_active)

        # Report only after the existing lock state and permitted controls have
        # been applied locally. Failure here never changes or blocks the UI lock.
        self.report_screen_lock_state()
        # Apply this panel's configured Alexa network/device lockouts in parallel.
        # These are ordinary HA switches, so Alexa discovery is never touched.
        self.apply_alexa_lockout_state(self.navigation_locked)

        if self.navigation_locked:
            if self.current_name != "Thermostat":
                self.set_page("Thermostat", force=True)
            else:
                self.header.set_page("Thermostat")

            # Locking cancels an armed Sync session so permitted temperature
            # changes cannot be copied to peer thermostats behind the lock.
            self._sync_active_until = 0.0
            self._sync_pending_changes = {}
            self.peer_sync_timer.stop()
            self.sync_button.setActive(False, 0)
            if not was_locked:
                self.run_async(
                    "screen-lock-sync-off",
                    lambda: self.s.api.post(
                        "/api/sync/arm",
                        {"action": "off", "source": "screen-lock"},
                        timeout=4.0,
                    ),
                    lambda result: self.apply_sync_status(result, authoritative=True),
                    lambda _err: None,
                )
            if show_toast:
                self.toast.show_message(self.lock_restriction_message())
        else:
            if isinstance(thermostat_page, ThermostatScreen):
                thermostat_page.sync(self.s.config, self.s.thermostat)
            if show_toast:
                self.toast.show_message("Screen controls unlocked")

    def toggle_navigation_lock(self):
        if not getattr(self, "navigation_locked", False):
            self.set_navigation_locked(True, secure=False, show_toast=True)
            return
        secure = bool(getattr(self, "security_lock_active", False))
        code = self.settings_code() if secure else self.alarm_disarm_code()
        if code:
            title = "Security Lock" if secure else "Screen Locked"
            prompt = "Enter Settings Code" if secure else "Enter Alarm Disarm Code"
            entered = CodeKeypadDialog.get_code(self, title, prompt, code)
            if entered is None:
                return
        self.set_navigation_locked(False, show_toast=True)

    def activate_security_lock(self):
        self.set_navigation_locked(True, secure=True, show_toast=False)
        self.header.flash_security_lock_confirmation()
        self.toast.show_message("Security lock active: alarm control only")

    def run_async(self, name: str, worker: Callable[[], Any], on_success: Callable[[Any], None] | None = None, on_error: Callable[[str], None] | None = None):
        job_id = f"{name}-{time.monotonic_ns()}"
        self._main_async_jobs[job_id] = (on_success, on_error)

        def target():
            try:
                result = worker()
                self.mainAsyncCompleted.emit({"id": job_id, "result": result, "error": None})
            except Exception as exc:
                self.mainAsyncCompleted.emit({"id": job_id, "result": None, "error": str(exc)})

        threading.Thread(target=target, name=f"main-{name}", daemon=True).start()

    def _handle_main_async_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        callbacks = self._main_async_jobs.pop(str(data.get("id") or ""), None)
        if not callbacks:
            return
        if getattr(self, "_thermal_protection_active", False):
            self.position_sleep_controls()
            return
        on_success, on_error = callbacks
        if data.get("error"):
            if on_error:
                on_error(str(data.get("error")))
            return
        if on_success:
            on_success(data.get("result"))

    def poll_visible_page_now(self, name: str | None = None):
        try:
            if name is not None and name != self.current_name:
                return
            if getattr(self, "_poll_busy", False):
                return
            if QApplication.activeModalWidget() is not None:
                return
            if self.current_name not in {"Lights", "Audio"}:
                return
            page = self.pages.get(self.current_name)
            if page is None:
                return
            self._poll_busy = True
            self._last_poll_by_page[self.current_name] = time.monotonic()
            page.poll()
        except Exception:
            pass
        finally:
            self._poll_busy = False

    def sync_visible_page(self, name: str | None = None):
        try:
            if name is not None and name != self.current_name:
                return
            page = self.pages.get(self.current_name)
            if page is not None:
                page.sync(self.s.config, self.s.thermostat)
        except Exception:
            pass

    def configured_alarm_entity_id(self) -> str:
        try:
            ha = self.s.ha()
            entity = ha.get("alarmEntity") or {}
            if not isinstance(entity, dict):
                return ""
            return str(entity.get("entityId") or entity.get("entity_id") or "").strip()
        except Exception:
            return ""

    def refresh_alarm_state(self):
        """Background poll for Home Assistant-initiated Alarmo state changes."""
        if getattr(self, "_thermal_protection_active", False):
            return
        if self.modal_interaction_active():
            return
        if getattr(self, "_alarm_refresh_running", False):
            return
        if not self.configured_alarm_entity_id():
            return
        self._alarm_refresh_running = True

        def worker():
            try:
                fresh = self.s.fetch_alarm_state()
                self.alarmRefreshCompleted.emit({"alarm": fresh, "error": None})
            except Exception as exc:
                self.alarmRefreshCompleted.emit({"alarm": None, "error": str(exc)})

        threading.Thread(target=worker, name="alarm-state-refresh", daemon=True).start()

    def _handle_alarm_refresh_completed(self, info: object):
        self._alarm_refresh_running = False
        data = info if isinstance(info, dict) else {}
        page = self.pages.get("Thermostat")
        fresh = data.get("alarm")
        if not isinstance(fresh, dict) or not fresh:
            if isinstance(page, ThermostatScreen):
                page.restore_alarm_card_from_cache()
            return
        self.s.apply_alarm_state(fresh)
        if self.background_interaction_busy():
            self._interaction_runtime_sync_pending = True
            return
        if isinstance(page, ThermostatScreen):
            page.apply_alarm_state_refresh(fresh)
        if self.current_name == "Thermostat":
            self.sync_visible_page("Thermostat")

    def refresh_assistant_status(self):
        """Poll the local backend only; no Home Assistant work occurs in this timer."""
        if getattr(self, "_thermal_protection_active", False):
            return
        if self.modal_interaction_active():
            return
        if getattr(self, "_assistant_status_running", False):
            return
        self._assistant_status_running = True
        api = self.api

        def worker():
            try:
                data = api.get("/api/assistant/status")
                self.assistantStatusCompleted.emit({"data": data, "error": None})
            except Exception as exc:
                self.assistantStatusCompleted.emit({"data": None, "error": str(exc)})

        self._submit_status_worker(worker)

    def _handle_assistant_status_completed(self, info: object):
        self._assistant_status_running = False
        if getattr(self, "_thermal_protection_active", False):
            return
        data = info if isinstance(info, dict) else {}
        if data.get("error"):
            self._update_assistant_poll_interval(False, error=True)
            return
        result = data.get("data") if isinstance(data.get("data"), dict) else {}
        assistant = result.get("assistant") if isinstance(result.get("assistant"), dict) else {}
        active = bool(assistant.get("active")) and str(assistant.get("stage") or "idle").lower() != "idle"
        self._update_assistant_poll_interval(active)
        if active:
            # Assistant activity may wake an automatically sleeping panel. A
            # manual Sleep-button lock remains touch-only and ignores this path.
            if getattr(self, "_display_sleeping", False):
                self.wake_display_screen(source="assistant")
            self._last_user_activity_at = time.monotonic()
        if self.background_interaction_busy():
            return
        changed = False
        if hasattr(self, "assistant_overlay"):
            changed = bool(self.assistant_overlay.set_status(assistant))
        if changed:
            self.position_sleep_controls()

    @staticmethod
    def thermal_protection_from_payload(payload: object) -> dict:
        data = payload if isinstance(payload, dict) else {}
        for key in ("thermalProtection", "thermal_protection"):
            value = data.get(key)
            if isinstance(value, dict):
                return value
        thermostat = data.get("thermostat") if isinstance(data.get("thermostat"), dict) else {}
        for key in ("thermalProtection", "thermal_protection"):
            value = thermostat.get(key)
            if isinstance(value, dict):
                return value
        return {}

    def thermal_timer_candidates(self) -> list[tuple[str, QTimer]]:
        # QTimer objects are QObject children even when a page stores them inside
        # a list/dictionary or a nested widget owns them. Walking the Qt object
        # tree catches every animation, debounce, polling, and one-shot timer
        # without relying on attribute names. The thermal-only watch timer is
        # filtered by the caller because it is the one lightweight loop required
        # to detect safe recovery.
        candidates: list[tuple[str, QTimer]] = []
        seen: set[int] = set()
        try:
            timers = self.findChildren(QTimer)
        except Exception:
            timers = []
        for timer in timers:
            marker = id(timer)
            if marker in seen:
                continue
            seen.add(marker)
            candidates.append((f"qt:{marker}", timer))
        return candidates

    def suspend_nonessential_thermal_activity(self):
        saved: dict[str, tuple[QTimer, int, bool, bool]] = {}
        for key, timer in self.thermal_timer_candidates():
            if timer is self.thermal_watch_timer:
                continue
            try:
                saved[key] = (timer, max(1, int(timer.interval() or 1)), bool(timer.isActive()), bool(timer.isSingleShot()))
                timer.stop()
            except Exception:
                continue
        self._thermal_suspended_timers = saved
        try:
            self._sync_pending_changes.clear()
            self._sync_active_until = 0.0
            self._sync_optimistic_active = False
        except Exception:
            pass

    def resume_nonessential_thermal_activity(self):
        saved = getattr(self, "_thermal_suspended_timers", {})
        self._thermal_suspended_timers = {}
        for _key, item in saved.items():
            try:
                timer, interval, was_active, single_shot = item
                # Do not replay stale one-shot UI actions that were pending when
                # the emergency screen took over. Repeating background timers
                # resume at their original cadence.
                if was_active and not single_shot:
                    timer.start(max(1, int(interval)))
            except Exception:
                continue

    def enter_thermal_protection_screen(self):
        if getattr(self, "_thermal_protection_active", False):
            return
        self._thermal_protection_active = True
        trace_runtime("Thermal protection screen entered; nonessential UI activity suspended")

        modal = QApplication.activeModalWidget()
        if modal is not None and modal is not self:
            try:
                modal.close()
            except Exception:
                pass

        if getattr(self, "_display_sleeping", False):
            self._display_sleeping = False
            self._manual_sleep_touch_only = False
            self._display_wake_block_until = 0.0
            self._run_display_power_command(SCREEN_SLEEP_ON_COMMAND)
        try:
            self.sleep_overlay.hide()
            self.assistant_overlay.set_status({"stage": "idle", "active": False})
            self.toast.hide()
        except Exception:
            pass
        self.suspend_nonessential_thermal_activity()
        self.position_sleep_controls()

    def leave_thermal_protection_screen(self):
        if not getattr(self, "_thermal_protection_active", False):
            return
        self._thermal_protection_active = False
        trace_runtime("Thermal protection screen cleared; normal UI activity restored")
        try:
            self.thermal_overlay.hide()
        except Exception:
            pass
        self.resume_nonessential_thermal_activity()
        self._update_assistant_poll_interval(False)
        self._update_screen_motion_poll_interval()
        self._last_user_activity_at = time.monotonic()
        self.position_sleep_controls()
        self.sync_runtime_only()
        QTimer.singleShot(0, self.refresh_status)
        QTimer.singleShot(0, self.refresh_alarm_state)
        QTimer.singleShot(0, self.refresh_assistant_status)

    def apply_thermal_protection_state(self, payload: object):
        thermal = self.thermal_protection_from_payload(payload)
        active = bool(thermal.get("active"))
        if active:
            if getattr(self, "_thermal_protection_active", False):
                self.position_sleep_controls()
            else:
                self.enter_thermal_protection_screen()
        else:
            self.leave_thermal_protection_screen()

    def refresh_thermal_status(self):
        """Poll only the Pi thermal latch; this remains active during emergency mode."""
        if getattr(self, "_thermal_status_running", False):
            return
        self._thermal_status_running = True
        api = self.s.api

        def worker():
            try:
                data = api.thermal_status()
                self.thermalStatusCompleted.emit({"data": data, "error": None})
            except Exception as exc:
                self.thermalStatusCompleted.emit({"data": None, "error": str(exc)})

        self._submit_status_worker(worker)

    def _handle_thermal_status_completed(self, info: object):
        self._thermal_status_running = False
        data = info if isinstance(info, dict) else {}
        if data.get("error"):
            self._update_thermal_poll_interval(error=True)
            return
        payload = data.get("data")
        if isinstance(payload, dict):
            self._update_thermal_poll_interval(payload)
            self.apply_thermal_protection_state(payload)

    def refresh_status(self):
        if getattr(self, "_status_refresh_running", False):
            return
        if self.modal_interaction_active():
            return
        if time.monotonic() < getattr(self.s, "status_refresh_paused_until", 0.0):
            return
        self._status_refresh_running = True
        api = self.s.api
        refresh_epoch = int(getattr(self.s, "status_refresh_epoch", 0) or 0)

        def worker():
            try:
                data = api.thermostat_status()
                self.statusRefreshCompleted.emit({"data": data, "error": None, "epoch": refresh_epoch})
            except Exception as exc:
                self.statusRefreshCompleted.emit({"data": None, "error": str(exc), "epoch": refresh_epoch})

        self._submit_status_worker(worker)

    def _handle_status_refresh_completed(self, info: object):
        self._status_refresh_running = False
        data = info if isinstance(info, dict) else {}
        if data.get("error"):
            return
        if int(data.get("epoch", -1)) != int(getattr(self.s, "status_refresh_epoch", 0) or 0):
            return
        if time.monotonic() < getattr(self.s, "status_refresh_paused_until", 0.0):
            return
        status = data.get("data")
        if isinstance(status, dict):
            self.s.ingest_thermostat(status)
            self.apply_thermal_protection_state(status)
            if not getattr(self, "_thermal_protection_active", False):
                if self.background_interaction_busy():
                    self._interaction_runtime_sync_pending = True
                else:
                    self.sync_runtime_only()

    def sync_runtime_only(self):
        t = self.s.thermostat or {}
        self.header.update_values(t.get("currentTemp"), t.get("targetTemp"))
        self.sync_visible_page()
        self.update_intimacy_button_state()
        self.update_sync_button_state()

    def reload_all(self):
        """Reload backend state without blocking the Qt event loop."""
        if self._reload_all_running:
            self._reload_all_pending = True
            return
        self._reload_all_running = True
        self._reload_all_pending = False
        api = self.s.api

        def worker():
            result = {"config": None, "thermostat": None, "systemInfo": None, "errors": []}
            try:
                result["config"] = api.get_config_record()
            except Exception as exc:
                result["errors"].append(f"config: {exc}")
            try:
                result["thermostat"] = api.thermostat_status()
            except Exception as exc:
                result["errors"].append(f"thermostat: {exc}")
            try:
                result["systemInfo"] = api.get("/api/system/info")
            except Exception as exc:
                result["errors"].append(f"system: {exc}")
            self.reloadAllCompleted.emit(result)

        threading.Thread(target=worker, name="full-state-reload", daemon=True).start()

    def _handle_reload_all_completed(self, info: object):
        data = info if isinstance(info, dict) else {}
        config_record = data.get("config")
        if isinstance(config_record, dict):
            self.s.config = config_record.get("config") or self.s.config
        thermostat = data.get("thermostat")
        if isinstance(thermostat, dict):
            self.s.ingest_thermostat(thermostat)
        self.s.thermostat_schedules()
        system_info = data.get("systemInfo")
        if isinstance(system_info, dict):
            self.s.system_info = system_info
        self.sync_runtime_only()
        self.update_sync_button_state()
        self.position_sleep_controls()
        QTimer.singleShot(0, self.refresh_device_internet_state)
        self._update_screen_motion_poll_interval()
        QTimer.singleShot(0, self.refresh_screen_motion_status)
        errors = [str(item) for item in (data.get("errors") or []) if str(item)]
        if errors and config_record is None and thermostat is None:
            self.toast.show_message("Reload failed: " + "; ".join(errors[:2]))
        self._reload_all_running = False
        if self._reload_all_pending:
            self._reload_all_pending = False
            QTimer.singleShot(0, self.reload_all)

    def _mark_ui_heartbeat(self):
        self._ui_heartbeat_at = time.monotonic()

    def _ui_stall_watchdog(self):
        """Record all Python thread stacks when the Qt loop stops responding."""
        while True:
            time.sleep(1.0)
            now = time.monotonic()
            stalled_for = now - float(getattr(self, "_ui_heartbeat_at", now) or now)
            if stalled_for < 5.0 or now - float(getattr(self, "_ui_stall_last_dump_at", 0.0) or 0.0) < 20.0:
                continue
            self._ui_stall_last_dump_at = now
            try:
                path = runtime_log_path("native-ui-stall.log")
                with open(path, "a", buffering=1) as handle:
                    handle.write(f"\n===== UI stall {datetime.now().isoformat(timespec='seconds')} duration={stalled_for:.1f}s =====\n")
                    faulthandler.dump_traceback(file=handle, all_threads=True)
                trace_runtime(f"UI stall trace written to {path} after {stalled_for:.1f}s")
            except Exception as exc:
                trace_runtime(f"UI stall trace failed: {exc}")

    def sync_all(self):
        t = self.s.thermostat or {}
        self.header.update_values(t.get("currentTemp"), t.get("targetTemp"))
        for p in self.pages.values():
            p.sync(self.s.config, self.s.thermostat)

    def poll(self):
        try:
            if getattr(self, "_thermal_protection_active", False):
                return
            if getattr(self, "_poll_busy", False):
                return
            if time.monotonic() - getattr(self, "_last_page_change_at", 0) < 1.2:
                return
            if QApplication.activeModalWidget() is not None:
                return
            now = time.monotonic()
            last = getattr(self, "_last_poll_by_page", {}).get(self.current_name, 0)
            min_interval = 3.0 if self.current_name == "Audio" else 10.0
            if now - last < min_interval:
                return
            self._poll_busy = True
            self._last_poll_by_page[self.current_name] = now
            self.pages[self.current_name].poll()
        except Exception:
            pass
        finally:
            self._poll_busy = False

    def domains_for(self, group: str) -> list[str]:
        if group == "room":
            return ["switch", "input_boolean", "button", "fan", "cover", "light", "lock"]
        if group == "light":
            return ["light"]
        if group == "cover":
            return ["cover"]
        if group == "audio-number":
            return ["number"]
        if group == "audio-toggle":
            return ["switch", "input_boolean"]
        if group in {"audio-media-player", "audio-primary-media-player"}:
            return ["media_player"]
        return []

    def cached_entities_for(self, group: str) -> list[dict]:
        ha = self.s.ha()
        if group == "room":
            return ha.get("roomAvailableEntities") or []
        if group == "light":
            return ha.get("lightAvailableEntities") or []
        if group == "cover":
            return ha.get("coverEntities") or []
        if group == "audio-number":
            return (ha.get("audioAvailableEntities") or {}).get("numbers") or []
        if group == "audio-toggle":
            return (ha.get("audioAvailableEntities") or {}).get("switches") or []
        if group in {"audio-media-player", "audio-primary-media-player"}:
            return ha.get("mediaPlayerEntities") or (ha.get("audioAvailableEntities") or {}).get("mediaPlayers") or []
        return []

    def assign_entity(self, group: str, obj: dict, kind: str):
        if getattr(self, "_entity_picker_loading", False):
            return
        cached = copy.deepcopy(self.cached_entities_for(group))
        payload = self.s.ha_payload({"domains": self.domains_for(group)})
        self._entity_picker_loading = True

        def open_with(entities):
            self._entity_picker_loading = False
            self._open_entity_assignment_picker(group, obj, kind, entities or cached)

        def failed(_error):
            self._entity_picker_loading = False
            self._open_entity_assignment_picker(group, obj, kind, cached)

        self.run_async(
            "entity-picker-load",
            lambda: self.s.api.post("/api/ha/entities", payload),
            lambda data: open_with((data or {}).get("entities") or [] if isinstance(data, dict) else []),
            failed,
        )

    def _open_entity_assignment_picker(self, group: str, obj: dict, kind: str, entities: list[dict]):
        if not entities:
            self.toast.show_message("No Home Assistant entities available")
            return
        display_kind = str(kind or "entry").replace("_", " ").replace("subwoofer", "sub").title()
        dlg = EntityPickerDialog(f"Assign {display_kind} Entity", entities, self)

        if group == "audio-primary-media-player":
            def apply_primary_media_player(ent):
                entity_id = str(ent.get("entityId") or ent.get("entity_id") or "").strip()
                if not entity_id:
                    self.toast.show_message("No media player selected")
                    return
                try:
                    ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                    ha["selectedMediaPlayerId"] = entity_id
                    ha["mediaPlayerEntity"] = {
                        "entityId": entity_id,
                        "name": ent.get("name") or entity_id,
                        "domain": "media_player",
                    }
                    audio_page = self.pages.get("Audio")
                    if audio_page is not None:
                        audio_page.player_state = {}
                    snapshot = copy.deepcopy(self.s.config)
                    self.sync_runtime_only()

                    def saved(_result):
                        self.toast.show_message(f"Audio player: {ent.get('name') or entity_id}")
                        if audio_page is not None:
                            audio_page._audio_detected_player_id = ""
                            QTimer.singleShot(
                                120,
                                lambda page=audio_page, eid=entity_id, name=(ent.get("name") or entity_id):
                                    page.auto_detect_audio_number_controls(eid, name, notify=True),
                            )

                    self.run_async(
                        "entity-assign-primary-save",
                        lambda: self.s.api.save_config(snapshot),
                        saved,
                        lambda err: self.toast.show_message(f"Save failed: {err}"),
                    )
                except Exception as exc:
                    self.toast.show_message(f"Save failed: {exc}")

            dlg.selected.connect(apply_primary_media_player)
            dlg.exec_()
            return

        if group in {"audio-number", "audio-toggle", "audio-media-player"}:
            target_kind = str((obj or {}).get("audioControlKind") or kind or "").strip().lower()

            def apply_audio(ent):
                entity_id = ent.get("entityId") or ent.get("entity_id") or ""
                domain = ent.get("domain") or (entity_id.split(".", 1)[0] if "." in entity_id else "")
                record = {
                    "entityId": entity_id,
                    "name": ent.get("name") or entity_id,
                    "domain": domain,
                }
                if group == "audio-number":
                    record["kind"] = target_kind
                    for key in ("min", "max", "step", "unit", "mode", "value", "state"):
                        if key in ent:
                            record[key] = ent.get(key)
                elif group == "audio-media-player":
                    record["kind"] = target_kind
                    for key in ("state", "supportedFeatures", "volumeLevel", "source", "sourceList", "mediaTitle", "mediaArtist", "mediaAlbum"):
                        if key in ent:
                            record[key] = ent.get(key)
                else:
                    for key in ("state", "deviceClass", "icon"):
                        if key in ent:
                            record[key] = ent.get(key)
                try:
                    ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                    controls = ha.setdefault("audioControlEntities", {})
                    controls[target_kind] = record
                    snapshot = copy.deepcopy(self.s.config)
                    self.sync_runtime_only()
                    self.run_async(
                        "entity-assign-audio-save",
                        lambda: self.s.api.save_config(snapshot),
                        lambda _result: self.toast.show_message(f"Assigned {record.get('name') or entity_id}"),
                        lambda err: self.toast.show_message(f"Save failed: {err}"),
                    )
                except Exception as exc:
                    self.toast.show_message(f"Save failed: {exc}")

            dlg.selected.connect(apply_audio)
            dlg.exec_()
            return

        def apply(ent):
            obj["haEntityId"] = ent.get("entityId") or ent.get("entity_id") or ""
            obj["haName"] = ent.get("name") or obj["haEntityId"]
            obj["name"] = ent.get("name") or obj.get("name") or obj["haEntityId"]
            if ent.get("domain"):
                obj["domain"] = ent.get("domain")
            obj["state"] = ent.get("state") or obj.get("state") or "unknown"
            obj["on"] = room_control_active(obj.get("state"), obj.get("domain"))
            for meta_key in ("deviceClass", "icon", "supportedFeatures", "currentPosition"):
                if meta_key in ent:
                    obj[meta_key] = ent.get(meta_key)
            try:
                snapshot = copy.deepcopy(self.s.config)
                self.sync_runtime_only()
                self.run_async(
                    "entity-assign-save",
                    lambda: self.s.api.save_config(snapshot),
                    lambda _result: self.toast.show_message(f"Assigned {obj.get('haName') or obj.get('name')}"),
                    lambda err: self.toast.show_message(f"Save failed: {err}"),
                )
            except Exception as exc:
                self.toast.show_message(f"Save failed: {exc}")
        dlg.selected.connect(apply)
        dlg.exec_()

    def show_settings(self):
        if getattr(self, "navigation_locked", False):
            self.toast.show_message(self.lock_restriction_message())
            return
        now = time.monotonic()
        if getattr(self, "_settings_dialog_open", False):
            return
        if now < getattr(self, "_settings_reopen_block_until", 0):
            return
        self._settings_dialog_open = True
        try:
            settings_code = self.settings_code()
            if settings_code:
                entered = CodeKeypadDialog.get_code(self, "Settings Locked", "Enter Settings Code", settings_code)
                if entered is None:
                    self._settings_reopen_block_until = time.monotonic() + 1.5
                    return
            if self.current_name == "Thermostat":
                started = time.monotonic()
                trace_runtime("settings unlock accepted; constructing thermostat settings dialog")
                dlg = SettingsDialog(self.s, self)
                trace_runtime(f"thermostat settings dialog constructed in {time.monotonic() - started:.2f}s")
            elif self.current_name == "Audio":
                dlg = AudioSettingsDialog(self.s, self)
                dlg.saved.connect(self.reload_all)
            elif self.current_name in {"Blinds", "Lights", "Room"}:
                dlg = RoomManagerSettingsDialog(self.s, self.current_name, self)
                # Room/Lights/Blinds settings already save their edits directly and
                # the full panel reload below runs after the dialog closes. Do not
                # reload the whole app from inside the still-open modal: that blocks
                # the touchscreen UI, can expose a black frame, and can snap the
                # panel back underneath the settings page while code checkboxes are
                # being edited.
            else:
                dlg = SimplePageSettingsDialog(self.current_name, self)
            dlg.exec_()
            self.reload_all()
        finally:
            self._settings_dialog_open = False
            QTimer.singleShot(0, self.flush_deferred_interaction_sync)
            # Touchscreens can emit a second tap/release after the modal closes.
            # Block immediate re-entry so the settings keypad does not pop back up.
            self._settings_reopen_block_until = time.monotonic() + 2.0

    def show_info(self):
        if getattr(self, "navigation_locked", False):
            self.toast.show_message(self.lock_restriction_message())
            return
        now = time.monotonic()
        if now < getattr(self, "_ignore_info_until", 0):
            return
        if getattr(self, "_info_dialog_open", False):
            return
        if now < getattr(self, "_info_reopen_block_until", 0):
            return
        self._info_dialog_open = True

        settings_code = self.settings_code()
        if settings_code:
            entered = CodeKeypadDialog.get_code(self, "Info Locked", "Enter Settings Code", settings_code)
            if entered is None:
                self._info_dialog_open = False
                self._info_reopen_block_until = time.monotonic() + 1.5
                return

        self.toast.show_message("Loading panel information…", 1800)
        self.run_async(
            "info-load",
            lambda: self.s.api.get("/api/system/info"),
            self._open_info_dialog,
            self._info_load_failed,
        )

    def _info_load_failed(self, error: str):
        self._info_dialog_open = False
        self._info_reopen_block_until = time.monotonic() + 1.5
        self.toast.show_message(f"Info failed: {error}")

    def _open_info_dialog(self, info: object):
        info = info if isinstance(info, dict) else {}
        try:
            dlg = QDialog(self)
            dlg.setModal(True)
            dlg.setWindowTitle("Thermostat Info")
            dlg.setFixedSize(940, 640)
            dlg.setStyleSheet("""
                QDialog {
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                        stop:0 #071222,
                        stop:0.52 #101d35,
                        stop:1 #0b1020);
                    color:#f7fbff;
                }
                QLabel {
                    color:#f7fbff;
                    background:transparent;
                    border:0;
                    font-family:Arial;
                }
            """)
            root = QVBoxLayout(dlg)
            root.setContentsMargins(24, 20, 24, 18)
            root.setSpacing(12)

            title = QLabel("<span style='color:#46e8ff; letter-spacing:4px; font-size:11px; font-weight:900'>THERMOSTAT</span><br><span style='font-size:31px; font-weight:1000; color:#ffffff'>Panel Information</span>")
            title.setTextFormat(Qt.RichText)
            root.addWidget(title)

            content = QHBoxLayout()
            content.setSpacing(14)

            grid_panel = GlassPanel(radius=22)
            grid_panel.setMinimumWidth(350)
            grid = QGridLayout(grid_panel)
            grid.setContentsMargins(18, 16, 18, 16)
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(10)
            rows = [
                ("Name", info.get("thermostatName") or info.get("name") or "Smart Thermostat"),
                ("IP / Port", info.get("address") or "--"),
                ("Version", info.get("version") or "--"),
                ("Uptime", info.get("uptime") or "--"),
                ("Thermal", info.get("thermal") or "--"),
            ]
            for row, (label, value) in enumerate(rows):
                l = QLabel(str(label).upper())
                l.setFont(font(8, QFont.Black, 20))
                l.setStyleSheet("color:#96a7c2;")
                v = QLabel(str(value))
                v.setFont(font(13, QFont.Black))
                v.setStyleSheet("color:#ffffff;")
                v.setWordWrap(True)
                grid.addWidget(l, row, 0)
                grid.addWidget(v, row, 1)
            content.addWidget(grid_panel, 4)

            motion_panel = GlassPanel(radius=22)
            motion_panel.setMinimumWidth(500)
            motion_layout = QVBoxLayout(motion_panel)
            motion_layout.setContentsMargins(18, 14, 18, 14)
            motion_layout.setSpacing(8)

            motion_header = QHBoxLayout()
            motion_icon = MotionStatusIndicator()
            motion_header.addWidget(motion_icon)
            motion_text = QVBoxLayout()
            motion_title = QLabel("MOTION SENSOR")
            motion_title.setFont(font(10, QFont.Black, 16))
            motion_title.setStyleSheet("color:#96a7c2; letter-spacing:2px;")
            motion_state = QLabel("Checking...")
            motion_state.setFont(font(20, QFont.Black))
            motion_state.setStyleSheet("color:#ffffff;")
            motion_detail = QLabel("GPIO27 REL")
            motion_detail.setFont(font(9, QFont.Bold))
            motion_detail.setStyleSheet("color:#aebbd0;")
            motion_text.addWidget(motion_title)
            motion_text.addWidget(motion_state)
            motion_text.addWidget(motion_detail)
            motion_text.addStretch(1)
            motion_header.addLayout(motion_text, 1)
            motion_layout.addLayout(motion_header)

            poll_row = QHBoxLayout()
            poll_label = QLabel("CHECK EVERY")
            poll_label.setFont(font(8, QFont.Black, 16))
            poll_label.setStyleSheet("color:#96a7c2;")
            poll_row.addWidget(poll_label)
            poll_row.addStretch(1)
            poll_buttons = {}
            for seconds in (1, 3, 6):
                button = RoundButton(f"{seconds}s", active=False, min_h=36)
                button.setFixedWidth(64)
                poll_buttons[seconds] = button
                poll_row.addWidget(button)
            motion_layout.addLayout(poll_row)

            sensitivity_row = QHBoxLayout()
            sensitivity_label = QLabel("Hardware sensitivity")
            sensitivity_label.setFont(font(11, QFont.Black))
            sensitivity_value = QLabel("Maximum")
            sensitivity_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sensitivity_value.setFont(font(10, QFont.Black))
            sensitivity_value.setStyleSheet("color:#49e6ff;")
            sensitivity_row.addWidget(sensitivity_label)
            sensitivity_row.addStretch(1)
            sensitivity_row.addWidget(sensitivity_value)
            motion_layout.addLayout(sensitivity_row)

            sensitivity_buttons = {}
            sensitivity_choices = QHBoxLayout()
            sensitivity_choices.setSpacing(10)
            for level, label in ((0, "Maximum"), (25, "Reduced")):
                button = RoundButton(label, active=False, min_h=46)
                button.setMinimumWidth(195)
                sensitivity_buttons[level] = button
                sensitivity_choices.addWidget(button, 1)
            motion_layout.addLayout(sensitivity_choices)

            ontime_row = QHBoxLayout()
            ontime_label = QLabel("Sensor hold time")
            ontime_label.setFont(font(11, QFont.Black))
            ontime_value = QLabel("2 seconds")
            ontime_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            ontime_value.setFont(font(10, QFont.Black))
            ontime_value.setStyleSheet("color:#49e6ff;")
            ontime_row.addWidget(ontime_label)
            ontime_row.addStretch(1)
            ontime_row.addWidget(ontime_value)
            motion_layout.addLayout(ontime_row)

            ontime_buttons = {}
            ontime_choices = QHBoxLayout()
            ontime_choices.setSpacing(10)
            for seconds, label in ((2, "2 seconds"), (600, "10 minutes")):
                button = RoundButton(label, active=False, min_h=46)
                button.setMinimumWidth(195)
                ontime_buttons[seconds] = button
                ontime_choices.addWidget(button, 1)
            motion_layout.addLayout(ontime_choices)

            motion_note = QLabel("The current board exposes two electrical settings for each control, so these are intentionally two-position touch controls rather than continuous sliders. Intermediate sensitivity or hardware hold-time levels require a filtered analog output or DAC hardware change.")
            motion_note.setWordWrap(True)
            motion_note.setFont(font(8, QFont.DemiBold))
            motion_note.setStyleSheet("color:#8fa0b9;")
            motion_layout.addWidget(motion_note)
            content.addWidget(motion_panel, 6)
            root.addLayout(content, 1)

            button_grid = QGridLayout()
            button_grid.setHorizontalSpacing(10)
            button_grid.setVerticalSpacing(10)
            fetch = RoundButton("Fetch Update", active=True, min_h=46)
            reboot = RoundButton("Restart", kind="purple", min_h=46)
            backup = RoundButton("Backup Config", active=False, min_h=46)
            close = RoundButton("Close", active=False, min_h=46)

            for b in [fetch, reboot, backup, close]:
                b.setMinimumWidth(190)
            button_grid.addWidget(fetch, 0, 0)
            button_grid.addWidget(reboot, 0, 1)
            button_grid.addWidget(backup, 0, 2)
            button_grid.addWidget(close, 0, 3)
            root.addLayout(button_grid)

            motion_timer = QTimer(dlg)
            motion_timer.setSingleShot(False)
            motion_write_watchdog = QTimer(dlg)
            motion_write_watchdog.setSingleShot(True)
            motion_write_watchdog.setInterval(4000)
            motion_update_guard = {"active": False}
            latest_motion = {"pollSeconds": 3, "sensitivityLevel": 0, "onTimeSeconds": 2}
            motion_io = {
                "readRunning": False,
                "writeRunning": False,
                "closed": False,
                "generation": 0,
                "pendingChanges": {},
                "activePayload": {},
            }

            def apply_motion_config_controls():
                poll_seconds = int(latest_motion.get("pollSeconds") or 3)
                sensitivity_level = int(latest_motion.get("sensitivityLevel") or 0)
                on_time_seconds = int(latest_motion.get("onTimeSeconds") or 2)

                motion_update_guard["active"] = True
                try:
                    for level, button in sensitivity_buttons.items():
                        button.setActive(level == sensitivity_level)
                    for seconds, button in ontime_buttons.items():
                        button.setActive(seconds == on_time_seconds)
                    for seconds, button in poll_buttons.items():
                        button.setActive(seconds == poll_seconds)
                finally:
                    motion_update_guard["active"] = False

                sensitivity_value.setText("Maximum (level 0)" if sensitivity_level == 0 else "Reduced (level 25)")
                ontime_value.setText("2 seconds" if on_time_seconds == 2 else "10 minutes")
                motion_timer.setInterval(max(1, poll_seconds) * 1000)

            def apply_motion_payload(data):
                data = data if isinstance(data, dict) else {}
                config = data.get("config") if isinstance(data.get("config"), dict) else {}
                latest_motion.update({
                    "pollSeconds": int(config.get("pollSeconds") or 3),
                    "sensitivityLevel": int(config.get("sensitivityLevel") or 0),
                    "onTimeSeconds": int(config.get("onTimeSeconds") or 2),
                })
                # A second tap may be queued while the first save is still in
                # flight. Keep the newer local selection visible instead of
                # briefly snapping back to the first server response.
                pending = motion_io.get("pendingChanges")
                if isinstance(pending, dict) and pending:
                    latest_motion.update(pending)

                available = bool(data.get("available"))
                motion = bool(data.get("motion"))
                motion_icon.setMotion(motion, available)
                if not available:
                    motion_state.setText("Unavailable")
                    motion_state.setStyleSheet("color:#ff6772;")
                    motion_detail.setText(str(data.get("error") or "Motion GPIO unavailable"))
                elif motion:
                    motion_state.setText("Motion detected")
                    motion_state.setStyleSheet("color:#50f1ae;")
                    motion_detail.setText("REL HIGH - GPIO27")
                else:
                    motion_state.setText("No motion")
                    motion_state.setStyleSheet("color:#ffffff;")
                    motion_detail.setText("REL LOW - GPIO27")

                apply_motion_config_controls()
                if isinstance(self.s.config, dict):
                    hardware = self.s.config.setdefault("hardware", {})
                    if isinstance(hardware, dict):
                        hardware["motion"] = copy.deepcopy(latest_motion)

            def show_motion_read_error(message: str):
                motion_icon.setMotion(False, False)
                motion_state.setText("Unavailable")
                motion_state.setStyleSheet("color:#ff6772;")
                motion_detail.setText(str(message))

            def refresh_motion():
                if motion_io["closed"] or motion_io["readRunning"] or motion_io["writeRunning"]:
                    return
                request_generation = int(motion_io["generation"])
                motion_io["readRunning"] = True

                def completed(data):
                    motion_io["readRunning"] = False
                    if motion_io["closed"] or request_generation != motion_io["generation"] or motion_io["writeRunning"]:
                        return
                    apply_motion_payload(data)

                def failed(message):
                    motion_io["readRunning"] = False
                    if motion_io["closed"] or request_generation != motion_io["generation"] or motion_io["writeRunning"]:
                        return
                    show_motion_read_error(message)

                # Never perform network I/O on the Qt event thread. At a one-second
                # poll interval even a brief local API stall otherwise freezes every
                # touch, animation, and repaint until the request times out.
                self.run_async(
                    "motion-read",
                    lambda: self.s.api.get("/api/hardware/motion"),
                    completed,
                    failed,
                )

            def flush_motion_save():
                if motion_io["closed"] or motion_io["writeRunning"]:
                    return
                pending = motion_io.get("pendingChanges")
                if not isinstance(pending, dict) or not pending:
                    return

                # Send only fields that actually changed. In particular, changing
                # CHECK EVERY must not re-toggle the SENS/ONTIME GPIO outputs.
                payload = dict(pending)
                pending.clear()
                motion_io["generation"] += 1
                request_generation = int(motion_io["generation"])
                motion_io["writeRunning"] = True
                motion_io["activePayload"] = dict(payload)
                motion_timer.stop()
                motion_write_watchdog.start()
                trace_runtime(f"motion setting save started generation={request_generation} changes={payload}")

                def completed(data):
                    if motion_io["closed"] or request_generation != motion_io["generation"]:
                        return
                    motion_write_watchdog.stop()
                    motion_io["writeRunning"] = False
                    motion_io["activePayload"] = {}
                    apply_motion_payload(data)
                    trace_runtime(f"motion setting save completed generation={request_generation}")
                    motion_timer.start()
                    if motion_io.get("pendingChanges"):
                        QTimer.singleShot(0, flush_motion_save)

                def failed(message):
                    if motion_io["closed"] or request_generation != motion_io["generation"]:
                        return
                    motion_write_watchdog.stop()
                    motion_io["writeRunning"] = False
                    motion_io["activePayload"] = {}
                    trace_runtime(f"motion setting save failed generation={request_generation}: {message}")
                    self.toast.show_message(f"Motion setting failed: {message}", 5500)
                    motion_timer.start()
                    refresh_motion()
                    if motion_io.get("pendingChanges"):
                        QTimer.singleShot(0, flush_motion_save)

                self.run_async(
                    "motion-write",
                    lambda: self.s.api.post("/api/hardware/motion", payload, timeout=2.5),
                    completed,
                    failed,
                )

            def recover_motion_write_timeout():
                if motion_io["closed"] or not motion_io["writeRunning"]:
                    return
                timed_out_generation = int(motion_io["generation"])
                motion_io["generation"] += 1  # Ignore any late worker callback.
                motion_io["writeRunning"] = False
                motion_io["activePayload"] = {}
                trace_runtime(f"motion setting save watchdog recovered generation={timed_out_generation}")
                self.toast.show_message("Motion setting timed out; controls recovered", 5500)
                motion_timer.start()
                refresh_motion()
                if motion_io.get("pendingChanges"):
                    QTimer.singleShot(0, flush_motion_save)

            motion_write_watchdog.timeout.connect(recover_motion_write_timeout)

            def request_motion_save(changes):
                if motion_io["closed"] or motion_update_guard["active"] or not isinstance(changes, dict):
                    return
                clean = {
                    key: value
                    for key, value in changes.items()
                    if key in {"pollSeconds", "sensitivityLevel", "onTimeSeconds"}
                    and latest_motion.get(key) != value
                }
                if not clean:
                    return

                # Return from the touchscreen's current click/release event before
                # changing styles or starting I/O. Disabling/restyling the pressed
                # widget inside that event can leave some X11 touch drivers holding
                # a stale pointer grab, which looks like the whole screen froze.
                def queue_change(next_changes=dict(clean)):
                    if motion_io["closed"]:
                        return
                    latest_motion.update(next_changes)
                    pending = motion_io.get("pendingChanges")
                    if isinstance(pending, dict):
                        pending.update(next_changes)
                    apply_motion_config_controls()
                    trace_runtime(f"motion setting selected changes={next_changes}")
                    flush_motion_save()

                QTimer.singleShot(0, queue_change)

            for seconds, button in poll_buttons.items():
                button.clicked.connect(lambda _checked=False, value=seconds: request_motion_save({"pollSeconds": value}))

            for level, button in sensitivity_buttons.items():
                button.clicked.connect(lambda _checked=False, value=level: request_motion_save({"sensitivityLevel": value}))
            for seconds, button in ontime_buttons.items():
                button.clicked.connect(lambda _checked=False, value=seconds: request_motion_save({"onTimeSeconds": value}))
            motion_timer.timeout.connect(refresh_motion)

            fetch.clicked.connect(lambda: (dlg.accept(), self.do_fetch_update()))
            reboot.clicked.connect(lambda: (dlg.accept(), self.do_restart()))
            backup.clicked.connect(lambda: (dlg.accept(), self.backup_config()))
            close.clicked.connect(dlg.accept)

            refresh_motion()
            motion_timer.start(max(1, int(latest_motion["pollSeconds"])) * 1000)
            dlg.exec_()
            motion_io["closed"] = True
            motion_timer.stop()
            motion_write_watchdog.stop()
        except Exception as exc:
            self.toast.show_message(f"Info failed: {exc}")
        finally:
            self._info_dialog_open = False
            self._info_reopen_block_until = time.monotonic() + 1.5

    def do_fetch_update(self):
        self.toast.show_message("Starting fetch update...", 3500)

        def done(data):
            data = data if isinstance(data, dict) else {}
            self.toast.show_message(data.get("message") or "Fetch update started. Panel will restart.", 6500)

        self.run_async(
            "fetch-update",
            lambda: self.s.api.post("/api/system/fetch-update", {}, timeout=8.0),
            done,
            lambda err: self.toast.show_message(f"Fetch update failed: {err}", 6500),
        )

    def do_restart(self):
        if QMessageBox.question(self, "Restart", "Restart the thermostat service?") != QMessageBox.Yes:
            return
        self.toast.show_message("Restart command sent...", 3500)
        self.run_async(
            "restart",
            lambda: self.s.api.post("/api/system/reboot", {}, timeout=4.0),
            lambda _data: None,
            lambda err: self.toast.show_message(f"Restart failed: {err}"),
        )

    def show_config_portal(self, mode: str = "backup"):
        self.toast.show_message("Opening backup portal...", 3000)

        def done(data):
            data = data if isinstance(data, dict) else {}
            url = str(data.get("url") or "").strip()
            if not url:
                self.toast.show_message(f"Backup portal failed: {data.get('error') or 'The backup portal address was not returned.'}", 8000)
                return
            self.show_config_portal_dialog(data, mode=mode)

        self.run_async(
            "config-portal",
            lambda: self.s.api.post("/api/system/config-web-portal", {}, timeout=4.0),
            done,
            lambda err: self.toast.show_message(f"Backup portal failed: {err}", 8000),
        )

    def show_config_portal_dialog(self, data: dict, mode: str = ""):
        url = str(data.get("url") or "").strip()
        address = str(data.get("address") or "").strip()
        name = str(data.get("thermostatName") or "Smart Thermostat").strip() or "Smart Thermostat"
        action_text = "download or upload a config backup"

        dlg = QDialog(self)
        dlg.setModal(True)
        dlg.setWindowTitle("Backup Config")
        dlg.setFixedSize(720, 410)
        dlg.setStyleSheet("""
            QDialog {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #071222,
                    stop:0.52 #101d35,
                    stop:1 #0b1020);
                color:#f7fbff;
            }
            QLabel {
                color:#f7fbff;
                background:transparent;
                border:0;
                font-family:Arial;
            }
        """)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(14)

        title = QLabel("<span style='color:#46e8ff; letter-spacing:4px; font-size:11px; font-weight:900'>BACKUP PORTAL READY</span><br><span style='font-size:30px; font-weight:1000; color:#ffffff'>Backup Config</span>")
        title.setTextFormat(Qt.RichText)
        root.addWidget(title)

        help_text = QLabel(
            f"Open this address from a computer on the same network to {action_text}. "
            "Keep this popup open while transferring; closing it shuts off the backup portal."
        )
        help_text.setWordWrap(True)
        help_text.setFont(font(12, QFont.Bold))
        help_text.setStyleSheet("color:#dce8ff;")
        root.addWidget(help_text)

        panel = GlassPanel(radius=22)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 16, 18, 16)
        panel_layout.setSpacing(9)

        name_label = QLabel(f"{name}  •  {address or 'Network address ready'}")
        name_label.setFont(font(11, QFont.Black))
        name_label.setStyleSheet("color:#96a7c2;")
        panel_layout.addWidget(name_label)

        url_label = QLabel(url)
        url_label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        url_label.setWordWrap(True)
        url_label.setFont(font(19, QFont.Black))
        url_label.setStyleSheet("color:#ffffff; padding:10px 0;")
        panel_layout.addWidget(url_label)

        note = QLabel("The backup page is only enabled while this popup is open. Closing this popup stops the backup portal so it is not left available on the network.")
        note.setWordWrap(True)
        note.setFont(font(10, QFont.Bold))
        note.setStyleSheet("color:#9fb0c8;")
        panel_layout.addWidget(note)
        root.addWidget(panel, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        copy_btn = RoundButton("Copy Address", active=True, min_h=48)
        close_btn = RoundButton("Close & Stop Backup", active=False, min_h=48)
        buttons.addStretch(1)
        buttons.addWidget(copy_btn)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        def copy_url():
            try:
                QApplication.clipboard().setText(url)
                self.toast.show_message("Backup portal address copied", 3000)
            except Exception:
                pass

        copy_btn.clicked.connect(copy_url)
        close_btn.clicked.connect(dlg.accept)
        try:
            dlg.exec_()
        finally:
            self.stop_config_portal()
            # A config uploaded through the browser is written by the backend,
            # while the native touchscreen keeps its current config in memory.
            # Reload after the portal closes so restored rooms/settings appear
            # immediately without rebooting the unit or restarting the UI.
            QTimer.singleShot(0, self.reload_all)

    def stop_config_portal(self):
        self.run_async(
            "config-portal-close",
            lambda: self.s.api.post("/api/system/config-web-portal/close", {}, timeout=3.0),
            None,
            None,
        )

    def backup_config(self):
        self.show_config_portal("backup")

    def export_config(self):
        self.backup_config()

    def import_config(self):
        self.backup_config()

    def force_panel_geometry(self):
        """Force the native kiosk window to cover the whole X screen.

        On the Raspberry Pi appliance we run directly under xinit with no
        window manager. In that mode Qt's showFullScreen() can leave the
        window at its previous/pre-rotation size, which exposes the black X
        root background on part of the touchscreen. Setting the screen
        geometry explicitly fixes the unused-screen-real-estate issue.
        """
        screen = QApplication.primaryScreen() or self.screen()
        if not screen:
            return
        geo = screen.geometry()
        self.setGeometry(geo)
        self.move(geo.topLeft())
        self.resize(geo.size())
        self.position_sleep_controls()
        try:
            print(f"Native UI geometry forced to {geo.width()}x{geo.height()} at {geo.x()},{geo.y()}", flush=True)
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_F11):
            self.showNormal() if self.isFullScreen() else self.showFullScreen()
            QTimer.singleShot(50, self.force_panel_geometry)
        else:
            super().keyPressEvent(event)


def main():
    install_crash_logging()
    trace_runtime("Native UI main starting")
    # The appliance display is a fixed touchscreen. Disable Qt auto scaling so
    # physical pixels map directly to the X screen size reported after rotation.
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
    os.environ.setdefault("QT_SCALE_FACTOR", "1")

    # Make the panel feel like a tablet: no Qt menu/dialog animation delays,
    # synthesize mouse events directly from the touchscreen, and use a short
    # drag threshold so sliders respond as soon as the finger moves.
    for attr_name, enabled in (
        ("AA_SynthesizeMouseForUnhandledTouchEvents", True),
        ("AA_SynthesizeTouchForUnhandledMouseEvents", False),
        ("AA_UseHighDpiPixmaps", False),
        ("AA_CompressHighFrequencyEvents", True),
        ("AA_CompressTabletEvents", True),
    ):
        attr = getattr(Qt, attr_name, None)
        if attr is not None:
            QApplication.setAttribute(attr, enabled)

    app = QApplication(sys.argv)
    app.setApplicationName("Smart Thermostat Native")
    app.setFont(font(10, QFont.Bold))
    try:
        app.setStartDragTime(120)
        app.setStartDragDistance(6)
        for effect in (
            getattr(Qt, "UI_AnimateMenu", None),
            getattr(Qt, "UI_FadeMenu", None),
            getattr(Qt, "UI_AnimateCombo", None),
            getattr(Qt, "UI_AnimateTooltip", None),
            getattr(Qt, "UI_FadeTooltip", None),
        ):
            if effect is not None:
                app.setEffectEnabled(effect, False)
    except Exception:
        pass
    w = MainWindow()
    if os.environ.get("SMART_THERMOSTAT_WINDOWED") == "1":
        w.resize(1600, 900)
        w.show()
    else:
        w.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        w.force_panel_geometry()
        w.showFullScreen()
        # Run this more than once because xrandr may have just rotated the DSI
        # output in native-xinit.sh before Python starts. This keeps the app
        # from being stuck at the old 800x1280 geometry.
        QTimer.singleShot(50, w.force_panel_geometry)
        QTimer.singleShot(350, w.force_panel_geometry)
        QTimer.singleShot(1000, w.force_panel_geometry)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
