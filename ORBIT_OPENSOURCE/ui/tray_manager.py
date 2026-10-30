"""System tray presence.

The tray icon is the guaranteed way to reach ORBIT when the global shortcut is
taken, and it always offers a way to close the overlay.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from core.shortcut_manager import ShortcutState
from ui import icons
from ui.theme_manager import Theme


class TrayManager(QObject):
    """Owns the tray icon, its menu and balloon notifications."""

    showMenuRequested = Signal()
    openStudioRequested = Signal()
    reloadConfigRequested = Signal()
    openConfigFolderRequested = Signal()
    changeShortcutRequested = Signal()
    searchRequested = Signal()
    quickControlsRequested = Signal()
    musicRequested = Signal()
    widgetsRequested = Signal()
    aboutRequested = Signal()
    quitRequested = Signal()
    pauseToggled = Signal(bool)

    def __init__(self, theme: Theme, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.tray = QSystemTrayIcon(self._icon(theme), parent)
        self.tray.setToolTip("ORBIT — radial action menu")
        self._paused = False
        self._flash_timer: QTimer | None = None
        self._flash_remaining = 0
        self._state = ShortcutState(False, message="Starting…")
        self._build_menu()
        self.tray.activated.connect(self._on_activated)

    # --------------------------------------------------------------------- setup
    def _icon(self, theme: Theme) -> QIcon:
        return QIcon(icons.icon_pixmap("orbit", 64, QColor(theme.accent)))

    def _build_menu(self) -> None:
        menu = QMenu()
        menu.setToolTipsVisible(True)

        self._status = QAction("Activation: starting…", menu)
        self._status.setEnabled(False)
        menu.addAction(self._status)
        menu.addSeparator()

        self.show_action = QAction("Show radial menu", menu)
        self.show_action.triggered.connect(self.showMenuRequested.emit)
        menu.addAction(self.show_action)

        studio = QAction("Open ORBIT Studio…", menu)
        studio.triggered.connect(self.openStudioRequested.emit)
        menu.addAction(studio)
        # Keep the tray menu focused on the radial workspace and its editor.
        self.pause_action = QAction("Pause the shortcut", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self._on_pause)
        quit_action = QAction("Quit ORBIT", menu)
        quit_action.triggered.connect(self.quitRequested.emit)
        menu.addAction(quit_action)

        self.menu = menu
        self.tray.setContextMenu(menu)

    # ------------------------------------------------------------------- control
    def install(self, *, visible: bool = True) -> bool:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        self.tray.setVisible(visible)
        return True

    def set_visible(self, visible: bool) -> None:
        self.tray.setVisible(bool(visible) and QSystemTrayIcon.isSystemTrayAvailable())

    def set_theme(self, theme: Theme) -> None:
        self.theme = theme
        self.tray.setIcon(self._icon(theme))

    def set_state(self, state: ShortcutState) -> None:
        self._state = state
        display = state.display or state.hotkey or "not set"
        if self._paused:
            self._status.setText("Activation: paused")
        elif state.ok:
            self._status.setText(f"Activation: {display}")
        elif state.conflict:
            self._status.setText(f"Activation: {display} already in use")
        else:
            self._status.setText(f"Activation: {display} unavailable")
        self.show_action.setToolTip(
            f"Open the radial menu ({display})" if state.ok else "Open the radial menu"
        )
        self.tray.setToolTip(
            f"ORBIT — {self._status.text()}" if state.ok or self._paused else f"ORBIT — {state.message or self._status.text()}"
        )

    def set_hotkey_label(self, label: str) -> None:
        self.show_action.setText(f"Show radial menu   ({label})" if label else "Show radial menu")

    def set_search_shortcut_state(self, state: ShortcutState) -> None:
        if hasattr(self, "search_action"):
            self.search_action.setText("Universal Search…   (" + (state.display or state.hotkey or "unavailable") + ")")
            self.search_action.setToolTip(state.message or "Open Universal Search")

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if self.pause_action.isChecked() != paused:
            self.pause_action.blockSignals(True)
            self.pause_action.setChecked(paused)
            self.pause_action.blockSignals(False)
        self.set_state(self._state)

    def flash_error(self, *, times: int = 3, interval_ms: int = 220) -> None:
        """Blink the tray icon in the danger colour (optional visual feedback)."""
        if not self.tray.isVisible():
            return
        self._flash_remaining = max(1, int(times))
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(max(80, interval_ms))
        self._flash_timer.timeout.connect(self._flash_step)
        self._flash_timer.start()

    def _flash_step(self) -> None:
        self._flash_remaining -= 1
        if self._flash_remaining <= 0:
            self._flash_timer.stop()
            self.tray.setIcon(self._icon(self.theme))
            return
        alert = QColor(self.theme.danger)
        self.tray.setIcon(QIcon(icons.icon_pixmap("alert", 64, alert)))
        QTimer.singleShot(90, lambda: self.tray.setIcon(self._icon(self.theme)))

    def notify(self, title: str, message: str, *, ok: bool = True) -> None:
        if not self.tray.isVisible():
            return
        icon = QSystemTrayIcon.MessageIcon.Information if ok else QSystemTrayIcon.MessageIcon.Warning
        self.tray.showMessage(title, message[:900], icon, 6000)

    def _on_pause(self, paused: bool) -> None:
        self._paused = paused
        self.pauseToggled.emit(paused)
        self.set_state(self._state)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self.showMenuRequested.emit()

    def hide(self) -> None:
        self.tray.hide()
