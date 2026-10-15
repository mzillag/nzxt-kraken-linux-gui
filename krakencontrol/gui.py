"""Desktop window and tray icon: a WebKitGTK view of the local UI.

The window is a single-instance ``Gtk.Application``.  In tray mode (``krakencontrol
tray``) the process keeps running after the window is closed and shows an
indicator in the panel, like CAM's "minimize to tray when closing"; launching the
app again (menu entry, ``krakencontrol gui``) activates the existing instance and
re-opens its window instead of starting a second one.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

LOGGER = logging.getLogger(__name__)

APP_ID = "org.krakencontrol.App"  # also the name of the .desktop file, so GNOME can match the window
# Tray icon: an absolute path is the one form every StatusNotifier host (GNOME's
# AppIndicator extension, KDE, XFCE) resolves without a theme lookup.
TRAY_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "icon.png")


def run_window(url: str, on_close=None, tray: bool = False, show_window: bool = True,
               width: int = 1080, height: int = 760) -> bool:
    """Open the UI window (and the tray icon when `tray` is set).

    Returns False if WebKitGTK is unavailable so the caller can fall back to a browser.
    Blocks until the application quits.
    """
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk, WebKit2, GLib  # noqa: F401
    except (ImportError, ValueError) as exc:
        LOGGER.warning("WebKitGTK not available (%s)", exc)
        return False

    app = _build_app(url, on_close, tray, show_window, width, height)
    app.run([])
    return True


def _build_app(url, on_close, tray, show_window, width, height):
    import signal
    from gi.repository import Gtk, WebKit2, GLib, Gio

    class KrakenApp(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.window = None
            self.indicator = None
            self._activations = 0
            GLib.set_prgname("kraken-control")

        # ---------------------------------------------------------- lifecycle
        def do_startup(self):
            Gtk.Application.do_startup(self)
            if tray:
                self.indicator = _make_indicator(self)
                if self.indicator is None:
                    LOGGER.warning("tray icon unavailable; closing the window will quit")
                else:
                    self.hold()  # stay alive without windows
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, lambda *_: GLib.idle_add(self.quit_all))

        def do_activate(self):
            # First activation: honour show_window (the tray can start minimized).
            # Later activations come from another launch of the app: always show.
            self._activations += 1
            if self._activations > 1 or show_window or self.indicator is None:
                self.show_window()

        def quit_all(self, *_args):
            if on_close:
                try:
                    on_close()
                except Exception:  # noqa: BLE001
                    LOGGER.exception("error while closing")
            if self.window is not None:
                window, self.window = self.window, None
                window.destroy()
            self.quit()
            return False

        # ---------------------------------------------------------- window
        def show_window(self):
            if self.window is None:
                self.window = self._create_window()
            self.window.show_all()
            self.window.present()

        def _create_window(self):
            window = Gtk.ApplicationWindow(application=self, title="Kraken Control")
            window.set_wmclass("kraken-control", "Kraken Control")
            window.set_default_size(width, height)
            window.set_size_request(920, 600)
            icon = os.path.join(os.path.dirname(__file__), "web", "icon.png")
            if os.path.exists(icon):
                window.set_icon_from_file(icon)

            context = WebKit2.WebContext.get_default()
            settings = WebKit2.Settings()
            settings.set_enable_developer_extras(True)
            settings.set_enable_write_console_messages_to_stdout(os.environ.get("KRAKEN_DEBUG") == "1")
            settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.ON_DEMAND)
            view = WebKit2.WebView.new_with_context(context)
            view.set_settings(settings)
            view.set_background_color(_rgba("#1a1c1e"))
            view.connect("run-file-chooser", lambda _v, req: _file_chooser(window, req))
            view.load_uri(url)
            window.add(view)

            def on_delete(*_args):
                if self.indicator is not None:
                    # Tray mode: closing only hides the window, like CAM
                    window.hide()
                    return True
                return False

            def on_destroy(*_args):
                if self.window is window:
                    self.window = None
                    if self.indicator is None:
                        self.quit_all()

            window.connect("delete-event", on_delete)
            window.connect("destroy", on_destroy)
            return window

    return KrakenApp()


def _make_indicator(app):
    """Panel icon with a menu (needs libappindicator and, on GNOME, the AppIndicator extension)."""
    try:
        import gi
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except (ImportError, ValueError):
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        from gi.repository import Gtk
    except (ImportError, ValueError) as exc:
        LOGGER.warning("AppIndicator not available (%s)", exc)
        return None

    indicator = AppIndicator.Indicator.new("kraken-control", TRAY_ICON,
                                           AppIndicator.IndicatorCategory.APPLICATION_STATUS)
    indicator.set_icon_full(TRAY_ICON, "Kraken Control")
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
    indicator.set_title("Kraken Control")

    menu = Gtk.Menu()
    item_open = Gtk.MenuItem(label="Open Kraken Control")
    item_open.connect("activate", lambda *_: app.show_window())
    menu.append(item_open)
    menu.append(Gtk.SeparatorMenuItem())
    item_quit = Gtk.MenuItem(label="Quit")
    item_quit.connect("activate", lambda *_: app.quit_all())
    menu.append(item_quit)
    menu.show_all()
    indicator.set_menu(menu)
    indicator.set_secondary_activate_target(item_open)  # middle click opens the window
    return indicator


def _file_chooser(window, request) -> bool:
    from gi.repository import Gtk

    dialog = Gtk.FileChooserDialog(title="Choose an image or GIF", parent=window,
                                   action=Gtk.FileChooserAction.OPEN)
    dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
    flt = Gtk.FileFilter()
    flt.set_name("Images and GIFs")
    for pattern in ("*.gif", "*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp", "*.GIF", "*.PNG", "*.JPG"):
        flt.add_pattern(pattern)
    dialog.add_filter(flt)
    dialog.set_select_multiple(request.get_select_multiple())
    response = dialog.run()
    if response == Gtk.ResponseType.OK:
        request.select_files(dialog.get_filenames())
    else:
        request.cancel()
    dialog.destroy()
    return True


def _rgba(hex_color: str):
    from gi.repository import Gdk
    rgba = Gdk.RGBA()
    rgba.parse(hex_color)
    return rgba


def open_in_browser(url: str) -> None:
    """Fallback: open the UI as an app window in Chrome/Chromium, else the default browser."""
    for name in ("google-chrome", "chromium", "chromium-browser", "brave-browser", "microsoft-edge"):
        exe = shutil.which(name)
        if exe:
            subprocess.Popen([exe, f"--app={url}", "--window-size=1080,760"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    import webbrowser
    webbrowser.open(url)
    print(f"UI available at {url}", file=sys.stderr)
