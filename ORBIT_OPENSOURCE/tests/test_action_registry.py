"""Action registry: schemas, validation and dispatch metadata."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.action_registry import (
    ORBIT_COMMAND_KEYS,
    ValidationContext,
    get_registry,
)
from core.models import Action, Config


@pytest.fixture()
def registry():
    return get_registry()


def test_all_builtin_types_are_registered(registry) -> None:
    expected = {
        "app", "path", "url", "image", "media", "command",
        "sequence", "submenu", "keystroke", "orbit",
    }
    assert set(registry.keys()) == expected


def test_every_type_builds_a_usable_draft(registry, config: Config) -> None:
    context = ValidationContext(config=config, registry=registry)
    for spec in registry.all():
        draft = registry.build_action(spec.key)
        assert draft.type == spec.key
        assert draft.icon == spec.icon
        # A fresh draft is expected to be incomplete, but never to crash.
        problems = registry.validate(draft, context)
        assert isinstance(problems, list)
        assert all(isinstance(item, str) and item for item in problems)


def test_every_option_field_has_a_supported_kind(registry) -> None:
    known = {
        "text", "program", "path_file", "path_dir", "path_any", "url", "args",
        "choice", "bool", "int", "float", "color", "submenu", "action_list", "keystrokes",
    }
    for spec in registry.all():
        for field in spec.fields:
            assert field.kind in known, (spec.key, field.key, field.kind)
            assert field.store in {"target", "args", "working_dir", "submenu_id", "option"}
            if field.store == "option":
                assert field.key


def test_orbit_targets_validate_against_keys(registry, config: Config) -> None:
    """Regression: the ORBIT type used to compare its target with label tuples."""

    action = registry.build_action("orbit")
    action.target = "open_studio"
    assert registry.validate(action, ValidationContext(config=config, registry=registry)) == []
    for key in ORBIT_COMMAND_KEYS:
        action.target = key
        assert registry.validate(action) == [], key
    action.target = "self_destruct"
    assert registry.validate(action)


def test_app_action_reports_missing_executable(registry, config: Config, tmp_path: Path) -> None:
    missing = tmp_path / "missing-tool.exe"
    action = registry.build_action("app", target=str(missing))
    problems = registry.validate(action, ValidationContext(config=config, registry=registry))
    assert problems
    assert any("missing-tool.exe" in item for item in problems)
    assert any("not found" in item.lower() or "does not exist" in item.lower() for item in problems)


def test_app_action_accepts_a_real_program(registry, config: Config) -> None:
    action = registry.build_action("app", target=sys.executable)
    assert registry.validate(action, ValidationContext(config=config, registry=registry)) == []


def test_command_action_through_shell_skips_executable_check(registry) -> None:
    action = registry.build_action("command", target="echo hi | findstr hi")
    action.options["run_through_shell"] = True
    assert registry.validate(action) == []


def test_url_validation_surfaces_problems(registry) -> None:
    action = registry.build_action("url", target="example.com")
    assert registry.validate(action)
    action.target = "https://example.com"
    assert registry.validate(action) == []


def test_keystroke_validation(registry) -> None:
    action = registry.build_action("keystroke", target="ctrl+shift+s")
    assert registry.validate(action) == []
    action.target = "ctrl+banana"
    assert registry.validate(action)


def test_submenu_needs_a_target_menu(registry, config: Config) -> None:
    context = ValidationContext(config=config, registry=registry)
    action = registry.build_action("submenu")
    assert registry.validate(action, context)
    action.submenu_id = "places"
    assert registry.validate(action, context) == []
    action.submenu_id = "missing"
    assert registry.validate(action, context)


def test_submenu_cannot_open_its_own_menu(registry, config: Config) -> None:
    context = ValidationContext(config=config, registry=registry)
    main = config.root_menu()
    main.actions[0].type = "submenu"
    main.actions[0].submenu_id = main.id
    assert registry.validate(main.actions[0], context)


def test_sequence_validation_checks_inner_actions(registry, config: Config) -> None:
    context = ValidationContext(config=config, registry=registry)
    action = registry.build_action("sequence")
    assert "no steps" in registry.validate(action, context)[0]
    action.options["steps"] = [{"action": {"name": "Bad url", "type": "url", "target": "nope"}}]
    problems = registry.validate(action, context)
    assert any("Step 1" in item for item in problems)


def test_menu_close_behaviour_precedence(registry, config: Config) -> None:
    browser = config.menus[0].actions[0]
    assert registry.keeps_menu_open(browser) is False
    submenu = next(action for action in config.menus[0].actions if action.type == "submenu")
    assert registry.keeps_menu_open(submenu) is True
    submenu.close_menu = True
    assert registry.keeps_menu_open(submenu) is False


def test_confirmation_requires_both_flags(registry, config: Config) -> None:
    action = Action(name="Lock", type="command", target="rundll32.exe", confirm=True)
    assert registry.needs_confirmation(action, config) is True
    config.settings.confirm_destructive = False
    assert registry.needs_confirmation(action, config) is False
    action.confirm = False
    config.settings.confirm_destructive = True
    assert registry.needs_confirmation(action, config) is False


def test_risk_labels_distinguish_shell_commands(registry) -> None:
    plain = Action(name="Echo", type="command", target="cmd.exe")
    shell = Action(name="Echo", type="command", target="dir | findstr x")
    shell.options["run_through_shell"] = True
    assert registry.risk_label(plain) != "Safe"  # command type is inherently risky
    assert "cmd.exe" in registry.risk_label(shell)
    assert registry.risk_label(Action(name="Web", type="url")) == "Safe"


def test_summaries_are_human_readable(registry, config: Config) -> None:
    url_action = config.menus[0].actions[0]
    assert "https://" in registry.summary(url_action, config)
    submenu = next(action for action in config.menus[0].actions if action.type == "submenu")
    assert "→" in registry.summary(submenu, config)


def test_icon_falls_back_to_menu_icon_for_submenus(registry, config: Config) -> None:
    submenu = next(action for action in config.menus[0].actions if action.type == "submenu")
    submenu.icon = ""
    assert registry.icon_for(submenu, config) == config.menu_by_id(submenu.submenu_id).icon
    submenu.icon = "star"
    assert registry.icon_for(submenu, config) == "star"


def test_hide_before_run_only_for_keystrokes(registry) -> None:
    assert registry.hide_before_run(Action(name="k", type="keystroke", target="ctrl+c")) is True
    assert registry.hide_before_run(Action(name="u", type="url", target="https://a.b")) is False


def test_unknown_type_is_reported_not_executed(registry) -> None:
    from core.action_registry import ExecContext

    config = Config()
    context = ExecContext(config=config)
    result = registry.execute(Action(name="?", type="nonsense"), context)
    assert result.ok is False
    assert "does not know" in result.message
