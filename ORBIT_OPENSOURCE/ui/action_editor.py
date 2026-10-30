"""Action editing interface.

The editor is generated from the action registry: every field it shows comes
from an :class:`core.action_registry.ActionTypeSpec`, so a new action type needs
no changes here.  It validates continuously, can test an action without leaving
the dialog, and only writes back to the configuration when the user accepts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.action_registry import Field, ValidationContext
from core.models import Action, Config
from ui import icons
from ui.theme_manager import Theme, ui_font
from ui.widgets import (
    ArgsEditor,
    Card,
    ColorButton,
    HotkeyEdit,
    IconButton,
    PathPicker,
    Row,
    ScrollPage,
    badge,
    combo,
    muted_label,
    section_label,
)

CLOSE_MENU_CHOICES = (
    ("global", "Use the global setting"),
    ("close", "Always close the menu"),
    ("keep", "Keep the menu open"),
)


# ---------------------------------------------------------------- type chooser
class ActionTypePicker(QDialog):
    """Choose which kind of action to create."""

    def __init__(self, registry, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add an action")
        self.setMinimumSize(460, 470)
        self.theme = theme
        self.registry = registry
        self._chosen = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        search = QLineEdit()
        search.setPlaceholderText("Search action types…")
        search.textChanged.connect(self._populate)
        layout.addWidget(search)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        layout.addWidget(self.list, 1)

        self.hint = muted_label("Pick a starting point. You can change the type later.")
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._search = search
        self._populate("")

    def _populate(self, needle: str) -> None:
        self.list.clear()
        needle = needle.strip().lower()
        for group, specs in self.registry.grouped().items():
            matching = [
                spec
                for spec in specs
                if not needle
                or needle in spec.label.lower()
                or needle in spec.description.lower()
                or needle in group.lower()
            ]
            if not matching:
                continue
            header = QListWidgetItem(group)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            font = ui_font(11, mono=False)
            font.setBold(True)
            header.setFont(font)
            self.list.addItem(header)
            for spec in matching:
                item = QListWidgetItem(f"    {spec.label} — {spec.description}")
                item.setData(Qt.ItemDataRole.UserRole, spec.key)
                item.setToolTip(spec.description)
                self.list.addItem(item)

    def _accept(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        key = items[0].data(Qt.ItemDataRole.UserRole)
        if key:
            self._chosen = str(key)
            self.accept()

    def chosen_type(self) -> str:
        return self._chosen


# --------------------------------------------------------------- sequence edit
class SequenceEditor(QWidget):
    """Ordered list of steps: inner actions and delays."""

    changed = Signal()

    def __init__(self, steps: list[dict], editor_factory: Callable[[Action], Action | None]) -> None:
        super().__init__()
        self._steps = [dict(step) for step in (steps or [])]
        self._editor_factory = editor_factory

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.list = QListWidget()
        self.list.setFixedHeight(150)
        self.list.itemDoubleClicked.connect(lambda _item: self._edit())
        layout.addWidget(self.list)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for text, handler, tooltip in (
            ("Add action…", self._add_action, "Append an action to the sequence"),
            ("Add delay…", self._add_delay, "Wait before the next step"),
            ("Edit…", self._edit, "Edit the selected step"),
            ("Remove", self._remove, "Remove the selected step"),
            ("Up", self._move_up, "Move the step earlier"),
            ("Down", self._move_down, "Move the step later"),
        ):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self._refresh()

    # -------------------------------------------------------------------- data
    def steps(self) -> list[dict]:
        return [dict(step) for step in self._steps]

    def _refresh(self) -> None:
        current = self.list.currentRow()
        self.list.clear()
        for index, step in enumerate(self._steps, start=1):
            raw = step.get("action")
            if raw:
                action = Action.from_dict(raw)
                label = f"{index}. {action.display_name()}  ·  {action.type}"
                if step.get("ignore_failure"):
                    label += "  (continue on failure)"
            else:
                label = f"{index}. wait {int(step.get('delay_ms', 0))} ms"
            item = QListWidgetItem(label)
            self.list.addItem(item)
        if 0 <= current < self.list.count():
            self.list.setCurrentRow(current)
        self.changed.emit()

    def _selected(self) -> int:
        return self.list.currentRow()

    # ----------------------------------------------------------------- actions
    def _add_action(self) -> None:
        blank = Action(name="Step", type="url", icon="globe")
        result = self._editor_factory(blank)
        if result is None:
            return
        self._steps.append({"action": result.to_dict()})
        self._refresh()
        self.list.setCurrentRow(len(self._steps) - 1)

    def _add_delay(self) -> None:
        value, ok = QInputDialog.getInt(self, "Add a delay", "Wait time (milliseconds):", 400, 0, 60000, 50)
        if ok:
            self._steps.append({"delay_ms": int(value)})
            self._refresh()
            self.list.setCurrentRow(len(self._steps) - 1)

    def _edit(self) -> None:
        row = self._selected()
        if not (0 <= row < len(self._steps)):
            return
        step = self._steps[row]
        if not step.get("action"):
            value, ok = QInputDialog.getInt(
                self, "Edit the delay", "Wait time (milliseconds):",
                int(step.get("delay_ms", 0)), 0, 60000, 50,
            )
            if ok:
                step["delay_ms"] = int(value)
                self._refresh()
            return
        action = Action.from_dict(step["action"])
        result = self._editor_factory(action)
        if result is None:
            return
        step["action"] = result.to_dict()
        self._refresh()

    def _remove(self) -> None:
        row = self._selected()
        if 0 <= row < len(self._steps):
            del self._steps[row]
            self._refresh()

    def _move_up(self) -> None:
        row = self._selected()
        if row > 0:
            self._steps[row - 1], self._steps[row] = self._steps[row], self._steps[row - 1]
            self._refresh()
            self.list.setCurrentRow(row - 1)

    def _move_down(self) -> None:
        row = self._selected()
        if 0 <= row < len(self._steps) - 1:
            self._steps[row + 1], self._steps[row] = self._steps[row], self._steps[row + 1]
            self._refresh()
            self.list.setCurrentRow(row + 1)


# ---------------------------------------------------------------- field binding
class FieldBinding:
    """Connects one registry field to a concrete widget."""

    def __init__(self, field: Field, widget: QWidget, signal_name: str) -> None:
        self.field = field
        self.widget = widget
        self.signal_name = signal_name

    def connect(self, slot) -> None:
        signal = getattr(self.widget, self.signal_name, None)
        if signal is not None:
            signal.connect(lambda *_: slot())

    def read(self) -> Any:
        kind = self.field.kind
        widget = self.widget
        if kind == "bool":
            return bool(widget.isChecked())
        if kind == "int":
            return int(widget.value())
        if kind == "float":
            return float(widget.value())
        if kind == "args":
            return widget.value()
        if kind == "action_list":
            return widget.steps()
        if kind == "color":
            return widget.colour()
        if isinstance(widget, (QLineEdit,)):
            return widget.text()
        if isinstance(widget, PathPicker):
            return widget.value()
        if isinstance(widget, HotkeyEdit):
            return widget.value()
        if isinstance(widget, IconButton):
            return widget.value()
        if isinstance(widget, QComboBox):
            return widget.currentData()
        return ""

    def write(self, value: Any) -> None:
        widget = self.widget
        blocked = widget.blockSignals(True)
        try:
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, (QSpinBox,)):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, PathPicker):
                widget.set_value(str(value or ""))
            elif isinstance(widget, ArgsEditor):
                widget.set_value(value or [])
            elif isinstance(widget, HotkeyEdit):
                widget.set_value(str(value or ""))
            elif isinstance(widget, IconButton):
                widget.set_value(str(value or ""))
            elif isinstance(widget, ColorButton):
                widget.set_colour(str(value or ""))
            elif isinstance(widget, QLineEdit):
                widget.setText("" if value is None else str(value))
        finally:
            widget.blockSignals(blocked)


# ----------------------------------------------------------------- main dialog
class ActionEditorDialog(QDialog):
    """Create or edit one action."""

    def __init__(
        self,
        controller,
        action: Action | None,
        config: Config,
        *,
        containing_menu_id: str = "",
        compact: bool = False,
        creating: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.registry = controller.registry
        self.config = config
        self.containing_menu_id = containing_menu_id
        self.compact = compact
        self.creating = creating or action is None
        self.theme: Theme = controller.theme
        self._original = action
        self._draft = (action or Action()).clone(new_identifier=False) if action else Action()
        self._bindings: list[FieldBinding] = []
        self._test_run_id: str | None = None

        self.setWindowTitle("Add an action" if self.creating else "Edit action")
        self.setModal(True)
        self.resize(700 if not compact else 600, 720 if not compact else 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.page = ScrollPage(self)
        root.addWidget(self.page, 1)
        body = self.page.body()

        self._build_header(body)
        self._fields_card = Card()
        body.addWidget(self._fields_card)
        if not compact:
            self._build_behaviour(body)
            self._build_visibility(body)
        self._build_validation(body)
        self.page.finish()

        root.addWidget(self._build_footer())
        self._refresh_fields()
        self._refresh_validation()
        self._update_summary()

    # ------------------------------------------------------------------ building
    def _build_header(self, body: QVBoxLayout) -> None:
        card = Card()
        header = QHBoxLayout()
        header.setSpacing(10)
        self._icon_button = IconButton(self._effective_icon(), self.theme)
        self._icon_button.setToolTip(
            "Click to choose an icon from the library, or to use any image file as the button icon."
        )
        self._icon_button.iconChanged.connect(self._on_icon_changed)
        header.addWidget(self._icon_button, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(6)
        self._name_edit = QLineEdit(self._draft.name)
        self._name_edit.setPlaceholderText("Button label")
        font = ui_font(14, mono=False)
        self._name_edit.setFont(font)
        self._name_edit.textChanged.connect(self._on_name_changed)
        text_column.addWidget(self._name_edit)

        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        self._type_combo = QComboBox()
        for group, specs in self.registry.grouped().items():
            for spec in specs:
                self._type_combo.addItem(f"{spec.label}", spec.key)
                self._type_combo.setItemData(
                    self._type_combo.count() - 1, f"{group} — {spec.description}", Qt.ItemDataRole.ToolTipRole
                )
        index = self._type_combo.findData(self._draft.type)
        self._type_combo.setCurrentIndex(index if index >= 0 else 0)
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self._type_combo, 2)
        self._accent_button = ColorButton(self._draft.accent or self.theme.accent)
        self._accent_button.setToolTip("Optional accent colour for this button")
        self._accent_button.colourChanged.connect(self._on_accent_changed)
        type_row.addWidget(self._accent_button, 0)
        text_column.addLayout(type_row)

        self._description_edit = QLineEdit(self._draft.description)
        self._description_edit.setPlaceholderText("Description shown when the mouse is over the button (optional)")
        self._description_edit.textChanged.connect(self._on_description_changed)
        text_column.addWidget(self._description_edit)

        # Custom artwork: the wheel masks this into the circle, so any format
        # Qt can decode works (PNG, SVG, JPG, WEBP, ICO, TIFF, GIF…).
        image_row = QHBoxLayout()
        image_row.setSpacing(8)
        self._image_picker = PathPicker(
            self._draft.image,
            mode="file",
            file_filter=icons.image_file_filter(),
            placeholder="Optional picture for the circle — any image format",
        )
        self._image_picker.setToolTip(
            "A picture shown inside this circle instead of the icon. "
            "It is cropped to a circle and follows the button."
        )
        self._image_picker.changed.connect(self._on_image_changed)
        image_row.addWidget(self._image_picker, 1)
        clear_image = QPushButton("Clear")
        clear_image.setObjectName("Ghost")
        clear_image.setToolTip("Remove the picture and go back to the icon")
        clear_image.clicked.connect(self._clear_image)
        image_row.addWidget(clear_image, 0)
        text_column.addLayout(image_row)

        header.addLayout(text_column, 1)
        card.body().addLayout(header)

        self._summary = muted_label("")
        card.body().addWidget(self._summary)
        body.addWidget(card)

    def _build_behaviour(self, body: QVBoxLayout) -> None:
        card = Card()
        card.add(section_label("Behaviour"))
        close_current = "global"
        if self._draft.close_menu is True:
            close_current = "close"
        elif self._draft.close_menu is False:
            close_current = "keep"
        self._close_combo = combo(CLOSE_MENU_CHOICES, close_current, on_change=self._on_close_changed)
        card.add_row(Row("After running", self._close_combo, help_text="Nested menus always stay open."))

        self._confirm_check = QCheckBox("Ask me to confirm before running")
        self._confirm_check.setChecked(self._draft.confirm)
        self._confirm_check.toggled.connect(self._on_confirm_changed)
        card.add(self._confirm_check)
        self._risk_badge = badge(self.registry.risk_label(self._draft), "accent")
        risk_row = QHBoxLayout()
        risk_row.addWidget(QLabel("Impact"))
        risk_row.addWidget(self._risk_badge)
        risk_row.addStretch(1)
        card.body().addLayout(risk_row)

        self._enabled_check = QCheckBox("Enabled (shown in the menu)")
        self._enabled_check.setChecked(self._draft.enabled)
        self._enabled_check.toggled.connect(self._on_enabled_changed)
        card.add(self._enabled_check)

        self._shortcut_edit = HotkeyEdit(self._draft.shortcut)
        self._shortcut_edit.captured.connect(self._on_shortcut_changed)
        card.add_row(
            Row(
                "Shortcut",
                self._shortcut_edit,
                help_text="Press this combination while the menu is open to run this button directly.",
            )
        )
        body.addWidget(card)

    def _build_visibility(self, body: QVBoxLayout) -> None:
        card = Card()
        card.add(section_label("Visibility"))
        rules = self._draft.visible_when or {}
        self._always_check = QCheckBox("Always visible")
        self._always_check.setChecked(not rules)
        self._always_check.toggled.connect(self._on_visibility_changed)
        card.add(self._always_check)

        self._file_picker = PathPicker(str(rules.get("file_exists", "")), mode="any")
        self._file_picker.changed.connect(lambda _value: self._on_visibility_changed())
        card.add_row(Row("Only when this path exists", self._file_picker, label_width=200))

        self._app_edit = QLineEdit(str(rules.get("app_running", "")))
        self._app_edit.setPlaceholderText("for example spotify.exe")
        self._app_edit.textChanged.connect(lambda _text: self._on_visibility_changed())
        card.add_row(Row("Only when this app is running", self._app_edit, label_width=200))
        body.addWidget(card)

    def _build_validation(self, body: QVBoxLayout) -> None:
        self._validation_card = Card()
        self._validation_card.add(section_label("Checks"))
        self._validation_label = QLabel("")
        self._validation_label.setWordWrap(True)
        self._validation_card.add(self._validation_label)
        body.addWidget(self._validation_card)

    def _build_footer(self) -> QWidget:
        holder = QFrame()
        holder.setStyleSheet(f"background: {self.theme.surface}; border-top: 1px solid {self.theme.border};")
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        self._test_button = QPushButton("Test action")
        self._test_button.setToolTip("Run this action once, using the values currently in the dialog.")
        self._test_button.clicked.connect(self._test)
        layout.addWidget(self._test_button)
        self._test_status = muted_label("", wrap=False)
        layout.addWidget(self._test_status, 1)

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)
        self._save_button = QPushButton("Save action")
        self._save_button.setObjectName("Primary")
        self._save_button.clicked.connect(self._accept)
        layout.addWidget(self._save_button)
        return holder

    # -------------------------------------------------------------------- fields
    def _refresh_fields(self) -> None:
        spec = self.registry.get(self._draft.type)
        while self._fields_card.body().count():
            item = self._fields_card.body().takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                _clear_layout(item.layout())
        self._bindings = []
        if spec is None:
            self._fields_card.add(muted_label("This action type is no longer available."))
            return

        self._fields_card.add(section_label(f"{spec.label} settings"))
        self._fields_card.add(muted_label(spec.description))
        for field in spec.fields:
            binding = self._make_binding(spec, field)
            if binding is None:
                continue
            self._bindings.append(binding)
            binding.connect(self._on_field_changed)
            self._fields_card.add_row(
                Row(
                    field.label,
                    binding.widget,
                    help_text=field.help,
                    label_width=180,
                )
            )
        self._apply_dependencies()

    def _make_binding(self, spec, field: Field) -> FieldBinding | None:
        value = self._draft.get_field(field.store, field.key, field.default)
        kind = field.kind
        if kind == "args":
            widget: QWidget = ArgsEditor(value or [])
            return FieldBinding(field, widget, "changed")
        if kind == "program":
            widget = PathPicker(str(value or ""), mode="program", placeholder="notepad.exe or C:\\Tools\\app.exe")
            return FieldBinding(field, widget, "changed")
        if kind == "path_file":
            widget = PathPicker(str(value or ""), mode="file", file_filter=field.file_filter or "All files (*.*)")
            return FieldBinding(field, widget, "changed")
        if kind == "path_dir":
            widget = PathPicker(str(value or ""), mode="dir")
            return FieldBinding(field, widget, "changed")
        if kind == "path_any":
            widget = PathPicker(str(value or ""), mode="any")
            return FieldBinding(field, widget, "changed")
        if kind == "choice":
            widget = combo(field.choices, str(value))
            return FieldBinding(field, widget, "currentIndexChanged")
        if kind == "bool":
            widget = QCheckBox("Enabled")
            widget.setChecked(bool(value))
            return FieldBinding(field, widget, "toggled")
        if kind == "int":
            widget = QSpinBox()
            widget.setRange(int(field.minimum if field.minimum is not None else 0),
                            int(field.maximum if field.maximum is not None else 100000))
            widget.setValue(int(value or 0))
            return FieldBinding(field, widget, "valueChanged")
        if kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(float(field.minimum if field.minimum is not None else 0.0),
                            float(field.maximum if field.maximum is not None else 100.0))
            widget.setSingleStep(0.05)
            widget.setValue(float(value or 0.0))
            return FieldBinding(field, widget, "valueChanged")
        if kind == "color":
            widget = ColorButton(str(value or self.theme.accent))
            return FieldBinding(field, widget, "colorChanged")
        if kind == "submenu":
            options = [
                (menu.id, menu.name)
                for menu in self.config.menus
                if menu.id != self.containing_menu_id
            ]
            if not options:
                options = [("", "Create another menu first")]
            widget = combo(options, str(value or ""))
            return FieldBinding(field, widget, "currentIndexChanged")
        if kind == "action_list":
            widget = SequenceEditor(value or [], self._edit_nested_action)
            return FieldBinding(field, widget, "changed")
        if kind == "keystrokes":
            widget = QLineEdit(str(value or ""))
            widget.setPlaceholderText(field.placeholder or "ctrl+shift+s")
            return FieldBinding(field, widget, "textChanged")
        # default: plain text
        widget = QLineEdit(str(value or ""))
        if field.placeholder:
            widget.setPlaceholderText(field.placeholder)
        return FieldBinding(field, widget, "textChanged")

    def _edit_nested_action(self, action: Action) -> Action | None:
        dialog = ActionEditorDialog(
            self.controller,
            action,
            self.config,
            compact=True,
            creating=not action.name,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.result_action()
        return None

    def _apply_dependencies(self) -> None:
        for binding in self._bindings:
            field = binding.field
            if not field.depends_on:
                continue
            key, expected = field.depends_on
            current = self._draft.options.get(key, "")
            visible = current == expected
            binding.widget.setVisible(visible)
            row = binding.widget.parentWidget()
            if row is not None:
                row.setVisible(visible)

    def _collect(self) -> None:
        for binding in self._bindings:
            field = binding.field
            value = binding.read()
            if field.store == "option":
                self._draft.options[field.key] = value
            else:
                self._draft.set_field(field.store, field.key, value)
        self._apply_dependencies()

    # ------------------------------------------------------------------- changes
    def _on_field_changed(self) -> None:
        self._collect()
        self._refresh_validation()
        self._update_summary()

    def _on_name_changed(self, text: str) -> None:
        self._draft.name = text
        self._refresh_validation()
        self._update_summary()

    def _on_description_changed(self, text: str) -> None:
        self._draft.description = text

    # ------------------------------------------------------------------ artwork
    def _effective_icon(self) -> str:
        """What the circle actually shows: the picture wins over the glyph."""
        image = (self._draft.image or "").strip()
        return f"file:{image}" if image else (self._draft.icon or "")

    def _set_image_field(self, path: str) -> None:
        """Write the image path without echoing back through the picker."""
        blocked = self._image_picker.blockSignals(True)
        try:
            self._image_picker.set_value(path)
        finally:
            self._image_picker.blockSignals(blocked)

    def _on_image_changed(self, path: str) -> None:
        value = str(path or "").strip()
        if self._image_picker.value().strip() != value:
            # Keep the field and the value in step (the picker normally drives
            # this itself; this also covers callers that set the picture).
            self._set_image_field(value)
        self._draft.image = value
        if value:
            # Keep a meaningful glyph as the fallback for when the picture is
            # cleared again.
            if not self._draft.icon or self._draft.icon.startswith("file:"):
                self._draft.icon = "image"
            self._icon_button.set_value(f"file:{value}")
        else:
            self._icon_button.set_value(self._draft.icon)
        self._update_summary()

    def _clear_image(self) -> None:
        self._set_image_field("")
        self._on_image_changed("")

    def _on_icon_changed(self, value: str) -> None:
        chosen = str(value or "").strip()
        if chosen.startswith("file:"):
            # "Use an image file…" in the icon picker means the same thing as
            # the picture field: both end up as the action's image.
            image = chosen[5:].strip()
            self._draft.image = image
            self._draft.icon = "image"
            self._set_image_field(image)
        else:
            self._draft.icon = chosen
            self._draft.image = ""
            self._set_image_field("")
        self._icon_button.set_value(self._effective_icon())
        self._update_summary()

    def _on_accent_changed(self, value: str) -> None:
        self._draft.accent = value

    def _on_type_changed(self, _index: int) -> None:
        new_type = self._type_combo.currentData()
        if not new_type or new_type == self._draft.type:
            return
        spec = self.registry.get(new_type)
        previous_spec = self.registry.get(self._draft.type)
        self._draft.type = new_type
        if spec is not None:
            for option_field in spec.option_fields():
                if option_field.key not in self._draft.options:
                    self._draft.options[option_field.key] = option_field.default
            if not self._draft.icon or (previous_spec and self._draft.icon == previous_spec.icon):
                self._draft.icon = spec.icon
                self._icon_button.set_value(self._effective_icon())
            if not self._draft.name or (previous_spec and self._draft.name == previous_spec.label):
                self._draft.name = spec.label
                self._name_edit.setText(spec.label)
            self._draft.confirm = spec.default_confirm
            self._confirm_check.setChecked(spec.default_confirm)
            self._risk_badge.setText(self.registry.risk_label(self._draft))
        self._refresh_fields()
        self._refresh_validation()
        self._update_summary()

    def _on_close_changed(self, value: str) -> None:
        self._draft.close_menu = {"global": None, "close": True, "keep": False}.get(value)

    def _on_confirm_changed(self, checked: bool) -> None:
        self._draft.confirm = checked

    def _on_enabled_changed(self, checked: bool) -> None:
        self._draft.enabled = checked

    def _on_shortcut_changed(self, value: str) -> None:
        self._draft.shortcut = value
        self._refresh_validation()

    def _on_visibility_changed(self) -> None:
        always = self._always_check.isChecked()
        self._file_picker.setEnabled(not always)
        self._app_edit.setEnabled(not always)
        if always:
            self._draft.visible_when = {}
        else:
            rules: dict[str, Any] = {}
            if self._file_picker.value().strip():
                rules["file_exists"] = self._file_picker.value().strip()
            if self._app_edit.text().strip():
                rules["app_running"] = self._app_edit.text().strip()
            self._draft.visible_when = rules

    # ----------------------------------------------------------------- validation
    def _problems(self) -> list[str]:
        context = ValidationContext(config=self.config, registry=self.registry)
        return self.registry.validate(self._draft, context)

    def _refresh_validation(self) -> None:
        problems = self._problems()
        if problems:
            text = "\n".join(f"•  {item}" for item in problems)
            self._validation_label.setText(text)
            self._validation_label.setStyleSheet(f"color: {self.theme.warning};")
        else:
            self._validation_label.setText("This action looks good.")
            self._validation_label.setStyleSheet(f"color: {self.theme.success};")
        self._save_button.setText("Save anyway" if problems else "Save action")

    def _update_summary(self) -> None:
        if not hasattr(self, "_summary"):
            return
        self._summary.setText(self.registry.summary(self._draft, self.config))

    # ------------------------------------------------------------------ testing
    def _test(self) -> None:
        self._collect()
        problems = self._problems()
        if problems:
            self._test_status.setText(f"Cannot test yet — {problems[0].splitlines()[0]}")
            self._test_status.setStyleSheet(f"color: {self.theme.warning};")
            return
        if self._draft.confirm:
            from PySide6.QtWidgets import QMessageBox

            answer = QMessageBox.question(
                self,
                "Test a destructive action",
                f"“{self._draft.display_name()}” is marked as needing confirmation.\n\nRun it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        runner = self.controller.runner
        if self._test_run_id is not None:
            runner.cancel(self._test_run_id)
            self._test_run_id = None
            self._test_button.setText("Test action")
            self._test_status.setText("Cancelled.")
            return

        action = self._draft.clone(new_identifier=False)
        action.id = "editor-test"
        handle = runner.submit(action)
        self._test_run_id = handle.run_id
        self._test_button.setText("Cancel test")
        self._test_status.setText("Running…")
        self._test_status.setStyleSheet(f"color: {self.theme.text_muted};")
        runner.finished.connect(self._on_test_finished)

    def _on_test_finished(self, record) -> None:
        if self._test_run_id is None or record.action_id != "editor-test":
            return
        self._test_run_id = None
        try:
            self.controller.runner.finished.disconnect(self._on_test_finished)
        except (RuntimeError, TypeError):
            pass
        self._test_button.setText("Test action")
        if record.ok:
            self._test_status.setText(f"Success — {record.message}")
            self._test_status.setStyleSheet(f"color: {self.theme.success};")
        else:
            self._test_status.setText(f"Failed — {record.message}")
            self._test_status.setStyleSheet(f"color: {self.theme.danger};")

    # --------------------------------------------------------------------- result
    def _accept(self) -> None:
        self._collect()
        problems = self._problems()
        if problems:
            from PySide6.QtWidgets import QMessageBox

            reply = QMessageBox.warning(
                self,
                "This action has problems",
                "ORBIT found these issues:\n\n"
                + "\n".join(f"•  {item}" for item in problems[:8])
                + "\n\nSave it anyway? You can fix it later in the studio.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Save:
                return
        if not self._draft.name.strip():
            self._draft.name = self._draft.target or self.registry.get(self._draft.type).label
        self.accept()

    def result_action(self) -> Action:
        """The edited action (a new object when creating)."""
        if self._original is not None:
            self._original.apply_from(self._draft)
            return self._original
        return self._draft


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout() is not None:
            _clear_layout(item.layout())
