"""The transparent desktop overlay that hosts the radial menu.

Responsibilities:

* pick the right monitor (cursor, primary or a named/indexed screen),
* cover exactly that screen so the rest of the desktop stays usable,
* dim the desktop subtly and animate the menu in and out,
* keep a navigation stack for nested menus and make dismissal bullet-proof.

The overlay never executes anything itself; it reports the activated action so
the application controller can confirm, run and report it.
"""

from __future__ import annotations

from typing import Callable, Any
import math
import uuid

from PySide6.QtCore import (
    QEvent,
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCursor, QColor, QGuiApplication, QLinearGradient, QPainter, QRadialGradient, QScreen
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSlider, QVBoxLayout, QWidget

from core.launcher import set_foreground_hwnd
from core.models import Action, Config, Menu
from ui.radial_menu import RadialMenu
from ui.theme_manager import Theme

MAX_NAV_DEPTH = 6
SCREEN_MARGIN = 24


class RadialNoteCard(QFrame):
    """A draggable, temporary card shown around the radial wheel."""

    def __init__(self, record: dict[str, Any], kind: str, on_change, on_hide, on_move, on_trash, parent=None) -> None:
        super().__init__(parent)
        self.record = record
        self.kind = kind
        self.on_change = on_change
        self.on_move = on_move
        self.on_trash = on_trash
        self._drag_origin = None
        self.setObjectName("RadialNoteCard")
        accent = str(record.get("color") or "#414C61")
        self.setStyleSheet(
            f"QFrame#RadialNoteCard {{ background:#20242B; border:1px solid {accent}; border-radius:8px; }}"
            "QLabel#RadialNoteTitle { color:#E7EAF0; font-size:11px; font-weight:600; }"
            "QLabel#RadialNoteBody { color:#B6BDC9; font-size:12px; }"
            "QCheckBox { color:#D7DCE5; font-size:12px; spacing:6px; }"
            "QCheckBox:hover { color:#FFFFFF; }"
            "QPushButton { color:#929BAA; background:transparent; border:0; font-size:15px; }"
            "QPushButton:hover { color:#FFFFFF; }"
        )
        self.setFixedWidth(245)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        layout = QVBoxLayout(self); layout.setContentsMargins(12, 10, 10, 10); layout.setSpacing(5)
        header = QHBoxLayout(); header.setSpacing(6)
        icon = str(record.get("icon") or ("☑" if kind == "task" else "✦"))
        label = "TIMER" if kind == "timer" else ("CHECKLIST" if kind == "task" else "NOTE")
        title = QLabel(f"{icon}  {label}  ·  {record.get('title') or 'Untitled'}")
        title.setObjectName("RadialNoteTitle"); title.setWordWrap(True)
        close = QPushButton("×"); close.setFixedSize(22, 22); close.setToolTip("Hide from the radial menu")
        # QPushButton.clicked emits a bool; do not let it replace the record
        # captured by the hide callback.
        close.clicked.connect(lambda _checked=False, handler=on_hide: handler())
        header.addWidget(title, 1); header.addWidget(close)
        layout.addLayout(header)
        meta = []
        if record.get("priority"): meta.append(f"Priority: {record['priority']}")
        if record.get("tags"): meta.append(" ".join(f"#{tag}" for tag in record["tags"]))
        if record.get("due"): meta.append(f"Due {record['due']}")
        if meta:
            meta_label = QLabel("  ·  ".join(meta)); meta_label.setObjectName("RadialNoteBody"); layout.addWidget(meta_label)
        if kind == "timer":
            self.body = QLabel(); self.body.setObjectName("RadialNoteBody"); layout.addWidget(self.body)
            self._update_timer_text()
        elif kind == "task":
            items = record.get("items") if isinstance(record.get("items"), list) else []
            if not items:
                items = [{"title": record.get("title") or "Untitled task", "done": bool(record.get("done"))}]
            for item in items:
                body = QCheckBox(item.get("title") or "Untitled item")
                body._orbit_item_title = item.get("title") or "Untitled item"
                body.setChecked(bool(item.get("done")))
                self._sync_checkmark(body, bool(item.get("done")))
                body.stateChanged.connect(lambda state, entry=item, checkbox=body: self._item_changed(entry, state, checkbox))
                layout.addWidget(body)
        else:
            self.body = QLabel(record.get("text") or "Empty note")
            self.body.setWordWrap(True); self.body.setObjectName("RadialNoteBody")
            layout.addWidget(self.body)

        if kind == "task" and isinstance(record.get("items"), list):
            done = sum(1 for item in record["items"] if item.get("done"))
            progress = QLabel(f"{done}/{len(record['items'])} complete"); progress.setObjectName("RadialNoteBody"); layout.addWidget(progress)

    def _update_timer_text(self) -> None:
        if self.kind != "timer": return
        remaining = max(0, int(self.record.get("deadline", 0) - __import__("time").time())) if self.record.get("running") else int(self.record.get("remaining", 0))
        self.body.setText(f"{remaining // 60:02d}:{remaining % 60:02d}")

    def _item_changed(self, item: dict, state: int, checkbox: QCheckBox | None = None) -> None:
        # PySide6 may deliver either the enum itself or its legacy integer
        # representation depending on the Qt binding version.
        checked = state == Qt.CheckState.Checked or getattr(state, "value", state) == Qt.CheckState.Checked.value
        item["done"] = checked
        if isinstance(self.record.get("items"), list):
            self.record["done"] = bool(self.record["items"]) and all(entry.get("done") for entry in self.record["items"])
        else:
            self.record["done"] = item["done"]
        if isinstance(checkbox, QCheckBox):
            self._sync_checkmark(checkbox, checked)
        self.on_change()

    @staticmethod
    def _sync_checkmark(checkbox: QCheckBox, checked: bool) -> None:
        """Keep a visible mark even when the app-wide Qt theme hides native glyphs."""
        title = getattr(checkbox, "_orbit_item_title", checkbox.text())
        checkbox.setText(f"✓  {title}" if checked else f"○  {title}")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            parent = self.parentWidget()
            if parent is not None:
                point = event.globalPosition().toPoint() - self._drag_origin
                x = max(12, min(parent.width() - self.width() - 12, point.x()))
                y = max(12, min(parent.height() - self.height() - 12, point.y()))
                self.move(x, y)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if self.on_trash(self):
            super().mouseReleaseEvent(event)
            return
        self.on_move(self)
        super().mouseReleaseEvent(event)


class RadialNotesLayer:
    """Builds short-lived note/checklist cards around the wheel."""

    def __init__(self, overlay, parent=None) -> None:
        self.overlay = overlay
        self.cards: list[RadialNoteCard] = []

    def refresh(self, menu_rect) -> None:
        # Timer updates must never rebuild a card while it is being dragged.
        if any(card._drag_origin is not None for card in self.cards):
            return
        for card in self.cards:
            card.deleteLater()
        self.cards.clear()
        cards = []
        store = getattr(self.overlay, "_productivity_store", None)
        if store is not None:
            cards.extend((record, "task") for record in store.data.tasks if record.get("radial_pinned"))
            cards.extend((record, "note") for record in store.data.notes if record.get("pinned"))
            cards.extend((record, "timer") for record in store.data.timers if record.get("radial_pinned"))
        cards = cards[:6]
        if not cards:
            self.overlay._set_trash_visible(False)
            return
        self.overlay._set_trash_visible(True)
        for index, (record, kind) in enumerate(cards):
            card = RadialNoteCard(record, kind, self._save, lambda _checked=False, r=record, k=kind: self._hide_record(r, k), self._card_moved, self._card_dropped_on_trash, self.overlay)
            self.cards.append(card)
            card.adjustSize()
            if record.get("radial_layout_version") != 2 or record.get("radial_x") is None or record.get("radial_y") is None:
                side_left = index % 2 == 0
                x = 28 if side_left else self.overlay.width() - card.width() - 28
                row = index // 2
                rows = (len(cards) + 1) // 2
                y = self.overlay.height() / 2 - (rows * card.height() + (rows - 1) * 12) / 2 + row * (card.height() + 12)
                card.move(max(12, int(x)), max(12, int(y)))
                record["radial_x"] = card.x(); record["radial_y"] = card.y(); record["radial_layout_version"] = 2
            else:
                x = max(12, min(self.overlay.width() - card.width() - 12, int(record.get("radial_x", 12))))
                y = max(12, min(self.overlay.height() - card.height() - 12, int(record.get("radial_y", 12))))
                card.move(x, y)
            card.show(); card.raise_()

    def _card_moved(self, card) -> None:
        card.record["radial_x"] = card.x(); card.record["radial_y"] = card.y(); card.record["radial_layout_version"] = 2; self._save()

    def _card_dropped_on_trash(self, card) -> bool:
        trash = getattr(self.overlay, "trash_bin", None)
        if trash is None or not trash.isVisible() or not trash.geometry().intersects(card.geometry()):
            return False
        self._hide_record(card.record, card.kind)
        return True

    def _save(self) -> None:
        store = getattr(self.overlay, "_productivity_store", None)
        if store is not None:
            try: store.save()
            except OSError: pass

    def _hide_record(self, record: dict[str, Any], kind: str | None = None) -> None:
        # Notes use `pinned`; checklists and timers use `radial_pinned`.
        if kind == "note":
            record["pinned"] = False
        elif kind in {"task", "timer"}:
            record["radial_pinned"] = False
        elif "radial_pinned" in record:
            record["radial_pinned"] = False
        else:
            record["pinned"] = False
        self._save(); self.refresh(self.overlay.menu.geometry())

    def hide(self) -> None:
        for card in self.cards:
            card.hide()
        self.overlay._set_trash_visible(False)


class RadialOverlay(QWidget):
    """Frameless, always-on-top overlay showing one radial menu at a time."""

    actionRequested = Signal(object)   # Action
    opened = Signal()
    closed = Signal()
    navigationBlocked = Signal(str)

    def __init__(
        self,
        config_provider: Callable[[], Config],
        theme_provider: Callable[[], Theme],
        runner=None,
        registry=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_provider = config_provider
        self._theme_provider = theme_provider
        self._runner = runner
        self._registry = registry

        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle("ORBIT")
        self.setMouseTracking(True)

        self.menu = RadialMenu(self)
        self.menu.actionTriggered.connect(self.actionRequested)
        self.menu.requestClose.connect(self.dismiss)
        self.menu.requestBack.connect(self.go_back)
        self._productivity_store = None
        self.radial_notes = RadialNotesLayer(self, self)
        self.add_bar = QFrame(self)
        self.add_bar.setObjectName("RadialAddBar")
        self.add_bar.setStyleSheet(
            "QFrame#RadialAddBar { background:#20242B; border:1px solid #3B424D; border-radius:8px; }"
            "QPushButton { color:#D7DCE5; background:#2A2F37; border:1px solid #414954; border-radius:6px; padding:6px 10px; font-size:11px; }"
            "QPushButton:hover { background:#343B46; border-color:#667386; }"
        )
        add_layout = QHBoxLayout(self.add_bar); add_layout.setContentsMargins(6, 6, 6, 6); add_layout.setSpacing(6)
        add_note = QPushButton("＋ ADD NOTE"); add_checklist = QPushButton("☑ ADD CHECKLIST"); add_timer = QPushButton("◷ ADD TIMER")
        add_note.clicked.connect(lambda: self.add_radial_item("note"))
        add_checklist.clicked.connect(lambda: self.add_radial_item("task"))
        add_timer.clicked.connect(lambda: self.add_radial_item("timer"))
        add_layout.addWidget(add_note); add_layout.addWidget(add_checklist); add_layout.addWidget(add_timer)
        self.add_bar.adjustSize(); self.add_bar.hide()
        self.music_bar = QFrame(self)
        self.music_bar.setStyleSheet("QFrame { background:#1A202B; border:1px solid #4A5870; border-radius:12px; } QPushButton { color:#F1F4FA; background:transparent; border:0; padding:7px; font-size:14px; } QPushButton:hover { background:#33405A; border-radius:7px; } QSlider::groove:horizontal { height:4px; background:#3B465A; } QSlider::sub-page:horizontal { background:#6C8CFF; } QSlider::handle:horizontal { width:12px; background:#F1F4FA; margin:-4px 0; border-radius:6px; }")
        music_layout = QHBoxLayout(self.music_bar); music_layout.setContentsMargins(6, 4, 8, 4); music_layout.setSpacing(2)
        self.music_previous = QPushButton("◀"); self.music_toggle = QPushButton("▶"); self.music_next = QPushButton("▶▶"); self.music_volume = QSlider(Qt.Orientation.Horizontal); self.music_volume.setRange(0, 100); self.music_volume.setValue(70); self.music_volume.setFixedWidth(100)
        for button in (self.music_previous, self.music_toggle, self.music_next): music_layout.addWidget(button)
        music_layout.addWidget(QLabel("Vol")); music_layout.addWidget(self.music_volume)
        self.music_bar.adjustSize(); self.music_bar.hide(); self._music_player = None
        self.trash_bin = QPushButton("Drop here to delete", self)
        self.trash_bin.setFixedSize(142, 38)
        self.trash_bin.setStyleSheet(
            "QPushButton { color:#E2A7A7; background:#302328; border:1px solid #70454B; border-radius:7px; font-size:11px; }"
            "QPushButton:hover { background:#402B31; border-color:#A86A70; }"
        )
        self.trash_bin.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.trash_bin.hide()

        self._stack: list[str] = []
        self._screen: QScreen | None = None
        self._fade: QPropertyAnimation | None = None
        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.timeout.connect(self.dismiss)
        self._radial_tick = QTimer(self)
        self._radial_tick.setInterval(1000)
        self._radial_tick.timeout.connect(lambda: self._refresh_radial_notes() if self.isVisible() else None)
        self._radial_tick.start()
        self._shader_phase = 0.0
        self._shader_tick = QTimer(self)
        self._shader_tick.setInterval(33)
        self._shader_tick.timeout.connect(self._advance_shader)
        self._shader_tick.start()
        self._fade_out_callback: Callable[[], None] | None = None

        self._watched_screens: set[int] = set()
        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(lambda _screen: self._watch_screens())
            app.screenRemoved.connect(lambda _screen: self._on_screen_changed())
            app.primaryScreenChanged.connect(lambda _screen: self._on_screen_changed())
        self._watch_screens()

    # ------------------------------------------------------------------- screens
    def _watch_screens(self) -> None:
        if not hasattr(self, "_watched_screens"):
            self._watched_screens = set()
        for screen in QGuiApplication.screens():
            key = id(screen)
            if key in self._watched_screens:
                continue
            screen.geometryChanged.connect(self._on_screen_changed)
            screen.availableGeometryChanged.connect(self._on_screen_changed)
            self._watched_screens.add(key)

    def _on_screen_changed(self, *_args) -> None:
        if self.isVisible():
            self._apply_geometry()

    def target_screen(self) -> QScreen | None:
        settings = self._config_provider().settings
        screens = QGuiApplication.screens()
        if not screens:
            return None
        mode = settings.monitor or "cursor"
        if mode == "primary":
            return QGuiApplication.primaryScreen()
        if mode.startswith("index:"):
            try:
                index = int(mode.split(":", 1)[1])
            except ValueError:
                index = 0
            if 0 <= index < len(screens):
                return screens[index]
            return QGuiApplication.primaryScreen()
        if mode.startswith("name:"):
            wanted = mode.split(":", 1)[1]
            for screen in screens:
                if screen.name() == wanted:
                    return screen
            return QGuiApplication.primaryScreen()
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def current_screen(self) -> QScreen | None:
        if self._screen is not None:
            return self._screen
        return QGuiApplication.primaryScreen()

    def _apply_geometry(self) -> None:
        screen = self.target_screen()
        if screen is None:
            return
        self._screen = screen
        geometry = screen.geometry()
        if self.geometry() != geometry:
            self.setGeometry(geometry)
        self._scale_menu_to_fit(geometry)
        self._position_add_bar()

    def _position_add_bar(self) -> None:
        self.add_bar.adjustSize()
        menu_rect = self.menu.geometry()
        x = menu_rect.center().x() - self.add_bar.width() // 2
        y = menu_rect.top() - self.add_bar.height() - 18
        if y < 18:
            x = menu_rect.right() + 22
            y = menu_rect.center().y() - self.add_bar.height() // 2
        x = max(18, min(self.width() - self.add_bar.width() - 18, x))
        y = max(18, min(self.height() - self.add_bar.height() - 18, y))
        self.add_bar.move(x, y)
        self.add_bar.raise_()
        self.trash_bin.move(self.width() // 2 - self.trash_bin.width() // 2, self.height() - self.trash_bin.height() - 28)
        self.trash_bin.raise_()
        self.music_bar.move(24, self.height() - self.music_bar.height() - 28)
        self.music_bar.raise_()

    def _set_trash_visible(self, visible: bool) -> None:
        if visible and self.isVisible():
            self._position_add_bar()
            self.trash_bin.show(); self.trash_bin.raise_()
        else:
            self.trash_bin.hide()

    def _scale_menu_to_fit(self, geometry) -> None:
        available_w = max(320, geometry.width() - SCREEN_MARGIN * 2)
        available_h = max(320, geometry.height() - SCREEN_MARGIN * 2)
        scale = 1.0
        for _attempt in range(3):
            self.menu.set_content_scale(scale)
            hint = self.menu.content_size_hint()
            over = max(hint.width() / available_w, hint.height() / available_h)
            if over <= 1.0:
                break
            scale = max(0.5, scale / over)
        self.menu.set_content_scale(scale)
        self._centre_menu()
        self.menu.move(
            int((self.width() - self.menu.width()) / 2),
            int((self.height() - self.menu.height()) / 2),
        )

    def _centre_menu(self) -> None:
        self.menu.move(
            int((self.width() - self.menu.width()) / 2),
            int((self.height() - self.menu.height()) / 2),
        )

    # ------------------------------------------------------------------ lifecycle
    def is_open(self) -> bool:
        return self.isVisible() and self._fade_out_callback is None

    def toggle(self) -> None:
        if self.isVisible() and self._fade_out_callback is None:
            self.dismiss()
        else:
            self.open_root()

    def open_root(self, *, menu_id: str | None = None) -> bool:
        config = self._config_provider()
        root = config.menu_by_id(menu_id) if menu_id else config.root_menu()
        if root is None:
            self.navigationBlocked.emit("The configured root menu does not exist.")
            return False
        return self.open_menu(root.id, reset=True)

    def open_menu(self, menu_id: str, *, reset: bool = False) -> bool:
        config = self._config_provider()
        menu = config.menu_by_id(menu_id)
        if menu is None:
            self.navigationBlocked.emit(f"The menu “{menu_id}” no longer exists.")
            return False

        if reset:
            self._stack = [menu.id]
        elif not self._stack:
            self._stack = [menu.id]
        elif menu_id in self._stack:
            # Recursion guard: never push a menu that is already open.
            self.navigationBlocked.emit(f"“{menu.name}” is already open.")
            return False
        else:
            if len(self._stack) >= MAX_NAV_DEPTH:
                self.navigationBlocked.emit("Nested menus reached the maximum depth.")
                return False
            self._stack.append(menu.id)

        self._prepare(menu, reset=reset)
        return True

    def _prepare(self, menu: Menu, *, reset: bool) -> None:
        config = self._config_provider()
        theme = self._theme_provider()
        self.menu.configure(config, theme, executor=getattr(self._runner, "executor", None))
        self.menu.set_breadcrumb([config.menu_by_id(item) or menu for item in self._stack])

        if self.isVisible() and not reset:
            # Geometry is re-fitted once the new menu is actually in place, so a
            # menu with more rings keeps its buttons on screen.
            self.menu.transition_to(menu, on_swapped=lambda: (self._apply_geometry(), self._refresh_radial_notes()))
        else:
            self.menu.set_menu(menu)
            self._apply_geometry()

        was_visible = self.isVisible()
        if not was_visible:
            self._auto_hide.stop()
            settings = config.settings
            if not settings.reduced_motion:
                self.setWindowOpacity(0.0)
            self.show()
            self._position_add_bar()
            self.add_bar.show()
            self.add_bar.raise_()
            self._set_trash_visible(bool(self.radial_notes.cards))
            if self._music_player is not None:
                self._position_add_bar(); self.music_bar.show(); self.music_bar.raise_()
            self._raise_and_focus()
            if settings.reduced_motion:
                self.setWindowOpacity(1.0)
                self.menu.animate_in()
            else:
                self._fade_window(1.0, seconds=0.12)
                self.menu.animate_in(delay_ms=30)
            if settings.auto_hide_seconds > 0:
                self._auto_hide.start(max(1, settings.auto_hide_seconds) * 1000)
            self.opened.emit()
        else:
            self._raise_and_focus()
        self.radial_notes.refresh(self.menu.geometry())

    def set_productivity_store(self, store) -> None:
        """Attach the shared notes/tasks store without coupling the overlay to it."""
        self._productivity_store = store

    def set_music_player(self, player) -> None:
        self._music_player = player
        self.music_previous.clicked.connect(player.previous)
        self.music_toggle.clicked.connect(player.toggle)
        self.music_next.clicked.connect(player.next_track)
        self.music_volume.setValue(player.volume.value() if hasattr(player, "volume") else 70)
        self.music_volume.valueChanged.connect(player._set_volume)

    def add_radial_item(self, kind: str) -> None:
        """Create a note/checklist directly from the open wheel."""
        store = self._productivity_store
        if store is None:
            return
        label = "Checklist" if kind == "task" else "Note"
        if kind == "timer":
            title, ok = self._radial_input("New timer", "Timer name:", default="Focus timer")
            if not ok or not title.strip(): return
            duration_text, ok = self._radial_input("Timer duration", "Minutes:", default="5")
            try: duration = max(1, int(duration_text.strip()) * 60)
            except ValueError: return
            record = {"id": uuid.uuid4().hex, "name": title.strip(), "title": title.strip(), "duration": duration, "remaining": duration, "running": True, "deadline": __import__("time").time() + duration, "radial_pinned": True, "icon": "◷"}
            store.data.timers.append(record)
            try: store.save()
            except OSError as exc: self.navigationBlocked.emit(f"Could not save timer: {exc}")
            self.radial_notes.refresh(self.menu.geometry())
            return
        title, ok = self._radial_input(f"New {label}", "Title:")
        title = title.strip()
        if not ok or not title:
            return
        record = {"id": uuid.uuid4().hex, "title": title, "created": __import__("time").time()}
        if kind == "task":
            items_text, items_ok = self._radial_input("Checklist items", "Add one item per line:", multiline=True)
            items = [{"title": line.strip(), "done": False} for line in items_text.splitlines() if line.strip()] if items_ok else []
            if not items:
                items = [{"title": title, "done": False}]
            due, _ = self._radial_input("Checklist due date", "Optional date/time:")
            record.update(done=False, pinned=False, radial_pinned=True, items=items, due=due.strip())
            store.data.tasks.append(record)
        else:
            record.update(text="", pinned=True, updated=__import__("time").time())
            store.data.notes.append(record)
        try:
            store.save()
        except OSError as exc:
            self.navigationBlocked.emit(f"Could not save {label.lower()}: {exc}")
            return
        if kind == "note":
            text, ok = self._radial_input("New note", "Note text:", multiline=True)
            if ok:
                record["text"] = text
                record["updated"] = __import__("time").time()
                tags, _ = self._radial_input("Note tags", "Tags separated by commas:")
                icon, _ = self._radial_input("Note icon", "Emoji or short icon:", default="✦")
                priority, _ = self._radial_input("Note priority", "low, normal, or high:", default="normal")
                color, _ = self._radial_input("Note color", "Hex color, for example #6C8CFF:", default="#6C8CFF")
                due, _ = self._radial_input("Note due date", "Optional date/time:")
                record.update(tags=[tag.strip() for tag in tags.split(",") if tag.strip()], icon=icon.strip() or "✦", priority=priority.strip().lower() or "normal", color=color.strip() or "#6C8CFF", due=due.strip())
                try:
                    store.save()
                except OSError as exc:
                    self.navigationBlocked.emit(f"Could not save note: {exc}")
        self.radial_notes.refresh(self.menu.geometry())

    def _radial_input(self, title: str, label: str, *, multiline: bool = False, default: str = "") -> tuple[str, bool]:
        """Show an input form above/beside the wheel instead of under it."""
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        layout = QVBoxLayout(dialog)
        prompt = QLabel(label); prompt.setWordWrap(True); layout.addWidget(prompt)
        if multiline:
            field = QPlainTextEdit(); field.setPlaceholderText("Write your note…"); field.setMinimumHeight(120)
            layout.addWidget(field)
        else:
            field = QLineEdit(); field.setPlaceholderText("Give it a short name…")
            field.setText(default)
            field.returnPressed.connect(dialog.accept)
            layout.addWidget(field)
        if multiline and default:
            field.setPlainText(default)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.adjustSize()
        dialog.resize(420 if multiline else 360, 220 if multiline else 130)
        menu_rect = self.menu.geometry()
        x = menu_rect.center().x() - dialog.width() // 2
        y = menu_rect.top() - dialog.height() - 24
        if y < 18:
            x = menu_rect.right() + 28
            y = menu_rect.center().y() - dialog.height() // 2
        x = max(18, min(self.width() - dialog.width() - 18, x))
        y = max(18, min(self.height() - dialog.height() - 18, y))
        dialog.move(self.mapToGlobal(QPoint(x, y)))
        dialog.raise_()
        dialog.activateWindow()
        field.setFocus(Qt.FocusReason.PopupFocusReason)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return (field.toPlainText() if multiline else field.text()), accepted

    def _refresh_radial_notes(self) -> None:
        if self.isVisible():
            self.radial_notes.refresh(self.menu.geometry())

    def refresh(self) -> None:
        """Rebuild from the current configuration (used after saving settings)."""
        config = self._config_provider()
        theme = self._theme_provider()
        self.menu.configure(config, theme, executor=getattr(self._runner, "executor", None))
        if self._stack:
            menus = [config.menu_by_id(item) for item in self._stack]
            menus = [menu for menu in menus if menu is not None]
            if not menus:
                self._stack = []
                return
            self._stack = [menu.id for menu in menus]
            self.menu.set_breadcrumb(menus)
            self.menu.set_menu(menus[-1])
            self._apply_geometry()
            if self.isVisible():
                self.menu.reveal = 1.0

    def go_back(self) -> None:
        if len(self._stack) <= 1:
            self.dismiss()
            return
        self._stack.pop()
        config = self._config_provider()
        previous = config.menu_by_id(self._stack[-1])
        if previous is None:
            self.dismiss()
            return
        self.menu.set_breadcrumb([config.menu_by_id(item) or previous for item in self._stack])
        self.menu.transition_to(previous, direction=-1, on_swapped=lambda: (self._apply_geometry(), self._refresh_radial_notes()))

    def dismiss(self) -> None:
        if not self.isVisible():
            return
        self._auto_hide.stop()
        settings = self._config_provider().settings
        if self._fade_out_callback is not None:
            return

        def finish() -> None:
            self._fade_out_callback = None
            self.hide()
            self.add_bar.hide()
            self.music_bar.hide()
            self.trash_bin.hide()
            self.setWindowOpacity(1.0)
            self.menu.reveal = 0.0
            self.radial_notes.hide()
            self._stack = []
            self.closed.emit()

        if settings.reduced_motion:
            finish()
            return
        self._fade_out_callback = finish
        self._fade_window(0.0, seconds=0.10)
        self.menu.animate_out(callback=finish)
        # Safety net: the overlay must never be impossible to dismiss, even if
        # an animation callback is missed while the machine is busy.
        QTimer.singleShot(max(400, settings.animation_duration + 400), self._ensure_hidden)

    def _ensure_hidden(self) -> None:
        if self.isVisible() and self._fade_out_callback is not None:
            self._fade_out_callback()

    def force_hide(self) -> None:
        """Last-resort hide used by the tray menu and on shutdown."""
        self._auto_hide.stop()
        self._fade_out_callback = None
        if self._fade is not None:
            self._fade.stop()
        self.menu.reveal = 0.0
        self.radial_notes.hide()
        self.add_bar.hide()
        self.music_bar.hide()
        self.trash_bin.hide()
        self.hide()
        self.setWindowOpacity(1.0)
        self._stack = []
        self.closed.emit()

    # -------------------------------------------------------------------- helpers
    def _fade_window(self, target: float, *, seconds: float) -> None:
        if self._fade is not None:
            self._fade.stop()
            self._fade.deleteLater()
            self._fade = None
        animation = QPropertyAnimation(self, b"windowOpacity", self)
        animation.setDuration(max(1, int(seconds * 1000)))
        animation.setStartValue(self.windowOpacity())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade = animation
        animation.start()

    def _raise_and_focus(self) -> None:
        self.raise_()
        try:
            handle = int(self.winId())
        except (TypeError, ValueError):
            handle = 0
        set_foreground_hwnd(handle)
        self.activateWindow()
        self.menu.setFocus(Qt.FocusReason.OtherFocusReason)

    def select_action(self, action_id: str) -> None:
        self.menu.select_action(action_id)

    def selected_action(self) -> Action | None:
        return self.menu.selected_action()

    def open_submenu(self, menu_id: str) -> bool:
        """Service used by the submenu action type."""
        return self.open_menu(menu_id)

    # --------------------------------------------------------------------- events
    def paintEvent(self, event) -> None:  # noqa: N802
        config = self._config_provider()
        theme = self._theme_provider()
        opacity = float(config.settings.overlay_opacity)
        if opacity <= 0.001:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        tint = QColor(theme.overlay_tint)
        strength = float(config.settings.screen_shader_strength)
        shader = config.settings.screen_shader
        if not config.settings.screen_shader_enabled or shader == "flat":
            tint.setAlphaF(min(1.0, opacity * (0.72 * strength)))
            painter.fillRect(self.rect(), tint)
        elif shader == "soft":
            tint.setAlphaF(min(1.0, opacity * (0.48 * strength)))
            painter.fillRect(self.rect(), tint)
        elif shader == "aurora":
            self._paint_aurora(painter, tint, opacity, strength)
        elif shader == "particles":
            self._paint_particles(painter, tint, opacity, strength)
        elif shader == "ripple":
            self._paint_ripples(painter, tint, opacity, strength)
        else:
            centre = QPointF(self.menu.geometry().center())
            radius = max(self.width(), self.height()) * 0.62
            gradient = QRadialGradient(centre, radius)
            inner = QColor(tint); inner.setAlphaF(min(1.0, opacity * (0.42 * strength)))
            outer = QColor(tint); outer.setAlphaF(min(1.0, opacity * (0.82 * strength)))
            gradient.setColorAt(0.0, inner)
            gradient.setColorAt(0.55, QColor(tint.red(), tint.green(), tint.blue(), int(255 * min(1.0, opacity * 0.62))))
            gradient.setColorAt(1.0, outer)
            painter.fillRect(self.rect(), gradient)
        painter.end()

    def _advance_shader(self) -> None:
        if not self.isVisible():
            return
        settings = self._config_provider().settings
        if settings.screen_shader_enabled and not settings.reduced_motion:
            self._shader_phase = (self._shader_phase + 0.035) % (math.pi * 200.0)
            self.update()

    def _shader_mouse(self) -> QPointF:
        point = self.mapFromGlobal(QCursor.pos())
        return QPointF(max(0, min(self.width(), point.x())), max(0, min(self.height(), point.y())))

    def _paint_aurora(self, painter: QPainter, tint: QColor, opacity: float, strength: float) -> None:
        """Slow moving bands with a small mouse-driven offset."""
        mouse = self._shader_mouse()
        phase = self._shader_phase
        x_shift = math.sin(phase * 0.7) * self.width() * 0.18 + (mouse.x() / max(1, self.width()) - 0.5) * self.width() * 0.12
        gradient = QLinearGradient(-self.width() * 0.2 + x_shift, 0, self.width() * 0.9 + x_shift, self.height())
        base = QColor(tint); base.setAlphaF(min(1.0, opacity * 0.48 * strength)); painter.fillRect(self.rect(), base)
        first = QColor("#516CFF"); first.setAlphaF(min(1.0, opacity * 0.22 * strength))
        second = QColor("#31C7B1"); second.setAlphaF(min(1.0, opacity * 0.18 * strength))
        third = QColor("#A77BFF"); third.setAlphaF(min(1.0, opacity * 0.15 * strength))
        gradient.setColorAt(0.05, first); gradient.setColorAt(0.42, second); gradient.setColorAt(0.78, third); gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(self.rect(), gradient)

    def _paint_particles(self, painter: QPainter, tint: QColor, opacity: float, strength: float) -> None:
        """Floating dots drift away from the current pointer position."""
        base = QColor(tint); base.setAlphaF(min(1.0, opacity * 0.42 * strength)); painter.fillRect(self.rect(), base)
        mouse = self._shader_mouse(); phase = self._shader_phase
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(34):
            seed = index * 1.618
            x = (math.sin(seed * 2.31) * 0.5 + 0.5) * self.width()
            y = (math.cos(seed * 1.73) * 0.5 + 0.5) * self.height()
            x += math.sin(phase * (0.45 + index % 3 * 0.08) + seed) * 28
            y += math.cos(phase * (0.35 + index % 4 * 0.06) + seed) * 22
            dx, dy = x - mouse.x(), y - mouse.y(); distance = max(30.0, math.hypot(dx, dy))
            influence = max(0.0, 1.0 - distance / 260.0)
            x += (dx / distance) * influence * 42
            y += (dy / distance) * influence * 42
            colour = QColor("#AEBBFF" if index % 2 else "#6DE0CF")
            colour.setAlphaF(min(1.0, opacity * strength * (0.18 + influence * 0.34)))
            radius = 1.5 + (index % 3) * 0.8
            painter.setBrush(colour); painter.drawEllipse(QPointF(x, y), radius, radius)

    def _paint_ripples(self, painter: QPainter, tint: QColor, opacity: float, strength: float) -> None:
        """Concentric rings follow the pointer without blocking interaction."""
        base = QColor(tint); base.setAlphaF(min(1.0, opacity * 0.38 * strength)); painter.fillRect(self.rect(), base)
        mouse = self._shader_mouse(); phase = self._shader_phase
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index in range(5):
            radius = 70 + ((phase * 85 + index * 85) % 430)
            colour = QColor("#8298FF"); colour.setAlphaF(min(1.0, opacity * strength * max(0.0, 0.16 - index * 0.022)))
            painter.setPen(colour); painter.drawEllipse(mouse, radius, radius)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        settings = self._config_provider().settings
        if not settings.dismiss_on_click_outside:
            event.accept()
            return
        point = event.position()
        if any(card.isVisible() and card.geometry().contains(point.toPoint()) for card in self.radial_notes.cards):
            event.accept()
            return
        if not self.menu.geometry().contains(point.toPoint()):
            self.dismiss()
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_N, Qt.Key.Key_T) and not event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.add_radial_item("note" if event.key() == Qt.Key.Key_N else "task")
            event.accept()
            return
        if self.menu.handle_key(event.key(), event.text()):
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        # The application-wide filter exists only while the overlay is up, so it
        # can never outlive this widget and swallow keys after it is gone.
        app = QGuiApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        app = QGuiApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        app = QGuiApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        # Escape must always dismiss the overlay, whichever ORBIT widget has focus.
        if event.type() == QEvent.Type.KeyPress and self.isVisible():
            key_event = event
            app = QApplication.instance()
            if app is not None and app.activeModalWidget() is not None:
                return False
            if key_event.key() in (Qt.Key.Key_N, Qt.Key.Key_T) and not key_event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.add_radial_item("note" if key_event.key() == Qt.Key.Key_N else "task")
                return True
            if key_event.key() == Qt.Key.Key_Escape:
                modal = app.activeModalWidget() if app is not None else None
                if modal is None and self._fade_out_callback is None:
                    self.dismiss()
                    return True
        return super().eventFilter(watched, event)
