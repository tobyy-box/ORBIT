"""ORBIT Studio: the configuration window.

The studio edits a *working copy* of the configuration.  Nothing is written
until Save is pressed, the overlay only changes when the working copy is
applied, and every save keeps a timestamped backup of the previous file.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core import paths
from core.config_manager import sanitize, validate_config
from core.launcher import LaunchError, open_with_default
from core.keycodes import format_hotkey
from core.models import Config
from ui import icons
from ui.radial_editor import MenusPage
from ui.settings_pages import AppearancePage, BehaviourPage
from ui.productivity_settings import ProductivitySettingsPage
from ui.theme_manager import Theme, studio_stylesheet
from ui.widgets import Card, Page, ScrollPage, muted_label, section_label

VERSION = "1.0"

NAV_ITEMS = (
    ("menus", "Radial wheel", "layers"),
    ("appearance", "Appearance", "palette"),
    ("behaviour", "Behaviour", "sliders"),
)

# Kept available internally for migration/backward-compatible tests, but no
# longer exposed as Studio navigation choices.
HIDDEN_PAGE_KEYS = ("data", "about")


class StudioWindow(QMainWindow):
    """Main configuration window."""

    closed = Signal()

    def __init__(self, controller) -> None:
        super().__init__()
        self.controller = controller
        self.working: Config = controller.config_manager.config.clone()
        self._dirty = False
        self._theme_name = ""
        self._pages: dict[str, Page] = {}

        self.setWindowTitle("ORBIT Studio — Design your wheel")
        self.setWindowIcon(QIcon(icons.icon_pixmap("orbit", 64, QColor(controller.theme.accent))))
        self.resize(1340, 860)
        self.setMinimumSize(QSize(1080, 680))

        root = QWidget()
        root.setObjectName("StudioRoot")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.setStatusBar(status)
        self.show_status_ready()

        self.apply_theme(controller.theme, force=True)
        self.show_page("menus")

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(246)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 18, 14, 16)
        layout.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.icon_pixmap("orbit", 26, QColor(self.controller.theme.accent)))
        brand.addWidget(logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        name = QLabel("ORBIT")
        name.setObjectName("SidebarTitle")
        titles.addWidget(name)
        subtitle = QLabel("Studio")
        subtitle.setObjectName("SidebarSubtitle")
        titles.addWidget(subtitle)
        brand.addLayout(titles)
        brand.addStretch(1)
        layout.addLayout(brand)
        intro = QLabel("Shape your shortcuts, tune the look,\nand make the wheel feel like yours.")
        intro.setObjectName("SidebarIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self._nav_buttons: dict[str, QPushButton] = {}
        for key, label, icon in NAV_ITEMS:
            button = QPushButton(f"  {label}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setIcon(QIcon(icons.icon_pixmap(icon, 18, QColor(self.controller.theme.text_muted))))
            button.setIconSize(QSize(17, 17))
            button.clicked.connect(lambda _checked, target=key: self.show_page(target))
            self.nav_group.addButton(button)
            self._nav_buttons[key] = button
            layout.addWidget(button)

        layout.addStretch(1)

        self._dirty_label = muted_label("No unsaved changes")
        layout.addWidget(self._dirty_label)

        self._save_button = QPushButton("Save changes")
        self._save_button.setObjectName("Primary")
        self._save_button.clicked.connect(self.save)
        self._save_button.setEnabled(False)
        layout.addWidget(self._save_button)

        self._revert_button = QPushButton("Revert")
        self._revert_button.clicked.connect(self.revert)
        self._revert_button.setEnabled(False)
        layout.addWidget(self._revert_button)

        return sidebar

    # -------------------------------------------------------------------- pages
    def _page_factory(self, key: str) -> Page:
        if key == "menus":
            return MenusPage(self)
        if key == "appearance":
            return AppearancePage(self)
        if key == "behaviour":
            return BehaviourPage(self)
        if key == "productivity":
            return ProductivitySettingsPage(self)
        if key == "data":
            return DataPage(self)
        return AboutPage(self)

    def _rebuild_pages(self, *, keep_page: str | None = None) -> None:
        current = keep_page or self._current_page_key()
        while self.stack.count():
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)
            widget.deleteLater()
        self._pages = {}
        for key in [item[0] for item in NAV_ITEMS] + list(HIDDEN_PAGE_KEYS):
            page = self._page_factory(key)
            page.changed.connect(self._mark_dirty)
            self._pages[key] = page
            self.stack.addWidget(page)
        self.show_page(current)

    def _current_page_key(self) -> str:
        index = self.stack.currentIndex()
        keys = list(self._pages)
        if 0 <= index < len(keys):
            return keys[index]
        return "menus"

    def show_page(self, key: str | None) -> None:
        if key is None:
            key = "menus"
        if key not in self._pages:
            key = "menus"
        page = self._pages[key]
        self.stack.setCurrentWidget(page)
        button = self._nav_buttons.get(key)
        if button is not None and not button.isChecked():
            button.setChecked(True)

    def reload_from_config(self) -> None:
        self.working = self.controller.config_manager.config.clone()
        self._set_dirty(False)
        self._rebuild_pages()

    # -------------------------------------------------------------------- theme
    def apply_theme(self, theme: Theme, *, force: bool = False) -> None:
        if not force and theme.name == self._theme_name:
            self.setStyleSheet(studio_stylesheet(theme))
            return
        self._theme_name = theme.name
        self.setStyleSheet(studio_stylesheet(theme))
        self._rebuild_pages()

    # -------------------------------------------------------------------- state
    def set_status(self, message: str, *, timeout: int = 6000) -> None:
        self.statusBar().showMessage(message, timeout)

    def show_status_ready(self) -> None:
        counts = (len(self.working.menus), len(self.working.used_action_ids()))
        self.statusBar().showMessage(
            f"{counts[0]} menus · {counts[1]} actions · configuration: {self.controller.config_manager.path}"
        )

    def _mark_dirty(self) -> None:
        self._set_dirty(True)

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._save_button.setEnabled(dirty)
        self._revert_button.setEnabled(dirty)
        self._dirty_label.setText(
            "Unsaved changes" if dirty else "No unsaved changes"
        )
        self._dirty_label.setStyleSheet(
            f"color: {self.controller.theme.warning};" if dirty else ""
        )

    @property
    def dirty(self) -> bool:
        return self._dirty

    # --------------------------------------------------------------------- save
    def save(self) -> bool:
        report = validate_config(self.working.to_dict())
        if report.errors:
            QMessageBox.critical(
                self,
                "This configuration cannot be saved yet",
                "Fix these problems first:\n\n"
                + "\n".join(f"•  {item}" for item in report.errors[:10]),
            )
            return False

        config, notes = sanitize(self.working)
        self.working = config
        previous_hotkey = self.controller.config_manager.config.settings.hotkey
        try:
            self.controller.config_manager.save(self.working, reason="studio")
        except OSError as exc:
            QMessageBox.critical(self, "Could not save", f"The configuration could not be written:\n{exc}")
            return False

        self.controller.apply_settings(config=self.working)

        hotkey = self.working.settings.hotkey
        if hotkey != previous_hotkey:
            state = self.controller.apply_hotkey(hotkey)
            self.set_status(state.message.replace("\n", " ") or f"Shortcut: {format_hotkey(hotkey)}")

        self._set_dirty(False)
        self.show_status_ready()
        message = "Configuration saved."
        if notes:
            message += " " + " ".join(notes[:2])
        self.set_status(message)
        return True

    def revert(self) -> None:
        if self._dirty:
            answer = QMessageBox.question(
                self,
                "Discard changes?",
                "Unsaved changes will be lost. Your saved configuration stays as it is.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.reload_from_config()
        self.set_status("Changes reverted to the saved configuration.")

    # -------------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._dirty:
            box = QMessageBox(self)
            box.setWindowTitle("Unsaved changes")
            box.setText("Save your changes before closing ORBIT Studio?")
            box.setIcon(QMessageBox.Icon.Question)
            box.setStandardButtons(
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel
            )
            box.setDefaultButton(QMessageBox.StandardButton.Save)
            choice = box.exec()
            if choice == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Save and not self.save():
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Discard:
                self.working = self.controller.config_manager.config.clone()
        self.closed.emit()
        super().closeEvent(event)


class DataPage(Page):
    """Import, export, backups and reset."""

    title = "Import & backups"
    description = "Move a configuration between machines, or roll back to an earlier one."

    def __init__(self, studio, parent: QWidget | None = None) -> None:
        super().__init__(studio, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.page = ScrollPage()
        layout.addWidget(self.page)
        body = self.page.body()
        body.addWidget(self.header())

        card = Card()
        card.add(section_label("Transfer"))
        export_button = QPushButton("Export configuration…")
        export_button.clicked.connect(self._export)
        card.add(export_button)
        import_button = QPushButton("Import configuration…")
        import_button.clicked.connect(self._import)
        card.add(import_button)
        open_folder = QPushButton("Open the configuration folder")
        open_folder.clicked.connect(lambda: self._open(paths.app_data_dir()))
        card.add(open_folder)
        card.add(
            muted_label(
                "Importing validates the file first and backs up your current configuration. "
                "A file that fails validation is never applied."
            )
        )
        body.addWidget(card)

        backups = Card()
        backups.add(section_label("Backups"))
        self._backup_list = QListWidget()
        self._backup_list.setFixedHeight(190)
        backups.add(self._backup_list)
        row = QHBoxLayout()
        row.setSpacing(6)
        restore = QPushButton("Restore selected")
        restore.clicked.connect(self._restore)
        row.addWidget(restore)
        refresh = QPushButton("Refresh list")
        refresh.clicked.connect(self._refresh_backups)
        row.addWidget(refresh)
        reveal = QPushButton("Show backup folder")
        reveal.clicked.connect(lambda: self._open(paths.backups_dir()))
        row.addWidget(reveal)
        row.addStretch(1)
        backups.body().addLayout(row)
        body.addWidget(backups)

        danger = Card()
        danger.add(section_label("Reset"))
        reset = QPushButton("Restore ORBIT's default configuration")
        reset.setObjectName("Danger")
        reset.clicked.connect(self._reset)
        danger.add(reset)
        danger.add(muted_label("Your current configuration is backed up automatically first."))
        body.addWidget(danger)

        info = Card()
        info.add(section_label("Current configuration"))
        self._info = QLabel("")
        self._info.setObjectName("Muted")
        self._info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.add(self._info)
        body.addWidget(info)
        self.page.finish()
        self.load()

    def load(self) -> None:
        self._refresh_backups()
        self._refresh_info()

    def _refresh_info(self) -> None:
        config = self.working
        teams = len(config.menus)
        actions = len(config.used_action_ids())
        hotkey = format_hotkey(config.settings.hotkey)
        self._info.setText(
            f"File: {self.controller.config_manager.path}\n"
            f"Version: {VERSION} (config schema {config.version})\n"
            f"Menus: {teams}   Actions: {actions}\n"
            f"Activation: {hotkey}   Theme: {config.settings.theme}"
        )

    def _refresh_backups(self) -> None:
        self._backup_list.clear()
        self._backups = self.controller.config_manager.list_backups()
        for path in self._backups:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            try:
                size = path.stat().st_size
                item.setToolTip(f"{path}\n{size} bytes")
            except OSError:
                pass
            self._backup_list.addItem(item)
        if not self._backups:
            self._backup_list.addItem("No backups yet — one is created on every save.")

    def _export(self) -> None:
        default = str(Path.home() / "orbit-config.json")
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "Export configuration", default, "JSON files (*.json)"
        )
        if not chosen:
            return
        ok, message = self.controller.config_manager.export_to(Path(chosen), config=self.working)
        self.studio.set_status(message.replace("\n", " "))
        if not ok:
            QMessageBox.critical(self, "Export failed", message)

    def _import(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "Import configuration", str(Path.home()), "JSON files (*.json)"
        )
        if not chosen:
            return
        answer = QMessageBox.question(
            self,
            "Import this configuration?",
            "Your current configuration will be backed up first, then replaced.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ok, messages = self.controller.config_manager.import_from(Path(chosen))
        if not ok:
            QMessageBox.critical(self, "Import stopped", "\n".join(f"•  {item}" for item in messages))
            return
        self.controller.config_manager.reload()
        self.controller.apply_settings()
        self.studio.reload_from_config()
        self.studio.set_status(" ".join(messages[:3]))
        QMessageBox.information(self, "Imported", "\n".join(messages))

    def _restore(self) -> None:
        items = self._backup_list.selectedItems()
        if not items:
            return
        target = items[0].data(Qt.ItemDataRole.UserRole)
        if not target:
            return
        answer = QMessageBox.question(
            self,
            "Restore this backup?",
            f"{Path(target).name} will replace the current configuration.\n"
            "The current file is backed up first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ok, message = self.controller.config_manager.restore_backup(Path(target))
        if not ok:
            QMessageBox.critical(self, "Restore failed", message)
            return
        self.controller.apply_settings()
        self.studio.reload_from_config()
        self.studio.set_status(message)

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self,
            "Restore the defaults?",
            "Every menu and action you configured will be replaced by ORBIT's defaults.\n\n"
            "Your current configuration is backed up first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ok, message = self.controller.config_manager.reset_to_defaults()
        self.controller.apply_settings()
        self.studio.reload_from_config()
        self.studio.set_status(message.replace("\n", " "))

    def _open(self, path) -> None:
        try:
            open_with_default(str(path))
        except LaunchError as exc:
            QMessageBox.warning(self, "Could not open", str(exc))


class AboutPage(Page):
    """Version, quick start and support information."""

    title = "About ORBIT"
    description = "A radial action wheel for Windows that stays out of your way."

    def __init__(self, studio, parent: QWidget | None = None) -> None:
        super().__init__(studio, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.page = ScrollPage()
        layout.addWidget(self.page)
        body = self.page.body()
        body.addWidget(self.header())

        quick = Card()
        quick.add(section_label("Quick start"))
        quick.add(
            muted_label(
                "1.  Press the activation shortcut (or click the tray icon) to open the wheel.\n"
                "2.  Hover an action to read its description, then click or press Enter to run it.\n"
                "3.  Use the arrow keys, the number keys or the first letters of a name to select.\n"
                "4.  Escape closes the wheel, Backspace returns to the previous menu."
            )
        )
        body.addWidget(quick)

        stats = Card()
        stats.add(section_label("This installation"))
        self._stats = QLabel("")
        stats.add(self._stats)
        body.addWidget(stats)

        paths_card = Card()
        paths_card.add(section_label("Files"))
        self._paths = QLabel("")
        self._paths.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._paths.setObjectName("Muted")
        paths_card.add(self._paths)
        row = QHBoxLayout()
        row.setSpacing(6)
        open_config = QPushButton("Open configuration folder")
        open_config.clicked.connect(lambda: self._open(paths.app_data_dir()))
        row.addWidget(open_config)
        open_log = QPushButton("Open the log file")
        open_log.clicked.connect(lambda: self._open(paths.log_path()))
        row.addWidget(open_log)
        row.addStretch(1)
        paths_card.body().addLayout(row)
        body.addWidget(paths_card)

        notes = Card()
        notes.add(section_label("Good to know"))
        notes.add(
            muted_label(
                "•  Commands run directly, without a shell, unless you enable “Run through cmd.exe”.\n"
                "•  ORBIT never requests administrator rights and installs no keyboard hooks.\n"
                "•  Media control uses the standard Windows media keys, so it works with any player "
                "that handles them.\n"
                "•  A missing file or program is reported instead of failing silently."
            )
        )
        body.addWidget(notes)
        self.page.finish()
        self.load()

    def load(self) -> None:
        config = self.working
        state = self.controller.shortcut.state
        self._stats.setText(
            f"ORBIT {VERSION}\n"
            f"Menus: {len(config.menus)}   Actions: {len(config.used_action_ids())}\n"
            f"Activation: {state.display or config.settings.hotkey}"
            + ("" if state.ok else "  (not registered)")
        )
        self._paths.setText(
            f"Configuration: {self.controller.config_manager.path}\n"
            f"Backups: {paths.backups_dir()}\n"
            f"Log: {paths.log_path()}"
        )

    def _open(self, path) -> None:
        try:
            open_with_default(str(path))
        except LaunchError as exc:
            QMessageBox.warning(self, "Could not open", str(exc))
