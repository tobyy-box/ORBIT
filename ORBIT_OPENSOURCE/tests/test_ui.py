"""UI behaviour that can be verified without a visible desktop.

These run against the offscreen Qt platform: layout, selection, navigation,
dismissal and the studio's wiring are all exercised for real.
"""

from __future__ import annotations

import pytest

from core.models import Action, Config, Menu

pytest.importorskip("PySide6", reason="PySide6 is required for the UI tests")


@pytest.fixture()
def config() -> Config:
    from core.config_manager import default_config

    return default_config()


def _menu(qapp, config: Config, *, menu_id: str | None = None):
    from ui.radial_menu import RadialMenu
    from ui.theme_manager import build_theme

    widget = RadialMenu()
    widget.configure(config, build_theme(config.settings), static_preview=True)
    widget.set_menu(config.menu_by_id(menu_id) if menu_id else config.root_menu())
    widget.set_content_scale(1.0)
    widget.reveal = 1.0
    return widget


def test_radial_menu_places_buttons_on_a_circle(qapp, config: Config) -> None:
    widget = _menu(qapp, config)
    placements = widget.placements
    assert len(placements) == 8

    centre = widget.screen_centre()
    distances = {
        round(((p.centre.x() - centre.x()) ** 2 + (p.centre.y() - centre.y()) ** 2) ** 0.5, 1)
        for p in placements
    }
    assert len(distances) == 1, "every button must sit on the same radius"
    assert 150 < distances.pop() < 260

    # The first button is at the top, the next ones continue clockwise.
    top = min(placements, key=lambda p: p.centre.y())
    assert top.index == 0
    assert top.angle_deg == -90
    angles = sorted(p.angle_deg for p in placements)
    assert angles == sorted([-90 + 45 * i for i in range(8)])


def test_selection_wraps_in_both_directions(qapp, config: Config) -> None:
    widget = _menu(qapp, config)
    names = [action.display_name() for action in widget.actions()]
    widget.select_index(0)
    widget.step(-1)
    assert widget.selected_action().display_name() == names[-1]
    widget.step(1)
    assert widget.selected_action().display_name() == names[0]


def test_keyboard_shortcuts_select_and_confirm(qapp, config: Config) -> None:
    from PySide6.QtCore import Qt

    widget = _menu(qapp, config)
    activated: list[Action] = []
    widget.actionTriggered.connect(activated.append)

    assert widget.handle_key(Qt.Key.Key_Right) is True
    assert widget.selected_action() is not None

    widget.select_index(1)
    assert widget.handle_key(Qt.Key.Key_Return) is True
    assert len(activated) == 1

    widget.select_index(0)
    assert widget.handle_key(Qt.Key.Key_2) is True
    assert len(activated) == 2
    assert activated[-1] is widget.actions()[1]


def test_preview_mode_never_executes(qapp, config: Config) -> None:
    widget = _menu(qapp, config)
    widget.set_execution_enabled(False)
    executed: list[Action] = []
    previewed: list[Action] = []
    widget.actionTriggered.connect(executed.append)
    widget.previewClicked.connect(previewed.append)

    widget.select_index(0)
    widget.handle_key(0x01000004)  # Qt.Key_Return
    assert executed == []
    assert len(previewed) == 1


def test_escape_and_back_are_reported(qapp, config: Config) -> None:
    from PySide6.QtCore import Qt

    widget = _menu(qapp, config)
    closed: list[bool] = []
    widget.requestClose.connect(lambda: closed.append(True))
    widget.handle_key(Qt.Key.Key_Escape)
    assert closed == [True]


def test_two_rings_when_there_are_many_actions(qapp, config: Config) -> None:
    big = config.root_menu().clone()
    for index in range(6):
        big.actions.append(
            Action(name=f"Extra {index}", type="url", target="https://example.com", order=10 + index)
        )
    widget = _menu(qapp, config)
    widget.set_menu(big)
    widget.reveal = 1.0
    assert len(widget.placements) == 14
    assert {p.ring for p in widget.placements} == {0, 1}
    inner = {round(p.radius) for p in widget.placements if p.ring == 0}
    outer = {round(p.radius) for p in widget.placements if p.ring == 1}
    assert len(inner) == 1 and len(outer) == 1
    assert outer.pop() > inner.pop()


def test_per_menu_layout_override_changes_geometry(qapp, config: Config) -> None:
    widget = _menu(qapp, config)
    before = widget.width()
    menu = config.root_menu()
    menu.layout = {"radius": 300, "button_size": 60}
    widget.refresh()
    widget.reveal = 1.0
    assert widget.width() > before


def test_hidden_actions_are_not_placed(qapp, config: Config) -> None:
    config.root_menu().actions[0].enabled = False
    widget = _menu(qapp, config)
    assert len(widget.placements) == 7


def test_hovering_a_button_selects_it_and_shows_its_name(qapp, config: Config) -> None:
    """Hover must be enough: no key press, no click, no repaint lag."""
    updates: list[int] = []
    widget = _menu(qapp, config)
    widget.update = lambda *args: updates.append(1)  # type: ignore[method-assign]

    placement = widget.placements[3]
    placement.button.hover_changed.emit(placement.action, True)

    assert widget._hovered_index == 3
    assert widget.selected_action() is placement.action
    metrics = widget._metrics()
    assert 3 in widget._label_rects(metrics, widget.content_scale)
    assert updates, "the wheel must repaint when the hovered action changes"


def test_every_hover_label_stays_clear_of_the_buttons(qapp, config: Config) -> None:
    """A name must never be painted underneath an icon (or a neighbour)."""
    for extra in (0, 2, 5):
        menu = config.root_menu().clone()
        for index in range(extra):
            menu.actions.append(
                Action(name=f"Extra {index}", type="url", target="https://example.com")
            )
        widget = _menu(qapp, config)
        widget.set_menu(menu)
        widget.reveal = 1.0
        metrics = widget._metrics()
        rects = widget._label_rects(metrics, widget.content_scale)
        assert rects, "a wheel this size must have room for at least one label"
        radius = metrics["diameter"] / 2.0
        for index, rect in rects.items():
            assert rect.left() >= 0 and rect.top() >= 0
            assert rect.right() <= widget.width() and rect.bottom() <= widget.height()
            for placement in widget.placements:
                dx = max(rect.left() - placement.centre.x(), 0.0,
                         placement.centre.x() - rect.right())
                dy = max(rect.top() - placement.centre.y(), 0.0,
                         placement.centre.y() - rect.bottom())
                assert (dx * dx + dy * dy) ** 0.5 >= radius, (
                    f"label {index} covers button {placement.index} "
                )


def test_overlay_navigation_stack(qapp, config: Config) -> None:
    from ui.radial_overlay import RadialOverlay
    from ui.theme_manager import build_theme

    overlay = RadialOverlay(lambda: config, lambda: build_theme(config.settings))
    try:
        assert overlay.open_root() is True
        assert overlay.menu.current_menu_id() == config.root_menu_id
        assert overlay.open_submenu("places") is True
        assert overlay._stack == [config.root_menu_id, "places"]
        # Opening the same menu twice is refused instead of recursing.
        assert overlay.open_submenu("places") is False
        overlay.go_back()
        assert overlay._stack == [config.root_menu_id]
        assert overlay.open_submenu("missing-menu") is False
    finally:
        overlay.force_hide()


def test_overlay_dismiss_is_immediate_with_reduced_motion(qapp, config: Config) -> None:
    from ui.radial_overlay import RadialOverlay
    from ui.theme_manager import build_theme

    config.settings.reduced_motion = True
    overlay = RadialOverlay(lambda: config, lambda: build_theme(config.settings))
    overlay.open_root()
    assert overlay.isVisible() is True
    overlay.dismiss()
    assert overlay.isVisible() is False


def test_action_editor_builds_for_every_type(qapp, config: Config) -> None:
    from core.action_registry import get_registry
    from ui.action_editor import ActionEditorDialog
    from ui.theme_manager import build_theme

    registry = get_registry()

    class Stub:
        theme = build_theme(config.settings)
        shortcuts = None

        def __init__(self) -> None:
            self.registry = registry
            self.config_manager = None
            self.runner = None
            self.shortcut = type("S", (), {"state": type("St", (), {"ok": True, "display": "Alt + Space", "message": ""})()})()

    stub = Stub()
    for spec in registry.all():
        draft = registry.build_action(spec.key)
        dialog = ActionEditorDialog(stub, draft, config, containing_menu_id="main", creating=True)
        assert dialog._bindings, spec.key
        assert isinstance(dialog._problems(), list)
        dialog.deleteLater()


def test_action_editor_puts_a_picture_inside_the_circle(qapp, config: Config, tmp_path) -> None:
    from core.action_registry import get_registry
    from ui.action_editor import ActionEditorDialog
    from ui.theme_manager import build_theme

    registry = get_registry()

    class Stub:
        theme = build_theme(config.settings)

        def __init__(self) -> None:
            self.registry = registry
            self.runner = None

    picture = tmp_path / "badge.svg"
    picture.write_text(
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 8 8'>"
        "<circle cx='4' cy='4' r='3' fill='#FF6A00'/></svg>",
        encoding="utf-8",
    )
    draft = registry.build_action("app")
    dialog = ActionEditorDialog(Stub(), draft, config, creating=True)

    # Choosing a file in the picture field makes the image the button artwork.
    dialog._on_image_changed(str(picture))
    assert dialog._draft.image == str(picture)
    assert dialog._icon_button.value() == f"file:{picture}"

    # The icon picker's "use an image file" branch lands in the same place.
    dialog._on_icon_changed("folder")
    assert dialog._draft.image == "" and dialog._draft.icon == "folder"
    dialog._on_icon_changed(f"file:{picture}")
    assert dialog._draft.image == str(picture)
    assert dialog._draft.icon == "image"

    # Clearing goes back to the glyph rather than leaving a dangling path.
    dialog._clear_image()
    assert dialog._draft.image == ""
    assert dialog._icon_button.value() == "image"
    dialog.deleteLater()


def test_icon_picker_keeps_a_chosen_picture(qapp, config: Config) -> None:
    from ui.theme_manager import build_theme
    from ui.widgets import IconPickerDialog

    theme = build_theme(config.settings)

    # Opening the grid renders every library icon — it must not raise.
    dialog = IconPickerDialog("globe", theme)
    assert dialog.list.count() > 0
    assert dialog.selected_icon() == "globe"

    # Browsing the grid highlights a glyph, which becomes the choice...
    dialog.list.setCurrentRow(3)
    highlighted = dialog.selected_icon()
    assert highlighted and highlighted != "globe"

    # ...but choosing an image file replaces it and survives confirmation,
    # instead of being overwritten by the highlighted row.
    dialog._set_choice("file:C:/art/logo.png")
    dialog.accept()
    assert dialog.selected_icon() == "file:C:/art/logo.png"

    # Reopening shows the picture as the current icon, and cancelling does
    # not throw it away.
    reopened = IconPickerDialog("file:C:/art/logo.png", theme)
    assert reopened.selected_icon() == "file:C:/art/logo.png"
    reopened.reject()
    assert reopened.selected_icon() == "file:C:/art/logo.png"


def test_action_editor_reports_validation_before_saving(qapp, config: Config) -> None:
    from core.action_registry import get_registry
    from ui.action_editor import ActionEditorDialog
    from ui.theme_manager import build_theme

    registry = get_registry()
    action = Action(name="Bad", type="url", target="not a url")

    class Stub:
        theme = build_theme(config.settings)

        def __init__(self) -> None:
            self.registry = registry
            self.runner = None
            self.shortcut = type("S", (), {"state": type("St", (), {"ok": True, "display": "", "message": ""})()})()

    dialog = ActionEditorDialog(Stub(), action, config, containing_menu_id="main")
    assert dialog._problems()
    assert "Save anyway" in dialog._save_button.text()
    dialog.deleteLater()


def test_editing_an_existing_action_is_written_back(qapp, config: Config) -> None:
    from ui.studio_window import StudioWindow

    class StubController:
        def __init__(self) -> None:
            from core.config_manager import ConfigManager
            from core.action_registry import get_registry
            from core.action_executor import ActionRunner
            from ui.theme_manager import build_theme

            self.theme = build_theme(config.settings)
            self.registry = get_registry()
            self.runner = ActionRunner(lambda: config, registry=self.registry, services={})
            self.config_manager = ConfigManager()
            self.config_manager.config = config
            self.shortcut = type("S", (), {
                "state": type("St", (), {"ok": True, "hotkey": "alt+space",
                                          "display": "Alt + Space", "message": ""})()
            })()

        def apply_settings(self, **_kwargs) -> None:
            pass

        def apply_hotkey(self, hotkey: str):
            return type("St", (), {"ok": True, "hotkey": hotkey,
                                    "display": hotkey, "message": "ok"})()

    window = StudioWindow(StubController())
    try:
        page = window._pages.get("menus")
        if page is None:
            window.show_page("menus")
            page = window._pages["menus"]
        page._table.selectRow(0)
        action = page._selected_action()
        assert action is not None
        original_name = action.name

        # Drive the dialog without a modal loop: accept it after a change.
        from PySide6.QtWidgets import QDialog
        from ui import radial_editor

        def fake_exec(self):
            self._name_edit.setText("Renamed by the editor")
            self._on_image_changed("C:/art/logo.png")
            self._accept()
            return QDialog.DialogCode.Accepted

        real_exec = radial_editor.ActionEditorDialog.exec
        radial_editor.ActionEditorDialog.exec = fake_exec
        try:
            page._edit_action()
        finally:
            radial_editor.ActionEditorDialog.exec = real_exec

        # The change must be applied to the action that lives in the config —
        # otherwise saving would silently discard it.
        assert action.name == "Renamed by the editor", f"still {action.name!r}"
        assert action.image == "C:/art/logo.png"
        assert original_name != action.name
    finally:
        window.deleteLater()


def test_studio_pages_load_from_a_working_copy(qapp, config: Config) -> None:
    from ui.studio_window import StudioWindow

    calls: list[str] = []

    class StubController:
        def __init__(self) -> None:
            from core.config_manager import ConfigManager
            from core.action_registry import get_registry
            from core.action_executor import ActionRunner
            from ui.theme_manager import build_theme

            self.theme = build_theme(config.settings)
            self.registry = get_registry()
            self.runner = ActionRunner(lambda: config, registry=self.registry, services={})
            self.config_manager = ConfigManager()
            self.config_manager.config = config
            self.shortcut = type("S", (), {
                "state": type("St", (), {"ok": True, "display": "Alt + Space", "message": "", "hotkey": "alt+space"})()
            })()

        def apply_settings(self, **_kwargs) -> None:
            calls.append("apply_settings")

        def apply_hotkey(self, hotkey: str):
            return type("St", (), {"ok": True, "hotkey": hotkey, "display": hotkey, "message": "ok"})()

    window = StudioWindow(StubController())
    try:
        assert window.working is not config
        assert window.dirty is False
        for key in ("menus", "appearance", "behaviour", "data", "about"):
            window.show_page(key)
            assert window.stack.currentWidget() is window._pages[key]

        # "Start ORBIT when I sign in" is offered on the Behaviour page and
        # mirrors the saved setting.
        behaviour = window._pages["behaviour"]
        assert behaviour._startup.text() == "Start ORBIT when I sign in"
        assert behaviour._startup.isChecked() is config.settings.start_on_login

        window.working.settings.accent = "#ff0000"
        window._mark_dirty()
        assert window.dirty is True
        window._set_dirty(False)
        window.revert()
        assert window.dirty is False
        assert window.working.settings.accent == config.settings.accent
    finally:
        window.close()


def test_studio_blocks_invalid_configurations(qapp, config: Config, monkeypatch) -> None:
    from ui import studio_window as module

    class StubController:
        def __init__(self) -> None:
            from core.config_manager import ConfigManager
            from core.action_registry import get_registry
            from ui.theme_manager import build_theme

            self.theme = build_theme(config.settings)
            self.registry = get_registry()
            self.runner = None
            self.config_manager = ConfigManager()
            self.config_manager.config = config
            self.shortcut = type("S", (), {"state": type("St", (), {"ok": True, "display": "", "message": "", "hotkey": ""})()})()

        def apply_settings(self, **_kwargs) -> None:
            raise AssertionError("an invalid configuration must not be applied")

    window = module.StudioWindow(StubController())
    shown: list[str] = []
    monkeypatch.setattr(module.QMessageBox, "critical", lambda *args, **kwargs: shown.append(args[2] if len(args) > 2 else ""))
    try:
        window.working.menus[0].actions[0].type = "not-a-type"
        assert window.save() is False
        assert shown, "the user must be told why saving failed"
    finally:
        window.close()
