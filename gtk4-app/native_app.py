#!/usr/bin/env python3
"""Native GTK4/Libadwaita desktop client for Loops.

Features:
- Responsive 3-column desktop layout (Dock Rail + Center Video + Right Comments/Interaction)
- Zero-glitch persistent GStreamer video playback engine (no widget recreation)
- ImageCache for avatars and video thumbnails
- Fully functional comment list with working comment likes
- Klipy GIF picker (/api/v1/klipy/gifs/trending)
- Comprehensive Profile view with avatar, bio, stats, and video grid
- Rich Explore view with suggested creators, featured hashtags, and video cards
- Search across accounts, hashtags, and videos
- Video file preview before publishing
- Direct OAuth 2.0 flow
"""

import hashlib
import json
import mimetypes
import os
import secrets
import shutil
import threading
import urllib.parse
import http.server
import socketserver
from pathlib import Path

# Configure GStreamer feature ranks before initialization
HW_DECODER_RANKS = (
    "vah264dec:512,vah265dec:512,vavp9dec:512,vaav1dec:512,vavp8dec:512,"
    "vapostproc:512,avdec_h264:256,avdec_hevc:256,avdec_vp9:256,avdec_aac:256"
)
os.environ.setdefault("GST_PLUGIN_FEATURE_RANK", HW_DECODER_RANKS)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Soup", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gst, Gtk, Soup

Gst.init(None)

APP_ID = "org.joinloops.Loops"
APP_NAME = "Loops"
DEFAULT_INSTANCE = "loops.video"
SCOPES = "user:read user:write video:create video:read"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 LoopsDesktop/1.0"
)


# ==============================================================================
# Helpers & Persistent Storage
# ==============================================================================

def config_path():
    root = Path(GLib.get_user_config_dir()) / "loops"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / "desktop-session.json"


def preferences_path():
    root = Path(GLib.get_user_config_dir()) / "loops"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root / "desktop-preferences.json"


def load_preferences():
    try:
        return json.loads(preferences_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_preferences(preferences):
    path = preferences_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(preferences), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)


def save_session(session):
    path = config_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(session), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)


def load_session():
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def open_external_url(url, parent_window=None):
    try:
        launcher = Gtk.UriLauncher.new(url)
        launcher.launch(parent_window, None, None)
        return True
    except Exception:
        pass
    try:
        Gio.AppInfo.launch_default_for_uri(url, None)
        return True
    except Exception:
        pass
    try:
        Gtk.show_uri(parent_window, url, 0)
        return True
    except Exception:
        return False


def load_custom_css():
    css_provider = Gtk.CssProvider()
    possible_paths = [
        Path(__file__).parent / "style.css",
        Path("/app/bin/style.css"),
        Path("/home/celinho/Downloads/loops-expo/gtk4-app/style.css"),
    ]
    for p in possible_paths:
        if p.exists():
            try:
                css_provider.load_from_path(str(p))
                display = Gdk.Display.get_default()
                if display:
                    Gtk.StyleContext.add_provider_for_display(
                        display, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                    )
                break
            except Exception as e:
                print(f"Error loading CSS from {p}: {e}")


def soup_json(method, url, token=None, form=None, json_body=None):
    session = Soup.Session.new()
    session.set_timeout(30)
    session.set_user_agent(USER_AGENT)

    if form is not None:
        multipart = Soup.Multipart.new(Soup.FORM_MIME_TYPE_MULTIPART)
        for key, value in form.items():
            multipart.append_form_string(key, str(value))
        message = Soup.Message.new_from_multipart(url, multipart)
    else:
        message = Soup.Message.new(method, url)

    if token:
        message.get_request_headers().append("Authorization", f"Bearer {token}")
    message.get_request_headers().append("Accept", "application/json")

    if form is None and method in ("POST", "PUT", "PATCH") and json_body is not None:
        message.set_request_body_from_bytes(
            "application/json",
            GLib.Bytes.new(json.dumps(json_body).encode("utf-8")),
        )

    try:
        bytes_data = session.send_and_read(message, None)
        status = message.get_status()
        body = bytes_data.get_data().decode("utf-8", "replace") if bytes_data else ""
    except Exception as e:
        raise RuntimeError(f"Falha de conexão ({e})")

    try:
        payload = json.loads(body) if body else {}
    except ValueError:
        payload = {"message": body[:500]}

    if status < 200 or status >= 300:
        detail = (
            payload.get("error_description")
            or payload.get("error")
            or payload.get("message")
        )
        raise RuntimeError(f"HTTP {status}: {detail or 'Falha ao falar com o servidor.'}")
    return payload


def payload_items_and_cursor(payload, keys=("data", "items", "videos", "accounts")):
    """Normalize common Loops collection envelopes and their pagination token."""
    if not isinstance(payload, dict):
        return (payload if isinstance(payload, list) else []), None

    meta = payload.get("meta") or payload.get("pagination") or payload.get("_pagination") or {}
    data = next((payload.get(key) for key in keys if isinstance(payload.get(key), (list, dict))), [])
    nested_container = data if isinstance(data, dict) else {}
    if isinstance(data, dict):
        nested_meta = data.get("meta") or data.get("pagination") or {}
        if isinstance(nested_meta, dict):
            meta = nested_meta or meta
        nested = next((data.get(key) for key in keys if isinstance(data.get(key), list)), None)
        data = nested if nested is not None else []

    cursor = None
    cursor_sources = (meta, nested_container, payload)
    for source in cursor_sources:
        if not isinstance(source, dict):
            continue
        cursor = source.get("next_cursor") or source.get("nextCursor") or source.get("next_page") or source.get("nextPage")
        if cursor:
            break
        meta = source
        cursor = None
        if not cursor:
            next_link = source.get("next")
            if isinstance(next_link, str):
                parsed = urllib.parse.urlparse(next_link)
                query = urllib.parse.parse_qs(parsed.query)
                cursor = next((query.get(key, [None])[0] for key in ("cursor", "max_id", "page") if query.get(key)), None)
                if not cursor and not next_link.startswith(("http://", "https://", "/")):
                    cursor = next_link
        if cursor:
            break
    return (data if isinstance(data, list) else []), cursor


# ==============================================================================
# Asynchronous Image Cache
# ==============================================================================

class ImageCache:
    """Downloads, caches, and sets remote images asynchronously."""

    def __init__(self):
        self.cache_dir = Path(GLib.get_user_cache_dir()) / "loops" / "img_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session = Soup.Session.new()
        self._session.set_user_agent(USER_AGENT)
        self._memory = {}

    def load_into(self, url, picture_widget):
        if not url:
            return
        if url in self._memory:
            picture_widget.set_paintable(self._memory[url])
            return

        def worker():
            try:
                fname = hashlib.sha256(url.encode()).hexdigest()
                local_path = self.cache_dir / f"{fname}.img"
                if not local_path.exists():
                    msg = Soup.Message.new("GET", url)
                    res = self._session.send_and_read(msg, None)
                    if msg.get_status() == 200 and res:
                        local_path.write_bytes(res.get_data())

                if local_path.exists():
                    texture = Gdk.Texture.new_from_file(Gio.File.new_for_path(str(local_path)))
                    self._memory[url] = texture
                    GLib.idle_add(picture_widget.set_paintable, texture)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def make_avatar(self, url, size=36):
        frame = Gtk.AspectFrame()
        frame.set_ratio(1.0)
        frame.set_obey_child(False)
        frame.set_size_request(size, size)
        frame.set_halign(Gtk.Align.CENTER)
        frame.set_valign(Gtk.Align.START)
        frame.set_hexpand(False)
        frame.set_vexpand(False)
        frame.set_overflow(Gtk.Overflow.HIDDEN)
        frame.add_css_class("avatar-circle")
        pic = Gtk.Picture()
        pic.set_size_request(size, size)
        pic.set_content_fit(Gtk.ContentFit.COVER)
        pic.set_can_shrink(True)
        pic.set_hexpand(True)
        pic.set_vexpand(True)
        if url:
            self.load_into(url, pic)
        else:
            fallback = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
            fallback.set_pixel_size(size - 8)
            fallback.set_halign(Gtk.Align.CENTER)
            fallback.set_valign(Gtk.Align.CENTER)
            frame.set_child(fallback)
            return frame
        frame.set_child(pic)
        return frame

    def make_thumbnail(self, url, width=140, height=200):
        pic = Gtk.Picture()
        pic.add_css_class("video-grid-thumb")
        pic.set_size_request(width, height)
        pic.set_content_fit(Gtk.ContentFit.COVER)
        pic.set_can_shrink(True)
        if url:
            self.load_into(url, pic)
        return pic


IMAGE_CACHE = ImageCache()


# ==============================================================================
# Zero-Glitch GStreamer Video Engine
# ==============================================================================

class GstPlayer:
    """Persistent playbin3 player with seamless URI switching (zero widget recreation)."""

    def __init__(self, muted=False, loop=True, autoplay=True, on_eos=None):
        self.loop = loop
        self.playing = False
        self.uri = None
        self.on_eos = on_eos
        self.duration_sec = 0.0
        self.position_sec = 0.0

        self.playbin = Gst.ElementFactory.make("playbin3", None)
        self.sink = Gst.ElementFactory.make("gtk4paintablesink", None)
        self.playbin.set_property("video-sink", self.sink)
        self.playbin.set_property("mute", muted)
        self.playbin.connect("source-setup", self._on_source_setup)

        self.picture = Gtk.Picture()
        self.picture.set_paintable(self.sink.props.paintable)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.set_can_shrink(True)

        bus = self.playbin.get_bus()
        bus.add_signal_watch()
        self._bus = bus
        self._bus_id = bus.connect("message", self._on_bus_message)
        self.playbin.set_state(Gst.State.READY)

    def _on_source_setup(self, _playbin, source):
        if hasattr(source.props, "user_agent"):
            source.props.user_agent = USER_AGENT
        if hasattr(source.props, "ssl_strict"):
            source.props.ssl_strict = False
        if hasattr(source.props, "automatic_redirect"):
            source.props.automatic_redirect = True

    def _on_bus_message(self, _bus, message):
        kind = message.type
        if kind == Gst.MessageType.EOS:
            if self.loop:
                self.seek(0)
            else:
                self.set_playing(False)
            if self.on_eos:
                self.on_eos()
        elif kind == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print(f"[GstPlayer] Erro de vídeo: {error.message} ({debug})")

    def load_uri(self, uri, autoplay=True):
        """Seamlessly change video URI without destroying the widget."""
        self.uri = uri
        self.duration_sec = 0.0
        self.position_sec = 0.0
        self.playbin.set_state(Gst.State.READY)
        self.playbin.set_property("uri", uri)
        if autoplay:
            self.set_playing(True)

    def update_position_and_duration(self):
        ok_dur, dur = self.playbin.query_duration(Gst.Format.TIME)
        if ok_dur and dur > 0:
            self.duration_sec = dur / 1e9

        ok_pos, pos = self.playbin.query_position(Gst.Format.TIME)
        if ok_pos and pos >= 0:
            self.position_sec = pos / 1e9
        return self.position_sec, self.duration_sec

    def seek(self, position_sec):
        if position_sec < 0:
            position_sec = 0
        ns = int(position_sec * 1e9)
        self.playbin.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            ns,
        )

    def set_playing(self, playing):
        self.playing = bool(playing)
        self.playbin.set_state(Gst.State.PLAYING if playing else Gst.State.PAUSED)

    def toggle_playing(self):
        self.set_playing(not self.playing)
        return self.playing

    def set_muted(self, muted):
        self.playbin.set_property("mute", bool(muted))

    def stop(self):
        self.playbin.set_state(Gst.State.NULL)
        try:
            self._bus.disconnect(self._bus_id)
            self._bus.remove_signal_watch()
        except Exception:
            pass


# ==============================================================================
# OAuth Callback Server
# ==============================================================================

class CallbackServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/oauth-callback":
            params = urllib.parse.parse_qs(parsed.query)
            state = self.server.auth_state
            received_state = params.get("state", [None])[0]

            if received_state != state["expected_state"]:
                state["error"] = "Validação de segurança do retorno OAuth falhou."
            else:
                state["code"] = params.get("code", [None])[0]
                state["error"] = params.get(
                    "error_description", params.get("error", [None])
                )[0]

            state["event"].set()

            body = (
                "<!doctype html>"
                "<html><head><meta charset='utf-8'><title>Loops - Autorizado</title>"
                "<style>"
                "body { font-family: system-ui, sans-serif; background: #121212; color: #fff; text-align: center; padding: 4rem 2rem; }"
                ".card { background: #1e1e1e; max-width: 480px; margin: auto; padding: 2.5rem; border-radius: 16px; border: 1px solid #333; }"
                "h1 { color: #3b82f6; margin-bottom: 0.5rem; }"
                "p { color: #a1a1aa; line-height: 1.6; }"
                "</style></head>"
                "<body><div class='card'>"
                "<h1>✓ Autorização concluída!</h1>"
                "<p>Sua conta do Loops foi conectada com sucesso.<br>Você já pode fechar esta aba e voltar para o aplicativo desktop.</p>"
                "</div></body></html>"
            ).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *_args):
        pass


# ==============================================================================
# Main Application Window
# ==============================================================================

class LoopsWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(
            application=app,
            title=APP_NAME,
            default_width=1120,
            default_height=780,
        )
        self.add_css_class("loops-window")
        self.session = load_session()
        self.videos = []
        self.video_index = 0
        self.feed_scope = "for-you"
        self.feed_cursor = None
        self.loading_more = False
        self.busy = False
        self.is_seeking = False
        self.data_pages = {}
        self.conversation_by_row = {}
        self.progress_timer_id = None
        self.create_player = None

        # Build Main View Hierarchy
        self._build_header()
        self._build_main_layout()
        self._setup_keyboard_shortcuts()

        # Connect window close
        self.connect("close-request", self._on_close_request)

        # Progress bar timer
        self.progress_timer_id = GLib.timeout_add(250, self._on_progress_tick)

        if self.session and self.session.get("access_token"):
            self.show_feed()
        else:
            self.show_login()

    def _on_close_request(self, *_args):
        if self.progress_timer_id:
            GLib.source_remove(self.progress_timer_id)
            self.progress_timer_id = None
        if hasattr(self, "player") and self.player:
            self.player.stop()
        if self.create_player:
            self.create_player.stop()
        return False

    # --------------------------------------------------------------------------
    # Header & Tabs
    # --------------------------------------------------------------------------

    def _build_header(self):
        self.header_bar = Adw.HeaderBar()
        self.header_bar.add_css_class("flat")

        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        tab_box.add_css_class("header-tab-bar")

        self.btn_following = Gtk.Button(label="👥 Following")
        self.btn_following.add_css_class("header-tab-btn")
        self.btn_following.connect("clicked", lambda *_: self._set_feed_scope("following"))

        self.btn_local = Gtk.Button(label="📍 Local")
        self.btn_local.add_css_class("header-tab-btn")
        self.btn_local.connect("clicked", lambda *_: self._set_feed_scope("local"))

        self.btn_for_you = Gtk.Button(label="🔥 For You")
        self.btn_for_you.add_css_class("header-tab-btn")
        self.btn_for_you.add_css_class("active")
        self.btn_for_you.connect("clicked", lambda *_: self._set_feed_scope("for-you"))

        tab_box.append(self.btn_following)
        tab_box.append(self.btn_local)
        tab_box.append(self.btn_for_you)
        self.header_bar.set_title_widget(tab_box)

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Menu")
        menu_btn.set_popover(self._build_menu_popover())
        self.header_bar.pack_end(menu_btn)

    def _build_menu_popover(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        actions = [
            ("Atualizar feed", lambda: self._load_feed()),
            ("Explorar", lambda: self._open_explore_page()),
            ("Notificações", lambda: self._open_data_page("notifications", "Notificações", "/api/v1/account/notifications")),
            ("Mensagens", lambda: self._open_data_page("messages", "Mensagens", "/api/v1/dm/conversations")),
            ("Meu perfil", lambda: self._open_profile_view()),
            ("Publicar vídeo", lambda: self._show_page("create")),
            ("Configurações", lambda: self._show_page("settings")),
            ("Sair", self._logout),
        ]
        for label, callback in actions:
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda _b, cb=callback: cb())
            box.append(btn)

        popover = Gtk.Popover()
        popover.set_child(box)
        return popover

    # --------------------------------------------------------------------------
    # Main 3-Column Layout
    # --------------------------------------------------------------------------

    def _build_main_layout(self):
        self.root_view = Adw.ToolbarView()
        self.root_view.add_top_bar(self.header_bar)

        self.main_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.root_view.set_content(self.main_stack)
        self.set_content(self.root_view)

        # Login Screen
        self._build_login_screen()

        # Workspace with Left Dock + Center/Right Stack
        self.workspace_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        # Left Dock
        self._build_left_dock()
        self.workspace_box.append(self.left_dock)

        # Content Stack
        self.content_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_hexpand(True)
        self.content_stack.set_vexpand(True)

        # Feed layout (Center Video + Right Interaction Panel)
        self.feed_layout = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._build_video_area()
        self._build_interaction_panel()
        self.feed_layout.append(self.video_area)
        self.feed_layout.append(self.interaction_panel)
        self.content_stack.add_named(self.feed_layout, "feed")

        # Subpages
        self._build_create_page()
        self._build_settings_page()

        self.workspace_box.append(self.content_stack)
        self.main_stack.add_named(self.workspace_box, "workspace")

    # --------------------------------------------------------------------------
    # Left Navigation Dock
    # --------------------------------------------------------------------------

    def _build_left_dock(self):
        self.left_dock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.left_dock.add_css_class("dock-rail")
        self.left_dock.set_size_request(60, -1)

        items = [
            ("go-home-symbolic", "Home", lambda: self._show_feed_page()),
            ("find-location-symbolic", "Explore", lambda: self._open_explore_page()),
            ("camera-video-symbolic", "Create", lambda: self._show_page("create")),
            ("mail-unread-symbolic", "Messages", lambda: self._open_data_page("messages", "Mensagens", "/api/v1/dm/conversations")),
            ("system-users-symbolic", "Following", self._open_following_suggestions_page),
            ("avatar-default-symbolic", "Profile", lambda: self._open_profile_view()),
            ("emblem-system-symbolic", "Settings", lambda: self._show_page("settings")),
            ("system-search-symbolic", "Search", lambda: self._open_search_view()),
        ]

        self.dock_buttons = {}
        for icon_name, tooltip, callback in items:
            btn = Gtk.Button(icon_name=icon_name, tooltip_text=tooltip)
            btn.add_css_class("dock-btn")
            btn.connect("clicked", lambda _b, cb=callback: cb())
            self.left_dock.append(btn)
            self.dock_buttons[tooltip] = btn

        self.dock_buttons["Home"].add_css_class("active")

    def _set_active_dock_button(self, name):
        for btn_name, btn in self.dock_buttons.items():
            if btn_name == name:
                btn.add_css_class("active")
            else:
                btn.remove_css_class("active")

    # --------------------------------------------------------------------------
    # Center Video Player Area (Zero Glitch)
    # --------------------------------------------------------------------------

    def _build_video_area(self):
        self.video_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.video_area.add_css_class("video-area")
        self.video_area.set_hexpand(True)
        self.video_area.set_vexpand(True)

        self.video_overlay = Gtk.Overlay()
        self.video_overlay.set_hexpand(True)
        self.video_overlay.set_vexpand(True)

        # Single persistent GstPlayer for zero widget recreate flicker
        muted = load_preferences().get("video_muted", False)
        self.player = GstPlayer(muted=muted, loop=True, autoplay=False)

        # Video container
        self.video_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.video_frame.set_hexpand(True)
        self.video_frame.set_vexpand(True)
        self.video_frame.append(self.player.picture)
        self.video_overlay.set_child(self.video_frame)

        # Scroll to switch videos
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.connect("scroll", self._on_video_scroll)
        self.video_frame.add_controller(wheel)

        # Click to toggle play/pause
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self._toggle_play_pause())
        self.video_frame.add_controller(click)

        # Large center play button overlay
        self.play_overlay_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_overlay_btn.add_css_class("play-overlay-circle")
        self.play_overlay_btn.set_halign(Gtk.Align.CENTER)
        self.play_overlay_btn.set_valign(Gtk.Align.CENTER)
        self.play_overlay_btn.connect("clicked", lambda *_: self._toggle_play_pause())
        self.play_overlay_btn.set_visible(False)
        self.video_overlay.add_overlay(self.play_overlay_btn)

        # Status text
        self.feed_status = Gtk.Label(label="")
        self.feed_status.set_halign(Gtk.Align.CENTER)
        self.feed_status.set_valign(Gtk.Align.START)
        self.feed_status.set_margin_top(12)
        self.feed_status.add_css_class("dim-label")
        self.feed_status.set_visible(False)
        self.video_overlay.add_overlay(self.feed_status)

        # Bottom Controls
        self._build_bottom_controls()
        self.video_overlay.add_overlay(self.controls_bar)

        self.video_area.append(self.video_overlay)

    def _build_bottom_controls(self):
        self.controls_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.controls_bar.add_css_class("video-controls-bar")
        self.controls_bar.set_valign(Gtk.Align.END)
        self.controls_bar.set_hexpand(True)

        self.ctrl_play_btn = Gtk.Button(icon_name="media-playback-pause-symbolic")
        self.ctrl_play_btn.add_css_class("video-control-btn")
        self.ctrl_play_btn.connect("clicked", lambda *_: self._toggle_play_pause())
        self.controls_bar.append(self.ctrl_play_btn)

        self.lbl_time_pos = Gtk.Label(label="0:00")
        self.lbl_time_pos.add_css_class("time-label")
        self.controls_bar.append(self.lbl_time_pos)

        self.progress_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.progress_scale.set_hexpand(True)
        self.progress_scale.set_draw_value(False)
        self.progress_scale.connect("change-value", self._on_scale_change_value)
        self.controls_bar.append(self.progress_scale)

        self.lbl_time_dur = Gtk.Label(label="0:00")
        self.lbl_time_dur.add_css_class("time-label")
        self.controls_bar.append(self.lbl_time_dur)

        self.ctrl_volume_btn = Gtk.Button(icon_name="audio-volume-high-symbolic", tooltip_text="Som")
        self.ctrl_volume_btn.add_css_class("video-control-btn")
        self.ctrl_volume_btn.connect("clicked", lambda *_: self._toggle_video_sound())
        self.controls_bar.append(self.ctrl_volume_btn)

        self.ctrl_download_btn = Gtk.Button(icon_name="document-save-symbolic", tooltip_text="Baixar vídeo")
        self.ctrl_download_btn.add_css_class("video-control-btn")
        self.ctrl_download_btn.connect("clicked", lambda *_: self._download_current_video())
        self.controls_bar.append(self.ctrl_download_btn)

        self.ctrl_fullscreen_btn = Gtk.Button(icon_name="view-fullscreen-symbolic", tooltip_text="Tela cheia")
        self.ctrl_fullscreen_btn.add_css_class("video-control-btn")
        self.ctrl_fullscreen_btn.connect("clicked", lambda *_: self._toggle_fullscreen())
        self.controls_bar.append(self.ctrl_fullscreen_btn)

    # --------------------------------------------------------------------------
    # Right Sidebar ("Comments & Interaction")
    # --------------------------------------------------------------------------

    def _build_interaction_panel(self):
        self.interaction_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.interaction_panel.add_css_class("interaction-panel")
        self.interaction_panel.set_size_request(340, -1)
        self.interaction_panel.set_vexpand(True)

        title = Gtk.Label(label="Comments & Interaction", xalign=0)
        title.add_css_class("interaction-title")
        self.interaction_panel.append(title)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        action_row.add_css_class("action-row")
        action_row.set_hexpand(True)

        self.btn_like = Gtk.Button(label="♡ Like")
        self.btn_like.add_css_class("action-card-btn")
        self.btn_like.set_hexpand(True)
        self.btn_like.connect("clicked", self._toggle_like)

        self.btn_share = Gtk.Button(label="↗ Share")
        self.btn_share.add_css_class("action-card-btn")
        self.btn_share.set_hexpand(True)
        self.btn_share.connect("clicked", self._share_video)

        self.btn_subscribe = Gtk.Button(label="🔔 Sub")
        self.btn_subscribe.add_css_class("action-card-btn")
        self.btn_subscribe.set_hexpand(True)
        self.btn_subscribe.connect("clicked", self._toggle_author_follow)

        self.btn_profile = Gtk.Button(label="👤 Profile")
        self.btn_profile.add_css_class("action-card-btn")
        self.btn_profile.set_hexpand(True)
        self.btn_profile.connect("clicked", self._open_current_author_profile)

        self.btn_save = Gtk.Button(label="☆ Save")
        self.btn_save.add_css_class("action-card-btn")
        self.btn_save.set_hexpand(True)
        self.btn_save.connect("clicked", self._toggle_bookmark)

        action_row.append(self.btn_like)
        action_row.append(self.btn_share)
        action_row.append(self.btn_subscribe)
        action_row.append(self.btn_profile)
        action_row.append(self.btn_save)
        self.interaction_panel.append(action_row)

        self.comments_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.comments_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.comments_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.comments_box.add_css_class("comments-list-box")
        self.comments_scroll.set_child(self.comments_box)
        self.interaction_panel.append(self.comments_scroll)

        composer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        composer.add_css_class("comment-composer-box")

        self.comment_entry = Gtk.Entry(placeholder_text="Add a comment…", hexpand=True)
        self.comment_entry.add_css_class("comment-entry")
        self.comment_entry.connect("activate", lambda *_: self._post_comment())

        self.gif_btn = Gtk.Button(label="GIF")
        self.gif_btn.add_css_class("composer-btn")
        self.gif_btn.add_css_class("gif-btn")
        self.gif_btn.connect("clicked", lambda *_: self._open_gif_picker())

        self.post_btn = Gtk.Button(label="Post")
        self.post_btn.add_css_class("composer-btn")
        self.post_btn.add_css_class("post-btn")
        self.post_btn.connect("clicked", lambda *_: self._post_comment())

        composer.append(self.comment_entry)
        composer.append(self.gif_btn)
        composer.append(self.post_btn)
        self.interaction_panel.append(composer)

    # --------------------------------------------------------------------------
    # Login Screen
    # --------------------------------------------------------------------------

    def _build_login_screen(self):
        page = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="Loops", subtitle="Entrar na conta"))
        page.add_top_bar(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.set_valign(Gtk.Align.CENTER)
        body.set_halign(Gtk.Align.CENTER)
        body.set_size_request(420, -1)
        body.set_margin_start(24)
        body.set_margin_end(24)

        title = Gtk.Label(label="Entre no Loops")
        title.add_css_class("title-1")
        body.append(title)

        subtitle = Gtk.Label(
            label="Conecte seu servidor Fediverse / Pixelfed / Loops para assistir e interagir.",
            wrap=True,
            justify=Gtk.Justification.CENTER,
        )
        subtitle.add_css_class("dim-label")
        body.append(subtitle)

        self.server_entry = Gtk.Entry(text=DEFAULT_INSTANCE, placeholder_text="servidor, ex.: loops.video")
        self.server_entry.set_input_purpose(Gtk.InputPurpose.URL)
        body.append(self.server_entry)

        self.login_button = Gtk.Button(label="Entrar com o navegador")
        self.login_button.add_css_class("suggested-action")
        self.login_button.add_css_class("pill")
        self.login_button.connect("clicked", self._begin_login)
        body.append(self.login_button)

        self.login_status = Gtk.Label(wrap=True, selectable=True, justify=Gtk.Justification.CENTER)
        self.login_status.add_css_class("dim-label")
        body.append(self.login_status)

        self.login_fallback_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.login_fallback_box.set_visible(False)
        self.manual_auth_btn = Gtk.Button(label="Abrir link no navegador")
        self.copy_auth_btn = Gtk.Button(label="Copiar link de autorização")
        self.login_fallback_box.append(self.manual_auth_btn)
        self.login_fallback_box.append(self.copy_auth_btn)
        body.append(self.login_fallback_box)

        page.set_content(body)
        self.main_stack.add_named(page, "login")

    def _begin_login(self, *_args):
        if self.busy:
            return
        instance = self.server_entry.get_text().strip().lower()
        instance = instance.removeprefix("https://").removeprefix("http://").strip("/")
        if not instance or "/" in instance or " " in instance:
            self.login_status.set_text("Digite apenas o endereço do servidor, ex.: loops.video")
            return
        self._set_login_busy(True, "Conectando ao servidor e registrando aplicativo…")
        threading.Thread(target=self._oauth_flow, args=(instance,), daemon=True).start()

    def _oauth_flow(self, instance):
        server = None
        try:
            server = CallbackServer(("127.0.0.1", 0), OAuthCallbackHandler)
            port = server.server_address[1]
            redirect_uri = f"http://127.0.0.1:{port}/oauth-callback"
            state = secrets.token_urlsafe(24)

            server.auth_state = {
                "event": threading.Event(),
                "expected_state": state,
                "code": None,
                "error": None,
            }
            threading.Thread(target=server.serve_forever, daemon=True).start()

            app_data = soup_json(
                "POST",
                f"https://{instance}/api/v1/apps",
                form={
                    "client_name": "Loops Desktop GTK",
                    "redirect_uris[]": redirect_uri,
                    "scopes": SCOPES,
                    "website": "https://joinloops.org",
                },
            )

            client_id = app_data.get("client_id")
            client_secret = app_data.get("client_secret")
            if not client_id or not client_secret:
                raise RuntimeError("O servidor não retornou credenciais de aplicativo.")

            query = urllib.parse.urlencode({
                "client_id": client_id,
                "scope": SCOPES,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "state": state,
            })
            auth_url = f"https://{instance}/oauth/authorize?{query}"

            GLib.idle_add(self._setup_manual_auth_links, auth_url)
            GLib.idle_add(open_external_url, auth_url, self)
            GLib.idle_add(
                self.login_status.set_text,
                "Navegador aberto! Autorize o Loops na página web.\nRetornarei ao aplicativo automaticamente após a autorização.",
            )

            server.auth_state["event"].wait(600)
            auth = server.auth_state
            server.shutdown()
            server.server_close()
            server = None

            if auth["error"]:
                raise RuntimeError(f"Autorização recusada: {auth['error']}")
            if not auth["code"]:
                raise RuntimeError("Tempo limite de autorização esgotado.")

            GLib.idle_add(self.login_status.set_text, "Concluindo autorização e recebendo token…")
            token_data = soup_json(
                "POST",
                f"https://{instance}/oauth/token",
                form={
                    "grant_type": "authorization_code",
                    "code": auth["code"],
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                },
            )
            token = token_data.get("access_token")
            if not token:
                raise RuntimeError("O servidor não retornou um token de acesso válido.")

            account = soup_json(
                "GET",
                f"https://{instance}/api/v1/account/info/self",
                token=token,
            )

            save_session({
                "instance": instance,
                "access_token": token,
                "account": account,
            })
            self.session = load_session()
            GLib.idle_add(self._login_succeeded)

        except Exception as error:
            if server:
                try:
                    server.shutdown()
                    server.server_close()
                except Exception:
                    pass
            GLib.idle_add(self._login_failed, str(error))

    def _setup_manual_auth_links(self, auth_url):
        self.login_fallback_box.set_visible(True)
        self.manual_auth_btn.connect("clicked", lambda *_: open_external_url(auth_url, self))
        self.copy_auth_btn.connect("clicked", lambda *_: self._copy_to_clipboard(auth_url))

    def _set_login_busy(self, busy, message=""):
        self.busy = busy
        self.login_button.set_sensitive(not busy)
        self.login_status.set_text(message)

    def _login_succeeded(self):
        self._set_login_busy(False)
        self.login_fallback_box.set_visible(False)
        self.show_feed()

    def _login_failed(self, message):
        self._set_login_busy(False, f"Falha no login: {message}")

    def show_login(self):
        if hasattr(self, "player") and self.player:
            self.player.set_playing(False)
        self.main_stack.set_visible_child_name("login")

    def show_feed(self):
        self.main_stack.set_visible_child_name("workspace")
        self._show_feed_page()
        self._load_feed()

    # --------------------------------------------------------------------------
    # Subpage: Video Creation / Upload with Live Preview
    # --------------------------------------------------------------------------

    def _build_create_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Simple nav bar (no HeaderBar = no duplicate window buttons)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.add_css_class("subpage-nav")
        nav.set_margin_start(12)
        nav.set_margin_end(12)
        nav.set_margin_top(10)
        nav.set_margin_bottom(10)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Voltar ao Feed")
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self._show_feed_page())
        title_lbl = Gtk.Label(label="Publicar vídeo")
        title_lbl.add_css_class("title-4")
        title_lbl.set_hexpand(True)
        title_lbl.set_xalign(0)
        nav.append(back)
        nav.append(title_lbl)
        page.append(nav)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        page.append(sep)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_size_request(450, -1)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        scroll.set_child(box)
        page.append(scroll)

        # Video Preview Holder
        self.create_preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.create_preview_box.set_size_request(240, 320)
        self.create_preview_box.add_css_class("video-grid-thumb")
        self.create_preview_box.set_halign(Gtk.Align.CENTER)
        box.append(self.create_preview_box)

        self.create_file_label = Gtk.Label(label="Selecione um arquivo de vídeo (MP4)", wrap=True)
        self.create_file_label.add_css_class("dim-label")

        choose_btn = Gtk.Button(label="Escolher arquivo…")
        choose_btn.connect("clicked", self._choose_video)

        self.caption_entry = Gtk.Entry(placeholder_text="Escreva uma legenda…")

        publish_btn = Gtk.Button(label="Publicar vídeo")
        publish_btn.add_css_class("suggested-action")
        publish_btn.connect("clicked", self._publish_video)

        self.create_status = Gtk.Label(wrap=True, selectable=True)

        box.append(self.create_file_label)
        box.append(choose_btn)
        box.append(self.caption_entry)
        box.append(publish_btn)
        box.append(self.create_status)

        self.content_stack.add_named(page, "create")


    def _choose_video(self, *_args):
        chooser = Gtk.FileChooserNative.new(
            "Escolher vídeo", self, Gtk.FileChooserAction.OPEN, "Escolher", "Cancelar"
        )
        media_filter = Gtk.FileFilter()
        media_filter.set_name("Vídeos (MP4/WebM)")
        media_filter.add_mime_type("video/mp4")
        media_filter.add_mime_type("video/webm")
        chooser.add_filter(media_filter)
        chooser.connect("response", self._video_chosen)
        chooser.show()

    def _video_chosen(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            gfile = chooser.get_file()
            if gfile:
                self.selected_video_path = gfile.get_path()
                self.create_file_label.set_text(f"Selecionado: {Path(self.selected_video_path).name}")

                # Live preview of selected video
                child = self.create_preview_box.get_first_child()
                while child:
                    nxt = child.get_next_sibling()
                    self.create_preview_box.remove(child)
                    child = nxt

                if self.create_player:
                    self.create_player.stop()

                uri = f"file://{self.selected_video_path}"
                self.create_player = GstPlayer(muted=False, loop=True, autoplay=True)
                self.create_player.load_uri(uri, autoplay=True)
                self.create_preview_box.append(self.create_player.picture)

        chooser.destroy()

    def _publish_video(self, *_args):
        if not getattr(self, "selected_video_path", None):
            self.create_status.set_text("Escolha um arquivo de vídeo primeiro.")
            return
        caption = self.caption_entry.get_text().strip()
        self.create_status.set_text("Enviando vídeo para o servidor…")

        def upload():
            try:
                video_data = Path(self.selected_video_path).read_bytes()
                mime, _ = mimetypes.guess_type(self.selected_video_path)
                mime = mime or "video/mp4"

                session = Soup.Session.new()
                session.set_timeout(120)
                session.set_user_agent(USER_AGENT)

                multipart = Soup.Multipart.new(Soup.FORM_MIME_TYPE_MULTIPART)
                multipart.append_form_string("caption", caption)
                multipart.append_form_string("visibility", "public")
                multipart.append_form_string("is_local", "1")
                multipart.append_form_string("is_sensitive", "0")

                buf = GLib.Bytes.new(video_data)
                multipart.append_form_file(
                    "video",
                    Path(self.selected_video_path).name,
                    mime,
                    buf,
                )

                msg = Soup.Message.new_from_multipart(
                    f"https://{self.session['instance']}/api/v1/studio/upload",
                    multipart,
                )
                msg.get_request_headers().append("Authorization", f"Bearer {self.session['access_token']}")
                res = session.send_and_read(msg, None)

                if msg.get_status() in (200, 201):
                    GLib.idle_add(self.create_status.set_text, "Vídeo publicado com sucesso!")
                    GLib.idle_add(self._show_feed_page)
                    GLib.idle_add(self._load_feed)
                else:
                    GLib.idle_add(self.create_status.set_text, f"Falha no envio: HTTP {msg.get_status()}")
            except Exception as e:
                GLib.idle_add(self.create_status.set_text, f"Erro ao enviar vídeo: {e}")

        threading.Thread(target=upload, daemon=True).start()

    # --------------------------------------------------------------------------
    # Subpage: Settings
    # --------------------------------------------------------------------------

    def _build_settings_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.add_css_class("subpage-nav")
        nav.set_margin_start(12)
        nav.set_margin_end(12)
        nav.set_margin_top(10)
        nav.set_margin_bottom(10)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Voltar ao Feed")
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self._show_feed_page())
        title_lbl = Gtk.Label(label="Configurações")
        title_lbl.add_css_class("title-4")
        title_lbl.set_hexpand(True)
        title_lbl.set_xalign(0)
        nav.append(back)
        nav.append(title_lbl)
        page.append(nav)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        page.append(sep)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_start(32)
        box.set_margin_end(32)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_size_request(500, -1)
        box.set_halign(Gtk.Align.CENTER)
        scroll.set_child(box)
        page.append(scroll)

        self.account_info_label = Gtk.Label(xalign=0, wrap=True)
        self.account_info_label.add_css_class("heading")
        box.append(self.account_info_label)

        theme_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lbl_theme = Gtk.Label(label="Tema / Aparência", xalign=0, hexpand=True)
        self.theme_choice = Gtk.DropDown.new_from_strings(["Usar o sistema", "Claro", "Escuro"])
        self.theme_choice.connect("notify::selected", self._save_theme)
        theme_row.append(lbl_theme)
        theme_row.append(self.theme_choice)
        box.append(theme_row)

        logout_btn = Gtk.Button(label="Sair desta conta")
        logout_btn.add_css_class("destructive-action")
        logout_btn.connect("clicked", self._logout)
        box.append(logout_btn)

        self.content_stack.add_named(page, "settings")


    def _update_settings_display(self):
        profile = (self.session or {}).get("account") or {}
        username = profile.get("username") or profile.get("acct") or ""
        instance = (self.session or {}).get("instance", "")
        self.account_info_label.set_text(f"Conectado como @{username}\nServidor: {instance}")

        settings = load_preferences()
        theme = settings.get("theme", "system")
        idx = {"system": 0, "light": 1, "dark": 2}.get(theme, 0)
        self.theme_choice.set_selected(idx)

    def _save_theme(self, *_args):
        selected = self.theme_choice.get_selected()
        theme = {0: "system", 1: "light", 2: "dark"}.get(selected, "system")
        pref = load_preferences()
        pref["theme"] = theme
        save_preferences(pref)
        style = Adw.StyleManager.get_default()
        style.set_color_scheme({
            "system": Adw.ColorScheme.DEFAULT,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }.get(theme, Adw.ColorScheme.DEFAULT))

    def _show_page(self, name):
        if hasattr(self, "player") and self.player:
            self.player.set_playing(False)
        if name == "settings":
            self._update_settings_display()
        self.content_stack.set_visible_child_name(name)
        # sync dock active state
        dock_map = {
            "create": "Create",
            "settings": "Settings",
        }
        if name in dock_map:
            self._set_active_dock_button(dock_map[name])

    def _make_subpage(self, title, back_label="Voltar ao Feed"):
        """Build an in-content title row without another window-controls bar."""
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.add_css_class("subpage-nav")
        nav.set_margin_start(12)
        nav.set_margin_end(12)
        nav.set_margin_top(8)
        nav.set_margin_bottom(8)

        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=back_label)
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self._show_feed_page())
        title_label = Gtk.Label(label=title, xalign=0, hexpand=True)
        title_label.add_css_class("title-4")
        nav.append(back)
        nav.append(title_label)
        page.append(nav)
        page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        return page


    def _show_feed_page(self):
        self.content_stack.set_visible_child_name("feed")
        self._set_active_dock_button("Home")
        if hasattr(self, "player") and self.player and self.player.uri:
            self.player.set_playing(True)

    # --------------------------------------------------------------------------
    # Feed Engine & Switching
    # --------------------------------------------------------------------------

    def _set_feed_scope(self, scope):
        self.feed_scope = scope
        self.btn_following.remove_css_class("active")
        self.btn_local.remove_css_class("active")
        self.btn_for_you.remove_css_class("active")

        if scope == "following":
            self.btn_following.add_css_class("active")
        elif scope == "local":
            self.btn_local.add_css_class("active")
        else:
            self.btn_for_you.add_css_class("active")

        self._show_feed_page()
        self._load_feed()

    def _load_feed(self):
        if not self.session:
            self.show_login()
            return
        self.feed_cursor = None
        self.videos = []
        self.loading_more = False
        self.feed_status.set_text("Carregando vídeos…")
        self.feed_status.set_visible(True)
        threading.Thread(target=self._fetch_feed, args=(None, False), daemon=True).start()

    def _fetch_feed(self, cursor=None, append=False):
        try:
            if self.feed_scope == "following":
                route = "/api/v1/feed/following"
            elif self.feed_scope == "local":
                route = "/api/v1/feed/for-you"
            else:
                route = "/api/v0/feed/recommended"

            if cursor:
                route += f"?cursor={urllib.parse.quote(str(cursor))}"

            try:
                payload = soup_json(
                    "GET",
                    f"https://{self.session['instance']}{route}",
                    token=self.session["access_token"],
                )
            except RuntimeError as route_err:
                if route.startswith("/api/v0/"):
                    payload = soup_json(
                        "GET",
                        f"https://{self.session['instance']}/api/v1/feed/for-you",
                        token=self.session["access_token"],
                    )
                else:
                    raise route_err

            meta = payload.get("meta") or {} if isinstance(payload, dict) else {}
            data = payload
            if isinstance(payload, dict):
                data = payload.get("data") or payload.get("items") or payload.get("videos") or []
            videos = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

            GLib.idle_add(self._feed_loaded, videos, None, meta.get("next_cursor"), append)
        except Exception as error:
            GLib.idle_add(self._feed_loaded, [], str(error), None, append)

    def _feed_loaded(self, videos, error, cursor, append):
        self.loading_more = False
        if append:
            self.videos.extend(videos)
        else:
            self.videos = videos
            self.video_index = 0

        if cursor:
            self.feed_cursor = cursor

        if error:
            self.feed_status.set_text(f"Erro ao carregar: {error}")
            self.feed_status.set_visible(True)
        elif not self.videos:
            self.feed_status.set_text("Nenhum vídeo encontrado.")
            self.feed_status.set_visible(True)
        else:
            self.feed_status.set_text("")
            self.feed_status.set_visible(False)

        self._show_video(self.video_index)

    def _set_feed_status(self, text):
        text = str(text or "")
        self.feed_status.set_text(text)
        self.feed_status.set_visible(bool(text))

    def _on_video_scroll(self, _controller, _dx, dy):
        if dy > 0:
            self._show_video(self.video_index + 1)
            return True
        elif dy < 0:
            self._show_video(self.video_index - 1)
            return True
        return False

    def _setup_keyboard_shortcuts(self):
        controller = Gtk.EventControllerKey.new()
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    def _on_key_pressed(self, _controller, keyval, _keycode, _state):
        if keyval == Gdk.KEY_space:
            self._toggle_play_pause()
            return True
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_Page_Down):
            self._show_video(self.video_index + 1)
            return True
        elif keyval in (Gdk.KEY_Up, Gdk.KEY_Page_Up):
            self._show_video(self.video_index - 1)
            return True
        elif keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self._toggle_video_sound()
            return True
        elif keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self._toggle_fullscreen()
            return True
        return False

    def _video_fields(self, item):
        if not isinstance(item, dict):
            return None, "", ""
        media = item.get("media") or item.get("video") or {}
        url = None
        if isinstance(media, dict):
            url = (
                media.get("src_url")
                or media.get("url")
                or media.get("playback_url")
                or media.get("video_url")
            )
        if not url:
            for att in item.get("media_attachments", []):
                if isinstance(att, dict) and att.get("type") in ("video", "gifv"):
                    url = att.get("url") or att.get("remote_url")
                    break

        if url and url.startswith("/"):
            inst = (self.session or {}).get("instance", "loops.video")
            url = f"https://{inst}{url}"

        author = item.get("account") or item.get("user") or {}
        name = author.get("display_name") or author.get("username") or author.get("acct") or "Loops"
        caption = item.get("caption") or item.get("description") or ""
        return url, name, caption

    def _show_video(self, index):
        if index < 0:
            return
        if self.videos and index >= len(self.videos):
            if self.feed_cursor and not self.loading_more:
                self.loading_more = True
                self.feed_status.set_text("Carregando mais vídeos…")
                self.feed_status.set_visible(True)
                threading.Thread(target=self._fetch_feed, args=(self.feed_cursor, True), daemon=True).start()
            return

        if not self.videos:
            self.player.set_playing(False)
            self._update_interaction_panel_empty()
            return

        self.video_index = min(index, len(self.videos) - 1)
        item = self.videos[self.video_index]
        url, author, caption = self._video_fields(item)

        if url:
            # Seamless URI transition (no widget recreation!)
            self.player.load_uri(url, autoplay=True)
            self.ctrl_play_btn.set_icon_name("media-playback-pause-symbolic")
            self.play_overlay_btn.set_visible(False)
        else:
            self.player.set_playing(False)
            self.play_overlay_btn.set_visible(False)

        self._update_interaction_panel(item, author)
        self._load_comments(item)

    def _toggle_play_pause(self):
        playing = self.player.toggle_playing()
        self.ctrl_play_btn.set_icon_name(
            "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        )
        self.play_overlay_btn.set_visible(not playing)

    def _toggle_video_sound(self):
        pref = load_preferences()
        muted = not pref.get("video_muted", False)
        pref["video_muted"] = muted
        save_preferences(pref)
        self.player.set_muted(muted)
        self.ctrl_volume_btn.set_icon_name(
            "audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic"
        )

    def _on_progress_tick(self):
        if not hasattr(self, "player") or not self.player or self.is_seeking:
            return True
        pos, dur = self.player.update_position_and_duration()
        if dur > 0:
            self.progress_scale.set_range(0, dur)
            self.progress_scale.set_value(pos)
            self.lbl_time_pos.set_text(self._format_seconds(pos))
            self.lbl_time_dur.set_text(self._format_seconds(dur))
        return True

    def _on_scale_change_value(self, _scale, _scroll, value):
        self.is_seeking = True
        self.player.seek(value)
        self.is_seeking = False
        return False

    def _format_seconds(self, s):
        mins = int(s) // 60
        secs = int(s) % 60
        return f"{mins}:{secs:02d}"

    def _toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
            self.ctrl_fullscreen_btn.set_icon_name("view-fullscreen-symbolic")
        else:
            self.fullscreen()
            self.ctrl_fullscreen_btn.set_icon_name("view-restore-symbolic")

    def _download_current_video(self):
        if not self.videos or self.video_index >= len(self.videos):
            return
        item = self.videos[self.video_index]
        url, _author, _caption = self._video_fields(item)
        if not url:
            return

        download_dir = Path(
            GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or (Path.home() / "Downloads")
        )
        download_dir.mkdir(parents=True, exist_ok=True)
        vid_id = item.get("id") or secrets.token_hex(4)
        target = download_dir / f"loops_{vid_id}.mp4"

        def download():
            try:
                GLib.idle_add(self._set_feed_status, "Baixando vídeo…")
                session = Soup.Session.new()
                session.set_user_agent(USER_AGENT)
                msg = Soup.Message.new("GET", url)
                res = session.send_and_read(msg, None)
                if msg.get_status() == 200 and res:
                    target.write_bytes(res.get_data())
                    GLib.idle_add(self._set_feed_status, f"Vídeo salvo em Downloads/loops_{vid_id}.mp4")
                else:
                    GLib.idle_add(self._set_feed_status, f"Falha ao baixar vídeo: HTTP {msg.get_status()}")
            except Exception as e:
                GLib.idle_add(self._set_feed_status, f"Erro no download: {e}")

        threading.Thread(target=download, daemon=True).start()

    # --------------------------------------------------------------------------
    # Right Panel: Interactions & Comments
    # --------------------------------------------------------------------------

    def _update_interaction_panel(self, item, author_name):
        has_liked = bool(item.get("has_liked"))
        has_bookmarked = bool(item.get("has_bookmarked"))
        author = item.get("account") or item.get("user") or {}
        is_following = bool(author.get("is_following"))

        self.btn_like.set_label("♥ Liked" if has_liked else "♡ Like")
        if has_liked:
            self.btn_like.add_css_class("active-like")
        else:
            self.btn_like.remove_css_class("active-like")

        self.btn_save.set_label("★ Saved" if has_bookmarked else "☆ Save")
        if has_bookmarked:
            self.btn_save.add_css_class("active-save")
        else:
            self.btn_save.remove_css_class("active-save")

        self.btn_subscribe.set_label("✓ Subbed" if is_following else "🔔 Sub")
        if is_following:
            self.btn_subscribe.add_css_class("active-sub")
        else:
            self.btn_subscribe.remove_css_class("active-sub")

    def _update_interaction_panel_empty(self):
        self.btn_like.set_sensitive(False)
        self.btn_share.set_sensitive(False)
        self.btn_subscribe.set_sensitive(False)
        self.btn_profile.set_sensitive(False)
        self.btn_save.set_sensitive(False)
        self._clear_comments_box()

    def _toggle_like(self, *_args):
        if not self.videos or self.video_index >= len(self.videos):
            return
        item = self.videos[self.video_index]
        liked = not bool(item.get("has_liked"))
        action = "like" if liked else "unlike"
        item["has_liked"] = liked
        self._update_interaction_panel(item, "")

        def send():
            try:
                soup_json(
                    "POST",
                    f"https://{self.session['instance']}/api/v1/video/{action}/{urllib.parse.quote(str(item['id']))}",
                    token=self.session["access_token"],
                    form={},
                )
            except Exception as e:
                print(f"Error toggling like: {e}")

        threading.Thread(target=send, daemon=True).start()

    def _toggle_bookmark(self, *_args):
        if not self.videos or self.video_index >= len(self.videos):
            return
        item = self.videos[self.video_index]
        saved = not bool(item.get("has_bookmarked"))
        action = "bookmark" if saved else "unbookmark"
        item["has_bookmarked"] = saved
        self._update_interaction_panel(item, "")

        def send():
            try:
                soup_json(
                    "POST",
                    f"https://{self.session['instance']}/api/v1/video/{action}/{urllib.parse.quote(str(item['id']))}",
                    token=self.session["access_token"],
                    form={},
                )
            except Exception as e:
                print(f"Error toggling bookmark: {e}")

        threading.Thread(target=send, daemon=True).start()

    def _toggle_author_follow(self, *_args):
        if not self.videos or self.video_index >= len(self.videos):
            return
        item = self.videos[self.video_index]
        author = item.get("account") or item.get("user") or {}
        author_id = author.get("id")
        if not author_id:
            return
        is_following = bool(author.get("is_following"))
        action = "unfollow" if is_following else "follow"
        author["is_following"] = not is_following
        self._update_interaction_panel(item, "")

        def send():
            try:
                soup_json(
                    "POST",
                    f"https://{self.session['instance']}/api/v1/account/{action}/{urllib.parse.quote(str(author_id))}",
                    token=self.session["access_token"],
                )
            except Exception as e:
                print(f"Error following author: {e}")

        threading.Thread(target=send, daemon=True).start()

    def _share_video(self, *_args):
        if not self.videos or self.video_index >= len(self.videos):
            return
        item = self.videos[self.video_index]
        inst = (self.session or {}).get("instance", "loops.video")
        url = item.get("url") or f"https://{inst}/p/{item.get('id')}"
        self._copy_to_clipboard(url)
        self.feed_status.set_text("Link copiado para a área de transferência!")
        self.feed_status.set_visible(True)

    def _copy_to_clipboard(self, text):
        display = Gdk.Display.get_default()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(text)

    def _open_current_author_profile(self, *_args):
        if not self.videos or self.video_index >= len(self.videos):
            return
        item = self.videos[self.video_index]
        author = item.get("account") or item.get("user") or {}
        author_id = author.get("id")
        if author_id:
            self._open_profile_view(account_id=author_id)

    # --------------------------------------------------------------------------
    # Comments Engine with Like & Avatar Support
    # --------------------------------------------------------------------------

    def _clear_comments_box(self):
        child = self.comments_box.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.comments_box.remove(child)
            child = following

    def _load_comments(self, item):
        self._clear_comments_box()
        loading_lbl = Gtk.Label(label="Carregando comentários…", wrap=True)
        loading_lbl.add_css_class("dim-label")
        self.comments_box.append(loading_lbl)
        threading.Thread(target=self._fetch_comments_worker, args=(item,), daemon=True).start()

    def _fetch_comments_worker(self, item):
        video_id = item.get("id")
        try:
            payload = soup_json(
                "GET",
                f"https://{self.session['instance']}/api/v1/video/comments/{urllib.parse.quote(str(video_id))}",
                token=self.session["access_token"],
            )
            comments = payload
            if isinstance(payload, dict):
                comments = payload.get("data") or payload.get("comments") or payload.get("items") or []
            GLib.idle_add(self._render_comments, item, comments, None)
        except Exception as error:
            GLib.idle_add(self._render_comments, item, [], str(error))

    def _render_comments(self, item, comments, error):
        self._clear_comments_box()
        if error:
            lbl = Gtk.Label(label=f"Não foi possível carregar comentários: {error}", wrap=True)
            self.comments_box.append(lbl)
            return

        if not comments:
            empty_lbl = Gtk.Label(label="Nenhum comentário ainda. Seja o primeiro!", wrap=True)
            empty_lbl.add_css_class("dim-label")
            empty_lbl.set_margin_top(16)
            self.comments_box.append(empty_lbl)
            return

        video_id = str(item.get("id"))
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            card.add_css_class("comment-card")
            card.set_margin_bottom(4)

            acc = comment.get("account") or comment.get("user") or {}
            avatar_url = acc.get("avatar")
            author_id = acc.get("id")
            author_name = acc.get("display_name") or acc.get("username") or "Usuário"

            # ── Clickable avatar button (42px circle) ──────────────────────────
            avatar_btn = Gtk.Button()
            avatar_btn.add_css_class("comment-avatar-btn")
            avatar_btn.set_size_request(42, 42)
            avatar_btn.set_halign(Gtk.Align.START)
            avatar_btn.set_valign(Gtk.Align.START)
            avatar_btn.set_hexpand(False)
            avatar_btn.set_vexpand(False)
            avatar_btn.set_overflow(Gtk.Overflow.HIDDEN)
            avatar_img = IMAGE_CACHE.make_avatar(avatar_url, size=42)
            avatar_btn.set_child(avatar_img)
            if author_id:
                avatar_btn.connect(
                    "clicked",
                    lambda _b, aid=author_id: self._open_profile_view(aid),
                )
            card.append(avatar_btn)

            # ── Center: bold username button + comment text ────────────────────
            details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            details_box.set_hexpand(True)

            name_btn = Gtk.Button(label=author_name)
            name_btn.add_css_class("comment-author-btn")
            name_btn.set_halign(Gtk.Align.START)
            if author_id:
                name_btn.connect(
                    "clicked",
                    lambda _b, aid=author_id: self._open_profile_view(aid),
                )
            details_box.append(name_btn)

            # Comment text
            text = (
                comment.get("caption")
                or comment.get("comment")
                or comment.get("content")
                or comment.get("text")
                or ""
            )
            if text:
                lbl_text = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
                lbl_text.add_css_class("comment-text")
                details_box.append(lbl_text)

            # GIF/media attachment (klipy)
            media_list = comment.get("media") or []
            if media_list and isinstance(media_list, list):
                m = media_list[0]
                gif_url = m.get("url") or ""
                if gif_url:
                    gif_pic = IMAGE_CACHE.make_thumbnail(gif_url, width=140, height=140)
                    gif_frame = Gtk.AspectFrame()
                    gif_frame.set_ratio(1.0)
                    gif_frame.set_obey_child(False)
                    gif_frame.set_size_request(140, 140)
                    gif_frame.set_child(gif_pic)
                    gif_frame.set_margin_top(4)
                    details_box.append(gif_frame)

            card.append(details_box)

            # ── Right: like button ─────────────────────────────────────────────
            comment_id = str(comment.get("id", ""))
            is_liked = bool(comment.get("liked") or comment.get("has_liked"))
            likes_count = int(comment.get("likes") or comment.get("likes_count") or 0)

            like_btn = Gtk.Button()
            like_btn.add_css_class("comment-like-btn")
            like_btn.set_valign(Gtk.Align.CENTER)
            self._update_comment_like_button(like_btn, is_liked, likes_count)

            like_btn.connect(
                "clicked",
                lambda _btn, cid=comment_id, c=comment, b=like_btn: self._toggle_comment_like(video_id, cid, c, b),
            )
            card.append(like_btn)

            self.comments_box.append(card)

    def _update_comment_like_button(self, button, liked, count):
        label_text = f"♥ {count}" if liked else (f"♡ {count}" if count > 0 else "♡")
        button.set_label(label_text)
        if liked:
            button.add_css_class("liked")
        else:
            button.remove_css_class("liked")

    def _toggle_comment_like(self, video_id, comment_id, comment_obj, button):
        is_liked = bool(comment_obj.get("liked") or comment_obj.get("has_liked"))
        new_liked = not is_liked
        comment_obj["liked"] = new_liked
        comment_obj["has_liked"] = new_liked

        count = int(comment_obj.get("likes") or comment_obj.get("likes_count") or 0)
        new_count = count + 1 if new_liked else max(0, count - 1)
        comment_obj["likes"] = new_count
        self._update_comment_like_button(button, new_liked, new_count)

        action = "like" if new_liked else "unlike"
        def worker():
            try:
                soup_json(
                    "POST",
                    f"https://{self.session['instance']}/api/v1/comments/{action}/{urllib.parse.quote(video_id)}/{urllib.parse.quote(comment_id)}",
                    token=self.session["access_token"],
                    form={},
                )
            except Exception as e:
                print(f"Error toggling comment like: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _post_comment(self):
        text = self.comment_entry.get_text().strip()
        if not text or not self.videos or self.video_index >= len(self.videos):
            return
        item = self.videos[self.video_index]
        self.comment_entry.set_text("")
        self.comment_entry.set_sensitive(False)

        def send():
            try:
                soup_json(
                    "POST",
                    f"https://{self.session['instance']}/api/v1/video/comments/{urllib.parse.quote(str(item['id']))}",
                    token=self.session["access_token"],
                    json_body={"comment": text},
                )
                self._fetch_comments_worker(item)
            except Exception as e:
                print(f"Error posting comment: {e}")
            finally:
                GLib.idle_add(self.comment_entry.set_sensitive, True)

        threading.Thread(target=send, daemon=True).start()

    def _open_gif_picker(self):
        """Klipy GIF picker — search uses ?q=, clicking a GIF sends it directly as a comment."""
        if not self.videos or self.video_index >= len(self.videos):
            return
        item = self.videos[self.video_index]
        video_id = str(item.get("id"))

        dialog = Gtk.Dialog(title="Escolher GIF", transient_for=self, modal=True)
        dialog.set_default_size(480, 560)
        dialog.add_button("Fechar", Gtk.ResponseType.CLOSE)
        area = dialog.get_content_area()
        area.set_spacing(10)

        search_entry = Gtk.SearchEntry(placeholder_text="Buscar GIFs…")
        area.append(search_entry)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_min_children_per_line(2)
        flow.set_max_children_per_line(4)
        flow.set_homogeneous(True)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll.set_child(flow)
        area.append(scroll)

        status = Gtk.Label(label="Carregando GIFs do Klipy…", wrap=True)
        area.append(status)

        dialog.connect("response", lambda d, _r: d.destroy())
        dialog.present()

        # Debounce timer id
        self._gif_search_timer = None

        def fetch(query=None):
            try:
                if query:
                    url = (
                        f"https://{self.session['instance']}/api/v1/klipy/gifs/search"
                        f"?q={urllib.parse.quote(query)}&type=gifs"
                    )
                else:
                    url = f"https://{self.session['instance']}/api/v1/klipy/gifs/trending"

                payload = soup_json("GET", url, token=self.session["access_token"])
                # API returns {"items": [...]} per the TypeScript types
                items = payload.get("items") or payload.get("data") or []
                GLib.idle_add(self._render_gif_results, flow, items, dialog, status, video_id)
            except Exception as e:
                GLib.idle_add(status.set_text, f"Falha ao carregar GIFs: {e}")

        def on_search_changed(entry):
            if self._gif_search_timer:
                GLib.source_remove(self._gif_search_timer)
            q = entry.get_text().strip()
            self._gif_search_timer = GLib.timeout_add(
                400,
                lambda: (threading.Thread(target=fetch, args=(q,), daemon=True).start(), False)[1],
            )

        search_entry.connect("search-changed", on_search_changed)
        search_entry.connect("activate", lambda e: fetch(e.get_text().strip()))
        threading.Thread(target=fetch, daemon=True).start()

    def _render_gif_results(self, flow, items, dialog, status, video_id):
        child = flow.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            flow.remove(child)
            child = nxt

        if not items:
            status.set_text("Nenhum GIF encontrado.")
            return
        status.set_text(f"{len(items)} GIFs disponíveis")

        for gif in items:
            btn = Gtk.Button()
            btn.add_css_class("flat")

            preview_obj = gif.get("preview") or {}
            preview_url = preview_obj.get("url") or gif.get("url") or ""
            thumb = IMAGE_CACHE.make_thumbnail(preview_url, width=140, height=140)
            frame = Gtk.AspectFrame()
            frame.set_ratio(1.0)
            frame.set_obey_child(False)
            thumb.set_hexpand(True)
            thumb.set_vexpand(True)
            frame.set_child(thumb)
            btn.set_child(frame)

            # Send GIF directly as a comment when clicked (no URL text insertion)
            btn.connect(
                "clicked",
                lambda _b, g=gif, d=dialog, vid=video_id: self._send_gif_comment(g, d, vid),
            )
            flow.append(btn)

    def _send_gif_comment(self, gif, dialog, video_id):
        """POST the GIF item directly as a media comment, then close the dialog."""
        dialog.destroy()

        # Build the item payload matching commentPostMedia in requests.ts
        full_obj   = gif.get("full") or {}
        mp4_obj    = gif.get("mp4") or {}
        webm_obj   = gif.get("webm") or {}
        preview_obj = gif.get("preview") or {}

        item_payload = {
            "id":      gif.get("id"),
            "slug":    gif.get("slug") or "",
            "title":   gif.get("title") or "",
            "width":   gif.get("width") or 0,
            "height":  gif.get("height") or 0,
            "full":    {"url": full_obj.get("url") or preview_obj.get("url") or ""},
            "mp4":     {"url": mp4_obj.get("url") or ""},
            "webm":    {"url": webm_obj.get("url") or ""},
            "preview": {"url": preview_obj.get("url") or ""},
        }

        def send():
            try:
                soup_json(
                    "POST",
                    f"https://{self.session['instance']}/api/v1/video/comments/{urllib.parse.quote(video_id)}/media",
                    token=self.session["access_token"],
                    json_body={
                        "comment": None,
                        "type": "gifs",
                        "item": item_payload,
                    },
                )
            except Exception as e:
                print(f"Error sending GIF comment (media endpoint): {e}")
                # Fallback: post the preview URL as plain text
                try:
                    fallback_url = preview_obj.get("url") or full_obj.get("url") or ""
                    if fallback_url:
                        soup_json(
                            "POST",
                            f"https://{self.session['instance']}/api/v1/video/comments/{urllib.parse.quote(video_id)}",
                            token=self.session["access_token"],
                            json_body={"comment": fallback_url},
                        )
                except Exception as e2:
                    print(f"Error sending GIF fallback comment: {e2}")
            finally:
                # Reload comments for current video
                if self.videos and self.video_index < len(self.videos):
                    self._fetch_comments_worker(self.videos[self.video_index])

        threading.Thread(target=send, daemon=True).start()

    # --------------------------------------------------------------------------
    # Subpage: Comprehensive Profile View (Self and Creators)
    # --------------------------------------------------------------------------

    def _open_profile_view(self, account_id=None):
        page_name = f"profile_{account_id or 'self'}"
        # Reuse an existing profile page. Gtk.Stack child names must be unique;
        # adding another child with the same name leaves navigation unreliable.
        if self.content_stack.get_child_by_name(page_name) is not None:
            self._show_page(page_name)
            if account_id is None or str(account_id) == str((self.session or {}).get("account", {}).get("id")):
                self._set_active_dock_button("Profile")
            return
        is_self = (
            account_id is None
            or str(account_id) == str((self.session or {}).get("account", {}).get("id"))
        )

        # ── Outer container (no Adw.ToolbarView = no duplicate window buttons) ──
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # ── Top nav bar ──────────────────────────────────────────────────────────
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav.add_css_class("subpage-nav")
        nav.set_margin_start(12)
        nav.set_margin_end(12)
        nav.set_margin_top(10)
        nav.set_margin_bottom(10)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Voltar")
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self._show_feed_page())
        title_nav = Gtk.Label(label="Meu Perfil" if is_self else "Perfil")
        title_nav.add_css_class("title-4")
        title_nav.set_hexpand(True)
        title_nav.set_xalign(0)
        nav.append(back)
        nav.append(title_nav)
        page.append(nav)
        page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Scrollable body ──────────────────────────────────────────────────────
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.set_child(body)
        page.append(scroll)

        # ── Banner ───────────────────────────────────────────────────────────────
        banner_box = Gtk.Box()
        banner_box.add_css_class("profile-banner")
        banner_box.set_size_request(-1, 160)
        banner_pic = Gtk.Picture()
        banner_pic.set_can_shrink(True)
        banner_pic.set_content_fit(Gtk.ContentFit.COVER)
        banner_pic.set_hexpand(True)
        banner_box.append(banner_pic)
        body.append(banner_box)

        # ── Header info (avatar centered over banner, name, username, bio) ───────
        header_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header_section.add_css_class("profile-header-section")
        header_section.set_halign(Gtk.Align.CENTER)
        header_section.set_margin_top(-48)   # overlap with banner
        header_section.set_margin_bottom(12)
        header_section.set_margin_start(24)
        header_section.set_margin_end(24)
        body.append(header_section)

        # Avatar (large, circular)
        avatar_box = Gtk.Box()
        avatar_box.set_halign(Gtk.Align.CENTER)
        avatar_box.add_css_class("profile-avatar-ring")
        header_section.append(avatar_box)

        # Display name
        lbl_name = Gtk.Label(label="Carregando…", xalign=0.5)
        lbl_name.add_css_class("profile-name")
        header_section.append(lbl_name)

        # @username
        lbl_username = Gtk.Label(label="", xalign=0.5)
        lbl_username.add_css_class("profile-username")
        header_section.append(lbl_username)

        # Bio
        lbl_bio = Gtk.Label(label="", xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER)
        lbl_bio.add_css_class("profile-bio")
        lbl_bio.set_max_width_chars(60)
        header_section.append(lbl_bio)

        # Website link
        lbl_website = Gtk.Label(label="", xalign=0.5, use_markup=True)
        lbl_website.add_css_class("profile-website")
        header_section.append(lbl_website)

        # ── Stats row ────────────────────────────────────────────────────────────
        stats_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        stats_row.set_halign(Gtk.Align.CENTER)
        stats_row.set_margin_top(8)
        stats_row.set_margin_bottom(8)

        lbl_videos_stat  = Gtk.Label(label="0 vídeos")
        lbl_followers_stat = Gtk.Label(label="0 seguidores")
        lbl_likes_stat   = Gtk.Label(label="0 curtidas")

        dot1 = Gtk.Label(label="•")
        dot2 = Gtk.Label(label="•")
        dot1.add_css_class("dim-label")
        dot2.add_css_class("dim-label")

        for w in (lbl_videos_stat, dot1, lbl_followers_stat, dot2, lbl_likes_stat):
            w.add_css_class("profile-stat-text")
            stats_row.append(w)

        body.append(stats_row)

        # ── Action buttons (Seguir / Mensagem) — only for other users ────────────
        if not is_self:
            actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            actions_row.set_halign(Gtk.Align.CENTER)
            actions_row.set_margin_bottom(8)

            follow_btn = Gtk.Button(label="Seguir")
            follow_btn.add_css_class("suggested-action")
            follow_btn.add_css_class("profile-action-btn")
            follow_btn.set_sensitive(False)

            msg_btn = Gtk.Button(label="Enviar Mensagem")
            msg_btn.add_css_class("profile-action-btn")
            msg_btn.connect("clicked", lambda *_: self._open_direct_message(account_id))

            actions_row.append(follow_btn)
            actions_row.append(msg_btn)
            body.append(actions_row)
        else:
            follow_btn = None
            msg_btn = None

        # ── Tab bar ──────────────────────────────────────────────────────────────
        tab_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        body.append(tab_sep)

        tab_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tab_bar.add_css_class("profile-tab-bar")
        body.append(tab_bar)

        tab_sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        body.append(tab_sep2)

        # Tab content (FlowBox for each tab)
        tabs_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        tabs_stack.set_hexpand(True)
        # Keep the whole profile in one scroll area so scrolling naturally
        # moves from the header and tabs down through every video row.
        tabs_stack.set_vexpand(False)
        tabs_stack.set_valign(Gtk.Align.START)
        body.append(tabs_stack)

        # Create tab pages
        def make_flow():
            flow = Gtk.FlowBox()
            flow.set_valign(Gtk.Align.START)
            flow.set_min_children_per_line(3)
            flow.set_max_children_per_line(6)
            flow.set_homogeneous(True)
            flow.set_column_spacing(6)
            flow.set_row_spacing(6)
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_margin_start(16)
            flow.set_margin_end(16)
            flow.set_margin_top(16)
            flow.set_margin_bottom(16)
            return flow

        if is_self:
            tab_defs = [("Vídeos", "videos"), ("Curtidas", "liked")]
        else:
            tab_defs = [("Vídeos", "videos"), ("Populares", "popular")]

        flows = {}
        tab_btns = []
        for tab_label, tab_key in tab_defs:
            flow = make_flow()
            flows[tab_key] = flow
            tabs_stack.add_named(flow, tab_key)

            tb = Gtk.Button(label=tab_label.upper())
            tb.add_css_class("profile-tab-btn")
            tab_bar.append(tb)
            tab_btns.append((tb, tab_key))

        # tab switching logic
        def switch_tab(key):
            tabs_stack.set_visible_child_name(key)
            for b, k in tab_btns:
                if k == key:
                    b.add_css_class("active")
                else:
                    b.remove_css_class("active")

        for btn, key in tab_btns:
            btn.connect("clicked", lambda _b, k=key: switch_tab(k))

        # Activate first tab
        first_key = tab_defs[0][1]
        tabs_stack.set_visible_child_name(first_key)
        tab_btns[0][0].add_css_class("active")

        profile_route = (
            "/api/v1/feed/account/self"
            if is_self
            else f"/api/v1/feed/account/{urllib.parse.quote(str(account_id))}"
        )
        profile_pagination = {"cursor": None, "loading": False}

        def profile_page_loaded(videos_page, next_cursor, error):
            profile_pagination["loading"] = False
            if error:
                print(f"Error loading more profile videos: {error}")
                return
            profile_pagination["cursor"] = next_cursor
            self._append_profile_video_cards(videos_page, flows.get("videos"))
            GLib.idle_add(profile_pagination["load_more"], scroll.get_vadjustment())

        def load_profile_page(adjustment):
            if (
                profile_pagination["loading"]
                or not profile_pagination["cursor"]
                or tabs_stack.get_visible_child_name() != "videos"
            ):
                return
            remaining = adjustment.get_upper() - (adjustment.get_value() + adjustment.get_page_size())
            if remaining >= 320:
                return
            profile_pagination["loading"] = True
            cursor = profile_pagination["cursor"]

            def worker():
                try:
                    url = f"https://{self.session['instance']}{profile_route}?cursor={urllib.parse.quote(str(cursor))}"
                    payload = soup_json("GET", url, token=self.session["access_token"])
                    videos_page, next_cursor = payload_items_and_cursor(payload)
                    GLib.idle_add(profile_page_loaded, videos_page, next_cursor, None)
                except Exception as error:
                    GLib.idle_add(profile_page_loaded, [], None, str(error))

            threading.Thread(target=worker, daemon=True).start()

        profile_adjustment = scroll.get_vadjustment()
        profile_pagination["load_more"] = load_profile_page
        profile_pagination["adjustment"] = profile_adjustment
        profile_adjustment.connect("value-changed", load_profile_page)
        profile_adjustment.connect("changed", load_profile_page)

        # Register page
        self.content_stack.add_named(page, page_name)
        self._show_page(page_name)
        if is_self:
            self._set_active_dock_button("Profile")

        # ── Fetch data in background ─────────────────────────────────────────────
        def fetch_profile():
            try:
                if is_self:
                    acc_info = soup_json(
                        "GET",
                        f"https://{self.session['instance']}/api/v1/account/info/self",
                        token=self.session["access_token"],
                    )
                    vids_payload = soup_json(
                        "GET",
                        f"https://{self.session['instance']}/api/v1/feed/account/self",
                        token=self.session["access_token"],
                    )
                    # The API exposes the signed-in account's liked videos here.
                    try:
                        liked_payload = soup_json(
                            "GET",
                            f"https://{self.session['instance']}/api/v1/account/videos/likes",
                            token=self.session["access_token"],
                        )
                        liked_videos = liked_payload.get("data") or []
                    except Exception:
                        liked_videos = []
                    saved_videos = []
                else:
                    acc_info = soup_json(
                        "GET",
                        f"https://{self.session['instance']}/api/v1/account/info/{account_id}",
                        token=self.session["access_token"],
                    )
                    vids_payload = soup_json(
                        "GET",
                        f"https://{self.session['instance']}/api/v1/feed/account/{account_id}",
                        token=self.session["access_token"],
                    )
                    liked_videos = []
                    saved_videos = []

                user_data = acc_info.get("data") or acc_info
                videos, video_cursor = payload_items_and_cursor(vids_payload)

                GLib.idle_add(
                    self._populate_profile_view,
                    user_data,
                    videos,
                    liked_videos,
                    saved_videos,
                    is_self,
                    avatar_box,
                    banner_pic,
                    lbl_name,
                    lbl_username,
                    lbl_bio,
                    lbl_website,
                    lbl_videos_stat,
                    lbl_followers_stat,
                    lbl_likes_stat,
                    flows,
                    follow_btn,
                    account_id,
                    video_cursor,
                    profile_pagination,
                )
            except Exception as e:
                print(f"Error fetching profile: {e}")

        threading.Thread(target=fetch_profile, daemon=True).start()

    def _make_video_grid_card(self, vid):
        """Build a uniform video card matching the mockup: thumbnail with play icon + duration badge + title + views."""
        media = vid.get("media") or {}
        thumb_url = media.get("thumbnail") or vid.get("thumbnail") or ""
        caption = vid.get("caption") or ""
        views = vid.get("plays_count") or vid.get("views") or vid.get("view_count") or 0
        duration = media.get("duration") or vid.get("duration") or 0

        def fmt_views(n):
            n = int(n)
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n / 1_000:.0f}K"
            return str(n)

        def fmt_dur(secs):
            try:
                secs = int(float(secs))
                return f"{secs // 60}:{secs % 60:02d}"
            except Exception:
                return ""

        # Outer wrapper
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_size_request(155, -1)
        outer.set_margin_start(2)
        outer.set_margin_end(2)

        # Thumbnail with overlay
        overlay = Gtk.Overlay()
        thumb = IMAGE_CACHE.make_thumbnail(thumb_url, width=155, height=155)
        thumb.add_css_class("profile-video-thumb")
        thumb.set_hexpand(True)
        thumb.set_vexpand(True)
        overlay.set_child(thumb)
        square_frame = Gtk.AspectFrame()
        square_frame.set_ratio(1.0)
        square_frame.set_obey_child(False)
        square_frame.set_child(overlay)

        # Play icon overlay
        play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
        play_icon.set_pixel_size(28)
        play_icon.set_halign(Gtk.Align.CENTER)
        play_icon.set_valign(Gtk.Align.CENTER)
        play_icon.add_css_class("video-play-overlay-icon")
        overlay.add_overlay(play_icon)

        # Duration badge (bottom-right)
        dur_str = fmt_dur(duration)
        if dur_str:
            dur_lbl = Gtk.Label(label=dur_str)
            dur_lbl.add_css_class("video-duration-badge")
            dur_lbl.set_halign(Gtk.Align.END)
            dur_lbl.set_valign(Gtk.Align.END)
            dur_lbl.set_margin_end(6)
            dur_lbl.set_margin_bottom(6)
            overlay.add_overlay(dur_lbl)

        outer.append(square_frame)

        # Title
        cap_lbl = Gtk.Label(label=caption[:40] + ("…" if len(caption) > 40 else ""), xalign=0.5, wrap=True, justify=Gtk.Justification.CENTER)
        cap_lbl.add_css_class("video-grid-title")
        cap_lbl.set_halign(Gtk.Align.CENTER)
        cap_lbl.set_justify(Gtk.Justification.CENTER)
        cap_lbl.set_max_width_chars(22)
        outer.append(cap_lbl)

        # Views
        views_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        views_box.set_halign(Gtk.Align.CENTER)
        eye_icon = Gtk.Image.new_from_icon_name("camera-video-symbolic")
        eye_icon.set_pixel_size(12)
        eye_icon.add_css_class("dim-label")
        views_lbl = Gtk.Label(label=fmt_views(views), xalign=0)
        views_lbl.add_css_class("video-grid-views")
        views_box.append(eye_icon)
        views_box.append(views_lbl)
        outer.append(views_box)

        return outer

    def _append_profile_video_cards(self, videos, flow):
        if flow is None:
            return
        for vid in videos:
            if not isinstance(vid, dict):
                continue
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("video-grid-btn")
            btn.set_child(self._make_video_grid_card(vid))
            btn.connect("clicked", lambda _b, it=vid: self._open_list_video(it))
            flow.append(btn)

    def _populate_profile_view(
        self, user_data, videos, liked_videos, saved_videos, is_self,
        avatar_box, banner_pic, lbl_name, lbl_username, lbl_bio, lbl_website,
        lbl_videos_stat, lbl_followers_stat, lbl_likes_stat,
        flows, follow_btn, account_id, video_cursor=None, profile_pagination=None,
    ):
        # Banner
        banner_url = user_data.get("header") or user_data.get("banner") or ""
        if banner_url:
            IMAGE_CACHE.load_into(banner_url, banner_pic)

        # Avatar (96px)
        avatar_url = user_data.get("avatar") or ""
        child = avatar_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            avatar_box.remove(child)
            child = nxt
        avatar_box.append(IMAGE_CACHE.make_avatar(avatar_url, size=96))

        # Text fields
        name = user_data.get("display_name") or user_data.get("username") or "Usuário"
        username = user_data.get("username") or user_data.get("acct") or ""
        bio = user_data.get("note") or user_data.get("bio") or ""
        # Strip HTML tags from bio (Mastodon returns HTML)
        import re as _re
        bio = _re.sub(r"<[^>]+>", " ", bio).strip()
        website = user_data.get("website") or user_data.get("url") or ""
        links = user_data.get("links") or []
        if not website and links:
            first_link = links[0] if isinstance(links[0], dict) else {}
            website = first_link.get("link") or first_link.get("url") or ""

        lbl_name.set_text(name)
        lbl_username.set_text(f"@{username}")
        lbl_bio.set_text(bio)

        if website:
            short = website.replace("https://", "").replace("http://", "").rstrip("/")
            lbl_website.set_markup(f'<a href="{website}">🔗 {short}</a>')
            lbl_website.set_visible(True)
        else:
            lbl_website.set_visible(False)

        # Account info uses the same count fields consumed by the mobile client.
        v_count = user_data.get("post_count") or user_data.get("statuses_count") or user_data.get("loops_count") or len(videos)
        f_count = user_data.get("follower_count") or user_data.get("followers_count") or 0
        l_count = user_data.get("likes_count") or user_data.get("liked_count") or 0

        def fmt_stat(n):
            n = int(n)
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n / 1_000:.1f}K"
            return str(n)

        lbl_videos_stat.set_text(f"{fmt_stat(v_count)} vídeos")
        lbl_followers_stat.set_text(f"{fmt_stat(f_count)} seguidores")
        lbl_likes_stat.set_text(f"{fmt_stat(l_count)} curtidas")

        # Follow button wiring
        if follow_btn and account_id:
            is_following = bool(user_data.get("is_following") or user_data.get("following"))
            follow_btn.set_label("Seguindo" if is_following else "Seguir")
            follow_btn.set_sensitive(True)
            acc_ref = {"id": account_id, "is_following": is_following}
            follow_btn.connect(
                "clicked",
                lambda _b, a=acc_ref, b=follow_btn: self._toggle_follow_account(a, b),
            )

        # Fill video tab flows and preserve the cursor for scroll pagination.
        self._append_profile_video_cards(videos, flows.get("videos"))
        if profile_pagination is not None:
            profile_pagination["cursor"] = video_cursor
            GLib.idle_add(profile_pagination["load_more"], profile_pagination["adjustment"])

        # Liked tab
        if "liked" in flows:
            for vid in liked_videos:
                if not isinstance(vid, dict):
                    continue
                card = self._make_video_grid_card(vid)
                btn = Gtk.Button()
                btn.add_css_class("flat")
                btn.add_css_class("video-grid-btn")
                btn.set_child(card)
                btn.connect("clicked", lambda _b, it=vid: self._open_list_video(it))
                flows["liked"].append(btn)

        # Saved tab
        if "saved" in flows:
            for vid in saved_videos:
                if not isinstance(vid, dict):
                    continue
                card = self._make_video_grid_card(vid)
                btn = Gtk.Button()
                btn.add_css_class("flat")
                btn.add_css_class("video-grid-btn")
                btn.set_child(card)
                btn.connect("clicked", lambda _b, it=vid: self._open_list_video(it))
                flows["saved"].append(btn)

        # Popular tab (other users): re-sort by likes
        if "popular" in flows:
            sorted_vids = sorted(
                [v for v in videos if isinstance(v, dict)],
                key=lambda v: int(v.get("likes_count") or v.get("likes") or 0),
                reverse=True,
            )
            for vid in sorted_vids:
                card = self._make_video_grid_card(vid)
                btn = Gtk.Button()
                btn.add_css_class("flat")
                btn.add_css_class("video-grid-btn")
                btn.set_child(card)
                btn.connect("clicked", lambda _b, it=vid: self._open_list_video(it))
                flows["popular"].append(btn)



    # --------------------------------------------------------------------------
    # Subpage: Rich Explore View (Creators, Hashtags, Videos)
    # --------------------------------------------------------------------------

    def _open_explore_page(self):
        page = self._make_subpage("Explorar · Criadores, tags e vídeos em alta")

        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        container.set_margin_start(24)
        container.set_margin_end(24)
        container.set_margin_top(16)
        container.set_margin_bottom(24)
        scroll.set_child(container)
        page.append(scroll)

        # Section 1: Suggested Creators
        lbl_creators = Gtk.Label(label="Perfis sugeridos para você", xalign=0)
        lbl_creators.add_css_class("heading")
        container.append(lbl_creators)

        creators_scroll = Gtk.ScrolledWindow()
        creators_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        creators_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        creators_scroll.set_child(creators_box)
        container.append(creators_scroll)

        # Section 2: Featured Hashtags
        lbl_tags = Gtk.Label(label="Hashtags em destaque", xalign=0)
        lbl_tags.add_css_class("heading")
        container.append(lbl_tags)

        tags_flow = Gtk.FlowBox()
        tags_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        tags_flow.set_max_children_per_line(8)
        container.append(tags_flow)

        # Section 3: Trending Videos Grid
        lbl_vids = Gtk.Label(label="Vídeos em alta", xalign=0)
        lbl_vids.add_css_class("heading")
        container.append(lbl_vids)

        vids_flow = Gtk.FlowBox()
        vids_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        vids_flow.set_min_children_per_line(3)
        vids_flow.set_max_children_per_line(6)
        vids_flow.set_homogeneous(True)
        container.append(vids_flow)
        vids_status = Gtk.Label(label="Carregando vídeos…", xalign=0)
        vids_status.add_css_class("dim-label")
        container.append(vids_status)

        self.explore_flow = vids_flow
        self.explore_status = vids_status
        self.explore_scroll = scroll
        self.explore_tag = None
        self.explore_cursor = None
        self.explore_loading = False
        self.explore_video_ids = set()
        explore_adjustment = scroll.get_vadjustment()
        explore_adjustment.connect("value-changed", self._on_explore_scroll)
        explore_adjustment.connect("changed", self._on_explore_scroll)

        self.content_stack.add_named(page, "explore_full")
        self._show_page("explore_full")
        self._set_active_dock_button("Explore")

        def fetch_explore():
            accounts = []
            tags = []
            try:
                accs_payload = soup_json("GET", f"https://{self.session['instance']}/api/v1/accounts/suggested", token=self.session["access_token"])
                accounts = accs_payload.get("data") if isinstance(accs_payload, dict) else accs_payload
            except Exception as e:
                print(f"Error fetching suggested profiles: {e}")
            try:
                tags_payload = soup_json("GET", f"https://{self.session['instance']}/api/v1/explore/tags", token=self.session["access_token"])
                tags = tags_payload.get("data") if isinstance(tags_payload, dict) else tags_payload
            except Exception as e:
                print(f"Error fetching trending tags: {e}")
            GLib.idle_add(
                self._populate_explore_view,
                accounts or [],
                tags or [],
                creators_box,
                tags_flow,
                vids_flow,
                vids_status,
            )

        threading.Thread(target=fetch_explore, daemon=True).start()

    def _populate_explore_view(self, accounts, tags, creators_box, tags_flow, vids_flow, vids_status):
        # 1. Creators
        for acc in accounts:
            if not isinstance(acc, dict):
                continue
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.add_css_class("creator-card")
            card.set_halign(Gtk.Align.CENTER)
            card.set_size_request(148, -1)

            avatar_url = acc.get("avatar")
            account_id = acc.get("id")
            profile_button = Gtk.Button()
            profile_button.add_css_class("flat")
            profile_button.add_css_class("profile-card-open")
            profile_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            profile_content.set_halign(Gtk.Align.CENTER)
            profile_content.append(IMAGE_CACHE.make_avatar(avatar_url, size=56))

            name = acc.get("display_name") or acc.get("username") or "Usuário"
            lbl_name = Gtk.Label(label=name[:24], xalign=0.5)
            lbl_name.add_css_class("heading")
            lbl_name.set_halign(Gtk.Align.CENTER)
            lbl_name.set_max_width_chars(20)
            lbl_name.set_wrap(True)
            profile_content.append(lbl_name)
            if acc.get("username"):
                username = Gtk.Label(label=f"@{acc['username']}", xalign=0.5)
                username.add_css_class("dim-label")
                profile_content.append(username)
            f_count = acc.get("follower_count") or 0
            lbl_f = Gtk.Label(label=f"{f_count} seg.", xalign=0.5)
            lbl_f.add_css_class("dim-label")
            profile_content.append(lbl_f)
            profile_button.set_child(profile_content)
            profile_button.connect("clicked", lambda _b, uid=account_id: self._open_profile_view(uid) if uid else None)
            card.append(profile_button)

            follow_btn = Gtk.Button(label="Seguir")
            follow_btn.add_css_class("suggested-action")
            follow_btn.connect("clicked", lambda _b, a=acc, b=follow_btn: self._toggle_follow_account(a, b))
            card.append(follow_btn)

            creators_box.append(card)

        # 2. Hashtags
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            tag_name = tag.get("name") or tag.get("slug") or ""
            count = tag.get("count", 0)
            btn = Gtk.Button(label=f"#{tag_name} ({count})")
            btn.add_css_class("tag-pill")
            btn.connect("clicked", lambda _b, tn=tag_name: self._filter_by_tag(tn, vids_flow))
            tags_flow.append(btn)

        self.explore_status = vids_status
        self.explore_loading = False
        self.explore_cursor = None
        self.explore_video_ids = set()
        first_tag = next((t.get("name") or t.get("slug") for t in tags if isinstance(t, dict) and (t.get("name") or t.get("slug"))), None)
        self._filter_by_tag(first_tag, vids_flow, reset=True)

    def _filter_by_tag(self, tag_name, vids_flow, reset=True):
        if self.explore_loading:
            return
        self.explore_tag = tag_name
        if reset:
            self.explore_cursor = None
            self.explore_video_ids = set()
            self.explore_status.set_text("Carregando vídeos…")
            child = vids_flow.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                vids_flow.remove(child)
                child = nxt
        self.explore_loading = True
        cursor = self.explore_cursor

        def worker():
            try:
                if tag_name:
                    route = f"/api/v1/explore/tag-feed/{urllib.parse.quote(tag_name)}"
                else:
                    route = "/api/v1/feed/for-you"
                if cursor:
                    route += f"?cursor={urllib.parse.quote(str(cursor))}"
                payload = soup_json("GET", f"https://{self.session['instance']}{route}", token=self.session["access_token"])
                vids, next_cursor = payload_items_and_cursor(payload)
                GLib.idle_add(self._render_explore_videos, vids, vids_flow, reset, next_cursor, None)
            except Exception as e:
                GLib.idle_add(self._render_explore_videos, [], vids_flow, reset, None, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_explore_scroll(self, adjustment):
        if self.explore_loading or not self.explore_cursor:
            return
        remaining = adjustment.get_upper() - (adjustment.get_value() + adjustment.get_page_size())
        if remaining < 280:
            self._filter_by_tag(self.explore_tag, self.explore_flow, reset=False)

    def _render_explore_videos(self, videos, vids_flow, reset, next_cursor, error):
        self.explore_loading = False
        self.explore_cursor = next_cursor
        if error:
            self.explore_status.set_text(f"Falha ao carregar vídeos: {error}")
            return

        for vid in videos:
            if not isinstance(vid, dict):
                continue
            vid_id = str(vid.get("id") or "")
            if vid_id and vid_id in self.explore_video_ids:
                continue
            if vid_id:
                self.explore_video_ids.add(vid_id)
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.add_css_class("video-grid-btn")
            btn.set_child(self._make_video_grid_card(vid))
            btn.connect("clicked", lambda _b, it=vid: self._open_list_video(it))
            vids_flow.append(btn)
        if next_cursor:
            self.explore_status.set_text("Role para carregar mais vídeos…")
            # If the first page is shorter than the viewport, fill it until the
            # user has enough content to scroll; also recheck after adjustment changes.
            GLib.idle_add(self._on_explore_scroll, self.explore_scroll.get_vadjustment())
        else:
            self.explore_status.set_text("Você chegou ao fim desta lista.")

    def _toggle_follow_account(self, account, button):
        acc_id = account.get("id")
        if not acc_id:
            return
        is_following = bool(account.get("is_following"))
        action = "unfollow" if is_following else "follow"
        account["is_following"] = not is_following
        button.set_label("Seguindo" if not is_following else "Seguir")

        def worker():
            try:
                soup_json("POST", f"https://{self.session['instance']}/api/v1/account/{action}/{acc_id}", token=self.session["access_token"])
            except Exception as e:
                print(f"Follow error: {e}")

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Subpage: Search View (Users, Hashtags, Videos)
    # --------------------------------------------------------------------------

    def _open_search_view(self):
        page = self._make_subpage("Pesquisar · Contas, hashtags e vídeos")

        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        container.set_margin_start(24)
        container.set_margin_end(24)
        container.set_margin_top(16)
        container.set_margin_bottom(24)
        scroll.set_child(container)
        page.append(scroll)

        query_entry = Gtk.SearchEntry(placeholder_text="Buscar no Loops…")
        container.append(query_entry)

        # Tab filter pills: Tudo | Usuários | Vídeos | Hashtags
        tab_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.search_type = "all"
        for label, stype in (("Tudo", "all"), ("Usuários", "users"), ("Vídeos", "videos"), ("Hashtags", "hashtags")):
            b = Gtk.Button(label=label)
            b.add_css_class("tag-pill")
            b.connect("clicked", lambda _b, t=stype, qe=query_entry: self._set_search_filter(t, qe))
            tab_box.append(b)
        container.append(tab_box)

        status_lbl = Gtk.Label(label="Digite algo para pesquisar.", wrap=True)
        status_lbl.add_css_class("dim-label")
        container.append(status_lbl)

        results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        container.append(results_box)

        query_entry.connect("activate", lambda e: self._perform_search(e.get_text().strip(), status_lbl, results_box))

        self.content_stack.add_named(page, "search_view")
        self._show_page("search_view")
        self._set_active_dock_button("Search")

    def _set_search_filter(self, stype, entry):
        self.search_type = stype
        entry.activate()

    def _perform_search(self, query, status_lbl, results_box):
        if not query:
            return
        status_lbl.set_text("Pesquisando…")

        child = results_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            results_box.remove(child)
            child = nxt

        def worker():
            try:
                params = urllib.parse.urlencode({
                    "query": query,
                    "type": self.search_type,
                    "limit": 20,
                })
                payload = soup_json("GET", f"https://{self.session['instance']}/api/v1/search?{params}", token=self.session["access_token"])
                if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                    payload = payload["data"]
                GLib.idle_add(self._render_search_results, payload, status_lbl, results_box)
            except Exception as e:
                GLib.idle_add(status_lbl.set_text, f"Falha na busca: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _render_search_results(self, payload, status_lbl, results_box):
        users = (payload.get("users") or payload.get("accounts") or []) if isinstance(payload, dict) else []
        videos = (payload.get("videos") or payload.get("posts") or []) if isinstance(payload, dict) else []
        hashtags = (payload.get("hashtags") or payload.get("tags") or []) if isinstance(payload, dict) else []

        total = len(users) + len(videos) + len(hashtags)
        if total == 0:
            status_lbl.set_text("Nenhum resultado encontrado.")
            return
        status_lbl.set_text(f"{total} resultados encontrados")

        # Render Users
        if users:
            results_box.append(Gtk.Label(label="Contas", xalign=0, css_classes=["heading"]))
            for u in users:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                row.add_css_class("comment-card")
                avatar = u.get("avatar")
                row.append(IMAGE_CACHE.make_avatar(avatar, size=36))

                ubox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                ubox.set_hexpand(True)
                name = u.get("display_name") or u.get("username") or "Usuário"
                ubox.append(Gtk.Label(label=name, xalign=0, css_classes=["comment-author"]))
                ubox.append(Gtk.Label(label=f"@{u.get('username')}", xalign=0, css_classes=["dim-label"]))
                row.append(ubox)

                btn_f = Gtk.Button(label="Ver perfil")
                btn_f.connect("clicked", lambda _b, uid=u.get("id"): self._open_profile_view(uid))
                row.append(btn_f)
                results_box.append(row)

        # Render Hashtags
        if hashtags:
            results_box.append(Gtk.Label(label="Hashtags", xalign=0, css_classes=["heading"]))
            tag_flow = Gtk.FlowBox()
            tag_flow.set_selection_mode(Gtk.SelectionMode.NONE)
            for h in hashtags:
                hname = h.get("name") or h.get("slug") or ""
                tbtn = Gtk.Button(label=f"#{hname}")
                tbtn.add_css_class("tag-pill")
                tbtn.connect("clicked", lambda _b, hn=hname: self._open_data_page(f"tag_{hn}", f"#{hn}", f"/api/v1/explore/tag-feed/{hn}"))
                tag_flow.append(tbtn)
            results_box.append(tag_flow)

        # Render Videos
        if videos:
            results_box.append(Gtk.Label(label="Vídeos", xalign=0, css_classes=["heading"]))
            vflow = Gtk.FlowBox()
            vflow.set_selection_mode(Gtk.SelectionMode.NONE)
            vflow.set_min_children_per_line(3)
            vflow.set_max_children_per_line(6)
            vflow.set_homogeneous(True)
            for v in videos:
                vbtn = Gtk.Button()
                vbtn.add_css_class("flat")
                vbtn.add_css_class("video-grid-btn")
                vbtn.set_child(self._make_video_grid_card(v))
                vbtn.connect("clicked", lambda _b, it=v: self._open_list_video(it))
                vflow.append(vbtn)
            results_box.append(vflow)

    # --------------------------------------------------------------------------
    # Subpage: Generic Collections with Avatars (Following, Suggested, DMs)
    # --------------------------------------------------------------------------

    def _open_following_suggestions_page(self):
        page_name = "following_suggestions"
        if page_name not in self.data_pages:
            page = self._make_subpage("Seguindo e sugestões")
            tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            tabs.set_margin_start(18)
            tabs.set_margin_end(18)
            tabs.set_margin_top(12)
            tabs.set_margin_bottom(8)
            stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
            stack.set_hexpand(True)
            stack.set_vexpand(True)

            tab_buttons = []
            for label, key in (("Seguindo", "following"), ("Sugestões", "suggestions")):
                button = Gtk.Button(label=label)
                button.add_css_class("tag-pill")
                button.connect("clicked", lambda _b, k=key: self._switch_relation_tab(k, stack, tab_buttons))
                tabs.append(button)
                tab_buttons.append((button, key))

                status = Gtk.Label(label="Carregando…", wrap=True)
                status.set_margin_top(12)
                rows = Gtk.FlowBox()
                rows.set_selection_mode(Gtk.SelectionMode.NONE)
                rows.set_min_children_per_line(2)
                rows.set_max_children_per_line(6)
                rows.set_homogeneous(True)
                rows.set_column_spacing(10)
                rows.set_row_spacing(10)
                scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
                scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
                scroll.set_child(rows)
                content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                content.set_margin_start(18)
                content.set_margin_end(18)
                content.append(status)
                content.append(scroll)
                stack.add_named(content, key)

                account = (self.session or {}).get("account") or {}
                if isinstance(account.get("data"), dict):
                    account = account["data"]
                account_id = account.get("id") or "self"
                path = (
                    f"/api/v1/account/following/{urllib.parse.quote(str(account_id))}"
                    if key == "following"
                    else "/api/v1/accounts/suggested"
                )
                self.data_pages[key] = (path, status, rows)

            page.append(tabs)
            page.append(stack)
            self.content_stack.add_named(page, page_name)
            self.data_pages[page_name] = "built"
            self._switch_relation_tab("following", stack, tab_buttons)

        self._show_page(page_name)
        self._set_active_dock_button("Following")
        for key in ("following", "suggestions"):
            path, status, _rows = self.data_pages[key]
            status.set_text("Carregando…")
            threading.Thread(target=self._fetch_collection, args=(key, path), daemon=True).start()

    def _switch_relation_tab(self, key, stack, buttons):
        stack.set_visible_child_name(key)
        for button, button_key in buttons:
            if key == button_key:
                button.add_css_class("active")
            else:
                button.remove_css_class("active")

    def _open_data_page(self, name, title, path):
        if name not in self.data_pages:
            page = self._make_subpage(title)

            status = Gtk.Label(label="Carregando…", wrap=True)
            status.set_margin_top(14)
            status.set_margin_bottom(14)

            rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            rows.add_css_class("boxed-list")
            if name == "messages":
                rows.connect("row-activated", self._open_conversation_row)

            scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(rows)

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            content.set_margin_start(24)
            content.set_margin_end(24)
            content.set_margin_bottom(18)
            content.append(status)
            content.append(scroll)
            page.append(content)

            self.content_stack.add_named(page, name)
            self.data_pages[name] = (path, status, rows)

        self._show_page(name)
        dock_name = {"explore": "Explore", "suggested": "Following", "messages": "Messages"}.get(name)
        if dock_name:
            self._set_active_dock_button(dock_name)

        _p, status, rows = self.data_pages[name]
        status.set_text("Carregando…")
        threading.Thread(target=self._fetch_collection, args=(name, path), daemon=True).start()

    def _fetch_collection(self, name, path):
        try:
            payload = soup_json(
                "GET",
                f"https://{self.session['instance']}{path}",
                token=self.session["access_token"],
            )
            GLib.idle_add(self._collection_loaded, name, payload, None)
        except Exception as error:
            GLib.idle_add(self._collection_loaded, name, None, str(error))

    def _collection_loaded(self, name, payload, error):
        _path, status, rows = self.data_pages[name]
        child = rows.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            rows.remove(child)
            child = following
        if error:
            status.set_text(f"Não foi possível carregar: {error}")
            return

        collection = payload
        if isinstance(payload, dict):
            for k in ("data", "items", "tags", "notifications", "videos", "accounts"):
                if isinstance(payload.get(k), list):
                    collection = payload[k]
                    break
            else:
                collection = [payload]

        if not isinstance(collection, list) or not collection:
            status.set_text("Nenhum item encontrado.")
            return

        status.set_text(f"{len(collection)} itens")
        for item in collection:
            if not isinstance(item, dict):
                continue
            account = item.get("participant") or item.get("account") or item.get("user")
            if not isinstance(account, dict):
                account = item if item.get("id") and item.get("avatar") else {}
            avatar_url = account.get("avatar") or item.get("avatar")

            title = (
                account.get("name")
                or
                item.get("display_name")
                or item.get("title")
                or item.get("name")
                or account.get("display_name")
                or account.get("username")
                or "Loops"
            )
            detail = (
                (f"@{account.get('username')} · {account.get('follower_count', 0)} seguidores" if account.get("username") else "")
                or
                item.get("caption")
                or item.get("content")
                or item.get("message")
                or item.get("description")
                or item.get("body")
                or item.get("acct")
                or ""
            )

            account_id = account.get("id") or item.get("id")
            if detail:
                detail_text = str(detail)
            else:
                detail_text = ""
            if name in ("suggestions", "following"):
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                card.add_css_class("creator-card")
                card.add_css_class("relation-profile-card")
                card.set_size_request(184, 184)
                profile_button = Gtk.Button()
                profile_button.add_css_class("flat")
                profile_button.add_css_class("profile-card-open")
                profile_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                profile_content.set_halign(Gtk.Align.CENTER)
                profile_content.append(IMAGE_CACHE.make_avatar(avatar_url, size=48))
                title_lbl = Gtk.Label(label=str(title), xalign=0.5, wrap=True, css_classes=["heading"])
                title_lbl.set_max_width_chars(20)
                profile_content.append(title_lbl)
                if detail_text:
                    det_lbl = Gtk.Label(label=detail_text, xalign=0.5, wrap=True, css_classes=["dim-label"])
                    det_lbl.set_max_width_chars(24)
                    profile_content.append(det_lbl)
                profile_button.set_child(profile_content)
                profile_button.connect("clicked", lambda _b, uid=account_id: self._open_profile_view(uid) if uid else None)
                card.append(profile_button)
                rows.append(card)
                if account.get("id"):
                    follow_btn = Gtk.Button(label="Seguindo" if account.get("is_following") else "Seguir")
                    follow_btn.connect("clicked", lambda _b, a=account, b=follow_btn: self._toggle_follow_account(a, b))
                    card.append(follow_btn)
                continue

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row_box.set_margin_start(14)
            row_box.set_margin_end(14)
            row_box.set_margin_top(10)
            row_box.set_margin_bottom(10)
            row_box.append(IMAGE_CACHE.make_avatar(avatar_url, size=40))
            line = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            line.set_hexpand(True)
            title_lbl = Gtk.Label(label=str(title), xalign=0, wrap=True, css_classes=["heading"])
            line.append(title_lbl)
            if detail_text:
                line.append(Gtk.Label(label=detail_text, xalign=0, wrap=True, selectable=True, css_classes=["dim-label"]))
            row_box.append(line)

            if name in ("suggestions", "following") and account.get("id"):
                follow_btn = Gtk.Button(label="Seguindo" if account.get("is_following") else "Seguir")
                follow_btn.set_valign(Gtk.Align.CENTER)
                follow_btn.connect("clicked", lambda _b, a=account, b=follow_btn: self._toggle_follow_account(a, b))
                row_box.append(follow_btn)

            if (item.get("media") or item.get("src_url")) and item.get("id"):
                btn_play = Gtk.Button(label="▶ Assistir")
                btn_play.connect("clicked", lambda _b, it=item: self._open_list_video(it))
                row_box.append(btn_play)

            row = Gtk.ListBoxRow()
            row.set_child(row_box)
            if name == "messages":
                self.conversation_by_row[id(row)] = item
                row.set_activatable(True)
            rows.append(row)

    def _open_list_video(self, item):
        self.videos = [item]
        self.video_index = 0
        self.feed_cursor = None
        self._show_feed_page()
        self.feed_status.set_text("")
        self.feed_status.set_visible(False)
        self._show_video(0)

    def _open_direct_message(self, account_id):
        if not account_id:
            return

        def worker():
            try:
                payload = soup_json(
                    "POST",
                    f"https://{self.session['instance']}/api/v1/dm/conversations/lookup",
                    token=self.session["access_token"],
                    json_body={"participant_id": account_id},
                )
                conv = payload.get("data") or payload
                GLib.idle_add(self._show_conversation, conv)
            except Exception as e:
                GLib.idle_add(self._set_feed_status, f"Não foi possível abrir a conversa: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _open_conversation_row(self, _listbox, row):
        conv = self.conversation_by_row.get(id(row))
        if not conv:
            return
        self._show_conversation(conv)

    def _show_conversation(self, conv):
        conv_id = conv.get("id")
        participant = conv.get("participant") or {}
        p_name = participant.get("display_name") or participant.get("username") or "Chat"

        dialog = Gtk.Dialog(title=p_name, transient_for=self, modal=True)
        dialog.set_default_size(440, 560)
        dialog.add_button("Fechar", Gtk.ResponseType.CLOSE)
        area = dialog.get_content_area()

        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(rows)
        area.append(scroll)

        composer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        entry = Gtk.Entry(placeholder_text="Escreva uma mensagem…", hexpand=True)
        send_btn = Gtk.Button(label="Enviar")
        composer.append(entry)
        composer.append(send_btn)
        area.append(composer)

        dialog.connect("response", lambda d, _r: d.destroy())
        send_btn.connect("clicked", lambda *_: self._send_dm_message(conv_id, entry, rows))
        dialog.present()

        threading.Thread(target=self._fetch_dm_messages, args=(conv_id, rows), daemon=True).start()

    def _fetch_dm_messages(self, conv_id, rows):
        try:
            payload = soup_json(
                "GET",
                f"https://{self.session['instance']}/api/v1/dm/conversations/{urllib.parse.quote(str(conv_id))}/messages",
                token=self.session["access_token"],
            )
            items = payload.get("data", []) if isinstance(payload, dict) else []
            GLib.idle_add(self._render_dm_messages, rows, items)
        except Exception as e:
            print(f"Error fetching DMs: {e}")

    def _render_dm_messages(self, rows, items):
        rows.remove_all()
        for msg in items:
            text = msg.get("body") or "Mídia / Vídeo"
            lbl = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
            lbl.set_margin_start(12)
            lbl.set_margin_end(12)
            lbl.set_margin_top(6)
            lbl.set_margin_bottom(6)
            rows.append(lbl)

    def _send_dm_message(self, conv_id, entry, rows):
        text = entry.get_text().strip()
        if not text:
            return
        entry.set_text("")

        def send():
            try:
                soup_json(
                    "POST",
                    f"https://{self.session['instance']}/api/v1/dm/messages",
                    token=self.session["access_token"],
                    json_body={"type": "text", "body": text, "conversation_id": str(conv_id)},
                )
                self._fetch_dm_messages(conv_id, rows)
            except Exception as e:
                print(f"Error sending DM: {e}")

        threading.Thread(target=send, daemon=True).start()

    # --------------------------------------------------------------------------
    # Logout
    # --------------------------------------------------------------------------

    def _logout(self, *_args):
        try:
            config_path().unlink(missing_ok=True)
        except OSError:
            pass
        self.session = None
        self.videos = []
        if hasattr(self, "player") and self.player:
            self.player.set_playing(False)
        self.show_login()


# ==============================================================================
# Application Entry Point
# ==============================================================================

class LoopsApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        load_custom_css()
        window = self.props.active_window or LoopsWindow(self)
        window.present()


def main():
    Adw.init()
    return LoopsApp().run(None)


if __name__ == "__main__":
    raise SystemExit(main())
