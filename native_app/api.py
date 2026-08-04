from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class ApiError(RuntimeError):
    pass


@dataclass
class ApiClient:
    base_url: str = os.environ.get("SMART_THERMOSTAT_API", "http://127.0.0.1:8080")
    timeout: float = float(os.environ.get("SMART_THERMOSTAT_API_TIMEOUT", "1.5"))
    control_timeout: float = float(os.environ.get("SMART_THERMOSTAT_CONTROL_TIMEOUT", "4.0"))

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url.rstrip("/") + path

    def get(self, path: str) -> dict[str, Any]:
        req = request.Request(self._url(path), headers={"Accept": "application/json"}, method="GET")
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except error.URLError as exc:
            raise ApiError(str(exc)) from exc
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ApiError(f"Bad JSON from {path}: {exc}") from exc

    def post(self, path: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        body = json.dumps(payload or {}).encode("utf-8")
        req = request.Request(
            self._url(path),
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout if timeout is None else timeout) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8")
                data = json.loads(raw or "{}")
                detail = data.get("error") or data.get("message")
                if detail:
                    raise ApiError(str(detail)) from exc
                raise ApiError(raw or str(exc)) from exc
            except json.JSONDecodeError:
                raise ApiError(raw or str(exc)) from exc
        except error.URLError as exc:
            raise ApiError(str(exc)) from exc
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ApiError(f"Bad JSON from {path}: {exc}") from exc

    def wait_until_ready(self, seconds: float = 20.0) -> bool:
        end = time.time() + seconds
        while time.time() < end:
            try:
                if self.get("/api/health").get("ok"):
                    return True
            except Exception:
                time.sleep(0.5)
        return False

    def get_config_record(self) -> dict[str, Any]:
        return self.get("/api/config")

    def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/config", {"config": config})

    def thermostat_status(self) -> dict[str, Any]:
        return self.get("/api/thermostat/status")

    def thermal_status(self) -> dict[str, Any]:
        return self.get("/api/system/thermal")

    def thermostat_update(self, changes: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/thermostat/control", {"thermostat": changes}, timeout=self.control_timeout)

    @staticmethod
    def ha_payload(config: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
        ha = (((config or {}).get("integrations") or {}).get("homeAssistant") or {})
        payload = {
            "url": ha.get("url") or "",
            "token": ha.get("token") or "",
        }
        if extra:
            payload.update(extra)
        return payload
