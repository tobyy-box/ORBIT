"""Configuration loading, validation, saving, backups and import/export.

Rules that matter for safety:

* Saving is atomic (write temp file, then replace) so a crash cannot leave a
  truncated config behind.
* Every save and every import snapshots the previous file into ``backups/``
  first.  A user's configuration is never silently overwritten.
* Imported JSON is validated before it replaces anything, and imported files
  that fail validation are rejected with an explanation.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from core import paths
from core.keycodes import validate_hotkey
from core.models import (
    CONFIG_VERSION,
    Action,
    Config,
    Menu,
    Settings,
    find_submenu_cycle,
    new_id,
)

MAX_BACKUPS = 25


class ConfigError(RuntimeError):
    """Raised when a configuration document cannot be used at all."""


# --------------------------------------------------------------------- defaults
def _action(**kwargs: Any) -> Action:
    return Action.from_dict(kwargs)


def default_config() -> Config:
    """A complete, immediately useful default configuration.

    Every default action points at something that exists on a stock Windows
    install (or a per-user folder), so ORBIT works on first launch without any
    account, network access or extra software.
    """
    home = "%USERPROFILE%"

    main = Menu(
        id="main",
        name="Main",
        description="Your everyday actions",
        icon="orbit",
        actions=[
            _action(
                id="main_browser", name="Browser", type="url", icon="globe",
                target="https://www.google.com", order=0,
                description="Open your default browser",
            ),
            _action(
                id="main_files", name="Files", type="path", icon="folder",
                target=home, order=1, description="Open your home folder",
            ),
            _action(
                id="main_projects", name="Places", type="submenu", icon="layers",
                submenu_id="places", order=2,
                description="Downloads, Documents, Pictures and more",
            ),
            _action(
                id="main_terminal", name="Terminal", type="app", icon="terminal",
                target="cmd.exe", order=3, description="Command Prompt",
                args=[],
                options={"new_window": True},
            ),
            _action(
                id="main_media", name="Music", type="media", icon="play",
                target="play_pause", order=4,
                description="Play or pause the active player",
                options={"method": "keys"},
            ),
            _action(
                id="main_tools", name="Tools", type="submenu", icon="wrench",
                submenu_id="tools", order=5, description="Utilities and system actions",
            ),
            _action(
                id="main_screenshot", name="Screenshot", type="keystroke", icon="camera",
                target="win+shift+s", order=6,
                description="Snip a region with Windows snipping",
            ),
            _action(
                id="main_studio", name="Studio", type="orbit", icon="sliders",
                target="open_studio", order=7, description="Configure ORBIT",
            ),
        ],
    )

    places = Menu(
        id="places",
        name="Places",
        description="Folders you use often",
        icon="folder-open",
        actions=[
            _action(id="places_downloads", name="Downloads", type="path", icon="download",
                    target=f"{home}\\Downloads", order=0),
            _action(id="places_documents", name="Documents", type="path", icon="file-text",
                    target=f"{home}\\Documents", order=1),
            _action(id="places_pictures", name="Pictures", type="path", icon="image",
                    target=f"{home}\\Pictures", order=2),
            _action(id="places_desktop", name="Desktop", type="path", icon="monitor",
                    target=f"{home}\\Desktop", order=3),
        ],
    )

    tools = Menu(
        id="tools",
        name="Tools",
        description="Utilities",
        icon="wrench",
        actions=[
            _action(id="tools_calc", name="Calculator", type="app", icon="calculator",
                    target="calc.exe", order=0),
            _action(id="tools_work", name="Start work", type="sequence", icon="zap",
                    order=1, description="Mail, music and documents in one step",
                    options={
                        "continue_on_error": True,
                        "steps": [
                            {"action": {"name": "Mail", "type": "url", "icon": "mail",
                                        "target": "https://mail.google.com"}},
                            {"action": {"name": "Music", "type": "media", "icon": "play",
                                        "target": "play_pause", "options": {"method": "keys"}}},
                            {"delay_ms": 400},
                            {"action": {"name": "Documents", "type": "path", "icon": "file-text",
                                        "target": f"{home}\\Documents"}},
                        ],
                    },
                    close_menu=True),
            _action(id="tools_lock", name="Lock screen", type="command", icon="lock",
                    target="rundll32.exe", args=["user32.dll,LockWorkStation"], order=2,
                    confirm=True, description="Locks this Windows session",
                    options={"show_console": False}),
            _action(id="tools_taskmgr", name="Task Manager", type="app", icon="activity",
                    target="taskmgr.exe", order=3, confirm=True),
            _action(id="tools_reload", name="Reload config", type="orbit", icon="refresh",
                    target="reload_config", order=4, close_menu=True,
                    description="Re-read config.json without restarting"),
            _action(id="tools_folder", name="Config folder", type="orbit", icon="settings",
                    target="open_config_folder", order=5, close_menu=True),
        ],
    )

    settings = Settings()
    return Config(
        version=CONFIG_VERSION,
        root_menu_id=main.id,
        menus=[main, places, tools],
        settings=settings,
    )


# -------------------------------------------------------------------- validation
@dataclass(slots=True)
class ValidationReport:
    errors: list[str]
    warnings: list[str]
    config: Config | None = None

    @property
    def ok(self) -> bool:
        return not self.errors and self.config is not None

    def summary(self, limit: int = 6) -> str:
        parts: list[str] = []
        if self.errors:
            parts.append("Errors:\n" + "\n".join(f"• {item}" for item in self.errors[:limit]))
        if self.warnings:
            parts.append("Notices:\n" + "\n".join(f"• {item}" for item in self.warnings[:limit]))
        extra = len(self.errors) + len(self.warnings) - limit * 2
        if extra > 0:
            parts.append(f"…and {extra} more.")
        return "\n\n".join(parts)


def _known_types() -> set[str]:
    from core.action_registry import get_registry

    return set(get_registry().keys())


def validate_config(data: Any, *, strict_types: bool = True) -> ValidationReport:
    """Validate a raw configuration document.

    Structural problems are errors (the document is refused).  Fixable and
    cosmetic problems are warnings (they are repaired by ``sanitize``).
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ValidationReport(["The file does not contain a JSON object."], [], None)

    if "menus" in data and not isinstance(data["menus"], list):
        errors.append('"menus" must be a list.')
    if "settings" in data and not isinstance(data["settings"], dict):
        errors.append('"settings" must be an object.')

    config = Config.from_dict(data)

    if config.version > CONFIG_VERSION:
        warnings.append(
            f"This file was written by a newer ORBIT (version {config.version}); "
            "unknown fields are preserved but may be ignored."
        )

    if not config.menus:
        errors.append("The configuration contains no menus.")

    seen_menu_ids: set[str] = set()
    for menu in config.menus:
        if menu.id in seen_menu_ids:
            errors.append(f"Duplicate menu id: {menu.id}")
        seen_menu_ids.add(menu.id)
        if not menu.name.strip():
            warnings.append(f"A menu with id {menu.id} has no name.")
        seen_action_ids: set[str] = set()
        for action in menu.actions:
            if action.id in seen_action_ids:
                errors.append(f'Duplicate action id "{action.id}" inside menu "{menu.name}".')
            seen_action_ids.add(action.id)
            if strict_types and action.type not in _known_types():
                errors.append(
                    f'Unknown action type "{action.type}" in menu "{menu.name}" '
                    f'(action "{action.display_name()}").'
                )

    if config.root_menu_id not in seen_menu_ids:
        warnings.append(
            f'The root menu "{config.root_menu_id}" does not exist; the first menu will be used.'
        )

    for menu in config.menus:
        for action in menu.actions:
            if action.type == "submenu":
                if not action.submenu_id:
                    warnings.append(f'Submenu action "{action.display_name()}" has no menu selected.')
                elif action.submenu_id not in seen_menu_ids:
                    warnings.append(
                        f'Action "{action.display_name()}" points at a missing menu '
                        f'("{action.submenu_id}").'
                    )
                elif action.submenu_id == menu.id:
                    errors.append(
                        f'Action "{action.display_name()}" opens its own menu, which would loop forever.'
                    )

    cycle = find_submenu_cycle(config.menus, config.root_menu_id)
    if cycle:
        errors.append("Submenus form a loop: " + " → ".join(config.menu_name(item) for item in cycle))

    hotkey_error = validate_hotkey(config.settings.hotkey)
    if hotkey_error:
        errors.append(f"Activation shortcut: {hotkey_error}")

    settings = config.settings
    if not (0.0 <= settings.overlay_opacity <= 1.0):
        warnings.append("Overlay opacity must be between 0 and 1; the default will be used.")
    if settings.radius < 90:
        warnings.append("Menu radius is very small; 90 px is the minimum.")

    return ValidationReport(errors, warnings, config)


def sanitize(config: Config) -> tuple[Config, list[str]]:
    """Repair fixable issues in place and describe what was changed."""
    notes: list[str] = []
    config.version = max(config.version, CONFIG_VERSION)

    seen_menu_ids: set[str] = set()
    for menu in config.menus:
        if not menu.id or menu.id in seen_menu_ids:
            old = menu.id
            menu.id = new_id("menu")
            notes.append(f'Renamed menu id "{old}" to "{menu.id}".')
        seen_menu_ids.add(menu.id)
        if not menu.name.strip():
            menu.name = "Untitled menu"
            notes.append("A menu had no name and was renamed to \"Untitled menu\".")

    global_action_ids: set[str] = set()
    for menu in config.menus:
        for action in menu.actions:
            if not action.id or action.id in global_action_ids:
                old = action.id
                action.id = new_id("act")
                notes.append(f'Renamed duplicate action id "{old}" to "{action.id}".')
            global_action_ids.add(action.id)
            if not action.name.strip():
                action.name = action.target or action.type
                notes.append(f'An action had no name; using "{action.name}".')

    if config.root_menu_id not in seen_menu_ids:
        if config.menus:
            config.root_menu_id = config.menus[0].id
            notes.append("Root menu was missing; the first menu is now the root.")

    for menu in config.menus:
        for action in menu.actions:
            if action.type == "submenu":
                if action.submenu_id not in seen_menu_ids or action.submenu_id == menu.id:
                    action.submenu_id = ""
                    notes.append(f'Cleared an invalid submenu target on "{action.display_name()}".')
    return config, notes


# ------------------------------------------------------------------ file helpers
def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


class ConfigManager:
    """Owns the live :class:`Config` and all persistence for it."""

    def __init__(self, path: Path | None = None) -> None:
        self.path: Path = Path(path) if path else paths.config_path()
        self.config: Config = default_config()
        self.load_warnings: list[str] = []
        self.load_errors: list[str] = []
        self.is_fresh_install: bool = False

    # ------------------------------------------------------------------ loading
    def load(self, *, create_if_missing: bool = True) -> Config:
        """Read the config file, falling back to defaults on any problem."""
        self.load_warnings = []
        self.load_errors = []
        if not self.path.exists():
            self.is_fresh_install = True
            self.config = default_config()
            if create_if_missing:
                try:
                    self.save(reason="initial", make_backup=False)
                except OSError as exc:  # pragma: no cover - disk problems
                    self.load_errors.append(f"Could not write the initial configuration:\n{exc}")
            return self.config

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            self.load_errors.append(
                f"{self.path.name} is not valid JSON (line {exc.lineno}, column {exc.colno}).\n"
                "ORBIT started with defaults. Your old file is untouched."
            )
            self.config = default_config()
            return self.config
        except OSError as exc:
            self.load_errors.append(f"{self.path.name} could not be read:\n{exc}")
            self.config = default_config()
            return self.config

        report = validate_config(raw)
        self.load_warnings = list(report.warnings)
        if report.errors:
            self.load_errors.extend(report.errors)
            self.load_warnings.append("Validation problems found; values were repaired where possible.")

        config = report.config or Config.from_dict(raw)
        config, notes = sanitize(config)
        self.load_warnings.extend(notes)
        self.config = config
        return self.config

    def reload(self) -> Config:
        return self.load(create_if_missing=False)

    # ------------------------------------------------------------------- saving
    def save(
        self,
        config: Config | None = None,
        *,
        reason: str = "save",
        make_backup: bool = True,
    ) -> Path:
        """Atomically write the configuration, keeping a backup of the old one."""
        target_config = config if config is not None else self.config
        clean, _notes = sanitize(target_config.clone())
        self.config = clean

        if make_backup and self.path.exists():
            self.backup(reason=reason)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(clean.to_dict(), indent=2, ensure_ascii=False)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(payload + "\n", encoding="utf-8")
        os.replace(temp, self.path)
        return self.path

    def backup(self, *, reason: str = "save") -> Path | None:
        """Snapshot the current file into ``backups/``."""
        if not self.path.exists():
            return None
        safe_reason = "".join(char for char in reason if char.isalnum() or char in "-_") or "save"
        target = paths.backups_dir() / f"config-{_timestamp()}-{safe_reason}.json"
        counter = 1
        while target.exists():
            target = paths.backups_dir() / f"config-{_timestamp()}-{safe_reason}-{counter}.json"
            counter += 1
        shutil.copy2(self.path, target)
        self._trim_backups()
        return target

    def list_backups(self) -> list[Path]:
        files = sorted(paths.backups_dir().glob("config-*.json"), key=lambda item: item.stat().st_mtime)
        return list(reversed(files))

    def _trim_backups(self) -> None:
        files = sorted(paths.backups_dir().glob("config-*.json"), key=lambda item: item.stat().st_mtime)
        for stale in files[:-MAX_BACKUPS]:
            try:
                stale.unlink()
            except OSError:
                pass

    def restore_backup(self, backup_path: Path) -> tuple[bool, str]:
        """Replace the live config with a backup (backing up the current file first)."""
        backup_path = Path(backup_path)
        if not backup_path.exists():
            return False, "That backup file no longer exists."
        try:
            raw = json.loads(backup_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return False, f"That backup could not be read:\n{exc}"
        report = validate_config(raw)
        if report.errors:
            return False, "That backup is not valid:\n" + "\n".join(report.errors[:5])
        # Snapshot what is live right now, then write the backup into place.
        self.backup(reason="before-restore")
        self.config = report.config or self.config
        self.save(self.config, reason="restore", make_backup=False)
        return True, f"Restored {backup_path.name}."

    # ----------------------------------------------------------- export / import
    def export_to(self, destination: Path, *, config: Config | None = None) -> tuple[bool, str]:
        source = config if config is not None else self.config
        destination = Path(destination)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(source.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            return False, f"The configuration could not be exported:\n{exc}"
        return True, f"Exported {len(source.menus)} menus to {destination.name}."

    def import_from(self, source: Path, *, replace: bool = True) -> tuple[bool, list[str]]:
        """Validate then adopt an exported configuration file.

        The existing configuration is backed up before it is replaced, and a
        failed validation leaves the live configuration completely untouched.
        """
        source = Path(source)
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, [f"Not valid JSON (line {exc.lineno}, column {exc.colno})."]
        except OSError as exc:
            return False, [f"The file could not be read:\n{exc}"]

        report = validate_config(raw)
        if report.errors:
            return False, ["Import stopped — the file has problems:"] + report.errors

        config = report.config
        assert config is not None
        config, notes = sanitize(config)
        messages = list(report.warnings) + notes

        if replace:
            backup = self.backup(reason="before-import")
            self.config = config
            self.save(config, reason="import", make_backup=False)
            if backup is not None:
                messages.append(f"Previous configuration backed up as {backup.name}.")
            messages.append(f"Imported {len(config.menus)} menus from {source.name}.")
        return True, messages

    # -------------------------------------------------------------------- reset
    def reset_to_defaults(self) -> tuple[bool, str]:
        backup = self.backup(reason="before-reset")
        self.config = default_config()
        self.save(self.config, reason="reset", make_backup=False)
        note = f"Previous configuration backed up as {backup.name}." if backup else ""
        return True, "\n".join(filter(None, ["Default configuration restored.", note]))


def collect_path_actions(config: Config) -> Iterable[Action]:
    """Every action that refers to a filesystem path (used by diagnostics)."""
    for _menu, action in config.iter_actions():
        if action.type in {"path", "image", "app", "command"}:
            yield action
