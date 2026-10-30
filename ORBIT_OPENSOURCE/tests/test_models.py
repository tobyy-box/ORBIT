"""Model round-tripping and structural helpers."""

from __future__ import annotations

from core.models import Action, Config, Menu, Settings, find_submenu_cycle, new_id


def test_action_round_trip_keeps_every_field() -> None:
    action = Action(
        id="a1",
        name="Deploy",
        type="command",
        target="deploy.bat",
        args=["--fast", "two words"],
        working_dir="C:\\Work",
        icon="rocket",
        image="",
        description="Ship it",
        shortcut="ctrl+shift+d",
        options={"capture_output": True, "nested": {"a": 1}},
        visible_when={"file_exists": "C:\\Work"},
        confirm=True,
        close_menu=False,
        submenu_id="",
        order=3.5,
        accent="#ff8800",
        enabled=False,
    )
    restored = Action.from_dict(action.to_dict())
    assert restored.to_dict() == action.to_dict()
    assert restored.args == ["--fast", "two words"]
    assert restored.options["nested"] == {"a": 1}
    assert restored.close_menu is False
    assert restored.confirm is True
    assert restored.enabled is False


def test_action_tolerates_garbage_and_keeps_unknown_fields() -> None:
    action = Action.from_dict(
        {
            "name": 42,
            "type": None,
            "args": "single",
            "options": "not a dict",
            "future_field": {"kept": True},
        }
    )
    assert action.name == "42"
    assert action.type == "command"
    assert action.args == ["single"]
    assert action.options == {}
    assert action.extra["future_field"] == {"kept": True}
    assert action.to_dict()["future_field"] == {"kept": True}


def test_close_menu_tri_state() -> None:
    assert Action.from_dict({"close_menu": None}).close_menu is None
    assert Action.from_dict({"close_menu": True}).close_menu is True
    assert Action.from_dict({"close_menu": False}).close_menu is False


def test_action_clone_and_apply_from() -> None:
    original = Action(id="a1", name="Mail", type="url", target="https://example.com")
    clone = original.clone()
    assert clone.id != original.id
    assert clone.name == "Mail copy"

    other = Action(id="zzz", name="Renamed", type="path", target="C:\\Temp")
    original.apply_from(other)
    assert original.id == "a1"
    assert original.name == "Renamed"
    assert original.type == "path"
    assert original.target == "C:\\Temp"


def test_menu_sorted_and_lookup() -> None:
    menu = Menu(
        name="Main",
        actions=[
            Action(id="b", name="B", order=2),
            Action(id="a", name="A", order=1),
            Action(id="c", name="C", order=0, enabled=False),
        ],
    )
    assert [item.id for item in menu.sorted_actions()] == ["a", "b"]
    assert [item.id for item in menu.sorted_actions(include_disabled=True)] == ["c", "a", "b"]
    assert menu.action_by_id("a") is not None
    assert menu.remove_action("a") is True
    assert menu.remove_action("missing") is False


def test_config_remove_menu_clears_submenu_references() -> None:
    child = Menu(id="child", name="Child")
    parent = Menu(
        id="parent",
        name="Parent",
        actions=[Action(id="link", type="submenu", submenu_id="child")],
    )
    config = Config(root_menu_id="parent", menus=[parent, child])
    cleared = config.remove_menu("child")
    assert cleared == ["link"]
    assert parent.actions[0].submenu_id == ""
    assert config.menu_by_id("child") is None


def test_config_require_menu_falls_back() -> None:
    config = Config(root_menu_id="nope", menus=[Menu(id="only", name="Only")])
    assert config.root_menu().id == "only"


def test_settings_clamp_values() -> None:
    settings = Settings.from_dict(
        {
            "radius": 5,
            "button_size": -3,
            "overlay_opacity": 4.2,
            "show_labels": "sometimes",
            "theme": "neon",
            "animation_duration": -40,
        }
    )
    assert settings.radius >= 90
    assert settings.button_size >= 48
    assert settings.overlay_opacity == 1.0
    assert settings.show_labels == "hover"
    assert settings.theme == "orbit-dark"
    assert settings.animation_duration == 0


def test_find_submenu_cycle_detects_loops() -> None:
    a = Menu(id="a", name="A", actions=[Action(type="submenu", submenu_id="b")])
    b = Menu(id="b", name="B", actions=[Action(type="submenu", submenu_id="a")])
    assert find_submenu_cycle([a, b], "a") == ["a", "b", "a"]

    c = Menu(id="c", name="C", actions=[Action(type="submenu", submenu_id="c")])
    assert find_submenu_cycle([c], "c") == ["c", "c"]

    leaf = Menu(id="leaf", name="Leaf")
    root = Menu(id="root", name="Root", actions=[Action(type="submenu", submenu_id="leaf")])
    assert find_submenu_cycle([root, leaf], "root") is None


def test_new_id_is_unique() -> None:
    assert len({new_id("act") for _ in range(200)}) == 200
