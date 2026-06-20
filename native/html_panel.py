#!/usr/bin/env python3
"""Lightweight native WebKit host for the Smart Thermostat HTML panel.

This is the hybrid runtime: the Pi still uses the existing FastAPI/web UI, but
it is shown inside a small native GTK/WebKit window instead of a full Chromium
kiosk session.  The goal is to keep the good-looking HTML screen while removing
Chrome's profile, update, tab, and compositor overhead from the wall display.
"""

from __future__ import annotations

import os
import signal
import sys
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


APP_NAME = "Smart Thermostat Hybrid Display"
DEFAULT_PANEL_URL = "http://127.0.0.1:8080"
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800


@dataclass(frozen=True)
class RuntimeConfig:
    panel_url: str
    fullscreen: bool
    width: int
    height: int
    title: str
    cache_dir: str
    data_dir: str
    user_agent_suffix: str


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def with_hybrid_query(url: str) -> str:
    """Add a harmless query marker so the web side can detect the wall runtime."""
    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.setdefault("runtime", "hybrid-webview")
    query.setdefault("wall", "1")
    return urlunsplit((split.scheme, split.netloc, split.path or "/", urlencode(query), split.fragment))


def load_config() -> RuntimeConfig:
    raw_url = (
        os.environ.get("SMART_HYBRID_URL")
        or os.environ.get("SMART_THERMOSTAT_URL")
        or os.environ.get("SMART_THERMOSTAT_API")
        or DEFAULT_PANEL_URL
    )
    return RuntimeConfig(
        panel_url=with_hybrid_query(raw_url.rstrip("/")),
        fullscreen=env_flag("SMART_HYBRID_FULLSCREEN", True),
        width=env_int("SMART_HYBRID_WIDTH", DEFAULT_WIDTH),
        height=env_int("SMART_HYBRID_HEIGHT", DEFAULT_HEIGHT),
        title=os.environ.get("SMART_HYBRID_TITLE", APP_NAME),
        cache_dir=os.environ.get("SMART_HYBRID_CACHE_DIR", f"/tmp/smart-thermostat-webkit-cache-{os.getuid()}"),
        data_dir=os.environ.get("SMART_HYBRID_DATA_DIR", f"/tmp/smart-thermostat-webkit-data-{os.getuid()}"),
        user_agent_suffix=os.environ.get("SMART_HYBRID_USER_AGENT_SUFFIX", "SmartThermostatHybrid/11.9"),
    )


def import_gtk_webkit():
    try:
        import gi  # type: ignore
    except Exception as exc:  # pragma: no cover - only hit on the Pi when packages are missing
        print(
            "GTK/WebKit is not installed. Run ./scripts/install-pi.sh or install "
            "python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1.",
            file=sys.stderr,
        )
        raise exc

    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("WebKit2", "4.1")
    except ValueError:
        gi.require_version("WebKit2", "4.0")

    from gi.repository import Gdk, GLib, Gtk, WebKit2  # type: ignore

    return Gdk, GLib, Gtk, WebKit2


def ensure_temp_dirs(config: RuntimeConfig) -> None:
    for path in (config.cache_dir, config.data_dir):
        os.makedirs(path, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def build_web_context(WebKit2, config: RuntimeConfig):
    """Create the lowest-write WebKit context available on the installed build."""
    context = None

    # Prefer an ephemeral manager when the WebKitGTK build supports it.  This
    # keeps cookies/cache/local browsing data out of the SD card.  If it is not
    # available, the launcher points XDG cache/data paths at /tmp.
    try:
        manager = WebKit2.WebsiteDataManager.new_ephemeral()
        context = WebKit2.WebContext.new_with_website_data_manager(manager)
    except Exception:
        pass

    if context is None:
        try:
            manager = WebKit2.WebsiteDataManager.new(
                base_cache_directory=config.cache_dir,
                base_data_directory=config.data_dir,
            )
            context = WebKit2.WebContext.new_with_website_data_manager(manager)
        except Exception:
            context = WebKit2.WebContext.get_default()

    try:
        context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)
    except Exception:
        pass

    try:
        context.set_favicon_database_directory(None)
    except Exception:
        pass

    return context


def set_setting(settings, name: str, value) -> None:
    try:
        settings.set_property(name, value)
    except Exception:
        pass


def build_web_view(WebKit2, context, config: RuntimeConfig):
    try:
        webview = WebKit2.WebView.new_with_context(context)
    except Exception:
        webview = WebKit2.WebView()

    settings = webview.get_settings()
    set_setting(settings, "enable-developer-extras", False)
    set_setting(settings, "enable-page-cache", False)
    set_setting(settings, "enable-html5-database", False)
    set_setting(settings, "enable-offline-web-application-cache", False)
    set_setting(settings, "enable-write-console-messages-to-stdout", False)
    set_setting(settings, "javascript-can-open-windows-automatically", False)
    set_setting(settings, "allow-modal-dialogs", True)
    set_setting(settings, "enable-smooth-scrolling", True)
    set_setting(settings, "enable-accelerated-2d-canvas", True)
    set_setting(settings, "media-playback-requires-user-gesture", False)

    try:
        current_agent = settings.get_property("user-agent") or ""
        if config.user_agent_suffix and config.user_agent_suffix not in current_agent:
            settings.set_property("user-agent", f"{current_agent} {config.user_agent_suffix}".strip())
    except Exception:
        pass

    try:
        webview.set_zoom_level(float(os.environ.get("SMART_HYBRID_ZOOM", "1.0")))
    except Exception:
        pass

    return webview


def main() -> int:
    config = load_config()
    ensure_temp_dirs(config)

    # Keep WebKit profile/cache data in RAM-backed /tmp unless the service env
    # intentionally points these elsewhere.
    os.environ.setdefault("XDG_CACHE_HOME", config.cache_dir)
    os.environ.setdefault("XDG_DATA_HOME", config.data_dir)
    os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")

    Gdk, GLib, Gtk, WebKit2 = import_gtk_webkit()

    window = Gtk.Window(title=config.title)
    window.set_default_size(config.width, config.height)
    window.set_decorated(False)
    window.set_keep_above(True)
    window.connect("destroy", Gtk.main_quit)

    context = build_web_context(WebKit2, config)
    webview = build_web_view(WebKit2, context, config)
    webview.load_uri(config.panel_url)

    def suppress_context_menu(*_args):
        return True

    def on_load_failed(_view, _load_event, failing_uri, error):
        print(f"Hybrid display load failed for {failing_uri}: {error}", file=sys.stderr)
        GLib.timeout_add_seconds(3, lambda: (_view.load_uri(config.panel_url), False)[1])
        return False

    def on_key_press(_widget, event):
        keyval = event.keyval
        state = event.state
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval in (Gdk.KEY_F5,):
            webview.reload_bypass_cache()
            return True
        if ctrl and keyval in (Gdk.KEY_r, Gdk.KEY_R):
            webview.reload_bypass_cache()
            return True
        # Do not let Escape exit fullscreen on the wall panel.
        if keyval == Gdk.KEY_Escape:
            return True
        return False

    webview.connect("context-menu", suppress_context_menu)
    webview.connect("load-failed", on_load_failed)
    window.connect("key-press-event", on_key_press)

    window.add(webview)
    window.show_all()
    if config.fullscreen:
        window.fullscreen()

    def quit_from_signal(_signum, _frame):
        GLib.idle_add(Gtk.main_quit)

    signal.signal(signal.SIGTERM, quit_from_signal)
    signal.signal(signal.SIGINT, quit_from_signal)

    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
