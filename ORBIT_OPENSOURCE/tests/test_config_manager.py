"""Configuration loading, validation, backups and import/export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.config_manager import ConfigManager, default_config, sanitize, validate_config
from core.models import Action, Menu


def test_fresh_install_writes_a_valid_file(tmp_path: Path) -> None:
    manager = ConfigManager(tmp_path / "config.json")
    config = manager.load()
    assert manager.is_fresh_install is True
    assert manager.path.exists()
    assert config.menus
    saved = json.loads(manager.path.read_text(encoding="utf-8"))
    assert saved["version"] >= 2
    assert isinstance(saved["menus"], list)


def test_default_config_has_no_validation_errors() -> None:
    report = validate_config(default_config().to_dict())
    assert report.errors == []
    assert report.warnings == []
    assert report.config is not None


def test_save_creates_backup_and_keeps_previous_content(manager: ConfigManager) -> None:
    original = manager.path.read_text(encoding="utf-8")
    manager.config.settings.accent = "#123456"
    manager.save(reason="test")
    backups = manager.list_backups()
    assert backups, "a backup should exist"
    assert backups[0].read_text(encoding="utf-8") == original
    assert json.loads(manager.path.read_text(encoding="utf-8"))["settings"]["accent"] == "#123456"


def test_corrupt_file_falls_back_to_defaults_without_overwriting(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    manager = ConfigManager(path)
    config = manager.load()
    assert config.menus
    assert manager.load_errors
    assert "not valid JSON" in manager.load_errors[0]
    assert path.read_text(encoding="utf-8") == "{ this is not json"


def test_duplicate_ids_are_repaired(manager: ConfigManager) -> None:
    menu = manager.config.menus[0]
    duplicate = menu.actions[0].clone(new_identifier=False)
    duplicate.name = "Clone"
    menu.actions.append(duplicate)
    manager.save(reason="dup")
    ids = [action.id for _menu, action in manager.config.iter_actions()]
    assert len(ids) == len(set(ids))


def test_dangling_submenu_reference_is_cleared(manager: ConfigManager) -> None:
    config, notes = sanitize(manager.config.clone())
    config.menus[0].actions[0].type = "submenu"
    config.menus[0].actions[0].submenu_id = "does-not-exist"
    repaired, notes = sanitize(config)
    assert repaired.menus[0].actions[0].submenu_id == ""
    assert any("submenu" in note.lower() for note in notes)


def test_validation_rejects_unknown_action_type() -> None:
    config = default_config()
    config.menus[0].actions[0].type = "teleport"
    report = validate_config(config.to_dict())
    assert any("Unknown action type" in error for error in report.errors)
    assert report.ok is False


def test_validation_rejects_submenu_cycle() -> None:
    config = default_config()
    config.menus[0].actions[0].type = "submenu"
    config.menus[0].actions[0].submenu_id = "tools"
    config.menus[2].actions.append(
        Action(name="Loop", type="submenu", submenu_id=config.menus[0].id)
    )
    report = validate_config(config.to_dict())
    assert any("loop" in error.lower() for error in report.errors)


def test_validation_rejects_bad_hotkey() -> None:
    config = default_config()
    config.settings.hotkey = "ctrl+"
    report = validate_config(config.to_dict())
    assert any("shortcut" in error.lower() for error in report.errors)


def test_export_and_import_round_trip(manager: ConfigManager, tmp_path: Path) -> None:
    manager.config.settings.accent = "#abcdef"
    target = tmp_path / "export.json"
    ok, message = manager.export_to(target)
    assert ok and target.exists()

    ok, messages = manager.import_from(target)
    assert ok, messages
    assert manager.config.settings.accent == "#abcdef"
    # Importing backs up the file it replaced.
    assert any("backed up" in message for message in messages)


def test_import_rejects_invalid_file_and_leaves_config_untouched(
    manager: ConfigManager, tmp_path: Path
) -> None:
    before = manager.path.read_text(encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"menus": [{"id": "x", "name": "X", "actions": [
        {"id": "a", "name": "Bad", "type": "not-a-type"}
    ]}]}), encoding="utf-8")
    ok, messages = manager.import_from(broken)
    assert ok is False
    assert messages
    assert manager.path.read_text(encoding="utf-8") == before


def test_import_reports_bad_json(manager: ConfigManager, tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{ nope", encoding="utf-8")
    ok, messages = manager.import_from(broken)
    assert ok is False
    assert "JSON" in messages[0]


def test_reset_restores_defaults_and_backs_up(manager: ConfigManager) -> None:
    manager.config.settings.accent = "#111111"
    manager.save(reason="before-reset-test")
    ok, message = manager.reset_to_defaults()
    assert ok
    assert manager.config.settings.accent == default_config().settings.accent
    assert "backed up" in message


def test_restore_backup_round_trip(manager: ConfigManager) -> None:
    manager.config.settings.accent = "#222222"
    manager.save(reason="first")
    manager.config.settings.accent = "#333333"
    manager.save(reason="second")
    # Every save snapshots the file it replaces, so this backup holds #222222.
    backup = manager.list_backups()[0]
    ok, message = manager.restore_backup(backup)
    assert ok, message
    assert manager.config.settings.accent == "#222222"
    import json

    on_disk = json.loads(manager.path.read_text(encoding="utf-8"))
    assert on_disk["settings"]["accent"] == "#222222", "the restore must reach the file on disk"


def test_newer_schema_is_preserved_with_a_warning(tmp_path: Path) -> None:
    payload = default_config().to_dict()
    payload["version"] = 99
    payload["future"] = {"x": 1}
    report = validate_config(payload)
    assert report.config is not None
    assert any("newer ORBIT" in warning for warning in report.warnings)
    assert report.config.to_dict()["future"] == {"x": 1}


def test_non_object_root_is_an_error() -> None:
    report = validate_config(["not", "a", "config"])
    assert report.errors
    assert report.config is None


@pytest.mark.parametrize("marker", ["file_exists", "app_running", "always"])
def test_visibility_rules_survive_round_trip(marker: str) -> None:
    action = Action(name="A", type="url", target="https://example.com", visible_when={marker: True})
    restored = Action.from_dict(action.to_dict())
    assert restored.visible_when == {marker: True}


def test_menu_clone_gets_new_ids(manager: ConfigManager) -> None:
    menu: Menu = manager.config.menus[0]
    clone = menu.clone()
    assert clone.id != menu.id
    assert clone.name.endswith("copy")
    assert {action.id for action in clone.actions}.isdisjoint({action.id for action in menu.actions})
