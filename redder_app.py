#!/usr/bin/env python3
"""Redder — a Cinnamon/XApp system-tray control for the X11 GPU gamma table."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("XApp", "1.0")

from gi.repository import Gdk, Gio, GLib, Gtk, XApp  # noqa: E402

from gamma_control import GammaController, GammaError  # noqa: E402

APP_ID = "com.redder.Redder"
APP_NAME = "Redder"
APP_VERSION = "1.0.0"
APP_ICON = os.environ.get("REDDER_ICON_NAME", "redder")
BASE_DIR = Path(__file__).resolve().parent
WINDOW_WIDTH = 120
WINDOW_HEIGHT = 294

LOG = logging.getLogger("redder")


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "redder"
    return Path.home() / ".local" / "state" / "redder"


def setup_logging() -> None:
    LOG.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        directory = state_dir()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            directory / "redder.log", maxBytes=256 * 1024, backupCount=1
        )
        handler.setFormatter(formatter)
        LOG.addHandler(handler)
    except OSError:
        pass
    if sys.stderr.isatty():
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        LOG.addHandler(stream)


class GammaJournal:
    """A crash-safe copy of the color table captured before Redder changed it."""

    def __init__(self) -> None:
        self.path = state_dir() / "gamma-backup.json"

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, controller: GammaController) -> None:
        snapshot = controller.export_originals()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                temporary.unlink()
            except OSError:
                pass
            raise
        os.replace(temporary, self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            LOG.warning("Could not remove gamma backup: %s", exc)

    def recover(self) -> bool:
        """Restore a table left behind by an unclean exit. Returns True if used."""
        if not self.path.exists():
            return False
        try:
            snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            LOG.warning("Ignoring unreadable gamma backup: %s", exc)
            self.clear()
            return False

        controller = None
        restored = False
        try:
            controller = GammaController(capture_originals=False)
            try:
                count = controller.apply_snapshot(snapshot)
                LOG.info("Recovered previous gamma table on %s CRTC(s)", count)
            except GammaError as exc:
                LOG.warning("Saved gamma table did not apply (%s); using linear ramp", exc)
                controller.apply_identity()
                LOG.info("Recovered display with a linear gamma ramp")
            restored = True
        except GammaError as exc:
            LOG.error("Could not recover the previous gamma table: %s", exc)
        finally:
            if controller is not None:
                controller.close(restore=False)
        if restored:
            self.clear()
        return restored


def load_css() -> None:
    css_path = BASE_DIR / "redder.css"
    if not css_path.exists():
        css_path = BASE_DIR / "assets" / "redder.css"
    provider = Gtk.CssProvider()

    def on_css_error(_provider, _section, error):
        LOG.warning("GTK CSS error: %s", error.message)

    provider.connect("parsing-error", on_css_error)
    try:
        provider.load_from_path(str(css_path))
    except GLib.Error as exc:
        LOG.error("Could not load %s: %s", css_path, exc)
        return
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


class RedderApp(Gtk.Application):
    def __init__(self, start_silent: bool = False) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._start_silent = start_silent
        self._first_activation = True
        self._held = False
        self._quitting = False
        self._cleaned_up = False
        self._popup_visible = False
        self._signal_sources: list[int] = []

        self.controller: GammaController | None = None
        self.percent = 0
        self._pending_percent: int | None = None
        self._apply_source = None
        self._syncing = False
        self._error: str | None = None
        self._journal = GammaJournal()

        self.window: Gtk.Window | None = None
        self.card: Gtk.Box | None = None
        self.scale: Gtk.Scale | None = None
        self.tray: XApp.StatusIcon | None = None

    # -- application lifecycle ----------------------------------------

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self.hold()
        self._held = True

        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", True)

        LOG.info("Starting Redder %s", APP_VERSION)
        if self._journal.recover():
            LOG.info("A previous display change was recovered at startup")

        try:
            self.controller = GammaController()
        except GammaError as exc:
            self._error = str(exc)
            LOG.error("Gamma control unavailable: %s", exc)

        load_css()
        self._build_ui()
        self._build_tray()
        self._install_signal_handlers()
        self._update_ui(0)

    def do_activate(self) -> None:
        first = self._first_activation
        self._first_activation = False
        if first and self._start_silent:
            LOG.info("Started silently in the tray")
            return
        self.show_popup()

    def do_shutdown(self) -> None:
        self._cleanup()
        Gtk.Application.do_shutdown(self)

    # -- tray and popup ------------------------------------------------

    def _build_tray(self) -> None:
        self.tray = XApp.StatusIcon.new_with_name("redder")
        self.tray.set_icon_name(APP_ICON)
        self.tray.set_tooltip_text("Redder — normal color")
        try:
            self.tray.set_property("icon-size", 24)
        except TypeError:
            pass
        self.tray.connect("activate", self._on_tray_activate)
        self.tray.set_visible(True)
        LOG.info("Tray icon registered (icon=%s)", APP_ICON)

    def _on_tray_activate(self, _icon, button: int, timestamp: int) -> None:
        LOG.info("Tray activate: button=%s", button)
        if button in (1, 3):
            if self._popup_visible:
                self.hide_popup()
            else:
                self.show_popup(timestamp)

    def _position_window(self) -> None:
        if self.window is None:
            return
        display = Gdk.Display.get_default()
        if display is None:
            return
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is None:
            return
        area = monitor.get_workarea()
        width, height = self.window.get_size()
        if width <= 1 or height <= 1:
            width, height = WINDOW_WIDTH, WINDOW_HEIGHT
        x = max(area.x, area.x + area.width - width - 2)
        y = max(area.y, area.y + area.height - height - 2)
        self.window.move(x, y)

    def show_popup(self, timestamp: int = 0) -> None:
        if self.window is None:
            return
        self._update_ui(self.percent)
        self._position_window()
        self.window.show()
        if timestamp:
            self.window.present_with_time(timestamp)
        else:
            self.window.present()
        self._popup_visible = True
        GLib.idle_add(self._focus_scale)
        GLib.idle_add(self._position_after_show)
        LOG.info("Popup shown")

    def _focus_scale(self):
        if self.scale is not None and self.scale.get_sensitive():
            self.scale.grab_focus()
        return GLib.SOURCE_REMOVE

    def _position_after_show(self):
        self._position_window()
        return GLib.SOURCE_REMOVE

    def hide_popup(self) -> None:
        self._flush_apply()
        if self.window is not None:
            self.window.hide()
        self._popup_visible = False
        LOG.info("Popup hidden")

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide_popup()
            return True
        return False

    def _on_delete_event(self, *_args) -> bool:
        self.hide_popup()
        return True

    # -- UI construction -----------------------------------------------

    def _build_ui(self) -> None:
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_application(self)
        self.window.set_title("Redder")
        self.window.set_icon_name(APP_ICON)
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_keep_above(True)
        self.window.set_accept_focus(True)
        self.window.set_focus_on_map(True)
        self.window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.window.set_position(Gtk.WindowPosition.NONE)
        self.window.set_default_size(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.window.set_size_request(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.window.get_style_context().add_class("redder-window")
        self.window.connect("key-press-event", self._on_key_press)
        self.window.connect("delete-event", self._on_delete_event)

        screen = Gdk.Screen.get_default()
        if screen is not None and screen.is_composited():
            visual = screen.get_rgba_visual()
            if visual is not None:
                self.window.set_visual(visual)
                self.window.set_app_paintable(True)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_margin_top(8)
        root.set_margin_bottom(8)
        root.set_margin_start(8)
        root.set_margin_end(8)

        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.card.get_style_context().add_class("redder-card")
        root.pack_start(self.card, True, True, 0)
        self.window.add(root)

        self._build_body()
        self._build_footer()

        root.show_all()
        self.window.hide()

    def _label(self, text: str, css_class: str, xalign: float = 0.0) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.set_xalign(xalign)
        label.get_style_context().add_class(css_class)
        return label

    def _build_body(self) -> None:
        assert self.card is not None
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_vexpand(True)

        rail = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        rail.set_halign(Gtk.Align.CENTER)
        rail.set_vexpand(True)

        title = Gtk.Label(label="REDDER →")
        title.set_angle(90)
        title.set_size_request(20, -1)
        title.set_valign(Gtk.Align.CENTER)
        title.set_halign(Gtk.Align.CENTER)
        title.get_style_context().add_class("redder-title")
        title.get_accessible().set_name("Redder")

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        right.set_halign(Gtk.Align.CENTER)
        right.set_vexpand(True)

        top = self._label("PURE RED", "scale-edge scale-edge-top", 0.5)
        top.set_margin_top(2)

        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, 0, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_has_origin(False)
        # A vertical Gtk.Scale normally puts its minimum at the top; invert it
        # so dragging upward increases red isolation (top = pure red).
        self.scale.set_inverted(True)
        self.scale.set_value_pos(Gtk.PositionType.TOP)
        self.scale.set_size_request(44, 140)
        self.scale.set_margin_top(16)
        self.scale.set_margin_bottom(16)
        self.scale.set_halign(Gtk.Align.CENTER)
        self.scale.set_valign(Gtk.Align.FILL)
        self.scale.set_vexpand(True)
        self.scale.get_style_context().add_class("redder-scale")
        self.scale.get_accessible().set_name("Red isolation")
        self.scale.set_sensitive(self.controller is not None)
        adjustment = self.scale.get_adjustment()
        adjustment.set_step_increment(1)
        adjustment.set_page_increment(10)
        self.scale.connect("value-changed", self._on_scale_changed)

        bottom = self._label("NORMAL", "scale-edge scale-edge-bottom", 0.5)

        right.pack_start(top, False, False, 0)
        right.pack_start(self.scale, True, True, 0)
        right.pack_start(bottom, False, False, 0)

        rail.pack_start(title, False, False, 0)
        rail.pack_start(right, True, True, 0)
        content.pack_start(rail, True, True, 0)
        self.card.pack_start(content, True, True, 0)

    def _build_footer(self) -> None:
        assert self.card is not None
        quit_button = Gtk.Button(label="Quit")
        quit_button.get_style_context().add_class("quit-button")
        quit_button.set_tooltip_text("Quit Redder and restore the original colors")
        quit_button.set_halign(Gtk.Align.CENTER)
        quit_button.set_margin_top(4)
        quit_button.set_margin_start(24)
        quit_button.connect("clicked", lambda _button: self.quit_redder())
        self.card.pack_start(quit_button, False, False, 0)

    # -- gamma control --------------------------------------------------

    def _ensure_journal(self) -> None:
        if self._journal.exists() or self.controller is None:
            return
        try:
            self._journal.save(self.controller)
        except (GammaError, OSError, ValueError) as exc:
            LOG.warning("Could not save gamma recovery file: %s", exc)

    def _on_scale_changed(self, scale: Gtk.Scale) -> None:
        if self._syncing or not self.controller:
            return
        percent = int(round(scale.get_value()))
        self._update_ui(percent, sync_scale=False)
        self._pending_percent = percent
        if self._apply_source is None:
            self._apply_source = GLib.timeout_add(32, self._flush_apply_timer)

    def _flush_apply_timer(self) -> bool:
        self._apply_source = None
        self._flush_apply()
        return GLib.SOURCE_REMOVE

    def _flush_apply(self) -> None:
        if self._apply_source is not None:
            GLib.source_remove(self._apply_source)
            self._apply_source = None
        if self._pending_percent is None:
            return
        percent = self._pending_percent
        self._pending_percent = None
        self._apply_percent(percent)

    def _apply_percent(self, percent: int) -> None:
        if self.controller is None:
            return
        if percent > 0:
            self._ensure_journal()
        try:
            self.controller.apply_percent(percent)
        except GammaError as exc:
            LOG.error("Gamma update failed at %s%%: %s", percent, exc)
            self._error = str(exc)
            self._sync_scale(int(round(self.controller.percent)))
            self._update_ui(int(round(self.controller.percent)))
            return
        self._error = None
        self.percent = percent
        if percent == 0:
            self._journal.clear()
        self._update_ui(percent)
        LOG.info("Applied %s%% red isolation", percent)

    def _sync_scale(self, percent: int) -> None:
        if self.scale is None:
            return
        self._syncing = True
        try:
            if int(round(self.scale.get_value())) != percent:
                self.scale.set_value(percent)
        finally:
            self._syncing = False

    # -- UI updates -----------------------------------------------------

    def _update_ui(self, percent: int, sync_scale: bool = True) -> None:
        percent = max(0, min(100, int(percent)))
        self.percent = percent
        if sync_scale:
            self._sync_scale(percent)
        if self.card is not None:
            context = self.card.get_style_context()
            if percent > 0:
                context.add_class("is-isolated")
            else:
                context.remove_class("is-isolated")

        if self.tray is not None:
            if self.controller is None:
                tooltip = "Redder — gamma control unavailable"
            elif self._error:
                tooltip = "Redder — error"
            elif percent <= 0:
                tooltip = "Redder — normal color"
            elif percent >= 100:
                tooltip = "Redder — pure red"
            else:
                tooltip = f"Redder — {percent}% red isolation"
            self.tray.set_tooltip_text(tooltip)

    # -- cleanup --------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                source_id = GLib.unix_signal_add(
                    GLib.PRIORITY_HIGH, signum, self._on_unix_signal, signum
                )
                self._signal_sources.append(source_id)
            except Exception as exc:
                LOG.warning("Could not install handler for signal %s: %s", signum, exc)

    def _on_unix_signal(self, signum: int):
        LOG.info("Received signal %s; shutting down", signum)
        self.quit_redder()
        return GLib.SOURCE_REMOVE

    def _cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        if self._apply_source is not None:
            GLib.source_remove(self._apply_source)
            self._apply_source = None
        self._pending_percent = None

        restored = False
        if self.controller is not None:
            try:
                self.controller.restore()
                restored = True
                LOG.info("Original display color table restored")
            except GammaError as exc:
                LOG.error("Could not restore the display color table: %s", exc)
            try:
                self.controller.close(restore=False)
            except Exception as exc:
                LOG.warning("Error closing display connection: %s", exc)
            self.controller = None
        if restored:
            self._journal.clear()

        if self.window is not None:
            try:
                self.window.hide()
            except Exception:
                pass
        if self.tray is not None:
            try:
                self.tray.set_visible(False)
            except Exception:
                pass
            self.tray = None

    def quit_redder(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._cleanup()
        if self._held:
            self._held = False
            try:
                self.release()
            except Exception:
                pass
        self.quit()


def reset_display() -> int:
    try:
        controller = GammaController(capture_originals=False)
    except GammaError as exc:
        print(f"Could not reset the display: {exc}", file=sys.stderr)
        return 1
    try:
        controller.apply_identity()
    except GammaError as exc:
        print(f"Could not reset the display: {exc}", file=sys.stderr)
        return 1
    finally:
        controller.close(restore=False)
    GammaJournal().clear()
    print("Display gamma reset to a standard linear ramp.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(arg in ("-h", "--help") for arg in args):
        print(
            "Usage: redder [--silent] [--reset]\n\n"
            "  --silent  start in the system tray without opening the panel\n"
            "  --reset   restore a standard color table and exit\n"
        )
        return 0
    if "--version" in args:
        print(f"Redder {APP_VERSION}")
        return 0
    if "--reset" in args:
        setup_logging()
        return reset_display()

    start_silent = "--silent" in args
    GLib.set_prgname("redder")
    GLib.set_application_name(APP_NAME)
    Gtk.init([])
    try:
        Gdk.set_program_class("Redder")
    except Exception:
        pass
    setup_logging()

    app = RedderApp(start_silent=start_silent)
    try:
        return app.run([sys.argv[0]])
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
