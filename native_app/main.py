#!/usr/bin/env python3
from __future__ import annotations

import copy
import faulthandler
import math
import html
import os
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
    shm = Path("/dev/shm")
    try:
        if shm.exists() and os.access(str(shm), os.W_OK):
            return shm / "smart-thermostat-native" / "logs"
    except Exception:
        pass
    return Path(os.environ.get("SMART_THERMOSTAT_RUNTIME_DIR", "/tmp/smart-thermostat-native")) / "logs"


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
    """Keep a tiny RAM-backed crash log so restart loops do not erase clues."""
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

# Display sleep is handled only by the native UI layer. Backend polling, HVAC
# runtime protection, Home Assistant sync, and alarm logic continue normally.
SCREEN_SLEEP_IDLE_SECONDS = float(os.environ.get("SMART_THERMOSTAT_SCREEN_SLEEP_SECONDS", "10800"))
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
        icon_col, bg1, bg2, border, text_col = self._palette()

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
    """Top-left page lock pill. Locked mode keeps the panel on Thermostat."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.locked = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(154, 48)
        self.setFont(font(9, QFont.Black, 18))

    def setLocked(self, locked: bool):
        self.locked = bool(locked)
        self.setToolTip("Locked to Thermostat" if self.locked else "Tap to lock to Thermostat")
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
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        g = QLinearGradient(r.topLeft(), r.bottomRight())
        if self.locked:
            g.setColorAt(0.0, QColor(92, 22, 39, 238))
            g.setColorAt(0.58, QColor(51, 16, 29, 230))
            g.setColorAt(1.0, QColor(18, 10, 18, 238))
            border = QColor(255, 82, 118, 175)
            icon_bg = QColor(255, 74, 111, 62)
            icon_fg = QColor(255, 210, 221, 230)
            txt = QColor(255, 230, 236)
            label = "LOCKED"
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
        if self.locked:
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



class Header(QWidget):
    navChanged = pyqtSignal(str)
    infoClicked = pyqtSignal()
    settingsClicked = pyqtSignal()
    lockClicked = pyqtSignal()

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
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.update_time)
        self.clock.start(1000)
        self.update_time()



    def set_locked(self, locked: bool):
        self.lock_button.setLocked(locked)
        # Keep Thermostat selectable, but grey out the other tabs while the
        # page lock is engaged. MainWindow still enforces this in set_page().
        for name, btn in self.nav.buttons.items():
            btn.setEnabled((not locked) or name == "Thermostat")

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
        # Local setpoint hold used by the native dial/buttons. Home Assistant and
        # the local backend can briefly report the old target while a just-touched
        # setpoint is still round-tripping. Keep the newly selected target on
        # screen for a short window so the dial does not snap backward and then
        # jump forward again.
        self._target_override_value: int | None = None
        self._target_override_until = 0.0
        self._mode_override: dict | None = None
        self._mode_override_until = 0.0

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

    def set_target_override(self, value: float, hold_seconds: float = 60.0):
        """Hold a locally selected setpoint against stale status refreshes."""
        try:
            val = int(round(float(value)))
        except Exception:
            return
        self._target_override_value = val
        self._target_override_until = time.monotonic() + max(1.0, float(hold_seconds))
        if not isinstance(self.thermostat, dict):
            self.thermostat = {}
        self.thermostat["targetTemp"] = val
        self.thermostat["lastComfortTarget"] = val

    def clear_target_override(self):
        self._target_override_value = None
        self._target_override_until = 0.0

    def set_mode_override(
        self,
        mode: str,
        *,
        away: bool = False,
        hold_seconds: float = 14.0,
        presence_home_override: dict | None = None,
    ):
        """Hold a just-requested local mode against one stale status refresh."""
        mode = str(mode or "").strip().lower()
        if mode not in {"off", "heat", "cool", "away"}:
            return
        override = {
            "mode": "cool" if mode == "away" else mode,
            "away": bool(away or mode == "away"),
            "awaySource": "manual" if (away or mode == "away") else "",
        }
        if presence_home_override is not None:
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
        thermostat["targetTemp"] = self._target_override_value
        thermostat["lastComfortTarget"] = self._target_override_value
        return thermostat

    def ingest_thermostat(self, payload: dict | None) -> dict:
        self.thermostat = self.apply_target_override(self.apply_mode_override(thermostat_detail_payload(payload)))
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
        self.title.setFont(font(17, QFont.Black))
        self.title.setStyleSheet("color:#ffffff; background:transparent; border:0;")
        self.body = QLabel("", self)
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
            self.setMinimumHeight(168)
            self.setMaximumHeight(238)
            self.title.setFont(font(22, QFont.Black))
            self.body.setFont(font(13, QFont.Black))
            self.bypass.setFixedWidth(190)
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
        self._layout_buttons(bypass_left=self.kind in {"lockout", "door-pause"} and bool(bypass))
        color = {
            "heat": "rgba(255,72,83,0.58)",
            "cool": "rgba(65,225,255,0.48)",
            "lockout": "rgba(188,132,255,0.50)",
            "door-pause": "rgba(255,154,36,0.76)",
            "auto": "rgba(72,214,255,0.42)",
            "safety": "rgba(255,72,83,0.56)" if "Heat" in title else "rgba(65,225,255,0.50)",
        }.get(self.kind, "rgba(72,214,255,0.38)")
        border = "rgba(255,231,164,0.52)" if self.kind == "door-pause" else "rgba(255,255,255,0.20)"
        radius = 26 if self.kind == "door-pause" else 24
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
                b = RoundButton(ch.lower(), active=True, min_h=42)
                b.setFixedSize(52, 42)
                b.pressed.connect(lambda c=ch: self.add_letter(c))
                self.letter_buttons.append((b, ch))
                row.addWidget(b)
            row.addStretch(1)
            root.addLayout(row)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.shift = RoundButton("Uppercase", active=False, min_h=46)
        space = RoundButton("Space", active=False, min_h=46)
        back = RoundButton("⌫", active=False, min_h=46)
        clear = RoundButton("Clear", active=False, min_h=46)
        cancel = RoundButton("Cancel", active=False, kind="danger", min_h=46)
        done = RoundButton("Done", active=True, min_h=46)
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
        self.shift.setText("lowercase" if self.uppercase else "Uppercase")
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

    def __init__(self, state: AppState, schedule: dict | None = None, parent=None):
        super().__init__(parent)
        self.s = state
        self.schedule = copy.deepcopy(schedule or {})
        self.people: list[dict] = []
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
        self.available_people: list[dict] = []
        self.load_people()
        self.build()
        QTimer.singleShot(0, self.fit_to_screen)

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
        saved_groups = []
        if isinstance(self.s.thermostat, dict):
            for key in ("people", "autoAwayPeople"):
                value = self.s.thermostat.get(key)
                if isinstance(value, list):
                    saved_groups.append(value)
        for saved_people in saved_groups:
            for p in saved_people:
                if isinstance(p, dict) and p.get("entityId") and all(str(x.get("entityId")) != str(p.get("entityId")) for x in self.available_people):
                    self.available_people.append(p)
        try:
            data = self.s.api.post("/api/ha/entities", self.s.ha_payload({"domains": ["person"]}))
            for p in data.get("entities") or []:
                eid = str(p.get("entityId") or "")
                if eid and all(str(x.get("entityId")) != eid for x in self.available_people):
                    self.available_people.append(p)
        except Exception:
            pass

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
        hdr.addWidget(self.small_label("Only run if these people are home"))
        hdr.addStretch(1)
        add = RoundButton("+ Person", active=True, min_h=34)
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
            if str(p.get("entityId")) == entity_id:
                return str(p.get("name") or p.get("friendly_name") or entity_id)
        return entity_id

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
            QMessageBox.warning(self, "People", "No Home Assistant person entities found.")
            return
        dlg = EntityPickerDialog("Choose Person", entities, self)
        def selected(e):
            eid = str(e.get("entityId") or "")
            if eid and eid not in self.person_ids:
                self.person_ids.append(eid)
                self.refresh()
        dlg.selected.connect(selected)
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
            "lastTriggeredDate": str(self.schedule.get("lastTriggeredDate") or ""),
        }
        self.saved.emit(payload)
        self.accept()


class ScheduleManagerDialog(QDialog):
    changed = pyqtSignal()

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.s = state
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
        close = RoundButton("Done", active=False, min_h=46)
        new_btn.clicked.connect(self.new_schedule)
        close.clicked.connect(self.accept)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(new_btn)
        header.addWidget(close)
        root.addLayout(header)
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
        people_text = "No person requirement" if not people else f"{len(people)} person{'s' if len(people) != 1 else ''} required"
        day_text = schedule_days_text(sched.get("days"))
        time_text = format_schedule_time_12h(sched.get('time') or '07:00')
        text = QLabel(f"<b>{sched.get('name') or 'Schedule'}</b><br>{day_text} at {time_text} • Cool {sched.get('coolSetpoint')}° • Heat {sched.get('heatSetpoint')}°<br>{people_text}")
        text.setTextFormat(Qt.RichText)
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

    def save_schedules(self, schedules: list[dict]):
        # Update the native UI first so shortcut buttons return instantly, then
        # persist to the backend. This prevents a slow local API write from
        # making the schedule screen look like it did nothing.
        self.s.set_thermostat_schedules_local(schedules)
        self.changed.emit()
        self.refresh()
        try:
            self.s.update_thermostat({"schedules": schedules})
            self.s.set_thermostat_schedules_local(self.s.thermostat_schedules())
            self.changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Schedules", f"Schedule save failed: {exc}")

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
        mode = str((self.s.thermostat or {}).get("mode") or "cool").lower()
        active = str((self.s.thermostat or {}).get("autoActiveMode") or "").lower()
        effective = active if mode == "auto" and active in {"heat", "cool"} else mode
        target = sched.get("heatSetpoint") if effective == "heat" else sched.get("coolSetpoint")
        try:
            val = int(float(target))
            self.s.set_target_override(val)
            self.s.update_thermostat({"targetTemp": val, "lastComfortTarget": val, "targetChangeSource": "panel"})
            self.changed.emit()
        except Exception as exc:
            self.s.clear_target_override()
            QMessageBox.warning(self, "Schedule", str(exc))



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
        self.door_card = InfoTile("Doors", "CLOSED", "▯", good=True)
        self.alarm_card = InfoTile("Alarmo", "DISARMED", "盾", good=True)
        self.virtual_panel = VirtualOutputsPanel()
        self.virtual_temp_pending: float | None = None
        self.virtual_temp_push_timer = QTimer(self)
        self.virtual_temp_push_timer.setSingleShot(True)
        self.virtual_temp_push_timer.timeout.connect(self.push_virtual_temp)
        self.virtual_panel.tempChanged.connect(self.set_virtual_temp)
        self.minus = IconCircle("−", "minus", 82)
        self.plus = IconCircle("+", "plus", 82)
        self.schedule_button = ScheduleClockButton()
        # Open on press instead of release. The touchscreen can occasionally
        # drop the release/click event near screen edges, which made the timer
        # button appear dead. A guard in open_schedule_manager prevents double-open.
        self.schedule_button.pressed.connect(self.open_schedule_manager)

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
        self.dial.targetChanged.connect(lambda v: self.set_target(v))
        # Doors already show their live state on the tile; no extra source toast is needed.
        self.alarm_card.clicked.connect(self.show_alarm_dialog)

        self.fx_phase = 0
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
        """Return True only when the expensive moving PCB background is enabled.

        The Raspberry Pi 3B can spend most of one CPU core repainting the full
        thermostat page when the temperature-reactive background animates every
        ~520 ms.  Keep the rich static background and temperature color shifts,
        but do not continuously repaint the whole page unless a developer
        explicitly opts in with SMART_THERMOSTAT_BACKGROUND_ANIMATION=1.
        """
        value = str(os.environ.get("SMART_THERMOSTAT_BACKGROUND_ANIMATION") or "").strip().lower()
        return value in {"1", "true", "yes", "on", "full"}

    def retune_fx_timer(self):
        if not hasattr(self, "fx_timer"):
            return
        # Keep the timer at one second for door-pause/manual-delay countdown
        # text refreshes.  Do not speed it up for background animation by
        # default; that was measured using ~80% CPU on a Pi-class panel.
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
            # Static phase keeps the PCB nodes from jumping on occasional status
            # refresh paints while the low-resource default is active.
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
            w = min(660, max(540, self.width() - 96))
            self.alert_banner.setFixedWidth(w)
            self.alert_banner.adjustSize()
            x = max(12, (self.width() - self.alert_banner.width()) // 2)
            y = 54
        else:
            w = min(520, max(420, self.width() - 120))
            self.alert_banner.setFixedWidth(w)
            self.alert_banner.adjustSize()
            x = max(12, (self.width() - self.alert_banner.width()) // 2)
            y = 72
        self.alert_banner.move(x, y)
        if self.alert_banner.isVisible():
            self.alert_banner.raise_()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
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
        heat_mode = self.active_visual_mode() == "heat"

        # Circuit-board climate background. This is drawn procedurally, so it
        # needs no image asset and can change color based on room temperature.
        # It intentionally matches the direction of a modern PCB / smart-panel
        # look: dark board, circuit traces, glowing nodes and temp-reactive color.
        cold_ratio = 0.0
        if (not neutral_off) and current <= 67.0:
            # Blue starts at 67° and reaches full dark-blue intensity by 64°.
            cold_ratio = clamp((67.0 - current) / 3.0, 0.0, 1.0)
            # Make the change visible right at 67°, then ramp darker as it drops.
            cold_ratio = max(cold_ratio, 0.18)
            if current <= 64.0:
                cold_ratio = 1.0

        hot_ratio = 0.0
        if (not neutral_off) and current >= 72.0:
            # Red starts at 72° and reaches full dark-red intensity by 76°.
            hot_ratio = clamp((current - 72.0) / 4.0, 0.0, 1.0)
            # Make the change visible right at 72°, then ramp darker as it rises.
            hot_ratio = max(hot_ratio, 0.18)
            if current >= 76.0:
                hot_ratio = 1.0

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

        heat_mode_visual = bool(heat_mode and cold_ratio <= 0 and hot_ratio <= 0)
        heat_hint = 0.30 if heat_mode_visual and not neutral_off else 0.0

        climate_intensity = max(cold_ratio, hot_ratio, heat_hint)
        cold_weight = cold_ratio
        hot_weight = max(hot_ratio, heat_hint)

        def mix_channel(cold_value: int, neutral_value: int, hot_value: int) -> int:
            value = neutral_value
            if cold_weight > 0:
                value = int(value * (1.0 - cold_weight) + cold_value * cold_weight)
            if hot_weight > 0:
                value = int(value * (1.0 - hot_weight) + hot_value * hot_weight)
            return int(clamp(value, 0, 255))

        board_a = QColor(
            mix_channel(0, 4, 32),
            mix_channel(8, 18, 7),
            mix_channel(42, 38, 16),
        )
        board_b = QColor(
            mix_channel(0, 8, 78),
            mix_channel(34, 46, 14),
            mix_channel(125, 78, 34),
        )
        trace = QColor(
            mix_channel(74, 54, 255),
            mix_channel(210, 224, 130),
            mix_channel(255, 116, 58),
            78 if not neutral_off else 50,
        )
        trace_dim = QColor(trace.red(), trace.green(), trace.blue(), 34 if not neutral_off else 24)
        trace_hot = QColor(
            mix_channel(112, 90, 255),
            mix_channel(238, 246, 172),
            mix_channel(255, 196, 94),
            132 if climate_intensity > 0.01 else 88,
        )
        node_col = QColor(trace_hot.red(), trace_hot.green(), trace_hot.blue(), 170 if not neutral_off else 110)
        glow_col = QColor(trace_hot.red(), trace_hot.green(), trace_hot.blue(), int(64 + 80 * climate_intensity))

        base = QLinearGradient(0, 0, w, h)
        base.setColorAt(0.0, QColor(max(0, board_a.red() - 2), max(0, board_a.green() - 2), max(0, board_a.blue() - 4)))
        base.setColorAt(0.46, board_a)
        base.setColorAt(0.74, board_b)
        base.setColorAt(1.0, QColor(2, 4, 10))
        p.fillRect(r, base)

        # Broad temperature glow behind the traces.
        if cold_weight > 0:
            cold_glow = QRadialGradient(QPointF(w * 0.28, h * 0.42), w * 0.78)
            cold_glow.setColorAt(0.0, QColor(0, 192, 255, int(98 * cold_weight)))
            cold_glow.setColorAt(0.46, QColor(0, 58, 210, int(62 * cold_weight)))
            cold_glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(r, cold_glow)
        if hot_weight > 0:
            hot_glow = QRadialGradient(QPointF(w * 0.74, h * 0.38), w * 0.78)
            hot_glow.setColorAt(0.0, QColor(255, 62, 42, int(104 * hot_weight)))
            hot_glow.setColorAt(0.42, QColor(170, 8, 42, int(62 * hot_weight)))
            hot_glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(r, hot_glow)

        # Avoid large translucent rectangles in the PCB background; they looked
        # like leftover UI panels behind the thermostat controls.

        phase = int(getattr(self, "fx_phase", 0) or 0)

        def board_point(col: int, row: int, cols: int = 12, rows: int = 8) -> QPointF:
            return QPointF(w * (0.05 + col * (0.90 / (cols - 1))), h * (0.07 + row * (0.82 / (rows - 1))))

        def draw_trace(points: list[QPointF], *, bright: bool = False, width_scale: float = 1.0):
            if len(points) < 2:
                return
            pen_color = trace_hot if bright else trace
            path = QPainterPath(points[0])
            for pt in points[1:]:
                path.lineTo(pt)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(pen_color.red(), pen_color.green(), pen_color.blue(), max(18, int(pen_color.alpha() * 0.28))), 7.5 * width_scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)
            p.setPen(QPen(pen_color, 2.0 * width_scale, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPath(path)

        # Fixed circuit routes. They are deterministic and lightweight, but
        # look much closer to a PCB than the old generic grid/lines.
        routes = [
            [(0, 0), (2, 0), (2, 2), (4, 2), (4, 1), (7, 1), (7, 3), (10, 3), (10, 1), (11, 1)],
            [(0, 2), (1, 2), (1, 4), (3, 4), (3, 5), (6, 5), (6, 3), (8, 3), (8, 2), (11, 2)],
            [(0, 5), (2, 5), (2, 6), (5, 6), (5, 4), (7, 4), (7, 6), (10, 6), (10, 7), (11, 7)],
            [(1, 7), (1, 6), (3, 6), (3, 3), (5, 3), (5, 2), (9, 2), (9, 0), (11, 0)],
            [(0, 1), (3, 1), (3, 0), (6, 0), (6, 2), (8, 2), (8, 4), (11, 4)],
            [(0, 6), (2, 6), (2, 4), (4, 4), (4, 6), (6, 6), (6, 7), (9, 7), (9, 5), (11, 5)],
            [(5, 0), (5, 1), (4, 1), (4, 3), (2, 3), (2, 4), (0, 4)],
            [(11, 6), (9, 6), (9, 4), (7, 4), (7, 5), (4, 5), (4, 7)],
            [(6, 1), (6, 2), (5, 2), (5, 4), (3, 4), (3, 6), (1, 6)],
            [(10, 0), (10, 2), (9, 2), (9, 3), (6, 3), (6, 5), (8, 5), (8, 7)],
        ]
        for idx, route in enumerate(routes):
            bright = ((idx * 7 + phase // 2) % 11) < 2 or (climate_intensity > 0.85 and idx % 3 == 0)
            pts = [board_point(c, rr) for c, rr in route]
            draw_trace(pts, bright=bright, width_scale=1.0 if idx % 2 else 1.08)

        # Micro traces: shorter details and branching lines.
        p.setBrush(Qt.NoBrush)
        for i in range(34):
            col = (i * 5 + 2) % 12
            row = (i * 3 + 1) % 8
            start_pt = board_point(col, row)
            direction = -1 if i % 2 else 1
            length = w * (0.045 + (i % 4) * 0.012)
            vertical = i % 5 == 0
            color = trace_dim if i % 6 else QColor(trace.red(), trace.green(), trace.blue(), trace.alpha())
            p.setPen(QPen(color, 1.25, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            if vertical:
                p.drawLine(start_pt, QPointF(start_pt.x(), start_pt.y() + direction * length))
            else:
                mid = QPointF(start_pt.x() + direction * length * 0.55, start_pt.y())
                end = QPointF(mid.x(), mid.y() + direction * length * 0.36)
                p.drawLine(start_pt, mid)
                p.drawLine(mid, end)

        # Nodes and glowing pads.
        p.setPen(Qt.NoPen)
        for row in range(8):
            for col in range(12):
                include = (col * 3 + row * 5) % 4 != 1
                if not include:
                    continue
                pt = board_point(col, row)
                pulse = 0.5 + 0.5 * math.sin((phase * 0.12) + col * 0.9 + row * 1.3)
                strong = ((col + row * 2 + phase // 5) % 17) == 0
                radius = 3.2 + ((col + row) % 3) * 0.8
                if strong:
                    halo = QRadialGradient(pt, 24 + 10 * pulse)
                    halo.setColorAt(0.0, QColor(glow_col.red(), glow_col.green(), glow_col.blue(), int(90 + 70 * pulse)))
                    halo.setColorAt(0.45, QColor(glow_col.red(), glow_col.green(), glow_col.blue(), int(30 + 34 * pulse)))
                    halo.setColorAt(1.0, QColor(0, 0, 0, 0))
                    p.setBrush(QBrush(halo))
                    p.drawEllipse(pt, 24 + 10 * pulse, 24 + 10 * pulse)
                p.setBrush(QColor(node_col.red(), node_col.green(), node_col.blue(), 88 + (42 if strong else 0)))
                p.drawEllipse(pt, radius, radius)

        # A few larger chip pads for a more intentional circuit-board style.
        chip_specs = (
            (0.43, 0.42, 0.18, 0.11),
            (0.14, 0.50, 0.14, 0.12),
        )
        for x_ratio, y_ratio, ww_ratio, hh_ratio in chip_specs:
            chip = QRectF(w * x_ratio, h * y_ratio, w * ww_ratio, h * hh_ratio)
            fill = QColor(2, 10, 22, 82)
            border = QColor(trace_hot.red(), trace_hot.green(), trace_hot.blue(), 76)
            p.setBrush(fill)
            p.setPen(QPen(border, 1.35))
            p.drawRoundedRect(chip, 10, 10)
            pin_count = 6
            p.setPen(QPen(QColor(trace.red(), trace.green(), trace.blue(), 66), 1.2, Qt.SolidLine, Qt.RoundCap))
            for i in range(pin_count):
                y = chip.top() + chip.height() * ((i + 1) / (pin_count + 1))
                p.drawLine(QPointF(chip.left() - 14, y), QPointF(chip.left(), y))
                p.drawLine(QPointF(chip.right(), y), QPointF(chip.right() + 14, y))

        # Final glass and vignette layer to keep labels/buttons readable.
        sheen = QLinearGradient(0, 0, 0, h)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 14 if not neutral_off else 8))
        sheen.setColorAt(0.18, QColor(255, 255, 255, 3))
        sheen.setColorAt(0.52, QColor(255, 255, 255, 0))
        sheen.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, sheen)

        # Subtle center darkening behind the dial/control area.
        dial_shadow = QRadialGradient(QPointF(w * 0.50, h * 0.50), min(w, h) * 0.44)
        dial_shadow.setColorAt(0.0, QColor(0, 0, 0, 44))
        dial_shadow.setColorAt(0.46, QColor(0, 0, 0, 20))
        dial_shadow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, dial_shadow)

        vignette = QRadialGradient(QPointF(w * 0.50, h * 0.48), max(w, h) * 0.84)
        vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
        vignette.setColorAt(0.56, QColor(0, 0, 0, 0))
        vignette.setColorAt(1.0, QColor(0, 0, 0, 88 if not neutral_off else 72))
        p.fillRect(r, vignette)

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
            self.alert_banner.set_alert(
                "door-pause",
                "Comfort Paused",
                f"{door_name} is open.\nUsing the away setpoint until it closes.",
                dismiss=False,
                revert=False,
                bypass=True,
                bypass_text="Snooze 5 Minutes",
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
            self.s.status_refresh_paused_until = max(getattr(self.s, "status_refresh_paused_until", 0.0), time.monotonic() + 2.5)
            self.sync(self.s.config, self.s.thermostat)

            changes = {"autoSwitchNotice": cleared_notice}
            if dismissed:
                changes["autoSwitchNoticeDismissed"] = dismissed
            self.s.update_thermostat(changes)
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Dismiss failed: {exc}")

    def dismiss_manual_override_notice(self):
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
            self.s.status_refresh_paused_until = max(getattr(self.s, "status_refresh_paused_until", 0.0), time.monotonic() + 2.5)
            self.sync(self.s.config, self.s.thermostat)
            self.s.update_thermostat({"autoSwitchHold": next_hold})
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Dismiss failed: {exc}")

    def revert_auto_switch(self):
        self.hide_notice_action_popup()
        t = self.thermostat_view()
        notice = t.get("autoSwitchNotice") if isinstance(t.get("autoSwitchNotice"), dict) else {}
        from_mode = str(notice.get("fromMode") or "").lower()
        if from_mode not in {"heat", "cool"}:
            self.dismiss_auto_switch()
            return
        try:
            self.s.update_thermostat({
                "mode": from_mode,
                "modeChangeSource": "panel",
                "away": False,
                "autoSwitchNotice": {"active": False, "source": "", "fromMode": "", "toMode": "", "switchTemp": 0, "outdoorTemp": 0, "coolTarget": 0, "heatTarget": 0, "createdAt": 0},
                "autoSwitchHold": {"active": True, "source": "manual", "mode": from_mode, "until": int(time.time() * 1000) + 600000, "reason": "revert"},
            })
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Revert failed: {exc}")

    def bypass_changeover_lockout(self):
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
            self.s.update_thermostat(changes)
            self.sync(self.s.config, self.s.thermostat)
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
            self.door_countdown.setText(f"{short_name} OPEN\nSNOOZED {remaining}")
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
        changes = {"pauseFunction": {"action": "snooze", "snoozeMinutes": 5}}
        pause = self.s.thermostat.setdefault("pauseFunction", {})
        if isinstance(pause, dict):
            pause["snoozeUntil"] = int(time.time() * 1000) + 300000
            pause["active"] = False
            pause["pausedAt"] = 0
            pause["previousTargetTemp"] = None
            pause["previousLastComfortTarget"] = None
            pause["activeEntityIds"] = []
            pause["countdownAllowed"] = False
            pause["countdownReason"] = "snoozed"
        self.s.status_refresh_paused_until = max(getattr(self.s, "status_refresh_paused_until", 0.0), time.monotonic() + 2.5)
        self.sync(self.s.config, self.s.thermostat)
        self.requestToast.emit("Door pause snoozed for 5 minutes")

        def done(result):
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "door-snooze",
            lambda: self.s.api.thermostat_update(changes),
            done,
            lambda err: self.requestToast.emit(f"Snooze failed: {err}"),
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
            self.schedule_shortcuts_lay.addWidget(b)
        self.schedule_shortcuts_lay.addStretch(1)

    def open_schedule_manager(self):
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

    def request_peer_sync(self, changes: dict):
        top = self.window()
        if hasattr(top, "request_peer_sync"):
            try:
                top.request_peer_sync(changes)
            except Exception:
                pass

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
        mode = str(mode or "").strip().lower()
        if mode not in {"off", "heat", "cool", "away", "arriving"}:
            return
        before_tap = copy.deepcopy(self.thermostat_view())
        if mode == "arriving":
            arriving_override = self.arriving_override_payload(before_tap)
            changes = {
                "away": False,
                "awaySource": "",
                "manualAwayPresenceLatch": None,
                "presenceHomeOverride": arriving_override,
            }
            resume_mode = str(before_tap.get("mode") or "cool").lower()
            if resume_mode == "auto":
                resume_mode = str(before_tap.get("autoActiveMode") or before_tap.get("activeMode") or "cool").lower()
            if resume_mode not in {"heat", "cool", "off"}:
                resume_mode = "cool"
            self.s.set_mode_override(resume_mode, away=False, presence_home_override=arriving_override)
            self.s.thermostat["away"] = False
            self.s.thermostat["awaySource"] = ""
            self.s.thermostat["manualAwayPresenceLatch"] = None
            self.s.thermostat["presenceHomeOverride"] = copy.deepcopy(arriving_override)
            if before_tap.get("lastComfortTarget") is not None:
                self.s.thermostat["targetTemp"] = before_tap.get("lastComfortTarget")
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
                self.s.thermostat["presenceHomeOverride"] = None
        else:
            # Physical/manual button taps should visibly win immediately.
            changes = {"mode": mode, "away": False, "awaySource": "", "modeChangeSource": "panel"}
            self.s.set_mode_override(mode, away=False)
            self.s.thermostat["mode"] = mode
            self.s.thermostat["away"] = False
            self.s.thermostat["awaySource"] = ""
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
        if mode not in {"away", "arriving"}:
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
        now = time.monotonic()
        if now - getattr(self, "_return_home_requested_at", 0.0) < 0.75:
            return
        self._return_home_requested_at = now
        # A tap on Away creates a short local mode hold so stale status reads do
        # not make the button feel broken. When Return Home is tapped, that hold
        # must be cleared immediately or the local UI can repaint Away several
        # more times before the backend response arrives.
        self.s.clear_mode_override()
        self.s.status_refresh_paused_until = max(getattr(self.s, "status_refresh_paused_until", 0.0), time.monotonic() + 2.5)
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
            # person trackers still say everyone is away. The backend releases
            # this override automatically once any configured Auto Away user reports Home.
            changes["presenceHomeOverride"] = {
                "active": True,
                "startedAt": int(time.time() * 1000),
                "entityIds": entity_ids,
                "reason": "manual-return-home",
            }
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
        t = self.thermostat_view()
        # One physical tap should move the main setpoint by exactly 1°F.
        # IconCircle now emits clicked() once, but keep this explicit so future
        # repeat/gesture changes cannot accidentally double the step size.
        step = 1 if delta >= 0 else -1
        self.set_target(float(t.get("targetTemp", t.get("target_temp", 70))) + step)

    def set_target(self, value: float):
        try:
            t = self.thermostat_view()
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
        self.apply_local_minimum_runtime_prediction(before_tap, val)
        self.sync(self.s.config, self.s.thermostat)
        if not bool(t.get("away")):
            self.request_peer_sync({"targetTemp": val})

        def done(result):
            if isinstance(result, dict):
                self.s.ingest_thermostat(result)
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "thermostat-target",
            lambda: self.s.api.thermostat_update({"targetTemp": val, "lastComfortTarget": val, "targetChangeSource": "panel"}),
            done,
            lambda err: (self.s.clear_target_override(), self.requestToast.emit(f"Set temp failed: {err}")),
        )

    def set_virtual_temp(self, value: float):
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
        try:
            self.s.update_thermostat({
                "currentTemp": value,
                "currentTempSource": "virtual",
                "currentTempSourceName": "Virtual Temp Test",
                "currentTempUpdatedAt": time.time(),
                "virtualTempOverrideUntil": int(time.time() * 1000) + 120000,
            })
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Virtual temp failed: {exc}")

    def show_alarm_dialog(self):
        now = time.monotonic()
        if getattr(self, "_alarm_dialog_open", False):
            return
        if now < getattr(self, "_alarm_reopen_block_until", 0.0):
            return
        ha = self.s.ha()
        entity = ha.get("alarmEntity") or {}
        eid = entity.get("entityId") or ""
        if not eid:
            self.requestToast.emit("No alarm entity assigned")
            return
        # Open the alarm panel immediately. A Home Assistant state refresh can
        # take long enough to make the touch feel dead, so refresh it in the
        # background and re-render the panel only if the dialog is still alive.
        # This avoids touching a modal dialog after Cancel/close on the Pi X11
        # touchscreen, which can otherwise drop the full native UI to black.
        self._alarm_dialog_open = True
        dlg = AlarmControlDialog(self.s, entity, self)
        trace_runtime(f"alarm dialog opened entity={eid} state={dlg.current_state()}")

        def refresh_done(fresh):
            if not fresh:
                return
            try:
                entity.update(fresh)
                if dlg.can_update_ui():
                    dlg.apply_fresh_alarm_state(fresh)
                else:
                    trace_runtime("alarm open refresh skipped because dialog already closed")
            except RuntimeError as exc:
                trace_runtime(f"alarm open refresh skipped after Qt object cleanup: {exc}")
            except Exception as exc:
                trace_runtime(f"alarm open refresh failed: {exc}")

        self.run_async("alarm-state-open", self.s.refresh_alarm_state, refresh_done, lambda err: trace_runtime(f"alarm open refresh failed: {err}"))

        def applied(alarm, action):
            if alarm:
                entity.update(alarm)
            self.requestToast.emit(f"Alarm {action.replace('_', ' ')} sent")
            self.sync(self.s.config, self.s.thermostat)
        dlg.actionDone.connect(applied)
        self._alarm_dialog = dlg
        try:
            dlg.exec_()
        finally:
            try:
                dlg.prepare_for_close("show_alarm_dialog finally")
            except Exception:
                pass
            if getattr(self, "_alarm_dialog", None) is dlg:
                self._alarm_dialog = None
            self._alarm_dialog_open = False
            # Touchscreens can emit a final release after the modal has closed.
            # Swallow it briefly so it cannot hit Sleep, Info, Settings, or Alarmo
            # underneath and make the panel look like it crashed.
            self._alarm_reopen_block_until = time.monotonic() + 1.2
            self._modal_touch_block_until = time.monotonic() + 0.55
            trace_runtime("alarm dialog closed")

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
        if hasattr(self, "person_presence_strip"):
            self.person_presence_strip.update_people(t.get("people") or [])
        self.notice.hide()
        self.refresh_schedule_shortcuts()
        if away:
            source = str(t.get("awaySource") or "").lower()
            if source == "presence":
                self.away_body.setText("No assigned Auto Away users are home. Tap Return Home to hold Home until one assigned Auto Away user reports Home again.")
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

    def poll(self):
        # Keep thermostat page light. Expensive HA polling is done only for cards with configured entities.
        pass


class InfoTile(HoldCard):
    def __init__(self, title: str, value: str, symbol: str, good: bool = False, parent=None):
        super().__init__(parent)
        self.title = title
        self.value = value
        self.symbol = symbol
        self.good = good
        self.alarm_state = ""
        self.flash_on = False
        self.flash_timer = QTimer(self)
        self.flash_timer.timeout.connect(self._flash_tick)
        self.setMinimumSize(226, 164)
        self.setMaximumWidth(270)

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
        p.setBrush(QBrush(g))
        p.setPen(QPen(border_color, 1.6))
        p.drawRoundedRect(r, 28, 28)

        self.draw_icon(p, r.center().x(), r.top() + 50, 68)

        p.setFont(font(15, QFont.Black))
        p.setPen(QColor(246, 251, 255))
        p.drawText(QRectF(16, 100, r.width() - 32, 24), Qt.AlignCenter, self.title)

        badge_text = str(self.value or "").upper()
        fm = p.fontMetrics()
        badge_w = max(86, min(r.width() - 34, fm.horizontalAdvance(badge_text) + 28))
        badge = QRectF(r.center().x() - badge_w / 2, 128, badge_w, 24)
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
        required_code = self._entry_code_for_action(ctl, action)
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
        self.requestToast.emit(f"{ctl.get('haName') or ctl.get('name')} {action.replace('_', ' ')} sent")
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
        self.requestToast.emit("Room lights updated")

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
        payload = {"entityId": entity_id, "action": action, "refresh": False}
        if brightness is not None:
            payload["brightness"] = int(clamp(brightness, 0, 100))
            payload["transition"] = 0.25
        if color:
            payload["color"] = color
        elif light.get("colorSupported") and light.get("color"):
            payload["color"] = light.get("color")
        self._set_light_optimistic(light, payload.get("brightness"), action=action, color=payload.get("color"))
        self._light_settle_until[entity_id] = time.monotonic() + 0.80

        def worker():
            return self.s.api.post("/api/ha/light/action", self.s.ha_payload(payload))

        def done(_result):
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
        self.requestToast.emit(f"Room blinds {action} sent")

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
        if not quiet:
            label = f"{int(desired_position)}%" if action == "position" and desired_position is not None else action
            self.requestToast.emit(f"{blind.get('haName') or blind.get('name')} {label}")

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
        self.room_title = QLabel("Livingroom Sonos")
        self.room_title.setFont(font(24, QFont.Black))
        self.room_title.setStyleSheet("color:#f6f8ff;")
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
        self.switch_buttons["tv_power"] = self.tv_power
        self.switch_buttons["projector"] = self.projector
        projector_row = QHBoxLayout()
        projector_row.setContentsMargins(0, 0, 0, 0)
        projector_row.setSpacing(12)
        projector_row.addStretch(1)
        projector_row.addWidget(self.tv_power)
        projector_row.addWidget(self.projector)
        projector_row.addStretch(1)
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

    def player_id(self):
        ha = self.s.ha()
        return ha.get("selectedMediaPlayerId") or nested_get(ha, "mediaPlayerEntity", "entityId", default="") or ""

    def sync(self, config, thermostat):
        super().sync(config, thermostat)
        ha = self.s.ha()
        mp = self.player_id()
        title = "Livingroom Sonos"
        for p in ha.get("mediaPlayerEntities") or ha.get("audioAvailableEntities", {}).get("mediaPlayers", []) or []:
            if p.get("entityId") == mp:
                title = p.get("name") or title
        self.room_title.setText(title)
        self.source_label.setText(title.upper())
        if not self.player_state:
            self.artist_label.setText(title)
        self.apply_audio_control_state()

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

        self.requestToast.emit(f"Applying {label}...")

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
            self.requestToast.emit(f"{label} applied" + (f"; skipped {skipped}" if skipped else ""))
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
        if not eid:
            return
        payload = self.s.ha_payload({"entityIds": [eid]})

        def done(result):
            players = (result or {}).get("players") or []
            if players:
                self.player_state = players[0]
                self.apply_player_state()

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
            b = RoundButton(label, active=(label not in {"⌫", "Cancel"}), min_h=64)
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
            self.code_display.repaint()

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

    def __init__(self, state: AppState, selected_people: list[dict] | None = None, parent=None, *, title: str = "Auto Away / Home", note: str | None = None):
        super().__init__(parent)
        self.s = state
        self.selected_people = copy.deepcopy(selected_people or [])
        self.dialog_title = str(title or "People")
        self.note_text = str(note or "Select the Home Assistant person entries to use for this feature.")
        self.available_people: list[dict] = []
        self.buttons: dict[str, RoundButton] = {}
        self.setModal(True)
        self.setWindowTitle(self.dialog_title)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        self.load_people()
        self.build()
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    def load_people(self):
        by_id: dict[str, dict] = {}
        for p in self.selected_people:
            if isinstance(p, dict):
                eid = str(p.get("entityId") or p.get("entity_id") or "").strip()
                if eid:
                    by_id[eid] = {"entityId": eid, "name": str(p.get("name") or p.get("friendly_name") or eid), "state": str(p.get("state") or "")}
        try:
            data = self.s.api.post("/api/ha/entities", self.s.ha_payload({"domains": ["person"]}))
            for p in data.get("entities") or []:
                eid = str(p.get("entityId") or p.get("entity_id") or "").strip()
                if eid:
                    by_id[eid] = {"entityId": eid, "name": str(p.get("name") or p.get("friendly_name") or eid), "state": str(p.get("state") or "")}
        except Exception:
            pass
        self.available_people = sorted(by_id.values(), key=lambda x: str(x.get("name") or x.get("entityId")).lower())

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
            none = QLabel("No Home Assistant person entities found.")
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



class ThermostatSyncSelectionDialog(QDialog):
    saved = pyqtSignal(list)

    def __init__(self, state: AppState, selected_peers: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.s = state
        self.selected_peers = copy.deepcopy(selected_peers or [])
        self.available_peers: list[dict] = []
        self.buttons: dict[str, RoundButton] = {}
        self.setModal(True)
        self.setWindowTitle("Thermostat Sync")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        self.load_peers()
        self.build()
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    def selected_ids(self) -> set[str]:
        return {str(p.get("entityId") or p.get("entity_id") or "").strip() for p in self.selected_peers if isinstance(p, dict)}

    def load_peers(self):
        by_id: dict[str, dict] = {}
        for peer in self.selected_peers:
            if not isinstance(peer, dict):
                continue
            eid = str(peer.get("entityId") or peer.get("entity_id") or "").strip()
            if eid.startswith("climate."):
                by_id[eid] = {
                    "entityId": eid,
                    "name": str(peer.get("name") or peer.get("friendlyName") or peer.get("friendly_name") or eid),
                    "state": str(peer.get("state") or "unknown"),
                    "domain": "climate",
                    "selected": True,
                }
        try:
            data = self.s.api.post("/api/sync/thermostats", self.s.ha_payload({"selected": list(by_id.values())}), timeout=8.0)
            for peer in data.get("thermostats") or []:
                if not isinstance(peer, dict):
                    continue
                eid = str(peer.get("entityId") or peer.get("entity_id") or "").strip()
                if eid.startswith("climate."):
                    by_id[eid] = {
                        "entityId": eid,
                        "name": str(peer.get("name") or peer.get("friendlyName") or peer.get("friendly_name") or eid),
                        "state": str(peer.get("state") or "unknown"),
                        "domain": "climate",
                        "away": bool(peer.get("away")),
                        "doorPauseActive": bool(peer.get("doorPauseActive")),
                        "selected": eid in self.selected_ids() or bool(peer.get("selected")),
                    }
        except Exception as exc:
            if not by_id:
                self._load_error = str(exc)
            else:
                self._load_error = ""
        self.available_peers = sorted(by_id.values(), key=lambda x: str(x.get("name") or x.get("entityId") or "").lower())

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("THERMOSTAT SYNC")
        title.setFont(font(22, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        refresh = RoundButton("Refresh", active=True, min_h=40)
        clear = RoundButton("Clear", active=False, kind="danger", min_h=40)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh)
        header.addWidget(clear)
        root.addLayout(header)

        note = QLabel("Select the other IHA thermostats that should receive mode and setpoint changes while the main-screen Sync button is active. Sync only stays armed for 30 seconds at a time.")
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
        self.refresh()

    def refresh(self):
        while self.body_lay.count():
            item = self.body_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.buttons = {}
        if not self.available_peers:
            detail = getattr(self, "_load_error", "")
            text = "No IHA thermostat climate entities found in Home Assistant."
            if detail:
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
            self.selected_peers.append({"entityId": eid, "name": str(peer.get("name") or eid), "state": str(peer.get("state") or "unknown"), "domain": "climate"})
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
            clean.append({"entityId": eid, "name": str(peer.get("name") or peer.get("friendlyName") or peer.get("friendly_name") or eid), "domain": "climate", "state": str(peer.get("state") or "unknown")})
        self.saved.emit(clean)
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

    PROFILES = {
        "Blinds": ("blinds", "Shade Rooms", "blinds", "Blind", 6),
        "Lights": ("lights", "Light Rooms", "lights", "Light", 12),
        "Room": ("roomControl", "Room Control Rooms", "controls", "Control", 12),
    }

    def __init__(self, state: AppState, page_name: str, parent=None):
        super().__init__(parent)
        self.s = state
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
        if not entries:
            empty = QLabel("No Room entries yet. Go to Rooms, add entries, then assign Home Assistant devices on the Room page.")
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
        try:
            self.s.save_config()
            self.saved.emit()
            # The tapped checkbox already shows the new state. Rebuilding the whole
            # scroll list from inside that checkbox signal can briefly tear down the
            # active widget and make the settings modal appear to disappear/flicker
            # on the Pi touchscreen. The list will be rebuilt when the user changes
            # tabs, sets/clears a code, or closes and reopens settings.
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

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
        try:
            self.s.save_config()
            self.saved.emit()
            self.rebuild_code_entries()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

    def clear_entry_code(self, ctl: dict):
        ctl.pop("accessCode", None)
        ctl.pop("codeRequiredStates", None)
        try:
            self.s.save_config()
            self.saved.emit()
            self.rebuild_code_entries()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

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
        self.s.save_config()
        self.saved.emit()
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
            self.rebuild_code_entries()

    def select_room(self, key: str):
        self.selected_key = key
        self.s.config.setdefault(self.domain, {})["room"] = key
        try:
            self.s.save_config()
            self.saved.emit()
        except Exception:
            pass
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
        try:
            self.save_and_refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

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
        try:
            self.s.save_config()
            self.saved.emit()
            self.rebuild()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

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
        try:
            self.s.save_config()
            self.saved.emit()
            self.rebuild()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))


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



class AudioSettingsDialog(QDialog):
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
        self.options_btn.clicked.connect(lambda checked=False: self.set_scene_mode(False))
        self.set_scenes_btn = RoundButton("Set Scenes", min_h=42)
        self.set_scenes_btn.setMinimumWidth(140)
        self.set_scenes_btn.clicked.connect(lambda checked=False: self.set_scene_mode(True))
        header.addWidget(self.options_btn)
        header.addWidget(self.set_scenes_btn)
        cancel = RoundButton("Cancel", min_h=42)
        cancel.setMinimumWidth(120)
        cancel.clicked.connect(self.reject)
        save = RoundButton("Save", active=True, min_h=42)
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
            "Real music jumps to Audio quickly. If you leave Audio while music keeps playing, the panel returns after 2 minutes without touch. When music stops, it returns to Thermostat after 2 minutes. TV audio is ignored."
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

        self.build_scene_editor()
        self.set_scene_mode(False)
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

    def set_scene_mode(self, enabled: bool):
        self.scene_mode = bool(enabled)
        if hasattr(self, "options_widget"):
            self.options_widget.setVisible(not self.scene_mode)
        if hasattr(self, "scene_panel"):
            self.scene_panel.setVisible(self.scene_mode)
        if hasattr(self, "options_btn"):
            self.options_btn.setActive(not self.scene_mode)
        if hasattr(self, "set_scenes_btn"):
            self.set_scenes_btn.setActive(self.scene_mode)
        self.fit_to_screen()

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
        try:
            self.store_scene_editor_values()
            audio = self.s.config.setdefault("audio", {})
            audio["enabledControls"] = {key: bool(cb.isChecked()) for key, cb in self.checks.items()}
            audio["autoNavigate"] = bool(self.auto_nav.isChecked())
            audio["presets"] = copy.deepcopy(self.preset_edits)
            self.s.save_config()
            self.saved.emit()
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))


class SettingsDialog(QDialog):
    saved = pyqtSignal()
    thermostatUpdateCompleted = pyqtSignal(object)
    settingsSaveCompleted = pyqtSignal(object)

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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setStyleSheet("background:transparent;")
        scroll.viewport().setStyleSheet("background:transparent;")
        self.grid = QGridLayout(body)
        self.grid.setSpacing(4)
        self.grid.setContentsMargins(0, 0, 0, 0)
        for col in range(4):
            self.grid.setColumnStretch(col, 1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self.controls: dict[str, QLabel] = {}
        self.value_control_widgets: dict[str, dict] = {}
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.timeout.connect(self.push_pending_settings)
        self._pending_settings_changes: dict | None = None
        self._pending_settings_quiet = True
        self._settings_saving = False
        self._settings_dirty = False
        self._settings_update_seq = 0
        self._last_settings_error = ""
        self.thermostatUpdateCompleted.connect(self.handle_settings_update_completed)
        self.settingsSaveCompleted.connect(self.handle_save_all_completed)
        self.build()
        self.done.clicked.connect(self.close_settings)
        self.hardware.clicked.connect(self.show_hardware)
        self.history.clicked.connect(self.show_history)
        self.bottom_save.clicked.connect(self.save_all)
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

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
        meta = self.value_control_widgets.get(key) or {}
        panel = meta.get("panel")
        val = meta.get("value")
        minus = meta.get("minus")
        plus = meta.get("plus")
        if not panel or not val:
            return
        seq = int(meta.get("popSeq") or 0) + 1
        meta["popSeq"] = seq
        panel.setMinimumHeight(50)
        panel.setMaximumHeight(58)
        panel.setStyleSheet(self.value_control_style(True))
        val.setMinimumWidth(68)
        val.setFont(font(14, QFont.Black))
        val.setStyleSheet("color:#ffffff; background:rgba(85,240,255,0.16); border:1px solid rgba(85,240,255,0.45); border-radius:10px; padding:2px 6px;")
        for button in (minus, plus):
            if button:
                button.setFixedSize(48, 40)
                button.setFont(font(16, QFont.Black))
        panel.updateGeometry()
        val.repaint()
        QTimer.singleShot(900, lambda k=key, s=seq: self.reset_value_control(k, s))

    def reset_value_control(self, key: str, seq: int | None = None):
        meta = self.value_control_widgets.get(key) or {}
        if seq is not None and int(meta.get("popSeq") or 0) != int(seq):
            return
        panel = meta.get("panel")
        val = meta.get("value")
        minus = meta.get("minus")
        plus = meta.get("plus")
        if panel:
            panel.setMinimumHeight(44)
            panel.setMaximumHeight(50)
            panel.setStyleSheet(self.value_control_style(False))
            panel.updateGeometry()
        if val:
            val.setMinimumWidth(56)
            val.setFont(font(11, QFont.Black))
            val.setStyleSheet("color:#ffffff; background:transparent; border:0;")
        for button in (minus, plus):
            if button:
                button.setFixedSize(40, 34)
                button.setFont(font(13, QFont.Black))
                if hasattr(button, "refresh"):
                    button.refresh()

    def mark_settings_dirty(self):
        self._settings_dirty = True
        self._last_settings_error = ""
        self.s.status_refresh_paused_until = max(getattr(self.s, "status_refresh_paused_until", 0.0), time.monotonic() + 8.0)

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
        minus.pressed.connect(lambda k=key: self.pop_value_control(k))
        plus.pressed.connect(lambda k=key: self.pop_value_control(k))
        minus.clicked.connect(lambda: self.adjust_value(key, -1, low, high, suffix))
        plus.clicked.connect(lambda: self.adjust_value(key, 1, low, high, suffix))
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

    def add_section(self, title: str, row: int, col: int, rowspan: int = 1, colspan: int = 1) -> QFrame:
        p = self.settings_panel(10)
        v = QVBoxLayout(p)
        v.setContentsMargins(7, 4, 7, 5)
        v.setSpacing(3)
        lab = QLabel(title)
        lab.setFont(font(9, QFont.Black))
        lab.setStyleSheet("color:#ffffff; background:transparent; border:0;")
        v.addWidget(lab)
        self.grid.addWidget(p, row, col, rowspan, colspan)
        return p

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
        self.s.config.setdefault("alarm", {})["disarmCode"] = code
        try:
            self.s.save_config()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        if hasattr(self, "security_code_field"):
            self.security_code_field.setText(self.masked_code(code))
        self.saved.emit()

    def edit_settings_code(self):
        code = CodeKeypadDialog.get_code(self, "Settings Code", "New 4-Digit Code")
        if code is None:
            return
        confirmed = CodeKeypadDialog.get_code(self, "Confirm Settings Code", "Re-enter New Code", verify_code=code)
        if confirmed is None:
            return
        self.s.config.setdefault("security", {})["settingsCode"] = code
        try:
            self.s.save_config()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        if hasattr(self, "settings_code_field"):
            self.settings_code_field.setText(self.masked_code(code))
        self.saved.emit()


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
            try:
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
                    })
                ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                ha["syncThermostatEntities"] = copy.deepcopy(clean)
                ha["syncAvailableThermostatEntities"] = copy.deepcopy(clean)
                self.s.save_config()
                if hasattr(self, "sync_peer_summary"):
                    self.sync_peer_summary.setText(self.sync_peer_summary_text())
                    self.sync_peer_summary.repaint()
                top = self.window()
                if hasattr(top, "update_sync_button_state"):
                    top.update_sync_button_state()
                self.saved.emit()
            except Exception as exc:
                QMessageBox.warning(self, "Sync", str(exc))

        dlg.saved.connect(apply)
        dlg.exec_()

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
            try:
                self.s.update_thermostat({"autoAwayPeople": people})
                summary = self.auto_away_people_summary_text()
                if hasattr(self, "people_summary"):
                    self.people_summary.setText(summary)
                    self.people_summary.repaint()
                self.saved.emit()
            except Exception as exc:
                QMessageBox.warning(self, "Auto Away / Home", str(exc))
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
            try:
                self.s.update_thermostat({"people": people})
                summary = self.people_summary_text()
                if hasattr(self, "person_tracking_summary"):
                    self.person_tracking_summary.setText(summary)
                    self.person_tracking_summary.repaint()
                top = self.window()
                thermo_page = getattr(top, "thermostat", None)
                strip = getattr(thermo_page, "person_presence_strip", None)
                if strip is not None:
                    strip.update_people(self.s.thermostat.get("people") or [])
                self.saved.emit()
            except Exception as exc:
                QMessageBox.warning(self, "Person Tracking", str(exc))
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
        ha = self.s.ha()
        stored = []
        if isinstance(ha, dict):
            for key in ("doorAvailableEntities", "pauseFunctionAvailableEntities"):
                if isinstance(ha.get(key), list):
                    stored.extend(ha.get(key) or [])
            current = ha.get("doorEntity")
            if isinstance(current, dict):
                stored.insert(0, current)
        current_entry = self.current_inside_door_entry()
        if isinstance(current_entry, dict):
            stored.insert(0, current_entry)

        entities = []
        try:
            data = self.s.api.post("/api/ha/entities", self.s.ha_payload({"domains": ["binary_sensor", "cover"]}))
            entities = data.get("entities") or []
        except Exception:
            entities = []

        by_id = {}
        # Load saved entries first, then let the live HA entity list overwrite
        # them. That keeps the picker and the saved selection on HA's current
        # friendly_name instead of an older/raw entry label.
        for item in list(stored) + list(entities):
            if not isinstance(item, dict):
                continue
            eid = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not eid:
                continue
            domain = str(item.get("domain") or (eid.split(".", 1)[0] if "." in eid else "")).strip()
            if domain not in {"binary_sensor", "cover"}:
                continue
            name = str(item.get("friendlyName") or item.get("friendly_name") or item.get("name") or eid).strip() or eid
            by_id[eid] = {
                "entityId": eid,
                "name": name,
                "friendlyName": name,
                "domain": domain,
                "state": item.get("state"),
                "deviceClass": item.get("deviceClass") or item.get("device_class") or "",
                "currentPosition": item.get("currentPosition") or item.get("current_position"),
                "isClosed": item.get("isClosed") if isinstance(item.get("isClosed"), bool) else item.get("is_closed"),
            }
        entities = list(by_id.values())
        if not entities:
            QMessageBox.warning(self, "Doors", "No Home Assistant binary_sensor or cover entries found.")
            return

        dlg = EntityPickerDialog("Choose Doors Entry", entities, self)
        def apply(e):
            try:
                eid = str(e.get("entityId") or e.get("entity_id") or "").strip()
                if not eid:
                    return
                domain = str(e.get("domain") or (eid.split(".", 1)[0] if "." in eid else "binary_sensor"))
                selected_name = str(e.get("friendlyName") or e.get("friendly_name") or e.get("name") or eid).strip() or eid
                selected = {
                    "entityId": eid,
                    "name": selected_name,
                    "friendlyName": selected_name,
                    "domain": domain,
                    "state": str(e.get("state") or "unknown"),
                    "deviceClass": str(e.get("deviceClass") or e.get("device_class") or ""),
                    "currentPosition": e.get("currentPosition") or e.get("current_position"),
                    "isClosed": e.get("isClosed") if isinstance(e.get("isClosed"), bool) else e.get("is_closed"),
                }
                ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                ha["doorEntity"] = selected
                available = [selected]
                for item in entities:
                    if isinstance(item, dict) and str(item.get("entityId") or "") != eid:
                        available.append(item)
                ha["doorAvailableEntities"] = available
                ha["pauseFunctionAvailableEntities"] = available
                self.s.save_config()
                duration = self.val_number("doorPauseDurationMinutes") if "doorPauseDurationMinutes" in self.controls else self.current_door_pause_duration()
                self.s.update_thermostat({
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
                })
                if hasattr(self, "inside_door_label"):
                    self.inside_door_label.setText(self.inside_door_summary_text())
                self.saved.emit()
            except Exception as exc:
                QMessageBox.warning(self, "Doors", str(exc))
        dlg.selected.connect(apply)
        dlg.exec_()


    def air_control_mode(self) -> str:
        mode = str((self.s.thermostat or {}).get("airControlMode") or "internal").strip().lower()
        return "external" if mode in {"external", "home-assistant", "ha", "remote"} else "internal"

    def external_air_entity(self, kind: str) -> dict | None:
        def allowed(entry: dict | None) -> dict | None:
            if not isinstance(entry, dict):
                return None
            eid = str(entry.get("entityId") or entry.get("entity_id") or "").strip()
            if not eid or "." not in eid:
                return None
            domain = str(entry.get("domain") or eid.split(".", 1)[0]).strip().lower()
            return entry if domain in {"switch", "input_boolean"} else None

        kind = "heat" if str(kind).lower() == "heat" else "cool"
        t = self.s.thermostat if isinstance(self.s.thermostat, dict) else {}
        key = "externalHeatEntity" if kind == "heat" else "externalCoolEntity"
        entry = allowed(t.get(key))
        if entry:
            return entry
        ha = self.s.ha()
        ha_key = "externalHeatControlEntity" if kind == "heat" else "externalCoolControlEntity"
        return allowed(ha.get(ha_key) if isinstance(ha, dict) else None)

    def external_air_summary_text(self, kind: str) -> str:
        label = "Heat" if str(kind).lower() == "heat" else "Cool"
        entry = self.external_air_entity(kind)
        if not entry:
            return f"{label}: No HA entry selected"
        name = str(entry.get("name") or entry.get("friendly_name") or entry.get("entityId") or f"{label} Entry")
        entity_id = str(entry.get("entityId") or entry.get("entity_id") or "")
        return f"{label}: {name}\n{entity_id}"

    def air_mode_summary_text(self) -> str:
        if self.air_control_mode() == "external":
            return "External mode: heat/cool calls control selected HA entries."
        return "Internal mode: onboard sensors/GPIO; HA heat/cool entries ignored."

    def update_air_control_widgets(self):
        external = self.air_control_mode() == "external"
        if hasattr(self, "air_mode_summary"):
            self.air_mode_summary.setText(self.air_mode_summary_text())
        if hasattr(self, "air_switch_button"):
            self.air_switch_button.setText("Internal / External: EXTERNAL" if external else "Internal / External: INTERNAL")
            if hasattr(self.air_switch_button, "setActive"):
                self.air_switch_button.setActive(external)
        if hasattr(self, "external_heat_label"):
            self.external_heat_label.setText(self.external_air_summary_text("heat") if external else "Heat: not used in Internal mode")
        if hasattr(self, "external_cool_label"):
            self.external_cool_label.setText(self.external_air_summary_text("cool") if external else "Cool: not used in Internal mode")
        for attr in ("choose_external_heat_button", "choose_external_cool_button"):
            if hasattr(self, attr):
                getattr(self, attr).setEnabled(external)

    def toggle_air_control_mode(self):
        next_mode = "internal" if self.air_control_mode() == "external" else "external"
        try:
            self.s.update_thermostat({"airControlMode": next_mode})
            self.update_air_control_widgets()
            self.saved.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Internal / External Air Switch", str(exc))

    def choose_external_air_entry(self, kind: str):
        kind = "heat" if str(kind).lower() == "heat" else "cool"
        label = "Heat" if kind == "heat" else "Cool"
        ha = self.s.ha()
        stored = []
        if isinstance(ha, dict):
            selected_key = "externalHeatControlEntity" if kind == "heat" else "externalCoolControlEntity"
            current = ha.get(selected_key)
            if isinstance(current, dict):
                stored.append(current)
            if isinstance(ha.get("externalAirControlAvailableEntities"), list):
                stored.extend(ha.get("externalAirControlAvailableEntities") or [])
        current_entry = self.external_air_entity(kind)
        if isinstance(current_entry, dict):
            stored.insert(0, current_entry)

        entities = []
        domains = ["switch", "input_boolean"]
        allowed_domains = set(domains)
        try:
            data = self.s.api.post("/api/ha/entities", self.s.ha_payload({"domains": domains}))
            entities = data.get("entities") or []
        except Exception:
            entities = []

        by_id = {}
        for item in list(entities) + list(stored):
            if not isinstance(item, dict):
                continue
            eid = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not eid or "." not in eid:
                continue
            domain = str(item.get("domain") or eid.split(".", 1)[0]).strip().lower()
            if domain not in allowed_domains:
                continue
            by_id[eid] = {
                "entityId": eid,
                "name": str(item.get("name") or item.get("friendly_name") or eid),
                "domain": domain,
                "state": item.get("state"),
            }
        entities = list(by_id.values())
        if not entities:
            QMessageBox.warning(self, f"External {label} Entry", "No Home Assistant switch or input_boolean entries found for external air control.")
            return

        dlg = EntityPickerDialog(f"Choose External {label} Entry", entities, self)
        def apply(e):
            try:
                eid = str(e.get("entityId") or e.get("entity_id") or "").strip()
                if not eid:
                    return
                domain = str(e.get("domain") or (eid.split(".", 1)[0] if "." in eid else "switch")).strip().lower()
                if domain not in allowed_domains:
                    QMessageBox.warning(self, f"External {label} Entry", "External air control can only use switch or input_boolean entries.")
                    return
                selected = {
                    "entityId": eid,
                    "name": str(e.get("name") or e.get("friendly_name") or eid),
                    "domain": domain,
                    "state": str(e.get("state") or "unknown"),
                }
                ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                selected_key = "externalHeatControlEntity" if kind == "heat" else "externalCoolControlEntity"
                thermo_key = "externalHeatEntity" if kind == "heat" else "externalCoolEntity"
                ha[selected_key] = selected
                available = [selected]
                for item in entities:
                    if isinstance(item, dict) and str(item.get("entityId") or "") != eid:
                        available.append(item)
                ha["externalAirControlAvailableEntities"] = available
                self.s.save_config()
                self.s.update_thermostat({"airControlMode": "external", thermo_key: selected})
                self.update_air_control_widgets()
                self.saved.emit()
                try:
                    dlg.accept()
                except Exception:
                    pass
            except Exception as exc:
                QMessageBox.warning(self, f"External {label} Entry", str(exc))
        dlg.selected.connect(apply)
        dlg.exec_()



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
        orientation = normalize_screen_orientation(orientation)
        display = self.s.config.setdefault("display", {})
        if not isinstance(display, dict):
            display = {}
            self.s.config["display"] = display
        display["screenOrientation"] = orientation
        display["xrandrRotation"] = screen_orientation_to_xrandr(orientation)
        self.refresh_screen_orientation_buttons()
        try:
            self.s.save_config()
        except Exception as exc:
            QMessageBox.warning(self, "Screen Rotation", f"Saved locally, but config save failed:\n{exc}")
            return
        ok, detail = self.apply_screen_orientation_now(orientation)
        top = self.window()
        if hasattr(top, "force_panel_geometry"):
            QTimer.singleShot(150, top.force_panel_geometry)
            QTimer.singleShot(550, top.force_panel_geometry)
            QTimer.singleShot(1100, top.force_panel_geometry)
        self.saved.emit()
        if ok:
            self.refresh_screen_orientation_buttons()
        else:
            QMessageBox.warning(self, "Screen Rotation", "The setting was saved, but the live rotation command failed. Rebooting or restarting the native service should apply it.\n\n" + str(detail))

    def build(self):
        t = self.s.thermostat or {}

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

        air_control = self.add_section("Internal / External Air", 2, 0, 1, 2)
        self.air_mode_summary = QLabel(self.air_mode_summary_text())
        self.air_mode_summary.setWordWrap(True)
        self.air_mode_summary.setFont(font(7, QFont.Black))
        self.air_mode_summary.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:8px; padding:4px;")
        air_control.layout().addWidget(self.air_mode_summary)
        self.air_switch_button = RoundButton("Internal / External Air Switch", active=(self.air_control_mode() == "external"), min_h=28)
        self.air_switch_button.clicked.connect(self.toggle_air_control_mode)
        air_control.layout().addWidget(self.air_switch_button)
        air_grid = self.section_grid(air_control, 2)
        self.external_heat_label = QLabel(self.external_air_summary_text("heat"))
        self.external_heat_label.setWordWrap(True)
        self.external_heat_label.setFont(font(7, QFont.Black))
        self.external_heat_label.setStyleSheet("color:#dfe9ff; background:transparent; border:0;")
        self.choose_external_heat_button = RoundButton("Heat Entry", active=True, min_h=27)
        self.choose_external_heat_button.clicked.connect(lambda checked=False: self.choose_external_air_entry("heat"))
        self.external_cool_label = QLabel(self.external_air_summary_text("cool"))
        self.external_cool_label.setWordWrap(True)
        self.external_cool_label.setFont(font(7, QFont.Black))
        self.external_cool_label.setStyleSheet("color:#dfe9ff; background:transparent; border:0;")
        self.choose_external_cool_button = RoundButton("Cool Entry", active=True, min_h=27)
        self.choose_external_cool_button.clicked.connect(lambda checked=False: self.choose_external_air_entry("cool"))
        air_grid.addWidget(self.external_heat_label, 0, 0)
        air_grid.addWidget(self.choose_external_heat_button, 0, 1)
        air_grid.addWidget(self.external_cool_label, 1, 0)
        air_grid.addWidget(self.choose_external_cool_button, 1, 1)
        self.update_air_control_widgets()

        temp_sources = self.add_section("Temperature Sources", 2, 2, 1, 2)
        temp_sources_grid = QGridLayout()
        temp_sources_grid.setContentsMargins(0, 0, 0, 0)
        temp_sources_grid.setHorizontalSpacing(8)
        temp_sources_grid.setVerticalSpacing(5)
        temp_sources.layout().addLayout(temp_sources_grid)

        selected_temp = nested_get(self.s.config, "integrations", "homeAssistant", "currentTempEntity", default=None)
        if isinstance(selected_temp, dict):
            source_name = selected_temp.get("name") or selected_temp.get("friendly_name") or selected_temp.get("entityId") or "Home Assistant Sensor"
            source_line = f"{selected_temp.get('entityId') or 'selected sensor'}"
        else:
            source_name = t.get("currentTempSourceName") or "Virtual Temp"
            source_line = "Virtual until sensor selected"
        self.temp_source_label = QLabel(f"Current: {compact_name(source_name, 26)}\n{source_line}")
        self.temp_source_label.setWordWrap(True)
        self.temp_source_label.setFont(font(7, QFont.Black))
        self.temp_source_label.setStyleSheet("color:#dfe9ff; background:rgba(5,10,20,0.28); border:1px solid rgba(160,180,210,0.16); border-radius:8px; padding:4px;")
        choose = RoundButton("Current", active=True, min_h=28)
        choose.setMinimumWidth(92)
        choose.clicked.connect(self.choose_temp_sensor)

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
        choose_outdoor = RoundButton("Outside", active=True, min_h=28)
        choose_outdoor.setMinimumWidth(92)
        choose_outdoor.clicked.connect(self.choose_outdoor_temp_sensor)

        temp_sources_grid.addWidget(self.temp_source_label, 0, 0)
        temp_sources_grid.addWidget(choose, 1, 0)
        temp_sources_grid.addWidget(self.outdoor_source_label, 0, 1)
        temp_sources_grid.addWidget(choose_outdoor, 1, 1)
        temp_sources_grid.setColumnStretch(0, 1)
        temp_sources_grid.setColumnStretch(1, 1)

        sync_section = self.add_section("Sync", 3, 2, 1, 2)
        self.sync_peer_summary = QLabel(self.sync_peer_summary_text())
        self.sync_peer_summary.setWordWrap(True)
        self.sync_peer_summary.setFont(font(7, QFont.Black))
        self.sync_peer_summary.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:8px; padding:4px;")
        choose_sync = RoundButton("Choose Thermostats", active=True, min_h=28)
        choose_sync.setMinimumWidth(156)
        choose_sync.clicked.connect(self.choose_sync_thermostats)
        sync_row = QHBoxLayout()
        sync_row.setSpacing(6)
        sync_row.addWidget(self.sync_peer_summary, 1)
        sync_row.addWidget(choose_sync)
        sync_section.layout().addLayout(sync_row)
        sync_note = QLabel("When the main Sync button is armed, manual mode and setpoint changes are copied to these selected Home Assistant climate entities for 30 seconds.")
        sync_note.setWordWrap(True)
        sync_note.setFont(font(7, QFont.Black))
        sync_note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        sync_section.layout().addWidget(sync_note)

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

        display = self.add_section("Screen Rotation", 7, 0, 1, 4)
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
        note = QLabel("Uses the two landscape orientations only. Touch is remapped after the screen rotates.")
        note.setWordWrap(True)
        note.setFont(font(7, QFont.Black))
        note.setStyleSheet("color:#9fb0c8; background:transparent; border:0;")
        display.layout().addWidget(note)

        self.grid.setRowStretch(8, 1)

    def val_number(self, key):
        text = self.controls[key].text().split()[0].replace("°", "")
        try:
            return int(float(text))
        except Exception:
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
        self.s.status_refresh_paused_until = max(getattr(self.s, "status_refresh_paused_until", 0.0), time.monotonic() + 8.0)
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
            self.s.status_refresh_paused_until = max(getattr(self.s, "status_refresh_paused_until", 0.0), time.monotonic() + 2.5)
            QTimer.singleShot(0, self.push_pending_settings)
        else:
            self.s.status_refresh_paused_until = time.monotonic() + 0.2

    def close_settings(self):
        self._settings_save_timer.stop()
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

    def save_all(self):
        self._settings_save_timer.stop()
        changes = self.merge_dicts(self._pending_settings_changes, self.build_settings_changes())
        self.apply_thermostat_changes_locally(changes)
        self._pending_settings_changes = None
        self._settings_dirty = False
        self._settings_saving = True
        self._settings_update_seq += 1
        seq = self._settings_update_seq
        self.s.status_refresh_paused_until = max(getattr(self.s, "status_refresh_paused_until", 0.0), time.monotonic() + 8.0)
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
        if hasattr(self, "security_code_field"):
            self.security_code_field.setText(self.masked_code(str((self.s.config.get("alarm") or {}).get("disarmCode") or "")))
        if hasattr(self, "settings_code_field"):
            self.settings_code_field.setText(self.masked_code(str((self.s.config.get("security") or {}).get("settingsCode") or "")))
        self.saved.emit()
        self.show_saved_then_close()

    def choose_temp_sensor(self):
        ha = self.s.ha()
        stored = ha.get("currentTempAvailableEntities") or []
        entities = []
        try:
            data = self.s.api.post("/api/ha/entities", self.s.ha_payload({"domains": ["sensor"]}))
            entities = data.get("entities") or []
        except Exception:
            entities = []

        by_id = {}
        for item in list(entities) + list(stored):
            if not isinstance(item, dict):
                continue
            eid = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not eid:
                continue
            by_id[eid] = {
                "entityId": eid,
                "name": str(item.get("name") or item.get("friendly_name") or eid),
                "domain": str(item.get("domain") or "sensor"),
                "state": item.get("state"),
                "unitOfMeasurement": item.get("unitOfMeasurement") or item.get("unit_of_measurement") or "",
            }
        entities = list(by_id.values())
        if not entities:
            QMessageBox.warning(self, "Failed", "No Home Assistant sensor entities found.")
            return

        dlg = EntityPickerDialog("Choose Temperature Sensor", entities, self)
        def apply(e):
            try:
                eid = str(e.get("entityId") or e.get("entity_id") or "").strip()
                if not eid:
                    return
                selected = {
                    "entityId": eid,
                    "name": str(e.get("name") or e.get("friendly_name") or eid),
                    "domain": str(e.get("domain") or "sensor"),
                    "unitOfMeasurement": str(e.get("unitOfMeasurement") or e.get("unit_of_measurement") or ""),
                }
                ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                # Force replace the selected current temp source. Do not keep
                # using the previous sensor just because it is still in the list.
                ha["currentTempEntity"] = selected
                available = [selected]
                for item in entities:
                    if isinstance(item, dict) and str(item.get("entityId") or "") != eid:
                        available.append(item)
                ha["currentTempAvailableEntities"] = available
                self.s.save_config()
                self.s.update_thermostat({
                    "currentTempSource": "home-assistant",
                    "currentTempSourceName": selected["name"],
                    "runtimeTempSource": "home-assistant",
                    "runtimeTempSourceName": selected["name"],
                })
                if hasattr(self, "temp_source_label"):
                    self.temp_source_label.setText(f"{selected['name']}\nUsing {selected['entityId']}")
                self.saved.emit()
            except Exception as exc:
                QMessageBox.warning(self, "Choose Sensor", str(exc))
        dlg.selected.connect(apply)
        dlg.exec_()

    def choose_outdoor_temp_sensor(self):
        ha = self.s.ha()
        stored = ha.get("weatherAvailableEntities") or []
        existing = ha.get("outdoorTempEntity") or ha.get("weatherEntity")
        if isinstance(existing, dict):
            stored = [existing] + list(stored)
        entities = []
        try:
            data = self.s.api.post("/api/ha/entities", self.s.ha_payload({"domains": ["sensor", "weather"]}))
            entities = data.get("entities") or []
        except Exception:
            entities = []

        by_id = {}
        for item in list(entities) + list(stored):
            if not isinstance(item, dict):
                continue
            eid = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not eid:
                continue
            domain = str(item.get("domain") or (eid.split(".", 1)[0] if "." in eid else "") or "sensor")
            by_id[eid] = {
                "entityId": eid,
                "name": str(item.get("name") or item.get("friendly_name") or eid),
                "domain": domain,
                "state": item.get("state"),
                "unitOfMeasurement": item.get("unitOfMeasurement") or item.get("unit_of_measurement") or "",
            }
        entities = list(by_id.values())
        if not entities:
            QMessageBox.warning(self, "Outside Temperature", "No Home Assistant sensor/weather entries found.")
            return

        dlg = EntityPickerDialog("Choose Outside Temperature", entities, self)
        def apply(e):
            try:
                eid = str(e.get("entityId") or e.get("entity_id") or "").strip()
                if not eid:
                    return
                domain = str(e.get("domain") or (eid.split(".", 1)[0] if "." in eid else "sensor"))
                selected = {
                    "entityId": eid,
                    "name": str(e.get("name") or e.get("friendly_name") or eid),
                    "domain": domain,
                    "unitOfMeasurement": str(e.get("unitOfMeasurement") or e.get("unit_of_measurement") or ""),
                }
                ha = self.s.config.setdefault("integrations", {}).setdefault("homeAssistant", {})
                ha["outdoorTempEntity"] = selected
                if domain == "weather":
                    ha["weatherEntity"] = selected
                available = [selected]
                for item in entities:
                    if isinstance(item, dict) and str(item.get("entityId") or "") != eid:
                        available.append(item)
                ha["weatherAvailableEntities"] = available
                self.s.save_config()
                self.s.update_thermostat({
                    "outdoorTempSource": "home-assistant",
                    "outdoorTempSourceName": selected["name"],
                })
                if hasattr(self, "outdoor_source_label"):
                    self.outdoor_source_label.setText(f"{selected['name']}\\nUsing {selected['entityId']}")
                self.saved.emit()
            except Exception as exc:
                QMessageBox.warning(self, "Outside Temperature", str(exc))
        dlg.selected.connect(apply)
        dlg.exec_()


    def show_hardware(self):
        try:
            info = self.s.api.get("/api/system/info")
            hw = self.s.api.get("/api/hardware/status")
            msg = f"{info.get('thermostatName') or info.get('name')}\n{info.get('address')}\nVersion {info.get('version')}\n{info.get('uptime')}\nThermal {info.get('thermal')}\n\nRelays: {hw.get('relays') or hw}"
            QMessageBox.information(self, "Hardware Information", msg)
        except Exception as exc:
            QMessageBox.warning(self, "Hardware Information", str(exc))

    def show_history(self):
        try:
            hist = self.s.api.get("/api/history")
            items = hist.get("events") or hist.get("history") or []
            if not items:
                msg = "No HVAC history entries yet."
            else:
                msg = "\n".join(str(x)[:180] for x in items[-15:])
            QMessageBox.information(self, "History", msg)
        except Exception as exc:
            QMessageBox.warning(self, "History", str(exc))



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
            self.render_keypad()
        else:
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
        state = self.current_state()
        accent = "#ff4979" if state != "triggered" else "#ff365b"
        title = "Alarm Triggered" if state == "triggered" else "Enter Code"
        self.title.setText(self.header_html(self.state_text(), title, accent))

        self.body.addWidget(self.make_status_panel(
            "Code required to disarm",
            "Enter the alarm disarm code. The panel will send Disarm automatically after four digits.",
            accent,
        ))

        content = QHBoxLayout()
        content.setSpacing(22)

        left = QVBoxLayout()
        left.setSpacing(16)
        left.addStretch(1)
        self.code_display = QLabel("····")
        self.code_display.setAlignment(Qt.AlignCenter)
        self.code_display.setFont(font(42, QFont.Black))
        self.code_display.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,255,255,0.07);
                border:1px solid rgba(255,73,121,0.55);
                border-radius:30px;
                padding:24px 16px;
                letter-spacing:12px;
            }
        """)
        left.addWidget(self.code_display)
        helper = QLabel("DISARM CODE")
        helper.setAlignment(Qt.AlignCenter)
        helper.setFont(font(11, QFont.Black))
        helper.setStyleSheet("color:#ff9cb5; letter-spacing:3px;")
        left.addWidget(helper)
        left.addStretch(1)
        content.addLayout(left, 1)

        keypad = QGridLayout()
        keypad.setHorizontalSpacing(12)
        keypad.setVerticalSpacing(12)
        keys = [
            ("1", 0, 0), ("2", 0, 1), ("3", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("7", 2, 0), ("8", 2, 1), ("9", 2, 2),
            ("⌫", 3, 0), ("0", 3, 1), ("Cancel", 3, 2),
        ]
        for label, row, col in keys:
            b = RoundButton(label, active=(label not in {"⌫", "Cancel"}), min_h=72)
            b.setMinimumWidth(92)
            if label == "Cancel":
                b.setKind("danger")
                b.clicked.connect(self.reject)
            elif label == "⌫":
                b.pressed.connect(self.backspace_code)
            else:
                b.pressed.connect(lambda d=label: self.add_code_digit(d))
            keypad.addWidget(b, row, col)
        content.addLayout(keypad, 1)
        self.body.addLayout(content, 1)
        self.update_code_display()

    def update_code_display(self):
        entered = "•" * len(self.code_buffer)
        remaining = "·" * max(0, 4 - len(self.code_buffer))
        next_text = entered + remaining
        if self.code_display.text() != next_text:
            self.code_display.setText(next_text)
            self.code_display.repaint()

    def add_code_digit(self, digit: str):
        if len(self.code_buffer) >= 4:
            return
        self.code_buffer += digit
        self.update_code_display()
        if len(self.code_buffer) == 4:
            QTimer.singleShot(120, self.auto_disarm)

    def backspace_code(self):
        self.code_buffer = self.code_buffer[:-1]
        self.update_code_display()

    def auto_disarm(self):
        if len(self.code_buffer) != 4:
            return
        expected = str((self.s.config.get("alarm") or {}).get("disarmCode") or "").strip()
        if not expected or self.code_buffer != expected:
            self.invalid_disarm_code()
            return
        self.send_action("disarm", self.code_buffer)

    def invalid_disarm_code(self):
        self.code_buffer = ""
        self.title.setText(self.header_html("INVALID CODE", "Try Again", "#ff4979"))
        self.code_display.setText("••••")
        self.code_display.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,54,91,0.18);
                border:1px solid rgba(255,74,111,0.78);
                border-radius:30px;
                padding:24px 16px;
                letter-spacing:12px;
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
                border-radius:30px;
                padding:24px 16px;
                letter-spacing:12px;
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

    def send_action(self, action: str, code: str = ""):
        if getattr(self, "_closing", False):
            trace_runtime(f"alarm action ignored after close action={action}")
            return
        if self._alarm_action_running:
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
    alarmRefreshCompleted = pyqtSignal(object)
    mainAsyncCompleted = pyqtSignal(object)
    screenBrightnessCompleted = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.api = ApiClient()
        self.s = AppState(self.api)
        self.setWindowTitle("Smart Thermostat Native")
        self.setMinimumSize(1000, 620)
        self.toast = StatusToast(self)
        self.navigation_locked = False
        self._display_sleeping = False
        self._display_wake_block_until = 0.0
        self._last_user_activity_at = time.monotonic()
        self.sleep_overlay = ScreenSleepOverlay(self)
        self.sleep_button = SleepButton(self)
        self.sleep_button.clicked.connect(lambda: self.enter_display_sleep(manual=True))
        self.sync_button = SyncButton(self)
        self.sync_button.clicked.connect(self.toggle_sync_mode)
        self._sync_active_until = 0.0
        self._sync_pending_changes: dict[str, object] = {}
        self._sync_apply_running = False
        self._last_sync_notice_at = 0.0
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
        self.current_name = "Thermostat"
        self.header.set_page(self.current_name)
        self.header.set_locked(self.navigation_locked)
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
        self._last_audio_manual_leave_at = -AUDIO_IDLE_SECONDS
        self._ignore_info_until = 0.0
        self._modal_touch_block_until = 0.0
        self._alarm_dialog_open = False
        self._alarm_reopen_block_until = 0.0
        self._status_refresh_running = False
        self._alarm_refresh_running = False
        self._main_async_jobs: dict[str, tuple[Callable | None, Callable | None]] = {}
        self.statusRefreshCompleted.connect(self._handle_status_refresh_completed)
        self.alarmRefreshCompleted.connect(self._handle_alarm_refresh_completed)
        self.mainAsyncCompleted.connect(self._handle_main_async_completed)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(4000)
        self.alarm_timer = QTimer(self)
        self.alarm_timer.timeout.connect(self.refresh_alarm_state)
        self.alarm_timer.start(int(max(5.0, ALARM_STATE_POLL_SECONDS) * 1000))
        self.auto_nav_timer = QTimer(self)
        self.auto_nav_timer.timeout.connect(self.check_auto_navigation)
        self.auto_nav_timer.start(1000)
        self.screen_sleep_timer = QTimer(self)
        self.screen_sleep_timer.timeout.connect(self.check_display_sleep_idle)
        self.screen_sleep_timer.start(30000)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        QTimer.singleShot(100, self.boot)

    def boot(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if not self.api.wait_until_ready(5):
                self.toast.show_message("Backend is not responding on 127.0.0.1:8080")
            self.s.load()
            try:
                self.s.refresh_alarm_state()
            except Exception:
                pass
            self.sync_runtime_only()
            self.sync_visible_page(self.current_name)
            self.position_sleep_controls()
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
            if hasattr(self, "sleep_overlay"):
                self.sleep_overlay.setGeometry(self.rect())
                if self.sleep_overlay.isVisible():
                    self.sleep_overlay.raise_()
            margin = 22
            sleep_x = max(0, self.width() - self.sleep_button.width() - margin) if hasattr(self, "sleep_button") else 0
            sleep_y = max(0, self.height() - self.sleep_button.height() - margin) if hasattr(self, "sleep_button") else 0
            if hasattr(self, "sleep_button"):
                self.sleep_button.move(sleep_x, sleep_y)
            if hasattr(self, "sync_button"):
                show_sync = (
                    not getattr(self, "_display_sleeping", False)
                    and self.current_name == "Thermostat"
                    and bool(self.sync_peer_entities())
                )
                self.sync_button.setVisible(show_sync)
                if show_sync:
                    self.sync_button.move(sleep_x, max(0, sleep_y - self.sync_button.height() - 10))
                    self.sync_button.raise_()
            if hasattr(self, "sleep_button") and not getattr(self, "_display_sleeping", False):
                self.sleep_button.raise_()
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
                })
            return clean
        except Exception:
            return []

    def sync_is_active(self) -> bool:
        return bool(self.sync_peer_entities()) and time.monotonic() < float(getattr(self, "_sync_active_until", 0.0) or 0.0)

    def update_sync_button_state(self):
        try:
            peers = self.sync_peer_entities()
            active = bool(peers) and time.monotonic() < float(getattr(self, "_sync_active_until", 0.0) or 0.0)
            if not active:
                self._sync_active_until = 0.0
                if not getattr(self, "_sync_apply_running", False):
                    self._sync_pending_changes = {}
            remaining = max(0, int(math.ceil(float(getattr(self, "_sync_active_until", 0.0) or 0.0) - time.monotonic()))) if active else 0
            if hasattr(self, "sync_button"):
                self.sync_button.setActive(active, remaining)
            self.position_sleep_controls()
        except Exception:
            pass

    def toggle_sync_mode(self):
        peers = self.sync_peer_entities()
        if self.sync_is_active():
            self._sync_active_until = 0.0
            self._sync_pending_changes = {}
            self.peer_sync_timer.stop()
            self.toast.show_message("Sync off")
            self.update_sync_button_state()
            return
        if not peers:
            self.toast.show_message("Choose Sync thermostats in Settings")
            self.update_sync_button_state()
            return
        self._sync_active_until = time.monotonic() + 30.0
        self._sync_pending_changes = {}
        self.toast.show_message(f"Sync armed for 30 seconds · {len(peers)} thermostat{'s' if len(peers) != 1 else ''}")
        self.update_sync_button_state()

    def request_peer_sync(self, changes: dict):
        if not self.sync_is_active():
            return
        if not isinstance(changes, dict):
            return
        allowed: dict[str, object] = {}
        mode = str(changes.get("mode") or changes.get("hvacMode") or "").strip().lower()
        if mode in {"off", "heat", "cool", "auto"}:
            allowed["mode"] = mode
        if "targetTemp" in changes:
            try:
                allowed["targetTemp"] = int(clamp(round(float(changes.get("targetTemp"))), 45, 95))
            except Exception:
                pass
        if not allowed:
            return
        self._sync_pending_changes.update(allowed)
        self.peer_sync_timer.start(250)
        self.update_sync_button_state()

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
            self.peer_sync_timer.start(300)
            return
        if not self.sync_is_active():
            self._sync_pending_changes = {}
            self.update_sync_button_state()
            return
        peers = self.sync_peer_entities()
        changes = dict(getattr(self, "_sync_pending_changes", {}) or {})
        if not peers or not changes:
            return
        self._sync_pending_changes = {}
        payload = {
            "entityIds": [peer.get("entityId") for peer in peers],
        }
        payload.update(changes)
        self._sync_apply_running = True

        def done(result):
            self._sync_apply_running = False
            info = result if isinstance(result, dict) else {}
            skipped = info.get("skipped") if isinstance(info.get("skipped"), list) else []
            errors = info.get("errors") if isinstance(info.get("errors"), list) else []
            if errors:
                self.show_sync_notice("Sync issue: " + str(errors[0])[:80], 2600)
            elif skipped:
                self.show_sync_notice("Sync skipped protected thermostat(s)", 2200)
            if self._sync_pending_changes and self.sync_is_active():
                self.peer_sync_timer.start(250)
            self.update_sync_button_state()

        def failed(err):
            self._sync_apply_running = False
            self.show_sync_notice(f"Sync failed: {err}", 3000)
            self.update_sync_button_state()

        self.run_async("peer-sync", lambda: self.s.api.post("/api/sync/apply", self.s.ha_payload(payload), timeout=8.0), done, failed)

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
            if event_type in self.display_input_event_types():
                if getattr(self, "_display_sleeping", False):
                    self.wake_display_screen()
                    return True
                if now < getattr(self, "_display_wake_block_until", 0.0):
                    return True
                if now < getattr(self, "_modal_touch_block_until", 0.0):
                    return True
                if event_type in self.display_activity_event_types():
                    self._last_user_activity_at = now
            if event_type in self.edge_brightness_event_types():
                if getattr(self, "_display_sleeping", False):
                    return False
                if now < getattr(self, "_display_wake_block_until", 0.0):
                    return True
                if self.handle_edge_brightness_event(event_type, event):
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
        if getattr(self, "_display_sleeping", False):
            return
        self._display_sleeping = True
        self._display_wake_block_until = 0.0
        self.position_sleep_controls()
        self.sleep_overlay.show()
        self.sleep_overlay.raise_()
        # Give Qt one paint cycle to place the black shield before DPMS turns the
        # panel off. If DPMS is unsupported, the black shield still prevents burn-in
        # and blocks accidental touches.
        QTimer.singleShot(120, lambda: self._run_display_power_command(SCREEN_SLEEP_OFF_COMMAND))

    def wake_display_screen(self):
        if not getattr(self, "_display_sleeping", False):
            return
        self._display_sleeping = False
        self._display_wake_block_until = time.monotonic() + SCREEN_WAKE_INPUT_BLOCK_SECONDS
        self._last_user_activity_at = time.monotonic()
        self._run_display_power_command(SCREEN_SLEEP_ON_COMMAND)
        self.sleep_overlay.hide()
        self.position_sleep_controls()
        # Some display stacks reset gamma/backlight state after DPMS wake. Reapply
        # the last edge-gesture brightness shortly after the panel comes back.
        QTimer.singleShot(250, lambda: self.set_screen_brightness_percent(getattr(self, "_screen_brightness_pct", SCREEN_BRIGHTNESS_DEFAULT_PERCENT), show_feedback=False))
        QTimer.singleShot(int(SCREEN_WAKE_INPUT_BLOCK_SECONDS * 1000), self.finish_display_wake)

    def finish_display_wake(self):
        self._display_wake_block_until = 0.0
        self._last_user_activity_at = time.monotonic()
        self.position_sleep_controls()

    def check_display_sleep_idle(self):
        try:
            if getattr(self, "_display_sleeping", False):
                return
            if SCREEN_SLEEP_IDLE_SECONDS <= 0:
                return
            if QApplication.activeModalWidget() is not None:
                return
            if time.monotonic() - getattr(self, "_last_user_activity_at", time.monotonic()) >= SCREEN_SLEEP_IDLE_SECONDS:
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
        if getattr(self, "navigation_locked", False) and name != "Thermostat" and not force:
            self.current_name = "Thermostat"
            self.stack.setCurrentWidget(self.pages["Thermostat"])
            self.header.set_page("Thermostat")
            self.toast.show_message("Locked to Thermostat")
            QTimer.singleShot(60, lambda: self.sync_visible_page("Thermostat"))
            return
        previous_name = self.current_name
        now = time.monotonic()
        if previous_name == "Audio" and name != "Audio" and not force and self.audio_page_is_music_playing():
            self._last_audio_manual_leave_at = now
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
        QTimer.singleShot(0, self.update_sync_button_state)



    def settings_code(self) -> str:
        security = self.s.config.get("security") or {}
        alarm = self.s.config.get("alarm") or {}
        code = str(security.get("settingsCode") or alarm.get("settingsCode") or alarm.get("disarmCode") or "").strip()
        # The appliance has historically used 3762 as the panel/settings code.
        # Keep it as a safe fallback for page unlocks when older configs do not
        # yet have security.settingsCode saved.
        return code or "3762"

    def alarm_disarm_code(self) -> str:
        alarm = self.s.config.get("alarm") or {}
        return str(alarm.get("disarmCode") or "").strip()

    def set_navigation_locked(self, locked: bool, *, show_toast: bool = False):
        self.navigation_locked = bool(locked)
        self.header.set_locked(self.navigation_locked)
        if self.navigation_locked:
            if self.current_name != "Thermostat":
                self.set_page("Thermostat", force=True)
            else:
                self.header.set_page("Thermostat")
            if show_toast:
                self.toast.show_message("Locked to Thermostat")
        elif show_toast:
            self.toast.show_message("Page lock released")

    def toggle_navigation_lock(self):
        if not getattr(self, "navigation_locked", False):
            self.set_navigation_locked(True, show_toast=True)
            return
        code = self.alarm_disarm_code()
        if code:
            entered = CodeKeypadDialog.get_code(self, "Screen Locked", "Enter Alarm Disarm Code", code)
            if entered is None:
                return
        self.set_navigation_locked(False, show_toast=True)

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
        fresh = data.get("alarm")
        if not isinstance(fresh, dict) or not fresh:
            return
        self.s.apply_alarm_state(fresh)
        page = self.pages.get("Thermostat")
        if isinstance(page, ThermostatScreen):
            page.apply_alarm_state_refresh(fresh)
        if self.current_name == "Thermostat":
            self.sync_visible_page("Thermostat")

    def refresh_status(self):
        if getattr(self, "_status_refresh_running", False):
            return
        self._status_refresh_running = True
        api = self.s.api

        def worker():
            try:
                data = api.thermostat_status()
                self.statusRefreshCompleted.emit({"data": data, "error": None})
            except Exception as exc:
                self.statusRefreshCompleted.emit({"data": None, "error": str(exc)})

        threading.Thread(target=worker, name="thermostat-status-refresh", daemon=True).start()

    def _handle_status_refresh_completed(self, info: object):
        self._status_refresh_running = False
        data = info if isinstance(info, dict) else {}
        if data.get("error"):
            return
        if time.monotonic() < getattr(self.s, "status_refresh_paused_until", 0.0):
            return
        status = data.get("data")
        if isinstance(status, dict):
            self.s.ingest_thermostat(status)
            self.sync_runtime_only()

    def sync_runtime_only(self):
        t = self.s.thermostat or {}
        self.header.update_values(t.get("currentTemp"), t.get("targetTemp"))
        self.sync_visible_page()
        self.update_sync_button_state()

    def reload_all(self):
        try:
            self.s.load()
            self.sync_runtime_only()
            self.update_sync_button_state()
        except Exception as exc:
            self.toast.show_message(f"Reload failed: {exc}")

    def sync_all(self):
        t = self.s.thermostat or {}
        self.header.update_values(t.get("currentTemp"), t.get("targetTemp"))
        for p in self.pages.values():
            p.sync(self.s.config, self.s.thermostat)

    def poll(self):
        try:
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
        if group == "audio-media-player":
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
        if group == "audio-media-player":
            return ha.get("mediaPlayerEntities") or (ha.get("audioAvailableEntities") or {}).get("mediaPlayers") or []
        return []

    def assign_entity(self, group: str, obj: dict, kind: str):
        entities = self.cached_entities_for(group)
        try:
            data = self.s.api.post("/api/ha/entities", self.s.ha_payload({"domains": self.domains_for(group)}))
            fresh = data.get("entities") or []
            if fresh:
                entities = fresh
        except Exception:
            pass
        if not entities:
            self.toast.show_message("No Home Assistant entities available")
            return
        display_kind = str(kind or "entry").replace("_", " ").replace("subwoofer", "sub").title()
        dlg = EntityPickerDialog(f"Assign {display_kind} Entity", entities, self)

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
                    self.s.save_config()
                    self.sync_runtime_only()
                    self.toast.show_message(f"Assigned {record.get('name') or entity_id}")
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
                self.s.save_config()
                self.sync_runtime_only()
                self.toast.show_message(f"Assigned {obj.get('haName') or obj.get('name')}")
            except Exception as exc:
                self.toast.show_message(f"Save failed: {exc}")
        dlg.selected.connect(apply)
        dlg.exec_()

    def show_settings(self):
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
                dlg = SettingsDialog(self.s, self)
                dlg.saved.connect(self.reload_all)
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
            # Touchscreens can emit a second tap/release after the modal closes.
            # Block immediate re-entry so the settings keypad does not pop back up.
            self._settings_reopen_block_until = time.monotonic() + 2.0

    def show_info(self):
        now = time.monotonic()
        if now < getattr(self, "_ignore_info_until", 0):
            return
        if getattr(self, "_info_dialog_open", False):
            return
        if now < getattr(self, "_info_reopen_block_until", 0):
            return
        self._info_dialog_open = True
        try:
            settings_code = self.settings_code()
            if settings_code:
                entered = CodeKeypadDialog.get_code(self, "Info Locked", "Enter Settings Code", settings_code)
                if entered is None:
                    self._info_reopen_block_until = time.monotonic() + 1.5
                    return
            info = self.s.api.get("/api/system/info")
            dlg = QDialog(self)
            dlg.setModal(True)
            dlg.setWindowTitle("Thermostat Info")
            dlg.setFixedSize(660, 500)
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

            title = QLabel("<span style='color:#46e8ff; letter-spacing:4px; font-size:11px; font-weight:900'>THERMOSTAT</span><br><span style='font-size:31px; font-weight:1000; color:#ffffff'>Panel Information</span>")
            title.setTextFormat(Qt.RichText)
            root.addWidget(title)

            grid_panel = GlassPanel(radius=22)
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
                grid.addWidget(l, row, 0)
                grid.addWidget(v, row, 1)
            root.addWidget(grid_panel, 1)

            button_grid = QGridLayout()
            button_grid.setHorizontalSpacing(10)
            button_grid.setVerticalSpacing(10)
            fetch = RoundButton("Fetch Update", active=True, min_h=46)
            reboot = RoundButton("Restart", kind="purple", min_h=46)
            backup = RoundButton("Backup Config", active=False, min_h=46)
            close = RoundButton("Close", active=False, min_h=46)

            for b in [fetch, reboot, backup, close]:
                b.setMinimumWidth(156)
            button_grid.addWidget(fetch, 0, 0)
            button_grid.addWidget(reboot, 0, 1)
            button_grid.addWidget(backup, 0, 2)
            button_grid.addWidget(close, 0, 3)
            root.addLayout(button_grid)

            fetch.clicked.connect(lambda: (dlg.accept(), self.do_fetch_update()))
            reboot.clicked.connect(lambda: (dlg.accept(), self.do_restart()))
            backup.clicked.connect(lambda: (dlg.accept(), self.backup_config()))
            close.clicked.connect(dlg.accept)
            dlg.exec_()
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
