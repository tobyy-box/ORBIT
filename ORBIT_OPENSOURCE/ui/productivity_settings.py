"""Settings for universal search, quick controls, local music and widgets."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)

from core.keycodes import validate_hotkey
from ui.widgets import Card, HotkeyEdit, Page, Row, ScrollPage, combo, muted_label, section_label


class ProductivitySettingsPage(Page):
    title = "Productivity"
    description = "Universal Search, Windows controls, local music and contextual widgets."

    def __init__(self, studio, parent=None) -> None:
        super().__init__(studio, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.page = ScrollPage()
        outer.addWidget(self.page)
        body = self.page.body()
        body.addWidget(self.header())
        self._build_search(body)
        self._build_controls(body)
        self._build_music(body)
        self._build_widgets(body)
        self.page.finish()
        self.load()

    def _build_search(self, body) -> None:
        card = Card()
        card.add(section_label("Universal Search"))
        self.search_enabled = QCheckBox("Enable Universal Search")
        self.search_enabled.toggled.connect(lambda v: self._set("search_enabled", v))
        card.add(self.search_enabled)
        self.search_hotkey = HotkeyEdit(self.working.settings.search_hotkey)
        self.search_hotkey.captured.connect(lambda value: self._hotkey("search_hotkey", value))
        card.add_row(Row("Global shortcut", self.search_hotkey, help_text="Works even when another app is focused. Must differ from other ORBIT shortcuts."))
        self.index_apps = QCheckBox("Index installed application shortcuts")
        self.index_apps.toggled.connect(lambda v: self._set("search_index_apps", v))
        card.add(self.index_apps)
        self.index_files = QCheckBox("Index files and folders recursively in configured folders")
        self.index_files.toggled.connect(lambda v: self._set("search_index_files", v))
        card.add(self.index_files)
        self.search_folders = QListWidget()
        self.search_folders.setMaximumHeight(110)
        card.add(self.search_folders)
        row = QHBoxLayout()
        add = QPushButton("Add indexed folder…")
        remove = QPushButton("Remove selected")
        add.clicked.connect(self._add_search_folder)
        remove.clicked.connect(self._remove_search_folder)
        row.addWidget(add); row.addWidget(remove); holder = __import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget(); holder.setLayout(row); card.add(holder)
        self.excluded = QListWidget(); self.excluded.setMaximumHeight(90); card.add(self.excluded)
        row = QHBoxLayout(); add_excluded = QPushButton("Exclude folder…"); remove_excluded = QPushButton("Remove exclusion")
        add_excluded.clicked.connect(self._add_excluded); remove_excluded.clicked.connect(self._remove_excluded)
        row.addWidget(add_excluded); row.addWidget(remove_excluded); holder = __import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget(); holder.setLayout(row); card.add(holder)
        card.add(muted_label("Search indexes the Start Menu and only folders you choose; it does not scan the whole disk. Excluded folders are skipped during indexing."))
        body.addWidget(card)

    def _build_controls(self, body) -> None:
        card = Card(); card.add(section_label("Quick Controls"))
        self.controls_enabled = QCheckBox("Enable Quick Controls")
        self.controls_enabled.toggled.connect(lambda v: self._set("quick_controls_enabled", v)); card.add(self.controls_enabled)
        self.quick_controls_hotkey = HotkeyEdit(self.working.settings.quick_controls_hotkey)
        self.quick_controls_hotkey.captured.connect(lambda value: self._hotkey("quick_controls_hotkey", value))
        card.add_row(Row("Global shortcut", self.quick_controls_hotkey))
        self.controls = QListWidget(); self.controls.setMaximumHeight(150)
        self.control_keys = [("audio", "System volume and output"), ("wifi", "Wi-Fi settings"), ("bluetooth", "Bluetooth settings"), ("display", "Display settings"), ("power", "Power & battery settings"), ("sound", "Sound settings"), ("network", "Network settings")]
        for key, label in self.control_keys:
            item = QListWidgetItem(label); item.setData(Qt.ItemDataRole.UserRole, key); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable); self.controls.addItem(item)
        card.add(self.controls)
        row = QHBoxLayout(); up = QPushButton("Move up"); down = QPushButton("Move down"); up.clicked.connect(lambda: self._move_control(-1)); down.clicked.connect(lambda: self._move_control(1)); row.addWidget(up); row.addWidget(down)
        from PySide6.QtWidgets import QWidget
        holder = QWidget(); holder.setLayout(row); card.add(holder)
        card.add(muted_label("Volume and mute modify the Windows audio endpoint. Wi-Fi, Bluetooth, display, sound, power and network open their Windows Settings page; ORBIT does not claim to toggle those switches directly."))
        body.addWidget(card)

    def _build_music(self, body) -> None:
        card = Card(); card.add(section_label("Music Hub · local files"))
        self.music_enabled = QCheckBox("Enable independent local music player")
        self.music_enabled.toggled.connect(lambda v: self._set("music_enabled", v)); card.add(self.music_enabled)
        self.music_hotkey = HotkeyEdit(self.working.settings.music_hotkey)
        self.music_hotkey.captured.connect(lambda value: self._hotkey("music_hotkey", value))
        card.add_row(Row("Global shortcut", self.music_hotkey))
        self.music_folders = QListWidget(); self.music_folders.setMaximumHeight(110); card.add(self.music_folders)
        row = QHBoxLayout(); add = QPushButton("Add music folder…"); remove = QPushButton("Remove selected")
        add.clicked.connect(self._add_music_folder); remove.clicked.connect(self._remove_music_folder); row.addWidget(add); row.addWidget(remove)
        from PySide6.QtWidgets import QWidget
        holder = QWidget(); holder.setLayout(row); card.add(holder)
        self.auto_next = QCheckBox("Play the next queued track automatically"); self.auto_next.toggled.connect(lambda v: self._set("music_auto_next", v)); card.add(self.auto_next)
        self.music_resume = QCheckBox("Restore the previous track and position at launch"); self.music_resume.toggled.connect(lambda v: self._set("music_resume", v)); card.add(self.music_resume)
        self.music_shuffle = QCheckBox("Shuffle playback by default"); self.music_shuffle.toggled.connect(lambda v: self._set("music_shuffle", v)); card.add(self.music_shuffle)
        self.music_repeat = combo((("off", "Repeat off"), ("all", "Repeat queue"), ("one", "Repeat track")), self.working.settings.music_repeat, on_change=lambda v: self._set("music_repeat", v))
        card.add_row(Row("Repeat", self.music_repeat))
        self.media_hotkeys = QCheckBox("Enable media keys while the Music Hub has focus"); self.media_hotkeys.toggled.connect(lambda v: self._set("music_hotkeys_enabled", v)); card.add(self.media_hotkeys)
        card.add(muted_label("Playback uses Qt Multimedia and local MP3, WAV, FLAC and OGG files. Music continues when the player window is hidden; it stops only when ORBIT exits."))
        body.addWidget(card)

    def _build_widgets(self, body) -> None:
        card = Card(); card.add(section_label("Contextual Widgets"))
        self.widget_boxes = {}
        self.widgets_hotkey = HotkeyEdit(self.working.settings.widgets_hotkey)
        self.widgets_hotkey.captured.connect(lambda value: self._hotkey("widgets_hotkey", value))
        card.add_row(Row("Global shortcut", self.widgets_hotkey))
        labels = (("timer", "Timer and stopwatch"), ("notes", "Quick notes"), ("tasks", "Task list"), ("system", "System information"))
        for key, label in labels:
            box = QCheckBox(label); box.toggled.connect(lambda _v, k=key: self._collect_widgets()); self.widget_boxes[key] = box; card.add(box)
        card.add(muted_label("Timers use wall-clock deadlines and keep running while the widget panel is hidden. Notes, tasks and timers are stored locally in %APPDATA%\\ORBIT\\productivity.json. Pin any task from Widgets → Tasks to keep it visible in a compact always-on-top checklist."))
        body.addWidget(card)

    def load(self) -> None:
        settings = self.working.settings
        self.search_enabled.setChecked(settings.search_enabled)
        self.search_hotkey.set_value(settings.search_hotkey)
        self.quick_controls_hotkey.set_value(settings.quick_controls_hotkey)
        self.music_hotkey.set_value(settings.music_hotkey)
        self.widgets_hotkey.set_value(settings.widgets_hotkey)
        self.index_apps.setChecked(settings.search_index_apps); self.index_files.setChecked(settings.search_index_files)
        self._fill_list(self.search_folders, settings.search_folders); self._fill_list(self.excluded, settings.search_excluded)
        for index in range(self.controls.count()):
            item = self.controls.item(index); item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in settings.quick_controls else Qt.CheckState.Unchecked)
        self.controls_enabled.setChecked(settings.quick_controls_enabled)
        self._fill_list(self.music_folders, settings.music_folders)
        self.music_enabled.setChecked(settings.music_enabled); self.auto_next.setChecked(settings.music_auto_next)
        self.music_resume.setChecked(settings.music_resume); self.music_shuffle.setChecked(settings.music_shuffle)
        self.music_repeat.setCurrentIndex(max(0, self.music_repeat.findData(settings.music_repeat)))
        self.media_hotkeys.setChecked(settings.music_hotkeys_enabled)
        for key, box in self.widget_boxes.items(): box.setChecked(key in settings.widgets_enabled)

    @staticmethod
    def _fill_list(widget, values: list[str]) -> None:
        widget.clear(); widget.addItems(values)

    def _set(self, key: str, value) -> None:
        setattr(self.working.settings, key, value)
        if key == "search_folders": self._fill_list(self.search_folders, value)
        elif key == "search_excluded": self._fill_list(self.excluded, value)
        elif key == "music_folders": self._fill_list(self.music_folders, value)
        elif key == "quick_controls":
            for index in range(self.controls.count()):
                item=self.controls.item(index); item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in value else Qt.CheckState.Unchecked)
        self.notify_changed()

    def _hotkey(self, key: str, value: str) -> None:
        problem = validate_hotkey(value)
        if problem:
            self.studio.set_status(problem)
            return
        existing = {self.working.settings.hotkey, self.working.settings.search_hotkey,
                    self.working.settings.quick_controls_hotkey, self.working.settings.music_hotkey,
                    self.working.settings.widgets_hotkey}
        existing.discard(getattr(self.working.settings, key))
        if value.strip().casefold() in {item.strip().casefold() for item in existing}:
            self.studio.set_status("Each ORBIT feature must have a different global shortcut.")
            return
        self._set(key, value)

    def _add_search_folder(self) -> None:
        folder=QFileDialog.getExistingDirectory(self,"Add search folder",str(Path.home()))
        if folder and folder not in self.working.settings.search_folders:self._set("search_folders",[*self.working.settings.search_folders,folder])

    def _remove_search_folder(self) -> None:
        row=self.search_folders.currentRow()
        if row>=0:self._set("search_folders",[v for i,v in enumerate(self.working.settings.search_folders) if i!=row])

    def _add_excluded(self) -> None:
        folder=QFileDialog.getExistingDirectory(self,"Exclude folder",str(Path.home()))
        if folder and folder not in self.working.settings.search_excluded:self._set("search_excluded",[*self.working.settings.search_excluded,folder])

    def _remove_excluded(self) -> None:
        row=self.excluded.currentRow()
        if row>=0:self._set("search_excluded",[v for i,v in enumerate(self.working.settings.search_excluded) if i!=row])

    def _add_music_folder(self) -> None:
        folder=QFileDialog.getExistingDirectory(self,"Add music folder",str(Path.home()))
        if folder and folder not in self.working.settings.music_folders:self._set("music_folders",[*self.working.settings.music_folders,folder])

    def _remove_music_folder(self) -> None:
        row=self.music_folders.currentRow()
        if row>=0:self._set("music_folders",[v for i,v in enumerate(self.working.settings.music_folders) if i!=row])

    def _collect_widgets(self) -> None:
        self.working.settings.widgets_enabled=[key for key,box in self.widget_boxes.items() if box.isChecked()]
        self.working.settings.widgets_order=list(self.working.settings.widgets_enabled)
        self.notify_changed()

    def _move_control(self, delta: int) -> None:
        row=self.controls.currentRow(); target=row+delta
        if not (0<=row<self.controls.count() and 0<=target<self.controls.count()):return
        item=self.controls.takeItem(row);self.controls.insertItem(target,item);self.controls.setCurrentRow(target)
        self._set("quick_controls",[self.controls.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.controls.count()) if self.controls.item(i).checkState()==Qt.CheckState.Checked])
