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
FRAME_MS = max(250, int(os.environ.get("SMART_NATIVE_FRAME_MS", "500")))
DISPLAY_RUNTIME_LABEL = os.environ.get("SMART_NATIVE_DISPLAY_LABEL", "Native touchscreen + local web API")
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
DEFAULT_NATIVE_SCHEDULES = [
    {"id": "native-home", "name": "Home", "time": "06:00", "enabled": True, "coolSetpoint": 73, "heatSetpoint": 70, "personEntityIds": []},
    {"id": "native-evening", "name": "Evening", "time": "18:00", "enabled": True, "coolSetpoint": 72, "heatSetpoint": 70, "personEntityIds": []},
    {"id": "native-sleep", "name": "Sleep", "time": "22:30", "enabled": True, "coolSetpoint": 70, "heatSetpoint": 67, "personEntityIds": []},
    {"id": "native-away", "name": "Away", "time": "08:00", "enabled": True, "coolSetpoint": 85, "heatSetpoint": 55, "personEntityIds": []},
]


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


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = str(value or "#000000").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return (0, 0, 0)
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return (0, 0, 0)


def blend_hex(a: str, b: str, amount: float) -> str:
    amount = clamp(amount, 0.0, 1.0)
    ar, ag, ab = hex_to_rgb(a)
    br, bg, bb = hex_to_rgb(b)
    return f"#{round(ar + (br - ar) * amount):02x}{round(ag + (bg - ag) * amount):02x}{round(ab + (bb - ab) * amount):02x}"


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
    long_action: Callable[[], None] | None = None


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
        self.settings_tab = "comfort"
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
        self.weather_state: dict[str, Any] | None = None
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
        self.drag_origin_y = 0
        self.drag_origin_target = 0.0
        self.pressed_button: ButtonSpec | None = None
        self.press_started_at = 0.0
        self.long_press_fired = False
        self.long_press_after_id: str | None = None
        self.dial_area: tuple[int, int, int, int] | None = None
        self.last_target_send = 0.0
        self.last_target_value: float | None = None
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
            self.root.after(FRAME_MS, self._tick)

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
                if payload.get("weather"):
                    self.weather_state = payload.get("weather")
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
            elif kind == "entity_picker_results":
                if self.modal == "entity_picker":
                    self.modal_data["loading"] = False
                    self.modal_data["entities"] = payload.get("entities") or []
                    self.modal_data["error"] = payload.get("error", "")
                    self.modal_data["page"] = 0
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
        weather_ent = ha.get("weatherEntity") or cfg.get("weatherEntity")
        if url and token and weather_ent:
            ent = weather_ent.get("entityId", weather_ent) if isinstance(weather_ent, dict) else weather_ent
            try:
                data = self.api.post("/api/ha/weather/state", {"url": url, "token": token, "entityId": ent}, timeout=4)
                if data.get("weather"):
                    out["weather"] = data.get("weather")
            except Exception:
                pass
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

    def button(self, x1: int, y1: int, x2: int, y2: int, label: str, action: Callable[[], None], fill: str = PANEL_2, outline: str = "#2e465b", text: str = TEXT, tag: str = "", size: int = 18, radius: int = 16, border: int = 2, long_action: Callable[[], None] | None = None) -> None:
        self.buttons.append(ButtonSpec(x1, y1, x2, y2, label, action, fill, outline, text, tag, long_action))
        self.round_rect(x1, y1, x2, y2, radius, fill, outline, border)
        self.canvas.create_text((x1+x2)//2, (y1+y2)//2, text=label, fill=text, font=self.font(size, "bold"), justify="center")

    def round_rect(self, x1: int, y1: int, x2: int, y2: int, r: int, fill: str, outline: str = "", width: int = 1) -> None:
        # Canvas polygon with smoothed corners keeps drawing light on the Pi.
        pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2, x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1]
        self.canvas.create_polygon(pts, smooth=True, splinesteps=8, fill=fill, outline=outline, width=width)

    def text(self, x: int, y: int, value: str, size: int = 18, fill: str = TEXT, weight: str = "normal", anchor: str = "center") -> None:
        self.canvas.create_text(x, y, text=value, fill=fill, font=self.font(size, weight), anchor=anchor)

    def round_rect_shadow(self, x1: int, y1: int, x2: int, y2: int, r: int, fill: str, outline: str = "", width: int = 1, shadow: str = "#05080c", offset: int = 4) -> None:
        if offset:
            self.round_rect(x1 + offset, y1 + offset, x2 + offset, y2 + offset, r, shadow, "", 0)
        self.round_rect(x1, y1, x2, y2, r, fill, outline, width)

    def glass_panel(self, x1: int, y1: int, x2: int, y2: int, r: int = 22, fill: str = "#18212b", outline: str = "#32404d") -> None:
        self.round_rect_shadow(x1, y1, x2, y2, r, fill, outline, 1, shadow="#06090e", offset=max(2, self.sy(4)))
        # A small highlight at the top sells the glass look without expensive blur.
        self.canvas.create_line(x1 + r, y1 + 2, x2 - r, y1 + 2, fill="#465462", width=1)

    def glow_dot(self, x: int, y: int, color: str, r: int = 4) -> None:
        self.canvas.create_oval(x-r*3, y-r*3, x+r*3, y+r*3, fill="#14231e", outline="")
        self.canvas.create_oval(x-r, y-r, x+r, y+r, fill=color, outline="")

    def glossy_button(self, x1: int, y1: int, x2: int, y2: int, label: str, action: Callable[[], None], active: bool = False, fill: str | None = None, text: str | None = None, outline: str | None = None, size: int = 11, tag: str = "", long_action: Callable[[], None] | None = None) -> None:
        if fill is None:
            fill = CYAN if active else "#252d37"
        if text is None:
            text = BLACK if active else "#d7dee8"
        if outline is None:
            outline = "#6de5ff" if active else "#3b4654"
        self.buttons.append(ButtonSpec(x1, y1, x2, y2, label, action, fill, outline, text, tag, long_action))
        self.round_rect_shadow(x1, y1, x2, y2, max(7, (y2-y1)//2), fill, outline, 1, shadow="#070b10", offset=max(1, self.sy(2)))
        if active:
            self.canvas.create_line(x1+10, y1+2, x2-10, y1+2, fill="#b7f4ff", width=1)
        self.text((x1+x2)//2, (y1+y2)//2, label, size, text, "bold")

    def draw(self) -> None:
        self.canvas.delete("all")
        self.buttons = []
        self.dial_area = None
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

    def climate_temperature_tone(self) -> tuple[str, float, float]:
        """Match the web app's temperature atmosphere logic.

        The browser UI drives the animated climate backdrop from room
        temperature around 71°F.  Keep the native appliance on the same rule so
        warm rooms feel amber/red, cold rooms feel blue/cyan, and comfortable
        rooms stay dark/neutral.
        """
        temp = as_float((self.thermostat or {}).get("currentTemp", (self.thermostat or {}).get("current_temperature")), 71.0)
        cold = clamp((71.0 - temp) / 9.0, 0.0, 1.0)
        heat = clamp((temp - 71.0) / 9.0, 0.0, 1.0)
        if cold > heat and cold > 0.01:
            return "cold", cold, heat
        if heat > 0.01:
            return "hot", cold, heat
        return "neutral", cold, heat

    def climate_palette(self) -> dict[str, str | float]:
        tone, cold, heat = self.climate_temperature_tone()
        away = as_bool((self.thermostat or {}).get("away"))
        intensity = max(cold, heat, 0.18)
        if away:
            tone = "away"
            intensity = max(intensity, 0.42)
        if tone == "hot":
            return {
                "tone": tone,
                "intensity": intensity,
                "base": blend_hex("#0c1018", "#3c1018", min(0.72, 0.32 + heat * 0.46)),
                "primary": blend_hex("#1a1018", "#a2252e", 0.22 + heat * 0.48),
                "secondary": blend_hex("#14121c", "#ff744a", 0.14 + heat * 0.30),
                "accent": blend_hex("#243244", "#ffb75b", 0.22 + heat * 0.46),
                "line": blend_hex("#283241", "#ff8a62", 0.28 + heat * 0.42),
            }
        if tone == "cold":
            return {
                "tone": tone,
                "intensity": intensity,
                "base": blend_hex("#08111a", "#08284a", min(0.72, 0.32 + cold * 0.46)),
                "primary": blend_hex("#071824", "#0b69b0", 0.22 + cold * 0.48),
                "secondary": blend_hex("#0b1320", "#19d9ff", 0.12 + cold * 0.26),
                "accent": blend_hex("#1d3242", "#5bf1ff", 0.22 + cold * 0.40),
                "line": blend_hex("#273341", "#52ddff", 0.24 + cold * 0.38),
            }
        if tone == "away":
            return {
                "tone": tone,
                "intensity": intensity,
                "base": "#120f22",
                "primary": "#2b174f",
                "secondary": "#24143a",
                "accent": "#8c78ff",
                "line": "#6e8cff",
            }
        return {
            "tone": tone,
            "intensity": intensity,
            "base": "#0b1119",
            "primary": "#0f2530",
            "secondary": "#10182a",
            "accent": "#2de0c4",
            "line": "#32485a",
        }

    def draw_climate_symbol(self, tone: str, color: str, phase: float) -> None:
        """Draw the faint web-style climate icon without using image assets."""
        cx = self.sx(1088 + math.sin(phase * 0.45) * 14)
        cy = self.sy(314 + math.cos(phase * 0.38) * 10)
        if tone == "hot":
            outer = self.sx(124)
            inner = self.sx(78)
            self.canvas.create_oval(cx-outer, cy-outer, cx+outer, cy+outer, outline=blend_hex(color, "#111821", 0.25), width=max(1, self.sx(2)))
            self.canvas.create_oval(cx-inner, cy-inner, cx+inner, cy+inner, outline=blend_hex(color, "#111821", 0.18), width=max(1, self.sx(3)))
            for i in range(12):
                a = phase * 0.15 + i * math.tau / 12
                r1 = outer + self.sx(20)
                r2 = outer + self.sx(86)
                x1 = cx + math.cos(a) * r1
                y1 = cy + math.sin(a) * r1
                x2 = cx + math.cos(a) * r2
                y2 = cy + math.sin(a) * r2
                self.canvas.create_line(x1, y1, x2, y2, fill=blend_hex(color, "#111821", 0.38), width=max(1, self.sx(8)), capstyle="round")
            return
        if tone == "cold":
            arm = self.sx(150)
            for i in range(6):
                a = phase * 0.12 + i * math.tau / 6
                x1 = cx + math.cos(a) * self.sx(22)
                y1 = cy + math.sin(a) * self.sy(22)
                x2 = cx + math.cos(a) * arm
                y2 = cy + math.sin(a) * arm
                self.canvas.create_line(x1, y1, x2, y2, fill=blend_hex(color, "#111821", 0.28), width=max(1, self.sx(5)), capstyle="round")
                for off in (0.68, 0.82):
                    bx = cx + math.cos(a) * arm * off
                    by = cy + math.sin(a) * arm * off
                    for branch in (-0.7, 0.7):
                        ba = a + branch
                        self.canvas.create_line(bx, by, bx + math.cos(ba) * self.sx(30), by + math.sin(ba) * self.sy(30), fill=blend_hex(color, "#111821", 0.34), width=max(1, self.sx(3)), capstyle="round")
            self.canvas.create_oval(cx-self.sx(20), cy-self.sy(20), cx+self.sx(20), cy+self.sy(20), outline=blend_hex(color, "#111821", 0.20), width=max(1, self.sx(3)))

    def draw_temperature_waves(self, color: str, phase: float) -> None:
        for row in range(3):
            y = self.sy(585 + row * 48)
            points: list[int] = []
            for i in range(0, 17):
                x = self.sx(i * 82 - 26)
                offset = math.sin(phase * 0.85 + row * 0.9 + i * 0.75) * self.sy(10 + row * 3)
                points.extend([x, int(y + offset)])
            self.canvas.create_line(*points, fill=blend_hex(color, "#0b1119", 0.45 + row * 0.12), width=max(1, self.sy(2)), smooth=True)

    def draw_background(self) -> None:
        """Modern web-inspired animated climate backdrop.

        The native app cannot use CSS radial gradients/blur, so this draws a
        lightweight vector version: temperature-dependent color blooms, a faint
        climate symbol, and slowly moving waves.  It updates every native tick
        and stays cheap enough for the Pi display.
        """
        palette = self.climate_palette()
        tone = str(palette["tone"])
        phase = time.time()
        self.canvas.create_rectangle(0, 0, self.width, self.height, fill=str(palette["base"]), outline="")

        # Subtle blueprint grid like the web shell, but keep it buried under the
        # atmosphere so it does not dirty the screen.
        grid = max(22, self.sx(56))
        grid_color = blend_hex(str(palette["base"]), "#ffffff", 0.035)
        for x in range(0, self.width + grid, grid):
            self.canvas.create_line(x, 0, x, self.height, fill=grid_color, width=1)
        for y in range(0, self.height + grid, grid):
            self.canvas.create_line(0, y, self.width, y, fill=grid_color, width=1)

        # Animated climate glows.  These are deliberately oversized and low
        # contrast to mimic the browser's radial-gradient backdrop.
        drift_x = math.sin(phase * 0.32) * self.sx(28)
        drift_y = math.cos(phase * 0.27) * self.sy(24)
        self.canvas.create_oval(self.sx(-170) + drift_x, self.sy(100) - drift_y, self.sx(520) + drift_x, self.sy(835) - drift_y, fill=str(palette["primary"]), outline="")
        self.canvas.create_oval(self.sx(360) - drift_x * 0.5, self.sy(60) + drift_y * 0.6, self.sx(1130) - drift_x * 0.5, self.sy(850) + drift_y * 0.6, fill=str(palette["secondary"]), outline="")
        self.canvas.create_oval(self.sx(790) + drift_x * 0.8, self.sy(142) + drift_y * 0.3, self.sx(1440) + drift_x * 0.8, self.sy(760) + drift_y * 0.3, fill=blend_hex(str(palette["primary"]), "#050812", 0.22), outline="")
        self.draw_climate_symbol(tone, str(palette["accent"]), phase)
        self.draw_temperature_waves(str(palette["line"]), phase)

        # Soft vignette and outer glass frame.  Do not paint an opaque full-page
        # panel here; that would hide the animated atmosphere underneath.
        self.canvas.create_oval(self.sx(160), self.sy(30), self.sx(1120), self.sy(850), outline=blend_hex(str(palette["accent"]), "#07101a", 0.72), width=max(1, self.sx(1)))
        frame = blend_hex(str(palette["accent"]), "#182230", 0.72)
        self.round_rect(self.sx(12), self.sy(14), self.sx(1268), self.sy(786), self.sx(28), "", frame, 1)

    def pill(self, x1:int, y1:int, x2:int, y2:int, text:str, fill:str="#232a34", outline:str="#37414d", color:str=TEXT, size:int=14, weight:str="bold", dot:str|None=None) -> None:
        self.round_rect(x1, y1, x2, y2, max(8, (y2-y1)//2), fill, outline, 1)
        tx = (x1+x2)//2
        if dot:
            self.canvas.create_oval(x1+self.sx(14), (y1+y2)//2-self.sy(4), x1+self.sx(22), (y1+y2)//2+self.sy(4), fill=dot, outline="")
            tx += self.sx(8)
        self.text(tx, (y1+y2)//2, text, size, color, weight)

    def label_chip(self, x:int, y:int, label:str, value:str, w:int=118) -> None:
        # Web-style environmental readout with no enclosing cell.  The browser UI
        # reads these as simple inline labels, not cards.  Draw label/value
        # separately so WIND never collides with the numeric mph value.
        label_text = label.upper()
        value_text = value.upper()
        value_offset = 78 if label_text == "OUTDOOR" else 62 if label_text == "WIND" else max(54, min(w - 38, len(label_text) * 10 + 16))
        baseline = self.sy(y + 12)
        self.text(self.sx(x), baseline, label_text, 9, "#b8c4d0", "bold", "w")
        self.text(self.sx(x + value_offset), baseline, value_text, 11, TEXT, "bold", "w")

    def small_button(self, x1:int, y1:int, x2:int, y2:int, label:str, action:Callable[[], None], fill:str=PANEL_2, outline:str="#2e465b", text:str=TEXT, size:int=12, tag:str="") -> None:
        self.button(x1, y1, x2, y2, label, action, fill=fill, outline=outline, text=text, tag=tag, size=size, radius=max(6, (y2-y1)//3), border=1)

    def icon_text(self, x:int, y:int, icon:str, size:int=34, fill:str=CYAN_2) -> None:
        self.text(self.sx(x), self.sy(y), icon, size, fill, "bold")

    def circle_button(self, cx:int, cy:int, r:int, label:str, action:Callable[[], None], fill:str="#232a34", outline:str="#343e4a", color:str=TEXT, size:int=20) -> None:
        x1,y1,x2,y2 = self.sx(cx-r), self.sy(cy-r), self.sx(cx+r), self.sy(cy+r)
        self.buttons.append(ButtonSpec(x1,y1,x2,y2,label,action,fill,outline,color))
        self.canvas.create_oval(x1,y1,x2,y2,fill=fill,outline=outline,width=2)
        self.text(self.sx(cx), self.sy(cy), label, size, color, "bold")

    def draw_header(self) -> None:
        # Top-left lock chip: small, translucent, and clickable like the web UI.
        x1, y1, x2, y2 = self.sx(24), self.sy(32), self.sx(132), self.sy(62)
        label = "🔒  LOCKED" if self.locked else "🔓  UNLOCKED"
        fill = "#16282b" if not self.locked else "#2b1b22"
        outline = "#2f6567" if not self.locked else "#7a3342"
        color = "#b9fff3" if not self.locked else "#ffd2dc"
        self.buttons.append(ButtonSpec(x1, y1, x2, y2, label, self.toggle_lock, fill, outline, color, "panel-lock"))
        self.round_rect_shadow(x1, y1, x2, y2, self.sy(15), fill, outline, 1, shadow="#06090d", offset=self.sy(2))
        self.text((x1+x2)//2, (y1+y2)//2, label, 8, color, "bold")
        # Right-side clock/info/settings chips. Keep them away from the physical
        # edge so the 10-inch bezel and touch calibration do not make them feel
        # crowded or hard to press.
        self.pill(self.sx(1088), self.sy(32), self.sx(1152), self.sy(62), time.strftime("%I:%M %p").lstrip("0"), fill="#242a33", outline="#3b4551", color=TEXT, size=9)
        self.circle_button(1186, 47, 18, "i", lambda: self.open_info(), fill="#123244", outline="#2d6c83", color=CYAN_2, size=16)
        self.circle_button(1232, 47, 18, "⚙", lambda: self.open_settings(), fill="#242b34", outline="#3b4551", color=MUTED, size=14)


    def draw_top_nav(self) -> None:
        # Native appliance navigation: bigger touch targets, more breathing room,
        # and no enclosing border/frame so the top bar stays clean and modern.
        pages = [("blinds", "Blinds"), ("audio", "Audio"), ("thermostat", "Thermostat"), ("lights", "Lights"), ("room", "Room")]
        widths = {"blinds": 98, "audio": 96, "thermostat": 146, "lights": 96, "room": 88}
        gap = 18
        total = sum(widths[p] for p, _ in pages) + gap * (len(pages) - 1)
        x = (1280 - total) // 2
        y, h = 28, 38
        for page, label in pages:
            w = widths[page]
            active = self.page == page
            if active:
                self.glossy_button(self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h), label, lambda p=page: self.set_page(p), active=True, size=13, tag=f"nav:{page}")
            else:
                # Draw inactive tabs as text-first soft chips instead of a heavy
                # bordered group. Each tab remains a large touch target.
                x1, y1, x2, y2 = self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h)
                self.buttons.append(ButtonSpec(x1, y1, x2, y2, label, lambda p=page: self.set_page(p), "", "", TEXT, f"nav:{page}"))
                self.round_rect(x1, y1, x2, y2, self.sy(16), "#141c25", "#1f2a35", 1)
                self.text((x1+x2)//2, (y1+y2)//2, label, 13, "#d9e6f2", "bold")
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
        weather = self.weather_state or {}
        outdoor = as_float(weather.get("temperature"), as_float(t.get("outdoorTemp", t.get("outdoor_temperature", 0)), 0))
        wind = as_float(weather.get("windSpeed"), as_float(t.get("outdoorWindSpeed", t.get("outdoor_wind_speed", 0)), 0))
        hum = as_float(t.get("humidity", 0), 0)
        if mode == "auto":
            active_side = self.effective_limit_mode()
            running = "Cooling" if action == "cooling" or relays.get("cool") else "Heating" if action == "heating" or relays.get("heat") else "Idle"
            action_label = f"Auto • {title_case(active_side)} • {running}"
        else:
            action_label = "Cooling" if action == "cooling" or relays.get("cool") else "Heating" if action == "heating" or relays.get("heat") else "Idle"
        action_color = CYAN if "cool" in action_label.lower() else RED if "heat" in action_label.lower() else GREEN

        # Web parity thermostat page, scaled down for the 10.1-inch Pi display.
        self.label_chip(60, 166, "Outdoor", f"{outdoor:.0f}°", 112)
        self.label_chip(180, 166, "Wind", f"{wind:.0f} mph", 104)
        # The web title is large, but on the native 10-inch panel the previous
        # size consumed too much of the layout.  Keep the same position but make
        # it about 40% smaller so the page breathes like the browser version.
        self.text(self.sx(58), self.sy(228), "Climate Control", 28, TEXT, "bold", "w")

        self.draw_runtime_alert()
        notice = self.auto_switch_notice()
        if notice:
            to_mode = str(notice.get("toMode") or notice.get("mode") or mode or "cool").lower()
            switch_temp = as_float(notice.get("switchTemp"), current)
            x1, y1, x2, y2 = self.sx(110), self.sy(310), self.sx(260), self.sy(382)
            self.buttons.append(ButtonSpec(x1, y1, x2, y2, "auto-switch", lambda: self.open_auto_switch(), "#06233d", "#1e78a8", TEXT))
            self.round_rect(x1, y1, x2, y2, self.sy(14), "#06233d", "#1e78a8", 1)
            self.text(self.sx(185), self.sy(326), "AUTO-SWITCHED", 8, CYAN_2, "bold")
            self.text(self.sx(185), self.sy(350), f"To {title_case(to_mode)}", 17, TEXT, "bold")
            self.pill(self.sx(152), self.sy(364), self.sx(218), self.sy(384), f"INSIDE {switch_temp:.0f}°", fill="#1f3945", outline="#335d6e", color=TEXT, size=8)

        # Status pill belongs above the dial, not partially underneath the outer
        # dial ring.  If it overlaps the ring it looks like a floating artifact.
        self.pill(self.sx(558), self.sy(234), self.sx(722), self.sy(260), action_label, fill="#252b36", outline="#3a4351", color=TEXT, size=10, dot=action_color)
        self.draw_web_style_dial(current, target, action_label, action_color)

        self.circle_button(378, 446, 31, "−", lambda: self.change_target(-1), fill="#242b34", outline="#3a444f", color=TEXT, size=26)
        self.circle_button(902, 446, 31, "+", lambda: self.change_target(1), fill="#242b34", outline="#3a444f", color=TEXT, size=25)

        self.draw_status_tile(110, 398, 148, 100, "Inside Doors", self.door_label(), "door", GREEN if self.door_label().lower() == "closed" else RED, lambda: self.show_toast("Door status updated from Home Assistant"), long_action=lambda: self.open_entity_picker("door_entity", ["binary_sensor", "cover"], "Select Inside Door / Entry Sensor"))
        self.draw_status_tile(1020, 398, 148, 100, "Alarmo", self.alarm_label(), "shield", GREEN, lambda: self.open_alarm(), long_action=lambda: self.open_entity_picker("alarm_entity", ["alarm_control_panel"], "Select Alarm Entity"))
        self.draw_relay_outputs(relays)

        self.draw_schedule_preset_bar()
        self.circle_button(54, 708, 23, "S", lambda: self.open_schedule(), fill="#143543", outline="#276273", color=TEXT, size=21)
        # Humidity is a simple web-style inline readout, not a boxed control.
        self.text(self.sx(244), self.sy(710), "HUMIDITY", 9, "#b8c4d0", "bold", "w")
        self.text(self.sx(328), self.sy(710), f"{hum:.0f}%", 14, TEXT, "bold", "w")
        self.segmented_control(500, 686, 360, 46, [("cool","Cool"),("heat","Heat"),("auto","Auto"),("away","Away")], "away" if away else mode, lambda v: self.toggle_away() if v == "away" else self.set_mode(v))
        self.segmented_control(888, 686, 152, 46, [("fan","Fan"),("auto", title_case(fan or "auto"))], "auto", lambda _v: self.set_fan("on" if fan != "on" else "auto"), label_first=True)


    def runtime_alert(self) -> dict[str, Any] | None:
        t = self.thermostat or {}
        outputs = self.status.get("outputs") if isinstance(self.status.get("outputs"), dict) else {}
        safety = str(t.get("safetyMode") or outputs.get("safetyMode") or "").strip().lower()
        current = as_float(t.get("currentTemp", t.get("current_temperature", 0)), 0)
        if safety in {"heat", "cool"}:
            target = as_float(t.get("safetyLow" if safety == "heat" else "safetyHigh"), t.get("targetTemp", 70))
            return {"type":"safety", "mode":safety, "title":f"Safety {title_case(safety)} Engaged", "detail":f"Setpoint {target:.0f}° • Current {current:.0f}°"}
        now_ms = time.time() * 1000
        auto_until = as_float(t.get("autoLockoutUntil"), 0)
        manual_until = as_float(t.get("manualLockoutUntil"), 0)
        until = max(auto_until, manual_until)
        if until > now_ms:
            remaining = int((until - now_ms) / 1000)
            mins, secs = divmod(max(0, remaining), 60)
            pending = str(t.get("autoPendingMode") or t.get("pendingMode") or "changeover")
            return {"type":"lockout", "mode":pending, "title":f"{title_case(pending)} Delay", "detail":f"Cooldown {mins}:{secs:02d} remaining"}
        return None

    def draw_runtime_alert(self) -> None:
        alert = self.runtime_alert()
        if not alert:
            return
        safety = alert.get("type") == "safety"
        x1,y1,x2,y2 = self.sx(108), self.sy(282), self.sx(312), self.sy(360)
        fill = "#351b17" if safety and alert.get("mode") == "heat" else "#112845" if safety else "#302513"
        outline = "#ff8065" if safety and alert.get("mode") == "heat" else CYAN if safety else YELLOW
        self.round_rect_shadow(x1,y1,x2,y2,self.sy(14),fill,outline,2,shadow="#06090d",offset=self.sy(2))
        self.text((x1+x2)//2, y1+self.sy(22), str(alert.get("title")), 12, TEXT, "bold")
        self.text((x1+x2)//2, y1+self.sy(44), str(alert.get("detail")), 9, MUTED, "bold")
        if alert.get("type") == "lockout":
            self.button(x1+self.sx(44), y1+self.sy(52), x2-self.sx(44), y2-self.sy(8), "Bypass", lambda:self.bypass_changeover(), fill="#493a13", outline=YELLOW, text=YELLOW, size=9, radius=8, border=1)

    def bypass_changeover(self) -> None:
        self.thermostat["autoLockoutUntil"] = 0
        self.thermostat["manualLockoutUntil"] = 0
        self._run_busy("Bypass", lambda: self.control({"autoLockoutUntil": 0, "manualLockoutUntil": 0, "autoPendingMode": ""}))


    def draw_temperature_value(self, x: int, y: int, value: float, number_size: int, degree_size: int, fill: str = TEXT, weight: str = "bold", degree_lift: int = 18) -> None:
        """Draw a clean thermostat number with a smaller raised degree mark."""
        number = f"{value:.0f}"
        degree = "°"
        number_font = self.font(number_size, weight)
        degree_font = self.font(degree_size, weight)
        number_width = number_font.measure(number)
        degree_width = degree_font.measure(degree)
        total_width = number_width + degree_width + self.sx(4)
        left = int(x - total_width / 2)
        self.canvas.create_text(left, y, text=number, fill=fill, font=number_font, anchor="w")
        self.canvas.create_text(left + number_width + self.sx(4), y - self.sy(degree_lift), text=degree, fill=fill, font=degree_font, anchor="w")

    def draw_web_style_dial(self, current: float, target: float, action_label: str, action_color: str) -> None:
        """Draw the thermostat dial using the old web dial geometry.

        Keep the native dial clean and circular.  No reflection artifacts, no
        fake oblong shine, and the visible min/max scale follows the comfort
        range returned by target_limits().
        """
        cx, cy = self.sx(640), self.sy(430)
        r = min(self.sx(148), self.sy(148))
        self.dial_area = (cx-r-self.sx(48), cy-r-self.sy(48), cx+r+self.sx(48), cy+r+self.sy(48))
        minimum, maximum = self.target_limits()
        target_pct = clamp((target - minimum) / max(1, maximum - minimum), 0, 1)
        current_pct = clamp((current - minimum) / max(1, maximum - minimum), 0, 1)

        # Old web dial scale: lower-left min, sweep over the top, lower-right max.
        # Canvas y grows downward, so 135° -> lower-left and 405°/45° -> lower-right.
        start_deg, span_deg = 135.0, 270.0

        # Outer glass puck and rings.
        self.canvas.create_oval(cx-r-18, cy-r-18, cx+r+18, cy+r+18, fill="#03060b", outline="#05070b", width=2)
        self.canvas.create_oval(cx-r-9, cy-r-9, cx+r+9, cy+r+9, fill="#0a111a", outline="#17202c", width=2)
        self.canvas.create_oval(cx-r+4, cy-r+4, cx+r-4, cy+r-4, fill="#0b121c", outline="#222c39", width=1)

        # Tick ring.  This is the primary range visual, so it must line up with
        # the same min/max limits used by dragging and the range labels.
        tick_count = 104
        for i in range(tick_count):
            pos = i / (tick_count - 1)
            angle = math.radians(start_deg + pos * span_deg)
            major = (i % 8 == 0)
            length = 21 if major else 13
            width = 2 if major else 1
            if pos <= target_pct:
                col = "#46d9ff" if pos < 0.62 else "#8d7dff"
            else:
                col = "#31404d"
            x1 = cx + math.cos(angle) * (r - length)
            y1 = cy + math.sin(angle) * (r - length)
            x2 = cx + math.cos(angle) * (r - 5)
            y2 = cy + math.sin(angle) * (r - 5)
            self.canvas.create_line(x1, y1, x2, y2, fill=col, width=width, capstyle="round")

        # Subtle inner rail; avoid Tk arc direction issues by drawing short line
        # segments with the exact same geometry as the tick ring.
        last = None
        rail_r = r - 37
        for i in range(80):
            pos = i / 79
            angle = math.radians(start_deg + pos * span_deg)
            point = (cx + math.cos(angle) * rail_r, cy + math.sin(angle) * rail_r)
            if last:
                self.canvas.create_line(last[0], last[1], point[0], point[1], fill="#192635", width=7, capstyle="round")
            last = point
        last = None
        active_steps = max(2, int(80 * target_pct))
        for i in range(active_steps):
            pos = i / 79
            angle = math.radians(start_deg + pos * span_deg)
            point = (cx + math.cos(angle) * rail_r, cy + math.sin(angle) * rail_r)
            if last:
                self.canvas.create_line(last[0], last[1], point[0], point[1], fill="#52dfff", width=7, capstyle="round")
            last = point

        # Current marker and target knob.
        def marker(pos: float, width: int, knob: bool) -> None:
            a = math.radians(start_deg + pos * span_deg)
            x1 = cx + math.cos(a) * (r - 12)
            y1 = cy + math.sin(a) * (r - 12)
            x2 = cx + math.cos(a) * (r + 14)
            y2 = cy + math.sin(a) * (r + 14)
            self.canvas.create_line(x1, y1, x2, y2, fill="#eef6ff", width=width, capstyle="round")
            if knob:
                self.canvas.create_oval(x2-self.sx(6), y2-self.sy(6), x2+self.sx(6), y2+self.sy(6), fill="#eef6ff", outline="#d8e6f7")
        marker(current_pct, 5, False)
        marker(target_pct, 4, True)

        # Inner face: match the web UI's dark glass dial instead of a bright
        # floating blue bubble.  The room temperature, set label, and setpoint
        # are one centered stack, fully inside the puck.
        inner = int(r * 0.64)
        action_lower = action_label.lower()
        if "heat" in action_lower:
            accent = "#ff8065"
            glow = "#3b1716"
        elif "cool" in action_lower:
            accent = "#53f0ff"
            glow = "#082e66"
        elif "away" in action_lower:
            accent = "#b986ff"
            glow = "#251845"
        else:
            accent = "#4ee083"
            glow = "#0b2a1d"
        self.canvas.create_oval(cx-inner-self.sx(14), cy-inner-self.sy(14), cx+inner+self.sx(14), cy+inner+self.sy(14), fill="#030811", outline=blend_hex(accent, "#07101a", 0.62), width=max(1, self.sx(2)))
        self.canvas.create_oval(cx-inner-self.sx(4), cy-inner-self.sy(4), cx+inner+self.sx(4), cy+inner+self.sy(4), fill=glow, outline=blend_hex(accent, "#07101a", 0.42), width=max(1, self.sx(1)))
        self.canvas.create_oval(cx-inner+self.sx(9), cy-inner+self.sy(9), cx+inner-self.sx(9), cy+inner-self.sy(9), fill="#07111e", outline="#1d3346", width=1)
        self.canvas.create_oval(cx-inner+self.sx(24), cy-inner+self.sy(18), cx+inner-self.sx(24), cy+self.sy(22), fill=blend_hex(glow, "#07111e", 0.42), outline="")
        self.canvas.create_arc(cx-inner+self.sx(12), cy-inner+self.sy(12), cx+inner-self.sx(12), cy+inner+self.sy(86), start=200, extent=140, style="arc", outline=blend_hex(accent, "#ffffff", 0.18), width=max(1, self.sy(2)))

        # Status/action lives in the pill above the dial. Keep this center clean.
        self.draw_temperature_value(cx, cy-self.sy(30), current, 66, 26, TEXT, "bold", degree_lift=26)
        self.text(cx, cy+self.sy(38), "Set Temperature", 8, "#bdd2e6", "bold")
        self.draw_temperature_value(cx, cy+self.sy(70), target, 30, 14, "#d8f7ff" if "cool" in action_lower else "#ffe2d6" if "heat" in action_lower else TEXT, "bold", degree_lift=12)
        self.pill(cx-self.sx(142), cy+self.sy(124), cx-self.sx(98), cy+self.sy(148), f"{minimum:.0f}°", fill="#080c12", outline="#111820", color=TEXT, size=10)
        self.pill(cx+self.sx(98), cy+self.sy(124), cx+self.sx(142), cy+self.sy(148), f"{maximum:.0f}°", fill="#080c12", outline="#111820", color=TEXT, size=10)


    def door_label(self) -> str:
        door = self.door_state or {}
        state = str(door.get("state", "unknown")).lower() if door else "unknown"
        return "Open" if state in {"on", "open", "unlocked"} else "Closed" if state in {"off", "closed", "locked"} else "Closed"

    def alarm_label(self) -> str:
        alarm = self.alarm_state or {}
        return title_case(alarm.get("state", "Disarmed")) if alarm else "Disarmed"

    def draw_status_tile(self, x:int, y:int, w:int, h:int, title:str, state:str, icon:str, accent:str, action:Callable[[],None], long_action: Callable[[], None] | None = None) -> None:
        x1,y1,x2,y2 = self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h)
        openish = state.lower() in {"open", "triggered", "armed away", "armed home"}
        border = ORANGE if title.lower().startswith("inside") and openish else "#18a877" if accent == GREEN else accent
        fill = "#172223" if not openish else "#2d210c"
        self.buttons.append(ButtonSpec(x1,y1,x2,y2,title,action,fill,border,TEXT,"",long_action))
        self.round_rect_shadow(x1,y1,x2,y2,self.sy(15),fill,border,2,shadow="#06090d",offset=self.sy(3))
        icx, icy = x + w//2, y + 30
        self.canvas.create_oval(self.sx(icx-28), self.sy(icy-28), self.sx(icx+28), self.sy(icy+28), fill="#173432" if not openish else "#4a3516", outline="#2f695f" if not openish else "#ae7b22", width=1)
        if icon == "door":
            self.canvas.create_rectangle(self.sx(icx-10), self.sy(icy-17), self.sx(icx+11), self.sy(icy+17), outline="#b9fff3", width=2)
            self.canvas.create_line(self.sx(icx-10), self.sy(icy-17), self.sx(icx+3), self.sy(icy-24), fill="#b9fff3", width=1)
            self.canvas.create_oval(self.sx(icx+6), self.sy(icy), self.sx(icx+10), self.sy(icy+4), fill="#b9fff3", outline="")
        else:
            pts=[self.sx(icx),self.sy(icy-20),self.sx(icx+20),self.sy(icy-9),self.sx(icx+15),self.sy(icy+15),self.sx(icx),self.sy(icy+25),self.sx(icx-15),self.sy(icy+15),self.sx(icx-20),self.sy(icy-9)]
            self.canvas.create_polygon(pts, fill="#2a8d61", outline="#b9fff3", width=2)
        self.text(self.sx(x+w//2), self.sy(y+67), title, 11, TEXT, "bold")
        pill_w = min(w-42, 86)
        self.pill(self.sx(x+(w-pill_w)//2), self.sy(y+80), self.sx(x+(w+pill_w)//2), self.sy(y+98), state.upper()[:13], fill="#1b4b39" if not openish else "#604311", outline=border, color="#effff8", size=7)


    def draw_relay_outputs(self, relays: dict[str, Any]) -> None:
        # Keep the useful live relay indicators, but remove the virtual room-temp
        # slider. Current room temperature now comes from the selected HA entry.
        x,y,w,h = 998, 226, 172, 76
        self.round_rect(self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h), self.sy(14), "#17212b", "#2b3744", 1)
        self.text(self.sx(x+w//2), self.sy(y+16), "OUTPUTS", 8, MUTED, "bold")
        labels=[("fan","Fan"),("heat","Heat"),("cool","Cool")]
        for i,(key,label) in enumerate(labels):
            bx=x+12+i*52
            on=as_bool(relays.get(key))
            self.round_rect(self.sx(bx), self.sy(y+30), self.sx(bx+42), self.sy(y+60), self.sy(8), "#164d3d" if on else "#262d36", "#2d755d" if on else "#3a444f", 1)
            self.canvas.create_oval(self.sx(bx+17), self.sy(y+36), self.sx(bx+24), self.sy(y+43), fill=GREEN if on else "#cfd4da", outline="")
            self.text(self.sx(bx+21), self.sy(y+52), label, 7, TEXT, "bold")

    def segmented_control(self, x:int, y:int, w:int, h:int, items:list[tuple[str,str]], active:str, on_pick:Callable[[str],None], label_first:bool=False) -> None:
        x1,y1,x2,y2 = self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h)
        self.round_rect_shadow(x1,y1,x2,y2,self.sy(18),"#202832","#394451",1,shadow="#070b10",offset=max(1,self.sy(2)))
        cell = w / len(items)
        for i,(key,label) in enumerate(items):
            bx1=self.sx(x+i*cell+4); bx2=self.sx(x+(i+1)*cell-4)
            selected = (key == active and not label_first) or (label_first and i == len(items)-1)
            if key == active and not label_first:
                fill = CYAN if key != "auto" else "#8c78ff"
                if key == "cool": fill = CYAN
                if key == "heat": fill = "#ff8065"
                if key == "away": fill = "#b78cff"
                self.round_rect(bx1,self.sy(y+5),bx2,self.sy(y+h-5),self.sy(15),fill,"#99edff",1)
                color=BLACK
            else:
                color=MUTED if label_first and i==0 else TEXT if key==active else "#aab4c0"
            self.buttons.append(ButtonSpec(bx1,y1,bx2,y2,label,lambda k=key:on_pick(k),"","",color))
            self.text((bx1+bx2)//2, self.sy(y+h/2), label, 13, color, "bold")


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
        candidates: list[Any] = []
        candidates.append(self.thermostat.get("schedules"))
        if isinstance(self.config, dict):
            candidates.append((self.config.get("thermostat") or {}).get("schedules") if isinstance(self.config.get("thermostat"), dict) else None)
            candidates.append(self.config.get("schedules"))
        for schedules in candidates:
            if isinstance(schedules, list) and schedules:
                return [item for item in schedules if isinstance(item, dict)]
        # The last known config provided by the browser build did not contain schedule rows,
        # so keep the front S shortcut useful instead of showing a dead/no-schedule screen.
        if os.environ.get("SMART_NATIVE_FALLBACK_SCHEDULES", "1").strip().lower() not in {"0", "false", "no", "off"}:
            return [dict(item) for item in DEFAULT_NATIVE_SCHEDULES]
        return []


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
        max_items = min(5, len(schedules))
        w, h, gap = 132, 34, 8
        total = w * max_items + gap * (max_items - 1)
        x = (1280 - total) // 2
        y = 632
        for sched in schedules[:max_items]:
            name = str(sched.get("name") or sched.get("label") or "Schedule")[:13]
            x1,y1,x2,y2 = self.sx(x), self.sy(y), self.sx(x+w), self.sy(y+h)
            self.buttons.append(ButtonSpec(x1,y1,x2,y2,name,lambda s=sched:self.apply_schedule(s),"#132c3e","#275774",TEXT))
            self.round_rect(x1,y1,x2,y2,self.sy(10),"#132c3e","#275774",1)
            self.text((x1+x2)//2, (y1+y2)//2, name, 11, TEXT, "bold")
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

    def effective_limit_mode(self) -> str:
        """Return the comfort-limit bucket that should drive the dial scale.

        The web panel does not use the broad auto safety range for the visible
        dial when Auto is actively controlling cool/heat.  In Auto, the dial
        should follow the active side of the changeover so an Auto→Cool screen
        uses the Cool comfort range, for example 65–80.
        """
        mode = str(self.thermostat.get("mode", "cool")).lower()
        if mode in {"cool", "heat"}:
            return mode
        if mode == "auto":
            for key in ("autoActiveMode", "autoPendingMode", "manualPendingMode"):
                value = str(self.thermostat.get(key, "") or "").lower()
                if value in {"cool", "heat"}:
                    return value
            action = str(self.thermostat.get("hvac_action", self.thermostat.get("hvacAction", "")) or "").lower()
            if "heat" in action:
                return "heat"
            if "cool" in action:
                return "cool"
            return "cool"
        return "cool"

    def target_limits(self) -> tuple[float, float]:
        mode = self.effective_limit_mode()
        limits_all = self.thermostat.get("limits", {}) if isinstance(self.thermostat.get("limits"), dict) else {}
        limits = limits_all.get(mode) or limits_all.get("cool") or limits_all.get("auto") or {}
        # Match the comfort-limit behavior from the web panel.  Auto→Cool should
        # not fall back to the broad 45–95 safety range; it should use the cool
        # comfort range, normally 65–80, unless the saved config overrides it.
        if mode == "heat":
            default_low, default_high = 60, 78
        else:
            default_low, default_high = 65, 80
        low = as_float(limits.get("min"), default_low)
        high = as_float(limits.get("max"), default_high)
        if high <= low:
            high = low + 2
        return low, high

    def begin_dial_adjust(self, x: int, y: int) -> bool:
        if self.locked:
            self.show_toast("Panel locked")
            return True
        if not self.dial_area:
            return False
        x1, y1, x2, y2 = self.dial_area
        if not (x1 <= x <= x2 and y1 <= y <= y2):
            return False
        cx, cy = self.sx(640), self.sy(430)
        r = min(self.sx(152), self.sy(152))
        dist = math.hypot(x - cx, y - cy)
        self.drag_origin_y = y
        self.drag_origin_target = as_float(self.thermostat.get("targetTemp", self.thermostat.get("target_temperature", 70)), 70)
        if dist < r * 0.55:
            self.drag_target = "dial_linear"
            return True
        self.drag_target = "dial_arc"
        self.apply_target_from_point(x, y, final=False)
        return True

    def apply_target_from_point(self, x: int, y: int, final: bool = False) -> None:
        minimum, maximum = self.target_limits()
        if self.drag_target == "dial_linear":
            value = self.drag_origin_target + (self.drag_origin_y - y) / max(6, self.sy(8))
        else:
            cx, cy = self.sx(640), self.sy(430)
            angle = math.degrees(math.atan2(y - cy, x - cx))
            if angle < 0:
                angle += 360
            start, span = 135.0, 270.0
            mapped = angle
            if mapped < start:
                mapped += 360
            pos = clamp((mapped - start) / span, 0.0, 1.0)
            value = minimum + pos * (maximum - minimum)
        value = round(clamp(value, minimum, maximum))
        if self.last_target_value == value and not final:
            return
        self.last_target_value = value
        self.thermostat["targetTemp"] = value
        self.draw()
        now = time.time()
        if final or now - self.last_target_send >= 0.25:
            self.last_target_send = now
            self._run_async("Target", lambda v=value: self.control({"targetTemp": v}))

    def change_target(self, delta: float) -> None:
        target = as_float(self.thermostat.get("targetTemp", self.thermostat.get("target_temperature", 70)), 70) + delta
        minimum, maximum = self.target_limits()
        target = clamp(target, minimum, maximum)
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

    def current_room_label(self, section: str) -> str:
        _cfg, _rooms, _key, room = self.room_bundle(section)
        return str(room.get("label") or title_case(_key) or "Room")

    def set_active_room(self, section: str, room_key: str) -> None:
        self.active_rooms[section] = room_key
        self.draw()

    def draw_page_shell(self, kicker: str, title: str, section: str | None = None) -> tuple[int, int, int, int]:
        self.glass_panel(self.sx(30), self.sy(92), self.sx(1250), self.sy(768), self.sy(26), fill="#141d27", outline="#30404e")
        self.text(self.sx(50), self.sy(122), kicker.upper(), 10, CYAN_2, "bold", "w")
        self.text(self.sx(50), self.sy(165), title, 42, TEXT, "bold", "w")
        if section:
            _cfg, rooms, selected, _room = self.room_bundle(section)
            keys = list(rooms.keys())
            x = 1220
            for key in reversed(keys[:5]):
                label = str((rooms.get(key) or {}).get("label") or key).strip()[:16]
                w = max(86, min(150, 30 + len(label) * 10))
                active = key == selected
                self.glossy_button(self.sx(x - w), self.sy(112), self.sx(x), self.sy(154), label, lambda k=key, sec=section: self.set_active_room(sec, k), active=active, size=10)
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
            self.buttons.append(ButtonSpec(x1,y1,x2,y2,"assign-blind",lambda: self.show_toast("Hold to assign this blind"),"","",TEXT,"",lambda rk=_room_key, idx=i: self.open_entity_picker("blind_entity", ["cover"], "Select Blind Cover", {"room": rk, "index": idx})))
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
        self.button(self.sx(1115), self.sy(228), self.sx(1200), self.sy(270), "Player ▾", lambda: self.open_entity_picker("media_player", ["media_player"], "Select Audio Media Player"), fill="#122533", outline="#274b61", text=TEXT, size=11)
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
            self.buttons.append(ButtonSpec(x1,y1,x2,y2,"toggle",lambda l=light: self.light_action(l, "toggle"),"","",TEXT,"",lambda rk=_room_key, idx=i: self.open_entity_picker("light_entity", ["light"], "Select Light Entity", {"room": rk, "index": idx})))
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
            self.buttons.append(ButtonSpec(x1,y1,x2,y2,"toggle",lambda c=control: self.room_action(c, "toggle"),"","",TEXT,"",lambda rk=_room_key, idx=i: self.open_entity_picker("room_entity", ["switch", "light", "cover", "binary_sensor", "lock", "input_boolean"], "Select Room Control Entity", {"room": rk, "index": idx})))

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
        if self.modal in {"settings", "entity_picker"}:
            x1,y1,x2,y2 = self.sx(84), self.sy(76), self.sx(1196), self.sy(748)
        elif self.modal == "alarm":
            x1,y1,x2,y2 = self.sx(310), self.sy(150), self.sx(970), self.sy(650)
        else:
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
        elif self.modal == "auto_switch":
            self.draw_auto_switch_modal(x1,y1,x2,y2)
        elif self.modal == "text_input":
            self.draw_text_input_modal(x1,y1,x2,y2)
        elif self.modal == "entity_picker":
            self.draw_entity_picker_modal(x1,y1,x2,y2)


    def auto_switch_notice(self) -> dict[str, Any] | None:
        notice = self.thermostat.get("autoSwitchNotice") if isinstance(self.thermostat, dict) else None
        if not isinstance(notice, dict) or not as_bool(notice.get("active")):
            return None
        return notice

    def empty_auto_switch_notice(self) -> dict[str, Any]:
        return {"active": False, "source": "", "fromMode": "", "toMode": "", "switchTemp": 0, "outdoorTemp": 0, "coolTarget": 0, "heatTarget": 0, "createdAt": 0}

    def open_auto_switch(self) -> None:
        if not self.auto_switch_notice():
            return
        self.modal = "auto_switch"
        self.modal_data = {}
        self.draw()

    def draw_auto_switch_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        notice = self.auto_switch_notice() or {}
        from_mode = title_case(notice.get("fromMode") or "previous mode")
        to_mode = title_case(notice.get("toMode") or notice.get("mode") or self.thermostat.get("mode") or "cool")
        room_temp = as_float(notice.get("switchTemp"), as_float(self.thermostat.get("currentTemp", self.thermostat.get("current_temperature", 0)), 0))
        heat_target = as_float(notice.get("heatTarget"), as_float(self.thermostat.get("autoHeatOutdoorTarget"), 68))
        cool_target = as_float(notice.get("coolTarget"), as_float(self.thermostat.get("autoCoolOutdoorTarget"), 74))
        self.text((x1+x2)//2, y1+self.sy(42), f"Auto-switched to {to_mode}", 28, TEXT, "bold")
        self.text((x1+x2)//2, y1+self.sy(92), f"Inside is {room_temp:.0f}°. Heat target {heat_target:.0f}° / Cool target {cool_target:.0f}°.", 16, MUTED, "bold")
        self.text((x1+x2)//2, y1+self.sy(128), "Dismiss the notice or revert until the next room temperature swing.", 14, MUTED, "bold")
        self.button(x1+self.sx(110), y1+self.sy(210), x1+self.sx(330), y1+self.sy(278), f"Revert to {from_mode}", lambda: self.revert_auto_switch_notice(), fill="#173246", text=TEXT)
        self.button(x1+self.sx(360), y1+self.sy(210), x1+self.sx(555), y1+self.sy(278), "Dismiss", lambda: self.dismiss_auto_switch_notice(), fill="#163d2a", text=GREEN)
        self.button(x2-self.sx(190), y2-self.sy(90), x2-self.sx(45), y2-self.sy(35), "Close", lambda: self.close_modal(), fill="#173246")

    def dismiss_auto_switch_notice(self) -> None:
        self.thermostat["autoSwitchNotice"] = self.empty_auto_switch_notice()
        self.close_modal()
        self._run_busy("Auto Switch", lambda: self.control({"autoSwitchNotice": self.empty_auto_switch_notice()}))

    def revert_auto_switch_notice(self) -> None:
        notice = self.auto_switch_notice() or {}
        from_mode = str(notice.get("fromMode") or "").strip().lower()
        if from_mode not in {"cool", "heat", "auto", "off"}:
            from_mode = "auto"
        self.thermostat["mode"] = from_mode
        self.thermostat["autoSwitchNotice"] = self.empty_auto_switch_notice()
        hold = {"active": True, "source": "native", "mode": from_mode}
        self.close_modal()
        self._run_busy("Auto Switch", lambda: self.control({"mode": from_mode, "autoSwitchNotice": self.empty_auto_switch_notice(), "autoSwitchHold": hold}))

    def draw_info_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        info = self.system_info or {}
        self.text((x1+x2)//2, y1+self.sy(42), "Panel Info", 30, TEXT, "bold")
        lines = [
            f"Version: {info.get('version') or _version()}",
            f"Address: {info.get('address') or info.get('ipAddress') or socket.gethostname()}",
            f"Host: {info.get('host') or socket.gethostname()}",
            f"Uptime: {info.get('uptime') or ''}",
            f"Display: {DISPLAY_RUNTIME_LABEL}",
            f"Chromium: disabled for the wall display",
            f"Native frame: {FRAME_MS} ms  •  API poll: {POLL_MS}/{SLOW_POLL_MS} ms",
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
        self.text((x1+x2)//2, y1+self.sy(42), "Enter Panel Code", 28, TEXT, "bold")
        self.text((x1+x2)//2, y1+self.sy(72), "The code unlocks automatically after 4 digits.", 12, MUTED, "bold")
        # Modern code dots instead of the old-school text row.
        cx = (x1+x2)//2 - self.sx(72)
        cy = y1+self.sy(118)
        for i in range(4):
            filled = i < len(self.code_buffer)
            self.canvas.create_oval(cx+self.sx(i*48), cy-self.sy(12), cx+self.sx(i*48)+self.sx(24), cy+self.sy(12), fill=CYAN if filled else "#182833", outline="#3d6678", width=2)
        keys = ["1","2","3","4","5","6","7","8","9","⌫","0","Clear"]
        pad_w, pad_h = self.sx(96), self.sy(64)
        start_x = (x1+x2)//2 - self.sx(158)
        start_y = y1+self.sy(158)
        for idx,k in enumerate(keys):
            col=idx%3; row=idx//3
            bx=start_x+self.sx(col*110); by=start_y+self.sy(row*76)
            fill = "#122d3d" if k.isdigit() else "#263141"
            self.button(bx,by,bx+pad_w,by+pad_h,k,lambda kk=k,t=target:self.handle_code_key(kk,t),fill=fill,outline="#46606e",text=TEXT,size=20 if k.isdigit() else 13,radius=18)
        self.button(x2-self.sx(190), y2-self.sy(82), x2-self.sx(45), y2-self.sy(34), "Cancel", lambda: self.close_modal(), fill="#34202a", size=14)

    def handle_code_key(self, key: str, target: str) -> None:
        if key in {"C", "Clear"}:
            self.code_buffer = ""
            self.draw()
            return
        if key in {"⌫", "Back"}:
            self.code_buffer = self.code_buffer[:-1]
            self.draw()
            return
        if key == "OK":
            self.validate_code(target)
            return
        if key.isdigit():
            self.code_buffer = (self.code_buffer + key)[-4:]
            if len(self.code_buffer) >= 4:
                self.validate_code(target)
            else:
                self.draw()

    def validate_code(self, target: str) -> None:
        code = self._access_code()
        if self.code_buffer == code or self.code_buffer == DEFAULT_ACCESS_CODE:
            if target == "unlock":
                self.locked = False
                self.close_modal()
                self.show_toast("Unlocked")
            else:
                self.modal = "settings"
                self.modal_data = {}
                self.code_buffer = ""
                self.draw()
        else:
            self.show_toast("Wrong code")
            self.code_buffer = ""
            self.draw()

    def draw_settings_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        t = self.thermostat
        cfg = self.config if isinstance(self.config, dict) else {}
        ha_cfg = ((cfg.get("integrations") or {}).get("homeAssistant") or {}) if isinstance(cfg, dict) else {}
        self.text(x1+self.sx(42), y1+self.sy(42), f"{title_case(self.page)} Settings", 28, TEXT, "bold", "w")
        self.text(x1+self.sx(42), y1+self.sy(74), "Settings here only control the current page. Update/restart/config import stay under the i button.", 12, MUTED, "bold", "w")

        page_tabs = {
            "thermostat": [("comfort","Comfort"),("auto","Auto"),("equipment","Equipment"),("entries","Entries"),("security","Security"),("ha","Home Assistant"),("display","Display")],
            "blinds": [("blinds","Blinds"),("ha","Home Assistant"),("display","Display")],
            "lights": [("lights","Lights"),("ha","Home Assistant"),("display","Display")],
            "audio": [("audio","Audio"),("ha","Home Assistant"),("display","Display")],
            "room": [("room","Room"),("ha","Home Assistant"),("display","Display")],
        }
        tabs = page_tabs.get(self.page, page_tabs["thermostat"])
        allowed = {k for k,_ in tabs}
        if self.settings_tab not in allowed:
            self.settings_tab = tabs[0][0]
        tx = x1 + self.sx(40)
        ty = y1 + self.sy(96)
        tab_w = max(88, min(128, int(780 / max(1, len(tabs)))))
        for key, label in tabs:
            active = self.settings_tab == key
            self.small_button(tx, ty, tx+self.sx(tab_w), ty+self.sy(34), label, lambda k=key:self.set_settings_tab(k), fill=CYAN if active else "#202a36", outline="#6eeeff" if active else "#3a4552", text=BLACK if active else TEXT, size=9)
            tx += self.sx(tab_w + 8)

        def ent_value(name: str) -> str:
            return self._entity_label(ha_cfg.get(name))

        sections: dict[str, list[dict[str, Any]]] = {
            "comfort": [
                {"label":"Target Temp", "key":"targetTemp", "kind":"number", "step":1},
                {"label":"Cool Min", "kind":"limit", "mode":"cool", "bound":"min", "step":1},
                {"label":"Cool Max", "kind":"limit", "mode":"cool", "bound":"max", "step":1},
                {"label":"Heat Min", "kind":"limit", "mode":"heat", "bound":"min", "step":1},
                {"label":"Heat Max", "kind":"limit", "mode":"heat", "bound":"max", "step":1},
                {"label":"Away Heat", "key":"awayHeat", "kind":"number", "step":1},
                {"label":"Away Cool", "key":"awayCool", "kind":"number", "step":1},
                {"label":"Low Safety", "key":"safetyLow", "kind":"number", "step":1},
                {"label":"High Safety", "key":"safetyHigh", "kind":"number", "step":1},
                {"label":"Humidity", "key":"humidity", "kind":"read"},
                {"label":"Outdoor Temp", "key":"outdoorTemp", "kind":"read"},
                {"label":"Outdoor Wind", "key":"outdoorWindSpeed", "kind":"read"},
            ],
            "auto": [
                {"label":"Auto Cool Target", "key":"autoCoolOutdoorTarget", "kind":"number", "step":1},
                {"label":"Auto Heat Target", "key":"autoHeatOutdoorTarget", "kind":"number", "step":1},
                {"label":"Auto Lockout Min", "key":"autoChangeoverLockoutMinutes", "kind":"number", "step":15},
                {"label":"Manual Lockout Min", "key":"manualChangeoverLockoutMinutes", "kind":"number", "step":1},
                {"label":"Auto Active Mode", "key":"autoActiveMode", "kind":"read"},
                {"label":"Auto Pending Mode", "key":"autoPendingMode", "kind":"read"},
                {"label":"Heat Lock", "key":"heatLocked", "kind":"bool"},
                {"label":"Cool Lock", "key":"coolLocked", "kind":"bool"},
            ],
            "equipment": [
                {"label":"Fan Mode", "key":"fan", "kind":"read"},
                {"label":"Cool Fan Hold Min", "key":"coolFanRemainOnMinutes", "kind":"number", "step":1},
                {"label":"Changeover Delay", "key":"manualChangeoverLockoutMinutes", "kind":"number", "step":1},
                {"label":"Fan Relay", "key":"relayFan", "kind":"relay", "relay":"fan"},
                {"label":"Heat Relay", "key":"relayHeat", "kind":"relay", "relay":"heat"},
                {"label":"Cool Relay", "key":"relayCool", "kind":"relay", "relay":"cool"},
                {"label":"Release Manual", "key":"relayRelease", "kind":"release"},
            ],
            "entries": [
                {"label":"Current Temp Entry", "value": ent_value("currentTempEntity"), "kind":"entity", "edit":"current_temp_entity", "domains":["sensor","number","input_number"], "title":"Select Current Temperature Entry"},
                {"label":"Inside Door Entity", "value": ent_value("doorEntity"), "kind":"entity", "edit":"door_entity", "domains":["binary_sensor","cover"], "title":"Select Inside Door / Entry Sensor"},
                {"label":"Alarm Entity", "value": ent_value("alarmEntity"), "kind":"entity", "edit":"alarm_entity", "domains":["alarm_control_panel"], "title":"Select Alarm Entity"},
                {"label":"Weather Entity", "value": ent_value("weatherEntity"), "kind":"entity", "edit":"weather_entity", "domains":["weather"], "title":"Select Weather Entity"},
            ],
            "security": [
                {"label":"Panel Code", "value": "****", "kind":"edit", "edit":"panel_code", "numeric": True, "maxlen": 4},
                {"label":"Alarm Code", "value": "****" if (cfg.get("alarm") or {}).get("disarmCode") else "Not set", "kind":"edit", "edit":"alarm_code", "numeric": True, "maxlen": 12},
                {"label":"Settings Protected", "value":"Enabled", "kind":"value"},
            ],
            "ha": [
                {"label":"HA URL", "value": str(ha_cfg.get("url") or "Not configured"), "kind":"edit", "edit":"ha_url", "numeric": False, "maxlen": 160},
                {"label":"HA Token", "value": "Saved" if ha_cfg.get("token") else "Not configured", "kind":"edit", "edit":"ha_token", "numeric": False, "maxlen": 512},
                {"label":"Alarm Entity", "value": ent_value("alarmEntity"), "kind":"entity", "edit":"alarm_entity", "domains":["alarm_control_panel"], "title":"Select Alarm Entity"},
                {"label":"Weather Entity", "value": ent_value("weatherEntity"), "kind":"entity", "edit":"weather_entity", "domains":["weather"], "title":"Select Weather Entity"},
            ],
            "display": [
                {"label":"Brightness", "value": str(((cfg.get("display") or {}).get("brightnessPercent") or 100)), "kind":"brightness"},
                {"label":"Screen Timeout", "value": str(((cfg.get("display") or {}).get("screenTimeoutMinutes") or "Off")), "kind":"edit", "edit":"screen_timeout", "numeric": True, "maxlen": 3},
                {"label":"Theme", "value": str(((cfg.get("display") or {}).get("theme") or "Regular")), "kind":"value"},
                {"label":"Version", "value": _version(), "kind":"value"},
            ],
            "blinds": [
                {"label":"Current Room", "value": self.current_room_label("blinds"), "kind":"value"},
                {"label":"Assign Blinds", "value":"Long-press a blind card", "kind":"value"},
                {"label":"Room Count", "value": str(len(((cfg.get("blinds") or {}).get("rooms") or {}))), "kind":"value"},
            ],
            "lights": [
                {"label":"Current Room", "value": self.current_room_label("lights"), "kind":"value"},
                {"label":"Assign Lights", "value":"Long-press a light card", "kind":"value"},
                {"label":"Room Count", "value": str(len(((cfg.get("lights") or {}).get("rooms") or {}))), "kind":"value"},
            ],
            "audio": [
                {"label":"Media Player", "value": str(ha_cfg.get("selectedMediaPlayerId") or "Not selected"), "kind":"entity", "edit":"media_player", "domains":["media_player"], "title":"Select Audio Media Player"},
                {"label":"Source", "value": str(ha_cfg.get("selectedSource") or "TV"), "kind":"value"},
                {"label":"Presets", "value":"Movie / Show / 40% / Max", "kind":"value"},
            ],
            "room": [
                {"label":"Current Room", "value": self.current_room_label("room"), "kind":"value"},
                {"label":"Assign Controls", "value":"Long-press a room card", "kind":"value"},
                {"label":"Supported", "value":"switch, light, cover, lock, sensor", "kind":"value"},
            ],
        }
        rows = sections.get(self.settings_tab, sections.get(tabs[0][0], sections["comfort"]))
        grid_x = x1 + self.sx(44)
        grid_y = y1 + self.sy(150)
        card_w, card_h, gap_x, gap_y = 316, 64, 28, 14
        for idx, item in enumerate(rows[:12]):
            col = idx % 3
            row = idx // 3
            bx = grid_x + self.sx(col * (card_w + gap_x))
            by = grid_y + self.sy(row * (card_h + gap_y))
            self.draw_setting_card(bx, by, self.sx(card_w), self.sy(card_h), item)
        if self.settings_tab in {"blinds","lights","room"}:
            self.text(x1+self.sx(48), y2-self.sy(98), "Tip: press and hold any card on this page to choose a Home Assistant entity.", 13, CYAN_2, "bold", "w")
        self.button(x2-self.sx(180), y2-self.sy(72), x2-self.sx(36), y2-self.sy(28), "Close", lambda: self.close_modal(), fill="#173246", size=14)


    def set_settings_tab(self, tab: str) -> None:
        self.settings_tab = tab
        self.draw()

    def _entity_label(self, item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("name") or item.get("entityId") or item.get("entity_id") or "Not selected")[:34]
        return str(item or "Not selected")[:34]

    def draw_setting_card(self, x:int, y:int, w:int, h:int, item:dict[str, Any]) -> None:
        self.round_rect(x,y,x+w,y+h,self.sy(12),"#18232d","#344250",1)
        self.text(x+self.sx(14), y+self.sy(19), str(item.get("label","Setting")), 12, MUTED, "bold", "w")
        kind = item.get("kind")
        key = item.get("key")
        if kind == "limit":
            mode = str(item.get("mode", "cool"))
            bound = str(item.get("bound", "min"))
            val = self.get_limit_value(mode, bound)
            self.small_button(x+w-self.sx(104), y+self.sy(18), x+w-self.sx(70), y+self.sy(52), "−", lambda m=mode,b=bound,st=item.get("step",1): self.adjust_limit(m, b, -as_float(st,1)), fill="#173246", size=14)
            self.text(x+w-self.sx(52), y+self.sy(36), f"{val:.0f}", 18, TEXT, "bold")
            self.small_button(x+w-self.sx(34), y+self.sy(18), x+w-self.sx(2), y+self.sy(52), "+", lambda m=mode,b=bound,st=item.get("step",1): self.adjust_limit(m, b, as_float(st,1)), fill="#173246", size=14)
        elif kind == "number":
            val = as_float(self.thermostat.get(key), 0)
            self.small_button(x+w-self.sx(104), y+self.sy(18), x+w-self.sx(70), y+self.sy(52), "−", lambda k=key,st=item.get("step",1): self.adjust_setting(k, -as_float(st,1)), fill="#173246", size=14)
            self.text(x+w-self.sx(52), y+self.sy(36), f"{val:.0f}", 18, TEXT, "bold")
            self.small_button(x+w-self.sx(34), y+self.sy(18), x+w-self.sx(2), y+self.sy(52), "+", lambda k=key,st=item.get("step",1): self.adjust_setting(k, as_float(st,1)), fill="#173246", size=14)
        elif kind == "bool":
            val = as_bool(self.thermostat.get(key))
            self.small_button(x+w-self.sx(98), y+self.sy(18), x+w-self.sx(12), y+self.sy(52), "ON" if val else "OFF", lambda k=key: self.toggle_setting(k), fill="#164d3d" if val else "#40202a", text=TEXT, size=12)
        elif kind == "relay":
            self.small_button(x+w-self.sx(110), y+self.sy(18), x+w-self.sx(12), y+self.sy(52), "Pulse", lambda r=item.get("relay","fan"): self.relay(str(r)), fill="#173246", size=12)
        elif kind == "release":
            self.small_button(x+w-self.sx(120), y+self.sy(18), x+w-self.sx(12), y+self.sy(52), "Release", lambda: self.release_relays(), fill="#402817", text=YELLOW, size=12)
        elif kind == "upload":
            self.small_button(x+w-self.sx(120), y+self.sy(18), x+w-self.sx(12), y+self.sy(52), "Upload", lambda: self.upload_config(), fill="#163d2a", text=GREEN, size=12)
        elif kind == "update":
            self.small_button(x+w-self.sx(120), y+self.sy(18), x+w-self.sx(12), y+self.sy(52), "Fetch", lambda: self.fetch_update(), fill="#17405a", text=CYAN, size=12)
        elif kind == "restart":
            self.small_button(x+w-self.sx(120), y+self.sy(18), x+w-self.sx(12), y+self.sy(52), "Restart", lambda: self.restart_panel(), fill="#402817", text=YELLOW, size=12)
        elif kind == "lock":
            self.small_button(x+w-self.sx(120), y+self.sy(18), x+w-self.sx(12), y+self.sy(52), "Unlock" if self.locked else "Lock", lambda: self.toggle_lock(), fill="#173246", size=12)
        elif kind == "entity":
            value = item.get("value", "Tap Select")
            self.text(x+w-self.sx(126), y+self.sy(42), str(value)[:24], 11, TEXT, "bold", "e")
            button_label = "Choose" if str(item.get("edit") or "") == "current_temp_entity" else "Select"
            self.small_button(x+w-self.sx(112), y+self.sy(18), x+w-self.sx(12), y+self.sy(52), button_label, lambda it=item: self.open_entity_picker(str(it.get("edit")), list(it.get("domains") or []), str(it.get("title") or it.get("label") or "Select Entity"), it.get("target") if isinstance(it.get("target"), dict) else None), fill="#173246", text=CYAN_2, size=12)
        elif kind == "brightness":
            display_cfg = self.config.get("display") if isinstance(self.config.get("display"), dict) else {}
            val = int(clamp(as_float(display_cfg.get("brightnessPercent"), 100), 1, 100))
            self.small_button(x+w-self.sx(104), y+self.sy(18), x+w-self.sx(70), y+self.sy(52), "−", lambda: self.adjust_brightness(-10), fill="#173246", size=14)
            self.text(x+w-self.sx(52), y+self.sy(36), f"{val}%", 16, TEXT, "bold")
            self.small_button(x+w-self.sx(34), y+self.sy(18), x+w-self.sx(2), y+self.sy(52), "+", lambda: self.adjust_brightness(10), fill="#173246", size=14)
        elif kind == "edit":
            value = item.get("value", self.thermostat.get(key, ""))
            display = "****" if str(item.get("edit", "")).endswith("code") or str(item.get("edit", "")) == "ha_token" else str(value or "Tap to set")
            self.text(x+w-self.sx(130), y+self.sy(42), display[:24], 12, TEXT, "bold", "e")
            self.small_button(x+w-self.sx(112), y+self.sy(18), x+w-self.sx(12), y+self.sy(52), "Edit", lambda it=item: self.open_text_input(it), fill="#173246", text=CYAN_2, size=12)
        else:
            value = item.get("value", self.thermostat.get(key, ""))
            self.text(x+w-self.sx(14), y+self.sy(42), str(value)[:28], 12, TEXT, "bold", "e")


    def get_ha_entity_id(self, value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("entityId") or value.get("entity_id") or "")
        return str(value or "")

    def entity_display_name(self, ent: dict[str, Any]) -> str:
        return str(ent.get("name") or ent.get("friendly_name") or ent.get("entityId") or ent.get("entity_id") or "Entity")

    def open_entity_picker(self, edit: str, domains: list[str], title: str, target: dict[str, Any] | None = None) -> None:
        self.modal = "entity_picker"
        self.modal_data = {"edit": edit, "domains": domains, "title": title, "target": target or {}, "loading": True, "entities": [], "error": "", "page": 0}
        self.draw()
        self._run_async("entity_picker", lambda d=list(domains), e=edit: self.fetch_entity_picker(d, e))

    def is_temperature_entry(self, ent: dict[str, Any]) -> bool:
        eid = str(ent.get("entityId") or ent.get("entity_id") or "").lower()
        name = str(ent.get("name") or ent.get("friendly_name") or "").lower()
        device_class = str(ent.get("deviceClass") or ent.get("device_class") or "").lower()
        unit = str(ent.get("unitOfMeasurement") or ent.get("unit_of_measurement") or "").strip().lower()
        if device_class == "temperature":
            return True
        if unit in {"°f", "f", "fahrenheit", "°c", "c", "celsius", "k", "kelvin"}:
            return True
        return "temp" in name or "temperature" in name or "temp" in eid or "temperature" in eid

    def fetch_entity_picker(self, domains: list[str], edit: str = "") -> None:
        ha = self._ha()
        if not ha:
            self.pending_jobs.put(("entity_picker_results", {"entities": [], "error": "Home Assistant is not configured"}))
            return
        data = self.api.post("/api/ha/entities", {"url": ha[0], "token": ha[1], "domains": domains}, timeout=12)
        if not data.get("ok", True):
            self.pending_jobs.put(("entity_picker_results", {"entities": [], "error": data.get("error", "Could not load Home Assistant entities")}))
            return
        entities = data.get("entities") or []
        cleaned = []
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            eid = ent.get("entityId") or ent.get("entity_id")
            if not eid:
                continue
            normalized = {**ent, "entityId": eid, "name": self.entity_display_name(ent)}
            if edit == "current_temp_entity" and not self.is_temperature_entry(normalized):
                continue
            cleaned.append(normalized)
        cleaned.sort(key=lambda e: (str(e.get("domain") or e.get("entityId", "")).lower(), str(e.get("name", "")).lower()))
        self.pending_jobs.put(("entity_picker_results", {"entities": cleaned, "error": ""}))

    def draw_entity_picker_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        title = str(self.modal_data.get("title") or "Select Home Assistant Entity")
        self.text((x1+x2)//2, y1+self.sy(42), title, 26, TEXT, "bold")
        domains = ", ".join(self.modal_data.get("domains") or [])
        help_text = "Showing temperature-capable Home Assistant entries." if self.modal_data.get("edit") == "current_temp_entity" else f"Showing: {domains}. Long-press cards on pages to assign them."
        self.text((x1+x2)//2, y1+self.sy(72), help_text, 12, MUTED, "bold")
        if self.modal_data.get("loading"):
            self.text((x1+x2)//2, (y1+y2)//2, "Loading entities from Home Assistant…", 22, CYAN_2, "bold")
        elif self.modal_data.get("error"):
            self.text((x1+x2)//2, (y1+y2)//2, str(self.modal_data.get("error")), 18, RED, "bold")
        else:
            entities = [e for e in (self.modal_data.get("entities") or []) if isinstance(e, dict)]
            page = int(self.modal_data.get("page") or 0)
            per_page = 8
            start = max(0, page * per_page)
            subset = entities[start:start+per_page]
            if not subset:
                self.text((x1+x2)//2, (y1+y2)//2, "No matching entities found.", 20, MUTED, "bold")
            else:
                card_w, card_h = 450, 58
                sx0 = x1 + self.sx(72)
                sy0 = y1 + self.sy(112)
                for idx, ent in enumerate(subset):
                    col = idx % 2
                    row = idx // 2
                    bx = sx0 + self.sx(col * (card_w + 36))
                    by = sy0 + self.sy(row * (card_h + 14))
                    ex1,ey1,ex2,ey2 = bx,by,bx+self.sx(card_w),by+self.sy(card_h)
                    self.buttons.append(ButtonSpec(ex1,ey1,ex2,ey2,"select",lambda e=ent:self.select_entity(e),"#132c3e","#2a6485",TEXT))
                    self.round_rect(ex1,ey1,ex2,ey2,self.sy(13),"#132c3e","#2a6485",1)
                    self.text(ex1+self.sx(16), ey1+self.sy(22), self.entity_display_name(ent)[:36], 14, TEXT, "bold", "w")
                    self.text(ex1+self.sx(16), ey1+self.sy(45), str(ent.get("entityId") or "")[:48], 9, CYAN_2, "bold", "w")
            total_pages = max(1, math.ceil(len(entities)/per_page))
            self.button(x1+self.sx(82), y2-self.sy(84), x1+self.sx(210), y2-self.sy(34), "Prev", lambda:self.entity_picker_page(-1), fill="#173246", size=13)
            self.text((x1+x2)//2, y2-self.sy(58), f"Page {page+1} of {total_pages}", 13, MUTED, "bold")
            self.button(x2-self.sx(210), y2-self.sy(84), x2-self.sx(82), y2-self.sy(34), "Next", lambda:self.entity_picker_page(1), fill="#173246", size=13)
        self.button(x2-self.sx(180), y1+self.sy(28), x2-self.sx(46), y1+self.sy(70), "Close", lambda:self.close_modal(), fill="#34202a", size=12)

    def entity_picker_page(self, delta: int) -> None:
        entities = self.modal_data.get("entities") or []
        per_page = 8
        max_page = max(0, math.ceil(len(entities)/per_page)-1)
        self.modal_data["page"] = max(0, min(max_page, int(self.modal_data.get("page") or 0) + delta))
        self.draw()

    def select_entity(self, ent: dict[str, Any]) -> None:
        edit = str(self.modal_data.get("edit") or "")
        target = self.modal_data.get("target") if isinstance(self.modal_data.get("target"), dict) else {}
        self._run_busy("Save Entity", lambda e=edit, entity=ent, tgt=target: self.save_entity_selection(e, entity, tgt))
        self.close_modal()

    def save_entity_selection(self, edit: str, ent: dict[str, Any], target: dict[str, Any] | None = None) -> str:
        target = target or {}
        cfg = json.loads(json.dumps(self.config if isinstance(self.config, dict) else {}))
        eid = str(ent.get("entityId") or ent.get("entity_id") or "")
        name = self.entity_display_name(ent)
        integrations = cfg.setdefault("integrations", {})
        if not isinstance(integrations, dict):
            cfg["integrations"] = integrations = {}
        ha = integrations.setdefault("homeAssistant", {})
        if not isinstance(ha, dict):
            integrations["homeAssistant"] = ha = {}
        simple = {
            "entityId": eid,
            "name": name,
            "domain": str(ent.get("domain") or eid.split(".")[0] if "." in eid else ""),
            "deviceClass": str(ent.get("deviceClass") or ent.get("device_class") or ""),
            "unitOfMeasurement": str(ent.get("unitOfMeasurement") or ent.get("unit_of_measurement") or ""),
            "state": ent.get("state"),
        }
        if edit == "current_temp_entity":
            ha["currentTempEntity"] = simple
            self.thermostat["currentTempSource"] = "home-assistant"
            self.thermostat["currentTempSourceName"] = name
        elif edit == "alarm_entity":
            ha["alarmEntity"] = simple
        elif edit == "weather_entity":
            ha["weatherEntity"] = simple
        elif edit == "door_entity":
            ha["doorEntity"] = simple
        elif edit == "media_player":
            ha["selectedMediaPlayerId"] = eid
        elif edit == "blind_entity":
            room_key = str(target.get("room") or "")
            idx = int(target.get("index") or 0)
            rooms = ((cfg.setdefault("blinds", {})).setdefault("rooms", {}))
            blinds = (((rooms.setdefault(room_key, {})).setdefault("blinds", [])))
            if 0 <= idx < len(blinds) and isinstance(blinds[idx], dict):
                blinds[idx].update({"haEntityId": eid, "haName": name, "name": name})
        elif edit == "light_entity":
            room_key = str(target.get("room") or "")
            idx = int(target.get("index") or 0)
            rooms = ((cfg.setdefault("lights", {})).setdefault("rooms", {}))
            lights = (((rooms.setdefault(room_key, {})).setdefault("lights", [])))
            if 0 <= idx < len(lights) and isinstance(lights[idx], dict):
                lights[idx].update({"haEntityId": eid, "haName": name, "name": name})
        elif edit == "room_entity":
            room_key = str(target.get("room") or "")
            idx = int(target.get("index") or 0)
            rooms = ((cfg.setdefault("roomControl", {})).setdefault("rooms", {}))
            room = rooms.setdefault(room_key, {})
            entries = room.get("controls") if isinstance(room.get("controls"), list) else room.setdefault("entries", [])
            if 0 <= idx < len(entries) and isinstance(entries[idx], dict):
                entries[idx].update({"haEntityId": eid, "haName": name, "name": name, "domain": simple["domain"]})
        result = self.api.post("/api/config", {"config": cfg}, timeout=8)
        if not result.get("ok", True):
            raise RuntimeError(result.get("error", "Config save failed"))
        self.config = result.get("config") or cfg
        self.fetch_slow()
        return f"Saved {name}"


    def adjust_brightness(self, delta: int) -> None:
        cfg = json.loads(json.dumps(self.config if isinstance(self.config, dict) else {}))
        display = cfg.setdefault("display", {})
        if not isinstance(display, dict):
            cfg["display"] = display = {}
        current = int(clamp(as_float(display.get("brightnessPercent"), 100), 1, 100))
        value = int(clamp(current + delta, 10, 100))
        display["brightnessPercent"] = value
        self.config = cfg
        self.draw()
        self._run_busy("Brightness", lambda v=value, c=cfg: self.save_display_brightness(c, v))

    def save_display_brightness(self, cfg: dict[str, Any], value: int) -> str:
        # Save the setting first so it survives reboot.  Try to apply it live to
        # Linux backlight devices; if permissions/device support are missing,
        # the saved setting still remains available for later service-level handling.
        result = self.api.post("/api/config", {"config": cfg}, timeout=8)
        if not result.get("ok", True):
            raise RuntimeError(result.get("error", "Config save failed"))
        self.config = result.get("config") or cfg
        try:
            for bl in Path("/sys/class/backlight").glob("*/brightness"):
                max_path = bl.parent / "max_brightness"
                max_val = int(max_path.read_text().strip() or "255")
                bl.write_text(str(max(1, int(max_val * value / 100))))
        except Exception:
            pass
        return f"Brightness {value}%"


    def open_text_input(self, item: dict[str, Any]) -> None:
        edit = str(item.get("edit") or "")
        cfg = self.config if isinstance(self.config, dict) else {}
        value = ""
        if edit == "panel_code":
            value = self._access_code()
        elif edit == "alarm_code":
            value = str((cfg.get("alarm") or {}).get("disarmCode") or "")
        elif edit == "weather_entity":
            ha = ((cfg.get("integrations") or {}).get("homeAssistant") or {})
            ent = ha.get("weatherEntity") or {}
            value = str(ent.get("entityId") if isinstance(ent, dict) else ent or "weather.home")
        elif edit == "ha_url":
            value = str(((cfg.get("integrations") or {}).get("homeAssistant") or {}).get("url") or "")
        elif edit == "ha_token":
            value = str(((cfg.get("integrations") or {}).get("homeAssistant") or {}).get("token") or "")
        elif edit == "screen_timeout":
            value = str(((cfg.get("display") or {}).get("screenTimeoutMinutes") or ""))
        self.modal = "text_input"
        self.modal_data = {
            "label": item.get("label", "Value"),
            "edit": edit,
            "value": value,
            "numeric": bool(item.get("numeric")),
            "maxlen": int(item.get("maxlen") or 64),
        }
        self.draw()

    def draw_text_input_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        label = str(self.modal_data.get("label") or "Value")
        value = str(self.modal_data.get("value") or "")
        numeric = bool(self.modal_data.get("numeric"))
        self.text((x1+x2)//2, y1+self.sy(42), label, 27, TEXT, "bold")
        self.round_rect(x1+self.sx(90), y1+self.sy(84), x2-self.sx(90), y1+self.sy(136), self.sy(14), "#111d27", "#38556a", 2)
        shown = "•" * len(value) if "code" in str(self.modal_data.get("edit")) else value
        self.text((x1+x2)//2, y1+self.sy(111), shown or "Tap keys below", 18, CYAN_2, "bold")
        if numeric:
            keys = ["1","2","3","4","5","6","7","8","9","⌫","0","Clear"]
            pad_w, pad_h = self.sx(96), self.sy(58)
            start_x = (x1+x2)//2 - self.sx(158)
            start_y = y1+self.sy(164)
            for idx,k in enumerate(keys):
                col=idx%3; row=idx//3
                bx=start_x+self.sx(col*110); by=start_y+self.sy(row*70)
                self.button(bx,by,bx+pad_w,by+pad_h,k,lambda kk=k:self.handle_text_key(kk),fill="#122d3d" if k.isdigit() else "#263141",outline="#46606e",size=18 if k.isdigit() else 12,radius=17)
        else:
            rows = ["1234567890", "qwertyuiop", "asdfghjkl._", "zxcvbnm:-"]
            start_y = y1+self.sy(160)
            for r,row in enumerate(rows):
                key_w = self.sx(46)
                row_w = key_w * len(row)
                start_x = (x1+x2)//2 - row_w//2
                for i,ch in enumerate(row):
                    bx = start_x + i*key_w
                    by = start_y + self.sy(r*58)
                    self.button(bx, by, bx+self.sx(40), by+self.sy(48), ch, lambda cc=ch:self.handle_text_key(cc), fill="#122d3d", outline="#46606e", size=13, radius=12)
            self.button(x1+self.sx(220), y1+self.sy(404), x1+self.sx(392), y1+self.sy(456), "Space", lambda:self.handle_text_key(" "), fill="#263141", size=13)
            self.button(x1+self.sx(408), y1+self.sy(404), x1+self.sx(580), y1+self.sy(456), "Backspace", lambda:self.handle_text_key("⌫"), fill="#263141", size=13)
        self.button(x1+self.sx(90), y2-self.sy(78), x1+self.sx(250), y2-self.sy(30), "Cancel", lambda: self.close_modal(), fill="#34202a", size=14)
        self.button(x2-self.sx(250), y2-self.sy(78), x2-self.sx(90), y2-self.sy(30), "Save", lambda: self.save_text_input(), fill="#164d3d", text=GREEN, size=14)

    def handle_text_key(self, key: str) -> None:
        value = str(self.modal_data.get("value") or "")
        maxlen = int(self.modal_data.get("maxlen") or 64)
        numeric = bool(self.modal_data.get("numeric"))
        if key in {"Clear", "C"}:
            value = ""
        elif key in {"⌫", "Backspace"}:
            value = value[:-1]
        else:
            if numeric and not key.isdigit():
                return
            value = (value + key)[:maxlen]
        self.modal_data["value"] = value
        self.draw()

    def save_text_input(self) -> None:
        edit = str(self.modal_data.get("edit") or "")
        value = str(self.modal_data.get("value") or "").strip()
        if edit in {"panel_code", "alarm_code"}:
            value = "".join(ch for ch in value if ch.isdigit())
        if edit == "panel_code" and len(value) != 4:
            return self.show_toast("Panel code must be 4 digits")
        if edit == "weather_entity" and value and not value.startswith("weather."):
            return self.show_toast("Weather entity must be weather.*")
        if edit == "screen_timeout" and value and not value.isdigit():
            return self.show_toast("Screen timeout must be minutes")
        self._run_busy("Save Setting", lambda e=edit,v=value: self.save_config_edit(e, v))
        self.close_modal()

    def save_config_edit(self, edit: str, value: str) -> str:
        cfg = json.loads(json.dumps(self.config if isinstance(self.config, dict) else {}))
        if edit == "panel_code":
            cfg["userAccessCode"] = value
            cfg["settingsAccessCode"] = value
            security = cfg.setdefault("security", {})
            if isinstance(security, dict):
                security["userAccessCode"] = value
                security["settingsAccessCode"] = value
        elif edit == "alarm_code":
            alarm = cfg.setdefault("alarm", {})
            if isinstance(alarm, dict):
                alarm["disarmCode"] = value
        elif edit in {"weather_entity", "ha_url", "ha_token"}:
            integrations = cfg.setdefault("integrations", {})
            if not isinstance(integrations, dict):
                cfg["integrations"] = integrations = {}
            ha = integrations.setdefault("homeAssistant", {})
            if not isinstance(ha, dict):
                integrations["homeAssistant"] = ha = {}
            if edit == "weather_entity":
                ha["weatherEntity"] = {"entityId": value or "weather.home", "name": value or "Home"}
            elif edit == "ha_url":
                ha["url"] = value.rstrip("/")
            elif edit == "ha_token":
                ha["token"] = value
        elif edit == "screen_timeout":
            display = cfg.setdefault("display", {})
            if not isinstance(display, dict):
                cfg["display"] = display = {}
            display["screenTimeoutMinutes"] = int(value) if value else 0
        result = self.api.post("/api/config", {"config": cfg}, timeout=8)
        if not result.get("ok", True):
            raise RuntimeError(result.get("error", "Config save failed"))
        self.config = result.get("config") or cfg
        self.fetch_slow()
        return "Saved"

    def get_limit_value(self, mode: str, bound: str) -> float:
        limits_all = self.thermostat.get("limits", {}) if isinstance(self.thermostat.get("limits"), dict) else {}
        limits = limits_all.get(mode) if isinstance(limits_all.get(mode), dict) else {}
        default = 65 if mode == "cool" and bound == "min" else 80 if mode == "cool" and bound == "max" else 60 if bound == "min" else 78
        return as_float(limits.get(bound), default)

    def adjust_limit(self, mode: str, bound: str, delta: float) -> None:
        limits_all = self.thermostat.setdefault("limits", {})
        if not isinstance(limits_all, dict):
            limits_all = {}
            self.thermostat["limits"] = limits_all
        current = limits_all.setdefault(mode, {})
        if not isinstance(current, dict):
            current = {}
            limits_all[mode] = current
        low = as_float(current.get("min"), 65 if mode == "cool" else 60)
        high = as_float(current.get("max"), 80 if mode == "cool" else 78)
        if bound == "min":
            low = clamp(low + delta, 45, high - 2)
        else:
            high = clamp(high + delta, low + 2, 95)
        current["min"] = round(low)
        current["max"] = round(high)
        minimum, maximum = self.target_limits()
        target = as_float(self.thermostat.get("targetTemp", self.thermostat.get("target_temperature", 70)), 70)
        self.thermostat["targetTemp"] = round(clamp(target, minimum, maximum))
        payload = {"limits": {mode: {"min": current["min"], "max": current["max"]}}, "targetTemp": self.thermostat["targetTemp"]}
        self.draw()
        self._run_busy("Comfort Limits", lambda p=payload: self.control(p))


    def adjust_setting(self, key: str, delta: float) -> None:
        value = as_float(self.thermostat.get(key), 0) + delta
        self.thermostat[key] = value
        self._run_busy("Setting", lambda k=key,v=value: self.control({k: v}))

    def toggle_setting(self, key: str) -> None:
        value = not as_bool(self.thermostat.get(key))
        self.thermostat[key] = value
        self._run_busy("Setting", lambda k=key,v=value: self.control({k: v}))

    def release_relays(self) -> None:
        self._run_busy("Release", lambda: self._post_message("/api/hardware/release", {}))

    def draw_alarm_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        self.text((x1+x2)//2, y1+self.sy(42), "Alarmo", 30, TEXT, "bold")
        state = title_case((self.alarm_state or {}).get("state", "Disarmed"))
        self.canvas.create_oval((x1+x2)//2-self.sx(42), y1+self.sy(78), (x1+x2)//2+self.sx(42), y1+self.sy(162), fill="#143b33", outline="#2b7f66", width=2)
        pts=[(x1+x2)//2,self.sy(y1+94),(x1+x2)//2+self.sx(28),self.sy(y1+108),(x1+x2)//2+self.sx(22),self.sy(y1+140),(x1+x2)//2,self.sy(y1+156),(x1+x2)//2-self.sx(22),self.sy(y1+140),(x1+x2)//2-self.sx(28),self.sy(y1+108)]
        self.canvas.create_polygon(pts, fill="#2a8d61", outline="#9ee9d1", width=2)
        self.pill((x1+x2)//2-self.sx(80), y1+self.sy(174), (x1+x2)//2+self.sx(80), y1+self.sy(204), state.upper(), fill="#163f33", outline="#1e6a52", color="#dbfff0", size=11)
        self.text((x1+x2)//2, y1+self.sy(238), "Choose an alarm action", 14, MUTED, "bold")
        self.button(x1+self.sx(58), y1+self.sy(280), x1+self.sx(230), y1+self.sy(342), "Arm Home", lambda: self.alarm_action("arm_home"), fill="#173246", size=14)
        self.button(x1+self.sx(244), y1+self.sy(280), x1+self.sx(416), y1+self.sy(342), "Arm Away", lambda: self.alarm_action("arm_away"), fill="#173246", size=14)
        self.button(x1+self.sx(430), y1+self.sy(280), x1+self.sx(602), y1+self.sy(342), "Disarm", lambda: self.alarm_action("disarm"), fill="#40202a", size=14)
        self.button(x2-self.sx(170), y2-self.sy(72), x2-self.sx(36), y2-self.sy(28), "Close", lambda: self.close_modal(), fill="#173246", size=14)


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
        self.pressed_button = None
        self.long_press_fired = False
        if self.long_press_after_id:
            try:
                self.root.after_cancel(self.long_press_after_id)
            except Exception:
                pass
            self.long_press_after_id = None
        for b in reversed(self.buttons):
            if b.x1 <= x <= b.x2 and b.y1 <= y <= b.y2:
                if b.long_action is not None:
                    self.pressed_button = b
                    self.press_started_at = time.time()
                    self.long_press_after_id = self.root.after(650, self._fire_long_press)
                    return
                b.action()
                return
        if not self.modal and self.page == "thermostat" and self.begin_dial_adjust(x, y):
            return
        # allow tap outside modal to close only if not busy
        if self.modal and not self.busy:
            pass

    def _fire_long_press(self) -> None:
        self.long_press_after_id = None
        b = self.pressed_button
        if b and b.long_action:
            self.long_press_fired = True
            b.long_action()

    def on_drag(self, event: tk.Event) -> None:
        if self.pressed_button is not None:
            # Movement cancels the long-press gesture so slider/dial gestures stay clean.
            if abs(int(event.x) - (self.pressed_button.x1+self.pressed_button.x2)//2) > self.sx(70) or abs(int(event.y) - (self.pressed_button.y1+self.pressed_button.y2)//2) > self.sy(70):
                if self.long_press_after_id:
                    try:
                        self.root.after_cancel(self.long_press_after_id)
                    except Exception:
                        pass
                    self.long_press_after_id = None
        if self.drag_target in {"dial_arc", "dial_linear"}:
            self.apply_target_from_point(int(event.x), int(event.y), final=False)

    def on_release(self, event: tk.Event) -> None:
        if self.long_press_after_id:
            try:
                self.root.after_cancel(self.long_press_after_id)
            except Exception:
                pass
            self.long_press_after_id = None
        if self.pressed_button is not None:
            b = self.pressed_button
            self.pressed_button = None
            if not self.long_press_fired:
                b.action()
            self.long_press_fired = False
            return
        if self.drag_target in {"dial_arc", "dial_linear"}:
            self.apply_target_from_point(int(event.x), int(event.y), final=True)
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
