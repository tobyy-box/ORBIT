"""ORBIT — a customizable desktop action wheel.

Start it with::

    python main.py              # tray + global shortcut, overlay on demand
    python main.py --studio     # open ORBIT Studio straight away
    python main.py --background # start quietly (used for launch on login)

The application lifecycle, the overlay, the global shortcut, the action runner
and the tray icon are all wired together here.  Everything else lives in
``core`` (logic, no UI) and ``ui`` (widgets, no business rules).
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from pathlib import Path

TRAY_MESSAGE_MS = 6000


def _fail_missing_dependency(error: Exception) -> int:
    message = (
        "ORBIT needs PySide6 (the Qt user interface).\n\n"
        f"Details: {error}\n\n"
        "Install the requirements with:\n"
        "    python -m pip install -r requirements.txt\n"
        "or\n"
        "    python -m pip install PySide6\n"
    )
    print(message, file=sys.stderr)
    return 1


try:  # pragma: no cover - import guard
    from PySide6.QtCore import QObject, QRect, QTimer
    from PySide6.QtGui import QColor, QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QMessageBox
except Exception as exc:  # pragma: no cover
    raise SystemExit(_fail_missing_dependency(exc))

from core import paths
from core.action_registry import ActionRegistry, get_registry
from core.action_executor import ActionRunner
from core.config_manager import ConfigManager
from core.launcher import LaunchError, open_with_default
from core.keycodes import format_hotkey
from core.media_controller import MediaController
from core.models import Action, Config
from core.shortcut_manager import ShortcutManager, ShortcutState
from core.startup import set_enabled as set_startup_enabled
from ui.image_viewer import ImageViewer
from ui.radial_overlay import RadialOverlay
from ui.theme_manager import Theme, build_theme, studio_stylesheet
from ui.toast import ToastManager
from ui.tray_manager import TrayManager

LOG = logging.getLogger("orbit")
SINGLE_INSTANCE_KEY = "orbit-single-instance-7f3c"


# --------------------------------------------------------------------- logging
def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s  %(message)s")

    try:
        handler = logging.handlers.RotatingFileHandler(
            paths.log_path(), maxBytes=512_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)
    except OSError:
        pass

    stream = sys.stderr
    if stream is not None:
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
        console = logging.StreamHandler(stream)
        console.setFormatter(formatter)
        console.setLevel(logging.WARNING)
        root.addHandler(console)


# ------------------------------------------------------------- single instance
def _claim_single_instance(app: QApplication) -> QLocalServer | None:
    """Return a server when this is the first instance, otherwise activate the first."""
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_KEY)
    if socket.waitForConnected(400):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(400)
        socket.disconnectFromServer()
        return None
    server = QLocalServer()
    QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
    if not server.listen(SINGLE_INSTANCE_KEY):
        LOG.warning("Could not create the single-instance socket: %s", server.errorString())
        return None
    return server


# ------------------------------------------------------------------- controller
class AppController(QObject):
    """Owns every long-lived ORBIT object and the action lifecycle."""

    def __init__(self, app: QApplication, *, config_path: Path | None = None) -> None:
        super().__init__()
        self.app = app
        self.config_manager = ConfigManager(config_path)
        # Load before anything reads settings: the first paint, the tray icon and
        # the shortcut registration all depend on the user's configuration.
        self.config_manager.load()
        self.registry: ActionRegistry = get_registry()
        self.media = MediaController()
        self.theme: Theme = build_theme(self.config_manager.config.settings)

        self.toasts = ToastManager(self.theme, screen_getter=self._screen_for_feedback)
        self.runner = ActionRunner(
            lambda: self.config_manager.config,
            registry=self.registry,
            media=self.media,
            services=self._services(),
            log=LOG.info,
        )
        self.overlay = RadialOverlay(
            lambda: self.config_manager.config,
            lambda: self.theme,
            self.runner,
            self.registry,
        )
        self.shortcut = ShortcutManager(self)
        self.tray = TrayManager(self.theme, self)

        self._studio = None
        self._viewer: ImageViewer | None = None
        from core.productivity import ProductivityStore
        from ui.productivity import MusicPlayer, ProductivityPanel, QuickControlsPanel, SearchDialog
        self.productivity = ProductivityStore(paths.app_data_dir() / "productivity.json")
        self.overlay.set_productivity_store(self.productivity)
        self.search_dialog = None
        self.quick_controls = None
        # Notes, checklists and timers are radial stickers now; no separate
        # Music Hub or Productivity pages are created at startup.
        self.music_player = None
        self.productivity_panel = None
        self._feature_windows = []
        self.search_shortcut = ShortcutManager(self)
        self.search_shortcut.state_changed.connect(lambda state: self.tray.set_search_shortcut_state(state))
        self.search_shortcut.activated.connect(self.open_search)
        self.quick_controls_shortcut = ShortcutManager(self)
        self.quick_controls_shortcut.activated.connect(self.open_quick_controls)
        self.music_shortcut = ShortcutManager(self)
        self.widgets_shortcut = ShortcutManager(self)
        self.quick_controls_class = QuickControlsPanel
        self.productivity_panel_class = ProductivityPanel
        self.search_dialog_class = SearchDialog

        self._connect()
        LOG.info("ORBIT controller ready (config: %s)", self.config_manager.path)

    # ------------------------------------------------------------------ services
    def _services(self) -> dict:
        # Resolved lazily: the runner is built before the overlay exists, but
        # these callables only run once an action is actually executed.
        return {
            "open_menu": lambda menu_id: self.overlay.open_submenu(menu_id),
            "open_image": lambda path: self._open_image_viewer(path),
            "orbit_command": lambda command: self._orbit_command(command),
        }

    def _connect(self) -> None:
        self.overlay.actionRequested.connect(self._on_action_requested)
        self.overlay.navigationBlocked.connect(
            lambda message: self._feedback("Navigation stopped", message, ok=False)
        )
        self.overlay.closed.connect(lambda: LOG.debug("Overlay closed"))

        self.runner.finished.connect(self._on_action_finished)
        self.shortcut.activated.connect(self._on_hotkey)
        self.shortcut.state_changed.connect(self._on_shortcut_state)
        self.shortcut.message.connect(lambda message: LOG.info("Hotkey: %s", message))

        self.tray.showMenuRequested.connect(self.overlay.toggle)
        self.tray.openStudioRequested.connect(self.open_studio)
        self.tray.reloadConfigRequested.connect(self.reload_configuration)
        self.tray.openConfigFolderRequested.connect(self.open_config_folder)
        self.tray.changeShortcutRequested.connect(lambda: self.open_studio("behaviour"))
        self.tray.searchRequested.connect(self.open_search)
        self.tray.quickControlsRequested.connect(self.open_quick_controls)
        self.tray.aboutRequested.connect(lambda: self.open_studio("about"))
        self.tray.quitRequested.connect(self.shutdown)
        self.tray.pauseToggled.connect(self._on_pause_toggled)

    # -------------------------------------------------------------------- startup
    def start(self, *, visible_tray: bool = True) -> None:
        config = self.config_manager.config
        for message in self.config_manager.load_errors:
            LOG.error("Configuration problem: %s", message)
        for message in self.config_manager.load_warnings:
            LOG.warning("Configuration notice: %s", message)
        if self.config_manager.load_errors:
            QTimer.singleShot(600, self._report_config_problems)

        tray_ok = self.tray.install(visible=config.settings.show_tray_icon and visible_tray)
        if not tray_ok:
            LOG.warning("No system tray available on this desktop session.")
        self.tray.set_hotkey_label(format_hotkey(config.settings.hotkey))
        self._register_hotkey(config.settings.hotkey, report=False)
        self._register_feature_hotkeys(config.settings)
        if self.music_player is not None:
            self.music_player.apply_settings(config.settings)
        self._apply_startup_setting(config)
        self.apply_settings(persist=False)

    def _report_config_problems(self) -> None:
        detail = "\n\n".join(self.config_manager.load_errors)
        warnings = "\n".join(f"• {item}" for item in self.config_manager.load_warnings[:8])
        box = QMessageBox()
        box.setWindowTitle("ORBIT could not read your configuration")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("ORBIT started with a safe configuration.")
        box.setInformativeText(f"{detail}\n\n{warnings}".strip())
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _apply_startup_setting(self, config: Config) -> None:
        # Only reconcile when the OS entry is out of sync with the setting.
        from core.startup import is_enabled, needs_repair

        desired = bool(config.settings.start_on_login)
        try:
            current = is_enabled()
            stale = current and desired and needs_repair()
        except Exception:  # pragma: no cover - platform specific
            return
        if desired == current and not stale:
            return
        ok, message = set_startup_enabled(desired)
        if not ok:
            LOG.warning("Startup setting not applied: %s", message)
        elif stale:
            # The entry pointed at a file that no longer exists; it was rewritten.
            LOG.info("Launch-on-login entry repaired: %s", message)

    # ------------------------------------------------------------------- shortcut
    def _register_hotkey(self, hotkey: str, *, report: bool = True) -> ShortcutState:
        state = self.shortcut.start(hotkey)
        display = state.display or hotkey
        if not state.ok:
            config = self.config_manager.config
            fallback = config.settings.fallback_hotkey
            if fallback and state.conflict:
                LOG.warning("Hotkey %s unavailable, trying fallback %s", hotkey, fallback)
                alternative = self.shortcut.start(fallback)
                if alternative.ok:
                    message = (
                        f"{display} is already used by another application.\n\n"
                        f"ORBIT is using {alternative.display} for now. "
                        "Choose your own shortcut in ORBIT Studio → Behaviour."
                    )
                    self.tray.set_hotkey_label(alternative.display)
                    self.tray.set_state(alternative)
                    if report:
                        self._feedback("Shortcut changed", message, ok=False)
                    else:
                        # At startup a silent fallback would be invisible; tell the user.
                        self.tray.notify("ORBIT — shortcut changed", message, ok=False)
                    return alternative
            if report:
                self._feedback("Shortcut unavailable", state.message, ok=False)
            else:
                self.tray.notify("ORBIT — shortcut unavailable", state.message or "The shortcut could not be registered.", ok=False)
            LOG.error("Shortcut registration failed: %s", state.message)
            return state
        LOG.info("Shortcut active: %s", display)
        return state

    def _on_shortcut_state(self, state: ShortcutState) -> None:
        self.tray.set_state(state)
        if state.ok:
            self.tray.set_hotkey_label(state.display)

    def _on_hotkey(self) -> None:
        self.overlay.toggle()

    def _register_feature_hotkeys(self, settings) -> None:
        """Register optional productivity shortcuts independently; conflicts are reported in the tray."""
        managers = ()
        reserved = {settings.hotkey.strip().casefold()}
        for manager, enabled, hotkey, _callback, label in managers:
            normalized = hotkey.strip().casefold()
            if not enabled:
                manager.stop()
                continue
            if not normalized or normalized in reserved:
                manager.stop()
                message = f"{label} shortcut must be set and differ from other ORBIT shortcuts."
                self.tray.notify(f"ORBIT — {label} shortcut unavailable", message, ok=False)
                continue
            reserved.add(normalized)
            try:
                state = manager.start(hotkey)
                if not state.ok and state.message:
                    self.tray.notify(f"ORBIT — {label} shortcut unavailable", state.message, ok=False)
            except Exception as exc:
                LOG.warning("Could not register %s shortcut: %s", label, exc)
                self.tray.notify(f"ORBIT — {label} shortcut unavailable", str(exc), ok=False)

    def open_search(self) -> None:
        if not self.config_manager.config.settings.search_enabled:
            self._feedback("Search is disabled", "Enable Universal Search in ORBIT Studio → Productivity.", ok=False)
            return
        if self.search_dialog is None:
            self.search_dialog = self.search_dialog_class(self)
            self._feature_windows.append(self.search_dialog)
        self.search_dialog.open_search()

    def open_quick_controls(self) -> None:
        if not self.config_manager.config.settings.quick_controls_enabled:
            self._feedback("Quick Controls are disabled", "Enable them in ORBIT Studio → Productivity.", ok=False)
            return
        if self.quick_controls is None:
            self.quick_controls = self.quick_controls_class(self)
            self._feature_windows.append(self.quick_controls)
        self.quick_controls.show_panel()

    def open_music(self) -> None:
        self._feedback("Music stickers only", "Music controls are available from the radial workspace.", ok=False)

    def open_widgets(self) -> None:
        self.overlay.open_root()

    def _on_pause_toggled(self, paused: bool) -> None:
        config = self.config_manager.config
        if paused:
            self.shortcut.stop()
            LOG.info("Shortcut paused from the tray")
        else:
            self._register_hotkey(config.settings.hotkey, report=True)

    # -------------------------------------------------------------------- actions
    def _on_action_requested(self, action: Action) -> None:
        config = self.config_manager.config

        if self.registry.needs_confirmation(action, config):
            from ui.dialogs import confirm_action

            if not confirm_action(action, self.registry, config, self.theme, self.overlay):
                LOG.info("Action %s cancelled by the user", action.id)
                return

        if action.type == "submenu":
            # Navigation happens on the GUI thread; no worker round trip needed.
            if not self.overlay.open_submenu(action.submenu_id):
                problems = self.registry.validate(action, self.runner.executor.validation_context())
                self._feedback(
                    "Menu not available",
                    problems[0] if problems else "That menu could not be opened.",
                    ok=False,
                )
            return

        if self.registry.hide_before_run(action):
            # Keystroke actions must not type into the overlay itself.
            self.overlay.force_hide()

        should_close = self.runner.executor.should_close_menu(action)
        if should_close:
            self.overlay.dismiss()

        handle = self.runner.submit(action)
        LOG.info("Running action %s (%s)", action.id, action.type)
        if config.settings.show_toasts and self._is_slow(action):
            self.toasts.start(
                handle.run_id,
                f"Running {action.display_name()}",
                self.registry.summary(action, config),
                cancel_callback=lambda: self.runner.cancel(handle.run_id),
            )

    def _is_slow(self, action: Action) -> bool:
        if action.type == "sequence":
            return True
        if action.type == "command":
            return bool(action.option("capture_output", False))
        if action.type == "media" and action.option("method") == "smtc":
            return True
        return False

    def _on_action_finished(self, record) -> None:
        config = self.config_manager.config
        result = record.result

        title = record.action_name if record.ok else f"{record.action_name} failed"
        body = record.message
        if record.detail and not record.ok:
            body = f"{record.message}\n{record.detail}"
        LOG.info(
            "Action %s finished in %d ms (ok=%s): %s",
            record.action_name, record.duration_ms, record.ok, record.message,
        )

        if config.settings.show_toasts:
            self.toasts.finish(record.run_id, title, body, ok=record.ok)
            if not record.ok:
                # A second copy in the tray: the toast may be missed if ORBIT is behind other windows.
                self.tray.notify(f"ORBIT — {title}", (body or "").splitlines()[0], ok=False)
        elif not record.ok:
            self.tray.notify(f"ORBIT — {title}", body or "The action did not complete.", ok=False)

        if not record.ok:
            if config.settings.feedback_pulse:
                self.tray.flash_error()
            if config.settings.feedback_sound:
                QApplication.beep()

        if result is not None and result.keep_menu_open:
            return
        if self.overlay.is_open():
            self.overlay.dismiss()

    def _find_action(self, action_id: str) -> Action | None:
        for _menu, action in self.config_manager.config.iter_actions():
            if action.id == action_id:
                return action
        return None

    # ------------------------------------------------------------------- feedback
    def _feedback(self, title: str, message: str, *, ok: bool = True) -> None:
        config = self.config_manager.config
        if config.settings.show_toasts:
            self.toasts.notify(title, message, ok=ok)
        if not ok:
            self.tray.notify(f"ORBIT — {title}", message, ok=False)
        LOG.warning("%s: %s", title, message)

    def _screen_for_feedback(self):
        screen = self.overlay.current_screen()
        if screen is not None:
            return screen
        return self.app.primaryScreen()

    # ------------------------------------------------------------------ ORBIT cmds
    def _orbit_command(self, target: str) -> tuple[bool, str]:
        if target == "search_settings":
            self.open_studio("productivity")
            return True, "Search settings opened."
        if target == "quick_controls_settings":
            self.open_studio("productivity")
            return True, "Quick Controls settings opened."
        if target == "music_settings":
            self.open_studio("productivity")
            return True, "Music settings opened."
        if target == "widgets_settings":
            self.open_studio("productivity")
            return True, "Widget settings opened."
        if target == "open_search":
            if not self.config_manager.config.settings.search_enabled:
                return False, "Universal Search is disabled in ORBIT Studio → Productivity."
            self.open_search()
            return True, "Universal Search opened."
        if target == "open_quick_controls":
            if not self.config_manager.config.settings.quick_controls_enabled:
                return False, "Quick Controls are disabled in ORBIT Studio → Productivity."
            self.open_quick_controls()
            return True, "Quick Controls opened."
        if target == "open_music":
            if not self.config_manager.config.settings.music_enabled:
                return False, "Music Hub is disabled in ORBIT Studio → Productivity."
            self.open_music()
            return True, "Music Hub opened."
        if target == "open_widgets":
            if not self.config_manager.config.settings.widgets_enabled:
                return False, "All productivity widgets are disabled in ORBIT Studio → Productivity."
            self.open_widgets()
            return True, "Contextual Widgets opened."
        if target == "open_studio":
            self.open_studio()
            return True, "ORBIT Studio opened."
        if target == "open_config_folder":
            try:
                open_with_default(str(paths.app_data_dir()))
            except LaunchError as exc:
                return False, str(exc)
            return True, "Configuration folder opened."
        if target == "reload_config":
            return self.reload_configuration()
        if target == "toggle_overlay":
            self.overlay.toggle()
            return True, "Radial menu toggled."
        if target == "quit":
            QTimer.singleShot(0, self.shutdown)
            return True, "ORBIT is closing."
        return False, f"Unknown ORBIT command: {target}"

    def reload_configuration(self) -> tuple[bool, str]:
        config = self.config_manager.reload()
        self.apply_settings(persist=False)
        notes = self.config_manager.load_errors + self.config_manager.load_warnings
        summary = f"Configuration reloaded — {len(config.menus)} menus."
        if notes:
            summary += "\n" + "\n".join(f"• {item}" for item in notes[:6])
        self._feedback("Configuration reloaded", summary, ok=not self.config_manager.load_errors)
        return True, summary

    def open_config_folder(self) -> None:
        try:
            open_with_default(str(paths.app_data_dir()))
        except LaunchError as exc:
            self._feedback("Could not open the folder", str(exc), ok=False)

    # ------------------------------------------------------------------- settings
    def apply_settings(self, *, persist: bool = False, config: Config | None = None) -> None:
        """Push the current configuration into every live component."""
        active = config or self.config_manager.config
        self.theme = build_theme(active.settings)
        self.overlay.menu.configure(active, self.theme, executor=self.runner.executor)
        self.overlay.refresh()
        self.overlay.update()
        self.toasts.set_theme(self.theme)
        self.tray.set_theme(self.theme)
        self.tray.set_visible(active.settings.show_tray_icon)
        self.tray.set_hotkey_label(format_hotkey(active.settings.hotkey))
        self._register_feature_hotkeys(active.settings)
        if self.music_player is not None:
            self.music_player.apply_settings(active.settings)
        if self.quick_controls is not None:
            self.quick_controls.apply_settings(active.settings)
            if not active.settings.quick_controls_enabled:
                self.quick_controls.hide()
        if self.productivity_panel is not None:
            self.productivity_panel.apply_settings(active.settings)

        if persist:
            try:
                self.config_manager.save(active, reason="settings")
            except OSError as exc:
                self._feedback("Could not save the configuration", str(exc), ok=False)

        if self._studio is not None:
            self._studio.apply_theme(self.theme)
            # A caller that passes an explicit configuration (the studio saving)
            # already holds the newest state; anything else means the file on
            # disk changed underneath the studio, so it is re-read.
            if config is None and not persist:
                self._studio.reload_from_config()

    def apply_hotkey(self, hotkey: str) -> ShortcutState:
        state = self._register_hotkey(hotkey, report=True)
        return state

    # --------------------------------------------------------------------- studio
    def open_studio(self, page: str | None = None) -> None:
        if self._studio is None:
            from ui.studio_window import StudioWindow

            self._studio = StudioWindow(self)
            self._studio.closed.connect(self._on_studio_closed)
        self._studio.apply_theme(self.theme)
        self._studio.show_page(page)
        self._studio.show()
        self._studio.raise_()
        self._studio.activateWindow()

    def _on_studio_closed(self) -> None:
        LOG.info("Studio closed")

    # ------------------------------------------------------------------ image view
    def _open_image_viewer(self, path: str) -> None:
        if self._viewer is not None:
            try:
                self._viewer.close()
            except RuntimeError:
                pass
            self._viewer = None
        viewer = ImageViewer(path, self.theme)
        viewer.closed.connect(lambda: setattr(self, "_viewer", None))
        screen = self._screen_for_feedback()
        geometry = screen.geometry() if screen is not None else QRect(0, 0, 1280, 800)
        viewer.show_on(geometry)
        self._viewer = viewer

    # -------------------------------------------------------------------- shutdown
    def shutdown(self) -> None:
        LOG.info("ORBIT shutting down")
        try:
            self.shortcut.stop()
            self.search_shortcut.stop()
            self.quick_controls_shortcut.stop()
            self.music_shortcut.stop()
            self.widgets_shortcut.stop()
        except Exception:
            pass
        for feature_window in self._feature_windows:
            try:
                if hasattr(feature_window, "shutdown"):
                    feature_window.shutdown()
                else:
                    feature_window.close()
            except Exception:
                LOG.exception("Error closing productivity feature window")
        self.productivity.save()
        try:
            self.runner.shutdown()
        except Exception:
            pass
        self.overlay.force_hide()
        self.toasts.hide_all()
        self.tray.hide()
        if self._viewer is not None:
            try:
                self._viewer.close()
            except RuntimeError:
                pass
        if self._studio is not None:
            try:
                self._studio.close()
            except RuntimeError:
                pass
        self.app.quit()


# ------------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orbit",
        description="ORBIT — a customizable desktop action wheel.",
    )
    parser.add_argument("--studio", action="store_true", help="open ORBIT Studio at launch")
    parser.add_argument(
        "--background",
        action="store_true",
        help="start quietly in the tray (used by launch on login)",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="do not create the tray icon (the global shortcut still works)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="use a specific configuration file instead of the default location",
    )
    parser.add_argument(
        "--reset-config",
        action="store_true",
        help="back up the current configuration and start from the defaults",
    )
    parser.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING or ERROR")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        from core import __doc__ as _doc  # noqa: F401

        print("ORBIT 1.0")
        return 0

    configure_logging(args.log_level)

    app = QApplication(sys.argv[:1] + (argv if argv is not None else []))
    app.setApplicationName("ORBIT")
    app.setApplicationDisplayName("ORBIT")
    app.setOrganizationName("ORBIT")
    app.setQuitOnLastWindowClosed(False)

    controller = AppController(app, config_path=Path(args.config) if args.config else None)
    app.setWindowIcon(QIcon(_app_icon_pixmap(controller.theme)))
    app.setStyleSheet(studio_stylesheet(controller.theme))
    app.aboutToQuit.connect(lambda: LOG.info("Event loop finished"))

    if args.reset_config:
        ok, message = controller.config_manager.reset_to_defaults()
        LOG.info("Reset requested: %s", message)
        controller.config_manager.reload()

    server = _claim_single_instance(app)
    if server is None:
        LOG.info("Another ORBIT instance is already running; asking it to show the menu.")
        return 0

    def on_new_connection() -> None:
        connection = server.nextPendingConnection()
        if connection is None:
            return
        connection.readyRead.connect(lambda: controller.overlay.open_root())
        connection.disconnected.connect(connection.deleteLater)

    server.newConnection.connect(on_new_connection)

    controller.start(visible_tray=not args.no_tray)
    if args.studio:
        controller.open_studio()
    if not args.background and not args.studio:
        LOG.info("ORBIT is running. Press %s to open the menu.", controller.config_manager.config.settings.hotkey)
        if not controller.shortcut.is_registered:
            QTimer.singleShot(0, lambda: controller.tray.notify(
                "ORBIT is running",
                "The activation shortcut is not available. Open the tray icon to use the menu "
                "or pick another shortcut in ORBIT Studio → Behaviour.",
                ok=False,
            ))
    return app.exec()


def _app_icon_pixmap(theme: Theme):
    from ui import icons

    return icons.icon_pixmap("orbit", 128, QColor(theme.accent))


if __name__ == "__main__":
    raise SystemExit(main())
