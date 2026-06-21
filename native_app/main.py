#!/usr/bin/env python3
from __future__ import annotations

import copy
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow running from this folder without installing a package.
APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPen, QBrush, QLinearGradient, QPainterPath, QRadialGradient
from PyQt5.QtWidgets import (
    QApplication,
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

    def __init__(self, app_state: "AppState", parent=None):
        super().__init__(parent)
        self.s = app_state
        self.config: dict = {}
        self.thermostat: dict = {}

    def sync(self, config: dict, thermostat: dict):
        self.config = config or {}
        self.thermostat = thermostat or {}

    def poll(self):
        pass


class Header(QWidget):
    navChanged = pyqtSignal(str)
    infoClicked = pyqtSignal()
    settingsClicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(102)
        self.current_pill = TopPill("--", "Current")
        self.set_pill = TopPill("--", "Set", active=True)
        self.nav = NavBar(["Blinds", "Audio", "Thermostat", "Lights", "Room"])
        self.time_pill = QLabel("--:--")
        self.time_pill.setAlignment(Qt.AlignCenter)
        self.time_pill.setFont(font(11, QFont.Black))
        self.time_pill.setStyleSheet("background:rgba(65,73,92,0.60); color:#f6f8ff; border:1px solid rgba(155,174,208,0.25); border-radius:19px; padding:9px 16px;")
        self.info = IconCircle("i", "info", 44, active=False)
        self.info.setFont(font(20, QFont.Black))
        self.gear = IconCircle("⚙", "settings", 44, active=False)
        self.gear.setFont(font(16, QFont.Black))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(42, 24, 42, 20)
        lay.setSpacing(14)
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
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.update_time)
        self.clock.start(1000)
        self.update_time()

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
        self.setFixedWidth(520)
        self.setMinimumHeight(134)
        self.setMaximumHeight(190)
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
        self.bypass.setFixedWidth(120)
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

    def set_alert(self, kind: str, title: str, body: str, *, dismiss=False, revert=False, bypass=False):
        self.kind = kind or "info"
        self.title.setText(title)
        self.body.setText(body)
        self.dismiss.setVisible(bool(dismiss))
        self.revert.setVisible(bool(revert))
        self.bypass.setVisible(bool(bypass))
        color = {
            "heat": "rgba(255,72,83,0.58)",
            "cool": "rgba(65,225,255,0.48)",
            "lockout": "rgba(188,132,255,0.50)",
            "auto": "rgba(72,214,255,0.42)",
            "safety": "rgba(255,72,83,0.56)" if "Heat" in title else "rgba(65,225,255,0.50)",
        }.get(self.kind, "rgba(72,214,255,0.38)")
        self.setStyleSheet(f"""
            ThermostatActionBanner {{
                background:qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {color},
                    stop:1 rgba(11,18,35,0.92));
                border:1px solid rgba(255,255,255,0.20);
                border-radius:24px;
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
        self.outdoor.setStyleSheet("color:#d3dbee; background:transparent; border:0; padding:0;")
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
        self.door_card = InfoTile("Inside Doors", "CLOSED", "▯", good=True)
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
        self.fx_timer.start(900)
        self.notice.hide()
        QTimer.singleShot(0, self.position_alert_banner)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "fx_timer") and not self.fx_timer.isActive():
            self.fx_timer.start(900)

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
        if getattr(self.alert_banner, "kind", "") == "lockout":
            w = min(470, max(390, self.width() - 160))
            self.alert_banner.setFixedWidth(w)
            self.alert_banner.adjustSize()
            x = min(max(280, self.width() // 4), max(12, self.width() - self.alert_banner.width() - 26))
            y = 104
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
        # Temperature ambience. Cold rooms get a blue wash and snow accents;
        # hot rooms get a darker red/orange wash and a sun pulse.
        cold_ratio = clamp((68.0 - current) / 14.0, 0.0, 1.0)
        hot_ratio = clamp((current - 76.0) / 14.0, 0.0, 1.0)
        safety_cold = current < low
        safety_hot = current > high
        if safety_cold:
            cold_ratio = max(cold_ratio, 0.65)
        if safety_hot:
            hot_ratio = max(hot_ratio, 0.65)
        if cold_ratio > 0:
            g = QRadialGradient(QPointF(r.width() * 0.25, r.height() * 0.45), r.width() * 0.72)
            g.setColorAt(0.0, QColor(40, 190, 255, int(80 * cold_ratio)))
            g.setColorAt(0.58, QColor(20, 82, 155, int(52 * cold_ratio)))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(r, g)
            p.setPen(QPen(QColor(190, 246, 255, int(115 * cold_ratio)), 2, Qt.SolidLine, Qt.RoundCap))
            for i in range(7):
                x = 86 + i * 118
                y = 112 + ((i * 37 + self.fx_phase * 9) % 190)
                size = 10 + (i % 3) * 3
                p.drawLine(QPointF(x - size, y), QPointF(x + size, y))
                p.drawLine(QPointF(x, y - size), QPointF(x, y + size))
                p.drawLine(QPointF(x - size * 0.7, y - size * 0.7), QPointF(x + size * 0.7, y + size * 0.7))
                p.drawLine(QPointF(x - size * 0.7, y + size * 0.7), QPointF(x + size * 0.7, y - size * 0.7))
        if hot_ratio > 0 or heat_mode:
            ratio = max(hot_ratio, 0.32 if heat_mode else 0.0)
            g = QRadialGradient(QPointF(r.width() * 0.78, r.height() * 0.42), r.width() * 0.72)
            g.setColorAt(0.0, QColor(255, 84, 48, int(88 * ratio)))
            g.setColorAt(0.55, QColor(128, 29, 40, int(62 * ratio)))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(r, g)
            cx = r.width() - 165
            cy = 120
            pulse = 1.0 + 0.08 * math.sin(self.fx_phase * 0.9)
            sun_r = 20 * pulse
            p.setBrush(QColor(255, 184, 66, int(145 * ratio)))
            p.setPen(QPen(QColor(255, 224, 137, int(165 * ratio)), 2))
            p.drawEllipse(QPointF(cx, cy), sun_r, sun_r)
            for a in range(0, 360, 45):
                rad = math.radians(a + self.fx_phase * 4)
                p.drawLine(QPointF(cx + math.cos(rad) * (sun_r + 8), cy + math.sin(rad) * (sun_r + 8)),
                           QPointF(cx + math.cos(rad) * (sun_r + 22), cy + math.sin(rad) * (sun_r + 22)))
        super().paintEvent(event)

    def format_remaining(self, seconds: float) -> str:
        seconds = max(0, int(seconds))
        minutes, sec = divmod(seconds, 60)
        if minutes >= 60:
            h, m = divmod(minutes, 60)
            return f"{h}h {m:02d}m"
        return f"{minutes}m {sec:02d}s"

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
            self.s.update_thermostat({"targetTemp": int(float(target)), "lastComfortTarget": int(float(target))})
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Schedule failed: {exc}")


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
        try:
            if mode == "away":
                going_away = not bool(self.thermostat.get("away"))
                self.s.update_thermostat({"away": going_away, "awaySource": "manual" if going_away else ""})
            else:
                # Physical/manual button taps should visibly win immediately.
                self.s.thermostat["mode"] = mode
                self.s.thermostat["away"] = False
                self.s.thermostat["awaySource"] = ""
                self.sync(self.s.config, self.s.thermostat)
                self.s.update_thermostat({"mode": mode, "away": False, "awaySource": ""})
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Thermostat update failed: {exc}")

    def return_home_from_away(self):
        try:
            self.s.update_thermostat({"away": False, "awaySource": "", "manualAwayPresenceLatch": None})
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Home failed: {exc}")

    def set_fan(self, fan: str):
        try:
            self.s.update_thermostat({"fan": fan})
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Fan update failed: {exc}")

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
            self.s.update_thermostat({"targetTemp": val, "lastComfortTarget": val})
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Set temp failed: {exc}")

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
        icon_rect = QRectF(cx - size / 2, cy - size / 2, size, size)
        ring = QRadialGradient(QPointF(cx, cy), size * 0.74)
        ring.setColorAt(0.0, QColor(98, 255, 205, 54))
        ring.setColorAt(0.72, QColor(98, 255, 205, 18))
        ring.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(ring)
        p.setPen(Qt.NoPen)
        p.drawEllipse(icon_rect.adjusted(-8, -8, 8, 8))

        p.setBrush(QColor(27, 95, 84, 112))
        p.setPen(QPen(QColor(147, 255, 224, 64), 1.4))
        p.drawEllipse(icon_rect)

        p.setPen(QPen(QColor(198, 255, 240, 185), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        title = self.title.lower()
        if "door" in title:
            door = QRectF(cx - size * 0.16, cy - size * 0.27, size * 0.32, size * 0.54)
            p.drawRect(door)
            p.drawLine(QPointF(cx - size * 0.31, cy - size * 0.36), QPointF(cx - size * 0.16, cy - size * 0.27))
            p.drawLine(QPointF(cx + size * 0.16, cy - size * 0.27), QPointF(cx + size * 0.31, cy - size * 0.36))
            p.drawLine(QPointF(cx - size * 0.31, cy + size * 0.36), QPointF(cx - size * 0.16, cy + size * 0.27))
            p.drawLine(QPointF(cx + size * 0.16, cy + size * 0.27), QPointF(cx + size * 0.31, cy + size * 0.36))
            p.setBrush(QColor(198, 255, 240, 190))
            p.drawEllipse(QRectF(cx + size * 0.06, cy - 2, 4, 4))
            p.setBrush(Qt.NoBrush)
        elif "alarm" in title:
            shield = QPainterPath()
            shield.moveTo(cx, cy - size * 0.34)
            shield.lineTo(cx + size * 0.27, cy - size * 0.22)
            shield.lineTo(cx + size * 0.24, cy + size * 0.16)
            shield.quadTo(cx, cy + size * 0.38, cx - size * 0.24, cy + size * 0.16)
            shield.lineTo(cx - size * 0.27, cy - size * 0.22)
            shield.closeSubpath()
            p.drawPath(shield)
        else:
            p.setFont(font(34, QFont.Black))
            p.drawText(icon_rect, Qt.AlignCenter, self.symbol)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        armed_alarm = "alarm" in self.title.lower() and (
            self.alarm_state.startswith("armed") or self.alarm_state in {"arming", "pending"}
        )
        pulse = 34 if (armed_alarm and self.flash_on) else 0
        glow = QRadialGradient(QPointF(r.center().x(), r.top() + 70), max(r.width(), r.height()) * 0.8)
        if armed_alarm:
            glow.setColorAt(0.0, QColor(255, 45, 92, 90 + pulse))
            glow.setColorAt(0.62, QColor(115, 17, 39, 58 + pulse))
        else:
            glow.setColorAt(0.0, QColor(42, 255, 187, 54 if self.good else 24))
            glow.setColorAt(0.62, QColor(27, 88, 77, 32))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(r, glow)

        g = QLinearGradient(r.topLeft(), r.bottomRight())
        if armed_alarm:
            g.setColorAt(0.0, QColor(108, 19, 43, 225))
            g.setColorAt(0.48, QColor(62, 16, 35, 220))
            g.setColorAt(1.0, QColor(22, 12, 28, 232))
            border_color = QColor(255, 67, 111, 168 + min(pulse, 40))
        else:
            g.setColorAt(0.0, QColor(22, 88, 78, 205 if self.good else 150))
            g.setColorAt(0.48, QColor(15, 45, 53, 210))
            g.setColorAt(1.0, QColor(12, 23, 39, 226))
            border_color = QColor(61, 221, 184, 118 if self.good else 70)
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
        p.setBrush(QColor(185, 35, 67, 220) if ("alarm" in self.title.lower() and (self.alarm_state.startswith("armed") or self.alarm_state in {"arming", "pending"})) else QColor(40, 141, 113, 205 if self.good else 135))
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
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
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
        try: self.s.save_config()
        except Exception: pass
        self.rebuild()

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
        try:
            result = self.s.api.post("/api/ha/room/action", self.s.ha_payload({"entityId": eid, "action": "toggle", "code": (self.config.get("alarm") or {}).get("disarmCode", "")}))
            state = result.get("control") or {}
            ctl["on"] = as_bool_state(state.get("state")) if state else not bool(ctl.get("on"))
            self.s.save_config()
            self.requestToast.emit(f"{ctl.get('haName') or ctl.get('name')} toggled")
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Control failed: {exc}")

    def poll(self):
        controls = []
        active = nested_get(self.config, "roomControl", "room", default="living")
        for ctl in nested_get(self.config, "roomControl", "rooms", active, "controls", default=[]) or []:
            if ctl.get("haEntityId"):
                controls.append(ctl)
        if not controls:
            return
        try:
            result = self.s.api.post("/api/ha/room/states", self.s.ha_payload({"entityIds": [c["haEntityId"] for c in controls]}))
            by_id = {x.get("entityId"): x for x in result.get("controls") or []}
            for ctl in controls:
                st = by_id.get(ctl.get("haEntityId"))
                if st:
                    ctl["on"] = as_bool_state(st.get("state"))
                    ctl["haName"] = st.get("name") or ctl.get("haName")
            self.sync(self.s.config, self.s.thermostat)
        except Exception:
            pass


class LightsScreen(Page):
    def __init__(self, app_state: AppState, parent=None):
        super().__init__(app_state, parent)
        self.cards: list[LightCard] = []
        self.room_buttons = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 0, 30, 22)
        self.panel = GlassPanel(radius=30)
        root.addWidget(self.panel, 1)
        self.lay = QVBoxLayout(self.panel)
        self.lay.setContentsMargins(24, 18, 24, 22)
        top = QHBoxLayout()
        self.title = SectionTitle("Light Control", "Living Room")
        top.addWidget(self.title)
        top.addStretch(1)
        self.room_tabs = QHBoxLayout()
        top.addLayout(self.room_tabs)
        self.lay.addLayout(top)
        center_buttons = QHBoxLayout()
        center_buttons.addStretch(1)
        self.on_btn = RoundButton("Room On", active=True, min_h=44)
        self.off_btn = RoundButton("Room Off", kind="purple", min_h=44)
        self.on_btn.setMinimumWidth(148); self.off_btn.setMinimumWidth(148)
        center_buttons.addWidget(self.on_btn); center_buttons.addWidget(self.off_btn)
        center_buttons.addStretch(1)
        self.lay.addLayout(center_buttons)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(12)
        self.lay.addLayout(self.grid, 1)
        self.on_btn.clicked.connect(lambda: self.room_action(True))
        self.off_btn.clicked.connect(lambda: self.room_action(False))

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
        try: self.s.save_config()
        except Exception: pass
        self.rebuild()

    def populate_cards(self):
        for c in self.cards:
            c.setParent(None); c.deleteLater()
        self.cards = []
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        active = nested_get(self.config, "lights", "room", default="living")
        room = nested_get(self.config, "lights", "rooms", active, default={}) or {}
        self.title.setText(f"<span style='color:#46e8ff; letter-spacing:4px; font-size:11px; font-weight:900'>LIGHT CONTROL</span><br><span style='font-size:38px; font-weight:1000; color:#ffffff'>{room.get('label') or active}</span>")
        for idx, light in enumerate(room.get("lights") or []):
            card = LightCard(light)
            card.clicked.connect(lambda checked=False, l=light: self.toggle_light(l))
            card.held.connect(lambda l=light: self.requestAssign.emit("light", l, "light"))
            card.brightnessChanged.connect(self.set_brightness)
            self.cards.append(card)
            self.grid.addWidget(card, 0, idx)
        self.grid.setColumnStretch(len(self.cards), 1)

    def sync(self, config, thermostat):
        first = self.config is not config
        super().sync(config, thermostat)
        if first or not self.cards:
            self.rebuild()
        else:
            active = nested_get(config, "lights", "room", default="living")
            lights = nested_get(config, "lights", "rooms", active, "lights", default=[]) or []
            for card, light in zip(self.cards, lights):
                card.setLight(light)

    def room_action(self, turn_on: bool):
        active = nested_get(self.config, "lights", "room", default="living")
        lights = [x for x in nested_get(self.config, "lights", "rooms", active, "lights", default=[]) or [] if x.get("haEntityId")]
        for light in lights:
            self._send_light(light, "on" if turn_on else "off", light.get("lastBrightness") or 100 if turn_on else 0)
        self.requestToast.emit("Room lights updated")

    def toggle_light(self, light: dict):
        if not light.get("haEntityId"):
            self.requestAssign.emit("light", light, "light")
            return
        action = "off" if bool(light.get("on")) else "on"
        bright = light.get("lastBrightness") or 100 if action == "on" else 0
        self._send_light(light, action, bright)

    def set_brightness(self, light: dict, value: int):
        if not light.get("haEntityId"):
            return
        action = "off" if value <= 0 else "on"
        self._send_light(light, action, value)

    def _send_light(self, light: dict, action: str, brightness: int | None = None):
        try:
            payload = {"entityId": light.get("haEntityId"), "action": action}
            if brightness is not None:
                payload["brightness"] = int(brightness)
            if light.get("colorSupported") and light.get("color"):
                payload["color"] = light.get("color")
            result = self.s.api.post("/api/ha/light/action", self.s.ha_payload(payload))
            st = result.get("light") or {}
            light["on"] = action == "on"
            if brightness is not None:
                light["brightness"] = int(brightness)
                if brightness > 0:
                    light["lastBrightness"] = int(brightness)
            if st.get("brightness") is not None:
                light["brightness"] = int(st.get("brightness") or 0)
            light["haName"] = st.get("name") or light.get("haName")
            self.s.save_config()
            self.sync(self.s.config, self.s.thermostat)
        except Exception as exc:
            self.requestToast.emit(f"Light failed: {exc}")

    def poll(self):
        active = nested_get(self.config, "lights", "room", default="living")
        lights = [x for x in nested_get(self.config, "lights", "rooms", active, "lights", default=[]) or [] if x.get("haEntityId")]
        if not lights:
            return
        try:
            result = self.s.api.post("/api/ha/light/states", self.s.ha_payload({"entityIds": [l["haEntityId"] for l in lights]}))
            by_id = {x.get("entityId"): x for x in result.get("lights") or []}
            for light in lights:
                st = by_id.get(light.get("haEntityId"))
                if st:
                    light["on"] = as_bool_state(st.get("state"))
                    if st.get("brightness") is not None:
                        light["brightness"] = int(st.get("brightness") or 0)
                    light["haName"] = st.get("name") or light.get("haName")
            self.sync(self.s.config, self.s.thermostat)
        except Exception:
            pass


class BlindCard(GlassPanel):
    openClicked = pyqtSignal(dict)
    closeClicked = pyqtSignal(dict)
    positionRequested = pyqtSignal(dict, int)
    assignRequested = pyqtSignal(dict)

    def __init__(self, blind: dict, parent=None):
        super().__init__(parent, radius=24)
        self.blind = blind
        self.setMinimumSize(244, 468)
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
        self.lay.setContentsMargins(24, 18, 24, 22)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(14)
        self.title = SectionTitle("Shade Control", "Living Room")
        self.title.setMinimumHeight(52)
        top.addWidget(self.title)
        top.addStretch(1)
        self.room_tabs = QHBoxLayout()
        top.addLayout(self.room_tabs)
        self.lay.addLayout(top)
        buttons = QHBoxLayout()
        # Pull the room-wide controls up; this removes the dead space between
        # the title/tabs and the blind cards on the 10.1" panel.
        buttons.setContentsMargins(0, -34, 0, -10)
        buttons.addStretch(1)
        self.open_room = RoundButton("Open Room", active=True, min_h=44)
        self.close_room = RoundButton("Close Room", kind="purple", min_h=44)
        self.open_room.setMinimumWidth(148); self.close_room.setMinimumWidth(148)
        buttons.addWidget(self.open_room); buttons.addWidget(self.close_room); buttons.addStretch(1)
        self.lay.addLayout(buttons)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, -22, 0, 0)
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
        try: self.s.save_config()
        except Exception: pass
        self.rebuild()

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
        try:
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

            result = self.s.api.post("/api/ha/cover/action", self.s.ha_payload(payload))
            state = result.get("state") or {}
            if state.get("currentPosition") is not None:
                reported = int(state.get("currentPosition") or 0)
                pending = blind.get("pendingPosition")
                pending_until = float(blind.get("pendingPositionUntil") or 0)
                if pending is None or time.time() >= pending_until or abs(reported - int(pending)) <= 3:
                    blind["position"] = reported
                    blind.pop("pendingPosition", None)
                    blind.pop("pendingPositionUntil", None)
            blind["haName"] = state.get("name") or blind.get("haName")
            self.s.save_config()
            self.sync(self.s.config, self.s.thermostat)
            if not quiet:
                label = f"{int(desired_position)}%" if action == "position" and desired_position is not None else action
                self.requestToast.emit(f"{blind.get('haName') or blind.get('name')} {label}")
        except Exception as exc:
            self.requestToast.emit(f"Blind failed: {exc}")

    def poll(self):
        active = nested_get(self.config, "blinds", "room", default="living")
        blinds = [x for x in nested_get(self.config, "blinds", "rooms", active, "blinds", default=[]) or [] if x.get("haEntityId")]
        if not blinds: return
        try:
            result = self.s.api.post("/api/ha/cover/states", self.s.ha_payload({"entityIds": [b["haEntityId"] for b in blinds]}))
            by_id = {x.get("entityId"): x for x in result.get("covers") or []}
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
        except Exception:
            pass


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
        try:
            result = self.s.api.post("/api/ha/media/action", self.s.ha_payload({"entityId": eid, "action": action, "value": value}))
            self.player_state = result.get("state") or self.player_state
            self.apply_player_state()
        except Exception as exc:
            self.requestToast.emit(f"Media failed: {exc}")

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
        try:
            self.s.api.post("/api/ha/audio/control/action", self.s.ha_payload({"entityId": eid, "value": value}))
        except Exception as exc:
            self.requestToast.emit(f"{name} failed: {exc}")

    def toggle_audio_switch(self, name: str):
        eid = self.control_entity(name)
        if not eid:
            self.requestToast.emit(f"No {name} switch assigned")
            return
        try:
            self.s.api.post("/api/ha/audio/switch/action", self.s.ha_payload({"entityId": eid, "action": "toggle"}))
        except Exception as exc:
            self.requestToast.emit(f"{name} failed: {exc}")

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
        if not eid: return
        try:
            result = self.s.api.post("/api/ha/media/states", self.s.ha_payload({"entityIds": [eid]}))
            players = result.get("players") or []
            if players:
                self.player_state = players[0]
                self.apply_player_state()
        except Exception:
            pass


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
        self.setFixedSize(720, 620)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        self.load_people()
        self.build()

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
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("AUTO AWAY / HOME")
        title.setFont(font(24, QFont.Black))
        title.setStyleSheet("color:#55f0ff; letter-spacing:3px;")
        select_all = RoundButton("Select All", active=True, min_h=44)
        clear = RoundButton("Clear", active=False, kind="danger", min_h=44)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(select_all)
        header.addWidget(clear)
        root.addLayout(header)

        note = QLabel("Select the Home Assistant person entries that keep the room in Home mode. If none of them are home, the thermostat enters Away. When any selected person comes home, it returns to Home automatically.")
        note.setWordWrap(True)
        note.setFont(font(10, QFont.Black))
        note.setStyleSheet("color:#cdd8ee; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.10); border-radius:14px; padding:10px;")
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
        cancel = RoundButton("Cancel", active=False, min_h=50)
        save = RoundButton("Save Selected", active=True, min_h=50)
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



class SettingsDialog(QDialog):
    saved = pyqtSignal()

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.s = state
        self.setWindowTitle("Comfort Setup")
        self.setModal(True)
        # The 10.1" DSI panel has a physical bezel and a rotated touch surface.
        # Keep the settings panel away from the top edge so the header buttons
        # remain reachable, then fit the dialog inside the real 1280x800 screen.
        self.setMinimumSize(900, 500)
        self.resize(1220, 650)
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
        root.setContentsMargins(10, 0, 10, 8)
        root.setSpacing(6)

        # Touch/bezel safe area. Without this, the top row can sit under the
        # panel edge and clicks on Done/Hardware/History become unreliable.
        self.top_safe_space = QWidget()
        self.top_safe_space.setFixedHeight(28)
        self.top_safe_space.setStyleSheet("background:transparent;")
        root.addWidget(self.top_safe_space)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("<span style='color:#46e8ff; letter-spacing:3px; font-size:9px; font-weight:900'>PANEL SETTINGS</span><br><span style='font-size:23px; font-weight:1000; color:#ffffff'>Comfort Setup</span>")
        title.setTextFormat(Qt.RichText)
        title.setMinimumHeight(42)
        title.setMaximumHeight(48)
        header.addWidget(title)
        header.addStretch(1)
        self.hardware = RoundButton("Hardware Information", active=True, min_h=40)
        self.history = RoundButton("History", active=True, min_h=40)
        self.done = RoundButton("Done", active=True, min_h=40)
        self.hardware.setMinimumWidth(190)
        self.history.setMinimumWidth(120)
        self.done.setMinimumWidth(118)
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
        self.grid.setSpacing(6)
        self.grid.setContentsMargins(0, 0, 2, 0)
        for col in range(4):
            self.grid.setColumnStretch(col, 1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Single clean bottom action bar. Hardware / History / Done stay in the
        # header only so the settings screen does not show duplicate controls.
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addStretch(1)
        self.bottom_save = RoundButton("Save Settings", active=True, min_h=46)
        self.bottom_save.setMinimumWidth(190)
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
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        top_safe = 46
        bottom_safe = 22
        w = min(1220, max(900, geo.width() - 36))
        h = min(650, max(500, geo.height() - top_safe - bottom_safe))
        self.resize(w, h)
        self.move(geo.x() + (geo.width() - w) // 2, geo.y() + top_safe)

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

    def build_value(self, key: str, label: str, value, row: int, col: int, low=None, high=None, suffix="°"):
        panel = self.settings_panel(13)
        panel.setMinimumHeight(46)
        panel.setMaximumHeight(52)
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(8, 5, 8, 5)
        lay.setSpacing(6)
        lab = QLabel(label)
        lab.setFont(font(9, QFont.Black))
        lab.setStyleSheet("color:#e7efff; background:transparent; border:0;")
        lab.setWordWrap(False)
        minus = RoundButton("−", min_h=30)
        minus.setFixedSize(34, 30)
        val = QLabel(str(value) + suffix)
        val.setAlignment(Qt.AlignCenter)
        val.setMinimumWidth(46)
        val.setFont(font(11, QFont.Black))
        val.setStyleSheet("color:#ffffff; background:transparent; border:0;")
        plus = RoundButton("+", min_h=30)
        plus.setFixedSize(34, 30)
        lay.addWidget(lab, 1)
        lay.addWidget(minus)
        lay.addWidget(val)
        lay.addWidget(plus)
        self.grid.addWidget(panel, row, col)
        self.controls[key] = val
        minus.clicked.connect(lambda: self.adjust_value(key, -1, low, high, suffix))
        plus.clicked.connect(lambda: self.adjust_value(key, 1, low, high, suffix))

    def add_section(self, title: str, row: int, col: int, rowspan: int = 1, colspan: int = 1) -> QFrame:
        p = self.settings_panel(14)
        v = QVBoxLayout(p)
        v.setContentsMargins(10, 7, 10, 8)
        v.setSpacing(5)
        lab = QLabel(title)
        lab.setFont(font(11, QFont.Black))
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


    def build(self):
        t = self.s.thermostat or {}
        self.build_value("safetyLow", "Low Safety", t.get("safetyLow", 55), 0, 0, 40, 75)
        self.build_value("safetyHigh", "High Safety", t.get("safetyHigh", 85), 0, 1, 75, 100)
        self.build_value("awayHeat", "Heat Away", t.get("awayHeat", 55), 0, 2, 40, 75)
        self.build_value("awayCool", "Cool Away", t.get("awayCool", 85), 0, 3, 75, 100)
        self.build_value("coolMin", "Cool Low", nested_get(t, "limits", "cool", "min", default=65), 1, 0, 50, 90)
        self.build_value("coolMax", "Cool High", nested_get(t, "limits", "cool", "max", default=80), 1, 1, 50, 90)
        self.build_value("heatMin", "Heat Low", nested_get(t, "limits", "heat", "min", default=60), 1, 2, 40, 80)
        self.build_value("heatMax", "Heat High", nested_get(t, "limits", "heat", "max", default=78), 1, 3, 40, 85)
        self.build_value("autoCoolOutdoorTarget", "Cool Mode Switch", t.get("autoCoolOutdoorTarget", 70), 2, 0, 40, 100)
        self.build_value("autoHeatOutdoorTarget", "Heat Mode Switch", t.get("autoHeatOutdoorTarget", 65), 2, 1, 40, 100)
        self.build_value("autoChangeoverLockoutMinutes", "Auto Delay", int(float(t.get("autoChangeoverLockoutMinutes", 120))/60), 2, 2, 0, 8, " hr")
        self.build_value("manualChangeoverLockoutMinutes", "Manual Delay", t.get("manualChangeoverLockoutMinutes", 10), 2, 3, 0, 60, " min")
        self.build_value("coolFanRemainOnMinutes", "Cool Fan", t.get("coolFanRemainOnMinutes", 2), 5, 2, 0, 15, " min")

        temp_source = self.add_section("Current Temperature Source", 3, 0, 1, 2)
        selected_temp = nested_get(self.s.config, "integrations", "homeAssistant", "currentTempEntity", default=None)
        if isinstance(selected_temp, dict):
            source_name = selected_temp.get("name") or selected_temp.get("friendly_name") or selected_temp.get("entityId") or "Home Assistant Sensor"
            source_line = f"Using {selected_temp.get('entityId') or 'selected sensor'}"
        else:
            source_name = t.get("currentTempSourceName") or "Virtual Temp"
            source_line = "Using virtual temp until a sensor is selected"
        self.temp_source_label = QLabel(f"{source_name}\n{source_line}")
        self.temp_source_label.setFont(font(10, QFont.Black))
        self.temp_source_label.setStyleSheet("color:#dfe9ff; background:transparent; border:0;")
        temp_source.layout().addWidget(self.temp_source_label)
        choose = RoundButton("Choose Sensor", active=True, min_h=34)
        choose.setMinimumWidth(150)
        choose.clicked.connect(self.choose_temp_sensor)
        temp_source.layout().addWidget(choose, 0, Qt.AlignRight)

        fan = self.add_section("Fan", 3, 2, 1, 2)
        row = QHBoxLayout()
        row.setSpacing(8)
        fan.layout().addLayout(row)
        for f in ["off", "on", "auto"]:
            b = RoundButton(f.capitalize(), active=(t.get("fan") or "auto") == f, min_h=34)
            b.clicked.connect(lambda checked=False, x=f: self.set_thermostat({"fan": x}))
            row.addWidget(b)
        outdoor_source = self.add_section("Outside Temperature Source", 4, 2, 1, 2)
        selected_outdoor = nested_get(self.s.config, "integrations", "homeAssistant", "outdoorTempEntity", default=None) or nested_get(self.s.config, "integrations", "homeAssistant", "weatherEntity", default=None)
        if isinstance(selected_outdoor, dict):
            outdoor_name = selected_outdoor.get("name") or selected_outdoor.get("friendly_name") or selected_outdoor.get("entityId") or "Outside Sensor"
            outdoor_line = f"Using {selected_outdoor.get('entityId') or 'selected entry'}"
        else:
            outdoor_name = "Not selected"
            outdoor_line = "Choose a Home Assistant outside temp entry"
        self.outdoor_source_label = QLabel(f"{outdoor_name}\n{outdoor_line}")
        self.outdoor_source_label.setFont(font(10, QFont.Black))
        self.outdoor_source_label.setStyleSheet("color:#dfe9ff; background:transparent; border:0;")
        outdoor_source.layout().addWidget(self.outdoor_source_label)
        choose_outdoor = RoundButton("Choose Outside", active=True, min_h=34)
        choose_outdoor.setMinimumWidth(160)
        choose_outdoor.clicked.connect(self.choose_outdoor_temp_sensor)
        outdoor_source.layout().addWidget(choose_outdoor, 0, Qt.AlignRight)

        people = self.add_section("Auto Away / Home", 4, 0, 1, 2)
        people_head = QHBoxLayout()
        self.people_summary = QLabel(self.people_summary_text())
        self.people_summary.setWordWrap(True)
        self.people_summary.setFont(font(9, QFont.Black))
        self.people_summary.setStyleSheet("color:#c4d0e5; background:rgba(5,10,20,0.42); border:1px dashed rgba(160,180,210,0.26); border-radius:10px; padding:7px;")
        add_people = RoundButton("+ Person", active=True, min_h=34)
        add_people.clicked.connect(self.choose_auto_away_people)
        people_head.addWidget(self.people_summary, 1)
        people_head.addWidget(add_people)
        people.layout().addLayout(people_head)

        code_sec = self.add_section("Security Code", 5, 0, 1, 1)
        current_security = str((self.s.config.get("alarm") or {}).get("disarmCode") or "")
        self.security_code_field = self.code_field(current_security, self.edit_security_code)
        self.security_code_field.setPlaceholderText("Alarm disarm code")
        code_sec.layout().addWidget(self.security_code_field)

        settings_code_sec = self.add_section("Settings Code", 5, 1, 1, 1)
        current_settings = str((self.s.config.get("security") or {}).get("settingsCode") or "")
        self.settings_code_field = self.code_field(current_settings, self.edit_settings_code)
        self.settings_code_field.setPlaceholderText("Settings access code")
        settings_code_sec.layout().addWidget(self.settings_code_field)

        self.grid.setRowStretch(6, 1)

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
    def __init__(self):
        super().__init__()
        self.api = ApiClient()
        self.s = AppState(self.api)
        self.setWindowTitle("Smart Thermostat Native")
        self.setMinimumSize(1000, 620)
        self.toast = StatusToast(self)
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
        self.current_name = "Thermostat"
        self.header.set_page(self.current_name)
        self.stack.setCurrentWidget(self.pages[self.current_name])

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(12000)
        self._last_poll_by_page: dict[str, float] = {}
        self._poll_busy = False
        self._last_page_change_at = time.monotonic()
        self._ignore_info_until = 0.0
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

    def set_page(self, name: str):
        if name not in self.pages:
            return
        self.current_name = name
        now = time.monotonic()
        self._last_page_change_at = now
        # Touchscreens can emit a ghost release after a nav tap. Do not let
        # that release open the nearby info button while the page is changing.
        self._ignore_info_until = now + 1.25
        self.stack.setCurrentWidget(self.pages[name])
        self.header.set_page(name)
        # Show the page immediately, even if fresh HA data is still catching up.
        QTimer.singleShot(60, lambda n=name: self.sync_visible_page(n))

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
        try:
            self.s.refresh_status()
            self.sync_runtime_only()
        except Exception:
            pass

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
            security = self.s.config.get("security") or {}
            alarm = self.s.config.get("alarm") or {}
            settings_code = str(security.get("settingsCode") or alarm.get("settingsCode") or alarm.get("disarmCode") or "").strip()
            if settings_code:
                entered = CodeKeypadDialog.get_code(self, "Settings Locked", "Enter Settings Code", settings_code)
                if entered is None:
                    self._settings_reopen_block_until = time.monotonic() + 1.5
                    return
            dlg = SettingsDialog(self.s, self)
            dlg.saved.connect(self.reload_all)
            dlg.exec_()
            self.reload_all()
        finally:
            self._settings_dialog_open = False
            # Touchscreens can emit a second tap/release after the modal closes.
            # Block immediate re-entry so the settings keypad does not pop back up.
            self._settings_reopen_block_until = time.monotonic() + 2.0

    def show_info(self):
        if time.monotonic() < getattr(self, "_ignore_info_until", 0):
            return
        try:
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
        path, _ = QFileDialog.getSaveFileName(self, "Save Config", str(Path.home() / "smart-thermostat-config.json"), "JSON (*.json)")
        if not path: return
        try:
            rec = self.s.api.get("/api/config")
            import json
            Path(path).write_text(json.dumps(rec, indent=2), encoding="utf-8")
            self.toast.show_message("Config saved")
        except Exception as exc:
            self.toast.show_message(f"Export failed: {exc}")

    def import_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "Upload Config", str(Path.home()), "JSON (*.json)")
        if not path: return
        try:
            import json
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.s.api.post("/api/system/config-import", data)
            self.reload_all()
            self.toast.show_message("Config uploaded")
        except Exception as exc:
            self.toast.show_message(f"Import failed: {exc}")

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
    app = QApplication(sys.argv)
    app.setApplicationName("Smart Thermostat Native")
    app.setFont(font(10, QFont.Bold))
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
