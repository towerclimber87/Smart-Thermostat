#!/usr/bin/env python3
"""Smart Thermostat local development / Raspberry Pi server.

Serves the web UI from ./public and provides a same-origin Home Assistant proxy
so the browser does not get blocked by CORS when loading entities.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request, error
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.close_connection = True
    handler.wfile.write(body)


def _safe_join_public(path: str) -> Path | None:
    if path == "/":
        path = "/index.html"
    candidate = (PUBLIC / path.lstrip("/")).resolve()
    try:
        candidate.relative_to(PUBLIC.resolve())
    except ValueError:
        return None
    return candidate


def _normalize_ha_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Home Assistant URL must include http:// or https:// and a host")
    return value


def _fetch_ha_covers(ha_url: str, token: str) -> list[dict]:
    ha_url = _normalize_ha_url(ha_url)
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing Home Assistant token")

    req = request.Request(
        f"{ha_url}/api/states",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "SmartThermostatPanel/0.1",
            "Connection": "close",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc

    states = json.loads(raw.decode("utf-8"))
    covers = []
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        if not entity_id.startswith("cover."):
            continue
        attrs = item.get("attributes") or {}
        covers.append({
            "entityId": entity_id,
            "name": attrs.get("friendly_name") or entity_id,
            "state": item.get("state") or "unknown",
            "currentPosition": attrs.get("current_position"),
            "supportedFeatures": attrs.get("supported_features"),
        })
    covers.sort(key=lambda item: item["name"].lower())
    return covers


def _fetch_ha_cover_states_for_entities(ha_url: str, token: str, entity_ids: list[str]) -> list[dict]:
    """Fetch current state for selected cover entities using one HA states call.

    This is intentionally lightweight for the Pi UI: the browser polls this endpoint
    only for linked blinds, and this function makes a single Home Assistant REST
    request per poll instead of one request per blind.
    """
    wanted = []
    seen = set()
    for raw in entity_ids or []:
        entity_id = str(raw or "").strip()
        if not entity_id.startswith("cover.") or entity_id in seen:
            continue
        seen.add(entity_id)
        wanted.append(entity_id)

    if not wanted:
        return []

    ha_url = _normalize_ha_url(ha_url)
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing Home Assistant token")

    req = request.Request(
        f"{ha_url}/api/states",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "SmartThermostatPanel/0.1",
            "Connection": "close",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc

    states = json.loads(raw.decode("utf-8"))
    wanted_set = set(wanted)
    covers = []
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        if entity_id not in wanted_set:
            continue
        attrs = item.get("attributes") or {}
        covers.append({
            "entityId": entity_id,
            "name": attrs.get("friendly_name") or entity_id,
            "state": item.get("state") or "unknown",
            "currentPosition": attrs.get("current_position"),
            "supportedFeatures": attrs.get("supported_features"),
        })

    # Preserve the caller's entity order so UI updates stay predictable.
    by_id = {item["entityId"]: item for item in covers}
    return [by_id[entity_id] for entity_id in wanted if entity_id in by_id]


def _ha_json_request(ha_url: str, token: str, method: str, path: str, payload: dict | None = None) -> object:
    ha_url = _normalize_ha_url(ha_url)
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing Home Assistant token")

    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "SmartThermostatPanel/0.1",
        "Connection": "close",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(f"{ha_url}{path}", data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {"raw": raw.decode("utf-8", errors="replace")}


def _normalize_media_player_item(ha_url: str, item: dict) -> dict:
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    picture = attrs.get("entity_picture") or ""
    if picture and picture.startswith("/"):
        picture = f"{_normalize_ha_url(ha_url)}{picture}"

    source_list = attrs.get("source_list") or []
    if isinstance(source_list, str):
        source_list = [source_list] if source_list.strip() else []
    elif not isinstance(source_list, list):
        source_list = []

    return {
        "entityId": entity_id,
        "name": attrs.get("friendly_name") or entity_id,
        "state": item.get("state") or "unknown",
        "volumeLevel": attrs.get("volume_level"),
        "isVolumeMuted": attrs.get("is_volume_muted"),
        "mediaContentId": attrs.get("media_content_id") or "",
        "mediaContentType": attrs.get("media_content_type") or "",
        "mediaTitle": attrs.get("media_title") or "",
        "mediaArtist": attrs.get("media_artist") or "",
        "mediaAlbum": attrs.get("media_album_name") or attrs.get("media_album") or "",
        "mediaPosition": attrs.get("media_position"),
        "mediaDuration": attrs.get("media_duration"),
        "source": attrs.get("source") or "",
        "sourceList": source_list,
        "pictureUrl": picture,
        "supportedFeatures": attrs.get("supported_features"),
    }


def _fetch_ha_media_players(ha_url: str, token: str) -> list[dict]:
    ha_url = _normalize_ha_url(ha_url)
    token = (token or "").strip()
    if not token:
        raise ValueError("Missing Home Assistant token")

    req = request.Request(
        f"{ha_url}/api/states",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "SmartThermostatPanel/0.1",
            "Connection": "close",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=12) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Home Assistant returned HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach Home Assistant: {exc.reason}") from exc

    states = json.loads(raw.decode("utf-8"))
    players = []
    for item in states:
        entity_id = str(item.get("entity_id", ""))
        if not entity_id.startswith("media_player."):
            continue
        players.append(_normalize_media_player_item(ha_url, item))
    players.sort(key=lambda item: item["name"].lower())
    return players


def _fetch_ha_media_states_for_entities(ha_url: str, token: str, entity_ids: list[str]) -> list[dict]:
    """Fetch selected media player states without pulling every HA entity.

    The panel polls this endpoint while the Audio page is active, so keep it
    lightweight. The old version used /api/states for every poll; this asks HA
    only for the selected media_player entity.
    """
    wanted = []
    seen = set()
    for raw in entity_ids or []:
        entity_id = str(raw or "").strip()
        if not entity_id.startswith("media_player.") or entity_id in seen:
            continue
        seen.add(entity_id)
        wanted.append(entity_id)
    return [_fetch_ha_media_state(ha_url, token, entity_id) for entity_id in wanted]


def _fetch_ha_media_state(ha_url: str, token: str, entity_id: str) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("media_player."):
        raise ValueError("Entity must be a media_player.* entity")
    item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
    return _normalize_media_player_item(ha_url, item)




def _normalize_number_control(item: dict, kind: str | None = None) -> dict:
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    raw_state = item.get("state")
    try:
        value = float(raw_state)
    except (TypeError, ValueError):
        value = None

    def num_attr(name: str, fallback: float | None = None) -> float | None:
        raw = attrs.get(name, fallback)
        try:
            return float(raw) if raw is not None and raw != "" else fallback
        except (TypeError, ValueError):
            return fallback

    return {
        "kind": kind or "",
        "entityId": entity_id,
        "name": attrs.get("friendly_name") or entity_id,
        "state": raw_state,
        "value": value,
        "min": num_attr("min", -10),
        "max": num_attr("max", 10),
        "step": num_attr("step", 1),
        "unit": attrs.get("unit_of_measurement") or "",
        "mode": attrs.get("mode") or "",
    }


def _compact_key(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _score_audio_control(item: dict, kind: str, player_entity_id: str, player_name: str) -> int:
    attrs = item.get("attributes") or {}
    entity_id = str(item.get("entity_id", ""))
    friendly = str(attrs.get("friendly_name") or "")
    haystack = f"{entity_id} {friendly}".lower()
    compact = _compact_key(haystack)

    kind_terms = {
        "bass": ["bass"],
        "treble": ["treble"],
        "gain": ["gain", "subwoofergain", "subgain"],
    }
    if not any(term in compact for term in kind_terms.get(kind, [kind])):
        return -1

    player_slug = player_entity_id.split(".", 1)[-1]
    player_compact = _compact_key(player_slug)
    name_words = [word for word in str(player_name or "").lower().replace("-", " ").split() if len(word) > 2]

    score = 5
    if player_compact and player_compact in compact:
        score += 20
    for word in name_words:
        if _compact_key(word) in compact:
            score += 4
    if "sonos" in compact:
        score += 1
    if kind == "gain" and "subwoofer" in compact:
        score += 10
    return score


def _fetch_ha_audio_controls(ha_url: str, token: str, media_player_id: str, media_player_name: str = "") -> dict:
    """Find HA number.* entities that represent tone controls for a media player.

    Sonos exposes bass/treble/subwoofer-gain as number entities in Home Assistant,
    not as generic media_player services. This lookup is done when selecting or
    loading an audio device, not on every UI tick.
    """
    media_player_id = (media_player_id or "").strip()
    if not media_player_id.startswith("media_player."):
        raise ValueError("Entity must be a media_player.* entity")

    states = _ha_json_request(ha_url, token, "GET", "/api/states")
    if not isinstance(states, list):
        return {"gain": None, "bass": None, "treble": None}

    controls: dict[str, dict | None] = {"gain": None, "bass": None, "treble": None}
    for kind in list(controls.keys()):
        best = None
        best_score = -1
        for item in states:
            entity_id = str(item.get("entity_id", ""))
            if not entity_id.startswith("number."):
                continue
            score = _score_audio_control(item, kind, media_player_id, media_player_name)
            if score > best_score:
                best = item
                best_score = score
        if best is not None and best_score >= 8:
            controls[kind] = _normalize_number_control(best, kind)
    return controls


def _fetch_ha_audio_control_states(ha_url: str, token: str, controls: dict) -> dict:
    refreshed: dict[str, dict | None] = {}
    for kind in ("gain", "bass", "treble"):
        entity_id = str((controls.get(kind) or {}).get("entityId") or "").strip()
        if not entity_id.startswith("number."):
            refreshed[kind] = None
            continue
        item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
        refreshed[kind] = _normalize_number_control(item, kind)
    return refreshed


def _call_number_service(ha_url: str, token: str, entity_id: str, value: int | float) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("number."):
        raise ValueError("Entity must be a number.* entity")
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Missing or invalid numeric value") from exc

    _ha_json_request(ha_url, token, "POST", "/api/services/number/set_value", {
        "entity_id": entity_id,
        "value": numeric_value,
    })
    try:
        item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
        return _normalize_number_control(item)
    except Exception:
        return {"entityId": entity_id, "name": entity_id, "value": numeric_value, "state": str(numeric_value)}

def _call_media_service(ha_url: str, token: str, entity_id: str, action: str, value: int | float | None = None) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("media_player."):
        raise ValueError("Entity must be a media_player.* entity")

    service_by_action = {
        "play_pause": "media_play_pause",
        "play": "media_play",
        "pause": "media_pause",
        "previous": "media_previous_track",
        "next": "media_next_track",
        "volume": "volume_set",
        "volume_up": "volume_up",
        "volume_down": "volume_down",
        "source": "select_source",
    }
    service = service_by_action.get(action)
    if not service:
        raise ValueError("Unsupported media action")

    payload = {"entity_id": entity_id}
    if action == "volume":
        if value is None:
            raise ValueError("Missing volume value")
        payload["volume_level"] = max(0, min(100, float(value))) / 100

    if action == "source":
        source = str(value or "").strip()
        if not source:
            raise ValueError("Missing source value")
        payload["source"] = source

    _ha_json_request(ha_url, token, "POST", f"/api/services/media_player/{service}", payload)
    try:
        return _fetch_ha_media_state(ha_url, token, entity_id)
    except Exception:
        return {"entityId": entity_id, "name": entity_id, "state": action}


def _fetch_ha_state(ha_url: str, token: str, entity_id: str) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("cover."):
        raise ValueError("Entity must be a cover.* entity")
    item = _ha_json_request(ha_url, token, "GET", f"/api/states/{entity_id}")
    attrs = item.get("attributes") or {}
    return {
        "entityId": item.get("entity_id") or entity_id,
        "name": attrs.get("friendly_name") or item.get("entity_id") or entity_id,
        "state": item.get("state") or "unknown",
        "currentPosition": attrs.get("current_position"),
        "supportedFeatures": attrs.get("supported_features"),
    }


def _call_cover_service(ha_url: str, token: str, entity_id: str, action: str, position: int | None = None) -> dict:
    entity_id = (entity_id or "").strip()
    if not entity_id.startswith("cover."):
        raise ValueError("Entity must be a cover.* entity")

    service_by_action = {
        "open": "open_cover",
        "close": "close_cover",
        "stop": "stop_cover",
        "position": "set_cover_position",
    }
    service = service_by_action.get(action)
    if not service:
        raise ValueError("Unsupported cover action")

    payload = {"entity_id": entity_id}
    if action == "position":
        if position is None:
            raise ValueError("Missing position")
        payload["position"] = max(0, min(100, int(position)))

    _ha_json_request(ha_url, token, "POST", f"/api/services/cover/{service}", payload)
    try:
        state = _fetch_ha_state(ha_url, token, entity_id)
    except Exception:
        # Some cover integrations update state slowly. Return an optimistic value so the UI changes immediately.
        optimistic = 100 if action == "open" else 0 if action == "close" else position
        state = {"entityId": entity_id, "name": entity_id, "state": action, "currentPosition": optimistic}
    return state


class SmartThermostatHandler(BaseHTTPRequestHandler):
    server_version = "SmartThermostatServer/0.1"

    def log_message(self, fmt: str, *args) -> None:  # token-safe basic logs
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        if self.path == "/api/health":
            return _json(self, 200, {"ok": True})

        path = urlparse(self.path).path
        file_path = _safe_join_public(path)
        if not file_path or not file_path.exists() or not file_path.is_file():
            self.send_error(404, "Not found")
            return

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(data)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {"/api/ha/covers", "/api/ha/cover/action", "/api/ha/cover/states", "/api/ha/media_players", "/api/ha/media/action", "/api/ha/media/states", "/api/ha/audio/controls", "/api/ha/audio/control_states", "/api/ha/audio/control/action"}:
            self.send_error(404, "Not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")

            if path == "/api/ha/covers":
                covers = _fetch_ha_covers(payload.get("url", ""), payload.get("token", ""))
                return _json(self, 200, {"ok": True, "covers": covers, "count": len(covers)})

            if path == "/api/ha/cover/states":
                covers = _fetch_ha_cover_states_for_entities(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityIds", []),
                )
                return _json(self, 200, {"ok": True, "covers": covers, "count": len(covers)})

            if path == "/api/ha/media_players":
                players = _fetch_ha_media_players(payload.get("url", ""), payload.get("token", ""))
                return _json(self, 200, {"ok": True, "players": players, "count": len(players)})

            if path == "/api/ha/media/states":
                players = _fetch_ha_media_states_for_entities(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityIds", []),
                )
                return _json(self, 200, {"ok": True, "players": players, "count": len(players)})

            if path == "/api/ha/audio/controls":
                controls = _fetch_ha_audio_controls(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("mediaPlayerId", ""),
                    payload.get("mediaPlayerName", ""),
                )
                return _json(self, 200, {"ok": True, "controls": controls})

            if path == "/api/ha/audio/control_states":
                controls = _fetch_ha_audio_control_states(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("controls", {}),
                )
                return _json(self, 200, {"ok": True, "controls": controls})

            if path == "/api/ha/audio/control/action":
                control = _call_number_service(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityId", ""),
                    payload.get("value"),
                )
                return _json(self, 200, {"ok": True, "control": control})

            if path == "/api/ha/media/action":
                state = _call_media_service(
                    payload.get("url", ""),
                    payload.get("token", ""),
                    payload.get("entityId", ""),
                    payload.get("action", ""),
                    payload.get("value"),
                )
                return _json(self, 200, {"ok": True, "state": state})

            state = _call_cover_service(
                payload.get("url", ""),
                payload.get("token", ""),
                payload.get("entityId", ""),
                payload.get("action", ""),
                payload.get("position"),
            )
            return _json(self, 200, {"ok": True, "state": state})
        except Exception as exc:
            _json(self, 502, {"ok": False, "error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Smart Thermostat local server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args()

    class SmartThermostatHTTPServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = 32

    httpd = SmartThermostatHTTPServer((args.host, args.port), SmartThermostatHandler)
    print(f"Smart Thermostat server running at http://{args.host}:{args.port}")
    print("Open http://localhost:%s" % args.port)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
