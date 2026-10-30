"""Small reusable widgets for ORBIT Studio.

Everything the studio needs is built from these primitives, so the pages stay
readable and the look stays consistent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.keycodes import MODIFIER_ALIASES, format_hotkey, parse_hotkey
from ui import icons
from ui.theme_manager import Theme, ui_font

# ------------------------------------------------------------------- primitives
class Card(QFrame):
    """A surface with a border, used to group settings."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 10) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 14, 16, 14)
        self._layout.setSpacing(spacing)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def add_row(self, row: QWidget) -> QWidget:
        self._layout.addWidget(row)
        return row


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("SectionTitle")
    return label


def muted_label(text: str, *, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(wrap)
    return label


def badge(text: str, kind: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName({"accent": "BadgeAccent", "danger": "BadgeDanger"}.get(kind, "Badge"))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


class Row(QWidget):
    """Label on the left, control on the right, optional help text underneath."""

    def __init__(
        self,
        label: str,
        control: QWidget,
        *,
        help_text: str = "",
        label_width: int = 168,
        control_stretch: int = 1,
    ) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        line = QHBoxLayout()
        line.setSpacing(12)
        self.label = QLabel(label)
        self.label.setFixedWidth(label_width)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        line.addWidget(self.label)
        line.addWidget(control, control_stretch)
        outer.addLayout(line)
        if help_text:
            hint = muted_label(help_text)
            hint.setContentsMargins(label_width + 12, 0, 0, 0)
            outer.addWidget(hint)


class ColorButton(QPushButton):
    """A swatch button that opens the colour picker."""

    colourChanged = Signal(str)

    def __init__(self, colour: str, *, title: str = "Choose a colour") -> None:
        super().__init__()
        self._title = title
        self._colour = QColor(colour if QColor(colour).isValid() else "#6C8CFF")
        self.setFixedWidth(96)
        self.clicked.connect(self._pick)
        self._refresh()

    def colour(self) -> str:
        return self._colour.name()

    def set_colour(self, colour: str) -> None:
        candidate = QColor(colour)
        if candidate.isValid():
            self._colour = candidate
            self._refresh()

    def _refresh(self) -> None:
        self.setText(self._colour.name().upper())
        text_colour = "#FFFFFF" if self._colour.lightness() < 150 else "#111318"
        self.setStyleSheet(
            f"background: {self._colour.name()}; color: {text_colour}; border: 1px solid "
            f"rgba(255,255,255,0.18); border-radius: 8px; padding: 5px 8px; font-size: 11.5px;"
        )

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(self._colour, self, self._title)
        if chosen.isValid():
            self._colour = chosen
            self._refresh()
            self.colourChanged.emit(chosen.name())


class LabeledSlider(QWidget):
    """Slider with a live numeric readout."""

    valueChanged = Signal(int)

    def __init__(
        self,
        value: int,
        *,
        minimum: int,
        maximum: int,
        suffix: str = "",
        step: int = 1,
    ) -> None:
        super().__init__()
        self._suffix = suffix
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(minimum)
        self.slider.setMaximum(maximum)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(step * 5)
        self.slider.setValue(int(value))
        self.slider.valueChanged.connect(self._on_change)
        layout.addWidget(self.slider, 1)
        self.readout = QLabel()
        self.readout.setFixedWidth(58)
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.readout)
        self._update_readout(self.slider.value())

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(value))
        self.slider.blockSignals(False)
        self._update_readout(self.slider.value())

    def _on_change(self, value: int) -> None:
        self._update_readout(value)
        self.valueChanged.emit(value)

    def _update_readout(self, value: int) -> None:
        self.readout.setText(f"{value}{self._suffix}")


class FloatSlider(QWidget):
    """Slider for fractional values (kept separate to avoid rounding surprises)."""

    valueChanged = Signal(float)

    def __init__(self, value: float, *, minimum: float, maximum: float, step: float = 0.01) -> None:
        super().__init__()
        self._scale = int(round(1 / step))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(int(minimum * self._scale))
        self.slider.setMaximum(int(maximum * self._scale))
        self.slider.setValue(int(round(value * self._scale)))
        self.slider.valueChanged.connect(self._on_change)
        layout.addWidget(self.slider, 1)
        self.readout = QLabel()
        self.readout.setFixedWidth(52)
        self.readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.readout)
        self._show(self.slider.value())

    def value(self) -> float:
        return self.slider.value() / self._scale

    def set_value(self, value: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(value * self._scale)))
        self.slider.blockSignals(False)
        self._show(self.slider.value())

    def _on_change(self, raw: int) -> None:
        self._show(raw)
        self.valueChanged.emit(self.value())

    def _show(self, raw: int) -> None:
        self.readout.setText(f"{raw / self._scale:.2f}")


class PathPicker(QWidget):
    """Line edit plus a Browse button (file, folder or either)."""

    changed = Signal(str)

    def __init__(
        self,
        value: str = "",
        *,
        mode: str = "any",
        file_filter: str = "All files (*.*)",
        placeholder: str = "",
    ) -> None:
        super().__init__()
        self._mode = mode
        self._filter = file_filter
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.edit = QLineEdit(value)
        self.edit.textChanged.connect(self.changed.emit)
        if placeholder:
            self.edit.setPlaceholderText(placeholder)
        layout.addWidget(self.edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        layout.addWidget(browse, 0)

    def value(self) -> str:
        return self.edit.text()

    def set_value(self, value: str) -> None:
        self.edit.setText(value or "")

    def _browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = self.edit.text() or str(Path.home())
        if self._mode == "file":
            chosen, _ = QFileDialog.getOpenFileName(self, "Choose a file", start, self._filter)
        elif self._mode == "dir":
            chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        elif self._mode == "program":
            chosen, _ = QFileDialog.getOpenFileName(
                self, "Choose a program", start, "Programs (*.exe *.bat *.cmd *.com *.ps1 *.py);;All files (*.*)"
            )
        else:
            dialog = QFileDialog(self, "Choose a file or folder", start)
            dialog.setFileMode(QFileDialog.FileMode.AnyFile)
            if dialog.exec():
                chosen = dialog.selectedFiles()[0]
            else:
                chosen = ""
        if chosen:
            self.edit.setText(str(Path(chosen)))


class ArgsEditor(QPlainTextEdit):
    """One argument per line: unambiguous and space-safe."""

    changed = Signal(list)

    def __init__(self, args: Sequence[str] = ()) -> None:
        super().__init__(("\n".join(args)) if args else "")
        self.setPlaceholderText("One argument per line, for example:\n--profile\nC:\\My Folder\\notes.txt")
        self.setFixedHeight(74)
        self.setFont(ui_font(12.5, mono=True))
        self.textChanged.connect(lambda: self.changed.emit(self.value()))

    def value(self) -> list[str]:
        return [line for line in self.toPlainText().splitlines() if line.strip()]

    def set_value(self, args: Iterable[str]) -> None:
        items = list(args)
        self.blockSignals(True)
        self.setPlainText("\n".join(items))
        self.blockSignals(False)


class HotkeyEdit(QLineEdit):
    """Records a key combination by pressing it."""

    captured = Signal(str)

    def __init__(self, value: str = "") -> None:
        super().__init__(value)
        self.setPlaceholderText("Click here and press a key combination")
        self.setReadOnly(True)
        self.setToolTip("Focus this field and press the combination you want to use.")
        self._recording = False

    def set_value(self, value: str) -> None:
        self.setText(value or "")

    def value(self) -> str:
        return self.text().strip()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
            Qt.Key.Key_unknown,
        ) or key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                self.setText("")
                self.captured.emit("")
                return
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        parts: list[str] = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("win")
        sequence = QKeySequence(key)
        token = sequence.toString(QKeySequence.SequenceFormat.PortableText).strip()
        if not token:
            return
        token = token.lower()
        if token in MODIFIER_ALIASES:
            return
        parts.append(token)
        combination = "+".join(parts)
        try:
            parse_hotkey(combination)
        except Exception:
            return
        self.setText(combination)
        self.captured.emit(combination)


class ScrollPage(QScrollArea):
    """A vertically scrolling page with consistent margins."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(24, 20, 24, 24)
        self._layout.setSpacing(14)
        self.setWidget(self._content)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def finish(self) -> None:
        self._layout.addStretch(1)


class IconPickerDialog(QDialog):
    """Grid of library icons with search, plus a custom image option."""

    def __init__(self, current: str, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.theme = theme
        self.setWindowTitle("Choose an icon")
        self.setMinimumSize(QSize(560, 470))
        self._choice = current

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search icons…")
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMovement(QListWidget.Movement.Static)
        self.list.setIconSize(QSize(26, 26))
        self.list.setGridSize(QSize(96, 62))
        self.list.setSpacing(3)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        # Selecting a glyph in the grid *is* the choice, so the value is kept
        # in one place and an explicit "use an image file…" decision can never
        # be overwritten by a highlighted row.
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list, 1)

        footer = QHBoxLayout()
        custom = QPushButton("Use an image file…")
        custom.clicked.connect(self._pick_image)
        footer.addWidget(custom)
        literal = QPushButton("Use text / symbol…")
        literal.clicked.connect(self._use_text)
        footer.addWidget(literal)
        footer.addStretch(1)
        clear = QPushButton("Clear icon")
        clear.setObjectName("Ghost")
        clear.clicked.connect(self._clear)
        footer.addWidget(clear)
        layout.addLayout(footer)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate()

    def _populate(self, filter_text: str = "") -> None:
        self.list.clear()
        colour = QColor(self.theme.text)
        needle = filter_text.strip().lower()
        for name in icons.available_icons():
            if needle and needle not in name:
                continue
            item = QListWidgetItem(QIcon_placeholder(self._icon(name, colour), name), name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.list.addItem(item)

    def _icon(self, name: str, colour: QColor):
        from PySide6.QtGui import QIcon

        kind, _payload = icons.resolve_icon(name)
        return QIcon(icons.icon_pixmap(name, 26, colour, circular=(kind == "image")))

    def _filter(self, text: str) -> None:
        self._populate(text)

    def _pick_image(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        chosen, _ = QFileDialog.getOpenFileName(
            self, "Choose an image", str(Path.home()), icons.image_file_filter(),
        )
        if chosen:
            # The file wins over whatever row happened to be highlighted.
            self._set_choice(f"file:{chosen}")
            self.accept()

    def _use_text(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(self, "Icon text", "Short text or symbol:")
        if ok and text.strip():
            self._set_choice(text.strip())
            self.accept()

    def _clear(self) -> None:
        self._set_choice("")
        self.accept()

    def _set_choice(self, value: str) -> None:
        """Commit an explicit choice, dropping any grid highlight."""
        self.list.clearSelection()
        self._choice = value

    def selected_icon(self) -> str:
        return self._choice

    def _on_selection_changed(self) -> None:
        items = self.list.selectedItems()
        if items:
            self._choice = str(items[0].data(Qt.ItemDataRole.UserRole))

    def accept(self) -> None:  # noqa: D102
        super().accept()


def QIcon_placeholder(pixmap, text: str):  # noqa: N802 - helper name kept explicit
    from PySide6.QtGui import QIcon

    return QIcon(pixmap)


class IconButton(QPushButton):
    """Shows the current icon and opens the picker."""

    iconChanged = Signal(str)

    def __init__(self, value: str, theme: Theme) -> None:
        super().__init__()
        self.theme = theme
        self._value = value or ""
        self.setFixedSize(QSize(62, 40))
        self.setIconSize(QSize(22, 22))
        self.clicked.connect(self._pick)
        self._refresh()

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value or ""
        self._refresh()

    def _refresh(self) -> None:
        kind, _payload = icons.resolve_icon(self._value or None)
        pixmap = icons.icon_pixmap(
            self._value or None, 22, QColor(self.theme.text), circular=(kind == "image")
        )
        self.setIcon(QIcon_placeholder(pixmap, ""))
        self.setText("" if self._value else "…")

    def _pick(self) -> None:
        dialog = IconPickerDialog(self._value, self.theme, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_value(dialog.selected_icon())
            self.iconChanged.emit(self._value)


def combo(
    options: Sequence[tuple[str, str]],
    current: str,
    *,
    on_change: Callable[[str], None] | None = None,
) -> QComboBox:
    """Build a combo box from ``(value, label)`` pairs."""
    box = QComboBox()
    for value, label in options:
        box.addItem(label, value)
    index = box.findData(current)
    box.setCurrentIndex(index if index >= 0 else 0)
    if on_change is not None:
        def handler(_index: int) -> None:
            value = box.currentData()
            # Repopulating a combo emits -1; never forward that as a value.
            if value is not None:
                on_change(value)

        box.currentIndexChanged.connect(handler)
    return box


def toolbar_button(text: str, *, tooltip: str = "", icon: str = "", theme: Theme | None = None,
                   primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    if icon and theme is not None:
        button.setIcon(QIcon_placeholder(icons.icon_pixmap(icon, 18, QColor(theme.text)), icon))
        button.setIconSize(QSize(17, 17))
    if primary:
        button.setObjectName("Primary")
    if tooltip:
        button.setToolTip(tooltip)
    return button


def icon_button(icon_name: str, theme: Theme, *, tooltip: str = "", colour: str = "") -> QToolButton:
    button = QToolButton()
    button.setIcon(QIcon_placeholder(icons.icon_pixmap(icon_name, 18, QColor(colour or theme.text)), icon_name))
    button.setIconSize(QSize(18, 18))
    button.setAutoRaise(True)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def grid_row(label_text: str, widget: QWidget, *, label_width: int = 150) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    label = QLabel(label_text)
    label.setFixedWidth(label_width)
    layout.addWidget(label)
    layout.addWidget(widget, 1)
    return row


def small_spin(value: int, *, minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(int(value))
    if suffix:
        spin.setSuffix(suffix)
    spin.setFixedWidth(110)
    return spin


def make_grid(pairs: Iterable[tuple[str, str]]) -> QGridLayout:
    grid = QGridLayout()
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(8)
    for row, (name, value) in enumerate(pairs):
        grid.addWidget(QLabel(name), row, 0)
        right = QLabel(value)
        right.setObjectName("Muted")
        right.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(right, row, 1)
    grid.setColumnStretch(1, 1)
    return grid


def bold(text: str, size: int = 13) -> QLabel:
    label = QLabel(text)
    font = ui_font(size, QFont.Weight.DemiBold)
    label.setFont(font)
    return label


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(255,255,255,0.08);")
    return line


class CheckRow(QCheckBox):
    """Checkbox with a description underneath."""

    def __init__(self, text: str, checked: bool, *, help_text: str = "") -> None:
        super().__init__(text)
        self.setChecked(bool(checked))
        if help_text:
            self.setToolTip(help_text)


class Page(QWidget):
    """Base class for a studio page.

    Pages edit the studio's working configuration copy and announce changes;
    they never write to disk themselves.
    """

    changed = Signal()
    title = ""
    description = ""

    def __init__(self, studio, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.studio = studio

    @property
    def controller(self):
        return self.studio.controller

    @property
    def working(self):
        return self.studio.working

    @property
    def executor(self):
        """The action executor, or ``None`` when previewing without one."""
        runner = getattr(self.controller, "runner", None)
        return getattr(runner, "executor", None)

    def notify_changed(self) -> None:
        self.changed.emit()

    def load(self) -> None:  # pragma: no cover - overridden by pages
        """Refresh from the working configuration."""

    def header(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(2)
        title = QLabel(self.title)
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        if self.description:
            hint = QLabel(self.description)
            hint.setObjectName("PageDescription")
            hint.setWordWrap(True)
            layout.addWidget(hint)
        return holder
