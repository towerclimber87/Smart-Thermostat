from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont


class T:
    BG = QColor("#070b16")
    BG2 = QColor("#101726")
    PANEL = QColor(37, 47, 65, 176)
    PANEL_STRONG = QColor(49, 61, 78, 198)
    PANEL_DARK = QColor(17, 24, 39, 218)
    BORDER = QColor(121, 139, 164, 76)
    BORDER_DASH = QColor(111, 138, 160, 90)
    TEXT = QColor("#f6f8ff")
    TEXT_DIM = QColor("#b8c1d3")
    TEXT_MUTED = QColor("#8793a9")
    CYAN = QColor("#46dfff")
    CYAN2 = QColor("#4facff")
    PURPLE = QColor("#b983ff")
    GREEN = QColor("#54ffc4")
    GREEN_DARK = QColor("#0e4b3a")
    YELLOW = QColor("#ffdd75")
    ORANGE = QColor("#ff9e59")
    RED = QColor("#ff667b")
    BLUE = QColor("#3c75ff")
    WHITE = QColor("#ffffff")

    RADIUS = 26


def font(size: int, weight: int = QFont.Bold, letter_spacing: float = 0.0) -> QFont:
    f = QFont("Arial")
    f.setStyleStrategy(QFont.PreferAntialias)
    f.setPointSize(size)
    f.setWeight(weight)
    if letter_spacing:
        f.setLetterSpacing(QFont.PercentageSpacing, 100.0 + letter_spacing)
    return f


def css_color(c: QColor) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def button_style(active: bool = False, danger: bool = False, green: bool = False, purple: bool = False) -> str:
    if danger:
        bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ffb457, stop:1 #ff766f)"
        color = "#120d0c"
        border = "rgba(255,255,255,0.22)"
    elif green:
        bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #51ffc8, stop:1 #44c596)"
        color = "#061713"
        border = "rgba(255,255,255,0.20)"
    elif purple:
        bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ead0ff, stop:1 #a86eff)"
        color = "#16091f"
        border = "rgba(255,255,255,0.22)"
    elif active:
        bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #45e3ff, stop:1 #4ba7ff)"
        color = "#031322"
        border = "rgba(255,255,255,0.24)"
    else:
        bg = "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(73,83,101,0.70), stop:1 rgba(29,36,52,0.72))"
        color = "#dbe3f4"
        border = "rgba(157,176,207,0.22)"
    return f"""
    QPushButton {{
        background: {bg};
        color: {color};
        border: 1px solid {border};
        border-radius: 20px;
        padding: 9px 16px;
        font-family: Arial;
        font-weight: 900;
    }}
    QPushButton:pressed {{ padding-top: 11px; padding-bottom: 7px; }}
    QPushButton:disabled {{ color: rgba(210,220,235,0.35); background: rgba(45,52,69,0.35); }}
    """


SLIDER_H = """
QSlider::groove:horizontal { height: 9px; border-radius: 4px; background: rgba(146,154,171,0.38); }
QSlider::sub-page:horizontal { height: 9px; border-radius: 4px; background: #49e6ff; }
QSlider::handle:horizontal { width: 28px; height: 28px; margin: -10px 0; border-radius: 14px; background: #f6f8ff; border: 2px solid rgba(100,130,155,0.55); }
"""

SLIDER_V = """
QSlider::groove:vertical { width: 16px; border-radius: 8px; background: rgba(146,154,171,0.25); }
QSlider::sub-page:vertical { width: 16px; border-radius: 8px; background: rgba(146,154,171,0.25); }
QSlider::add-page:vertical { width: 16px; border-radius: 8px; background: #49e6ff; }
QSlider::handle:vertical { height: 38px; width: 38px; margin: 0 -12px; border-radius: 19px; background: #f8f5ff; border: 2px solid rgba(100,130,155,0.55); }
"""
