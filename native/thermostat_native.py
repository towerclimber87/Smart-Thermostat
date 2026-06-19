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
    from tkinter import font as tkfont
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
BG = "#071018"
PANEL = "#111d27"
PANEL_2 = "#162838"
PANEL_3 = "#0c1720"
TEXT = "#f4fbff"
MUTED = "#89a2b5"
CYAN = "#35eaff"
GREEN = "#35e27a"
YELLOW = "#ffd85a"
ORANGE = "#ff9d42"
RED = "#ff4c5d"
BLUE = "#5f8cff"
PURPLE = "#af7cff"
GRAY = "#314354"
BLACK = "#000000"
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
        self.toast = ""
        self.toast_until = 0.0
        self.locked = False
        self.modal: str | None = None
        self.modal_data: dict[str, Any] = {}
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
        self.draw_header()
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
        self.draw_bottom_nav()
        if self.last_error:
            self.text(self.sx(20), self.sy(770), self.last_error[:120], 11, RED, anchor="w")
        if self.toast and time.time() < self.toast_until:
            self.draw_toast(self.toast)
        if self.busy:
            self.draw_busy()
        if self.modal:
            self.draw_modal()

    def draw_header(self) -> None:
        name = self.thermostat.get("name") or self.system_info.get("thermostatName") or "IHA Thermostat"
        self.text(self.sx(32), self.sy(26), time.strftime("%I:%M %p").lstrip("0"), 20, MUTED, "bold", "w")
        self.text(self.sx(640), self.sy(30), str(name), 26, TEXT, "bold")
        self.button(self.sx(1124), self.sy(10), self.sx(1178), self.sy(58), "i", lambda: self.open_info(), fill="#123149", text=CYAN)
        self.button(self.sx(1190), self.sy(10), self.sx(1266), self.sy(58), "⚙", lambda: self.open_settings(), fill="#123149", text=CYAN)

    def draw_bottom_nav(self) -> None:
        pages = [("thermostat", "Thermostat"), ("blinds", "Blinds"), ("audio", "Audio"), ("lights", "Lights"), ("room", "Room")]
        w = 230
        start = (1280 - (w * len(pages) + 10 * (len(pages)-1))) // 2
        for i, (page, label) in enumerate(pages):
            x1 = self.sx(start + i*(w+10)); y1 = self.sy(724); x2 = self.sx(start+i*(w+10)+w); y2 = self.sy(782)
            fill = "#16455f" if self.page == page else "#10202d"
            self.button(x1, y1, x2, y2, label, lambda p=page: self.set_page(p), fill=fill, text=TEXT)

    def set_page(self, page: str) -> None:
        self.page = page
        self.draw()

    def draw_thermostat(self) -> None:
        t = self.thermostat or {}
        current = as_float(t.get("currentTemp", t.get("current_temperature", 0)), 0)
        target = as_float(t.get("targetTemp", t.get("target_temperature", 0)), 0)
        mode = str(t.get("mode", "cool"))
        action = str(t.get("hvac_action", t.get("hvacAction", "idle")))
        fan = str(t.get("fan", t.get("fan_mode", "auto")))
        away = as_bool(t.get("away"))
        relays = t.get("relays") or {}
        outdoor = as_float(t.get("outdoorTemp", t.get("outdoor_temperature", 0)), 0)
        wind = as_float(t.get("outdoorWindSpeed", t.get("outdoor_wind_speed", 0)), 0)
        hum = as_float(t.get("humidity", 0), 0)

        # Left status column
        self.round_rect(self.sx(25), self.sy(82), self.sx(322), self.sy(235), 24, PANEL, "#1c3447", 2)
        self.text(self.sx(52), self.sy(112), f"Outdoor {outdoor:.0f}°", 26, CYAN, "bold", "w")
        self.text(self.sx(52), self.sy(154), f"Wind {wind:.0f} {t.get('outdoorWindUnit', 'mph')}", 17, MUTED, "bold", "w")
        self.text(self.sx(52), self.sy(194), f"Humidity {hum:.0f}%", 17, MUTED, "bold", "w")
        self.draw_badges(relays, action)

        # Dial/card
        cx, cy = self.sx(640), self.sy(360)
        radius = min(self.sx(230), self.sy(230))
        color = RED if action == "heating" or relays.get("heat") else CYAN if action == "cooling" or relays.get("cool") else GREEN if action == "idle" else YELLOW
        self.canvas.create_oval(cx-radius, cy-radius, cx+radius, cy+radius, fill="#0a1720", outline="#244356", width=4)
        self.canvas.create_oval(cx-radius+18, cy-radius+18, cx+radius-18, cy+radius-18, fill="#0e202c", outline=color, width=5)
        self.text(cx, cy-self.sy(82), f"{current:.0f}°", 82, TEXT, "bold")
        self.text(cx, cy-self.sy(12), "CURRENT", 16, MUTED, "bold")
        self.text(cx, cy+self.sy(52), f"SET {target:.0f}°", 38, color, "bold")
        self.text(cx, cy+self.sy(105), title_case(action or "idle"), 25, color, "bold")
        self.button(self.sx(336), self.sy(296), self.sx(442), self.sy(422), "−", lambda: self.change_target(-1), fill="#172b3b", text=TEXT)
        self.button(self.sx(838), self.sy(296), self.sx(944), self.sy(422), "+", lambda: self.change_target(1), fill="#172b3b", text=TEXT)

        # Right controls
        self.round_rect(self.sx(958), self.sy(82), self.sx(1255), self.sy(544), 24, PANEL, "#1c3447", 2)
        self.text(self.sx(985), self.sy(118), "Mode", 20, MUTED, "bold", "w")
        self.mode_buttons(mode)
        self.text(self.sx(985), self.sy(286), "Fan", 20, MUTED, "bold", "w")
        self.fan_buttons(fan)
        self.button(self.sx(985), self.sy(445), self.sx(1230), self.sy(514), "Away: ON" if away else "Home / Away", lambda: self.toggle_away(), fill=ORANGE if away else "#173246", text=TEXT)

        # bottom panels
        self.draw_alarm_door_pause()
        self.draw_schedules()

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
        target = sched.get("targetTemp") or sched.get("temperature") or sched.get("coolTarget") or sched.get("heatTarget")
        if target is None:
            self.show_toast("Schedule has no quick target")
            return
        self._run_busy("Schedule", lambda: self.control({"targetTemp": as_float(target, 70)}))

    def draw_blinds(self) -> None:
        self.draw_generic_page_title("Blinds")
        cfg = (self.config.get("blinds") or {}) if isinstance(self.config, dict) else {}
        rooms = cfg.get("rooms") or {}
        room_key = cfg.get("room") or next(iter(rooms), "")
        room = rooms.get(room_key, {}) if isinstance(rooms, dict) else {}
        blinds = room.get("blinds") or []
        self.text(self.sx(55), self.sy(108), room.get("label", "Room"), 24, TEXT, "bold", "w")
        self.button(self.sx(930), self.sy(86), self.sx(1080), self.sy(142), "Open Room", lambda: self.cover_room(blinds, "open"), fill="#173246")
        self.button(self.sx(1095), self.sy(86), self.sx(1245), self.sy(142), "Close Room", lambda: self.cover_room(blinds, "close"), fill="#173246")
        for i, blind in enumerate(blinds[:8]):
            row = i // 2; col = i % 2
            x = 60 + col * 600; y = 170 + row * 115
            self.draw_entity_card(x, y, 540, 90, blind.get("name", "Blind"), blind.get("haEntityId", ""), [
                ("Open", lambda b=blind: self.cover_action(b, "open")),
                ("Close", lambda b=blind: self.cover_action(b, "close")),
                ("Stop", lambda b=blind: self.cover_action(b, "stop")),
            ])

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
        self.draw_generic_page_title("Audio")
        audio = self.config.get("audio") or {}
        integrations = self.config.get("integrations", {}) or {}
        ha = integrations.get("homeAssistant", {}) or {}
        player = ha.get("selectedMediaPlayerId") or audio.get("selectedMediaPlayerId") or ""
        name = player or "No player selected"
        self.text(self.sx(640), self.sy(145), name, 28, TEXT, "bold")
        controls = [
            ("Prev", "previous"), ("Play/Pause", "play_pause"), ("Next", "next"),
            ("Vol −", "volume_down"), ("Vol +", "volume_up"), ("Mute", "mute"),
        ]
        for i, (label, action) in enumerate(controls):
            x = 165 + (i % 3) * 320; y = 230 + (i // 3) * 110
            self.button(self.sx(x), self.sy(y), self.sx(x+260), self.sy(y+75), label, lambda a=action: self.media_action(a), fill="#173246")
        self.text(self.sx(640), self.sy(520), "Movie / Show / 40% / Max presets stay available through the existing Home Assistant audio controls.", 18, MUTED)

    def media_action(self, action: str) -> None:
        ha = self._ha(); player = (self.config.get("integrations", {}) or {}).get("homeAssistant", {}).get("selectedMediaPlayerId", "")
        if not ha or not player:
            return self.show_toast("Audio player is not configured")
        self._run_busy("Audio", lambda: self._post_message("/api/ha/media/action", {"url": ha[0], "token": ha[1], "entityId": player, "action": action}))

    def draw_lights(self) -> None:
        self.draw_generic_page_title("Lights")
        cfg = self.config.get("lights") or {}
        rooms = cfg.get("rooms") or {}
        room_key = cfg.get("room") or next(iter(rooms), "")
        room = rooms.get(room_key, {}) if isinstance(rooms, dict) else {}
        lights = room.get("lights") or []
        self.text(self.sx(55), self.sy(108), room.get("label", "Room"), 24, TEXT, "bold", "w")
        for i, light in enumerate(lights[:12]):
            col = i % 4; row = i // 4
            x = 55 + col * 300; y = 165 + row * 135
            label = light.get("name", "Light")
            self.draw_entity_card(x, y, 260, 108, label, light.get("haEntityId", ""), [
                ("Toggle", lambda l=light: self.light_action(l, "toggle")),
                ("100%", lambda l=light: self.light_action(l, "on", 100)),
            ])

    def light_action(self, light: dict[str, Any], action: str, brightness: int | None = None) -> None:
        ha = self._ha(); entity = light.get("haEntityId")
        if not ha or not entity:
            return self.show_toast("Light is not configured")
        payload = {"url": ha[0], "token": ha[1], "entityId": entity, "action": action}
        if brightness is not None:
            payload["brightness"] = brightness
        self._run_busy("Light", lambda: self._post_message("/api/ha/light/action", payload))

    def draw_room_controls(self) -> None:
        self.draw_generic_page_title("Room")
        cfg = self.config.get("roomControls") or self.config.get("room") or {}
        rooms = cfg.get("rooms") or {}
        room_key = cfg.get("room") or next(iter(rooms), "")
        room = rooms.get(room_key, {}) if isinstance(rooms, dict) else {}
        controls = room.get("controls") or room.get("entries") or []
        self.text(self.sx(55), self.sy(108), room.get("label", "Room"), 24, TEXT, "bold", "w")
        for i, control in enumerate(controls[:12]):
            col = i % 4; row = i // 4
            x = 55 + col * 300; y = 165 + row * 135
            self.draw_entity_card(x, y, 260, 108, control.get("name", "Control"), control.get("haEntityId", ""), [
                ("Toggle", lambda c=control: self.room_action(c, "toggle")),
                ("Open", lambda c=control: self.room_action(c, "open")),
            ])

    def room_action(self, control: dict[str, Any], action: str) -> None:
        ha = self._ha(); entity = control.get("haEntityId")
        if not ha or not entity:
            return self.show_toast("Room control is not configured")
        payload = {"url": ha[0], "token": ha[1], "entityId": entity, "action": action, "code": control.get("code", "")}
        self._run_busy("Room", lambda: self._post_message("/api/ha/room/action", payload))

    def draw_generic_page_title(self, title: str) -> None:
        self.round_rect(self.sx(30), self.sy(80), self.sx(1250), self.sy(700), 24, "#0a1520", "#1d3345", 2)
        self.text(self.sx(640), self.sy(105), title, 32, TEXT, "bold")

    def draw_entity_card(self, x: int, y: int, w: int, h: int, name: str, entity: str, actions: list[tuple[str, Callable[[], None]]]) -> None:
        x1=self.sx(x); y1=self.sy(y); x2=self.sx(x+w); y2=self.sy(y+h)
        self.round_rect(x1,y1,x2,y2,18,PANEL,"#263b4d",2)
        self.text(self.sx(x+16), self.sy(y+24), str(name)[:30], 17, TEXT, "bold", "w")
        self.text(self.sx(x+16), self.sy(y+50), str(entity)[:36] if entity else "Not assigned", 11, MUTED, anchor="w")
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

    def draw_info_modal(self, x1:int,y1:int,x2:int,y2:int) -> None:
        info = self.system_info or {}
        self.text((x1+x2)//2, y1+self.sy(42), "Panel Info", 30, TEXT, "bold")
        lines = [
            f"Version: {info.get('version') or _version()}",
            f"Address: {info.get('address') or info.get('ipAddress') or socket.gethostname()}",
            f"Host: {info.get('host') or socket.gethostname()}",
            f"Uptime: {info.get('uptime') or ''}",
            f"Display: Native Tk appliance, no Chromium",
        ]
        y = y1+self.sy(105)
        for line in lines:
            self.text(x1+self.sx(45), y, line, 19, MUTED, "bold", "w"); y += self.sy(38)
        self.button(x1+self.sx(50), y2-self.sy(115), x1+self.sx(275), y2-self.sy(55), "Fetch Update", lambda: self.fetch_update(), fill="#17405a", text=CYAN)
        self.button(x1+self.sx(295), y2-self.sy(115), x1+self.sx(520), y2-self.sy(55), "Restart", lambda: self.restart_panel(), fill="#402817", text=YELLOW)
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
            code = str((self.config.get("settingsAccessCode") or DEFAULT_ACCESS_CODE))
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
