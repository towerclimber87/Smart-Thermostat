from __future__ import annotations

import math
from typing import Callable, Iterable

from PyQt5.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
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
        self.setText(f"<span style='color:#46e8ff; letter-spacing:4px; font-size:11px; font-weight:900'>{eyebrow.upper()}</span><br><span style='font-size:46px; font-weight:1000; color:#ffffff'>{title}</span>")
        self.setTextFormat(Qt.RichText)
        self.setMinimumHeight(106)


class NavBar(GlassPanel):
    changed = pyqtSignal(str)

    def __init__(self, tabs: Iterable[str], parent=None):
        super().__init__(parent, radius=28, strong=False)
        self.tabs = list(tabs)
        self.buttons: dict[str, RoundButton] = {}
        self.active = self.tabs[0]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 7, 8, 7)
        lay.setSpacing(8)
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
        self.setMinimumSize(420, 420)
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
            lim = limits.get(self.mode) or limits.get("auto") or {}
            self.min_temp = float(lim.get("min", 60))
            self.max_temp = float(lim.get("max", 80))
        self.update()

    def sizeHint(self):
        return QSize(520, 520)

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
        for i in range(74):
            pct = i / 73
            angle = math.radians(225 - pct * 270)
            x1 = c.x() + math.cos(angle) * radius_inner
            y1 = c.y() - math.sin(angle) * radius_inner
            x2 = c.x() + math.cos(angle) * radius_outer
            y2 = c.y() - math.sin(angle) * radius_outer
            col = QColor(83, 222, 255, 155 if i % 2 == 0 else 88)
            p.setPen(QPen(col, 3 if i % 3 else 4))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        arc_rect = rect.adjusted(side * 0.165, side * 0.165, -side * 0.165, -side * 0.165)
        start_angle = int(225 * 16)
        span = int(-270 * 16)
        p.setPen(QPen(QColor(105, 142, 255, 72), side * 0.08, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(arc_rect, start_angle, span)
        target_ang = self._angle_for_temp(self.target)
        target_span = int((target_ang - 225) * 16)
        grad_pen = QPen(T.PURPLE if self.active_mode == "heat" else T.CYAN, side * 0.08, Qt.SolidLine, Qt.RoundCap)
        p.setPen(grad_pen)
        p.drawArc(arc_rect, start_angle, target_span)

        inner = rect.adjusted(side * 0.29, side * 0.29, -side * 0.29, -side * 0.29)
        g = QRadialGradient(inner.center(), inner.width() / 2)
        if self.active_mode == "heat":
            g.setColorAt(0, QColor(146, 110, 255))
            g.setColorAt(1, QColor(75, 122, 255))
        else:
            g.setColorAt(0, QColor(70, 225, 255))
            g.setColorAt(1, QColor(58, 108, 255))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(inner)

        p.setPen(T.TEXT)
        p.setFont(font(max(11, int(side * 0.027)), QFont.Black, 12))
        mode_label = "AUTO " + (self.active_mode or self.mode).upper() if self.mode == "auto" else self.mode.upper()
        p.drawText(QRectF(inner.x(), inner.y() + inner.height() * 0.25, inner.width(), 28), Qt.AlignCenter, mode_label)
        p.setFont(font(max(52, int(side * 0.17)), QFont.Black))
        p.drawText(QRectF(inner.x(), inner.y() + inner.height() * 0.36, inner.width(), inner.height() * 0.32), Qt.AlignCenter, fmt_temp(self.current).replace("°", ""))
        p.setFont(font(max(18, int(side * 0.043)), QFont.Black))
        p.drawText(QRectF(inner.x() + inner.width() * 0.63, inner.y() + inner.height() * 0.43, 44, 30), Qt.AlignLeft | Qt.AlignTop, "°F")

        pill = QRectF(inner.center().x() - 76, inner.y() + inner.height() * 0.70, 152, 38)
        p.setBrush(QColor(31, 112, 218, 165))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(pill, 18, 18)
        p.setFont(font(12, QFont.Black))
        p.setPen(T.TEXT)
        p.drawText(pill, Qt.AlignCenter, f"Set Temp  {fmt_temp(self.target)}")

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

        ang = math.radians(target_ang)
        rr = arc_rect.width() / 2
        knob = QPointF(c.x() + math.cos(ang) * rr, c.y() - math.sin(ang) * rr)
        p.setBrush(T.TEXT)
        p.setPen(Qt.NoPen)
        p.drawEllipse(knob, 11, 11)

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
        self.setMinimumSize(260, 260)

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

        # icon glass square
        icon = QRectF(26, 24, 96, 96)
        g = QLinearGradient(icon.topLeft(), icon.bottomRight())
        g.setColorAt(0, QColor(98, 140, 152, 94))
        g.setColorAt(1, QColor(42, 54, 71, 170))
        p.setBrush(g)
        p.setPen(QPen(QColor(89, 229, 249, 66), 1.2))
        p.drawRoundedRect(icon, 18, 18)
        p.setFont(font(42, QFont.Black))
        p.setPen(T.GREEN if active else T.CYAN if not assigned else QColor(186, 220, 230, 145))
        symbol = "⏻" if assigned else "+"
        p.drawText(icon, Qt.AlignCenter, symbol)

        badge = QRectF(r.width()-78, 26, 54, 28)
        p.setBrush(QColor(93, 255, 206, 90 if active else 45))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(badge, 14, 14)
        p.setFont(font(8, QFont.Black, 10))
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
        p.setFont(font(19, QFont.Black))
        p.setPen(T.TEXT if assigned else T.TEXT_DIM)
        p.drawText(QRectF(24, r.height()-96, r.width()-42, 54), Qt.AlignLeft | Qt.AlignVCenter, "\n".join(lines))
        p.setFont(font(8, QFont.Black, 12))
        p.setPen(T.TEXT_MUTED)
        p.drawText(QRectF(24, r.height()-54, r.width()-42, 18), Qt.AlignLeft, (self.control.get("domain") or "switch").upper() if assigned else "UNASSIGNED")
        p.setPen(T.CYAN)
        p.drawText(QRectF(24, r.height()-32, r.width()-42, 18), Qt.AlignLeft, "HOLD TO ASSIGN")
        p.setPen(QPen(QColor(160, 180, 210, 42), 2))
        p.drawLine(24, int(r.height()-17), int(r.width()-24), int(r.height()-17))


class LightCard(HoldCard):
    brightnessChanged = pyqtSignal(dict, int)
    colorRequested = pyqtSignal(dict)

    def __init__(self, light: dict, parent=None):
        super().__init__(parent, dashed=not bool(light.get("haEntityId")))
        self.light = light
        self.setMinimumSize(230, 490)
        self.slider = QSlider(Qt.Vertical, self)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(light.get("brightness") or 0))
        self.slider.setStyleSheet(SLIDER_V)
        self.slider.sliderReleased.connect(self._release)
        self.slider.valueChanged.connect(lambda v: self.update())
        self.slider.setCursor(Qt.PointingHandCursor)

    def resizeEvent(self, event):
        self.slider.setGeometry(int(self.width()/2 - 25), 205, 50, max(170, self.height() - 295))

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
        p.setFont(font(21, QFont.Black))
        p.setPen(T.TEXT if assigned else T.TEXT_DIM)
        lines = []
        cur = ""
        for w in str(name).split():
            if len((cur + " " + w).strip()) > 14 and cur:
                lines.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur: lines.append(cur)
        p.drawText(QRectF(22, 20, r.width()-44, 70), Qt.AlignLeft | Qt.AlignTop, "\n".join(lines[:2]))
        # bulb plate
        plate = QRectF(r.width()/2 - 36, 104, 72, 72)
        p.setBrush(QColor(73, 86, 105, 130))
        p.setPen(QPen(QColor(180, 200, 225, 43), 1.2))
        p.drawRoundedRect(plate, 20, 20)
        p.setBrush(color if on else QColor(105, 112, 124, 120))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(plate.x()+22, plate.y()+20, 28, 32), 6, 6)
        p.setBrush(T.TEXT if on else QColor(130,140,150,90))
        p.drawRoundedRect(QRectF(plate.x()+19, plate.y()+17, 34, 8), 4, 4)
        if self.light.get("colorSupported"):
            p.setBrush(color)
            p.setPen(QPen(QColor(255,255,255,120),1))
            p.drawEllipse(QPointF(plate.right()-8, plate.bottom()-8), 8, 8)
        p.setFont(font(22, QFont.Black))
        p.setPen(T.TEXT if on else T.TEXT_DIM)
        p.drawText(QRectF(0, r.height()-70, r.width(), 28), Qt.AlignCenter, f"{bright}%")
        p.setFont(font(8, QFont.Black, 18))
        p.setPen(T.TEXT_DIM)
        p.drawText(QRectF(0, r.height()-43, r.width(), 20), Qt.AlignCenter, "BRIGHTNESS")


class BlindPreview(QWidget):
    def __init__(self, position: int = 100, parent=None):
        super().__init__(parent)
        self.position = position
        self.setMinimumHeight(250)

    def setPosition(self, pos):
        try: self.position = int(pos)
        except Exception: self.position = 0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(4,4,-4,-4)
        p.setBrush(QColor(5, 9, 15, 210))
        p.setPen(QPen(QColor(255,255,255,32),1))
        p.drawRoundedRect(r, 8, 8)
        bg = QLinearGradient(r.topLeft(), r.bottomRight())
        bg.setColorAt(0, QColor(68,92,102,180))
        bg.setColorAt(0.52, QColor(245,230,160,110))
        bg.setColorAt(1, QColor(32,50,66,190))
        inner = r.adjusted(14, 18, -14, -14)
        p.fillRect(inner, bg)
        p.setPen(QPen(QColor(255,255,255,180), 2))
        top = inner.top() + 8
        p.drawRoundedRect(QRectF(inner.left()+16, top, inner.width()-32, 8), 4, 4)
        count = 18
        visible = max(2, int(count * max(0, min(100, self.position)) / 100))
        gap = (inner.height()-42) / count
        for i in range(visible):
            y = inner.top()+30+i*gap
            p.setPen(QPen(QColor(255,255,255,185), 1.4))
            p.drawLine(QPointF(inner.left()+20, y), QPointF(inner.right()-20, y-1))
            p.setPen(QPen(QColor(0,0,0,40), 1))
            p.drawLine(QPointF(inner.left()+22, y+2), QPointF(inner.right()-22, y+1))


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
