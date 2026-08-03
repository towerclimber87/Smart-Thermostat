#!/usr/bin/env python3
"""Typed command console for the thermostat's Home Assistant assistant bridge."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib import error, request


def api_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + (path if path.startswith("/") else "/" + path)


def get_json(base_url: str, path: str, timeout: float = 5.0) -> dict[str, Any]:
    req = request.Request(
        api_url(base_url, path),
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(response_error(raw, str(exc))) from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach the thermostat backend: {exc.reason}") from exc
    return decode_json(raw)


def post_json(base_url: str, path: str, payload: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        api_url(base_url, path),
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(response_error(raw, str(exc))) from exc
    except error.URLError as exc:
        raise RuntimeError(f"Could not reach the thermostat backend: {exc.reason}") from exc
    return decode_json(raw)


def decode_json(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Backend returned invalid JSON: {exc}") from exc
    return data if isinstance(data, dict) else {"result": data}


def response_error(raw: str, fallback: str) -> str:
    try:
        data = json.loads(raw or "{}")
        if isinstance(data, dict):
            return str(data.get("error") or data.get("message") or fallback)
    except json.JSONDecodeError:
        pass
    return raw.strip() or fallback


def print_status(base_url: str) -> None:
    data = get_json(base_url, "/api/assistant/status?include_config=1")
    assistant = data.get("assistant") if isinstance(data.get("assistant"), dict) else {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    print(
        "Status: {stage} | speaker: {speaker} | TTS: {tts}".format(
            stage=assistant.get("stage") or "idle",
            speaker=config.get("effectiveMediaPlayerId") or "not configured",
            tts=config.get("ttsEntityId") or "automatic",
        )
    )


def run_command(base_url: str, text: str, *, speak: bool, new_conversation: bool) -> bool:
    payload: dict[str, Any] = {
        "text": text,
        "speak": speak,
        "newConversation": new_conversation,
    }
    result = post_json(base_url, "/api/assistant/process", payload)
    reply = str(result.get("response") or "Home Assistant returned no text response.").strip()
    print(f"JARVIS> {reply}")
    if speak:
        if result.get("speechPlayed"):
            speaker = result.get("mediaPlayerId") or "the selected media player"
            print(f"         Voice sent to {speaker}.")
        else:
            detail = str(result.get("speechError") or "TTS did not report successful playback.")
            print(f"         Voice warning: {detail}")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Type commands that animate the thermostat and run through Home Assistant Assist."
    )
    parser.add_argument(
        "command",
        nargs="*",
        help="Optional one-time command. Omit it to open the continuous prompt.",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080",
        help="Thermostat backend URL (default: http://127.0.0.1:8080).",
    )
    parser.add_argument("--no-speak", action="store_true", help="Show the reply but do not send it to Sonos/TTS.")
    parser.add_argument("--new", action="store_true", help="Start a new Home Assistant conversation for the first command.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    speak = not args.no_speak
    one_time = " ".join(args.command).strip()
    try:
        if one_time:
            run_command(args.url, one_time, speak=speak, new_conversation=args.new)
            return 0
        print("JARVIS-ish typed console")
        print("Type a command. Use /new for a fresh conversation, /status to inspect it, or /quit to leave.\n")
        new_conversation = bool(args.new)
        while True:
            try:
                text = input("You> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nConsole closed.")
                return 0
            if not text:
                continue
            lowered = text.lower()
            if lowered in {"/quit", "/exit", "quit", "exit"}:
                print("Console closed.")
                return 0
            if lowered == "/new":
                new_conversation = True
                print("The next command will start a fresh conversation.")
                continue
            if lowered == "/status":
                try:
                    print_status(args.url)
                except RuntimeError as exc:
                    print(f"Status error: {exc}", file=sys.stderr)
                continue
            try:
                run_command(args.url, text, speak=speak, new_conversation=new_conversation)
                new_conversation = False
            except RuntimeError as exc:
                print(f"JARVIS error: {exc}", file=sys.stderr)
    except RuntimeError as exc:
        print(f"JARVIS error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
