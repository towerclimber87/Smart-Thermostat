from __future__ import annotations

import math
from typing import Callable, Iterable

from PyQt5.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt5.QtWidgets import (
    QAbstractButton,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from theme import T, button_style, css_color, font, SLIDER_H, SLIDER_V


def fmt_temp(value, default="--") -> str:
    try:
        return f"{float(value):.0f}°"
    except Exception:
        return default


def compact_name(name: str, max_len: int = 26) -> str:
    name = str(name or "").replace("_", " ").strip()
    if len(name) <= max_len:
        return name
    return name[: max_len - 1].rstrip() + "…"


class Background(QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = self.rect()
        p.fillRect(r, T.BG)

        g = QLinearGradient(0, 0, r.width(), r.height())
        g.setColorAt(0.0, QColor(23, 31, 44))
        g.setColorAt(0.55, QColor(15, 20, 34))
        g.setColorAt(1.0, QColor(6, 10, 22))
        p.fillRect(r, g)

        for x in range(0, r.width(), 72):
            p.setPen(QPen(QColor(255, 255, 255, 10), 1))
            p.drawLine(x, 0, x, r.height())
        for y in range(0, r.height(), 72):
            p.setPen(QPen(QColor(255, 255, 255, 8), 1))
            p.drawLine(0, y, r.width(), y)

        for cx, cy, col, rad in [
            (0.06, 0.18, QColor(65, 231, 255, 48), 240),
            (0.42, 0.54, QColor(71, 127, 255, 54), 330),
            (0.92, 0.18, QColor(173, 126, 255, 34), 300),
            (0.86, 0.78, QColor(54, 228, 178, 34), 260),
        ]:
            rg = QRadialGradient(QPointF(r.width() * cx, r.height() * cy), rad)
            rg.setColorAt(0.0, col)
            rg.setColorAt(0.45, QColor(col.red(), col.green(), col.blue(), max(0, col.alpha() // 3)))
            rg.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.fillRect(r, rg)


class GlassPanel(QFrame):
    def __init__(self, parent=None, radius: int = 24, strong: bool = False, dashed: bool = False):
        super().__init__(parent)
        self.radius = radius
        self.strong = strong
        self.dashed = dashed
        self.setAttribute(Qt.WA_TranslucentBackground)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if self.strong:
            g.setColorAt(0.0, QColor(70, 83, 101, 208))
            g.setColorAt(1.0, QColor(23, 31, 48, 224))
        else:
            g.setColorAt(0.0, QColor(66, 79, 96, 128))
            g.setColorAt(1.0, QColor(18, 27, 45, 178))
        p.setBrush(QBrush(g))
        pen = QPen(T.BORDER_DASH if self.dashed else T.BORDER, 1.4)
        if self.dashed:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawRoundedRect(rect, self.radius, self.radius)
        super().paintEvent(event)


class RoundButton(QPushButton):
    def __init__(self, text: str = "", active: bool = False, kind: str = "normal", min_h: int = 46, parent=None):
        super().__init__(text, parent)
        self._active = active
        self._kind = kind
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(min_h)
        self.setFont(font(12))
        self.refresh()

    def setActive(self, active: bool):
        self._active = bool(active)
        self.refresh()

    def setKind(self, kind: str):
        self._kind = kind
        self.refresh()

    def refresh(self):
        self.setStyleSheet(button_style(active=self._active, danger=self._kind == "danger", green=self._kind == "green", purple=self._kind == "purple"))


class IconCircle(QAbstractButton):
    clickedValue = pyqtSignal(str)

    def __init__(self, text: str, value: str = "", diameter: int = 58, active: bool = False, parent=None):
        super().__init__(parent)
        self.text = text
        self.value = value or text
        self.active = active
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(diameter, diameter)
        self.setFont(font(max(12, diameter // 3)))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if self.active:
            g.setColorAt(0, QColor(72, 225, 255))
            g.setColorAt(1, QColor(75, 142, 255))
            text = QColor(2, 15, 30)
        else:
            g.setColorAt(0, QColor(70, 80, 99, 180))
            g.setColorAt(1, QColor(25, 31, 46, 210))
            text = T.TEXT
        p.setBrush(g)
        p.setPen(QPen(T.BORDER, 1.2))
        p.drawEllipse(rect)
        p.setPen(text)
        p.setFont(self.font())
        p.drawText(rect, Qt.AlignCenter, self.text)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            self.clickedValue.emit(self.value)
        super().mouseReleaseEvent(event)


class TopPill(QWidget):
    def __init__(self, big: str, small: str, active: bool = False, parent=None):
        super().__init__(parent)
        self.big = big
        self.small = small
        self.active = active
        self.setMinimumSize(88, 50)
        self.setMaximumHeight(52)

    def setValues(self, big: str, small: str):
        self.big = big
        self.small = small
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        if self.active:
            g.setColorAt(0, QColor(67, 58, 100, 230))
            g.setColorAt(1, QColor(82, 66, 132, 220))
            big_col = T.CYAN
        else:
            g.setColorAt(0, QColor(68, 78, 96, 180))
            g.setColorAt(1, QColor(31, 37, 52, 210))
            big_col = T.TEXT
        p.setBrush(g)
        p.setPen(QPen(T.BORDER, 1.2))
        p.drawRoundedRect(rect, 18, 18)
        p.setFont(font(16, QFont.Black))
        p.setPen(big_col)
        p.drawText(QRectF(rect.x(), rect.y() + 7, rect.width(), 22), Qt.AlignCenter, self.big)
        p.setFont(font(7, QFont.Black, 18))
        p.setPen(T.TEXT_DIM)
        p.drawText(QRectF(rect.x(), rect.y() + 31, rect.width(), 14), Qt.AlignCenter, self.small.upper())


class SectionTitle(QLabel):
    def __init__(self, eyebrow: str, title: str, parent=None):
        super().__init__(parent)
        self.setText(f"<span style='color:#46e8ff; letter-spacing:3px; font-size:10px; font-weight:900'>{eyebrow.upper()}</span><br><span style='font-size:38px; font-weight:1000; color:#ffffff'>{title}</span>")
        self.setTextFormat(Qt.RichText)
        self.setMinimumHeight(82)


class NavBar(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, tabs: Iterable[str], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background:transparent; border:0;")
        self.tabs = list(tabs)
        self.buttons: dict[str, RoundButton] = {}
        self.active = self.tabs[0]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        for tab in self.tabs:
            b = RoundButton(tab, tab == self.active, min_h=42)
            b.clicked.connect(lambda checked=False, t=tab: self.setActive(t, emit=True))
            b.setMinimumWidth(122)
            self.buttons[tab] = b
            lay.addWidget(b)

    def setActive(self, tab: str, emit: bool = False):
        if tab not in self.buttons:
            return
        self.active = tab
        for name, btn in self.buttons.items():
            btn.setActive(name == tab)
        if emit:
            self.changed.emit(tab)


class ThermostatDial(QWidget):
    targetChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current = 71.0
        self.target = 73.0
        self.mode = "auto"
        self.active_mode = "cool"
        self.min_temp = 60.0
        self.max_temp = 80.0
        self.dragging = False
        self.setMinimumSize(360, 360)
        self.setMaximumSize(470, 470)
        self.setCursor(Qt.PointingHandCursor)

    def setData(self, current=None, target=None, mode=None, active_mode=None, limits=None):
        if current is not None:
            try: self.current = float(current)
            except Exception: pass
        if target is not None:
            try: self.target = float(target)
            except Exception: pass
        if mode:
            self.mode = str(mode)
        if active_mode:
            self.active_mode = str(active_mode)
        if isinstance(limits, dict):
            mode_key = str(self.mode or "auto").lower()
            active_key = str(self.active_mode or "").lower()
            range_key = active_key if mode_key == "auto" and active_key in {"cool", "heat"} else mode_key
            lim = limits.get(range_key) or limits.get(mode_key) or limits.get("auto") or {}
            try:
                self.min_temp = float(lim.get("min", 60))
                self.max_temp = float(lim.get("max", 80))
            except Exception:
                self.min_temp = 60.0
                self.max_temp = 80.0
            if self.max_temp <= self.min_temp:
                self.max_temp = self.min_temp + 1
            self.target = max(self.min_temp, min(self.max_temp, self.target))
        self.update()

    def sizeHint(self):
        return QSize(455, 455)

    def _angle_for_temp(self, temp: float) -> float:
        t = max(self.min_temp, min(self.max_temp, float(temp)))
        pct = (t - self.min_temp) / max(1, self.max_temp - self.min_temp)
        return 225 - (270 * pct)

    def _temp_for_pos(self, pos) -> float:
        c = QPointF(self.width() / 2, self.height() / 2)
        dx = pos.x() - c.x()
        dy = pos.y() - c.y()
        ang = math.degrees(math.atan2(-dy, dx))
        # map 225 -> min, -45/315 -> max around the lower gap
        if ang < -45:
            ang += 360
        pct = (225 - ang) / 270
        pct = max(0, min(1, pct))
        return round(self.min_temp + pct * (self.max_temp - self.min_temp))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        side = min(self.width(), self.height()) - 12
        rect = QRectF((self.width() - side) / 2, (self.height() - side) / 2, side, side)
        center = rect.center()

        shadow = QRadialGradient(center, side * 0.54)
        shadow.setColorAt(0.70, QColor(70, 120, 255, 54))
        shadow.setColorAt(1.00, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), shadow)

        p.setBrush(QColor(4, 6, 11))
        p.setPen(QPen(QColor(24, 31, 42), side * 0.045))
        p.drawEllipse(rect.adjusted(side * 0.04, side * 0.04, -side * 0.04, -side * 0.04))

        tick_rect = rect.adjusted(side * 0.12, side * 0.12, -side * 0.12, -side * 0.12)
        c = tick_rect.center()
        radius_outer = tick_rect.width() / 2
        radius_inner = radius_outer - side * 0.08
        heat_active = str(self.active_mode or self.mode).lower() == "heat"
        heat_color = QColor(255, 72, 83)
        cool_color = T.CYAN
        active_color = heat_color if heat_active else cool_color

        def pct_for_temp(temp: float) -> float:
            return max(0.0, min(1.0, (float(temp) - self.min_temp) / max(1.0, self.max_temp - self.min_temp)))

        current_pct = pct_for_temp(self.current)
        target_pct = pct_for_temp(self.target)
        band_low = min(current_pct, target_pct)
        band_high = max(current_pct, target_pct)

        # Base scale ticks stay dim. The highlighted ticks only cover the
        # actual space between current room temp and the selected set temp.
        # This makes the dial read like "where we are" vs "where we are going"
        # instead of filling from the low limit all the way to the target.
        for i in range(91):
            pct = i / 90
            angle = math.radians(225 - pct * 270)
            is_major = i % 10 == 0
            is_mid = i % 5 == 0
            in_band = band_low <= pct <= band_high and abs(current_pct - target_pct) > 0.015
            inner_offset = side * (0.105 if is_major else 0.087 if is_mid else 0.066)
            x1 = c.x() + math.cos(angle) * (radius_outer - inner_offset)
            y1 = c.y() - math.sin(angle) * (radius_outer - inner_offset)
            x2 = c.x() + math.cos(angle) * radius_outer
            y2 = c.y() - math.sin(angle) * radius_outer
            if in_band:
                alpha = 205 if is_major else 180 if is_mid else 145
                col = QColor(active_color.red(), active_color.green(), active_color.blue(), alpha)
                width = 5 if is_major else 4 if is_mid else 3
            else:
                col = QColor(105, 143, 175, 82 if is_major else 58 if is_mid else 40)
                width = 4 if is_major else 3 if is_mid else 2
            p.setPen(QPen(col, width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        arc_rect = rect.adjusted(side * 0.165, side * 0.165, -side * 0.165, -side * 0.165)
        start_angle = int(225 * 16)
        span = int(-270 * 16)
        p.setPen(QPen(QColor(105, 142, 255, 52), side * 0.065, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(arc_rect, start_angle, span)
        target_ang = self._angle_for_temp(self.target)
        current_ang = self._angle_for_temp(self.current)

        # Highlight only the current-to-target band.
        if abs(current_pct - target_pct) > 0.015:
            band_start_ang = max(current_ang, target_ang)
            band_end_ang = min(current_ang, target_ang)
            band_span = int((band_end_ang - band_start_ang) * 16)
            grad_pen = QPen(active_color, side * 0.07, Qt.SolidLine, Qt.RoundCap)
            p.setPen(grad_pen)
            p.drawArc(arc_rect, int(band_start_ang * 16), band_span)

        inner = rect.adjusted(side * 0.29, side * 0.29, -side * 0.29, -side * 0.29)
        g = QRadialGradient(inner.center(), inner.width() / 2)
        if heat_active:
            g.setColorAt(0, QColor(255, 95, 92))
            g.setColorAt(0.58, QColor(232, 55, 70))
            g.setColorAt(1, QColor(126, 26, 44))
        else:
            g.setColorAt(0, QColor(70, 225, 255))
            g.setColorAt(1, QColor(58, 108, 255))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(inner)

        p.setPen(T.TEXT)
        # The mode/equipment text is already shown above the dial, so keep the
        # center clean and make the current temperature the visual focus.
        p.setFont(font(max(50, int(side * 0.162)), QFont.Black))
        p.drawText(QRectF(inner.x(), inner.y() + inner.height() * 0.22, inner.width(), inner.height() * 0.44), Qt.AlignCenter, fmt_temp(self.current).replace("°", ""))
        p.setFont(font(max(16, int(side * 0.039)), QFont.Black))
        p.drawText(QRectF(inner.x() + inner.width() * 0.62, inner.y() + inner.height() * 0.33, 44, 34), Qt.AlignLeft | Qt.AlignVCenter, "°F")

        set_rect = QRectF(inner.x(), inner.y() + inner.height() * 0.66, inner.width(), 34)
        p.setFont(font(12, QFont.Black))
        p.setPen(QColor(224, 241, 255))
        p.drawText(set_rect, Qt.AlignCenter, f"Set Temp  {fmt_temp(self.target)}")

        for label, temp in [(str(int(self.min_temp)), self.min_temp), (str(int(self.max_temp)), self.max_temp)]:
            ang = math.radians(self._angle_for_temp(temp))
            rr = arc_rect.width() / 2 + 10
            x = c.x() + math.cos(ang) * rr
            y = c.y() - math.sin(ang) * rr
            tag = QRectF(x - 22, y - 13, 44, 26)
            p.setBrush(QColor(2, 4, 8, 210))
            p.setPen(QPen(QColor(255,255,255,35),1))
            p.drawRoundedRect(tag, 13, 13)
            p.setFont(font(10, QFont.Black))
            p.setPen(T.TEXT_DIM)
            p.drawText(tag, Qt.AlignCenter, label + "°")

        rr = arc_rect.width() / 2

        # Current room temperature marker.
        current_ang_rad = math.radians(current_ang)
        current_knob = QPointF(c.x() + math.cos(current_ang_rad) * rr, c.y() - math.sin(current_ang_rad) * rr)
        p.setBrush(QColor(active_color.red(), active_color.green(), active_color.blue(), 145))
        p.setPen(QPen(QColor(255, 255, 255, 120), 2))
        p.drawEllipse(current_knob, 8, 8)

        # Target setpoint marker.
        ang = math.radians(target_ang)
        knob = QPointF(c.x() + math.cos(ang) * rr, c.y() - math.sin(ang) * rr)
        p.setBrush(T.TEXT)
        p.setPen(QPen(QColor(active_color.red(), active_color.green(), active_color.blue(), 170), 3))
        p.drawEllipse(knob, 12, 12)

    def mousePressEvent(self, event):
        self.dragging = True
        self.target = self._temp_for_pos(event.pos())
        self.update()

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.target = self._temp_for_pos(event.pos())
            self.update()

    def mouseReleaseEvent(self, event):
        if self.dragging:
            self.dragging = False
            self.target = self._temp_for_pos(event.pos())
            self.targetChanged.emit(self.target)
            self.update()


class HoldCard(GlassPanel):
    clicked = pyqtSignal()
    held = pyqtSignal()

    def __init__(self, parent=None, hold_ms: int = 650, dashed: bool = False):
        super().__init__(parent, radius=24, dashed=dashed)
        self.setCursor(Qt.PointingHandCursor)
        self._held_fired = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(hold_ms)
        self._timer.timeout.connect(self._fire_hold)

    def _fire_hold(self):
        self._held_fired = True
        self.held.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._held_fired = False
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._timer.isActive():
            self._timer.stop()
        if event.button() == Qt.LeftButton and not self._held_fired and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class RoomControlCard(HoldCard):
    def __init__(self, control: dict, parent=None):
        super().__init__(parent, dashed=not bool(control.get("haEntityId")))
        self.control = control
        # The Room page is designed for a 6 x 3 grid on the 10.1" panel.
        # Keep cards compact enough to avoid overlap while still scaling up
        # when the window has more space.
        self.setMinimumSize(132, 142)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def setControl(self, control: dict):
        self.control = control
        self.dashed = not bool(control.get("haEntityId"))
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect())
        active = bool(self.control.get("on"))
        assigned = bool(self.control.get("haEntityId"))

        if active:
            glow = QRadialGradient(QPointF(r.width()*0.28, r.height()*0.28), r.width()*0.58)
            glow.setColorAt(0, QColor(84, 255, 197, 95))
            glow.setColorAt(1, QColor(0,0,0,0))
            p.fillRect(self.rect(), glow)
            p.setPen(QPen(QColor(86, 255, 206, 120), 2))
            p.drawRoundedRect(r.adjusted(1.5,1.5,-1.5,-1.5), self.radius, self.radius)

        pad = max(10, min(18, int(r.width() * 0.10)))
        icon_size = max(42, min(62, int(r.height() * 0.38)))

        # icon glass square
        icon = QRectF(pad, 12, icon_size, icon_size)
        g = QLinearGradient(icon.topLeft(), icon.bottomRight())
        g.setColorAt(0, QColor(98, 140, 152, 94))
        g.setColorAt(1, QColor(42, 54, 71, 170))
        p.setBrush(g)
        p.setPen(QPen(QColor(89, 229, 249, 66), 1.2))
        p.drawRoundedRect(icon, 14, 14)
        p.setFont(font(max(22, int(icon_size * 0.45)), QFont.Black))
        p.setPen(T.GREEN if active else T.CYAN if not assigned else QColor(186, 220, 230, 145))
        symbol = "⏻" if assigned else "+"
        p.drawText(icon, Qt.AlignCenter, symbol)

        badge_w = max(42, min(54, int(r.width() * 0.36)))
        badge = QRectF(r.width()-badge_w-pad, 18, badge_w, 24)
        p.setBrush(QColor(93, 255, 206, 90 if active else 45))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(badge, 14, 14)
        p.setFont(font(7, QFont.Black, 10))
        p.setPen(T.TEXT if active else T.TEXT_DIM)
        p.drawText(badge, Qt.AlignCenter, "ON" if active else "OFF" if assigned else "ASSIGN")

        name = self.control.get("haName") or self.control.get("name") or "Unassigned"
        words = str(name).replace("-", "-").split()
        lines = []
        cur = ""
        for w in words:
            if len(cur + " " + w) > 16 and cur:
                lines.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur: lines.append(cur)
        lines = lines[:2]
        p.setFont(font(max(10, min(14, int(r.width() * 0.085))), QFont.Black))
        p.setPen(T.TEXT if assigned else T.TEXT_DIM)
        p.drawText(QRectF(pad, r.height()-64, r.width()-(pad*2), 36), Qt.AlignLeft | Qt.AlignVCenter, "\n".join(lines))
        p.setFont(font(6, QFont.Black, 10))
        p.setPen(T.TEXT_MUTED)
        p.drawText(QRectF(pad, r.height()-31, r.width()-(pad*2), 13), Qt.AlignLeft, (self.control.get("domain") or "switch").upper() if assigned else "UNASSIGNED")
        p.setPen(T.CYAN)
        p.drawText(QRectF(pad, r.height()-18, r.width()-(pad*2), 13), Qt.AlignLeft, "HOLD TO ASSIGN")
        p.setPen(QPen(QColor(160, 180, 210, 42), 2))
        p.drawLine(pad, int(r.height()-8), int(r.width()-pad), int(r.height()-8))


class LightCard(HoldCard):
    brightnessChanged = pyqtSignal(dict, int)
    colorRequested = pyqtSignal(dict)

    def __init__(self, light: dict, parent=None):
        super().__init__(parent, dashed=not bool(light.get("haEntityId")))
        self.light = light
        self.setMinimumSize(132, 330)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.slider = QSlider(Qt.Vertical, self)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(light.get("brightness") or 0))
        self.slider.setStyleSheet(SLIDER_V)
        self.slider.sliderReleased.connect(self._release)
        self.slider.valueChanged.connect(lambda v: self.update())
        self.slider.setCursor(Qt.PointingHandCursor)

    def resizeEvent(self, event):
        top = 150
        bottom = 74
        self.slider.setGeometry(int(self.width()/2 - 22), top, 44, max(110, self.height() - top - bottom))

    def setLight(self, light: dict):
        self.light = light
        self.dashed = not bool(light.get("haEntityId"))
        val = int(light.get("brightness") or 0)
        if not self.slider.isSliderDown():
            self.slider.setValue(val)
        self.update()

    def _release(self):
        self.brightnessChanged.emit(self.light, int(self.slider.value()))

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect())
        name = self.light.get("haName") or self.light.get("name") or "Light"
        assigned = bool(self.light.get("haEntityId"))
        bright = int(self.slider.value())
        on = bright > 0 and bool(self.light.get("on", bright > 0))
        color = QColor(str(self.light.get("color") or "#ffd76f"))
        if not color.isValid():
            color = T.YELLOW
        if on:
            glow = QRadialGradient(QPointF(r.width()*0.50, r.height()*0.48), r.width()*0.65)
            glow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 88))
            glow.setColorAt(1, QColor(0,0,0,0))
            p.fillRect(self.rect(), glow)
        p.setFont(font(max(11, min(15, int(r.width() * 0.10))), QFont.Black))
        p.setPen(T.TEXT if assigned else T.TEXT_DIM)
        lines = []
        cur = ""
        for w in str(name).split():
            if len((cur + " " + w).strip()) > 14 and cur:
                lines.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur: lines.append(cur)
        p.drawText(QRectF(14, 16, r.width()-28, 54), Qt.AlignLeft | Qt.AlignTop, "\n".join(lines[:2]))
        # bulb plate
        plate = QRectF(r.width()/2 - 30, 82, 60, 60)
        p.setBrush(QColor(73, 86, 105, 130))
        p.setPen(QPen(QColor(180, 200, 225, 43), 1.2))
        p.drawRoundedRect(plate, 16, 16)
        p.setBrush(color if on else QColor(105, 112, 124, 120))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(plate.x()+18, plate.y()+17, 24, 27), 6, 6)
        p.setBrush(T.TEXT if on else QColor(130,140,150,90))
        p.drawRoundedRect(QRectF(plate.x()+16, plate.y()+14, 28, 7), 4, 4)
        if self.light.get("colorSupported"):
            p.setBrush(color)
            p.setPen(QPen(QColor(255,255,255,120),1))
            p.drawEllipse(QPointF(plate.right()-8, plate.bottom()-8), 8, 8)
        p.setFont(font(18, QFont.Black))
        p.setPen(T.TEXT if on else T.TEXT_DIM)
        p.drawText(QRectF(0, r.height()-58, r.width(), 24), Qt.AlignCenter, f"{bright}%")
        p.setFont(font(8, QFont.Black, 18))
        p.setPen(T.TEXT_DIM)
        p.drawText(QRectF(0, r.height()-34, r.width(), 18), Qt.AlignCenter, "BRIGHTNESS")


class BlindPreview(QWidget):
    positionPreviewed = pyqtSignal(int)
    positionRequested = pyqtSignal(int)

    def __init__(self, position: int = 100, parent=None):
        super().__init__(parent)
        self.position = position
        self.dragging = False
        self.setMinimumHeight(230)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)

    def setPosition(self, pos):
        try:
            self.position = max(0, min(100, int(pos)))
        except Exception:
            self.position = 0
        self.update()

    def position_from_pos(self, pos) -> int:
        # Top of preview = open/up/100. Bottom = closed/down/0.
        r = QRectF(self.rect()).adjusted(18, 24, -18, -18)
        pct = 100.0 - ((float(pos.y()) - r.top()) / max(1.0, r.height()) * 100.0)
        return int(max(0, min(100, round(pct))))

    def preview_position_from_pos(self, pos):
        value = self.position_from_pos(pos)
        self.setPosition(value)
        self.positionPreviewed.emit(value)
        return value

    def mousePressEvent(self, event):
        self.dragging = True
        self.preview_position_from_pos(event.pos())
        event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging and event.buttons() & Qt.LeftButton:
            self.preview_position_from_pos(event.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.dragging:
            value = self.preview_position_from_pos(event.pos())
            self.dragging = False
            self.positionRequested.emit(value)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def event(self, event):
        if event.type() in (QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd):
            pts = event.touchPoints()
            if pts:
                value = self.preview_position_from_pos(pts[0].pos().toPoint())
                if event.type() == QEvent.TouchEnd:
                    self.dragging = False
                    self.positionRequested.emit(value)
                else:
                    self.dragging = True
                event.accept()
                return True
        return super().event(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        p.setBrush(QColor(5, 9, 15, 210))
        p.setPen(QPen(QColor(255, 255, 255, 32), 1))
        p.drawRoundedRect(r, 8, 8)

        bg = QLinearGradient(r.topLeft(), r.bottomRight())
        bg.setColorAt(0, QColor(68, 92, 102, 180))
        bg.setColorAt(0.52, QColor(245, 230, 160, 112))
        bg.setColorAt(1, QColor(32, 50, 66, 190))
        inner = r.adjusted(14, 18, -14, -14)
        p.fillRect(inner, bg)

        # Header cassette. Home Assistant cover position is 100=open and 0=closed.
        p.setBrush(QColor(238, 235, 220, 215))
        p.setPen(QPen(QColor(255, 255, 255, 190), 1.6))
        cassette = QRectF(inner.left() + 14, inner.top() + 6, inner.width() - 28, 10)
        p.drawRoundedRect(cassette, 5, 5)

        count = 20
        pos = max(0, min(100, int(self.position)))
        # Draw open as a tight stack at the top, closed as slats filling the window.
        covered_count = int(round(count * (100 - pos) / 100))
        stacked_count = max(2, int(round(7 * pos / 100)))
        gap = (inner.height() - 40) / count

        # The pulled-up stack grows when opening.
        for i in range(stacked_count):
            y = inner.top() + 24 + i * 2.3
            alpha = max(80, 184 - i * 13)
            p.setPen(QPen(QColor(255, 255, 255, alpha), 1.5))
            p.drawLine(QPointF(inner.left() + 22, y), QPointF(inner.right() - 22, y))

        # The lowered section grows downward as the blind closes.
        for i in range(covered_count):
            y = inner.top() + 38 + i * gap
            p.setPen(QPen(QColor(255, 255, 255, 186), 1.45))
            p.drawLine(QPointF(inner.left() + 20, y), QPointF(inner.right() - 20, y))
            p.setPen(QPen(QColor(0, 0, 0, 45), 1))
            p.drawLine(QPointF(inner.left() + 22, y + 2), QPointF(inner.right() - 22, y + 2))

        # Small thumb marker only; no text/guide line.
        thumb_y = inner.top() + (100 - pos) / 100.0 * inner.height()
        thumb = QRectF(inner.right() - 13, thumb_y - 10, 8, 20)
        p.setBrush(QColor(73, 224, 255, 180))
        p.setPen(QPen(QColor(210, 248, 255, 145), 1))
        p.drawRoundedRect(thumb, 4, 4)


class MiniTextKeyboardDialog(QDialog):
    def __init__(self, title: str, value: str = "", parent=None):
        super().__init__(parent)
        self.result_text = str(value or "")
        self.setModal(True)
        self.setWindowTitle(title)
        self.setFixedSize(640, 390)
        self.setStyleSheet("""
            QDialog { background:#09111f; color:#f7fbff; }
            QLabel { color:#f7fbff; font-family:Arial; font-weight:900; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(font(22, QFont.Black))
        root.addWidget(title_label)
        self.display = QLabel("")
        self.display.setAlignment(Qt.AlignCenter)
        self.display.setFont(font(22, QFont.Black))
        self.display.setStyleSheet("background:rgba(255,255,255,0.08); border:1px solid rgba(85,240,255,0.34); border-radius:18px; padding:12px;")
        root.addWidget(self.display)

        rows = [
            "1234567890",
            "QWERTYUIOP",
            "ASDFGHJKL",
            "ZXCVBNM._-",
        ]
        for row_text in rows:
            row = QHBoxLayout()
            row.setSpacing(6)
            for ch in row_text:
                b = QPushButton(ch)
                b.setFixedHeight(42)
                b.setStyleSheet(button_style(False))
                b.clicked.connect(lambda checked=False, c=ch: self.add_char(c.lower()))
                row.addWidget(b)
            root.addLayout(row)

        bottom = QHBoxLayout()
        space = QPushButton("Space")
        back = QPushButton("⌫")
        clear = QPushButton("Clear")
        cancel = QPushButton("Cancel")
        done = QPushButton("Done")
        for b in [space, back, clear, cancel, done]:
            b.setFixedHeight(46)
            b.setStyleSheet(button_style(b is done))
        space.clicked.connect(lambda: self.add_char(" "))
        back.clicked.connect(self.backspace)
        clear.clicked.connect(self.clear_text)
        cancel.clicked.connect(self.reject)
        done.clicked.connect(self.accept)
        bottom.addWidget(space, 2)
        bottom.addWidget(back)
        bottom.addWidget(clear)
        bottom.addWidget(cancel)
        bottom.addWidget(done)
        root.addLayout(bottom)
        self.refresh()

    def refresh(self):
        self.display.setText(self.result_text or "Search")

    def add_char(self, ch: str):
        if len(self.result_text) < 80:
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
        dlg = MiniTextKeyboardDialog(title, value, parent)
        if dlg.exec_() == QDialog.Accepted:
            return dlg.result_text.strip()
        return None



class EntityPickerDialog(QDialog):
    selected = pyqtSignal(dict)

    def __init__(self, title: str, entities: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.entities = sorted(entities or [], key=lambda e: str(e.get("name") or e.get("entityId") or "").lower())
        self.setModal(True)
        self.resize(760, 640)
        self.setStyleSheet("""
            QDialog { background: #0b1020; color: #f6f8ff; }
            QLabel { color: #f6f8ff; font-family: Arial; font-weight: 900; }
            QLineEdit { background: rgba(55,66,86,0.85); color:#f6f8ff; border:1px solid rgba(150,170,205,0.25); border-radius:14px; padding:12px; font-weight:800; }
            QListWidget { background: rgba(28,36,54,0.92); color:#eef3ff; border:1px solid rgba(150,170,205,0.24); border-radius:18px; padding:8px; }
            QListWidget::item { padding:12px; border-bottom:1px solid rgba(255,255,255,0.06); }
            QListWidget::item:selected { background:#3edfff; color:#06121d; border-radius:10px; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22,22,22,22)
        label = QLabel(title)
        label.setFont(font(24, QFont.Black))
        lay.addWidget(label)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Home Assistant entities…")
        self.search.mousePressEvent = lambda event: self.open_search_keyboard()
        lay.addWidget(self.search)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        self.cancel = RoundButton("Cancel")
        self.use = RoundButton("Assign Selected", active=True)
        btns.addStretch(1); btns.addWidget(self.cancel); btns.addWidget(self.use)
        lay.addLayout(btns)
        self.cancel.clicked.connect(self.reject)
        self.use.clicked.connect(self._choose_current)
        self.list.itemDoubleClicked.connect(lambda item: self._choose_item(item))
        self.search.textChanged.connect(self.refresh)
        self.refresh()

    def open_search_keyboard(self):
        value = MiniTextKeyboardDialog.get_text(self, "Search", self.search.text())
        if value is not None:
            self.search.setText(value)
            self.refresh()

    def refresh(self):
        q = self.search.text().strip().lower()
        self.list.clear()
        for e in self.entities:
            name = str(e.get("name") or e.get("friendly_name") or e.get("entityId") or "")
            entity = str(e.get("entityId") or e.get("entity_id") or "")
            domain = str(e.get("domain") or entity.split(".")[0] if "." in entity else "")
            text = f"{name}\n{entity}"
            if q and q not in text.lower():
                continue
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, {"name": name, "entityId": entity, "domain": domain})
            self.list.addItem(item)

    def _choose_current(self):
        item = self.list.currentItem()
        if item:
            self._choose_item(item)

    def _choose_item(self, item: QListWidgetItem):
        self.selected.emit(item.data(Qt.UserRole))
        self.accept()
