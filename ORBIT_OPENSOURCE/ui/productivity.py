"""Native PySide6 panels for ORBIT's productivity features."""
from __future__ import annotations

import base64
import os
import random
import subprocess
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSlider, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget, QFrame, QSizePolicy,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from core.productivity import AUDIO_EXTENSIONS, MusicLibrary, ProductivityStore, fuzzy_score, new_record
from ui.icons import icon_pixmap


def _normalized_path(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(os.path.expandvars(os.fspath(path)))))


def _is_excluded(path: str | os.PathLike[str], excluded: list[str]) -> bool:
    candidate = _normalized_path(path)
    for value in excluded:
        root = _normalized_path(value)
        try:
            if os.path.commonpath((candidate, root)) == root:
                return True
        except ValueError:
            continue
    return False


def _walk_search_paths(root: Path, excluded: list[str]):
    if _is_excluded(root, excluded):
        return
    for current, dirs, files in os.walk(root, onerror=lambda _error: None):
        dirs[:] = [name for name in dirs if not name.startswith(".") and not _is_excluded(Path(current) / name, excluded)]
        yield from (Path(current) / name for name in dirs)
        yield from (Path(current) / name for name in files)


def build_search_index(config, *, recent: list[str] | None = None,
                       favorites: list[str] | None = None) -> list[dict[str, Any]]:
    """Build a deduplicated local search index; callers run this off the UI thread."""
    settings = config.settings
    entries: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, str]] = set()
    excluded = list(settings.search_excluded)
    folders = list(settings.search_folders)
    if settings.search_index_apps:
        folders.extend((
            os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), r"Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        ))
    app_extensions = {".lnk", ".exe", ".url", ".bat", ".cmd", ".ps1"}
    for folder in folders:
        root = Path(os.path.expandvars(folder)).expanduser()
        if not root.is_dir():
            continue
        for path in _walk_search_paths(root, excluded):
            suffix = path.suffix.lower()
            if suffix not in app_extensions:
                continue
            result_type = "Application" if suffix in {".lnk", ".exe", ".bat", ".cmd"} else "Shortcut"
            key = (_normalized_path(path), result_type)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            entries.append({"name": path.stem, "type": result_type, "path": str(path),
                            "target": str(path), "icon": "app-window"})
    if settings.search_index_files:
        for folder in settings.search_folders:
            root = Path(os.path.expandvars(folder)).expanduser()
            if not root.is_dir():
                continue
            for path in _walk_search_paths(root, excluded):
                try:
                    is_dir = path.is_dir()
                except OSError:
                    continue
                result_type = "Folder" if is_dir else "File"
                key = (_normalized_path(path), result_type)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                entries.append({"name": path.name, "type": result_type, "path": str(path),
                                "target": str(path), "icon": "folder" if is_dir else "file-text"})
    action_ids: set[str] = set()
    for _menu, action in config.iter_actions():
        if action.enabled:
            action_ids.add(action.id)
            entries.append({"name": action.display_name(), "type": "ORBIT Action", "path": action.target,
                            "target": action.target, "favorite_key": action.id, "recent_key": action.id,
                            "action": action, "icon": action.icon or "zap"})
    features = (
        ("open_search", "Universal Search", ""), ("open_quick_controls", "Quick Controls", ""),
        ("open_music", "Music Hub", ""), ("open_widgets", "Productivity Widgets", ""),
        ("search_settings", "Search Settings", "productivity"),
        ("quick_controls_settings", "Quick Controls Settings", "productivity"),
        ("music_settings", "Music Settings", "productivity"),
        ("widgets_settings", "Widget Settings", "productivity"),
        ("open_studio", "Appearance Settings", "appearance"),
        ("open_studio", "Behavior Settings", "behaviour"),
        ("open_studio", "Menu Editor", "menus"),
        ("open_studio", "Import and Backups", "data"),
    )
    for target, label, page in features:
        entries.append({"name": label, "type": "ORBIT Feature", "path": "", "target": target,
                        "page": page, "icon": "orbit"})
    widget_labels = {"timer": "Timer and Stopwatch", "notes": "Quick Notes",
                     "tasks": "Task List", "system": "System Information"}
    for key in settings.widgets_enabled:
        if key in widget_labels:
            entries.append({"name": widget_labels[key], "type": "ORBIT Widget", "path": "",
                            "target": "open_widgets", "widget_tab": key, "icon": "grid"})
    known_targets = {str(item.get("target", "")) for item in entries}
    for value in [*(favorites or []), *(recent or [])]:
        if value in action_ids or value in known_targets:
            continue
        entries.append({"name": Path(value).name or value, "type": "Recent / favorite", "path": value,
                        "target": value, "icon": "star"})
        known_targets.add(value)
    return entries


class FeatureWindow(QMainWindow):
    def __init__(self, controller, title: str, width: int, height: int) -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle(title)
        self.resize(width, height)
        self.setMinimumSize(min(480, width), min(360, height))
        self.setStyleSheet(
            f"QMainWindow {{ background:#12151b; color:#edf0f7; }} "
            f"QWidget {{ color:#edf0f7; font-size:13px; }} "
            f"QLineEdit,QPlainTextEdit,QListWidget,QComboBox,QSpinBox {{ background:#1b2029; "
            f"border:1px solid #303744; border-radius:8px; padding:7px; "
            f"selection-background-color:{getattr(getattr(controller, 'theme', None), 'accent', '#6C8CFF')}; }} "
            "QPushButton { background:#222834; border:1px solid #333b49; border-radius:8px; padding:8px 12px; } "
            "QPushButton:hover { background:#2b3442; }"
        )

    def _open_centered(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()


class StickyTasksWindow(QMainWindow):
    """A small always-on-top task strip for the tasks the user pins."""

    def __init__(self, controller, store: ProductivityStore, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.store = store
        self.setWindowTitle("ORBIT · Pinned tasks")
        self.setMinimumSize(280, 120)
        self.resize(340, 230)
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setStyleSheet(
            "QMainWindow { background:#141820; color:#F3F5FA; }"
            "QWidget { color:#F3F5FA; font-size:13px; }"
            "QFrame#StickyCard { background:#1D2330; border:1px solid #343D4E; border-radius:14px; }"
            "QLabel#StickyTitle { font-size:15px; font-weight:700; }"
            "QLabel#StickyCount { color:#98A0B0; font-size:11px; }"
            "QCheckBox { spacing:8px; padding:7px 4px; border-radius:8px; }"
            "QCheckBox:hover { background:#273043; }"
            "QCheckBox:checked { color:#8F98A8; }"
            "QPushButton { background:transparent; border:0; color:#98A0B0; padding:4px 6px; }"
            "QPushButton:hover { color:#FFFFFF; background:#2A3343; border-radius:6px; }"
        )
        self._root = QWidget()
        root_layout = QVBoxLayout(self._root)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(8)
        card = QFrame(); card.setObjectName("StickyCard")
        card_layout = QVBoxLayout(card); card_layout.setContentsMargins(12, 10, 12, 10); card_layout.setSpacing(4)
        header = QHBoxLayout()
        title = QLabel("Pinned to-dos"); title.setObjectName("StickyTitle")
        self.count = QLabel(); self.count.setObjectName("StickyCount")
        header.addWidget(title); header.addWidget(self.count); header.addStretch(1)
        hide = QPushButton("Hide"); hide.setToolTip("Hide pinned tasks (they stay pinned)"); hide.clicked.connect(self.hide)
        header.addWidget(hide)
        card_layout.addLayout(header)
        self.task_layout = QVBoxLayout(); self.task_layout.setContentsMargins(0, 4, 0, 0); self.task_layout.setSpacing(2)
        card_layout.addLayout(self.task_layout)
        self.empty = QLabel("Pin a task from Widgets → Tasks")
        self.empty.setStyleSheet("color:#98A0B0; padding:8px 3px;")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.empty)
        root_layout.addWidget(card)
        self.setCentralWidget(self._root)
        self.refresh()

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()

    def refresh(self) -> None:
        while self.task_layout.count():
            item = self.task_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        pinned = [task for task in self.store.data.tasks if task.get("pinned")]
        pinned.sort(key=lambda task: (bool(task.get("done")), task.get("created", 0)))
        for task in pinned:
            check = QCheckBox(task.get("title") or "Untitled task")
            check.setChecked(bool(task.get("done")))
            check.setToolTip("Mark complete")
            check.stateChanged.connect(lambda state, record=task: self._set_done(record, state))
            self.task_layout.addWidget(check)
        self.empty.setVisible(not pinned)
        self.count.setText(f"{len(pinned)} active" if pinned else "ready when you are")
        self.adjustSize()

    def _set_done(self, task: dict, state: int) -> None:
        task["done"] = state == int(Qt.CheckState.Checked)
        try:
            self.store.save()
        except OSError as exc:
            self.controller.tray.notify("ORBIT task could not be saved", str(exc), ok=False)
        self.refresh()
        panel = getattr(self.controller, "productivity_panel", None)
        if panel is not None:
            panel._render_tasks()


class SearchDialog(FeatureWindow):
    """Global, indexed-as-you-type search over apps, files, actions and ORBIT features."""
    def __init__(self, controller) -> None:
        super().__init__(controller, "ORBIT Search", 670, 570)
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self._index: list[dict[str, Any]] = []
        self._index_generation = 0
        self._search_generation = 0
        self._index_signature: tuple[Any, ...] | None = None
        self._selected: dict[str, Any] | None = None
        self._tasks: list[Any] = []
        self._worker_signals: list[Any] = []
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 14)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search apps, files, actions, music, settings…")
        self.query.setClearButtonEnabled(True)
        layout.addWidget(self.query)
        self.status = QLabel("Indexing Windows apps and configured folders…")
        layout.addWidget(self.status)
        self.results = QListWidget()
        self.results.setUniformItemSizes(True)
        layout.addWidget(self.results, 1)
        buttons = QHBoxLayout()
        self.favorite_button = QPushButton("☆ Favorite")
        self.favorite_button.clicked.connect(self._favorite)
        buttons.addWidget(self.favorite_button)
        self.reveal_button = QPushButton("Show in Explorer")
        self.reveal_button.clicked.connect(self._reveal)
        buttons.addWidget(self.reveal_button)
        buttons.addStretch(1)
        buttons.addWidget(QLabel("↑ ↓ navigate · Enter open · Esc close"))
        layout.addLayout(buttons)
        self.setCentralWidget(root)
        self.query.textChanged.connect(self._schedule_search)
        self.query.installEventFilter(self)
        self.results.itemActivated.connect(lambda _item: self._execute_selected())
        self.results.currentItemChanged.connect(self._current_changed)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._build_index_async)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(65)
        self._search_timer.timeout.connect(self._search_async)
        self._build_index_async()

    def open_search(self) -> None:
        signature = self._current_index_signature()
        if signature != self._index_signature or not self._index:
            self._refresh_timer.start(1)
        self._open_centered()
        self.query.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.query.selectAll()
        self._schedule_search()

    def _current_index_signature(self) -> tuple[Any, ...]:
        settings = self.controller.config_manager.config.settings
        return (tuple(settings.search_folders), tuple(settings.search_excluded),
                bool(settings.search_index_apps), bool(settings.search_index_files),
                tuple((action.id, action.name, action.enabled, action.target, action.type)
                      for _menu, action in self.controller.config_manager.config.iter_actions()),
                tuple((path, item.get("title", ""), item.get("mtime"))
                      for path, item in sorted(self.controller.productivity.data.library_tracks.items())),
                tuple((item.get("id"), item.get("title"), item.get("updated"))
                      for item in self.controller.productivity.data.notes),
                tuple((item.get("id"), item.get("title"), item.get("done"))
                      for item in self.controller.productivity.data.tasks),
                tuple(self.controller.productivity.data.favorites))

    def _build_index_async(self) -> None:
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal as QtSignal
        self._index_signature = self._current_index_signature()
        self._index_generation += 1
        generation = self._index_generation
        dialog = self
        config_snapshot = self.controller.config_manager.config.clone()
        data_snapshot = self.controller.productivity.data
        recent_snapshot = list(data_snapshot.recent) + list(data_snapshot.favorites)
        favorites_snapshot = list(config_snapshot.settings.search_favorites)
        library_snapshot = {key: dict(value) for key, value in data_snapshot.library_tracks.items()}
        notes_snapshot = [dict(value) for value in data_snapshot.notes]
        tasks_snapshot = [dict(value) for value in data_snapshot.tasks]

        class ResultSignals(QObject):
            ready = QtSignal(int, object)

        class IndexTask(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = ResultSignals()

            def run(self):
                entries = build_search_index(
                    config_snapshot,
                    recent=recent_snapshot,
                    favorites=favorites_snapshot,
                )
                for path, track in library_snapshot.items():
                    entries.append({"name": str(track.get("title") or Path(path).stem),
                                    "type": "Music", "path": path, "target": path,
                                    "icon": "play"})
                for note in notes_snapshot:
                    entries.append({"name": note.get("title") or "Untitled note", "type": "Note",
                                    "path": "", "target": "open_widgets", "widget_tab": "Notes",
                                    "icon": "file-text"})
                for task_record in tasks_snapshot:
                    if not task_record.get("done"):
                        entries.append({"name": task_record.get("title", "Task"), "type": "Task",
                                        "path": "", "target": "open_widgets", "widget_tab": "Tasks",
                                        "icon": "check"})
                self.signals.ready.emit(generation, entries)

        task = IndexTask()
        task._index_task = True
        task.signals._index_signals = True
        self._tasks = [current for current in self._tasks if not getattr(current, "_index_task", False)] + [task]
        self._worker_signals = [current for current in self._worker_signals if not getattr(current, "_index_signals", False)] + [task.signals]
        task.signals.ready.connect(self._index_ready)

        QThreadPool.globalInstance().start(task)

    def _index_ready(self, generation: int, entries: list[dict[str, Any]]) -> None:
        if generation != self._index_generation:
            return
        self._index = entries
        self._tasks = [task for task in self._tasks if not getattr(task, "_index_task", False)]
        self._worker_signals = [signals for signals in self._worker_signals if not getattr(signals, "_index_signals", False)]
        self._schedule_search()

    def _search_entries(self) -> list[dict[str, Any]]:
        return list(self._index)

    def _schedule_search(self, _text: str = "") -> None:
        signature = self._current_index_signature()
        if signature != self._index_signature:
            self._refresh_timer.start()
        self.status.setText("Searching…")
        self._search_timer.start()

    def _search_async(self) -> None:
        from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal as QtSignal

        query = self.query.text().strip()
        entries = self._search_entries()
        favorites = set(self.controller.config_manager.config.settings.search_favorites)
        recent = list(self.controller.productivity.data.recent)
        self._search_generation += 1
        generation = self._search_generation

        class ResultSignals(QObject):
            ready = QtSignal(int, str, object)

        class SearchTask(QRunnable):
            def __init__(self):
                super().__init__()
                self.signals = ResultSignals()

            def run(self):
                scored = []
                for entry in entries:
                    score = fuzzy_score(query, f"{entry['name']} {entry.get('type','')} {entry.get('path','')}")
                    if query and score <= 0:
                        continue
                    target = entry.get("target", entry.get("path", ""))
                    if entry.get("favorite_key", target) in favorites:
                        score += 22
                    recent_key = entry.get("recent_key", target)
                    if recent_key in recent:
                        score += max(0, 12 - recent.index(recent_key))
                    scored.append((score, entry))
                scored.sort(key=lambda pair: (-pair[0], pair[1]["name"].casefold()))
                self.signals.ready.emit(generation, query, [entry for _score, entry in scored[:100]])

        task = SearchTask()
        task._search_task = True
        task.signals._search_signals = True
        task._search_task = True
        task.signals._search_signals = True
        self._tasks = [current for current in self._tasks if not getattr(current, "_search_task", False)] + [task]
        self._worker_signals = [current for current in self._worker_signals if not getattr(current, "_search_signals", False)] + [task.signals]
        task.signals.ready.connect(self._show_results)
        QThreadPool.globalInstance().start(task)

    def _show_results(self, generation: int, query: str, entries: list[dict[str, Any]]) -> None:
        self._tasks = [task for task in self._tasks if not getattr(task, "_search_task", False)]
        self._worker_signals = [signals for signals in self._worker_signals if not getattr(signals, "_search_signals", False)]
        if generation != self._search_generation or query != self.query.text().strip():
            return
        self.results.clear()
        for entry in entries:
            path = entry.get("path", "")
            label = f"{entry['name']}     · {entry['type']}"
            if path and entry["type"] not in {"ORBIT Action", "ORBIT Feature"}:
                label += f"\n{path}"
            item = QListWidgetItem(label)
            item.setIcon(QIcon(icon_pixmap(entry.get("icon", "circle-dot"), 22, QColor(self.controller.theme.accent))))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)
            self.status.setText(f"Showing {len(entries)} results · Enter open · Ctrl+Enter reveal")
        elif query:
            self.status.setText(f"No matches for “{query}”. Add indexed folders in Studio → Productivity.")
        else:
            self.status.setText(f"{len(self._index)} indexed items · type to search")

    def _current_changed(self, current, _previous) -> None:
        self._selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        is_path = bool(self._selected and self._selected.get("path") and self._selected.get("type") not in {"ORBIT Action", "ORBIT Feature"})
        self.reveal_button.setEnabled(is_path)
        target = self._selected.get("favorite_key", self._selected.get("target", "")) if self._selected else ""
        self.favorite_button.setEnabled(bool(target))
        self.favorite_button.setText("★ Favorited" if target in self.controller.config_manager.config.settings.search_favorites else "☆ Favorite")

    def _execute_selected(self) -> None:
        entry = self._selected
        if not entry:
            return
        action = entry.get("action")
        if action is not None:
            self.close()
            self.controller._on_action_requested(action)
            self.controller.productivity.touch_recent(action.id)
            return
        target = entry.get("target") or entry.get("path")
        if not target:
            return
        try:
            if entry.get("type") == "Music":
                if not Path(target).is_file():
                    raise FileNotFoundError(f"Music file is unavailable: {target}")
                self.controller.open_music()
                self.controller.music_player.play_track(target)
            elif entry.get("widget_tab"):
                self.controller.open_widgets()
                panel = self.controller.productivity_panel
                if panel is not None:
                    panel.open_widget(entry["widget_tab"])
            elif entry.get("type") == "ORBIT Feature":
                page = entry.get("page")
                if target == "open_studio":
                    self.controller.open_studio(page or None)
                else:
                    ok, message = self.controller._orbit_command(target)
                    if not ok:
                        raise RuntimeError(message)
            elif os.path.exists(target):
                from core.launcher import open_with_default
                open_with_default(target)
            else:
                raise FileNotFoundError(f"The selected item is no longer available: {target}")
        except Exception as exc:
            QMessageBox.warning(self, "Could not open item", str(exc))
            return
        self.controller.productivity.touch_recent(entry.get("recent_key", target))
        self.close()

    def _reveal(self) -> None:
        if not self._selected or not self._selected.get("path"):
            return
        path = self._selected["path"]
        try:
            if os.path.isdir(path):
                os.startfile(path)
            elif os.name == "nt":
                subprocess.Popen(["explorer.exe", "/select,", os.path.abspath(path)])
            else:
                from core.launcher import open_with_default
                open_with_default(str(Path(path).parent))
        except Exception as exc:
            QMessageBox.warning(self, "Could not show item", str(exc))

    def _favorite(self) -> None:
        if not self._selected:
            return
        target = self._selected.get("favorite_key", self._selected.get("target") or self._selected.get("path"))
        if not target:
            return
        config = self.controller.config_manager.config.clone()
        favorites = config.settings.search_favorites
        if target in favorites:
            favorites.remove(target)
        else:
            favorites.append(target)
        try:
            self.controller.config_manager.save(config, reason="search-favorite")
            if self._index_signature is not None:
                self._index_signature = None
            self._render_results()
        except OSError as exc:
            QMessageBox.warning(self, "Could not save favorite", str(exc))

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.query and event.type() == event.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if self.results.count():
                    delta = 1 if event.key() == Qt.Key.Key_Down else -1
                    row = self.results.currentRow()
                    self.results.setCurrentRow(min(self.results.count() - 1, max(0, row + delta)))
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._execute_selected()
                return True
            if event.key() == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._reveal()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.query.hasFocus():
            row = max(0, self.results.currentRow() + (1 if event.key() == Qt.Key.Key_Down else -1))
            if self.results.count():
                self.results.setCurrentRow(min(row, self.results.count() - 1))
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._execute_selected()
            return
        super().keyPressEvent(event)


class QuickControlsPanel(FeatureWindow):
    """System volume/mute, output selection, and truthful Windows Settings shortcuts."""
    def __init__(self, controller) -> None:
        super().__init__(controller, "ORBIT Quick Controls", 570, 560)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 22, 22, 22)
        title = QLabel("QUICK CONTROLS")
        title.setStyleSheet("font-size:18px;font-weight:700;letter-spacing:2px")
        layout.addWidget(title)
        self.audio_status = QLabel("Loading Windows audio endpoints…")
        audio_row = QHBoxLayout()
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_value = QLabel("—")
        self.volume_value.setMinimumWidth(46)
        self.mute = QPushButton("Mute")
        audio_row.addWidget(QLabel("System volume")); audio_row.addWidget(self.volume_slider, 1)
        audio_row.addWidget(self.volume_value); audio_row.addWidget(self.mute)
        self.device = QComboBox()
        self.device.setToolTip("Select an active Windows audio output")
        self.device_status = QLabel("Output changes use Windows audio policy.")
        self.no_devices = QLabel("No active output endpoints were found.")
        self.no_devices.setVisible(False)
        self.audio_panel = QWidget()
        audio_layout = QVBoxLayout(self.audio_panel)
        audio_layout.setContentsMargins(0, 0, 0, 0)
        audio_layout.addWidget(self.audio_status)
        audio_layout.addLayout(audio_row)
        audio_layout.addWidget(self.device)
        audio_layout.addWidget(self.device_status)
        audio_layout.addWidget(self.no_devices)
        layout.insertWidget(1, self.audio_panel)
        self.audio_panel.setVisible("audio" in controller.config_manager.config.settings.quick_controls)
        self.volume_slider.valueChanged.connect(self._set_volume)
        self.mute.clicked.connect(self._toggle_mute)
        self.device.currentIndexChanged.connect(self._select_device)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2500)
        self._refresh_timer.timeout.connect(self.refresh_audio)
        layout.addWidget(QLabel("Windows Settings · opens the relevant settings page"))
        self.settings_buttons: dict[str, QPushButton] = {}
        self.settings_layout = layout
        controls = [("wifi", "Wi-Fi", "ms-settings:network-wifi"), ("bluetooth", "Bluetooth", "ms-settings:bluetooth"), ("display", "Display", "ms-settings:display"), ("power", "Power & battery", "ms-settings:powersleep"), ("sound", "Sound", "ms-settings:sound"), ("network", "Network", "ms-settings:network-status")]
        for key, label, uri in controls:
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, u=uri, l=label: self._open_settings(u, l))
            button.setVisible(key in controller.config_manager.config.settings.quick_controls)
            self.settings_buttons[key] = button
            layout.addWidget(button)
        self.setCentralWidget(root)
        self._refresh_timer.start()
        self.refresh_audio()
        self.apply_settings(controller.config_manager.config.settings)

    def show_panel(self) -> None:
        self.refresh_audio()
        self._open_centered()

    def apply_settings(self, settings) -> None:
        selected = list(settings.quick_controls)
        for key, button in self.settings_buttons.items():
            button.setVisible(key in selected)
        # Reinsert selected settings controls in the exact saved order; audio remains first.
        for button in self.settings_buttons.values():
            self.settings_layout.removeWidget(button)
        for index, key in enumerate(selected, start=3):
            button = self.settings_buttons.get(key)
            if button is not None:
                self.settings_layout.insertWidget(index, button)
        self.audio_panel.setVisible("audio" in selected)
        self._quick_controls_enabled = bool(settings.quick_controls_enabled)
        if not self._quick_controls_enabled:
            self.hide()

    @staticmethod
    def _audio_api():
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities

    def refresh_audio(self) -> None:
        if getattr(self, "_refreshing_audio", False):
            return
        self._refreshing_audio = True
        try:
            from pycaw.constants import DEVICE_STATE, EDataFlow
            api = self._audio_api()
            device = api.GetSpeakers()
            endpoint = device.EndpointVolume
            self.volume_slider.blockSignals(True)
            self.volume_slider.setValue(round(endpoint.GetMasterVolumeLevelScalar() * 100))
            self.volume_slider.blockSignals(False)
            muted = bool(endpoint.GetMute())
            self.mute.setText("Unmute" if muted else "Mute")
            self.volume_value.setText(f"{self.volume_slider.value()}%" + (" · muted" if muted else ""))
            self.audio_status.setText(f"System output: {device.FriendlyName}")
            endpoints = list(api.GetAllDevices(data_flow=EDataFlow.eRender.value, device_state=DEVICE_STATE.ACTIVE.value))
            self.no_devices.setVisible(not endpoints)
            current = self.device.currentData()
            self.device.blockSignals(True)
            self.device.clear()
            for output in endpoints:
                self.device.addItem(output.FriendlyName, getattr(output, "id", None))
            index = self.device.findData(current)
            if index < 0:
                index = self.device.findText(device.FriendlyName)
            if index >= 0:
                self.device.setCurrentIndex(index)
            self.device.blockSignals(False)
            self._default_device = device
            self.device_status.setText("Master volume/mute are direct. Device switching uses pycaw's Windows policy API.")
            self.volume_slider.setEnabled(True)
            self.mute.setEnabled(True)
        except Exception as exc:
            self.no_devices.setVisible(True)
            self.device.clear()
            self._default_device = None
            self.audio_status.setText("Windows audio endpoint is unavailable.")
            self.device_status.setText(f"Audio control error: {exc}")
            self.volume_slider.setEnabled(False)
            self.mute.setEnabled(False)
        finally:
            self._refreshing_audio = False

    def _set_volume(self, value: int) -> None:
        try:
            if self._refreshing_audio:
                return
            if not getattr(self, "_default_device", None):
                raise RuntimeError("No active Windows output device is available.")
            self._default_device.EndpointVolume.SetMasterVolumeLevelScalar(value / 100.0, None)
            self.volume_value.setText(f"{value}%")
        except Exception as exc:
            self.audio_status.setText(f"Could not set system volume: {exc}")


    def _toggle_mute(self) -> None:
        try:
            if self._refreshing_audio:
                return
            if not getattr(self, "_default_device", None):
                raise RuntimeError("No active Windows output device is available.")
            volume = self._default_device.EndpointVolume
            volume.SetMute(not bool(volume.GetMute()), None)
            self.refresh_audio()
        except Exception as exc:
            self.audio_status.setText(f"Could not change mute: {exc}")

    def _select_device(self, index: int) -> None:
        if index < 0 or not getattr(self, "_default_device", None) or getattr(self, "_refreshing_audio", False):
            return
        device_id = self.device.itemData(index)
        if not device_id or device_id == getattr(self._default_device, "id", None):
            return
        try:
            result = self._audio_api().SetDefaultDevice(device_id)
            if result is False:
                raise RuntimeError("Windows did not accept the default-output request.")
            self.device_status.setText("Windows default audio output changed.")
            QTimer.singleShot(700, self.refresh_audio)
        except Exception as exc:
            self.device_status.setText(f"Could not switch output. Choose it in Windows Settings → Sound. {exc}")

    def _open_settings(self, uri: str, label: str) -> None:
        try:
            from PySide6.QtCore import QDesktopServices
            if not QDesktopServices.openUrl(QUrl(uri)):
                raise RuntimeError("Windows did not handle the Settings link.")
        except Exception as exc:
            QMessageBox.warning(self, "Could not open Windows Settings", f"{label}: {exc}")


class MusicPlayer(FeatureWindow):
    """Independent local-file player powered by Qt Multimedia."""
    trackChanged = Signal(object)

    def __init__(self, settings, store: ProductivityStore) -> None:
        super().__init__(None, "ORBIT Music Hub", 850, 680)
        self.controller = None
        self.settings = settings
        self.store = store
        cache = {key: {**value, "art": base64.b64decode(value["art"]) if isinstance(value.get("art"), str) else value.get("art")} for key, value in store.data.library_tracks.items()}
        self.library = MusicLibrary(settings.music_folders, self, cache=cache)
        self.library.excluded = list(settings.search_excluded)
        self.library.scanFinished.connect(self._library_ready)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.output.setVolume(settings.music_volume / 100.0)
        self._restore_position = 0
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._playback_error)
        self._position_save_timer = QTimer(self)
        self._position_save_timer.setInterval(15000)
        self._position_save_timer.timeout.connect(self._save)
        self._position_save_timer.start()
        self._queue = list(store.data.music_queue if store.data.music_volume is not None else settings.music_queue)
        self._queue_pos = min(store.data.queue_position, max(0, len(self._queue) - 1))
        if not settings.music_playlists and store.data.playlists:
            settings.music_playlists = {name: list(paths) for name, paths in store.data.playlists.items()}
        if store.data.music_volume is not None:
            settings.music_volume = store.data.music_volume
        if store.data.music_volume is not None and not store.data.music_shuffle:
            settings.music_shuffle = False
        if store.data.music_volume is not None:
            settings.music_repeat = store.data.music_repeat
        self._library_count = len(self.library.tracks)
        self._tracks_by_path: dict[str, dict[str, Any]] = dict(self.library.tracks)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 18, 18, 18)
        now = QHBoxLayout()
        self.cover = QLabel("♪")
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFixedSize(92, 92)
        self.cover.setStyleSheet("background:#1e2430;border-radius:18px;font-size:38px")
        now.addWidget(self.cover)
        meta = QVBoxLayout()
        self.track_title = QLabel("Nothing playing")
        self.track_title.setStyleSheet("font-size:18px;font-weight:700")
        self.track_artist = QLabel("Choose a local track")
        meta.addWidget(self.track_title); meta.addWidget(self.track_artist); meta.addStretch()
        now.addLayout(meta, 1)
        layout.addLayout(now)
        self.status = QLabel("Local audio · playback continues when this window closes")
        layout.addWidget(self.status)
        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        times = QHBoxLayout()
        self.current_time = QLabel("0:00"); self.total_time = QLabel("0:00")
        times.addWidget(self.current_time); times.addStretch(); times.addWidget(self.total_time)
        layout.addLayout(times)
        controls = QHBoxLayout()
        self.prev = QPushButton("Previous"); self.play = QPushButton("Play"); self.next = QPushButton("Next")
        self.shuffle = QCheckBox("Shuffle")
        self.repeat = QComboBox(); self.repeat.addItems(["Repeat off", "Repeat all", "Repeat one"])
        self.volume = QSlider(Qt.Orientation.Horizontal); self.volume.setRange(0, 100); self.volume.setValue(settings.music_volume)
        for button in (self.prev, self.play, self.next): controls.addWidget(button)
        controls.addWidget(self.shuffle); controls.addWidget(self.repeat); controls.addWidget(QLabel("Player volume")); controls.addWidget(self.volume, 1)
        layout.addLayout(controls)

        tabs = QTabWidget()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search title, artist, album, or file…")
        self.library_list = QListWidget(); self.queue_list = QListWidget(); self.playlist_list = QListWidget()
        library_page = QWidget(); lib_layout = QVBoxLayout(library_page); row = QHBoxLayout()
        add_folder = QPushButton("Add music folder…"); scan = QPushButton("Refresh library"); add_files = QPushButton("Add files…")
        for button in (add_folder, scan, add_files): row.addWidget(button)
        lib_layout.addLayout(row); lib_layout.addWidget(self.search); lib_layout.addWidget(self.library_list); tabs.addTab(library_page, "Library")
        queue_page = QWidget(); queue_layout = QVBoxLayout(queue_page); qrow = QHBoxLayout()
        remove_queue = QPushButton("Remove selected"); up = QPushButton("Move up"); down = QPushButton("Move down")
        for button in (remove_queue, up, down): qrow.addWidget(button)
        queue_layout.addLayout(qrow); queue_layout.addWidget(self.queue_list); tabs.addTab(queue_page, "Queue")
        playlist_page = QWidget(); playlist_layout = QVBoxLayout(playlist_page); prow = QHBoxLayout()
        create_playlist = QPushButton("New playlist"); edit_playlist = QPushButton("Rename playlist"); delete_playlist = QPushButton("Delete playlist"); add_playlist = QPushButton("Add selected track")
        for button in (create_playlist, edit_playlist, delete_playlist, add_playlist): prow.addWidget(button)
        playlist_layout.addLayout(prow); playlist_layout.addWidget(self.playlist_list); tabs.addTab(playlist_page, "Playlists")
        layout.addWidget(tabs, 1)
        self.setCentralWidget(root)

        add_folder.clicked.connect(self._add_folder); scan.clicked.connect(self._scan); add_files.clicked.connect(self._add_files)
        self.search.textChanged.connect(self._render_library); self.library_list.itemDoubleClicked.connect(self._play_item)
        self.play.clicked.connect(self.toggle); self.prev.clicked.connect(self.previous); self.next.clicked.connect(self.next_track)
        self.progress.sliderMoved.connect(self.player.setPosition); self.progress.sliderReleased.connect(lambda: self.player.setPosition(self.progress.value())); self.volume.valueChanged.connect(self._set_volume)
        self.shuffle.setChecked(bool(store.data.music_shuffle if store.data.music_volume is not None else settings.music_shuffle)); self.shuffle.toggled.connect(self._set_shuffle)
        saved_repeat = store.data.music_repeat if store.data.music_volume is not None else settings.music_repeat
        self.repeat.setCurrentIndex({"off": 0, "all": 1, "one": 2}.get(saved_repeat, 0))
        self.repeat.currentIndexChanged.connect(self._set_repeat)
        remove_queue.clicked.connect(self._remove_queue); up.clicked.connect(lambda: self._move_queue(-1)); down.clicked.connect(lambda: self._move_queue(1))
        create_playlist.clicked.connect(self._create_playlist); edit_playlist.clicked.connect(self._rename_playlist)
        delete_playlist.clicked.connect(self._delete_playlist); add_playlist.clicked.connect(self._add_to_playlist)
        self.playlist_list.itemDoubleClicked.connect(self._play_playlist)
        self.library.folders = list(settings.music_folders)
        self.library.excluded = list(settings.search_excluded)
        self._render_queue(); self._render_playlists(); self._scan()
        self._position_save_timer.start()
        self._queue_sync_timer = QTimer(self)
        self._queue_sync_timer.setInterval(5000)
        self._queue_sync_timer.timeout.connect(self._sync_queue_from_settings)
        self._queue_sync_timer.start()
        if settings.music_resume and store.data.music_track and Path(store.data.music_track).exists():
            self.play_track(store.data.music_track, start_playing=False, position=store.data.music_position_ms)
        self._media_keys = [QShortcut(QKeySequence(key), self) for key in ("Media Play", "Media Pause", "Media Next", "Media Previous")]
        for shortcut in self._media_keys:
            shortcut.setEnabled(bool(settings.music_hotkeys_enabled))
        self._media_keys[0].activated.connect(self.toggle); self._media_keys[1].activated.connect(self.toggle)
        self._media_keys[2].activated.connect(self.next_track); self._media_keys[3].activated.connect(self.previous)

    def apply_settings(self, settings) -> None:
        folders_changed = self.library.folders != list(settings.music_folders) or self.library.excluded != list(settings.search_excluded)
        self.settings = settings
        self.library.folders = list(settings.music_folders)
        self.library.excluded = list(settings.search_excluded)
        self.output.setVolume(settings.music_volume / 100.0)
        self.volume.blockSignals(True); self.volume.setValue(settings.music_volume); self.volume.blockSignals(False)
        self.shuffle.blockSignals(True); self.shuffle.setChecked(settings.music_shuffle); self.shuffle.blockSignals(False)
        self.repeat.blockSignals(True); self.repeat.setCurrentIndex({"off": 0, "all": 1, "one": 2}.get(settings.music_repeat, 0)); self.repeat.blockSignals(False)
        for shortcut in self._media_keys:
            shortcut.setEnabled(bool(settings.music_hotkeys_enabled))
        if folders_changed:
            self._scan()
        if hasattr(self, "playlist_list"):
            self._render_playlists()

    def _sync_queue_from_settings(self) -> None:
        configured = list(self.controller.config_manager.config.settings.music_queue)
        if configured != self._queue:
            self._queue = configured
            self._queue_pos = min(self._queue_pos, max(0, len(self._queue) - 1))
            self._render_queue()

    def show_player(self) -> None:
        self._open_centered()

    def shutdown(self) -> None:
        self._position_save_timer.stop()
        self._queue_sync_timer.stop()
        self._save()
        self.player.stop()
        try:
            self.library.scanFinished.disconnect(self._library_ready)
        except (RuntimeError, TypeError):
            pass
        worker = self.library._worker
        if worker is not None:
            worker.join(timeout=2.0)

    def _save(self) -> None:
        if not hasattr(self, "volume"):
            return
        data = self.store.data
        data.music_queue = list(self._queue); data.queue_position = self._queue_pos
        data.playlists = dict(self.settings.music_playlists); data.music_shuffle = self.shuffle.isChecked()
        data.music_repeat = ["off", "all", "one"][self.repeat.currentIndex()]
        data.music_volume = self.volume.value()
        self.settings.music_queue = list(self._queue); self.settings.music_shuffle = data.music_shuffle
        self.settings.music_repeat = data.music_repeat; self.settings.music_volume = self.volume.value()
        data.music_track = self.player.source().toLocalFile(); data.music_position_ms = self.player.position()
        data.library_tracks = {key: {**value, "art": base64.b64encode(value["art"]).decode("ascii") if isinstance(value.get("art"), bytes) else value.get("art")} for key, value in self.library.tracks.items()}
        try:
            self.store.save()
        except OSError as exc:
            self.status.setText(f"Could not save Music Hub state: {exc}")

    def _playback_error(self, error, message: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            self.status.setText(f"Playback error: {message or 'The audio file could not be decoded.'}")

    def _scan(self) -> None:
        self.status.setText("Scanning music folders in background…")
        self.library.scan()

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Add a music folder", str(Path.home()))
        if folder and folder not in self.settings.music_folders:
            config = self.controller.config_manager.config.clone()
            config.settings.music_folders = [*config.settings.music_folders, folder]
            try:
                self.controller.config_manager.save(config, reason="music-folders")
            except OSError as exc:
                self.status.setText(f"Could not save music folder: {exc}")
                return
            self.controller.apply_settings(config=config)
            self._scan()

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add local music files", str(Path.home()), "Audio (*.mp3 *.wav *.flac *.ogg *.oga *.opus)")
        if files:
            start = len(self._queue); self._queue.extend(files); self._render_queue()
            if not self.player.source().isValid(): self._play_queue_position(start)

    def _library_ready(self, tracks: list, count: int) -> None:
        self._tracks_by_path = {item["path"]: item for item in tracks}
        self.store.data.library_tracks = {key: {**value, "art": base64.b64encode(value["art"]).decode("ascii") if isinstance(value.get("art"), bytes) else value.get("art")} for key, value in self.library.tracks.items()}
        self._library_count = count
        self._render_library()
        try:
            self._save()
        except OSError as exc:
            self.status.setText(f"Library indexed, but its cache could not be saved: {exc}")
            return
        self.status.setText(f"{len(tracks)} tracks indexed · {count} audio files found")

    def _render_library(self) -> None:
        query = self.search.text()
        tracks = self.library.search(query) if query else self.library.all_tracks()
        self.library_list.clear()
        for track in tracks:
            title = track.get("title") or Path(track["path"]).stem
            item = QListWidgetItem(f"{title} · {track.get('artist') or 'Unknown artist'}\n{track.get('album') or track['path']}")
            item.setData(Qt.ItemDataRole.UserRole, track["path"]); self.library_list.addItem(item)

    def _play_item(self, item) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path not in self._queue:
            self._queue.append(path)
        self._queue_pos = self._queue.index(path)
        self.play_track(path); self._render_queue()

    def play_track(self, path: str, start_playing: bool = True, position: int = 0) -> None:
        if not Path(path).is_file():
            self.status.setText(f"Track is unavailable: {path}")
            return
        try:
            self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
            track = self._tracks_by_path.get(path, {})
            self.track_title.setText(track.get("title") or Path(path).stem)
            self.track_artist.setText(track.get("artist") or "Unknown artist")
            self.status.setText(track.get("album") or "Local file")
            art = track.get("art")
            if art:
                pix = QPixmap(); pix.loadFromData(art)
                self.cover.setPixmap(pix.scaled(self.cover.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
            else:
                self.cover.setPixmap(QPixmap()); self.cover.setText("♪")
            if path not in self._queue:
                self._queue.append(path)
            self._queue_pos = self._queue.index(path)
            self._render_queue()
            self._restore_position = max(0, int(position))
            if start_playing:
                self.player.play()
            self._save()
        except Exception as exc:
            self.status.setText(f"Could not play this file: {exc}")

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState: self.player.pause()
        elif self.player.source().isValid(): self.player.play()
        elif self._queue: self._play_queue_position(self._queue_pos)

    def next_track(self) -> None:
        if not self._queue: return
        if self.shuffle.isChecked():
            choices = [index for index in range(len(self._queue)) if index != self._queue_pos]
            self._queue_pos = random.choice(choices) if choices else self._queue_pos
        elif self._queue_pos + 1 < len(self._queue): self._queue_pos += 1
        elif self.repeat.currentIndex() == 1: self._queue_pos = 0
        else: return
        self._play_queue_position(self._queue_pos)

    def previous(self) -> None:
        if self.player.position() > 3000:
            self.player.setPosition(0); return
        if self._queue:
            self._queue_pos = max(0, self._queue_pos - 1); self._play_queue_position(self._queue_pos)

    def _play_queue_position(self, index: int) -> None:
        if not 0 <= index < len(self._queue):
            return
        self._queue_pos = index
        path = self._queue[index]
        if not Path(path).is_file():
            self.status.setText(f"Track is unavailable: {path}")
            self._render_queue()
            return
        self.play_track(path)

    def _position_changed(self, pos: int) -> None:
        if not self.progress.isSliderDown(): self.progress.setValue(pos)
        self.current_time.setText(self._time(pos))

    def _duration_changed(self, duration: int) -> None:
        self.progress.setRange(0, duration); self.total_time.setText(self._time(duration))
        position = getattr(self, "_restore_position", 0)
        if position and duration > 0:
            self.player.setPosition(min(position, duration))
            self._restore_position = 0

    def _playback_changed(self, state) -> None:
        self.play.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")

    def _media_status_changed(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.repeat.currentIndex() == 2:
                self.player.setPosition(0); self.player.play()
            elif self.settings.music_auto_next:
                self.next_track()

    @staticmethod
    def _time(ms: int) -> str:
        return f"{ms // 60000}:{(ms // 1000) % 60:02d}"

    def _set_volume(self, value: int) -> None:
        self.settings.music_volume = int(value); self.output.setVolume(value / 100.0)
        if self.controller is not None:
            self._persist_music_preferences()
        self._save()

    def _set_shuffle(self, value: bool) -> None:
        self.settings.music_shuffle = value
        if self.controller is not None:
            self._persist_music_preferences()
        self._save()

    def _set_repeat(self, index: int) -> None:
        self.settings.music_repeat = ["off", "all", "one"][index]
        if self.controller is not None:
            self._persist_music_preferences()
        self._save()

    def _render_queue(self) -> None:
        self.queue_list.clear()
        for i, path in enumerate(self._queue):
            self.queue_list.addItem(("▶  " if i == self._queue_pos else "") + Path(path).stem)
        if self.controller is not None:
            self._persist_music_preferences()
        self._save()

    def _remove_queue(self) -> None:
        row = self.queue_list.currentRow()
        if row >= 0:
            current_track = self.player.source().toLocalFile()
            removed_current = self._queue[row] == current_track
            was_before_current = row < self._queue_pos
            self._queue.pop(row)
            if was_before_current:
                self._queue_pos -= 1
            elif removed_current:
                self._queue_pos = min(row, max(0, len(self._queue) - 1))
            self._render_queue()
            if removed_current:
                if self._queue:
                    self._play_queue_position(self._queue_pos)
                else:
                    self.player.stop(); self.player.setSource(QUrl()); self.track_title.setText("Nothing playing")
                    self.track_artist.setText("Choose a local track")

    def _move_queue(self, delta: int) -> None:
        row = self.queue_list.currentRow(); target = row + delta
        if 0 <= row < len(self._queue) and 0 <= target < len(self._queue):
            self._queue[row], self._queue[target] = self._queue[target], self._queue[row]
            if self._queue_pos == row: self._queue_pos = target
            elif self._queue_pos == target: self._queue_pos = row
            self._render_queue(); self.queue_list.setCurrentRow(target)

    def _render_playlists(self) -> None:
        current = self.playlist_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.playlist_list.currentItem() else None
        self.playlist_list.clear()
        for name, tracks in self.settings.music_playlists.items():
            item = QListWidgetItem(f"{name} · {len(tracks)} tracks")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.playlist_list.addItem(item)
            if name == current:
                self.playlist_list.setCurrentItem(item)

    def _create_playlist(self) -> None:
        name, ok = QInputDialog.getText(self, "New playlist", "Playlist name:")
        if ok and name.strip() and name.strip() not in self.settings.music_playlists:
            self.settings.music_playlists[name.strip()] = []; self._render_playlists(); self._persist_music_preferences(); self._save()

    def _rename_playlist(self) -> None:
        item = self.playlist_list.currentItem()
        if item is None: return
        old = item.data(Qt.ItemDataRole.UserRole)
        name, ok = QInputDialog.getText(self, "Rename playlist", "Playlist name:", text=old)
        if ok and name.strip() and name.strip() != old and name.strip() not in self.settings.music_playlists:
            self.settings.music_playlists[name.strip()] = self.settings.music_playlists.pop(old)
            self._render_playlists(); self._persist_music_preferences(); self._save()

    def _delete_playlist(self) -> None:
        item = self.playlist_list.currentItem()
        if item is not None:
            name = item.data(Qt.ItemDataRole.UserRole)
            del self.settings.music_playlists[name]
            self._render_playlists(); self._persist_music_preferences(); self._save()

    def _add_to_playlist(self) -> None:
        track = self.library_list.currentItem(); playlist = self.playlist_list.currentItem()
        if not track or not playlist: return
        name = playlist.data(Qt.ItemDataRole.UserRole); path = track.data(Qt.ItemDataRole.UserRole)
        if path not in self.settings.music_playlists[name]:
            self.settings.music_playlists[name].append(path); self._render_playlists(); self._persist_music_preferences(); self._save()

    def _persist_music_preferences(self) -> None:
        if self.controller is None or getattr(self, "_syncing_music_settings", False):
            return
        manager = self.controller.config_manager
        config = manager.config.clone()
        config.settings.music_queue = list(self._queue)
        config.settings.music_playlists = {name: list(paths) for name, paths in self.settings.music_playlists.items()}
        config.settings.music_volume = int(self.volume.value())
        config.settings.music_shuffle = self.shuffle.isChecked()
        config.settings.music_repeat = ["off", "all", "one"][self.repeat.currentIndex()]
        self._syncing_music_settings = True
        try:
            manager.save(config, reason="music-preferences")
            self.settings = self.controller.config_manager.config.settings
            self.controller.apply_settings(config=manager.config)
        except OSError as exc:
            self.status.setText(f"Could not save Music Hub preferences: {exc}")
        finally:
            self._syncing_music_settings = False

    def _play_playlist(self, item) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        paths = self.settings.music_playlists.get(name, [])
        if not paths:
            self.status.setText(f"Playlist “{name}” is empty.")
            return
        self._queue = list(paths); self._queue_pos = 0; self._render_queue()
        self.tabs.setCurrentIndex(1)
        if not Path(self._queue[0]).is_file():
            self.status.setText(f"Playlist track is unavailable: {self._queue[0]}")
            return
        self._play_queue_position(0)


class ProductivityPanel(FeatureWindow):
    """Persistent timer, notes, tasks and live system information."""
    def __init__(self, controller, store: ProductivityStore) -> None:
        super().__init__(controller, "ORBIT Widgets", 810, 620)
        self.store = store; self._stopwatch_start = None; self._stopwatch_elapsed = 0
        self.sticky_tasks = StickyTasksWindow(controller, store, self)
        root = QWidget(); layout = QVBoxLayout(root); layout.setContentsMargins(18, 18, 18, 18)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs); self.setCentralWidget(root)
        self._build_timer(); self._build_notes(); self._build_tasks(); self._build_system()
        self.clock = QTimer(self); self.clock.setInterval(500); self.clock.timeout.connect(self._tick); self.clock.start()
        self._system_timer = QTimer(self); self._system_timer.setInterval(3000); self._system_timer.timeout.connect(self._update_system); self._system_timer.start()
        self._render_notes(); self._render_tasks(); self._render_timers(); self._update_system()
        self.apply_settings(controller.config_manager.config.settings)
        if any(task.get("pinned") for task in self.store.data.tasks):
            self.sticky_tasks.show()

    def show_panel(self) -> None:
        self._render_notes(); self._render_tasks(); self._render_timers(); self._update_system(); self._open_centered()

    def open_widget(self, key: str) -> None:
        page = getattr(self, "_widget_pages", {}).get(key)
        if page is None:
            page = {"timer": self.tabs.widget(0), "notes": self.tabs.widget(1),
                    "tasks": self.tabs.widget(2), "system": self.tabs.widget(3)}.get(key)
        if page is not None:
            index = self.tabs.indexOf(page)
            if index >= 0 and self.tabs.isTabEnabled(index):
                self.tabs.setCurrentIndex(index)

    def _build_timer(self) -> None:
        page=QWidget(); layout=QVBoxLayout(page); row=QHBoxLayout()
        self.minutes=QSpinBox(); self.minutes.setRange(0,999); self.minutes.setValue(5)
        self.seconds=QSpinBox(); self.seconds.setRange(0,59)
        for widget in (QLabel("Countdown"),self.minutes,QLabel("min"),self.seconds,QLabel("sec")): row.addWidget(widget)
        self.start_timer_button=QPushButton("Start timer"); self.start_timer_button.clicked.connect(self._start_timer); row.addWidget(self.start_timer_button)
        layout.addLayout(row); self.timer_list=QListWidget(); layout.addWidget(self.timer_list,1)
        sw=QHBoxLayout(); self.stopwatch_display=QLabel("00:00.0"); self.stopwatch_display.setStyleSheet("font-size:24px;font-weight:700")
        self.sw_start=QPushButton("Start stopwatch"); self.sw_reset=QPushButton("Reset")
        self.sw_start.clicked.connect(self._toggle_stopwatch); self.sw_reset.clicked.connect(self._reset_stopwatch)
        sw.addWidget(self.stopwatch_display); sw.addWidget(self.sw_start); sw.addWidget(self.sw_reset); layout.addLayout(sw); self.tabs.addTab(page,"Timers")

    def _build_notes(self) -> None:
        page=QWidget(); layout=QVBoxLayout(page); row=QHBoxLayout()
        self.note_list=QListWidget(); self.note_list.setMaximumWidth(190); row.addWidget(self.note_list)
        editor=QVBoxLayout(); self.note_title=QLineEdit(); self.note_title.setPlaceholderText("Note title")
        self.note_text=QPlainTextEdit(); self.note_text.setPlaceholderText("Write a note… changes save automatically")
        editor.addWidget(self.note_title); editor.addWidget(self.note_text); row.addLayout(editor,1); layout.addLayout(row,1)
        buttons=QHBoxLayout(); add=QPushButton("New note"); delete=QPushButton("Delete note"); self.pin_note=QCheckBox("Show in radial wheel")
        buttons.addWidget(add); buttons.addWidget(delete); buttons.addWidget(self.pin_note); layout.addLayout(buttons); self.tabs.addTab(page,"Notes")
        self._loading_note=False; self.note_list.currentRowChanged.connect(self._select_note)
        self.note_title.textChanged.connect(self._save_current_note); self.note_text.textChanged.connect(self._save_current_note); self.pin_note.toggled.connect(self._save_current_note)
        add.clicked.connect(self._new_note); delete.clicked.connect(self._delete_note)

    def _build_tasks(self) -> None:
        page=QWidget(); layout=QVBoxLayout(page); row=QHBoxLayout(); self.task_edit=QLineEdit(); self.task_edit.setPlaceholderText("Add a task…")
        add=QPushButton("Add task"); add.clicked.connect(self._add_task); self.task_edit.returnPressed.connect(self._add_task)
        row.addWidget(self.task_edit,1); row.addWidget(add); layout.addLayout(row)
        self.task_list=QListWidget(); layout.addWidget(self.task_list,1)
        buttons=QHBoxLayout(); self.task_edit_button=QPushButton("Edit selected"); self.task_pin_button=QPushButton("📌 Pin to desktop"); self.task_radial_button=QPushButton("Show in wheel"); self.task_delete_button=QPushButton("Delete selected")
        self.task_edit_button.clicked.connect(self._edit_task); self.task_delete_button.clicked.connect(self._delete_task)
        self.task_pin_button.clicked.connect(self._toggle_selected_pin)
        self.task_radial_button.clicked.connect(self._toggle_selected_radial)
        buttons.addWidget(self.task_edit_button); buttons.addWidget(self.task_pin_button); buttons.addWidget(self.task_radial_button); buttons.addWidget(self.task_delete_button); layout.addLayout(buttons); self.tabs.addTab(page,"Tasks")
        self.task_list.currentItemChanged.connect(lambda *_: self._update_task_buttons())
        self.task_list.itemChanged.connect(self._task_changed)

    def _build_system(self) -> None:
        page=QWidget(); form=QFormLayout(page); self.cpu_label=QLabel("—"); self.memory_label=QLabel("—")
        self.battery_label=QLabel("Unavailable"); self.datetime_label=QLabel("—")
        form.addRow("CPU",self.cpu_label); form.addRow("Memory",self.memory_label); form.addRow("Battery",self.battery_label); form.addRow("Local time",self.datetime_label); self.tabs.addTab(page,"System")

    def _start_timer(self) -> None:
        duration=self.minutes.value()*60+self.seconds.value()
        if duration<=0:return
        self.store.data.timers.append(new_record(name=f"Timer {len(self.store.data.timers)+1}",duration=duration,remaining=duration,running=True,deadline=time.time()+duration))
        self._save(); self._render_timers()

    def _toggle_timer(self, record: dict) -> None:
        if record.get("running"):
            record["remaining"]=max(0,int(record.get("deadline",time.time())-time.time())); record["running"]=False
        else:
            record["running"]=True; record["deadline"]=time.time()+record.get("remaining",0)
        self._save(); self._render_timers()

    def _remove_timer(self, record: dict) -> None:
        self.store.data.timers=[item for item in self.store.data.timers if item.get("id")!=record.get("id")]; self._save(); self._render_timers()

    def _render_timers(self) -> None:
        self.timer_list.clear()
        for record in self.store.data.timers:
            remaining=max(0,int(record.get("deadline",time.time())-time.time())) if record.get("running") else int(record.get("remaining",0))
            row=QWidget(); line=QHBoxLayout(row); line.setContentsMargins(4,2,4,2)
            label=QLabel(f"{record.get('name','Timer')}   {remaining//60:02d}:{remaining%60:02d}")
            toggle=QPushButton("Pause" if record.get("running") else "Resume"); remove=QPushButton("×")
            toggle.clicked.connect(lambda _=False,r=record:self._toggle_timer(r)); remove.clicked.connect(lambda _=False,r=record:self._remove_timer(r))
            line.addWidget(label,1); line.addWidget(toggle); line.addWidget(remove)
            item=QListWidgetItem(); item.setSizeHint(row.sizeHint()); self.timer_list.addItem(item); self.timer_list.setItemWidget(item,row)

    def _tick(self) -> None:
        if self._stopwatch_start is not None:
            elapsed=self._stopwatch_elapsed+time.monotonic()-self._stopwatch_start
            self.stopwatch_display.setText(f"{int(elapsed)//60:02d}:{int(elapsed)%60:02d}.{int(elapsed*10)%10}")
        expired=[]
        for record in self.store.data.timers:
            if record.get("running") and time.time()>=record.get("deadline",0):
                record["running"]=False; record["remaining"]=0; expired.append(record.get("name","Timer"))
        if expired:
            self._save(); self._render_timers()
            for name in expired: QApplication.beep(); self.controller.tray.notify("ORBIT timer complete",name)
        elif any(record.get("running") for record in self.store.data.timers) and self.isVisible(): self._render_timers()

    def _toggle_stopwatch(self) -> None:
        if self._stopwatch_start is None:
            self._stopwatch_start=time.monotonic(); self.sw_start.setText("Pause stopwatch")
        else:
            self._stopwatch_elapsed+=time.monotonic()-self._stopwatch_start; self._stopwatch_start=None; self.sw_start.setText("Resume stopwatch")

    def _reset_stopwatch(self) -> None:
        self._stopwatch_start=None; self._stopwatch_elapsed=0; self.stopwatch_display.setText("00:00.0"); self.sw_start.setText("Start stopwatch")

    @staticmethod
    def _note_order(note: dict) -> tuple[bool, float]:
        try:
            updated = float(note.get("updated", 0))
        except (TypeError, ValueError):
            updated = 0.0
        return not bool(note.get("pinned")), -updated

    def _render_notes(self) -> None:
        self.note_list.blockSignals(True); self.note_list.clear()
        notes = sorted(self.store.data.notes, key=self._note_order)
        for note in notes:
            item = QListWidgetItem(("★ " if note.get("pinned") else "")+(note.get("title") or "Untitled"))
            item.setData(Qt.ItemDataRole.UserRole, note.get("id"))
            self.note_list.addItem(item)
        self.note_list.blockSignals(False)
        if self.store.data.notes and self.note_list.currentRow()<0:self.note_list.setCurrentRow(0)

    def _select_note(self,row:int) -> None:
        item = self.note_list.item(row) if row >= 0 else None
        note_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        note = next((entry for entry in self.store.data.notes if entry.get("id") == note_id), None)
        if note is not None:
            self._loading_note=True; self.note_title.setText(note.get("title","")); self.note_text.setPlainText(note.get("text","")); self.pin_note.setChecked(note.get("pinned",False)); self._loading_note=False

    def _save_current_note(self,*_args) -> None:
        if self._loading_note:return
        row=self.note_list.currentRow()
        item = self.note_list.item(row) if row >= 0 else None
        note_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        note = next((entry for entry in self.store.data.notes if entry.get("id") == note_id), None)
        if note is not None:
            note.update(title=self.note_title.text(),text=self.note_text.toPlainText(),pinned=self.pin_note.isChecked(),updated=time.time())
            try:
                self.store.save()
            except OSError as exc:
                self.controller.tray.notify("ORBIT note could not be saved", str(exc), ok=False)
                return
            item.setText(("★ " if note["pinned"] else "")+(note["title"] or "Untitled"))
            if hasattr(self.controller, "overlay"):
                self.controller.overlay._refresh_radial_notes()

    def _new_note(self) -> None:
        note = new_record(title="", text="", pinned=False, updated=time.time())
        self.store.data.notes.append(note)
        self._save()
        self._render_notes()
        for row in range(self.note_list.count()):
            if self.note_list.item(row).data(Qt.ItemDataRole.UserRole) == note["id"]:
                self.note_list.setCurrentRow(row)
                break
        self.note_title.setFocus()

    def _delete_note(self) -> None:
        item = self.note_list.currentItem()
        note_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        note = next((entry for entry in self.store.data.notes if entry.get("id") == note_id), None)
        if note is not None:
            self.store.data.notes.remove(note)
            self._save()
            self._render_notes()
            self.note_title.clear()
            self.note_text.clear()

    def _render_tasks(self) -> None:
        self.task_list.blockSignals(True); self.task_list.clear()
        for task in sorted(self.store.data.tasks,key=lambda item:(item.get("done",False),item.get("created",0))):
            item=QListWidgetItem(task.get("title", "")); item.setData(Qt.ItemDataRole.UserRole,task.get("id")); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(Qt.CheckState.Checked if task.get("done") else Qt.CheckState.Unchecked)
            item.setToolTip("Pinned tasks stay visible in a small always-on-top window.")
            if task.get("pinned"):
                item.setText("📌  " + (task.get("title") or "Untitled task"))
            self.task_list.addItem(item)
        self.task_list.blockSignals(False)
        self._update_task_buttons()
        if hasattr(self, "sticky_tasks"):
            self.sticky_tasks.refresh()
        if hasattr(self.controller, "overlay"):
            self.controller.overlay._refresh_radial_notes()

    def _add_task(self) -> None:
        title=self.task_edit.text().strip()
        if title:self.store.data.tasks.append(new_record(title=title,done=False,pinned=False,created=time.time())); self.task_edit.clear(); self._save(); self._render_tasks()

    def _task_changed(self,item) -> None:
        task=next((task for task in self.store.data.tasks if task.get("id")==item.data(Qt.ItemDataRole.UserRole)),None)
        if task:
            task["title"]=item.text().removeprefix("📌  "); task["done"]=item.checkState()==Qt.CheckState.Checked; self._save(); QTimer.singleShot(0,self._render_tasks)

    def _update_task_buttons(self) -> None:
        item = self.task_list.currentItem() if hasattr(self, "task_list") else None
        task = next((record for record in self.store.data.tasks if record.get("id") == item.data(Qt.ItemDataRole.UserRole)), None) if item else None
        self.task_edit_button.setEnabled(task is not None)
        self.task_delete_button.setEnabled(task is not None)
        self.task_pin_button.setEnabled(task is not None)
        self.task_radial_button.setEnabled(task is not None)
        self.task_pin_button.setText("📌 Unpin from desktop" if task and task.get("pinned") else "📌 Pin to desktop")
        self.task_radial_button.setText("Hide from wheel" if task and task.get("radial_pinned") else "Show in wheel")

    def _toggle_selected_pin(self) -> None:
        item = self.task_list.currentItem()
        if not item:
            return
        task = next((record for record in self.store.data.tasks if record.get("id") == item.data(Qt.ItemDataRole.UserRole)), None)
        if task is None:
            return
        task["pinned"] = not bool(task.get("pinned"))
        self._save(); self._render_tasks()
        if task["pinned"]:
            self.sticky_tasks.show(); self.sticky_tasks.raise_(); self.sticky_tasks.activateWindow()

    def _toggle_selected_radial(self) -> None:
        item = self.task_list.currentItem()
        if not item:
            return
        task = next((record for record in self.store.data.tasks if record.get("id") == item.data(Qt.ItemDataRole.UserRole)), None)
        if task is None:
            return
        task["radial_pinned"] = not bool(task.get("radial_pinned"))
        self._save(); self._render_tasks()
        if hasattr(self.controller, "overlay"):
            self.controller.overlay._refresh_radial_notes()

    def _edit_task(self) -> None:
        item=self.task_list.currentItem()
        if not item:
            return
        current = item.text().replace("📌  ", "", 1)
        title, ok = QInputDialog.getText(self, "Edit task", "Task", text=current)
        if ok and title.strip():
            task = next((record for record in self.store.data.tasks if record.get("id") == item.data(Qt.ItemDataRole.UserRole)), None)
            if task:
                task["title"] = title.strip(); self._save(); self._render_tasks()

    def _delete_task(self) -> None:
        item=self.task_list.currentItem()
        if item:self.store.data.tasks=[task for task in self.store.data.tasks if task.get("id")!=item.data(Qt.ItemDataRole.UserRole)]; self._save(); self._render_tasks()

    def _update_system(self) -> None:
        try:
            import psutil
            self.cpu_label.setText(f"{psutil.cpu_percent(interval=None):.0f}%")
            memory=psutil.virtual_memory(); self.memory_label.setText(f"{memory.percent:.0f}% · {memory.used//(1024**3)} / {memory.total//(1024**3)} GB")
            battery=psutil.sensors_battery(); self.battery_label.setText(f"{battery.percent:.0f}%"+(" · charging" if battery.power_plugged else "") if battery else "Unavailable on this device")
        except Exception as exc:
            self.cpu_label.setText(f"Unavailable ({exc})"); self.memory_label.setText("Unavailable")
        from datetime import datetime
        self.datetime_label.setText(datetime.now().astimezone().strftime("%A, %B %d · %H:%M:%S %Z"))

    def apply_settings(self, settings) -> None:
        enabled = list(settings.widgets_enabled)
        ordered = [key for key in settings.widgets_order if key in enabled]
        ordered.extend(key for key in enabled if key not in ordered)
        page_by_key = dict(getattr(self, "_widget_pages", {}))
        if not page_by_key:
            page_by_key = {"timer": self.tabs.widget(0), "notes": self.tabs.widget(1),
                           "tasks": self.tabs.widget(2), "system": self.tabs.widget(3)}
            self._widget_pages = page_by_key
        labels = {"timer": "Timer", "notes": "Notes", "tasks": "Tasks", "system": "System"}
        for key, page in page_by_key.items():
            index = self.tabs.indexOf(page)
            if index >= 0:
                self.tabs.removeTab(index)
        for key in ordered:
            self.tabs.addTab(page_by_key[key], labels[key])
            self.tabs.setTabEnabled(self.tabs.count() - 1, True)
        disabled_order = [key for key in ("timer", "notes", "tasks", "system") if key not in enabled]
        for key in disabled_order:
            self.tabs.addTab(page_by_key[key], labels[key])
            self.tabs.setTabEnabled(self.tabs.count() - 1, False)

    def shutdown(self) -> None:
        self.clock.stop(); self._system_timer.stop(); self.sticky_tasks.close(); self._save()

    def _save(self) -> None:
        try:
            self.store.save()
        except OSError as exc:
            self.controller.tray.notify("ORBIT widgets could not be saved", str(exc), ok=False)
