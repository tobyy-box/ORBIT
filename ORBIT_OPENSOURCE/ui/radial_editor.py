"""Radial menu editor: menus, their actions and a live preview."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.models import Action, Menu, new_id
from ui.action_editor import ActionEditorDialog, ActionTypePicker
from ui.preview_panel import PreviewPanel
from ui.widgets import (
    Card,
    IconButton,
    LabeledSlider,
    Page,
    Row,
    ScrollPage,
    muted_label,
    section_label,
    small_spin,
)

TABLE_HEADERS = ("#", "Name", "Type", "Target", "Shortcut", "State")


class MenusPage(Page):
    title = "Menus"
    description = (
        "Create radial menus and fill them with actions. Drag-free reordering, "
        "duplicate and nested menus are all here."
    )

    def __init__(self, studio, parent: QWidget | None = None) -> None:
        super().__init__(studio, parent)
        self._menu_id: str = ""
        self._building = False
        self._sizing_widgets: list[QWidget] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.header())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_menu_panel())
        splitter.addWidget(self._build_action_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([196, 560, 386])
        layout.addWidget(splitter, 1)
        self.load()

    # ------------------------------------------------------------------- panels
    def _build_menu_panel(self) -> QWidget:
        card = Card()
        card.add(section_label("Menus"))
        self._menu_list = QListWidget()
        self._menu_list.currentItemChanged.connect(self._on_menu_selected)
        card.add(self._menu_list)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        add = QPushButton("Add")
        add.clicked.connect(self._add_menu)
        row1.addWidget(add)
        duplicate = QPushButton("Duplicate")
        duplicate.clicked.connect(self._duplicate_menu)
        row1.addWidget(duplicate)
        card.body().addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        rename = QPushButton("Rename…")
        rename.clicked.connect(self._rename_menu)
        row2.addWidget(rename)
        root_button = QPushButton("Set as root")
        root_button.setToolTip("The root menu is the one the activation shortcut opens.")
        root_button.clicked.connect(self._set_root)
        row2.addWidget(root_button)
        card.body().addLayout(row2)

        remove = QPushButton("Delete menu")
        remove.setObjectName("Danger")
        remove.clicked.connect(self._delete_menu)
        card.add(remove)
        card.add(
            muted_label(
                "Nested menus are built by adding a “Submenu” action inside another menu."
            )
        )
        return card

    def _build_action_panel(self) -> QWidget:
        card = Card()
        self._menu_title = QLabel("")
        self._menu_title.setObjectName("SectionTitle")
        card.add(self._menu_title)

        fields = QHBoxLayout()
        fields.setSpacing(8)
        self._menu_icon = IconButton("circle-dot", self.controller.theme)
        self._menu_icon.setToolTip("Menu icon (shown in the hub and for submenu buttons)")
        self._menu_icon.iconChanged.connect(self._on_menu_icon_changed)
        fields.addWidget(self._menu_icon)
        self._menu_name = QLineEdit()
        self._menu_name.setPlaceholderText("Menu name")
        self._menu_name.textChanged.connect(self._on_menu_name_changed)
        fields.addWidget(self._menu_name, 2)
        self._menu_description = QLineEdit()
        self._menu_description.setPlaceholderText("Menu description (optional)")
        self._menu_description.textChanged.connect(self._on_menu_description_changed)
        fields.addWidget(self._menu_description, 3)
        card.body().addLayout(fields)

        self._table = QTableWidget(0, len(TABLE_HEADERS))
        self._table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.doubleClicked.connect(lambda _index: self._edit_action())
        self._table.setWordWrap(False)
        self._table.setTextElideMode(Qt.TextElideMode.ElideRight)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(56)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 36)
        self._table.setColumnWidth(2, 104)
        self._table.setColumnWidth(4, 88)
        self._table.setColumnWidth(5, 104)
        self._table.setMinimumWidth(500)
        card.add(self._table)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        for text, handler, tooltip in (
            ("Add action…", self._add_action, "Create a new button in this menu"),
            ("Edit…", self._edit_action, "Edit the selected button"),
            ("Duplicate", self._duplicate_action, "Copy the selected button"),
            ("Up", self._move_action_up, "Move the button earlier"),
            ("Down", self._move_action_down, "Move the button later"),
            ("Test", self._test_action, "Run the selected button once"),
            ("Remove", self._remove_action, "Delete the selected button"),
        ):
            button = QPushButton(text)
            button.setToolTip(tooltip)
            if text == "Remove":
                button.setObjectName("Danger")
            button.clicked.connect(handler)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        card.body().addLayout(toolbar)
        return card

    def _build_preview_panel(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self._preview = PreviewPanel(
            lambda: self.working,
            executor=self.executor,
        )
        self._preview.setMinimumHeight(330)
        layout.addWidget(self._preview, 1)

        sizing = Card()
        # Keep the sizing card at its natural height; the preview absorbs slack
        # and the whole column scrolls on small screens instead of clipping.
        sizing.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        sizing.add(section_label("Sizing"))
        self._custom_sizing = QCheckBox("Custom size for this menu")
        self._custom_sizing.setToolTip("Otherwise the global appearance settings apply.")
        self._custom_sizing.toggled.connect(self._on_custom_sizing)
        sizing.add(self._custom_sizing)

        settings = self.working.settings
        self._sizing_sliders: dict[str, LabeledSlider] = {}
        for key, label, value, low, high, suffix in (
            ("radius", "Radius", settings.radius, 90, 320, " px"),
            ("button_size", "Button size", settings.button_size, 48, 140, " px"),
            ("icon_size", "Icon size", settings.icon_size, 14, 64, " px"),
            ("ring_gap", "Ring gap", settings.ring_gap, 60, 200, " px"),
        ):
            slider = LabeledSlider(value, minimum=low, maximum=high, suffix=suffix)
            slider.valueChanged.connect(lambda _value, name=key: self._on_sizing_changed(name))
            self._sizing_sliders[key] = slider
            row = Row(label, slider, label_width=110)
            sizing.add_row(row)
            self._sizing_widgets.append(row)

        self._per_ring = small_spin(settings.max_per_ring, minimum=3, maximum=12)
        self._per_ring.setSuffix(" per ring")
        self._per_ring.setFixedWidth(140)
        self._per_ring.valueChanged.connect(lambda _value: self._on_sizing_changed("max_per_ring"))
        per_ring_row = Row("Buttons per ring", self._per_ring, label_width=110,
                           help_text="More buttons than this spill onto the next ring (up to three).")
        sizing.add_row(per_ring_row)
        self._sizing_widgets.append(per_ring_row)
        layout.addWidget(sizing)
        return holder

    # --------------------------------------------------------------------- load
    def load(self) -> None:
        self._building = True
        try:
            self._reload_menu_list()
            self._reload_actions()
            self._preview.reload_config()
        finally:
            self._building = False
        self.load_sizing()

    def _reload_menu_list(self) -> None:
        config = self.working
        self._menu_list.clear()
        if not self._menu_id or config.menu_by_id(self._menu_id) is None:
            self._menu_id = config.root_menu_id
        for menu in config.menus:
            marker = "★ " if menu.id == config.root_menu_id else ""
            item = QListWidgetItem(f"{marker}{menu.name}")
            item.setData(Qt.ItemDataRole.UserRole, menu.id)
            item.setToolTip(f"{len(menu.actions)} actions")
            self._menu_list.addItem(item)
            if menu.id == self._menu_id:
                self._menu_list.setCurrentItem(item)

    def _current_menu(self) -> Menu | None:
        return self.working.menu_by_id(self._menu_id)

    def _reload_actions(self) -> None:
        menu = self._current_menu()
        self._table.setRowCount(0)
        if menu is None:
            self._menu_title.setText("NO MENU SELECTED")
            return
        self._menu_title.setText(f"{menu.name.upper()}  ·  {len(menu.actions)} ACTIONS")
        self._menu_name.blockSignals(True)
        self._menu_name.setText(menu.name)
        self._menu_name.blockSignals(False)
        self._menu_description.blockSignals(True)
        self._menu_description.setText(menu.description)
        self._menu_description.blockSignals(False)
        self._menu_icon.set_value(menu.icon)

        for index, action in enumerate(menu.sorted_actions(include_disabled=True)):
            self._table.insertRow(index)
            values = (
                str(index + 1),
                action.display_name(),
                self.controller.registry.get(action.type).label
                if self.controller.registry.get(action.type)
                else action.type,
                self.controller.registry.summary(action, self.working).split("·", 1)[-1].strip(),
                action.shortcut,
                self._state_label(action),
            )
            tooltip = "\n".join(
                part
                for part in (
                    action.display_name(),
                    self.controller.registry.summary(action, self.working),
                    action.description.strip(),
                    "Double-click to edit",
                )
                if part
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setData(Qt.ItemDataRole.UserRole, action.id)
                item.setToolTip(tooltip)
                if not action.enabled:
                    item.setForeground(Qt.GlobalColor.gray)
                self._table.setItem(index, column, item)
        if self._table.rowCount() and self._table.currentRow() < 0:
            self._table.selectRow(0)

    def _validation_context(self):
        executor = self.executor
        if executor is not None:
            return executor.validation_context()
        from core.action_registry import ValidationContext

        return ValidationContext(config=self.working, registry=self.controller.registry)

    def _state_label(self, action: Action) -> str:
        problems = self.controller.registry.validate(action, self._validation_context())
        if problems:
            return "Issues"
        if not action.enabled:
            return "Disabled"
        if action.confirm:
            return "Confirms first"
        if action.close_menu is False:
            return "Stays open"
        return "Ready"

    def load_sizing(self) -> None:
        menu = self._current_menu()
        settings = self.working.settings
        layout = (menu.layout if menu else {}) or {}
        has_custom = any(key in layout for key in ("radius", "button_size", "icon_size", "ring_gap", "max_per_ring"))
        self._custom_sizing.blockSignals(True)
        self._custom_sizing.setChecked(has_custom)
        self._custom_sizing.blockSignals(False)
        for key, slider in self._sizing_sliders.items():
            value = layout.get(key)
            try:
                slider.set_value(int(value) if value else int(getattr(settings, key)))
            except (TypeError, ValueError):
                slider.set_value(int(getattr(settings, key)))
            slider.setEnabled(has_custom)
        per_ring = layout.get("max_per_ring")
        self._per_ring.blockSignals(True)
        self._per_ring.setValue(int(per_ring) if per_ring else int(settings.max_per_ring))
        self._per_ring.blockSignals(False)
        self._per_ring.setEnabled(has_custom)
        for widget in self._sizing_widgets:
            widget.setEnabled(has_custom)

    # ------------------------------------------------------------------ menu edit
    def _on_menu_selected(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None or self._building:
            return
        self._menu_id = current.data(Qt.ItemDataRole.UserRole)
        self._reload_actions()
        self._preview.set_menu(self._menu_id)
        self.load_sizing()

    def _add_menu(self) -> None:
        name, ok = QInputDialog.getText(self, "New menu", "Menu name:", text="New menu")
        if not ok or not name.strip():
            return
        menu = Menu(id=new_id("menu"), name=name.strip(), icon="circle-dot")
        self.working.menus.append(menu)
        self._menu_id = menu.id
        self._after_structure_change()

    def _duplicate_menu(self) -> None:
        menu = self._current_menu()
        if menu is None:
            return
        clone = menu.clone()
        self.working.menus.append(clone)
        self._menu_id = clone.id
        self._after_structure_change()

    def _rename_menu(self) -> None:
        menu = self._current_menu()
        if menu is None:
            return
        name, ok = QInputDialog.getText(self, "Rename menu", "Menu name:", text=menu.name)
        if ok and name.strip():
            menu.name = name.strip()
            self._after_structure_change()

    def _set_root(self) -> None:
        menu = self._current_menu()
        if menu is None:
            return
        self.working.root_menu_id = menu.id
        self._after_structure_change()

    def _delete_menu(self) -> None:
        menu = self._current_menu()
        if menu is None:
            return
        if len(self.working.menus) <= 1:
            QMessageBox.information(self, "Cannot delete", "A configuration needs at least one menu.")
            return
        answer = QMessageBox.question(
            self,
            "Delete this menu?",
            f"“{menu.name}” and its {len(menu.actions)} actions will be removed.\n\n"
            "Buttons in other menus that opened it will be cleared.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        cleared = self.working.remove_menu(menu.id)
        self._menu_id = self.working.root_menu_id
        self._after_structure_change()
        if cleared:
            self._status(f"{len(cleared)} submenu button(s) were cleared because the menu is gone.")

    def _on_menu_name_changed(self, text: str) -> None:
        if self._building:
            return
        menu = self._current_menu()
        if menu is None:
            return
        menu.name = text or "Untitled menu"
        self._menu_title.setText(f"{menu.name.upper()}  ·  {len(menu.actions)} ACTIONS")
        self._refresh_menu_list_labels()

    def _on_menu_description_changed(self, text: str) -> None:
        if self._building:
            return
        menu = self._current_menu()
        if menu is not None:
            menu.description = text

    def _on_menu_icon_changed(self, value: str) -> None:
        menu = self._current_menu()
        if menu is not None:
            menu.icon = value
            self._refresh_menu_list_labels()
            self._mark_changed()

    def _refresh_menu_list_labels(self) -> None:
        config = self.working
        for row in range(self._menu_list.count()):
            item = self._menu_list.item(row)
            menu = config.menu_by_id(item.data(Qt.ItemDataRole.UserRole))
            if menu is not None:
                marker = "★ " if menu.id == config.root_menu_id else ""
                item.setText(f"{marker}{menu.name}")
                item.setToolTip(f"{len(menu.actions)} actions")
        self._mark_changed()

    # ---------------------------------------------------------------- action edit
    def _selected_action(self) -> Action | None:
        menu = self._current_menu()
        if menu is None:
            return None
        row = self._table.currentRow()
        if row < 0 or row >= self._table.rowCount():
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        return menu.action_by_id(item.data(Qt.ItemDataRole.UserRole))

    def _add_action(self) -> None:
        menu = self._current_menu()
        if menu is None:
            return
        picker = ActionTypePicker(self.controller.registry, self.controller.theme, self)
        if picker.exec() != QDialog.DialogCode.Accepted:
            return
        draft = self.controller.registry.build_action(picker.chosen_type())
        draft.order = float(len(menu.actions))
        dialog = ActionEditorDialog(
            self.controller,
            draft,
            self.working,
            containing_menu_id=menu.id,
            creating=True,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            action = dialog.result_action()
            action.order = float(len(menu.actions))
            menu.actions.append(action)
            self._after_structure_change(select_action=action.id)

    def _edit_action(self) -> None:
        menu = self._current_menu()
        action = self._selected_action()
        if menu is None or action is None:
            return
        dialog = ActionEditorDialog(
            self.controller,
            action,
            self.working,
            containing_menu_id=menu.id,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # The dialog edits a copy of the action; without this call every
            # change (name, target, picture, …) would be silently thrown away
            # when the dialog closes.
            dialog.result_action()
            self._after_structure_change(select_action=action.id)

    def _duplicate_action(self) -> None:
        menu = self._current_menu()
        action = self._selected_action()
        if menu is None or action is None:
            return
        clone = action.clone()
        clone.order = action.order + 0.5
        menu.actions.append(clone)
        self._normalise_order(menu)
        self._after_structure_change(select_action=clone.id)

    def _remove_action(self) -> None:
        menu = self._current_menu()
        action = self._selected_action()
        if menu is None or action is None:
            return
        answer = QMessageBox.question(
            self,
            "Remove this action?",
            f"“{action.display_name()}” will be removed from “{menu.name}”.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        menu.remove_action(action.id)
        self._normalise_order(menu)
        self._after_structure_change()

    def _move_action_up(self) -> None:
        self._move_action(-1)

    def _move_action_down(self) -> None:
        self._move_action(1)

    def _move_action(self, delta: int) -> None:
        menu = self._current_menu()
        action = self._selected_action()
        if menu is None or action is None:
            return
        ordered = menu.sorted_actions(include_disabled=True)
        index = ordered.index(action)
        target = index + delta
        if not (0 <= target < len(ordered)):
            return
        ordered[index], ordered[target] = ordered[target], ordered[index]
        for position, item in enumerate(ordered):
            item.order = float(position)
        menu.actions = ordered
        self._after_structure_change(select_action=action.id, keep_row=target)

    def _normalise_order(self, menu: Menu) -> None:
        for position, item in enumerate(menu.sorted_actions(include_disabled=True)):
            item.order = float(position)

    def _test_action(self) -> None:
        action = self._selected_action()
        if action is None:
            return
        runner = getattr(self.controller, "runner", None)
        if runner is None:
            self._status("Testing is not available right now.")
            return
        problems = self.controller.registry.validate(action, self._validation_context())
        if problems:
            QMessageBox.warning(
                self,
                "This action is not ready",
                "Fix these first:\n\n" + "\n".join(f"•  {item}" for item in problems[:6]),
            )
            return
        executor = self.executor
        if action.confirm and (executor is None or not executor.should_close_menu(action)):
            answer = QMessageBox.question(
                self,
                "Test a destructive action",
                f"“{action.display_name()}” is marked as needing confirmation.\n\nRun it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        runner.submit(action)
        self._status(f"Test started for “{action.display_name()}”.")

    # ---------------------------------------------------------------- sizing
    def _on_custom_sizing(self, checked: bool) -> None:
        menu = self._current_menu()
        if menu is None:
            return
        if checked:
            settings = self.working.settings
            menu.layout = {
                "radius": self._sizing_sliders["radius"].value() or settings.radius,
                "button_size": self._sizing_sliders["button_size"].value() or settings.button_size,
                "icon_size": self._sizing_sliders["icon_size"].value() or settings.icon_size,
                "ring_gap": self._sizing_sliders["ring_gap"].value() or settings.ring_gap,
                "max_per_ring": self._per_ring.value(),
            }
        else:
            menu.layout = {}
        self.load_sizing()
        self._mark_changed(rebuild_preview=True)

    def _on_sizing_changed(self, key: str) -> None:
        menu = self._current_menu()
        if menu is None or not self._custom_sizing.isChecked():
            return
        if key == "max_per_ring":
            menu.layout[key] = int(self._per_ring.value())
        else:
            menu.layout[key] = int(self._sizing_sliders[key].value())
        self._mark_changed(rebuild_preview=True)

    # ----------------------------------------------------------------- helpers
    def _after_structure_change(self, *, select_action: str = "", keep_row: int = -1) -> None:
        self._refresh_menu_list_labels()
        self._reload_actions()
        if select_action:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == select_action:
                    self._table.selectRow(row)
                    break
        elif keep_row >= 0 and keep_row < self._table.rowCount():
            self._table.selectRow(keep_row)
        self._preview.refresh()
        self._mark_changed()

    def _mark_changed(self, *, rebuild_preview: bool = True) -> None:
        if self._building:
            return
        if rebuild_preview:
            self._preview.refresh()
        self.notify_changed()

    def _status(self, message: str) -> None:
        self.studio.set_status(message)
