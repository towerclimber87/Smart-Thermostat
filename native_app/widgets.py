from __future__ import annotations

import math
from typing import Callable, Iterable

from PyQt5.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractItemView,
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
    QScroller,
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


def fit_dialog_to_available_screen(dialog: QDialog, margin: int = 0):
    screen = dialog.screen() or QApplication.primaryScreen()
    if not screen:
        return
    geo = screen.availableGeometry()
    margin = max(0, int(margin))
    dialog.resize(max(320, geo.width() - (margin * 2)), max(260, geo.height() - (margin * 2)))
    dialog.move(geo.x() + margin, geo.y() + margin)


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
        inside = event.button() == Qt.LeftButton and self.rect().contains(event.pos())
        # Let QAbstractButton emit the normal clicked() signal exactly once.
        # Emitting clicked() here and then calling the base implementation made
        # touchscreen plus/minus buttons fire twice, which changed the main
        # thermostat setpoint by 2° per tap instead of 1°.
        super().mouseReleaseEvent(event)
        if inside:
            self.clickedValue.emit(self.value)


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
        self.off_mode = False
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
        if target is not None and not self.dragging:
            try: self.target = float(target)
            except Exception: pass
        if mode:
            self.mode = str(mode)
        if active_mode:
            self.active_mode = str(active_mode)
        self.off_mode = str(self.mode or "").strip().lower() == "off"
        self.setCursor(Qt.ArrowCursor if self.off_mode else Qt.PointingHandCursor)
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
        heat_active = (not self.off_mode) and str(self.active_mode or self.mode).lower() == "heat"
        heat_color = QColor(255, 72, 83)
        cool_color = T.CYAN
        neutral_color = QColor(182, 196, 218)
        active_color = neutral_color if self.off_mode else (heat_color if heat_active else cool_color)

        def pct_for_temp(temp: float) -> float:
            return max(0.0, min(1.0, (float(temp) - self.min_temp) / max(1.0, self.max_temp - self.min_temp)))

        current_pct = pct_for_temp(self.current)
        target_pct = pct_for_temp(self.target)
        band_low = min(current_pct, target_pct)
        band_high = max(current_pct, target_pct)
        has_gap = (not self.off_mode) and abs(current_pct - target_pct) > 0.015

        # Home Assistant-style progress:
        # - the full dial track stays neutral gray
        # - a faint heat/cool color runs only from the low end to the first existing marker
        # - the existing current-to-target band stays the stronger/darker color
        # Nothing after the second marker should get the faint active color.
        faint_band_high = band_low if has_gap else current_pct

        for i in range(91):
            pct = i / 90
            angle = math.radians(225 - pct * 270)
            is_major = i % 10 == 0
            is_mid = i % 5 == 0
            in_dark_band = (not self.off_mode) and band_low <= pct <= band_high and has_gap
            in_faint_band = (not self.off_mode) and pct <= faint_band_high + 1e-6 and not in_dark_band
            inner_offset = side * (0.105 if is_major else 0.087 if is_mid else 0.066)
            x1 = c.x() + math.cos(angle) * (radius_outer - inner_offset)
            y1 = c.y() - math.sin(angle) * (radius_outer - inner_offset)
            x2 = c.x() + math.cos(angle) * radius_outer
            y2 = c.y() - math.sin(angle) * radius_outer
            if in_dark_band:
                alpha = 230 if is_major else 205 if is_mid else 175
                col = QColor(active_color.red(), active_color.green(), active_color.blue(), alpha)
                width = 5 if is_major else 4 if is_mid else 3
            elif in_faint_band:
                alpha = 78 if is_major else 62 if is_mid else 46
                col = QColor(active_color.red(), active_color.green(), active_color.blue(), alpha)
                width = 5 if is_major else 4 if is_mid else 3
            else:
                col = QColor(112, 118, 126, 58 if is_major else 40 if is_mid else 26)
                width = 4 if is_major else 3 if is_mid else 2
            p.setPen(QPen(col, width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        arc_rect = rect.adjusted(side * 0.165, side * 0.165, -side * 0.165, -side * 0.165)
        start_angle = int(225 * 16)
        span = int(-270 * 16)
        p.setPen(QPen(QColor(106, 112, 120, 36), side * 0.065, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(arc_rect, start_angle, span)
        target_ang = self._angle_for_temp(self.target)
        current_ang = self._angle_for_temp(self.current)

        def angle_for_pct(pct: float) -> float:
            pct = max(0.0, min(1.0, pct))
            return 225 - (270 * pct)

        # Faint mode-colored track from the low end only to the first marker.
        # This is intentionally not a full-scale color wash.
        if (not self.off_mode) and faint_band_high > 0.001:
            faint_end_ang = angle_for_pct(faint_band_high)
            faint_span = int((faint_end_ang - 225) * 16)
            p.setPen(QPen(QColor(active_color.red(), active_color.green(), active_color.blue(), 66), side * 0.07, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc_rect, int(225 * 16), faint_span)

        # Strong highlight only between the current and target markers.
        if has_gap:
            band_start_ang = angle_for_pct(band_low)
            band_end_ang = angle_for_pct(band_high)
            band_span = int((band_end_ang - band_start_ang) * 16)
            grad_pen = QPen(active_color, side * 0.07, Qt.SolidLine, Qt.RoundCap)
            p.setPen(grad_pen)
            p.drawArc(arc_rect, int(band_start_ang * 16), band_span)

        inner = rect.adjusted(side * 0.29, side * 0.29, -side * 0.29, -side * 0.29)
        g = QRadialGradient(inner.center(), inner.width() / 2)
        if self.off_mode:
            g.setColorAt(0, QColor(48, 58, 72))
            g.setColorAt(0.58, QColor(30, 38, 50))
            g.setColorAt(1, QColor(12, 16, 24))
        elif heat_active:
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
        # center clean and make the current temperature the visual focus. Draw
        # the number and °F as one measured group so the unit never sits on top
        # of the last digit when the value gets wider.
        current_text = fmt_temp(self.current).replace("°", "")
        number_font = font(max(50, int(side * 0.162)), QFont.Black)
        unit_font = font(max(16, int(side * 0.039)), QFont.Black)
        number_rect = QRectF(inner.x(), inner.y() + inner.height() * 0.22, inner.width(), inner.height() * 0.44)

        p.setFont(number_font)
        number_w = p.fontMetrics().horizontalAdvance(current_text)
        p.setFont(unit_font)
        unit_w = p.fontMetrics().horizontalAdvance("°F")
        unit_gap = max(10, int(side * 0.026))
        group_w = number_w + unit_gap + unit_w
        number_left = inner.center().x() - group_w / 2

        p.setFont(number_font)
        p.drawText(QRectF(number_left, number_rect.y(), number_w, number_rect.height()), Qt.AlignRight | Qt.AlignVCenter, current_text)
        p.setFont(unit_font)
        p.drawText(QRectF(number_left + number_w + unit_gap, inner.y() + inner.height() * 0.315, max(52, unit_w + 10), 36), Qt.AlignLeft | Qt.AlignVCenter, "°F")

        if not self.off_mode:
            set_rect = QRectF(inner.x(), inner.y() + inner.height() * 0.66, inner.width(), 34)
            p.setFont(font(12, QFont.Black))
            p.setPen(QColor(224, 241, 255))
            p.drawText(set_rect, Qt.AlignCenter, f"Set Temp  {fmt_temp(self.target)}")

        for label, temp in ([] if self.off_mode else [(str(int(self.min_temp)), self.min_temp), (str(int(self.max_temp)), self.max_temp)]):
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
        p.setBrush(QColor(active_color.red(), active_color.green(), active_color.blue(), 120 if self.off_mode else 145))
        p.setPen(QPen(QColor(255, 255, 255, 120), 2))
        p.drawEllipse(current_knob, 8, 8)

        # Target setpoint marker.
        if not self.off_mode:
            ang = math.radians(target_ang)
            knob = QPointF(c.x() + math.cos(ang) * rr, c.y() - math.sin(ang) * rr)
            p.setBrush(T.TEXT)
            p.setPen(QPen(QColor(active_color.red(), active_color.green(), active_color.blue(), 170), 3))
            p.drawEllipse(knob, 12, 12)
            if self.dragging:
                self._draw_drag_bubble(p, c, rr, side, active_color)

    def mousePressEvent(self, event):
        if self.off_mode:
            return
        self.dragging = True
        self.target = self._temp_for_pos(event.pos())
        self.update()

    def mouseMoveEvent(self, event):
        if self.off_mode:
            return
        if self.dragging:
            self.target = self._temp_for_pos(event.pos())
            self.update()

    def _draw_drag_bubble(self, p: QPainter, center: QPointF, arc_radius: float, side: float, active_color: QColor):
        """Draw a large live setpoint badge just outside the dial while dragging.

        The user's finger is usually on the arc/knob, so the center setpoint text
        can be covered.  This badge rides the same temperature angle as the knob
        but is pulled outward and clamped inside the widget so it remains visible
        on the 10-inch touchscreen.
        """
        target_ang_rad = math.radians(self._angle_for_temp(self.target))
        knob = QPointF(
            center.x() + math.cos(target_ang_rad) * arc_radius,
            center.y() - math.sin(target_ang_rad) * arc_radius,
        )
        bubble_w = max(92.0, side * 0.225)
        bubble_h = max(54.0, side * 0.132)
        lift = max(42.0, side * 0.105)
        bubble_center = QPointF(
            center.x() + math.cos(target_ang_rad) * (arc_radius + lift),
            center.y() - math.sin(target_ang_rad) * (arc_radius + lift),
        )

        margin = 8.0
        bubble_center.setX(max(margin + bubble_w / 2, min(self.width() - margin - bubble_w / 2, bubble_center.x())))
        bubble_center.setY(max(margin + bubble_h / 2, min(self.height() - margin - bubble_h / 2, bubble_center.y())))
        bubble = QRectF(
            bubble_center.x() - bubble_w / 2,
            bubble_center.y() - bubble_h / 2,
            bubble_w,
            bubble_h,
        )

        glow = QRadialGradient(bubble.center(), max(bubble_w, bubble_h) * 0.78)
        glow.setColorAt(0.0, QColor(active_color.red(), active_color.green(), active_color.blue(), 115))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), glow)

        # Small connector makes it clear which point on the curve is being set.
        p.setPen(QPen(QColor(active_color.red(), active_color.green(), active_color.blue(), 125), max(2, int(side * 0.008)), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(knob, bubble_center)

        bg = QLinearGradient(bubble.topLeft(), bubble.bottomRight())
        bg.setColorAt(0.0, QColor(255, 255, 255, 244))
        bg.setColorAt(1.0, QColor(210, 232, 255, 232))
        p.setBrush(bg)
        p.setPen(QPen(QColor(active_color.red(), active_color.green(), active_color.blue(), 210), max(2, int(side * 0.007))))
        p.drawRoundedRect(bubble, bubble_h / 2, bubble_h / 2)

        p.setFont(font(max(24, int(side * 0.073)), QFont.Black))
        p.setPen(QColor(8, 18, 32))
        p.drawText(bubble, Qt.AlignCenter, fmt_temp(self.target))

    def mouseReleaseEvent(self, event):
        if self.off_mode:
            return
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

    def _icon_kind(self) -> str:
        entity_id = str(self.control.get("haEntityId") or "").lower()
        domain = str(self.control.get("domain") or (entity_id.split(".", 1)[0] if "." in entity_id else "switch")).lower()
        icon = str(self.control.get("icon") or "").lower()
        device_class = str(self.control.get("deviceClass") or self.control.get("device_class") or "").lower()
        name = str(self.control.get("haName") or self.control.get("name") or entity_id).lower()
        hay = " ".join([icon, device_class, domain, name, entity_id])
        if not self.control.get("haEntityId"):
            return "assign"
        if domain == "lock" or "lock" in hay:
            return "lock"
        if domain == "cover" or any(x in hay for x in ("blind", "shade", "shutter", "garage", "curtain", "door")):
            return "cover"
        if domain == "fan" or "fan" in hay:
            return "fan"
        if domain == "light" or any(x in hay for x in ("light", "lamp", "bulb", "sconce")):
            return "light"
        if any(x in hay for x in ("tv", "television", "projector", "display", "screen")):
            return "screen"
        if any(x in hay for x in ("outlet", "plug", "socket")):
            return "outlet"
        if domain in {"button", "input_button", "scene", "script"}:
            return "button"
        return "switch"

    def _draw_room_icon(self, p: QPainter, rect: QRectF, kind: str, active: bool, assigned: bool):
        fg = T.GREEN if active else (T.CYAN if not assigned else QColor(210, 232, 240, 170))
        p.setPen(QPen(fg, max(2.0, rect.width() * 0.045), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        cx, cy = rect.center().x(), rect.center().y()
        w, h = rect.width(), rect.height()

        if kind == "assign":
            p.drawLine(QPointF(cx - w * 0.18, cy), QPointF(cx + w * 0.18, cy))
            p.drawLine(QPointF(cx, cy - h * 0.18), QPointF(cx, cy + h * 0.18))
            return

        if kind == "light":
            p.drawEllipse(QRectF(cx - w * 0.19, cy - h * 0.25, w * 0.38, h * 0.38))
            p.drawLine(QPointF(cx - w * 0.10, cy + h * 0.03), QPointF(cx + w * 0.10, cy + h * 0.03))
            p.drawLine(QPointF(cx - w * 0.08, cy + h * 0.14), QPointF(cx + w * 0.08, cy + h * 0.14))
            p.drawLine(QPointF(cx - w * 0.05, cy + h * 0.24), QPointF(cx + w * 0.05, cy + h * 0.24))
            return

        if kind == "fan":
            p.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))
            for angle in (90, 210, 330):
                a = math.radians(angle)
                bx, by = math.cos(a), math.sin(a)
                path = QPainterPath(QPointF(cx + bx * w * 0.07, cy + by * h * 0.07))
                path.quadTo(QPointF(cx + bx * w * 0.29 - by * w * 0.08, cy + by * h * 0.29 + bx * h * 0.08),
                            QPointF(cx + bx * w * 0.35, cy + by * h * 0.35))
                p.drawPath(path)
            return

        if kind == "lock":
            unlocked = str(self.control.get("state") or "").lower() in {"unlocked", "open"}
            if unlocked:
                p.drawArc(QRectF(cx - w * 0.30, cy - h * 0.36, w * 0.34, h * 0.34), 25 * 16, 155 * 16)
            else:
                p.drawArc(QRectF(cx - w * 0.22, cy - h * 0.36, w * 0.44, h * 0.40), 0, 180 * 16)
            body = QRectF(cx - w * 0.28, cy - h * 0.06, w * 0.56, h * 0.38)
            p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 30))
            p.drawRoundedRect(body, 7, 7)
            p.setBrush(fg)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QRectF(cx - 2.4, cy + h * 0.08, 4.8, 4.8))
            return

        if kind == "cover":
            frame = QRectF(cx - w * 0.30, cy - h * 0.31, w * 0.60, h * 0.60)
            p.drawRoundedRect(frame, 4, 4)
            for y in (0.18, 0.02, -0.14):
                p.drawLine(QPointF(frame.left() + 4, cy + h * y), QPointF(frame.right() - 4, cy + h * y))
            return

        if kind == "screen":
            body = QRectF(cx - w * 0.32, cy - h * 0.24, w * 0.64, h * 0.42)
            p.drawRoundedRect(body, 5, 5)
            p.drawLine(QPointF(cx - w * 0.10, cy + h * 0.28), QPointF(cx + w * 0.10, cy + h * 0.28))
            p.drawLine(QPointF(cx, cy + h * 0.18), QPointF(cx, cy + h * 0.28))
            return

        if kind == "outlet":
            p.drawEllipse(QRectF(cx - w * 0.24, cy - h * 0.28, w * 0.48, h * 0.56))
            p.drawLine(QPointF(cx - w * 0.08, cy - h * 0.07), QPointF(cx - w * 0.08, cy + h * 0.08))
            p.drawLine(QPointF(cx + w * 0.08, cy - h * 0.07), QPointF(cx + w * 0.08, cy + h * 0.08))
            return

        if kind == "button":
            path = QPainterPath(QPointF(cx - w * 0.16, cy - h * 0.22))
            path.lineTo(QPointF(cx - w * 0.16, cy + h * 0.22))
            path.lineTo(QPointF(cx + w * 0.20, cy))
            path.closeSubpath()
            p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 35))
            p.drawPath(path)
            return

        # Default modern power/switch glyph.
        p.drawArc(QRectF(cx - w * 0.26, cy - h * 0.21, w * 0.52, h * 0.52), 35 * 16, 290 * 16)
        p.drawLine(QPointF(cx, cy - h * 0.31), QPointF(cx, cy - h * 0.04))

    def _code_protected(self) -> bool:
        if not str(self.control.get("accessCode") or "").strip():
            return False
        required = self.control.get("codeRequiredStates")
        if isinstance(required, dict):
            return any(bool(value) for value in required.values())
        if isinstance(required, (list, tuple, set)):
            return bool(required)
        return True

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect())
        active = bool(self.control.get("on"))
        assigned = bool(self.control.get("haEntityId"))
        protected = self._code_protected()

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
        self._draw_room_icon(p, icon.adjusted(7, 7, -7, -7), self._icon_kind(), active, assigned)

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
        if cur:
            lines.append(cur)
        lines = lines[:2]
        p.setFont(font(max(10, min(14, int(r.width() * 0.085))), QFont.Black))
        p.setPen(T.TEXT if assigned else T.TEXT_DIM)
        p.drawText(QRectF(pad, r.height()-64, r.width()-(pad*2), 38), Qt.AlignLeft | Qt.AlignVCenter, "\n".join(lines))
        p.setFont(font(6, QFont.Black, 10))
        p.setPen(T.TEXT_MUTED)
        domain_text = (self.control.get("domain") or "switch").upper() if assigned else "UNASSIGNED"
        if protected:
            domain_text = f"{domain_text}  •  CODE"
        p.drawText(QRectF(pad, r.height()-24, r.width()-(pad*2), 14), Qt.AlignLeft, domain_text)
        p.setPen(QPen(QColor(160, 180, 210, 42), 2))
        p.drawLine(pad, int(r.height()-8), int(r.width()-pad), int(r.height()-8))


class LightCard(HoldCard):
    brightnessChanged = pyqtSignal(dict, int)
    powerClicked = pyqtSignal(dict)
    colorRequested = pyqtSignal(dict)

    def __init__(self, light: dict, parent=None):
        super().__init__(parent, dashed=not bool(light.get("haEntityId")))
        self.light = light
        # Six cards have to fit across the 10.1" panel. Keep the card narrow,
        # then spend the vertical room on the brightness slider.
        self.setMinimumSize(118, 340)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.slider = QSlider(Qt.Vertical, self)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(light.get("brightness") or 0))
        self.slider.setStyleSheet(SLIDER_V)
        self.slider.sliderReleased.connect(self._release)
        self.slider.valueChanged.connect(self._value_changed)
        self.slider.installEventFilter(self)
        self.slider.setCursor(Qt.PointingHandCursor)
        self._slider_dragging = False

        self._power_pressed = False
        self._power_held = False
        self._power_timer = QTimer(self)
        self._power_timer.setSingleShot(True)
        # Make the RGB long-press feel responsive on the touch panel while
        # still leaving normal quick taps available for simple on/off toggles.
        self._power_timer.setInterval(260)
        self._power_timer.timeout.connect(self._fire_power_hold)

    def _power_rect(self) -> QRectF:
        w = float(self.width())
        return QRectF(w / 2.0 - 36.0, 62.0, 72.0, 56.0)

    def _power_hit_rect(self) -> QRectF:
        return self._power_rect().adjusted(-30.0, -24.0, 30.0, 24.0)

    def _slider_value_from_pos(self, pos) -> int:
        y = max(0.0, min(float(self.slider.height() - 1), float(pos.y())))
        span = max(1.0, float(self.slider.height() - 1))
        pct = 1.0 - (y / span)
        raw = self.slider.minimum() + pct * (self.slider.maximum() - self.slider.minimum())
        return int(max(self.slider.minimum(), min(self.slider.maximum(), round(raw))))

    def _set_slider_from_pos(self, pos):
        self.slider.setValue(self._slider_value_from_pos(pos))

    def eventFilter(self, obj, event):
        if obj is self.slider:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._slider_dragging = True
                self.slider.setSliderDown(True)
                self._set_slider_from_pos(event.pos())
                event.accept()
                return True
            if event.type() == QEvent.MouseMove and self._slider_dragging:
                self._set_slider_from_pos(event.pos())
                event.accept()
                return True
            if event.type() == QEvent.MouseButtonRelease and self._slider_dragging:
                self._set_slider_from_pos(event.pos())
                self._slider_dragging = False
                self._release()
                self.slider.setSliderDown(False)
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        top = 128 if self.height() >= 320 else 118
        # Leave just enough room for the percent/brightness labels; the light
        # page now stretches the card rows, so this gives the slider more throw
        # and pulls it closer to the bottom of the card.
        bottom = 60
        self.slider.setGeometry(int(self.width() / 2 - 21), top, 42, max(78, self.height() - top - bottom))

    def isSliderActive(self) -> bool:
        return bool(self.slider.isSliderDown() or self._slider_dragging)

    def setLight(self, light: dict, preserve_slider: bool = False):
        self.light = light
        self.dashed = not bool(light.get("haEntityId"))
        val = int(light.get("brightness") or 0)
        if not preserve_slider and not self.slider.isSliderDown():
            # External HA updates should not be interpreted as user input.
            was_blocked = self.slider.blockSignals(True)
            self.slider.setValue(val)
            self.slider.blockSignals(was_blocked)
        self.update()

    def _value_changed(self, value: int):
        self.update()
        # While the user is actively sliding, emit live previews so the light can
        # follow the finger. Programmatic setLight() updates are signal-blocked.
        if self.slider.isSliderDown():
            self.brightnessChanged.emit(self.light, int(value))

    def _release(self):
        self.brightnessChanged.emit(self.light, int(self.slider.value()))

    def _fire_power_hold(self):
        self._power_held = True
        self._power_pressed = False
        self.colorRequested.emit(self.light)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._power_hit_rect().contains(QPointF(event.pos())):
            self._power_pressed = True
            self._power_held = False
            self._power_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._power_pressed:
            if self._power_timer.isActive():
                self._power_timer.stop()
            pressed_inside = self._power_hit_rect().contains(QPointF(event.pos()))
            was_held = self._power_held
            self._power_pressed = False
            self._power_held = False
            if pressed_inside and not was_held:
                # Only the dedicated top plate toggles the light.
                self.powerClicked.emit(self.light)
            event.accept()
            return
        super().mouseReleaseEvent(event)

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
            glow = QRadialGradient(QPointF(r.width()*0.50, r.height()*0.50), r.width()*0.70)
            glow.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 94))
            glow.setColorAt(1, QColor(0,0,0,0))
            p.fillRect(self.rect(), glow)

        # Name area. Keep it compact so the power button and slider get the room.
        p.setFont(font(max(10, min(13, int(r.width() * 0.095))), QFont.Black))
        p.setPen(T.TEXT if assigned else T.TEXT_DIM)
        lines = []
        cur = ""
        for w in str(name).replace("-", "-").split():
            if len((cur + " " + w).strip()) > 13 and cur:
                lines.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur: lines.append(cur)
        p.drawText(QRectF(12, 13, r.width()-24, 48), Qt.AlignLeft | Qt.AlignTop, "\n".join(lines[:2]))

        # Dedicated on/off plate. Tapping toggles. Holding opens RGB color picker.
        plate = self._power_rect()
        pg = QLinearGradient(plate.topLeft(), plate.bottomRight())
        if on:
            pg.setColorAt(0, QColor(color.red(), color.green(), color.blue(), 220))
            pg.setColorAt(1, QColor(30, 44, 70, 220))
        else:
            pg.setColorAt(0, QColor(85, 100, 122, 150))
            pg.setColorAt(1, QColor(32, 41, 60, 210))
        p.setBrush(pg)
        p.setPen(QPen(QColor(255,255,255,90 if on else 42), 1.3))
        p.drawRoundedRect(plate, 17, 17)
        p.setFont(font(17, QFont.Black))
        p.setPen(T.TEXT if on else QColor(170, 188, 208, 160))
        p.drawText(plate.adjusted(0, -6, 0, -4), Qt.AlignCenter, "⏻" if assigned else "+")
        p.setFont(font(7, QFont.Black, 12))
        p.setPen(T.TEXT if on else T.TEXT_DIM)
        p.drawText(plate.adjusted(0, 24, 0, -3), Qt.AlignCenter, "ON" if on else "OFF" if assigned else "ASSIGN")
        if self.light.get("colorSupported"):
            p.setBrush(color)
            p.setPen(QPen(QColor(255,255,255,150), 1))
            p.drawEllipse(QPointF(plate.right()-7, plate.bottom()-7), 7, 7)

        p.setFont(font(18, QFont.Black))
        p.setPen(T.TEXT if on else T.TEXT_DIM)
        p.drawText(QRectF(0, r.height()-55, r.width(), 23), Qt.AlignCenter, f"{bright}%")
        p.setFont(font(7, QFont.Black, 16))
        p.setPen(T.TEXT_DIM)
        p.drawText(QRectF(0, r.height()-33, r.width(), 18), Qt.AlignCenter, "BRIGHTNESS")


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
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setMinimumSize(720, 520)
        self.resize(1280, 800)
        self.setStyleSheet("""
            QDialog { background: #0b1020; color: #f6f8ff; }
            QLabel { color: #f6f8ff; font-family: Arial; font-weight: 900; }
            QLineEdit { background: rgba(55,66,86,0.85); color:#f6f8ff; border:1px solid rgba(150,170,205,0.25); border-radius:14px; padding:10px; font-weight:800; }
            QListWidget { background: rgba(28,36,54,0.92); color:#eef3ff; border:1px solid rgba(150,170,205,0.24); border-radius:18px; padding:6px; }
            QListWidget::item { padding:12px; border-bottom:1px solid rgba(255,255,255,0.06); }
            QListWidget::item:selected { background:#3edfff; color:#06121d; border-radius:10px; }
            QScrollBar:vertical { background:rgba(255,255,255,0.06); width:28px; margin:2px; border-radius:14px; }
            QScrollBar::handle:vertical { background:rgba(180,210,255,0.55); min-height:54px; border-radius:12px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12,10,12,10)
        lay.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(10)
        label = QLabel(title)
        label.setFont(font(22, QFont.Black))
        self.cancel = RoundButton("Cancel", active=False, min_h=48)
        self.use = RoundButton("Assign Selected", active=True, min_h=48)
        self.use.setMinimumWidth(170)
        header.addWidget(label, 1)
        header.addWidget(self.cancel)
        header.addWidget(self.use)
        lay.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search Home Assistant entities…")
        self.search.mousePressEvent = lambda event: self.open_search_keyboard()
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setAutoScroll(False)
        try:
            self.list.setWheelScrollLines(6)
        except Exception:
            pass
        try:
            QScroller.grabGesture(self.list.viewport(), QScroller.LeftMouseButtonGesture)
        except Exception:
            pass
        lay.addWidget(self.list, 1)
        self.cancel.clicked.connect(self._cancel)
        self.use.clicked.connect(self._choose_current)
        self.list.itemDoubleClicked.connect(lambda item: self._choose_item(item))
        self.search.textChanged.connect(self.refresh)
        self.refresh()
        QTimer.singleShot(0, self.fit_to_screen)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_to_screen()

    def fit_to_screen(self):
        fit_dialog_to_available_screen(self, margin=0)

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

    def _cancel(self):
        self.reject()
        self.close()

    def _choose_current(self):
        item = self.list.currentItem()
        if item:
            self._choose_item(item)

    def _choose_item(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole)
        self.selected.emit(data)
        # Close on the next event-loop pass so any connected save handler can
        # finish first, but every picker closes after Assign Selected.
        QTimer.singleShot(0, self.accept)
