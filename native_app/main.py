#!/usr/bin/env python3
from __future__ import annotations

import copy
import math
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# Allow running from this folder without installing a package.
APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QFont, QIcon, QImage, QPainter, QPen, QBrush, QLinearGradient, QPainterPath, QRadialGradient
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractButton,
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


def as_bool_state(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").lower()
    return text in {"on", "open", "opening", "playing", "heat", "cool", "true", "1"}


def nested_get(data: dict, *keys, default=None):
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


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

    def ha(self) -> dict:
        return nested_get(self.config, "integrations", "homeAssistant", default={}) or {}

    def ha_payload(self, extra: dict | None = None) -> dict:
        return ApiClient.ha_payload(self.config, extra)

    def refresh_alarm_state(self):
        """Read the current Alarmo/HA alarm state instead of trusting saved config."""
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
        fresh = alarms[0]
        entity.update(fresh)
        ha["alarmEntity"] = entity
        return fresh

    def save_config(self):
        record = self.api.save_config(self.config)
        self.config = record.get("config") or self.config
        return record

    def load(self):
        rec = self.api.get_config_record()
        self.config = rec.get("config") or {}
        self.thermostat = self.api.thermostat_status()
        try:
            self.system_info = self.api.get("/api/system/info")
        except Exception:
            self.system_info = {}

    def refresh_status(self):
        self.thermostat = self.api.thermostat_status()
        return self.thermostat

    def update_thermostat(self, changes: dict):
        self.thermostat = self.api.thermostat_update(changes)
        return self.thermostat


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
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(10)
        buttons.addStretch(1)
        buttons.addWidget(self.dismiss)
        buttons.addWidget(self.revert)
        buttons.addWidget(self.bypass)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(7)
        layout.addWidget(self.title)
        layout.addWidget(self.body)
        layout.addLayout(buttons)
        self.dismiss.clicked.connect(self.dismissClicked.emit)
        self.revert.clicked.connect(self.revertClicked.emit)
        self.bypass.clicked.connect(self.bypassClicked.emit)
        self.hide()

    def set_alert(self, kind: str, title: str, body: str, *, dismiss=False, revert=False, bypass=False, dismiss_text="Dismiss", revert_text="Revert", bypass_text="Bypass"):
        self.kind = kind or "info"
        if self.kind == "door-pause":
            self.setMinimumHeight(168)
            self.setMaximumHeight(238)
            self.title.setFont(font(22, QFont.Black))
            self.body.setFont(font(13, QFont.Black))
            self.bypass.setFixedWidth(190)
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
        rows = [list("QWERTYUIOP"), list("ASDFGHJKL"), list("ZXCVBNM")]
        for letters in rows:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addStretch(1)
            for ch in letters:
                b = RoundButton(ch, active=True, min_h=42)
                b.setFixedSize(52, 42)
                b.clicked.connect(lambda checked=False, c=ch: self.add_char(c))
                row.addWidget(b)
            row.addStretch(1)
            root.addLayout(row)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        space = RoundButton("Space", active=False, min_h=46)
        back = RoundButton("⌫", active=False, min_h=46)
        clear = RoundButton("Clear", active=False, min_h=46)
        cancel = RoundButton("Cancel", active=False, kind="danger", min_h=46)
        done = RoundButton("Done", active=True, min_h=46)
        space.clicked.connect(lambda: self.add_char(" "))
        back.clicked.connect(self.backspace)
        clear.clicked.connect(self.clear_text)
        cancel.clicked.connect(self.reject)
        done.clicked.connect(self.accept)
        bottom.addWidget(space)
        bottom.addWidget(back)
        bottom.addWidget(clear)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(done)
        root.addLayout(bottom)
        self.refresh()

    def refresh(self):
        self.display.setText(self.result_text or " ")

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
        self.setFixedSize(780, 620)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        self.name = str(self.schedule.get("name") or "Morning")
        self.hour = 7
        self.minute = 0
        raw_time = str(self.schedule.get("time") or "07:00")
        try:
            h, m = raw_time.split(":", 1)
            self.hour = max(0, min(23, int(h)))
            self.minute = max(0, min(59, int(m)))
        except Exception:
            pass
        self.cool = int(float(self.schedule.get("coolSetpoint") or 72))
        self.heat = int(float(self.schedule.get("heatSetpoint") or 68))
        self.enabled = bool(self.schedule.get("enabled", True))
        self.person_ids = [str(x) for x in (self.schedule.get("personEntityIds") or []) if str(x)]
        self.available_people: list[dict] = []
        self.load_people()
        self.build()

    def load_people(self):
        saved_people = self.s.thermostat.get("people") if isinstance(self.s.thermostat, dict) else []
        if isinstance(saved_people, list):
            for p in saved_people:
                if isinstance(p, dict) and p.get("entityId"):
                    self.available_people.append(p)
        try:
            data = self.s.api.post("/api/ha/entities", self.s.ha_payload({"domains": ["person"]}))
            for p in data.get("entities") or []:
                eid = str(p.get("entityId") or "")
                if eid and all(str(x.get("entityId")) != eid for x in self.available_people):
                    self.available_people.append(p)
        except Exception:
            pass

    def build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        title = QLabel("SCHEDULE")
        title.setFont(font(24, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        root.addWidget(title)

        top = QGridLayout()
        top.setHorizontalSpacing(12)
        top.setVerticalSpacing(10)
        self.name_btn = RoundButton(self.name, active=True, min_h=52)
        self.name_btn.clicked.connect(self.edit_name)
        top.addWidget(QLabel("Name"), 0, 0)
        top.addWidget(self.name_btn, 0, 1, 1, 3)

        self.time_label = QLabel("")
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setFont(font(22, QFont.Black))
        self.time_label.setStyleSheet("background:rgba(255,255,255,0.07); border:1px solid rgba(85,240,255,0.25); border-radius:16px; padding:8px;")
        top.addWidget(QLabel("Time"), 1, 0)
        top.addWidget(self.time_label, 1, 1)
        for text_value, delta_h, delta_m, col in [("Hour −", -1, 0, 2), ("Hour +", 1, 0, 3), ("Min −", 0, -5, 2), ("Min +", 0, 5, 3)]:
            b = RoundButton(text_value, active=False, min_h=42)
            b.clicked.connect(lambda checked=False, dh=delta_h, dm=delta_m: self.adjust_time(dh, dm))
            top.addWidget(b, 1 if delta_m == 0 else 2, col)
        top.addWidget(QLabel(""), 2, 0)
        root.addLayout(top)

        target_row = QHBoxLayout()
        target_row.setSpacing(12)
        target_row.addWidget(self.target_control("Cool Target", "cool", 45, 95))
        target_row.addWidget(self.target_control("Heat Target", "heat", 45, 95))
        root.addLayout(target_row)

        people_panel = GlassPanel(radius=18)
        people_lay = QVBoxLayout(people_panel)
        people_lay.setContentsMargins(12, 10, 12, 10)
        people_lay.setSpacing(8)
        hdr = QHBoxLayout()
        hdr.addWidget(QLabel("Only run if these people are home"))
        hdr.addStretch(1)
        add = RoundButton("+ Person", active=True, min_h=38)
        add.clicked.connect(self.add_person)
        hdr.addWidget(add)
        people_lay.addLayout(hdr)
        self.people_box = QVBoxLayout()
        people_lay.addLayout(self.people_box)
        root.addWidget(people_panel, 1)

        bottom = QHBoxLayout()
        self.enabled_btn = RoundButton("Enabled", active=self.enabled, min_h=48)
        self.enabled_btn.clicked.connect(self.toggle_enabled)
        cancel = RoundButton("Cancel", active=False, kind="danger", min_h=48)
        save = RoundButton("Save", active=True, min_h=48)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.save)
        bottom.addWidget(self.enabled_btn)
        bottom.addStretch(1)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        root.addLayout(bottom)
        self.refresh()

    def target_control(self, title: str, attr: str, low: int, high: int) -> QWidget:
        panel = GlassPanel(radius=18)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lab = QLabel(title)
        lab.setFont(font(12, QFont.Black))
        lay.addWidget(lab, 0, Qt.AlignCenter)
        val = QLabel("")
        val.setObjectName(attr + "_value")
        val.setAlignment(Qt.AlignCenter)
        val.setFont(font(30, QFont.Black))
        val.setStyleSheet("background:transparent; border:0;")
        lay.addWidget(val)
        row = QHBoxLayout()
        minus = RoundButton("−", active=False, min_h=42)
        plus = RoundButton("+", active=True, min_h=42)
        minus.clicked.connect(lambda: self.adjust_target(attr, -1, low, high))
        plus.clicked.connect(lambda: self.adjust_target(attr, 1, low, high))
        row.addWidget(minus)
        row.addWidget(plus)
        lay.addLayout(row)
        return panel

    def refresh(self):
        self.name_btn.setText(self.name)
        self.time_label.setText(f"{self.hour:02d}:{self.minute:02d}")
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
        if not self.person_ids:
            none = QLabel("No people selected. This schedule runs every day at the set time.")
            none.setWordWrap(True)
            none.setStyleSheet("color:#c4d0e5; background:rgba(255,255,255,0.05); border-radius:10px; padding:8px;")
            self.people_box.addWidget(none)
        for eid in self.person_ids:
            row = QHBoxLayout()
            name = self.person_name(eid)
            lab = QLabel(f"{name}\n{eid}")
            lab.setFont(font(10, QFont.Black))
            lab.setStyleSheet("color:#e8f1ff; background:transparent; border:0;")
            remove = RoundButton("Remove", active=False, kind="danger", min_h=34)
            remove.clicked.connect(lambda checked=False, x=eid: self.remove_person(x))
            row.addWidget(lab, 1)
            row.addWidget(remove)
            self.people_box.addLayout(row)

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

    def adjust_target(self, attr: str, delta: int, low: int, high: int):
        if attr == "cool":
            self.cool = int(clamp(self.cool + delta, low, high))
        else:
            self.heat = int(clamp(self.heat + delta, low, high))
        self.refresh()

    def toggle_enabled(self):
        self.enabled = not self.enabled
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
        self.setFixedSize(760, 620)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
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

    def schedules(self) -> list[dict]:
        t = self.s.thermostat or {}
        schedules = t.get("schedules") if isinstance(t.get("schedules"), list) else []
        return copy.deepcopy(schedules)

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
        people_text = "Runs every day" if not people else f"{len(people)} person{'s' if len(people) != 1 else ''} required"
        text = QLabel(f"<b>{sched.get('name') or 'Schedule'}</b><br>{sched.get('time') or '--:--'} • Cool {sched.get('coolSetpoint')}° • Heat {sched.get('heatSetpoint')}°<br>{people_text}")
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
        self.s.update_thermostat({"schedules": schedules})
        self.changed.emit()
        self.refresh()

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
            self.s.update_thermostat({"targetTemp": int(float(target)), "lastComfortTarget": int(float(target))})
            self.changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Schedule", str(exc))



class ThermostatScreen(Page):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(app_state, parent)
        self.dial = ThermostatDial()
        self.dial.setMaximumSize(470, 470)
        self.mode_buttons: dict[str, RoundButton] = {}
        self.fan_buttons: dict[str, RoundButton] = {}
        self.fan_status_button: RoundButton | None = None
        self.status_badge = QLabel("●  Auto • Cool • Idle")
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
        self.notice = QLabel("")
        self.notice.setAlignment(Qt.AlignCenter)
        self.notice.setFont(font(12, QFont.Black))
        self.notice.setCursor(Qt.PointingHandCursor)
        self.notice.setMinimumSize(220, 96)
        self.notice.setMaximumWidth(260)
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("background:rgba(2,78,130,0.65); color:#f6f8ff; border:1px solid rgba(71,224,255,0.45); border-radius:22px; padding:12px 18px;")
        self.notice.mousePressEvent = lambda event: self.show_auto_switch_menu()
        self.bypass_pill = RoundButton("Bypass", active=True, kind="purple", min_h=42)
        self.bypass_pill.setFixedWidth(180)
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

        root = QVBoxLayout(self)
        root.setContentsMargins(42, 18, 42, 34)
        root.setSpacing(0)
        title_row = QHBoxLayout()
        left_title = QVBoxLayout()
        left_title.setSpacing(8)
        left_title.addWidget(self.outdoor, 0, Qt.AlignLeft)
        title = QLabel("Climate Control")
        title.setFont(font(49, QFont.Black))
        title.setStyleSheet("color:#ffffff;")
        left_title.addWidget(title)
        title_row.addLayout(left_title)
        title_row.addStretch(1)
        title_row.addWidget(self.door_countdown, 0, Qt.AlignRight | Qt.AlignTop)
        root.addLayout(title_row)

        mid = QGridLayout()
        mid.setHorizontalSpacing(20)
        mid.setVerticalSpacing(10)
        mid.setColumnStretch(0, 3)
        mid.setColumnStretch(1, 1)
        mid.setColumnStretch(2, 5)
        mid.setColumnStretch(3, 1)
        mid.setColumnStretch(4, 3)

        # Side cards are intentionally aligned in matching vertical lanes.
        # The original web UI keeps Inside Doors and Alarmo horizontally lined
        # up even when the auto-switch notice is hidden. A normal vertical
        # layout collapses hidden widgets and pushed the door tile too high,
        # making the native UI look uneven. These fixed-height top lanes reserve
        # the same visual space on both sides before the status cards.
        side_top_h = 188

        left_col = QVBoxLayout()
        left_col.setSpacing(12)
        left_top = QWidget()
        left_top.setFixedHeight(side_top_h)
        left_top_lay = QVBoxLayout(left_top)
        left_top_lay.setContentsMargins(0, 0, 0, 0)
        left_top_lay.addStretch(1)
        left_top_lay.addWidget(self.bypass_pill, 0, Qt.AlignCenter)
        left_top_lay.addWidget(self.notice, 0, Qt.AlignCenter)
        left_col.addWidget(left_top)
        left_col.addWidget(self.door_card, 0, Qt.AlignCenter)
        left_col.addStretch(1)
        self.schedule_shortcuts = QWidget()
        self.schedule_shortcuts.setMaximumWidth(238)
        self.schedule_shortcuts_lay = QHBoxLayout(self.schedule_shortcuts)
        self.schedule_shortcuts_lay.setContentsMargins(0, 0, 0, 0)
        self.schedule_shortcuts_lay.setSpacing(6)
        left_col.addWidget(self.schedule_shortcuts, 0, Qt.AlignLeft)
        self.schedule_button = RoundButton("◷", active=False, min_h=46)
        self.schedule_button.setFixedSize(50, 46)
        self.schedule_button.setFont(font(22, QFont.Black))
        self.schedule_button.clicked.connect(self.open_schedule_manager)
        left_col.addWidget(self.schedule_button, 0, Qt.AlignLeft)
        mid.addLayout(left_col, 0, 0, 2, 1)
        mid.addWidget(self.minus, 0, 1, 2, 1, Qt.AlignCenter)

        center = QVBoxLayout()
        center.setSpacing(8)
        center.addWidget(self.status_badge, 0, Qt.AlignCenter)
        center.addWidget(self.dial, 1, Qt.AlignCenter)
        center.addLayout(self._mode_bar())
        mid.addLayout(center, 0, 2, 2, 1)
        mid.addWidget(self.plus, 0, 3, 2, 1, Qt.AlignCenter)

        right_col = QVBoxLayout()
        right_col.setSpacing(12)
        right_top = QWidget()
        right_top.setFixedHeight(side_top_h)
        right_top_lay = QVBoxLayout(right_top)
        right_top_lay.setContentsMargins(0, -34, 0, 0)
        right_top_lay.addWidget(self.virtual_panel, 0, Qt.AlignRight | Qt.AlignTop)
        right_top_lay.addStretch(1)
        right_col.addWidget(right_top)
        right_col.addWidget(self.alarm_card, 0, Qt.AlignCenter)
        right_col.addStretch(1)
        fan_bar = self._fan_bar()
        right_col.addLayout(fan_bar)
        mid.addLayout(right_col, 0, 4, 2, 1)
        root.addLayout(mid, 1)

        self.minus.clicked.connect(lambda: self.change_target(-1))
        self.plus.clicked.connect(lambda: self.change_target(1))
        self.dial.targetChanged.connect(lambda v: self.set_target(v))
        self.door_card.clicked.connect(lambda: self.requestToast.emit("Door status is synced from Home Assistant."))
        self.alarm_card.clicked.connect(self.show_alarm_dialog)

        self.fx_phase = 0
        self.alert_banner = ThermostatActionBanner(self)
        self.alert_banner.dismissClicked.connect(self.dismiss_auto_switch)
        self.alert_banner.revertClicked.connect(self.revert_auto_switch)
        self.alert_banner.bypassClicked.connect(self.bypass_changeover_lockout)
        self.fx_timer = QTimer(self)
        self.fx_timer.timeout.connect(self.animate_environment)
        # Fast enough for visible snow/heat movement, still light enough for the Pi
        # because only this thermostat page repaints while it is shown.
        self.fx_timer.start(240)
        self.notice.hide()
        QTimer.singleShot(0, self.position_alert_banner)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "fx_timer") and not self.fx_timer.isActive():
            self.fx_timer.start(240)

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

    def animate_environment(self):
        self.fx_phase = (self.fx_phase + 1) % 10000
        self.update_door_pause_ui()
        self.update()
        if self.alert_banner.isVisible():
            self.alert_banner.raise_()

    def safe_float(self, value, default=0.0):
        try:
            return float(value)
        except Exception:
            return default

    def active_visual_mode(self) -> str:
        t = self.thermostat_view()
        outputs = t.get("outputs") if isinstance(t.get("outputs"), dict) else {}
        safety = str(t.get("safetyMode") or outputs.get("safetyMode") or "").lower()
        if safety in {"heat", "cool"}:
            return safety
        mode = str(t.get("mode") or "cool").lower()
        if mode in {"heat", "cool"}:
            return mode
        active = str(t.get("autoActiveMode") or t.get("activeMode") or "cool").lower()
        return active if active in {"heat", "cool"} else "cool"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_alert_banner()
        self.position_away_overlay()

    def position_away_overlay(self):
        if not hasattr(self, "away_overlay"):
            return
        margin = 30
        self.away_overlay.setGeometry(margin, margin, max(10, self.width() - margin * 2), max(10, self.height() - margin * 2))
        if self.away_overlay.isVisible():
            self.away_overlay.raise_()

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
        t = self.thermostat_view()
        current = self.safe_float(t.get("currentTemp"), 70.0)
        low = self.safe_float(t.get("safetyLow"), 55.0)
        high = self.safe_float(t.get("safetyHigh"), 85.0)
        heat_mode = self.active_visual_mode() == "heat"

        # Base climate-page ambience. This keeps the thermostat screen vivid
        # even while the equipment is idle, closer to the clean glassy look of
        # the reference layout without adding polling or animation load.
        base_cool = QRadialGradient(QPointF(r.width() * 0.47, r.height() * 0.46), r.width() * 0.50)
        base_cool.setColorAt(0.0, QColor(58, 145, 255, 32))
        base_cool.setColorAt(0.55, QColor(44, 100, 220, 16))
        base_cool.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, base_cool)

        base_purple = QRadialGradient(QPointF(r.width() * 0.63, r.height() * 0.35), r.width() * 0.44)
        base_purple.setColorAt(0.0, QColor(154, 104, 255, 26))
        base_purple.setColorAt(0.62, QColor(84, 62, 190, 12))
        base_purple.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, base_purple)

        base_teal = QRadialGradient(QPointF(r.width() * 0.12, r.height() * 0.70), r.width() * 0.36)
        base_teal.setColorAt(0.0, QColor(50, 255, 195, 22))
        base_teal.setColorAt(0.62, QColor(28, 138, 128, 10))
        base_teal.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, base_teal)

        # Temperature ambience. The old thresholds were too subtle and did not
        # show heat until the room was already very warm. Start warming the room
        # at 72°, start cooling it at 67°, and make 66°/76° feel obvious.
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
        safety_cold = current < low
        safety_hot = current > high
        if safety_cold:
            cold_ratio = max(cold_ratio, 0.90)
        if safety_hot:
            hot_ratio = max(hot_ratio, 0.90)

        # The cold and hot environment effects must be mutually exclusive.
        # A cold room can still be in heat mode while it is recovering, but
        # that should not paint the warm/sun layer on top of snowflakes.
        if cold_ratio > 0 and hot_ratio > 0:
            midpoint = (low + high) / 2 if high > low else 69.5
            if current <= midpoint:
                hot_ratio = 0.0
            else:
                cold_ratio = 0.0
        heat_mode_visual = bool(heat_mode and cold_ratio <= 0 and hot_ratio <= 0)

        if cold_ratio > 0:
            # Strong blue wash from the left side of the room.
            g = QRadialGradient(QPointF(r.width() * 0.25, r.height() * 0.43), r.width() * 0.86)
            g.setColorAt(0.0, QColor(35, 205, 255, int(150 * cold_ratio)))
            g.setColorAt(0.42, QColor(22, 106, 220, int(108 * cold_ratio)))
            g.setColorAt(0.74, QColor(8, 40, 105, int(58 * cold_ratio)))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(r, g)

            # A second lighter pass gives the cold state an icy, frosted look.
            frost = QLinearGradient(0, 0, r.width(), r.height())
            frost.setColorAt(0.0, QColor(128, 238, 255, int(52 * cold_ratio)))
            frost.setColorAt(0.50, QColor(36, 122, 255, int(24 * cold_ratio)))
            frost.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(r, frost)

            # Only light flakes at 67°. By 66° they become very obvious.
            flake_strength = clamp((cold_ratio - 0.22) / 0.78, 0.0, 1.0)
            if flake_strength > 0:
                p.setPen(QPen(QColor(210, 250, 255, int(70 + 130 * flake_strength)), 2, Qt.SolidLine, Qt.RoundCap))
                flake_count = 7 + int(9 * flake_strength)
                for i in range(flake_count):
                    x = 64 + ((i * 141 + self.fx_phase * 7) % max(240, r.width() - 128))
                    y = 95 + ((i * 61 + self.fx_phase * 13) % max(170, int(r.height() * 0.58)))
                    size = 8 + (i % 4) * 3 + int(5 * flake_strength)
                    p.drawLine(QPointF(x - size, y), QPointF(x + size, y))
                    p.drawLine(QPointF(x, y - size), QPointF(x, y + size))
                    p.drawLine(QPointF(x - size * 0.7, y - size * 0.7), QPointF(x + size * 0.7, y + size * 0.7))
                    p.drawLine(QPointF(x - size * 0.7, y + size * 0.7), QPointF(x + size * 0.7, y - size * 0.7))

        if hot_ratio > 0 or heat_mode_visual:
            ratio = max(hot_ratio, 0.38 if heat_mode_visual else 0.0)
            # Warm glassy wash that starts showing at 72°. The heat-mode-only
            # fallback stays subtle and is disabled while cold effects are up.
            g = QRadialGradient(QPointF(r.width() * 0.76, r.height() * 0.42), r.width() * 0.88)
            g.setColorAt(0.0, QColor(255, 96, 42, int(156 * ratio)))
            g.setColorAt(0.42, QColor(218, 46, 42, int(118 * ratio)))
            g.setColorAt(0.74, QColor(104, 18, 36, int(66 * ratio)))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(r, g)

            amber = QLinearGradient(r.width(), 0, 0, r.height())
            amber.setColorAt(0.0, QColor(255, 190, 82, int(64 * ratio)))
            amber.setColorAt(0.45, QColor(255, 76, 52, int(38 * ratio)))
            amber.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(r, amber)

            # Modern heat shimmer: layered soft thermal waves instead of the
            # old cartoon sun/rays. Subtle at 72°, more visible as it gets hot.
            for layer, width, alpha_mult, y_offset in ((0, 2, 1.0, 0), (1, 4, 0.34, 14)):
                p.setPen(QPen(QColor(255, 214, 148, int((46 + 102 * ratio) * alpha_mult)), width, Qt.SolidLine, Qt.RoundCap))
                for i in range(5):
                    y = 145 + i * 52 + y_offset + 8 * math.sin((self.fx_phase + i * 10 + layer * 7) * 0.22)
                    start_x = r.width() * 0.53 + i * 17
                    path = QPainterPath(QPointF(start_x, y))
                    for step in range(1, 7):
                        x = start_x + step * 58
                        yy = y + math.sin((self.fx_phase * 0.18) + step * 0.86 + i + layer) * (7 + 9 * ratio)
                        path.lineTo(QPointF(x, yy))
                    p.drawPath(path)

            # A polished heat halo in the upper-right replaces the gimmicky sun.
            # It reads as "hot" without literal rotating rays.
            cx = r.width() - 158
            cy = 118
            pulse = 1.0 + 0.08 * math.sin(self.fx_phase * 0.24)
            halo_r = (50 + 28 * ratio) * pulse
            halo = QRadialGradient(QPointF(cx, cy), halo_r)
            halo.setColorAt(0.0, QColor(255, 226, 138, int(138 + 72 * ratio)))
            halo.setColorAt(0.34, QColor(255, 146, 66, int(92 + 52 * ratio)))
            halo.setColorAt(0.68, QColor(255, 58, 58, int(38 + 34 * ratio)))
            halo.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(halo))
            p.drawEllipse(QPointF(cx, cy), halo_r, halo_r)

            p.setBrush(Qt.NoBrush)
            for ring in range(3):
                ring_r = halo_r * (0.52 + ring * 0.23) + math.sin(self.fx_phase * 0.18 + ring) * 2.6
                alpha = int((92 - ring * 22) * ratio)
                p.setPen(QPen(QColor(255, 222, 156, alpha), 1.7, Qt.SolidLine, Qt.RoundCap))
                p.drawEllipse(QPointF(cx, cy), ring_r, ring_r)

            p.setPen(QPen(QColor(255, 240, 192, int(76 + 70 * ratio)), 2.2, Qt.SolidLine, Qt.RoundCap))
            for i in range(3):
                x = cx - 20 + i * 20
                top = cy - 30 + 4 * math.sin(self.fx_phase * 0.20 + i)
                path = QPainterPath(QPointF(x, top + 36))
                path.cubicTo(QPointF(x - 13, top + 25), QPointF(x + 14, top + 14), QPointF(x, top))
                p.drawPath(path)
        super().paintEvent(event)

    def format_remaining(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        minutes, sec = divmod(seconds, 60)
        if minutes >= 60:
            h, m = divmod(minutes, 60)
            return f"{h}h {m:02d}m"
        return f"{minutes}m {sec:02d}s"

    def pause_entry_name(self, entry: dict | None, fallback: str = "Door") -> str:
        if not isinstance(entry, dict):
            return fallback
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
            if current < low:
                safety_mode = "heat"
            elif current > high:
                safety_mode = "cool"
        if safety_mode in {"heat", "cool"}:
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
        if pause.get("active"):
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
            remaining = self.format_remaining((until - now_ms) / 1000)
            self.alert_banner.hide()
            self.bypass_pill.show()
            self.notice.setText(f"COOLDOWN\n{pending.capitalize()} in {remaining}")
            self.notice.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(91,48,170,0.90), stop:1 rgba(20,14,42,0.90)); color:#ffffff; border:1px solid rgba(194,155,255,0.72); border-radius:22px; padding:12px 18px;")
            self.notice.show()
            self.bypass_pill.raise_()
            self.notice.raise_()
            return
        if auto_pending in {"heat", "cool"} and auto_until > now_ms:
            remaining = self.format_remaining((auto_until - now_ms) / 1000)
            self.alert_banner.hide()
            self.bypass_pill.show()
            self.notice.setText(f"AUTO COOLDOWN\n{auto_pending.capitalize()} in {remaining}")
            self.notice.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(91,48,170,0.90), stop:1 rgba(20,14,42,0.90)); color:#ffffff; border:1px solid rgba(194,155,255,0.72); border-radius:22px; padding:12px 18px;")
            self.notice.show()
            self.bypass_pill.raise_()
            self.notice.raise_()
            return

        hold = t.get("autoSwitchHold") if isinstance(t.get("autoSwitchHold"), dict) else {}
        if hold.get("active") and str(hold.get("source") or "").lower() == "manual" and not hold.get("dismissed"):
            manual_mode = str(hold.get("mode") or "").lower()
            suggested = str(hold.get("suggestedMode") or "").lower()
            if manual_mode in {"heat", "cool"} and suggested in {"heat", "cool"} and manual_mode != suggested:
                self.bypass_pill.hide()
                self.alert_banner.hide()
                self.notice.setText(f"MANUAL OVERRIDE\n{manual_mode.capitalize()} allowed\nAUTO WOULD {suggested.upper()}")
                self.notice.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(91,48,170,0.92), stop:1 rgba(18,13,40,0.92)); color:#ffffff; border:1px solid rgba(194,155,255,0.78); border-radius:22px; padding:12px 18px;")
                self.notice.show()
                self.notice.raise_()
                return

        notice = t.get("autoSwitchNotice") if isinstance(t.get("autoSwitchNotice"), dict) else {}
        if notice.get("active"):
            self.bypass_pill.hide()
            self.alert_banner.hide()
            to_mode = str(notice.get("toMode") or self.active_visual_mode()).lower()
            switch_temp = notice.get("switchTemp") or current
            mode_label = to_mode.capitalize() if to_mode in {"heat", "cool"} else "Auto"
            self.notice.setText(f"AUTO-SWITCHED\nTo {mode_label}\nINSIDE {fmt_temp(switch_temp)}")
            if to_mode == "heat":
                self.notice.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(120,16,38,0.88), stop:1 rgba(28,12,28,0.88)); color:#ffffff; border:1px solid rgba(255,74,111,0.72); border-radius:22px; padding:12px 18px;")
            else:
                self.notice.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(2,78,130,0.88), stop:1 rgba(10,28,55,0.88)); color:#f6f8ff; border:1px solid rgba(71,224,255,0.62); border-radius:22px; padding:12px 18px;")
            self.notice.show()
            self.notice.raise_()
            return
        self.bypass_pill.hide()
        self.notice.hide()
        self.alert_banner.hide()

    def show_auto_switch_menu(self):
        t = self.thermostat_view()
        notice = t.get("autoSwitchNotice") if isinstance(t.get("autoSwitchNotice"), dict) else {}
        hold = t.get("autoSwitchHold") if isinstance(t.get("autoSwitchHold"), dict) else {}
        if not notice.get("active") and not (hold.get("active") and str(hold.get("source") or "").lower() == "manual"):
            return
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
        if notice.get("active"):
            from_mode = str(notice.get("fromMode") or "").lower()
            if from_mode in {"heat", "cool"}:
                revert = menu.addAction(f"Revert to {from_mode.capitalize()}")
                revert.triggered.connect(self.revert_auto_switch)
            dismiss = menu.addAction("Dismiss")
            dismiss.triggered.connect(self.dismiss_auto_switch)
        else:
            manual_mode = str(hold.get("mode") or "").lower()
            suggested = str(hold.get("suggestedMode") or "").lower()
            if suggested in {"heat", "cool"}:
                follow = menu.addAction(f"Follow Auto {suggested.capitalize()}")
                follow.triggered.connect(lambda checked=False, m=suggested: self.set_mode(m))
            dismiss = menu.addAction("Dismiss Notice")
            dismiss.triggered.connect(self.dismiss_manual_override_notice)
        menu.exec_(self.notice.mapToGlobal(self.notice.rect().bottomLeft()))

    def dismiss_auto_switch(self):
        try:
            self.s.update_thermostat({"autoSwitchNotice": {"active": False, "source": "", "fromMode": "", "toMode": "", "switchTemp": 0, "outdoorTemp": 0, "coolTarget": 0, "heatTarget": 0, "createdAt": 0}})
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Dismiss failed: {exc}")

    def dismiss_manual_override_notice(self):
        t = self.thermostat_view()
        hold = t.get("autoSwitchHold") if isinstance(t.get("autoSwitchHold"), dict) else {}
        if not hold.get("active"):
            return
        try:
            next_hold = dict(hold)
            next_hold["dismissed"] = True
            self.s.update_thermostat({"autoSwitchHold": next_hold})
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Dismiss failed: {exc}")

    def revert_auto_switch(self):
        t = self.thermostat_view()
        notice = t.get("autoSwitchNotice") if isinstance(t.get("autoSwitchNotice"), dict) else {}
        from_mode = str(notice.get("fromMode") or "").lower()
        if from_mode not in {"heat", "cool"}:
            self.dismiss_auto_switch()
            return
        try:
            self.s.update_thermostat({
                "mode": from_mode,
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
        self.sync(self.s.config, self.s.thermostat)
        self.requestToast.emit("Door pause snoozed for 5 minutes")

        def done(result):
            if isinstance(result, dict):
                self.s.thermostat = result
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
        schedules = self.thermostat_view().get("schedules") or []
        for sched in schedules[:3]:
            name = str(sched.get("name") or "Schedule").strip()[:18] or "Schedule"
            b = RoundButton(name, active=False, min_h=32)
            b.setFixedHeight(32)
            # Side-by-side, but hard-capped so three shortcuts cannot widen the
            # left column and push the dial into the + / − buttons.
            b.setFixedWidth(max(58, min(76, 28 + len(name) * 7)))
            b.setStyleSheet("""
                QPushButton {
                    background:rgba(255,255,255,0.075);
                    color:#eaf3ff;
                    border:1px solid rgba(130,229,255,0.28);
                    border-radius:16px;
                    padding:0 8px;
                    font-weight:900;
                    font-size:10px;
                }
                QPushButton:pressed {
                    background:rgba(71,224,255,0.30);
                    border-color:rgba(71,224,255,0.72);
                }
            """)
            b.clicked.connect(lambda checked=False, s=copy.deepcopy(sched): self.apply_schedule_now(s))
            self.schedule_shortcuts_lay.addWidget(b)

    def open_schedule_manager(self):
        dlg = ScheduleManagerDialog(self.s, self)
        dlg.changed.connect(lambda: (self.sync(self.s.config, self.s.thermostat), self.refresh_schedule_shortcuts()))
        dlg.exec_()
        self.sync(self.s.config, self.s.thermostat)

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
        self.s.thermostat["targetTemp"] = val
        self.s.thermostat["lastComfortTarget"] = val
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            if isinstance(result, dict):
                self.s.thermostat = result
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "schedule-run",
            lambda: self.s.api.thermostat_update({"targetTemp": val, "lastComfortTarget": val}),
            done,
            lambda err: self.requestToast.emit(f"Schedule failed: {err}"),
        )


    def _mode_bar(self):
        # Floating mode buttons. No shared rail/border. These use a tighter,
        # fully rounded pill style so they do not look squared-off or overlap.
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        lay.addStretch(1)
        for mode in ["cool", "heat", "auto", "away"]:
            b = RoundButton(mode.capitalize(), active=False, min_h=38)
            b.setFixedSize(94, 38)
            b.clicked.connect(lambda checked=False, m=mode: self.set_mode(m))
            self.mode_buttons[mode] = b
            lay.addWidget(b)
        lay.addStretch(1)
        return lay

    def _fan_bar(self):
        # Compact floating fan status pill. Tap it for Off / On / Auto.
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

    def show_fan_menu(self):
        if not self.fan_status_button:
            return
        current = str(self.thermostat.get("fan") or "auto").lower()
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
        for fan in ["off", "on", "auto"]:
            action = menu.addAction(("✓  " if fan == current else "   ") + fan.capitalize())
            action.triggered.connect(lambda checked=False, f=fan: self.set_fan(f))
        menu.exec_(self.fan_status_button.mapToGlobal(self.fan_status_button.rect().topLeft()))

    def set_mode(self, mode: str):
        if mode == "away":
            going_away = not bool(self.thermostat.get("away"))
            changes = {"away": going_away, "awaySource": "manual" if going_away else ""}
            self.s.thermostat["away"] = going_away
            self.s.thermostat["awaySource"] = changes["awaySource"]
        else:
            # Physical/manual button taps should visibly win immediately.
            changes = {"mode": mode, "away": False, "awaySource": ""}
            self.s.thermostat["mode"] = mode
            self.s.thermostat["away"] = False
            self.s.thermostat["awaySource"] = ""
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            if isinstance(result, dict):
                self.s.thermostat = result
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "thermostat-mode",
            lambda: self.s.api.thermostat_update(changes),
            done,
            lambda err: self.requestToast.emit(f"Thermostat update failed: {err}"),
        )

    def return_home_from_away(self):
        changes = {"away": False, "awaySource": "", "manualAwayPresenceLatch": None}
        self.s.thermostat["away"] = False
        self.s.thermostat["awaySource"] = ""
        self.s.thermostat["manualAwayPresenceLatch"] = None
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            if isinstance(result, dict):
                self.s.thermostat = result
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "thermostat-home",
            lambda: self.s.api.thermostat_update(changes),
            done,
            lambda err: self.requestToast.emit(f"Home failed: {err}"),
        )

    def set_fan(self, fan: str):
        self.s.thermostat["fan"] = fan
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            if isinstance(result, dict):
                self.s.thermostat = result
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "thermostat-fan",
            lambda: self.s.api.thermostat_update({"fan": fan}),
            done,
            lambda err: self.requestToast.emit(f"Fan update failed: {err}"),
        )

    def change_target(self, delta: int):
        self.set_target(float(self.thermostat.get("targetTemp", self.thermostat.get("target_temp", 70))) + delta)

    def set_target(self, value: float):
        try:
            t = self.thermostat_view()
            limits = t.get("limits") or {}
            mode = str(t.get("mode") or "auto").lower()
            active = str(t.get("autoActiveMode") or t.get("activeMode") or "").lower()
            range_key = active if mode == "auto" and active in {"cool", "heat"} else mode
            lim = limits.get(range_key) or limits.get(mode) or limits.get("auto") or {"min": 55, "max": 90}
            val = clamp(round(float(value)), float(lim.get("min", 55)), float(lim.get("max", 90)))
        except Exception as exc:
            self.requestToast.emit(f"Set temp failed: {exc}")
            return
        self.s.thermostat["targetTemp"] = val
        self.s.thermostat["lastComfortTarget"] = val
        self.sync(self.s.config, self.s.thermostat)

        def done(result):
            if isinstance(result, dict):
                self.s.thermostat = result
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "thermostat-target",
            lambda: self.s.api.thermostat_update({"targetTemp": val, "lastComfortTarget": val}),
            done,
            lambda err: self.requestToast.emit(f"Set temp failed: {err}"),
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
        ha = self.s.ha()
        entity = ha.get("alarmEntity") or {}
        eid = entity.get("entityId") or ""
        if not eid:
            self.requestToast.emit("No alarm entity assigned")
            return
        try:
            fresh = self.s.refresh_alarm_state()
            if fresh:
                entity.update(fresh)
        except Exception:
            pass
        dlg = AlarmControlDialog(self.s, entity, self)
        def applied(alarm, action):
            if alarm:
                entity.update(alarm)
            self.requestToast.emit(f"Alarm {action.replace('_', ' ')} sent")
            self.sync(self.s.config, self.s.thermostat)
        dlg.actionDone.connect(applied)
        dlg.exec_()


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
        mode = str(t.get("mode") or "auto").lower()
        away = bool(t.get("away"))
        if mode == "auto":
            active = str(t.get("autoActiveMode") or t.get("activeMode") or "cool").lower()
        else:
            active = mode
        self.dial.setData(t.get("currentTemp"), t.get("targetTemp"), mode, active, t.get("limits"))
        for m, b in self.mode_buttons.items():
            selected = (m == mode and not away) or (m == "away" and away)
            b.setStyleSheet(self._floating_button_style(selected))
        fan = str(t.get("fan") or "auto").lower()
        if self.fan_status_button:
            self.fan_status_button.setText(fan.capitalize())
            self.fan_status_button.setStyleSheet(self._floating_button_style(False))
        out = t.get("outdoorTemp") or t.get("outdoor_temperature") or "--"
        wind = t.get("outdoorWindSpeed") or t.get("outdoor_wind_speed") or 0
        unit = t.get("outdoorWindUnit") or t.get("outdoor_wind_unit") or "mph"
        self.outdoor.setText(f"OUTDOOR  {fmt_temp(out)}   WIND  {wind} {unit}".upper())
        relays = t.get("relays") or {}
        equipment = "Idle"
        if relays.get("cool"):
            equipment = "Cooling"
        elif relays.get("heat"):
            equipment = "Heating"
        elif t.get("coolingFanHold") or t.get("coolFanHoldUntil"):
            equipment = "Idle • Fan Hold"
        elif relays.get("fan"):
            equipment = "Fan"
        mode_label = "Away" if away else mode.capitalize()
        self.status_badge.setText(f"• {mode_label} • {equipment}")
        self.notice.hide()
        self.refresh_schedule_shortcuts()
        if away:
            source = str(t.get("awaySource") or "").lower()
            if source == "presence":
                self.away_body.setText("No assigned people are home. Tap to return Home, or it will return automatically when someone comes home.")
            else:
                self.away_body.setText("Tap to return Home and resume normal comfort.")
            self.position_away_overlay()
            self.away_overlay.show()
            self.away_overlay.raise_()
        else:
            self.away_overlay.hide()
        self.update_door_pause_ui()
        self.update_alert_banner()
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
        self.value = value
        self.update()

    def setGood(self, good: bool):
        self.good = bool(good)
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

    def toggle_control(self, ctl: dict):
        eid = ctl.get("haEntityId") or ""
        if not eid:
            self.requestAssign.emit("room", ctl, "room")
            return
        ctl["on"] = not bool(ctl.get("on"))
        self.sync(self.s.config, self.s.thermostat)
        self.requestToast.emit(f"{ctl.get('haName') or ctl.get('name')} toggled")
        payload = self.s.ha_payload({"entityId": eid, "action": "toggle", "code": (self.config.get("alarm") or {}).get("disarmCode", "")})

        def done(result):
            state = (result or {}).get("control") or {} if isinstance(result, dict) else {}
            if state:
                ctl["on"] = as_bool_state(state.get("state"))
                ctl["haName"] = state.get("name") or ctl.get("haName")
                self.sync(self.s.config, self.s.thermostat)

        self.run_async(
            "room-action",
            lambda: self.s.api.post("/api/ha/room/action", payload),
            done,
            lambda err: self.requestToast.emit(f"Control failed: {err}"),
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
                    ctl["on"] = as_bool_state(st.get("state"))
                    ctl["haName"] = st.get("name") or ctl.get("haName")
            self.sync(self.s.config, self.s.thermostat)

        self.run_async("room-poll", lambda: self.s.api.post("/api/ha/room/states", payload), done, None)


class LightsScreen(Page):
    _lightActionCompleted = pyqtSignal(object)
    _lightPollCompleted = pyqtSignal(object)

    def __init__(self, app_state: AppState, parent=None):
        super().__init__(app_state, parent)
        self.cards: list[LightCard] = []
        self.room_buttons = {}
        self._card_by_entity: dict[str, LightCard] = {}
        self._light_send_timers: dict[str, QTimer] = {}
        self._light_pending: dict[str, dict] = {}
        self._light_inflight: set[str] = set()
        self._light_settle_until: dict[str, float] = {}
        self._light_poll_running = False
        self._last_light_error = ""
        self._lightActionCompleted.connect(self._handle_light_action_completed)
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
                self._send_light(item, "color", brightness_for(item), color=color)

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
        root = QHBoxLayout(self)
        root.setContentsMargins(36, 8, 36, 24)
        root.setSpacing(18)
        self.left = GlassPanel(radius=28, strong=True)
        self.right = GlassPanel(radius=28, strong=True)
        root.addWidget(self.left, 5)
        root.addWidget(self.right, 4)
        self.build_left()
        self.build_right()

    def build_left(self):
        lay = QVBoxLayout(self.left)
        lay.setContentsMargins(26, 20, 26, 24)
        top = QHBoxLayout()
        for text, icon, kind in [("Movie Mode", "⚙", "normal"), ("Show Mode", "♙", "normal"), ("40% Volume", "♬", "normal"), ("Max", "♬", "danger")]:
            b = RoundButton(f"{icon}\n{text}", kind=kind, min_h=76)
            b.setMinimumWidth(118)
            top.addWidget(b)
            if "40" in text:
                b.clicked.connect(lambda: self.media_action("volume", 0.40))
            elif text == "Max":
                b.clicked.connect(lambda: self.media_action("volume", 1.0))
            else:
                b.clicked.connect(lambda checked=False, t=text: self.requestToast.emit(f"{t} sent"))
        top.addStretch(1)
        lay.addLayout(top)
        lay.addStretch(1)
        now = QLabel("NOW PLAYING")
        now.setAlignment(Qt.AlignCenter)
        now.setFont(font(9, QFont.Black, 26))
        now.setStyleSheet("color:#46e8ff;")
        lay.addWidget(now)
        mid = QHBoxLayout()
        self.art = QLabel("NP")
        self.art.setAlignment(Qt.AlignCenter)
        self.art.setFont(font(52, QFont.Black))
        self.art.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #49e5ff, stop:1 #6d4dd0); color:rgba(15,35,57,0.92); border-radius:34px;")
        self.art.setFixedSize(198, 198)
        self.track = QLabel("Nothing\nPlaying")
        self.track.setFont(font(38, QFont.Black))
        self.track.setStyleSheet("color:#f6f8ff;")
        self.source_label = QLabel("Livingroom Sonos")
        self.source_label.setFont(font(15, QFont.Black))
        self.source_label.setStyleSheet("color:#d9e0ee;")
        text_col = QVBoxLayout()
        text_col.addWidget(self.track)
        text_col.addWidget(self.source_label)
        text_col.addStretch(1)
        mid.addWidget(self.art)
        mid.addSpacing(24)
        mid.addLayout(text_col, 1)
        lay.addLayout(mid)
        lay.addStretch(1)
        self.progress = QFrame()
        self.progress.setFixedHeight(10)
        self.progress.setStyleSheet("background:rgba(160,170,190,0.20); border-radius:5px;")
        lay.addWidget(self.progress)
        lay.addStretch(1)

    def build_right(self):
        lay = QVBoxLayout(self.right)
        lay.setContentsMargins(22, 20, 22, 22)
        header = QHBoxLayout()
        self.room_title = QLabel("Livingroom Sonos")
        self.room_title.setFont(font(24, QFont.Black))
        self.room_title.setStyleSheet("color:#f6f8ff;")
        self.state_pill = QLabel("Idle")
        self.state_pill.setFont(font(9, QFont.Black))
        self.state_pill.setStyleSheet("background:rgba(70,80,96,0.65); color:#dbe3f4; border:1px solid rgba(255,255,255,0.16); border-radius:14px; padding:7px 11px;")
        self.source = QComboBox()
        self.source.setMinimumWidth(150)
        self.source.setStyleSheet("QComboBox{background:rgba(22,36,52,0.85); color:#f6f8ff; border:1px solid rgba(71,224,255,0.18); border-radius:16px; padding:10px; font-weight:900;} QAbstractItemView{background:#182235;color:#fff;}")
        header.addWidget(self.room_title)
        header.addWidget(self.state_pill)
        header.addStretch(1)
        header.addWidget(self.source)
        lay.addLayout(header)
        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.sub = RoundButton("Sub", active=False, min_h=56)
        self.prev = IconCircle("◀◀", "previous", 58)
        self.play = IconCircle("▶", "play_pause", 74, active=True)
        self.next = IconCircle("▶▶", "next", 58)
        self.sur = RoundButton("Surround", active=False, min_h=56)
        for w in [self.sub, self.prev, self.play, self.next, self.sur]:
            controls.addWidget(w)
        lay.addLayout(controls)
        vol_panel = GlassPanel(radius=22)
        vol_lay = QVBoxLayout(vol_panel)
        vol_lay.setContentsMargins(16,14,16,14)
        self.vol_label = QLabel("Volume                                                              --%")
        self.vol_label.setFont(font(12, QFont.Black))
        self.vol_label.setStyleSheet("color:#dbe3f4;")
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setStyleSheet(SLIDER_H)
        vol_lay.addWidget(self.vol_label)
        vol_lay.addWidget(self.volume)
        self.projector = RoundButton("▭", min_h=54)
        self.projector.setMaximumWidth(86)
        vol_lay.addWidget(self.projector, 0, Qt.AlignCenter)
        lay.addWidget(vol_panel)
        eq = QHBoxLayout()
        self.eq_sliders = {}
        for name in ["Gain", "Bass", "Treble"]:
            card = GlassPanel(radius=22)
            card.setMinimumHeight(165)
            v = QVBoxLayout(card)
            label = QLabel(f"{name}\n--")
            label.setAlignment(Qt.AlignCenter)
            label.setFont(font(10, QFont.Black))
            label.setStyleSheet("color:#dbe3f4;")
            sl = QSlider(Qt.Vertical)
            sl.setRange(0, 100); sl.setValue(50); sl.setStyleSheet("""
                QSlider::groove:vertical { width:10px; border-radius:5px; background:rgba(160,170,190,0.22); }
                QSlider::add-page:vertical { width:10px; border-radius:5px; background:#49e6ff; }
                QSlider::handle:vertical { height:32px; width:32px; margin:0 -11px; border-radius:16px; background:#f8f5ff; }
            """)
            v.addWidget(label); v.addWidget(sl, 1, Qt.AlignCenter)
            self.eq_sliders[name.lower()] = (sl, label)
            sl.sliderReleased.connect(lambda n=name.lower(), s=sl: self.set_number_control(n, s.value()))
            eq.addWidget(card)
        lay.addLayout(eq)
        self.prev.clicked.connect(lambda: self.media_action("previous"))
        self.play.clicked.connect(lambda: self.media_action("play_pause"))
        self.next.clicked.connect(lambda: self.media_action("next"))
        self.volume.sliderReleased.connect(lambda: self.media_action("volume", self.volume.value()/100.0))
        self.sub.clicked.connect(lambda: self.toggle_audio_switch("subwoofer"))
        self.sur.clicked.connect(lambda: self.toggle_audio_switch("surround"))
        self.projector.clicked.connect(lambda: self.toggle_audio_switch("projector"))

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
        self.source_label.setText(title)

    def media_action(self, action: str, value=None):
        eid = self.player_id()
        if not eid:
            self.requestToast.emit("No media player selected")
            return
        if action == "volume" and value is not None:
            try:
                pct = int(float(value) * 100)
                self.volume.blockSignals(True)
                self.volume.setValue(pct)
                self.volume.blockSignals(False)
                self.vol_label.setText(f"Volume                                                              {pct}%")
            except Exception:
                pass
        payload = self.s.ha_payload({"entityId": eid, "action": action, "value": value})

        def done(result):
            if isinstance(result, dict):
                self.player_state = result.get("state") or self.player_state
                self.apply_player_state()

        self.run_async(
            "audio-media",
            lambda: self.s.api.post("/api/ha/media/action", payload),
            done,
            lambda err: self.requestToast.emit(f"Media failed: {err}"),
        )

    def control_entity(self, name: str):
        controls = nested_get(self.config, "integrations", "homeAssistant", "audioControlEntities", default={}) or {}
        value = controls.get(name)
        if isinstance(value, dict):
            return value.get("entityId") or ""
        return value or ""

    def set_number_control(self, name: str, value: int):
        eid = self.control_entity(name)
        if not eid:
            self.requestToast.emit(f"No {name} control assigned")
            return
        payload = self.s.ha_payload({"entityId": eid, "value": value})
        self.run_async(
            "audio-number",
            lambda: self.s.api.post("/api/ha/audio/control/action", payload),
            None,
            lambda err, n=name: self.requestToast.emit(f"{n} failed: {err}"),
        )

    def toggle_audio_switch(self, name: str):
        eid = self.control_entity(name)
        if not eid:
            self.requestToast.emit(f"No {name} switch assigned")
            return
        payload = self.s.ha_payload({"entityId": eid, "action": "toggle"})
        self.run_async(
            "audio-switch",
            lambda: self.s.api.post("/api/ha/audio/switch/action", payload),
            None,
            lambda err, n=name: self.requestToast.emit(f"{n} failed: {err}"),
        )

    def apply_player_state(self):
        st = self.player_state or {}
        state = str(st.get("state") or "idle").capitalize()
        self.state_pill.setText(state)
        media_title = st.get("mediaTitle") or st.get("media_title") or "Nothing\nPlaying"
        if media_title == "Nothing Playing": media_title = "Nothing\nPlaying"
        self.track.setText(str(media_title).replace(" - ", "\n"))
        vol = st.get("volumeLevel")
        if vol is None: vol = st.get("volume_level")
        try: pct = int(float(vol) * 100)
        except Exception: pct = self.volume.value() or 0
        self.volume.blockSignals(True); self.volume.setValue(pct); self.volume.blockSignals(False)
        self.vol_label.setText(f"Volume                                                              {pct}%")

    def poll(self):
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
                b.clicked.connect(self.backspace_code)
            else:
                b.clicked.connect(lambda checked=False, d=label: self.add_code_digit(d))
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
        self.code_display.setText(entered + remaining)

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

    def __init__(self, state: AppState, selected_people: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.s = state
        self.selected_people = copy.deepcopy(selected_people or [])
        self.available_people: list[dict] = []
        self.buttons: dict[str, RoundButton] = {}
        self.setModal(True)
        self.setWindowTitle("Auto Away / Home")
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
        title = QLabel("AUTO AWAY / HOME")
        title.setFont(font(22, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        select_all = RoundButton("Select All", active=True, min_h=40)
        clear = RoundButton("Clear", active=False, kind="danger", min_h=40)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(select_all)
        header.addWidget(clear)
        root.addLayout(header)

        note = QLabel("Select the Home Assistant person entries that keep the room in Home mode. If none of them are home, the thermostat enters Away. When any selected person comes home, it returns to Home automatically.")
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

        bottom = QHBoxLayout()
        self.add_btn = RoundButton("Add Room", active=True, min_h=50)
        self.delete_btn = RoundButton("Delete Selected", kind="danger", min_h=50)
        close_btn = RoundButton("Close", min_h=50)
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



class SettingsDialog(QDialog):
    saved = pyqtSignal()

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
                border-radius:11px;
                padding:7px 10px;
                font-weight:900;
                font-size:13px;
                min-height:26px;
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
        root.setContentsMargins(6, 4, 6, 6)
        root.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("<span style='color:#46e8ff; letter-spacing:3px; font-size:9px; font-weight:900'>PANEL SETTINGS</span><br><span style='font-size:23px; font-weight:1000; color:#ffffff'>Comfort Setup</span>")
        title.setTextFormat(Qt.RichText)
        title.setMinimumHeight(38)
        title.setMaximumHeight(42)
        header.addWidget(title)
        header.addStretch(1)
        self.hardware = RoundButton("Hardware Information", active=True, min_h=36)
        self.history = RoundButton("History", active=True, min_h=36)
        self.done = RoundButton("Done", active=True, min_h=36)
        self.hardware.setMinimumWidth(176)
        self.history.setMinimumWidth(108)
        self.done.setMinimumWidth(104)
        header.addWidget(self.hardware)
        header.addWidget(self.history)
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
        self.grid.setSpacing(5)
        self.grid.setContentsMargins(0, 0, 0, 0)
        for col in range(4):
            self.grid.setColumnStretch(col, 1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Single clean bottom action bar. Hardware / History / Done stay in the
        # header only so the settings screen does not show duplicate controls.
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.addStretch(1)
        self.bottom_save = RoundButton("Save Settings", active=True, min_h=38)
        self.bottom_save.setMinimumWidth(170)
        bottom.addWidget(self.bottom_save)
        root.addLayout(bottom)

        self.controls: dict[str, QLabel] = {}
        self.build()
        self.done.clicked.connect(self.accept)
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

    def value_control(self, key: str, label: str, value, low=None, high=None, suffix="°") -> QFrame:
        panel = self.settings_panel(10)
        panel.setMinimumHeight(40)
        panel.setMaximumHeight(46)
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(7, 4, 7, 4)
        lay.setSpacing(5)
        lab = QLabel(label)
        lab.setFont(font(8, QFont.Black))
        lab.setStyleSheet("color:#e7efff; background:transparent; border:0;")
        lab.setWordWrap(False)
        minus = RoundButton("−", min_h=28)
        minus.setFixedSize(32, 28)
        val = QLabel(str(value) + suffix)
        val.setAlignment(Qt.AlignCenter)
        val.setMinimumWidth(44)
        val.setFont(font(10, QFont.Black))
        val.setStyleSheet("color:#ffffff; background:transparent; border:0;")
        plus = RoundButton("+", min_h=28)
        plus.setFixedSize(32, 28)
        lay.addWidget(lab, 1)
        lay.addWidget(minus)
        lay.addWidget(val)
        lay.addWidget(plus)
        self.controls[key] = val
        minus.clicked.connect(lambda: self.adjust_value(key, -1, low, high, suffix))
        plus.clicked.connect(lambda: self.adjust_value(key, 1, low, high, suffix))
        return panel

    def build_value(self, key: str, label: str, value, row: int, col: int, low=None, high=None, suffix="°"):
        self.grid.addWidget(self.value_control(key, label, value, low, high, suffix), row, col)

    def section_grid(self, section: QFrame, columns: int = 2) -> QGridLayout:
        g = QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(5)
        g.setVerticalSpacing(5)
        for col in range(max(1, int(columns))):
            g.setColumnStretch(col, 1)
        section.layout().addLayout(g)
        return g

    def add_section_value(self, layout: QGridLayout, key: str, label: str, value, row: int, col: int, low=None, high=None, suffix="°", colspan: int = 1):
        layout.addWidget(self.value_control(key, label, value, low, high, suffix), row, col, 1, colspan)

    def add_section(self, title: str, row: int, col: int, rowspan: int = 1, colspan: int = 1) -> QFrame:
        p = self.settings_panel(12)
        v = QVBoxLayout(p)
        v.setContentsMargins(8, 5, 8, 6)
        v.setSpacing(4)
        lab = QLabel(title)
        lab.setFont(font(10, QFont.Black))
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
        self.s.config.setdefault("security", {})["settingsCode"] = code
        try:
            self.s.save_config()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return
        if hasattr(self, "settings_code_field"):
            self.settings_code_field.setText(self.masked_code(code))
        self.saved.emit()


    def people_summary_text(self) -> str:
        people = self.s.thermostat.get("people") if isinstance(self.s.thermostat, dict) else []
        names = []
        if isinstance(people, list):
            for person in people:
                if isinstance(person, dict):
                    names.append(str(person.get("name") or person.get("entityId") or "").strip())
        names = [x for x in names if x]
        if not names:
            return "No people assigned. Tap + Person to add Home Assistant person entries."
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f" +{len(names)-3} more"
        return f"Auto away uses: {shown}"

    def choose_auto_away_people(self):
        current = self.s.thermostat.get("people") if isinstance(self.s.thermostat, dict) else []
        dlg = PeopleSelectionDialog(self.s, current if isinstance(current, list) else [], self)
        def apply(people):
            try:
                self.s.update_thermostat({"people": people})
                if hasattr(self, "people_summary"):
                    self.people_summary.setText(self.people_summary_text())
                self.saved.emit()
            except Exception as exc:
                QMessageBox.warning(self, "Auto Away / Home", str(exc))
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
        return f"{name}\nUsing {entity_id}"

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
        for item in list(entities) + list(stored):
            if not isinstance(item, dict):
                continue
            eid = str(item.get("entityId") or item.get("entity_id") or "").strip()
            if not eid:
                continue
            domain = str(item.get("domain") or (eid.split(".", 1)[0] if "." in eid else "")).strip()
            if domain not in {"binary_sensor", "cover"}:
                continue
            by_id[eid] = {
                "entityId": eid,
                "name": str(item.get("name") or item.get("friendly_name") or eid),
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
                selected = {
                    "entityId": eid,
                    "name": str(e.get("name") or e.get("friendly_name") or eid),
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
        return f"{label}: {name}\nUsing {entity_id}"

    def air_mode_summary_text(self) -> str:
        if self.air_control_mode() == "external":
            return "External mode: heat and cool calls control the selected Home Assistant entries."
        return "Internal mode: future onboard sensors/GPIO control are used; HA heat/cool entries are ignored."

    def update_air_control_widgets(self):
        external = self.air_control_mode() == "external"
        if hasattr(self, "air_mode_summary"):
            self.air_mode_summary.setText(self.air_mode_summary_text())
        if hasattr(self, "air_switch_button"):
            self.air_switch_button.setText("Internal / External Air Switch: EXTERNAL" if external else "Internal / External Air Switch: INTERNAL")
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


    def build(self):
        t = self.s.thermostat or {}

        auto_home = self.add_section("Auto Away / Home", 0, 0, 1, 2)
        auto_grid = self.section_grid(auto_home, 2)
        self.add_section_value(auto_grid, "awayHeat", "Heat Away", t.get("awayHeat", 55), 0, 0, 40, 75)
        self.add_section_value(auto_grid, "awayCool", "Cool Away", t.get("awayCool", 85), 0, 1, 75, 100)
        people_head = QHBoxLayout()
        people_head.setSpacing(6)
        self.people_summary = QLabel(self.people_summary_text())
        self.people_summary.setWordWrap(True)
        self.people_summary.setFont(font(8, QFont.Black))
        self.people_summary.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:9px; padding:5px;")
        add_people = RoundButton("+ Person", active=True, min_h=32)
        add_people.setMinimumWidth(112)
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

        changeover = self.add_section("Changeover / Fan", 1, 2, 1, 2)
        change_grid = self.section_grid(changeover, 2)
        self.add_section_value(change_grid, "autoChangeoverLockoutMinutes", "Auto Delay", int(float(t.get("autoChangeoverLockoutMinutes", 120))/60), 0, 0, 0, 8, " hr")
        self.add_section_value(change_grid, "manualChangeoverLockoutMinutes", "Manual Delay", t.get("manualChangeoverLockoutMinutes", 10), 0, 1, 0, 60, " min")
        self.add_section_value(change_grid, "coolFanRemainOnMinutes", "Cool Fan", t.get("coolFanRemainOnMinutes", 2), 1, 0, 0, 15, " min", colspan=2)
        fan_row = QHBoxLayout()
        fan_row.setSpacing(6)
        changeover.layout().addLayout(fan_row)
        for f in ["off", "on", "auto"]:
            b = RoundButton(f.capitalize(), active=(t.get("fan") or "auto") == f, min_h=32)
            b.clicked.connect(lambda checked=False, x=f: self.set_thermostat({"fan": x}))
            fan_row.addWidget(b)

        air_control = self.add_section("Internal / External Air Switch", 2, 0, 1, 2)
        self.air_mode_summary = QLabel(self.air_mode_summary_text())
        self.air_mode_summary.setWordWrap(True)
        self.air_mode_summary.setFont(font(8, QFont.Black))
        self.air_mode_summary.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:9px; padding:5px;")
        air_control.layout().addWidget(self.air_mode_summary)
        self.air_switch_button = RoundButton("Internal / External Air Switch", active=(self.air_control_mode() == "external"), min_h=32)
        self.air_switch_button.clicked.connect(self.toggle_air_control_mode)
        air_control.layout().addWidget(self.air_switch_button)
        air_grid = self.section_grid(air_control, 2)
        self.external_heat_label = QLabel(self.external_air_summary_text("heat"))
        self.external_heat_label.setWordWrap(True)
        self.external_heat_label.setFont(font(8, QFont.Black))
        self.external_heat_label.setStyleSheet("color:#dfe9ff; background:transparent; border:0;")
        self.choose_external_heat_button = RoundButton("Heat Entry", active=True, min_h=30)
        self.choose_external_heat_button.clicked.connect(lambda checked=False: self.choose_external_air_entry("heat"))
        self.external_cool_label = QLabel(self.external_air_summary_text("cool"))
        self.external_cool_label.setWordWrap(True)
        self.external_cool_label.setFont(font(8, QFont.Black))
        self.external_cool_label.setStyleSheet("color:#dfe9ff; background:transparent; border:0;")
        self.choose_external_cool_button = RoundButton("Cool Entry", active=True, min_h=30)
        self.choose_external_cool_button.clicked.connect(lambda checked=False: self.choose_external_air_entry("cool"))
        air_grid.addWidget(self.external_heat_label, 0, 0)
        air_grid.addWidget(self.choose_external_heat_button, 0, 1)
        air_grid.addWidget(self.external_cool_label, 1, 0)
        air_grid.addWidget(self.choose_external_cool_button, 1, 1)
        self.update_air_control_widgets()

        temp_source = self.add_section("Current Temperature Source", 2, 2, 1, 2)
        selected_temp = nested_get(self.s.config, "integrations", "homeAssistant", "currentTempEntity", default=None)
        if isinstance(selected_temp, dict):
            source_name = selected_temp.get("name") or selected_temp.get("friendly_name") or selected_temp.get("entityId") or "Home Assistant Sensor"
            source_line = f"Using {selected_temp.get('entityId') or 'selected sensor'}"
        else:
            source_name = t.get("currentTempSourceName") or "Virtual Temp"
            source_line = "Using virtual temp until a sensor is selected"
        self.temp_source_label = QLabel(f"{source_name}\n{source_line}")
        self.temp_source_label.setFont(font(9, QFont.Black))
        self.temp_source_label.setStyleSheet("color:#dfe9ff; background:transparent; border:0;")
        temp_source.layout().addWidget(self.temp_source_label)
        choose = RoundButton("Choose Sensor", active=True, min_h=32)
        choose.setMinimumWidth(142)
        choose.clicked.connect(self.choose_temp_sensor)
        temp_source.layout().addWidget(choose, 0, Qt.AlignRight)

        outdoor_source = self.add_section("Outside Temperature Source", 3, 0, 1, 2)
        selected_outdoor = nested_get(self.s.config, "integrations", "homeAssistant", "outdoorTempEntity", default=None) or nested_get(self.s.config, "integrations", "homeAssistant", "weatherEntity", default=None)
        if isinstance(selected_outdoor, dict):
            outdoor_name = selected_outdoor.get("name") or selected_outdoor.get("friendly_name") or selected_outdoor.get("entityId") or "Outside Sensor"
            outdoor_line = f"Using {selected_outdoor.get('entityId') or 'selected entry'}"
        else:
            outdoor_name = "Not selected"
            outdoor_line = "Choose a Home Assistant outside temp entry"
        self.outdoor_source_label = QLabel(f"{outdoor_name}\n{outdoor_line}")
        self.outdoor_source_label.setFont(font(9, QFont.Black))
        self.outdoor_source_label.setStyleSheet("color:#dfe9ff; background:transparent; border:0;")
        outdoor_source.layout().addWidget(self.outdoor_source_label)
        choose_outdoor = RoundButton("Choose Outside", active=True, min_h=32)
        choose_outdoor.setMinimumWidth(150)
        choose_outdoor.clicked.connect(self.choose_outdoor_temp_sensor)
        outdoor_source.layout().addWidget(choose_outdoor, 0, Qt.AlignRight)

        door_source = self.add_section("Doors / Comfort Pause", 3, 2, 1, 2)
        self.inside_door_label = QLabel(self.inside_door_summary_text())
        self.inside_door_label.setWordWrap(True)
        self.inside_door_label.setFont(font(8, QFont.Black))
        self.inside_door_label.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:9px; padding:5px;")
        door_source.layout().addWidget(self.inside_door_label)
        pause = t.get("pauseFunction") if isinstance(t.get("pauseFunction"), dict) else {}
        door_grid = self.section_grid(door_source, 2)
        self.add_section_value(door_grid, "doorPauseDurationMinutes", "Door Delay", int(float(pause.get("durationMinutes") or 5)), 0, 0, 1, 60, " min")
        choose_door = RoundButton("Choose Entry", active=True, min_h=32)
        choose_door.clicked.connect(self.choose_inside_door_entry)
        door_grid.addWidget(choose_door, 0, 1)

        codes = self.add_section("Security Codes", 4, 0, 1, 4)
        code_grid = QGridLayout()
        code_grid.setContentsMargins(0, 0, 0, 0)
        code_grid.setHorizontalSpacing(6)
        code_grid.setVerticalSpacing(4)
        codes.layout().addLayout(code_grid)
        alarm_lab = QLabel("Alarm Disarm")
        alarm_lab.setFont(font(8, QFont.Black))
        alarm_lab.setStyleSheet("color:#c4d0e5; background:transparent; border:0;")
        settings_lab = QLabel("Settings Access")
        settings_lab.setFont(font(8, QFont.Black))
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

        self.grid.setRowStretch(5, 1)

    def val_number(self, key):
        text = self.controls[key].text().split()[0].replace("°", "")
        try: return int(float(text))
        except Exception: return 0

    def adjust_value(self, key, delta, low, high, suffix):
        val = self.val_number(key) + delta
        if low is not None: val = max(low, val)
        if high is not None: val = min(high, val)
        self.controls[key].setText(str(val) + suffix)
        self.apply_values()

    def apply_values(self):
        limits = copy.deepcopy(self.s.thermostat.get("limits") or {})
        limits.setdefault("cool", {})["min"] = self.val_number("coolMin")
        limits.setdefault("cool", {})["max"] = self.val_number("coolMax")
        limits.setdefault("heat", {})["min"] = self.val_number("heatMin")
        limits.setdefault("heat", {})["max"] = self.val_number("heatMax")
        limits.setdefault("auto", {})["min"] = min(limits["cool"]["min"], limits["heat"]["min"])
        limits.setdefault("auto", {})["max"] = max(limits["cool"]["max"], limits["heat"]["max"])
        pause = self.s.thermostat.get("pauseFunction") if isinstance(self.s.thermostat.get("pauseFunction"), dict) else {}
        pause_entries = pause.get("entries") if isinstance(pause.get("entries"), list) else []
        changes = {
            "safetyLow": self.val_number("safetyLow"),
            "safetyHigh": self.val_number("safetyHigh"),
            "awayHeat": self.val_number("awayHeat"),
            "awayCool": self.val_number("awayCool"),
            "autoCoolOutdoorTarget": self.val_number("autoCoolOutdoorTarget"),
            "autoHeatOutdoorTarget": self.val_number("autoHeatOutdoorTarget"),
            "autoChangeoverLockoutMinutes": self.val_number("autoChangeoverLockoutMinutes") * 60,
            "manualChangeoverLockoutMinutes": self.val_number("manualChangeoverLockoutMinutes"),
            "coolFanRemainOnMinutes": self.val_number("coolFanRemainOnMinutes"),
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
        self.set_thermostat(changes, quiet=True)

    def set_thermostat(self, changes, quiet=False):
        try:
            self.s.update_thermostat(changes)
            self.saved.emit()
        except Exception as exc:
            if not quiet: QMessageBox.warning(self, "Update failed", str(exc))

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
        self.apply_values()
        try:
            self.s.save_config()
            self.saved.emit()
            if hasattr(self, "security_code_field"):
                self.security_code_field.setText(self.masked_code(str((self.s.config.get("alarm") or {}).get("disarmCode") or "")))
            if hasattr(self, "settings_code_field"):
                self.settings_code_field.setText(self.masked_code(str((self.s.config.get("security") or {}).get("settingsCode") or "")))
            self.show_saved_then_close()
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

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


class AlarmControlDialog(QDialog):
    actionDone = pyqtSignal(dict, str)

    def __init__(self, state: AppState, alarm_entity: dict, parent=None):
        super().__init__(parent)
        self.s = state
        self.entity = alarm_entity or {}
        self.code_buffer = ""
        self.remaining = 0
        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self.countdown_tick)

        self.setModal(True)
        self.setWindowTitle("Alarm Control")
        self.setFixedSize(470, 430)
        self.setStyleSheet("""
            QDialog {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 #070d18,
                    stop:0.55 #101a32,
                    stop:1 #210c17);
                color:#f7fbff;
            }
            QLabel {
                color:#f7fbff;
                font-family:Arial;
            }
            QPushButton {
                font-family:Arial;
                font-weight:900;
            }
        """)

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(22, 18, 22, 18)
        self.root.setSpacing(12)

        self.title = QLabel("")
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setTextFormat(Qt.RichText)
        self.root.addWidget(self.title)

        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        self.root.addLayout(self.body, 1)

        self.render()

    def clear_body(self):
        while self.body.count():
            item = self.body.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget:
                widget.deleteLater()
            elif child_layout:
                while child_layout.count():
                    child = child_layout.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

    def current_state(self) -> str:
        return str(self.entity.get("state") or "disarmed").lower()

    def is_armed(self) -> bool:
        state = self.current_state()
        return state.startswith("armed") or state in {"arming", "pending", "triggered"}

    def render(self):
        self.clear_body()
        if self.is_armed():
            self.render_keypad()
        else:
            self.render_arm_options()

    def render_arm_options(self):
        self.setFixedSize(470, 320)
        self.title.setText(
            "<span style='color:#55f0ff; letter-spacing:3px; font-size:12px; font-weight:900'>ALARM DISARMED</span>"
            "<br><span style='font-size:32px; font-weight:1000; color:#ffffff'>Select Arm Mode</span>"
        )

        buttons = QVBoxLayout()
        buttons.setSpacing(12)

        self.arm_home = RoundButton("Arm Home", active=True, min_h=62)
        self.arm_away = RoundButton("Arm Away  •  60 sec", active=True, kind="purple", min_h=62)
        self.cancel = RoundButton("Cancel", active=False, min_h=54)

        self.arm_home.clicked.connect(lambda: self.send_action("arm_home"))
        self.arm_away.clicked.connect(self.begin_arm_away_countdown)
        self.cancel.clicked.connect(self.reject)

        buttons.addWidget(self.arm_home)
        buttons.addWidget(self.arm_away)
        buttons.addWidget(self.cancel)
        self.body.addLayout(buttons)

    def render_keypad(self):
        self.setFixedSize(470, 520)
        self.title.setText(
            "<span style='color:#ff4979; letter-spacing:3px; font-size:12px; font-weight:900'>ALARM ARMED</span>"
            "<br><span style='font-size:32px; font-weight:1000; color:#ffffff'>Enter Code</span>"
        )

        self.code_display = QLabel("••••")
        self.code_display.setAlignment(Qt.AlignCenter)
        self.code_display.setFont(font(30, QFont.Black))
        self.code_display.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,255,255,0.07);
                border:1px solid rgba(255,73,121,0.52);
                border-radius:24px;
                padding:12px;
                letter-spacing:9px;
            }
        """)
        self.body.addWidget(self.code_display)

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
                b.clicked.connect(self.backspace_code)
            else:
                b.clicked.connect(lambda checked=False, d=label: self.add_code_digit(d))
            keypad.addWidget(b, row, col)

        self.body.addLayout(keypad)

    def update_code_display(self):
        entered = "•" * len(self.code_buffer)
        remaining = "·" * max(0, 4 - len(self.code_buffer))
        self.code_display.setText(entered + remaining)

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
        self.title.setText(
            "<span style='color:#ff4979; letter-spacing:3px; font-size:12px; font-weight:900'>INVALID CODE</span>"
            "<br><span style='font-size:32px; font-weight:1000; color:#ffffff'>Try Again</span>"
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
        QTimer.singleShot(800, self.restore_keypad_after_invalid)

    def restore_keypad_after_invalid(self):
        self.title.setText(
            "<span style='color:#ff4979; letter-spacing:3px; font-size:12px; font-weight:900'>ALARM ARMED</span>"
            "<br><span style='font-size:32px; font-weight:1000; color:#ffffff'>Enter Code</span>"
        )
        self.code_display.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,255,255,0.07);
                border:1px solid rgba(255,73,121,0.52);
                border-radius:24px;
                padding:12px;
                letter-spacing:9px;
            }
        """)
        self.update_code_display()

    def begin_arm_away_countdown(self):
        self.clear_body()
        self.setFixedSize(470, 320)
        self.remaining = 60
        self.title.setText(
            "<span style='color:#ffb65c; letter-spacing:3px; font-size:12px; font-weight:900'>ARMING AWAY</span>"
            "<br><span style='font-size:32px; font-weight:1000; color:#ffffff'>Exit Timer</span>"
        )

        self.countdown_label = QLabel("")
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.countdown_label.setFont(font(46, QFont.Black))
        self.countdown_label.setStyleSheet("""
            QLabel {
                color:#ffffff;
                background:rgba(255,91,121,0.16);
                border:1px solid rgba(255,91,121,0.45);
                border-radius:28px;
                padding:18px;
            }
        """)
        self.body.addWidget(self.countdown_label)

        cancel = RoundButton("Cancel Countdown", active=False, kind="danger", min_h=58)
        cancel.clicked.connect(self.reject)
        self.body.addWidget(cancel)

        self.countdown_timer.start(1000)
        self.countdown_tick(first=True)

    def countdown_tick(self, first: bool = False):
        if not first:
            self.remaining -= 1
        if self.remaining <= 0:
            self.countdown_timer.stop()
            self.send_action("arm_away")
            return
        self.countdown_label.setText(str(self.remaining))

    def reject(self):
        if self.countdown_timer.isActive():
            self.countdown_timer.stop()
        super().reject()

    def set_busy(self, busy: bool):
        for btn in self.findChildren(QPushButton):
            btn.setEnabled(not busy)

    def send_action(self, action: str, code: str = ""):
        self.set_busy(True)
        QApplication.processEvents()
        try:
            result = self.s.api.post("/api/ha/alarm/action", self.s.ha_payload({
                "entityId": self.entity.get("entityId") or "",
                "action": action,
                "code": code,
            }))
            alarm = result.get("alarm") or {}
            if alarm:
                self.entity.update(alarm)
            self.actionDone.emit(alarm or self.entity, action)
            self.accept()
        except Exception as exc:
            self.set_busy(False)
            if action == "disarm":
                self.code_buffer = ""
                self.update_code_display()
            QMessageBox.warning(self, "Alarm Action Failed", str(exc))


class MainWindow(Background):
    statusRefreshCompleted = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.api = ApiClient()
        self.s = AppState(self.api)
        self.setWindowTitle("Smart Thermostat Native")
        self.setMinimumSize(1000, 620)
        self.toast = StatusToast(self)
        self.navigation_locked = False
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

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(12000)
        self._last_poll_by_page: dict[str, float] = {}
        self._poll_busy = False
        self._last_page_change_at = time.monotonic()
        self._ignore_info_until = 0.0
        self._status_refresh_running = False
        self.statusRefreshCompleted.connect(self._handle_status_refresh_completed)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(4000)
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
            self.toast.show_message("Native panel ready")
        except Exception as exc:
            self.toast.show_message(f"Startup problem: {exc}", 6000)
        finally:
            QApplication.restoreOverrideCursor()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.toast.isVisible():
            self.toast.move((self.width() - self.toast.width()) // 2, self.height() - self.toast.height() - 28)

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
        self.current_name = name
        now = time.monotonic()
        self._last_page_change_at = now
        # Touchscreens can emit a ghost release after a nav tap. Do not let
        # that release open the nearby info button while the page is changing.
        self._ignore_info_until = now + 1.25
        self.stack.setCurrentWidget(self.pages[name])
        self.header.set_page(name)
        # Show the page immediately, then kick a fresh Lights poll so it does not
        # sit on saved config for several seconds after navigation.
        QTimer.singleShot(60, lambda n=name: self.sync_visible_page(n))
        QTimer.singleShot(140, lambda n=name: self.poll_visible_page_now(n))



    def settings_code(self) -> str:
        security = self.s.config.get("security") or {}
        alarm = self.s.config.get("alarm") or {}
        code = str(security.get("settingsCode") or alarm.get("settingsCode") or alarm.get("disarmCode") or "").strip()
        # The appliance has historically used 3762 as the panel/settings code.
        # Keep it as a safe fallback for page unlocks when older configs do not
        # yet have security.settingsCode saved.
        return code or "3762"

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
        code = self.settings_code()
        entered = CodeKeypadDialog.get_code(self, "Screen Locked", "Enter Settings Code", code)
        if entered is None:
            return
        self.set_navigation_locked(False, show_toast=True)

    def poll_visible_page_now(self, name: str | None = None):
        try:
            if name is not None and name != self.current_name:
                return
            if getattr(self, "_poll_busy", False):
                return
            if QApplication.activeModalWidget() is not None:
                return
            if self.current_name != "Lights":
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
        status = data.get("data")
        if isinstance(status, dict):
            self.s.thermostat = status
            self.sync_runtime_only()

    def sync_runtime_only(self):
        t = self.s.thermostat or {}
        self.header.update_values(t.get("currentTemp"), t.get("targetTemp"))
        self.sync_visible_page()

    def reload_all(self):
        try:
            self.s.load()
            self.sync_runtime_only()
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
            if now - last < 10.0:
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
            return ["switch", "input_boolean", "button", "fan", "cover", "light"]
        if group == "light":
            return ["light"]
        if group == "cover":
            return ["cover"]
        return []

    def cached_entities_for(self, group: str) -> list[dict]:
        ha = self.s.ha()
        if group == "room":
            return ha.get("roomAvailableEntities") or []
        if group == "light":
            return ha.get("lightAvailableEntities") or []
        if group == "cover":
            return ha.get("coverEntities") or []
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
        dlg = EntityPickerDialog(f"Assign {kind.capitalize()} Entry", entities, self)
        def apply(ent):
            obj["haEntityId"] = ent.get("entityId") or ent.get("entity_id") or ""
            obj["haName"] = ent.get("name") or obj["haEntityId"]
            obj["name"] = ent.get("name") or obj.get("name") or obj["haEntityId"]
            if ent.get("domain"):
                obj["domain"] = ent.get("domain")
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
            elif self.current_name in {"Blinds", "Lights", "Room"}:
                dlg = RoomManagerSettingsDialog(self.s, self.current_name, self)
                dlg.saved.connect(self.reload_all)
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
            export = RoundButton("Download Config", active=False, min_h=46)
            upload = RoundButton("Upload Config", active=False, min_h=46)
            close = RoundButton("Close", active=False, min_h=46)

            # Two rows so labels are never cut off on the 10.1" panel.
            for b in [fetch, reboot, export, upload, close]:
                b.setMinimumWidth(176)
            button_grid.addWidget(fetch, 0, 0)
            button_grid.addWidget(reboot, 0, 1)
            button_grid.addWidget(close, 0, 2)
            button_grid.addWidget(export, 1, 0)
            button_grid.addWidget(upload, 1, 1)
            button_grid.setColumnStretch(2, 1)
            root.addLayout(button_grid)

            fetch.clicked.connect(lambda: (dlg.accept(), self.do_fetch_update()))
            reboot.clicked.connect(lambda: (dlg.accept(), self.do_restart()))
            export.clicked.connect(lambda: (dlg.accept(), self.export_config()))
            upload.clicked.connect(lambda: (dlg.accept(), self.import_config()))
            close.clicked.connect(dlg.accept)
            dlg.exec_()
        except Exception as exc:
            self.toast.show_message(f"Info failed: {exc}")
        finally:
            self._info_dialog_open = False
            self._info_reopen_block_until = time.monotonic() + 1.5

    def do_fetch_update(self):
        try:
            data = self.s.api.post("/api/system/fetch-update", {})
            self.toast.show_message(data.get("message") or "Fetch update started. Panel will restart.", 6500)
        except Exception as exc:
            self.toast.show_message(f"Fetch update failed: {exc}", 6500)

    def do_restart(self):
        if QMessageBox.question(self, "Restart", "Restart the thermostat service?") != QMessageBox.Yes:
            return
        try:
            self.s.api.post("/api/system/reboot", {})
        except Exception as exc:
            self.toast.show_message(f"Restart failed: {exc}")

    def export_config(self):
        try:
            data = self.s.api.post("/api/system/config-export-usb", {})
            self.toast.show_message(data.get("message") or "Config downloaded to USB", 7000)
        except Exception as exc:
            self.toast.show_message(f"USB download failed: {exc}", 8000)

    def import_config(self):
        try:
            data = self.s.api.post("/api/system/config-import-usb", {})
            self.reload_all()
            self.toast.show_message(data.get("message") or "Config uploaded from USB", 7000)
        except Exception as exc:
            self.toast.show_message(f"USB upload failed: {exc}", 8000)

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
