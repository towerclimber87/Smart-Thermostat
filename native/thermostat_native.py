#!/usr/bin/env python3
"""Native touchscreen application for the IHA Smart Thermostat appliance.

This display client intentionally does not embed Chromium.  The existing
server.py process remains the local API/control engine, Home Assistant proxy,
update endpoint, config storage, relay controller, and HA discovery endpoint.
The native UI talks to that local API over loopback.
"""
from __future__ import annotations

import json
import math
import os
import queue
import socket
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import request, error

try:
    import tkinter as tk
    from tkinter import font as tkfont, filedialog
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "python3-tk is required for the native thermostat display. "
        "Run scripts/install-pi.sh or install python3-tk."
    ) from exc

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "VERSION"
DEFAULT_API_BASE = os.environ.get("SMART_THERMOSTAT_API", "http://127.0.0.1:8080").rstrip("/")
POLL_MS = int(os.environ.get("SMART_NATIVE_POLL_MS", "1500"))
SLOW_POLL_MS = int(os.environ.get("SMART_NATIVE_SLOW_POLL_MS", "8000"))
THERMOSTAT_ONLY = os.environ.get("SMART_NATIVE_THERMOSTAT_ONLY", "0").strip().lower() not in {"0", "false", "no", "off"}
VISUAL_MODE = os.environ.get("SMART_NATIVE_VISUAL_MODE", "web_parity").strip().lower()
BG = "#121820"
BG_2 = "#171d27"
PANEL = "#1b232d"
PANEL_2 = "#202a36"
PANEL_3 = "#111821"
TEXT = "#f4f7fb"
MUTED = "#9aa6b5"
CYAN = "#48cfff"
CYAN_2 = "#6eeeff"
GREEN = "#4ee083"
YELLOW = "#ffd85a"
ORANGE = "#ff9d42"
RED = "#ff4c5d"
BLUE = "#1f78ff"
BLUE_DARK = "#0a4fc4"
PURPLE = "#22203e"
GRAY = "#314354"
BLACK = "#000000"
CARD_BORDER = "#35404d"
SOFT_BORDER = "#28323f"
DEFAULT_ACCESS_CODE = "3762"


def _version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "native"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "home", "open", "active"}
    return bool(value)


def title_case(value: Any) -> str:
    return str(value or "").replace("_", " ").replace("-", " ").strip().title()


class ApiClient:
    def __init__(self, base: str = DEFAULT_API_BASE) -> None:
        self.base = base.rstrip("/")

    def get(self, path: str, timeout: float = 3.0) -> dict[str, Any]:
        url = f"{self.base}{path}"
        req = request.Request(url, headers={"Accept": "application/json"})
        with request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data or "{}")

    def post(self, path: str, payload: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
        url = f"{self.base}{path}"
        body = json.dumps(payload or {}).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data or "{}")
        except error.HTTPError as exc:
            try:
                data = exc.read().decode("utf-8")
                parsed = json.loads(data or "{}")
            except Exception:
                parsed = {"ok": False, "error": str(exc)}
            return parsed


@dataclass
class ButtonSpec:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str
    action: Callable[[], None]
    fill: str = PANEL_2
    outline: str = "#263b4d"
    text: str = TEXT
    tag: str = ""


class NativeThermostatApp:
    def __init__(self) -> None:
        self.api = ApiClient()
        self.root = tk.Tk()
        self.root.title("IHA Smart Thermostat Native")
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda _e: self.toggle_fullscreen())
        self.root.bind("<Configure>", self.on_configure)
        self.root.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.width = 1280
        self.height = 800
        self.scale = 1.0
        self.buttons: list[ButtonSpec] = []
        self.page = "thermostat"
        self.active_rooms = {"blinds": "", "lights": "", "room": ""}
        self.toast = ""
        self.toast_until = 0.0
        self.locked = False
        self.modal: str | None = None
        self.modal_data: dict[str, Any] = {}
        self.schedule_shortcuts_open = True
        self.code_buffer = ""
        self.status: dict[str, Any] = {}
        self.thermostat: dict[str, Any] = {}
        self.config_payload: dict[str, Any] = {}
        self.config: dict[str, Any] = {}
        self.system_info: dict[str, Any] = {}
        self.hardware: dict[str, Any] = {}
        self.alarm_state: dict[str, Any] | None = None
        self.door_state: dict[str, Any] | None = None
        self.ha_cache: dict[str, Any] = {}
        self.last_error = ""
        self.last_fast_fetch = 0.0
        self.last_slow_fetch = 0.0
        self.pending_jobs: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.current_task = ""
        self.shutdown_event = threading.Event()
        self.canvas.bind("<Button-1>", self.on_touch)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.drag_target = ""
        self.drag_last = 0.0
        self.fonts: dict[tuple[str, int, str], tkfont.Font] = {}
        self._schedule_fetch(initial=True)
        self.root.after(100, self._process_jobs)
        self.root.after(250, self._tick)

    def font(self, size: int, weight: str = "normal", family: str = "DejaVu Sans") -> tkfont.Font:
        key = (family, int(size * self.scale), weight)
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(family=family, size=key[1], weight=weight)
        return self.fonts[key]

    def sx(self, value: float) -> int:
        return int(value * self.width / 1280)

    def sy(self, value: float) -> int:
        return int(value * self.height / 800)

    def on_configure(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self.width = max(320, int(event.width))
            self.height = max(240, int(event.height))
            self.scale = min(self.width / 1280, self.height / 800)
            self.draw()

    def toggle_fullscreen(self) -> None:
        self.root.attributes("-fullscreen", not bool(self.root.attributes("-fullscreen")))

    def shutdown(self) -> None:
        self.shutdown_event.set()
        self.root.destroy()

    def run(self) -> None:
        self.draw()
        self.root.mainloop()

    def _schedule_fetch(self, initial: bool = False) -> None:
        now = time.time()
        if initial or now - self.last_fast_fetch >= POLL_MS / 1000:
            self.last_fast_fetch = now
            self._run_async("refresh", self.fetch_status)
        if initial or now - self.last_slow_fetch >= SLOW_POLL_MS / 1000:
            self.last_slow_fetch = now
            self._run_async("refresh_slow", self.fetch_slow)

    def _tick(self) -> None:
        self._schedule_fetch()
        self.draw()
        if not self.shutdown_event.is_set():
            self.root.after(500, self._tick)

    def _process_jobs(self) -> None:
        while True:
            try:
                kind, payload = self.pending_jobs.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status = payload
                self.thermostat = payload.get("thermostat") or payload.get("climate") or {}
                self.last_error = ""
            elif kind == "slow":
                self.system_info = payload.get("system") or self.system_info
                self.config_payload = payload.get("config_payload") or self.config_payload
                self.config = self.config_payload.get("config", self.config_payload.get("data", {})) if isinstance(self.config_payload, dict) else {}
                self.hardware = payload.get("hardware") or self.hardware
                self.alarm_state = payload.get("alarm")
                self.door_state = payload.get("door")
            elif kind == "error":
                self.last_error = str(payload)
            elif kind == "toast":
                self.show_toast(str(payload))
            elif kind == "busy":
                self.busy = bool(payload.get("busy"))
                self.current_task = payload.get("task", "")
            elif kind == "modal":
                self.modal = payload.get("modal")
                self.modal_data = payload.get("data", {})
            elif kind == "page":
                self.page = str(payload)
            self.draw()
        self.root.after(100, self._process_jobs)

    def _run_async(self, label: str, func: Callable[[], Any]) -> None:
        def runner() -> None:
            try:
                func()
            except Exception as exc:
                self.pending_jobs.put(("error", f"{label}: {exc}"))
        threading.Thread(target=runner, daemon=True).start()

    def _run_busy(self, label: str, func: Callable[[], Any]) -> None:
        if self.busy:
            return
        def runner() -> None:
            self.pending_jobs.put(("busy", {"busy": True, "task": label}))
            try:
                result = func()
                if isinstance(result, str) and result:
                    self.pending_jobs.put(("toast", result))
            except Exception as exc:
                self.pending_jobs.put(("toast", f"{label} failed: {exc}"))
                self.pending_jobs.put(("error", traceback.format_exc(limit=2)))
            finally:
                self.pending_jobs.put(("busy", {"busy": False, "task": ""}))
                self.fetch_status()
        threading.Thread(target=runner, daemon=True).start()

    def fetch_status(self) -> None:
        payload = self.api.get("/api/thermostat/status", timeout=2.5)
        self.pending_jobs.put(("status", payload))

    def fetch_slow(self) -> None:
        output: dict[str, Any] = {}
        try:
            output["system"] = self.api.get("/api/system/info", timeout=2.5)
        except Exception:
            pass
        try:
            output["config_payload"] = self.api.get("/api/config", timeout=2.5)
        except Exception:
            pass
        try:
            output["hardware"] = self.api.get("/api/hardware/status", timeout=4.0)
        except Exception:
            pass
        try:
            output.update(self.fetch_ha_badges())
        except Exception:
            pass
        self.pending_jobs.put(("slow", output))

    def fetch_ha_badges(self) -> dict[str, Any]:
        cfg = self.config or {}
        integrations = cfg.get("integrations", {}) if isinstance(cfg, dict) else {}
        ha = integrations.get("homeAssistant", {}) if isinstance(integrations, dict) else {}
        url = ha.get("url", "")
        token = ha.get("token", "")
        out: dict[str, Any] = {}
        alarm_ent = ha.get("alarmEntity") or cfg.get("alarmEntity")
        door_ent = ha.get("doorEntity") or cfg.get("doorEntity")
        if url and token and alarm_ent:
            data = self.api.post("/api/ha/alarm/states", {"url": url, "token": token, "entityIds": [alarm_ent.get("entityId", alarm_ent) if isinstance(alarm_ent, dict) else alarm_ent]}, timeout=4)
            alarms = data.get("alarms") or []
            if alarms:
                out["alarm"] = alarms[0]
        if url and token and door_ent:
            data = self.api.post("/api/ha/binary_sensor/states", {"url": url, "token": token, "entityIds": [door_ent.get("entityId", door_ent) if isinstance(door_ent, dict) else door_ent]}, timeout=4)
            sensors = data.get("sensors") or []
            if sensors:
                out["door"] = sensors[0]
        return out

    def control(self, patch: dict[str, Any]) -> str:
        result = self.api.post("/api/thermostat/control", patch, timeout=4)
        if not result.get("ok", True):
            raise RuntimeError(result.get("error", "Control failed"))
        return "Updated"

    def show_toast(self, message: str, seconds: float = 4.0) -> None:
        self.toast = message
        self.toast_until = time.time() + seconds

    def button(self, x1: int, y1: int, x2: int, y2: int, label: str, action: Callable[[], None], fill: str = PANEL_2, outline: str = "#2e465b", text: str = TEXT, tag: str = "") -> None:
        self.buttons.append(ButtonSpec(x1, y1, x2, y2, label, action, fill, outline, text, tag))
        self.round_rect(x1, y1, x2, y2, 16, fill, outline, 2)
        self.canvas.create_text((x1+x2)//2, (y1+y2)//2, text=label, fill=text, font=self.font(18, "bold"), justify="center")

    def round_rect(self, x1: int, y1: int, x2: int, y2: int, r: int, fill: str, outline: str = "", width: int = 1) -> None:
        # Canvas polygon with smoothed corners keeps drawing light on the Pi.
        pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2, x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1]
        self.canvas.create_polygon(pts, smooth=True, splinesteps=8, fill=fill, outline=outline, width=width)

    def text(self, x: int, y: int, value: str, size: int = 18, fill: str = TEXT, weight: str = "normal", anchor: str = "center") -> None:
        self.canvas.create_text(x, y, text=value, fill=fill, font=self.font(size, weight), anchor=anchor)

    def draw(self) -> None:
        self.canvas.delete("all")
        self.buttons = []
        self.canvas.configure(bg=BG)
        self.draw_background()
        self.draw_header()
        if not THERMOSTAT_ONLY:
            self.draw_top_nav()
        if THERMOSTAT_ONLY and self.page != "thermostat":
            self.page = "thermostat"
        if self.page == "thermostat":
            self.draw_thermostat()
        elif self.page == "blinds":
            self.draw_blinds()
        elif self.page == "audio":
            self.draw_audio()
        elif self.page == "lights":
            self.draw_lights()
        elif self.page == "room":
            self.draw_room_controls()
        else:
            self.draw_thermostat()
        # Top navigation owns page switching in the native appliance UI.
        if self.last_error:
            self.text(self.sx(20), self.sy(770), self.last_error[:120], 11, RED, anchor="w")
        if self.toast and time.time() < self.toast_until:
            self.draw_toast(self.toast)
        if self.busy:
            self.draw_busy()
        if self.modal:
            self.draw_modal()

    def draw_background(self) -> None:
        # Keep this lightweight: a few large shapes, no blur or per-frame images.
        self.canvas.create_rectangle(0, 0, self.width, self.height, fill=BG, outline="")
        self.canvas.create_oval(self.sx(420), self.sy(210), self.sx(1020), self.sy(760), fill="#15173a", outline="")
        self.canvas.create_oval(self.sx(0), self.sy(0), self.sx(360), self.sy(300), fill="#121c25", outline="")
        self.round_rect(self.sx(12), self.sy(14), self.sx(1268), self.sy(786), self.sx(26), fill="#121821", outline=SOFT_BORDER, width=2)

    def pill(self, x1:int, y1:int, x2:int, y2:int, text:str, fill:str="#232a34", outline:str="#37414d", color:str=TEXT, size:int=14, weight:str="bold", dot:str|None=None) -> None:
        self.round_rect(x1, y1, x2, y2, max(8, (y2-y1)//2), fill, outline, 1)
        tx = (x1+x2)//2
        if dot:
            self.canvas.create_oval(x1+self.sx(14), (y1+y2)//2-self.sy(4), x1+self.sx(22), (y1+y2)//2+self.sy(4), fill=dot, outline="")
            tx += self.sx(8)
        self.text(tx, (y1+y2)//2, text, size, color, weight)

    def label_chip(self, x:int, y:int, label:str, value:str, w:int=118) -> None:
        self.round_rect(self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+22), self.sy(11), "#2a3038", "#343c46", 1)
        self.text(self.sx(x+14), self.sy(y+11), label.upper(), 10, MUTED, "bold", "w")
        self.text(self.sx(x+w-10), self.sy(y+11), value, 13, TEXT, "bold", "e")

    def circle_button(self, cx:int, cy:int, r:int, label:str, action:Callable[[], None], fill:str="#232a34", outline:str="#343e4a", color:str=TEXT, size:int=20) -> None:
        x1,y1,x2,y2 = self.sx(cx-r), self.sy(cy-r), self.sx(cx+r), self.sy(cy+r)
        self.buttons.append(ButtonSpec(x1,y1,x2,y2,label,action,fill,outline,color))
        self.canvas.create_oval(x1,y1,x2,y2,fill=fill,outline=outline,width=2)
        self.text(self.sx(cx), self.sy(cy), label, size, color, "bold")

    def draw_header(self) -> None:
        self.pill(self.sx(28), self.sy(32), self.sx(118), self.sy(62), "🔒  UNLOCKED" if not self.locked else "🔒  LOCKED", fill="#1c3133", outline="#306468", color="#b9fff3", size=10)
        self.pill(self.sx(1118), self.sy(32), self.sx(1174), self.sy(62), time.strftime("%I:%M %p").lstrip("0"), fill="#2b3038", outline="#3d4551", color=TEXT, size=11)
        self.circle_button(1210, 47, 20, "i", lambda: self.open_info(), fill="#123144", outline="#2c6a82", color=CYAN_2, size=18)
        self.circle_button(1248, 47, 20, "⚙", lambda: self.open_settings(), fill="#242b34", outline="#3b4551", color=MUTED, size=15)


    def draw_top_nav(self) -> None:
        pages = [("blinds", "Blinds"), ("audio", "Audio"), ("thermostat", "Thermostat"), ("lights", "Lights"), ("room", "Room")]
        w, h, gap = 118, 36, 10
        total = w * len(pages) + gap * (len(pages) - 1)
        x = (1280 - total) // 2
        y = 29
        for page, label in pages:
            active = self.page == page
            fill = "#45c8f6" if active else "#202833"
            outline = "#7de7ff" if active else "#3b4654"
            color = BLACK if active else TEXT
            self.button(self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h), label, lambda p=page: self.set_page(p), fill=fill, outline=outline, text=color, tag=f"nav:{page}")
            x += w + gap

    def draw_bottom_nav(self) -> None:
        pages = [("thermostat", "Thermostat"), ("blinds", "Blinds"), ("audio", "Audio"), ("lights", "Lights"), ("room", "Room")]
        w = 230
        start = (1280 - (w * len(pages) + 10 * (len(pages)-1))) // 2
        for i, (page, label) in enumerate(pages):
            x1 = self.sx(start + i*(w+10)); y1 = self.sy(724); x2 = self.sx(start+i*(w+10)+w); y2 = self.sy(782)
            fill = "#16455f" if self.page == page else "#10202d"
            self.button(x1, y1, x2, y2, label, lambda p=page: self.set_page(p), fill=fill, text=TEXT)

    def set_page(self, page: str) -> None:
        if self.locked and page != self.page:
            self.show_toast("Panel locked")
            return
        self.page = page
        self.draw()

    def draw_thermostat(self) -> None:
        t = self.thermostat or {}
        current = as_float(t.get("currentTemp", t.get("current_temperature", 0)), 0)
        target = as_float(t.get("targetTemp", t.get("target_temperature", 0)), 0)
        mode = str(t.get("mode", "cool")).lower()
        action = str(t.get("hvac_action", t.get("hvacAction", "idle"))).lower()
        fan = str(t.get("fan", t.get("fan_mode", "auto"))).lower()
        away = as_bool(t.get("away"))
        relays = t.get("relays") or {}
        outdoor = as_float(t.get("outdoorTemp", t.get("outdoor_temperature", 0)), 0)
        wind = as_float(t.get("outdoorWindSpeed", t.get("outdoor_wind_speed", 0)), 0)
        hum = as_float(t.get("humidity", 0), 0)
        action_label = "Cooling" if action == "cooling" or relays.get("cool") else "Heating" if action == "heating" or relays.get("heat") else "Idle"
        action_color = CYAN if action_label == "Cooling" else RED if action_label == "Heating" else GREEN

        # Status chips + title exactly in the cleaner web style.
        self.label_chip(60, 168, "Outdoor", f"{outdoor:.0f}°", 112)
        self.label_chip(180, 168, "Wind", f"{wind:.0f} mph", 104)
        self.text(self.sx(60), self.sy(244), "Climate Control", 54, TEXT, "bold", "w")
        self.pill(self.sx(604), self.sy(260), self.sx(676), self.sy(290), action_label, fill="#252b36", outline="#3a4351", color=TEXT, size=11, dot=action_color)

        # Center dial.
        self.draw_web_style_dial(current, target, action_label, action_color)

        # Plus/minus controls.
        self.circle_button(378, 446, 32, "−", lambda: self.change_target(-1), fill="#242b34", outline="#3a444f", color=TEXT, size=28)
        self.circle_button(902, 446, 32, "+", lambda: self.change_target(1), fill="#242b34", outline="#3a444f", color=TEXT, size=27)

        # Left/right HA cards.
        self.draw_status_tile(110, 412, 150, 112, "Inside Doors", self.door_label(), "door", GREEN if self.door_label().lower() == "closed" else RED, lambda: self.show_toast("Door status updated from Home Assistant"))
        self.draw_status_tile(1020, 412, 150, 112, "Alarmo", self.alarm_label(), "shield", GREEN, lambda: self.open_alarm())
        self.draw_virtual_outputs(relays, current)

        # Schedule preset shortcuts + bottom controls.
        self.draw_schedule_preset_bar()
        self.circle_button(54, 708, 24, "S", lambda: self.open_schedule(), fill="#143543", outline="#276273", color=TEXT, size=22)
        self.pill(self.sx(244), self.sy(686), self.sx(350), self.sy(732), f"HUMIDITY   {hum:.0f}%", fill="#242b34", outline="#3a444f", color=TEXT, size=15)
        self.segmented_control(500, 686, 360, 46, [("cool","Cool"),("heat","Heat"),("auto","Auto"),("away","Away")], "away" if away else mode, lambda v: self.toggle_away() if v == "away" else self.set_mode(v))
        self.segmented_control(888, 686, 152, 46, [("fan","Fan"),("auto", title_case(fan or "auto"))], "auto", lambda _v: self.set_fan("on" if fan != "on" else "auto"), label_first=True)

    def draw_web_style_dial(self, current: float, target: float, action_label: str, action_color: str) -> None:
        cx, cy = self.sx(640), self.sy(430)
        r = min(self.sx(172), self.sy(172))
        self.canvas.create_oval(cx-r-16, cy-r-16, cx+r+16, cy+r+16, fill="#05080d", outline="#0b1118", width=3)
        self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r, fill="#0b1118", outline="#161e28", width=2)
        # Tick ring, intentionally simple and static for the Pi.
        for i in range(96):
            angle = math.radians(218 + i * 284 / 95)
            length = 20 if i % 6 == 0 else 14
            col = "#4fbfee" if i < 38 else "#34404c"
            x1 = cx + math.cos(angle) * (r - length)
            y1 = cy + math.sin(angle) * (r - length)
            x2 = cx + math.cos(angle) * (r - 5)
            y2 = cy + math.sin(angle) * (r - 5)
            self.canvas.create_line(x1, y1, x2, y2, fill=col, width=2)
        # White position markers like the web dial.
        for deg in (198, 248):
            a = math.radians(deg)
            x1 = cx + math.cos(a) * (r - 12); y1 = cy + math.sin(a) * (r - 12)
            x2 = cx + math.cos(a) * (r + 20); y2 = cy + math.sin(a) * (r + 20)
            self.canvas.create_line(x1, y1, x2, y2, fill="#eaf0f5", width=6, capstyle="round")
        inner = int(r * 0.62)
        self.canvas.create_oval(cx-inner, cy-inner, cx+inner, cy+inner, fill=BLUE_DARK if action_label == "Cooling" else "#a23424" if action_label == "Heating" else "#116146", outline="")
        self.text(cx, cy-self.sy(72), action_label.upper(), 14, "#dceaff", "bold")
        self.text(cx, cy-self.sy(10), f"{current:.0f}°", 72, TEXT, "bold")
        self.pill(cx-self.sx(58), cy+self.sy(62), cx+self.sx(58), cy+self.sy(96), f"Set Temp  {target:.0f}°", fill="#1456b5" if action_label == "Cooling" else "#203c49", outline="#1d6bd2", color=TEXT, size=12)
        self.pill(cx-self.sx(154), cy+self.sy(142), cx-self.sx(108), cy+self.sy(168), "65°", fill="#090d13", outline="#111820", color=TEXT, size=12)
        self.pill(cx+self.sx(108), cy+self.sy(142), cx+self.sx(154), cy+self.sy(168), "80°", fill="#090d13", outline="#111820", color=TEXT, size=12)

    def door_label(self) -> str:
        door = self.door_state or {}
        state = str(door.get("state", "unknown")).lower() if door else "unknown"
        return "Open" if state in {"on", "open", "unlocked"} else "Closed" if state in {"off", "closed", "locked"} else "Closed"

    def alarm_label(self) -> str:
        alarm = self.alarm_state or {}
        return title_case(alarm.get("state", "Disarmed")) if alarm else "Disarmed"

    def draw_status_tile(self, x:int, y:int, w:int, h:int, title:str, state:str, icon:str, accent:str, action:Callable[[],None]) -> None:
        x1,y1,x2,y2 = self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h)
        self.buttons.append(ButtonSpec(x1,y1,x2,y2,title,action,"#06261f","#146455",TEXT))
        self.round_rect(x1,y1,x2,y2,self.sy(22),"#071f1d","#146455",2)
        self.canvas.create_oval(self.sx(x+45), self.sy(y+16), self.sx(x+105), self.sy(y+76), fill="#15312f", outline="#2a625d", width=1)
        if icon == "door":
            self.canvas.create_rectangle(self.sx(x+62), self.sy(y+32), self.sx(x+88), self.sy(y+68), outline="#9ee9d1", width=2)
            self.canvas.create_oval(self.sx(x+82), self.sy(y+50), self.sx(x+86), self.sy(y+54), fill="#9ee9d1", outline="")
        else:
            pts=[self.sx(x+75),self.sy(y+28),self.sx(x+96),self.sy(y+38),self.sx(x+91),self.sy(y+62),self.sx(x+75),self.sy(y+74),self.sx(x+59),self.sy(y+62),self.sx(x+54),self.sy(y+38)]
            self.canvas.create_polygon(pts, fill="#2a8d61", outline="#9ee9d1", width=2)
        self.text(self.sx(x+w//2), self.sy(y+82), title, 14, TEXT, "bold")
        self.pill(self.sx(x+50), self.sy(y+96), self.sx(x+w-50), self.sy(y+118), state.upper(), fill="#163f33", outline="#1e6a52", color="#dbfff0", size=9)

    def draw_virtual_outputs(self, relays: dict[str, Any], current: float) -> None:
        x,y,w,h = 998, 218, 172, 112
        self.round_rect(self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h), self.sy(14), "#222933", "#3d4651", 1)
        self.text(self.sx(x+w//2), self.sy(y+16), "VIRTUAL OUTPUTS", 8, MUTED, "bold")
        labels=[("fan","Fan"),("heat","Heat"),("cool","Cool")]
        for i,(key,label) in enumerate(labels):
            bx=x+12+i*52
            on=as_bool(relays.get(key))
            self.round_rect(self.sx(bx), self.sy(y+30), self.sx(bx+42), self.sy(y+60), self.sy(8), "#164d3d" if on else "#262d36", "#2d755d" if on else "#3a444f", 1)
            self.canvas.create_oval(self.sx(bx+17), self.sy(y+36), self.sx(bx+24), self.sy(y+43), fill=GREEN if on else "#cfd4da", outline="")
            self.text(self.sx(bx+21), self.sy(y+52), label, 7, TEXT, "bold")
        self.text(self.sx(x+12), self.sy(y+82), "VIRTUAL TEMP", 8, MUTED, "bold", "w")
        self.text(self.sx(x+w-18), self.sy(y+82), f"{current:.1f}°", 10, TEXT, "bold", "e")
        self.canvas.create_line(self.sx(x+14), self.sy(y+96), self.sx(x+w-14), self.sy(y+96), fill="#eef2f7", width=1)
        knob_x = self.sx(x+14 + (w-28) * clamp((current-50)/40, 0, 1))
        self.canvas.create_oval(knob_x-self.sx(5), self.sy(y+91), knob_x+self.sx(5), self.sy(y+101), fill=CYAN_2, outline="")

    def segmented_control(self, x:int, y:int, w:int, h:int, items:list[tuple[str,str]], active:str, on_pick:Callable[[str],None], label_first:bool=False) -> None:
        x1,y1,x2,y2 = self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h)
        self.round_rect(x1,y1,x2,y2,self.sy(18),"#222933","#3a444f",1)
        cell = w / len(items)
        for i,(key,label) in enumerate(items):
            bx1=self.sx(x+i*cell+4); bx2=self.sx(x+(i+1)*cell-4)
            if key == active and not label_first:
                self.round_rect(bx1,self.sy(y+5),bx2,self.sy(y+h-5),self.sy(15),CYAN,"#60dbff",1)
                color=BLACK
            else:
                color=MUTED if label_first and i==0 else TEXT if key==active else MUTED
            self.buttons.append(ButtonSpec(bx1,y1,bx2,y2,label,lambda k=key:on_pick(k),"","",color))
            self.text((bx1+bx2)//2, self.sy(y+h/2), label, 14, color, "bold")

    def draw_badges(self, relays: dict[str, Any], action: str) -> None:
        specs = [("Fan", relays.get("fan")), ("Heat", relays.get("heat")), ("Cool", relays.get("cool"))]
        x = 40
        for label, on in specs:
            fill = GREEN if on else "#172735"
            self.round_rect(self.sx(x), self.sy(255), self.sx(x+88), self.sy(306), 14, fill, "#345064", 1)
            self.text(self.sx(x+44), self.sy(280), label, 15, BLACK if on else MUTED, "bold")
            x += 98

    def mode_buttons(self, mode: str) -> None:
        modes = [("cool", "Cool"), ("heat", "Heat"), ("auto", "Auto")]
        y = 145
        for key, label in modes:
            fill = CYAN if key == mode else PANEL_2
            color = BLACK if key == mode else TEXT
            self.button(self.sx(985), self.sy(y), self.sx(1230), self.sy(y+47), label, lambda k=key: self.set_mode(k), fill=fill, text=color)
            y += 52

    def fan_buttons(self, fan: str) -> None:
        modes = [("off", "Off"), ("on", "On"), ("auto", "Auto")]
        x = 985
        for key, label in modes:
            fill = CYAN if key == fan else PANEL_2
            color = BLACK if key == fan else TEXT
            self.button(self.sx(x), self.sy(318), self.sx(x+75), self.sy(376), label, lambda k=key: self.set_fan(k), fill=fill, text=color)
            x += 84

    def draw_alarm_door_pause(self) -> None:
        alarm = self.alarm_state or {}
        door = self.door_state or {}
        alarm_label = title_case(alarm.get("state", "Alarm")) if alarm else "Alarm"
        door_state = str(door.get("state", "unknown")).lower() if door else "unknown"
        door_label = "Open" if door_state in {"on", "open", "unlocked"} else "Closed" if door_state in {"off", "closed", "locked"} else "Door"
        self.button(self.sx(36), self.sy(610), self.sx(290), self.sy(684), f"Alarm\n{alarm_label}", lambda: self.open_alarm(), fill="#40202a" if "trigger" in alarm_label.lower() else "#12293a")
        self.button(self.sx(310), self.sy(610), self.sx(565), self.sy(684), f"Inside Doors\n{door_label}", lambda: self.show_toast("Door status updated from Home Assistant"), fill="#45202a" if door_label == "Open" else "#153525")
        pause = self.thermostat.get("pauseFunction") or {}
        active = as_bool(pause.get("active"))
        self.button(self.sx(585), self.sy(610), self.sx(840), self.sy(684), "Comfort Pause\nActive" if active else "Comfort Pause\nReady", lambda: self.delay_pause(), fill=ORANGE if active else "#12293a")
        self.button(self.sx(860), self.sy(610), self.sx(1115), self.sy(684), "Hardware", lambda: self.open_hardware(), fill="#12293a")
        self.button(self.sx(1135), self.sy(610), self.sx(1245), self.sy(684), "Lock" if not self.locked else "Unlock", lambda: self.toggle_lock(), fill="#12293a")


    def get_schedules(self) -> list[dict[str, Any]]:
        schedules = self.thermostat.get("schedules") or []
        if not schedules and isinstance(self.config, dict):
            schedules = (self.config.get("thermostat") or {}).get("schedules") or []
        return [item for item in schedules if isinstance(item, dict)]

    def schedule_detail(self, sched: dict[str, Any]) -> str:
        mode = str(self.thermostat.get("mode", "cool")).lower()
        if mode == "heat":
            value = sched.get("heatSetpoint", sched.get("heatTarget"))
            return f"{as_float(value, 71):.0f}° Heat" if value is not None else str(sched.get("time", ""))
        value = sched.get("coolSetpoint", sched.get("coolTarget"))
        return f"{as_float(value, 68):.0f}° Cool" if value is not None else str(sched.get("time", ""))

    def draw_schedule_preset_bar(self) -> None:
        schedules = [s for s in self.get_schedules() if s.get("enabled", True) is not False]
        if not schedules:
            return
        # Matches the web preset strip: small named shortcuts above the bottom controls.
        max_items = min(5, len(schedules))
        w, h, gap = 156, 44, 10
        total = w * max_items + gap * (max_items - 1)
        x = (1280 - total) // 2
        y = 622
        for sched in schedules[:max_items]:
            name = str(sched.get("name") or sched.get("label") or "Schedule")[:16]
            detail = self.schedule_detail(sched)
            x1,y1,x2,y2 = self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h)
            self.buttons.append(ButtonSpec(x1,y1,x2,y2,name,lambda s=sched:self.apply_schedule(s),"#132c3e","#275774",TEXT))
            self.round_rect(x1,y1,x2,y2,self.sy(12),"#132c3e","#275774",2)
            self.text((x1+x2)//2, y1+self.sy(15), name, 12, TEXT, "bold")
            self.text((x1+x2)//2, y1+self.sy(32), detail, 10, CYAN_2, "bold")
            x += w + gap

    def draw_schedules(self) -> None:
        schedules = self.thermostat.get("schedules") or []
        if not schedules:
            return
        x = 38
        for sched in schedules[:4]:
            name = str(sched.get("name") or sched.get("label") or "Schedule")[:18]
            self.button(self.sx(x), self.sy(538), self.sx(x+210), self.sy(590), name, lambda s=sched: self.apply_schedule(s), fill="#12293a", text=TEXT)
            x += 220

    def change_target(self, delta: float) -> None:
        target = as_float(self.thermostat.get("targetTemp", self.thermostat.get("target_temperature", 70)), 70) + delta
        limits = self.thermostat.get("limits", {}).get(str(self.thermostat.get("mode", "cool")), {"min": 45, "max": 95})
        target = clamp(target, as_float(limits.get("min"), 45), as_float(limits.get("max"), 95))
        self.thermostat["targetTemp"] = target
        self.draw()
        self._run_busy("Target", lambda: self.control({"targetTemp": target}))

    def set_mode(self, mode: str) -> None:
        if self.locked:
            return self.show_toast("Panel locked")
        self.thermostat["mode"] = mode
        self.draw()
        self._run_busy("Mode", lambda: self.control({"mode": mode}))

    def set_fan(self, fan: str) -> None:
        if self.locked:
            return self.show_toast("Panel locked")
        self.thermostat["fan"] = fan
        self.draw()
        self._run_busy("Fan", lambda: self.control({"fan": fan}))

    def toggle_away(self) -> None:
        if self.locked:
            return self.show_toast("Panel locked")
        next_value = not as_bool(self.thermostat.get("away"))
        self.thermostat["away"] = next_value
        self.draw()
        self._run_busy("Away", lambda: self.control({"away": next_value}))

    def delay_pause(self) -> None:
        self._run_busy("Pause", lambda: self.control({"pauseFunctionResumeMs": int(time.time()*1000) + 5*60*1000}))

    def apply_schedule(self, sched: dict[str, Any]) -> None:
        mode = str(self.thermostat.get("mode", "cool")).lower()
        target = None
        if mode == "heat":
            target = sched.get("heatSetpoint", sched.get("heatTarget"))
        else:
            target = sched.get("coolSetpoint", sched.get("coolTarget"))
        if target is None:
            target = sched.get("targetTemp") or sched.get("temperature")
        if target is None:
            self.show_toast("Schedule has no quick target")
            return
        self.close_modal()
        self._run_busy("Schedule", lambda: self.control({"targetTemp": as_float(target, 70)}))


    def display_name(self, value: Any, fallback: str = "Item") -> str:
        text = str(value or fallback).strip() or fallback
        for prefix in ("Kitchen-Robby-Blinds ", "Fish-Tank-Lights ", "Couch-Coffee ", "Tv-Light "):
            text = text.replace(prefix, "")
        text = text.replace("Livingroom", "Living Room")
        return text

    def room_bundle(self, section: str) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
        if section == "room":
            cfg = self.config.get("roomControl") or self.config.get("roomControls") or self.config.get("room") or {}
        else:
            cfg = self.config.get(section) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        rooms = cfg.get("rooms") or {}
        if not isinstance(rooms, dict):
            rooms = {}
        selected = self.active_rooms.get(section) or cfg.get("room") or next(iter(rooms), "")
        if selected not in rooms and rooms:
            selected = next(iter(rooms))
        self.active_rooms[section] = selected
        room = rooms.get(selected, {}) if selected else {}
        if not isinstance(room, dict):
            room = {}
        return cfg, rooms, selected, room

    def set_active_room(self, section: str, room_key: str) -> None:
        self.active_rooms[section] = room_key
        self.draw()

    def draw_page_shell(self, kicker: str, title: str, section: str | None = None) -> tuple[int, int, int, int]:
        self.round_rect(self.sx(30), self.sy(92), self.sx(1250), self.sy(768), self.sy(26), "#121a24", "#2f3a47", 2)
        self.text(self.sx(50), self.sy(122), kicker.upper(), 11, CYAN_2, "bold", "w")
        self.text(self.sx(50), self.sy(165), title, 42, TEXT, "bold", "w")
        if section:
            _cfg, rooms, selected, _room = self.room_bundle(section)
            keys = list(rooms.keys())
            x = 1220
            for key in reversed(keys[:5]):
                label = str((rooms.get(key) or {}).get("label") or key).strip()[:16]
                w = max(86, min(150, 30 + len(label) * 10))
                active = key == selected
                self.button(self.sx(x - w), self.sy(112), self.sx(x), self.sy(154), label, lambda k=key, sec=section: self.set_active_room(sec, k), fill=CYAN if active else "#232b36", outline="#65dfff" if active else "#3a4552", text=BLACK if active else TEXT)
                x -= w + 12
        return self.sx(48), self.sy(185), self.sx(1232), self.sy(748)

    def draw_blind_visual(self, x: int, y: int, w: int, h: int, position: float) -> None:
        x1, y1, x2, y2 = self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h)
        self.round_rect(x1, y1, x2, y2, self.sy(10), "#071019", "#0e1b26", 2)
        self.canvas.create_rectangle(x1+self.sx(10), y1+self.sy(10), x2-self.sx(10), y2-self.sy(10), fill="#394953", outline="#253541")
        light_alpha = clamp(position / 100.0, 0, 1)
        stripe_w = max(1, self.sx(10))
        for i in range(0, 30):
            sx = x1 + self.sx(20) + i * self.sx(14)
            col = "#d9d2a1" if i % 3 == 1 else "#7f887f"
            if sx < x2-self.sx(20):
                self.canvas.create_rectangle(sx, y1+self.sy(14), min(sx+stripe_w, x2-self.sx(12)), y2-self.sy(14), fill=col, outline="")
        for j in range(17):
            yy = y1 + self.sy(22 + j * 14)
            if yy < y2 - self.sy(18):
                self.canvas.create_line(x1+self.sx(24), yy, x2-self.sx(24), yy, fill="#edf1e8", width=1)
        self.canvas.create_line(x1+self.sx(28), y1+self.sy(20), x2-self.sx(28), y1+self.sy(20), fill="#f3f0df", width=4, capstyle="round")

    def draw_light_icon(self, cx: int, cy: int, on: bool, color: str) -> None:
        r = self.sx(26)
        fill = color if on else "#58636e"
        self.canvas.create_oval(self.sx(cx)-r, self.sy(cy)-r, self.sx(cx)+r, self.sy(cy)+r, fill="#364450", outline="#5a6674", width=2)
        self.canvas.create_rectangle(self.sx(cx-12), self.sy(cy-8), self.sx(cx+12), self.sy(cy+14), fill=fill, outline="")
        self.canvas.create_polygon(self.sx(cx-9), self.sy(cy-8), self.sx(cx+9), self.sy(cy-8), self.sx(cx+5), self.sy(cy-17), self.sx(cx-5), self.sy(cy-17), fill="#f8f0b8" if on else "#68727d", outline="")
        if on:
            self.canvas.create_oval(self.sx(cx-46), self.sy(cy+44), self.sx(cx+46), self.sy(cy+148), fill="#fff4b0", outline="")

    def short_lines(self, text: str, max_chars: int = 18, max_lines: int = 2) -> list[str]:
        words = str(text or "").split()
        lines: list[str] = []
        cur = ""
        for word in words:
            if len((cur + " " + word).strip()) <= max_chars:
                cur = (cur + " " + word).strip()
            else:
                if cur:
                    lines.append(cur)
                cur = word
                if len(lines) >= max_lines - 1:
                    break
        if cur and len(lines) < max_lines:
            lines.append(cur)
        return lines or [str(text or "")[:max_chars]]

    def draw_blinds(self) -> None:
        _cfg, rooms, _room_key, room = self.room_bundle("blinds")
        room_label = str(room.get("label") or "Living Room")
        blinds = [b for b in (room.get("blinds") or []) if isinstance(b, dict)]
        self.draw_page_shell("Shade Control", room_label, "blinds")
        self.button(self.sx(515), self.sy(212), self.sx(635), self.sy(254), "Open Room", lambda: self.cover_room(blinds, "open"), fill=CYAN, outline="#65dfff", text=BLACK)
        self.button(self.sx(645), self.sy(212), self.sx(765), self.sy(254), "Close Room", lambda: self.cover_room(blinds, "close"), fill="#c79bff", outline="#dcbaff", text=BLACK)
        if not blinds:
            self.text(self.sx(640), self.sy(415), "No blinds configured for this room.", 22, MUTED, "bold")
            return
        card_w, card_h = 285, 390
        start_x, start_y = 58, 285
        gap = 16
        for i, blind in enumerate(blinds[:4]):
            x = start_x + i * (card_w + gap)
            y = start_y
            pos = as_float(blind.get("position"), 100)
            x1,y1,x2,y2 = self.sx(x), self.sy(y), self.sx(x+card_w), self.sy(y+card_h)
            self.round_rect(x1,y1,x2,y2,self.sy(18),"#1b242e","#313b47",2)
            self.text(self.sx(x+15), self.sy(y+26), self.display_name(blind.get("name"), "Blind")[:24], 13, TEXT, "bold", "w")
            self.text(self.sx(x+card_w-15), self.sy(y+26), f"{pos:.0f}%", 13, "#f4e8b7", "bold", "e")
            self.button(self.sx(x+14), self.sy(y+44), self.sx(x+card_w-14), self.sy(y+76), "Open", lambda b=blind: self.cover_action(b, "open"), fill="#f1e2ad", outline="#fff1bd", text="#1b1710")
            self.draw_blind_visual(x+14, y+90, card_w-28, 255, pos)
            self.button(self.sx(x+14), self.sy(y+354), self.sx(x+card_w-14), self.sy(y+382), "Close", lambda b=blind: self.cover_action(b, "close"), fill="#e7e2d8", outline="#f6f2ea", text="#171717")

    def cover_room(self, blinds: list[dict[str, Any]], action: str) -> None:
        for b in blinds:
            if b.get("haEntityId"):
                self.cover_action(b, action, quiet=True)
        self.show_toast(f"Room {action} sent")

    def cover_action(self, blind: dict[str, Any], action: str, quiet: bool = False) -> None:
        ha = self._ha()
        if not ha:
            return self.show_toast("Home Assistant is not configured")
        entity = blind.get("haEntityId")
        if not entity:
            return self.show_toast("Blind has no HA entity")
        def run() -> str:
            result = self.api.post("/api/ha/cover/action", {"url": ha[0], "token": ha[1], "entityId": entity, "action": action}, timeout=8)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Cover command failed"))
            return "Sent" if not quiet else ""
        self._run_busy("Blind", run)

    def draw_audio(self) -> None:
        self.draw_page_shell("Audio Control", "Livingroom Sonos", None)
        integrations = self.config.get("integrations", {}) or {}
        ha = integrations.get("homeAssistant", {}) or {}
        player = ha.get("selectedMediaPlayerId") or "media_player.livingroom_sonos"
        controls = ha.get("audioControlEntities") or {}
        self.round_rect(self.sx(50), self.sy(185), self.sx(700), self.sy(730), self.sy(24), "#202933", "#3a4450", 2)
        presets = [("🎬", "Movie Mode", "movie"), ("🎙", "Show Mode", "show"), ("🔊", "40% Volume", "forty"), ("🔊", "Max", "max")]
        x = 170
        for icon,label,action in presets:
            self.button(self.sx(x), self.sy(205), self.sx(x+116), self.sy(286), f"{icon}\n{label}", lambda a=action: self.media_action(a), fill="#26313d", outline="#4a5663", text=TEXT)
            x += 132
        self.text(self.sx(325), self.sy(325), "NOW PLAYING", 10, CYAN_2, "bold")
        self.round_rect(self.sx(82), self.sy(415), self.sx(260), self.sy(585), self.sy(28), "#4fc9ff", "#66e6ff", 1)
        self.text(self.sx(171), self.sy(500), "NP", 72, "#15344a", "bold")
        self.text(self.sx(286), self.sy(410), "Nothing\nPlaying", 50, TEXT, "bold", "w")
        self.text(self.sx(288), self.sy(555), "Livingroom Sonos", 20, "#dce6ef", "bold", "w")
        self.canvas.create_line(self.sx(286), self.sy(635), self.sx(665), self.sy(635), fill="#46505c", width=5, capstyle="round")

        self.round_rect(self.sx(720), self.sy(185), self.sx(1220), self.sy(730), self.sy(24), "#202833", "#3a4450", 2)
        self.text(self.sx(740), self.sy(228), "Livingroom Sonos", 28, TEXT, "bold", "w")
        self.pill(self.sx(975), self.sy(212), self.sx(1018), self.sy(238), "Idle", fill="#303842", outline="#46505c", color=MUTED, size=9)
        self.text(self.sx(1135), self.sy(214), "Source", 9, MUTED, "bold", "w")
        self.button(self.sx(1115), self.sy(228), self.sx(1200), self.sy(270), "TV ▾", lambda: self.show_toast("Source selection uses the web settings page for now"), fill="#122533", outline="#274b61", text=TEXT)
        self.button(self.sx(785), self.sy(318), self.sx(860), self.sy(382), "Sub", lambda: self.media_action("subwoofer"), fill="#173246", outline="#2a5f80")
        self.circle_button(902, 350, 32, "◀", lambda: self.media_action("previous"), fill="#252b35", outline="#3b4552", size=18)
        self.circle_button(980, 350, 43, "▶", lambda: self.media_action("play_pause"), fill=CYAN, outline="#78e9ff", color=BLACK, size=22)
        self.circle_button(1058, 350, 32, "▶▶", lambda: self.media_action("next"), fill="#252b35", outline="#3b4552", size=13)
        self.button(self.sx(1100), self.sy(318), self.sx(1175), self.sy(382), "Surround", lambda: self.media_action("surround"), fill="#173246", outline="#2a5f80")
        self.round_rect(self.sx(740), self.sy(410), self.sx(1200), self.sy(555), self.sy(16), "#272d3a", "#3b4552", 2)
        volume = 64
        self.text(self.sx(758), self.sy(435), "Volume", 12, TEXT, "bold", "w")
        self.text(self.sx(1185), self.sy(435), f"{volume}%", 12, TEXT, "bold", "e")
        self.canvas.create_line(self.sx(760), self.sy(482), self.sx(1185), self.sy(482), fill="#57616d", width=5, capstyle="round")
        self.canvas.create_line(self.sx(760), self.sy(482), self.sx(1035), self.sy(482), fill=CYAN_2, width=5, capstyle="round")
        self.canvas.create_oval(self.sx(1022), self.sy(469), self.sx(1048), self.sy(495), fill="#dce8f2", outline="#425062", width=2)
        labels = [("Gain", controls.get("gain", {}).get("value", 15)), ("Bass", controls.get("bass", {}).get("value", 10)), ("Treble", controls.get("treble", {}).get("value", 8))]
        for i,(label,val) in enumerate(labels):
            x0 = 740 + i*160
            self.round_rect(self.sx(x0), self.sy(590), self.sx(x0+145), self.sy(725), self.sy(14), "#282d42", "#3c4554", 1)
            self.text(self.sx(x0+72), self.sy(620), label, 11, "#cbd4df", "bold")
            self.text(self.sx(x0+72), self.sy(646), str(int(as_float(val, 0))), 14, TEXT, "bold")
            self.canvas.create_line(self.sx(x0+72), self.sy(675), self.sx(x0+72), self.sy(710), fill=CYAN_2, width=5, capstyle="round")
            self.canvas.create_oval(self.sx(x0+60), self.sy(660), self.sx(x0+84), self.sy(684), fill="#f0eefc", outline="#423060", width=2)

    def media_action(self, action: str) -> None:
        ha = self._ha(); player = (self.config.get("integrations", {}) or {}).get("homeAssistant", {}).get("selectedMediaPlayerId", "")
        if not ha or not player:
            return self.show_toast("Audio player is not configured")
        self._run_busy("Audio", lambda: self._post_message("/api/ha/media/action", {"url": ha[0], "token": ha[1], "entityId": player, "action": action}))

    def draw_lights(self) -> None:
        _cfg, rooms, _room_key, room = self.room_bundle("lights")
        room_label = str(room.get("label") or "Living Room")
        lights = [l for l in (room.get("lights") or []) if isinstance(l, dict)]
        self.draw_page_shell("Light Control", room_label, "lights")
        self.button(self.sx(515), self.sy(212), self.sx(635), self.sy(254), "Room On", lambda: self.show_toast("Room On sent"), fill=CYAN, outline="#65dfff", text=BLACK)
        self.button(self.sx(645), self.sy(212), self.sx(765), self.sy(254), "Room Off", lambda: self.show_toast("Room Off sent"), fill="#c79bff", outline="#dcbaff", text=BLACK)
        if not lights:
            self.text(self.sx(640), self.sy(415), "No lights configured for this room.", 22, MUTED, "bold")
            return
        card_w, card_h = 182, 430
        start_x, start_y = 55, 310
        gap = 17
        for i, light in enumerate(lights[:6]):
            x = start_x + i * (card_w + gap)
            y = start_y
            on = as_bool(light.get("on")) or as_float(light.get("brightness"), 0) > 0
            bright = int(clamp(as_float(light.get("brightness"), 0), 0, 100))
            color = str(light.get("color") or ("#ffe889" if on else "#77808a"))
            x1,y1,x2,y2 = self.sx(x), self.sy(y), self.sx(x+card_w), self.sy(y+card_h)
            self.round_rect(x1,y1,x2,y2,self.sy(18),"#202a34" if on else "#202832","#3b4652",2)
            if on:
                self.canvas.create_oval(self.sx(x+20), self.sy(y+70), self.sx(x+card_w-20), self.sy(y+330), fill="#253b3f", outline="")
            for n,line in enumerate(self.short_lines(self.display_name(light.get("name"), "Light"), 16, 2)):
                self.text(self.sx(x+14), self.sy(y+30+n*22), line, 18, TEXT, "bold", "w")
            self.draw_light_icon(x+card_w//2, y+95, on, color)
            self.buttons.append(ButtonSpec(x1,y1,x2,y2,"toggle",lambda l=light: self.light_action(l, "toggle"),"","",TEXT))
            self.canvas.create_line(self.sx(x+card_w//2), self.sy(y+165), self.sx(x+card_w//2), self.sy(y+340), fill="#edf7ff" if on else "#4d5b67", width=8, capstyle="round")
            knob_y = y + 340 - int((bright/100)*175)
            self.canvas.create_oval(self.sx(x+card_w//2-15), self.sy(knob_y-15), self.sx(x+card_w//2+15), self.sy(knob_y+15), fill="#fff3b0" if on else "#c8bd8e", outline="")
            self.text(self.sx(x+card_w//2), self.sy(y+382), f"{bright}%", 18, "#fff6bf", "bold")
            self.text(self.sx(x+card_w//2), self.sy(y+405), "BRIGHTNESS", 8, MUTED, "bold")
            if light.get("colorSupported"):
                self.canvas.create_oval(self.sx(x+card_w-28), self.sy(y+88), self.sx(x+card_w-16), self.sy(y+100), fill="#fb42ff", outline="#23e5ff")

    def light_action(self, light: dict[str, Any], action: str, brightness: int | None = None) -> None:
        ha = self._ha(); entity = light.get("haEntityId")
        if not ha or not entity:
            return self.show_toast("Light is not configured")
        payload = {"url": ha[0], "token": ha[1], "entityId": entity, "action": action}
        if brightness is not None:
            payload["brightness"] = brightness
        self._run_busy("Light", lambda: self._post_message("/api/ha/light/action", payload))

    def draw_room_controls(self) -> None:
        _cfg, rooms, _room_key, room = self.room_bundle("room")
        room_label = str(room.get("label") or "Living Room")
        controls = [c for c in (room.get("controls") or room.get("entries") or []) if isinstance(c, dict)]
        self.draw_page_shell("Room Control", room_label, "room")
        if not controls:
            self.text(self.sx(640), self.sy(415), "No room controls configured for this room.", 22, MUTED, "bold")
            return
        card_w, card_h = 205, 178
        start_x, start_y = 55, 278
        gap_x, gap_y = 18, 18
        for i, control in enumerate(controls[:10]):
            col = i % 5; row = i // 5
            x = start_x + col * (card_w + gap_x); y = start_y + row * (card_h + gap_y)
            on = as_bool(control.get("on"))
            assigned = bool(control.get("haEntityId"))
            x1,y1,x2,y2 = self.sx(x), self.sy(y), self.sx(x+card_w), self.sy(y+card_h)
            self.round_rect(x1,y1,x2,y2,self.sy(14),"#24352f" if on else "#202832","#48ad7b" if on else "#33404d",2)
            # icon plate
            self.round_rect(self.sx(x+22), self.sy(y+18), self.sx(x+82), self.sy(y+78), self.sy(12), "#2d4650", "#436071", 1)
            self.text(self.sx(x+52), self.sy(y+48), "⏻" if assigned else "+", 28, GREEN if on else CYAN, "bold")
            self.pill(self.sx(x+card_w-58), self.sy(y+20), self.sx(x+card_w-16), self.sy(y+42), "ON" if on else ("OFF" if assigned else "ASSIGN"), fill="#2b6f55" if on else "#3d4753", outline="#436071", color="#e5fff1" if on else "#cbd5df", size=8)
            for n,line in enumerate(self.short_lines(self.display_name(control.get("name"), "Entry"), 19, 2)):
                self.text(self.sx(x+18), self.sy(y+118+n*21), line, 17, TEXT, "bold", "w")
            self.text(self.sx(x+18), self.sy(y+162), str(control.get("domain") or "unassigned").upper(), 8, MUTED, "bold", "w")
            self.canvas.create_line(self.sx(x+18), self.sy(y+168), self.sx(x+card_w-18), self.sy(y+168), fill=CYAN if on else "#384452", width=2)
            self.buttons.append(ButtonSpec(x1,y1,x2,y2,"toggle",lambda c=control: self.room_action(c, "toggle"),"","",TEXT))

    def room_action(self, control: dict[str, Any], action: str) -> None:
        ha = self._ha(); entity = control.get("haEntityId")
        if not ha or not entity:
            return self.show_toast("Room control is not configured")
        payload = {"url": ha[0], "token": ha[1], "entityId": entity, "action": action, "code": control.get("code", "")}
        self._run_busy("Room", lambda: self._post_message("/api/ha/room/action", payload))

    def draw_generic_page_title(self, title: str) -> None:
        self.draw_page_shell(f"{title} Control", title, None)

    def draw_entity_card(self, x: int, y: int, w: int, h: int, name: str, entity: str, actions: list[tuple[str, Callable[[], None]]]) -> None:
        x1=self.sx(x); y1=self.sy(y); x2=self.sx(x+w); y2=self.sy(y+h)
        self.round_rect(x1,y1,x2,y2,18,"#202832","#33404d",2)
        self.text(self.sx(x+16), self.sy(y+24), self.display_name(name)[:30], 17, TEXT, "bold", "w")
        self.text(self.sx(x+16), self.sy(y+50), str(entity)[:36] if entity else "Hold to assign", 10, MUTED, "bold", "w")
        bx = x + 16
        for label, action in actions[:3]:
            self.button(self.sx(bx), self.sy(y+h-40), self.sx(bx+78), self.sy(y+h-8), label, action, fill="#173246", text=TEXT)
            bx += 86

    def _ha(self) -> tuple[str, str] | None:
        integrations = self.config.get("integrations", {}) if isinstance(self.config, dict) else {}
        ha = integrations.get("homeAssistant", {}) if isinstance(integrations, dict) else {}
        url = ha.get("url", ""); token = ha.get("token", "")
        if not url or not token:
            return None
        return str(url), str(token)

    def _access_code(self) -> str:
        cfg = self.config if isinstance(self.config, dict) else {}
        security = cfg.get("security") if isinstance(cfg.get("security"), dict) else {}
        code = str(
            cfg.get("userAccessCode")
            or security.get("userAccessCode")
            or cfg.get("settingsAccessCode")
            or DEFAULT_ACCESS_CODE
        )
        code = "".join(ch for ch in code if ch.isdigit())[:4]
        return code if len(code) == 4 else DEFAULT_ACCESS_CODE

    def _post_message(self, path: str, payload: dict[str, Any]) -> str:
        result = self.api.post(path, payload, timeout=8)
        if not result.get("ok", True):
            raise RuntimeError(result.get("error", "Command failed"))
        return "Sent"

    def open_info(self) -> None:
        self.modal = "info"; self.modal_data = {}; self.draw()

    def open_settings(self) -> None:
        if self.locked:
            return self.open_code("unlock")
        self.open_code("settings")

    def open_code(self, target: str) -> None:
        self.modal = "code"; self.modal_data = {"target": target}; self.code_buffer = ""; self.draw()

    def toggle_lock(self) -> None:
        if self.locked:
            self.open_code("unlock")
        else:
            self.locked = True; self.show_toast("Panel locked")

    def open_schedule(self) -> None:
        self.modal = "schedule"; self.modal_data = {}; self.draw()

    def open_alarm(self) -> None:
        self.modal = "alarm"; self.modal_data = {}; self.draw()

    def open_hardware(self) -> None:
        self.modal = "hardware"; self.modal_data = {}; self.draw()

    def draw_modal(self) -> None:
        self.canvas.create_rectangle(0, 0, self.width, self.height, fill="#000000", stipple="gray50")
        x1,y1,x2,y2 = self.sx(250), self.sy(120), self.sx(1030), self.sy(670)
        self.round_rect(x1,y1,x2,y2,24,"#0b1620","#2b4d64",3)
        if self.modal == "info":
            self.draw_info_modal(x1,y1,x2,y2)
        elif self.modal == "code":
            self.draw_code_modal(x1,y1,x2,y2)
        elif self.modal == "settings":
            self.draw_settings_modal(x1,y1,x2,y2)
        elif self.modal == "alarm":
            self.draw_alarm_modal(x1,y1,x2,y2)
        elif self.modal == "hardware":
            self.draw_hardware_modal(x1,y1,x2,y2)
        elif self.modal == "schedule":
            self.draw_schedule_modal(x1,y1,x2,y2)

    def draw_info_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        info = self.system_info or {}
        self.text((x1+x2)//2, y1+self.sy(42), "Panel Info", 30, TEXT, "bold")
        lines = [
            f"Version: {info.get('version') or _version()}",
            f"Address: {info.get('address') or info.get('ipAddress') or socket.gethostname()}",
            f"Host: {info.get('host') or socket.gethostname()}",
            f"Uptime: {info.get('uptime') or ''}",
            f"Display: Native Tk web-parity appliance, no Chromium",
        ]
        y = y1+self.sy(105)
        for line in lines:
            self.text(x1+self.sx(45), y, line, 19, MUTED, "bold", "w"); y += self.sy(38)
        self.button(x1+self.sx(40), y2-self.sy(118), x1+self.sx(215), y2-self.sy(58), "Fetch Update", lambda: self.fetch_update(), fill="#17405a", text=CYAN)
        self.button(x1+self.sx(235), y2-self.sy(118), x1+self.sx(410), y2-self.sy(58), "Upload Config", lambda: self.upload_config(), fill="#163d2a", text=GREEN)
        self.button(x1+self.sx(430), y2-self.sy(118), x1+self.sx(590), y2-self.sy(58), "Restart", lambda: self.restart_panel(), fill="#402817", text=YELLOW)
        self.button(x2-self.sx(190), y2-self.sy(90), x2-self.sx(45), y2-self.sy(35), "Close", lambda: self.close_modal(), fill="#173246")


    def draw_schedule_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        schedules = [s for s in self.get_schedules() if s.get("enabled", True) is not False]
        self.text((x1+x2)//2, y1+self.sy(42), "Schedule Shortcuts", 30, TEXT, "bold")
        self.text((x1+x2)//2, y1+self.sy(78), "Tap a schedule to apply its current mode setpoint now.", 14, MUTED, "bold")
        if not schedules:
            self.text((x1+x2)//2, y1+self.sy(180), "No saved schedules are currently available.", 20, MUTED, "bold")
        else:
            cols = 2
            card_w, card_h = 295, 76
            start_x = x1 + self.sx(82)
            start_y = y1 + self.sy(120)
            for idx, sched in enumerate(schedules[:8]):
                col = idx % cols
                row = idx // cols
                bx = start_x + self.sx(col * (card_w + 34))
                by = start_y + self.sy(row * (card_h + 20))
                name = str(sched.get("name") or sched.get("label") or "Schedule")[:26]
                detail = self.schedule_detail(sched)
                xA,yA,xB,yB = bx,by,bx+self.sx(card_w),by+self.sy(card_h)
                self.buttons.append(ButtonSpec(xA,yA,xB,yB,name,lambda s=sched:self.apply_schedule(s),"#132c3e","#2a6485",TEXT))
                self.round_rect(xA,yA,xB,yB,self.sy(16),"#132c3e","#2a6485",2)
                self.text(xA+self.sx(20), yA+self.sy(25), name, 17, TEXT, "bold", "w")
                detail_parts = []
                if sched.get("time"):
                    detail_parts.append(str(sched.get("time")))
                detail_parts.append(detail)
                self.text(xA+self.sx(20), yA+self.sy(52), "  •  ".join(detail_parts), 13, CYAN_2, "bold", "w")
        self.button(x2-self.sx(190), y2-self.sy(90), x2-self.sx(45), y2-self.sy(35), "Close", lambda: self.close_modal(), fill="#173246")

    def draw_code_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        target = self.modal_data.get("target", "settings")
        self.text((x1+x2)//2, y1+self.sy(42), "Enter Code", 30, TEXT, "bold")
        self.text((x1+x2)//2, y1+self.sy(88), "●" * len(self.code_buffer), 36, CYAN, "bold")
        keys = ["1","2","3","4","5","6","7","8","9","C","0","OK"]
        for idx,k in enumerate(keys):
            col=idx%3; row=idx//3
            bx=x1+self.sx(210+col*120); by=y1+self.sy(135+row*78)
            self.button(bx,by,bx+self.sx(90),by+self.sy(58),k,lambda kk=k,t=target:self.handle_code_key(kk,t),fill="#173246")
        self.button(x2-self.sx(190), y2-self.sy(90), x2-self.sx(45), y2-self.sy(35), "Cancel", lambda: self.close_modal(), fill="#34202a")

    def handle_code_key(self, key: str, target: str) -> None:
        if key == "C":
            self.code_buffer = ""
        elif key == "OK":
            code = self._access_code()
            if self.code_buffer == code or self.code_buffer == DEFAULT_ACCESS_CODE:
                if target == "unlock":
                    self.locked = False; self.close_modal(); self.show_toast("Unlocked")
                else:
                    self.modal = "settings"; self.modal_data = {}; self.code_buffer = ""
            else:
                self.show_toast("Wrong code"); self.code_buffer = ""
        else:
            self.code_buffer = (self.code_buffer + key)[-8:]
        self.draw()

    def draw_settings_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        t = self.thermostat
        self.text((x1+x2)//2, y1+self.sy(42), "Comfort Settings", 30, TEXT, "bold")
        rows = [
            ("Away Heat", "awayHeat", -1, 1),
            ("Away Cool", "awayCool", -1, 1),
            ("Safety Low", "safetyLow", -1, 1),
            ("Safety High", "safetyHigh", -1, 1),
            ("Fan Hold Min", "coolFanRemainOnMinutes", -1, 1),
        ]
        y=y1+self.sy(105)
        for label,key,down,up in rows:
            val = as_float(t.get(key), 0)
            self.text(x1+self.sx(65),y+self.sy(25),label,18,MUTED,"bold","w")
            self.button(x1+self.sx(340),y,x1+self.sx(405),y+self.sy(50),"−",lambda k=key,d=down:self.adjust_setting(k,d),fill="#173246")
            self.text(x1+self.sx(470),y+self.sy(25),f"{val:.0f}",24,TEXT,"bold")
            self.button(x1+self.sx(535),y,x1+self.sx(600),y+self.sy(50),"+",lambda k=key,u=up:self.adjust_setting(k,u),fill="#173246")
            y += self.sy(62)
        self.button(x2-self.sx(190), y2-self.sy(90), x2-self.sx(45), y2-self.sy(35), "Close", lambda: self.close_modal(), fill="#173246")

    def adjust_setting(self, key: str, delta: float) -> None:
        value = as_float(self.thermostat.get(key), 0) + delta
        self.thermostat[key] = value
        self._run_busy("Setting", lambda k=key,v=value: self.control({k: v}))

    def draw_alarm_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        self.text((x1+x2)//2, y1+self.sy(42), "Alarm", 30, TEXT, "bold")
        state = title_case((self.alarm_state or {}).get("state", "Unknown"))
        self.text((x1+x2)//2, y1+self.sy(100), state, 28, CYAN, "bold")
        self.button(x1+self.sx(85), y1+self.sy(175), x1+self.sx(295), y1+self.sy(245), "Arm Home", lambda: self.alarm_action("arm_home"), fill="#173246")
        self.button(x1+self.sx(330), y1+self.sy(175), x1+self.sx(540), y1+self.sy(245), "Arm Away", lambda: self.alarm_action("arm_away"), fill="#173246")
        self.button(x1+self.sx(575), y1+self.sy(175), x1+self.sx(695), y1+self.sy(245), "Disarm", lambda: self.alarm_action("disarm"), fill="#40202a")
        self.button(x2-self.sx(190), y2-self.sy(90), x2-self.sx(45), y2-self.sy(35), "Close", lambda: self.close_modal(), fill="#173246")

    def alarm_action(self, action: str) -> None:
        ha = self._ha()
        alarm = self.alarm_state or {}
        entity = alarm.get("entityId") or alarm.get("entity_id")
        if not ha or not entity:
            return self.show_toast("Alarm not configured")
        code = (self.config.get("alarm") or {}).get("disarmCode", "")
        self._run_busy("Alarm", lambda: self._post_message("/api/ha/alarm/action", {"url":ha[0],"token":ha[1],"entityId":entity,"action":action,"code":code}))

    def draw_hardware_modal(self,x1:int,y1:int,x2:int,y2:int)->None:
        self.text((x1+x2)//2, y1+self.sy(42), "Hardware", 30, TEXT, "bold")
        hw = self.hardware or {}
        lines = [json.dumps(hw.get("gpio", {}))[:80], json.dumps(hw.get("i2c", {}))[:80], json.dumps(hw.get("relays", {}))[:80]]
        y=y1+self.sy(105)
        for line in lines:
            self.text(x1+self.sx(45),y,line,14,MUTED,"normal","w"); y+=self.sy(38)
        self.button(x1+self.sx(70),y2-self.sy(115),x1+self.sx(230),y2-self.sy(55),"Fan",lambda:self.relay("fan"),fill="#173246")
        self.button(x1+self.sx(250),y2-self.sy(115),x1+self.sx(410),y2-self.sy(55),"Heat",lambda:self.relay("heat"),fill="#40202a")
        self.button(x1+self.sx(430),y2-self.sy(115),x1+self.sx(590),y2-self.sy(55),"Cool",lambda:self.relay("cool"),fill="#173246")
        self.button(x2-self.sx(190), y2-self.sy(90), x2-self.sx(45), y2-self.sy(35), "Close", lambda: self.close_modal(), fill="#173246")

    def relay(self, name: str) -> None:
        self._run_busy("Relay", lambda: self._post_message("/api/hardware/relay", {"relay": name, "on": True}))

    def fetch_update(self) -> None:
        def run() -> str:
            result = self.api.post("/api/system/fetch-update", {}, timeout=120)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Update failed"))
            return result.get("message", "Update complete")
        self._run_busy("Update", run)


    def upload_config(self) -> None:
        def choose_and_upload() -> str:
            # Tk file dialog must run on the UI thread.  This method is called from a button handler,
            # then the actual upload runs in the normal background worker.
            return ""
        try:
            filename = filedialog.askopenfilename(
                parent=self.root,
                title="Upload Smart Thermostat Config",
                filetypes=(("JSON config", "*.json"), ("All files", "*.*")),
            )
        except Exception as exc:
            self.show_toast(f"File picker failed: {exc}")
            return
        if not filename:
            return
        path = Path(filename)
        def run() -> str:
            try:
                backup = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise RuntimeError(f"Could not read config JSON: {exc}") from exc
            result = self.api.post("/api/system/config-import", {"backup": backup}, timeout=30)
            if not result.get("ok"):
                raise RuntimeError(result.get("error", "Config upload failed"))
            self.fetch_slow()
            return result.get("message", "Config uploaded. Settings restored.")
        self._run_busy("Upload Config", run)

    def restart_panel(self) -> None:
        self._run_busy("Restart", lambda: self._post_message("/api/system/reboot", {}))

    def close_modal(self) -> None:
        self.modal = None; self.modal_data = {}; self.code_buffer=""; self.draw()

    def draw_toast(self, message: str) -> None:
        x1,y1,x2,y2 = self.sx(320), self.sy(30), self.sx(960), self.sy(84)
        self.round_rect(x1,y1,x2,y2,18,"#123149",CYAN,2)
        self.text((x1+x2)//2,(y1+y2)//2,message[:70],17,TEXT,"bold")

    def draw_busy(self) -> None:
        x1,y1,x2,y2 = self.sx(420), self.sy(348), self.sx(860), self.sy(452)
        self.round_rect(x1,y1,x2,y2,20,"#071018",CYAN,3)
        self.text((x1+x2)//2, y1+self.sy(38), self.current_task or "Working", 22, TEXT, "bold")
        self.text((x1+x2)//2, y1+self.sy(74), "Please wait…", 15, MUTED)

    def on_touch(self, event: tk.Event) -> None:
        x, y = int(event.x), int(event.y)
        for b in reversed(self.buttons):
            if b.x1 <= x <= b.x2 and b.y1 <= y <= b.y2:
                b.action()
                return
        # allow tap outside modal to close only if not busy
        if self.modal and not self.busy:
            pass

    def on_drag(self, event: tk.Event) -> None:
        pass

    def on_release(self, event: tk.Event) -> None:
        self.drag_target = ""


def wait_for_api(api: ApiClient, timeout: float = 75) -> None:
    started = time.time()
    while time.time() - started < timeout:
        try:
            if api.get("/api/health", timeout=1).get("ok"):
                return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit(f"Thermostat API did not become ready at {api.base}")


def main() -> None:
    wait_for_api(ApiClient())
    app = NativeThermostatApp()
    app.run()


if __name__ == "__main__":
    main()
